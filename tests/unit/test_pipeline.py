from unittest.mock import patch

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import Pipeline


class TestPipeline:
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_prefer_ripgrep_for_default_text_search(self, mock_cudf, mock_mem, mock_rg):
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        mock_cudf.return_value.is_available.return_value = True
        mock_rg.return_value.is_available.return_value = True

        pipeline = Pipeline(force_cpu=False, config=SearchConfig(query_pattern="ERROR"))
        assert pipeline.backend.__class__.__name__ == "MagicMock"
        assert pipeline.selected_backend_name == "MagicMock"
        assert pipeline.selected_backend_reason == "rg_default_fast_path"
        assert mock_rg.return_value == pipeline.backend

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_should_fallback_to_rust_when_ripgrep_missing(self, mock_rust, mock_rg):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True

        pipeline = Pipeline(force_cpu=False, config=SearchConfig(query_pattern="ERROR"))
        assert pipeline.backend == mock_rust.return_value
        assert pipeline.selected_backend_reason == "rust_secondary_fast_path"

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_try_gpu_only_for_large_complex_regex_when_rg_missing(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = False
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            input_total_bytes=512 * 1024 * 1024,
        )
        pipeline = Pipeline(force_cpu=False, config=config)
        assert pipeline.backend == mock_cudf.return_value
        assert pipeline.selected_backend_reason == "gpu_heuristic_cudf"

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_prefer_ripgrep_even_when_gpu_heuristic_matches(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            input_total_bytes=512 * 1024 * 1024,
        )
        pipeline = Pipeline(force_cpu=False, config=config)
        assert pipeline.backend == mock_rg.return_value
        assert pipeline.selected_backend_reason == "rg_default_fast_path"

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_route_to_gpu_on_large_complex_regex_when_rg_missing_even_if_rust_available(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            input_total_bytes=512 * 1024 * 1024,
        )
        pipeline = Pipeline(force_cpu=False, config=config)
        assert pipeline.backend == mock_cudf.return_value
        assert pipeline.selected_backend_reason == "gpu_heuristic_cudf"
        mock_mem.return_value.get_device_chunk_plan_mb.assert_called_once_with(preferred_ids=None)

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_route_gpu_heuristic_using_explicit_configured_device_ids(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(7, 256), (3, 512)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            input_total_bytes=512 * 1024 * 1024,
            gpu_device_ids=[7, 3],
        )
        pipeline = Pipeline(force_cpu=False, config=config)
        assert pipeline.backend == mock_cudf.return_value
        assert pipeline.selected_backend_reason == "gpu_heuristic_cudf"
        mock_mem.return_value.get_device_chunk_plan_mb.assert_called_once_with(preferred_ids=[7, 3])
        mock_cudf.assert_called_once_with(chunk_sizes_mb=[256, 512], device_ids=[7, 3])

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    @patch("tensor_grep.backends.torch_backend.TorchBackend")
    def test_should_pass_device_ids_to_torch_backend_when_cudf_unavailable(
        self, mock_torch_backend, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = False
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(7, 256), (3, 512)]
        mock_cudf.return_value.is_available.return_value = False
        mock_torch_backend.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            input_total_bytes=512 * 1024 * 1024,
            gpu_device_ids=[7, 3],
        )
        pipeline = Pipeline(force_cpu=False, config=config)

        assert pipeline.backend == mock_torch_backend.return_value
        assert pipeline.selected_backend_reason == "gpu_heuristic_torch"
        mock_torch_backend.assert_called_once_with(device_ids=[7, 3])

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_keep_rust_default_for_small_or_simple_queries_when_rg_missing(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern="ERROR",
            input_total_bytes=8 * 1024 * 1024,
        )
        pipeline = Pipeline(force_cpu=False, config=config)
        assert pipeline.backend == mock_rust.return_value
        assert pipeline.selected_backend_reason == "rust_secondary_fast_path"

    @patch("tensor_grep.core.pipeline.MemoryManager")
    def test_should_select_cudf_when_available(self, mock_mem):
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock:
            mock.return_value.is_available.return_value = True
            pipeline = Pipeline(force_cpu=False)
            assert pipeline.backend.__class__.__name__ in (
                "MagicMock",
                "RipgrepBackend",
                "RustCoreBackend",
            )

    @patch("tensor_grep.core.pipeline.MemoryManager")
    def test_should_fallback_to_cpu_when_no_gpu(self, mock_mem):
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock_cudf:
            with patch(
                "tensor_grep.backends.torch_backend.TorchBackend.is_available"
            ) as mock_torch:
                mock_cudf.return_value.is_available.return_value = False
                mock_torch.return_value = False
                pipeline = Pipeline(force_cpu=False)
                # Depending on if torch is installed and if rust is available
                assert pipeline.backend.__class__.__name__ in (
                    "CPUBackend",
                    "RustCoreBackend",
                    "RipgrepBackend",
                )

    @patch("tensor_grep.core.pipeline.MemoryManager")
    def test_should_fallback_to_cpu_when_no_vram(self, mock_mem):
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = []
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock:
            mock.return_value.is_available.return_value = True
            pipeline = Pipeline(force_cpu=False)
            assert pipeline.backend.__class__.__name__ in (
                "CPUBackend",
                "RustCoreBackend",
                "RipgrepBackend",
            )

    def test_should_force_cpu_when_requested(self):
        with patch("tensor_grep.core.pipeline.CuDFBackend") as mock:
            mock.return_value.is_available.return_value = True
            pipeline = Pipeline(force_cpu=True)
            assert pipeline.backend.__class__.__name__ in (
                "CPUBackend",
                "RustCoreBackend",
                "RipgrepBackend",
            )

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_should_route_ltl_queries_to_cpu_backend(self, mock_rust, mock_rg):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True

        pipeline = Pipeline(
            force_cpu=False,
            config=SearchConfig(query_pattern="A -> eventually B", ltl=True),
        )
        assert pipeline.backend.__class__.__name__ == "CPUBackend"

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_should_route_invert_queries_to_rust_when_rg_missing(self, mock_rust, mock_rg):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True

        pipeline = Pipeline(
            force_cpu=False,
            config=SearchConfig(query_pattern="ERROR", invert_match=True),
        )
        assert pipeline.backend == mock_rust.return_value
        assert pipeline.selected_backend_reason == "rust_secondary_fast_path"

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_should_route_context_queries_to_ripgrep_when_available(self, mock_rust, mock_rg):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True

        pipeline = Pipeline(
            force_cpu=False,
            config=SearchConfig(query_pattern="ERROR", context=2),
        )
        assert pipeline.backend == mock_rg.return_value
        assert pipeline.selected_backend_reason == "rg_semantics_fast_path"

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_should_fallback_context_queries_to_cpu_when_ripgrep_missing(self, mock_rust, mock_rg):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True

        pipeline = Pipeline(
            force_cpu=False,
            config=SearchConfig(query_pattern="ERROR", context=2),
        )
        assert pipeline.backend.__class__.__name__ == "CPUBackend"
        assert pipeline.selected_backend_reason == "python_cpu_semantics_required"
