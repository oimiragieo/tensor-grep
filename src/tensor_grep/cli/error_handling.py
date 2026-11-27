from __future__ import annotations

import dataclasses
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

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
from tensor_grep.cli.output_envelope import _json_output_version, _safe_stdout_line
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
from tensor_grep.core.case_semantics import case_regex_flags
from tensor_grep.io.project_markers import path_has_project_marker as _path_has_project_marker
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
    IMPLICIT_SEARCH_WALK_FILE_CEILING,
    UNBOUNDED_VENDORED_ROOT_DIR_NAMES,
)

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig

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
    from tensor_grep.core.config import SearchConfig


def _is_invalid_regex_error(exc: Exception) -> bool:
    if isinstance(exc, re.error):
        return True
    message = str(exc).lower()
    if (
        "regex parse error" in message
        or "error parsing regex" in message
        or "invalid regex" in message
    ):
        return True
    return exc.__class__.__name__ == "InvalidRegexError"


def _search_error_payload(error: str, detail: str) -> dict[str, object]:
    from tensor_grep.cli.formatters.json_fmt import JSON_OUTPUT_VERSION

    return {
        "version": JSON_OUTPUT_VERSION,
        "schema_version": JSON_OUTPUT_VERSION,
        "ok": False,
        "error": error,
        "detail": detail,
    }


def _emit_search_error_json(error: str, detail: str) -> None:
    _safe_stdout_line(json.dumps(_search_error_payload(error, detail)))


def _exit_search_error(
    error: str,
    detail: str,
    *,
    json_mode: bool,
    stderr_detail: str | None = None,
    exit_code: int = 2,
) -> None:
    if json_mode:
        _emit_search_error_json(error, detail)
    else:
        typer.echo(f"Error: {stderr_detail or detail}", err=True)
    sys.exit(exit_code)


def _is_inline_flag_regex_error(message: str) -> bool:
    """Return True when ``message`` is the "inline flag group not at the start of the
    pattern" rejection that PCRE2 (``-P``) accepts but the default Rust/``re`` engine does
    not (e.g. ``a(?s).*b``). Centralized so both the remediation hint (M14) and the
    transparent PCRE2 fallback (M14b) classify the error identically."""
    lowered = message.lower()
    return "global flags not at the start" in lowered or (
        "flag" in lowered and ("(?" in message or "inline" in lowered)
    )


def _invalid_regex_remediation(message: str) -> str:
    """Return a remediation hint that never converts a hard regex error into a silent
    wrong answer (audit M14).

    The default Rust regex engine rejects inline flag groups that are not at the start
    of the expression (e.g. ``a(?s).*b``). Suggesting ``-F`` there is actively harmful:
    ``-F`` searches the literal text ``a(?s).*b`` and returns a silent zero-match
    success, masking the real problem. For inline-flag / parse errors, point the user at
    ``-P`` (the PCRE2 engine, which accepts mid-expression inline flags) or at moving the
    flag to the front of the pattern instead.
    """
    if _is_inline_flag_regex_error(message):
        return (
            "Use -P (PCRE2) to allow inline flags mid-pattern, or move the inline flag "
            "group (for example (?s)) to the very start of the pattern."
        )
    return (
        "Use -P (PCRE2) for extended regex syntax, or --fixed-strings (-F) only if you "
        "intended to search this pattern as a literal string."
    )


def _exit_invalid_regex(exc: Exception, *, json_mode: bool = False) -> None:
    message = str(exc)
    if "invalid regex" not in message.lower():
        message = f"invalid regex pattern: {message}"
    _exit_search_error(
        "invalid_regex",
        message,
        json_mode=json_mode,
        stderr_detail=f"{message}. {_invalid_regex_remediation(message)}",
    )


def _engine_is_explicit_pcre2(config: SearchConfig) -> bool:
    """True when the user explicitly selected PCRE2, via ``-P``/``--pcre2`` or
    ``--engine pcre2``. PCRE2 accepts mid-pattern inline flag groups, so the Python
    pre-flight validator must not reject patterns the chosen engine would accept."""
    return bool(config.pcre2) or str(getattr(config, "engine", "") or "").lower() == "pcre2"


