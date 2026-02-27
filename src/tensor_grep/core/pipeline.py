from tensor_grep.backends.base import ComputeBackend
from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.backends.cudf_backend import CuDFBackend
from tensor_grep.backends.rust_backend import RustCoreBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.hardware.memory_manager import MemoryManager


class Pipeline:
    def __init__(self, force_cpu: bool = False, config: SearchConfig | None = None):
        self.backend: ComputeBackend
        self.config = config

        # The rust backend is our fallback now because it's 30x faster than pure python
        rust_backend = RustCoreBackend()

        # Check if config has complex flags that the Rust core doesn't support yet
        needs_python_cpu = False
        if config:
            if (
                config.invert_match
                or config.context
                or config.before_context
                or config.after_context
            ):
                needs_python_cpu = True
            if config.line_regexp or config.word_regexp:
                needs_python_cpu = True

        if needs_python_cpu or not rust_backend.is_available():
            fallback_backend: ComputeBackend = CPUBackend()
        else:
            fallback_backend = rust_backend

        if force_cpu:
            self.backend = fallback_backend
        elif config and (
            config.context
            or config.before_context
            or config.after_context
            or config.line_regexp
            or config.word_regexp
        ):
            # Complex flags currently require the pure python CPU backend to handle line queues and boundaries perfectly
            self.backend = CPUBackend()
        elif config and config.ast:
            try:
                from tensor_grep.backends.ast_backend import AstBackend

                ast_backend = AstBackend()
                if ast_backend.is_available():
                    self.backend = ast_backend
                else:
                    self.backend = fallback_backend
            except ImportError:
                self.backend = fallback_backend
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

    def get_backend(self) -> ComputeBackend:
        return self.backend
