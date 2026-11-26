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
    except Exception as exc:  # covered: test_an_unexpected_staleness_error_is_unknown_never_current
        # An unexpected failure is UNKNOWN, never "current" -- a check that cannot run has
        # not passed.
        return _STATUS_UNKNOWN, f"{type(exc).__name__}: {exc}"
    return _STATUS_CURRENT, None


def _collect_repo_merkle_candidates(cache_root: Path) -> tuple[list[Path], list[Path], bool]:
    from tensor_grep.cli import lang_registry, repo_map

    cap = repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT
    walked_all = repo_map._iter_repo_files(cache_root, max_files=cap + 1)
    truncated = len(walked_all) > cap
    walked = walked_all[:cap]
    context_files = [p for p in walked if repo_map._is_repo_context_file(p, cache_root)]
    code_files = [p for p in context_files if lang_registry.spec_for_path(p) is not None]
    return code_files, context_files, truncated


def _unified_cache_status(root: Path) -> tuple[dict[str, Any] | None, str | None, str | None]:
    from tensor_grep.cli.repo_map_fast_cache import resolve_canonical_cache_root
    from tensor_grep.cli.unified_cache import (
        MANIFEST_REL_PATH,
        check_view_freshness,
        compute_generation_merkle_root,
        load_unified_manifest,
    )

    cache_root = resolve_canonical_cache_root(root)
    manifest_path = cache_root / MANIFEST_REL_PATH
    if not manifest_path.exists():
        return None, None, None

    manifest = load_unified_manifest(cache_root)
    if manifest is None:
        unverified_detail = "manifest_v1.json could not be verified or decoded"
        return (
            {
                "root": str(cache_root),
                "status": _STATUS_UNKNOWN,
                "views": {},
                "detail": unverified_detail,
            },
            _STATUS_UNKNOWN,
            unverified_detail,
        )

    code_files, context_files, walked_truncated = _collect_repo_merkle_candidates(cache_root)
    if walked_truncated:
        trunc_detail = "repository file walk exceeded scan_limit"
        return (
            {
                "root": str(cache_root),
                "status": _STATUS_UNKNOWN,
                "views": {},
                "detail": trunc_detail,
            },
            _STATUS_UNKNOWN,
            trunc_detail,
        )
    merkle_code = compute_generation_merkle_root(cache_root, code_files)
    matched_merkle: str | None = merkle_code if merkle_code == manifest.get("merkle_root") else None
    if matched_merkle is None:
        merkle_context = compute_generation_merkle_root(cache_root, context_files)
        if merkle_context == manifest.get("merkle_root"):
            matched_merkle = merkle_context

    raw_views = manifest.get("views")
    views_dict = raw_views if isinstance(raw_views, dict) else {}
    view_names = tuple(str(k) for k in views_dict)
    fresh_map = (
        check_view_freshness(manifest, matched_merkle, view_names, repo_root=cache_root)
        if matched_merkle is not None and view_names
        else dict.fromkeys(view_names, False)
    )
    ready_views = [
        str(name)
        for name, entry in views_dict.items()
        if isinstance(entry, dict) and entry.get("ready") is True
    ]

    status: str
    detail: str | None = None
    if matched_merkle is None:
        status = _STATUS_STALE
        detail = "working tree changed since .tg_cache/manifest_v1.json was recorded"
    elif not ready_views:
        status = _STATUS_STALE
        detail = "no ready views in .tg_cache/manifest_v1.json"
    elif any(not fresh_map.get(name, False) for name in ready_views):
        stale_views = [name for name in ready_views if not fresh_map.get(name, False)]
        status = _STATUS_STALE
        detail = f"stale cache view(s): {', '.join(stale_views)}"
    else:
        status = _STATUS_CURRENT

    cache_info: dict[str, Any] = {
        "root": str(cache_root),
        "merkle_root": manifest.get("merkle_root"),
        "status": status,
        "views": {
            name: {
                "ready": bool(
                    isinstance(views_dict.get(name), dict) and views_dict[name].get("ready") is True
                ),
                "fresh": bool(fresh_map.get(name, False)),
            }
            for name in view_names
        },
    }
    if detail:
        cache_info["detail"] = detail
    return cache_info, status, detail


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
        # Annotated because the two branches disagree: the `except` arm always produces a
        # detail string while `_session_status` returns `str | None` for the current case.
        # Without this, mypy narrows `detail` to `str` from whichever arm it sees first.
        status: str
        detail: str | None
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

    cache_info, cache_status, _ = _unified_cache_status(root)
    if cache_info is not None:
        payload["unified_cache"] = cache_info
        if cache_status == _STATUS_STALE:
            payload["stale_count"] += 1
        elif cache_status == _STATUS_UNKNOWN:
            payload["unknown_count"] += 1

    has_persisted_state = payload["session_count"] > 0 or cache_info is not None
    if not has_persisted_state:
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
        payload["incomplete_reason"] = (
            "cache_state_unreadable"
            if payload["session_count"] == 0 and cache_status == _STATUS_UNKNOWN
            else "session_state_unreadable"
        )
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
    uc = payload.get("unified_cache")
    if isinstance(uc, dict):
        uc_detail = f"  -- {uc['detail']}" if uc.get("detail") else ""
        lines.append(f"  {uc.get('status', 'unknown')!s:<8} unified_cache{uc_detail}")
    lines.append(
        f"sessions={payload['session_count']} stale={payload['stale_count']} "
        f"unknown={payload['unknown_count']}"
    )
    if payload.get("result_incomplete"):
        lines.append(f"INCOMPLETE RESULT ({payload.get('incomplete_reason')})")
        lines.append(str(payload.get("remediation") or ""))
    return "\n".join(lines)