def _pcre2_fallback_backend_available() -> bool:
    """True when the resolved ripgrep backend can actually run PCRE2. The rg shipped on some
    platforms (and most CI images) is built WITHOUT PCRE2, so blindly retrying under PCRE2
    would raise a confusing ConfigurationError instead of the helpful ``-P`` remediation."""
    try:
        from tensor_grep.backends.ripgrep_backend import RipgrepBackend

        return bool(RipgrepBackend().supports_pcre2())
    except Exception:
        return False


def _eligible_for_pcre2_inline_flag_fallback(config: SearchConfig) -> bool:
    """True when an inline-flag regex rejection should transparently retry under PCRE2
    instead of erroring (audit M14b). Fires for the default/unset engine and for
    ``--engine auto``; ``-F`` is honored (literal intent) and an explicit PCRE2 engine
    already routes through PCRE2, so neither needs the fallback. The default engine value
    is the same whether the user typed ``--engine default`` or nothing, so both opt in --
    matching the bare ``tg search 'a(?s).*b'`` repro. (Whether a PCRE2-capable rg backend
    actually exists is a separate, environment-dependent check applied at the call site.)"""
    if config.fixed_strings or _engine_is_explicit_pcre2(config):
        return False
    return str(getattr(config, "engine", "") or "").lower() in {"default", "auto", ""}


def _validate_search_regex(pattern: str, config: SearchConfig) -> None:
    if config.fixed_strings or _engine_is_explicit_pcre2(config):
        return

    flags = case_regex_flags(config, pattern, strict=False)

    candidate = pattern
    if config.line_regexp:
        candidate = f"^{pattern}$"
    elif config.word_regexp:
        candidate = rf"\b{pattern}\b"

    try:
        re.compile(candidate, flags)
    except re.error as exc:
        from tensor_grep.backends.cpu_backend import InvalidRegexError

        raise InvalidRegexError(f"error parsing regex: {exc}") from exc


_LEADING_INLINE_FLAG_RE = re.compile(r"^\(\?([aiLmsux]+)\)")


def _scope_leading_inline_flag(pattern: str) -> str:
    """Rewrite a GLOBAL leading inline flag group (``(?i)foo``) to the SCOPED form
    (``(?i:foo)``) so it stays legal -- and stays scoped to its own branch, never leaking
    case-insensitivity/etc. across the rest of the alternation -- once it is no longer the
    first thing in a combined multi-pattern regex (audit #69, re-do of #441)."""
    match = _LEADING_INLINE_FLAG_RE.match(pattern)
    if not match:
        return pattern
    flags = match.group(1)
    rest = pattern[match.end() :]
    return f"(?{flags}:{rest})"


def _combine_multi_patterns(patterns: list[str], *, fixed_strings: bool) -> str:
    """OR-combine multiple ``-e``/``-f`` patterns into one rg-parity alternation regex: a
    line matches if ANY pattern matches (rg's own multi-pattern semantics), reported once
    even when more than one pattern matches the same line -- never N independent passes.
    Each pattern becomes its own non-capturing-group branch (never a bare top-level ``|``
    join), and the whole alternation gets one more enclosing group, so downstream
    ``-w``/``-x``/``--line-regexp`` wrapping (which wraps the WHOLE pattern string, e.g.
    ``rf"\\b{pattern}\\b"``) applies to the entire alternation rather than mis-scoping to
    just the first/last branch via ``|``'s low precedence."""
    branches = []
    for raw_pattern in patterns:
        candidate = (
            re.escape(raw_pattern) if fixed_strings else _scope_leading_inline_flag(raw_pattern)
        )
        branches.append(f"(?:{candidate})")
    return "(?:" + "|".join(branches) + ")"


def _read_patterns_from_file_list(file_paths: list[str], *, json_mode: bool) -> list[str]:
    """Read ``-f``/``--file`` pattern files, one pattern per line (rg parity: a genuinely
    blank line is an EMPTY pattern that matches every line, so it is intentionally NOT
    filtered out here). A missing/unreadable file fails loud with exit 2 -- per the Backend
    Fail-Closed Contract -- instead of the pre-fix silent flood (an unread ``-f`` collapsed
    to an empty ``pattern`` that matched every line in every file)."""
    patterns: list[str] = []
    for file_path in file_paths:
        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _exit_search_error(
                "pattern_file_error",
                f"failed to read pattern file: {file_path} ({exc})",
                json_mode=json_mode,
                exit_code=2,
            )
            return []  # pragma: no cover -- _exit_search_error always calls sys.exit
        patterns.extend(content.splitlines())
    return patterns


