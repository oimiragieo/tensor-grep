from types import SimpleNamespace

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
