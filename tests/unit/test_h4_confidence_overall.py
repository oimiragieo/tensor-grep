"""H4 audit: `edit_plan_seed.confidence` never produces an ``overall`` key, so both capsule
consumers (`agent_capsule._primary_target` :560 and `_confidence` :2256) fall back to a flat
0.9 default -- a weak lexical hit reports a confident answer and clears the >=0.75
ask-before-edit threshold. This file pins the fix:

  (1) the seed now derives a REAL ``overall`` from {file, symbol} (max; file anchors when no
      symbol matched; `test` is a separate validation axis and must not drag it down);
  (2) `build_agent_capsule_from_map` caps `primary_target.confidence` to the ladder-capped
      ``confidence.overall`` unconditionally, so a later budget/empty-snippets/primary-omitted
      downgrade can never leave `primary_target.confidence` at a higher raw seed value.

RED first on each; every assertion targets a pre-fix failure, not just a post-fix shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import agent_capsule, repo_map


def _write_symbol_project(
    tmp_path: Path, file_name: str = "impl.py", symbol_name: str = "process_widget_report"
) -> Path:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / file_name).write_text(
        f"def {symbol_name}(payload):\n    return payload\n",
        encoding="utf-8",
    )
    return project


# ---------------------------------------------------------------------------
# 1. Producer: the seed emits a DERIVED overall (not a flat 0.9), across BOTH seed
#    producers (heavy `_build_edit_plan_seed` + lightweight `_attach_lightweight_navigation_metadata`).
# ---------------------------------------------------------------------------


def test_derive_seed_overall_uses_max_file_symbol_and_ignores_test() -> None:
    assert repo_map._derive_seed_overall({"file": 0.2, "symbol": 0.15, "test": 0.0}) == 0.2
    assert repo_map._derive_seed_overall({"file": 0.9, "symbol": 0.6, "test": 0.0}) == 0.9
    assert repo_map._derive_seed_overall({"file": 0.4, "symbol": 0.0, "test": 1.0}) == 0.4
    assert repo_map._derive_seed_overall({"file": 0.4, "symbol": None, "test": 0.0}) == 0.4
    assert repo_map._derive_seed_overall({"file": 0.0, "symbol": 0.3, "test": 0.0}) == 0.3
    assert repo_map._derive_seed_overall({"file": 0.0, "symbol": 0.0, "test": 0.0}) == 0.0


def test_lightweight_seed_emits_derived_overall(tmp_path: Path) -> None:
    """The real lightweight producer (`build_context_edit_plan` -> ... ->
    `_attach_lightweight_navigation_metadata`) must emit ``overall`` == max(file, symbol),
    not silently omit it (which would leave consumers on the 0.9 default)."""
    project = _write_symbol_project(tmp_path)
    result = repo_map.build_context_edit_plan("process_widget_report", project)
    seed_confidence = result["edit_plan_seed"]["confidence"]
    assert "overall" in seed_confidence
    assert seed_confidence["overall"] == round(
        max(seed_confidence["file"], seed_confidence["symbol"]), 3
    )
    assert 0.0 <= seed_confidence["overall"] <= 1.0


def test_filtered_alignment_cap_inherits_into_overall(tmp_path: Path) -> None:
    """The heavy seed's 0.65 filtered-alignment cap must propagate into overall (H4 design:
    'overall inherits that cap'), so a capped seed can never report a higher overall."""
    target = repo_map._derive_seed_overall({"file": 0.9, "symbol": 0.9, "test": 0.3})
    capped = repo_map._derive_seed_overall({
        key: round(min(float(value), 0.65), 3)
        for key, value in {"file": 0.9, "symbol": 0.9, "test": 0.3}.items()
    })
    assert target == 0.9
    assert capped == 0.65


# ---------------------------------------------------------------------------
# 2. Consumer: primary_target.confidence must never exceed the ladder-capped overall,
#    even when the raw seed carried a high `overall` and a later ladder downgraded it.
# ---------------------------------------------------------------------------


def test_primary_target_capped_by_empty_snippets_ladder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _write_symbol_project(tmp_path)
    rm = repo_map.build_repo_map(project)
    resolved_file = str((project / "impl.py").resolve())

    def _no_snippet_render(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "routing_backend": "RepoMap",
            "routing_reason": "context-render",
            "semantic_provider": "native",
            "files": [resolved_file],
            "sources": [],
            "scan_limit": {"possibly_truncated": False},
            "partial": False,
            "validation_commands": [],
            "edit_plan_seed": {
                "primary_file": resolved_file,
                "primary_symbol": {"name": "process_widget_report", "kind": "function"},
                "confidence": {"overall": 0.9},
            },
            "navigation_pack": {},
            "candidate_edit_targets": {},
            "context_consistency": {
                "primary_file_included": False,
                "rendered_context_includes_primary": False,
            },
        }

    monkeypatch.setattr(repo_map, "build_context_render_from_map", _no_snippet_render)

    result = agent_capsule.build_agent_capsule_from_map(
        rm, "process_widget_report", max_tokens=8000
    )

    overall = result["confidence"]["overall"]
    # empty-snippets + primary-omitted ladder must land <= 0.55...
    assert overall <= 0.55
    # ...and the primary target must be capped to the SAME overall, never left at the 0.9 raw seed.
    assert result["primary_target"]["confidence"] == overall
    assert result["primary_target"]["confidence"] <= 0.55
    assert result["ask_user_before_editing"]["required"] is True
