"""Tests for `suggested_scope` on the AGENT capsule (#133 dogfood, 2026-07-11).

The v1.61.2 dogfood flagged that `tg orient` offers a `suggested_scope` narrowing hint on a
truncated scan but `tg agent --json` did not -- an agent driving off the capsule got an
incomplete map with no guidance. `build_agent_capsule` now threads `include_suggested_scope=True`
into `repo_map.build_context_render`, which computes the SAME centrality-weighted directory rollup
`tg orient` emits -- from the raw map it already built (no second scan) -- and the capsule copies
it onto its result as an additive, conditional key (present only on a truncated scan with a clear
winner). The `_suggested_scope_from_map` ranking itself is covered by test_orient_suggested_scope.py;
these tests cover the new PLUMBING: the render flag, its gate, and the capsule copy.
"""

from pathlib import Path
from typing import Any

import tensor_grep.cli.agent_capsule as agent_capsule
import tensor_grep.cli.repo_map as repo_map
from tensor_grep.cli.agent_capsule import build_agent_capsule


def _build_truncating_repo(root: Path) -> None:
    # Two subdirs, 6 files each = 12 files; a small --max-repo-files truncates the walk. Whichever
    # directory wins the real centrality roll-up, the hint must be well-formed or absent, never a
    # crash or a malformed shape.
    for sub in ("alpha", "beta"):
        d = root / sub
        d.mkdir()
        for i in range(6):
            (d / f"m{i}.py").write_text(f"def f_{sub}_{i}():\n    return {i}\n", encoding="utf-8")


def test_context_render_emits_suggested_scope_on_truncated_scan(tmp_path: Path) -> None:
    _build_truncating_repo(tmp_path)
    render = repo_map.build_context_render(
        "m", tmp_path, max_repo_files=4, include_suggested_scope=True
    )
    scope = render.get("suggested_scope")
    if scope is not None:
        assert scope["confidence"] == "heuristic"
        assert isinstance(scope["dirs"], list) and scope["dirs"]


def test_context_render_omits_suggested_scope_when_flag_off(tmp_path: Path) -> None:
    # Default (flag off) must NOT emit suggested_scope -- other build_context_render callers
    # (the `context` command, MCP) stay byte-identical; the field is opt-in for the agent path.
    _build_truncating_repo(tmp_path)
    render = repo_map.build_context_render("m", tmp_path, max_repo_files=4)
    assert "suggested_scope" not in render


def test_context_render_omits_suggested_scope_on_complete_small_scan(tmp_path: Path) -> None:
    # A complete (non-truncated) scan has nothing left to narrow -> no hint even with the flag on.
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("def helper():\n    pass\n", encoding="utf-8")
    render = repo_map.build_context_render("run", tmp_path, include_suggested_scope=True)
    assert "suggested_scope" not in render


def test_agent_capsule_copies_suggested_scope_from_render(tmp_path: Path, monkeypatch: Any) -> None:
    # Deterministic copy-path check: whatever `suggested_scope` the inner render carries, the
    # capsule surfaces it verbatim on its result. Monkeypatch the render (not the map) so this
    # does not depend on the real centrality/truncation internals.
    #
    # task #108: build_agent_capsule now delegates to build_agent_capsule_from_map, which calls
    # build_context_render_from_map (NOT the build_context_render wrapper) -- real_render must
    # be retargeted to match, and its call shape is (rm, query, **kwargs), not (query, path,
    # **kwargs). include_suggested_scope was never a build_context_render_from_map kwarg (it is
    # wrapper-only), so there is nothing left to filter out of kwargs before forwarding.
    hint = {"dirs": [str(tmp_path / "core")], "confidence": "heuristic"}
    real_render = repo_map.build_context_render_from_map

    def _render_with_hint(*args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = real_render(*args, **kwargs)
        payload["suggested_scope"] = hint
        return payload

    monkeypatch.setattr(agent_capsule.repo_map, "build_context_render_from_map", _render_with_hint)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = build_agent_capsule("x", tmp_path)
    assert result.get("suggested_scope") == hint


def test_agent_capsule_omits_suggested_scope_when_render_has_none(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # When the inner render carries no hint (complete scan / flat signal), the capsule must NOT
    # stamp an empty-but-present key -- byte-identical to a pre-feature capsule. See the
    # task #108 retargeting note on the sibling test above.
    real_render = repo_map.build_context_render_from_map

    def _render_no_hint(*args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = real_render(*args, **kwargs)
        payload.pop("suggested_scope", None)
        return payload

    monkeypatch.setattr(agent_capsule.repo_map, "build_context_render_from_map", _render_no_hint)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = build_agent_capsule("x", tmp_path)
    assert "suggested_scope" not in result
