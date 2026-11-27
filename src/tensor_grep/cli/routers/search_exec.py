from __future__ import annotations

import dataclasses
import os
import re
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import typer

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import backend_fallback as _backend_fallback
from tensor_grep.cli import citations as _citations
from tensor_grep.cli._main_binding import _self

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
from tensor_grep.cli.error_handling import (
    _combine_multi_patterns,
    _config_with_guarded_broad_root_globs,
    _eligible_for_pcre2_inline_flag_fallback,
    _emit_broad_scan_refusal,
    _emit_search_error_json,
    _exit_invalid_regex,
    _exit_search_error,
    _format_unbounded_large_root_scan_error,
    _is_inline_flag_regex_error,
    _is_invalid_regex_error,
    _pcre2_fallback_backend_available,
    _read_patterns_from_file_list,
    _should_refuse_unbounded_large_root_scan,
    _sum_total_bytes,
    _validate_search_regex,
)
from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.cli.investigation_options import (
    _note_late_rerank_degraded as _note_late_rerank_degraded,
)
from tensor_grep.cli.investigation_options import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)
from tensor_grep.cli.output_envelope import (
    _looks_like_binary_path,
    _ordered_path_output,
    _safe_stdout_line,
    _write_path_list,
)
from tensor_grep.cli.prepare_service import _build_prepare_payload as _build_prepare_payload
from tensor_grep.cli.rg_post_process import post_process_matches as _rg_out
from tensor_grep.cli.routers.find import _apply_semantic_rerank
from tensor_grep.cli.routers.search_helpers import (
    _can_passthrough_rg,
    _collect_candidate_files,
    _scope_filtered,
    _selected_gpu_execution_defaults,
    _selected_route_supports_rg_passthrough,
)
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
from tensor_grep.core.observability import nvtx_range
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
)

if TYPE_CHECKING:
    from tensor_grep.core.result import SearchResult

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

if TYPE_CHECKING:
    from tensor_grep.core.result import SearchResult


