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


def test_no_marker_falls_back_to_todays_behaviour(tmp_path: Path) -> None:
    """A directory with no project marker above it keeps the old semantics.

    Without this the change would silently relocate stores for every ad-hoc directory.

    THIS TEST HAS NO MONKEYPATCH, AND THAT IS THE POINT -- it needed one twice, and both times the
    control was wrong in a different way:

      1. The first draft stubbed `_find_project_root` itself. A codex audit called it out: it
         could not observe the real resolver at all, and passed against the PRE-CHANGE code too.
      2. The replacement bounded `_MAX_PROJECT_ROOT_ASCENT` on `session_store`. Splitting the
         resolver into `session_root` silently DISARMED that: Python resolves the bare name
         through the DEFINING module's globals, so rebinding the re-exported copy left the real
         reader on 24. Measured after the split -- patch `session_store`, `session_root` still
         sees 24. The test kept passing while controlling nothing, which is the worst outcome
         available.

    Both controls existed because `tmp_path` lives under `%TEMP%`, which holds a stray
    `Cargo.toml` on this machine, so the stated premise ("no marker anywhere above") was FALSE.
    `_shared_territory_roots` now makes the premise TRUE by construction -- temp and home are
    denied outright -- so the honest test is the plain assertion with no control at all.

    What guards that: `test_a_project_under_the_temp_dir_does_not_anchor_to_the_temp_dir` below,
    which is perturbation-proved (drop the deny rule from the manifest pass and it FAILS). If that
    deny rule is ever removed, this test fails too rather than passing vacuously.
    """
    sub = tmp_path / "loose" / "dir"
    sub.mkdir(parents=True)

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
    """G4.2 through the daemon's OWN entry point, not through the resolver twice.

    Two codex rounds landed on this test. Round 1: none of the six tests touched the daemon at
    all, so every one could pass while the G4.2 half did nothing. Round 2 caught the fix for that
    -- the replacement called `session_store._resolve_root` itself on both inputs and compared the
    results, which exercises the RESOLVER twice and would pass even if `session_daemon` stopped
    resolving subtree roots entirely. A test that re-runs the function under test on both arms
    cannot observe the caller it claims to cover.

    So this drives `get_session_daemon_status`, the daemon's public path-taking entry: register a
    daemon at the project root, ask from a SUBTREE, and require the answer to name the root.
    """
    import json

    from tensor_grep.cli import session_daemon

    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)

    metadata_path = session_daemon._daemon_metadata_path(tmp_path.resolve())
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps({"pid": 999999, "port": 65000, "token": "t", "root": str(tmp_path.resolve())}),
        encoding="utf-8",
    )

    status = session_daemon.get_session_daemon_status(str(sub))

    assert Path(status["root"]).resolve() == tmp_path.resolve(), (
        f"asked from the subtree {sub}, the daemon layer reported root {status.get('root')} "
        f"instead of the project root {tmp_path} -- a daemon started at the root is unreachable "
        f"from a subtree (G4.2)"
    )


def test_a_project_under_the_temp_dir_does_not_anchor_to_the_temp_dir(tmp_path: Path) -> None:
    """Shared territory is never a project root -- found by a REGRESSION, not by review.

    An earlier revision of this slice documented this as an accepted residual risk. It then broke
    `test_context_render_warm_daemon_bounds_suggested_edits_same_as_cold`, which passed on
    `origin/main` and failed here: the test builds a project under `tmp_path`, starts a real
    daemon at it, and the client resolved that project to `%TEMP%` itself (measured), so the
    request silently fell back to the COLD route -- `routing_reason` read `context-render` instead
    of `session-context-render`. Nothing errored; the wrong answer simply looked slower.

    `%TEMP%` holds a stray `Cargo.toml` on this machine and `$HOME` a `package.json`, so without
    the deny rule every unrelated tree under either would share ONE session store and ONE daemon
    -- the same hazard the innermost-`.git` rule fixes for nested checkouts, reached through the
    manifest pass instead. An accepted risk that a test can trip is a defect.

    This test uses the REAL temp dir on purpose: `tmp_path` already lives under it, so the stray
    marker is genuinely present rather than simulated.
    """
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)

    resolved = session_store._resolve_root(project)

    assert resolved == project.resolve(), (
        f"a project under the system temp dir anchored to {resolved} -- every unrelated tree "
        f"under temp would share one session store and one daemon"
    )
    for shared in session_store._shared_territory_roots():
        assert resolved != shared, f"anchored to shared territory {shared}"


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
