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

import hashlib
import os
import stat
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

_os_walk = os.walk  # private seam: tests patch this, never the stdlib attribute
_os_open = os.open  # private seam: the ONE safe open of every file read from the walked tree
_fdopen = os.fdopen  # private seam: wraps the opened fd (unbuffered)
_readlink = os.readlink  # private seam: link targets are read ONCE per link
_lstat = os.lstat  # private seam for link classification

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

    def _cap(self) -> int:
        return max(0, min(self.per_file_limit, self.remaining))

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

        Returns b"" only at real EOF. A raw (unbuffered) read may legally return fewer than
        requested bytes before EOF, so callers needing a full block use `_read_exact_or_eof`.
        Reads at most `min(per_file_limit, remaining) + 1` bytes of the item in total."""
        want = min(n, self._cap() + 1 - self._item_total)
        if want <= 0:
            return b""
        data = handle.read(want)
        self._item_total += len(data)
        self._charge(len(data))
        exceeded = self._overflow(self._item_total, self._item_start)
        if exceeded is not None:
            raise exceeded
        return data

    def iter_chunks(self, handle: BinaryIO, hard_cap: int | None = None) -> Iterator[bytes]:
        """Yield the chunks of one item until REAL EOF, charging each. `hard_cap` (markers)
        raises `marker_too_large` rather than recording a truncated digest."""
        self.begin_item()
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


def _cachedir_tag_valid(tag: Path, ledger: _ByteLedger) -> bool:
    """The FIRST LINE must be exactly the signature, then LF, CRLF or CONFIRMED EOF.

    CRLF is accepted (the spec's signature line ends at the newline and a tag edited on Windows
    ends in CRLF; the existing exact-match tests pin it); a bare CR or any other tail is not a
    signature line. The head is gathered with `_read_exact_or_eof`, so `head == sig` means the
    file REALLY ended after the signature, never "the read happened to stop there". Any
    OSError (open, read or close) is `unreadable_path`."""
    st = _marker_stat(tag)
    if st is None:
        # The caller saw a regular file an instant ago; it was swapped for something else.
        raise _PopulationWalkError("unreadable_path")
    try:
        with _open_regular_no_follow(tag, (st.st_dev, st.st_ino)) as handle:
            ledger.begin_item()
            head = _read_exact_or_eof(handle, 128, ledger)
    except OSError as exc:
        raise _PopulationWalkError("unreadable_path") from exc
    sig = _CACHEDIR_TAG_SIGNATURE
    return head == sig or head.startswith((sig + b"\n", sig + b"\r\n"))


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
            for chunk in ledger.iter_chunks(handle, hard_cap=_MARKER_HASH_CAP):
                hasher.update(chunk)
    except OSError as exc:
        raise _PopulationWalkError("unreadable_path") from exc
    return hasher.hexdigest()


def _content_prune_marker(path: Path, ledger: _ByteLedger) -> str | None:
    """Name of the regular-file marker that makes `path` an unambiguous build/cache tree."""
    for marker in _BUILD_ROOT_MARKERS:
        if _regular_marker(path / marker):
            return marker
    if _regular_marker(path / _CMAKE_CACHE) and not (path / _CMAKE_SOURCE).exists():
        return _CMAKE_CACHE  # out-of-source CMake build tree only
    tag = path / "CACHEDIR.TAG"
    if _regular_marker(tag) and _cachedir_tag_valid(tag, ledger):
        return "CACHEDIR.TAG"
    return None


def _is_pruned_dir(path: Path, ledger: _ByteLedger | None = None) -> bool:
    """Unambiguous dependency/cache/build-root trees only (G-03: build/dist/target may hold source)."""
    ledger = ledger or _ByteLedger.unlimited()
    return path.name in _ALWAYS_PRUNED_DIRS or _content_prune_marker(path, ledger) is not None


def _population_paths(
    root: Path,
    pruned: list[str],
    content_pruned: dict[str, str] | None = None,
    ledger: _ByteLedger | None = None,
) -> Iterator[str]:
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
    for dirpath, dirnames, filenames in _os_walk(root, followlinks=False, onerror=_on_error):
        visited += 1
        if visited > _MAX_WALK_DIRS:
            raise _PopulationWalkError("dir_count_limit")
        current = Path(dirpath)
        rel_dir = current.relative_to(root)
        keep: list[str] = []
        leaves: list[str] = list(filenames)
        for d in sorted(dirnames):
            child = current / d
            try:
                child_is_link = _is_link(child)
            except OSError as exc:
                raise _PopulationWalkError("unreadable_path") from exc
            if child_is_link:
                leaves.append(
                    d
                )  # a directory symlink/junction is a leaf: never descended, never skipped
                continue
            marker = _content_prune_marker(child, ledger)
            if marker is not None or d in _ALWAYS_PRUNED_DIRS:
                rel = (rel_dir / d).as_posix()
                if len(pruned) < _MAX_REPORTED_PRUNED:
                    pruned.append(rel)
                if content_pruned is not None:
                    if marker is not None:
                        if n_content >= _MAX_CONTENT_PRUNED_DIRS:
                            raise _PopulationWalkError("pruned_dir_limit", "content")
                        n_content += 1
                        content_pruned[rel] = f"{marker}:{_marker_digest(child / marker, ledger)}"
                    else:
                        if n_name >= _MAX_NAME_PRUNED_DIRS:
                            raise _PopulationWalkError("pruned_dir_limit", "name")
                        n_name += 1
                        content_pruned[rel] = _NAME_PRUNED
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(leaves):
            # os.walk swallows a DirEntry.is_dir() failure (no onerror) and lists the directory
            # among `filenames`; fingerprinting it as a leaf would omit its whole subtree. A
            # directory is a leaf only when it is a link/junction.
            try:
                leaf_st = _lstat(current / name)
            except OSError as exc:
                raise _PopulationWalkError("unreadable_path") from exc
            if stat.S_ISDIR(leaf_st.st_mode) and not _link_from_stat(leaf_st):
                raise _PopulationWalkError("unreadable_path")
            yield (rel_dir / name).as_posix()


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
        hasher = hashlib.sha256()
        for chunk in ledger.iter_chunks(handle):
            hasher.update(chunk)
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
    root = Path(repo_root)
    result: dict[str, str] = {}
    scanned_files = 0
    ledger = _ByteLedger(max_file_bytes, max_aggregate_bytes)
    incomplete_reason: str | None = None
    limit_kind: str | None = None
    pruned: list[str] = []
    content_pruned: dict[str, str] = {}

    try:
        for rel in _population_paths(root, pruned, content_pruned, ledger):
            item = root / rel

            if scanned_files >= max_files:
                incomplete_reason = "file_count_limit"
                break

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

    if incomplete_reason is not None:
        population = {
            "verified": False,
            "status": "incomplete",
            "reason": incomplete_reason,
            "limit_kind": limit_kind,
            "population_policy": "agt04-v2",
            "population_source": "filesystem-walk",
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
