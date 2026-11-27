from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import typer

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli.investigation_options import (
    _note_late_rerank_degraded as _note_late_rerank_degraded,
)
from tensor_grep.cli.investigation_options import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)

# perf (+10% campaign #6 / F2.4): import the 5 broad-scan-guard constants from the
# zero-dependency `tensor_grep.io.scan_limits` module directly, NOT from
# `tensor_grep.io.directory_scanner` (which does `from tensor_grep.core.config import
# SearchConfig` at its own module level -- SearchConfig transitively pulls in the stdlib
# `dataclasses`/`inspect` chain). This module-level import runs for every full-CLI command
# (--help, scan, test, ast-info, ...); these 5 names are plain frozensets/ints consumed only by
# the module-level broad-scan literals below, never SearchConfig itself, so there is no reason
# to pay for the heavier module merely to read 5 constants from it. `DirectoryScanner` itself is
# still imported lazily, function-local, at each call site that actually walks a tree (unchanged
# by this PR). See `tensor_grep.io.scan_limits`'s module docstring for the full rationale.
# Read this module object through late-bound
# attribute reads. A BARE call to a monkeypatched name resolves through THIS module's
# globals, welding the caller to this file -- move it and the test still passes while
# production runs the unpatched original. `_self.NAME(...)` resolves at CALL time.
# The two branches are load-bearing. At runtime only `sys.modules[__name__]` works (the
# module is mid-import, so importing itself by name would be circular), but that is typed
# `ModuleType`, whose `__getattr__` returns `Any` -- under a single-branch form every
# converted call returns Any and mypy raises `no-any-return` at each concrete-returning
# caller. The TYPE_CHECKING branch never executes; it exists so the checker resolves
# `_self` to this module and keeps every real signature.
# scripts/bare_call_ratchet.py pins the remaining bare-call count and fails if it grows.
# Re-exports from the modules split out of this file on 2026-08-20 (see
# cli/_main_binding.py). EVERY moved name is
# rebound here, not only the ones a scan can prove are read. The test suite patches many of
# them on `main` and the moved code reads them back through `_self`, so the patch target must
# not move with the code -- and tests reach them through local aliases, so 'nothing references
# it' would be a claim about the scan, not about the code. Rebinding all of them keeps
# `main.<name>` exactly the surface it was before the split. Plain assignment rather than
# `from ... import x as x` only because isort expands the aliased form to three lines each.
# --- from cli/ast_scan.py ----------------------------------------------------
# --- from cli/doctor_report.py -----------------------------------------------
# --- from cli/doctor_payload.py ----------------------------------------------
# --- from cli/native_frontdoor.py --------------------------------------------
# --- from cli/windows_launcher.py --------------------------------------------
# backlog #1 : kept numerically in sync with
# repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT (raised 512 -> 2000 for routing accuracy -- a file past
# the old cap never entered the map, so edit-plan/agent/context-render/defs misrouted on repos
# >512 files). This is a SEPARATE literal (not an import) because it is this module's CLI-option
# default, shared across both ROUTING commands (edit-plan/agent/context-render/defs/source) and
# CALLER-SCAN commands (callers/refs/blast-radius/impact/blast-radius-plan). Raising it to 2000
# is safe for the caller-scan commands ONLY because repo_map.CALLER_SCAN_FILE_CEILING bounds
# their actual per-file scan work at 512 regardless of how large this default is -- the
# chokepoint, not a per-command repoint, is what keeps them fast.
from tensor_grep.cli.output_envelope import (
    _echo_symbol_location_rows,
    _maybe_swap_reversed_session_path,
    _resolve_path_and_query,
    _resolve_path_and_symbol,
    _with_schema_version,
)
from tensor_grep.cli.prepare_service import _build_prepare_payload as _build_prepare_payload
from tensor_grep.cli.runtime_paths import _native_tg_version as _native_tg_version
from tensor_grep.cli.runtime_paths import _native_tg_version_matches as _native_tg_version_matches
from tensor_grep.cli.runtime_paths import inspect_native_tg_binary as inspect_native_tg_binary
from tensor_grep.cli.runtime_paths import (
    is_cross_domain_native_binary as is_cross_domain_native_binary,
)

