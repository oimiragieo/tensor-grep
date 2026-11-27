from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO, cast

from tensor_grep.cli import session_store

logger = logging.getLogger(__name__)


def session_context(
    session_id: str,
    query: str,
    path: str = ".",
    *,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = session_store._load_session_payload(
        session_id, path, refresh_on_stale=refresh_on_stale
    )
    context = session_store.build_context_pack_from_map(payload["repo_map"], query)
    context["session_id"] = session_id
    context["routing_reason"] = "session-context"
    return context


def session_context_edit_plan(
    session_id: str,
    query: str,
    path: str = ".",
    *,
    max_files: int = 3,
    max_sources: int | None = None,
    max_tokens: int | None = None,
    max_symbols: int = 5,
    max_repo_files: int | None = session_store._DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    started_at = session_store.monotonic()
    payload = session_store._load_session_payload(
        session_id, path, refresh_on_stale=refresh_on_stale
    )
    loaded_at = session_store.monotonic()
    repo_map = session_store._limited_session_repo_map(
        cast(dict[str, Any], payload["repo_map"]),
        max_repo_files=max_repo_files,
    )
    context = session_store.build_context_edit_plan_from_map(
        repo_map,
        query,
        max_files=max_files,
        max_sources=max_sources,
        max_tokens=max_tokens,
        max_symbols=max_symbols,
    )
    built_at = session_store.monotonic()
    context["session_id"] = session_id
    context["routing_reason"] = "session-context-edit-plan"
    context["session_timing"] = {
        "cache_status": "disk-load",
        "load_session_seconds": max(0.0, loaded_at - started_at),
        "build_edit_plan_seconds": max(0.0, built_at - loaded_at),
        "total_seconds": max(0.0, built_at - started_at),
    }
    return context


def _limited_session_repo_map(
    repo_map: dict[str, Any],
    *,
    max_repo_files: int | None,
) -> dict[str, Any]:
    if max_repo_files is None:
        return repo_map
    return session_store.apply_repo_map_output_limits(
        repo_map, max_files=max(1, int(max_repo_files))
    )


def _serve_request_cache_value(request: dict[str, Any], name: str, default: Any = "") -> str:
    value = request.get(name, default)
    if value in (None, ""):
        return ""
    return str(value)


def _serve_payload_fingerprint(payload: dict[str, Any]) -> tuple[str, ...]:
    repo_map = cast(dict[str, Any], payload.get("repo_map") or {})
    return (
        str(payload.get("root", "")),
        str(payload.get("created_at", "")),
        str(payload.get("refreshed_at", "")),
        str(len(cast(list[Any], repo_map.get("files", [])))),
        str(len(cast(list[Any], repo_map.get("symbols", [])))),
    )


def _serve_response_cache_key(
    *,
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    command = str(request.get("command", "")).strip().lower()
    common = (
        str(session_store._session_root_for_payload(session_id, path)),
        session_id,
        *session_store._serve_payload_fingerprint(payload),
        command,
        str(request.get("query", "")).strip(),
        session_store._serve_request_cache_value(request, "max_files", 3),
        session_store._serve_request_cache_value(request, "max_sources"),
        session_store._serve_request_cache_value(request, "max_tokens"),
        session_store._serve_request_cache_value(request, "max_repo_files"),
    )
    if command == "context_render":
        return (
            *common,
            session_store._serve_request_cache_value(request, "max_symbols_per_file", 6),
            session_store._serve_request_cache_value(request, "max_render_chars"),
            session_store._serve_request_cache_value(request, "model"),
            session_store._serve_request_cache_value(request, "optimize_context", False),
            session_store._serve_request_cache_value(request, "render_profile", "full"),
            session_store._serve_request_cache_value(request, "profile", False),
        )
    return (
        *common,
        session_store._serve_request_cache_value(request, "max_symbols", 5),
    )


def session_context_render(
    session_id: str,
    query: str,
    path: str = ".",
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    max_repo_files: int | None = session_store._DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    started_at = session_store.monotonic()
    payload = session_store._load_session_payload(
        session_id, path, refresh_on_stale=refresh_on_stale
    )
    loaded_at = session_store.monotonic()
    repo_map = session_store._limited_session_repo_map(
        cast(dict[str, Any], payload["repo_map"]),
        max_repo_files=max_repo_files,
    )
    context = session_store.build_context_render_from_map(
        repo_map,
        query,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        max_tokens=max_tokens,
        model=model,
        optimize_context=optimize_context,
        render_profile=render_profile,
        profile=profile,
    )
    built_at = session_store.monotonic()
    context["session_id"] = session_id
    context["routing_reason"] = "session-context-render"
    context["session_timing"] = {
        "cache_status": "disk-load",
        "load_session_seconds": max(0.0, loaded_at - started_at),
        "build_context_render_seconds": max(0.0, built_at - loaded_at),
        "total_seconds": max(0.0, built_at - started_at),
    }
    return context


def session_blast_radius(
    session_id: str,
    symbol: str,
    path: str = ".",
    *,
    max_depth: int = 3,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = session_store._load_session_payload(
        session_id, path, refresh_on_stale=refresh_on_stale
    )
    response = session_store.build_symbol_blast_radius_from_map(
        payload["repo_map"],
        symbol,
        max_depth=max_depth,
    )
    response["session_id"] = session_id
    response["routing_reason"] = "session-blast-radius"
    return response


def session_file_importers(
    session_id: str,
    file_path: str,
    path: str = ".",
    *,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    """Zero-reparse reverse #74 lookup: who imports ``file_path``, served from the session's
    cached repo map (no fresh repo scan, unlike the cold ``build_file_importers``)."""
    payload = session_store._load_session_payload(
        session_id, path, refresh_on_stale=refresh_on_stale
    )
    response = session_store.build_file_importers_from_map(payload["repo_map"], file_path)
    response["session_id"] = session_id
    response["routing_reason"] = "session-file-importers"
    return response


def session_blast_radius_plan(
    session_id: str,
    symbol: str,
    path: str = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    max_repo_files: int | None = session_store._DEFAULT_SESSION_BLAST_RADIUS_PLAN_REPO_MAP_LIMIT,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = session_store._load_session_payload(
        session_id, path, refresh_on_stale=refresh_on_stale
    )
    repo_map = session_store._limited_session_repo_map(
        cast(dict[str, Any], payload["repo_map"]),
        max_repo_files=max_repo_files,
    )
    response = session_store.build_symbol_blast_radius_plan_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_symbols=max_symbols,
    )
    response["session_id"] = session_id
    response["routing_reason"] = "session-blast-radius-plan"
    return response


def session_blast_radius_render(
    session_id: str,
    symbol: str,
    path: str = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = session_store._load_session_payload(
        session_id, path, refresh_on_stale=refresh_on_stale
    )
    response = session_store.build_symbol_blast_radius_render_from_map(
        payload["repo_map"],
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        optimize_context=optimize_context,
        render_profile=render_profile,
    )
    response["session_id"] = session_id
    response["routing_reason"] = "session-blast-radius-render"
    return response


def _serve_session_request_from_payload(
    session_id: str,
    request: dict[str, Any],
    payload: dict[str, Any],
    *,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    repo_map = cast(dict[str, Any], payload["repo_map"])
    command = str(request.get("command", "")).strip().lower()

    if command == "ping":
        return {"version": session_store._SESSION_VERSION, "session_id": session_id, "ok": True}

    if command == "show":
        response = dict(payload)
        response["session_id"] = session_id
        return response

    if deadline_monotonic is None:
        deadline_monotonic = (
            session_store.monotonic() + session_store.WARM_DAEMON_DEFAULT_DEADLINE_SECONDS
        )
    session_store._ensure_session_not_stale(
        payload,
        detect_added_files=bool(request.get("refresh_on_stale", False)),
        deadline_monotonic=deadline_monotonic,
    )

    if command == "repo_map":
        response = dict(repo_map)
        response["session_id"] = session_id
        response["routing_reason"] = "session-repo-map"
        return response

    if command == "context":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("context requests require a non-empty query")
        response = session_store.build_context_pack_from_map(
            repo_map, query, deadline_monotonic=deadline_monotonic
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-context"
        return response

    if command == "context_render":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("context_render requests require a non-empty query")
        max_repo_files = request.get(
            "max_repo_files",
            session_store._DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
        )
        response = session_store.build_context_render_from_map(
            session_store._limited_session_repo_map(
                repo_map,
                max_repo_files=(
                    None if max_repo_files in (None, "") else int(cast(int | str, max_repo_files))
                ),
            ),
            query,
            max_files=int(request.get("max_files", 3)),
            max_sources=int(request.get("max_sources", 5)),
            max_symbols_per_file=int(request.get("max_symbols_per_file", 6)),
            max_render_chars=(
                None
                if request.get("max_render_chars") in (None, "")
                else int(request["max_render_chars"])
            ),
            max_tokens=(
                None if request.get("max_tokens") in (None, "") else int(request["max_tokens"])
            ),
            model=(None if request.get("model") in (None, "") else str(request["model"])),
            optimize_context=bool(request.get("optimize_context", False)),
            render_profile=str(request.get("render_profile", "full")),
            profile=bool(request.get("profile", False)),
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-context-render"
        return response

    if command == "context_edit_plan":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("context_edit_plan requests require a non-empty query")
        max_repo_files = request.get(
            "max_repo_files", session_store._DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT
        )
        scoped_repo_map = session_store._limited_session_repo_map(
            repo_map,
            max_repo_files=(
                None if max_repo_files in (None, "") else int(cast(int | str, max_repo_files))
            ),
        )
        response = session_store.build_context_edit_plan_from_map(
            scoped_repo_map,
            query,
            max_files=int(request.get("max_files", 3)),
            max_sources=(
                None if request.get("max_sources") in (None, "") else int(request["max_sources"])
            ),
            max_tokens=(
                None if request.get("max_tokens") in (None, "") else int(request["max_tokens"])
            ),
            max_symbols=int(request.get("max_symbols", 5)),
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-context-edit-plan"
        return response

    provider = str(request.get("provider", "native"))
    raw_max_tests = request.get("max_tests")
    max_tests = None if raw_max_tests in (None, "") else int(cast(int | str, raw_max_tests))
    if command == "defs":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("defs requests require a non-empty symbol")
        response = session_store.build_symbol_defs_from_map(
            repo_map,
            symbol,
            semantic_provider=provider,
            max_tests=max_tests,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-defs"
        return response

    if command == "impact":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("impact requests require a non-empty symbol")
        response = session_store.build_symbol_impact_from_map(
            repo_map,
            symbol,
            semantic_provider=provider,
            max_tests=max_tests,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-impact"
        return response

    if command == "refs":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("refs requests require a non-empty symbol")
        response = session_store.build_symbol_refs_from_map(
            repo_map,
            symbol,
            semantic_provider=provider,
            max_tests=max_tests,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-refs"
        return response

    if command == "callers":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("callers requests require a non-empty symbol")
        response = session_store.build_symbol_callers_from_map(
            repo_map,
            symbol,
            semantic_provider=provider,
            max_tests=max_tests,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-callers"
        return response

    if command == "file_importers":
        target_file = str(request.get("file", "")).strip()
        if not target_file:
            raise ValueError("file_importers requests require a non-empty file")
        response = session_store.build_file_importers_from_map(
            repo_map, target_file, deadline_monotonic=deadline_monotonic
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-file-importers"
        return response

    if command == "blast_radius":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("blast_radius requests require a non-empty symbol")
        response = session_store.build_symbol_blast_radius_from_map(
            repo_map,
            symbol,
            max_depth=int(request.get("max_depth", 3)),
            semantic_provider=provider,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-blast-radius"
        return response

    if command == "blast_radius_render":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("blast_radius_render requests require a non-empty symbol")
        response = session_store.build_symbol_blast_radius_render_from_map(
            repo_map,
            symbol,
            max_depth=int(request.get("max_depth", 3)),
            max_files=int(request.get("max_files", 3)),
            max_sources=int(request.get("max_sources", 5)),
            max_symbols_per_file=int(request.get("max_symbols_per_file", 6)),
            max_render_chars=(
                None
                if request.get("max_render_chars") in (None, "")
                else int(request["max_render_chars"])
            ),
            optimize_context=bool(request.get("optimize_context", False)),
            render_profile=str(request.get("render_profile", "full")),
            profile=bool(request.get("profile", False)),
            semantic_provider=provider,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-blast-radius-render"
        return response

    if command == "blast_radius_plan":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("blast_radius_plan requests require a non-empty symbol")
        max_repo_files = request.get(
            "max_repo_files",
            session_store._DEFAULT_SESSION_BLAST_RADIUS_PLAN_REPO_MAP_LIMIT,
        )
        response = session_store.build_symbol_blast_radius_plan_from_map(
            session_store._limited_session_repo_map(
                repo_map,
                max_repo_files=(
                    None if max_repo_files in (None, "") else int(cast(int | str, max_repo_files))
                ),
            ),
            symbol,
            max_depth=int(request.get("max_depth", 3)),
            max_files=int(request.get("max_files", 3)),
            max_symbols=int(request.get("max_symbols", 5)),
            semantic_provider=provider,
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-blast-radius-plan"
        return response

    if command == "orient":
        response = session_store.build_orient_capsule_from_map(
            repo_map,
            max_tokens=int(request.get("max_tokens", 3000)),
            max_central_files=int(request.get("max_central_files", 10)),
            ignore=tuple(request.get("ignore") or ()),
            auto_deweight=bool(request.get("auto_deweight", True)),
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-orient"
        return response

    if command == "agent":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("agent requests require a non-empty query")
        raw_max_tokens = request.get("max_tokens", 1200)
        raw_max_repo_files = request.get(
            "max_repo_files", session_store._DEFAULT_SESSION_AGENT_REPO_MAP_LIMIT
        )
        response = session_store.build_agent_capsule_from_map(
            repo_map,
            query,
            max_files=int(request.get("max_files", 3)),
            max_sources=int(request.get("max_sources", 5)),
            max_tokens=(
                None if raw_max_tokens in (None, "") else int(cast(int | str, raw_max_tokens))
            ),
            max_repo_files=(
                None
                if raw_max_repo_files in (None, "")
                else int(cast(int | str, raw_max_repo_files))
            ),
            model=(None if request.get("model") in (None, "") else str(request["model"])),
            semantic_provider=provider,
            ignore=tuple(request.get("ignore") or ()),
            deadline_monotonic=deadline_monotonic,
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-agent"
        return response

    raise ValueError(f"unknown session command: {command or '<empty>'}")


def serve_session_request(
    session_id: str,
    request: dict[str, Any],
    path: str = ".",
    *,
    payload: dict[str, Any] | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    resolved_session_id, resolved_path = session_store._resolve_request_session_target(
        request, session_id, path
    )
    session_payload = (
        payload
        if payload is not None
        else session_store.get_session(resolved_session_id, resolved_path)
    )
    return session_store._serve_session_request_from_payload(
        resolved_session_id,
        request,
        session_payload,
        deadline_monotonic=deadline_monotonic,
    )


def serve_session_stream(
    session_id: str,
    path: str = ".",
    *,
    refresh_on_stale: bool = False,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    request_stream = input_stream or sys.stdin
    response_stream = output_stream or sys.stdout
    request_count = 0
    started_at = session_store.monotonic()
    payload_cache = session_store._SessionServeCache()
    response_cache = session_store._SessionServeResponseCache()

    for raw_line in request_stream:
        line = raw_line.strip()
        if not line:
            continue
        request_count += 1
        request_deadline = (
            session_store.monotonic() + session_store.WARM_DAEMON_DEFAULT_DEADLINE_SECONDS
        )
        request_session_id = session_id
        request_path = path
        response: dict[str, Any]
        try:
            request = cast(dict[str, Any], json.loads(line))
            request_session_id, request_path = session_store._resolve_request_session_target(
                request, session_id, path
            )
            if refresh_on_stale and not bool(request.get("refresh_on_stale", False)):
                request = dict(request)
                request["refresh_on_stale"] = True
            command = str(request.get("command", "")).strip().lower()
            if command == "stats":
                response = {
                    "version": session_store._SESSION_VERSION,
                    "ok": True,
                    "cache_hits": payload_cache.hits,
                    "cache_misses": payload_cache.misses,
                    "refresh_count": payload_cache.refreshes,
                    "root_count": payload_cache.root_count,
                    "session_count": payload_cache.session_count,
                    "sessions": payload_cache.sessions,
                    "cache_size_bytes": payload_cache.size_bytes,
                    "response_cache_hits": response_cache.hits,
                    "response_cache_misses": response_cache.misses,
                    "response_cache_puts": response_cache.puts,
                    "response_cache_entries": response_cache.entry_count,
                    "response_cache_size_bytes": response_cache.size_bytes,
                    "response_cache_max_size_bytes": response_cache.max_size_bytes,
                    "response_cache_oversized_skips": response_cache.oversized_skips,
                    "response_cache_stale_detection": "snapshot_content_sha256",
                    "response_cache_added_file_detection": False,
                    "response_cache_refresh_hint": (
                        "Use tg session refresh or request refresh_on_stale when new files must "
                        "invalidate daemon response-cache hits."
                    ),
                    "uptime_seconds": max(0.0, session_store.monotonic() - started_at),
                    "request_count": request_count,
                }
            elif command == "health":
                payload, cache_status = payload_cache.load_with_status(
                    request_session_id, request_path
                )
                response = session_store._session_health_payload(request_session_id, payload)
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": payload_cache.session_count,
                    "root_count": payload_cache.root_count,
                }
            else:
                payload, cache_status = payload_cache.load_with_status(
                    request_session_id, request_path
                )
                response_cache_status = "bypass"
                cacheable_response_command = command in {
                    "context_edit_plan",
                    "context_render",
                } and not bool(request.get("refresh_on_stale", False))
                if cacheable_response_command:
                    session_store._ensure_session_not_stale(
                        payload,
                        detect_added_files=False,
                        deadline_monotonic=request_deadline,
                    )
                    response_cache_key = session_store._serve_response_cache_key(
                        session_id=request_session_id,
                        path=request_path,
                        request=request,
                        payload=payload,
                    )
                    cached_response = response_cache.get(response_cache_key)
                    if cached_response is not None:
                        response = cached_response
                        response_cache_status = "hit"
                    else:
                        response = session_store.serve_session_request(
                            request_session_id,
                            request,
                            request_path,
                            payload=payload,
                            deadline_monotonic=request_deadline,
                        )
                        response_cache.put(response_cache_key, response)
                        response_cache_status = "miss"
                else:
                    response = session_store.serve_session_request(
                        request_session_id,
                        request,
                        request_path,
                        payload=payload,
                        deadline_monotonic=request_deadline,
                    )
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": payload_cache.session_count,
                    "root_count": payload_cache.root_count,
                }
                if cacheable_response_command:
                    response["serve_response_cache"] = {
                        "status": response_cache_status,
                        "entries": response_cache.entry_count,
                        "hits": response_cache.hits,
                        "misses": response_cache.misses,
                        "size_bytes": response_cache.size_bytes,
                        "max_size_bytes": response_cache.max_size_bytes,
                        "oversized_skips": response_cache.oversized_skips,
                    }
        except session_store.SessionStaleError as exc:
            if refresh_on_stale:
                session_store.refresh_session(
                    request_session_id,
                    request_path,
                    payload_cache=payload_cache,
                    deadline_monotonic=request_deadline,
                )
                payload_cache.record_refresh()
                payload, cache_status = payload_cache.load_with_status(
                    request_session_id, request_path
                )
                response = session_store.serve_session_request(
                    request_session_id,
                    request,
                    request_path,
                    payload=payload,
                    deadline_monotonic=request_deadline,
                )
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": payload_cache.session_count,
                    "root_count": payload_cache.root_count,
                }
            else:
                response = {
                    "version": session_store._SESSION_VERSION,
                    "session_id": request_session_id,
                    "error": {"code": "stale_session", "message": str(exc)},
                }
        except Exception as exc:
            response = {
                "version": session_store._SESSION_VERSION,
                "session_id": request_session_id,
                "error": {"code": "invalid_request", "message": str(exc)},
            }
        response_stream.write(json.dumps(response) + "\n")
        response_stream.flush()

    return request_count
