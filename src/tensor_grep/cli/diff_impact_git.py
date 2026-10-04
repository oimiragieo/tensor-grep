"""Hardened git plumbing for diff-impact: sanitised env, batch blob reads, batch file hashing.

Split out of diff_impact.py (file-size ratchet). One long-lived `git cat-file --batch` and one
`git hash-object --stdin-paths` process serve a whole mapping run, instead of two spawns per
analysed file.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import subprocess
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


def parse_hash_reply(stream: IO[bytes]) -> str | None:
    raw = stream.readline()
    if not raw:  # EOF: the process died or the watchdog killed it, which is not "no hash"
        raise BlobUnavailable("git hash-object closed its output")
    line = raw.decode("ascii", errors="replace").strip()
    return line if OID_RE.match(line) else None


def c_quote_path(path: str) -> str:
    """Quote a path for `--stdin-paths` when a bare line would be misread (quote/newline/etc.)."""
    if path[:1] != '"' and not any(c in path for c in '\\\n\r\t"'):
        return path
    escaped = (
        path
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


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
    """The per-run batch processes: `cat-file --batch` (blobs) and `hash-object --stdin-paths`."""

    def __init__(self, root: Path, deadline_monotonic: float | None = None) -> None:
        self.root = root
        self.deadline_monotonic = deadline_monotonic
        self._cat: _GitBatch | None = None
        self._hash: _GitBatch | None = None

    def cat(self) -> _GitBatch:
        if self._cat is None:
            self._cat = _GitBatch(self.root, ["cat-file", "--batch"], self.deadline_monotonic)
        return self._cat

    def hasher(self) -> _GitBatch:
        if self._hash is None:
            self._hash = _GitBatch(
                self.root, ["hash-object", "--stdin-paths"], self.deadline_monotonic
            )
        return self._hash

    def close(self) -> None:
        for batch in (self._cat, self._hash):
            if batch is not None:
                batch.close()
        self._cat = self._hash = None


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
