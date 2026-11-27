from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import typer

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import citations as _citations
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
    _cli_deadline_monotonic,
    _deadline_option,
    _emit_scan_incompleteness_banner,
    _query_deprecation_warning,
    _resolve_path_and_query,
    _scan_incomplete,
)
from tensor_grep.cli.prepare_service import _build_prepare_payload as _build_prepare_payload
from tensor_grep.cli.routers.search_helpers import (
    _parse_gpu_device_ids_cli,
    _warn_unavailable_gpu_device_ids,
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


_AGENT_DEADLINE_PARTIAL_REASONS = frozenset({"deadline", "deadline_exceeded"})


def _agent_trustworthy_deadline_partial_note(payload: dict[str, Any]) -> str | None:
    """Return a one-line ASCII stderr note when (and ONLY when) `tg agent`'s exit-2 is caused
    SOLELY by a trustworthy `--deadline` cutoff, else ``None``.

    "Solely" means every one of these holds against the SAME capsule that is about to exit 2:
      * ``partial`` is true with a deadline-type ``partial_reason`` (``deadline``/
        ``deadline_exceeded``) -- a `--deadline` cutoff, not some other truncation.
      * Neither ``scan_limit`` nor ``caller_scan_limit`` is ``possibly_truncated``, and
        ``caller_scan_truncated`` is not set -- a genuine scan-coverage gap (the repo-file-count
        ceiling or the caller-scan file ceiling) is a DIFFERENT, non-deadline truncation vector
        even when it happens to coincide with a deadline hit, so it must never read as
        "trustworthy" -- this stays silent (returns ``None``) and the plain exit-2 stands.
      * ``ask_user_before_editing.required`` is false -- the capsule itself never asked for
        confirmation.
      * ``confidence.overall`` is at/above the capsule's own confident threshold, reusing
        ``agent_capsule._capsule_low_confidence_ask_reason`` (currently 0.75) rather than a
        second hardcoded cutoff that could silently drift from the real ask-gate.

    A genuine needs-attention exit-2 -- ``ask_user_before_editing.required`` true, low confidence,
    or a non-deadline partial -- gets no note and keeps reading as needs-attention, unchanged.

    Fail-safe on a malformed capsule (independent-gate nit): a present-but-non-dict
    ``confidence``/``ask_user_before_editing``, or a bool/non-numeric ``confidence.overall``
    (bool subclasses int and would otherwise coerce to 1.0), suppresses the note (returns
    ``None``) rather than raising -- matching the ``scan_limit``/``caller_scan_limit``
    ``isinstance`` guards below. Unreachable from ``build_agent_capsule_from_map``'s
    single-return output (it always emits dict-shaped ``confidence``/``ask_user_before_editing``),
    but an advisory helper must never be what crashes an exit-2 path.

    Additive/advisory only: never changes the exit code, the stdout JSON, or the capsule schema --
    called only at the two existing ``raise typer.Exit(2)`` sites inside ``agent()``, stderr-only.
    """
    if not payload.get("partial"):
        return None
    if payload.get("partial_reason") not in _AGENT_DEADLINE_PARTIAL_REASONS:
        return None
    for key in ("scan_limit", "caller_scan_limit"):
        limit = payload.get(key)
        if isinstance(limit, dict) and limit.get("possibly_truncated"):
            return None
    if payload.get("caller_scan_truncated"):
        return None
    ask_user_before_editing = payload.get("ask_user_before_editing", {})
    if not isinstance(ask_user_before_editing, dict):
        return None
    if bool(ask_user_before_editing.get("required")):
        return None
    confidence = payload.get("confidence", {})
    if not isinstance(confidence, dict):
        return None
    overall = confidence.get("overall")
    if isinstance(overall, bool) or not isinstance(overall, (int, float)):
        return None

    from tensor_grep.cli.agent_capsule import _capsule_low_confidence_ask_reason

    if _capsule_low_confidence_ask_reason(float(overall)) is not None:
        return None
    return (
        f"note: partial result -- stopped at the --deadline (confidence {float(overall):.2f}, "
        "no ask required); the answer is usable. Re-run with a larger --deadline for full "
        "coverage."
    )


def _emit_agent_text(payload: dict[str, Any], gpu_device_ids: str | None) -> None:
    _emit_scan_incompleteness_banner(payload)
    primary = payload.get("primary_target", {})
    primary_file = primary.get("file") or "<none>"
    primary_line = primary.get("line") or 1
    primary_symbol = primary.get("symbol") or "<unknown>"
    validation_commands = payload.get("validation_commands", [])
    confidence = payload.get("confidence", {}).get("overall", 0)
    gpu_acceleration = payload.get("gpu_acceleration", {})
    ambiguity = payload.get("ambiguity", {})
    ask_user_before_editing = payload.get("ask_user_before_editing", {})
    context_consistency = payload.get("context_consistency", {})
    alternatives = payload.get("alternative_targets", [])
    typer.echo(f"Agent capsule for {payload['path']}")
    typer.echo(f"query={payload['query']}")
    typer.echo(f"primary={primary_file}#L{primary_line} {primary_symbol}")
    typer.echo(f"validation={len(validation_commands)} commands")
    typer.echo(f"confidence={confidence}")
    typer.echo(f"ask_required={bool(ask_user_before_editing.get('required'))}")
    typer.echo(f"ambiguity={ambiguity.get('status', 'unknown')}")
    typer.echo(
        "alternatives="
        f"{len(alternatives)}"
        f" omitted={context_consistency.get('alternative_targets_omitted_count', 0)}"
    )
    if gpu_device_ids:
        typer.echo(f"gpu_acceleration={gpu_acceleration.get('status', 'unknown')}")


def agent(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query_arg: str | None = typer.Argument(None, help="Natural-language task or symbol query."),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the capsule."
    ),
    max_sources: int = typer.Option(
        5, "--max-sources", min=1, help="Maximum exact source blocks to include."
    ),
    max_tokens: int | None = typer.Option(
        1200, "--max-tokens", min=1, help="Approximate maximum capsule snippet tokens."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    model: str | None = typer.Option(
        None, "--model", help="Future tokenizer model selector; currently accepted but ignored."
    ),
    provider: str = typer.Option(
        "native",
        "--provider",
        help="Semantic provider for primary target proof: native, lsp, or hybrid.",
    ),
    gpu_device_ids: str | None = typer.Option(
        None,
        "--gpu-device-ids",
        help=(
            "Comma-separated GPU IDs for an opt-in native evidence scan. "
            "Sidecar routes are reported as unsupported."
        ),
    ),
    gpu_timeout_s: float = typer.Option(
        5.0,
        "--gpu-timeout-s",
        min=0.1,
        help="Maximum seconds for each opt-in agent GPU evidence command.",
    ),
    ignore: list[str] = typer.Option(
        [],
        "--ignore",
        help=(
            "Glob(s) to exclude from the capsule ranking (basename or repo-relative path), e.g. "
            "--ignore 'seo/**' --ignore 'core/skills/**'. Excludes vendor/skill CODE trees that "
            "otherwise rank as the primary target on a harness repo. Repeatable."
        ),
    ),
    deadline: float | None = _deadline_option(
        "Stop after N seconds, measured from CLI command entry (not just the underlying repo scan -- excludes only the ~100-200ms interpreter-startup/dispatch prefix before this command body runs), and return a partial capsule (partial=true, deadline_limit) with whatever was found so far, instead of running unbounded. The cold path (no running session daemon) defaults to 60s so a huge repo can't hang an agent loop; pass --no-deadline to disable the bound."
    ),
    no_deadline: bool = typer.Option(
        False,
        "--no-deadline",
        help="Disable the cold path's default 60s --deadline bound; let the scan run unbounded "
        "(a warm session daemon is unaffected either way).",
    ),
    plan_hops: bool = typer.Option(
        False, "--plan-hops", help="Plan at most three evidence-backed investigation stages."
    ),
    grounding: str = typer.Option(
        "local", "--grounding", help="Dependency evidence: off, local, registry (explicit network)."
    ),
    rerank: str = typer.Option(
        "off", "--rerank", help="Native advisory snippet reranking: off, auto, cross-encoder."
    ),
    detail: str = _detail_tier.detail_option(),
    compact: bool = _detail_tier.compact_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    citations: bool = typer.Option(False, "--citations", help="Emit citation ranges."),
) -> None:
    """Return an actionable context capsule for agents before editing."""
    # Anchor explicit deadlines before lazy imports and daemon lookup; the cold default starts
    # only after a daemon miss. Interpreter startup precedes this command entry point.
    effective_deadline = None if no_deadline else deadline
    deadline_monotonic = _cli_deadline_monotonic(effective_deadline)

    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.cli.agent_capsule import (
        DEFAULT_AGENT_CLI_DEADLINE_SECONDS,
        build_agent_capsule,
    )
    from tensor_grep.cli.investigation_options import validate_investigation_options
    from tensor_grep.core.pipeline import ConfigurationError

    try:
        validate_investigation_options(grounding, rerank)
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="agent",
        )
        parsed_gpu_device_ids = _parse_gpu_device_ids_cli(gpu_device_ids)
        _warn_unavailable_gpu_device_ids(parsed_gpu_device_ids)
        # Daemon misses or unreliable evidence fall through to the cold build. Requests requiring
        # fresh deadlines or new option contracts use the cold path until daemon schema support.
        effective_detail = _detail_tier.resolve_compact_detail(
            compact, detail, json_output=json_output, citations=citations
        )
        daemon_payload = (
            _self._maybe_agent_via_running_daemon(
                path=resolved_path,
                query=resolved_query,
                max_files=max_files,
                max_sources=max_sources,
                max_tokens=max_tokens,
                max_repo_files=max_repo_files,
                model=model,
                provider=provider,
                gpu_device_ids=parsed_gpu_device_ids,
                ignore=tuple(ignore),
            )
            if effective_deadline is None
            and not plan_hops
            and rerank == "off"
            and grounding != "registry"
            and not citations
            else None
        )
        if daemon_payload is not None:
            from tensor_grep.core.dependency_grounding import dependency_grounding

            daemon_grounding = dependency_grounding(resolved_path, grounding, max_tokens=256)
            if daemon_grounding is not None:
                daemon_payload["dependency_grounding"] = daemon_grounding
            else:
                daemon_payload.pop("dependency_grounding", None)
            if json_output:
                typer.echo(
                    _detail_tier.dump_detail_json(
                        daemon_payload, effective_detail, ensure_ascii=False
                    )
                )
            else:
                _emit_scan_incompleteness_banner(daemon_payload)
                _emit_agent_text(daemon_payload, gpu_device_ids)
            if _scan_incomplete(daemon_payload):
                deadline_partial_note = _agent_trustworthy_deadline_partial_note(daemon_payload)
                if deadline_partial_note is not None:
                    typer.echo(deadline_partial_note, err=True)
                raise typer.Exit(2)
            return

        # Apply the default 60-second bound after daemon lookup, preserving warm reuse.
        cold_deadline_seconds = effective_deadline
        if cold_deadline_seconds is None and not no_deadline:
            cold_deadline_seconds = DEFAULT_AGENT_CLI_DEADLINE_SECONDS
            deadline_monotonic = _cli_deadline_monotonic(cold_deadline_seconds)

        payload = build_agent_capsule(
            resolved_query,
            resolved_path,
            max_files=max_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_repo_files=max_repo_files,
            model=model,
            semantic_provider=provider,
            gpu_device_ids=parsed_gpu_device_ids,
            gpu_timeout_s=gpu_timeout_s,
            ignore=tuple(ignore),
            deadline_seconds=cold_deadline_seconds,
            deadline_monotonic=deadline_monotonic,
            plan_hops=plan_hops,
            grounding=grounding,
            rerank=rerank,
        )
    except (FileNotFoundError, ValueError, ConfigurationError, BackendExecutionError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    # Emit the full capsule before applying the scan-truncation exit contract.
    if citations:
        _citations.emit_payload_citations(
            _detail_tier.apply_detail_tier(payload, effective_detail) if json_output else payload,
            resolved_path,
            json_output=json_output,
        )
    elif json_output:
        typer.echo(_detail_tier.dump_detail_json(payload, effective_detail, ensure_ascii=False))
    else:
        _emit_scan_incompleteness_banner(payload)
        _emit_agent_text(payload, gpu_device_ids)
    if _scan_incomplete(payload):
        deadline_partial_note = _agent_trustworthy_deadline_partial_note(payload)
        if deadline_partial_note is not None:
            typer.echo(deadline_partial_note, err=True)
        raise typer.Exit(2)


def edit_plan(
    path: str = typer.Argument(".", help="File or directory to inventory"),
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repository files to scan before ranking edit targets.",
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
    provider: str = typer.Option(
        "native",
        "--provider",
        help="Semantic provider for primary target proof: native, lsp, or hybrid.",
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-phase profiling in JSON output."
    ),
    deadline: float | None = _deadline_option(
        "Stop after N seconds, measured from CLI command entry (not just the underlying repo scan -- excludes only the ~100-200ms interpreter-startup/dispatch prefix before this command body runs), and return a partial plan (partial=true, deadline_limit) with whatever was found so far, instead of running unbounded. Pass --no-deadline to keep the (already default) unbounded behavior explicit."
    ),
    no_deadline: bool = typer.Option(
        False,
        "--no-deadline",
        help="Accepted for command-surface parity with codemap; a no-op since edit-plan already "
        "defaults to an unbounded --deadline.",
    ),
    compact: bool = _detail_tier.compact_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a machine-readable edit-planning bundle without rendered source text."""
    # Anchor deadline_monotonic at CLI command entry (closes the #197/#200 front-door residual):
    # computed here, BEFORE the lazy repo_map import, path resolution, and the daemon gate below,
    # so front-door time counts against an explicit --deadline the same way the scan already does.
    # CLI consistency fix : `--deadline` used to be undefined on
    # `tg edit-plan` (Click "No such option" exit-2).
    effective_deadline = None if no_deadline else deadline
    deadline_monotonic = _cli_deadline_monotonic(effective_deadline)

    from tensor_grep.cli.repo_map import build_context_edit_plan

    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="edit-plan",
        )
        # Skip the warm-daemon fast path entirely when a --deadline was requested (a warm
        # session's cached repo_map cannot honor a fresh per-request scan deadline) -- mirrors
        # refs/callers/impact/blast-radius's own daemon gate.
        edit_detail = _detail_tier.resolve_compact_detail(
            compact, "standard", json_output=json_output
        )
        daemon_payload = (
            _self._maybe_edit_plan_via_running_daemon(
                path=resolved_path,
                query=resolved_query,
                max_files=max_files,
                max_repo_files=max_repo_files,
                max_sources=max_sources,
                max_tokens=max_tokens,
                max_symbols=max_symbols,
                provider=provider,
                profile=profile,
            )
            if effective_deadline is None
            else None
        )
        if daemon_payload is not None:
            # Output-before-exit (Cluster B, 2026-07-06): same exit-2-on-scan-truncation contract as
            # the cold path below -- print the full daemon payload, then exit 2 if it was truncated.
            if json_output:
                typer.echo(_detail_tier.dump_detail_json(daemon_payload, edit_detail))
            else:
                from tensor_grep.cli.formatters.human_fmt import format_edit_plan_human

                _emit_scan_incompleteness_banner(daemon_payload)
                typer.echo(format_edit_plan_human(daemon_payload))
            if _scan_incomplete(daemon_payload):
                raise typer.Exit(2)
            return

        payload = build_context_edit_plan(
            resolved_query,
            resolved_path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_symbols=max_symbols,
            semantic_provider=provider,
            profile=profile,
            deadline_seconds=effective_deadline,
            deadline_monotonic=deadline_monotonic,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Cold path (Cluster B, 2026-07-06): build the payload once and dump it here (byte-identical to
    # the old build_context_edit_plan_json helper: json.dumps(payload, indent=2)) so both json and
    # text branches share the same scan-truncation gate below -- output the full payload FIRST, then
    # exit 2 if the scan itself (not just the output) was capped.
    if json_output:
        typer.echo(_detail_tier.dump_detail_json(payload, edit_detail))
    else:
        from tensor_grep.cli.formatters.human_fmt import format_edit_plan_human

        _emit_scan_incompleteness_banner(payload)
        typer.echo(format_edit_plan_human(payload))

    if _scan_incomplete(payload):
        raise typer.Exit(2)


_ROUTE_TEST_CONFIDENCE_WARNING_THRESHOLD = 0.75


_ROUTE_TEST_CONFIDENCE_FLOOR = 0.4


def _route_test_int(value: object) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _route_test_confidence_score(confidence: object) -> float | None:
    if isinstance(confidence, dict):
        for key in ("overall", "primary", "target"):
            try:
                return float(cast(Any, confidence[key]))
            except (KeyError, TypeError, ValueError):
                continue
        primary_scores: list[float] = []
        for key in ("file", "symbol"):
            try:
                primary_scores.append(float(cast(Any, confidence[key])))
            except (KeyError, TypeError, ValueError):
                continue
        if primary_scores:
            return min(primary_scores)
        return None
    try:
        return float(cast(Any, confidence))
    except (TypeError, ValueError):
        return None


def _route_test_primary_target(payload: dict[str, Any]) -> dict[str, Any]:
    raw_primary_target = payload.get("primary_target")
    if not isinstance(raw_primary_target, dict):
        navigation_pack = payload.get("navigation_pack")
        if isinstance(navigation_pack, dict):
            raw_primary_target = navigation_pack.get("primary_target")
    primary_target = dict(raw_primary_target) if isinstance(raw_primary_target, dict) else {}

    edit_plan_seed = payload.get("edit_plan_seed")
    seed = dict(edit_plan_seed) if isinstance(edit_plan_seed, dict) else {}
    primary_symbol = seed.get("primary_symbol")
    primary_symbol_payload = dict(primary_symbol) if isinstance(primary_symbol, dict) else {}
    primary_span = seed.get("primary_span")
    primary_span_payload = dict(primary_span) if isinstance(primary_span, dict) else {}

    file_path = str(
        primary_target.get("file")
        or seed.get("primary_file")
        or primary_symbol_payload.get("file")
        or ""
    )
    symbol = str(
        primary_target.get("symbol")
        or primary_symbol_payload.get("name")
        or primary_symbol_payload.get("symbol")
        or ""
    )
    line = (
        _route_test_int(primary_target.get("line"))
        or _route_test_int(primary_target.get("start_line"))
        or _route_test_int(primary_span_payload.get("start_line"))
        or _route_test_int(primary_symbol_payload.get("line"))
        or _route_test_int(primary_symbol_payload.get("start_line"))
    )
    end_line = (
        _route_test_int(primary_target.get("end_line"))
        or _route_test_int(primary_span_payload.get("end_line"))
        or _route_test_int(primary_symbol_payload.get("end_line"))
        or line
    )
    confidence = primary_target.get("confidence")
    if confidence is None:
        confidence = seed.get("confidence")
    confidence_score = _route_test_confidence_score(confidence)

    return {
        "file": file_path or None,
        "symbol": symbol or None,
        "line": line,
        "end_line": end_line,
        "confidence": confidence if confidence is not None else None,
        "confidence_score": confidence_score,
    }


def _route_test_normalized_file(value: object) -> str:
    if not value:
        return ""
    try:
        return os.path.normcase(str(Path(str(value)).expanduser().resolve(strict=False)))
    except OSError:
        return os.path.normcase(str(value))


def _route_test_validation_command_count(payload: dict[str, Any]) -> int | None:
    validation_commands = payload.get("validation_commands")
    if isinstance(validation_commands, list):
        return len(validation_commands)
    navigation_pack = payload.get("navigation_pack")
    if isinstance(navigation_pack, dict) and isinstance(
        navigation_pack.get("validation_commands"), list
    ):
        return len(navigation_pack["validation_commands"])
    edit_plan_seed = payload.get("edit_plan_seed")
    if isinstance(edit_plan_seed, dict) and isinstance(
        edit_plan_seed.get("validation_commands"), list
    ):
        return len(edit_plan_seed["validation_commands"])
    return None


def _build_route_test_payload(
    *,
    path: str,
    query: str,
    max_files: int,
    max_repo_files: int,
    max_sources: int,
    max_symbols_per_file: int,
    max_symbols: int,
    provider: str,
    profile: bool,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    from tensor_grep.cli.repo_map import build_context_edit_plan, build_context_render

    # #223 SLA fix: ONE shared, pre-anchored absolute deadline threaded into BOTH builder calls
    # (each already honors deadline_monotonic AS-IS post-#671, never recomputing it) instead of
    # each side silently getting its own unbounded None. Side 1 (context-render) runs first and
    # spends against the shared budget; side 2 (edit-plan) naturally gets whatever wall-clock
    # remains under the SAME anchor -- no separate per-side split needed.
    context_payload = build_context_render(
        query,
        path,
        max_files=max_files,
        max_repo_files=max_repo_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        render_profile="llm",
        optimize_context=True,
        semantic_provider=provider,
        profile=profile,
        deadline_monotonic=deadline_monotonic,
    )
    edit_payload = build_context_edit_plan(
        query,
        path,
        max_files=max_files,
        max_repo_files=max_repo_files,
        max_sources=max_sources,
        max_symbols=max_symbols,
        semantic_provider=provider,
        profile=profile,
        deadline_monotonic=deadline_monotonic,
    )

    context_target = _route_test_primary_target(context_payload)
    edit_target = _route_test_primary_target(edit_payload)
    file_agrees = _route_test_normalized_file(
        context_target["file"]
    ) == _route_test_normalized_file(edit_target["file"])
    symbol_agrees = context_target["symbol"] == edit_target["symbol"]
    line_agrees = context_target["line"] == edit_target["line"]
    agreement = bool(
        context_target["file"]
        and edit_target["file"]
        and file_agrees
        and symbol_agrees
        and line_agrees
    )

    warnings: list[str] = []
    notes: list[str] = []
    if not agreement:
        warnings.append("primary targets disagree between context-render and edit-plan")
    low_confidence_lines: list[str] = []
    scored_confidences: list[float] = []
    for label, target in (
        ("context-render", context_target),
        ("edit-plan", edit_target),
    ):
        confidence_score = target.get("confidence_score")
        if isinstance(confidence_score, int | float):
            scored_confidences.append(float(confidence_score))
            if confidence_score < _ROUTE_TEST_CONFIDENCE_WARNING_THRESHOLD:
                low_confidence_lines.append(
                    f"{label} primary target confidence {confidence_score:.3f} is below "
                    f"{_ROUTE_TEST_CONFIDENCE_WARNING_THRESHOLD:.2f}"
                )
    if low_confidence_lines:
        both_very_low = len(scored_confidences) >= 2 and all(
            c < _ROUTE_TEST_CONFIDENCE_FLOOR for c in scored_confidences
        )
        if agreement and not both_very_low:
            # Routes agree -> low confidence is calibration, not routing doubt: demote to a note.
            notes.append(
                "context-render and edit-plan agree on the primary target; the sub-threshold "
                "confidence reflects ranking-score calibration, not routing disagreement"
            )
            notes.extend(low_confidence_lines)
        else:
            warnings.extend(low_confidence_lines)

    context_validation_count = _route_test_validation_command_count(context_payload)
    edit_validation_count = _route_test_validation_command_count(edit_payload)
    result: dict[str, Any] = {
        "version": 1,
        "routing_reason": "route-test",
        "path": str(Path(path).expanduser().resolve(strict=False)),
        "query": query,
        "agreement": agreement,
        "agreement_details": {
            "file": file_agrees,
            "symbol": symbol_agrees,
            "line": line_agrees,
        },
        "warnings": warnings,
        "notes": notes,
        "context_render": {
            "routing_reason": context_payload.get("routing_reason"),
            "primary_target": context_target,
            "validation_command_count": context_validation_count,
        },
        "edit_plan": {
            "routing_reason": edit_payload.get("routing_reason"),
            "primary_target": edit_target,
            "validation_command_count": edit_validation_count,
        },
        "validation_command_counts": {
            "context_render": context_validation_count,
            "edit_plan": edit_validation_count,
        },
    }
    # #223 SLA honesty: each builder independently stamps its OWN top-level `partial` (+
    # `partial_reason`/`deadline_limit`) when the SHARED deadline truncated it (build_context_
    # render_from_map / build_context_edit_plan_from_map's return-time backstops). Kept
    # additive-only -- mirrors the result_incomplete/partial convention used throughout this
    # codebase (architecture-contract's partial-results contract) -- so a complete run's JSON
    # stays byte-identical to before this fix. `agreement_basis` is the new tell an agent must
    # check before trusting `agreement` at face value: an agreement computed from one or two
    # TRUNCATED sides must not read as a full-confidence verdict just because the payload shape
    # otherwise looks the same as always.
    context_partial = bool(context_payload.get("partial"))
    edit_partial = bool(edit_payload.get("partial"))
    if context_partial or edit_partial:
        result["partial"] = True
        result["partial_reason"] = "deadline"
        result["agreement_basis"] = "partial"
        result["deadline_limit"] = {
            "deadline_exceeded": True,
            "context_render": context_partial,
            "edit_plan": edit_partial,
        }
    return result


def route_test(
    path: str = typer.Argument(".", help="File or directory to test routing for."),
    query_arg: str | None = typer.Argument(
        None, help="Query text to compare through context-render and edit-plan."
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in each route."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repository files to scan before ranking targets.",
    ),
    max_sources: int = typer.Option(
        5, "--max-sources", min=1, help="Maximum source/span records to retain per route."
    ),
    max_symbols_per_file: int = typer.Option(
        6,
        "--max-symbols-per-file",
        min=1,
        help="Maximum context-render summary symbols to include per file.",
    ),
    max_symbols: int = typer.Option(
        5, "--max-symbols", min=1, help="Maximum edit-plan ranked symbols to retain."
    ),
    provider: str = typer.Option(
        "native",
        "--provider",
        help="Semantic provider for primary target proof: native, lsp, or hybrid.",
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-route profiling in the compared builders."
    ),
    deadline: float | None = _deadline_option(
        'Stop after N seconds, measured from CLI command entry, and mark the result partial (partial=true, agreement_basis="partial") with whatever was found so far instead of running unbounded. route-test runs the FULL context-render AND edit-plan builds back to back, so -- unlike context-render/edit-plan, which stay unbounded by default -- it defaults to 60s (mirrors tg agent\'s cold-path default) rather than running unbounded. Pass --no-deadline to disable the bound.'
    ),
    no_deadline: bool = typer.Option(
        False,
        "--no-deadline",
        help="Disable route-test's default 60s --deadline bound; let both routes run unbounded.",
    ),
    json_output: bool = typer.Option(
        False, "--json/--text", help="Emit machine-readable JSON output."
    ),
) -> None:
    """Diagnose routing agreement between context-render and edit-plan for one query.

    Runs the same PATH/QUERY through both target-selection paths and reports their primary
    targets side by side, so an agent or operator can confirm the two routes agree (same file,
    symbol, and line) before trusting an edit-plan's target -- or see exactly where and why they
    diverge.
    """
    # #223: anchor deadline_monotonic at CLI command entry, BEFORE path/query resolution, so
    # front-door time counts against the bound the same way the underlying repo scans already do
    # (mirrors context-render/edit-plan/agent's own #197/#200 anchor). route-test defaults to a
    # bounded wall clock -- reusing DEFAULT_AGENT_CLI_DEADLINE_SECONDS (tg agent's existing 60s
    # cold-path constant, F4) rather than inventing a second default -- because it pays the FULL
    # context-render + edit-plan cost twice (dogfood v19: ~27s alone, hit a 60s external harness
    # timeout under concurrent load). An explicit --deadline overrides the default; --no-deadline
    # disables the bound entirely.
    from tensor_grep.cli.agent_capsule import DEFAULT_AGENT_CLI_DEADLINE_SECONDS

    effective_deadline = (
        None
        if no_deadline
        else (deadline if deadline is not None else DEFAULT_AGENT_CLI_DEADLINE_SECONDS)
    )
    deadline_monotonic = _cli_deadline_monotonic(effective_deadline)

    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="route-test",
            echo_warning=not json_output,
        )
        payload = _build_route_test_payload(
            path=resolved_path,
            query=resolved_query,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_symbols=max_symbols,
            provider=provider,
            profile=profile,
            deadline_monotonic=deadline_monotonic,
        )
        if json_output and query is not None:
            # --json keeps stderr empty: the warning travels in the payload instead.
            payload.setdefault("warnings", []).append(_query_deprecation_warning("route-test"))
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Output-before-exit (mirrors context-render/edit-plan/agent, Cluster B 2026-07-06): print the
    # full payload FIRST, then exit 2 if either side's build was truncated -- never a silent exit
    # 0 that reads as a complete, full-confidence agreement.
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        _emit_scan_incompleteness_banner(payload)
        context_target = payload["context_render"]["primary_target"]
        edit_target = payload["edit_plan"]["primary_target"]
        typer.echo(f"Route test for {payload['path']}")
        typer.echo(f"query={payload['query']}")
        typer.echo(
            "context-render="
            f"{context_target.get('file')}#L{context_target.get('line')} "
            f"{context_target.get('symbol')}"
        )
        typer.echo(
            "edit-plan="
            f"{edit_target.get('file')}#L{edit_target.get('line')} "
            f"{edit_target.get('symbol')}"
        )
        # `agreement=` is a VERDICT, and a verdict computed from a truncated scan is the single
        # most over-readable line this command emits -- so the qualifier leads it (task #329).
        # The key=value form is kept rather than converted to a `warning:` banner: this whole
        # block is a key=value listing and something may parse `partial=true`. Same call made for
        # `prepare`, and the opposite call for `codemap`/`inventory`, whose trailing lines are
        # prose in the same register as a banner and would have said it twice.
        if payload.get("partial"):
            typer.echo(f"partial=true agreement_basis={payload.get('agreement_basis')}")
        typer.echo(f"agreement={payload['agreement']}")
        for warning in payload["warnings"]:
            typer.echo(f"warning={warning}")

    if _scan_incomplete(payload):
        raise typer.Exit(2)


def prepare(
    path: str = typer.Argument(".", help="File or directory to prepare an edit for."),
    query_arg: str | None = typer.Argument(
        None, help="Natural-language task or symbol query to prepare an edit for."
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
    claim: bool = typer.Option(
        False,
        "--claim",
        help=(
            "Actually submit the advisory coordination claim (writes .tensor-grep/ledger/ and "
            "reports live overlaps from other agents). Default is emit-args-only: "
            "coordination.claim.submitted stays false and nothing is written."
        ),
    ),
    deadline: float | None = _deadline_option(
        'Stop after N seconds, measured from CLI command entry, and mark the result partial (partial=true, partial_reason="deadline") with whatever was found so far instead of running unbounded. prepare runs the full agent capsule build PLUS a blast-radius floor scan, so -- like route-test -- it defaults to 60s (mirrors tg agent\'s cold-path default) rather than running unbounded. Pass --no-deadline to disable the bound.'
    ),
    no_deadline: bool = typer.Option(
        False,
        "--no-deadline",
        help="Disable prepare's default 60s --deadline bound; let it run unbounded.",
    ),
    compact: bool = _detail_tier.compact_option(),
    json_output: bool = typer.Option(
        False, "--json/--text", help="Emit machine-readable JSON output."
    ),
    out: str | None = typer.Option(
        None,
        "--out",
        "--output",
        help=(
            "Also persist the full capsule JSON to FILE (always full, even when stdout uses "
            "--text or --json --compact), so `tg evidence emit --capsule FILE` can reuse it "
            "without a manual save. Refuses to write through a pre-existing symlink."
        ),
    ),
) -> None:
    """One-call edit-readiness capsule: primary target, confidence, a callers/blast-radius
    floor, validation commands, and claim/evidence coordination hooks -- the single call meant
    to replace the orient -> search -> agent -> route-test -> callers -> evidence -> ledger loop.
    """
    _detail_tier.resolve_compact_detail(compact, "standard", json_output=json_output)
    # Anchor deadline_monotonic at CLI command entry (mirrors route-test/agent's #197/#200 fix):
    # computed BEFORE path/query resolution so front-door time counts against the same bound.
    from tensor_grep.cli.agent_capsule import DEFAULT_AGENT_CLI_DEADLINE_SECONDS

    effective_deadline = (
        None
        if no_deadline
        else (deadline if deadline is not None else DEFAULT_AGENT_CLI_DEADLINE_SECONDS)
    )
    deadline_monotonic = _cli_deadline_monotonic(effective_deadline)

    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="prepare",
        )
        payload = _self._build_prepare_payload(
            path=resolved_path,
            query=resolved_query,
            claim=claim,
            deadline_monotonic=deadline_monotonic,
            include_next_action=True,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Output-before-exit (mirrors context-render/edit-plan/agent/route-test): print the full
    # payload FIRST, then exit 2 if either the capsule or the blast-radius floor was truncated --
    # never a silent exit 0 that reads as a complete, full-confidence result.
    from tensor_grep.cli.prepare_format import emit_prepare_output, write_prepare_out

    if not json_output:
        _emit_scan_incompleteness_banner(payload)
    emit_prepare_output(payload, json_output=json_output, compact=compact)

    if out is not None:
        try:
            write_prepare_out(out, payload)
        except OSError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    if _scan_incomplete(payload):
        raise typer.Exit(2)


def diff_impact(
    ref: str | None = typer.Argument(
        None, help="Git revision or commit range (e.g. HEAD~1, main)."
    ),
    staged: bool = typer.Option(
        False, "--staged", help="Compare staged changes instead of working tree."
    ),
    deadline: float | None = _deadline_option(
        "Stop repo scan after N seconds and return partial:true JSON."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    fail_threshold: float | None = typer.Option(
        None,
        "--fail-threshold",
        min=0.0,
        max=1.0,
        help="Fail (exit 2) if blast_radius_score exceeds this threshold.",
    ),
    fail_on_risk: str | None = typer.Option(
        None,
        "--fail-on-risk",
        help="Fail (exit 2) if risk_tier is at or above this level (low, medium, high, critical).",
    ),
) -> None:
    """Analyze blast radius, affected callers, and test impact of git diff changes."""
    from tensor_grep.cli.diff_impact import diff_impact_command

    diff_impact_command(
        ref=ref,
        staged=staged,
        deadline=deadline,
        json_output=json_output,
        fail_threshold=fail_threshold,
        fail_on_risk=fail_on_risk,
    )


def register(app: typer.Typer) -> None:
    app.command(name="agent")(agent)
    app.command(name="edit-plan")(edit_plan)
    app.command(name="route-test")(route_test)
    app.command(name="prepare")(prepare)
    app.command(name="diff-impact")(diff_impact)
