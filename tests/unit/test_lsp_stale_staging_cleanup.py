"""The staging/backup cleanup must clear a SYMLINK, not silently step over it.

WHY THIS EXISTS. `_ensure_node_runtime` stages a downloaded Node runtime at a predictable
sibling path and clears any leftover first. That clear used
`if p.exists(): shutil.rmtree(p, ignore_errors=True)`.

`shutil.rmtree` REFUSES a symlink, and `ignore_errors=True` silences the refusal -- so the guard
was a no-op against exactly the input it most needed to clear. The following
`shutil.move(extracted_dir, staged_dir)` then resolves the surviving link and deposits the
downloaded runtime INSIDE the link target.

Measured, not theorised (2026-08-05): rmtree left the link, and the move landed the payload in
the target directory beside an untouched canary.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli.lsp_provider_setup import _remove_stale_staging_path


def _symlink_or_skip(link: Path, target: Path) -> None:
    """Windows needs privilege for symlinks; skipping is honest, silently passing is not."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform dependent
        pytest.skip(f"symlink creation unavailable here: {exc}")


def test_a_symlinked_staging_path_is_removed(tmp_path: Path) -> None:
    """The red arm: the OLD implementation left this link in place."""
    target = tmp_path / "attacker_target"
    target.mkdir()
    canary = target / "canary.txt"
    canary.write_text("original", encoding="utf-8")
    link = tmp_path / "staging"
    _symlink_or_skip(link, target)

    _remove_stale_staging_path(link)

    assert not link.is_symlink(), (
        "the staging symlink survived cleanup -- a later shutil.move would follow it and write "
        "the downloaded runtime into the link target"
    )
    assert not link.exists()
    assert target.is_dir() and canary.read_text(encoding="utf-8") == "original", (
        "cleanup must remove the LINK, never recurse into and destroy its target"
    )


def test_the_move_after_cleanup_cannot_reach_the_link_target(tmp_path: Path) -> None:
    """End-to-end on the real sequence: clean, then move, and prove the target is untouched.

    Asserting only that the link is gone would miss a cleanup that removed the link but left the
    directory-shaped hazard some other way. This reproduces the actual two-step the production
    code performs.
    """
    target = tmp_path / "attacker_target"
    target.mkdir()
    (target / "canary.txt").write_text("original", encoding="utf-8")
    staged = tmp_path / "staging"
    _symlink_or_skip(staged, target)

    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "node.exe").write_text("payload", encoding="utf-8")

    _remove_stale_staging_path(staged)
    shutil.move(str(extracted), str(staged))

    # ESCAPE ASSERTIONS FIRST. An adversarial review caught that this test previously led with
    # `(staged/"node.exe").is_file()`, so in the red arm it failed on "payload did not land at
    # staging" and the assertions naming the actual security consequence NEVER EXECUTED. The test
    # discriminated, but not on the claim it is named for.
    assert not (target / "node.exe").exists(), "payload escaped into the link target"
    assert not (target / "extracted").exists(), "payload escaped into the link target"
    assert sorted(p.name for p in target.iterdir()) == ["canary.txt"], (
        f"link target was modified: {sorted(p.name for p in target.iterdir())}"
    )
    assert (staged / "node.exe").is_file(), "the payload should land at the staging path itself"


def test_a_dangling_symlink_is_also_removed(tmp_path: Path) -> None:
    """`exists()` follows links and is False for a DANGLING one.

    The old guard therefore skipped a broken link entirely -- while `shutil.move` would still
    resolve it. This is the case a fix that merely swapped rmtree for `if exists(): unlink()`
    would still get wrong.
    """
    link = tmp_path / "staging"
    _symlink_or_skip(link, tmp_path / "does_not_exist")
    assert link.is_symlink() and not link.exists()

    _remove_stale_staging_path(link)

    assert not link.is_symlink()


def test_an_ordinary_stale_directory_is_still_removed(tmp_path: Path) -> None:
    """CONTROL: the normal path must keep working, or the fix trades one break for another."""
    stale = tmp_path / "staging"
    (stale / "nested").mkdir(parents=True)
    (stale / "nested" / "f.txt").write_text("x", encoding="utf-8")

    _remove_stale_staging_path(stale)

    assert not stale.exists()


def test_a_missing_path_is_a_no_op(tmp_path: Path) -> None:
    """CONTROL: cleanup runs unconditionally now, so absence must not raise."""
    _remove_stale_staging_path(tmp_path / "never_existed")


def _junction_or_skip(link: Path, target: Path) -> None:
    """Windows directory junction. Unlike a symlink this needs NO privilege to create."""
    if os.name != "nt":  # pragma: no cover - platform dependent
        pytest.skip("junctions are Windows-only")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:  # pragma: no cover - platform dependent
        pytest.skip(f"mklink unavailable: {result.stderr.strip()}")


def test_a_windows_junction_staging_path_is_removed(tmp_path: Path) -> None:
    """The case an is_symlink()-first fix MISSES, and the more reachable one on Windows.

    `Path.is_symlink()` is False for a junction while `is_dir()` is True, and `shutil.rmtree`
    refuses junctions exactly as it refuses symlinks. So the first cut of this fix left a junction
    in place and the subsequent move still escaped into its target -- measured, not theorised.

    Creating a symlink on Windows needs SeCreateSymbolicLinkPrivilege; creating a junction needs
    nothing. On the one platform where the attacker is plausibly unprivileged, this is THE case.
    """
    target = tmp_path / "attacker_target"
    target.mkdir()
    (target / "canary.txt").write_text("original", encoding="utf-8")
    junction = tmp_path / "staging"
    _junction_or_skip(junction, target)

    _remove_stale_staging_path(junction)

    assert not junction.exists(), "the junction survived cleanup"
    assert target.is_dir() and (target / "canary.txt").exists(), (
        "cleanup must remove the JUNCTION, never recurse into and destroy its target"
    )


def test_the_move_after_cleanup_cannot_reach_a_junction_target(tmp_path: Path) -> None:
    """End-to-end junction equivalent of the symlink escape test."""
    target = tmp_path / "attacker_target"
    target.mkdir()
    (target / "canary.txt").write_text("original", encoding="utf-8")
    staged = tmp_path / "staging"
    _junction_or_skip(staged, target)

    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "node.exe").write_text("payload", encoding="utf-8")

    _remove_stale_staging_path(staged)
    shutil.move(str(extracted), str(staged))

    assert sorted(p.name for p in target.iterdir()) == ["canary.txt"], (
        f"payload escaped into the junction target: {sorted(p.name for p in target.iterdir())}"
    )
    assert (staged / "node.exe").is_file()
