from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import typer

from tensor_grep.cli._index_lock import (
    atomic_write_bytes_confined,
    dir_identity,
    file_identity,
    index_lock,
)
from tensor_grep.cli.prepare_service import build_prepare_snapshot
from tensor_grep.cli.session_provenance import seal_session
from tensor_grep.cli.session_root import (
    _index_path,
    _session_payload_path,
    _session_root_for_payload,
    _session_scan_target,
    _snapshot_generation,
)
from tensor_grep.cli.session_store import (
    SessionStaleError,
    _capture_snapshot,
    _load_session_payload,
    get_session,
)
from tensor_grep.cli.symbols_cache import (
    CONTENT_ABSENT_RECEIPT,
    CONTENT_PRESENT_RECEIPT,
    collect_content_receipts,
    content_receipt_key,
    record_content_receipt_digest,
)


def _session_store_identity(
    root: Path, session_path: Path
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Bind a prepare request to the project, session directory, and payload it observed."""
    try:
        root_identity = dir_identity(root)
        parent_identity = dir_identity(session_path.parent)
        payload_identity = file_identity(session_path)[:2]
    except OSError as exc:
        raise SessionStaleError(
            "session store changed while preparing; retry the prepare request"
        ) from exc
    return root_identity, parent_identity, payload_identity


def _require_session_store_identity(
    root: Path,
    session_path: Path,
    expected: tuple[tuple[int, int], tuple[int, int], tuple[int, int]],
) -> None:
    if _session_store_identity(root, session_path) != expected:
        raise SessionStaleError("session store changed while preparing; retry the prepare request")


def _require_authenticated_prepare_inputs(
    session_snapshot: list[dict[str, Any]],
    receipts: dict[str, str],
    *,
    snapshot_complete: bool,
) -> None:
    """Bind every newly scanned input to the authenticated session snapshot."""
    authenticated = {
        content_receipt_key(str(entry.get("path"))): str(entry.get("content_sha256"))
        for entry in session_snapshot
        if entry.get("path") and entry.get("content_sha256")
    }
    invalid_receipt = False
    for path, digest in receipts.items():
        if digest == CONTENT_ABSENT_RECEIPT:
            if path in authenticated or not snapshot_complete:
                invalid_receipt = True
            else:
                try:
                    Path(path).lstat()
                    invalid_receipt = True
                except (FileNotFoundError, NotADirectoryError):
                    pass
                except OSError:
                    invalid_receipt = True
        elif digest == CONTENT_PRESENT_RECEIPT:
            invalid_receipt = path not in authenticated
        else:
            invalid_receipt = authenticated.get(path) != digest
        if invalid_receipt:
            break
    if authenticated.keys() - receipts.keys():
        invalid_receipt = True
    if (authenticated and not receipts) or invalid_receipt:
        raise SessionStaleError(
            "project contents changed while preparing; retry the prepare request"
        )


def _require_authenticated_snapshot_unchanged(
    root: Path, session_snapshot: list[dict[str, Any]]
) -> None:
    expected = {
        str(entry.get("path")): str(entry.get("content_sha256"))
        for entry in session_snapshot
        if entry.get("path") and entry.get("content_sha256")
    }
    observed = _capture_snapshot(list(expected), root=root, expected=expected)
    for entry in observed:
        record_content_receipt_digest(
            str(entry["path"]),
            str(entry["content_sha256"]),
        )
    if len(observed) != len(expected):
        raise SessionStaleError(
            "project contents changed while preparing; retry the prepare request"
        )


def session_prepare(
    session_id: str,
    query: str,
    path: str = ".",
) -> dict[str, Any]:
    """Build a prepare snapshot and save it into the active session for warm resumption."""
    root = _session_root_for_payload(session_id, path)
    session_path = _session_payload_path(root, session_id)

    initial_store_identity = _session_store_identity(root, session_path)
    payload = _load_session_payload(session_id, path)
    _require_session_store_identity(root, session_path, initial_store_identity)
    initial_provenance = payload.get("session_provenance")
    scan_target = _session_scan_target(payload, root)

    # AGT-02 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 02): stamp this
    # decision with the session's CURRENT content identity, not a wall-clock timestamp. A
    # legacy payload written before this feature existed (or before any refresh recomputed
    # it) has no `current_generation` yet; derive it from the payload's own `snapshot` rather
    # than leaving it permanently unknowable, but never invent identity out of thin air.
    current_generation = payload.get("current_generation")
    existing_snapshot = cast(list[dict[str, Any]], payload.get("snapshot") or [])
    if current_generation is None and existing_snapshot:
        current_generation = _snapshot_generation(existing_snapshot)

    # P9 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 06): reuse the
    # session's own repo_map only when its content generation still matches the snapshot.
    reusable_repo_map: dict[str, Any] | None = None
    if existing_snapshot and current_generation is not None:
        if _snapshot_generation(existing_snapshot) == current_generation:
            candidate_repo_map = payload.get("repo_map")
            if isinstance(candidate_repo_map, dict):
                reusable_repo_map = candidate_repo_map

    scan_limit = payload.get("scan_limit")
    snapshot_complete = (
        isinstance(scan_limit, dict)
        and scan_limit.get("possibly_truncated") is False
        and "snapshot_unreadable_paths" not in payload
    )
    authenticated_sizes: dict[str | Path, int] = {}
    for entry in existing_snapshot:
        entry_path = entry.get("path")
        entry_size = entry.get("size")
        if (
            entry_path
            and isinstance(entry_size, int)
            and not isinstance(entry_size, bool)
            and entry_size >= 0
        ):
            authenticated_sizes[content_receipt_key(str(entry_path))] = entry_size
    authenticated_scan_paths: list[str] = []
    payload_repo_map = payload.get("repo_map")
    if isinstance(payload_repo_map, dict):
        authenticated_scan_paths = [
            str(current)
            for current in payload_repo_map.get("related_paths", [])
            if content_receipt_key(str(current)) in authenticated_sizes
        ]
    with collect_content_receipts(
        authenticated_root=scan_target,
        authenticated_sizes=authenticated_sizes,
        authenticated_scan_paths=authenticated_scan_paths,
    ) as build_receipts:
        snapshot = build_prepare_snapshot(
            path=str(scan_target),
            query=query,
            repo_map=reusable_repo_map,
            _input_validator=lambda: _require_authenticated_snapshot_unchanged(
                root, existing_snapshot
            ),
        )
    _require_session_store_identity(root, session_path, initial_store_identity)
    _require_authenticated_prepare_inputs(
        existing_snapshot,
        build_receipts,
        snapshot_complete=snapshot_complete,
    )
    _require_session_store_identity(root, session_path, initial_store_identity)
    snap_dict = asdict(snapshot)
    snap_dict["decision_generation"] = current_generation
    snap_dict["current_generation"] = current_generation
    snap_dict["decision_freshness"] = "current" if current_generation is not None else "unknown"

    # Publish under the same lock as refresh, but keep freshness hashing and the cold build out
    # of the store-wide critical section. The provenance tag covers every mutable session field;
    # the root/directory/payload identities prevent a byte-identical copied store from winning
    # that content CAS after a parent swap.
    with index_lock(_index_path(root)):
        _require_session_store_identity(root, session_path, initial_store_identity)
        for absent_path, digest in build_receipts.items():
            if digest == CONTENT_ABSENT_RECEIPT:
                try:
                    Path(absent_path).lstat()
                except (FileNotFoundError, NotADirectoryError):
                    continue
                raise SessionStaleError(
                    "project contents changed while preparing; retry the prepare request"
                )
        current_payload = get_session(session_id, path)
        _require_session_store_identity(root, session_path, initial_store_identity)
        if current_payload.get("session_provenance") != initial_provenance:
            raise SessionStaleError("session changed while preparing; retry the prepare request")
        current_payload["current_generation"] = current_generation
        current_payload["last_prepare"] = snap_dict
        seal_session(current_payload, root)
        atomic_write_bytes_confined(
            root,
            session_path,
            json.dumps(current_payload, indent=2).encode("utf-8"),
            expected_parent_identity=initial_store_identity[1],
            expected_file_identity=initial_store_identity[2],
        )

    res = dict(snap_dict)
    res["session_id"] = session_id
    return res


def _last_prepare_with_effective_freshness(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Fill in `decision_freshness` for a `last_prepare` that predates this feature, or whose
    identity metadata was stripped some other way. Never inferred from wall-clock timestamps: a
    missing `decision_generation` is always `unknown`, regardless of when the record was written.
    """
    last_prepare = payload.get("last_prepare")
    if not isinstance(last_prepare, dict):
        return cast(dict[str, Any] | None, last_prepare)
    result = dict(last_prepare)
    decision_generation = result.get("decision_generation")
    if "decision_freshness" not in result:
        result["decision_freshness"] = "unknown" if decision_generation is None else "current"
    if "current_generation" not in result:
        result["current_generation"] = payload.get("current_generation")
    return result


def session_resume(session_id: str, path: str = ".") -> dict[str, Any]:
    """Resume a warm session, reporting its last prepared snapshot and decision context."""
    payload = _load_session_payload(session_id, path)
    return {
        "version": payload.get("version", 1),
        "session_id": session_id,
        "root": payload.get("root", str(Path(path).resolve())),
        "created_at": payload.get("created_at", ""),
        "resumed": True,
        "last_prepare": _last_prepare_with_effective_freshness(payload),
        "current_generation": payload.get("current_generation"),
    }


def dispatch_session_prepare_cli(
    session_id: str,
    query: str,
    path: str,
    json_output: bool,
) -> None:
    try:
        res = session_prepare(session_id, query, path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        json.dumps(res, indent=2, ensure_ascii=False)
        if json_output
        else f"Prepared session {session_id}"
    )


def dispatch_session_resume_cli(
    session_id: str,
    path: str,
    json_output: bool,
) -> None:
    try:
        res = session_resume(session_id, path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        json.dumps(res, indent=2, ensure_ascii=False)
        if json_output
        else f"Resumed session {session_id}"
    )


def session_prepare_cmd(
    session_id: str = typer.Argument(..., help="Session ID."),
    query: str = typer.Argument(..., help="Query."),
    path: str = typer.Argument(".", help="Root."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    dispatch_session_prepare_cli(session_id, query, path, json_output)


def session_resume_cmd(
    session_id: str = typer.Argument(..., help="Session ID."),
    path: str = typer.Argument(".", help="Root."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    dispatch_session_resume_cli(session_id, path, json_output)
