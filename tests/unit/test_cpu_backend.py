from cudf_grep.backends.cpu_backend import CPUBackend

class TestCPUBackend:
    def test_should_find_simple_pattern(self, sample_log_file):
        backend = CPUBackend()
        result = backend.search(str(sample_log_file), "ERROR")
        assert result.total_matches == 2

    def test_should_return_empty_for_no_match(self, sample_log_file):
        backend = CPUBackend()
        result = backend.search(str(sample_log_file), "NONEXISTENT")
        assert result.is_empty is True
