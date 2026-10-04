"""Edit-ready tickets: pre-edit fingerprints and a fail-closed verify (AGT-04).

Threat model (bug hunt G-03/G-08):

* Defends against a cooperative-but-fallible agent that edits outside its declared scope or
  reports edits it did not make (pre-edit fingerprints vs the current tree). It is NOT a
  sandbox against a hostile agent: `.git/hooks`, `$HOME`, and edits inside a vendored
  `node_modules` are out of scope (residual risk).
* The population is a plain bounded filesystem walk with NO git dependency and a small,
  UNAMBIGUOUS prune set. Pruned at any depth by NAME: `_ALWAYS_PRUNED_DIRS` (never source).
  Pruned at any depth by CONTENT, where the marker must be a REGULAR, non-symlink file directly
  inside the directory: `pyvenv.cfg` (a Python venv, whatever its name), a `CACHEDIR.TAG` with
  the standard Cache Directory Tagging signature, `.rustc_info.json` (cargo target-dir root),
  and `CMakeCache.txt` only WITHOUT a sibling `CMakeLists.txt` (an in-source CMake build keeps
  real source beside the cache). EVERYTHING else is covered: `build/`, `dist/`, `target/`
  without a tag, `venv/` without `pyvenv.cfg`, git-ignored files, nested repos' working files.
* Availability cost (disclosed): an untagged large build output is walked and may hit the
  per-file/aggregate byte budget -> `population_incomplete` -> verify FAILs closed (unusable,
  never wrong).
* Unreadable subtrees (`os.walk` onerror) -> `unreadable_path`; more than `_MAX_WALK_DIRS`
  directories -> `dir_count_limit`. Symlink leaves are fingerprinted as `symlink:<target>` and
  never followed.
* Residuals: a `pyvenv.cfg` inside a SUBdirectory that also holds hand-edited source prunes
  that subdirectory (the walk root is never pruned); a marker planted BEFORE minting is outside
  the cooperative threat model.
* Every pruned directory (by name or by marker) is recorded at mint in `pruned_set`; at verify
  a pruned directory that did not exist at mint is `newly_pruned:<dir>` (fail closed: an agent
  that runs `npm install` mid-ticket must re-mint or declare it), a changed marker is
  `marker_changed:<dir>`, a dropped content-prune is `no_longer_pruned:<dir>`.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
import sys
import threading
import time
import uuid
from collections.abc import Generator, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple

# ---------------------------------------------------------------------------------------------
# Enumeration is BOUND TO AN OPEN DIRECTORY, never to a pathname lookup.
#
# A pathname `lstat` cannot authenticate which directory supplied a listing (A is moved aside, a
# symlink to an empty dir takes its name, the walker lists the impostor, A is restored before the
# tuple is yielded). So:
#   POSIX   - `os.fwalk(follow_symlinks=False)` lists through an open dirfd; every leaf is then
#             stat'ed / opened / readlink'ed RELATIVE to that dirfd (`dir_fd=`), and the dirfd's
#             own `fstat` identity is compared with the identity recorded at classification.
#   Windows - no `dir_fd`: every directory in the current root-to-leaf chain is HELD open
#             (`CreateFileW`, FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES, OPEN_EXISTING,
#             BACKUP_SEMANTICS | OPEN_REPARSE_POINT, share READ|WRITE but NOT DELETE) for as long
#             as it is walked, so it cannot be renamed, deleted or replaced; the handle must not
#             be a reparse point and its file id must match the recorded identity.
# The per-directory dirfd registry below makes the path-taking seams (`_lstat`, `_os_open`,
# `_readlink`) use `dir_fd=` automatically while a directory's tuple is being processed.
# ---------------------------------------------------------------------------------------------
_dir_ctx = threading.local()


def _ctx_dirfds() -> dict[str, int]:
    fds: dict[str, int] | None = getattr(_dir_ctx, "fds", None)
    if fds is None:
        fds = {}
        _dir_ctx.fds = fds
    return fds


def _dirfd_and_name(path: str | Path) -> tuple[int | None, str]:
    p = Path(path)
    return _ctx_dirfds().get(str(p.parent)), p.name


def _default_lstat(path: str | Path) -> os.stat_result:
    fd, name = _dirfd_and_name(path)
    if fd is None:
        return os.lstat(path)
    return os.stat(name, dir_fd=fd, follow_symlinks=False)


def _default_os_open(path: str | Path, flags: int) -> int:
    fd, name = _dirfd_and_name(path)
    if fd is None:
        return os.open(path, flags)
    return os.open(name, flags, dir_fd=fd)


def _default_readlink(path: str | Path) -> str:
    fd, name = _dirfd_and_name(path)
    if fd is None:
        return os.readlink(path)
    return os.readlink(name, dir_fd=fd)


_os_open = _default_os_open  # private seam: the ONE safe open of every file read from the tree
_fdopen = os.fdopen  # private seam: wraps the opened fd (unbuffered)
_readlink = _default_readlink  # private seam: link targets are read ONCE per link
_lstat = _default_lstat  # private seam for link classification

_ALWAYS_PRUNED_DIRS = frozenset({
    "node_modules",
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "site-packages",
})
_CACHEDIR_TAG_SIGNATURE = b"Signature: 8a477f597d28d172789f06886806bc55"
_BUILD_ROOT_MARKERS = ("pyvenv.cfg", ".rustc_info.json")
# CMakeCache.txt counts ONLY when the directory has no sibling CMakeLists.txt: an IN-SOURCE
# CMake build writes the cache next to the source tree's own CMakeLists.txt, and pruning that
# directory would hide real source from the population.
_CMAKE_CACHE = "CMakeCache.txt"
_CMAKE_SOURCE = "CMakeLists.txt"
_MAX_REPORTED_PRUNED = 200
_MAX_WALK_DIRS = 200_000
# Separate budgets: a name-pruned entry is a path and the string "name" (cheap), a
# content-pruned entry costs a marker read and a hash.
_MAX_NAME_PRUNED_DIRS = 50_000
_MAX_CONTENT_PRUNED_DIRS = 2_000
_NAME_PRUNED = "name"
_MARKER_HASH_CAP = 16 * 1024 * 1024

_DEFAULT_MAX_FILES = 20_000
_DEFAULT_MAX_FILE_BYTES = 10_000_000
_DEFAULT_MAX_AGGREGATE_BYTES = 200_000_000


@dataclass(frozen=True)
class EditReadyTicketV1:
    ticket_id: str
    version: int
    created_at: float
    repo_root: str
    target_path: str
    query: str
    allowed_files: list[str]
    working_tree_fingerprint: str
    pre_edit_fingerprints: dict[str, str]
    # AGT-04: explicit non-success population result (never a silent truncation). A ticket
    # deserialized from before this field existed defaults to "unknown" -- a documented
    # fail-open compatibility path for legacy tickets, not a relaxation of the new fail-closed
    # default that build_edit_ready_ticket now always sets for NEW tickets.
    population_status: dict[str, Any] = field(
        default_factory=lambda: {"status": "unknown", "verified": None}
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EditReadyTicketV1:
        return cls(
            ticket_id=str(data["ticket_id"]),
            version=int(data["version"]),
            created_at=float(data["created_at"]),
            repo_root=str(data["repo_root"]),
            target_path=str(data["target_path"]),
            query=str(data["query"]),
            allowed_files=list(data["allowed_files"]),
            working_tree_fingerprint=str(data["working_tree_fingerprint"]),
            pre_edit_fingerprints=dict(data["pre_edit_fingerprints"]),
            population_status=dict(data["population_status"])
            if "population_status" in data
            else {"status": "unknown", "verified": None},
        )


class _PopulationWalkError(Exception):
    """Directory enumeration failed or the dir budget was hit: the population is incomplete."""

    def __init__(self, reason: str, kind: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind  # which pruned-dir cap was hit: "name" or "content"


def _is_link(path: str | Path) -> bool:
    """A symlink OR (Windows) NTFS junction; neither is ever followed or descended.

    Decided from ONE `lstat`: `S_ISLNK`, or the reparse tag `IO_REPARSE_TAG_MOUNT_POINT`. This
    works on Python 3.11, which has no `os.path.isjunction` (added in 3.12; relying on it
    would treat a junction as an ordinary directory there). An lstat failure PROPAGATES
    (callers turn it into `unreadable_path`): "cannot tell" is never "not a link"."""
    return _link_from_stat(_lstat(path))


def _link_from_stat(st: os.stat_result) -> bool:
    """Link classification from an already-taken lstat (single stat, no second look)."""
    if stat.S_ISLNK(st.st_mode):
        return True
    mount_point = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
    tag = getattr(st, "st_reparse_tag", 0)
    return bool(tag) and tag == mount_point


class _BudgetExceeded(_PopulationWalkError):
    """A byte budget was crossed WHILE reading (`per_file_byte_limit` / `aggregate_byte_limit`).

    Every byte read is already charged to the `_ByteLedger` (including the one-byte probe past
    the limit), so an overflow is never refunded."""


class _ByteLedger:
    """ONE ledger for every byte a walk consumes: leaf content, marker digests, CACHEDIR.TAG
    heads and link text. Each reader reads at most `min(per_file_limit, remaining) + 1` bytes
    and is charged exactly what it read (probe byte included); crossing a limit raises
    `_BudgetExceeded`. `consumed` is the walk's `scanned_bytes`."""

    def __init__(self, per_file_limit: int, aggregate_limit: int) -> None:
        self.per_file_limit = per_file_limit
        self.remaining = aggregate_limit
        self.consumed = 0
        self._item_start = aggregate_limit
        self._item_total = 0

    def begin_item(self) -> None:
        """Start accounting one file/marker/tag (per-file limit and aggregate start point)."""
        self._item_start = self.remaining
        self._item_total = 0

    @classmethod
    def unlimited(cls) -> _ByteLedger:
        return cls(sys.maxsize, sys.maxsize)

    def _charge(self, n: int) -> None:
        self.consumed += n
        self.remaining -= n

    def _overflow(self, item_total: int, start_remaining: int) -> _BudgetExceeded | None:
        if item_total > self.per_file_limit:
            return _BudgetExceeded("per_file_byte_limit")
        if item_total > start_remaining:
            return _BudgetExceeded("aggregate_byte_limit")
        return None

    def read_budgeted(self, handle: BinaryIO, n: int) -> bytes:
        """ONE raw read of at most `n` bytes, bounded by the budget and CHARGED.

        Returns b"" ONLY when the real `read()` returned b"" (true EOF): the ledger never
        manufactures EOF. The allowance counts each budget exactly once: bytes left under the
        per-file limit (`per_file_limit - item_total`) and under the aggregate (`remaining`,
        already net of every earlier read), plus one probe byte. An allowance <= 0 means a
        budget is already exceeded while the item is unfinished: that is `_BudgetExceeded`,
        never EOF."""
        allowance = min(self.per_file_limit - self._item_total, self.remaining) + 1
        if allowance <= 0:
            raise self._exceeded_reason()
        data = handle.read(min(n, allowance))
        self._item_total += len(data)
        self._charge(len(data))
        exceeded = self._overflow(self._item_total, self._item_start)
        if exceeded is not None:
            raise exceeded
        return data

    def _exceeded_reason(self) -> _BudgetExceeded:
        if self._item_total > self.per_file_limit:
            return _BudgetExceeded("per_file_byte_limit")
        return _BudgetExceeded("aggregate_byte_limit")

    def iter_chunks(self, handle: BinaryIO, hard_cap: int | None = None) -> Iterator[bytes]:
        """Yield the chunks of one item until REAL EOF, charging each. `hard_cap` (markers)
        raises `marker_too_large` rather than recording a truncated digest."""
        self.begin_item()
        yield from self.iter_rest(handle, hard_cap)

    def iter_rest(self, handle: BinaryIO, hard_cap: int | None = None) -> Iterator[bytes]:
        """Continue the CURRENT item (no `begin_item`): the rest of a file whose header was
        already read in the same session."""
        while True:
            if hard_cap is not None and self._item_total > hard_cap:
                raise _PopulationWalkError("marker_too_large")
            n = 65536 if hard_cap is None else min(65536, hard_cap + 1 - self._item_total)
            chunk = _read_exact_or_eof(handle, n, self)
            if not chunk:
                return
            if hard_cap is not None and self._item_total > hard_cap:
                raise _PopulationWalkError("marker_too_large")
            yield chunk

    def charge_link(self, nbytes: int) -> None:
        """Charge a link target measured in BYTES (not characters)."""
        start = self.remaining
        self._charge(nbytes)
        exceeded = self._overflow(nbytes, start)
        if exceeded is not None:
            raise exceeded


