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


class Pipeline:
    @staticmethod
    def _needs_python_cpu(config: SearchConfig | None) -> bool:
        if config is None:
            return False
        return bool(
            config.invert_match
            or config.context
            or config.before_context
            or config.after_context
            or config.line_regexp
            or config.word_regexp
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

    def __init__(self, force_cpu: bool = False, config: SearchConfig | None = None):
        self.backend: ComputeBackend
        self.config = config
        selected_backend_name = "unknown"
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
            rg_available = rg_backend.is_available()
            rust_available = rust_backend.is_available()

            if rg_available:
                fallback_backend: ComputeBackend = rg_backend
            elif needs_python_cpu or not rust_available:
                fallback_backend = CPUBackend()
            else:
                fallback_backend = rust_backend

            if force_cpu:
                if rust_available and not needs_python_cpu:
                    self.backend = rust_backend
                else:
                    self.backend = CPUBackend()
            elif config and config.ast:
                try:
                    from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend

                    ast_wrapper = AstGrepWrapperBackend()

                    # Check for one-off CLI queries, prefer native ast-grep if installed for instant resolution
                    if ast_wrapper.is_available():
                        self.backend = ast_wrapper
                    else:
                        from tensor_grep.backends.ast_backend import AstBackend

                        ast_backend = AstBackend()
                        if ast_backend.is_available():
                            self.backend = ast_backend
                        else:
                            self.backend = fallback_backend
                except ImportError:
                    self.backend = fallback_backend
            elif config and config.count and rust_available:
                # For pure counting, our Rust backend beats rg and everything else
                self.backend = rust_backend
            elif (
                config
                and config.fixed_strings
                and sz_backend.is_available()
                and not needs_python_cpu
            ):
                # For literal string searches without context boundaries, StringZilla's SIMD destroys C
                self.backend = sz_backend
            elif config and (
                config.context
                or config.before_context
                or config.after_context
                or config.line_regexp
                or config.word_regexp
            ):
                # Complex flags require ripgrep or pure python CPU backend
                if rg_available:
                    self.backend = rg_backend
                else:
                    self.backend = CPUBackend()
            elif rg_available and not should_try_gpu:
                # Default search path: delegate to native rg for best end-to-end CLI speed.
                self.backend = rg_backend
            elif rust_available and not should_try_gpu:
                # Secondary fast path when rg is unavailable.
                self.backend = rust_backend
            else:
                # Inject memory manager to get chunk sizes across all available GPUs
                memory_manager = MemoryManager()
                chunk_sizes = memory_manager.get_all_device_chunk_sizes_mb()

                # If no chunk sizes were returned but we didn't force CPU, something is wrong with CUDA, fallback
                if not chunk_sizes:
                    self.backend = fallback_backend
                else:
                    cudf_backend = CuDFBackend(chunk_sizes_mb=chunk_sizes)
                    if cudf_backend.is_available():
                        self.backend = cudf_backend
                    else:
                        try:
                            from tensor_grep.backends.torch_backend import TorchBackend

                            torch_backend = TorchBackend()
                            if torch_backend.is_available():
                                self.backend = torch_backend
                            else:
                                self.backend = fallback_backend
                        except ImportError:
                            self.backend = fallback_backend

            selected_backend_name = type(self.backend).__name__
            if span is not None:
                span.set_attribute("backend.selected", selected_backend_name)
                span.set_attribute("needs_python_cpu", needs_python_cpu)
                span.set_attribute("should_try_gpu", should_try_gpu)

        self.selected_backend_name = selected_backend_name

    def get_backend(self) -> ComputeBackend:
        return self.backend
