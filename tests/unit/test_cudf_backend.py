import os
import sys
import types
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
        assert backend.device_ids == [0]

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
    def test_should_report_zero_total_files_when_single_gpu_path_has_no_matches(self, mock_getsize):
        import cudf

        mock_series = MagicMock()
        mock_series.str.contains.return_value = MagicMock()
        mock_matched = MagicMock()
        mock_matched.index.to_pandas.return_value = []
        mock_matched.to_pandas.return_value = []
        mock_series.__getitem__.return_value = mock_matched
        cudf.read_text.return_value = mock_series

        from tensor_grep.backends.cudf_backend import CuDFBackend

        backend = CuDFBackend()
        with patch.dict("sys.modules", {"tensor_grep.rust_core": None}):
            result = backend.search("test.log", "ERROR")

        assert result.total_matches == 0
        assert result.total_files == 0

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

    @patch.dict("sys.modules", {"cudf": MagicMock(), "rmm": MagicMock(), "re": MagicMock()})
    @patch("os.path.getsize", return_value=1024 * 1024 * 10)  # 10 MB file
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    def test_should_report_zero_total_files_when_distributed_path_has_no_matches(
        self, mock_pool, mock_getsize
    ):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        backend = CuDFBackend(chunk_sizes_mb=[2, 2], device_ids=[3, 7])
        mock_executor = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_executor

        with patch("tensor_grep.backends.cudf_backend.as_completed", return_value=[]):
            result = backend.search("test.log", "ERROR")

        assert result.total_matches == 0
        assert result.total_files == 0

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
        assert result.routing_backend == "CuDFBackend"
        assert result.routing_reason == "cudf_chunked_zero_copy"
        assert result.routing_gpu_device_ids == [0]
        assert result.routing_gpu_chunk_plan_mb == [(0, 1)]
        assert result.routing_distributed is False
        assert result.routing_worker_count == 1

    def test_should_build_execution_plan_with_explicit_device_ids(self):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        plan = CuDFBackend._build_execution_plan(
            file_size=10 * 1024 * 1024,
            device_chunks_mb=[(3, 2), (7, 2)],
        )
        assert plan[0][0] == 3
        assert plan[1][0] == 7
        assert plan[2][0] == 3

    @patch.dict("sys.modules", {"cudf": MagicMock(), "rmm": MagicMock(), "re": MagicMock()})
    @patch("os.path.getsize", return_value=1024 * 1024 * 10)  # 10 MB file
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    def test_should_submit_chunks_using_configured_device_ids(self, mock_pool, mock_getsize):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        backend = CuDFBackend(chunk_sizes_mb=[2, 2], device_ids=[3, 7])
        mock_executor = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_executor

        with patch("tensor_grep.backends.cudf_backend.as_completed", return_value=[]):
            result = backend.search("test.log", "ERROR")

        submitted_device_ids = [call.args[1] for call in mock_executor.submit.call_args_list]
        assert submitted_device_ids[:2] == [3, 7]
        assert result.routing_backend == "CuDFBackend"
        assert result.routing_reason == "cudf_distributed_fanout"
        assert result.routing_gpu_device_ids == [3, 7]
        assert result.routing_gpu_chunk_plan_mb == [(3, 2), (7, 2)]
        assert result.routing_distributed is True
        assert result.routing_worker_count == 2

    @patch.dict("sys.modules", {"cudf": MagicMock(), "rmm": MagicMock(), "re": MagicMock()})
    @patch("os.path.getsize", return_value=1024 * 1024 * 2)
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    def test_should_not_claim_distributed_fanout_when_duplicate_device_ids_collapse_to_one_worker(
        self, mock_pool, mock_getsize
    ):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        backend = CuDFBackend(chunk_sizes_mb=[1, 1], device_ids=[3, 3])
        mock_executor = MagicMock()
        mock_pool.return_value.__enter__.return_value = mock_executor

        with patch(
            "tensor_grep.backends.cudf_backend._process_chunk_on_device",
            return_value=([MagicMock(line_number=1, text="3", file="test.log")], 1),
        ):
            result = backend.search("test.log", "ERROR")

        mock_pool.assert_not_called()
        assert result.routing_reason == "cudf_single_worker_plan"
        assert result.routing_gpu_device_ids == [3]
        assert result.routing_gpu_chunk_plan_mb == [(3, 1)]
        assert result.routing_distributed is False
        assert result.routing_worker_count == 1

    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("tensor_grep.backends.cudf_backend._process_chunk_on_device")
    def test_should_not_spawn_process_pool_when_multi_gpu_plan_has_single_chunk(
        self, mock_process_chunk, mock_pool
    ):
        from tensor_grep.backends.cudf_backend import CuDFBackend
        from tensor_grep.core.result import MatchLine

        backend = CuDFBackend(chunk_sizes_mb=[512, 512], device_ids=[3, 7])
        mock_process_chunk.return_value = (
            [MatchLine(line_number=1, text="ERROR", file="test.log")],
            1,
        )

        matches, worker_count = backend._search_distributed(
            file_path="test.log",
            pattern="ERROR",
            file_size=1 * 1024 * 1024,  # small enough to produce exactly one chunk
            device_chunks_mb=[(3, 512), (7, 512)],
            config=None,
        )

        mock_pool.assert_not_called()
        mock_process_chunk.assert_called_once()
        assert len(matches) == 1
        assert matches[0].line_number == 1
        assert worker_count == 1

    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("tensor_grep.backends.cudf_backend._process_chunk_on_device")
    def test_should_not_spawn_process_pool_when_plan_uses_single_worker_across_many_chunks(
        self, mock_process_chunk, mock_pool
    ):
        from tensor_grep.backends.cudf_backend import CuDFBackend
        from tensor_grep.core.result import MatchLine

        backend = CuDFBackend(chunk_sizes_mb=[1, 1], device_ids=[3, 3])
        # 3 chunks: lines should be offset by returned chunk line counts.
        mock_process_chunk.side_effect = [
            ([MatchLine(line_number=1, text="a", file="test.log")], 2),
            ([MatchLine(line_number=1, text="b", file="test.log")], 2),
            ([MatchLine(line_number=1, text="c", file="test.log")], 2),
        ]

        matches, worker_count = backend._search_distributed(
            file_path="test.log",
            pattern="ERROR",
            file_size=3 * 1024 * 1024,
            device_chunks_mb=[(3, 1), (3, 1)],
            config=None,
        )

        mock_pool.assert_not_called()
        assert [m.line_number for m in matches] == [1, 3, 5]
        assert [m.text for m in matches] == ["a", "b", "c"]
        assert worker_count == 1

    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("tensor_grep.backends.cudf_backend.as_completed", return_value=[])
    def test_should_cap_process_pool_workers_to_execution_plan_size(
        self, _mock_as_completed, mock_pool
    ):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        backend = CuDFBackend(chunk_sizes_mb=[512, 512, 512, 512], device_ids=[0, 1, 2, 3])
        mock_pool.return_value.__enter__.return_value = MagicMock()

        # 700MB file with 512MB chunking => exactly 2 planned tasks
        backend._search_distributed(
            file_path="test.log",
            pattern="ERROR",
            file_size=700 * 1024 * 1024,
            device_chunks_mb=[(0, 512), (1, 512), (2, 512), (3, 512)],
            config=None,
        )

        assert mock_pool.call_args.kwargs["max_workers"] == 2
        assert mock_pool.call_args.kwargs["max_tasks_per_child"] == 1

    @patch("tensor_grep.backends.cudf_backend._process_chunk_on_device")
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("tensor_grep.backends.cudf_backend.as_completed", return_value=[])
    def test_should_deduplicate_duplicate_device_ids_before_worker_sizing(
        self, _mock_as_completed, mock_pool, mock_process_chunk
    ):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        backend = CuDFBackend(chunk_sizes_mb=[256, 512], device_ids=[3, 3])
        mock_pool.return_value.__enter__.return_value = MagicMock()
        mock_process_chunk.return_value = ([], 0)

        backend._search_distributed(
            file_path="test.log",
            pattern="ERROR",
            file_size=700 * 1024 * 1024,
            device_chunks_mb=[(3, 256), (3, 512)],
            config=None,
        )

        mock_pool.assert_not_called()
        assert all(call.args[0] == 3 for call in mock_process_chunk.call_args_list)

    def test_worker_isolation_sets_cuda_visible_devices_before_worker_imports(
        self, monkeypatch
    ):
        from tensor_grep.backends import cudf_backend

        observed_env: dict[str, str | None] = {}
        fake_series = MagicMock()
        fake_series.str.contains.return_value = MagicMock()
        fake_matched = MagicMock()
        fake_matched.index.to_pandas.return_value = []
        fake_matched.to_pandas.return_value = []
        fake_series.__getitem__.return_value = fake_matched

        fake_cudf = types.SimpleNamespace(read_text=MagicMock(return_value=fake_series))
        fake_rmm = types.SimpleNamespace(reinitialize=MagicMock())

        real_import = __import__

        def import_with_env_capture(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "cudf":
                observed_env["cudf"] = os.environ.get("CUDA_VISIBLE_DEVICES")
                return fake_cudf
            if name == "rmm":
                observed_env["rmm"] = os.environ.get("CUDA_VISIBLE_DEVICES")
                return fake_rmm
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
        monkeypatch.delenv("CUDA_DEVICE_ORDER", raising=False)
        monkeypatch.setattr("builtins.__import__", import_with_env_capture)
        monkeypatch.delitem(sys.modules, "cudf", raising=False)
        monkeypatch.delitem(sys.modules, "rmm", raising=False)

        cudf_backend._process_chunk_on_device(
            7,
            "test.log",
            0,
            1024,
            "ERROR",
        )

        assert observed_env == {"cudf": "7", "rmm": "7"}
        fake_rmm.reinitialize.assert_called_once_with(devices=[0])

    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("tensor_grep.backends.cudf_backend.as_completed", return_value=[])
    def test_worker_isolation_uses_fresh_process_pool_children_on_windows(
        self, _mock_as_completed, mock_pool
    ):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        backend = CuDFBackend(chunk_sizes_mb=[512, 512, 512, 512], device_ids=[0, 1, 2, 3])
        mock_pool.return_value.__enter__.return_value = MagicMock()

        backend._search_distributed(
            file_path="test.log",
            pattern="ERROR",
            file_size=700 * 1024 * 1024,
            device_chunks_mb=[(0, 512), (1, 512), (2, 512), (3, 512)],
            config=None,
        )

        assert mock_pool.call_args.kwargs["max_workers"] == 2
        assert mock_pool.call_args.kwargs["max_tasks_per_child"] == 1
