from cudf_grep.backends.base import ComputeBackend
from cudf_grep.backends.cpu_backend import CPUBackend
from cudf_grep.backends.cudf_backend import CuDFBackend

class Pipeline:
    def __init__(self, force_cpu: bool = False):
        self.backend: ComputeBackend
        
        if force_cpu:
            self.backend = CPUBackend()
        else:
            cudf_backend = CuDFBackend()
            if cudf_backend.is_available():
                self.backend = cudf_backend
            else:
                self.backend = CPUBackend()

    def get_backend(self) -> ComputeBackend:
        return self.backend
