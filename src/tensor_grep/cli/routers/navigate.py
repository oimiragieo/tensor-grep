from __future__ import annotations

from typing import Any

import typer

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import compact_payloads as _compact_payloads
from tensor_grep.cli import detail_tier as _detail_tier
from tensor_grep.cli._main_binding import _self
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
    _annotate_result_completeness,
    _apply_defs_class_filter,
    _attach_symbol_omissions,
    _completeness_caveat_lines,
    _daemon_blast_radius_no_match_is_unreliable,
    _deadline_option,
    _echo_symbol_location_rows,
    _emit_symbol_command_result,
    _render_blast_radius_mermaid,
    _resolve_path_and_symbol,
    _safe_stdout_line,
    _scan_incomplete,
    _symbol_not_found_claim,
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
from tensor_grep.cli.symbol_output import source_text_lines
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


def defs(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    class_filter: str | None = typer.Option(
        None,
        "--class",
        help=(
            "Only return definitions whose enclosing class matches TEXT "
            "(case-insensitive). Disambiguates common method names like 'search'."
        ),
    ),
    max_tests: int | None = typer.Option(
        _DEFAULT_SYMBOL_MAX_TESTS,
        "--max-tests",
        min=1,
        help="Maximum relevant test files to include in output; raise for full coverage.",
    ),
    max_tokens: int = typer.Option(
        # Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS (literal keeps the heavy repo_map import
        # lazy). Answer-first: secondary fields (tests/related_paths) are trimmed before
        # `definitions` itself. 0 = unbounded opt-out.
        16000,
        "--max-tokens",
        min=0,
        help="Approximate maximum payload size in tokens (0 = unbounded).",
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return partial:true JSON with whatever was found so far, instead of running unbounded."
    ),
    detail: str = _detail_tier.detail_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return exact definition locations for a symbol."""
    from tensor_grep.cli.repo_map import _apply_symbol_token_budget, build_symbol_defs

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="defs",
        )
        # task #94 Part A Tier-1: default-OFF warm-daemon fast path. Fails open to the cold
        # build_symbol_defs(...) call below on any miss/error -- see
        # _maybe_symbol_command_via_running_daemon's docstring for the full contract.
        # CLI consistency fix : `--deadline` used to be undefined on `tg defs`
        # (Click "No such option" exit-2) even though its true siblings refs/callers/impact/
        # blast-radius already had it -- mirrors their exact shape (deadline defaults to None
        # already, no --no-deadline companion) and their daemon gate (skip the warm fast path
        # entirely when a --deadline was requested; a warm session's cached repo_map cannot honor
        # a fresh per-request scan deadline).
        payload = (
            _self._maybe_symbol_command_via_running_daemon(
                command="defs",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
                max_tests=max_tests,
            )
            if deadline is None
            else None
        )
        if payload is None:
            payload = build_symbol_defs(
                resolved_symbol,
                resolved_path,
                semantic_provider=provider,
                max_repo_files=max_repo_files,
                max_tests=max_tests,
                deadline_seconds=deadline,
            )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    if class_filter is not None:
        _apply_defs_class_filter(payload, class_filter)

    payload = _apply_symbol_token_budget(payload, max_tokens, primary_field="definitions")
    _attach_symbol_omissions(
        payload,
        command_name="defs",
        path=resolved_path,
        symbol=resolved_symbol,
        max_tests=max_tests,
        max_tokens=max_tokens,
        primary_field="definitions",
    )

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Definitions for {current['symbol']} in {current['path']}")
        typer.echo(f"definitions={len(current['definitions'])}")
        _echo_symbol_location_rows(current["definitions"])

    _emit_symbol_command_result(
        payload,
        result_key="definitions",
        json_output=json_output,
        emit_text=_emit_text,
        detail=detail,
    )


def source(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return partial:true JSON with whatever was found so far, instead of running unbounded."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return exact source blocks for a symbol definition."""
    from tensor_grep.cli.repo_map import build_symbol_source

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="source",
        )
        # v1.72.1 dogfood M1: `--deadline` used to be undefined on `tg source` (Click "No such
        # option" exit-2) even though its true sibling `defs` already had it -- mirrors defs's exact
        # shape (deadline defaults to None already, no --no-deadline companion). No daemon fast path
        # exists for `source` today, so there is no daemon-skip gate to add here.
        payload = build_symbol_source(
            resolved_symbol,
            resolved_path,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
            deadline_seconds=deadline,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    def _emit_text(current: dict[str, Any]) -> None:
        for line in source_text_lines(current):
            _safe_stdout_line(line)  # user source is verbatim; never crash a legacy console

    _emit_symbol_command_result(
        payload,
        result_key="sources",
        json_output=json_output,
        emit_text=_emit_text,
    )


def impact(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to evaluate."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return partial:true JSON with whatever was found so far, instead of running unbounded."
    ),
    max_tests: int | None = typer.Option(
        _DEFAULT_SYMBOL_MAX_TESTS,
        "--max-tests",
        min=1,
        help="Maximum relevant test files to include in output; raise for full coverage.",
    ),
    max_tokens: int = typer.Option(
        # Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS (literal keeps the heavy repo_map import
        # lazy). Answer-first: secondary fields (tests/related_paths) are trimmed before `files`
        # itself. 0 = unbounded opt-out.
        16000,
        "--max-tokens",
        min=0,
        help="Approximate maximum payload size in tokens (0 = unbounded).",
    ),
    detail: str = _detail_tier.detail_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return likely impacted files and tests for a symbol change."""
    from tensor_grep.cli.repo_map import (
        _apply_symbol_token_budget,
        _copy_partial_signal,
        _deadline_monotonic_from_seconds,
        build_repo_map,
        build_symbol_callers_from_map,
        build_symbol_impact_from_map,
    )

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="impact",
        )
        # task #103: build the repo_map and convert --deadline to an absolute monotonic
        # timestamp ONCE, then share both across the impact + callers passes below -- mirrors
        # build_symbol_blast_radius's own shared-map pattern (repo_map.py's build_repo_map(...)
        # once + two `_from_map` calls against it) and the daemon/MCP server, which already
        # share one repo_map across multiple `_from_map` calls in a session. Previously each of
        # the two independent wrapper calls (build_symbol_impact + build_symbol_callers) built
        # its OWN repo_map from scratch -- parsing the whole repo twice -- AND independently
        # re-derived deadline_monotonic from a fresh time.monotonic() at its own start, so
        # --deadline silently allowed up to ~2x the requested budget for `tg impact`.
        deadline_monotonic = _deadline_monotonic_from_seconds(deadline)

        def _merge_impact_and_callers(
            impact_payload: dict[str, Any], callers_payload: dict[str, Any]
        ) -> None:
            # H5 merge (task #103): impact previously surfaced only definition/import-derived
            # `files` and so under-reported call sites relative to `tg callers`. Shared by BOTH
            # the cold and warm-daemon (task #94 Part A) arms below so they cannot silently
            # diverge into two different merge behaviors.
            impact_payload["callers"] = list(callers_payload.get("callers", []))
            # Propagate the caller-scan's --deadline partial signal (cursor review 1.40.0): impact's
            # second pass can be deadline-truncated even when the first pass wasn't, so carry partial +
            # deadline_limit onto the impact payload or _emit_symbol_command_result would exit 0 while
            # `tg callers` with the same flags exits 2.
            if callers_payload.get("partial"):
                impact_payload["partial"] = True
                caller_deadline_limit = callers_payload.get("deadline_limit")
                # Don't clobber a deadline_limit the first (impact) pass already set (cursor review LOW).
                if (
                    isinstance(caller_deadline_limit, dict)
                    and "deadline_limit" not in impact_payload
                ):
                    impact_payload["deadline_limit"] = dict(caller_deadline_limit)
            for caller in impact_payload["callers"]:
                caller_file = str(caller.get("file", ""))
                if caller_file and caller_file not in impact_payload["files"]:
                    impact_payload["files"].append(caller_file)

        # task #94 Part A Tier-1: default-OFF warm-daemon fast path, skipped entirely when a
        # --deadline was requested (mirrors defs/refs/callers/blast-radius above). impact's cold
        # path is a TWO-PASS shared-repo_map call (task #103): impact + a callers-merge. The
        # daemon session caches ONE repo_map per (path, max_repo_files) key, so issuing TWO
        # daemon requests (impact, then callers) against that same implicit session reuses the
        # same cached map -- equivalent sharing to the cold path's single build_repo_map call,
        # just over two IPC round-trips instead of two in-process calls. Both requests must
        # succeed (or the symbol must be a confirmed no_match, which never needs a callers pass)
        # or this falls through to the cold path entirely, so the merged result is never a
        # warm/cold hybrid.
        daemon_callers_payload: dict[str, Any] | None = None
        daemon_impact_payload = (
            _self._maybe_symbol_command_via_running_daemon(
                command="impact",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
                max_tests=max_tests,
            )
            if deadline is None
            else None
        )
        if daemon_impact_payload is not None and not daemon_impact_payload.get("no_match"):
            daemon_callers_payload = _self._maybe_symbol_command_via_running_daemon(
                command="callers",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
            )
            if daemon_callers_payload is None:
                daemon_impact_payload = None  # both-or-nothing -- fall through to cold below

        if daemon_impact_payload is not None:
            payload = daemon_impact_payload
            if not payload.get("no_match"):
                # Invariant: reaching here with daemon_impact_payload set and no_match falsy
                # means the "both-or-nothing" check above already confirmed the callers request
                # succeeded (any callers miss reset daemon_impact_payload to None instead).
                assert daemon_callers_payload is not None
                _merge_impact_and_callers(payload, daemon_callers_payload)
            else:
                payload.setdefault("callers", [])
        else:
            repo_map = build_repo_map(
                resolved_path,
                max_repo_files=max_repo_files,
                deadline_monotonic=deadline_monotonic,
            )
            payload = build_symbol_impact_from_map(
                repo_map,
                resolved_symbol,
                semantic_provider=provider,
                deadline_monotonic=deadline_monotonic,
                max_tests=max_tests,
            )
            _copy_partial_signal(payload, repo_map)
            # H5: impact previously surfaced only definition/import-derived `files` and so
            # under-reported call sites relative to `tg callers` (which finds the CLI
            # handler, RPC handler, and tests). Populate a top-level `callers` key from the
            # same caller pass so impact is a superset, not a subset, of callers.
            if not payload.get("no_match"):
                callers_payload = build_symbol_callers_from_map(
                    repo_map,
                    resolved_symbol,
                    semantic_provider=provider,
                    deadline_monotonic=deadline_monotonic,
                )
                _copy_partial_signal(callers_payload, repo_map)
                _merge_impact_and_callers(payload, callers_payload)
            else:
                payload.setdefault("callers", [])
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    payload = _apply_symbol_token_budget(
        payload, max_tokens, primary_field="files", companion_fields=("file_matches",)
    )
    _attach_symbol_omissions(
        payload,
        command_name="impact",
        path=resolved_path,
        symbol=resolved_symbol,
        max_tests=max_tests,
        max_tokens=max_tokens,
        primary_field="files",
    )

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Impact for {current['symbol']} in {current['path']}")
        typer.echo(
            f"files={len(current['files'])} tests={len(current['tests'])} "
            f"callers={len(current['callers'])}"
        )
        typer.echo("preferred=blast-radius for direct symbol impact")

    _emit_symbol_command_result(
        payload,
        result_key="files",
        json_output=json_output,
        emit_text=_emit_text,
        detail=detail,
    )


def refs(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return partial:true JSON with whatever was found so far, instead of running unbounded."
    ),
    max_tests: int | None = typer.Option(
        _DEFAULT_SYMBOL_MAX_TESTS,
        "--max-tests",
        min=1,
        help="Maximum relevant test files to include in output; raise for full coverage.",
    ),
    max_tokens: int = typer.Option(
        # Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS (literal keeps the heavy repo_map import
        # lazy). Answer-first: secondary fields (tests/related_paths) are trimmed before
        # `references` itself. 0 = unbounded opt-out.
        16000,
        "--max-tokens",
        min=0,
        help="Approximate maximum payload size in tokens (0 = unbounded).",
    ),
    detail: str = _detail_tier.detail_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return Python-first symbol references across the inventory root."""
    from tensor_grep.cli.repo_map import _apply_symbol_token_budget, build_symbol_refs

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="refs",
        )
        # task #94 Part A Tier-1: default-OFF warm-daemon fast path, skipped entirely when a
        # --deadline was requested (a warm session's cached repo_map cannot honor a fresh
        # per-request scan deadline) so that flag combination always takes the cold path.
        payload = (
            _self._maybe_symbol_command_via_running_daemon(
                command="refs",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
                max_tests=max_tests,
            )
            if deadline is None
            else None
        )
        if payload is None:
            payload = build_symbol_refs(
                resolved_symbol,
                resolved_path,
                semantic_provider=provider,
                max_repo_files=max_repo_files,
                deadline_seconds=deadline,
                max_tests=max_tests,
            )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    payload = _apply_symbol_token_budget(payload, max_tokens, primary_field="references")
    _attach_symbol_omissions(
        payload,
        command_name="refs",
        path=resolved_path,
        symbol=resolved_symbol,
        max_tests=max_tests,
        max_tokens=max_tokens,
        primary_field="references",
    )

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"References for {current['symbol']} in {current['path']}")
        typer.echo(f"references={len(current['references'])} files={len(current['files'])}")
        _echo_symbol_location_rows(current["references"])

    _emit_symbol_command_result(
        payload,
        result_key="references",
        json_output=json_output,
        emit_text=_emit_text,
        detail=detail,
    )


