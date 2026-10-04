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
from collections.abc import Generator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tensor_grep.cli import edit_ticket_walk as _walk
from tensor_grep.cli.edit_ticket_walk import (  # noqa: F401 - re-exported for callers
    _FINGERPRINT_TAGS,
    _MAX_REPORTED_PRUNED,
    _NAME_PRUNED,
    _BudgetExceeded,
    _link_from_stat,
    _PopulationWalkError,
    compute_file_fingerprint,
)

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
    ledger = _walk._ByteLedger(max_file_bytes, max_aggregate_bytes)
    incomplete_reason: str | None = None
    limit_kind: str | None = None
    pruned: list[str] = []
    content_pruned: dict[str, str] = {}

    paths: Generator[tuple[str, str | None], None, None] | None = None
    handle_root_identity: list[int] = []
    try:
        try:
            root_st = _walk._lstat(root)
        except OSError as exc:
            raise _PopulationWalkError("unreadable_path") from exc
        if not stat.S_ISDIR(root_st.st_mode) or _link_from_stat(root_st):
            raise _PopulationWalkError("unreadable_path")
        root_identity = [root_st.st_dev, root_st.st_ino]  # fallback for handle-less walkers
        paths = _walk._population_paths(
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
                size_st = _walk._lstat(item)  # ONE lstat sizes the leaf (links: link-text length)
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
                fingerprint = _walk._fingerprint_enumerated(item, ledger)
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
    except OSError:  # backstop: an OSError is never an exception for mint / verify
        if incomplete_reason is None:
            incomplete_reason = "unreadable_path"
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
