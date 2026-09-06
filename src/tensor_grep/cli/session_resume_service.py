from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer

from tensor_grep.cli._index_lock import index_lock
from tensor_grep.cli.prepare_service import build_prepare_snapshot
from tensor_grep.cli.session_root import _session_payload_path, _session_root_for_payload
from tensor_grep.cli.session_store import _load_session_payload, _write_json_atomic


def session_prepare(
    session_id: str,
    query: str,
    path: str = ".",
) -> dict[str, Any]:
    """Build a prepare snapshot and save it into the active session for warm resumption."""
    root = _session_root_for_payload(session_id, path)
    session_path = _session_payload_path(root, session_id)

    with index_lock(root):
        payload = _load_session_payload(session_id, path)
        snapshot = build_prepare_snapshot(
            path=path,
            query=query,
        )
        snap_dict = asdict(snapshot)
        payload["last_prepare"] = snap_dict
        _write_json_atomic(session_path, payload)

    res = dict(snap_dict)
    res["session_id"] = session_id
    return res


def session_resume(session_id: str, path: str = ".") -> dict[str, Any]:
    """Resume a warm session, reporting its last prepared snapshot and decision context."""
    payload = _load_session_payload(session_id, path)
    return {
        "version": payload.get("version", 1),
        "session_id": session_id,
        "root": payload.get("root", str(Path(path).resolve())),
        "created_at": payload.get("created_at", ""),
        "resumed": True,
        "last_prepare": payload.get("last_prepare"),
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
