from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import edit_ticket_service
from tensor_grep.cli.edit_ticket_service import (
    _walk_tracked_files_bounded,
    build_edit_ready_ticket,
    verify_edit_ticket,
)


def test_unreadable_file_marks_population_incomplete_not_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that raises OSError on stat() (permission change, vanished mid-walk) must not
    silently disappear from the result while population still reports "complete" -- that is
    the exact false-PASS AGT-04 exists to prevent (see the module docstring)."""
    (tmp_path / "good.py").write_text("1\n", encoding="utf-8")
    unreadable = tmp_path / "unreadable.py"
    unreadable.write_text("2\n", encoding="utf-8")

    real_lstat = os.lstat

    def _flaky_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if Path(str(path)).name == "unreadable.py":
            raise OSError("simulated permission error")
        return real_lstat(path, *args, **kwargs)

    # The walker now classifies, sizes and fingerprints each leaf through the `_lstat` seam
    # (one stat per step) instead of `Path.stat`; the behaviour pinned here is unchanged.
    monkeypatch.setattr(edit_ticket_service, "_lstat", _flaky_lstat, raising=False)

    files, population = _walk_tracked_files_bounded(tmp_path)

    assert "good.py" in files
    assert "unreadable.py" not in files
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert population["verified"] is False


def test_dependency_tree_is_pruned_before_descent(tmp_path: Path) -> None:
    """AGT-04: node_modules/.venv/etc must never be entered, not merely filtered after a full
    walk -- pruning before descent is what keeps a huge vendored tree from being read at all."""
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    dep_dir = tmp_path / "node_modules" / "some-pkg"
    dep_dir.mkdir(parents=True)
    (dep_dir / "index.js").write_text("module.exports = {}\n", encoding="utf-8")

    files, population = _walk_tracked_files_bounded(tmp_path)

    assert "src.py" in files
    assert not any("node_modules" in path for path in files)
    assert population["status"] == "complete"


# G-03: build/dist/target are build outputs that may hold edited source; only unambiguous
# dependency/cache trees (by name) are pruned. Ambiguous names are covered by the tests below.
_UNAMBIGUOUS_PRUNED_NAMES = [
    "node_modules",
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "site-packages",
]


@pytest.mark.parametrize("name", _UNAMBIGUOUS_PRUNED_NAMES)
def test_all_known_dependency_dirs_are_pruned(tmp_path: Path, name: str) -> None:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "junk.bin").write_bytes(b"x")
    (tmp_path / "kept.py").write_text("1\n", encoding="utf-8")

    files, population = _walk_tracked_files_bounded(tmp_path)

    assert set(files) == {"kept.py"}
    assert population["status"] == "complete"


def test_tracked_dotfile_survives_pruning(tmp_path: Path) -> None:
    """A tracked dotfile (e.g. .gitignore) must still be hashed -- pruning targets dependency
    directory NAMES, not a blanket dot-prefix exclusion."""
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")

    files, population = _walk_tracked_files_bounded(tmp_path)

    assert ".gitignore" in files
    assert ".github/workflows/ci.yml" in files
    assert population["status"] == "complete"


def test_file_count_budget_reports_incomplete_not_false_pass(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("1\n", encoding="utf-8")

    files, population = _walk_tracked_files_bounded(tmp_path, max_files=3)

    assert population["status"] == "incomplete"
    assert population["reason"] == "file_count_limit"
    assert population["verified"] is False
    assert len(files) <= 3


def test_aggregate_byte_budget_reports_incomplete(tmp_path: Path) -> None:
    for i in range(4):
        (tmp_path / f"f{i}.bin").write_bytes(b"x" * 1000)

    _files, population = _walk_tracked_files_bounded(tmp_path, max_aggregate_bytes=1500)

    assert population["status"] == "incomplete"
    assert population["reason"] == "aggregate_byte_limit"
    assert population["verified"] is False


def test_per_file_byte_budget_skips_oversized_file_without_false_pass(tmp_path: Path) -> None:
    small = tmp_path / "small.py"
    small.write_text("1\n", encoding="utf-8")
    big = tmp_path / "huge.bin"
    big.write_bytes(b"x" * 10000)

    files, population = _walk_tracked_files_bounded(tmp_path, max_file_bytes=100)

    assert "small.py" in files
    assert population["status"] == "incomplete"
    assert population["reason"] == "per_file_byte_limit"


def test_verify_edit_ticket_fails_closed_on_incomplete_population(tmp_path: Path) -> None:
    """A ticket built from a truncated population must never let verify_edit_ticket return PASS
    -- an unread file could hide undeclared drift outside the ticket's allowed scope."""
    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("b = 1\n", encoding="utf-8")

    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
        max_files=1,
    )
    assert ticket.population_status["status"] == "incomplete"

    allowed.write_text("a = 2\n", encoding="utf-8")

    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=["allowed.py"],
    )

    assert result["verdict"] == "FAIL"
    assert result["reason"] == "population_incomplete"


def test_verify_edit_ticket_still_passes_on_complete_population(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")

    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
    )
    assert ticket.population_status["status"] == "complete"

    allowed.write_text("a = 2\n", encoding="utf-8")

    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=ticket,
        modified_files=["allowed.py"],
    )

    assert result["verdict"] == "PASS"


def test_ticket_without_population_status_is_refused_as_format_outdated(
    tmp_path: Path,
) -> None:
    """A ticket serialized before population_status existed carries no pruned-directory record,
    so verify cannot detect a marker planted after minting. It is REFUSED (re-mint), never
    verified through a fail-open compatibility path."""
    from tensor_grep.cli.edit_ticket_service import EditReadyTicketV1

    allowed = tmp_path / "allowed.py"
    allowed.write_text("a = 1\n", encoding="utf-8")

    legacy = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path=str(allowed),
        query="a = 1",
        allowed_files=["allowed.py"],
    )
    legacy_dict = legacy.to_dict()
    del legacy_dict["population_status"]
    reloaded = EditReadyTicketV1.from_dict(legacy_dict)
    assert reloaded.population_status == {"status": "unknown", "verified": None}

    allowed.write_text("a = 2\n", encoding="utf-8")
    result = verify_edit_ticket(
        repo_root=str(tmp_path),
        ticket=reloaded,
        modified_files=["allowed.py"],
    )
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "ticket_format_outdated"
    assert result["violations"] == ["ticket_format_outdated"]


_CACHEDIR_SIG = b"Signature: 8a477f597d28d172789f06886806bc55\n"


