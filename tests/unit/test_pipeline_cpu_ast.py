"""CPU preference must preserve or explicitly refuse requested AST semantics."""

from unittest.mock import MagicMock

import pytest

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import ConfigurationError, Pipeline
from tensor_grep.core.result import MatchLine, SearchResult


@pytest.fixture
def engines(monkeypatch):
    targets = {
        "rust": "tensor_grep.core.pipeline.RustCoreBackend",
        "rg": "tensor_grep.core.pipeline.RipgrepBackend",
        "sz": "tensor_grep.core.pipeline.StringZillaBackend",
        "native": "tensor_grep.backends.ast_backend.AstBackend",
        "wrapper": "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
    }
    engines = {}
    for name, target in targets.items():
        factory = MagicMock()
        factory.return_value.is_available.return_value = name != "sz"
        factory.return_value.search.return_value = SearchResult()
        monkeypatch.setattr(target, factory)
        engines[name] = factory.return_value
    return engines


@pytest.mark.parametrize("force_cpu", [False, True])
def test_metavariable_executes_wrapper_semantics_or_explicitly_refuses(
    engines, tmp_path, force_cpu
):
    path = tmp_path / "calls.py"
    path.write_text('print(alpha)\nprint(beta)\ntext = "print(alpha)"\n')
    config = SearchConfig(ast=True, lang="python", query_pattern="print($A)")
    expected = SearchResult(
        matches=[MatchLine(1, "print(alpha)", str(path)), MatchLine(2, "print(beta)", str(path))],
        total_matches=2,
    )
    # Backend result seam complements the actual CLI semantic reproduction; regex is empty.
    engines["wrapper"].search.return_value = expected
    try:
        pipeline = Pipeline(force_cpu=force_cpu, config=config)
    except ConfigurationError as error:
        assert force_cpu and "AST" in str(error) and "CPU" in str(error)
        return
    result = pipeline.backend.search(str(path), config.query_pattern, config=config)
    assert result.total_matches == 2
    assert [match.text for match in result.matches] == ["print(alpha)", "print(beta)"]
    engines["wrapper"].search.assert_called_once_with(str(path), "print($A)", config=config)
    engines["rust"].search.assert_not_called()
    engines["native"].search.assert_not_called()


@pytest.mark.parametrize("force_cpu", [False, True])
def test_supported_native_kind_preference_remains_ast(engines, force_cpu):
    config = SearchConfig(
        ast=True, lang="python", query_pattern="function_definition", ast_prefer_native=True
    )
    try:
        pipeline = Pipeline(force_cpu=force_cpu, config=config)
    except ConfigurationError as error:
        assert force_cpu and "AST" in str(error) and "CPU" in str(error)
        return
    assert pipeline.backend is engines["native"]
    assert pipeline.selected_backend_reason == "ast_backend_available"


@pytest.mark.parametrize("force_cpu", [False, True])
def test_missing_wrapper_refuses_metavariable_instead_of_regex(engines, force_cpu):
    engines["wrapper"].is_available.return_value = False
    with pytest.raises(ConfigurationError, match="ast-grep"):
        Pipeline(
            force_cpu=force_cpu,
            config=SearchConfig(ast=True, lang="python", query_pattern="print($A)"),
        )
    engines["rust"].search.assert_not_called()


@pytest.mark.parametrize("force_cpu", [False, True])
def test_ast_gpu_device_request_is_explicitly_refused_even_with_cpu(engines, force_cpu):
    with pytest.raises(ConfigurationError, match="GPU"):
        Pipeline(
            force_cpu=force_cpu,
            config=SearchConfig(
                ast=True, lang="python", query_pattern="print($A)", gpu_device_ids=[0]
            ),
        )


def test_ordinary_cpu_regex_still_uses_rust(engines):
    pipeline = Pipeline(force_cpu=True, config=SearchConfig(query_pattern="print"))
    assert pipeline.backend is engines["rust"]
    assert pipeline.selected_backend_reason == "force_cpu_rust"
