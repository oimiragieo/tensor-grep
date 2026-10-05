"""A search that needs columns must be answered by rg (authoritative submatch byte offsets).

The native-engine CPU result tuples carry no offsets, so ``--force-cpu`` + ``--column`` used to
render a fabricated column 1 where main printed the real one. Routing the column request to rg
fixes it at the source; with rg absent the formatter keeps column 1 and says so on stderr.
"""

from unittest.mock import patch

import pytest

from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import Pipeline


def _rg_or_skip():
    from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary

    if resolve_ripgrep_binary() is None:
        pytest.skip("rg not installed")


@pytest.mark.parametrize("kw", [{"column": True}, {"vimgrep": True}])
@pytest.mark.parametrize("force_cpu", [False, True])
@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
@patch("tensor_grep.core.pipeline.MemoryManager")
@patch("tensor_grep.core.pipeline.CuDFBackend")
def test_column_request_selects_ripgrep(mock_cudf, mock_mem, mock_rust, mock_rg, force_cpu, kw):
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(force_cpu=force_cpu, config=SearchConfig(query_pattern="b.r", **kw))
    assert p.backend == mock_rg.return_value


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
@patch("tensor_grep.core.pipeline.MemoryManager")
@patch("tensor_grep.core.pipeline.CuDFBackend")
def test_no_column_request_keeps_force_cpu_routing(mock_cudf, mock_mem, mock_rust, mock_rg):
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(force_cpu=True, config=SearchConfig(query_pattern="b.r"))
    assert p.selected_backend_reason == "force_cpu_rust"
    # JSON does not need a column (it omits the field), so it must not change routing either
    q = Pipeline(force_cpu=True, config=SearchConfig(query_pattern="b.r", json_mode=True))
    assert q.selected_backend_reason == "force_cpu_rust"


@patch("tensor_grep.core.pipeline.RipgrepBackend")
@patch("tensor_grep.core.pipeline.RustCoreBackend")
@patch("tensor_grep.core.pipeline.MemoryManager")
@patch("tensor_grep.core.pipeline.CuDFBackend")
def test_column_with_ltl_or_count_keeps_existing_arms(mock_cudf, mock_mem, mock_rust, mock_rg):
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(force_cpu=True, config=SearchConfig(query_pattern="foo", column=True, count=True))
    assert p.selected_backend_reason == "force_cpu_rust"


@pytest.mark.parametrize("kw", [{"column": True}, {"vimgrep": True}])
def test_force_cpu_column_renders_real_column_through_real_pipeline(tmp_path, kw):
    _rg_or_skip()
    f = tmp_path / "a.txt"
    f.write_bytes(b"zzz\nxx bar\n")
    cfg = SearchConfig(query_pattern="b.r", **kw)
    p = Pipeline(force_cpu=True, config=cfg)
    out = RipgrepFormatter(cfg).format(p.backend.search(str(f), "b.r", cfg))
    assert out.endswith("2:4:xx bar"), out


def test_rg_absent_keeps_column_one_and_says_so(tmp_path, capsys):
    from tensor_grep.core.pipeline import RipgrepBackend

    f = tmp_path / "a.txt"
    f.write_bytes(b"zzz\nxx bar\n")
    cfg = SearchConfig(query_pattern="b.r", column=True)
    with patch.object(RipgrepBackend, "is_available", return_value=False):
        p = Pipeline(force_cpu=True, config=cfg)
        result = p.backend.search(str(f), "b.r", cfg)
    out = RipgrepFormatter(cfg).format(result)
    assert out == "2:1:xx bar"
    err = capsys.readouterr().err
    assert "column approximated (1): rg not available for exact offsets" in err
    assert err.count("column approximated") == 1  # once per formatter, not per line


def test_known_column_emits_no_notice(tmp_path, capsys):
    f = tmp_path / "a.txt"
    f.write_bytes(b"xx bar\n")
    cfg = SearchConfig(query_pattern="bar", column=True)  # literal: formatter can scan exactly
    p = Pipeline(force_cpu=True, config=cfg)
    out = RipgrepFormatter(cfg).format(p.backend.search(str(f), "bar", cfg))
    assert out.endswith("1:4:xx bar") or out.endswith("4:xx bar")
    assert "column approximated" not in capsys.readouterr().err


@pytest.mark.parametrize("force_cpu", [False, True])
def test_ltl_column_request_keeps_cpu_ltl_routing(tmp_path, force_cpu):
    f = tmp_path / "a.txt"
    f.write_text("foo\nbar\n", encoding="utf-8")
    query = "foo -> eventually bar"
    cfg = SearchConfig(query_pattern=query, ltl=True, column=True)
    p = Pipeline(force_cpu=force_cpu, config=cfg)
    assert p.selected_backend_reason != "column_rg_offsets"
    assert p.backend.__class__.__name__ != "RipgrepBackend"
