from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


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


def _walk_tracked_files(repo_root: str | Path) -> dict[str, str]:
    """Per-file fingerprints for every file under repo_root, keyed by POSIX-normalized relative
    path. Shared by compute_working_tree_fingerprint (aggregate) and build/verify_edit_ticket
    (per-file, so a single drifted file can be named rather than only detected in aggregate)."""
    root = Path(repo_root)
    result: dict[str, str] = {}
    for item in sorted(root.rglob("*")):
        if item.is_file() and not any(
            part.startswith(".") or part == "__pycache__" for part in item.parts
        ):
            rel = str(item.relative_to(root)).replace("\\", "/")
            result[rel] = compute_file_fingerprint(item)
    return result


def compute_working_tree_fingerprint(repo_root: str | Path) -> str:
    entries = [f"{rel}:{fp}" for rel, fp in sorted(_walk_tracked_files(repo_root).items())]
    content = "\n".join(entries).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def build_edit_ready_ticket(
    *,
    repo_root: str,
    target_path: str,
    query: str,
    allowed_files: list[str],
) -> EditReadyTicketV1:
    # Whole-tree, not just allowed_files: verify_edit_ticket needs a pre-edit fingerprint for
    # every file to name which one drifted outside the declared scope, not just detect that
    # SOME file did via the aggregate working_tree_fingerprint.
    pre_fps = _walk_tracked_files(repo_root)
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
    )


def verify_edit_ticket(
    *,
    repo_root: str,
    ticket: EditReadyTicketV1,
    modified_files: list[str],
) -> dict[str, Any]:
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
    current_fps = _walk_tracked_files(repo_root)
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
