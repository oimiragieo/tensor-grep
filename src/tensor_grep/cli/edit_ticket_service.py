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
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_os_walk = os.walk  # private seam: tests patch this, never the stdlib attribute

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


def _regular_marker(path: Path) -> bool:
    """A marker counts only as a REGULAR, non-symlink file (never follow a marker link)."""
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _cachedir_tag_valid(tag: Path) -> bool:
    """The FIRST LINE must be exactly the signature, then LF, CRLF or EOF (bounded read)."""
    try:
        with open(tag, "rb") as handle:
            head = handle.read(128)
    except OSError:
        return False  # cannot classify -> walk it (covered, fail-closed by budget)
    sig = _CACHEDIR_TAG_SIGNATURE
    return head == sig or head.startswith((sig + b"\n", sig + b"\r\n"))


def _marker_digest(path: Path) -> str:
    """sha256 of the marker's bytes (bounded) so a changed marker is detectable at verify."""
    hasher = hashlib.sha256()
    remaining = _MARKER_HASH_CAP
    try:
        with open(path, "rb") as handle:
            while remaining > 0 and (chunk := handle.read(min(65536, remaining))):
                hasher.update(chunk)
                remaining -= len(chunk)
    except OSError:
        return "unreadable"
    return hasher.hexdigest()


def _content_prune_marker(path: Path) -> str | None:
    """Name of the regular-file marker that makes `path` an unambiguous build/cache tree."""
    for marker in _BUILD_ROOT_MARKERS:
        if _regular_marker(path / marker):
            return marker
    if _regular_marker(path / _CMAKE_CACHE) and not (path / _CMAKE_SOURCE).exists():
        return _CMAKE_CACHE  # out-of-source CMake build tree only
    tag = path / "CACHEDIR.TAG"
    if _regular_marker(tag) and _cachedir_tag_valid(tag):
        return "CACHEDIR.TAG"
    return None


def _is_pruned_dir(path: Path) -> bool:
    """Unambiguous dependency/cache/build-root trees only (G-03: build/dist/target may hold source)."""
    return path.name in _ALWAYS_PRUNED_DIRS or _content_prune_marker(path) is not None


def _population_paths(
    root: Path, pruned: list[str], content_pruned: dict[str, str] | None = None
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
            if os.path.islink(child):
                leaves.append(d)  # a directory symlink is a leaf: never descended, never skipped
                continue
            marker = _content_prune_marker(child)
            if marker is not None or d in _ALWAYS_PRUNED_DIRS:
                rel = (rel_dir / d).as_posix()
                if len(pruned) < _MAX_REPORTED_PRUNED:
                    pruned.append(rel)
                if content_pruned is not None:
                    if marker is not None:
                        if n_content >= _MAX_CONTENT_PRUNED_DIRS:
                            raise _PopulationWalkError("pruned_dir_limit", "content")
                        n_content += 1
                        content_pruned[rel] = f"{marker}:{_marker_digest(child / marker)}"
                    else:
                        if n_name >= _MAX_NAME_PRUNED_DIRS:
                            raise _PopulationWalkError("pruned_dir_limit", "name")
                        n_name += 1
                        content_pruned[rel] = _NAME_PRUNED
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(leaves):
            yield (rel_dir / name).as_posix()


def compute_file_fingerprint(path: str | Path) -> str:
    p = Path(path)
    if p.is_symlink():
        # Never follow a leaf link: its target may be out-of-root or huge (G-08).
        return hashlib.sha256(b"symlink:" + os.fsencode(os.readlink(p))).hexdigest()
    if not p.is_file():
        return ""
    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


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
    scanned_bytes = 0
    incomplete_reason: str | None = None
    limit_kind: str | None = None
    pruned: list[str] = []
    content_pruned: dict[str, str] = {}

    try:
        for rel in _population_paths(root, pruned, content_pruned):
            item = root / rel

            if scanned_files >= max_files:
                incomplete_reason = "file_count_limit"
                break

            try:
                size = len(os.readlink(item)) if item.is_symlink() else item.stat().st_size
            except OSError:
                # A file that vanishes or becomes unreadable mid-walk must not silently
                # disappear from `result` while the population still reports "complete" --
                # that is the exact false-PASS this function exists to prevent.
                incomplete_reason = incomplete_reason or "unreadable_path"
                continue

            if size > max_file_bytes:
                incomplete_reason = "per_file_byte_limit"
                scanned_files += 1
                continue

            if scanned_bytes + size > max_aggregate_bytes:
                incomplete_reason = "aggregate_byte_limit"
                break

            try:
                result[rel] = compute_file_fingerprint(item)
            except OSError:
                incomplete_reason = incomplete_reason or "unreadable_path"
                continue
            scanned_files += 1
            scanned_bytes += size
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
            "scanned_bytes": scanned_bytes,
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
            "scanned_bytes": scanned_bytes,
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
    # incomplete population reach PASS. "unknown" (legacy tickets predating this field) is
    # deliberately NOT treated as incomplete -- that is the documented compatibility path.
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
