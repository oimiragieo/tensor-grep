"""Pin session/daemon root anchoring: a subtree must resolve to its project root.

Closes G4.1 (session store cwd-keyed) and G4.2 (warm daemon unreachable from a subtree).
Both were reproduced on published v1.111.7; both trace to one function.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import session_store


def test_subtree_resolves_to_the_project_root(tmp_path: Path) -> None:
    """A path INSIDE a project must resolve to the project root, not to itself."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)

    assert session_store._resolve_root(sub) == tmp_path.resolve(), (
        "a subtree resolved to itself, so it gets its own session store and cannot see the "
        "daemon started at the project root"
    )


def test_project_root_still_resolves_to_itself(tmp_path: Path) -> None:
    """Control: the fix must not move the root when the caller already passes it."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert session_store._resolve_root(tmp_path) == tmp_path.resolve()


def test_subtree_under_a_nested_manifest_still_resolves_to_the_vcs_root(tmp_path: Path) -> None:
    """The bug a naive nearest-marker walk would leave behind.

    A directory holding its OWN language manifest, inside a git checkout, must still resolve to
    the checkout root -- otherwise `rust_core/` and `npm/` keep the original defect while `src/`
    appears fixed. Measured against the real repo before the resolver was written:
    nearest-marker gave rust_core/src -> rust_core and npm -> npm.
    """
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    nested = tmp_path / "rust_core"
    (nested / "src").mkdir(parents=True)
    (nested / "Cargo.toml").write_text("[package]\nname='y'\n", encoding="utf-8")

    assert session_store._resolve_root(nested / "src") == tmp_path.resolve(), (
        "a subtree with its own manifest resolved to that manifest's directory instead of the "
        "VCS root -- src/ would be fixed while rust_core/ silently keeps the defect"
    )


def test_no_marker_falls_back_to_todays_behaviour(monkeypatch, tmp_path: Path) -> None:
    """A directory with no project marker above it keeps the old semantics.

    Without this the change would silently relocate stores for every ad-hoc directory.

    THE TEST MUST CONTROL ITS OWN ENVIRONMENT, BUT NOT BY STUBBING THE THING IT TESTS. The first
    draft monkeypatched `_find_project_root` itself, which a codex audit (2026-08-22) correctly
    called out: it could not observe the real resolver at all, and passed against the PRE-CHANGE
    code too. The premise really is false on this machine -- `%TEMP%` holds a stray `Cargo.toml`
    and `$HOME` a `package.json`, so a pytest `tmp_path` genuinely HAS a marker ~5 levels up.
    The honest control is to bound the ASCENT so the real walk cannot leave the isolated subtree.
    """
    sub = tmp_path / "loose" / "dir"
    sub.mkdir(parents=True)

    # depth of `sub` below tmp_path is 2, so an ascent of 3 covers sub, loose, tmp_path and stops
    # strictly BELOW any ambient marker on the host.
    monkeypatch.setattr(session_store, "_MAX_PROJECT_ROOT_ASCENT", 3)
    assert session_store._resolve_root(sub) == sub.resolve()


def test_a_nested_project_anchors_to_itself_not_the_outer_checkout(tmp_path: Path) -> None:
    """The HIGH a codex audit (2026-08-22) found in the first draft of this fix.

    The first `_find_project_root` kept walking and returned the OUTERMOST `.git`, so a standalone
    project vendored inside another checkout anchored to the OUTER repo -- two unrelated trees
    then share one session store AND one daemon, which is a worse bug than the one being fixed.
    Reproduced before the fix landed: `<outer>/vendor/standalone/src` resolved to `<outer>`.
    """
    (tmp_path / ".git").mkdir()  # OUTER checkout
    inner = tmp_path / "vendor" / "standalone"
    (inner / "src").mkdir(parents=True)
    (inner / ".git").mkdir()  # INNER standalone project
    (inner / "pyproject.toml").write_text("[project]\nname='inner'\n", encoding="utf-8")

    assert session_store._resolve_root(inner / "src") == inner.resolve(), (
        "a vendored project anchored to the OUTER checkout -- it would share the outer repo's "
        "session store and daemon with an unrelated tree"
    )


def test_anchoring_reaches_the_daemon_not_only_the_session_store(tmp_path: Path) -> None:
    """G4.2 proper: the daemon lookup, not just the store.

    A codex audit (2026-08-22) noted that all the other tests here pin the RESOLVER or the SESSION
    STORE, so every one of them could pass while the daemon half of the fix did nothing. The
    daemon derives its metadata path from the same resolver (`session_daemon._nearby_daemon_roots`
    calls `_resolve_root`), and this asserts that seam directly: a subtree and the project root
    must name the SAME daemon metadata file, or a daemon started at the root is unreachable from
    the subtree.
    """
    from tensor_grep.cli import session_daemon

    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)

    from_root = session_daemon._daemon_metadata_path(session_store._resolve_root(tmp_path))
    from_sub = session_daemon._daemon_metadata_path(session_store._resolve_root(sub))

    assert from_sub == from_root, (
        f"the subtree looks for its daemon at {from_sub} while one started at the project root "
        f"registers at {from_root} -- the warm daemon is unreachable from a subtree (G4.2)"
    )


def test_file_argument_still_resolves_to_a_directory(tmp_path: Path) -> None:
    """Control for the existing `.parent` behaviour on a file path."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    f = tmp_path / "src" / "mod.py"
    f.parent.mkdir(parents=True)
    f.write_text("x = 1\n", encoding="utf-8")
    assert session_store._resolve_root(f) == tmp_path.resolve()


def test_session_opened_in_a_subtree_is_visible_from_the_project_root(tmp_path: Path) -> None:
    """The G4.1 symptom end to end: open in a subtree, read from the project root.

    The unit tests above pin the RESOLVER; this pins the user-visible behaviour the resolver
    exists to provide. Without it the resolver could be correct while the CLI still failed.

    A council seat (droid_glm) caught the first draft of this test: `session show` takes an
    OPTIONAL trailing PATH that defaults to `.`, so a version that omitted it would have read the
    test process's cwd -- the real repo -- instead of tmp_path, and passed or failed for a reason
    unrelated to the fix. Both invocations below pass their path explicitly.
    """
    import json

    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "mod.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = runner.invoke(app, ["session", "open", str(sub), "--json"])
    assert opened.exit_code == 0, opened.output
    session_id = json.loads(opened.stdout)["session_id"]

    # Addressed from the PROJECT ROOT, while the session was opened in the SUBTREE.
    shown = runner.invoke(app, ["session", "show", session_id, str(tmp_path), "--json"])
    assert shown.exit_code == 0, (
        f"session {session_id} was opened under {sub} and is invisible when addressed from the "
        f"project root {tmp_path} -- the two lookups disagree about the root. "
        f"Output: {shown.output}"
    )