def _search_paths_include_guarded_broad_root(paths: list[str]) -> bool:
    for path in paths:
        if not path or path == "-" or path.startswith("-"):
            continue
        normalized = path.replace("\\", "/").rstrip("/").lower()
        if normalized in _GUARDED_BROAD_SEARCH_ROOTS:
            return True
        if any(normalized.endswith(f"/{root}") for root in _GUARDED_BROAD_SEARCH_ROOTS):
            return True
    return False


def _config_with_guarded_broad_root_globs(config: SearchConfig) -> SearchConfig:
    existing_globs = list(config.glob or [])
    for glob in _GUARDED_BROAD_ROOT_RG_GLOBS:
        if glob not in existing_globs:
            existing_globs.append(glob)
    return replace(config, glob=existing_globs)


def _has_generated_scan_bound(config: SearchConfig) -> bool:
    return bool(
        config.max_depth is not None
        or config.glob
        or config.iglob
        or config.file_type
        or config.type_not
    )


def _has_walk_scope_bound(config: SearchConfig, *, paths_defaulted: bool) -> bool:
    if config.max_depth is not None:
        return True
    if paths_defaulted:
        return False
    return bool(config.glob or config.iglob or config.file_type or config.type_not)


def _workspace_project_child_names(paths: list[str]) -> list[str]:
    found: set[str] = set()
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = Path(raw_path)
        try:
            if not path.is_dir():
                continue
            # Item #154: a root carrying its OWN project marker (e.g. a top-level
            # `package.json`) is not skipped outright -- it can *also* be a workspace parent
            # (a real repro: a workspace dir with its own `package.json` that also contains
            # dozens of independently-marked sibling projects). A marked root uses the higher
            # "marked-root" threshold, since an ordinary single project can legitimately carry
            # a handful of marked children without being a workspace parent; an unmarked root
            # keeps the original (lower) threshold.
            threshold = (
                _BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
                if _path_has_project_marker(path)
                else _BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
            )
            child_project_names: list[str] = []
            for child in path.iterdir():
                try:
                    if child.is_dir() and _path_has_project_marker(child):
                        child_project_names.append(child.name)
                except OSError:
                    continue
            if len(child_project_names) >= threshold:
                found.update(child_project_names)
        except OSError:
            continue
    return sorted(found, key=lambda item: item.lower())


def _should_refuse_unbounded_workspace_root_scan(
    paths: list[str],
    config: SearchConfig,
    *,
    allow_broad_generated_scan: bool,
    paths_defaulted: bool,
) -> tuple[bool, list[str]]:
    if allow_broad_generated_scan or _has_walk_scope_bound(config, paths_defaulted=paths_defaulted):
        return False, []
    project_dirs = _workspace_project_child_names(paths)
    return bool(project_dirs), project_dirs


_UNBOUNDED_VENDORED_ROOT_DIR_NAMES = UNBOUNDED_VENDORED_ROOT_DIR_NAMES


def _root_top_level_vendored_dir_names(
    paths: list[str], follow_symlinks: bool = False
) -> list[str]:
    """One-level vendored-dir probe shared with bootstrap; follows only when search will."""
    from tensor_grep.io.root_probe import iter_top_level_vendored_dirs

    found = set(
        iter_top_level_vendored_dirs(paths, _UNBOUNDED_VENDORED_ROOT_DIR_NAMES, follow_symlinks)
    )
    return sorted(found, key=lambda item: item.lower())


def _should_refuse_unbounded_vendored_root_scan(
    paths: list[str],
    config: SearchConfig,
    *,
    allow_broad_generated_scan: bool,
    paths_defaulted: bool,
) -> tuple[bool, list[str]]:
    if allow_broad_generated_scan or _has_walk_scope_bound(config, paths_defaulted=paths_defaulted):
        return False, []
    vendored_dirs = _root_top_level_vendored_dir_names(paths, config.effective_rg_follow)
    return bool(vendored_dirs), vendored_dirs


