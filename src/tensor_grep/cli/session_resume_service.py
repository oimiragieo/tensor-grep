from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import typer

from tensor_grep.cli._index_lock import index_lock
from tensor_grep.cli.prepare_service import build_prepare_snapshot
from tensor_grep.cli.session_root import (
    _index_path,
    _session_payload_path,
    _session_root_for_payload,
    _snapshot_generation,
)
from tensor_grep.cli.session_store import _load_session_payload, _write_json_atomic


def session_prepare(
    session_id: str,
    query: str,
    path: str = ".",
) -> dict[str, Any]:
    """Build a prepare snapshot and save it into the active session for warm resumption."""
    root = _session_root_for_payload(session_id, path)
    session_path = _session_payload_path(root, session_id)

    # Lock on the SAME key refresh_session uses for its own payload critical section
    # (session_store.py) -- this previously locked on `root` itself, a key nothing else in the
    # session subsystem locks on, so it provided zero mutual exclusion against a concurrent
    # `tg session refresh` writing the same session_path (Codex Sol delta-verification audit
    # HIGH finding: "unlocked prepare/refresh read-modify-write race").
    with index_lock(_index_path(root)):
        payload = _load_session_payload(session_id, path)
        snapshot = build_prepare_snapshot(
            path=path,
            query=query,
        )
        snap_dict = asdict(snapshot)

        # AGT-02 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 02): stamp this
        # decision with the session's CURRENT content identity, not a wall-clock timestamp. A
        # legacy payload written before this feature existed (or before any refresh recomputed
        # it) has no `current_generation` yet; derive it from the payload's own `snapshot` rather
        # than leaving it permanently unknowable, but never invent identity out of thin air.
        current_generation = payload.get("current_generation")
        if current_generation is None:
            existing_snapshot = cast(list[dict[str, Any]], payload.get("snapshot") or [])
            if existing_snapshot:
                current_generation = _snapshot_generation(existing_snapshot)
                payload["current_generation"] = current_generation
        snap_dict["decision_generation"] = current_generation
        snap_dict["current_generation"] = current_generation
        snap_dict["decision_freshness"] = "current" if current_generation is not None else "unknown"

        payload["last_prepare"] = snap_dict
        _write_json_atomic(session_path, payload)

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
