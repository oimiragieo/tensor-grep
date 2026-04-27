import builtins
from unittest.mock import patch

import pytest

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import ConfigurationError, Pipeline


class TestPipeline:
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend")
    @patch("tensor_grep.backends.ast_backend.AstBackend")
    def test_should_prefer_ast_backend_over_wrapper_when_both_are_available(
        self, mock_ast_backend, mock_ast_wrapper, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_ast_backend.return_value.is_available.return_value = True
        mock_ast_wrapper.return_value.is_available.return_value = True

        pipeline = Pipeline(
            force_cpu=False,
            config=SearchConfig(
                ast=True,
                ast_prefer_native=True,
                lang="python",
                query_pattern="function_definition",
            ),
        )

        assert pipeline.backend == mock_ast_backend.return_value
        assert pipeline.selected_backend_reason == "ast_backend_available"

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend")
    @patch("tensor_grep.backends.ast_backend.AstBackend")
    def test_should_prefer_ast_wrapper_by_default_when_both_ast_backends_are_available(
        self, mock_ast_backend, mock_ast_wrapper, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_ast_backend.return_value.is_available.return_value = True
        mock_ast_wrapper.return_value.is_available.return_value = True

        pipeline = Pipeline(force_cpu=False, config=SearchConfig(ast=True, lang="python"))

        assert pipeline.backend == mock_ast_wrapper.return_value
        assert pipeline.selected_backend_reason == "ast_wrapper_available"

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend")
    @patch("tensor_grep.backends.ast_backend.AstBackend")
    def test_should_prefer_ast_wrapper_for_ast_grep_style_patterns_even_when_native_is_requested(
        self, mock_ast_backend, mock_ast_wrapper, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_ast_backend.return_value.is_available.return_value = True
        mock_ast_wrapper.return_value.is_available.return_value = True

        pipeline = Pipeline(
            force_cpu=False,
            config=SearchConfig(
                ast=True,
                ast_prefer_native=True,
                lang="python",
                query_pattern="def $FUNC():",
            ),
        )

        assert pipeline.backend == mock_ast_wrapper.return_value
        assert pipeline.selected_backend_reason == "ast_wrapper_available"

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend")
    @patch("tensor_grep.backends.ast_backend.AstBackend")
    def test_should_reject_ast_grep_style_pattern_when_wrapper_is_unavailable(
        self, mock_ast_backend, mock_ast_wrapper, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_ast_backend.return_value.is_available.return_value = True
        mock_ast_wrapper.return_value.is_available.return_value = False

        with pytest.raises(ConfigurationError, match="ast-grep"):
            Pipeline(
                force_cpu=False,
                config=SearchConfig(
                    ast=True,
                    ast_prefer_native=True,
                    lang="python",
                    query_pattern="return 1",
                ),
            )

    @patch("tensor_grep.backends.cybert_backend.CybertBackend")
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_nlp_routing_should_select_cybert_backend_for_nlp_queries(
        self, mock_rust, mock_rg, mock_cybert_backend
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_cybert_backend.return_value.is_available.return_value = True

        pipeline = Pipeline(
            force_cpu=False,
            config=SearchConfig(query_pattern="classify ssh brute force attempts"),
        )

        assert pipeline.backend == mock_cybert_backend.return_value
        assert pipeline.selected_backend_reason == "nlp_cybert"

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
        assert pipeline.selected_gpu_device_ids == []
        assert pipeline.selected_gpu_chunk_plan_mb == []
        assert mock_rg.return_value == pipeline.backend

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_should_fallback_to_rust_when_ripgrep_missing(self, mock_rust, mock_rg):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True

        pipeline = Pipeline(force_cpu=False, config=SearchConfig(query_pattern="ERROR"))
        assert pipeline.backend == mock_rust.return_value
        assert pipeline.selected_backend_reason == "rust_secondary_fast_path"
        assert pipeline.selected_gpu_device_ids == []
        assert pipeline.selected_gpu_chunk_plan_mb == []

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_should_raise_configuration_error_for_pcre2_when_rg_lacks_pcre2(
        self, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rg.return_value.supports_pcre2.return_value = False
        mock_rust.return_value.is_available.return_value = True

        with pytest.raises(ConfigurationError, match="PCRE2 requested"):
            Pipeline(force_cpu=False, config=SearchConfig(pcre2=True, query_pattern="a(?=b)"))

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_should_raise_configuration_error_for_pcre2_when_rg_is_missing(
        self, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rg.return_value.supports_pcre2.return_value = False
        mock_rust.return_value.is_available.return_value = True

        with pytest.raises(ConfigurationError, match="PCRE2 requested"):
            Pipeline(force_cpu=False, config=SearchConfig(pcre2=True, query_pattern="a(?=b)"))

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
        assert pipeline.selected_gpu_device_ids == [0]
        assert pipeline.selected_gpu_chunk_plan_mb == [(0, 512)]

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_route_to_gpu_when_explicit_device_ids_provided_even_if_rg_available(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(3, 512), (7, 512)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern="ERROR",
            input_total_bytes=8 * 1024 * 1024,
            gpu_device_ids=[3, 7],
        )
        pipeline = Pipeline(force_cpu=False, config=config)

        assert pipeline.backend == mock_cudf.return_value
        assert pipeline.selected_backend_reason == "gpu_explicit_ids_cudf"
        assert pipeline.selected_gpu_device_ids == [3, 7]
        assert pipeline.selected_gpu_chunk_plan_mb == [(3, 512), (7, 512)]
        mock_mem.return_value.get_device_chunk_plan_mb.assert_called_once_with(preferred_ids=[3, 7])

    @patch("tensor_grep.backends.torch_backend.TorchBackend")
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_raise_configuration_error_when_explicit_gpu_ids_have_no_available_gpu_backend(
        self, mock_cudf, mock_mem, mock_rust, mock_rg, mock_torch_backend
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(3, 512), (7, 512)]
        mock_cudf.return_value.is_available.return_value = False
        mock_torch_backend.return_value.is_available.return_value = False

        config = SearchConfig(
            query_pattern="ERROR",
            input_total_bytes=8 * 1024 * 1024,
            gpu_device_ids=[3, 7],
        )

        with pytest.raises(
            ConfigurationError,
            match=r"Explicit GPU device selection .*\[3, 7\]",
        ):
            Pipeline(force_cpu=False, config=config)

    @patch(
        "tensor_grep.backends.torch_backend.TorchBackend", side_effect=ImportError("torch missing")
    )
    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_raise_configuration_error_when_explicit_gpu_ids_torch_backend_import_fails(
        self, mock_cudf, mock_mem, mock_rust, mock_rg, _mock_torch_backend
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(3, 512), (7, 512)]
        mock_cudf.return_value.is_available.return_value = False

        config = SearchConfig(
            query_pattern="ERROR",
            input_total_bytes=8 * 1024 * 1024,
            gpu_device_ids=[3, 7],
        )

        with pytest.raises(
            ConfigurationError,
            match=r"Explicit GPU device selection .*Torch backend imports failed after CuDF was unavailable",
        ):
            Pipeline(force_cpu=False, config=config)

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
        assert pipeline.selected_gpu_device_ids == []
        assert pipeline.selected_gpu_chunk_plan_mb == []

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
        assert pipeline.selected_gpu_device_ids == [0]
        assert pipeline.selected_gpu_chunk_plan_mb == [(0, 512)]
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
        assert pipeline.selected_backend_reason == "gpu_explicit_ids_cudf"
        assert pipeline.selected_gpu_device_ids == [7, 3]
        assert pipeline.selected_gpu_chunk_plan_mb == [(7, 256), (3, 512)]
        mock_mem.return_value.get_device_chunk_plan_mb.assert_called_once_with(preferred_ids=[7, 3])
        mock_cudf.assert_called_once_with(chunk_sizes_mb=[256, 512], device_ids=[7, 3])

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_allow_mixed_preferred_device_ids_and_use_normalized_chunk_plan(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True
        # MemoryManager normalizes preferred IDs and returns only routable IDs/chunks.
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(7, 256)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            input_total_bytes=512 * 1024 * 1024,
            gpu_device_ids=[7, 99, 7],
        )
        pipeline = Pipeline(force_cpu=False, config=config)

        assert pipeline.backend == mock_cudf.return_value
        assert pipeline.selected_backend_reason == "gpu_explicit_ids_cudf"
        assert pipeline.selected_gpu_device_ids == [7]
        assert pipeline.selected_gpu_chunk_plan_mb == [(7, 256)]
        mock_mem.return_value.get_device_chunk_plan_mb.assert_called_once_with(
            preferred_ids=[7, 99, 7]
        )
        mock_cudf.assert_called_once_with(chunk_sizes_mb=[256], device_ids=[7])

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_normalize_duplicate_and_invalid_chunk_entries_from_memory_plan(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True
        # Duplicate device 7 should keep the largest chunk; invalid chunk sizes are dropped.
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [
            (7, 256),
            (7, 512),
            (3, 0),
            (5, -4),
        ]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            input_total_bytes=512 * 1024 * 1024,
            gpu_device_ids=[7, 3, 5],
        )
        pipeline = Pipeline(force_cpu=False, config=config)

        assert pipeline.backend == mock_cudf.return_value
        assert pipeline.selected_backend_reason == "gpu_explicit_ids_cudf"
        assert pipeline.selected_gpu_device_ids == [7]
        assert pipeline.selected_gpu_chunk_plan_mb == [(7, 512)]
        mock_cudf.assert_called_once_with(chunk_sizes_mb=[512], device_ids=[7])

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
        assert pipeline.selected_backend_reason == "gpu_explicit_ids_torch"
        assert pipeline.selected_gpu_device_ids == [7, 3]
        assert pipeline.selected_gpu_chunk_plan_mb == [(7, 256), (3, 512)]
        mock_torch_backend.assert_called_once_with(device_ids=[7, 3], chunk_sizes_mb=[256, 512])

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_pipeline_fallback_should_raise_configuration_error_when_explicit_gpu_ids_have_no_routable_chunk_plan(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = []

        config = SearchConfig(
            query_pattern="ERROR",
            input_total_bytes=8 * 1024 * 1024,
            gpu_device_ids=[3, 7],
        )

        with pytest.raises(
            ConfigurationError,
            match=r"Explicit GPU device selection .*\[3, 7\]",
        ):
            Pipeline(force_cpu=False, config=config)

        mock_cudf.assert_not_called()

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    def test_pipeline_fallback_should_raise_configuration_error_when_ast_dependencies_fail_to_import(
        self, mock_rust, mock_rg, monkeypatch
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        original_import = builtins.__import__

        def failing_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in {
                "tensor_grep.backends.ast_backend",
                "tensor_grep.backends.ast_wrapper_backend",
            }:
                raise ImportError("AST dependencies missing")
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", failing_import)

        with pytest.raises(
            ConfigurationError,
            match="Explicit AST search requires AST dependencies",
        ):
            Pipeline(
                force_cpu=False,
                config=SearchConfig(
                    ast=True,
                    lang="python",
                    query_pattern="function_definition",
                ),
            )

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend")
    @patch("tensor_grep.backends.ast_backend.AstBackend")
    def test_pipeline_fallback_should_raise_configuration_error_when_no_ast_backend_is_available(
        self, mock_ast_backend, mock_ast_wrapper, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = True
        mock_rust.return_value.is_available.return_value = True
        mock_ast_backend.return_value.is_available.return_value = False
        mock_ast_wrapper.return_value.is_available.return_value = False

        with pytest.raises(
            ConfigurationError,
            match="Explicit AST search requires AST dependencies: no AST backend is available",
        ):
            Pipeline(
                force_cpu=False,
                config=SearchConfig(
                    ast=True,
                    lang="python",
                    query_pattern="function_definition",
                ),
            )

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
        assert pipeline.selected_gpu_device_ids == []
        assert pipeline.selected_gpu_chunk_plan_mb == []

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    @patch("tensor_grep.core.pipeline.StringZillaBackend")
    def test_should_not_try_gpu_for_fixed_strings_when_rg_and_rust_are_unavailable(
        self, mock_sz, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = False
        mock_sz.return_value.is_available.return_value = False
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern="ERROR",
            fixed_strings=True,
            input_total_bytes=512 * 1024 * 1024,
        )
        pipeline = Pipeline(force_cpu=False, config=config)

        assert pipeline.backend.__class__.__name__ == "CPUBackend"
        assert pipeline.selected_backend_reason == "fallback_backend"
        mock_mem.return_value.get_device_chunk_plan_mb.assert_not_called()
        mock_cudf.assert_not_called()

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_not_try_gpu_for_count_queries_when_rg_is_unavailable(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern="ERROR",
            count=True,
            input_total_bytes=512 * 1024 * 1024,
        )
        pipeline = Pipeline(force_cpu=False, config=config)

        assert pipeline.backend == mock_rust.return_value
        assert pipeline.selected_backend_reason == "count_rust_fast_path"
        mock_mem.return_value.get_device_chunk_plan_mb.assert_not_called()
        mock_cudf.assert_not_called()

    @patch("tensor_grep.core.pipeline.RipgrepBackend")
    @patch("tensor_grep.core.pipeline.RustCoreBackend")
    @patch("tensor_grep.core.pipeline.MemoryManager")
    @patch("tensor_grep.core.pipeline.CuDFBackend")
    def test_should_not_try_gpu_for_python_semantics_when_rg_missing(
        self, mock_cudf, mock_mem, mock_rust, mock_rg
    ):
        mock_rg.return_value.is_available.return_value = False
        mock_rust.return_value.is_available.return_value = True
        mock_mem.return_value.get_device_chunk_plan_mb.return_value = [(0, 512)]
        mock_cudf.return_value.is_available.return_value = True

        config = SearchConfig(
            query_pattern=r"(ERROR|WARN).*timeout\s+\d+",
            context=2,
            input_total_bytes=512 * 1024 * 1024,
        )
        pipeline = Pipeline(force_cpu=False, config=config)

        assert pipeline.backend.__class__.__name__ == "CPUBackend"
        assert pipeline.selected_backend_reason == "python_cpu_semantics_required"
        mock_mem.return_value.get_device_chunk_plan_mb.assert_not_called()
        mock_cudf.assert_not_called()

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

    def test_should_preserve_explicit_multi_gpu_ids_through_torch_execution(self, tmp_path):
        import types

        path = tmp_path / "torch_pipeline.log"
        path.write_text("ERROR A\nERROR B\nERROR C\nERROR D\n", encoding="utf-8")

        class _FakeScalar:
            def __init__(self, value: bool):
                self._value = value

            def item(self):
                return self._value

        class _FakeAny:
            def __init__(self, values: list[bool]):
                self._values = values

            def any(self):
                return _FakeScalar(any(self._values))

        class _FakeCompare:
            def __init__(self, windows: list[list[int]], pattern: list[int]):
                self._windows = windows
                self._pattern = pattern

            def all(self, dim=1):
                _ = dim
                return _FakeAny([window == self._pattern for window in self._windows])

        class _FakeWindows:
            def __init__(self, windows: list[list[int]]):
                self._windows = windows

            def __eq__(self, other):
                return _FakeCompare(self._windows, other.data)

        class _FakeTensor:
            def __init__(self, data: list[int]):
                self.data = data

            def unfold(self, dim: int, size: int, step: int):
                _ = dim
                windows: list[list[int]] = []
                for i in range(0, max(len(self.data) - size + 1, 0), step):
                    windows.append(self.data[i : i + size])
                return _FakeWindows(windows)

        class _FakeTorch(types.ModuleType):
            uint8 = "uint8"

            def __init__(self):
                super().__init__("torch")

            def device(self, value: str):
                return value

            def tensor(self, values, dtype=None, device=None):
                _ = (dtype, device)
                return _FakeTensor(list(values))

        fake_torch = _FakeTorch()
        fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True)

        config = SearchConfig(
            query_pattern="ERROR",
            fixed_strings=False,
            gpu_device_ids=[7, 3],
        )

        with (
            patch.dict("sys.modules", {"torch": fake_torch}),
            patch("tensor_grep.core.pipeline.RipgrepBackend.is_available", return_value=False),
            patch("tensor_grep.core.pipeline.StringZillaBackend.is_available", return_value=False),
            patch("tensor_grep.core.pipeline.RustCoreBackend.is_available", return_value=False),
            patch("tensor_grep.core.pipeline.CuDFBackend.is_available", return_value=False),
            patch(
                "tensor_grep.backends.torch_backend.TorchBackend.is_available", return_value=True
            ),
            patch(
                "tensor_grep.core.hardware.memory_manager.MemoryManager.get_device_chunk_plan_mb",
                return_value=[(7, 3), (3, 1)],
            ),
        ):
            from tensor_grep.backends.torch_backend import TorchBackend

            pipeline = Pipeline(force_cpu=False, config=config)
            assert isinstance(pipeline.get_backend(), TorchBackend)

            result = pipeline.get_backend().search(str(path), "ERROR", config)

        assert pipeline.selected_backend_reason == "gpu_explicit_ids_torch"
        assert pipeline.selected_gpu_device_ids == [7, 3]
        assert pipeline.selected_gpu_chunk_plan_mb == [(7, 3), (3, 1)]
        assert result.total_matches == 4
        assert result.routing_backend == "TorchBackend"
        assert result.routing_reason == "torch_multi_gpu_fanout"
        assert result.routing_gpu_device_ids == [7, 3]
        assert result.routing_gpu_chunk_plan_mb == [(7, 3), (3, 1)]
        assert result.routing_distributed is True
        assert result.routing_worker_count == 2