def callers(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return partial:true JSON with whatever was found so far, instead of running unbounded."
    ),
    max_tests: int | None = typer.Option(
        _DEFAULT_SYMBOL_MAX_TESTS,
        "--max-tests",
        min=1,
        help="Maximum relevant test files to include in output; raise for full coverage.",
    ),
    max_tokens: int = typer.Option(
        # Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS (literal keeps the heavy repo_map import
        # lazy). Answer-first: secondary fields (tests/related_paths) are trimmed before
        # `callers` itself. 0 = unbounded opt-out.
        16000,
        "--max-tokens",
        min=0,
        help="Approximate maximum payload size in tokens (0 = unbounded).",
    ),
    detail: str = _detail_tier.detail_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return Python-first call sites and likely impacted tests for a symbol."""
    from tensor_grep.cli.repo_map import _apply_symbol_token_budget, build_symbol_callers

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="callers",
        )
        # task #94 Part A Tier-1: default-OFF warm-daemon fast path, skipped entirely when a
        # --deadline was requested (a warm session's cached repo_map cannot honor a fresh
        # per-request scan deadline) so that flag combination always takes the cold path.
        payload = (
            _self._maybe_symbol_command_via_running_daemon(
                command="callers",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
                max_tests=max_tests,
            )
            if deadline is None
            else None
        )
        if payload is None:
            payload = build_symbol_callers(
                resolved_symbol,
                resolved_path,
                semantic_provider=provider,
                max_repo_files=max_repo_files,
                deadline_seconds=deadline,
                max_tests=max_tests,
            )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    payload = _apply_symbol_token_budget(payload, max_tokens, primary_field="callers")
    _attach_symbol_omissions(
        payload,
        command_name="callers",
        path=resolved_path,
        symbol=resolved_symbol,
        max_tests=max_tests,
        max_tokens=max_tokens,
        primary_field="callers",
    )

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Callers for {current['symbol']} in {current['path']}")
        typer.echo(
            f"callers={len(current['callers'])} files={len(current['files'])} "
            f"import_consumers={len(current.get('import_graph_consumers', []))}"
        )
        _echo_symbol_location_rows(current["callers"])

    _emit_symbol_command_result(
        payload,
        result_key="callers",
        json_output=json_output,
        emit_text=_emit_text,
        detail=detail,
    )


def imports(
    file: str = typer.Argument(..., help="File to inspect for its own imports."),
    deadline: float | None = _deadline_option(
        "Accepted for interface parity; single-file dependency read, no repo scan to bound."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return what a single FILE imports, resolved to target files where possible.

    The scoped forward file-dependency primitive (#74): O(1) -- parses exactly one file, no
    repo scan. Use `tg importers FILE` for the reverse question (who imports this file). Both
    are far cheaper than `tg map` for a single file's dependency edges.

    v1.72.1 dogfood M1: `--deadline` is accepted as a documented NO-OP for command-surface
    parity with the scanning symbol commands -- an agent that learned --deadline works elsewhere
    must not get a Click "No such option" exit-2 here. There is no repo scan to bound (this reads
    exactly one file), so the value is intentionally never threaded anywhere below.
    """
    from tensor_grep.cli.repo_map import build_file_imports

    try:
        payload = build_file_imports(file)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Imports for {current['file']}")
        typer.echo(
            f"imports={len(current['imports'])} resolved={len(current['resolved_files'])} "
            f"external={len(current['external_modules'])} unresolved={len(current['unresolved'])}"
        )
        for entry in current["imports"]:
            if entry.get("resolved"):
                target = str(entry["resolved"])
            elif entry.get("external"):
                target = "external"
            else:
                target = "unresolved"
            # #93 SUB-1: a dynamic call (`importlib.import_module(...)` / `import(...)`) with a
            # non-literal argument has no module name to print -- label it instead of an empty
            # string, and flag every dynamic entry so a human reader can tell it apart from a
            # static import statement.
            module_label = entry["module"] or "<dynamic>"
            suffix = " [dynamic]" if entry.get("dynamic") else ""
            typer.echo(f"  {entry['line']}: {module_label} -> {target}{suffix}")

    _emit_symbol_command_result(
        payload,
        result_key="imports",
        json_output=json_output,
        emit_text=_emit_text,
    )


