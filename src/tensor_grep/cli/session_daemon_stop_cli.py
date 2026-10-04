"""CLI behaviour for ``tg session daemon stop`` / ``status`` (kept out of the size-ratcheted ``main.py``).

ONE exit-code table (``stop_exit_code`` / ``status_exit_code``) decides every exit status, so a stop
that was not proven can never read as success:

* exit 0 ONLY for an explicitly proven outcome -- ``running`` is False, the payload carries NO ``error``
  and a ``proof`` from the closed set the stop code can produce (``no_metadata``, ``endpoint_refused``,
  ``cooperative_refused``, ``pid_refused``);
* everything else is exit 2: unconfirmed results, any retained ``error`` (a failed request that a proven
  shutdown superseded is moved to ``stop_reply_error`` by the stop code, so it is an explicit success),
  existing metadata with a missing / null / invalid endpoint (``daemon.json`` is kept), and any payload
  the table does not know.

Everything tg itself prints is ASCII (exception text is escaped) and ``--json`` mode uses the structured
``error`` shape.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import typer

_REASONS = {
    "metadata_invalid": "daemon.json exists but is not valid JSON / not a JSON object, so it was kept",
    "metadata_unreadable": "daemon.json exists but could not be read, so it was kept",
    "pid_unproven": "the recorded pid could not be proven to be this root's daemon, so it was not signalled",
    "termination_failed": "the daemon process could not be terminated",
    "endpoint_still_accepting_connections": "the daemon is still accepting connections",
    "endpoint_unverifiable": "daemon.json records no usable endpoint, so shutdown cannot be checked",
    "no_bound_process_handle": (
        "no kernel process handle (Windows handle / Linux pidfd) is available to signal the process "
        "safely, so it was not signalled"
    ),
    "stop_not_confirmed": "the daemon did not acknowledge the stop request and still answers",
}


def stop_exit_code(payload: Any) -> int:
    """The single stop exit table: 0 only for a proven, error-free, not-running outcome."""
    from tensor_grep.cli.session_daemon_trust import _STOP_PROOFS

    if not isinstance(payload, dict) or "error" in payload:
        return 2
    if payload.get("running") is not False:
        return 2
    proof = payload.get("proof")
    return 0 if isinstance(proof, str) and proof in _STOP_PROOFS else 2


def status_exit_code(payload: Any) -> int:
    """The status exit table: 0 for an authenticated running daemon or no metadata at all; 2 when
    metadata exists but the daemon could not be authenticated (stale-looking metadata, possibly with a
    listener behind it), when an error is present, or for any payload this table does not know."""
    if not isinstance(payload, dict) or "error" in payload:
        return 2
    running = payload.get("running")
    if running is True:
        return 0
    if running is not False:
        return 2
    if (
        payload.get("stale_metadata")
        or payload.get("endpoint_accepting_connections")
        or payload.get("metadata_error")
    ):
        return 2
    return 0


def _ascii(text: str) -> str:
    return text.encode("ascii", "backslashreplace").decode("ascii")


def stop_unconfirmed_message(payload: dict[str, Any]) -> str:
    reason = str(payload.get("unconfirmed_reason", "stop_not_confirmed"))
    detail = _REASONS.get(reason, reason)
    return f"Session daemon shutdown could not be confirmed ({reason}): {detail}. daemon.json was kept."


def _fail(text: str, code: str, json_output: bool, with_schema_version: Callable[..., Any]) -> int:
    message = _ascii(text)
    if json_output:
        typer.echo(
            json.dumps(
                with_schema_version({"error": {"code": code, "message": message}}, version=1),
                indent=2,
            )
        )
    else:
        typer.echo(message, err=True)
    return 2


def run_session_daemon_stop(
    path: str,
    json_output: bool,
    with_schema_version: Callable[..., dict[str, Any]],
) -> int:
    """Run the stop and print its result; the exit code comes from ``stop_exit_code`` only."""
    from tensor_grep.cli.session_daemon import stop_session_daemon

    try:
        payload = stop_session_daemon(path)
    except Exception as exc:
        # tg's OWN message: ASCII only (non-ASCII in the exception text is escaped), exit 2, and a
        # structured error in --json mode.
        return _fail(
            f"Session daemon stop failed: {exc}", "stop_failed", json_output, with_schema_version
        )

    code = stop_exit_code(payload)
    if code != 0:
        if payload.get("running") is True:
            error = {"code": "stop_unconfirmed", "message": stop_unconfirmed_message(payload)}
        else:
            error = {
                "code": "stop_unproven",
                "message": "Session daemon stop result could not be trusted (no proof of shutdown, or an error was reported).",
            }
        if "error" in payload:  # keep the daemon's own reply (e.g. unauthorized) for diagnosis
            payload = {**payload, "stop_reply_error": payload["error"]}
        payload = {**payload, "error": error}
    if json_output:
        typer.echo(json.dumps(with_schema_version(payload, version=1), indent=2))
    elif code != 0:
        typer.echo(_ascii(payload["error"]["message"]), err=True)
    else:
        typer.echo(
            "Session daemon stopped" if payload.get("stopped") else "Session daemon not running"
        )
    return code


def run_session_daemon_status(
    path: str,
    json_output: bool,
    with_schema_version: Callable[..., dict[str, Any]],
) -> int:
    """Run ``status`` and print it; the exit code comes from ``status_exit_code`` only."""
    from tensor_grep.cli.session_daemon import get_session_daemon_status

    try:
        payload = get_session_daemon_status(path)
    except Exception as exc:
        return _fail(
            f"Session daemon status failed: {exc}",
            "status_failed",
            json_output,
            with_schema_version,
        )

    code = status_exit_code(payload)
    if code != 0 and payload.get("running") is not True:
        listening = "yes" if payload.get("endpoint_accepting_connections") else "no"
        damage = payload.get("metadata_error")
        message = (
            f"Session daemon status could not be confirmed ({damage}): daemon.json is damaged."
            if damage
            else "Session daemon status could not be confirmed: daemon.json exists but the daemon "
            f"did not authenticate (endpoint accepting connections: {listening})."
        )
        if "error" in payload:
            payload = {**payload, "status_reply_error": payload["error"]}
        payload = {**payload, "error": {"code": "status_unconfirmed", "message": message}}
    if json_output:
        typer.echo(json.dumps(with_schema_version(payload, version=1), indent=2))
    elif payload.get("running") is True and code == 0:
        typer.echo(
            f"Session daemon running on {payload['host']}:{payload['port']} pid={payload['pid']}"
        )
        if payload.get("response_cache_scope"):
            typer.echo(f"response_cache_scope={payload['response_cache_scope']}")
    elif code != 0:
        typer.echo(_ascii(payload["error"]["message"]), err=True)
    else:
        typer.echo("Session daemon not running")
    return code
