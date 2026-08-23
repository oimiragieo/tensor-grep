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
    """A directory with no project marker anywhere above it keeps the old semantics.

    Without this the change would silently relocate stores for every ad-hoc directory, which is
    a behaviour change nobody asked for and the kind of blast radius this slice excludes.

    THE TEST MUST CONTROL ITS OWN ENVIRONMENT. Written naively it FAILED on this machine, and the
    failure was real information, not noise: `%TEMP%` contains a stray `Cargo.toml` and the home
    directory contains a `package.json`, so a pytest `tmp_path` genuinely HAS a marker 5 levels
    above it and the test's stated premise ("no marker anywhere above") was false. Asserting a
    premise the filesystem does not satisfy tests the machine, not the code -- the same class as
    a fixture that never bites. The walk is stubbed to the isolated subtree so the premise holds
    by construction.

    The underlying hazard is documented in `_find_project_root` rather than hidden: a caller
    inside a directory with a stray manifest above it and no closer project marker WILL anchor to
    that stray. Every real checkout has a `.git` or its own manifest nearer, and the VCS pass runs
    first.
    """
    sub = tmp_path / "loose" / "dir"
    sub.mkdir(parents=True)

    real_find = session_store._find_project_root

    def _bounded(start: Path):
        # Only consider candidates inside tmp_path, so ambient markers on the host cannot leak in.
        if not str(start).startswith(str(tmp_path)):
            return None
        for candidate in (start, *start.parents):
            if not str(candidate).startswith(str(tmp_path)):
                return None
            found = real_find(candidate) if candidate == start else None
            if found is not None and str(found).startswith(str(tmp_path)):
                return found
        return None

    monkeypatch.setattr(session_store, "_find_project_root", _bounded)
    assert session_store._resolve_root(sub) == sub.resolve()


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
