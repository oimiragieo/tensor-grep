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

        from tensor_grep.backends.cudf_backend import CuDFBackend
        backend = CuDFBackend()
        backend.search(str(sample_log_file), "ERROR")

        cudf.read_text.assert_called_once()

    def test_should_use_byte_range_for_large_files(self, tmp_path):
        from tensor_grep.backends.cudf_backend import CuDFBackend
        backend = CuDFBackend(chunk_sizes_mb=[256])
        assert backend.chunk_sizes_mb == [256]

    @patch.dict("sys.modules", {"cudf": MagicMock()})
    @patch("os.path.getsize", return_value=1024)
    def test_should_use_str_contains_for_regex(self, mock_getsize):
        import cudf
        mock_series = MagicMock()
        cudf.read_text.return_value = mock_series

        from tensor_grep.backends.cudf_backend import CuDFBackend
        backend = CuDFBackend()
        backend.search("test.log", r"ERROR.*timeout")

        mock_series.str.contains.assert_called_once()
        
    @patch.dict("sys.modules", {"cudf": MagicMock(), "rmm": MagicMock(), "re": MagicMock()})
    @patch("os.path.getsize", return_value=1024 * 1024 * 10) # 10 MB file
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    def test_should_shardDataAcrossGPUs_when_multiGpuDetected(self, mock_pool, mock_getsize):
        from tensor_grep.backends.cudf_backend import CuDFBackend
        # Setup backend with 2 GPUs, each gets a 2MB chunk capacity
        backend = CuDFBackend(chunk_sizes_mb=[2, 2])
        
        # We mock the executor to just run the function synchronously to test mapping
        mock_executor = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_executor
        
        # Ensure that as_completed returns something iterable but empty for now
        with patch("tensor_grep.backends.cudf_backend.as_completed", return_value=[]):
            backend.search("test.log", "ERROR")
        
        # It should have mapped chunks across the pool
        mock_executor.submit.assert_called()
