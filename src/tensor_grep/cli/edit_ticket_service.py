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


def compute_working_tree_fingerprint(repo_root: str | Path) -> str:
    root = Path(repo_root)
    entries: list[str] = []
    for item in sorted(root.rglob("*")):
        if item.is_file() and not any(
            part.startswith(".") or part == "__pycache__" for part in item.parts
        ):
            rel = str(item.relative_to(root)).replace("\\", "/")
            entries.append(f"{rel}:{compute_file_fingerprint(item)}")
    content = "\n".join(entries).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def build_edit_ready_ticket(
    *,
    repo_root: str,
    target_path: str,
    query: str,
    allowed_files: list[str],
) -> EditReadyTicketV1:
    root = Path(repo_root)
    pre_fps: dict[str, str] = {}
    for f in allowed_files:
        full_path = root / f
        pre_fps[f] = compute_file_fingerprint(full_path)

    tree_fp = compute_working_tree_fingerprint(repo_root)
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
    violations: list[str] = []
    for m in modified_files:
        norm_m = m.replace("\\", "/")
        if norm_m not in [f.replace("\\", "/") for f in ticket.allowed_files]:
            violations.append(norm_m)

    if violations:
        return {
            "verdict": "FAIL",
            "reason": "edit_contract_violated",
            "violations": violations,
            "ticket_id": ticket.ticket_id,
        }

    return {
        "verdict": "PASS",
        "reason": None,
        "violations": [],
        "ticket_id": ticket.ticket_id,
    }
