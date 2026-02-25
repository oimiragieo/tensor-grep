from tensor_grep.backends.base import ComputeBackend
from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.backends.cudf_backend import CuDFBackend
from tensor_grep.gpu.memory_manager import MemoryManager

class Pipeline:
    def __init__(self, force_cpu: bool = False):
        self.backend: ComputeBackend
        
        if force_cpu:
            self.backend = CPUBackend()
        else:
            # Inject memory manager to get chunk sizes across all available GPUs
            memory_manager = MemoryManager()
            chunk_sizes = memory_manager.get_all_device_chunk_sizes_mb()
            
            # If no chunk sizes were returned but we didn't force CPU, something is wrong with CUDA, fallback
            if not chunk_sizes:
                self.backend = CPUBackend()
            else:
                cudf_backend = CuDFBackend(chunk_sizes_mb=chunk_sizes)
                if cudf_backend.is_available():
                    self.backend = cudf_backend
                else:
                    self.backend = CPUBackend()

    def get_backend(self) -> ComputeBackend:
        return self.backend
