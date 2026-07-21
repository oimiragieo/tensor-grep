"""Tests for the multi-project-workspace-root advisory on `tg agent` (CEO #2 auto-narrow,
2026-07-20) -- the agent-capsule sibling of test_orient_workspace_root_advisory.py.

`build_agent_capsule_from_map` now computes the SAME `_detect_workspace_root` (orient_capsule.py)
check `tg orient` uses, and ORs it into the existing scan-limit-truncation gate that already
populates `suggested_scope` (agent_capsule.py's TRAP-B block) -- so a genuine multi-project
workspace root gets `workspace_root_detected: true` + a proactive `suggested_scope` even when the
scan itself was small enough to complete without truncating. Additive + conditional (present only
when true/non-null), matching `suggested_scope`'s/`suggested_ignore`'s existing convention -- a
single-project repo's capsule stays byte-identical to before this fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tensor_grep.cli.orient_capsule as oc
from tensor_grep.cli.agent_capsule import build_agent_capsule


def _write_sibling_projects(root: Path, names: list[str]) -> None:
    for name in names:
        child = root / name
        child.mkdir(parents=True)
        (child / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        (child / "main.py").write_text(f"def run_{name}():\n    return 1\n", encoding="utf-8")


def _write_single_project(root: Path) -> None:
    (root / "pyproject.toml").write_text("[project]\nname = 'solo'\n", encoding="utf-8")
    src = root / "src"
    src.mkdir()
    (src / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (src / "helper.py").write_text("def helper():\n    return 2\n", encoding="utf-8")


def test_agent_capsule_surfaces_workspace_root_detected_and_suggested_scope(
    tmp_path: Path,
) -> None:
    hub = tmp_path / "core"
    hub.mkdir()
    (hub / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (hub / "hub.py").write_text(
        "def hub_fn():\n    return 1\n\n\ndef hub_fn2():\n    return 2\n", encoding="utf-8"
    )
    for name in ("siblingA", "siblingB"):
        d = tmp_path / name
        d.mkdir()
        (d / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (d / "lonely.py").write_text("x = 1\n", encoding="utf-8")

    payload = build_agent_capsule("hub_fn", tmp_path, max_tokens=4000)

    assert payload["workspace_root_detected"] is True
    assert payload.get("suggested_scope") is not None
    assert payload["suggested_scope"]["confidence"] == "heuristic"


def test_agent_capsule_does_not_perturb_exit_relevant_fields(tmp_path: Path) -> None:
    # Confirm the new fields don't perturb result_incomplete/partial -- the fields tg agent's
    # exit-code contract actually branches on.
    for name in ("proj1", "proj2", "proj3"):
        d = tmp_path / name
        d.mkdir()
        (d / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (d / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    payload = build_agent_capsule("f", tmp_path, max_tokens=4000)

    assert payload["workspace_root_detected"] is True
    assert "result_incomplete" not in payload
    assert "partial" not in payload


def test_agent_capsule_single_project_workspace_root_detected_absent(tmp_path: Path) -> None:
    """THE NEGATIVE / NO-REGRESSION GUARD (literal CEO fixture): one pyproject.toml at root, a
    plain src/ dir -- must never trigger, end to end, with no monkeypatching at all."""
    _write_single_project(tmp_path)

    payload = build_agent_capsule("run", tmp_path, max_tokens=4000)

    assert "workspace_root_detected" not in payload
    assert "suggested_scope" not in payload


def test_agent_capsule_workspace_root_detected_forced_true_is_additive_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolates the causal effect of the flag on the SAME underlying repo: forcing
    `_detect_workspace_root` True must ONLY add `workspace_root_detected` (+ newly populate the
    already-additive/conditional `suggested_scope`) -- every other capsule field is unaffected."""
    hub = tmp_path / "core"
    hub.mkdir()
    (hub / "hub.py").write_text("def hub_fn():\n    return 1\n", encoding="utf-8")
    (hub / "leaf_a.py").write_text("from hub import hub_fn\n", encoding="utf-8")
    (tmp_path / "misc").mkdir()
    (tmp_path / "misc" / "lonely.py").write_text("x = 1\n", encoding="utf-8")

    baseline = build_agent_capsule("hub_fn", tmp_path, max_tokens=4000)
    assert "workspace_root_detected" not in baseline

    monkeypatch.setattr(oc, "_detect_workspace_root", lambda *a, **k: True)
    forced_on = build_agent_capsule("hub_fn", tmp_path, max_tokens=4000)

    assert forced_on["workspace_root_detected"] is True

    additive_keys = {"workspace_root_detected", "suggested_scope"}
    baseline_stripped = {k: v for k, v in baseline.items() if k not in additive_keys}
    forced_on_stripped = {k: v for k, v in forced_on.items() if k not in additive_keys}
    assert baseline_stripped == forced_on_stripped


def test_agent_capsule_workspace_root_advisory_coexists_with_scan_limit_truncation(
    tmp_path: Path,
) -> None:
    """When BOTH triggers could apply (a truncated scan on a multi-project root), the payload must
    stay well-formed -- no duplicate-write crash, no contradictory suggested_scope."""
    for name in ("proj1", "proj2", "proj3"):
        d = tmp_path / name
        d.mkdir()
        (d / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        for i in range(4):
            (d / f"m{i}.py").write_text(f"def f_{name}_{i}():\n    return {i}\n", encoding="utf-8")

    payload = build_agent_capsule("f", tmp_path, max_tokens=4000, max_repo_files=4)

    assert payload["workspace_root_detected"] is True
    scope = payload.get("suggested_scope")
    if scope is not None:
        assert scope["confidence"] == "heuristic"
        assert isinstance(scope["dirs"], list) and scope["dirs"]
