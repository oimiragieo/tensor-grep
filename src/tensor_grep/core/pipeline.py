import re
from contextlib import nullcontext
from typing import Any

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.backends.cudf_backend import CuDFBackend
from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.backends.rust_backend import RustCoreBackend
from tensor_grep.backends.stringzilla_backend import StringZillaBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.hardware.memory_manager import MemoryManager
from tensor_grep.core.query_analyzer import QueryAnalyzer, QueryType


class ConfigurationError(RuntimeError):
    """Raised when explicit user routing intent cannot be satisfied."""


class Pipeline:
    @staticmethod
    def _raise_explicit_gpu_configuration_error(
        config: SearchConfig | None,
        detail: str,
        cause: Exception | None = None,
    ) -> None:
        requested_ids = list(config.gpu_device_ids or []) if config is not None else []
        message = (
            "Explicit GPU device selection "
            f"{requested_ids} could not initialize a GPU backend: {detail}"
        )
        if cause is not None:
            raise ConfigurationError(message) from cause
        raise ConfigurationError(message)

    @staticmethod
    def _raise_explicit_ast_configuration_error(
        detail: str,
        cause: Exception | None = None,
    ) -> None:
        message = f"Explicit AST search requires AST dependencies: {detail}"
        if cause is not None:
            raise ConfigurationError(message) from cause
        raise ConfigurationError(message)

    @staticmethod
    def _supports_native_ast_pattern(config: SearchConfig | None) -> bool:
        if config is None:
            return False
        pattern = (config.query_pattern or "").strip()
        if not pattern:
            return False
        if pattern.startswith("("):
            return True
        return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pattern))

    @staticmethod
    def _normalize_device_chunk_plan(
        device_chunk_plan: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        """
        Normalize scheduler-provided (device_id, chunk_mb) plan.
        - Drop non-positive chunk entries.
        - Deduplicate device IDs while preserving first-seen order.
        - Keep the largest chunk size for duplicate devices.
        """
        normalized: list[tuple[int, int]] = []
        index_by_device: dict[int, int] = {}
        for device_id, chunk_mb in device_chunk_plan:
            if chunk_mb <= 0:
                continue
            if device_id not in index_by_device:
                index_by_device[device_id] = len(normalized)
                normalized.append((device_id, chunk_mb))
                continue
            slot = index_by_device[device_id]
            existing_device_id, existing_chunk_mb = normalized[slot]
            if chunk_mb > existing_chunk_mb:
                normalized[slot] = (existing_device_id, chunk_mb)
        return normalized

    @staticmethod
    def _needs_python_cpu(config: SearchConfig | None) -> bool:
        if config is None:
            return False
        return bool(
            config.context
            or config.before_context
            or config.after_context
            or config.line_regexp
            or config.word_regexp
            or config.ltl
        )

    @staticmethod
    def _is_complex_regex(config: SearchConfig | None) -> bool:
        if config is None or config.fixed_strings:
            return False
        pattern = (config.query_pattern or "").strip()
        if not pattern:
            return False
        # Heuristic: treat dense metacharacter patterns as complex regex workloads.
        metachar_count = sum(1 for ch in pattern if ch in r".*+?[](){}|\\")
        return metachar_count >= 3 or len(pattern) >= 32

    @classmethod
    def _should_try_gpu(cls, config: SearchConfig | None, needs_python_cpu: bool) -> bool:
        if config is None:
            return False
        if config.ast or config.count or config.fixed_strings:
            return False
        if needs_python_cpu:
            return False
        large_input = config.input_total_bytes >= 256 * 1024 * 1024
        return large_input and cls._is_complex_regex(config)

    @staticmethod
    def _should_honor_explicit_gpu_ids(config: SearchConfig | None, needs_python_cpu: bool) -> bool:
        if config is None:
            return False
        if not config.gpu_device_ids:
            return False
        if config.ast or config.count or config.fixed_strings:
            return False
        if needs_python_cpu:
            return False
        return True

    @staticmethod
    def _detect_query_type(config: SearchConfig | None) -> QueryType:
        if config is None or not config.query_pattern:
            return QueryType.FAST
        return QueryAnalyzer().analyze(config.query_pattern).query_type

    def __init__(self, force_cpu: bool = False, config: SearchConfig | None = None):
        self.backend: ComputeBackend
        self.config = config
        selected_backend_name = "unknown"
        selected_backend_reason = "unknown"
        selected_gpu_device_ids: list[int] = []
        selected_gpu_chunk_plan_mb: list[tuple[int, int]] = []
        span_ctx: Any = nullcontext()

        try:
            from opentelemetry import trace

            tracer = trace.get_tracer(__name__)
            span_ctx = tracer.start_as_current_span("pipeline.select_backend")
        except ImportError:
            pass

        with span_ctx as span:
            if span is not None:
                span.set_attribute("force_cpu", force_cpu)
                span.set_attribute("config.ast", bool(config and config.ast))
                span.set_attribute("config.count", bool(config and config.count))
                span.set_attribute("config.fixed_strings", bool(config and config.fixed_strings))

            # The rust backend is our fallback now because it's 30x faster than pure python for counts/simple strings
            rust_backend = RustCoreBackend()

            # Native ripgrep backend for standard regex parsing (if installed)
            rg_backend = RipgrepBackend()

            # StringZilla backend for ultra-fast fixed string SIMD matching
            sz_backend = StringZillaBackend()

            needs_python_cpu = self._needs_python_cpu(config)
            should_try_gpu = self._should_try_gpu(config, needs_python_cpu)
            explicit_gpu_requested = self._should_honor_explicit_gpu_ids(config, needs_python_cpu)
            query_type = self._detect_query_type(config)
            rg_available = rg_backend.is_available()
            rust_available = rust_backend.is_available()

            if rg_available:
                fallback_backend: ComputeBackend = rg_backend
            elif needs_python_cpu or not rust_available:
                fallback_backend = CPUBackend()
            else:
                fallback_backend = rust_backend

            if config and config.pcre2:
                if rg_available:
                    self.backend = rg_backend
                    selected_backend_reason = "pcre2_explicit_ripgrep"
                else:
                    raise ConfigurationError(
                        "PCRE2 requested but 'rg' binary is not available. "
                        "Please install ripgrep with PCRE2 support."
                    )
            elif force_cpu:
                if rust_available and not needs_python_cpu:
                    self.backend = rust_backend
                    selected_backend_reason = "force_cpu_rust"
                else:
                    self.backend = CPUBackend()
                    selected_backend_reason = "force_cpu_python_cpu"
            elif config and config.ast:
                try:
                    from tensor_grep.backends.ast_backend import AstBackend
                    from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend

                    ast_backend = AstBackend()
                    ast_wrapper = AstGrepWrapperBackend()
                    if (
                        config.ast_prefer_native
                        and self._supports_native_ast_pattern(config)
                        and ast_backend.is_available()
                    ):
                        self.backend = ast_backend
                        selected_backend_reason = "ast_backend_available"
                    elif ast_wrapper.is_available():
                        self.backend = ast_wrapper
                        selected_backend_reason = "ast_wrapper_available"
                    elif ast_backend.is_available():
                        self.backend = ast_backend
                        selected_backend_reason = "ast_backend_available_fallback"
                    else:
                        self._raise_explicit_ast_configuration_error("no AST backend is available")
                except ImportError as exc:
                    self._raise_explicit_ast_configuration_error(
                        "backend imports failed",
                        cause=exc,
                    )
            elif query_type is QueryType.NLP:
                from tensor_grep.backends.cybert_backend import CybertBackend

                cybert_backend = CybertBackend()
                if cybert_backend.is_available():
                    self.backend = cybert_backend
                    selected_backend_reason = "nlp_cybert"
                else:
                    self.backend = fallback_backend
                    selected_backend_reason = "nlp_backend_unavailable_fallback"
            elif config and config.count and rust_available:
                # For pure counting, our Rust backend beats rg and everything else
                self.backend = rust_backend
                selected_backend_reason = "count_rust_fast_path"
            elif (
                config
                and config.fixed_strings
                and sz_backend.is_available()
                and not needs_python_cpu
            ):
                # For literal string searches without context boundaries, StringZilla's SIMD destroys C
                self.backend = sz_backend
                selected_backend_reason = "fixed_strings_stringzilla_fast_path"
            elif config and (
                config.context
                or config.before_context
                or config.after_context
                or config.line_regexp
                or config.word_regexp
                or config.ltl
            ):
                # Use native rg for supported semantics when available; keep Python for LTL or no-rg fallback.
                if config.ltl or not rg_available:
                    self.backend = CPUBackend()
                    selected_backend_reason = "python_cpu_semantics_required"
                else:
                    self.backend = rg_backend
                    selected_backend_reason = "rg_semantics_fast_path"
            elif explicit_gpu_requested:
                # Explicit per-request GPU routing override.
                # Inject memory manager to get chunk sizes across selected/routable GPUs.
                memory_manager = MemoryManager()
                preferred_gpu_ids = config.gpu_device_ids if config else None
                device_chunk_plan = self._normalize_device_chunk_plan(
                    memory_manager.get_device_chunk_plan_mb(preferred_ids=preferred_gpu_ids)
                )
                chunk_sizes = [chunk_mb for _, chunk_mb in device_chunk_plan]
                device_ids = [device_id for device_id, _ in device_chunk_plan]
                selected_gpu_chunk_plan_mb = list(device_chunk_plan)

                # If no chunk sizes were returned but we didn't force CPU, something is wrong with CUDA, fallback
                if not chunk_sizes:
                    self._raise_explicit_gpu_configuration_error(
                        config,
                        "no routable GPU chunk plan was available",
                    )
                else:
                    cudf_backend = CuDFBackend(chunk_sizes_mb=chunk_sizes, device_ids=device_ids)
                    if cudf_backend.is_available():
                        self.backend = cudf_backend
                        selected_backend_reason = "gpu_explicit_ids_cudf"
                        selected_gpu_device_ids = list(device_ids)
                    else:
                        try:
                            from tensor_grep.backends.torch_backend import TorchBackend

                            torch_backend = TorchBackend(
                                device_ids=device_ids,
                                chunk_sizes_mb=chunk_sizes,
                            )
                            if torch_backend.is_available():
                                self.backend = torch_backend
                                selected_backend_reason = "gpu_explicit_ids_torch"
                                selected_gpu_device_ids = list(device_ids)
                            else:
                                self._raise_explicit_gpu_configuration_error(
                                    config,
                                    "CuDF and Torch GPU backends were unavailable",
                                )
                        except ImportError as exc:
                            self._raise_explicit_gpu_configuration_error(
                                config,
                                "Torch backend imports failed after CuDF was unavailable",
                                cause=exc,
                            )
            elif rg_available:
                # Default search path: always delegate to native rg for best end-to-end CLI speed.
                self.backend = rg_backend
                selected_backend_reason = "rg_default_fast_path"
            elif should_try_gpu:
                # Heuristic GPU path for large/complex regex when rg is unavailable.
                memory_manager = MemoryManager()
                preferred_gpu_ids = config.gpu_device_ids if config else None
                device_chunk_plan = self._normalize_device_chunk_plan(
                    memory_manager.get_device_chunk_plan_mb(preferred_ids=preferred_gpu_ids)
                )
                chunk_sizes = [chunk_mb for _, chunk_mb in device_chunk_plan]
                device_ids = [device_id for device_id, _ in device_chunk_plan]
                selected_gpu_chunk_plan_mb = list(device_chunk_plan)

                if not chunk_sizes:
                    self.backend = fallback_backend
                    selected_backend_reason = "gpu_selected_no_chunk_sizes_fallback"
                else:
                    cudf_backend = CuDFBackend(chunk_sizes_mb=chunk_sizes, device_ids=device_ids)
                    if cudf_backend.is_available():
                        self.backend = cudf_backend
                        selected_backend_reason = "gpu_heuristic_cudf"
                        selected_gpu_device_ids = list(device_ids)
                    else:
                        try:
                            from tensor_grep.backends.torch_backend import TorchBackend

                            torch_backend = TorchBackend(
                                device_ids=device_ids,
                                chunk_sizes_mb=chunk_sizes,
                            )
                            if torch_backend.is_available():
                                self.backend = torch_backend
                                selected_backend_reason = "gpu_heuristic_torch"
                                selected_gpu_device_ids = list(device_ids)
                            else:
                                self.backend = fallback_backend
                                selected_backend_reason = "gpu_heuristic_no_gpu_backend_fallback"
                        except ImportError:
                            self.backend = fallback_backend
                            selected_backend_reason = "gpu_heuristic_torch_import_error_fallback"
            elif rust_available and not needs_python_cpu:
                # Secondary fast path when rg is unavailable and GPU heuristics do not match.
                self.backend = rust_backend
                selected_backend_reason = "rust_secondary_fast_path"
            else:
                self.backend = fallback_backend
                selected_backend_reason = "fallback_backend"

            selected_backend_name = type(self.backend).__name__
            if span is not None:
                span.set_attribute("backend.selected", selected_backend_name)
                span.set_attribute("backend.reason", selected_backend_reason)
                span.set_attribute("needs_python_cpu", needs_python_cpu)
                span.set_attribute("should_try_gpu", should_try_gpu)
                span.set_attribute(
                    "backend.gpu_device_ids", ",".join(map(str, selected_gpu_device_ids))
                )
                span.set_attribute(
                    "backend.gpu_chunk_plan_mb",
                    ",".join(
                        f"{device_id}:{chunk_mb}"
                        for device_id, chunk_mb in selected_gpu_chunk_plan_mb
                    ),
                )

        self.selected_backend_name = selected_backend_name
        self.selected_backend_reason = selected_backend_reason
        self.selected_gpu_device_ids = selected_gpu_device_ids
        self.selected_gpu_chunk_plan_mb = selected_gpu_chunk_plan_mb

    def get_backend(self) -> ComputeBackend:
        return self.backend
