"""``tg freshness PATH`` -- has tg's persisted state drifted from the code?

WHY. tg persists sessions whose answers are served without re-parsing. Staleness detection
already exists (``_ensure_session_not_stale``), but it only fires when something *asks* for a
session, and it reports by raising. There was no way for an agent -- or a human -- to ask the
direct question "is what tg would tell me right now actually current?" before trusting an
answer.

THE FLOOR THIS SURFACE OWES. "No persisted state" is NOT "fresh". A freshness command that
returns a clean bill of health for a repo it has never indexed is worse than no command: it
converts absence of evidence into evidence of currency, which is the exact silent-confidence
failure the rest of this codebase's contract exists to prevent. So an empty state set reports
``result_incomplete`` with ``no_persisted_state``, never a confident "current".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_STATUS_CURRENT = "current"
_STATUS_STALE = "stale"
_STATUS_UNKNOWN = "unknown"


def _session_status(payload: dict[str, Any]) -> tuple[str, str | None]:
    """Return ``(status, detail)`` for one persisted session.

    Delegates to the SAME staleness check the serving path uses rather than re-deriving one.
    A second, independently-written staleness rule is a second thing to drift; this surface
    is only useful if it agrees with what tg would actually do when serving.
    """
    from tensor_grep.cli.session_store import SessionStaleError, _ensure_session_not_stale

    try:
        _ensure_session_not_stale(payload, detect_added_files=True)
    except SessionStaleError as exc:
        return _STATUS_STALE, str(exc)
    except Exception as exc:  # pragma: no cover - defensive
        # An unexpected failure is UNKNOWN, never "current" -- a check that cannot run has
        # not passed.
        return _STATUS_UNKNOWN, f"{type(exc).__name__}: {exc}"
    return _STATUS_CURRENT, None


def check_freshness(root: Path) -> dict[str, Any]:
    """Report whether every persisted session for ``root`` still matches the working tree."""
    from tensor_grep.cli.session_store import get_session, list_sessions

    payload: dict[str, Any] = {
        "path": str(root),
        "sessions": [],
        "session_count": 0,
        "stale_count": 0,
        "unknown_count": 0,
    }

    try:
        records = list_sessions(str(root))
    except Exception as exc:
        payload["result_incomplete"] = True
        payload["incomplete_reason"] = "session_index_unreadable"
        payload["remediation"] = (
            f"could not read the session index for {root} ({type(exc).__name__}). "
            "Freshness is UNRESOLVED, not proven current."
        )
        return payload

    for record in records:
        session_id = getattr(record, "session_id", None)
        if session_id is None:
            continue
        try:
            session_payload = get_session(session_id, str(root))
        except Exception as exc:
            status, detail = _STATUS_UNKNOWN, f"{type(exc).__name__}: {exc}"
        else:
            status, detail = _session_status(session_payload)

        entry: dict[str, Any] = {"session_id": session_id, "status": status}
        if detail:
            entry["detail"] = detail
        payload["sessions"].append(entry)
        if status == _STATUS_STALE:
            payload["stale_count"] += 1
        elif status == _STATUS_UNKNOWN:
            payload["unknown_count"] += 1

    payload["session_count"] = len(payload["sessions"])

    if payload["session_count"] == 0:
        # THE floor. Never report a confident "current" for state that does not exist.
        payload["result_incomplete"] = True
        payload["incomplete_reason"] = "no_persisted_state"
        payload["remediation"] = (
            f"tg has no persisted session for {root}, so there is nothing whose freshness "
            "could be checked. This is UNRESOLVED, not a clean bill of health -- open one "
            "with `tg session open PATH` if you want served answers to be staleness-tracked."
        )
    elif payload["unknown_count"]:
        payload["result_incomplete"] = True
        payload["incomplete_reason"] = "session_state_unreadable"
        payload["remediation"] = (
            f"{payload['unknown_count']} session(s) could not be checked; their freshness is "
            "UNRESOLVED, not current."
        )

    return payload


def freshness_command(path: str, *, json_output: bool) -> int:
    """CLI body for ``tg freshness``. Returns the process exit code."""
    import json

    import typer

    payload = check_freshness(Path(path).expanduser().resolve())
    typer.echo(json.dumps(payload, indent=2) if json_output else render_freshness_text(payload))
    # Three-state exit contract: 0 = everything checked and current; 2 = stale or UNRESOLVED.
    stale_or_unresolved = bool(payload.get("result_incomplete")) or payload["stale_count"] > 0
    return 2 if stale_or_unresolved else 0


def render_freshness_text(payload: dict[str, Any]) -> str:
    lines = [f"{payload['path']}"]
    for entry in payload["sessions"]:
        detail = f"  -- {entry['detail']}" if entry.get("detail") else ""
        lines.append(f"  {entry['status']:<8} {entry['session_id']}{detail}")
    lines.append(
        f"sessions={payload['session_count']} stale={payload['stale_count']} "
        f"unknown={payload['unknown_count']}"
    )
    if payload.get("result_incomplete"):
        lines.append(f"INCOMPLETE RESULT ({payload.get('incomplete_reason')})")
        lines.append(str(payload.get("remediation") or ""))
    return "\n".join(lines)
