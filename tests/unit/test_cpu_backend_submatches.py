"""I-01 follow-up: CPUBackend's Python-regex loop must hand formatters an authoritative column.

The loop already computes an ``re.Match`` per matching line; it used to discard it, which left the
formatters to re-derive a column by re-running the user's pattern. These tests drive that loop
(the `fixed_strings` fallback taken when the native engine raises a non-syntax error) and render
through the real formatters.
"""

from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli.formatters.json_fmt import _column_for_match
from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
from tensor_grep.core.config import SearchConfig


def _python_loop_result(monkeypatch, tmp_path, text: bytes, pattern: str, **kw):
    def native_fault(*args, **kwargs):
        raise RuntimeError("simulated native fault")

    monkeypatch.setattr(CPUBackend, "_rust_match_set", native_fault)
    f = tmp_path / "a.txt"
    f.write_bytes(text)
    cfg = SearchConfig(query_pattern=pattern, fixed_strings=True, column=True, **kw)
    result = CPUBackend().search(str(f), pattern, cfg)
    return cfg, result


def test_python_loop_populates_byte_offset_submatches(monkeypatch, tmp_path):
    cfg, result = _python_loop_result(monkeypatch, tmp_path, b"zzz\n\xc3\xa9x b.r\n", "b.r")
    assert result.routing_reason.startswith("cpu_python")
    (match,) = result.matches
    assert match.submatches == ({"match": {"text": "b.r"}, "start": 4, "end": 7},)
    assert _column_for_match(match, cfg) == 5


def test_python_loop_column_renders_without_reevaluating_pattern(monkeypatch, tmp_path):
    cfg, result = _python_loop_result(monkeypatch, tmp_path, b"xx b.r\n", "b.r", ignore_case=True)
    assert RipgrepFormatter(cfg).format(result) == "1:4:xx b.r"


def test_invert_match_lines_have_no_submatches(monkeypatch, tmp_path):
    _cfg, result = _python_loop_result(
        monkeypatch, tmp_path, b"aaa\nxx b.r\n", "b.r", invert_match=True
    )
    assert [m.line_number for m in result.matches] == [1]
    assert result.matches[0].submatches is None