def _read_exact_or_eof(handle: BinaryIO, n: int, ledger: _ByteLedger) -> bytes:
    """Read until `n` bytes are collected or a read returns b"" (REAL EOF), charging every read.

    An unbuffered raw `read(n)` may legally return fewer than `n` bytes before EOF, so a short
    read is NEVER treated as end-of-file: a CACHEDIR.TAG holding the signature followed by junk
    must not look like "signature then EOF". The ONE primitive every budgeted read goes through
    (leaf hash, marker digest, tag head)."""
    parts: list[bytes] = []
    got = 0
    while got < n:
        chunk = ledger.read_budgeted(handle, n - got)
        if not chunk:
            break
        parts.append(chunk)
        got += len(chunk)
    return b"".join(parts)


def _open_regular_no_follow(
    path: str | Path, expected_ident: tuple[int, int] | None = None
) -> BinaryIO:
    """THE open for every file read from the walked tree (marker, tag, leaf).

    `O_NOFOLLOW | O_NONBLOCK` (each via getattr) so a FIFO or link swapped in after an earlier
    lstat neither hangs `open()` waiting for a writer nor is followed; the handle is then
    `fstat`ed and must be a REGULAR file, with `expected_ident` (st_dev, st_ino) when given.
    Any failure is `unreadable_path` (never "treat as absent"). Windows has no FIFO at a
    filesystem path (a named pipe lives in a different namespace), so the fstat checks are
    sufficient there."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = _os_open(path, flags)
    except OSError as exc:
        raise _PopulationWalkError("unreadable_path") from exc
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode):
            raise _PopulationWalkError("unreadable_path")
        if (
            expected_ident is not None
            and expected_ident[1]
            and fst.st_ino
            and expected_ident != (fst.st_dev, fst.st_ino)
        ):
            raise _PopulationWalkError("unreadable_path")  # replaced between lstat and open
        # UNBUFFERED: a buffered handle would pull a whole buffer from the OS for a 10-byte
        # budgeted read, so the byte budget would bound nothing. Every read reaches the raw fd.
        return _fdopen(fd, "rb", buffering=0)  # type: ignore[return-value]
    except BaseException:
        os.close(fd)
        raise


def _marker_stat(path: Path) -> os.stat_result | None:
    """lstat of a marker if it is a REGULAR, non-symlink file, else None."""
    try:
        st = _lstat(path)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def _regular_marker(path: Path) -> bool:
    """A marker counts only as a REGULAR, non-symlink file (never follow a marker link)."""
    return _marker_stat(path) is not None


class _TagResult(NamedTuple):
    """Outcome of the ONE read session over a CACHEDIR.TAG."""

    valid: bool
    sha256: str  # hex digest of the whole file, from the bytes actually read in the session
    ident: tuple[int, int]  # (st_dev, st_ino) of the OPENED handle (fstat) that was read


def _cachedir_tag_valid(tag: Path, ledger: _ByteLedger) -> _TagResult:
    """Classify a CACHEDIR.TAG and hash it in ONE open handle and ONE per-file ledger session.

    The FIRST LINE must be exactly the signature, then LF, CRLF or CONFIRMED EOF. CRLF is
    accepted (a tag edited on Windows ends in CRLF; the exact-match tests pin it); a bare CR or
    any other tail is not a signature line. The head is gathered with `_read_exact_or_eof`, so
    `head == sig` means the file REALLY ended after the signature.

    The header bytes are reused in the hash and the rest is read from the same handle, so the
    file is charged once and can never get two per-file allowances. The caller uses the digest
    directly: for a valid tag as the pruned-set marker digest, for an INVALID tag (walked as an
    ordinary leaf) as its final leaf fingerprint, EMITTED into the population at that point.
    There is no cache and no identity/size/mtime comparison to fool: like every other leaf the
    file is hashed exactly once, at one point in the walk. Any OSError (open, read or close) is
    `unreadable_path`."""
    st = _marker_stat(tag)
    if st is None:
        # The caller saw a regular file an instant ago; it was swapped for something else.
        raise _PopulationWalkError("unreadable_path")
    try:
        with _open_regular_no_follow(tag, (st.st_dev, st.st_ino)) as handle:
            opened = os.fstat(handle.fileno())
            size_at_open = opened.st_size
            ledger.begin_item()
            head = _read_exact_or_eof(handle, 128, ledger)
            sig = _CACHEDIR_TAG_SIGNATURE
            valid = head == sig or head.startswith((sig + b"\n", sig + b"\r\n"))
            hasher = hashlib.sha256(head)
            for chunk in ledger.iter_rest(handle, hard_cap=_MARKER_HASH_CAP if valid else None):
                hasher.update(chunk)
            if ledger._item_total != size_at_open:  # independent invariant: no prefix hash
                raise _PopulationWalkError("unreadable_path")
    except OSError as exc:
        raise _PopulationWalkError("unreadable_path") from exc
    return _TagResult(valid, hasher.hexdigest(), (opened.st_dev, opened.st_ino))


def _marker_digest(path: Path, ledger: _ByteLedger) -> str:
    """sha256 of the WHOLE marker so a changed marker is detectable at verify.

    Never records a truncated digest as complete: a marker larger than _MARKER_HASH_CAP raises
    `marker_too_large`, and a read failure raises `unreadable_path` (no sentinel digest that
    would compare equal at mint and verify). Both make the population incomplete."""
    hasher = hashlib.sha256()
    st = _marker_stat(path)
    if st is None:
        raise _PopulationWalkError("unreadable_path")  # swapped for a non-regular object
    try:
        with _open_regular_no_follow(path, (st.st_dev, st.st_ino)) as handle:
            size_at_open = os.fstat(handle.fileno()).st_size
            for chunk in ledger.iter_chunks(handle, hard_cap=_MARKER_HASH_CAP):
                hasher.update(chunk)
            if ledger._item_total != size_at_open:  # independent invariant: no prefix digest
                raise _PopulationWalkError("unreadable_path")
    except OSError as exc:
        raise _PopulationWalkError("unreadable_path") from exc
    return hasher.hexdigest()


class _Classified(NamedTuple):
    """What classifying a directory's markers found (and read)."""

    marker: str | None
    tag_digest: str | None = None  # whole-file sha256 of a VALID CACHEDIR.TAG (same session)
    # final leaf fingerprint of an INVALID CACHEDIR.TAG (same session) and the (st_dev, st_ino)
    # of the OPENED handle it was read from
    tag_leaf: tuple[str, tuple[int, int]] | None = None