def _should_refuse_unbounded_generated_scan(
    paths: list[str],
    config: SearchConfig,
    *,
    allow_broad_generated_scan: bool,
    files_mode: bool,
) -> tuple[bool, list[str]]:
    if allow_broad_generated_scan or _has_generated_scan_bound(config):
        return False, []
    from tensor_grep.cli.scan_guardrails import generated_scan_dir_names

    unrestricted = (
        (files_mode and config.hidden)
        or config.no_ignore
        or config.no_ignore_files
        or config.no_ignore_vcs
        or config.unrestricted > 0
    )
    generated_dirs = generated_scan_dir_names(
        paths, _BROAD_GENERATED_SCAN_DIR_NAMES, include_child_dirs=files_mode and unrestricted
    )
    if not unrestricted:
        # Direct generated roots (e.g. `tg search PAT .venv`) are refused even without --no-ignore.
        return bool(generated_dirs), generated_dirs
    return bool(generated_dirs), generated_dirs


def _format_broad_generated_scan_error(generated_dirs: list[str]) -> str:
    visible_dirs = ", ".join(generated_dirs[:8])
    if len(generated_dirs) > 8:
        visible_dirs = f"{visible_dirs}, ..."
    return (
        "Error: broad generated-root scan refused as a safety guard, not a zero-match result: "
        "path contains generated, cache, "
        f"or dependency directories ({visible_dirs}). Scope the path, add --glob, --type, "
        "or --max-depth, or pass --allow-broad-generated-scan to opt in.\n"
        "For bounded output:\n"
        "tg search --files <path> --hidden --max-depth <N>\n"
        "For intentional broad scans:\n"
        "--allow-broad-generated-scan"
    )


def _format_broad_workspace_scan_error(project_dirs: list[str]) -> str:
    visible_dirs = ", ".join(project_dirs[:8])
    if len(project_dirs) > 8:
        visible_dirs = f"{visible_dirs}, ..."
    return (
        "Error: broad workspace-root scan refused as a safety guard, not a zero-match result: "
        "path looks like a multi-project "
        f"workspace root ({visible_dirs}). Scope the path to one project, add --glob, "
        "--type, or --max-depth, or pass --allow-broad-generated-scan to opt in.\n"
        "For bounded output:\n"
        'tg search <pattern> <workspace> --glob "*.py"\n'
        "tg search <pattern> <workspace> --max-depth <N>\n"
        "For intentional broad scans:\n"
        "--allow-broad-generated-scan"
    )


def _emit_broad_scan_refusal(
    message: str,
    *,
    json_output: bool,
    path: str,
    incomplete_reason_class: str = "scan_limit",
    error_code: str = "broad_scan_refused",
) -> None:
    """Emit a scan-policy refusal on BOTH surfaces, then let the caller exit 2.

    An external dogfood asked for "the same exit-2 refuse for bare `--json` unscoped as the
    multi-project parent" and it was half-right: the exit code WAS already 2. What was missing is
    that `--json` printed **zero bytes** to stdout, so a machine consumer got `JSONDecodeError` and
    had to parse English off stderr to learn why. Measured on the shipped v1.101.9:
    `tg search PAT --json` on a large implicit root -> exit 2, stdout 0 bytes.

    A refusal is the one answer a `--json` caller most needs machine-readable: it is precisely the
    case where an empty result must NOT be read as "no matches". Emitting nothing forces the
    consumer into the inference this whole surface exists to prevent.

    The envelope MIRRORS the MCP `tg_search` refusal payload (`mcp_server.py`) field for field --
    `truncated` + `result_incomplete` + `incomplete_reason` + `incomplete_reason_class` +
    `error.code` + `retryable: false`. Defaults stay ``scan_limit`` / ``broad_scan_refused`` for
    generated/vendored/large-root ceilings (MCP already settled those as scan-policy ceilings).
    The multi-project workspace-root guard passes ``workspace_root_refused`` for BOTH class and
    code so agents do not confuse a parent-workspace refuse with a file-cap truncation.

    `total_matches: 0` is safe here ONLY because it travels with those flags: the zero is qualified
    on the same line it appears, which is the difference between a count and an absence claim.

    ``budget_remediable`` is derived from the shared allow-list in ``incompleteness.py`` so a
    ``workspace_root_refused`` payload cannot be mistaken for a file-cap that wants
    ``--max-repo-files`` raised (enterprise launch W2.a / dogfood ask).
    """
    from tensor_grep.cli.formatters.json_fmt import JSON_OUTPUT_VERSION
    from tensor_grep.cli.incompleteness import budget_remediable as _budget_remediable

    typer.echo(message, err=True)
    if not json_output:
        return
    typer.echo(
        json.dumps(
            {
                "version": JSON_OUTPUT_VERSION,
                "path": path,
                "total_matches": 0,
                "total_files": 0,
                "matches": [],
                "truncated": True,
                "result_incomplete": True,
                "incomplete_reason": message,
                "incomplete_reason_class": incomplete_reason_class,
                "budget_remediable": _budget_remediable(incomplete_reason_class),
                "error": {
                    "code": error_code,
                    "message": message,
                    "retryable": False,
                },
            },
            indent=2,
        )
    )


