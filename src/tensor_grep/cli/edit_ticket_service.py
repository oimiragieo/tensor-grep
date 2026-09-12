from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# AGT-04 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 04, first-fix slice):
# reviewed set of dependency-tree / build-output directory names to prune BEFORE descending,
# not filter after a full walk. This is deliberately narrow (well-known package-manager and
# build-tool output dirs only) -- it never matches an ordinary tracked dotfile like .github or
# .gitignore, so those remain hashed (see test_tracked_dotfile_survives_pruning).
_IGNORED_DEPENDENCY_DIRS = frozenset({
    "node_modules",
    ".venv",
    "venv",
    "target",
    "dist",
    "build",
    ".git",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "site-packages",
})

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


def compute_file_fingerprint(path: str | Path) -> str:
    p = Path(path)
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

    Uses os.walk with topdown pruning so a dependency tree in _IGNORED_DEPENDENCY_DIRS is never
    entered at all -- unlike a post-hoc filter over Path.rglob, which still reads every file in
    node_modules/.venv/target before discarding the results.
    """
    root = Path(repo_root)
    result: dict[str, str] = {}
    scanned_files = 0
    scanned_bytes = 0
    incomplete_reason: str | None = None

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DEPENDENCY_DIRS]
        for name in sorted(filenames):
            item = Path(dirpath) / name
            rel_parts = item.relative_to(root).parts
            rel = "/".join(rel_parts)

            if scanned_files >= max_files:
                incomplete_reason = "file_count_limit"
                break

            try:
                size = item.stat().st_size
            except OSError:
                # A file that vanishes or becomes unreadable mid-walk (permission change, a
                # concurrent delete) must not silently disappear from `result` while the
                # population still reports "complete" -- that is the exact false-PASS this
                # function exists to prevent (see the module docstring). Skip the file but mark
                # the population incomplete rather than `continue`ing silently.
                incomplete_reason = incomplete_reason or "unreadable_path"
                continue

            if size > max_file_bytes:
                incomplete_reason = "per_file_byte_limit"
                scanned_files += 1
                continue

            if scanned_bytes + size > max_aggregate_bytes:
                incomplete_reason = "aggregate_byte_limit"
                break

            result[rel] = compute_file_fingerprint(item)
            scanned_files += 1
            scanned_bytes += size
        if incomplete_reason is not None:
            break

    if incomplete_reason is not None:
        population = {
            "verified": False,
            "status": "incomplete",
            "reason": incomplete_reason,
            "population_policy": "agt04-v1",
            "scanned_files": scanned_files,
            "scanned_bytes": scanned_bytes,
        }
    else:
        population = {
            "verified": True,
            "status": "complete",
            "reason": None,
            "population_policy": "agt04-v1",
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
    """
    try:
        return str(Path(root).resolve()).replace("\\", "/").rstrip("/").lower()
    except OSError:
        return str(root).replace("\\", "/").rstrip("/").lower()


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

    undeclared_drift: list[str] = []
    for path in sorted(all_paths):
        pre_fp = ticket.pre_edit_fingerprints.get(path, "")
        cur_fp = current_fps.get(path, "")
        if pre_fp != cur_fp and path not in norm_declared:
            undeclared_drift.append(path)

    if undeclared_drift:
        return {
            "verdict": "FAIL",
            "reason": "edit_contract_violated",
            "violations": undeclared_drift,
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
