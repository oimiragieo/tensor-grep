"""M1 (A27/A39 class fix): create-side checkpoint containment for symlinked/junctioned
ANCESTOR directories.

The undo side already refuses a snapshot SOURCE whose ancestor directory is a symlink or
(Windows) junction escaping the snapshot dir (``_resolve_within_root`` over
``snapshot_dir``, audit H3 -- see test_undo_refuses_source_with_*_ancestor_directory in
test_checkpoint_containment.py). The CREATE side, ``create_checkpoint``'s copy loop, has the
identical defect and never gained the twin guard: it composes ``source = root / rel_path``
and copies with ``follow_symlinks=False``, which refuses only a link AT THE LEAF. A
symlinked or junctioned ANCESTOR inside root is transparently traversed by the OS, so
``shutil.copy2(root/a/b.txt, ...)`` reads the CONTENT of ``<out-of-root>/b.txt`` into the
checkpoint snapshot (out-of-root disclosure on the create side -- a checkpoint is a
persistent copied artifact, so the disclosed bytes survive until the checkpoint is pruned).

On Windows, an attacker needs NO privilege for the attack: directory junctions are created
unprivileged via ``mklink /J`` (unlike symlinks), and ``os.walk``/``os.scandir`` descend
junctions as if they were plain directories (a junction reads ``is_symlink() == False``),
so the filesystem-snapshot enumerator itself walks INTO the junction and lists the
out-of-root file as an ordinary entry. Symlinked ancestors are the Unix equivalent and
reach the same copy loop through the git-worktree-snapshot enumerator (``git ls-files``).

The fix resolves ONLY the parent chain (``(root / rel_path).parent.resolve()``) and refuses
containment violations, leaving the LEAF's raw identity intact -- a legitimately tracked
out-of-root-pointing LEAF symlink must still be stored AS A LINK, never followed and never
refused (law A38; do not copy ``_resolve_within_root``'s leaf-following `resolve()` onto the
create side).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "tg@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "tensor-grep"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_commit_all(path: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def _plant_ancestor_link_or_skip(repo: Path, rel_dir: str, target: Path) -> Path:
    """Replace ``repo/rel_dir`` with a link to ``target``, trying every mechanism that works
    on this platform and skipping ONLY when real link creation is genuinely impossible.

    Order on Windows: ``mklink /J`` (directory junction -- works UNPRIVILEGED, the actual
    attacker surface) first, then ``symlink_to(..., target_is_directory=True)`` as a
    fallback (requires Developer Mode). On other platforms: plain ``symlink_to``.
    """
    link = repo / rel_dir
    shutil.rmtree(link)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        if result.returncode == 0:
            return link
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("junction and symlink creation both failed on this platform")
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation requires privilege on this platform")
    return link


def _assert_no_secret_under(root: Path, secret: str) -> None:
    """Assert no REGULAR file under ``root`` contains ``secret`` (links themselves are fine)."""
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert secret not in path.read_text(encoding="utf-8", errors="ignore")


def test_create_checkpoint_refuses_junctioned_ancestor_directory(tmp_path: Path) -> None:
    """Windows attack platform: an ANCESTOR junction inside root must be refused on create.

    Plantation: ``root/a`` (originally a real dir holding ``b.txt``) is swapped for a
    junction pointing OUT of root at a directory holding a ``b.txt`` with distinctive
    content. The filesystem-snapshot enumerator walks THROUGH the junction (a junction
    reads as a plain directory, not a symlink), and the unguarded copy loop would then
    copy the OUT-OF-ROOT content into the snapshot -- disclosure of arbitrary readable
    files into a persistent copied artifact.
    """
    if os.name != "nt":
        pytest.skip("directory junctions are a Windows-only reparse-point mechanism")

    repo = tmp_path / "repo"
    repo.mkdir()
    tracked_dir = repo / "a"
    tracked_dir.mkdir()
    (tracked_dir / "b.txt").write_text("benign-in-repo\n", encoding="utf-8")

    outside = tmp_path / "outside_secret_dir"
    outside.mkdir()
    (outside / "b.txt").write_text("SECRET-OUT-OF-ROOT-VIA-JUNCTION\n", encoding="utf-8")

    link = _plant_ancestor_link_or_skip(repo, "a", outside)

    # Fixture-BITES precheck (oracle Form 6): prove the redirect actually resolves before
    # probing, or a vacuously-green run would read as "no defect".
    assert (link / "b.txt").resolve() == (outside / "b.txt").resolve(), (
        "fixture is vacuous: the junction does not resolve into the out-of-root dir"
    )
    assert not link.is_symlink(), "sanity: a junction must NOT read as is_symlink()"

    with pytest.raises(ValueError, match="Refusing checkpoint entry outside root"):
        checkpoint_store.create_checkpoint(str(repo))

    # The out-of-root content must never have been snapshotted anywhere under the repo.
    assert (outside / "b.txt").read_text(encoding="utf-8") == "SECRET-OUT-OF-ROOT-VIA-JUNCTION\n"
    storage = repo / checkpoint_store._CHECKPOINT_DIRNAME
    if storage.exists():
        _assert_no_secret_under(storage, "SECRET-OUT-OF-ROOT-VIA-JUNCTION")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_create_checkpoint_refuses_symlinked_ancestor_directory(tmp_path: Path) -> None:
    """Unix arm (and git-mode twin): a symlinked ANCESTOR of a TRACKED path must be refused.

    Filesystem mode never descends a symlinked dir (``os.walk(followlinks=False)``), so the
    Unix-visible defect lives in git-worktree-snapshot mode, where ``git ls-files`` lists the
    tracked path ``a/b.txt`` from the index even after ``a`` becomes a symlink -- the copy
    loop then follows the ancestor link. On Windows, junction mode reaches the same loop
    through the same enumerator when symlink creation is unavailable.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    tracked_dir = repo / "a"
    tracked_dir.mkdir()
    (tracked_dir / "b.txt").write_text("benign-in-repo\n", encoding="utf-8")
    _git_commit_all(repo, "init")

    outside = tmp_path / "outside_secret_dir"
    outside.mkdir()
    (outside / "b.txt").write_text("SECRET-OUT-OF-ROOT-VIA-ANCESTOR\n", encoding="utf-8")

    link = _plant_ancestor_link_or_skip(repo, "a", outside)

    assert (link / "b.txt").resolve() == (outside / "b.txt").resolve(), (
        "fixture is vacuous: the ancestor link does not resolve into the out-of-root dir"
    )

    with pytest.raises(ValueError, match="Refusing checkpoint entry outside root"):
        checkpoint_store.create_checkpoint(str(repo))

    assert (outside / "b.txt").read_text(encoding="utf-8") == "SECRET-OUT-OF-ROOT-VIA-ANCESTOR\n"
    storage = repo / checkpoint_store._CHECKPOINT_DIRNAME
    if storage.exists():
        _assert_no_secret_under(storage, "SECRET-OUT-OF-ROOT-VIA-ANCESTOR")


