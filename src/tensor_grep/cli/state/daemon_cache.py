from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from tensor_grep.cli import session_daemon
from tensor_grep.cli import session_daemon_implicit as _implicit_sessions


def _path_cache_key(path: str) -> str:
    resolved = str(session_daemon._resolve_root(Path(path)))
    return resolved.lower() if session_daemon.sys.platform.startswith("win") else resolved


def _request_cache_value(
    request: dict[str, Any],
    name: str,
    default: Any = "",
) -> str:
    value = request.get(name, default)
    if value in (None, ""):
        return ""
    return str(value)


def _session_payload_fingerprint(payload: dict[str, Any]) -> tuple[str, ...]:
    repo_map = cast(dict[str, Any], payload.get("repo_map") or {})
    return (
        str(payload.get("root", "")),
        str(payload.get("created_at", "")),
        str(payload.get("refreshed_at", "")),
        str(len(cast(list[Any], repo_map.get("files", [])))),
        str(len(cast(list[Any], repo_map.get("symbols", [])))),
    )


def _context_edit_plan_response_cache_key(
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    return (
        session_daemon._path_cache_key(path),
        session_id,
        *session_daemon._session_payload_fingerprint(payload),
        str(request.get("query", "")).strip(),
        session_daemon._request_cache_value(request, "max_files", 3),
        session_daemon._request_cache_value(request, "max_sources"),
        session_daemon._request_cache_value(request, "max_tokens"),
        session_daemon._request_cache_value(request, "max_symbols", 5),
        session_daemon._request_cache_value(
            request,
            "max_repo_files",
            session_daemon._DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT,
        ),
    )


def _context_render_response_cache_key(
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    return (
        session_daemon._path_cache_key(path),
        session_id,
        *session_daemon._session_payload_fingerprint(payload),
        str(request.get("query", "")).strip(),
        session_daemon._request_cache_value(request, "max_files", 3),
        session_daemon._request_cache_value(request, "max_sources", 5),
        session_daemon._request_cache_value(request, "max_symbols_per_file", 6),
        session_daemon._request_cache_value(request, "max_render_chars"),
        session_daemon._request_cache_value(request, "max_tokens"),
        session_daemon._request_cache_value(request, "model"),
        session_daemon._request_cache_value(request, "optimize_context", False),
        session_daemon._request_cache_value(request, "render_profile", "full"),
        session_daemon._request_cache_value(request, "profile", False),
        session_daemon._request_cache_value(
            request,
            "max_repo_files",
            session_daemon._DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
        ),
    )


def _symbol_command_response_cache_key(
    command: str,
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    # audit #113: one key function shared by all 5 symbol commands (defs/impact/refs/callers/
    # blast_radius), so `command` MUST be a key field -- otherwise a `callers(foo)` lookup could
    # serve a cached `defs(foo)` response (cross-command response bleed). Completeness of the
    # REST of this tuple is equally load-bearing: a missing field (provider/max_tests/max_depth/
    # max_repo_files) would let two requests that differ only in that field collide on the same
    # cached answer (cross-request response bleed) -- see _symbol_command_response_cache_key's
    # sibling _context_render_response_cache_key above for the same discipline.
    return (
        session_daemon._path_cache_key(path),
        session_id,
        *session_daemon._session_payload_fingerprint(payload),
        command,
        str(request.get("symbol", "")).strip(),
        session_daemon._request_cache_value(request, "provider", "native"),
        session_daemon._request_cache_value(request, "max_tests"),
        session_daemon._request_cache_value(request, "max_depth", 3),
        session_daemon._request_cache_value(
            request,
            "max_repo_files",
            session_daemon._DEFAULT_SESSION_SYMBOL_REPO_MAP_LIMIT,
        ),
    )


def _orient_response_cache_key(
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    # task #108 (Tier-2 daemon capability): `orient` has NO query (it is a whole-repo capsule, unlike
    # every other cacheable command) -- the literal "orient" command-string field below is what
    # keeps this key isolated from every OTHER command's key space (test: orient-key never
    # collides with agent-key), same anti-bleed discipline as _symbol_command_response_cache_key.
    return (
        session_daemon._path_cache_key(path),
        session_id,
        *session_daemon._session_payload_fingerprint(payload),
        "orient",
        session_daemon._request_cache_value(request, "max_tokens", 3000),
        session_daemon._request_cache_value(request, "max_central_files", 10),
        str(tuple(request.get("ignore") or ())),
        session_daemon._request_cache_value(request, "auto_deweight", True),
    )


def _agent_response_cache_key(
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    # task #108 (Tier-2 daemon capability): EVERY output-affecting flag `tg agent` exposes is a key
    # field (query + max_files/max_sources/max_tokens/model/provider/max_repo_files/ignore) -- a
    # missing field here would let two requests differing only in that field collide on the same
    # cached answer, same discipline as _context_render_response_cache_key /
    # _symbol_command_response_cache_key above. `include_blast_radius`/`gpu_device_ids` are
    # deliberately excluded: neither is reachable from a daemon request (see
    # build_agent_capsule_from_map's docstring), so there is nothing to distinguish.
    return (
        session_daemon._path_cache_key(path),
        session_id,
        *session_daemon._session_payload_fingerprint(payload),
        "agent",
        str(request.get("query", "")).strip(),
        session_daemon._request_cache_value(request, "max_files", 3),
        session_daemon._request_cache_value(request, "max_sources", 5),
        session_daemon._request_cache_value(request, "max_tokens", 1200),
        session_daemon._request_cache_value(request, "model"),
        session_daemon._request_cache_value(request, "provider", "native"),
        str(tuple(request.get("ignore") or ())),
        session_daemon._request_cache_value(
            request,
            "max_repo_files",
            session_daemon._DEFAULT_SESSION_AGENT_REPO_MAP_LIMIT,
        ),
    )


def _response_cache_key_for_command(
    command: str,
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...] | None:
    if command == "context_render":
        return session_daemon._context_render_response_cache_key(session_id, path, request, payload)
    if command == "context_edit_plan":
        return session_daemon._context_edit_plan_response_cache_key(
            session_id, path, request, payload
        )
    if command == "orient":
        return session_daemon._orient_response_cache_key(session_id, path, request, payload)
    if command == "agent":
        return session_daemon._agent_response_cache_key(session_id, path, request, payload)
    if command in session_daemon._IMPLICIT_SESSION_SYMBOL_COMMANDS:
        return session_daemon._symbol_command_response_cache_key(
            command, session_id, path, request, payload
        )
    return None


def _session_payload_is_possibly_truncated(payload: dict[str, Any]) -> bool:
    repo_map = payload.get("repo_map")
    candidates: list[Any] = [payload.get("scan_limit")]
    if isinstance(repo_map, dict):
        candidates.append(repo_map.get("scan_limit"))
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("possibly_truncated") is True:
            return True
    return False


def _optional_positive_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(1, int(cast(int | str, value)))
    except (TypeError, ValueError):
        return None


def _implicit_session_max_repo_files(command: str, request: dict[str, Any]) -> int | None:
    requested = session_daemon._optional_positive_int(request.get("max_repo_files"))
    if requested is not None:
        return requested
    if command == "context_render":
        return session_daemon._DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT
    if command == "context_edit_plan":
        return session_daemon._DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT
    if command in session_daemon._IMPLICIT_SESSION_SYMBOL_COMMANDS:
        return session_daemon._DEFAULT_SESSION_SYMBOL_REPO_MAP_LIMIT
    if command == "orient":
        return session_daemon._DEFAULT_SESSION_ORIENT_REPO_MAP_LIMIT
    if command == "agent":
        return session_daemon._DEFAULT_SESSION_AGENT_REPO_MAP_LIMIT
    return None


def _remove_implicit_session_payload(
    path: str, session_id: str, *, deadline_monotonic: float | None = None
) -> None:
    _implicit_sessions.remove_payload(
        session_daemon,
        path,
        session_id,
        deadline_monotonic=deadline_monotonic,
    )


def _implicit_session_id_for_request(
    server: Any,
    *,
    command: str,
    session_id: str,
    path: str,
    request: dict[str, Any],
    deadline_monotonic: float | None = None,
    pin_receipt: list[Any] | None = None,
) -> str:
    return _implicit_sessions.resolve(
        session_daemon,
        server,
        command=command,
        session_id=session_id,
        path=path,
        request=request,
        deadline_monotonic=deadline_monotonic,
        pin_receipt=pin_receipt,
    )


def _release_implicit_session_pin(
    server: Any, pin_receipt: list[Any] | None, *, deadline_monotonic: float | None = None
) -> None:
    _implicit_sessions.release(
        session_daemon,
        server,
        pin_receipt,
        deadline_monotonic=deadline_monotonic,
    )


def _reacquire_missing_implicit_session(
    server: Any,
    *,
    path: str,
    request: dict[str, Any],
    session_id: str,
    pin_receipt: list[Any],
    deadline_monotonic: float | None,
    exc: BaseException,
) -> str | None:
    return _implicit_sessions.reacquire_missing(
        session_daemon,
        server,
        path=path,
        request=request,
        session_id=session_id,
        pin_receipt=pin_receipt,
        deadline_monotonic=deadline_monotonic,
        exc=exc,
    )


def _serve_daemon_response_with_cache(
    *,
    server: Any,
    command: str,
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
    deadline_monotonic: float | None = None,
) -> tuple[dict[str, Any], str]:
    session_request = dict(request)
    if deadline_monotonic is None:
        deadline_monotonic = (
            session_daemon.monotonic() + session_daemon.WARM_DAEMON_DEFAULT_DEADLINE_SECONDS
        )
    if bool(
        session_request.get("refresh_on_stale")
    ) and session_daemon._session_payload_is_possibly_truncated(payload):
        # Truncated snapshots validate included files below; added-file detection would
        # treat files omitted by the cap as stale and prevent response-cache writes.
        session_request["refresh_on_stale"] = False
    response_cache_key = session_daemon._response_cache_key_for_command(
        command, session_id, path, session_request, payload
    )
    if response_cache_key is None:
        return session_daemon.serve_session_request(
            session_id,
            session_request,
            path,
            payload=payload,
            deadline_monotonic=deadline_monotonic,
        ), "bypass"

    # audit #113 trap #1: context_render/context_edit_plan intentionally use
    # detect_added_files=False here (see docs/CONTRACTS.md) so a cache HIT never pays for a
    # directory walk -- new files stay invisible to those two commands until an explicit
    # refresh. The 5 symbol commands (task #108: now also orient/agent, via
    # session_daemon._ADDED_FILE_SENSITIVE_COMMANDS) are a correctness-sensitive code-intelligence surface (a
    # blast-radius/callers answer that silently omits a newly-added call site is a WRONG
    # answer, not just a stale one -- and identically, a new central file changes orient's
    # ranking and a new call site changes agent's call-site evidence), so they must honor the
    # caller's real refresh_on_stale flag here instead of copying the context-command False -- a
    # new call site in a brand-new file (nothing about any already-tracked file changes) must
    # still bust the cache.
    detect_added_files = (
        bool(session_request.get("refresh_on_stale", False))
        if command in session_daemon._ADDED_FILE_SENSITIVE_COMMANDS
        else False
    )
    session_daemon._ensure_session_not_stale(
        payload,
        detect_added_files=detect_added_files,
        deadline_monotonic=deadline_monotonic,
    )
    with server._response_cache_lock:
        cached_response = server.response_cache.get(response_cache_key)
    if cached_response is not None:
        cached_response.pop("serve_response_cache", None)
        return cached_response, "hit"

    if detect_added_files:
        # The gate above already walked for added files and proved `payload` fresh for THIS
        # request -- flip refresh_on_stale off before the miss-path recompute so
        # serve_session_request's own internal _ensure_session_not_stale (session_store.py)
        # does not re-walk the same added-file probe a second time in one request (mirrors the
        # truncated-snapshot flip above). Guarded on detect_added_files (not unconditional):
        # context_render/context_edit_plan never ran the walk above (detect_added_files=False
        # for them, by design), so for THEM the inner call below is the ONLY place a miss
        # discovers an added file -- flipping the flag unconditionally would silently disable
        # that discovery (regression caught by
        # test_session_daemon_refresh_on_added_file_response_is_cached).
        session_request["refresh_on_stale"] = False
    response = session_daemon.serve_session_request(
        session_id, session_request, path, payload=payload, deadline_monotonic=deadline_monotonic
    )
    # #200: a deadline-truncated (`partial=True`) response must NEVER be cached and replayed to a
    # later request that would have had a full budget to finish -- the response cache has no TTL
    # tied to "how much of the original deadline is left", so a cached partial answer would be
    # served indefinitely to every subsequent identical request, silently downgrading them all to
    # the same truncated result even though each gets its own fresh
    # WARM_DAEMON_DEFAULT_DEADLINE_SECONDS budget (session_store.py). Excluding it from the cache
    # entirely is the simplest correct option: a follow-up identical request just recomputes with
    # its own fresh budget instead of replaying a stale truncation.
    #
    # review nit (PR #647): this guard is SHARED by every command this function serves,
    # including the 5 symbol commands (defs/impact/refs/callers/blast_radius), not just the 4
    # #200 targets. It is currently a proven no-op for the symbol commands: `open_session` builds
    # the cached map with no deadline (session_store.open_session), and the warm symbol path
    # never threads a per-request deadline into their builders either (the still-open #390 gap),
    # so a warm symbol response can never be `partial=True` today. If #390 is ever closed by
    # threading a deadline into the symbol commands too, this guard starts applying to them as
    # well -- which is the CORRECT behavior (a truncated symbol answer must not be cached either),
    # but flagging it here so that future change doesn't have to rediscover this coupling.
    if not response.get("partial"):
        with server._response_cache_lock:
            server.response_cache.put(response_cache_key, response)
    return response, "miss"
