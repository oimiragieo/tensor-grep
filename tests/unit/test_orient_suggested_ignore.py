"""Tests for build_orient_capsule's `suggested_ignore` hint (#164).

When auto-deweight (`_detect_vendored_subtrees`) finds a vendor/skill/tool-config subtree to
de-weight, `suggested_ignore` surfaces the deweighted tree roots as ready-to-paste `--ignore` globs
(e.g. `.claude/**`) so an agent that wants a HARD exclude (not just a lowered centrality score)
doesn't have to hand-derive the glob syntax. `None` -- never an empty list -- when nothing was
deweighted, mirroring `suggested_scope`'s never-guess-empty convention (audit #93 SUB-2 sibling)."""

from pathlib import Path

from tensor_grep.cli.orient_capsule import _apply_ignore_globs, build_orient_capsule


def test_suggested_ignore_none_when_nothing_deweighted(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("def helper():\n    pass\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_tokens=500)

    assert payload["suggested_ignore"] is None
    assert payload["deweighted_trees"] == []


def test_suggested_ignore_populated_for_claude_tool_config_dir(tmp_path: Path) -> None:
    hooks_dir = tmp_path / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "run-hook.cjs").write_text("module.exports = () => {};\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_tokens=500)

    assert payload["suggested_ignore"] == [".claude/**"]
    assert payload["deweighted_trees"]
    assert payload["deweighted_trees"][0]["path"] == str((tmp_path / ".claude").resolve())


def test_suggested_ignore_absent_when_auto_deweight_disabled(tmp_path: Path) -> None:
    hooks_dir = tmp_path / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "run-hook.cjs").write_text("module.exports = () => {};\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_tokens=500, auto_deweight=False)

    assert payload["suggested_ignore"] is None
    assert payload["deweighted_trees"] == []


def test_suggested_ignore_glob_round_trips_through_apply_ignore_globs(tmp_path: Path) -> None:
    # The glob suggested_ignore emits must actually be consumable by `--ignore`: feed it back
    # through the same `_apply_ignore_globs` the CLI uses and confirm the .claude file is dropped.
    hooks_dir = tmp_path / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "run-hook.cjs").write_text("module.exports = () => {};\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_tokens=500)
    glob = payload["suggested_ignore"][0]

    rm = {
        "path": str(tmp_path),
        "files": [str(hooks_dir / "run-hook.cjs"), str(tmp_path / "main.py")],
        "symbols": [],
        "imports": [],
    }
    filtered = _apply_ignore_globs(rm, (glob,))
    assert filtered["files"] == [str(tmp_path / "main.py")]


# ---------------------------------------------------------------------------
# M1: whole vendor/skill tree globs (not just nested-manifest islands).
# ---------------------------------------------------------------------------


def test_suggested_ignore_whole_vendor_tree_without_manifest(tmp_path: Path) -> None:
    # Task scenario (a): a bare vendor tree (no nested manifest) must surface its OWN whole-tree
    # glob in `suggested_ignore` -- not just a narrower nested-manifest island. Uses `third_party/`
    # rather than `node_modules/`/`vendor/`/`external_repos/` deliberately: those three are ALSO in
    # `repo_map._SKIP_DIR_NAMES`, so the repo-map WALKER never descends into them at all (a
    # separate, pre-existing, stronger-than-deweight protection) -- a real end-to-end scan would
    # never see their contents regardless of this fix, so they can't exercise the STRONG-0
    # detection path end-to-end this way. `test_orient_deweight_vendored.py` covers all 5 STRONG-0
    # vendor names (including `node_modules`) via a synthetic (walker-bypassing) `rm` fixture.
    pkg_dir = tmp_path / "third_party" / "left-pad"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "index.js").write_text("module.exports = () => {};\n", encoding="utf-8")
    (tmp_path / "app.js").write_text("require('left-pad');\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_tokens=500)

    assert payload["suggested_ignore"] == ["third_party/**"]


def test_suggested_ignore_whole_skill_tree_of_leaf_skills(tmp_path: Path) -> None:
    # Task scenario (b): a `core/skills/` tree of many leaf skills (each its own SKILL.md + a small
    # script) surfaces the WHOLE-tree glob, not a per-leaf breakdown.
    skills_dir = tmp_path / "core" / "skills"
    for name in ("alpha", "beta", "gamma"):
        leaf = skills_dir / name
        leaf.mkdir(parents=True)
        (leaf / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        (leaf / "run.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_tokens=500)

    assert payload["suggested_ignore"] == ["core/skills/**"]


def test_suggested_ignore_dedups_nested_manifest_island_inside_skill_tree(tmp_path: Path) -> None:
    # Task scenario 3 (dedup): `core/skills/` qualifies via the STRONG-3 shape heuristic AND one of
    # its own children (`conpty-run/`) independently qualifies via a nested manifest (STRONG-1) --
    # only the OUTER whole-tree glob must be emitted, never both `core/skills/**` AND the narrower
    # `core/skills/conpty-run/**`.
    skills_dir = tmp_path / "core" / "skills"
    for name in ("alpha", "beta"):
        leaf = skills_dir / name
        leaf.mkdir(parents=True)
        (leaf / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        (leaf / "run.py").write_text("def run():\n    pass\n", encoding="utf-8")
    nested = skills_dir / "conpty-run"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("# conpty-run\n", encoding="utf-8")
    (nested / "package.json").write_text('{"name": "conpty-run"}\n', encoding="utf-8")
    (nested / "index.js").write_text("module.exports = () => {};\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_tokens=500)

    assert payload["suggested_ignore"] == ["core/skills/**"]


def test_suggested_ignore_absent_for_genuine_skills_package_imported_across_repo(
    tmp_path: Path,
) -> None:
    # Task scenario (c) -- the false-positive guard end-to-end: a genuine product `skills/` package
    # imported from `main.py` must NOT appear in `suggested_ignore`, even though its one plugin
    # subfolder is leaf-shaped (its own SKILL.md).
    plugin = tmp_path / "skills" / "plugin_a"
    plugin.mkdir(parents=True)
    (plugin / "SKILL.md").write_text("# plugin_a\n", encoding="utf-8")
    (plugin / "handler.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from skills.plugin_a.handler import run\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_tokens=500)

    assert payload["suggested_ignore"] is None
    assert payload["deweighted_trees"] == []


def test_suggested_ignore_absent_for_skills_package_via_subpackage_symbol_import(
    tmp_path: Path,
) -> None:
    # Opus-gate MUST-FIX regression, end-to-end: a genuine product `skills/` package consumed via
    # the COMMON idiom `from skills.auth import Auth` (symbol/subpackage import, subpackages carry
    # `__init__.py`, no SKILL.md) must yield `suggested_ignore is None`. This is the exact form the
    # stem-only import graph can't resolve -- pre-STRONG-3 `main` returned None here, so it is a
    # regression the manifest-required + `__init__.py`-refusal guards must prevent.
    for sub in ("auth", "db"):
        pkg = tmp_path / "skills" / sub
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(f"class {sub.capitalize()}:\n    pass\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from skills.auth import Auth\n\ndef go():\n    return Auth()\n", encoding="utf-8"
    )

    payload = build_orient_capsule(tmp_path, max_tokens=500)

    assert payload["suggested_ignore"] is None
    assert payload["deweighted_trees"] == []


def test_suggested_ignore_absent_for_skills_dir_with_init_py(tmp_path: Path) -> None:
    # Opus-gate MUST-FIX regression, end-to-end: a `skills/__init__.py` at the tree root is an
    # unambiguous real-Python-package marker -- STRONG-3 is refused, `suggested_ignore is None`.
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "__init__.py").write_text("from .auth import Auth\n", encoding="utf-8")
    leaf = skills_dir / "auth"
    leaf.mkdir()
    (leaf / "SKILL.md").write_text("# auth\n", encoding="utf-8")
    (leaf / "impl.py").write_text("class Auth:\n    pass\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("from skills import Auth\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_tokens=500)

    assert payload["suggested_ignore"] is None
    assert payload["deweighted_trees"] == []
