"""Identity checks and best-effort confined checkpoint rollback."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from functools import partial
from pathlib import Path

from tensor_grep.cli._index_lock import (
    _is_link_or_junction,
    atomic_write_bytes_confined,
    dir_identity,
)
from tensor_grep.io.confined import unlink_confined, validate_confined_parent_prefix


def check_restore_target(
    root: Path, path: Path, parent_identity: tuple[int, int], leaf_identity: tuple[int, int]
) -> None:
    validate_confined_parent_prefix(root, path)
    info = path.lstat()
    if (
        dir_identity(path.parent) != parent_identity
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or _is_link_or_junction(path)
        or (info.st_dev, info.st_ino) != leaf_identity
    ):
        raise OSError("checkpoint restore target identity changed")


def read_for_rollback(
    root: Path, path: Path, checkpoint_id: str, read: Callable[[Path, str], bytes]
) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    parent_identity = dir_identity(path.parent)
    info = path.lstat()
    leaf_identity = (info.st_dev, info.st_ino)
    check_restore_target(root, path, parent_identity, leaf_identity)
    data = read(path, checkpoint_id)
    check_restore_target(root, path, parent_identity, leaf_identity)
    after_read = path.lstat()
    if (info.st_size, info.st_mtime_ns) != (after_read.st_size, after_read.st_mtime_ns):
        raise OSError("checkpoint restore target changed during rollback read")
    return data, parent_identity, leaf_identity


def unlink_with_pinned_parent(
    root: Path,
    path: Path,
    captured_parent: tuple[int, int] | None,
    captured_file: tuple[int, int] | None,
) -> None:
    """Remove only the undo-created leaf under the parent recorded in its journal."""
    if captured_parent is None or captured_file is None:
        raise OSError("Refusing to remove an undo target without captured file identities")
    if dir_identity(path.parent) != captured_parent:
        raise OSError("Refusing to remove an undo target whose parent directory changed")
    unlink_confined(
        root,
        path,
        expected_parent_identity=captured_parent,
        expected_file_identity=captured_file,
    )


def publish_restored_file(
    root: Path,
    path: Path,
    data: bytes,
    metadata: os.stat_result,
    *,
    expected_parent_identity: tuple[int, int] | None = None,
    expected_file_identity: tuple[int, int] | None = None,
    expect_absent: bool = False,
    on_new_file_identity: Callable[[tuple[int, int]], None] | None = None,
) -> None:
    """Restore bytes atomically under the expected parent without writing through hardlinks."""
    times = (metadata.st_atime_ns, metadata.st_mtime_ns)
    mode = stat.S_IMODE(metadata.st_mode)
    new_file_identity: list[tuple[int, int] | None] = [None]

    def preserve_metadata(descriptor: int, temporary: Path) -> None:
        if os.name == "nt":
            os.utime(temporary, ns=times)
            os.chmod(temporary, mode)
        else:
            os.utime(descriptor, ns=times)
            fchmod = getattr(os, "fchmod", None)
            if fchmod is None:
                raise OSError("checkpoint restore cannot preserve file mode on this platform")
            fchmod(descriptor, mode)
        if on_new_file_identity is not None:
            info = os.fstat(descriptor)
            new_file_identity[0] = (info.st_dev, info.st_ino)

    def record_published_identity() -> None:
        if on_new_file_identity is not None:
            identity = new_file_identity[0]
            if identity is None:
                raise OSError("restored file identity was not captured before publication")
            on_new_file_identity(identity)

    atomic_write_bytes_confined(
        root,
        path,
        data,
        replace=not expect_absent,
        temp_ready=preserve_metadata,
        expected_parent_identity=expected_parent_identity,
        expected_file_identity=expected_file_identity,
        after_publish=record_published_identity,
    )


def rollback_overwrites(
    root: Path,
    entries: list[
        tuple[
            Path,
            bytes | None,
            os.stat_result | None,
            tuple[int, int],
            list[tuple[int, int] | None],
        ]
    ],
) -> list[Path]:
    failures: list[Path] = []
    for path, prior_bytes, prior_info, parent_identity, published_identity in reversed(entries):
        try:
            expected_file_identity = published_identity[0]
            if prior_bytes is None:
                if expected_file_identity is not None:
                    unlink_with_pinned_parent(root, path, parent_identity, expected_file_identity)
                continue
            if expected_file_identity is None and prior_info is not None:
                expected_file_identity = (prior_info.st_dev, prior_info.st_ino)
            if prior_info is None or expected_file_identity is None:
                raise OSError("Refusing to restore an undo target without captured identities")
            publish_restored_file(
                root,
                path,
                prior_bytes,
                prior_info,
                expected_parent_identity=parent_identity,
                expected_file_identity=expected_file_identity,
            )
        except BaseException:
            failures.append(path)
    return failures


def _check_removed_parent(path: Path, identity: tuple[int, int]) -> None:
    if dir_identity(path.parent) != identity:
        raise OSError("checkpoint removed parent identity changed")


def _check_removed_parent_after_publish(
    _file_identity: tuple[int, int], *, path: Path, identity: tuple[int, int]
) -> None:
    _check_removed_parent(path, identity)


def rollback_removes(
    root: Path,
    entries: list[tuple[Path, bytes, os.stat_result, tuple[int, int]]],
) -> list[Path]:
    failures: list[Path] = []
    for path, prior_bytes, prior_info, parent_identity in reversed(entries):
        try:
            check = partial(_check_removed_parent, path, parent_identity)
            check()
            if os.path.lexists(path):
                current = path.lstat()
                if (current.st_dev, current.st_ino) != (prior_info.st_dev, prior_info.st_ino):
                    raise OSError("checkpoint removed target was replaced before rollback")
                continue
            publish_restored_file(
                root,
                path,
                prior_bytes,
                prior_info,
                expected_parent_identity=parent_identity,
                expect_absent=True,
                on_new_file_identity=partial(
                    _check_removed_parent_after_publish, path=path, identity=parent_identity
                ),
            )
        except BaseException:
            failures.append(path)
    return failures