@pytest.mark.parametrize(
    "rel",
    [
        "build/gen.py",
        "src/build/gen.py",
        "dist/a.py",
        "pkg/dist/b.py",
        "target/x.py",
        "venv/notes.py",
    ],
)
def test_ambiguous_build_dirs_are_covered_at_any_depth(tmp_path: Path, rel: str) -> None:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x = 1\n", encoding="utf-8")
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert rel in files
    assert population["status"] == "complete"
    assert population["population_source"] == "filesystem-walk"


@pytest.mark.parametrize(
    "dep",
    [
        "node_modules/m/i.js",
        "pkg/node_modules/m/i.js",
        "a/__pycache__/x.pyc",
        ".git/HEAD",
        "x/.pytest_cache/v",
    ],
)
def test_unambiguous_dependency_dirs_are_pruned_at_any_depth(tmp_path: Path, dep: str) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    f = tmp_path / dep
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("1\n", encoding="utf-8")
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert set(files) == {"app.py"}
    assert population["status"] == "complete"


def test_content_marked_dirs_are_pruned_whatever_their_name(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    venv = tmp_path / "myenv"
    (venv / "lib").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (venv / "lib" / "m.py").write_text("1\n", encoding="utf-8")
    cache = tmp_path / "target"
    (cache / "debug").mkdir(parents=True)
    (cache / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG)
    (cache / "debug" / "out.o").write_bytes(b"\x00")
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert set(files) == {"app.py"}
    assert {"myenv", "target"} <= set(population["pruned_dirs"])


def test_in_source_cmake_build_dir_is_covered_and_undeclared_edit_fails(tmp_path: Path) -> None:
    # Council round 6: CMakeCache.txt next to CMakeLists.txt = in-source build; real source lives there.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    (lib / "CMakeCache.txt").write_text("CMAKE_X:STRING=1\n", encoding="utf-8")
    src = lib / "core.c"
    src.write_text("int x;\n", encoding="utf-8")
    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path), target_path="app.py", query="a", allowed_files=["app.py"]
    )
    src.write_text("int y;\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["violations"] == ["lib/core.c"]


@pytest.mark.parametrize("marker", [".rustc_info.json", "CMakeCache.txt"])
def test_build_root_markers_prune_without_cachedir_tag(tmp_path: Path, marker: str) -> None:
    # Council round 4 (measured): a maturin-created cargo target/ has .rustc_info.json, no CACHEDIR.TAG.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    target = tmp_path / "rust_core" / "target"
    (target / "debug").mkdir(parents=True)
    (target / marker).write_text("{}\n", encoding="utf-8")
    (target / "debug" / "big.rlib").write_bytes(b"\x00" * 64)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert set(files) == {"app.py"}
    assert "rust_core/target" in population["pruned_dirs"]


def test_symlinked_marker_does_not_prune_and_is_never_opened(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG)
    root = tmp_path / "repo"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "m.py").write_text("x = 1\n", encoding="utf-8")
    try:
        (src / "CACHEDIR.TAG").symlink_to(outside / "CACHEDIR.TAG")
        (src / "pyvenv.cfg").symlink_to(outside / "CACHEDIR.TAG")
    except OSError:
        pytest.skip("symlinks unavailable")
    files, population = _walk_tracked_files_bounded(root)
    assert "src/m.py" in files  # covered: a symlinked marker never counts
    assert "src" not in population["pruned_dirs"]


def test_unreadable_subtree_makes_population_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.py").write_text("1\n", encoding="utf-8")
    (tmp_path / "locked").mkdir()
    real_walk = os.walk

    def walk_with_error(top, *a, onerror=None, **k):
        yield from real_walk(top, *a, onerror=onerror, **k)
        if onerror is not None:
            onerror(PermissionError(13, "denied", str(Path(top) / "locked")))

    # Patch a private seam, never the stdlib attribute through a module alias (patching
    # `edit_ticket_service.os.walk` would stub os.walk GLOBALLY).
    monkeypatch.setattr(edit_ticket_service, "_os_walk", walk_with_error, raising=False)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"


def test_directory_budget_stops_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(30):
        (tmp_path / f"d{i:02d}").mkdir()  # many EMPTY dirs: file/byte budgets never trip
    monkeypatch.setattr(
        edit_ticket_service, "_MAX_WALK_DIRS", 10, raising=False
    )  # council round 8: behavioural RED on main
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "dir_count_limit"


def test_cachedir_tag_without_signature_does_not_prune(tmp_path: Path) -> None:
    d = tmp_path / "target"
    d.mkdir()
    (d / "CACHEDIR.TAG").write_bytes(b"not the signature\n")
    (d / "x.py").write_text("1\n", encoding="utf-8")
    files, _population = _walk_tracked_files_bounded(tmp_path)
    assert "target/x.py" in files


def _ticket(tmp_path: Path):
    return build_edit_ready_ticket(
        repo_root=str(tmp_path), target_path="app.py", query="a", allowed_files=["app.py"]
    )


@pytest.mark.parametrize("rel", ["build/gen.py", ".env", "secrets/k.txt", "sub/build/gen.py"])
def test_undeclared_edit_outside_dependency_trees_fails_verify(tmp_path: Path, rel: str) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        ".env\nsecrets/\n", encoding="utf-8"
    )  # git-ignore is irrelevant now
    (tmp_path / "sub" / ".git").mkdir(
        parents=True
    )  # a nested repo's .git is pruned, its files are covered
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    target.write_text("new\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "edit_contract_violated"
    assert result["violations"] == [rel]


def test_declared_edit_in_build_dir_passes(tmp_path: Path) -> None:  # positive control
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    gen = tmp_path / "build" / "gen.py"
    gen.parent.mkdir()
    gen.write_text("x = 1\n", encoding="utf-8")
    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path="app.py",
        query="a",
        allowed_files=["app.py", "build/gen.py"],
    )
    gen.write_text("x = 2\n", encoding="utf-8")
    result = verify_edit_ticket(
        repo_root=str(tmp_path), ticket=ticket, modified_files=["build/gen.py"]
    )
    assert result["verdict"] == "PASS"


def test_enumeration_stops_at_max_files_without_walking_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # UNIT COVERAGE of the new lazy generator, NOT RED evidence (council round 9): on main
    # `_population_paths` does not exist, so this fails with AttributeError there. E.1's
    # behavioural RED is the ambiguous-directory, undeclared-edit and unreadable-path tests.
    for i in range(50):
        (tmp_path / f"f{i:02d}.txt").write_text("x\n", encoding="utf-8")
    seen = {"n": 0}
    real = edit_ticket_service._population_paths

    def counting(*a: object, **k: object):
        for p in real(*a, **k):
            seen["n"] += 1
            yield p

    monkeypatch.setattr(edit_ticket_service, "_population_paths", counting)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_files=5
    )  # real kwarg (edit_ticket_service.py:90)
    assert population["status"] == "incomplete"
    assert population["reason"] == "file_count_limit"
    assert seen["n"] <= 6  # lazy: stopped right after the limit


