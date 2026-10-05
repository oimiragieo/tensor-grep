"""Stop a daemon that ``session_daemon._probe_daemon`` rejected (F-07, wave-2b Part H.6).

Kept out of ``session_daemon.py`` (size-ratcheted). Every collaborator is looked up on the
``session_daemon`` module at CALL time, so the long-standing test seams
(``monkeypatch.setattr(session_daemon, "_daemon_request" | "_terminate_daemon_by_pid" |
"_DAEMON_START_TIMEOUT_SECONDS", ...)``) keep working unchanged.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from tensor_grep.cli.runtime_paths import _expected_tg_version
from tensor_grep.cli.session_daemon_trust import (
    _await_endpoint_refused,
    _daemon_pid_state,
    _is_loopback_host,
    _stale_success_fields,
    _stop_success,
    _unconfirmed_fields,
    _valid_daemon_port,
    _verify_ping_reply,
)
from tensor_grep.cli.session_store import _SESSION_VERSION

# A refused loopback connect can take ~2 s on Windows; the stop probes need to see the refusal.
_DAEMON_STOP_PROBE_CONNECT_TIMEOUT_SECONDS = 3.0


def _stop_unprobed_daemon(root: Path, metadata: dict[str, Any] | None) -> tuple[str, int | None]:
    """Stop a daemon that ``_probe_daemon`` rejected (e.g. a package-version skew) -- but only one
    that PROVES it serves ``root``. Returns ``(outcome, proven_pid)``; outcome is one of:

    * ``absent``: no metadata recorded.
    * ``ineligible``: non-loopback host or malformed port: no request, no pid fallback.
    * ``not_listening``: the eligible endpoint REFUSED the first ping (nothing to stop).
    * ``unverified``: could not PROVE the listener serves ``root`` (no/forged HMAC proof, timeout,
      auth failure): nothing is stopped by THIS function; the caller's existing attested-pid path
      (``_classify_daemon_pid`` == "ours") remains the only thing that may still signal.
    * ``current_version``: proof verified but the daemon runs THIS package version, so it is not the
      version-skew case this function exists for (the probe failed for another reason): left to the
      caller's existing path.
    * ``cooperative``: proof verified, ``stop`` acked, and the endpoint then REFUSED connections.
    * ``unresponsive``: proof verified but no refusal observed (the pid escalation, if any, uses the
      PROVEN pid from the signed reply, never the metadata's).

    A refused connection is the only accepted evidence that a daemon is gone: a timeout, an auth
    failure, an ack or a delivered signal is not (council wave-2b r3/r25).
    """
    from tensor_grep.cli import session_daemon as sd

    if not metadata:
        return "absent", None
    host, port = metadata.get("host", sd._DAEMON_HOST), _valid_daemon_port(metadata.get("port"))
    if port is None or not _is_loopback_host(host):
        return "ineligible", None
    timeouts: dict[str, Any] = {
        "response_timeout": sd._DAEMON_CONNECT_TIMEOUT_SECONDS,
        "connect_timeout": _DAEMON_STOP_PROBE_CONNECT_TIMEOUT_SECONDS,
        "token": sd._daemon_token(metadata),
    }
    nonce = secrets.token_hex(16)
    try:
        reply = sd._daemon_request(str(host), port, {"command": "ping", "nonce": nonce}, **timeouts)
    except ConnectionRefusedError:
        return "not_listening", None
    except Exception:
        return "unverified", None
    if not reply.get("ok") or not _verify_ping_reply(reply, nonce, root, port):
        return "unverified", None
    proven_pid = int(reply["pid"])
    if metadata.get("package_version") == _expected_tg_version():
        return "current_version", proven_pid
    try:
        ack = sd._daemon_request(str(host), port, {"command": "stop"}, **timeouts)
    except (OSError, RuntimeError, ValueError):  # socket error/timeout, closed connection, bad JSON
        return "unresponsive", proven_pid
    if not ack.get("ok"):
        return "unresponsive", proven_pid
    if _await_endpoint_refused(host, port, sd._DAEMON_START_TIMEOUT_SECONDS):
        return "cooperative", proven_pid
    return "unresponsive", proven_pid


def _records_no_process(metadata: dict[str, Any]) -> bool:
    pid = metadata.get("pid")
    return isinstance(pid, int) and not isinstance(pid, bool) and pid <= 0


def _stop_rejected_daemon(
    root: Path, stale_metadata: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, str]:
    """``(result, outcome)`` for a daemon ``_probe_daemon`` rejected. ``result`` is None when the
    caller's established path must decide (no metadata, unverified, current version, or a refused
    endpoint whose pid may be a live process). See ``_stop_unprobed_daemon``."""
    from tensor_grep.cli import session_daemon as sd

    outcome, proven_pid = _stop_unprobed_daemon(root, stale_metadata)
    if stale_metadata is None or outcome in {"absent", "unverified", "current_version"}:
        return None, outcome
    if outcome == "not_listening" and not _records_no_process(stale_metadata):
        # A refused endpoint plus a pid that may be a LIVE process keeps the established attested-pid /
        # unconfirmed handling (the caller's path); only metadata that records no process at all
        # (pid <= 0) is settled by the refusal alone.
        return None, outcome
    base: dict[str, Any] = {"version": _SESSION_VERSION, "root": str(root)}
    if outcome == "ineligible":
        return {**base, **_unconfirmed_fields("gone", False, endpoint_ok=False)}, outcome
    host, port = str(stale_metadata.get("host", sd._DAEMON_HOST)), stale_metadata.get("port")
    stale_pid, stale_port = sd._daemon_identity(stale_metadata)
    if outcome == "unresponsive" and proven_pid is not None:
        target = {**stale_metadata, "pid": proven_pid}
        signalled = sd._terminate_daemon_by_pid(target, root=root)
        # A delivered signal is not a stopped daemon: only a refused connection is (r25).
        if signalled and _await_endpoint_refused(host, port, sd._DAEMON_START_TIMEOUT_SECONDS):
            outcome = "pid"
        else:
            state = _daemon_pid_state(target, root)
            return {**base, **_unconfirmed_fields(state, signalled)}, outcome  # metadata KEPT
    if stale_pid is not None:
        sd._remove_daemon_metadata(root, expected_pid=stale_pid, expected_port=stale_port)
    if outcome == "not_listening":
        return {**base, **_stale_success_fields(False, True)}, outcome
    return _stop_success(dict(base), root, "pid" if outcome == "pid" else "cooperative"), outcome