def importers(
    file: str = typer.Argument(
        ...,
        help=(
            "File to find importers of. Resolved against the current directory (like any "
            "normal path argument) whether relative or absolute -- NOT joined onto ROOT."
        ),
    ),
    root: str = typer.Argument(".", help="Root to scan for importers (the scan boundary only)."),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return partial:true JSON with whatever was found so far, instead of running unbounded."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return the files that import a single FILE (the reverse #74 file-dependency primitive).

    Bounded reverse lookup: prefilters candidate importers via the repo's import-alias graph,
    then re-parses and CONFIRMS each candidate against FILE before reporting it as an edge (the
    alias prefilter alone over-counts -- see `tg callers`' import-consumer precision notes).

    FILE is always resolved independently against the current directory (same rule as `tg
    imports FILE`), never joined onto ROOT -- e.g. from a parent directory,
    `tg importers myrepo/src/util.py myrepo` resolves FILE to `<cwd>/myrepo/src/util.py`, not
    `<cwd>/myrepo/myrepo/src/util.py` (dogfood #104).
    """
    from tensor_grep.cli.repo_map import build_file_importers

    try:
        payload = build_file_importers(
            file,
            root,
            max_repo_files=max_repo_files,
            deadline_seconds=deadline,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Importers of {current['file']}")
        typer.echo(f"importers={current['importer_count']} files={len(current['importer_files'])}")
        _echo_symbol_location_rows(current["importers"])

    _emit_symbol_command_result(
        payload,
        result_key="importers",
        json_output=json_output,
        emit_text=_emit_text,
    )


def blast_radius(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    max_callers: int | None = typer.Option(
        _DEFAULT_BLAST_RADIUS_JSON_MAX_CALLERS,
        "--max-callers",
        min=1,
        help="Maximum caller records to include in output; raise for fuller broad impact analysis.",
    ),
    max_files: int | None = typer.Option(
        _DEFAULT_BLAST_RADIUS_JSON_MAX_FILES,
        "--max-files",
        min=1,
        help="Maximum impacted files to include in output; raise for fuller broad impact analysis.",
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return partial:true JSON with whatever was found so far, instead of running unbounded."
    ),
    detail: str = _detail_tier.detail_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    mermaid_output: bool = typer.Option(
        False,
        "--mermaid",
        help="Render the direct-caller graph as a Mermaid `graph TD` (doc/agent-friendly).",
    ),
) -> None:
    """Return exact callers plus a transitive file/test blast radius for a symbol.

    The machine-readable caller GRAPH. Pass --json for callers, caller_tree,
    affected_files, blast_radius_score, imports, tests, and graph_trust_summary
    (~3s on a mid-size repo). Use this, not blast-radius-render, when you want the
    impact graph rather than a prose paste-in.
    """
    from tensor_grep.cli.repo_map import (
        _apply_blast_radius_output_limits,
        build_symbol_blast_radius,
    )

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="blast-radius",
        )
        # task #94 Part A Tier-1: default-OFF warm-daemon fast path, skipped entirely when a
        # --deadline was requested (a warm session's cached repo_map cannot honor a fresh
        # per-request scan deadline) so that flag combination always takes the cold path. The
        # daemon-served build_symbol_blast_radius_from_map does not itself apply the
        # --max-callers/--max-files OUTPUT caps (unlike the cold build_symbol_blast_radius
        # wrapper, which calls _apply_blast_radius_output_limits internally) -- apply the same
        # helper here so warm output matches cold output byte-for-byte.
        payload = (
            _self._maybe_symbol_command_via_running_daemon(
                command="blast_radius",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
                max_depth=max_depth,
            )
            if deadline is None
            else None
        )
        if payload is not None and _daemon_blast_radius_no_match_is_unreliable(payload):
            # audit #107: discard the unreliable warm no_match and fall through to the cold
            # path below, which has the literal-seed rescue the daemon route lacks.
            payload = None
        if payload is not None:
            payload = _apply_blast_radius_output_limits(
                payload, max_callers=max_callers, max_files=max_files
            )
        else:
            payload = build_symbol_blast_radius(
                resolved_symbol,
                resolved_path,
                max_depth=max_depth,
                semantic_provider=provider,
                max_repo_files=max_repo_files,
                max_callers=max_callers,
                max_files=max_files,
                deadline_seconds=deadline,
            )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    # Honor rg's no-match exit convention (audit #12): a typo'd/nonexistent symbol previously exited
    # 0 with an empty callers list -- on a refactor-safety command that reads as "resolved, zero
    # impact" instead of "never found". Compute + stamp BEFORE any output path (mirrors
    # _emit_symbol_command_result) so json/text/mermaid all see the same additive `not_found` field.
    not_found = _symbol_not_found_claim(payload, "callers")
    payload["not_found"] = not_found
    # Annotate completeness BEFORE any output path so mermaid/json/text all see result_incomplete and
    # honor the shared exit contract (cursor review 1.40.0): a --deadline partial or scan-cap
    # truncation must exit 2, never a silent exit 0 that reads as complete; an output-only cap
    # carries the OUTPUT LIMITED advisory but stays exit 0. (The mermaid renderer also reads
    # payload.result_incomplete for its `%% truncated` comment.)
    caveat, is_truncation = _annotate_result_completeness(payload, result_key="callers")
    # Exit 2 ONLY for analysis incompleteness: a SCAN truncation (--deadline partial, or a
    # --max-repo-files scan cap via the shared _scan_incomplete gate), or a preexisting
    # upstream-stamped result_incomplete=true (no scan shape of its own -- _scan_incomplete
    # cannot see it, but the annotation preserved it fail-closed above). An OUTPUT cap
    # (--max-callers/--max-files) is a COMPLETE analysis with a capped display
    # (callers_truncated/files_truncated) and stays exit 0: the agent raises the cap for more.
    # _annotate_result_completeness sets result_incomplete from scan truncation or the preexisting
    # stamp ONLY -- never from an output cap -- so reading it here cannot flip a paginated display
    # to exit 2 (the output-cap-stays-0 contract, defined once in _scan_incomplete's docstring).
    # A SCAN-truncated blast radius is INCOMPLETE regardless of whether callers were found -> exit 2
    # (council-verified B, 2026-07-05; found-but-truncated->0 was tried in #399 and overturned). A
    # truncated caller-set silently trusted as exhaustive is exactly the wrong-refactor risk this gate
    # exists to prevent. `caller_scan_truncated` = the backlog-#1 caller-scan ceiling
    # (CALLER_SCAN_FILE_CEILING) dropped files the 2000-map covers -> a SCAN truncation (exit 2),
    # distinct from an output cap. Without this the ceiling would silently exit 0 with a caller-set
    # truncated at 512 . `_scan_incomplete` is the shared gate reused by
    # every daemon/render fast-path (map, context-render, edit-plan, blast-radius-render; Cluster B,
    # 2026-07-06) so the scan-vs-output-cap contract is defined exactly once.
    incomplete = _scan_incomplete(payload) or bool(payload.get("result_incomplete"))

    if mermaid_output and json_output:
        # task #164: `--json --mermaid` together used to let mermaid short-circuit json (an
        # agent asking for both got only the human diagram and a `json.loads` on stdout raised).
        # Embed, don't refuse: fold the rendered mermaid text into the JSON payload so one call
        # returns both the machine graph and the diagram.
        payload["mermaid"] = _render_blast_radius_mermaid(payload)
        typer.echo(_detail_tier.dump_detail_json(payload, detail))
    elif mermaid_output:
        typer.echo(_render_blast_radius_mermaid(payload))
    elif json_output:
        typer.echo(_detail_tier.dump_detail_json(payload, detail))
    else:
        # Same leading-banner-vs-trailing-note split as _emit_symbol_command_result: a truncation
        # warning has to be read BEFORE the counts it qualifies (an agent reading
        # `callers=3` first has already formed the answer by the time a trailing line lands).
        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)
        if leading is not None:
            typer.echo(leading)
        from tensor_grep.cli.formatters.human_fmt import format_blast_radius_human

        typer.echo(format_blast_radius_human(payload))
        if trailing is not None:
            typer.echo(trailing)

    # Exit-order: a SCAN truncation (2) always wins over a genuine no-match (1) -- a truncated scan
    # never had the chance to find the symbol, so "not found" is not yet a trustworthy answer.
    if incomplete:
        raise typer.Exit(2)
    if not_found:
        raise typer.Exit(1)


def blast_radius_render(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
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
    render_profile: str | None = typer.Option(
        None,
        "--render-profile",
        help="Render profile: full, compact, llm, or focused. Defaults to llm for JSON and full for text.",
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-phase profiling in JSON output."
    ),
    deadline: float | None = typer.Option(
        None,
        "--deadline",
        min=0.1,
        help=(
            "Stop the underlying repo scan after N seconds and return partial:true JSON with "
            "whatever was found so far, instead of running unbounded."
        ),
    ),
    compact: bool = _detail_tier.compact_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready blast-radius bundle for a symbol.

    Emits PROSE for pasting into a prompt. For the machine-readable caller graph
    (callers/caller_tree/affected_files/blast_radius_score), use
    `tg blast-radius SYMBOL --json` instead -- it is faster and agent-consumable.
    """
    _compact_payloads.validate_compact_flags(compact, json_output=json_output)
    from tensor_grep.cli.repo_map import (
        _deadline_monotonic_from_seconds,
        build_symbol_blast_radius_render,
    )

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="blast-radius-render",
        )
        resolved_render_profile = render_profile or ("llm" if json_output else "full")
        resolved_optimize_context = optimize_context or (json_output and render_profile is None)
        deadline_monotonic = _deadline_monotonic_from_seconds(deadline)

        payload = build_symbol_blast_radius_render(
            resolved_symbol,
            resolved_path,
            max_depth=max_depth,
            max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            optimize_context=resolved_optimize_context,
            render_profile=resolved_render_profile,
            profile=profile,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
            deadline_monotonic=deadline_monotonic,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    # Shared 0/1/2 emitter (B-05): unknown symbol -> 1, scan truncation -> 2 with result_incomplete.
    # candidate_symbols stays in the JSON (additive-only contract for this payload).
    _emit_symbol_command_result(
        payload,
        result_key="definitions",
        json_output=json_output,
        emit_text=lambda current: typer.echo(current["rendered_context"]),
        keep_candidate_symbols=True,
        detail="compact" if compact else "standard",
    )


def blast_radius_plan(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the plan."
    ),
    max_symbols: int = typer.Option(
        5, "--max-symbols", min=1, help="Maximum ranked symbols to retain in the plan payload."
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return partial:true JSON with whatever was found so far, instead of running unbounded."
    ),
    compact: bool = _detail_tier.compact_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a machine-readable blast-radius planning bundle without rendered source text.

    Like `blast-radius --json` but shaped as an edit/action plan (no source snippets).
    """
    _compact_payloads.validate_compact_flags(compact, json_output=json_output)
    from tensor_grep.cli.repo_map import build_symbol_blast_radius_plan

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="blast-radius-plan",
        )
        # v1.72.1 dogfood M1: `--deadline` used to be undefined on `tg blast-radius-plan`
        # (Click "No such option" exit-2) even though its true sibling `blast-radius` already had
        # it -- mirrors that shape. No daemon fast path exists for `blast-radius-plan` today, so
        # there is no daemon-skip gate to add here.
        payload = build_symbol_blast_radius_plan(
            resolved_symbol,
            resolved_path,
            max_depth=max_depth,
            max_files=max_files,
            max_symbols=max_symbols,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
            deadline_seconds=deadline,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    # Shared 0/1/2 emitter (B-05, supersedes the F14 local gate): unknown symbol -> 1, scan
    # truncation -> 2 with result_incomplete. candidate_symbols stays in the JSON (additive-only).
    def _emit_text(current: dict[str, Any]) -> None:
        from tensor_grep.cli.formatters.human_fmt import format_blast_radius_plan_human

        typer.echo(format_blast_radius_plan_human(current))

    _emit_symbol_command_result(
        payload,
        result_key="definitions",
        json_output=json_output,
        emit_text=_emit_text,
        keep_candidate_symbols=True,
        detail="compact" if compact else "standard",
    )


def file_api(
    path: str = typer.Argument(..., help="File whose API surface to list."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """List every signature in FILE, without the bodies (the file's API surface)."""
    from tensor_grep.cli.file_api import file_api_command

    raise typer.Exit(code=file_api_command(path, json_output=json_output))


def register(app: typer.Typer) -> None:
    app.command()(defs)
    app.command()(source)
    app.command()(impact)
    app.command()(refs)
    app.command()(callers)
    app.command()(imports)
    app.command()(importers)
    app.command(name="blast-radius")(blast_radius)
    app.command(name="blast-radius-render")(blast_radius_render)
    app.command(name="blast-radius-plan")(blast_radius_plan)
    app.command(name="file-api")(file_api)
