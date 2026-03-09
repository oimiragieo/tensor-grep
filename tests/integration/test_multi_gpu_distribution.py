from typing import ClassVar
from unittest.mock import MagicMock, patch

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine


class _FakeFuture:
    def __init__(self, result):
        self._result = result

    def result(self):
        return self._result


class _TorchFuture:
    def __init__(self, result):
        self._result = result

    def result(self):
        return self._result


class _TorchExecutor:
    submitted_devices: ClassVar[list[str]] = []

    def __init__(self, *args, **kwargs):
        _ = (args, kwargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False

    def submit(self, fn, **kwargs):
        _TorchExecutor.submitted_devices.append(str(kwargs["device"]))
        return _TorchFuture(fn(**kwargs))


class _FakeTorchModule:
    uint8 = "uint8"

    @staticmethod
    def device(value: str):
        return value

    @staticmethod
    def tensor(values, dtype=None, device=None):
        _ = (values, dtype, device)
        return object()


class _FakeExecutor:
    def __init__(self):
        self.submitted_device_ids: list[int] = []
        self.futures: list[_FakeFuture] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, _fn, device_id, file_path, _offset, _size, _pattern, _config):
        self.submitted_device_ids.append(device_id)
        # Each chunk contributes exactly 3 lines for deterministic offset testing.
        future = _FakeFuture(([MatchLine(line_number=1, text=str(device_id), file=file_path)], 3))
        self.futures.append(future)
        return future


