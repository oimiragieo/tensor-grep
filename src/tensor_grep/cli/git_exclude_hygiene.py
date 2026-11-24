"""Local `.git/info/exclude` hygiene for `.tensor-grep/` state directories."""

from __future__ import annotations

import os
import stat
from pathlib import Path

_MAX_ASCENT = 24
_MAX_GITDIR_FILE_BYTES = 4096


def _is_symlink_or_reparse(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _lstat_if_exists(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _has_reparse_in_path(path: Path) -> bool:
    """Return True if `path` or any existing ancestor directory is a symlink or reparse point."""
    for part in [path, *path.parents]:
        st = _lstat_if_exists(part)
        if st is not None and _is_symlink_or_reparse(st):
            return True
    return False


def _is_safe_worktree_gitdir(target: Path, dot_git: Path) -> bool:
    """Allow `gitdir:` targets only when they point to a real `.git/worktrees/<name>` with matching backpointer."""
    if not (
        target.name != ""
        and target.parent.name == "worktrees"
        and target.parent.parent.name == ".git"
        and not _has_reparse_in_path(target)
    ):
        return False
    backpointer = target / "gitdir"
    bp_st = _lstat_if_exists(backpointer)
    if bp_st is None or _is_symlink_or_reparse(bp_st) or not stat.S_ISREG(bp_st.st_mode):
        return False
    if bp_st.st_size > _MAX_GITDIR_FILE_BYTES:
        return False
    try:
        bp_bytes = _read_regular_file_verified(backpointer, bp_st)
        if bp_bytes is None:
            return False
        bp_raw = bp_bytes.decode("utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return False
    if not bp_raw:
        return False
    bp_path = Path(bp_raw)
    if not bp_path.is_absolute():
        bp_path = target / bp_path
    return os.path.normcase(os.path.normpath(bp_path)) == os.path.normcase(
        os.path.normpath(dot_git)
    )


def _resolve_git_dir(repo_root: Path) -> Path | None:
    """Walk up from `repo_root` to find a real `.git` dir or safe `gitdir:` pointer."""
    start = Path(os.path.abspath(repo_root))
    for candidate in [start, *start.parents][:_MAX_ASCENT]:
        dot_git = candidate / ".git"
        st = _lstat_if_exists(dot_git)
        if st is None:
            continue
        if _has_reparse_in_path(dot_git):
            return None
        if stat.S_ISDIR(st.st_mode):
            return dot_git
        if stat.S_ISREG(st.st_mode):
            if st.st_size > _MAX_GITDIR_FILE_BYTES:
                return None
            try:
                raw_bytes = _read_regular_file_verified(dot_git, st)
                if raw_bytes is None:
                    return None
                content = raw_bytes.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                return None
            first_line = content.splitlines()[0].strip() if content.splitlines() else ""
            if not first_line.lower().startswith("gitdir:"):
                return None
            raw_target = first_line[7:].strip()
            if not raw_target:
                return None
            raw_path = Path(raw_target)
            joined = raw_path if raw_path.is_absolute() else (candidate / raw_path)
            target = Path(os.path.normpath(joined))
            target_st = _lstat_if_exists(target)
            if target_st is None or not stat.S_ISDIR(target_st.st_mode):
                return None
            if not _is_safe_worktree_gitdir(target, dot_git):
                return None
            common_git = target.parent.parent
            return common_git
        return None
    return None


def _read_regular_file_verified(path: Path, expected_st: os.stat_result) -> bytes | None:
    """Read `path` using O_NOFOLLOW (when available) and verify opened-object st_ino/st_dev identity."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        opened_st = os.fstat(fd)
        if not stat.S_ISREG(opened_st.st_mode):
            return None
        if expected_st.st_ino and (
            opened_st.st_ino != expected_st.st_ino or opened_st.st_dev != expected_st.st_dev
        ):
            return None
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


def ensure_git_info_exclude(repo_root: Path, entry: str = ".tensor-grep/") -> bool:
    """Ensure `entry` is listed in `.git/info/exclude` without touching `.gitignore`.

    Returns True when `.git/info/exclude` contains `entry` (added or already present),
    and False on non-git directories, symlinked git metadata, or OS/permission errors.
    """
    try:
        git_dir = _resolve_git_dir(Path(repo_root))
        if git_dir is None:
            return False

        info_dir = git_dir / "info"
        info_st = _lstat_if_exists(info_dir)
        if info_st is None:
            info_dir.mkdir(parents=False, exist_ok=True)
            info_st = _lstat_if_exists(info_dir)
        if info_st is None or _has_reparse_in_path(info_dir) or not stat.S_ISDIR(info_st.st_mode):
            return False

        exclude_path = info_dir / "exclude"
        from tensor_grep.cli._index_lock import atomic_write_bytes, index_lock

        with index_lock(exclude_path):
            excl_st = _lstat_if_exists(exclude_path)
            existing_bytes = b""
            if excl_st is not None:
                if _is_symlink_or_reparse(excl_st) or not stat.S_ISREG(excl_st.st_mode):
                    return False
                verified = _read_regular_file_verified(exclude_path, excl_st)
                if verified is None:
                    return False
                existing_bytes = verified

            existing_text = existing_bytes.decode("utf-8", errors="replace")
            accepted = {entry, entry.rstrip("/")}
            if any(line.strip() in accepted for line in existing_text.splitlines()):
                return True

            newline = b"\r\n" if b"\r\n" in existing_bytes else b"\n"
            prefix = b""
            if existing_bytes and not existing_bytes.endswith((b"\n", b"\r")):
                prefix = newline
            updated_bytes = existing_bytes + prefix + entry.encode("utf-8") + newline
            atomic_write_bytes(exclude_path, updated_bytes)
        return True
    except OSError:
        return False
