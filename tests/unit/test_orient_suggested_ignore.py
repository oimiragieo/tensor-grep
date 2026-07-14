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