def _content_prune_marker(path: Path, ledger: _ByteLedger) -> _Classified:
    """The regular-file marker that makes `path` an unambiguous build/cache tree, if any."""
    for marker in _BUILD_ROOT_MARKERS:
        if _regular_marker(path / marker):
            return _Classified(marker)
    if _regular_marker(path / _CMAKE_CACHE) and not _exists(path / _CMAKE_SOURCE):
        return _Classified(_CMAKE_CACHE)  # out-of-source CMake build tree only
    tag = path / "CACHEDIR.TAG"
    if _regular_marker(tag):
        result = _cachedir_tag_valid(tag, ledger)
        if result.valid:
            return _Classified("CACHEDIR.TAG", tag_digest=result.sha256)
        return _Classified(None, tag_leaf=("file:" + result.sha256, result.ident))
    return _Classified(None)


def _is_pruned_dir(path: Path, ledger: _ByteLedger | None = None) -> bool:
    """Unambiguous dependency/cache/build-root trees only (G-03: build/dist/target may hold source)."""
    ledger = ledger or _ByteLedger.unlimited()
    return (
        path.name in _ALWAYS_PRUNED_DIRS or _content_prune_marker(path, ledger).marker is not None
    )


class _DirHandle:
    """The held/open directory a tuple was listed from. `ident` is its (st_dev, st_ino)."""

    def __init__(self, ident: tuple[int, int], is_dir: bool = True, closer: Any = None) -> None:
        self.ident = ident
        self.is_dir = is_dir
        self._closer = closer

    def close(self) -> None:
        closer, self._closer = self._closer, None
        if closer is not None:
            closer()


# The walks below are ITERATIVE (an explicit stack of frames that own their open directory), never
# recursive: a recursive generator chain raises RecursionError after ~1000 directory tuples, which
# is far below every count and byte limit, so an unchanged deep tree could never verify.
# `os.fwalk` is NOT used: CPython 3.12+ implements it with an explicit stack, but CPython <= 3.11
# (this project supports >= 3.11) implements `_fwalk` as a recursive `yield from _fwalk(...)`
# (checked in the installed 3.11 and 3.12 `Lib/os.py`). The depth of an fd chain is bounded by
# RLIMIT_NOFILE (POSIX) / available handles (Windows): running out is `unreadable_path`
# (incomplete) through the same OSError mapping as any other failure to open a directory.


class _FdFrame:
    """One open directory on the iterative walk's stack."""

    __slots__ = ("fd", "path", "pending")

    def __init__(self, path: str, fd: int) -> None:
        self.path = path
        self.fd = fd
        self.pending: list[str] = []  # children still to descend into (reversed: pop() = next)


