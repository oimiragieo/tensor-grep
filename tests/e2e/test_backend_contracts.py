from tensor_grep.backends.base import ComputeBackend
from tensor_grep.backends.cpu_backend import CPUBackend


class TestBackendContract:
    """Every ComputeBackend must satisfy these contracts."""

    def _check_contract(self, backend: ComputeBackend, file_path, pattern):
        result = backend.search(str(file_path), pattern)
        assert hasattr(result, "matches")
        assert hasattr(result, "total_matches")
        assert hasattr(result, "is_empty")

    def test_cpu_backend_satisfies_contract(self, sample_log_file):
        self._check_contract(CPUBackend(), sample_log_file, "ERROR")

    def test_cudf_backend_satisfies_contract(self, sample_log_file):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        backend = CuDFBackend()
        if not backend.is_available():
            import pytest

            pytest.skip("cuDF not available or insufficient driver")
        self._check_contract(backend, sample_log_file, "ERROR")