def test_oversize_symlink_target_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x" * 5000)
    root = tmp_path / "repo"
    root.mkdir()
    try:
        # RELATIVE target (14 chars) so the link-text size stays under the 100-byte cap on Windows,
        # where absolute tmp paths exceed 100 chars (council round 1).
        (root / "link.bin").symlink_to(Path("..") / "outside.bin")
    except OSError:
        pytest.skip("symlinks unavailable")
    files, population = _walk_tracked_files_bounded(root, max_file_bytes=100)
    assert population["status"] == "complete"
    assert (
        files["link.bin"]
        == "symlink:" + hashlib.sha256(os.fsencode(os.readlink(root / "link.bin"))).hexdigest()
    )


def test_unreadable_fingerprint_marks_incomplete_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.py").write_text("1\n", encoding="utf-8")

    def _boom(*_a: object, **_k: object) -> str:
        raise PermissionError("denied")

    # the walker fingerprints enumerated leaves through its own fail-closed helper
    monkeypatch.setattr(edit_ticket_service, "_fingerprint_enumerated", _boom, raising=False)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert files == {}
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"


def test_planted_cachedir_tag_in_existing_source_dir_is_caught(tmp_path: Path) -> None:
    # A CACHEDIR.TAG planted AFTER minting hides src/ from the verify-time walk; the pre-edit
    # fingerprints then show those files as drift, so the plant is caught (fail closed).
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("x = 1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    (src / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG)
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert "src/m.py" in result["violations"]


# ---- Codex audit FIX-FIRST: three verify_edit_ticket bypasses ----


@pytest.mark.parametrize("marker", ["pyvenv.cfg", ".rustc_info.json", "CACHEDIR.TAG"])
def test_planted_marker_cannot_hide_undeclared_edit_in_existing_dir(
    tmp_path: Path, marker: str
) -> None:
    # Bypass 1: planting a marker prunes src/, so the declared src/allowed.py looks DELETED
    # (satisfying the declared change) and the undeclared src/evil.py never enters either
    # population. The set of content-pruned dirs is recorded at mint and compared at verify.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "allowed.py").write_text("x = 1\n", encoding="utf-8")
    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path="src/allowed.py",
        query="x",
        allowed_files=["src/allowed.py"],
    )
    (src / "allowed.py").write_text("x = 2\n", encoding="utf-8")
    (src / "evil.py").write_text("boom\n", encoding="utf-8")
    (src / marker).write_bytes(_CACHEDIR_SIG if marker == "CACHEDIR.TAG" else b"{}\n")
    result = verify_edit_ticket(
        repo_root=str(tmp_path), ticket=ticket, modified_files=["src/allowed.py"]
    )
    assert result["verdict"] == "FAIL"
    assert "newly_pruned:src" in result["violations"]


def test_pruned_dir_marker_change_and_unprune_are_violations(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_text("home = a\n", encoding="utf-8")
    (env / "lib.py").write_text("1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    assert ticket.population_status["pruned_set"]  # recorded at mint
    # unchanged: PASS (positive control)
    assert (
        verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])["verdict"]
        == "PASS"
    )
    (env / "pyvenv.cfg").write_text("home = b\n", encoding="utf-8")
    changed = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert changed["verdict"] == "FAIL"
    assert "marker_changed:env" in changed["violations"]
    (env / "pyvenv.cfg").unlink()
    gone = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert gone["verdict"] == "FAIL"
    assert any(v.endswith(":env") for v in gone["violations"])


def _make_dir_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink creation not permitted here: {exc}")


def test_repointed_directory_symlink_fails_verify(tmp_path: Path) -> None:
    # Bypass 2: os.walk(followlinks=False) lists a directory symlink in dirnames and never
    # yields it, so re-pointing it was invisible.
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "dir-a").mkdir()
    (tmp_path / "dir-b").mkdir()
    (root / "app.py").write_text("a = 1\n", encoding="utf-8")
    link = root / "alias"
    _make_dir_symlink(link, Path("..") / "dir-a")
    ticket = _ticket(root)
    link.unlink()
    _make_dir_symlink(link, Path("..") / "dir-b")
    result = verify_edit_ticket(repo_root=str(root), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["violations"] == ["alias"]


def test_unchanged_directory_symlink_passes_verify(tmp_path: Path) -> None:  # positive control
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "dir-a").mkdir()
    (root / "app.py").write_text("a = 1\n", encoding="utf-8")
    _make_dir_symlink(root / "alias", Path("..") / "dir-a")
    ticket = _ticket(root)
    assert "alias" in ticket.pre_edit_fingerprints
    result = verify_edit_ticket(repo_root=str(root), ticket=ticket, modified_files=[])
    assert result["verdict"] == "PASS"


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
def test_valid_cachedir_tag_line_endings_are_pruned(tmp_path: Path, ending: bytes) -> None:
    d = tmp_path / "cache"
    d.mkdir()
    tail = b"# a comment line\n" if ending else b""
    (d / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG.rstrip(b"\n") + ending + tail)
    (d / "x.py").write_text("1\n", encoding="utf-8")
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert "cache/x.py" not in files
    assert "cache" in population["pruned_dirs"]