def _fd_walk(top: str | Path, onerror: Any) -> Iterator[tuple[str, list[str], list[str], Any]]:
    """POSIX: list through an open dirfd; every directory is opened `O_NOFOLLOW | O_DIRECTORY`
    RELATIVE to its parent's dirfd; registered so leaf ops are `dir_fd`-relative."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    fds = _ctx_dirfds()
    stack: list[_FdFrame] = []

    def _listing(fd: int) -> tuple[list[str], list[str]] | None:
        try:
            with os.scandir(fd) as it:
                entries = list(it)
        except OSError as exc:
            onerror(exc)
            return None
        dirs: list[str] = []
        files: list[str] = []
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False  # listed as a file; the leaf check refuses a directory there
            (dirs if is_dir else files).append(entry.name)
        return dirs, files

    def _own(path: str, fd: int) -> _FdFrame:
        """Transfer ownership of a freshly opened `fd` to the stack IMMEDIATELY (no fallible call
        between `os.open` returning and this): every later failure, however it unwinds, is closed
        exactly once by the walk's `finally` (or by `_enter` when it pops the frame itself)."""
        frame = _FdFrame(path, fd)
        stack.append(frame)
        return frame

    def _enter(frame: _FdFrame) -> Iterator[tuple[str, list[str], list[str], Any]]:
        listing = _listing(frame.fd)  # may raise (onerror): the stack already owns frame.fd
        if listing is None:
            stack.pop()  # `frame` is the top: closed here, exactly once, and no longer owned
            os.close(frame.fd)
            return
        dirs, files = listing
        st = os.fstat(frame.fd)
        key = str(Path(frame.path))
        fds[key] = frame.fd
        try:
            yield (
                frame.path,
                dirs,
                files,
                _DirHandle((st.st_dev, st.st_ino), stat.S_ISDIR(st.st_mode)),
            )
        finally:
            fds.pop(key, None)
        frame.pending = list(reversed(dirs))  # the consumer prunes `dirs` in place

    top_s = os.fspath(top)
    try:
        try:
            root_fd = _os_open_dir(top_s, flags)
        except OSError as exc:
            onerror(exc)
            return
        yield from _enter(_own(top_s, root_fd))
        while stack:
            frame = stack[-1]
            if not frame.pending:
                stack.pop()
                os.close(frame.fd)
                continue
            name = frame.pending.pop()
            child = os.path.join(frame.path, name)
            try:
                child_fd = os.open(name, flags, dir_fd=frame.fd)
            except OSError as exc:
                raise _PopulationWalkError("unreadable_path") from exc
            yield from _enter(_own(child, child_fd))
    finally:
        while stack:
            os.close(stack.pop().fd)


