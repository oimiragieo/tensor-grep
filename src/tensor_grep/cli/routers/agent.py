from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import citations as _citations
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
    _cli_deadline_monotonic,
    _completeness_caveat_lines,
    _deadline_option,
    _emit_scan_incompleteness_banner,
    _resolve_path_and_query,
    _safe_stdout_line,
    _scan_incomplete,
    _truncation_message,
)
from tensor_grep.cli.prepare_service import _build_prepare_payload as _build_prepare_payload
from tensor_grep.cli.runtime_paths import _native_tg_version as _native_tg_version
from tensor_grep.cli.runtime_paths import _native_tg_version_matches as _native_tg_version_matches
from tensor_grep.cli.runtime_paths import env_flag_disabled, env_flag_enabled
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


def map(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    max_files: int | None = typer.Option(
        None, "--max-files", min=1, help="Maximum source files to include in output."
    ),
    max_repo_files: int | None = typer.Option(
        2000,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning. Defaults to the agent-safe 2000-file cap.",
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return a partial map (partial=true, deadline_limit) with whatever was found so far, instead of running unbounded. Unlike `codemap`, no bound is applied by default -- pass --deadline to opt in."
    ),
    no_deadline: bool = typer.Option(
        False,
        "--no-deadline",
        help="Accepted for command-surface parity with codemap; a no-op since map already "
        "defaults to an unbounded --deadline.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a deterministic repository map for AI editing workflows."""
    from tensor_grep.cli.repo_map import (
        DEFAULT_AGENT_REPO_MAP_LIMIT,
        _deadline_monotonic_from_seconds,
        apply_repo_map_output_limits,
        build_repo_map,
    )

    try:
        effective_max_repo_files = max_repo_files or DEFAULT_AGENT_REPO_MAP_LIMIT
        # CLI consistency fix : `--deadline` used to be undefined on `tg map`
        # (Click "No such option" exit-2) even though build_repo_map already accepts
        # deadline_monotonic -- this is a pure CLI-layer wiring gap, not a builder gap.
        effective_deadline = None if no_deadline else deadline
        deadline_monotonic = _deadline_monotonic_from_seconds(effective_deadline)
        payload = build_repo_map(
            path, max_repo_files=effective_max_repo_files, deadline_monotonic=deadline_monotonic
        )
        payload = apply_repo_map_output_limits(payload, max_files=max_files)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Cold path (Cluster B, 2026-07-06): dump the SAME payload/limit order the old build_repo_map_json
    # helper used (build_repo_map then apply_repo_map_output_limits, json.dumps(indent=2)), with the
    # shared completeness annotation applied FIRST so JSON and text tell one truth -- then gate the
    # exit on the annotation's truncation classification, exactly like the blast-radius command:
    # a scan truncation OR an independently stamped upstream result_incomplete=true exits 2 with a
    # leading warning, while an output-only cap (--max-files) stays exit 0 with a trailing
    # "note: OUTPUT LIMITED ..." advisory. Composition is owned by `_annotate_result_completeness`
    # -- this route deliberately does not re-derive scan-vs-output semantics (one shared
    # annotation is the point; a second derivation here is how map and blast-radius drifted).
    caveat, is_truncation = _annotate_result_completeness(payload)
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)
        if leading is not None:
            typer.echo(leading)
        typer.echo(f"Repository map for {payload['path']}")
        typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")
        typer.echo(f"symbols={len(payload['symbols'])} imports={len(payload['imports'])}")
        if trailing is not None:
            typer.echo(trailing)

    if is_truncation:
        raise typer.Exit(2)


def inventory(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    max_repo_files: int = typer.Option(
        # Literal mirrors inventory.DEFAULT_MAX_INVENTORY_FILES (kept literal so the heavy
        # repo_map import stays lazy, matching `map`'s literal-default pattern); a guard test pins them.
        50_000,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before truncating (walk-only; defaults to 50000).",
    ),
    deadline: float | None = _deadline_option(
        "Stop the inventory scan after N seconds and return a partial manifest labeled scan_limit.truncation_cause='deadline' (counts are a floor), instead of running unbounded on a huge tree."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Emit a single-pass repository inventory (files, bytes, languages, categories)."""
    import json as _json

    from tensor_grep.cli.inventory import build_inventory, render_inventory_text

    try:
        payload = build_inventory(path, max_files=max_repo_files, deadline_seconds=deadline)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(_json.dumps(payload))
    else:
        # NOT banner-wired, and not an oversight: `render_inventory_text` (cli/inventory.py) already
        # ends with its own cause-specific notice -- "[!] truncated at max_files=N (cause=X); counts
        # are a floor, not complete." That is prose in the SAME register as the banner, so adding
        # one here would say the same thing twice, exactly as it would have in `codemap`. Its
        # trailing POSITION is a separate, tracked defect; fixing it belongs in `inventory.py`
        # where the message is built, not as a second message in front of it.
        #
        # Found late: an audit that scanned only THIS file reported inventory as having no
        # disclosure, because its disclosure is one call away in another module. A probe that
        # cannot see past the file it greps will report a delegating caller as silent.
        typer.echo(render_inventory_text(payload))

    # #130(a) optional bundle: mirror `map`'s exit-2-on-scan-truncation contract (:7418-7419)
    # -- a truncated scan (scan_limit.possibly_truncated, e.g. a fired --deadline) previously
    # always exited 0, indistinguishable from a genuinely complete inventory. _scan_incomplete
    # already checks exactly this payload's scan_limit.possibly_truncated shape.
    if _scan_incomplete(payload):
        raise typer.Exit(2)


def _docs_scan_is_unreadable_truncated(payload: dict[str, Any]) -> bool:
    """True when a docs-coverage scan was cut short by a path it could not read.

    Task #294. `docs-coverage` deliberately exits 0 on `scan_limit.possibly_truncated`, and
    docs/CONTRACTS.md says so explicitly -- but that decision was made when the field could ONLY
    mean the `--max-repo-files` count cap, whose remedy is "raise the cap". Exiting 0 was
    defensible because the caller could always fix it by spending more budget.

    #276/#767/#768 then WIDENED the same field to also mean "the walk hit an unreadable path", a
    cause NO budget increase fixes. The exit-code decision is a consumer of that field's meaning
    and was never revisited, so the command kept returning 0 -- indistinguishable from a complete
    scan -- for a condition the caller genuinely needs to know about. Measured on the published
    1.98.17 against a real ACL-denied subtree: exit 0 on the truncated tree AND on a clean one,
    i.e. the exit code carried no information at all, while sibling `tg inventory` exited 2.

    Deliberately NARROW: only the non-budget-remediable cause flips the exit code. A pure
    count-cap truncation still exits 0, so the documented budget contract is unchanged and no
    existing caller of that path breaks. Follows `tg codemap`, which already made
    `unreadable_path` a new exit-2 trigger (docs/CONTRACTS.md), rather than inventing a rule.
    """
    scan_limit = payload.get("scan_limit")
    if not isinstance(scan_limit, dict):
        return False
    return scan_limit.get("truncation_cause") == "unreadable-path"


def docs_coverage(
    path: str = typer.Argument(".", help="File or directory to check for governing-doc coverage"),
    max_repo_files: int = typer.Option(
        50_000,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before truncating (walk-only; defaults to 50000).",
    ),
    ignore: list[str] = typer.Option(
        [],
        "--ignore",
        help="Glob(s) of source files to exclude entirely (repeatable). Matched against the "
        "repo-relative path and basename, e.g. --ignore 'commands/*/index.js' --ignore '*.stub.py'. "
        "An intentional stub group stops being re-flagged and no longer drags coverage_pct.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Emit a paste-ready Markdown table of undocumented files (path/size/first line).",
    ),
    stale: bool = typer.Option(
        False,
        "--stale",
        help="Inverse mode: report governing-doc references to files that no longer exist.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Exit non-zero when any file is uncovered (or, with --stale, any reference is stale) "
        "-- turns docs-coverage into a CI doc-drift gate. Respects --ignore.",
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return partial:true JSON with whatever was found so far, instead of running unbounded."
    ),
) -> None:
    """List source files not referenced by any governing doc (CLAUDE.md/README/AGENTS.md)."""
    import json as _json

    from tensor_grep.cli.docs_coverage import (
        build_docs_coverage,
        build_docs_stale_references,
        render_docs_coverage_fix_markdown,
        render_docs_coverage_text,
        render_docs_stale_text,
    )

    # --fix renders a Markdown table of UNCOVERED source files (build_docs_coverage's
    # uncovered_details shape); --stale reports a disjoint shape (doc -> dangling reference) with no
    # analogous fix-table renderer. Silently ignoring --fix here previously looked like a no-op with
    # no signal (audit #23); reject up front rather than emit a report the flag never affected.
    if stale and fix:
        typer.echo(
            "Error: --fix is not supported with --stale (no fix table for stale references).",
            err=True,
        )
        raise typer.Exit(1)

    try:
        if stale:
            stale_payload = build_docs_stale_references(
                path, max_files=max_repo_files, ignore=tuple(ignore), deadline_seconds=deadline
            )
            if json_output:
                typer.echo(_json.dumps(stale_payload))
            else:
                _safe_stdout_line(render_docs_stale_text(stale_payload))
            # v1.72.1 dogfood M1: a --deadline-truncated scan is INCOMPLETE -- exit 2, checked
            # BEFORE --check's exit-1 below (truncation trumps found, mirrors the symbol-command
            # _emit_symbol_command_result / blast-radius-plan's _scan_incomplete contract). This is
            # scoped to the NEW `partial` (time-budget) signal only -- the pre-existing
            # `scan_limit.possibly_truncated` (--max-repo-files count-cap) contract is UNCHANGED and
            # still exits 0, so this is additive-only unless --deadline is explicitly passed.
            if stale_payload.get("partial") or _docs_scan_is_unreadable_truncated(stale_payload):
                raise typer.Exit(2)
            # --check exits AFTER emitting the report, so CI shows what failed AND fails the job.
            if check and stale_payload["totals"]["stale"] > 0:
                raise typer.Exit(1)
            return
        payload = build_docs_coverage(
            path,
            max_files=max_repo_files,
            include_details=fix,
            ignore=tuple(ignore),
            deadline_seconds=deadline,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(_json.dumps(payload))
    # Text output can embed a resolved filesystem path (non-English username -> non-ASCII); route
    # through the cp1252-safe writer, never bare typer.echo (the #346 crash class).
    elif fix:
        _safe_stdout_line(render_docs_coverage_fix_markdown(payload))
    else:
        _safe_stdout_line(render_docs_coverage_text(payload))
    # See the --stale branch above: truncation trumps --check. Two exit-2 triggers now: the
    # --deadline `partial` time-budget signal, and an unreadable path (task #294).
    # --max-repo-files' possibly_truncated still stays exit 0.
    if payload.get("partial") or _docs_scan_is_unreadable_truncated(payload):
        raise typer.Exit(2)
    if check and payload["totals"]["uncovered"] > 0:
        raise typer.Exit(1)


def orient(
    path: str = typer.Argument(".", help="File or directory to orient on"),
    max_tokens: int = typer.Option(3000, "--max-tokens", help="Snippet token budget", min=1),
    max_central_files: int = typer.Option(
        10, "--max-central-files", help="Number of top central files to surface", min=1
    ),
    ignore: list[str] = typer.Option(
        [],
        "--ignore",
        help=(
            "Glob(s) to exclude from the centrality ranking (basename or repo-relative path), e.g. "
            "--ignore 'seo/**' --ignore 'core/skills/**'. Excludes vendor/skill CODE trees that "
            "otherwise rank as 'central' on a harness repo. Repeatable."
        ),
    ),
    no_auto_deweight: bool = typer.Option(
        False,
        "--no-auto-deweight",
        help=(
            "Disable auto de-weighting of detected vendor/skill/generated CODE subtrees (nested "
            "package manifest + import-island or name prior). De-weighting is ON by default and "
            "only LOWERS a subtree's centrality score -- it never excludes files; use --ignore for "
            "a hard exclude."
        ),
    ),
    deadline: float | None = _deadline_option(
        "Stop after N seconds, measured from CLI command entry (not just the underlying repo scan -- excludes only the ~100-200ms interpreter-startup/dispatch prefix before this command body runs), and return a partial capsule with whatever was found so far, instead of running unbounded. `tg orient` has NO exit-2 contract: a truncated scan still exits 0, surfacing partial/deadline_limit as informational fields only (never a retry signal). Pass --no-deadline to keep the (already default) unbounded behavior explicit."
    ),
    no_deadline: bool = typer.Option(
        False,
        "--no-deadline",
        help="Accepted for command-surface parity with codemap; a no-op since orient already "
        "defaults to an unbounded --deadline.",
    ),
    compact: bool = _detail_tier.compact_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit the capsule as JSON"),
) -> None:
    """Emit a one-call codebase orientation capsule (central files, entry points, AST snippets)."""
    # Anchor deadline_monotonic at CLI command entry (closes #197/#200): computed BEFORE the lazy
    # orient_capsule import and the daemon gate, so front-door time counts against --deadline.
    # CLI consistency fix : `--deadline` used to be undefined on `tg orient`
    # (Click "No such option" exit-2).
    orient_detail = _detail_tier.resolve_compact_detail(
        compact, "standard", json_output=json_output
    )
    effective_deadline = None if no_deadline else deadline
    deadline_monotonic = _cli_deadline_monotonic(effective_deadline)

    from tensor_grep.cli.orient_capsule import build_orient_capsule

    # Task #108 (Tier-2 daemon capability): probe BEFORE the try block -- a daemon hit is already a
    # ready-built dict (no filesystem call left that could raise FileNotFoundError/ValueError), so
    # it does not need the cold path's exception handling. A miss/error/mismatch falls open to the
    # unchanged cold path below (fail-open contract). Skipped entirely when a --deadline was
    # requested (a warm session's cached repo_map cannot honor a fresh per-request scan deadline),
    # mirroring refs/callers/impact/blast-radius's own daemon gate.
    daemon_payload = (
        _self._maybe_orient_via_running_daemon(
            path=path,
            max_tokens=max_tokens,
            max_central_files=max_central_files,
            ignore=tuple(ignore),
            auto_deweight=not no_auto_deweight,
        )
        if effective_deadline is None
        else None
    )
    if daemon_payload is not None:
        payload = daemon_payload
    else:
        try:
            # Build the payload dict directly (not via build_orient_capsule_json) so json AND text
            # output share ONE code path -- matches the Cluster B pattern the other repo-scanning
            # commands use (map/context/context-render/edit-plan/agent).
            payload = build_orient_capsule(
                path,
                max_tokens=max_tokens,
                max_central_files=max_central_files,
                ignore=tuple(ignore),
                auto_deweight=not no_auto_deweight,
                deadline_seconds=effective_deadline,
                deadline_monotonic=deadline_monotonic,
            )
        except (FileNotFoundError, ValueError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

    if json_output:
        # build_orient_capsule_json's exact format (json.dumps(..., indent=2), no
        # ensure_ascii=False) -- match it here so a warm hit is byte-identical to a cold miss.
        typer.echo(_detail_tier.dump_detail_json(payload, orient_detail))
        return

    # `tg orient` has no exit-2 contract by design (see --deadline's help): a truncated scan still
    # exits 0. That makes the TEXT disclosure the ONLY signal a caller gets -- and it was absent.
    # Measured on `tg orient src/tensor_grep/cli --deadline 0.1`: exit 0, `central files (0)`, zero
    # bytes of stderr, while the `--json` arm carried `partial: true`. An agent reading that sees a
    # codebase with no central files and no reason to look further, which is the confident-false-zero
    # this surface exists to prevent -- made worse here, not better, by the deliberate exit 0.
    #
    # Leads the payload rather than trailing it, per the disclosure-position contract.
    if payload.get("partial") or payload.get("result_incomplete"):
        reason = payload.get("incomplete_reason")
        limit = payload.get("deadline_limit")
        if not isinstance(reason, str) or not reason:
            # `deadline_limit` is a dict of counters, not prose -- interpolating it verbatim
            # produced a banner containing a raw `{'deadline_exceeded': True, ...}`. Read the one
            # field a caller can act on and say it in words.
            scanned = limit.get("files_scanned") if isinstance(limit, dict) else None
            reason = (
                f"the scan stopped at the --deadline after {scanned} file(s)"
                if isinstance(scanned, int)
                else "the scan stopped at the --deadline"
            )
        typer.echo(_truncation_message(reason))
    typer.echo(f"# Codebase orientation: {payload['path']}")
    typer.echo(f"central files ({len(payload['central_files'])}):")
    for cf in payload["central_files"]:
        typer.echo(f"  {cf['file']}  (in-degree={cf['graph_score']})")
    if payload["deweighted_trees"]:
        typer.echo(f"deweighted_trees ({len(payload['deweighted_trees'])}):")
        for tree in payload["deweighted_trees"]:
            typer.echo(f"  {tree['path']}  ({', '.join(tree['reasons'])})")
    typer.echo(
        f"entry_points={len(payload['entry_points'])} "
        f"snippets={len(payload['snippets'])} ~{payload['token_estimate']} tokens"
    )


def codemap(
    path: str = typer.Argument(".", help="Directory to render a browsable code map for"),
    out: str | None = typer.Option(
        None,
        "--out",
        "--output",
        help="Output directory for pages + _coverage.json. Defaults to <path>/docs/code-map.",
    ),
    index_file: str = typer.Option(
        "index.md",
        "--index",
        help="Index filename, resolved inside --out unless given as an absolute path.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Read-only freshness check of an existing code map (no re-parse); exits 1 when stale.",
    ),
    max_repo_files: int = typer.Option(
        # Literal mirrors codemap.DEFAULT_MAX_REPO_FILES (kept literal so the heavy repo_map
        # import stays lazy, matching map's/inventory's pattern); a guard test pins them.
        50_000,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before truncating (walk-only; defaults to 50000).",
    ),
    max_symbols_per_file: int = typer.Option(
        50,
        "--max-symbols-per-file",
        min=1,
        help="Per-file symbol cap before an overflow pointer line (defaults to 50).",
    ),
    ignore: list[str] = typer.Option(
        [],
        "--ignore",
        help="Glob(s) of source files to exclude entirely (repeatable). Matched against the "
        "repo-relative path and basename, e.g. --ignore 'benchmarks/**' --ignore '*.stub.py'. "
        "Excluded paths never reach the generated pages or index.",
    ),
    deadline: float = typer.Option(
        # Literal mirrors codemap.DEFAULT_CLI_DEADLINE_SECONDS (kept literal so the heavy codemap
        # import stays lazy, matching max_repo_files' pattern above); a guard test pins them.
        60.0,
        "--deadline",
        min=0.1,
        help=(
            "Stop after N seconds, measured from CLI command entry (not just the underlying repo "
            "scan -- excludes only the ~100-200ms interpreter-startup/dispatch prefix before this "
            "command body runs), and return a partial map "
            "(partial=true, partial_reason='deadline') with whatever was found so far, instead "
            "of running unbounded. Defaults to 60s so a huge multi-root workspace can't hang an "
            "agent loop; pass --no-deadline to disable the bound."
        ),
    ),
    no_deadline: bool = typer.Option(
        False,
        "--no-deadline",
        help="Disable the default --deadline bound; let the scan run unbounded.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Render a persisted, browsable folder->file->symbol code map (lean index + per-folder pages)."""
    # Anchor deadline_monotonic at CLI command entry (closes #197/#200): computed BEFORE the lazy
    # codemap import, so import cost counts against the (non-`--check`) scanning path's budget.
    # --check is a read-only freshness check with no scan/deadline; this is a cheap no-op there.
    effective_deadline = None if no_deadline else deadline
    deadline_monotonic = _cli_deadline_monotonic(effective_deadline)

    from tensor_grep.cli.codemap import (
        CodemapRootNotDirectoryError,
        CodemapRootNotFoundError,
        build_codemap,
        check_codemap_freshness,
    )
    from tensor_grep.cli.codemap_artifacts import CodemapOutputError

    if check:
        try:
            result = check_codemap_freshness(
                path, out=out, index=index_file, max_repo_files=max_repo_files
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

        if json_output:
            typer.echo(json.dumps(result, indent=2, sort_keys=True))
        else:
            status = "fresh" if result["fresh"] else "stale"
            _safe_stdout_line(f"codemap --check: {status} -- {result['reason']}")

        if not result["fresh"]:
            raise typer.Exit(1)
        return

    try:
        payload = build_codemap(
            path,
            out=out,
            index=index_file,
            max_repo_files=max_repo_files,
            max_symbols_per_file=max_symbols_per_file,
            ignore=tuple(ignore),
            deadline_seconds=effective_deadline,
            deadline_monotonic=deadline_monotonic,
        )
    except (CodemapRootNotFoundError, CodemapRootNotDirectoryError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except (OSError, CodemapOutputError) as exc:
        typer.echo(f"tg codemap: {exc}", err=True)
        hint = (
            "Choose non-conflicting --out/--index paths or narrow PATH, then regenerate."
            if isinstance(exc, CodemapOutputError)
            else "Check destination permissions or wait for another codemap process, then retry."
        )
        typer.echo(hint, err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        # LEADING (task #329). This block used to sit BELOW the counts, which is the position
        # defect: a reader who has seen `symbols=1204` has already formed the answer by the time a
        # trailing PARTIAL line lands. `codemap` was the ONE command in this family that disclosed
        # at all, which is exactly why its ordering went unexamined for so long -- "it discloses"
        # read as "it is fine".
        if payload.get("partial"):
            _safe_stdout_line(f"PARTIAL: {payload.get('remediation', '')}")
        _safe_stdout_line(f"Code map for {payload['path']}")
        _safe_stdout_line(f"out={payload['out']} index={payload['index']}")
        _safe_stdout_line(
            f"folders={payload['folders_total']} files={payload['files_total']} "
            f"symbols={payload['symbols_total']}"
        )

    if _scan_incomplete(payload):
        raise typer.Exit(2)


def context(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query_arg: str | None = typer.Argument(
        None, help="Query text used to rank relevant repo context."
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
    max_files: int | None = typer.Option(
        None, "--max-files", min=1, help="Maximum ranked source files to include."
    ),
    max_repo_files: int | None = typer.Option(
        None, "--max-repo-files", min=1, help="Maximum repo files to scan before ranking."
    ),
    max_tokens: int = typer.Option(
        # Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS (literal keeps the heavy repo_map import lazy,
        # matching inventory's 50_000 pattern; a guard test pins them). The pack is for prompt
        # injection, so bound it by default -- an unbounded pack ballooned to >1MB (dogfood v1.19.9).
        16000,
        "--max-tokens",
        min=0,
        help="Bound the context pack to ~N tokens for prompt injection (0 = unbounded).",
    ),
    deadline: float | None = _deadline_option(
        "Stop the underlying repo scan after N seconds and return a partial pack (partial=true, deadline_limit) with whatever was found so far, instead of running unbounded. Pass --no-deadline to keep the (already default) unbounded behavior explicit."
    ),
    no_deadline: bool = typer.Option(
        False,
        "--no-deadline",
        help="Accepted for command-surface parity with codemap; a no-op since context already "
        "defaults to an unbounded --deadline.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a ranked repository context pack for edit planning."""
    from tensor_grep.cli.repo_map import build_context_pack

    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="context",
        )
        # CLI consistency fix : `--deadline` used to be undefined on
        # `tg context` (Click "No such option" exit-2).
        effective_deadline = None if no_deadline else deadline
        payload = build_context_pack(
            resolved_query,
            resolved_path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_tokens=max_tokens,
            deadline_seconds=effective_deadline,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Build the payload ONCE and gate both branches on it (mirrors `map`'s cold-path contract,
    # Cluster B 2026-07-06): the old json branch called build_context_pack_json + returned early,
    # which meant a >max_repo_files scan (default cap) silently truncated and always exited 0 --
    # `context` was the only command in this family that never gated on `_scan_incomplete` (audit #9).
    if json_output:
        typer.echo(json.dumps(payload))
    else:
        from tensor_grep.cli.formatters.human_fmt import format_context_human

        _emit_scan_incompleteness_banner(payload)
        typer.echo(format_context_human(payload))

    if _scan_incomplete(payload):
        raise typer.Exit(2)


def _daemon_directory_path(path: str) -> str | None:
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except OSError:
        return None
    return str(resolved) if resolved.is_dir() else None


def _session_daemon_autostart_enabled() -> bool:
    """TG_SESSION_DAEMON_AUTOSTART opt-out for the default Tier-1 warm-daemon fast path.

    DEFAULT ON: unset -- or any value other than an explicit falsy token
    (``0``/``false``/``no``/``off``, see ``env_flag_disabled`` in runtime_paths.py) -- routes
    defs/impact/refs/callers/blast-radius through a running ``tg session daemon``, non-blocking
    auto-spawning one on a miss. Repeated requests can reuse the daemon snapshot; actual
    latency depends on the workload. Set an explicit falsy token to use the cold path.

    Auto-forced OFF whenever CI or GITHUB_ACTIONS is set, regardless of the flag's own value,
    so a CI job can never leave a background session-daemon process (idle-lived up to
    TG_SESSION_DAEMON_IDLE_SECONDS, 900s default) running past the job that spawned it.
    """
    if env_flag_enabled("CI") or env_flag_enabled("GITHUB_ACTIONS"):
        return False
    return not env_flag_disabled("TG_SESSION_DAEMON_AUTOSTART")


def _maybe_symbol_command_via_running_daemon(
    *,
    command: str,
    path: str,
    symbol: str,
    provider: str,
    max_repo_files: int,
    max_tests: int | None = None,
    max_depth: int | None = None,
) -> dict[str, Any] | None:
    """Fail-open Tier-1 default fast path (task #94 Part A) for defs/impact/refs/callers/
    blast_radius.

    Mirrors the existing ``_maybe_context_render_via_running_daemon`` /
    ``_maybe_edit_plan_via_running_daemon`` fail-open shape (probe-only, ``except Exception:
    return None``) with one addition: on a probe MISS (no daemon reachable yet) it fires a
    non-blocking spawn so a LATER call is warm, while THIS call still returns None and the
    caller runs the existing cold path unchanged (must-fix 3 -- cold call #1 must never block
    on daemon warmup).

    Returns None (forcing the caller's cold path) whenever: the flag is off, a non-native
    provider was requested (the daemon session is native-only, same rule as context-render/
    edit-plan), the path does not resolve to a directory, no daemon could be reached, or the
    daemon responded with an error. A `refresh_on_stale=True` request (mirroring
    ``_maybe_context_render_via_running_daemon``) means a session whose files changed on disk
    is refreshed once before being served, so warm output matches cold output on a changed
    tree (must-fix 5).
    """
    if not _self._session_daemon_autostart_enabled():
        return None
    if provider != "native":
        return None
    daemon_path = _daemon_directory_path(path)
    if daemon_path is None:
        return None
    try:
        from tensor_grep.cli.session_daemon import (
            maybe_autostart_session_daemon_nonblocking,
            request_running_session_daemon,
        )

        request: dict[str, Any] = {
            "command": command,
            "path": daemon_path,
            "symbol": symbol,
            "provider": provider,
            "refresh_on_stale": True,
            "max_repo_files": max_repo_files,
        }
        if max_tests is not None:
            request["max_tests"] = max_tests
        if max_depth is not None:
            request["max_depth"] = max_depth
        payload = request_running_session_daemon(daemon_path, request)
        if payload is None:
            # No daemon reachable yet. Fire-and-forget spawn so a LATER call is warm; THIS
            # call must not block on daemon startup -- run the cold path below (must-fix 3).
            maybe_autostart_session_daemon_nonblocking(daemon_path)
            return None
        if "error" in payload:
            return None
        return payload
    except Exception:
        return None


def _maybe_orient_via_running_daemon(
    *,
    path: str,
    max_tokens: int,
    max_central_files: int,
    ignore: tuple[str, ...],
    auto_deweight: bool,
) -> dict[str, Any] | None:
    """Task #108 (Tier-2 daemon capability): fail-open warm-daemon fast path for `tg orient`, mirroring
    `_maybe_symbol_command_via_running_daemon`'s probe/autostart-on-miss shape (task #94 Tier-1)
    byte-for-byte -- gated behind the SAME `_session_daemon_autostart_enabled` flag Tier-1 uses
    (no separate flag; inherits the CI/GITHUB_ACTIONS force-off), and a probe MISS fires a
    non-blocking autostart so a LATER call is warm while THIS call still runs the cold path
    below. `orient` has no semantic-provider concept (no --provider flag), so unlike the symbol
    commands/agent there is no provider gate here.
    """
    if not _self._session_daemon_autostart_enabled():
        return None
    daemon_path = _daemon_directory_path(path)
    if daemon_path is None:
        return None
    try:
        from tensor_grep.cli.session_daemon import (
            maybe_autostart_session_daemon_nonblocking,
            request_running_session_daemon,
        )

        request: dict[str, Any] = {
            "command": "orient",
            "path": daemon_path,
            "refresh_on_stale": True,
            "max_tokens": max_tokens,
            "max_central_files": max_central_files,
            "ignore": list(ignore),
            "auto_deweight": auto_deweight,
        }
        payload = request_running_session_daemon(daemon_path, request)
        if payload is None:
            maybe_autostart_session_daemon_nonblocking(daemon_path)
            return None
        if "error" in payload:
            return None
        return payload
    except Exception:
        return None


def _maybe_agent_via_running_daemon(
    *,
    path: str,
    query: str,
    max_files: int,
    max_sources: int,
    max_tokens: int | None,
    max_repo_files: int,
    model: str | None,
    provider: str,
    gpu_device_ids: list[int] | None,
    ignore: tuple[str, ...],
) -> dict[str, Any] | None:
    """Task #108 (Tier-2 daemon capability): fail-open warm-daemon fast path for `tg agent`, mirroring
    `_maybe_symbol_command_via_running_daemon`'s probe/autostart-on-miss shape. Two additional
    refusals beyond the symbol-command template: a non-native provider (same native-only rule as
    every other daemon-served command) and an explicit `--gpu-device-ids` request -- the GPU
    evidence probe shells out to a fresh `tg search` subprocess
    (`agent_capsule._agent_gpu_evidence`) and must never run inside a long-lived daemon worker
    thread, so it always takes the cold path (the design review's "GPU-in-agent stays
    cold/CLI-only" scope note).

    TRAP A (audit #107 class, task #108 design review): a daemon response carrying the internal
    `daemon_evidence_unreliable` sentinel (stamped by `agent_capsule.build_agent_capsule_from_map`
    when its call-site-evidence collector hit a no_match it cannot trust -- no literal-seed rescue
    available on the daemon's cached map) is discarded here exactly like a transport error, so the
    caller's cold path (which DOES have the rescue) runs instead.
    """
    if not _self._session_daemon_autostart_enabled():
        return None
    if provider != "native":
        return None
    if gpu_device_ids:
        return None
    daemon_path = _daemon_directory_path(path)
    if daemon_path is None:
        return None
    try:
        from tensor_grep.cli.session_daemon import (
            maybe_autostart_session_daemon_nonblocking,
            request_running_session_daemon,
        )

        request: dict[str, Any] = {
            "command": "agent",
            "path": daemon_path,
            "query": query,
            "provider": provider,
            "refresh_on_stale": True,
            "max_files": max_files,
            "max_sources": max_sources,
            "max_tokens": max_tokens,
            "max_repo_files": max_repo_files,
            "model": model,
            "ignore": list(ignore),
        }
        payload = request_running_session_daemon(daemon_path, request)
        if payload is None:
            maybe_autostart_session_daemon_nonblocking(daemon_path)
            return None
        if "error" in payload:
            return None
        if payload.get("daemon_evidence_unreliable"):
            return None
        return payload
    except Exception:
        return None


def _maybe_context_render_via_running_daemon(
    *,
    path: str,
    query: str,
    max_files: int,
    max_repo_files: int,
    max_sources: int,
    max_symbols_per_file: int,
    max_render_chars: int | None,
    max_tokens: int | None,
    model: str | None,
    optimize_context: bool,
    render_profile: str,
    provider: str,
    profile: bool,
) -> dict[str, Any] | None:
    if provider != "native":
        return None
    daemon_path = _daemon_directory_path(path)
    if daemon_path is None:
        return None
    try:
        from tensor_grep.cli.session_daemon import request_running_session_daemon

        payload = request_running_session_daemon(
            daemon_path,
            {
                "command": "context_render",
                "path": daemon_path,
                "query": query,
                "refresh_on_stale": True,
                "max_files": max_files,
                "max_sources": max_sources,
                "max_symbols_per_file": max_symbols_per_file,
                "max_render_chars": max_render_chars,
                "max_tokens": max_tokens,
                "model": model,
                "optimize_context": optimize_context,
                "render_profile": render_profile,
                "profile": profile,
                "max_repo_files": max_repo_files,
            },
        )
        if payload is None or "error" in payload:
            return None
        return payload
    except Exception:
        return None


def _maybe_edit_plan_via_running_daemon(
    *,
    path: str,
    query: str,
    max_files: int,
    max_repo_files: int,
    max_sources: int | None,
    max_tokens: int | None,
    max_symbols: int,
    provider: str,
    profile: bool,
) -> dict[str, Any] | None:
    if provider != "native":
        return None
    daemon_path = _daemon_directory_path(path)
    if daemon_path is None:
        return None
    try:
        from tensor_grep.cli.session_daemon import request_running_session_daemon

        payload = request_running_session_daemon(
            daemon_path,
            {
                "command": "context_edit_plan",
                "path": daemon_path,
                "query": query,
                "refresh_on_stale": True,
                "max_files": max_files,
                "max_sources": max_sources,
                "max_tokens": max_tokens,
                "max_symbols": max_symbols,
                "profile": profile,
                "max_repo_files": max_repo_files,
            },
        )
        if payload is None or "error" in payload:
            return None
        return payload
    except Exception:
        return None


def context_render(
    path: str = typer.Argument(".", help="File or directory to inventory"),
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
        help="Maximum repo files to scan before returning a bounded result.",
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
    provider: str = typer.Option(
        "native",
        "--provider",
        help="Semantic provider for primary target proof: native, lsp, or hybrid.",
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-phase profiling in JSON output."
    ),
    citations: bool = typer.Option(False, "--citations", help="Emit citation ranges."),
    deadline: float | None = _deadline_option(
        "Stop after N seconds, measured from CLI command entry (not just the underlying repo scan -- excludes only the ~100-200ms interpreter-startup/dispatch prefix before this command body runs), and return a partial bundle (partial=true, deadline_limit) with whatever was found so far, instead of running unbounded. Pass --no-deadline to keep the (already default) unbounded behavior explicit."
    ),
    no_deadline: bool = typer.Option(
        False,
        "--no-deadline",
        help="Accepted for command-surface parity with codemap; a no-op since context-render "
        "already defaults to an unbounded --deadline.",
    ),
    compact: bool = _detail_tier.compact_option(),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready repository context bundle for edit planning."""
    _compact_payloads.validate_compact_flags(compact, json_output=json_output, citations=citations)
    # Anchor deadline_monotonic at CLI command entry (closes #197/#200): computed BEFORE the lazy
    # repo_map import, path resolution, and daemon gate, so front-door time counts against --deadline.
    # CLI consistency fix : `--deadline` used to be undefined on
    # `tg context-render` (Click "No such option" exit-2).
    effective_deadline = None if no_deadline else deadline
    deadline_monotonic = _cli_deadline_monotonic(effective_deadline)

    from tensor_grep.cli.repo_map import build_context_render

    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="context-render",
        )
        resolved_render_profile = render_profile or ("llm" if json_output else "full")
        resolved_optimize_context = optimize_context or (json_output and render_profile is None)
        # Skip the warm-daemon fast path entirely when a --deadline was requested (a warm
        # session's cached repo_map cannot honor a fresh per-request scan deadline) -- mirrors
        # refs/callers/impact/blast-radius's own daemon gate.
        daemon_payload = (
            _self._maybe_context_render_via_running_daemon(
                path=resolved_path,
                query=resolved_query,
                max_files=max_files,
                max_repo_files=max_repo_files,
                max_sources=max_sources,
                max_symbols_per_file=max_symbols_per_file,
                max_render_chars=max_render_chars,
                max_tokens=max_tokens,
                model=model,
                optimize_context=resolved_optimize_context,
                render_profile=resolved_render_profile,
                provider=provider,
                profile=profile,
            )
            if effective_deadline is None and not citations
            else None
        )
        if daemon_payload is not None:
            # Output-before-exit (Cluster B, 2026-07-06): the warm-daemon path must honor the same
            # exit-2-on-scan-truncation contract as the cold path below -- a truncated daemon payload
            # still prints in full, then exits 2, instead of a silent exit 0 that reads as complete.
            if json_output:
                typer.echo(_compact_payloads.dump_render(daemon_payload, compact))
            else:
                _emit_scan_incompleteness_banner(daemon_payload)
                typer.echo(str(daemon_payload.get("rendered_context", "")))
            if _scan_incomplete(daemon_payload):
                raise typer.Exit(2)
            return

        payload = build_context_render(
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
            semantic_provider=provider,
            profile=profile,
            deadline_seconds=effective_deadline,
            deadline_monotonic=deadline_monotonic,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Cold path (Cluster B, 2026-07-06): build the payload once and dump it here (byte-identical to
    # the old build_context_render_json helper: separators=(",", ":") for an "llm" render profile,
    # else indent=2) so both json and text branches share the same scan-truncation gate below --
    # output the full payload FIRST, then exit 2 if the scan itself (not just the output) was capped.
    if citations:
        _citations.emit_payload_citations(payload, resolved_path, json_output=json_output)
    elif json_output:
        typer.echo(_compact_payloads.dump_context_render_json(payload, compact=compact))
    else:
        _emit_scan_incompleteness_banner(payload)
        typer.echo(payload["rendered_context"])

    if _scan_incomplete(payload):
        raise typer.Exit(2)


def register(app: typer.Typer) -> None:
    app.command()(map)
    app.command()(inventory)
    app.command(name="docs-coverage")(docs_coverage)
    app.command()(orient)
    app.command()(codemap)
    app.command()(context)
    app.command(name="context-render")(context_render)
