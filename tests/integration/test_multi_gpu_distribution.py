from unittest.mock import MagicMock, patch

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine


class _FakeFuture:
    def __init__(self, result):
        self._result = result

    def result(self):
        return self._result


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
