"""CLI behaviour for ``tg session daemon stop`` (kept out of the size-ratcheted ``main.py``).

An UNCONFIRMED stop (``running=True, stopped=False``) must never read as "not running" with exit 0:
it prints an ASCII message saying shutdown could not be confirmed, with the reason, and exits 2,
using the structured ``error`` shape in ``--json`` mode.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import typer

_REASONS = {
    "pid_unproven": "the recorded pid could not be proven to be this root's daemon, so it was not signalled",
    "termination_failed": "the daemon process could not be terminated",
    "endpoint_still_accepting_connections": "the daemon is still accepting connections",
    "no_bound_process_handle": (
        "no kernel process handle (Windows handle / Linux pidfd) is available to signal the process "
        "safely, so it was not signalled"
    ),
    "stop_not_confirmed": "the daemon did not acknowledge the stop request and still answers",
}


def stop_unconfirmed_message(payload: dict[str, Any]) -> str:
    reason = str(payload.get("unconfirmed_reason", "stop_not_confirmed"))
    detail = _REASONS.get(reason, reason)
    return f"Session daemon shutdown could not be confirmed ({reason}): {detail}. daemon.json was kept."


def run_session_daemon_stop(
    path: str,
    json_output: bool,
    with_schema_version: Callable[..., dict[str, Any]],
) -> int:
    """Run the stop and print its result; returns the process exit code (0 or 2)."""
    from tensor_grep.cli.session_daemon import stop_session_daemon

    try:
        payload = stop_session_daemon(path)
    except Exception as exc:
        # tg's OWN message: ASCII only (non-ASCII in the exception text is escaped), exit 2, and a
        # structured error in --json mode.
        text = f"Session daemon stop failed: {exc}".encode("ascii", "backslashreplace").decode(
            "ascii"
        )
        if json_output:
            error = {"error": {"code": "stop_failed", "message": text}}
            typer.echo(json.dumps(with_schema_version(error, version=1), indent=2))
        else:
            typer.echo(text, err=True)
        return 2

    unconfirmed = payload.get("running") is True and not payload.get("stopped")
    if unconfirmed:
        message = stop_unconfirmed_message(payload)
        if "error" in payload:  # keep the daemon's own reply (e.g. unauthorized) for diagnosis
            payload = {**payload, "stop_reply_error": payload["error"]}
        payload = {**payload, "error": {"code": "stop_unconfirmed", "message": message}}
    if json_output:
        typer.echo(json.dumps(with_schema_version(payload, version=1), indent=2))
    elif unconfirmed:
        typer.echo(payload["error"]["message"], err=True)
    else:
        typer.echo(
            "Session daemon stopped" if payload.get("stopped") else "Session daemon not running"
        )
    return 2 if unconfirmed else 0