# Re-exported, not used here: a front-door checksum test reads `main.native_frontdoor_metadata_path`.
from tensor_grep.cli.runtime_paths import (
    native_frontdoor_metadata_path as native_frontdoor_metadata_path,
)
from tensor_grep.cli.runtime_paths import resolve_native_tg_binary as resolve_native_tg_binary
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary as resolve_ripgrep_binary
from tensor_grep.cli.runtime_paths import (
    translate_path_for_windows_binary as translate_path_for_windows_binary,
)
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
)

_DEFAULT_AGENT_REPO_SCAN_LIMIT = 2000
_DEFAULT_BLAST_RADIUS_JSON_MAX_CALLERS = 25
_DEFAULT_BLAST_RADIUS_JSON_MAX_FILES = 25
# audit #96 (answer-first payloads): defs/refs/callers/impact's own DEDICATED tests-cap, wired
# on-by-default like blast-radius's --max-callers/--max-files precedent above (not an opt-in-only
# flag -- the audit's "95% payload filler" bug needs a default that actually fixes it).
_DEFAULT_SYMBOL_MAX_TESTS = 25
_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_WINDOWS_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_PROBE_TIMEOUT_ENV = "TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS"
_DOCTOR_SCHEMA_VERSION = 3
_DOCTOR_LSP_SCHEMA_VERSION = 2
_GUARDED_BROAD_SEARCH_ROOTS = {".claude", ".claude/context"}
_BROAD_GENERATED_SCAN_DIR_NAMES = {
    "__pycache__",
    ".claude",
    ".cache",
    ".cargo",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".npm",
    ".nuget",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    "AppData",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
# Single source of truth: `io/directory_scanner.py` (item #154) -- keeps this file and
# `cli/bootstrap.py`'s front-door mirror from drifting out of sync.
_BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD = BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
_BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD = BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
_BROAD_WORKSPACE_PROJECT_MARKERS = BROAD_WORKSPACE_PROJECT_MARKERS
_GUARDED_BROAD_ROOT_RG_GLOBS = (
    "!context/**",
    "!**/context/**",
    "!node_modules/**",
    "!**/node_modules/**",
    "!__pycache__/**",
    "!**/__pycache__/**",
    "!dist/**",
    "!**/dist/**",
    "!build/**",
    "!**/build/**",
)


def session_open(
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    max_repo_files: int | None = typer.Option(
        512,
        "--max-repo-files",
        min=1,
        help=(
            "Maximum files scanned into the initial session repo map. "
            "Defaults to the agent-safe 512-file cap."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Create a cached repo-map session for repeated edit loops."""
    from tensor_grep.cli.session_store import open_session

    try:
        payload = open_session(path, max_repo_files=max_repo_files)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload.__dict__, version=1), indent=2))
        return

    # LEADING (task #329): `files=`/`symbols=` are the numbers the cap qualifies, and a session is
    # opened once and then trusted for its whole lifetime -- a caveat read after the counts is a
    # caveat read after the decision to trust them.
    if isinstance(payload.scan_limit, dict) and payload.scan_limit.get("possibly_truncated"):
        typer.echo(
            "Session repo map is capped; reopen with a larger --max-repo-files for full coverage."
        )
    typer.echo(
        f"Opened session {payload.session_id} "
        f"(files={payload.file_count}, symbols={payload.symbol_count})"
    )


def session_daemon_start(
    path: str = typer.Argument(".", help="File or directory rooted at the daemon scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Start or reuse a warm localhost session daemon for the current root."""
    from tensor_grep.cli.session_daemon import start_session_daemon

    try:
        payload = start_session_daemon(path)
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps({
                    "version": 1,
                    "schema_version": 1,
                    "ok": False,
                    "error": {
                        "reason_code": getattr(exc, "reason_code", "daemon_start_failed"),
                        "detail": getattr(exc, "detail", str(exc)),
                    },
                })
            )
            raise typer.Exit(1) from exc
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(
        f"Session daemon running on {payload['host']}:{payload['port']} pid={payload['pid']}"
    )
    if payload.get("response_cache_scope"):
        typer.echo(f"response_cache_scope={payload['response_cache_scope']}")


def session_daemon_status(
    path: str = typer.Argument(".", help="File or directory rooted at the daemon scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Show daemon status for the current root."""
    from tensor_grep.cli.session_daemon_stop_cli import run_session_daemon_status

    raise typer.Exit(run_session_daemon_status(path, json_output, _with_schema_version))


def session_daemon_stop(
    path: str = typer.Argument(".", help="File or directory rooted at the daemon scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Stop the warm localhost session daemon for the current root."""
    from tensor_grep.cli.session_daemon_stop_cli import run_session_daemon_stop

    raise typer.Exit(run_session_daemon_stop(path, json_output, _with_schema_version))


def session_list(
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """List cached sessions for the current root, with nearby-scope discovery."""
    from tensor_grep.cli.session_store import list_sessions_with_discovery

    try:
        session_records, scope_root, discovered = list_sessions_with_discovery(path)
        records = [record.__dict__ for record in session_records]
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "version": 1,
                    "schema_version": 1,
                    "root": scope_root,
                    "discovered": discovered,
                    "sessions": records,
                },
                indent=2,
            )
        )
        return

    if not records:
        typer.echo("No sessions found.")
        return

    if discovered:
        typer.echo(f"Discovered sessions outside current scope under {scope_root}.")

    for record in records:
        typer.echo(
            f"{record['session_id']}  {record['created_at']}  "
            f"files={record['file_count']} symbols={record['symbol_count']}"
        )


def session_show(
    session_id: str = typer.Argument(..., help="Session ID to inspect."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Show the cached repo-map payload for a session."""
    from tensor_grep.cli.session_store import get_session

    try:
        session_id, path = _maybe_swap_reversed_session_path(
            session_id=session_id,
            path=path,
            command_name="show",
        )
        payload = get_session(session_id, path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    repo_map = cast(dict[str, Any], payload.get("repo_map") or {})
    file_count = len(cast(list[Any], repo_map.get("files", [])))
    symbol_count = len(cast(list[Any], repo_map.get("symbols", [])))

    if json_output:
        # Additive parity with `session open --json` / `session list --json`, which both
        # surface top-level file_count/symbol_count (audit M8). Only fill them when absent
        # so a payload that already carries them is left untouched.
        json_payload = dict(payload)
        json_payload.setdefault("file_count", file_count)
        json_payload.setdefault("symbol_count", symbol_count)
        typer.echo(json.dumps(_with_schema_version(json_payload, version=1), indent=2))
        return

    typer.echo(f"Session {payload['session_id']} for {payload['root']}")
    typer.echo(f"files={file_count} symbols={symbol_count}")


def session_refresh(
    session_id: str = typer.Argument(..., help="Session ID to refresh."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Refresh a cached session after file changes."""
    from tensor_grep.cli.session_store import refresh_session

    try:
        payload = refresh_session(session_id, path)
    except FileNotFoundError as exc:
        message = str(exc)
        if not message.startswith("Session not found:"):
            typer.echo(message, err=True)
            raise typer.Exit(1) from exc
        hint = "pass SESSION_ID (see tg session list PATH --json)"
        if json_output:
            typer.echo(
                json.dumps(
                    {"error": {"code": "session_not_found", "message": message, "hint": hint}},
                    indent=2,
                )
            )
        else:
            typer.echo(message, err=True)
            typer.echo(f"Hint: {hint}", err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload.__dict__, version=1), indent=2))
        return

    typer.echo(
        f"Refreshed session {payload.session_id} "
        f"(files={payload.file_count}, symbols={payload.symbol_count})"
    )


def session_context_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    query_arg: str | None = typer.Argument(
        None, help="Query text used to rank relevant repo context."
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    max_tokens: int = typer.Option(
        # Bound the session context pack for prompt injection, matching the standalone `context`
        # command (dogfood 1.27.0: `session context --daemon` was UNBOUNDED at ~557KB / 384 files
        # while standalone capped to ~84KB — a 6x payload bump on the daemon surface agents use for
        # speed). 0 = unbounded opt-out. Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS.
        16000,
        "--max-tokens",
        min=0,
        help="Bound the context pack to ~N tokens for prompt injection (0 = unbounded).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a context pack derived from a cached session."""
    from tensor_grep.cli.repo_map import _apply_context_token_budget
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_context

    try:
        session_id, path = _maybe_swap_reversed_session_path(
            session_id=session_id,
            path=path,
            command_name="context",
        )
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="session context",
        )
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "context",
                    "session_id": session_id,
                    "path": resolved_path,
                    "query": resolved_query,
                    "refresh_on_stale": refresh_on_stale,
                    "max_tokens": max_tokens,
                },
            )
        else:
            payload = session_context(
                session_id,
                resolved_query,
                resolved_path,
                refresh_on_stale=refresh_on_stale,
            )
        # Bound the pack for prompt injection on BOTH the direct and daemon paths (the daemon still
        # returns the full pack today; this guarantees the agent-facing payload is capped). 0 =
        # unbounded. The budget records token_budget honestly and never orphans a symbol.
        payload = _apply_context_token_budget(payload, max_tokens)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(f"Session context for {payload['session_id']}")
    typer.echo(f"query={payload['query']}")
    typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")


def session_context_render_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    query_arg: str | None = typer.Argument(
        None, help="Query text used to rank and render repo context."
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the render bundle."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum cached repo files to score before rendering warm session context.",
    ),
    max_sources: int = typer.Option(
        5, "--max-sources", min=1, help="Maximum exact source blocks to include."
    ),
    max_symbols_per_file: int = typer.Option(
        6, "--max-symbols-per-file", min=1, help="Maximum summary symbols to include per file."
    ),
    max_render_chars: int | None = typer.Option(
        None, "--max-render-chars", min=1, help="Maximum characters to emit in rendered_context."
    ),
    max_tokens: int = typer.Option(
        # Bound a prompt-ready render bundle by default, mirroring the `context` command (dogfood
        # 1.23.0: context-render defaulted to ~800KB, too big for prompt injection). 0 = unbounded;
        # downstream normalizes <=0 -> None (repo_map.py _normalize / _apply_context_token_budget).
        16000,
        "--max-tokens",
        min=0,
        help="Bound the rendered_context to ~N tokens for prompt injection (0 = unbounded).",
    ),
    model: str | None = typer.Option(
        None, "--model", help="Future tokenizer model selector; currently accepted but ignored."
    ),
    optimize_context: bool = typer.Option(
        False,
        "--optimize-context",
        help="Strip blank lines and comment-only lines from rendered source blocks.",
    ),
    render_profile: str | None = typer.Option(
        None,
        "--render-profile",
        help="Render profile: full, compact, llm, or focused. Defaults to llm for JSON and full for text.",
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready render bundle derived from a cached session."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import SessionStaleError, session_context_render

    try:
        session_id, path = _maybe_swap_reversed_session_path(
            session_id=session_id,
            path=path,
            command_name="context-render",
        )
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="session context-render",
        )
        resolved_render_profile = render_profile or ("llm" if json_output else "full")
        resolved_optimize_context = optimize_context or (json_output and render_profile is None)
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "context_render",
                    "session_id": session_id,
                    "path": resolved_path,
                    "query": resolved_query,
                    "max_files": max_files,
                    "max_repo_files": max_repo_files,
                    "max_sources": max_sources,
                    "max_symbols_per_file": max_symbols_per_file,
                    "max_render_chars": max_render_chars,
                    "max_tokens": max_tokens,
                    "model": model,
                    "optimize_context": resolved_optimize_context,
                    "render_profile": resolved_render_profile,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_context_render(
                session_id,
                resolved_query,
                resolved_path,
                max_files=max_files,
                max_repo_files=max_repo_files,
                max_sources=max_sources,
                max_symbols_per_file=max_symbols_per_file,
                max_render_chars=max_render_chars,
                max_tokens=max_tokens,
                model=model,
                optimize_context=resolved_optimize_context,
                render_profile=resolved_render_profile,
                refresh_on_stale=refresh_on_stale,
            )
    except SessionStaleError as exc:
        error_payload = {
            "version": 1,
            "schema_version": 1,
            "session_id": session_id,
            "error": {"code": "invalid_input", "message": str(exc)},
        }
        typer.echo(json.dumps(error_payload, indent=2))
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(payload["rendered_context"])


def session_edit_plan_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    query_arg: str | None = typer.Argument(None, help="Query text used to rank edit targets."),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the plan."
    ),
    max_sources: int | None = typer.Option(
        None,
        "--max-sources",
        min=1,
        help="Maximum related source/span records to retain in the plan.",
    ),
    max_tokens: int | None = typer.Option(
        None,
        "--max-tokens",
        min=1,
        help="Accepted for agent command-surface parity; edit-plan emits no rendered source text.",
    ),
    max_symbols: int = typer.Option(
        5, "--max-symbols", min=1, help="Maximum ranked symbols to retain in the plan payload."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum cached repo files to score before ranking warm edit-plan targets.",
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a cached-session edit-planning bundle without rendered source text."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_context_edit_plan

    try:
        session_id, path = _maybe_swap_reversed_session_path(
            session_id=session_id,
            path=path,
            command_name="edit-plan",
        )
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="session edit-plan",
        )
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "context_edit_plan",
                    "session_id": session_id,
                    "path": resolved_path,
                    "query": resolved_query,
                    "max_files": max_files,
                    "max_sources": max_sources,
                    "max_tokens": max_tokens,
                    "max_symbols": max_symbols,
                    "max_repo_files": max_repo_files,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_context_edit_plan(
                session_id,
                resolved_query,
                resolved_path,
                max_files=max_files,
                max_sources=max_sources,
                max_tokens=max_tokens,
                max_symbols=max_symbols,
                max_repo_files=max_repo_files,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(f"Session edit plan for {payload['session_id']}")
    typer.echo(f"query={payload['query']}")
    typer.echo(
        f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
    )


def session_blast_radius_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a cached-session blast radius for a symbol."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_blast_radius

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="session blast-radius",
        )
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "blast_radius",
                    "session_id": session_id,
                    "path": resolved_path,
                    "symbol": resolved_symbol,
                    "max_depth": max_depth,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_blast_radius(
                session_id,
                resolved_symbol,
                resolved_path,
                max_depth=max_depth,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(payload["rendered_caller_tree"])


def session_importers_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    file: str = typer.Argument(..., help="File to find importers of."),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a cached-session (zero-reparse) list of the files that import FILE."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_file_importers

    try:
        if daemon:
            payload = request_session_daemon(
                ".",
                {
                    "command": "file_importers",
                    "session_id": session_id,
                    "path": ".",
                    "file": file,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_file_importers(
                session_id,
                file,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(f"Importers of {payload['file']}")
    typer.echo(f"importers={payload['importer_count']} files={len(payload['importer_files'])}")
    _echo_symbol_location_rows(payload["importers"])


def session_blast_radius_render_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the render bundle."
    ),
    max_sources: int = typer.Option(
        5, "--max-sources", min=1, help="Maximum exact source blocks to include."
    ),
    max_symbols_per_file: int = typer.Option(
        6, "--max-symbols-per-file", min=1, help="Maximum summary symbols to include per file."
    ),
    max_render_chars: int | None = typer.Option(
        None, "--max-render-chars", min=1, help="Maximum characters to emit in rendered_context."
    ),
    optimize_context: bool = typer.Option(
        False,
        "--optimize-context",
        help="Strip blank lines and comment-only lines from rendered source blocks.",
    ),
    render_profile: str = typer.Option(
        "full",
        "--render-profile",
        help="Render profile: full, compact, llm, or focused.",
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready cached-session blast radius bundle."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_blast_radius_render

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="session blast-radius-render",
        )
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "blast_radius_render",
                    "session_id": session_id,
                    "path": resolved_path,
                    "symbol": resolved_symbol,
                    "max_depth": max_depth,
                    "max_files": max_files,
                    "max_sources": max_sources,
                    "max_symbols_per_file": max_symbols_per_file,
                    "max_render_chars": max_render_chars,
                    "optimize_context": optimize_context,
                    "render_profile": render_profile,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_blast_radius_render(
                session_id,
                resolved_symbol,
                resolved_path,
                max_depth=max_depth,
                max_files=max_files,
                max_sources=max_sources,
                max_symbols_per_file=max_symbols_per_file,
                max_render_chars=max_render_chars,
                optimize_context=optimize_context,
                render_profile=render_profile,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(payload["rendered_context"])


def session_blast_radius_plan_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the plan."
    ),
    max_symbols: int = typer.Option(
        5, "--max-symbols", min=1, help="Maximum ranked symbols to retain in the plan payload."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum cached repo files to score before building the warm blast-radius plan.",
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a cached-session blast-radius planning bundle without rendered source text."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_blast_radius_plan

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="session blast-radius-plan",
        )
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "blast_radius_plan",
                    "session_id": session_id,
                    "path": resolved_path,
                    "symbol": resolved_symbol,
                    "max_depth": max_depth,
                    "max_files": max_files,
                    "max_symbols": max_symbols,
                    "max_repo_files": max_repo_files,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_blast_radius_plan(
                session_id,
                resolved_symbol,
                resolved_path,
                max_depth=max_depth,
                max_files=max_files,
                max_symbols=max_symbols,
                max_repo_files=max_repo_files,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(f"Session blast radius plan for {payload['session_id']}")
    typer.echo(f"symbol={payload['symbol']}")
    typer.echo(
        f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
    )


def session_serve(
    session_id: str = typer.Argument(..., help="Session ID to serve from cache."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    jsonl: bool = typer.Option(
        True,
        "--jsonl/--no-jsonl",
        help="Read newline-delimited JSON requests from stdin and emit JSON responses.",
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
) -> None:
    """Serve repeated repo-map and symbol requests from a cached session."""
    from tensor_grep.cli.session_store import serve_session_stream

    if not jsonl:
        typer.echo("session serve currently requires --jsonl mode", err=True)
        raise typer.Exit(2)

    try:
        serve_session_stream(session_id, path, refresh_on_stale=refresh_on_stale)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def checkpoint_create(
    path: str = typer.Argument(".", help="File or directory rooted at the checkpoint scope."),
    paths: list[str] | None = typer.Option(
        None,
        "--paths",
        help="File or subdirectory to include (repeatable: --paths a --paths b); scopes create + undo.",
    ),
    label: str | None = typer.Option(None, "--label", help="Optional label for this checkpoint."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Create a checkpoint for the current editable tree."""
    from tensor_grep.cli.checkpoint_store import create_checkpoint

    try:
        payload = create_checkpoint(path, paths=paths, label=label)
    except (ValueError, FileNotFoundError) as exc:
        code = "path_not_found" if isinstance(exc, FileNotFoundError) else "invalid_input"
        if json_output:
            typer.echo(json.dumps({"error": {"code": code, "message": str(exc)}}, indent=2))
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps({"error": {"code": "create_failed", "message": str(exc)}}, indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload.__dict__, version=1), indent=2))
        return

    label_suffix = f" label={payload.label!r}" if payload.label is not None else ""
    typer.echo(
        f"Created checkpoint {payload.checkpoint_id} ({payload.mode}, files={payload.file_count})"
        f"{label_suffix}"
    )
    typer.echo(f"Undo command: {payload.undo_command}")


def checkpoint_list(
    path: str = typer.Argument(".", help="File or directory rooted at the checkpoint scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    discover: bool = typer.Option(
        False,
        "--discover",
        help=(
            "Discover bounded child checkpoint scopes under PATH instead of listing one detected "
            "scope. Generated/cache roots are skipped except artifacts checkpoint scopes."
        ),
    ),
    discover_full: bool = typer.Option(
        False,
        "--discover-full",
        help=(
            "Exhaustively discover checkpoint scopes under PATH, including generated/cache roots. "
            "May be slow on broad workspaces."
        ),
    ),
) -> None:
    """List available checkpoints."""
    from tensor_grep.cli.checkpoint_store import (
        describe_checkpoint_scope,
        discover_checkpoint_scopes_result,
        discover_nearby_checkpoint_scopes,
    )

    def _scope_payloads(scopes: list[Any]) -> tuple[list[dict[str, Any]], int]:
        scope_payloads = [
            {
                "root": scope.root,
                "mode": scope.mode,
                "checkpoint_count": scope.checkpoint_count,
                "checkpoints": [record.__dict__ for record in scope.checkpoints],
            }
            for scope in scopes
        ]
        checkpoint_count = sum(
            int(cast(int, scope_payload["checkpoint_count"])) for scope_payload in scope_payloads
        )
        return scope_payloads, checkpoint_count

    def _discovered_payloads(*, full: bool = False) -> tuple[list[dict[str, Any]], int, bool]:
        result = discover_checkpoint_scopes_result(path, full=full)
        scope_payloads, checkpoint_count = _scope_payloads(result.scopes)
        return scope_payloads, checkpoint_count, result.truncated

    def _nearby_payloads() -> tuple[list[dict[str, Any]], int]:
        return _scope_payloads(discover_nearby_checkpoint_scopes(path))

    def _emit_discovered(
        scope_payloads: list[dict[str, Any]],
        checkpoint_count: int,
        *,
        auto_discovered: bool,
        truncated: bool = False,
    ) -> None:
        if json_output:
            payload = {
                "version": 1,
                "schema_version": 1,
                "path": str(Path(path).expanduser().resolve()),
                "checkpoint_count": checkpoint_count,
                "discovered_scopes": scope_payloads,
            }
            if auto_discovered:
                payload["auto_discovered"] = True
            if truncated:
                payload["truncated"] = True
                payload["warning"] = "walk truncated; use --discover-full to override"
            typer.echo(json.dumps(payload, indent=2))
            return

        if truncated:
            typer.echo("walk truncated; use --discover-full to override", err=True)
        if not scope_payloads:
            typer.echo(f"No checkpoint scopes found under {Path(path).expanduser().resolve()}.")
            return

        prefix = "Auto-discovered" if auto_discovered else "Discovered"
        typer.echo(
            f"{prefix} {checkpoint_count} checkpoint(s) across {len(scope_payloads)} scope(s)."
        )
        for scope_payload in scope_payloads:
            typer.echo(
                f"Checkpoint root: {scope_payload['root']} "
                f"({scope_payload['mode']}, count={scope_payload['checkpoint_count']})"
            )
            checkpoint_records = cast(list[dict[str, object]], scope_payload["checkpoints"])
            for record in checkpoint_records:
                label_suffix = f" label={record['label']!r}" if record.get("label") else ""
                typer.echo(
                    f"  {record['checkpoint_id']}  {record['mode']}  "
                    f"{record['created_at']}  files={record['file_count']}{label_suffix}"
                )

    try:
        if discover and discover_full:
            typer.echo("Use either --discover or --discover-full, not both.", err=True)
            raise typer.Exit(1)
        if discover or discover_full:
            scope_payloads, checkpoint_count, truncated = _discovered_payloads(full=discover_full)
            _emit_discovered(
                scope_payloads,
                checkpoint_count,
                auto_discovered=False,
                truncated=truncated,
            )
            return

        scope_result = describe_checkpoint_scope(path)
        records = [record.__dict__ for record in scope_result.checkpoints]
        if not records:
            scope_payloads, checkpoint_count = _nearby_payloads()
            if scope_payloads:
                _emit_discovered(scope_payloads, checkpoint_count, auto_discovered=True)
                return
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "version": 1,
                    "schema_version": 1,
                    "root": scope_result.root,
                    "mode": scope_result.mode,
                    "checkpoint_count": scope_result.checkpoint_count,
                    "checkpoints": records,
                },
                indent=2,
            )
        )
        return

    if not records:
        typer.echo(f"Checkpoint root: {scope_result.root} ({scope_result.mode})")
        typer.echo("No checkpoints found under this scope.")
        typer.echo("Use `tg checkpoint list PATH --discover` to search child scopes explicitly.")
        return

    typer.echo(
        f"Checkpoint root: {scope_result.root} "
        f"({scope_result.mode}, count={scope_result.checkpoint_count})"
    )
    for record in records:
        label_suffix = f" label={record['label']!r}" if record.get("label") else ""
        typer.echo(
            f"{record['checkpoint_id']}  {record['mode']}  "
            f"{record['created_at']}  files={record['file_count']}{label_suffix}"
        )


def checkpoint_undo(
    checkpoint_id: str | None = typer.Argument(
        None,
        help="Checkpoint ID to restore, or omit when using --last.",
    ),
    path: str = typer.Argument(".", help="File or directory rooted at the checkpoint scope."),
    last: bool = typer.Option(False, "--last", help="Restore the newest checkpoint in scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Restore a checkpoint."""
    from tensor_grep.cli.checkpoint_store import (
        CheckpointCorruptError,
        CheckpointNotFoundError,
        CheckpointUndoUnsafeError,
        resolve_latest_checkpoint,
        undo_checkpoint,
    )

    try:
        if last:
            if checkpoint_id is not None and path != ".":
                typer.echo("Use either a checkpoint id or --last, not both.", err=True)
                raise typer.Exit(1)
            latest_path = path
            if checkpoint_id is not None:
                candidate = Path(checkpoint_id).expanduser()
                if not candidate.exists() and checkpoint_id.startswith("ckpt-"):
                    typer.echo("Use either a checkpoint id or --last, not both.", err=True)
                    raise typer.Exit(1)
                latest_path = checkpoint_id
            latest = resolve_latest_checkpoint(latest_path)
            payload = undo_checkpoint(latest.checkpoint_id, latest.root)
        else:
            if checkpoint_id is None:
                typer.echo("Checkpoint id is required unless --last is provided.", err=True)
                raise typer.Exit(1)
            payload = undo_checkpoint(checkpoint_id, path)
    except Exception as exc:
        message = str(exc)
        # A downstream FileNotFoundError does not establish that the checkpoint was absent.
        error_code = "undo_failed"
        if isinstance(exc, CheckpointNotFoundError):
            error_code = "checkpoint_not_found"
        elif isinstance(exc, CheckpointCorruptError):
            error_code = "checkpoint_corrupt"
        elif isinstance(exc, CheckpointUndoUnsafeError):
            error_code = "undo_unsafe"
        if not last and checkpoint_id is not None:
            candidate = Path(checkpoint_id).expanduser()
            if candidate.exists():
                message = (
                    f"{message}. The first positional argument is parsed as CHECKPOINT_ID; "
                    f"to restore the newest checkpoint for this path, use "
                    f"`tg checkpoint undo --last {checkpoint_id}`."
                )
        if json_output:
            typer.echo(
                json.dumps(
                    _with_schema_version(
                        {
                            "ok": False,
                            "error": error_code,
                            "detail": message,
                            "checkpoint_id": checkpoint_id,
                            "path": path,
                        },
                        version=1,
                    ),
                    indent=2,
                )
            )
            raise typer.Exit(1) from exc
        typer.echo(message, err=True)
        raise typer.Exit(1) from exc

    if json_output:
        # Task #308: `diverged_paths` is ADDITIVE-CONDITIONAL. Emitting `[]` on every undo would
        # change the payload for the ordinary case and teach readers to skip the key -- the same
        # reasoning that keeps `unreadable_paths`/`deadline_limit` absent when they have nothing
        # to say. Present means "these paths had post-checkpoint edits that undo discarded".
        undo_payload = dict(payload.__dict__)
        for _conditional in ("diverged_paths", "divergence_unchecked_paths"):
            if not undo_payload.get(_conditional):
                undo_payload.pop(_conditional, None)
        typer.echo(json.dumps(_with_schema_version(undo_payload, version=1), indent=2))
        return

    typer.echo(
        f"Restored checkpoint {payload.checkpoint_id} "
        f"({payload.mode}, restored_files={payload.restored_files}, removed_paths={payload.removed_paths})"
    )
    if payload.divergence_unchecked_paths:
        # Distinct line from the one below on purpose: "could not decide" is not "decided it was
        # unchanged", and collapsing them would recreate the ambiguity this field exists to remove.
        typer.echo(
            f"Could not check {len(payload.divergence_unchecked_paths)} path(s) for "
            "post-checkpoint edits (unreadable); their status is unknown, not clean.",
            err=True,
        )
    if payload.diverged_paths:
        shown = ", ".join(payload.diverged_paths[:5])
        more = (
            f" (+{len(payload.diverged_paths) - 5} more)" if len(payload.diverged_paths) > 5 else ""
        )
        typer.echo(
            f"Discarded post-checkpoint edits to {len(payload.diverged_paths)} file(s): "
            f"{shown}{more}",
            err=True,
        )


def freshness(
    path: str = typer.Argument(".", help="Repository root to check."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Report whether tg's persisted state for PATH has drifted from the code."""
    from tensor_grep.cli.freshness import freshness_command

    raise typer.Exit(code=freshness_command(path, json_output=json_output))


def coverage(
    ctx: typer.Context,
    path: str = typer.Argument(".", help="Repository root to inspect."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Report indexed, stale, and unparsed files without changing repository state."""
    from tensor_grep.cli.coverage import coverage_command

    if ctx.args:
        typer.echo(f"coverage: unknown option or argument: {ctx.args[0]}", err=True)
        raise typer.Exit(code=1)
    raise typer.Exit(code=coverage_command(path, json_output=json_output))


def register(
    app: typer.Typer,
    checkpoint_app: typer.Typer,
    session_app: typer.Typer,
    session_daemon_app: typer.Typer,
) -> None:
    session_app.command("open")(session_open)
    session_daemon_app.command("start")(session_daemon_start)
    session_daemon_app.command("status")(session_daemon_status)
    session_daemon_app.command("stop")(session_daemon_stop)
    session_app.command("list")(session_list)
    session_app.command("show")(session_show)
    session_app.command("refresh")(session_refresh)
    session_app.command("context")(session_context_cmd)
    session_app.command("context-render")(session_context_render_cmd)
    session_app.command("edit-plan")(session_edit_plan_cmd)
    session_app.command("blast-radius")(session_blast_radius_cmd)
    session_app.command("importers")(session_importers_cmd)
    session_app.command("blast-radius-render")(session_blast_radius_render_cmd)
    session_app.command("blast-radius-plan")(session_blast_radius_plan_cmd)
    session_app.command("serve")(session_serve)
    checkpoint_app.command("create")(checkpoint_create)
    checkpoint_app.command("list")(checkpoint_list)
    checkpoint_app.command("undo")(checkpoint_undo)
    app.command(name="freshness")(freshness)
    app.command(
        name="coverage", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
    )(coverage)