@pytest.mark.parametrize(
    "tag",
    [
        b"Signature: 8a477f597d28d172789f06886806bc55NOT-A-SIGNATURE-LINE",
        b"Signature: 8a477f597d28d172789f06886806bc55 trailing\n",
        b"signature: 8a477f597d28d172789f06886806bc55\n",
        b"Signature: 8A477F597D28D172789F06886806BC55\n",
        b"Signature: 8a477f597d28d172789f06886806bc55\r",
    ],
)
def test_malformed_cachedir_tag_does_not_prune_and_edit_fails_verify(
    tmp_path: Path, tag: bytes
) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "CACHEDIR.TAG").write_bytes(tag)
    src = d / "m.py"
    src.write_text("x = 1\n", encoding="utf-8")
    files, _population = _walk_tracked_files_bounded(tmp_path)
    assert "pkg/m.py" in files
    ticket = _ticket(tmp_path)
    src.write_text("x = 2\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["violations"] == ["pkg/m.py"]


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("src/node_modules/evil.py", "newly_pruned:src/node_modules"),
        ("__pycache__/x.py", "newly_pruned:__pycache__"),
        ("pkg/.tox/e.py", "newly_pruned:pkg/.tox"),
        (".nox/e.py", "newly_pruned:.nox"),
        ("lib/site-packages/e.py", "newly_pruned:lib/site-packages"),
    ],
)
def test_newly_created_name_pruned_dir_is_a_violation(
    tmp_path: Path, rel: str, expected: str
) -> None:
    # A whole NEW directory with an always-pruned name hides undeclared files from the verify
    # walk. Fail closed: a pruned directory that did not exist at mint is a violation.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    evil = tmp_path / rel
    evil.parent.mkdir(parents=True, exist_ok=True)
    evil.write_text("boom\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["violations"] == [expected]


def test_newly_pruned_violation_names_the_directory(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    ticket = _ticket(tmp_path)
    (tmp_path / "src" / "node_modules").mkdir()
    (tmp_path / "src" / "node_modules" / "evil.py").write_text("x\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["violations"] == ["newly_pruned:src/node_modules"]


def test_preexisting_name_pruned_dir_with_changes_inside_passes(tmp_path: Path) -> None:
    # Control: contents of a name-pruned dir that existed at mint stay out of scope, as designed.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    assert ticket.population_status["pruned_set"]["node_modules"] == "name"
    (nm / "index.js").write_text("2\n", encoding="utf-8")
    (nm / "new.js").write_text("3\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "PASS"


@pytest.mark.parametrize("missing", ["pruned_set", "population_policy", "population_source"])
def test_ticket_missing_pruned_record_or_policy_is_refused_not_verified(
    tmp_path: Path, missing: str
) -> None:
    # Without `pruned_set` a planted marker reopens the bypass: modify the declared file, add an
    # undeclared sibling, plant src/pyvenv.cfg -> the old fail-open skip returned PASS.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "allowed.py").write_text("x = 1\n", encoding="utf-8")
    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path),
        target_path="src/allowed.py",
        query="x",
        allowed_files=["src/allowed.py"],
    )
    legacy_dict = ticket.to_dict()
    del legacy_dict["population_status"][missing]
    legacy = edit_ticket_service.EditReadyTicketV1.from_dict(legacy_dict)
    (src / "allowed.py").write_text("x = 2\n", encoding="utf-8")
    (src / "evil.py").write_text("boom\n", encoding="utf-8")
    (src / "pyvenv.cfg").write_bytes(b"home = x\n")
    result = verify_edit_ticket(
        repo_root=str(tmp_path), ticket=legacy, modified_files=["src/allowed.py"]
    )
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "ticket_format_outdated"


def test_many_name_pruned_dirs_stay_complete_and_verify_passes(tmp_path: Path) -> None:
    # Name-pruned entries are tiny (a path and "name"): a large Python/JS repo with thousands
    # of __pycache__ dirs must NOT fail closed. 3000 > the old shared cap of 2000.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    for i in range(3000):
        (tmp_path / f"p{i:04d}" / "__pycache__").mkdir(parents=True)
    ticket = _ticket(tmp_path)
    assert ticket.population_status["status"] == "complete"
    assert len(ticket.population_status["pruned_set"]) == 3000
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "PASS"


def test_content_pruned_budget_makes_population_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(5):
        d = tmp_path / f"env{i}"
        d.mkdir()
        (d / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    monkeypatch.setattr(edit_ticket_service, "_MAX_CONTENT_PRUNED_DIRS", 3, raising=False)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "pruned_dir_limit"
    assert population["limit_kind"] == "content"


def test_name_pruned_budget_makes_population_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(5):
        (tmp_path / f"d{i}" / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(edit_ticket_service, "_MAX_NAME_PRUNED_DIRS", 3, raising=False)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "pruned_dir_limit"
    assert population["limit_kind"] == "name"


def test_marker_edit_beyond_hash_cap_is_not_recorded_as_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A digest of only the first CAP bytes cannot see an edit past the cap. Never record a
    # truncated digest as complete: a marker larger than the cap makes the population
    # incomplete (marker_too_large), so mint and verify fail closed.
    monkeypatch.setattr(edit_ticket_service, "_MARKER_HASH_CAP", 64, raising=False)
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    env = tmp_path / "env"
    env.mkdir()
    marker = env / "pyvenv.cfg"
    marker.write_bytes(b"x" * 64 + b"A")
    ticket = _ticket(tmp_path)
    marker.write_bytes(b"x" * 64 + b"B")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert ticket.population_status["status"] == "incomplete"
    assert ticket.population_status["reason"] == "marker_too_large"


def test_marker_exactly_at_cap_is_hashed_in_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # positive control: the cap is inclusive
    monkeypatch.setattr(edit_ticket_service, "_MARKER_HASH_CAP", 64, raising=False)
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_bytes(b"x" * 64)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "complete"


def test_unreadable_marker_makes_population_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")

    def _denied(*_a: object, **_k: object) -> object:
        raise PermissionError(13, "denied")

    # private seam for the marker read; raising=False keeps the test collectable on main
    monkeypatch.setattr(edit_ticket_service, "_os_open", _denied, raising=False)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert "unreadable" not in population["pruned_set"].values()


def _make_junction(link: Path, target: Path) -> None:
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=True,
        timeout=30,
        capture_output=True,
    )


def _remove_junction(link: Path) -> None:
    # rmdir on a junction removes the link only, never the target's contents
    if os.path.lexists(link):
        os.rmdir(link)


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")


@windows_only
def test_unchanged_junction_passes_verify(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "dir-a").mkdir()
    (root / "app.py").write_text("a = 1\n", encoding="utf-8")
    link = root / "alias"
    _make_junction(link, tmp_path / "dir-a")
    try:
        ticket = _ticket(root)
        assert "alias" in ticket.pre_edit_fingerprints
        result = verify_edit_ticket(repo_root=str(root), ticket=ticket, modified_files=[])
        assert result["verdict"] == "PASS"
    finally:
        _remove_junction(link)


@windows_only
def test_repointed_junction_to_identical_marker_fails_verify(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("dir-a", "dir-b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "pyvenv.cfg").write_text("home = same\n", encoding="utf-8")
    (root / "app.py").write_text("a = 1\n", encoding="utf-8")
    link = root / "alias"
    _make_junction(link, tmp_path / "dir-a")
    try:
        ticket = _ticket(root)
        _remove_junction(link)
        _make_junction(link, tmp_path / "dir-b")
        result = verify_edit_ticket(repo_root=str(root), ticket=ticket, modified_files=[])
        assert result["verdict"] == "FAIL"
        assert "alias" in result["violations"]
    finally:
        _remove_junction(link)


@windows_only
def test_junction_detection_works_without_os_path_isjunction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Python 3.11 has no os.path.isjunction (added in 3.12). Junction detection must come from
    # lstat's reparse tag, so remove the 3.12 helper and require the same verdicts.
    monkeypatch.delattr(os.path, "isjunction", raising=False)
    assert not hasattr(os.path, "isjunction")
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("dir-a", "dir-b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "pyvenv.cfg").write_text("home = same\n", encoding="utf-8")
    (root / "app.py").write_text("a = 1\n", encoding="utf-8")
    link = root / "alias"
    _make_junction(link, tmp_path / "dir-a")
    try:
        ticket = _ticket(root)
        assert "alias" in ticket.pre_edit_fingerprints  # control: a leaf, not descended
        same = verify_edit_ticket(repo_root=str(root), ticket=ticket, modified_files=[])
        assert same["verdict"] == "PASS"
        _remove_junction(link)
        _make_junction(link, tmp_path / "dir-b")
        result = verify_edit_ticket(repo_root=str(root), ticket=ticket, modified_files=[])
        assert result["verdict"] == "FAIL"
        assert "alias" in result["violations"]
    finally:
        _remove_junction(link)


def test_lstat_failure_while_classifying_a_directory_is_unreadable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "locked").mkdir()
    (tmp_path / "a.py").write_text("1\n", encoding="utf-8")
    real = os.lstat

    def _lstat(path: object, *a: object, **k: object) -> os.stat_result:
        if Path(str(path)).name == "locked":
            raise PermissionError(13, "denied")
        return real(path, *a, **k)

    monkeypatch.setattr(edit_ticket_service, "_lstat", _lstat, raising=False)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"


def test_file_whose_bytes_mimic_the_link_domain_cannot_be_swapped_for_a_symlink(
    tmp_path: Path,
) -> None:
    # Fingerprint domains must not overlap: a regular file containing exactly b"symlink:victim.py"
    # used to hash identically to a symlink to victim.py.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "victim.py").write_text("v = 1\n", encoding="utf-8")
    alias = tmp_path / "alias.py"
    alias.write_bytes(b"symlink:victim.py")
    ticket = _ticket(tmp_path)
    alias.unlink()
    try:
        alias.symlink_to("victim.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation not permitted here: {exc}")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert "alias.py" in result["violations"]


@windows_only
def test_file_mimicking_junction_domain_cannot_be_swapped_for_a_junction(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "dir-a").mkdir()
    (root / "app.py").write_text("a = 1\n", encoding="utf-8")
    probe = root / "probe"
    _make_junction(probe, tmp_path / "dir-a")
    try:
        link_text = os.readlink(probe)
    finally:
        _remove_junction(probe)
    alias = root / "alias"
    alias.write_bytes(b"symlink:" + os.fsencode(link_text))
    ticket = _ticket(root)
    alias.unlink()
    _make_junction(alias, tmp_path / "dir-a")
    try:
        result = verify_edit_ticket(repo_root=str(root), ticket=ticket, modified_files=[])
        assert result["verdict"] == "FAIL"
        assert "alias" in result["violations"]
    finally:
        _remove_junction(alias)


def test_fingerprints_are_type_tagged(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "t.py").write_text("y\n", encoding="utf-8")
    try:
        (tmp_path / "l.py").symlink_to("t.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation not permitted here: {exc}")
    files, _population = _walk_tracked_files_bounded(tmp_path)
    assert files["f.py"].startswith("file:")
    assert files["l.py"] == "symlink:" + hashlib.sha256(b"t.py").hexdigest()


def test_unchanged_link_passes_verify_with_tagged_fingerprints(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "victim.py").write_text("v = 1\n", encoding="utf-8")
    try:
        (tmp_path / "alias.py").symlink_to("victim.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation not permitted here: {exc}")
    ticket = _ticket(tmp_path)
    assert ticket.pre_edit_fingerprints["alias.py"].startswith("symlink:")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "PASS"


def test_untagged_legacy_fingerprints_fail_closed_as_format_outdated(tmp_path: Path) -> None:
    # Decision: an old-format ticket (bare hex fingerprints) is REFUSED, never tagged on read.
    # Old tickets could hold links hashed by the old scheme, so re-tagging them `file:` would
    # re-open the collision for exactly the tickets that predate the fix.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    legacy_dict = ticket.to_dict()
    legacy_dict["pre_edit_fingerprints"] = {
        k: v.split(":", 1)[1] for k, v in ticket.pre_edit_fingerprints.items()
    }
    legacy = edit_ticket_service.EditReadyTicketV1.from_dict(legacy_dict)
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=legacy, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "ticket_format_outdated"
    assert result["violations"] == ["ticket_format_outdated"]


def test_directory_listed_among_filenames_is_unreadable_path_not_a_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When DirEntry.is_dir() raises but stat succeeds, os.walk swallows the error (no onerror)
    # and lists the directory among `filenames`. Fingerprinting it as a leaf would omit the
    # whole subtree and report a complete population.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("x = 1\n", encoding="utf-8")
    real_walk = os.walk

    def walk_misclassifying_src(top, *a, **k):
        for dirpath, dirnames, filenames in real_walk(top, *a, **k):
            if "src" in dirnames:
                dirnames.remove("src")
                filenames.append("src")
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(edit_ticket_service, "_os_walk", walk_misclassifying_src, raising=False)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert not any(v.startswith("other:") for v in files.values())


def test_missing_path_fingerprint_is_empty_string_not_an_exception(tmp_path: Path) -> None:
    assert edit_ticket_service.compute_file_fingerprint(tmp_path / "missing" / "x") == ""
    assert edit_ticket_service.compute_file_fingerprint(tmp_path / "nope.py") == ""


def test_non_notfound_lstat_failure_in_fingerprint_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "f.py"
    target.write_text("1\n", encoding="utf-8")

    def _denied(*_a: object, **_k: object) -> os.stat_result:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(edit_ticket_service, "_lstat", _denied, raising=False)
    with pytest.raises(PermissionError):
        edit_ticket_service.compute_file_fingerprint(target)


def _scripted_lstat(tmp_path: Path, victim: str, script: list[str]):
    """An `_lstat` seam that returns a scripted answer for `victim`'s Nth stat: 'real' passes
    through, 'dir' returns a real directory's stat (the file was replaced by a directory)."""
    real = os.lstat
    dir_stat = real(tmp_path)
    calls = {"n": 0}

    def _lstat(path: object, *a: object, **k: object) -> os.stat_result:
        if Path(str(path)).name != victim:
            return real(path, *a, **k)
        step = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        if step == "missing":
            raise FileNotFoundError(2, "vanished", str(path))
        return dir_stat if step == "dir" else real(path, *a, **k)

    return _lstat, calls


def test_file_replaced_by_directory_between_checks_is_unreadable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # regular (leaf check) / regular (size) / directory (fingerprint stat)
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "allowed.py").write_text("x = 1\n", encoding="utf-8")
    seam, calls = _scripted_lstat(tmp_path, "allowed.py", ["real", "real", "dir"])
    monkeypatch.setattr(edit_ticket_service, "_lstat", seam, raising=False)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert calls["n"] >= 3  # the walker really did stat it three times
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert not any(v.startswith("other:") for v in files.values())


def test_file_replaced_by_directory_never_passes_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "allowed.py").write_text("x = 1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    seam, _calls = _scripted_lstat(tmp_path, "allowed.py", ["real", "real", "dir"])
    monkeypatch.setattr(edit_ticket_service, "_lstat", seam, raising=False)
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "verify_population_incomplete"


def test_file_disappearing_before_fingerprint_is_incomplete_not_empty_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # regular (leaf check) / regular (size check) / MISSING (fingerprint stat): the file vanished
    # after the size check. "" would compare equal to "absent from the ticket".
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "gone.py").write_text("x = 1\n", encoding="utf-8")
    seam, calls = _scripted_lstat(tmp_path, "gone.py", ["real", "real", "missing"])
    monkeypatch.setattr(edit_ticket_service, "_lstat", seam, raising=False)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert calls["n"] >= 3
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
    assert files.get("gone.py") != ""


def test_walker_fingerprints_normal_file_and_link_correctly(tmp_path: Path) -> None:
    # positive controls for the walker's own fingerprint helper
    (tmp_path / "f.py").write_bytes(b"x\n")
    (tmp_path / "t.py").write_bytes(b"y\n")
    expected = "file:" + hashlib.sha256(b"x\n").hexdigest()
    try:
        (tmp_path / "l.py").symlink_to("t.py")
        link_ok = True
    except (OSError, NotImplementedError):
        link_ok = False
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "complete"
    assert files["f.py"] == expected
    assert files["f.py"] == edit_ticket_service.compute_file_fingerprint(tmp_path / "f.py")
    if link_ok:
        assert files["l.py"] == "symlink:" + hashlib.sha256(b"t.py").hexdigest()


def test_standalone_fingerprint_of_missing_path_keeps_empty_string_contract(
    tmp_path: Path,
) -> None:
    assert edit_ticket_service.compute_file_fingerprint(tmp_path / "missing.py") == ""


def _growing_lstat(victim: Path, grow_by: int, grow_after_call: int = 2):
    """An `_lstat` seam that appends `grow_by` bytes to `victim` (same inode) right after the
    walker's size-check stat (its 2nd stat of that file)."""
    real = os.lstat
    calls = {"n": 0}

    def _lstat(path: object, *a: object, **k: object) -> os.stat_result:
        result = real(path, *a, **k)
        if Path(str(path)) == victim:
            calls["n"] += 1
            if calls["n"] == grow_after_call:
                with open(victim, "ab") as handle:
                    handle.write(b"y" * grow_by)
        return result

    return _lstat


def test_file_growing_after_size_check_hits_the_per_file_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / "grow.bin"
    victim.write_bytes(b"x" * 5)
    monkeypatch.setattr(edit_ticket_service, "_lstat", _growing_lstat(victim, 20), raising=False)
    _files, population = _walk_tracked_files_bounded(tmp_path, max_file_bytes=10)
    assert population["status"] == "incomplete"
    assert population["reason"] == "per_file_byte_limit"


def test_file_growing_after_size_check_hits_the_aggregate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / "grow.bin"
    victim.write_bytes(b"x" * 5)
    monkeypatch.setattr(edit_ticket_service, "_lstat", _growing_lstat(victim, 20), raising=False)
    _files, population = _walk_tracked_files_bounded(tmp_path, max_aggregate_bytes=10)
    assert population["status"] == "incomplete"
    assert population["reason"] == "aggregate_byte_limit"


def test_scanned_bytes_charges_the_bytes_actually_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / "grow.bin"
    victim.write_bytes(b"x" * 5)
    monkeypatch.setattr(edit_ticket_service, "_lstat", _growing_lstat(victim, 3), raising=False)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "complete"
    assert population["scanned_bytes"] == 8  # 5 at the size check + 3 grown = what was read


def test_file_of_exactly_the_per_file_limit_is_complete(tmp_path: Path) -> None:
    (tmp_path / "exact.bin").write_bytes(b"x" * 10)
    _files, population = _walk_tracked_files_bounded(tmp_path, max_file_bytes=10)
    assert population["status"] == "complete"
    assert population["scanned_bytes"] == 10


def test_files_summing_to_exactly_the_aggregate_limit_are_complete(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"x" * 5)
    (tmp_path / "b.bin").write_bytes(b"y" * 5)
    _files, population = _walk_tracked_files_bounded(tmp_path, max_aggregate_bytes=10)
    assert population["status"] == "complete"
    assert population["scanned_bytes"] == 10


_FIFO_CHILD = """
import json, os, sys
from pathlib import Path
from tensor_grep.cli import edit_ticket_service as svc

root = Path(sys.argv[1])
victim = root / "f.py"
real = os.lstat
state = {"n": 0}

def seam(path, *a, **k):
    result = real(path, *a, **k)
    if Path(str(path)) == victim:
        state["n"] += 1
        if state["n"] == 3:  # the fingerprint stat saw a regular file; now swap in a FIFO
            os.unlink(victim)
            os.mkfifo(victim)
    return result

svc._lstat = seam
files, population = svc._walk_tracked_files_bounded(root)
print(json.dumps({"status": population["status"], "reason": population["reason"]}))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFOs only (no FIFO at a Windows path)")
def test_fifo_swapped_in_after_lstat_does_not_hang_and_is_unreadable_path(tmp_path: Path) -> None:
    # open(O_RDONLY) on a FIFO blocks for a writer. Run in a SUBPROCESS with a hard timeout:
    # a hang raises TimeoutExpired, which FAILS this test (never a pass).
    import json

    work = tmp_path / "work"
    work.mkdir()
    (work / "f.py").write_text("x = 1\n", encoding="utf-8")
    script = tmp_path / "child.py"
    script.write_text(_FIFO_CHILD, encoding="utf-8")
    src_dir = str(Path(edit_ticket_service.__file__).resolve().parents[2])
    env = {**os.environ, "PYTHONPATH": src_dir}
    done = subprocess.run(
        [sys.executable, str(script), str(work)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=True,
    )
    outcome = json.loads(done.stdout.strip().splitlines()[-1])
    assert outcome == {"status": "incomplete", "reason": "unreadable_path"}


def test_per_file_overflow_charges_the_bytes_it_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Three files each grow past the per-file limit after their size check. Every overflow read
    # (cap + 1 probe byte) must be CHARGED, so the aggregate budget eventually trips; a refund
    # let the walk continue with the full allowance and report scanned_bytes == 0.
    victims = [tmp_path / f"grow{i}.bin" for i in range(3)]
    for v in victims:
        v.write_bytes(b"x" * 5)
    real = os.lstat
    seen: dict[str, int] = {}

    def _lstat(path: object, *a: object, **k: object) -> os.stat_result:
        result = real(path, *a, **k)
        p = Path(str(path))
        if p in victims:
            seen[p.name] = seen.get(p.name, 0) + 1
            if seen[p.name] == 2:  # right after the size-check stat
                with open(p, "ab") as handle:
                    handle.write(b"y" * 20)
        return result

    monkeypatch.setattr(edit_ticket_service, "_lstat", _lstat, raising=False)
    files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=30
    )
    # file0: cap 10 -> 11 bytes read; file1: cap 10 -> 11; file2: remaining 8 -> cap 8 -> 9 read
    assert population["scanned_bytes"] == 11 + 11 + 9
    assert population["status"] == "incomplete"
    assert population["reason"] == "aggregate_byte_limit"
    assert files == {}


_MARKER_FIFO_CHILD = """
import json, os, sys
from pathlib import Path
from tensor_grep.cli import edit_ticket_service as svc

root = Path(sys.argv[1])
victim = root / "env" / sys.argv[2]
real = os.lstat
state = {"n": 0}

def seam(path, *a, **k):
    result = real(path, *a, **k)
    if Path(str(path)) == victim:
        state["n"] += 1
        if state["n"] == 1:  # _regular_marker just saw a regular file; swap in a FIFO
            os.unlink(victim)
            os.mkfifo(victim)
    return result

svc._lstat = seam
files, population = svc._walk_tracked_files_bounded(root)
print(json.dumps({"status": population["status"], "reason": population["reason"]}))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFOs only (no FIFO at a Windows path)")
@pytest.mark.parametrize(
    ("marker", "content"),
    [("pyvenv.cfg", b"home = x\n"), ("CACHEDIR.TAG", _CACHEDIR_SIG)],
)
def test_fifo_swapped_in_before_marker_read_does_not_hang(
    tmp_path: Path, marker: str, content: bytes
) -> None:
    # The marker readers (_marker_digest, _cachedir_tag_valid) must use the same safe open as
    # the leaf fingerprint. SUBPROCESS + hard timeout: a hang raises TimeoutExpired = FAIL.
    import json

    work = tmp_path / "work"
    (work / "env").mkdir(parents=True)
    (work / "env" / marker).write_bytes(content)
    script = tmp_path / "child.py"
    script.write_text(_MARKER_FIFO_CHILD, encoding="utf-8")
    src_dir = str(Path(edit_ticket_service.__file__).resolve().parents[2])
    done = subprocess.run(
        [sys.executable, str(script), str(work), marker],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": src_dir},
        check=True,
    )
    outcome = json.loads(done.stdout.strip().splitlines()[-1])
    assert outcome == {"status": "incomplete", "reason": "unreadable_path"}


# ---- one ledger for every byte the walker consumes ----


def _symlink_or_skip(link: Path, target: str) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation not permitted here: {exc}")


def test_marker_bytes_are_charged_to_the_budget(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_bytes(b"x" * 11)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=10
    )
    assert population["status"] == "incomplete"
    assert population["reason"] == "per_file_byte_limit"
    assert population["scanned_bytes"] == 11  # every byte read, including the probe byte


def test_cachedir_tag_head_bytes_are_charged_to_the_budget(tmp_path: Path) -> None:
    d = tmp_path / "cache"
    d.mkdir()
    (d / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG)  # 44 bytes
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=20, max_aggregate_bytes=1000
    )
    assert population["status"] == "incomplete"
    assert population["reason"] == "per_file_byte_limit"
    assert population["scanned_bytes"] >= 21


def test_link_target_is_measured_in_bytes_not_characters(tmp_path: Path) -> None:
    target = "é" * 60  # 60 characters, 120 UTF-8 bytes
    _symlink_or_skip(tmp_path / "alias", target)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=100, max_aggregate_bytes=100
    )
    assert population["status"] == "incomplete"
    assert population["reason"] == "per_file_byte_limit"


def test_marker_of_exactly_the_limit_is_complete_and_fully_charged(tmp_path: Path) -> None:
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_bytes(b"x" * 10)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=10
    )
    assert population["status"] == "complete"
    assert population["scanned_bytes"] == 10


