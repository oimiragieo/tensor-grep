"""PR-A slice 3 (rg-exit2-partial-results): rg exit 2 is a SOFT per-file error (e.g. one
unreadable/missing path among many) AND rg still emits matches for the readable files. The old
parser raised unconditionally on exit>1, discarding those partial results. Fix: parse-first, then
keep partial results + flag result_incomplete (rg-parity exit 2 + a suppression!=absence marker);
only a genuine total failure (exit>2, or exit 2 with nothing parsed) stays fail-closed.
"""

from __future__ import annotations

import json as _json
from types import SimpleNamespace

import pytest

import tensor_grep.backends.ripgrep_backend as rb
from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import SearchResult, merge_runtime_routing


def _fake(returncode: int, stdout: str, stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _match(path: str = "a.log", text: str = "ERROR", line: int = 1) -> str:
    return _json.dumps({
        "type": "match",
        "data": {"path": {"text": path}, "lines": {"text": text + "\n"}, "line_number": line},
    })


def _patch_rg(monkeypatch, fake):
    monkeypatch.setattr(rb, "run_subprocess", lambda *a, **k: fake)
    monkeypatch.setattr(RipgrepBackend, "_get_binary_name", lambda self: "rg")


def test_search_exit2_with_matches_keeps_partial_and_flags_incomplete(monkeypatch) -> None:
    _patch_rg(monkeypatch, _fake(2, _match() + "\n", "rg: b.log: No such file or directory"))
    result = RipgrepBackend().search("a.log", "ERROR", SearchConfig())
    assert result.total_matches == 1  # partial results KEPT (was: discarded via raise)
    assert result.result_incomplete is True
    assert "No such file" in (result.incomplete_reason or "")


def test_search_exit2_zero_parsed_still_fails_closed(monkeypatch) -> None:
    # exit 2 with NOTHING parsed = a genuine failure (e.g. regex syntax) -> raise, byte-identical.
    _patch_rg(monkeypatch, _fake(2, "", "regex parse error"))
    with pytest.raises(RuntimeError, match="exit code 2"):
        RipgrepBackend().search("a.log", "ERROR", SearchConfig())


def test_search_exit_gt2_always_fails_closed_even_with_matches(monkeypatch) -> None:
    _patch_rg(monkeypatch, _fake(3, _match() + "\n", "fatal"))
    with pytest.raises(RuntimeError, match="exit code 3"):
        RipgrepBackend().search("a.log", "ERROR", SearchConfig())


def test_search_exit01_unchanged(monkeypatch) -> None:
    _patch_rg(monkeypatch, _fake(0, _match() + "\n"))
    result = RipgrepBackend().search("a.log", "ERROR", SearchConfig())
    assert result.total_matches == 1
    assert result.result_incomplete is False


def test_files_with_matches_exit2_keeps_partial(monkeypatch) -> None:
    _patch_rg(monkeypatch, _fake(2, "a.log\nc.log\n", "rg: b.log: No such file"))
    result = RipgrepBackend()._search_files_with_matches(
        "x", "ERROR", SearchConfig(files_with_matches=True)
    )
    assert result.matched_file_paths == ["a.log", "c.log"]
    assert result.result_incomplete is True


def test_counts_exit2_keeps_partial(monkeypatch) -> None:
    _patch_rg(monkeypatch, _fake(2, "3\n", "rg: b.log: No such file"))
    result = RipgrepBackend()._search_counts("a.log", "ERROR", SearchConfig(count=True))
    assert result.total_matches == 3
    assert result.result_incomplete is True


def test_merge_runtime_routing_or_merges_incompleteness() -> None:
    agg = SearchResult()
    sub = SearchResult(result_incomplete=True, incomplete_reason="rg exit 2")
    merge_runtime_routing(agg, sub)
    assert agg.result_incomplete is True
    assert agg.incomplete_reason == "rg exit 2"


def test_json_formatter_emits_incompleteness_only_when_partial() -> None:
    from tensor_grep.cli.formatters.json_fmt import JsonFormatter, NdjsonFormatter
    from tensor_grep.core.result import MatchLine

    incomplete = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
        total_matches=1,
        total_files=1,
        result_incomplete=True,
        incomplete_reason="rg exit 2 (partial results)",
    )
    out = _json.loads(JsonFormatter().format(incomplete))
    assert out["result_incomplete"] is True
    assert out["incomplete_reason"] == "rg exit 2 (partial results)"
    # NDJSON spreads the full envelope, so it carries it per row.
    nd_row = _json.loads(NdjsonFormatter().format(incomplete).splitlines()[0])
    assert nd_row["result_incomplete"] is True
    # Complete result: byte-identical shape, no incompleteness keys.
    complete = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
        total_matches=1,
        total_files=1,
    )
    out2 = _json.loads(JsonFormatter().format(complete))
    assert "result_incomplete" not in out2
    assert "incomplete_reason" not in out2
