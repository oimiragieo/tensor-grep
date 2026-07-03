"""TG-4: blast-radius-render was ~70x slower than blast-radius --json (3.5 min vs 3s) on a
high-fan-in symbol. Root cause: build_symbol_blast_radius_render_from_map called the expensive
build_symbol_source_from_map once PER candidate symbol in the top files, bounded only by
accumulating max_sources — so a symbol whose candidates rarely yield a matching source scanned
them all. The fix caps the expensive per-candidate lookups. This test proves the bound
deterministically (a spy on the expensive call), without needing to reproduce the wall-clock.
"""

from __future__ import annotations

import pytest

import tensor_grep.cli.repo_map as rm


def test_render_bounds_expensive_source_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1000 candidate symbols all in the single top file; none of them will yield a source that
    # matches the current file (the spy returns an OTHER.py source), so WITHOUT the cap the loop
    # would call build_symbol_source_from_map 1000 times.
    monkeypatch.setattr(
        rm,
        "build_symbol_blast_radius_from_map",
        lambda repo_map, symbol, **k: {
            "files": ["f.py"],
            "file_matches": [],
            "file_summaries": [],
            "symbols": [{"file": "f.py", "name": f"s{i}", "score": 1.0} for i in range(1000)],
        },
    )
    calls = {"n": 0}

    def _spy_source(repo_map: object, name: str, **k: object) -> dict:
        calls["n"] += 1
        # Source file != the candidate's file "f.py" -> inner loop skips it -> nothing accumulates
        # -> the outer loop keeps going until the candidate cap (the pathology being bounded).
        return {"sources": [{"file": "OTHER.py", "start_line": 1, "end_line": 1, "text": "x"}]}

    monkeypatch.setattr(rm, "build_symbol_source_from_map", _spy_source)
    # Neutralize the expensive rendering tail so this isolates the candidate loop.
    monkeypatch.setattr(
        rm, "_render_context_string_and_sections", lambda *a, **k: ("", [], False, 0, [])
    )
    monkeypatch.setattr(rm, "_attach_edit_plan_metadata", lambda repo_map, payload, **k: payload)

    rm.build_symbol_blast_radius_render_from_map({}, "target")

    # Bounded: max(max_sources*8, 24) = 40 for default max_sources=5. Pre-fix: 1000 lookups.
    assert calls["n"] <= 40, f"expected <=40 expensive lookups, got {calls['n']}"


def test_render_still_collects_sources_when_candidates_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: when candidates DO yield matching sources, rendering still collects up to
    # max_sources (the cap must not starve the normal path).
    monkeypatch.setattr(
        rm,
        "build_symbol_blast_radius_from_map",
        lambda repo_map, symbol, **k: {
            "files": ["f.py"],
            "file_matches": [],
            "file_summaries": [],
            "symbols": [{"file": "f.py", "name": f"s{i}", "score": 1.0} for i in range(20)],
        },
    )
    monkeypatch.setattr(
        rm,
        "build_symbol_source_from_map",
        lambda repo_map, name, **k: {
            "sources": [{"file": "f.py", "start_line": 1, "end_line": 2, "text": f"src {name}"}]
        },
    )
    monkeypatch.setattr(
        rm, "_render_context_string_and_sections", lambda *a, **k: ("", [], False, 0, [])
    )
    monkeypatch.setattr(rm, "_attach_edit_plan_metadata", lambda repo_map, payload, **k: payload)

    result = rm.build_symbol_blast_radius_render_from_map({}, "target", max_sources=5)
    assert len(result["sources"]) == 5  # collected up to the cap, not starved