def test_link_of_exactly_the_limit_is_complete_and_fully_charged(tmp_path: Path) -> None:
    _symlink_or_skip(tmp_path / "alias", "t" * 10)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=10
    )
    assert population["status"] == "complete"
    assert population["scanned_bytes"] == 10


def test_markers_files_and_links_share_one_aggregate_ledger(tmp_path: Path) -> None:
    # order: marker (dir classification) 8, a.bin 10, b_link 8, then c.bin hits the aggregate
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_bytes(b"m" * 8)
    (tmp_path / "a.bin").write_bytes(b"a" * 10)
    _symlink_or_skip(tmp_path / "b_link", "t" * 8)
    (tmp_path / "c.bin").write_bytes(b"c" * 10)
    _files, population = _walk_tracked_files_bounded(tmp_path, max_aggregate_bytes=30)
    assert population["status"] == "incomplete"
    assert population["reason"] == "aggregate_byte_limit"
    # 8 + 10 + 8 actually read; c.bin (10 bytes) exceeds the remaining 4 at its size check, so
    # nothing of it is read (and nothing is charged for it)
    assert population["scanned_bytes"] == 8 + 10 + 8


# ---- raw reads are bounded; links are read once ----


class _CountingRaw:
    """Raw handle recording the bytes ACTUALLY pulled from the OS."""

    def __init__(self, inner: object, counter: dict[str, int]) -> None:
        self._inner = inner
        self._counter = counter
        self._counter["opened"] = self._counter.get("opened", 0) + 1

    def make(self, buffering: int) -> object:
        import io

        counter = self._counter
        inner = self._inner

        class _Raw(io.RawIOBase):
            def readable(self) -> bool:
                return True

            def readinto(self, buf: bytearray) -> int:
                n = inner.readinto(buf) or 0  # type: ignore[attr-defined]
                counter["raw_bytes"] = counter.get("raw_bytes", 0) + n
                return n

            def fileno(self) -> int:
                return inner.fileno()  # type: ignore[attr-defined]

            def close(self) -> None:
                inner.close()  # type: ignore[attr-defined]
                super().close()

        raw = _Raw()
        return raw if buffering == 0 else io.BufferedReader(raw)