def _format_unbounded_vendored_root_scan_error(vendored_dirs: list[str]) -> str:
    visible_dirs = ", ".join(vendored_dirs[:8])
    if len(vendored_dirs) > 8:
        visible_dirs = f"{visible_dirs}, ..."
    return (
        "Error: broad root scan refused as a safety guard, not a zero-match result: "
        "path contains a heavy vendored/index "
        f"directory at its top level ({visible_dirs}). Scope the path, add --glob, --type, "
        "or --max-depth, or pass --allow-broad-generated-scan to opt in.\n"
        "For bounded output:\n"
        'tg search <pattern> <root> --glob "*.py"\n'
        "tg search <pattern> <root> --max-depth <N>\n"
        "For intentional broad scans:\n"
        "--allow-broad-generated-scan"
    )


_LARGE_ROOT_SCAN_FILE_CEILING = IMPLICIT_SEARCH_WALK_FILE_CEILING


def _should_refuse_unbounded_large_root_scan(
    candidate_file_count: int,
    config: SearchConfig,
    *,
    allow_broad_generated_scan: bool,
    paths_defaulted: bool,
) -> bool:
    if allow_broad_generated_scan or _has_walk_scope_bound(config, paths_defaulted=paths_defaulted):
        return False
    return candidate_file_count > _LARGE_ROOT_SCAN_FILE_CEILING


def _format_unbounded_large_root_scan_error(file_count_floor: int) -> str:
    return (
        "Error: broad root scan refused as a safety guard, not a zero-match result: "
        f"path is a large single-project root (over {file_count_floor} files); --glob/--type/"
        "--iglob narrow WHICH files match but do not bound how much of the tree must be "
        "walked to find them, and no fast native/rg engine is available for this query -- an "
        "unscoped scan here would burn the search deadline instead of failing fast. Scope the "
        "path explicitly, add --max-depth, or pass --allow-broad-generated-scan to opt in.\n"
        "For bounded output:\n"
        'tg search <pattern> <root> --glob "*.py"\n'
        "tg search <pattern> <root> --max-depth <N>\n"
        "For intentional broad scans:\n"
        "--allow-broad-generated-scan"
    )


def _implicit_glob_search_walk_exceeds_ceiling(
    paths: list[str],
    config: SearchConfig,
    ceiling: int,
) -> bool:
    from tensor_grep.io.directory_scanner import DirectoryScanner

    probe_config = dataclasses.replace(config, glob=None, iglob=None, file_type=None, type_not=None)
    count = 0
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        scanner = DirectoryScanner(probe_config)
        for _ in scanner.walk(raw_path):
            count += 1
            if count > ceiling:
                return True
    return False


def _sum_total_bytes(paths: list[str]) -> int:
    total = 0
    for p in paths:
        try:
            total += Path(p).stat().st_size
        except OSError:
            continue
    return total


def _audit_diff_error_payload(message: str, *, code: str) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "AuditManifest",
        "routing_reason": "audit-manifest-diff",
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


def _audit_history_error_payload(message: str, *, code: str) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "AuditManifest",
        "routing_reason": "audit-manifest-history",
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


def _review_bundle_error_payload(
    message: str, *, code: str, routing_reason: str
) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "AuditManifest",
        "routing_reason": routing_reason,
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


def _evidence_error_payload(message: str, *, code: str, routing_reason: str) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "EvidenceReceipt",
        "routing_reason": routing_reason,
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


def _ledger_error_payload(message: str, *, code: str, routing_reason: str) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": routing_reason,
        "sidecar_used": False,
        "advisory": True,
        "error": {"code": code, "message": message},
    }


def _emit_ledger_error(
    exc: BaseException, *, json_output: bool, code: str, routing_reason: str
) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                _ledger_error_payload(str(exc), code=code, routing_reason=routing_reason),
                indent=2,
            )
        )
    else:
        typer.echo(str(exc), err=True)
