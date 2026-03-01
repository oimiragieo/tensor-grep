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

        # We need to make the rust_core import fail specifically within this test
        with patch.dict("sys.modules", {"tensor_grep.rust_core": None}):
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

    @patch.dict("sys.modules", {"cudf": MagicMock()})
    @patch("os.path.getsize", return_value=1024)
    def test_should_ignoreCase_when_usingCudfBackend(self, mock_getsize):
        import re

        import cudf

        mock_series = MagicMock()
        cudf.read_text.return_value = mock_series

        from tensor_grep.backends.cudf_backend import CuDFBackend
        from tensor_grep.core.config import SearchConfig

        backend = CuDFBackend()
        config = SearchConfig(ignore_case=True)
        backend.search("test.log", r"error", config=config)

        mock_series.str.contains.assert_called_with(r"error", regex=True, flags=re.IGNORECASE)

    @patch.dict("sys.modules", {"cudf": MagicMock()})
    @patch("os.path.getsize", return_value=1024)
    def test_should_invertMatch_when_usingCudfBackend(self, mock_getsize):
        import cudf

        mock_series = MagicMock()

        # We need to mock the ~ operator for the mask
        mock_mask = MagicMock()
        mock_inverted_mask = MagicMock()
        mock_mask.__invert__.return_value = mock_inverted_mask
        mock_series.str.contains.return_value = mock_mask
        cudf.read_text.return_value = mock_series

        from tensor_grep.backends.cudf_backend import CuDFBackend
        from tensor_grep.core.config import SearchConfig

        backend = CuDFBackend()
        config = SearchConfig(invert_match=True)
        backend.search("test.log", r"error", config=config)

        mock_series.__getitem__.assert_called_with(mock_inverted_mask)

    @patch.dict("sys.modules", {"cudf": MagicMock(), "rmm": MagicMock(), "re": MagicMock()})
    @patch("os.path.getsize", return_value=1024 * 1024 * 10)  # 10 MB file
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

    @patch("os.path.getsize", return_value=1024 * 1024 * 10)
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    def test_should_return_after_chunked_path_without_process_pool(self, mock_pool, mock_getsize):
        import types

        from tensor_grep.backends.cudf_backend import CuDFBackend

        backend = CuDFBackend(chunk_sizes_mb=[1])

        mock_series = MagicMock()
        mock_series.__len__.return_value = 1
        mock_mask = MagicMock()
        mock_series.str.contains.return_value = mock_mask

        mock_matched = MagicMock()
        mock_matched.index.to_pandas.return_value = [0]
        mock_matched.to_pandas.return_value = ["ERROR line"]
        mock_series.__getitem__.return_value = mock_matched

        cudf_mod = types.ModuleType("cudf")
        cudf_mod.Series = MagicMock()
        cudf_mod.Series.from_arrow.return_value = mock_series
        cudf_mod.core = types.SimpleNamespace(
            buffer=types.SimpleNamespace(acquire_spill_lock=MagicMock())
        )

        pa_mod = types.ModuleType("pyarrow")
        pa_mod.array = MagicMock(return_value=object())

        rust_mod = types.ModuleType("tensor_grep.rust_core")
        rust_mod.read_mmap_to_arrow_chunked = MagicMock(return_value=[object()])

        mem_mgr_mod = types.ModuleType("tensor_grep.core.hardware.memory_manager")
        mem_mgr_mod.MemoryManager = MagicMock()
        mem_mgr_mod.MemoryManager.return_value.get_vram_budget_mb.return_value = 1

        with patch.dict(
            "sys.modules",
            {
                "cudf": cudf_mod,
                "pyarrow": pa_mod,
                "tensor_grep.rust_core": rust_mod,
                "tensor_grep.core.hardware.memory_manager": mem_mgr_mod,
            },
        ):
            result = backend.search("test.log", "ERROR")

        mock_pool.assert_not_called()
        assert result.total_matches == 1