def test_create_checkpoint_normal_ancestor_dirs_still_snapshot(tmp_path: Path) -> None:
    """Control: a NORMAL tree (no link anywhere) must keep snapshotting -- the new ancestor
    guard must not be over-strict."""
    repo = tmp_path / "repo"
    repo.mkdir()
    nested = repo / "a" / "b" / "c" / "deep.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("deep-original\n", encoding="utf-8")

    created = checkpoint_store.create_checkpoint(str(repo))
    snapshot = checkpoint_store._snapshot_path(repo, created.checkpoint_id)

    assert (snapshot / "a" / "b" / "c" / "deep.py").read_text(encoding="utf-8") == "deep-original\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for git checkpoint tests")
def test_create_checkpoint_stores_tracked_leaf_symlink_as_link(tmp_path: Path) -> None:
    """Control (law A38): a legitimately tracked LEAF symlink pointing out of root must be
    stored AS A LINK -- never refused by the ancestor guard, never followed (the guard
    resolves only the PARENT chain; the leaf's raw identity survives)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "real.py").write_text("in-repo content\n", encoding="utf-8")

    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET-LEAF-TARGET\n", encoding="utf-8")
    link = repo / "link.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this platform")
    _git_commit_all(repo, "init")

    created = checkpoint_store.create_checkpoint(str(repo))
    snapshot = checkpoint_store._snapshot_path(repo, created.checkpoint_id)

    stored = snapshot / "link.txt"
    assert stored.is_symlink(), "the tracked leaf symlink must be stored AS a link, not followed"
    assert stored.resolve() == secret.resolve(), (
        "the stored link must still point at the ORIGINAL out-of-root target, untouched"
    )
    # No REGULAR file in the snapshot may contain the leaf target's content.
    _assert_no_secret_under(snapshot, "SECRET-LEAF-TARGET")