def test_budgeted_read_pulls_at_most_limit_plus_one_raw_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A buffered handle pulls a whole buffer from the OS even for a 10-byte budgeted read, so the
    # budget bounds nothing. The handle must be unbuffered: every request reaches the raw read.
    (tmp_path / "big.bin").write_bytes(b"x" * 200_000)
    counter: dict[str, int] = {}
    real_fdopen = os.fdopen

    def _fdopen(fd: int, mode: str = "r", buffering: int = -1, *a: object, **k: object) -> object:
        inner = real_fdopen(fd, "rb", buffering=0)
        return _CountingRaw(inner, counter).make(buffering)

    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen, raising=False)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=10
    )
    # the size check rejects 200 KB before reading, so grow-after-check isn't needed: also probe
    # the open path directly with the same budget
    assert counter.get("opened", 0) >= 0
    ledger = edit_ticket_service._ByteLedger(10, 10)
    with edit_ticket_service._open_regular_no_follow(tmp_path / "big.bin") as handle:
        try:
            list(ledger.iter_chunks(handle))
        except edit_ticket_service._BudgetExceeded:
            pass
    assert counter.get("opened", 0) >= 1, "the safe open did not go through the _fdopen seam"
    assert counter["raw_bytes"] <= 11
    assert population["status"] == "incomplete"


