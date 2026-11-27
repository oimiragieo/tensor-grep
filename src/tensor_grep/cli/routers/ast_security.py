from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import cast

import typer

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import ast_scan as _ast_scan
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
# Re-exports from the modules split out of this file on 2026-08-20 (see
# cli/_main_binding.py). EVERY moved name is
# rebound here, not only the ones a scan can prove are read. The test suite patches many of
# them on `main` and the moved code reads them back through `_self`, so the patch target must
# not move with the code -- and tests reach them through local aliases, so 'nothing references
# it' would be a claim about the scan, not about the code. Rebinding all of them keeps
# `main.<name>` exactly the surface it was before the split. Plain assignment rather than
# `from ... import x as x` only because isort expands the aliased form to three lines each.
# --- from cli/ast_scan.py ----------------------------------------------------
from tensor_grep.cli.error_handling import _exit_search_error
from tensor_grep.cli.investigation_options import (
    _note_late_rerank_degraded as _note_late_rerank_degraded,
)
from tensor_grep.cli.investigation_options import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)
from tensor_grep.cli.output_envelope import (
    _VERSION_UNAVAILABLE_SENTINEL,
    _cli_package_version,
    _json_output_version,
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
from tensor_grep.sidecar import DEFAULT_CLASSIFY_MAX_LINES

_build_rulesets_payload = _ast_scan._build_rulesets_payload
_filter_ast_rule_specs = _ast_scan._filter_ast_rule_specs
_load_inline_rule_specs = _ast_scan._load_inline_rule_specs

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


def classify(
    file_path: str | None = typer.Argument(
        None, help="The log file to classify (omit when using --stdin or --text)."
    ),
    format_type: str | None = typer.Option(None, "--format", help="Output format"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output"),
    max_lines: int = typer.Option(
        DEFAULT_CLASSIFY_MAX_LINES,
        "--max-lines",
        help="Maximum input lines to emit in JSON output (0 disables the cap).",
    ),
    stdin_flag: bool = typer.Option(False, "--stdin", help="Read text from stdin."),
    text: str | None = typer.Option(None, "--text", help="Classify a literal string."),
) -> None:
    """Run log classification with local heuristics or an explicit cyBERT provider."""
    import json

    from tensor_grep.io.reader_fallback import FallbackReader
    from tensor_grep.sidecar import (
        _apply_classify_line_budget,
        _classify_lines_with_metadata,
        _enrich_classifications,
    )

    if stdin_flag and text is not None:
        typer.echo("Error: tg classify --stdin cannot be combined with --text", err=True)
        raise typer.Exit(1)
    if stdin_flag and file_path is not None:
        typer.echo(
            "Error: tg classify --stdin cannot be combined with a file path argument", err=True
        )
        raise typer.Exit(1)
    if text is not None and file_path is not None:
        typer.echo(
            "Error: tg classify --text cannot be combined with a file path argument", err=True
        )
        raise typer.Exit(1)
    if json_output and format_type is not None and format_type != "json":
        typer.echo("Error: tg classify --json cannot be combined with --format", err=True)
        raise typer.Exit(2)

    # Mirrors sidecar.py's _classify_payload: content supplied directly (stdin/--text) skips
    # the file-read branch entirely, and empty content degrades cleanly instead of hanging or
    # crashing (audit MED's fix for the sidecar's own empty-content branch, mirrored here for
    # front-door parity).
    source_path: str | None = None
    if stdin_flag:
        content = sys.stdin.read()
        lines = content.splitlines(keepends=True)
        if content and not lines:
            lines = [content]
        if not lines:
            typer.echo("no content to classify (input is empty)", err=True)
            raise typer.Exit(1)
    elif text is not None:
        lines = text.splitlines(keepends=True)
        if text and not lines:
            lines = [text]
        if not lines:
            typer.echo("no content to classify (input is empty)", err=True)
            raise typer.Exit(1)
    else:
        if file_path is None:
            typer.echo(
                "Error: classify requires a file path, or --stdin, or --text <literal>",
                err=True,
            )
            raise typer.Exit(1)
        classify_path = Path(file_path).expanduser()
        if not classify_path.exists():
            typer.echo(
                "Error: classify expects a file path; use --text for a literal string or "
                f"--stdin to read from stdin. Received: {file_path}",
                err=True,
            )
            raise typer.Exit(1)

        source_path = file_path
        reader = FallbackReader()
        lines = list(reader.read_lines(file_path))
        if not lines:
            sys.exit(1)

    budgeted_lines, line_budget = _apply_classify_line_budget(lines, max_lines)
    results, classification_backend = _classify_lines_with_metadata(budgeted_lines)

    if json_output or format_type == "json":
        data = {
            "version": _json_output_version(),
            "schema_version": _json_output_version(),
            "classification_backend": classification_backend,
            "line_budget": line_budget,
            "classifications": _enrich_classifications(
                results,
                budgeted_lines,
                source_path=source_path,
            ),
        }
        print(json.dumps(data))
    else:
        from tensor_grep.cli.formatters.human_fmt import format_classify_human

        typer.echo(format_classify_human(results, budgeted_lines, line_budget))


def rulesets(
    json_output: bool = typer.Option(False, "--json", help="Emit structured ruleset metadata."),
) -> None:
    """List built-in security and compliance rule packs."""
    payload = _build_rulesets_payload()
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    if not payload["rulesets"]:
        typer.echo("No built-in rulesets are currently registered.")
        return

    # ONE banner, before the listing -- not one warning per ruleset. Six repetitions of the same
    # sentence is noise that trains the reader to skip it.
    if payload.get("rulesets_runnable") is False:
        typer.echo(f"WARNING: {payload['rulesets_unavailable_reason']}")
        typer.echo("")

    for ruleset in cast(list[dict[str, object]], payload["rulesets"]):
        typer.echo(
            f"{ruleset['name']}: {ruleset['description']} "
            f"[category={ruleset['category']} status={ruleset['status']} "
            f"languages={','.join(cast(list[str], ruleset['languages']))} "
            f"rules={ruleset['rule_count']}]"
        )


def audit_help() -> None:
    """Audit command entry points: audit-verify, audit-history, audit-diff, review-bundle."""
    typer.echo("Audit commands:")
    typer.echo("  tg audit-verify MANIFEST [--json]")
    typer.echo("  tg audit-history [PATH] [--json]")
    typer.echo("  tg audit-diff PREVIOUS CURRENT [--json]")
    typer.echo("  tg review-bundle create --manifest MANIFEST [--json]")
    typer.echo("  tg review-bundle verify BUNDLE [--json]")


def scan(
    paths: list[str] | None = typer.Argument(
        None,
        help="Optional scan paths for tensor-grep's bounded AST scan slice.",
    ),
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config"
    ),
    rule_file: str | None = typer.Option(
        None,
        "--rule",
        "-r",
        help="Scan with a single ast-grep rule file without requiring sgconfig.",
    ),
    ruleset: str | None = typer.Option(
        None,
        "--ruleset",
        help="Built-in security/compliance ruleset to scan without sgconfig.",
    ),
    inline_rules: str | None = typer.Option(
        None,
        "--inline-rules",
        help="Scan using inline ast-grep rule YAML without requiring sgconfig.",
    ),
    filter_regex: str | None = typer.Option(
        None,
        "--filter",
        "-f",
        help="Filter loaded rule IDs with a regex before scanning.",
    ),
    path: str = typer.Option(
        ".",
        "--path",
        help="Scan root when using a built-in ruleset.",
    ),
    language: str | None = typer.Option(
        None,
        "--language",
        help="Language override when using a built-in ruleset.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured scan findings.",
    ),
    sarif: bool = typer.Option(
        False,
        "--sarif",
        help="Emit findings as a SARIF v2.1.0 log (GitHub code scanning, Azure DevOps, Sonar).",
    ),
    output_format: str | None = typer.Option(
        None,
        "--format",
        help="Output format ('sarif' aliases --sarif, 'json' aliases --json).",
    ),
    baseline: str | None = typer.Option(
        None,
        "--baseline",
        help="Compare matched findings against a saved baseline fingerprint file.",
    ),
    write_baseline: str | None = typer.Option(
        None,
        "--write-baseline",
        help="Write the current matched finding fingerprints to a baseline file.",
    ),
    suppressions: str | None = typer.Option(
        None,
        "--suppressions",
        help="Mark matched findings present in a suppression fingerprint file as suppressed.",
    ),
    write_suppressions: str | None = typer.Option(
        None,
        "--write-suppressions",
        help="Write the current matched finding fingerprints to a suppression file.",
    ),
    justification: str | None = typer.Option(
        None,
        "--justification",
        help="Required justification text when writing suppressions.",
    ),
    include_evidence_snippets: bool = typer.Option(
        False,
        "--include-evidence-snippets",
        help="Attach bounded raw match snippets to structured ruleset scan evidence rows.",
    ),
    max_evidence_snippets_per_file: int = typer.Option(
        1,
        "--max-evidence-snippets-per-file",
        min=1,
        help="Maximum number of snippets to keep per matched file when snippet evidence is enabled.",
    ),
    max_evidence_snippet_chars: int = typer.Option(
        120,
        "--max-evidence-snippet-chars",
        min=1,
        help="Maximum characters to keep per evidence snippet when snippet evidence is enabled.",
    ),
    glob: list[str] | None = typer.Option(
        None,
        "--glob",
        "-g",
        help="Include/exclude files matching a glob before executing scan rules.",
    ),
    type_filter: list[str] | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Scan only files with this extension/type name. May be repeated.",
    ),
    max_depth: int | None = typer.Option(
        None,
        "--max-depth",
        min=0,
        help="Limit directory traversal depth for broad scan roots.",
    ),
    allow_broad_generated_scan: bool = typer.Option(
        False,
        "--allow-broad-generated-scan",
        help=(
            "Permit broad AST scans through temp, cache, dependency, system, or "
            "multi-project workspace roots. Prefer scoped --path or --max-depth."
        ),
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show scan banners and zero-match rules."
    ),
) -> None:
    """Scan code with tensor-grep's bounded AST rule/config surface."""
    from tensor_grep.backends.ast_backend import normalize_ast_language
    from tensor_grep.cli.scan_guardrails import BroadScanRefusedError

    def _scan_err(error: str, detail: str, code: int = 1) -> None:
        _exit_search_error(error, detail, json_mode=bool(json_output), exit_code=code)

    if output_format is not None:
        fmt = output_format.strip().lower()
        if fmt == "sarif":
            sarif = True
        elif fmt != "json":
            _scan_err(
                "invalid_argument",
                f"Unsupported --format {output_format!r}; expected 'sarif' or 'json'.",
            )
        else:
            json_output = True

    inline_source_count = sum(item is not None for item in (ruleset, inline_rules, rule_file))
    if inline_source_count > 1:
        _scan_err(
            "invalid_argument",
            "--rule, --inline-rules, and --ruleset are mutually exclusive.",
        )
    if rule_file is not None and filter_regex is not None:
        _scan_err("invalid_argument", "--filter is incompatible with --rule.")
    scan_paths = list(paths or [])
    if scan_paths and path != ".":
        _scan_err("invalid_argument", "positional PATHS are incompatible with --path.")
    effective_scan_paths = scan_paths or [path]

    # FAIL CLOSED on a scan root that does not exist. Rationale, and the `tg search` precedent
    # whose `path_not_found` taxonomy this reuses, live in the helper's docstring.
    from tensor_grep.cli.scan_guardrails import missing_scan_paths

    absent_scan_paths = missing_scan_paths(effective_scan_paths)
    if absent_scan_paths:
        _exit_search_error(
            detail="scan path does not exist: " + ", ".join(absent_scan_paths),
            error="path_not_found",
            json_mode=bool(json_output),
        )

    candidate_files: list[str] | None = None
    project_scan_fast_path = False
    prepared_scope = None
    if ruleset:
        try:
            from tensor_grep.cli.ruleset_scope import _prepare_builtin_ruleset_scan

            project_cfg, rules, prepared_scope = _prepare_builtin_ruleset_scan(
                ruleset,
                language,
                scan_paths=effective_scan_paths,
                scan_globs=glob,
                scan_types=type_filter,
                scan_max_depth=max_depth,
                allow_broad_generated_scan=allow_broad_generated_scan,
                filter_regex=filter_regex,
            )
        except BroadScanRefusedError as exc:
            _scan_err("broad_scan_refused", str(exc), 2)
        except ValueError as exc:
            _scan_err("invalid_ruleset", str(exc))
        scan_banner = f"Scanning project using built-in ruleset {project_cfg['name']} ({project_cfg['public_language']})"
        routing_reason = "builtin-ruleset-scan"
    elif rule_file is not None:
        rule_path = Path(rule_file).expanduser().resolve()
        try:
            rules = _load_inline_rule_specs(
                rule_path.read_text(encoding="utf-8"),
                default_language=language,
            )
        except OSError as exc:
            _scan_err("rule_file_error", f"failed to read rule file {rule_path}: {exc}")
        except ValueError as exc:
            _scan_err("invalid_rule", str(exc))
        if not rules:
            _scan_err("invalid_rule", f"No valid rule was found in {rule_path}.")
        inferred_language = (
            normalize_ast_language(language) if language else str(rules[0]["language"])
        )
        project_cfg = {
            "config_path": rule_path,
            "root_dir": Path(effective_scan_paths[0]).resolve(),
            "rule_dirs": [],
            "test_dirs": [],
            "language": inferred_language,
        }
        scan_banner = f"Scanning project using rule file {rule_path}"
        routing_reason = "ast-single-rule-scan"
    elif inline_rules is not None:
        try:
            rules = _load_inline_rule_specs(inline_rules, default_language=language)
        except ValueError as exc:
            _scan_err("invalid_rule", str(exc))
        try:
            # Same uniform --filter application as --ruleset above (audit #22): previously silently
            # ignored here, so a --inline-rules run always scanned every parsed rule regardless of
            # --filter.
            rules = _filter_ast_rule_specs(rules, filter_regex)
        except ValueError as exc:
            _scan_err("invalid_filter", str(exc))
        if not rules:
            _scan_err("invalid_rule", "No valid inline rules were found.")
        inferred_language = (
            normalize_ast_language(language) if language else str(rules[0]["language"])
        )
        project_cfg = {
            "config_path": "inline-rules",
            "root_dir": Path(effective_scan_paths[0]).resolve(),
            "rule_dirs": [],
            "test_dirs": [],
            "language": inferred_language,
        }
        scan_banner = "Scanning project using inline AST rules"
        routing_reason = "ast-inline-rules-scan"
    else:
        from tensor_grep.cli.ast_workflows import _load_ast_project_data

        try:
            project_cfg, rules, candidate_files, _test_data, _hints = _load_ast_project_data(config)
        except (FileNotFoundError, OSError, ValueError) as exc:
            _scan_err("scan_config_error", str(exc))
        try:
            rules = _filter_ast_rule_specs(rules, filter_regex)
        except ValueError as exc:
            _scan_err("invalid_filter", str(exc))

        if not rules:
            _scan_err(
                "invalid_rule",
                "No valid rules found after applying configuration and filters.",
            )
        scan_banner = "Scanning project using adaptive AST routing"
        routing_reason = "ast-project-scan"
        project_scan_fast_path = True
        if scan_paths:
            project_scan_fast_path = False

    try:
        payload = _self._run_ast_scan_payload(
            project_cfg,
            rules,
            routing_reason=routing_reason,
            scan_paths=scan_paths or None,
            candidate_files=candidate_files,
            prepared_scope=prepared_scope,
            project_scan_fast_path=project_scan_fast_path,
            ruleset_name=cast(str, project_cfg["name"]) if ruleset else None,
            scan_globs=glob,
            scan_types=type_filter,
            scan_max_depth=max_depth,
            allow_broad_generated_scan=allow_broad_generated_scan,
            baseline_path=baseline,
            write_baseline_path=write_baseline,
            suppressions_path=suppressions,
            write_suppressions_path=write_suppressions,
            suppression_justification=justification,
            include_evidence_snippets=include_evidence_snippets,
            max_evidence_snippets_per_file=max_evidence_snippets_per_file,
            max_evidence_snippet_chars=max_evidence_snippet_chars,
        )
    except BroadScanRefusedError as exc:
        _scan_err("broad_scan_refused", str(exc), 2)
    except (ValueError, RuntimeError) as exc:
        _scan_err("scan_error", str(exc))
    if sarif:
        # #310. Rendered from the SAME payload `--json` emits, deliberately: a second extraction
        # path would be a second place for the completeness fields to be forgotten, which is the
        # defect family this whole surface exists to close. `sarif.py` maps `partial` /
        # `unreadable_paths` onto `invocations[].executionSuccessful`, so an incomplete scan is
        # visible to a CI gate that never reads our exit code.
        # The exit code is deliberately NOT changed here. `tg scan` returns 0 on a disclosed
        # partial today (#299), and flipping that is a contract change with its own consumers --
        # exactly the six-consumer surprise that #276 slice C0 had to clear first. SARIF carries
        # the signal in-band instead.
        from tensor_grep.cli.sarif import scan_payload_to_sarif

        # `base_path` is what makes the output usable by the consumer that matters: the payload
        # carries absolute paths, and GitHub code scanning resolves URIs against the repo root.
        tool_version = _cli_package_version()
        typer.echo(
            json.dumps(
                scan_payload_to_sarif(
                    payload,
                    tool_version=tool_version,
                    base_path=str(project_cfg.get("root_dir") or ""),
                    version_unavailable=tool_version == _VERSION_UNAVAILABLE_SENTINEL,
                ),
                indent=2,
            )
        )
        raise typer.Exit(1 if payload.get("error") else 0)

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        raise typer.Exit(1 if payload.get("error") else 0)

    if payload.get("error"):
        typer.echo(f"warning: {cast(dict[str, object], payload['error'])['message']}")
        raise typer.Exit(1)

    from tensor_grep.cli.formatters.human_fmt import format_scan_human

    stdout_lines, stderr_lines = format_scan_human(
        payload,
        scan_banner=scan_banner,
        config_path=project_cfg["config_path"],
        refusal_reason=project_cfg.get("refusal_reason"),
        verbose=verbose,
    )
    for line in stdout_lines:
        typer.echo(line)
    for line in stderr_lines:
        typer.echo(line, err=True)


