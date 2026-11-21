from pathlib import Path
from types import SimpleNamespace

import pytest

from tensor_grep.cli import repo_map_coverage_gaps as gaps


def test_expired_coverage_scan_sets_deadline_and_does_not_cache(tmp_path) -> None:
    source = tmp_path / "a.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    deadline = SimpleNamespace(hit=False)
    gaps._clear_source_coverage_cache()

    assert (
        gaps.source_coverage_gaps([source], tmp_path, deadline_monotonic=0.0, deadline_hit=deadline)
        == []
    )
    assert deadline.hit is True
    assert gaps._MEMO is None


def test_partial_coverage_classification_is_recomputed(tmp_path, monkeypatch) -> None:
    source = tmp_path / "a.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    calls = 0

    def controlled(files, root, *, deadline_monotonic=None, deadline_hit=None):
        nonlocal calls
        calls += 1
        deadline_hit.hit = True
        return {("unreadable", "python"): {"files": ["a.py"], "causes": set()}}

    monkeypatch.setattr(gaps, "_classify_uncached", controlled)
    gaps._clear_source_coverage_cache()

    for _ in range(2):
        deadline = SimpleNamespace(hit=False)
        result = gaps._classify([source], tmp_path, deadline_hit=deadline)
        assert result
        assert deadline.hit is True
        assert gaps._MEMO is None
    assert calls == 2


def test_deadline_crossed_during_final_stat_does_not_reuse_cache(tmp_path, monkeypatch) -> None:
    source = tmp_path / "a.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    gaps._clear_source_coverage_cache()
    gaps._classify([source], tmp_path)
    assert gaps._MEMO is not None

    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(gaps.time, "monotonic", lambda: next(ticks))
    deadline = SimpleNamespace(hit=False)

    assert gaps._classify([source], tmp_path, deadline_monotonic=1.0, deadline_hit=deadline) == {}
    assert deadline.hit is True


def test_deadline_crossed_while_classifying_last_file_is_reported_and_not_cached(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "a.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    clock = {"now": 0.0}
    reads = 0

    def read_after_deadline(_path: str) -> str:
        nonlocal reads
        reads += 1
        clock["now"] = 2.0
        return "def value():\n    return 1\n"

    monkeypatch.setattr(gaps.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr("tensor_grep.cli.repo_map._read_source_text_cached", read_after_deadline)
    gaps._clear_source_coverage_cache()
    deadline = SimpleNamespace(hit=False)

    assert (
        gaps.source_coverage_gaps([source], tmp_path, deadline_monotonic=1.0, deadline_hit=deadline)
        == []
    )
    assert reads == 1
    assert deadline.hit is True
    assert gaps._MEMO is None


def test_expired_language_gap_scan_skips_later_scope_probes(tmp_path, monkeypatch) -> None:
    source = tmp_path / "a.ts"
    source.write_text("export const value = 1;\n", encoding="utf-8")
    deadline = SimpleNamespace(hit=False)

    def unexpected_scope_probe(*args, **kwargs):
        raise AssertionError("scope probing must not run after the deadline")

    monkeypatch.setattr(gaps, "js_ts_scope_gap", unexpected_scope_probe)

    assert (
        gaps._language_coverage_gaps_for_universe(
            [source], tmp_path, deadline_monotonic=0.0, deadline_hit=deadline
        )
        == []
    )
    assert deadline.hit is True


def test_language_gap_scan_rechecks_deadline_before_scope_probe(tmp_path, monkeypatch) -> None:
    source = tmp_path / "a.ts"
    source.write_text("export const value = 1;\n", encoding="utf-8")
    deadline = SimpleNamespace(hit=False)
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(gaps.time, "monotonic", lambda: next(ticks, 2.0))

    def unexpected_scope_probe(*args, **kwargs):
        raise AssertionError("scope probing must not start after the deadline")

    monkeypatch.setattr(gaps, "js_ts_scope_gap", unexpected_scope_probe)

    gaps._language_coverage_gaps_for_universe(
        [source], tmp_path, deadline_monotonic=1.0, deadline_hit=deadline
    )
    assert deadline.hit is True


def test_language_gap_scan_rechecks_deadline_after_scope_probe(tmp_path, monkeypatch) -> None:
    source = tmp_path / "a.ts"
    source.write_text("export const value = 1;\n", encoding="utf-8")
    deadline = SimpleNamespace(hit=False)
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(gaps.time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(gaps, "js_ts_scope_gap", lambda *args, **kwargs: None)

    def unexpected_source_probe(*args, **kwargs):
        raise AssertionError("source probing must not start after the deadline")

    monkeypatch.setattr(gaps, "source_coverage_gaps", unexpected_source_probe)

    gaps._language_coverage_gaps_for_universe(
        [source], tmp_path, deadline_monotonic=1.0, deadline_hit=deadline
    )
    assert deadline.hit is True


def test_js_ts_scope_gap_stops_mid_pass_on_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from tensor_grep.cli import js_ts_scope_gap as gap_mod
    from tensor_grep.cli.repo_map_coverage_gaps import _language_coverage_gaps_for_universe

    deadline = time.monotonic() + 60.0
    monkeypatch.setattr(gap_mod.time, "monotonic", lambda: deadline + 1.0)
    ts_file = tmp_path / "a" / "b" / "c" / "app.ts"
    ts_file.parent.mkdir(parents=True)
    ts_file.write_text("export const x = 1;\n", encoding="utf-8")

    hit = SimpleNamespace(hit=False)
    gaps_list = _language_coverage_gaps_for_universe(
        [ts_file],
        ts_file.parent,
        deadline_monotonic=deadline,
        deadline_hit=hit,
    )
    assert hit.hit is True
    assert gaps_list == []