def _counting_readlink(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"n": 0}
    real = os.readlink

    def _readlink(path: object, *a: object, **k: object) -> object:
        calls["n"] += 1
        return real(path, *a, **k)

    monkeypatch.setattr(edit_ticket_service, "_readlink", _readlink, raising=False)
    return calls


def test_link_target_is_read_exactly_once_and_charged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Contract: link targets are read in full once; bounded by the platform path limit.
    _symlink_or_skip(tmp_path / "alias", "t" * 10)
    calls = _counting_readlink(monkeypatch)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=10
    )
    assert calls["n"] == 1
    assert population["status"] == "complete"
    assert population["scanned_bytes"] == 10


def test_oversized_link_target_is_rejected_after_the_single_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _symlink_or_skip(tmp_path / "alias", "t" * 11)
    calls = _counting_readlink(monkeypatch)
    _files, population = _walk_tracked_files_bounded(
        tmp_path, max_file_bytes=10, max_aggregate_bytes=10
    )
    assert calls["n"] == 1
    assert population["status"] == "incomplete"
    assert population["reason"] == "per_file_byte_limit"
    assert population["scanned_bytes"] == 11  # the one read is charged


# ---- raw reads may be short (round 11) and may fail mid-read ----


def _fdopen_with(max_per_read: int | None = None, fail_on_read: bool = False):
    """An `_fdopen` seam returning an UNBUFFERED handle whose reads return at most
    `max_per_read` bytes (a legal short read) or raise OSError(EIO) on the first read."""
    import io

    real_fdopen = os.fdopen

    def _fdopen(fd: int, mode: str = "r", buffering: int = -1, *a: object, **k: object) -> object:
        inner = real_fdopen(fd, "rb", buffering=0)

        class _Raw(io.RawIOBase):
            def readable(self) -> bool:
                return True

            def readinto(self, buf: bytearray) -> int:
                if fail_on_read:
                    raise OSError(5, "simulated EIO during read")
                view = memoryview(buf)
                if max_per_read is not None:
                    view = view[:max_per_read]
                return inner.readinto(view) or 0  # type: ignore[attr-defined]

            def fileno(self) -> int:
                return inner.fileno()  # type: ignore[attr-defined]

            def close(self) -> None:
                inner.close()  # type: ignore[attr-defined]
                super().close()

        return _Raw()

    return _fdopen