class TestMultiGpuDistributionIntegration:
    @patch("tensor_grep.backends.cudf_backend.as_completed")
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("os.path.getsize", return_value=4 * 1024 * 1024)
    def test_should_prefer_distributed_execution_for_multi_gpu_even_when_chunked_reader_exists(
        self, _mock_getsize, mock_pool, mock_as_completed
    ):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        fake_executor = _FakeExecutor()
        mock_pool.return_value.__enter__.return_value = fake_executor
        mock_as_completed.side_effect = lambda futures: list(reversed(futures))

        with patch.dict(
            "sys.modules",
            {
                "cudf": MagicMock(),
                "rmm": MagicMock(),
                "pyarrow": MagicMock(),
                "tensor_grep.rust_core": MagicMock(),
            },
        ):
            backend = CuDFBackend(chunk_sizes_mb=[1, 1], device_ids=[3, 7])
            result = backend.search("test.log", "ERROR")

        # Multi-device should use distributed ProcessPool path as first-class runtime execution.
        assert mock_pool.called is True
        assert fake_executor.submitted_device_ids[:2] == [3, 7]
        assert result.total_matches == len(result.matches)
        assert result.routing_distributed is True
        assert result.routing_worker_count == 2

    @patch("tensor_grep.backends.cudf_backend.as_completed")
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("os.path.getsize", return_value=4 * 1024 * 1024)
    @patch("tensor_grep.backends.cudf_backend.CuDFBackend.is_available", return_value=True)
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    def test_should_fanout_multi_gpu_through_pipeline_backend_execution(
        self,
        mock_memory,
        mock_rust,
        mock_rg,
        _mock_cudf_available,
        _mock_getsize,
        mock_pool,
        mock_as_completed,
    ):
        from tensor_grep.core.pipeline import Pipeline

        fake_executor = _FakeExecutor()
        mock_pool.return_value.__enter__.return_value = fake_executor
        mock_as_completed.side_effect = lambda futures: list(reversed(futures))

        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = False
        mock_memory.return_value.get_device_chunk_plan_mb.return_value = [(3, 1), (7, 1)]

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            input_total_bytes=512 * 1024 * 1024,
        )
        pipeline = Pipeline(force_cpu=False, config=config)
        with patch.dict(
            "sys.modules",
            {"cudf": MagicMock(), "rmm": MagicMock(), "tensor_grep.rust_core": None},
        ):
            result = pipeline.get_backend().search("test.log", "ERROR")

        assert pipeline.selected_backend_reason == "gpu_heuristic_cudf"
        assert fake_executor.submitted_device_ids[:2] == [3, 7]
        assert result.total_matches == len(result.matches)
        assert [m.line_number for m in result.matches][:2] == [1, 4]
        assert [m.text for m in result.matches][:2] == ["3", "7"]
        assert result.routing_distributed is True
        assert result.routing_worker_count == 2

    @patch("tensor_grep.backends.cudf_backend.as_completed")
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("os.path.getsize", return_value=4 * 1024 * 1024)
    @patch("tensor_grep.backends.cudf_backend.CuDFBackend.is_available", return_value=True)
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    def test_should_fanout_using_preferred_gpu_ids_from_search_config(
        self,
        mock_memory,
        mock_rust,
        mock_rg,
        _mock_cudf_available,
        _mock_getsize,
        mock_pool,
        mock_as_completed,
    ):
        from tensor_grep.core.pipeline import Pipeline

        fake_executor = _FakeExecutor()
        mock_pool.return_value.__enter__.return_value = fake_executor
        mock_as_completed.side_effect = lambda futures: list(reversed(futures))

        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = False
        mock_memory.return_value.get_device_chunk_plan_mb.return_value = [(7, 1), (3, 1)]

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            input_total_bytes=512 * 1024 * 1024,
            gpu_device_ids=[7, 3],
        )
        pipeline = Pipeline(force_cpu=False, config=config)
        with patch.dict(
            "sys.modules",
            {"cudf": MagicMock(), "rmm": MagicMock(), "tensor_grep.rust_core": None},
        ):
            result = pipeline.get_backend().search("test.log", "ERROR")

        assert pipeline.selected_backend_reason == "gpu_explicit_ids_cudf"
        mock_memory.return_value.get_device_chunk_plan_mb.assert_called_once_with(
            preferred_ids=[7, 3]
        )
        assert fake_executor.submitted_device_ids[:2] == [7, 3]
        assert result.total_matches == len(result.matches)
        assert result.routing_distributed is True
        assert result.routing_worker_count == 2

    @patch("tensor_grep.backends.cudf_backend.as_completed")
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("os.path.getsize", return_value=4 * 1024 * 1024)
    @patch("tensor_grep.backends.cudf_backend.CuDFBackend.is_available", return_value=True)
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    def test_should_use_explicit_gpu_ids_as_first_class_pipeline_signal_even_when_rg_exists(
        self,
        mock_memory,
        mock_rust,
        mock_rg,
        _mock_cudf_available,
        _mock_getsize,
        mock_pool,
        mock_as_completed,
    ):
        from tensor_grep.core.pipeline import Pipeline

        fake_executor = _FakeExecutor()
        mock_pool.return_value.__enter__.return_value = fake_executor
        mock_as_completed.side_effect = lambda futures: list(reversed(futures))

        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_memory.return_value.get_device_chunk_plan_mb.return_value = [(7, 1), (3, 1)]

        config = SearchConfig(
            query_pattern="ERROR",
            input_total_bytes=8 * 1024 * 1024,
            gpu_device_ids=[7, 3],
        )
        pipeline = Pipeline(force_cpu=False, config=config)
        with patch.dict(
            "sys.modules",
            {"cudf": MagicMock(), "rmm": MagicMock(), "tensor_grep.rust_core": None},
        ):
            result = pipeline.get_backend().search("test.log", "ERROR")

        assert pipeline.selected_backend_reason == "gpu_explicit_ids_cudf"
        assert fake_executor.submitted_device_ids[:2] == [7, 3]
        assert result.total_matches == len(result.matches)
        assert result.routing_distributed is True
        assert result.routing_worker_count == 2

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_pass_device_ids_from_pipeline_to_cudf_backend(
        self, mock_cudf, mock_memory, mock_rust, mock_rg
    ):
        from tensor_grep.core.pipeline import Pipeline

        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = False
        mock_memory.return_value.get_device_chunk_plan_mb.return_value = [(3, 256), (7, 256)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            input_total_bytes=512 * 1024 * 1024,
        )
        pipeline = Pipeline(force_cpu=False, config=config)

        assert pipeline.backend == mock_cudf.return_value
        mock_cudf.assert_called_once_with(chunk_sizes_mb=[256, 256], device_ids=[3, 7])

    @patch("tensor_grep.backends.cudf_backend.as_completed")
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("os.path.getsize", return_value=4 * 1024 * 1024)
    def test_should_distribute_chunks_across_devices_and_preserve_line_offsets(
        self, _mock_getsize, mock_pool, mock_as_completed
    ):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        fake_executor = _FakeExecutor()
        mock_pool.return_value.__enter__.return_value = fake_executor

        # Return futures out-of-order to verify ordered aggregation logic.
        mock_as_completed.side_effect = lambda futures: list(reversed(futures))

        with patch.dict(
            "sys.modules",
            {"cudf": MagicMock(), "rmm": MagicMock(), "tensor_grep.rust_core": None},
        ):
            backend = CuDFBackend(chunk_sizes_mb=[1, 1], device_ids=[3, 7])
            result = backend.search("test.log", "ERROR")

        assert fake_executor.submitted_device_ids[:2] == [3, 7]
        assert result.total_matches == len(result.matches)
        # 2 chunks at 1 line each with 3-line chunk offsets => line 1 then line 4.
        assert [m.line_number for m in result.matches][:2] == [1, 4]
        assert [m.text for m in result.matches][:2] == ["3", "7"]
        assert result.routing_distributed is True
        assert result.routing_worker_count == 2

    @patch("tensor_grep.backends.cudf_backend._process_chunk_on_device")
    @patch("tensor_grep.backends.cudf_backend.as_completed")
    @patch("tensor_grep.backends.cudf_backend.ProcessPoolExecutor")
    @patch("os.path.getsize", return_value=4 * 1024 * 1024)
    def test_should_collapse_duplicate_device_ids_to_single_worker_in_distributed_path(
        self, _mock_getsize, mock_pool, mock_as_completed, mock_process_chunk
    ):
        from tensor_grep.backends.cudf_backend import CuDFBackend

        fake_executor = _FakeExecutor()
        mock_pool.return_value.__enter__.return_value = fake_executor
        mock_as_completed.side_effect = lambda futures: list(reversed(futures))
        mock_process_chunk.return_value = ([MatchLine(line_number=1, text="3", file="test.log")], 3)

        with patch.dict(
            "sys.modules",
            {"cudf": MagicMock(), "rmm": MagicMock(), "tensor_grep.rust_core": None},
        ):
            backend = CuDFBackend(chunk_sizes_mb=[1, 1], device_ids=[3, 3])
            result = backend.search("test.log", "ERROR")

        # Single-worker plans should use the sequential fast path without spawning a process pool.
        mock_pool.assert_not_called()
        assert all(call.args[0] == 3 for call in mock_process_chunk.call_args_list)
        assert result.total_matches == len(result.matches)
        assert result.routing_distributed is False
        assert result.routing_worker_count == 1

    @patch("tensor_grep.backends.cudf_backend.CuDFBackend.is_available", return_value=False)
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.backends.torch_backend.ThreadPoolExecutor", _TorchExecutor)
    @patch("tensor_grep.backends.torch_backend.TorchBackend.is_available", return_value=True)
    @patch(
        "tensor_grep.backends.torch_backend.TorchBackend._contains_literal_torch",
        side_effect=lambda **kwargs: "ERROR" in kwargs["line"],
    )
    def test_should_execute_torch_fanout_across_selected_gpu_ids_via_pipeline(
        self,
        _mock_contains,
        _mock_torch_available,
        mock_memory,
        mock_rust,
        mock_rg,
        _mock_cudf_available,
        tmp_path,
    ):
        from tensor_grep.core.pipeline import Pipeline

        log_path = tmp_path / "torch_pipeline.log"
        log_path.write_text("ERROR A\nINFO B\nERROR C\nWARN D\n", encoding="utf-8")

        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = False
        mock_memory.return_value.get_device_chunk_plan_mb.return_value = [(7, 128), (3, 128)]

        config = SearchConfig(
            query_pattern="ERROR",
            input_total_bytes=8 * 1024 * 1024,
            gpu_device_ids=[7, 3],
        )
        pipeline = Pipeline(force_cpu=False, config=config)
        _TorchExecutor.submitted_devices = []
        with patch.dict("sys.modules", {"torch": _FakeTorchModule()}):
            result = pipeline.get_backend().search(str(log_path), "ERROR")

        assert pipeline.selected_backend_reason == "gpu_explicit_ids_torch"
        assert pipeline.selected_gpu_device_ids == [7, 3]
        assert pipeline.selected_gpu_chunk_plan_mb == [(7, 128), (3, 128)]
        assert _TorchExecutor.submitted_devices == ["cuda:7", "cuda:3"]
        assert result.total_matches == 2
        assert result.routing_backend == "TorchBackend"
        assert result.routing_reason == "torch_multi_gpu_fanout"
        assert result.routing_gpu_device_ids == [7, 3]
        assert result.routing_gpu_chunk_plan_mb == [(7, 128), (3, 128)]
        assert result.routing_distributed is True
        assert result.routing_worker_count == 2

    @patch("tensor_grep.backends.cudf_backend._process_chunk_on_device")
    @patch("tensor_grep.backends.cudf_backend.CuDFBackend.is_available", return_value=True)
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("os.path.getsize", return_value=2 * 1024 * 1024)
    def test_should_report_single_worker_cudf_execution_through_pipeline_when_gpu_plan_collapses(
        self,
        _mock_getsize,
        mock_memory,
        mock_rust,
        mock_rg,
        _mock_cudf_available,
        mock_process_chunk,
    ):
        from tensor_grep.core.pipeline import Pipeline

        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = False
        mock_memory.return_value.get_device_chunk_plan_mb.return_value = [(3, 1), (3, 1)]
        mock_process_chunk.return_value = ([MatchLine(line_number=1, text="3", file="test.log")], 1)

        config = SearchConfig(
            query_pattern="ERROR",
            input_total_bytes=8 * 1024 * 1024,
            gpu_device_ids=[3, 3],
        )
        pipeline = Pipeline(force_cpu=False, config=config)

        with patch.dict(
            "sys.modules",
            {"cudf": MagicMock(), "rmm": MagicMock(), "tensor_grep.rust_core": None},
        ):
            result = pipeline.get_backend().search("test.log", "ERROR")

        assert pipeline.selected_backend_reason == "gpu_explicit_ids_cudf"
        assert pipeline.selected_gpu_device_ids == [3]
        assert pipeline.selected_gpu_chunk_plan_mb == [(3, 1)]
        assert result.routing_backend == "CuDFBackend"
        assert result.routing_reason == "cudf_chunked_single_worker_plan"
        assert result.routing_gpu_device_ids == [3]
        assert result.routing_gpu_chunk_plan_mb == [(3, 1)]
        assert result.routing_distributed is False
        assert result.routing_worker_count == 1

    @patch("tensor_grep.core.pipeline.CuDFBackend")
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.backends.torch_backend.ThreadPoolExecutor", _TorchExecutor)
    @patch("tensor_grep.backends.torch_backend.TorchBackend.is_available", return_value=True)
    def test_should_report_cpu_fallback_through_pipeline_when_selected_torch_plan_hits_regex_path(
        self,
        _mock_torch_available,
        mock_memory,
        mock_rust,
        mock_rg,
        mock_cudf,
        tmp_path,
    ):
        from tensor_grep.core.pipeline import Pipeline

        log_path = tmp_path / "torch_regex_fallback.log"
        log_path.write_text("ERROR timeout 1\nINFO ok\n", encoding="utf-8")

        mock_cudf.return_value.is_available.return_value = False
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = False
        mock_memory.return_value.get_device_chunk_plan_mb.return_value = [(7, 128), (3, 128)]

        config = SearchConfig(
            query_pattern=r"ERROR.*timeout\s+\d+",
            input_total_bytes=8 * 1024 * 1024,
            gpu_device_ids=[7, 3],
        )
        pipeline = Pipeline(force_cpu=False, config=config)
        _TorchExecutor.submitted_devices = []

        with patch.dict("sys.modules", {"torch": _FakeTorchModule()}):
            result = pipeline.get_backend().search(str(log_path), r"ERROR.*timeout\s+\d+")

        assert pipeline.selected_backend_reason == "gpu_explicit_ids_torch"
        assert pipeline.selected_gpu_device_ids == [7, 3]
        assert pipeline.selected_gpu_chunk_plan_mb == [(7, 128), (3, 128)]
        assert _TorchExecutor.submitted_devices == []
        assert result.total_matches == 1
        assert result.routing_backend == "CPUBackend"
        assert result.routing_reason == "torch_regex_cpu_fallback"
        assert result.routing_gpu_device_ids == []
        assert result.routing_gpu_chunk_plan_mb == []
        assert result.routing_distributed is False
        assert result.routing_worker_count == 1
