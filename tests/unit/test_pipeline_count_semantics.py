"""Part D.4 (I-04): -c with flags the Rust count fast path cannot honour must not take it.

RustCoreBackend.count_matches(pattern, path, ignore_case, fixed_strings) has no word/line/
smart-case/max-count/null-data/stop-on-nonmatch inputs, so those configs route to rg.
"""

from unittest.mock import patch

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import Pipeline


@pytest.mark.parametrize(
    "kw",
    [
        {"word_regexp": True},
        {"line_regexp": True},
        {"smart_case": True},
        {"max_count": 1},
        {"null_data": True},
        {"stop_on_nonmatch": True},
    ],
)
@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
@patch("tensor_grep.core.pipeline.MemoryManager")
@patch("tensor_grep.core.pipeline.CuDFBackend")
def test_count_with_semantic_flag_uses_rg_not_rust_count(
    mock_cudf, mock_mem, mock_rust, mock_rg, kw
):
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(force_cpu=False, config=SearchConfig(query_pattern="foo", count=True, **kw))
    assert p.backend == mock_rg.return_value
    assert p.selected_backend_reason == "count_rg_semantics"


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
@patch("tensor_grep.core.pipeline.MemoryManager")
@patch("tensor_grep.core.pipeline.CuDFBackend")
def test_plain_count_still_uses_rust_fast_path(mock_cudf, mock_mem, mock_rust, mock_rg):
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(force_cpu=False, config=SearchConfig(query_pattern="foo", count=True))
    assert p.selected_backend_reason == "count_rust_fast_path"


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
@patch("tensor_grep.core.pipeline.MemoryManager")
@patch("tensor_grep.core.pipeline.CuDFBackend")
def test_count_unforwardable_flag_without_rg_fails_closed(mock_cudf, mock_mem, mock_rust, mock_rg):
    mock_rg.return_value.is_available.return_value = False
    mock_rust.return_value.is_available.return_value = True
    with pytest.raises(BackendExecutionError):
        Pipeline(
            force_cpu=False,
            config=SearchConfig(query_pattern="foo", count=True, null_data=True),
        )


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
@patch("tensor_grep.core.pipeline.MemoryManager")
@patch("tensor_grep.core.pipeline.CuDFBackend")
def test_count_word_regexp_without_rg_uses_python_cpu(mock_cudf, mock_mem, mock_rust, mock_rg):
    mock_rg.return_value.is_available.return_value = False
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(
        force_cpu=False,
        config=SearchConfig(query_pattern="foo", count=True, word_regexp=True),
    )
    assert p.selected_backend_reason == "count_python_cpu_semantics"


_SEMANTIC_KW = [
    {"word_regexp": True},
    {"line_regexp": True},
    {"smart_case": True},
    {"max_count": 1},
    {"null_data": True},
    {"stop_on_nonmatch": True},
]


@pytest.mark.parametrize("kw", _SEMANTIC_KW)
@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
@patch("tensor_grep.core.pipeline.MemoryManager")
@patch("tensor_grep.core.pipeline.CuDFBackend")
def test_force_cpu_count_with_semantic_flag_still_uses_rg(
    mock_cudf, mock_mem, mock_rust, mock_rg, kw
):
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(force_cpu=True, config=SearchConfig(query_pattern="foo", count=True, **kw))
    assert p.backend == mock_rg.return_value
    assert p.selected_backend_reason == "count_rg_semantics"


@pytest.mark.parametrize("kw", [{"null_data": True}, {"stop_on_nonmatch": True}])
@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
@patch("tensor_grep.core.pipeline.MemoryManager")
@patch("tensor_grep.core.pipeline.CuDFBackend")
def test_force_cpu_count_unforwardable_flag_without_rg_fails_closed(
    mock_cudf, mock_mem, mock_rust, mock_rg, kw
):
    mock_rg.return_value.is_available.return_value = False
    mock_rust.return_value.is_available.return_value = True
    with pytest.raises(BackendExecutionError):
        Pipeline(force_cpu=True, config=SearchConfig(query_pattern="foo", count=True, **kw))


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
@patch("tensor_grep.core.pipeline.MemoryManager")
@patch("tensor_grep.core.pipeline.CuDFBackend")
def test_force_cpu_plain_count_unchanged(mock_cudf, mock_mem, mock_rust, mock_rg):
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(force_cpu=True, config=SearchConfig(query_pattern="foo", count=True))
    assert p.selected_backend_reason == "force_cpu_rust"


def test_force_cpu_count_max_count_is_honoured_end_to_end(tmp_path):
    from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary

    if resolve_ripgrep_binary() is None:
        pytest.skip("rg not installed")
    f = tmp_path / "a.txt"
    f.write_text("foo\nfoo\nfoo\n", encoding="utf-8")
    cfg = SearchConfig(query_pattern="foo", count=True, max_count=1)
    p = Pipeline(force_cpu=True, config=cfg)
    result = p.backend.search(str(f), "foo", cfg)
    assert result.total_matches == 1