def test(
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit structured JSON test results."),
) -> None:
    """Test structural rules in tensor-grep's bounded AST workflow slice."""
    from tensor_grep.cli import ast_workflows

    exit_code = ast_workflows.test_command(config, json_output=json_output)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


def _validate_ast_new_name(name: str) -> None:
    if (
        name in {".", ".."}
        or not name
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.~#-"
            for c in name
        )
    ):
        raise ValueError(f"Invalid item name {name!r}; use letters, digits, _, ., ~, #, or -.")


def new(
    command: str | None = typer.Argument(
        None,
        help="Scaffold kind for tensor-grep's bounded AST workflow: project, rule, test, or util.",
    ),
    name: str | None = typer.Argument(None, help="Name for project/rule/test/util scaffolds."),
    lang: str | None = typer.Option(None, "--lang", "-l", help="Language for generated items."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Accept default scaffold choices without prompting."
    ),
    base_dir: Path = typer.Option(
        Path("."), "--base-dir", "-b", help="Directory where scaffold files are created."
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to sgconfig.yml for selecting configured rule/test/util directories.",
    ),
) -> None:
    """Create bounded AST workflow project, rule, test, or util scaffolds."""
    _ = yes
    scaffold_kind = command or "project"
    try:
        if lang is not None and not lang.strip():
            raise ValueError(f"Error: Invalid --lang {json.dumps(lang)}; use a bare language name.")
        if scaffold_kind == "project":
            project_dir = base_dir
            if name is not None:
                _validate_ast_new_name(name)
                project_dir = base_dir / name
            from tensor_grep.cli.ast_scaffold import _write_ast_project_scaffold

            config_path = _write_ast_project_scaffold(project_dir, lang or "python")
            typer.echo(f"Initialized new tensor-grep structural search project in {config_path}.")
            return

        if scaffold_kind not in {"rule", "test", "util"}:
            raise ValueError(
                "Unsupported scaffold kind "
                f"{scaffold_kind!r}; expected project, rule, test, or util."
            )
        if name is None:
            raise ValueError(f"tg new {scaffold_kind} requires a name.")
        _validate_ast_new_name(name)

        project_cfg: dict[str, object] | None = None
        if config is not None:
            from tensor_grep.cli.ast_scaffold_resolve import load_ast_scaffold_config

            project_cfg = load_ast_scaffold_config(config, scaffold_kind)

        if scaffold_kind == "rule":
            from tensor_grep.cli.ast_scaffold import _ast_rule_scaffold_contents

            target_dir = (
                cast(Path, project_cfg["root_dir"]) / cast(list[str], project_cfg["rule_dirs"])[0]
                if project_cfg is not None
                else base_dir / "rules"
            )
            target_path = target_dir / f"{name}.yml"
            contents = _ast_rule_scaffold_contents(name, lang or "python")
        elif scaffold_kind == "test":
            from tensor_grep.cli.ast_scaffold_resolve import resolve_ast_test_scaffold

            target_path, contents = resolve_ast_test_scaffold(base_dir, project_cfg, name, lang)
            target_dir = target_path.parent
        else:
            target_dir = (
                cast(Path, project_cfg["root_dir"]) / cast(str, project_cfg["utils_dir"])
                if project_cfg is not None
                else base_dir / "utils"
            )
            target_path = target_dir / f"{name}.yml"
            contents = f"id: {json.dumps(name, ensure_ascii=False)}\npattern: ''\n"

        if os.path.lexists(target_path):
            raise FileExistsError(f"Scaffold target already exists: {target_path}")
        target_dir.mkdir(parents=True, exist_ok=True)
        _index_lock.atomic_write_bytes_anchored(
            target_path, contents.encode("utf-8"), mode=0o644, replace=False
        )
    except (FileExistsError, ValueError, OSError) as exc:
        message = str(exc)
        if scaffold_kind == "test" and not message.startswith("Error:"):
            message = f"Error: {message}"
        typer.echo(message, err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Created {scaffold_kind} scaffold in {target_path}.")


def ast_info(
    json_output: bool = typer.Option(
        False, "--json", help="Output supported AST languages as JSON."
    ),
) -> None:
    """List supported AST language identifiers."""
    from tensor_grep.cli.ast_workflows import ast_info_command

    ast_info_command(json_output=json_output)


def run(
    arguments: list[str] | None = typer.Argument(
        None,
        help="The positional AST pattern and optional path, or just path when --pattern is used.",
    ),
    pattern_option: str | None = typer.Option(
        None,
        "--pattern",
        "-p",
        help="The AST pattern to search for, matching ast-grep's option form.",
    ),
    rewrite: str | None = typer.Option(None, "--rewrite", "-r", help="Replacement pattern."),
    lang: str | None = typer.Option(None, "--lang", "-l", help="Language for AST parsing."),
    apply: bool = typer.Option(False, "--apply", help="Apply the rewrite to files."),
    verify: bool = typer.Option(False, "--verify", help="Verify the rewrite with tests."),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
    checkpoint: bool = typer.Option(False, "--checkpoint", help="Enable edit checkpoints."),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Start interactive edit session"
    ),
    update_all: bool = typer.Option(
        False,
        "--update-all",
        "-U",
        help="ast-grep-compatible alias for applying all rewrite edits.",
    ),
    selector: str | None = typer.Option(
        None,
        "--selector",
        help="ast-grep matcher selector for read-only structural search.",
    ),
    strictness: str | None = typer.Option(
        None,
        "--strictness",
        help="ast-grep strictness control for read-only structural search.",
    ),
    stdin: bool = typer.Option(
        False,
        "--stdin",
        help="Read source code from stdin for read-only structural search.",
    ),
    globs: list[str] | None = typer.Option(
        None,
        "--globs",
        help="ast-grep include/exclude glob. May be repeated; prefix with ! to exclude.",
    ),
    filter_regex: str | None = typer.Option(
        None, "--filter", help="Filter matched AST nodes by text regex"
    ),
    files_with_matches: bool = typer.Option(
        False,
        "--files-with-matches",
        help="Print only paths with at least one AST match.",
    ),
) -> None:
    from tensor_grep.cli.ast_workflows import run_command as execute_run

    if update_all and rewrite is None:
        typer.echo("Error: tg run --update-all requires --rewrite.", err=True)
        raise typer.Exit(code=2)

    positional_args = list(arguments or [])
    if pattern_option:
        if len(positional_args) > 1:
            typer.echo(
                "Error: tg run --pattern accepts at most one positional PATH argument.",
                err=True,
            )
            raise typer.Exit(code=2)
        resolved_pattern = pattern_option
        resolved_path = positional_args[0] if positional_args else None
    else:
        if not positional_args:
            typer.echo(
                "Error: tg run requires --pattern <PATTERN> or positional PATTERN.",
                err=True,
            )
            raise typer.Exit(code=2)
        if len(positional_args) > 2:
            typer.echo("Error: tg run accepts at most PATTERN and PATH positionals.", err=True)
            raise typer.Exit(code=2)
        if (
            (selector is not None or strictness is not None or stdin or globs)
            and len(positional_args) == 1
            and Path(positional_args[0]).exists()
        ):
            typer.echo(
                "Error: tg run ast-grep semantic options require --pattern <PATTERN> "
                "before PATH; positional arguments without --pattern are treated as PATTERN.",
                err=True,
            )
            raise typer.Exit(code=2)
        # L9: a lone positional that resolves to an existing file/dir is almost certainly
        # a PATH supplied without a PATTERN. Previously it was swallowed as the AST
        # pattern, yielding a silent zero-match exit 1. Fail loudly with a clear message
        # instead so the missing pattern is obvious.
        if len(positional_args) == 1 and Path(positional_args[0]).exists():
            typer.echo(
                "Error: tg run requires a PATTERN. Received only a PATH "
                f"({positional_args[0]!r}); pass the AST pattern before the path "
                "(tg run <PATTERN> <PATH>) or use --pattern <PATTERN>.",
                err=True,
            )
            raise typer.Exit(code=2)
        resolved_pattern = positional_args[0]
        resolved_path = positional_args[1] if len(positional_args) > 1 else None

    exit_code = execute_run(
        pattern=resolved_pattern,
        path=resolved_path,
        rewrite=rewrite,
        lang=lang,
        apply=apply or update_all,
        verify=verify,
        json_mode=json_output,
        checkpoint=checkpoint,
        interactive=interactive,
        filter_regex=filter_regex,
        files_with_matches=files_with_matches,
        selector=selector,
        strictness=strictness,
        stdin=stdin,
        globs=globs,
    )
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


def register(app: typer.Typer) -> None:
    app.command()(classify)
    app.command()(rulesets)
    app.command(name="audit")(audit_help)
    app.command()(scan)
    app.command()(test)
    app.command()(new)
    app.command(name="ast-info")(ast_info)
    app.command(
        name="run",
        help="Run a validated AST slice for structural search and guarded rewrites. PowerShell users should single-quote AST patterns containing $ captures, for example 'def $NAME($$$ARGS):\\n    $$$BODY'. Patterns match exact AST node shapes: a single-line 'def $NAME($$$ARGS): $$$BODY' does not match a multiline function body (TypeScript: 'function $NAME($$$ARGS) {\\n  $$$BODY\\n}'); use --selector function_definition to match any function regardless of formatting. When the ast-grep `sg` binary is on PATH, the pattern is delegated to it verbatim (full $NAME/$$$ARGS/--selector/--strictness compatibility); without `sg`, a native-shaped pattern still runs through tg's own tree-sitter backend, but a $-metavariable pattern needs `sg` and fails with a clean error instead of a mistranslated result.",
    )(run)
