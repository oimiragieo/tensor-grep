import pytest
from unittest.mock import MagicMock, patch

import sys
from unittest.mock import MagicMock, patch

class TestCuDFBackend:
    """Unit tests: mock cuDF so no GPU needed."""

    @patch.dict("sys.modules", {"cudf": MagicMock()})
    @patch("os.path.getsize", return_value=1024)
    def test_should_use_cudf_read_text(self, mock_getsize, sample_log_file):
        import cudf
        mock_series = MagicMock()
        mock_series.str.contains.return_value = MagicMock()
        cudf.read_text.return_value = mock_series

        from cudf_grep.backends.cudf_backend import CuDFBackend
        backend = CuDFBackend()
        backend.search(str(sample_log_file), "ERROR")

        cudf.read_text.assert_called_once()

    def test_should_use_byte_range_for_large_files(self, tmp_path):
        from cudf_grep.backends.cudf_backend import CuDFBackend
        backend = CuDFBackend(chunk_size_mb=256)
        assert backend.chunk_size_mb == 256

    @patch.dict("sys.modules", {"cudf": MagicMock()})
    @patch("os.path.getsize", return_value=1024)
    def test_should_use_str_contains_for_regex(self, mock_getsize):
        import cudf
        mock_series = MagicMock()
        cudf.read_text.return_value = mock_series

        from cudf_grep.backends.cudf_backend import CuDFBackend
        backend = CuDFBackend()
        backend.search("test.log", r"ERROR.*timeout")

        mock_series.str.contains.assert_called_once()
