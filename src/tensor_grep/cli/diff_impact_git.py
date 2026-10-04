"""Hardened git plumbing for diff-impact: sanitised env, batch blob reads, batch file hashing.

Split out of diff_impact.py (file-size ratchet). One long-lived `git cat-file --batch` process
serves a whole staged/range mapping run, instead of two spawns per analysed file. Working-tree
files need no git process at all: their bytes are bound to the diff's post-image id in-process.
"""

from __future__ import annotations

import contextlib
import contextvars
import errno
import hashlib
import os
import re
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, Any

from tensor_grep.cli.subprocess_policy import (
    configured_git_timeout_seconds,
    deadline_capped_timeout_seconds,
)

# Environment variables that change `git diff` output or which repository/index git reads. The
# argv pins the config twin of these; this is the environment twin. GIT_DIR/GIT_WORK_TREE/
# GIT_INDEX_FILE are stripped unconditionally (decided: the cwd plus `rev-parse --show-toplevel`
# decide the repo, so an inherited redirect, e.g. from a hook, cannot point us at another one).
_GIT_ENV_STRIP = frozenset({
    "GIT_DIFF_OPTS",
    "GIT_EXTERNAL_DIFF",
    "GIT_PAGER",
    "PAGER",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_DIFF_PATH_COUNTER",
    "GIT_DIFF_PATH_TOTAL",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
})
_GIT_ENV_STRIP_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def git_env() -> dict[str, str]:
    """A copy of os.environ safe for a read-only `git diff` (stable, non-localized output)."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in _GIT_ENV_STRIP and not k.upper().startswith(_GIT_ENV_STRIP_PREFIXES)
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


class BlobUnavailable(Exception):
    """The content a diff record describes could not be read or did not match its oid."""


class BlobDeadline(BlobUnavailable):
    """The overall --deadline passed: no further exchange is started and nothing is respawned."""


class BlobOverCap(Exception):
    """The blob exceeds the per-file parse byte cap the extractors themselves enforce."""


OID_RE = re.compile(r"^[0-9a-f]{4,64}$")


def git_cmd(*args: str) -> list[str]:
    return ["git", "-c", "core.quotepath=false", "-c", "core.fsmonitor=false", *args]


def spawn_git(root: Path, args: list[str]) -> subprocess.Popen[bytes]:
    """Start ONE long-lived git process (hardened -c pins and env) that answers line requests."""
    return subprocess.Popen(
        git_cmd(*args),
        cwd=str(root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=git_env(),
    )


def _discard(stream: IO[bytes], count: int) -> None:
    """Consume `count` bytes without retaining them (keeps a batch stream in sync)."""
    while count > 0:
        chunk = stream.read(min(count, 65536))
        if not chunk:
            raise BlobUnavailable("git closed its output mid-reply")
        count -= len(chunk)


def parse_cat_file_reply(stream: IO[bytes], max_bytes: int) -> bytes:
    """Read one `git cat-file --batch` reply: `<oid> <type> <size>\\n<bytes>\\n`.

    The type and the size are checked BEFORE the bytes are read, so an over-cap blob is never
    held in memory (its bytes are drained to keep the stream in sync).
    """
    header = stream.readline()
    if not header:
        raise BlobUnavailable("git cat-file closed its output")
    parts = header.decode("ascii", errors="replace").split()
    if len(parts) == 2 and parts[1] in ("missing", "ambiguous"):
        raise BlobUnavailable(f"git cat-file: {parts[0]} is {parts[1]}")
    if len(parts) != 3 or not parts[2].isdigit() or not parts[2].isascii():
        raise BlobUnavailable(f"unexpected git cat-file header: {header[:80]!r}")
    kind, size = parts[1], int(parts[2])
    if kind != "blob":
        _discard(stream, size + 1)
        raise BlobUnavailable(f"{parts[0]} is a {kind}, not a blob")
    if size > max_bytes:
        _discard(stream, size + 1)
        raise BlobOverCap(f"blob {parts[0]} is {size} bytes (cap {max_bytes})")
    data = stream.read(size)
    trailer = stream.read(1)
    if len(data) != size or trailer != b"\n":
        raise BlobUnavailable(f"truncated git cat-file reply for {parts[0]}")
    return data


def _blob_ids(data: bytes, oid_prefix: str) -> bool:
    """True if the git blob id of `data` starts with `oid_prefix`.

    The object format follows the id's length (git reports full ids in the repo's own format, and
    the diff argv passes --full-index): 64 hex is a SHA-256 repository, 40 is SHA-1. An
    abbreviation cannot say, so both are tried.
    """
    header = f"blob {len(data)}\0".encode()
    names: tuple[str, ...]
    if len(oid_prefix) == 64:
        names = ("sha256",)
    elif len(oid_prefix) == 40:
        names = ("sha1",)
    else:
        names = ("sha1", "sha256")
    return any(
        hashlib.new(name, header + data, usedforsecurity=False).hexdigest().startswith(oid_prefix)
        for name in names
    )


def blob_hash_matches(data: bytes, oid_prefix: str) -> bool:
    return _blob_ids(data, oid_prefix)


def classify_worktree_content(raw: bytes, oid_prefix: str) -> str:
    """Bind working-tree bytes to the diff's post-image id, in-process (no git, no race).

    "exact": the bytes ARE the described content. "eol": the only difference is CRLF -> LF
    normalisation, so line structure is preserved and the extractor (which handles CRLF) may run
    on the raw bytes. "transformed": a git filter or an edit changed the content in a way that
    cannot be mapped back to the diff's line ranges; the bytes must NOT be analysed.
    """
    if _blob_ids(raw, oid_prefix):
        return "exact"
    if b"\r\n" in raw and _blob_ids(raw.replace(b"\r\n", b"\n"), oid_prefix):
        return "eol"
    return "transformed"


class _GitBatch:
    """One long-lived `git` process serving many requests (a single spawn per run).

    Each exchange is bounded by the existing git timeout (a watchdog kills the process, which
    turns a hang into EOF), and a dead process is respawned for the next request, so one bad path
    cannot poison the rest. `close()` always kills and reaps it.
    """

    def __init__(
        self, root: Path, args: list[str], deadline_monotonic: float | None = None
    ) -> None:
        self.root = root
        self.args = args
        self.deadline_monotonic = deadline_monotonic
        self.proc: subprocess.Popen[bytes] | None = None

    def _expired(self) -> bool:
        return self.deadline_monotonic is not None and time.monotonic() >= self.deadline_monotonic

    def _ensure(self) -> subprocess.Popen[bytes]:
        if self._expired():  # never spawn or respawn past the deadline
            raise BlobDeadline("overall deadline exceeded")
        if self.proc is None or self.proc.poll() is not None:
            self.close()
            self.proc = spawn_git(self.root, self.args)
        return self.proc

    def exchange(self, request: str, read_reply: Callable[[IO[bytes]], Any]) -> Any:
        # Every wait is capped by the REMAINING deadline, not the full git timeout: the watchdog
        # below is the only thing that can interrupt a blocked pipe read.
        timeout = deadline_capped_timeout_seconds(
            configured_git_timeout_seconds(), deadline_monotonic=self.deadline_monotonic
        )
        if timeout is None:
            raise BlobDeadline("overall deadline exceeded")
        try:
            proc = self._ensure()
        except OSError as exc:
            raise BlobUnavailable(str(exc)) from exc
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(request.encode("utf-8", errors="surrogateescape") + b"\n")
            proc.stdin.flush()
            return read_reply(proc.stdout)
        except (OSError, ValueError, BlobUnavailable) as exc:
            if proc.poll() is not None or isinstance(exc, (OSError, ValueError)):
                self.close()
            if self._expired():  # the watchdog fired because the deadline ran out
                raise BlobDeadline("overall deadline exceeded") from exc
            if isinstance(exc, BlobUnavailable):
                raise
            raise BlobUnavailable(str(exc)) from exc
        finally:
            timer.cancel()

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        with contextlib.suppress(OSError):
            proc.kill()
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            proc.wait(timeout=1)  # killed above: reaping is immediate, never the full timeout


class _ContentSessions:
    """The per-run batch process: `git cat-file --batch` (blobs for staged/range diffs)."""

    def __init__(self, root: Path, deadline_monotonic: float | None = None) -> None:
        self.root = root
        self.deadline_monotonic = deadline_monotonic
        self._cat: _GitBatch | None = None

    def cat(self) -> _GitBatch:
        if self._cat is None:
            self._cat = _GitBatch(self.root, ["cat-file", "--batch"], self.deadline_monotonic)
        return self._cat

    def close(self) -> None:
        if self._cat is not None:
            self._cat.close()
        self._cat = None


_ACTIVE_SESSIONS: contextvars.ContextVar[_ContentSessions | None] = contextvars.ContextVar(
    "diff_impact_content_sessions", default=None
)


@contextlib.contextmanager
def content_sessions(
    root: Path, deadline_monotonic: float | None = None
) -> Iterator[_ContentSessions]:
    sessions = _ContentSessions(root, deadline_monotonic)
    token = _ACTIVE_SESSIONS.set(sessions)
    try:
        yield sessions
    finally:
        _ACTIVE_SESSIONS.reset(token)
        sessions.close()


# ---------------------------------------------------------------------------------------------
# Line numbering: git numbers lines by LF. Python's universal-newline reading also breaks on a
# lone CR, and `str.splitlines()` (used by several extractors) additionally breaks on VT, FF,
# FS, GS, RS, NEL, LS and PS. Content containing any of these can shift an extractor's line
# numbers away from git's, silently moving a changed symbol out of its diff range, so such
# content is REPORTED instead of analysed (rejecting is simpler than translating coordinates).
# ---------------------------------------------------------------------------------------------
_AMBIGUOUS_LINE_BREAK_RE = re.compile(
    rb"\r(?!\n)|[\x0b\x0c\x1c\x1d\x1e]|\xc2\x85|\xe2\x80[\xa8\xa9]"
)


def has_ambiguous_line_breaks(data: bytes) -> bool:
    return _AMBIGUOUS_LINE_BREAK_RE.search(data) is not None


# ---------------------------------------------------------------------------------------------
# Confined, non-blocking read of one working-tree file.
# ---------------------------------------------------------------------------------------------
class PathChanged(Exception):
    """The path is no longer the regular in-root file the diff described (swapped/vanished)."""


class UnreadablePath(Exception):
    """The path could not be opened or read (permissions, I/O error)."""


_READ_CHUNK = 65536
_PATH_CHANGED_ERRNOS = {
    errno.ELOOP,
    errno.ENOTDIR,
    errno.ENXIO,
    errno.EISDIR,
    errno.ENOENT,
    getattr(errno, "EMLINK", -1),
}


def _read_fd_bounded(read: Callable[[int], bytes], cap: int) -> bytes:
    chunks: list[bytes] = []
    remaining = cap + 1
    while remaining > 0:
        block = read(min(remaining, _READ_CHUNK))
        if not block:
            break
        chunks.append(block)
        remaining -= len(block)
    data = b"".join(chunks)
    if len(data) > cap:
        raise BlobOverCap(f"file exceeds the {cap}-byte parse cap")
    return data


if sys.platform != "win32":

    def _read_confined_posix(root: Path, rel_path: Path, cap: int) -> bytes:
        parts = rel_path.parts
        if not parts or rel_path.is_absolute() or any(p in ("", ".", "..") for p in parts):
            raise PathChanged(f"not a plain relative path: {rel_path}")
        opened: list[int] = []
        try:
            current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            opened.append(current)
            for component in parts[:-1]:  # one component at a time, never following a symlink
                current = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=current,
                )
                opened.append(current)
            # O_NONBLOCK: opening a FIFO with no writer must return, never block forever.
            fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=current,
            )
            opened.append(fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PathChanged(f"{rel_path} is not a regular file")
            return _read_fd_bounded(lambda n: os.read(fd, n), cap)
        except OSError as exc:
            if exc.errno in _PATH_CHANGED_ERRNOS:
                raise PathChanged(str(exc)) from exc
            raise UnreadablePath(str(exc)) from exc
        finally:
            for descriptor in reversed(opened):
                with contextlib.suppress(OSError):
                    os.close(descriptor)

    # Read at most `cap` bytes of the regular file `rel_path` under `root`, through ONE handle.
    # Never follows a symlink or reparse point (final component OR any parent), never blocks on a
    # FIFO or device, and refuses anything that is not a regular file inside `root`. Raises
    # PathChanged (swapped/vanished/not a regular file), UnreadablePath, or BlobOverCap.
    read_regular_file_confined = _read_confined_posix

else:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _k32.GetFileInformationByHandle.restype = wintypes.BOOL
    _k32.GetFileType.argtypes = [wintypes.HANDLE]
    _k32.GetFileType.restype = wintypes.DWORD
    _k32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL

    _GENERIC_READ = 0x80000000
    _SHARE_ALL = 0x7
    _OPEN_EXISTING = 3
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _ATTR_DIRECTORY = 0x10
    _ATTR_REPARSE_POINT = 0x400
    _FILE_TYPE_DISK = 1
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    def _strip_win_prefix(path: str) -> str:
        if path.startswith("\\\\?\\UNC\\"):
            return "\\\\" + path[8:]
        if path.startswith("\\\\?\\"):
            return path[4:]
        return path

    def _read_confined_windows(root: Path, rel_path: Path, cap: int) -> bytes:
        parts = rel_path.parts
        if not parts or rel_path.is_absolute() or any(p in ("", ".", "..") for p in parts):
            raise PathChanged(f"not a plain relative path: {rel_path}")
        # FILE_FLAG_OPEN_REPARSE_POINT: if the final component was swapped for a symlink or
        # junction we open the LINK ITSELF (and reject it) instead of following it.
        handle = _k32.CreateFileW(
            os.path.join(str(root), *parts),
            _GENERIC_READ,
            _SHARE_ALL,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle is None or handle == _INVALID_HANDLE:
            code = ctypes.get_last_error()
            if code in (2, 3):  # file / path not found: it vanished or a parent changed
                raise PathChanged(f"{rel_path} is gone (error {code})")
            raise UnreadablePath(f"CreateFileW failed with error {code}")
        fd: int | None = None
        try:
            info = _ByHandleFileInformation()
            if not _k32.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise UnreadablePath("GetFileInformationByHandle failed")
            if (
                info.dwFileAttributes & (_ATTR_REPARSE_POINT | _ATTR_DIRECTORY)
                or _k32.GetFileType(handle) != _FILE_TYPE_DISK
            ):
                raise PathChanged(f"{rel_path} is a reparse point or not a regular file")
            buffer = ctypes.create_unicode_buffer(32768)
            length = _k32.GetFinalPathNameByHandleW(handle, buffer, 32768, 0)
            if length == 0 or length >= 32768:
                raise UnreadablePath("GetFinalPathNameByHandleW failed")
            final = os.path.normcase(_strip_win_prefix(buffer.value))
            root_real = os.path.normcase(_strip_win_prefix(os.path.realpath(root))).rstrip("\\")
            if final != root_real and not final.startswith(root_real + "\\"):
                raise PathChanged(f"{rel_path} resolves outside the repository root")
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            handle = None  # the descriptor now owns the handle
            return _read_fd_bounded(lambda n: os.read(fd, n), cap)  # type: ignore[arg-type]
        except OSError as exc:
            raise UnreadablePath(str(exc)) from exc
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
            elif handle is not None:
                _k32.CloseHandle(handle)

    read_regular_file_confined = _read_confined_windows