@pytest.mark.parametrize("per_read", [1, 43])
def test_short_read_of_a_malformed_cachedir_tag_does_not_prune_or_hide_an_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, per_read: int
) -> None:
    # 43 == len(signature): a read that returns exactly the signature bytes of a longer file is
    # NOT end-of-file. `head == sig` used to accept it, prune pkg/ and hide an undeclared edit.
    monkeypatch.setattr(
        edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=per_read), raising=False
    )
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "CACHEDIR.TAG").write_bytes(
        b"Signature: 8a477f597d28d172789f06886806bc55NOT-A-SIGNATURE-LINE"
    )
    core = pkg / "core.py"
    core.write_text("x = 1\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    assert "pkg/core.py" in ticket.pre_edit_fingerprints
    core.write_text("x = 2\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["violations"] == ["pkg/core.py"]


@pytest.mark.parametrize("per_read", [1, 43])
@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
def test_short_reads_of_a_valid_cachedir_tag_still_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, per_read: int, ending: bytes
) -> None:
    monkeypatch.setattr(
        edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=per_read), raising=False
    )
    d = tmp_path / "cache"
    d.mkdir()
    tail = b"# a comment line\n" if ending else b""
    (d / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG.rstrip(b"\n") + ending + tail)
    (d / "x.py").write_text("1\n", encoding="utf-8")
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert "cache/x.py" not in files
    assert "cache" in population["pruned_dirs"]


def test_signature_followed_by_a_lone_carriage_return_is_not_a_valid_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CRLF is accepted (a Windows-edited tag), a bare CR or any other tail is not
    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=1), raising=False)
    d = tmp_path / "cache"
    d.mkdir()
    (d / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG.rstrip(b"\n") + b"\r")
    (d / "x.py").write_text("1\n", encoding="utf-8")
    files, _population = _walk_tracked_files_bounded(tmp_path)
    assert "cache/x.py" in files


def test_short_read_leaf_fingerprint_equals_the_full_read_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "f.py").write_bytes(b"x = 1\n" * 5000)
    full, _p = _walk_tracked_files_bounded(tmp_path)
    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=7), raising=False)
    short, _p2 = _walk_tracked_files_bounded(tmp_path)
    assert short == full


def test_short_read_marker_digest_equals_the_full_read_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "env"
    env.mkdir()
    (env / "pyvenv.cfg").write_bytes(b"home = x\n" * 100)
    _f, full = _walk_tracked_files_bounded(tmp_path)
    monkeypatch.setattr(edit_ticket_service, "_fdopen", _fdopen_with(max_per_read=3), raising=False)
    _f2, short = _walk_tracked_files_bounded(tmp_path)
    assert short["pruned_set"] == full["pruned_set"]


def _tree_for(kind: str, tmp_path: Path) -> None:
    if kind == "tag":
        d = tmp_path / "cache"
        d.mkdir()
        (d / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG)
    elif kind == "marker":
        d = tmp_path / "env"
        d.mkdir()
        (d / "pyvenv.cfg").write_bytes(b"home = x\n")
    else:
        (tmp_path / "f.py").write_bytes(b"x = 1\n")


@pytest.mark.parametrize("kind", ["tag", "marker", "leaf"])
def test_oserror_during_read_is_unreadable_path_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _tree_for(kind, tmp_path)
    monkeypatch.setattr(
        edit_ticket_service, "_fdopen", _fdopen_with(fail_on_read=True), raising=False
    )
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
