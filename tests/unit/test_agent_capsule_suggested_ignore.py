"""Tests for `suggested_ignore` parity between `tg orient` and `tg agent` (M2).

`_detect_vendored_subtrees` + the de-weight-based ranking were already SHARED between
`build_orient_capsule` and `build_agent_capsule` (`_build_context_pack_from_map`'s own
`auto_deweight` pass, repo_map.py) -- only the `suggested_ignore` ready-to-paste `--ignore`-glob
HINT was orient-only; `tg agent REPO "task" --json` had no `suggested_ignore` key at all, so an
agent that wanted a hard exclude had to hand-derive the glob or shell out to `tg orient` first.

`build_agent_capsule_from_map` now populates the SAME key, via the SAME
`orient_capsule._suggested_ignore_from_deweighted_trees` builder over the SAME
`_detect_vendored_subtrees(rm)` result `tg orient` uses -- so a repo with a detected vendor/skill
tree gets an IDENTICAL hint from both commands. Additive-only: present only when non-empty
(mirroring `suggested_scope`'s convention, see test_agent_capsule_tie_suggested_scope.py) -- never
an empty-but-present key, and no existing capsule key is renamed or removed.
"""

from pathlib import Path

from tensor_grep.cli.agent_capsule import build_agent_capsule
from tensor_grep.cli.orient_capsule import build_orient_capsule


def test_agent_capsule_suggested_ignore_matches_orient_for_bare_vendor_tree(
    tmp_path: Path,
) -> None:
    """A bare `third_party/` tree (no nested manifest, STRONG-0) must produce the identical
    `suggested_ignore` from `tg agent` as it does from `tg orient` for the same repo. Uses
    `third_party/` rather than `node_modules/`/`vendor/`/`external_repos/` deliberately -- those
    three are ALSO in `repo_map._SKIP_DIR_NAMES`, so the repo-map walker never descends into them
    at all (a separate, pre-existing, stronger-than-deweight protection); a real scan would never
    see their contents regardless of this fix."""
    project = tmp_path / "workspace"
    pkg_dir = project / "third_party" / "left-pad"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "index.js").write_text("module.exports = () => {};\n", encoding="utf-8")
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.py").write_text(
        "def process_widget():\n    return 1\n", encoding="utf-8"
    )

    orient_payload = build_orient_capsule(project, max_tokens=500)
    agent_payload = build_agent_capsule("process widget", project, max_tokens=2000)

    assert orient_payload["suggested_ignore"] == ["third_party/**"]
    assert agent_payload["suggested_ignore"] == orient_payload["suggested_ignore"]


def test_agent_capsule_suggested_ignore_matches_orient_for_skill_tree(tmp_path: Path) -> None:
    """A `skills/` tree of leaf skills (STRONG-3 shape heuristic) must also parity-match."""
    project = tmp_path / "workspace"
    skills_dir = project / "core" / "skills"
    for name in ("alpha", "beta", "gamma"):
        leaf = skills_dir / name
        leaf.mkdir(parents=True)
        (leaf / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        (leaf / "run.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.py").write_text(
        "def process_widget():\n    return 1\n", encoding="utf-8"
    )

    orient_payload = build_orient_capsule(project, max_tokens=500)
    agent_payload = build_agent_capsule("process widget", project, max_tokens=2000)

    assert orient_payload["suggested_ignore"] == ["core/skills/**"]
    assert agent_payload["suggested_ignore"] == orient_payload["suggested_ignore"]


def test_agent_capsule_suggested_ignore_absent_when_nothing_deweighted(tmp_path: Path) -> None:
    """An ordinary repo with nothing to de-weight must NOT gain a `suggested_ignore` key -- additive
    only, mirroring `suggested_scope`'s "absent, never empty-but-present" contract."""
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "main.py").write_text("def solo_widget():\n    return 1\n", encoding="utf-8")

    payload = build_agent_capsule("solo widget", project, max_tokens=2000)

    assert "suggested_ignore" not in payload


def test_agent_capsule_suggested_ignore_absent_for_genuine_skills_package(tmp_path: Path) -> None:
    """The false-positive guard end-to-end on the agent capsule: a genuine product `skills/`
    package imported from `main.py` must not appear in `suggested_ignore`."""
    project = tmp_path / "workspace"
    plugin = project / "skills" / "plugin_a"
    plugin.mkdir(parents=True)
    (plugin / "SKILL.md").write_text("# plugin_a\n", encoding="utf-8")
    (plugin / "handler.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (project / "main.py").write_text("from skills.plugin_a.handler import run\n", encoding="utf-8")

    payload = build_agent_capsule("run", project, max_tokens=2000)

    assert "suggested_ignore" not in payload


def test_agent_capsule_suggested_ignore_absent_for_skills_package_via_symbol_import(
    tmp_path: Path,
) -> None:
    """Opus-gate MUST-FIX regression via M2: the symbol/subpackage-import false positive
    (`from skills.auth import Auth`, subpackages with `__init__.py`, no SKILL.md) must not reach the
    `tg agent` capsule either -- `suggested_ignore` absent. This is the moat-facing surface the gate
    called out: M2 wires the same detection into the agent/daemon path, so the FP had to be proven
    gone on BOTH commands, not just `tg orient`."""
    project = tmp_path / "workspace"
    for sub in ("auth", "db"):
        pkg = project / "skills" / sub
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(f"class {sub.capitalize()}:\n    pass\n", encoding="utf-8")
    (project / "main.py").write_text(
        "from skills.auth import Auth\n\ndef go():\n    return Auth()\n", encoding="utf-8"
    )

    payload = build_agent_capsule("auth", project, max_tokens=2000)

    assert "suggested_ignore" not in payload


def test_agent_capsule_suggested_ignore_respects_explicit_ignore_flag(tmp_path: Path) -> None:
    """`--ignore` (hard exclude) is applied to `rm` BEFORE the M2 suggested_ignore recompute -- a
    tree the caller already excluded outright must not also show up as a suggestion."""
    project = tmp_path / "workspace"
    pkg_dir = project / "third_party" / "left-pad"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "index.js").write_text("module.exports = () => {};\n", encoding="utf-8")
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.py").write_text(
        "def process_widget():\n    return 1\n", encoding="utf-8"
    )

    payload = build_agent_capsule(
        "process widget", project, max_tokens=2000, ignore=("third_party/**",)
    )

    assert "suggested_ignore" not in payload
