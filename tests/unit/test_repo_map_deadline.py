"""Moat P0-6 STEP 1: build_repo_map deadline -> partial results on a time budget.

A supplied ABSOLUTE monotonic deadline stops the CPU-bound per-file parse loop early and returns
partial:true + a deadline_limit sibling, instead of the caller's hard timeout discarding all work
(the recurring dogfood complaint: '60s cap errors with bare timed-out, exit 1, zero JSON'). The
signal is kept SEPARATE from scan_limit (file-cap cause) so the remediation advice is the right knob.
"""

from __future__ import annotations

import time
from pathlib import Path

import tensor_grep.cli.repo_map as repo_map


def _make_repo(root: Path, count: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    for index in range(count):
        (src / f"m{index}.py").write_text(
            f"def f{index}():\n    return {index}\n", encoding="utf-8"
        )


def test_deadline_already_expired_returns_partial_immediately(tmp_path: Path) -> None:
    _make_repo(tmp_path, 6)
    # An already-expired deadline must return a valid partial dict (no exception, no hang).
    result = repo_map.build_repo_map(str(tmp_path), deadline_monotonic=time.monotonic() - 1.0)
    assert isinstance(result, dict)
    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True
    assert result["deadline_limit"]["files_scanned"] == 0  # broke before any parse


def test_deadline_mid_scan_keeps_partial_results(tmp_path: Path, monkeypatch) -> None:
    _make_repo(tmp_path, 10)
    # Deterministic fake clock: monotonic only advances when a file is parsed, so the deadline
    # crosses after exactly 5 parses regardless of any other monotonic() callers.
    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_parse = repo_map._imports_and_symbols_for_path

    def _clock_advancing_parse(path, **kwargs):
        clock["t"] += 1.0
        return original_parse(path, **kwargs)

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", _clock_advancing_parse)

    result = repo_map.build_repo_map(str(tmp_path), deadline_monotonic=base + 5.0)

    assert result.get("partial") is True
    deadline_limit = result["deadline_limit"]
    assert deadline_limit["deadline_exceeded"] is True
    assert 0 < deadline_limit["files_scanned"] < deadline_limit["files_total"]  # some but not all
    assert result["symbols"], "partial work must be RETAINED, not zeroed"


def test_deadline_none_is_no_op(tmp_path: Path) -> None:
    _make_repo(tmp_path, 6)
    result = repo_map.build_repo_map(str(tmp_path))  # no deadline -> unchanged behavior
    assert "partial" not in result
    assert "deadline_limit" not in result
    assert result["symbols"]  # full parse, nothing bounded


def _install_advancing_clock(monkeypatch, base: float = 1000.0) -> None:
    # monotonic only advances when a file is parsed, so a deadline crosses deterministically.
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_parse = repo_map._imports_and_symbols_for_path

    def _advancing(path, **kwargs):
        clock["t"] += 1.0
        return original_parse(path, **kwargs)

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", _advancing)


def test_step3_top_level_builders_thread_deadline_to_partial(tmp_path: Path, monkeypatch) -> None:
    # moat P0-6 step 3: each top-level symbol builder converts deadline_seconds to one absolute
    # budget, threads it into build_repo_map, and surfaces partial:true on the wrapped output.
    _make_repo(tmp_path, 12)
    _install_advancing_clock(monkeypatch)  # deadline crosses after ~5 parses (base + 5.0)
    for builder in (
        repo_map.build_symbol_refs,
        repo_map.build_symbol_callers,
        repo_map.build_symbol_impact,
        repo_map.build_symbol_blast_radius,
    ):
        result = builder("f1", str(tmp_path), deadline_seconds=5.0)
        assert result.get("partial") is True, (
            f"{builder.__name__} dropped the deadline partial flag"
        )


def test_step3_deadline_none_leaves_builders_unbounded(tmp_path: Path) -> None:
    _make_repo(tmp_path, 4)
    result = repo_map.build_symbol_callers("f1", str(tmp_path))  # no deadline
    assert "partial" not in result
    assert "deadline_limit" not in result


def _make_caller_repo(root: Path, callers: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "target.py").write_text("def widget():\n    return 1\n", encoding="utf-8")
    for index in range(callers):
        (src / f"caller{index}.py").write_text(
            "from src.target import widget\n\n\ndef use():\n    return widget()\n", encoding="utf-8"
        )


def test_step6_caller_scan_honors_already_expired_deadline(tmp_path: Path) -> None:
    # moat P0-6 step 6: the CALLER-SCAN traversal (not just the repo-map parse) must honor the
    # deadline -- this is why central symbols hung past --deadline while leaf symbols didn't.
    _make_caller_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))  # full map, no deadline
    result = repo_map.build_symbol_callers_from_map(
        rm, "widget", deadline_monotonic=time.monotonic() - 1.0
    )
    assert result.get("partial") is True
    assert result["graph_completeness"] == "partial"
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_step6_caller_scan_no_deadline_is_complete(tmp_path: Path) -> None:
    _make_caller_repo(tmp_path, 4)
    rm = repo_map.build_repo_map(str(tmp_path))
    result = repo_map.build_symbol_callers_from_map(rm, "widget")  # no deadline
    assert "partial" not in result
    assert result["graph_completeness"] == "moderate"
    assert len(result["callers"]) >= 1  # found the real callers when unbounded
