from cudf_grep.io.base import IOBackend
from cudf_grep.io.reader_fallback import FallbackReader

class TestIOContract:
    """Every IOBackend must satisfy these contracts."""
    def _check_contract(self, reader: IOBackend, file_path):
        lines = list(reader.read_lines(str(file_path)))
        assert len(lines) > 0
        assert all(isinstance(line, str) for line in lines)

    def test_fallback_satisfies_contract(self, sample_log_file):
        self._check_contract(FallbackReader(), sample_log_file)