def _os_open_dir(path: str, flags: int) -> int:
    return os.open(path, flags)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = (("lo", wintypes.DWORD), ("hi", wintypes.DWORD))

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = (
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", _FILETIME),
            ("ftLastAccessTime", _FILETIME),
            ("ftLastWriteTime", _FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        )

    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = (
            ("VolumeSerialNumber", ctypes.c_ulonglong),
            ("FileId", ctypes.c_ubyte * 16),
        )

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _k32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _k32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    )
    _k32.GetFileInformationByHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _k32.CloseHandle.restype = wintypes.BOOL
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    def _hold_dir(path: str | Path) -> _DirHandle:
        """Hold `path` open WITHOUT FILE_SHARE_DELETE (it cannot be renamed, deleted or replaced
        while held); it must be a plain directory, not a reparse point (symlink / junction)."""
        # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES; share READ | WRITE only;
        # FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT (open the link itself)
        handle = _k32.CreateFileW(
            os.path.abspath(path),
            0x0001 | 0x0080,
            0x1 | 0x2,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if handle is None or handle == _INVALID_HANDLE:
            raise OSError(ctypes.get_last_error(), "cannot hold directory", str(path))
        # From here until the `_DirHandle` (whose closer owns the handle) is returned, ANY failure
        # (a ctypes call raising, a failed query, a reparse point) closes the handle exactly once.
        try:
            info = _BY_HANDLE_FILE_INFORMATION()
            if not _k32.GetFileInformationByHandle(handle, ctypes.byref(info)):
                raise OSError(ctypes.get_last_error(), "cannot query held directory", str(path))
            if (
                info.dwFileAttributes & 0x400 or not info.dwFileAttributes & 0x10
            ):  # reparse / not dir
                raise _PopulationWalkError("unreadable_path")
            # Same identity os.lstat reports: st_dev = 64-bit volume serial (FILE_ID_INFO), st_ino
            # = file index. If FILE_ID_INFO is unavailable, dev is 0 and only the index is compared.
            index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
            id_info = _FILE_ID_INFO()
            got_id = _k32.GetFileInformationByHandleEx(  # FileIdInfo == 18
                handle, 18, ctypes.byref(id_info), ctypes.sizeof(id_info)
            )
            ident = (int(id_info.VolumeSerialNumber) if got_id else 0, index)
        except BaseException:
            _k32.CloseHandle(handle)
            raise
        return _DirHandle(ident, True, lambda: _k32.CloseHandle(handle))

else:

    def _hold_dir(path: str | Path) -> _DirHandle:  # pragma: no cover - POSIX uses fwalk
        raise OSError("held directory handles are Windows-only")


class _HeldFrame:
    """One held directory on the iterative Windows walk's stack."""

    __slots__ = ("held", "path", "pending")

    def __init__(self, path: str, held: _DirHandle) -> None:
        self.path = path
        self.held = held
        self.pending: list[str] = []


def _held_walk(top: str | Path, onerror: Any) -> Iterator[tuple[str, list[str], list[str], Any]]:
    """Windows: a top-down, ITERATIVE walk holding every directory of the current chain open.

    Each stack frame owns its held handle and its pending children; a child is held (and checked
    not to be a reparse point) before it is listed, pushed, and popped + closed when its subtree
    is done. Everything still on the stack is closed in `finally` (early break, exception,
    `close()`)."""
    stack: list[_HeldFrame] = []

    def _listing(path: str) -> tuple[list[str], list[str]] | None:
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError as exc:
            onerror(exc)
            return None
        dirs: list[str] = []
        files: list[str] = []
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False  # listed as a file; the leaf check refuses a directory there
            (dirs if is_dir else files).append(entry.name)
        return dirs, files

    def _own(path: str, held: _DirHandle) -> _HeldFrame:
        """Transfer ownership of a freshly held handle to the stack IMMEDIATELY: every later
        failure unwinds through the walk's `finally` (or `_enter` popping the frame itself)."""
        frame = _HeldFrame(path, held)
        stack.append(frame)
        return frame

    def _enter(frame: _HeldFrame) -> Iterator[tuple[str, list[str], list[str], Any]]:
        listing = _listing(frame.path)  # may raise (onerror): the stack already owns the handle
        if listing is None:
            stack.pop().held.close()  # popped and closed here, exactly once
            return
        dirs, files = listing
        yield frame.path, dirs, files, frame.held
        frame.pending = list(reversed(dirs))  # the consumer prunes `dirs` in place

    top_s = os.fspath(top)
    try:
        try:
            root_held = _hold_dir(top_s)
        except OSError as exc:
            onerror(exc)
            return
        yield from _enter(_own(top_s, root_held))
        while stack:
            frame = stack[-1]
            if not frame.pending:
                stack.pop().held.close()
                continue
            child = os.path.join(frame.path, frame.pending.pop())
            try:
                child_held = _hold_dir(child)
            except OSError as exc:
                raise _PopulationWalkError("unreadable_path") from exc
            yield from _enter(_own(child, child_held))
    finally:
        while stack:
            stack.pop().held.close()


def _default_walk(top: str | Path, onerror: Any) -> Iterator[tuple[str, list[str], list[str], Any]]:
    if sys.platform == "win32":
        yield from _held_walk(top, onerror)
    else:
        yield from _fd_walk(top, onerror)


_walk_impl = _default_walk  # private seam: tests drive/wrap the walk through this


def _exists(path: str | Path) -> bool:
    """`Path.exists()` through the dir-fd aware `_lstat` seam ("cannot tell" counts as existing)."""
    try:
        _lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _same_identity(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """(st_dev, st_ino) equality; a zero inode or zero dev means "unknown" and is not compared."""
    if not (a[1] and b[1]):
        return True
    if a[1] != b[1]:
        return False
    return not (a[0] and b[0] and a[0] != b[0])


@contextlib.contextmanager
def _authenticated_child(child: Path, child_st: os.stat_result) -> Iterator[None]:
    """OPEN and AUTHENTICATE `child` BEFORE any of its markers is inspected.

    The marker stat/open/read for `child/<marker>` would otherwise resolve `child` by pathname,
    and `child` can be swapped for a link to an outside directory right after its classification
    `lstat`. The directory is opened without following links, compared with `child_st`, and held
    for the whole classification AND digesting; every marker operation is bound to it:
      POSIX   - `O_NOFOLLOW | O_DIRECTORY` dirfd, registered so `_lstat` / `_os_open` use `dir_fd=`
      Windows - the same no-share-delete held handle the walk chain uses (path pinned while held)
    Any failure is `unreadable_path`. Released on exit; if `child` is kept, the walker re-opens it
    for the descent and re-checks the recorded identity there."""
    ident = (child_st.st_dev, child_st.st_ino)
    if sys.platform == "win32":
        try:
            held = _hold_dir(child)
        except OSError as exc:
            raise _PopulationWalkError("unreadable_path") from exc
        try:
            if not _same_identity(ident, held.ident):
                raise _PopulationWalkError("unreadable_path")
            yield
        finally:
            held.close()
        return
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = _os_open(child, flags)
    except OSError as exc:
        raise _PopulationWalkError("unreadable_path") from exc
    try:
        fds = _ctx_dirfds()
        key = str(child)
        fst = os.fstat(fd)
        if not stat.S_ISDIR(fst.st_mode) or not _same_identity(ident, (fst.st_dev, fst.st_ino)):
            raise _PopulationWalkError("unreadable_path")
        fds[key] = fd
        try:
            yield
        finally:
            fds.pop(key, None)
    finally:
        os.close(fd)


def _population_paths(
    root: Path,
    pruned: list[str],
    content_pruned: dict[str, str] | None = None,
    ledger: _ByteLedger | None = None,
    root_ident: tuple[int, int] | None = None,
    root_identity_out: list[int] | None = None,
) -> Generator[tuple[str, str | None], None, None]:
    """Lazy, sorted-per-directory walk; `pruned` is filled (root-relative, capped) as it proceeds.

    `content_pruned` (root-relative dir -> "<marker>:<sha256 of marker>", or "name" for a
    directory pruned only by NAME) records EVERY pruned directory so verify can detect a
    directory that was pruned/created or a marker planted/changed/removed after minting.
    Directory symlinks are yielded as LEAVES (fingerprinted as `symlink:<target>`), checked
    BEFORE any marker classification, and never descended.

    Raises _PopulationWalkError on any directory-enumeration error (never silently skip a
    subtree) or when more than _MAX_WALK_DIRS directories are visited."""

    def _on_error(exc: OSError) -> None:
        raise _PopulationWalkError("unreadable_path") from exc

    ledger = ledger or _ByteLedger.unlimited()
    visited = 0
    n_name = n_content = 0
    # Every directory we KEEP (descend into) with its (st_dev, st_ino) at classification.
    # `os.walk` re-checks `islink` itself and SILENTLY skips a directory swapped for a link
    # after our check; so each kept directory must be yielded back, and unchanged.
    expected: dict[str, tuple[int, int]] = {}
    # directory -> (fingerprint, (st_dev, st_ino)) of the invalid CACHEDIR.TAG that was read
    # (once) in that directory's marker-classification session. Keyed by DIRECTORY and matched
    # at the leaf stage by FILE IDENTITY, never by name: on a case-insensitive filesystem the
    # tag is opened as `CACHEDIR.TAG` but enumerated as `cachedir.tag`.
    pending: dict[str, tuple[str, tuple[int, int]]] = {}
    first = True
    for dirpath, dirnames, filenames, handle in _walk_impl(root, _on_error):
        visited += 1
        if visited > _MAX_WALK_DIRS:
            raise _PopulationWalkError("dir_count_limit")
        current = Path(dirpath)
        if first:
            first = False
            # The ROOT tuple's directory handle (open dirfd / held handle) is the directory that
            # actually SUPPLIED this listing. It must be the directory the root pathname
            # lstat authenticated, and the identity recorded for the ticket is the HANDLE's.
            h_ident = getattr(handle, "ident", None) if handle is not None else None
            if h_ident is not None:
                if not handle.is_dir or (
                    root_ident is not None and not _same_identity(root_ident, tuple(h_ident))
                ):
                    raise _PopulationWalkError("unreadable_path")  # root swapped before opening
                if root_identity_out is not None:
                    root_identity_out[:] = [int(h_ident[0]), int(h_ident[1])]
        else:
            ident = expected.pop(str(current), None)
            if ident is None:
                raise _PopulationWalkError("unreadable_path")  # a directory we never kept
            if handle is not None and getattr(handle, "ident", None) is not None:
                # The identity of the directory the LISTING came from (open dirfd / held
                # handle), not of whatever a pathname resolves to now.
                if not handle.is_dir or (
                    ident[1]
                    and handle.ident[1]
                    and (
                        ident[1] != handle.ident[1]
                        or (ident[0] and handle.ident[0] and ident[0] != handle.ident[0])
                    )
                ):
                    raise _PopulationWalkError("unreadable_path")  # swapped after classification
            else:  # a custom walker without a handle: fall back to a pathname check
                try:
                    now = _lstat(current)
                except OSError as exc:
                    raise _PopulationWalkError("unreadable_path") from exc
                if (
                    _link_from_stat(now)
                    or not stat.S_ISDIR(now.st_mode)
                    or (ident[1] and now.st_ino and ident != (now.st_dev, now.st_ino))
                ):
                    raise _PopulationWalkError("unreadable_path")  # swapped after classification
        rel_dir = current.relative_to(root)
        keep: list[str] = []
        leaves: list[str] = list(filenames)
        for d in sorted(dirnames):
            child = current / d
            try:
                child_st = _lstat(child)
            except OSError as exc:
                raise _PopulationWalkError("unreadable_path") from exc
            if _link_from_stat(child_st):
                leaves.append(
                    d
                )  # a directory symlink/junction is a leaf: never descended, never skipped
                continue
            if not stat.S_ISDIR(child_st.st_mode):
                # Listed as a directory but not one now (swapped for a file): refuse BEFORE any
                # directory classification (name pruning would record it as "name" and it
                # would never be fingerprinted). The reverse (listed as a file, a directory by
                # the leaf lstat) is refused by the leaf stage below: both are incomplete.
                raise _PopulationWalkError("unreadable_path")
            rel = (rel_dir / d).as_posix()
            if d in _ALWAYS_PRUNED_DIRS:
                # Name-pruned straight after link classification: record "name" and NEVER open,
                # hold, read or charge anything inside it (a marker there must not turn the
                # name-prune into a content-prune, nor consume budget for an unchanged tree).
                if len(pruned) < _MAX_REPORTED_PRUNED:
                    pruned.append(rel)
                if content_pruned is not None:
                    if n_name >= _MAX_NAME_PRUNED_DIRS:
                        raise _PopulationWalkError("pruned_dir_limit", "name")
                    n_name += 1
                    content_pruned[rel] = _NAME_PRUNED
                continue
            digest: str | None = None
            with _authenticated_child(child, child_st):
                classified = _content_prune_marker(child, ledger)
                marker = classified.marker
                if classified.tag_leaf is not None:
                    # an invalid tag was read, charged and hashed in its classification
                    # session: that IS its leaf fingerprint, emitted when the leaf is reached
                    pending[str(child)] = classified.tag_leaf
                if marker is not None and content_pruned is not None:
                    if n_content >= _MAX_CONTENT_PRUNED_DIRS:
                        raise _PopulationWalkError("pruned_dir_limit", "content")
                    if marker == "CACHEDIR.TAG":
                        digest = classified.tag_digest
                    else:
                        digest = _marker_digest(child / marker, ledger)
            if marker is not None:
                if len(pruned) < _MAX_REPORTED_PRUNED:
                    pruned.append(rel)
                if content_pruned is not None:
                    n_content += 1
                    content_pruned[rel] = f"{marker}:{digest}"
            else:
                keep.append(d)
                expected[str(child)] = (child_st.st_dev, child_st.st_ino)
        dirnames[:] = keep
        for name in sorted(leaves):
            # os.walk swallows a DirEntry.is_dir() failure (no onerror) and lists the directory
            # among `filenames`; fingerprinting it as a leaf would omit its whole subtree. A
            # directory is a leaf only when it is a link/junction. EVERY leaf gets this lstat and
            # type check, including one whose fingerprint was already computed.
            try:
                leaf_st = _lstat(current / name)
            except OSError as exc:
                raise _PopulationWalkError("unreadable_path") from exc
            if stat.S_ISDIR(leaf_st.st_mode) and not _link_from_stat(leaf_st):
                raise _PopulationWalkError("unreadable_path")
            tag = pending.get(str(current))
            if (
                tag is not None
                and tag[1][1]
                and tag[1] == (leaf_st.st_dev, leaf_st.st_ino)
                and stat.S_ISREG(leaf_st.st_mode)
                and not _link_from_stat(leaf_st)
            ):
                # This leaf IS the object the invalid CACHEDIR.TAG classification session read
                # (same file identity, found by identity so the filename's case is irrelevant;
                # the fingerprint is recorded under the ENUMERATED on-disk name). Its bytes are
                # already hashed and charged: no re-read, no re-charge. Identity authorizes
                # using bytes already read in THIS walk from THIS object; size and mtime are
                # never compared, so this is not a metadata-keyed cache.
                del pending[str(current)]
                yield (rel_dir / name).as_posix(), tag[0]
                continue
            yield (rel_dir / name).as_posix(), None
        if str(current) in pending:
            # The tag object that was read and hashed is no longer among this directory's leaves
            # (replaced by a different inode, a link, or gone): never trust its old bytes.
            raise _PopulationWalkError("unreadable_path")
    if first:
        # The walk never yielded the ROOT tuple (os.fwalk(follow_symlinks=False) silently yields
        # nothing for a symlink root; the held chain on Windows must have held the root): an
        # empty walk is NOT an empty population.
        raise _PopulationWalkError("unreadable_path")
    if expected:  # a kept directory was never visited: it vanished or became a link
        raise _PopulationWalkError("unreadable_path")


_FINGERPRINT_TAGS = ("file:", "symlink:", "other:")


def compute_file_fingerprint(path: str | Path) -> str:
    """Type-tagged fingerprint so the domains of different object types cannot overlap.

    `file:<sha256 of content>`, `symlink:<sha256 of link text>` (symlinks and junctions, never
    followed; G-08), `other:<S_IFMT octal>` for any other object (fifo, socket, device).
    "" means the path no longer exists. Untagged hex is the OLD format, which collided: a
    regular file holding b"symlink:victim.py" hashed like a link to victim.py."""
    p = Path(path)
    try:
        st = _lstat(p)  # ONE lstat classifies link / regular / other
    except FileNotFoundError:
        return ""
    if _link_from_stat(st):
        # Never follow a leaf link: its target may be out-of-root or huge (G-08).
        return "symlink:" + hashlib.sha256(os.fsencode(os.readlink(p))).hexdigest()
    mode = st.st_mode
    if not stat.S_ISREG(mode):
        return f"other:{stat.S_IFMT(mode):o}"
    hasher = hashlib.sha256()
    try:
        handle = _open_regular_no_follow(p, (st.st_dev, st.st_ino))
    except _PopulationWalkError as exc:
        raise OSError(f"cannot safely open {p}") from exc
    with handle as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return "file:" + hasher.hexdigest()


def _fingerprint_enumerated(path: Path, ledger: _ByteLedger) -> str:
    """Fingerprint a leaf the walker ENUMERATED; every doubt is `unreadable_path`.

    Every byte read (content, or link text measured in BYTES) is charged to `ledger`.

    Unlike the standalone `compute_file_fingerprint` (which keeps its "" contract for direct
    callers), a path that was listed moments ago must not read as absent or as an empty
    directory: a vanished path or a non-link directory raises, so the population is incomplete.
    A regular file is opened ONCE and hashed through that handle after `fstat` confirms it is
    a regular file with the identity `lstat` saw, so the object classified is the object
    hashed (no check-then-use window). The hash loop enforces BOTH byte limits while reading
    (at most limit+1 bytes), so a file that grows after the size check cannot bypass a budget.
    Links: one lstat plus readlink.

    POSIX: `O_NONBLOCK` makes `open()` of a FIFO swapped in after the lstat return at once
    instead of waiting for a writer; `fstat` then rejects it. Windows has no FIFO at a
    filesystem path (a named pipe lives in a different namespace), so the lstat/fstat checks
    are sufficient there."""
    try:
        st = _lstat(path)
    except FileNotFoundError as exc:
        raise _PopulationWalkError("unreadable_path") from exc
    if _link_from_stat(st):
        # Link targets are read in FULL, ONCE (os.readlink cannot be bounded), bounded by the
        # platform path limit; the value is charged here and reused for hashing.
        target = os.fsencode(_readlink(path))
        ledger.charge_link(len(target))
        return "symlink:" + hashlib.sha256(target).hexdigest()
    if stat.S_ISDIR(st.st_mode):
        raise _PopulationWalkError("unreadable_path")
    if not stat.S_ISREG(st.st_mode):
        return f"other:{stat.S_IFMT(st.st_mode):o}"  # never opened: a fifo would block
    with _open_regular_no_follow(path, (st.st_dev, st.st_ino)) as handle:
        size_at_open = os.fstat(handle.fileno()).st_size
        hasher = hashlib.sha256()
        for chunk in ledger.iter_chunks(handle):
            hasher.update(chunk)
        # Independent invariant (a second check that does not share the ledger's arithmetic): the
        # bytes the fingerprint covers must be the file's size at open. A prefix hash can never be
        # recorded as a complete fingerprint, whatever the cause of a premature EOF.
        if ledger._item_total != size_at_open:
            raise _PopulationWalkError("unreadable_path")
    return "file:" + hasher.hexdigest()


def _walk_tracked_files_bounded(
    repo_root: str | Path,
    *,
    max_files: int = _DEFAULT_MAX_FILES,
    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
    max_aggregate_bytes: int = _DEFAULT_MAX_AGGREGATE_BYTES,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Per-file fingerprints for every file under repo_root, keyed by POSIX-normalized relative
    path, plus an explicit population-result dict (AGT-04: a budget hit must report
    "incomplete", never silently truncate and claim a complete population).

    Lazy generator (`_population_paths`) consumed by the file/byte budgets; see the module
    docstring for the prune set and threat model.
    """
    # A legitimate root alias (a checkout behind a symlink or junction, e.g. macOS /tmp and
    # /var) is resolved ONCE, explicitly; the RESOLVED root is authenticated by lstat and its
    # identity recorded. Every descendant stays no-follow.
    root = Path(os.path.realpath(repo_root))
    root_identity: list[int] | None = None
    result: dict[str, str] = {}
    scanned_files = 0
    ledger = _ByteLedger(max_file_bytes, max_aggregate_bytes)
    incomplete_reason: str | None = None
    limit_kind: str | None = None
    pruned: list[str] = []
    content_pruned: dict[str, str] = {}

    paths: Generator[tuple[str, str | None], None, None] | None = None
    handle_root_identity: list[int] = []
    try:
        try:
            root_st = _lstat(root)
        except OSError as exc:
            raise _PopulationWalkError("unreadable_path") from exc
        if not stat.S_ISDIR(root_st.st_mode) or _link_from_stat(root_st):
            raise _PopulationWalkError("unreadable_path")
        root_identity = [root_st.st_dev, root_st.st_ino]  # fallback for handle-less walkers
        paths = _population_paths(
            root,
            pruned,
            content_pruned,
            ledger,
            root_ident=(root_st.st_dev, root_st.st_ino),
            root_identity_out=handle_root_identity,
        )
        for rel, emitted_fp in paths:
            item = root / rel

            if scanned_files >= max_files:
                incomplete_reason = "file_count_limit"
                break

            if emitted_fp is not None:
                # hashed exactly once, at classification; nothing left to stat, size or read
                result[rel] = emitted_fp
                scanned_files += 1
                continue

            try:
                size_st = _lstat(item)  # ONE lstat sizes the leaf (links: link-text length)
                # a link's size is its target text, which is read (and charged) exactly once
                # by `_fingerprint_enumerated`; the size check must not read it a second time
                size = 0 if _link_from_stat(size_st) else size_st.st_size
            except OSError:
                # A file that vanishes or becomes unreadable mid-walk must not silently
                # disappear from `result` while the population still reports "complete" --
                # that is the exact false-PASS this function exists to prevent.
                incomplete_reason = incomplete_reason or "unreadable_path"
                continue

            if size > ledger.per_file_limit:
                incomplete_reason = "per_file_byte_limit"
                scanned_files += 1
                continue

            if size > ledger.remaining:
                incomplete_reason = "aggregate_byte_limit"
                break

            try:
                fingerprint = _fingerprint_enumerated(item, ledger)
            except _BudgetExceeded as exc:
                # every byte read is already on the ledger: nothing is refunded
                incomplete_reason = exc.reason
                if exc.reason == "per_file_byte_limit":
                    # the item alone is too big (the more specific reason); if its read also
                    # exhausted the aggregate allowance, the NEXT item trips the aggregate
                    # limit at its own size check / read (cap 0), so the walk still stops
                    scanned_files += 1
                    continue
                break
            except OSError:
                incomplete_reason = incomplete_reason or "unreadable_path"
                continue
            result[rel] = fingerprint
            scanned_files += 1
    except _PopulationWalkError as exc:
        if incomplete_reason is None:
            incomplete_reason = exc.reason
            limit_kind = exc.kind
    finally:
        if paths is not None:
            paths.close()  # release held directory handles / dirfds even on an early break
    if handle_root_identity:
        root_identity = handle_root_identity  # recorded from the walked HANDLE, not a pathname

    if incomplete_reason is not None:
        population = {
            "verified": False,
            "status": "incomplete",
            "reason": incomplete_reason,
            "limit_kind": limit_kind,
            "population_policy": "agt04-v2",
            "population_source": "filesystem-walk",
            "root_identity": root_identity,
            "pruned_dirs": sorted(pruned)[:_MAX_REPORTED_PRUNED],
            "pruned_set": dict(sorted(content_pruned.items())),
            "scanned_files": scanned_files,
            "scanned_bytes": ledger.consumed,
        }
    else:
        population = {
            "verified": True,
            "status": "complete",
            "reason": None,
            "population_policy": "agt04-v2",
            "population_source": "filesystem-walk",
            "root_identity": root_identity,
            "pruned_dirs": sorted(pruned)[:_MAX_REPORTED_PRUNED],
            "pruned_set": dict(sorted(content_pruned.items())),
            "scanned_files": scanned_files,
            "scanned_bytes": ledger.consumed,
        }
    return result, population


def compute_working_tree_fingerprint(repo_root: str | Path) -> str:
    files, _population = _walk_tracked_files_bounded(repo_root)
    entries = [f"{rel}:{fp}" for rel, fp in sorted(files.items())]
    content = "\n".join(entries).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def build_edit_ready_ticket(
    *,
    repo_root: str,
    target_path: str,
    query: str,
    allowed_files: list[str],
    max_files: int = _DEFAULT_MAX_FILES,
    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
    max_aggregate_bytes: int = _DEFAULT_MAX_AGGREGATE_BYTES,
) -> EditReadyTicketV1:
    # Whole-tree, not just allowed_files: verify_edit_ticket needs a pre-edit fingerprint for
    # every file to name which one drifted outside the declared scope, not just detect that
    # SOME file did via the aggregate working_tree_fingerprint.
    pre_fps, population = _walk_tracked_files_bounded(
        repo_root,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_aggregate_bytes=max_aggregate_bytes,
    )
    tree_fp_content = "\n".join(f"{rel}:{fp}" for rel, fp in sorted(pre_fps.items())).encode(
        "utf-8"
    )
    tree_fp = hashlib.sha256(tree_fp_content).hexdigest()
    ticket_id = f"ticket_{uuid.uuid4().hex[:12]}"

    return EditReadyTicketV1(
        ticket_id=ticket_id,
        version=1,
        created_at=time.time(),
        repo_root=str(repo_root),
        target_path=str(target_path),
        query=query,
        allowed_files=list(allowed_files),
        working_tree_fingerprint=tree_fp,
        pre_edit_fingerprints=pre_fps,
        population_status=population,
    )


def _normalized_root(root: str | Path) -> str:
    """Compare repo roots by RESOLVED identity, not by path spelling.

    A path string is not an opened object, but two spellings of the same directory
    (trailing slash, mixed separators, a relative form) must not read as different trees.
    Falls back to the lexical form when the path cannot be resolved, so a missing directory
    still compares deterministically instead of raising inside a verdict path.

    Case folding is delegated to ``os.path.normcase``, which lowercases on Windows and is
    the IDENTITY on POSIX. An unconditional ``.lower()`` (shipped in v1.119.8) made
    ``/tmp/Repo`` and ``/tmp/repo`` -- two genuinely different trees on a case-sensitive
    filesystem -- compare equal, so a cross-tree verify could slip past
    ``repo_root_mismatch`` entirely when the contents happened to match. Case-insensitivity
    is a property of the FILESYSTEM, never of the string.
    """

    def _fold(value: str) -> str:
        return os.path.normcase(value).replace("\\", "/").rstrip("/")

    try:
        return _fold(str(Path(root).resolve()))
    except OSError:
        return _fold(str(root))


def _content_pruned_violations(minted: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Directories whose content-pruning differs between mint and verify.

    A marker planted into an existing directory hides every file in it from the verify-time
    walk (the declared file then reads as deleted and undeclared siblings are never seen), so
    any difference in the pruned set or in a marker's bytes is a violation. Tickets minted
    before this field existed carry no record and are skipped (documented legacy path)."""
    before = minted.get("pruned_set")
    if not isinstance(before, dict):
        return []
    after = current.get("pruned_set") or {}
    out: list[str] = []
    for rel in sorted(set(before) | set(after)):
        if rel not in before:
            out.append(f"newly_pruned:{rel}")
        elif before[rel] == _NAME_PRUNED:
            continue  # existed at mint, pruned by name: its contents stay out of scope
        elif rel not in after:
            out.append(f"no_longer_pruned:{rel}")
        elif before[rel] != after[rel]:
            out.append(f"marker_changed:{rel}")
    return out


def verify_edit_ticket(
    *,
    repo_root: str,
    ticket: EditReadyTicketV1,
    modified_files: list[str],
) -> dict[str, Any]:
    # AGT-04 fail-closed gate: a ticket built from an incomplete population may be missing
    # fingerprints for files a budget cut off, so drift there is undetectable -- never let an
    # incomplete population reach PASS. A ticket with no population record at all ("unknown")
    # is REFUSED below as ticket_format_outdated, not verified through a compatibility path.
    # Old-format (untagged) fingerprints are REFUSED, never re-tagged on read: old tickets could
    # hold links hashed by the colliding scheme, so tagging them `file:` would keep the hole
    # open for exactly the tickets that predate the fix. Re-mint.
    # The same refusal covers a ticket with no `pruned_set` or policy fields: without the
    # mint-time record of pruned directories a marker planted afterwards (modify the declared
    # file, add an undeclared sibling, plant src/pyvenv.cfg) cannot be detected.
    pop = ticket.population_status
    if (
        not isinstance(pop.get("pruned_set"), dict)
        or pop.get("population_policy") != "agt04-v2"
        or not pop.get("population_source")
        or not pop.get("root_identity")
    ) or any(not fp.startswith(_FINGERPRINT_TAGS) for fp in ticket.pre_edit_fingerprints.values()):
        return {
            "verdict": "FAIL",
            "reason": "ticket_format_outdated",
            "violations": ["ticket_format_outdated"],
            "ticket_id": ticket.ticket_id,
        }

    if ticket.population_status.get("status") == "incomplete":
        return {
            "verdict": "FAIL",
            "reason": "population_incomplete",
            "violations": [],
            "ticket_id": ticket.ticket_id,
        }

    # The ticket records the repo_root it was BUILT from, but this function took the root as an
    # independent argument and never compared the two -- so a ticket minted against tree A could
    # be verified against tree B, and every fingerprint comparison below would silently be
    # cross-tree. A verdict is only meaningful about the tree the ticket describes.
    if _normalized_root(repo_root) != _normalized_root(ticket.repo_root):
        return {
            "verdict": "FAIL",
            "reason": "repo_root_mismatch",
            "violations": [],
            "ticket_id": ticket.ticket_id,
        }

    norm_declared = {m.replace("\\", "/") for m in modified_files}
    norm_allowed = {f.replace("\\", "/") for f in ticket.allowed_files}

    # Scope check: every file the caller CLAIMS to have modified must be in the ticket's
    # allowed scope. (Preserved from the original implementation.)
    out_of_allowlist = sorted(norm_declared - norm_allowed)
    if out_of_allowlist:
        return {
            "verdict": "FAIL",
            "reason": "edit_contract_violated",
            "violations": out_of_allowlist,
            "ticket_id": ticket.ticket_id,
        }

    # Real fingerprint re-check: recompute the CURRENT tree state and compare against the
    # ticket's pre-edit snapshot. Without this, verify_edit_ticket only trusts the caller's
    # self-reported modified_files list -- an agent could silently touch a file outside its
    # ticket's scope and simply omit it, and this function would never know. Re-hashing the
    # tree closes that gap; the fail-closed contract is "prove the tree matches the declared
    # change set," not "trust the declared change set."
    current_fps, current_population = _walk_tracked_files_bounded(repo_root)
    # The ticket-side population gate above cannot speak for THIS walk. If the verify-time walk
    # was itself cut off by a budget, files it never reached have no current fingerprint, so
    # drift in them is undetectable -- and the loop below would read a missing entry as "" and
    # only flag it when the ticket happened to carry a fingerprint for it. Discarding this
    # result (it was `_current_population`) let an incomplete verify reach PASS.
    if current_population.get("status") == "incomplete":
        return {
            "verdict": "FAIL",
            "reason": "verify_population_incomplete",
            "violations": [],
            "ticket_id": ticket.ticket_id,
        }
    if list(ticket.population_status.get("root_identity") or []) != list(
        current_population.get("root_identity") or []
    ):
        # the (resolved) repo root now is a different directory than the one minted from
        return {
            "verdict": "FAIL",
            "reason": "verify_population_incomplete",
            "violations": ["root_identity_changed"],
            "ticket_id": ticket.ticket_id,
        }
    all_paths = set(ticket.pre_edit_fingerprints) | set(current_fps)
    pruned_violations = _content_pruned_violations(ticket.population_status, current_population)

    undeclared_drift: list[str] = []
    for path in sorted(all_paths):
        pre_fp = ticket.pre_edit_fingerprints.get(path, "")
        cur_fp = current_fps.get(path, "")
        if pre_fp != cur_fp and path not in norm_declared:
            undeclared_drift.append(path)

    if undeclared_drift or pruned_violations:
        return {
            "verdict": "FAIL",
            "reason": "edit_contract_violated",
            "violations": pruned_violations + undeclared_drift,
            "ticket_id": ticket.ticket_id,
        }

    # Hallucination check: a file the caller CLAIMS to have modified must actually differ from
    # its pre-edit fingerprint. A declared-but-unchanged file means the agent reported an edit
    # that never happened.
    not_actually_modified = sorted(
        path
        for path in norm_declared
        if ticket.pre_edit_fingerprints.get(path, "") == current_fps.get(path, "")
    )
    if not_actually_modified:
        return {
            "verdict": "FAIL",
            "reason": "declared_edit_not_applied",
            "violations": not_actually_modified,
            "ticket_id": ticket.ticket_id,
        }

    return {
        "verdict": "PASS",
        "reason": None,
        "violations": [],
        "ticket_id": ticket.ticket_id,
    }