def _execute_search_command(
    *,
    allow_broad_generated_scan: Any,
    citations: Any,
    config: Any,
    count: Any,
    debug: Any,
    effective_force_cpu: Any,
    enrich_ast: Any,
    explicit_hidden_search_root: Any,
    explicit_rg_format: Any,
    file: Any,
    files: Any,
    files_with_matches: Any,
    files_without_match: Any,
    format_type: Any,
    guarded_broad_root: Any,
    json: Any,
    ltl: Any,
    max_count: Any,
    multi_pattern_source: Any,
    ndjson: Any,
    null: Any,
    only_matching: Any,
    parsed_gpu_device_ids: Any,
    paths_defaulted: Any,
    paths_to_search: Any,
    pattern: Any,
    quiet: Any,
    regexp_patterns: Any,
    rg_backend: Any,
    rg_is_available: Any,
    stats: Any,
) -> None:
    from tensor_grep.backends.base import BackendExecutionError, PassthroughExecutionError
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.io.directory_scanner import DirectoryScanner

    can_passthrough_rg = (
        not citations
        and not guarded_broad_root
        and not explicit_hidden_search_root
        and rg_is_available
        and _can_passthrough_rg(
            config,
            format_type=format_type,
            explicit_rg_format=explicit_rg_format,
            json_mode=json,
            ndjson_mode=ndjson,
            files_mode=files,
            files_with_matches=files_with_matches,
            files_without_match=files_without_match,
            only_matching=only_matching,
            stats_mode=stats,
        )
    )
    if can_passthrough_rg:
        if not stats:
            passthrough_paths = [] if paths_defaulted else paths_to_search
            with nvtx_range("search.passthrough_rg", color="green"):
                exit_code = rg_backend.search_passthrough(passthrough_paths, pattern, config=config)
            sys.exit(exit_code)

    if multi_pattern_source and not regexp_patterns:
        # The `-f`-alone sub-case (see the comment above the earlier `combined_multi_patterns`
        # assignment) is deferred until HERE, now that a real search is confirmed to not be
        # rg-passthrough (the `if can_passthrough_rg: if not stats: ... sys.exit(...)` block
        # just above already returned when it would have applied). Reading `-f` eagerly broke
        # `test_python_search_treats_file_option_as_pattern_file_not_regex`, where real `rg`
        # itself must read the pattern file on the passthrough path, never tg. This must land
        # before `Pipeline(...)` below, which reads `config.query_pattern` to route (audit #69,
        # re-do of #441).
        file_sourced_patterns = _read_patterns_from_file_list(file or [], json_mode=json)
        try:
            for regex_pattern in file_sourced_patterns:
                _validate_search_regex(regex_pattern, config)
        except Exception as exc:
            if _is_invalid_regex_error(exc):
                if (
                    _is_inline_flag_regex_error(str(exc))
                    and _eligible_for_pcre2_inline_flag_fallback(config)
                    and _pcre2_fallback_backend_available()
                ):
                    config = dataclasses.replace(config, pcre2=True)
                    typer.echo(
                        "note: retried with PCRE2 (-P) for inline-flag pattern",
                        err=True,
                    )
                else:
                    _exit_invalid_regex(exc, json_mode=json)
            else:
                raise
        pattern = _combine_multi_patterns(file_sourced_patterns, fixed_strings=config.fixed_strings)
        config = dataclasses.replace(config, query_pattern=pattern, fixed_strings=False)

    if ltl:
        # An invalid --ltl query is a USER error, not an engine failure: surface it once,
        # cleanly, through the same exit-2 taxonomy as path_not_found/invalid_regex --
        # never as CPUBackend._compile_ltl's raw ValueError traceback (exit 1), and never
        # as a BackendExecutionError (which would wrongly trigger the CPU-retry fallback).
        from tensor_grep.backends.cpu_backend import CPUBackend

        try:
            CPUBackend._compile_ltl(pattern, 0)
        except re.error as exc:
            _exit_invalid_regex(exc, json_mode=json)
        except ValueError as exc:
            _exit_search_error("invalid_ltl_query", str(exc), json_mode=json)

    from tensor_grep.core.pipeline import ConfigurationError

    scanner = DirectoryScanner(config)
    try:
        candidate_files_ordered, candidate_files_set = _collect_candidate_files(
            scanner, paths_to_search
        )
    except ConfigurationError as exc:
        _exit_search_error("configuration_error", str(exc), json_mode=json)
    config.input_total_bytes = _sum_total_bytes(candidate_files_ordered)

    from tensor_grep.core.pipeline import Pipeline
    from tensor_grep.core.result import SearchResult, merge_runtime_routing

    try:
        pipeline = Pipeline(force_cpu=effective_force_cpu, config=config)
    except (ConfigurationError, BackendExecutionError) as exc:
        # Task #166 finding A: Pipeline's explicit-routing guards (e.g. --gpu-device-ids with
        # no GPU backend available, or --pcre2 with no PCRE2-capable rg) deliberately raise
        # ConfigurationError as a fail-closed signal (core/pipeline.py), but this CLI boundary
        # previously let it propagate as a raw, uncaught traceback instead of a clean error.
        # Route it through the same `_exit_search_error` helper this function already uses for
        # every other expected/clean CLI error (empty_pattern, unsupported_flag, path_not_found,
        # invalid_regex) so the user gets a single-line `Error: ...` message and exit code 2
        # instead of a Python stack trace. A genuinely unexpected exception is NOT caught here
        # (only this specific, deliberate exception type), so a real bug still surfaces loudly.
        _exit_search_error("configuration_error", str(exc), json_mode=json)
        raise
    backend = pipeline.get_backend()
    selected_backend_name = getattr(pipeline, "selected_backend_name", backend.__class__.__name__)
    selected_backend_reason = getattr(pipeline, "selected_backend_reason", "unknown")
    selected_gpu_device_ids = list(getattr(pipeline, "selected_gpu_device_ids", []) or [])
    selected_gpu_chunk_plan_mb = list(getattr(pipeline, "selected_gpu_chunk_plan_mb", []) or [])
    if (
        can_passthrough_rg
        and stats
        and _selected_route_supports_rg_passthrough(
            selected_backend_name=selected_backend_name,
            selected_backend_reason=selected_backend_reason,
            selected_gpu_device_ids=selected_gpu_device_ids,
            selected_gpu_chunk_plan_mb=selected_gpu_chunk_plan_mb,
        )
    ):
        passthrough_paths = [] if paths_defaulted else paths_to_search
        with nvtx_range("search.passthrough_rg", color="green"):
            exit_code = rg_backend.search_passthrough(passthrough_paths, pattern, config=config)
        # Task #24: this branch exits before the is_empty scope note below; same three gates.
        if exit_code == 1 and paths_defaulted and not _scope_filtered(config) and not quiet:
            from tensor_grep.cli.bootstrap import _write_defaulted_scope_note

            _write_defaulted_scope_note()
        sys.exit(exit_code)

    # F6: at this point neither native delegation, the rg-passthrough fast path, nor the
    # stats-passthrough branch just above is handling this query for real -- the ONLY
    # remaining fast lane is Pipeline itself having routed to `RipgrepBackend` (the single
    # branch below that hands ALL candidates to one native call). Anything else means the
    # slow per-file Python loop is about to run with no bound but the wall-clock deadline
    # (trap: refusing a working native/rg-routed search would turn an instant search into
    # an error on every ordinary repo, so this checks the ACTUAL selected backend, not just
    # binary availability).
    if selected_backend_name != "RipgrepBackend" and _should_refuse_unbounded_large_root_scan(
        len(candidate_files_ordered),
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        paths_defaulted=paths_defaulted,
    ):
        _emit_broad_scan_refusal(
            _format_unbounded_large_root_scan_error(_self._LARGE_ROOT_SCAN_FILE_CEILING),
            json_output=json,
            path=str(paths_to_search[0]) if paths_to_search else ".",
        )
        raise typer.Exit(2)

    if debug:
        typer.echo(
            f"[debug] routing.backend={selected_backend_name} reason={selected_backend_reason}"
        )
        if selected_gpu_device_ids or selected_gpu_chunk_plan_mb:
            typer.echo(
                f"[debug] routing.gpu_device_ids={selected_gpu_device_ids} "
                f"routing.gpu_chunk_plan_mb={selected_gpu_chunk_plan_mb}"
            )

    if files:
        if candidate_files_ordered:
            _write_path_list(candidate_files_ordered, use_nul=null)
            sys.exit(0)
        sys.exit(1)

    tracer = None
    try:
        from opentelemetry import trace as otel_trace

        tracer = otel_trace.get_tracer(__name__)
    except ImportError:
        tracer = None

    all_results = SearchResult(matches=[], total_files=0, total_matches=0)
    all_results.routing_backend = selected_backend_name
    all_results.routing_reason = selected_backend_reason
    all_results.requested_gpu_device_ids = list(parsed_gpu_device_ids or [])
    all_results.routing_gpu_device_ids = selected_gpu_device_ids
    all_results.routing_gpu_chunk_plan_mb = selected_gpu_chunk_plan_mb
    all_results.fallback_reason = getattr(pipeline, "fallback_reason", None)
    search_start = time.perf_counter()
    matched_file_paths: set[str] = set()
    matched_file_paths_ordered: list[str] = []
    binary_match_file_paths: set[str] = set()

    def _record_matched_file(file_path: str | None) -> None:
        if not file_path or file_path in matched_file_paths:
            return
        matched_file_paths.add(file_path)
        matched_file_paths_ordered.append(file_path)

    def _merge_runtime_routing(result: SearchResult) -> None:
        merge_runtime_routing(all_results, result)
        if result.fallback_reason is not None:
            all_results.fallback_reason = result.fallback_reason

    def _merge_count_metadata(result: SearchResult, file_path: str | None = None) -> None:
        if result.binary_match_files and file_path is not None:
            try:
                identity = os.path.normcase(str(Path(file_path).resolve(strict=False)))
            except (OSError, RuntimeError):
                identity = os.path.normcase(os.path.abspath(file_path))
            if identity not in binary_match_file_paths:
                binary_match_file_paths.add(identity)
                all_results.binary_match_files += result.binary_match_files
        elif file_path is None:
            all_results.binary_match_files += result.binary_match_files
        for file_path, count in result.match_counts_by_file.items():
            all_results.match_counts_by_file[file_path] = (
                all_results.match_counts_by_file.get(file_path, 0) + count
            )

    if backend.__class__.__name__ == "RipgrepBackend":
        rg_backend = cast(RipgrepBackend, backend)
        if guarded_broad_root:
            rg_search_config = _config_with_guarded_broad_root_globs(config)
        else:
            rg_search_config = config
        if explicit_hidden_search_root:
            rg_search_config = dataclasses.replace(rg_search_config, hidden=True)
        if files_without_match:
            rg_search_config = dataclasses.replace(
                rg_search_config,
                files_without_match=False,
            )
        search_targets = (
            paths_to_search
            if (guarded_broad_root or files_with_matches)
            else candidate_files_ordered
            if files_without_match
            else paths_to_search
        )
        span_ctx = (
            tracer.start_as_current_span("search.file") if tracer is not None else nullcontext()
        )
        with span_ctx as span, nvtx_range("search.file", color="cyan"):
            if span is not None:
                span.set_attribute("backend", backend.__class__.__name__)
                span.set_attribute("path_count", len(search_targets))
            try:
                result = rg_backend.search(search_targets, pattern, config=rg_search_config)
            except Exception as exc:
                if _is_invalid_regex_error(exc):
                    _exit_invalid_regex(exc, json_mode=json)
                if isinstance(exc, BackendExecutionError):  # e.g. rg output not representable
                    _exit_search_error("backend_error", str(exc), json_mode=json)
                raise
            if span is not None:
                span.set_attribute("matches", result.total_matches)
            all_results.matches.extend(result.matches)
            for matched_path in result.matched_file_paths:
                _record_matched_file(matched_path)
            _merge_count_metadata(result)
            all_results.total_matches += result.total_matches
            all_results.total_files += result.total_files
            for match in result.matches:
                _record_matched_file(match.file)
            _merge_runtime_routing(result)
    else:
        from tensor_grep.backends.cpu_backend import (
            compute_native_walk_deadline,
            native_walk_deadline_exceeded,
        )
        from tensor_grep.cli.subprocess_policy import configured_ripgrep_timeout_seconds

        native_walk_deadline = compute_native_walk_deadline()
        for current_file in candidate_files_ordered:
            if native_walk_deadline_exceeded(native_walk_deadline):
                timeout_seconds = configured_ripgrep_timeout_seconds()
                all_results.result_incomplete = True
                all_results.incomplete_reason = (
                    f"native search exceeded the {timeout_seconds:g}s timeout and was "
                    "stopped; returning partial results. Scope the search to a smaller "
                    "path, or raise TG_RG_TIMEOUT_SECONDS."
                )
                all_results.incomplete_reason_class = "timeout"
                sys.stderr.write(
                    "tg: native search exceeded the "
                    f"{timeout_seconds:g}s timeout, keeping partial results: "
                    f"{all_results.incomplete_reason}\n"
                )
                break
            span_ctx = (
                tracer.start_as_current_span("search.file") if tracer is not None else nullcontext()
            )
            with span_ctx as span, nvtx_range("search.file", color="cyan"):
                if span is not None:
                    span.set_attribute("backend", backend.__class__.__name__)
                    span.set_attribute("path", current_file)
                try:
                    result = backend.search(current_file, pattern, config=config)
                except PassthroughExecutionError as exc:
                    _self._exit_search_error("backend_error", str(exc), json_mode=json)
                except BackendExecutionError as exc:
                    try:
                        result = _self._search_with_cpu_fallback(
                            current_file, pattern, config, exc, backend
                        )
                    except BackendExecutionError as fallback_exc:
                        _self._exit_search_error("backend_error", str(fallback_exc), json_mode=json)
                    _backend_fallback.record_fallback_on_aggregate(all_results, result)
                except Exception as exc:
                    if _is_invalid_regex_error(exc):
                        _exit_invalid_regex(exc, json_mode=json)
                    raise
                if span is not None:
                    span.set_attribute("matches", result.total_matches)
            all_results.matches.extend(result.matches)
            for matched_path in result.matched_file_paths:
                _record_matched_file(matched_path)
            _merge_count_metadata(result, current_file)
            all_results.total_matches += result.total_matches
            if result.total_files > 0 or result.total_matches > 0:
                all_results.total_files += 1
                _record_matched_file(current_file)
            for match in result.matches:
                _record_matched_file(match.file)
            _merge_runtime_routing(result)

        # Task #276 slice 1: the CPU/native route's candidate-file list came from `scanner`
        # (via `_collect_candidate_files` above), NOT from rg's own walk -- so a directory
        # `scanner.walk()` could not read (permission denied) or an entry-count cap it hit
        # (`DirectoryScanner`'s own defensive budget) silently narrowed
        # `candidate_files_ordered` before this loop ever started, with NOTHING to surface it.
        # The rg-backend branch above doesn't need this: rg re-walks the tree itself and
        # already reports its own exit-2 soft error (see `RipgrepBackend.search`). Only set
        # this when nothing already flagged incompleteness (the deadline check above takes
        # precedence if it already fired -- first-cause-wins, matches `incomplete_reason`'s
        # merge convention elsewhere).
        #
        # Deliberately a BARE attribute read, not `getattr(..., False)`: this is the ONLY
        # signal on this branch (unlike `mcp_server.py`'s `scan_truncated` read, which ORs
        # into an independently-computed `scan_capped` and exists to bool-coerce a MagicMock
        # test double, not to survive a missing attribute) -- a silent `False` fallback here
        # would resurrect exactly the fail-open silence this fix exists to close, just one
        # layer up. `scanner` is always a real `DirectoryScanner` in production; a test double
        # that doesn't carry these fields is a stale double that no longer matches the
        # contract (fixed at its own definition, `test_cli_modes.py`'s `_FakeScanner`), not a
        # reason to make the production code degrade silently.
        if scanner.scan_truncated and not all_results.result_incomplete:
            unreadable_count = scanner.unreadable_path_count
            if scanner.scan_truncation_cause == "max-scan-entries":
                all_results.incomplete_reason_class = "scan_limit"
                all_results.incomplete_reason = (
                    "directory scan exceeded its entry budget "
                    f"({scanner.max_scan_entries} entries) and was stopped; returning partial "
                    "results. Scope the search to a smaller path, or raise "
                    "TG_DIR_SCAN_MAX_ENTRIES."
                )
                if unreadable_count:
                    # The entry-cap cause unconditionally overwrote an earlier unreadable-path
                    # cause (directory_scanner.py's own first-wins tracking only covers WHICH
                    # cause is reported first, not this second, independent truncation
                    # reaching the cap afterward) -- do not let the unreadable-path count go
                    # unmentioned just because the cap also fired, or the reader gets the
                    # WRONG-KNOB advice ("raise TG_DIR_SCAN_MAX_ENTRIES") with no hint that a
                    # bigger budget won't fix the unreadable-path portion of the shortfall.
                    all_results.incomplete_reason += (
                        f" (the scan also skipped {unreadable_count} unreadable path(s) before "
                        "hitting the cap; raising the entry budget will not make those "
                        "readable)."
                    )
            elif scanner.scan_truncation_cause == "unreadable_path":
                sample = ", ".join(scanner.unreadable_path_sample) or "an unreadable path"
                all_results.incomplete_reason_class = "unreadable_path"
                all_results.incomplete_reason = (
                    f"directory scan skipped {unreadable_count} unreadable path(s) "
                    f"(e.g. {sample}) and returned partial results. More budget will not fix "
                    "this: the path(s) need to become readable, or scope the search away from "
                    "them."
                )
            else:
                # `scanner.scan_truncated=True` and `scan_truncation_cause` is neither
                # `"max-scan-entries"` nor `"unreadable_path"` -- unreachable by construction
                # today (`directory_scanner.py` only ever sets those two together with the
                # flag), but fail LOUDLY here rather than silently mislabeling a future third
                # cause (or a `None` cause) as `"unreadable_path"`. `incomplete_reason_class`
                # is a documented closed vocabulary (docs/CONTRACTS.md); inventing an
                # undocumented 5th value would be worse than crashing, since a crash at least
                # forces the new cause to be classified deliberately instead of guessed.
                raise AssertionError(
                    "DirectoryScanner reported scan_truncated=True with an unrecognized "
                    f"scan_truncation_cause={scanner.scan_truncation_cause!r}; add an explicit "
                    "incomplete_reason_class mapping for this cause before shipping it."
                )
            all_results.result_incomplete = True
            sys.stderr.write(f"tg: {all_results.incomplete_reason}\n")

    try:  # rg's own -o/-r output; a record it cannot represent exactly is a clean exit 2
        all_results.matches = _rg_out(all_results.matches, pattern, config, only_matching)
    except BackendExecutionError as exc:
        _exit_search_error("backend_error", str(exc), json_mode=json)

    if only_matching:
        all_results.total_matches = sum(m.rg_kind != "context" for m in all_results.matches)
        all_results.total_files = len({m.file for m in all_results.matches})
        matched_file_paths = {m.file for m in all_results.matches}
        matched_file_paths_ordered = []
        for match in all_results.matches:
            if match.file not in matched_file_paths_ordered:
                matched_file_paths_ordered.append(match.file)

    matched_files = set(matched_file_paths)
    all_results.matched_file_paths = sorted(matched_files)
    if not all_results.match_counts_by_file and all_results.matches:
        for match in all_results.matches:
            all_results.match_counts_by_file[match.file] = (
                all_results.match_counts_by_file.get(match.file, 0) + 1
            )
    if config.semantic_rank:
        if all_results.matches:
            try:
                all_results = _apply_semantic_rerank(all_results, pattern)
            except BackendExecutionError as exc:
                # F4 : a genuine dense-backend fault (e.g. a corrupt model
                # directory) must exit cleanly with a `tg:` message, never a raw traceback --
                # `_apply_semantic_rerank` deliberately does NOT catch this (see its docstring);
                # this is the CLI boundary the Backend Fail-Closed Contract requires.
                if json:
                    _emit_search_error_json("semantic_backend_error", str(exc))
                else:
                    typer.echo(f"tg: {exc}", err=True)
                sys.exit(2)
        else:
            from tensor_grep.cli.investigation_options import (
                apply_semantic_zero_match_bm25_fallback,
            )

            apply_semantic_zero_match_bm25_fallback(
                all_results,
                pattern,
                candidate_files_ordered,
                max_count=max_count,
                corpus_chunk_cap=_self._SEMANTIC_CORPUS_CHUNK_CAP,
                set_fallback_reason_fn=_set_semantic_rank_fallback_reason,
            )
    elif config.rank_bm25 and all_results.matches:
        from tensor_grep.core.reranker import rerank_by_bm25

        all_results = rerank_by_bm25(all_results, pattern, all_results.matched_file_paths)
    matched_file_count = len(matched_files) or all_results.total_files
    elapsed_ms = (time.perf_counter() - search_start) * 1000.0
    runtime_override_active = (
        all_results.routing_backend is not None
        and all_results.routing_backend != selected_backend_name
    ) or (
        all_results.routing_reason is not None
        and all_results.routing_reason != selected_backend_reason
    )
    if (
        not runtime_override_active
        and all_results.routing_worker_count == 0
        and (all_results.routing_gpu_device_ids or all_results.routing_gpu_chunk_plan_mb)
    ):
        (
            all_results.routing_distributed,
            all_results.routing_worker_count,
        ) = _selected_gpu_execution_defaults(
            list(all_results.routing_gpu_device_ids),
            list(all_results.routing_gpu_chunk_plan_mb),
        )

    def _emit_runtime_debug() -> None:
        if not debug:
            return
        runtime_backend = all_results.routing_backend or selected_backend_name
        runtime_reason = all_results.routing_reason or selected_backend_reason
        runtime_gpu_device_ids = all_results.routing_gpu_device_ids or selected_gpu_device_ids
        runtime_gpu_chunk_plan_mb = (
            all_results.routing_gpu_chunk_plan_mb or selected_gpu_chunk_plan_mb
        )

        runtime_differs = (
            runtime_backend != selected_backend_name
            or runtime_reason != selected_backend_reason
            or runtime_gpu_device_ids != selected_gpu_device_ids
            or runtime_gpu_chunk_plan_mb != selected_gpu_chunk_plan_mb
        )
        if not runtime_differs:
            return

        typer.echo(
            f"[debug] routing.runtime backend={runtime_backend} reason={runtime_reason}",
            err=True,
        )
        if runtime_gpu_device_ids or runtime_gpu_chunk_plan_mb:
            typer.echo(
                (
                    f"[debug] routing.runtime.gpu_device_ids={runtime_gpu_device_ids} "
                    f"routing.runtime.gpu_chunk_plan_mb={runtime_gpu_chunk_plan_mb} "
                    f"distributed={all_results.routing_distributed} "
                    f"workers={all_results.routing_worker_count}"
                ),
                err=True,
            )

    def _emit_stats() -> None:
        if not stats:
            return
        typer.echo(
            (
                f"[stats] scanned_files={len(candidate_files_ordered)} "
                f"matched_files={matched_file_count} "
                f"total_matches={all_results.total_matches} "
                f"elapsed_ms={elapsed_ms:.2f}"
            ),
            err=True,
        )
        typer.echo(
            (
                f"[stats] backend={all_results.routing_backend or selected_backend_name} "
                f"reason={all_results.routing_reason or selected_backend_reason}"
            ),
            err=True,
        )
        if runtime_override_active:
            stats_gpu_device_ids = list(all_results.routing_gpu_device_ids)
            stats_gpu_chunk_plan_mb = list(all_results.routing_gpu_chunk_plan_mb)
        else:
            stats_gpu_device_ids = all_results.routing_gpu_device_ids or selected_gpu_device_ids
            stats_gpu_chunk_plan_mb = (
                all_results.routing_gpu_chunk_plan_mb or selected_gpu_chunk_plan_mb
            )
        if stats_gpu_device_ids or stats_gpu_chunk_plan_mb:
            typer.echo(
                (
                    f"[stats] gpu_device_ids={stats_gpu_device_ids} "
                    f"gpu_chunk_plan_mb={stats_gpu_chunk_plan_mb} "
                    f"distributed={all_results.routing_distributed} "
                    f"workers={all_results.routing_worker_count}"
                ),
                err=True,
            )

    _emit_runtime_debug()

    # Backlog #22 RULING, 2026-08-01: an unhonoured explicit `--gpu-device-ids` request does
    # NOT exit 2. It stays whatever the search itself earned (0 / 1).
    #
    # An earlier cut of this branch made it exit 2 on the reasoning that "the caller asked for a
    # specific execution mode and tg could not provide it, so the request went unhonoured, and
    # that is a refusal". Defensible in isolation, and it CONTRADICTS the written contract.
    # `docs/CONTRACTS.md` section 4: `2` = INCOMPLETE, meaning the SCAN was truncated. That
    # search ran to completion over every file it was asked about and returned correct, complete
    # results -- it simply computed them on the CPU. Which processor did the work is a ROUTING
    # fact, not an incompleteness.
    #
    # The contract already carries the analogous precedent, twice, and both go the other way:
    #   * "An OUTPUT-only cap ... is a COMPLETE analysis capped only for display and stays exit
    #     `0`; only a SCAN truncation exits `2`."
    #   * `tg imports --deadline` is "a documented NO-OP ... output is byte-identical with or
    #     without it" -- an accepted flag that changes nothing does not move the exit code.
    #
    # Promoting this to exit 2 would also break every consumer branching on 1-vs-2 for the most
    # ordinary GPU-requesting invocation there is, on a machine that simply has no GPU.
    #
    # THE SIGNAL IS NOT LOST -- it was never missing. `gpu_request_unhonoured` still classifies
    # the request, and the `--json` envelope carries `gpu_evidence_status` / `gpu_proof` /
    # `native_gpu_unavailable` / `not_gpu_proof_reason` (json_fmt.py). Those are strictly MORE
    # informative than a coarse exit code, and a harness that wants to know "did GPU actually run"
    # should read them rather than branch on 0-vs-2. That is what the fields are for.
    #
    # Recorded in `_SEARCH_COMMAND_TAIL_EXIT_CODE_POLICIES` above so the decision is discoverable
    # from the registry rather than only from this comment.
    exit_incomplete = all_results.result_incomplete

    if files_with_matches:
        if matched_files:
            _emit_stats()
            output_paths = _ordered_path_output(
                matched_file_paths_ordered or sorted(matched_files),
                config,
            )
            _write_path_list(output_paths, use_nul=null)
            sys.exit(2 if exit_incomplete else 0)
        _emit_stats()
        sys.exit(2 if exit_incomplete else 1)

    if files_without_match:
        unmatched_candidates = candidate_files_set - matched_files
        if not (config.text or config.binary):
            unmatched_candidates = {
                path for path in unmatched_candidates if not _looks_like_binary_path(path)
            }
        unmatched = _ordered_path_output(sorted(unmatched_candidates), config)
        if unmatched:
            _emit_stats()
            _write_path_list(unmatched, use_nul=null)
            sys.exit(2 if exit_incomplete else 0)
        _emit_stats()
        sys.exit(2 if exit_incomplete else 1)

    _citations.exit_if_search_citations(
        all_results,
        paths_to_search,
        citations,
        json or format_type == "json",
        query_pattern=pattern,
        fixed_strings=bool(config.fixed_strings),
    )

    if all_results.is_empty:
        _emit_stats()
        # The FULL-CLI twin of the passthrough note added in #857. A `_requires_full_cli` flag
        # (`--ast`, `--rank`, `--semantic`, `--stats`) bypasses bootstrap's rg passthrough and
        # lands HERE, so the scope note shipped for the common route never fired for these four.
        #
        # Reachability traced, not assumed -- `tg search NO_MATCH --ast --lang python` exits at
        # this branch. An earlier attempt at this fix was written into a branch the invocation
        # never takes and had no observable effect.
        #
        # Exit stays 1 below: a defaulted-scope search that RAN TO COMPLETION is complete, it just
        # answered a narrower question. Exit 2 remains reserved for result_incomplete.
        # Three gates, all load-bearing (external audit of the first cut found each one):
        #
        # `paths_defaulted` alone is NOT "the caller did not choose a scope" -- it only means no
        # positional PATH. A search scoped by `--glob`/`--iglob`/`--type`/`--max-depth` DID choose
        # a scope, and telling it "no PATH was given, so the search defaulted to the current
        # directory" is a false positive that misdescribes what ran. `config.glob` etc. are checked
        # directly rather than via `_has_walk_scope_bound`, which returns False for exactly the
        # `paths_defaulted` case we are inside.
        #
        # `quiet` suppresses it: `--quiet` promises no incidental output, and emitting an
        # informational note there is a silent contract change on a flag whose entire purpose is
        # silence.
        scope_filtered = _scope_filtered(config)
        if paths_defaulted and not scope_filtered:
            # Stamp the JSON body BEFORE the formatter runs, so a machine consumer reading only
            # stdout learns why the zero is ambiguous. v1.101.22 dogfood: "PATH note is
            # stderr-only -- agents that ignore stderr can miss it." Not gated on `quiet`: the
            # field is part of the document a --json caller asked for, whereas the stderr note is
            # incidental output that --quiet legitimately suppresses.
            from tensor_grep.cli.bootstrap import _defaulted_scope_note

            all_results.path_was_defaulted = True
            all_results.scope_note = _defaulted_scope_note()
        if paths_defaulted and not scope_filtered and not quiet:
            from tensor_grep.cli.bootstrap import _write_defaulted_scope_note

            _write_defaulted_scope_note()
        if json or format_type == "json":
            from tensor_grep.cli.formatters.json_fmt import JsonFormatter

            _safe_stdout_line(JsonFormatter().format(all_results))
        sys.exit(2 if exit_incomplete else int(not all_results.rg_exit_zero))

    if quiet:
        _emit_stats()
        sys.exit(2 if exit_incomplete else 0)

    if enrich_ast and all_results.matches:
        from tensor_grep.cli.ast_enrichment import (
            AST_ENRICH_FILE_LIMIT,
            enrich_match_with_container,
        )

        unique_files = list(dict.fromkeys(m.file for m in all_results.matches))
        selected_files = set(unique_files[:AST_ENRICH_FILE_LIMIT])
        if len(unique_files) > AST_ENRICH_FILE_LIMIT:
            all_results.ast_enrichment_truncated = True
        symbols_cache: dict[str, Any] = {}
        enriched_matches = []
        for m in all_results.matches:
            if m.file in selected_files:
                container = enrich_match_with_container(m.file, m.line_number, symbols_cache)
                if container:
                    m = dataclasses.replace(m, container=container)
            enriched_matches.append(m)
        all_results.matches = enriched_matches

    formatter: OutputFormatter

    if ndjson:
        from tensor_grep.cli.formatters.json_fmt import NdjsonFormatter

        formatter = NdjsonFormatter()
    elif json or format_type == "json":
        from tensor_grep.cli.formatters.json_fmt import JsonFormatter

        # Pass the search config so aggregate --json match objects can carry the 1-based
        # `column` for text-search matches (which have no ast-grep range) — audit L5.
        formatter = JsonFormatter(config=config)
    elif format_type == "table":
        from tensor_grep.cli.formatters.table_fmt import TableFormatter

        formatter = TableFormatter()
    elif format_type == "csv":
        from tensor_grep.cli.formatters.csv_fmt import CsvFormatter

        formatter = CsvFormatter()
    else:
        from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter

        formatter = RipgrepFormatter(config=config)

    _safe_stdout_line(formatter.format(all_results))
    _emit_stats()
    if exit_incomplete:
        # rg-parity: partial results (rg exit 2, soft per-file error) exit 2 after a formatted
        # success, not 0 — so a caller/agent sees the same incompleteness rg would signal.
        #
        # An unhonoured explicit --gpu-device-ids request does NOT reach here: `exit_incomplete`
        # above reads `result_incomplete` and nothing else. Backlog #22 was RETIRED as an
        # exit-code rule on 2026-08-01 (PR #868) — see the `gpu_request_unhonoured`
        # `_TailExitCodePolicy` entry for the reasoning, and docs/CONTRACTS.md section 4, which
        # PR #911 corrected to match. This comment previously claimed the opposite ("takes the
        # same exit"), so the file contradicted both that ruling and its own contract doc.
        sys.exit(2)
