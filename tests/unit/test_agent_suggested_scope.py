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


# ---------------------------------------------------------------------------
# #179: suggested_scope must not point into a tree suggested_ignore already flags -- the tg-agent /
# tg-context-render sibling of orient's #168/#606 fix (test_orient_suggested_scope.py). #606 only
# threaded `deweighted_trees` through `build_orient_capsule`'s own `_suggested_scope_from_map` call;
# `build_context_render` (this module) and `build_agent_capsule_from_map` (agent_capsule.py)
# independently called `_suggested_scope_from_map(rm)` with no exclusion set at all, so either
# surface could still point an agent straight at a tree its own `suggested_ignore` already flags.
# ---------------------------------------------------------------------------


def _claude_densest_rm(root: Path) -> dict[str, Any]:
    """Same fixture shape as test_orient_suggested_scope.py's `_claude_densest_rm` (#168/#606): a
    hand-built repo map where `.claude/hooks` is the RAW-densest top-level directory (an internal
    import edge + 6 symbols, clearing the 1.5x margin against `src/` on its own) but is ALSO a
    STRONG-0 tool-config tree once run through `_detect_vendored_subtrees` (an on-disk
    `.claude`-named directory, matched on exact basename -- no manifest needed). `src/` is real,
    less-dense code that must win once `.claude` is excluded from the candidate set."""
    files = [
        str(root / ".claude" / "hooks" / "hookmain.cjs"),
        str(root / ".claude" / "hooks" / "hookb.cjs"),
        str(root / ".claude" / "hooks" / "hookc.cjs"),
        str(root / "src" / "hub.py"),
        str(root / "src" / "leaf_a.py"),
        str(root / "src" / "leaf_b.py"),
    ]
    imports = [
        {"file": str(root / ".claude" / "hooks" / "hookb.cjs"), "imports": ["hookmain"]},
        {"file": str(root / ".claude" / "hooks" / "hookc.cjs"), "imports": ["hookmain"]},
        {"file": str(root / "src" / "leaf_a.py"), "imports": ["hub"]},
        {"file": str(root / "src" / "leaf_b.py"), "imports": ["hub"]},
    ]
    symbols = [
        {
            "name": f"Hook{i}",
            "kind": "function",
            "file": str(root / ".claude" / "hooks" / "hookmain.cjs"),
            "line": i + 1,
        }
        for i in range(6)
    ] + [{"name": "Hub", "kind": "function", "file": str(root / "src" / "hub.py"), "line": 1}]
    return {
        "path": str(root),
        "files": files,
        "imports": imports,
        "symbols": symbols,
        "tests": [],
        "scan_limit": {
            "max_repo_files": len(files),
            "scanned_files": len(files),
            "possibly_truncated": True,
            "truncation_cause": "project-files",
        },
    }


def test_context_render_suggested_scope_excludes_claude_tool_config_dir(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#179: `build_context_render` (the wrapper behind `tg context-render`, and reused by any
    other `include_suggested_scope=True` caller) must exclude the same auto-detected tool-config
    tree from its own `_suggested_scope_from_map` call that `tg orient` already excludes (#168/
    #606) -- otherwise this sibling wrapper could point an agent straight at `.claude/` on a
    truncated scan of a harness repo."""
    fake_rm = _claude_densest_rm(tmp_path)
    monkeypatch.setattr(repo_map, "build_repo_map", lambda *_a, **_k: dict(fake_rm))

    render = repo_map.build_context_render("hook", tmp_path, include_suggested_scope=True)

    assert render["suggested_scope"] is not None
    assert render["suggested_scope"]["dirs"] == [str(tmp_path / "src")]


def test_agent_capsule_suggested_scope_excludes_claude_tool_config_dir_on_truncated_scan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#179 end-to-end on `tg agent` (CONFIRMED via dogfood on the shipped v1.76.5 wheel):
    `.claude/` is the raw-densest top-level directory on a truncated scan of a harness repo, and
    `suggested_ignore` already flags it (M2, the SAME `_detect_vendored_subtrees` detection). The
    two fields must not contradict each other -- `suggested_scope` must point at real code (`src/`),
    never at the tree the SAME capsule tells the agent to ignore. Mirrors orient's #168/#606 fix
    (test_orient_suggested_scope.py) applied to the agent-capsule TRAP-B call site
    (agent_capsule.py)."""
    fake_rm = _claude_densest_rm(tmp_path)
    monkeypatch.setattr(repo_map, "build_repo_map", lambda *_a, **_k: dict(fake_rm))

    payload = build_agent_capsule("hook", tmp_path, max_tokens=2000)

    assert payload["suggested_ignore"] == [".claude/**"]
    assert payload["suggested_scope"] is not None
    assert payload["suggested_scope"]["dirs"] == [str(tmp_path / "src")]


def test_agent_capsule_suggested_scope_none_when_only_claude_has_signal(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """#179: if the truncated scan captured ONLY the ignored tool-config tree (no other code
    directory at all), suggested_scope must degrade to None rather than guess -- mirrors orient's
    equivalent guard (test_build_orient_capsule_suggested_scope_none_when_only_claude_has_signal)."""
    files = [
        str(tmp_path / ".claude" / "hooks" / "hookmain.cjs"),
        str(tmp_path / ".claude" / "hooks" / "hookb.cjs"),
    ]
    fake_rm = {
        "path": str(tmp_path),
        "files": files,
        "imports": [
            {"file": str(tmp_path / ".claude" / "hooks" / "hookb.cjs"), "imports": ["hookmain"]}
        ],
        "symbols": [
            {
                "name": "Hook",
                "kind": "function",
                "file": str(tmp_path / ".claude" / "hooks" / "hookmain.cjs"),
                "line": 1,
            }
        ],
        "tests": [],
        "scan_limit": {
            "max_repo_files": len(files),
            "scanned_files": len(files),
            "possibly_truncated": True,
            "truncation_cause": "project-files",
        },
    }
    monkeypatch.setattr(repo_map, "build_repo_map", lambda *_a, **_k: dict(fake_rm))

    payload = build_agent_capsule("hook", tmp_path, max_tokens=2000)

    assert payload["suggested_ignore"] == [".claude/**"]
    assert "suggested_scope" not in payload
