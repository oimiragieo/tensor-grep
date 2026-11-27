from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from tensor_grep.cli import _index_lock as _index_lock
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
from tensor_grep.cli.output_envelope import _TailExitCodePolicy
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
from tensor_grep.io.file_types import BUILTIN_TYPE_LIST as _BUILTIN_TYPE_LIST
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
)

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.io.directory_scanner import DirectoryScanner

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
    from tensor_grep.io.directory_scanner import DirectoryScanner

_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS = (
    "regexp",
    "file_patterns",
    "pre",
    "pre_glob",
    "search_zip",
    "crlf",
    "dfa_size_limit",
    "encoding",
    "engine",
    "line_regexp",
    "mmap",
    "multiline",
    "multiline_dotall",
    "auto_hybrid_regex",
    "no_unicode",
    "unicode",
    "pcre2_unicode",
    "null_data",
    "pcre2",
    "regex_size_limit",
    "smart_case",
    "stop_on_nonmatch",
    "text",
    "threads",
    "binary",
    "follow",
    "glob_case_insensitive",
    "hidden",
    "iglob",
    "ignore_file",
    "ignore_file_case_insensitive",
    "max_depth",
    "max_filesize",
    "ignore",
    "no_ignore_dot",
    "no_ignore_exclude",
    "no_ignore_files",
    "no_ignore_global",
    "no_ignore_parent",
    "no_ignore_vcs",
    "no_require_git",
    "require_git",
    "no_hidden",
    "one_file_system",
    "file_type",
    "type_not",
    "type_add",
    "type_clear",
    "unrestricted",
    "after_context",
    "before_context",
    "block_buffered",
    "byte_offset",
    "color",
    "colors",
    "context_separator",
    "field_context_separator",
    "field_match_separator",
    "heading",
    "hostname_bin",
    "hyperlink_format",
    "include_zero",
    "line_buffered",
    "max_columns",
    "max_columns_preview",
    "null",
    "only_matching",
    "passthru",
    "pretty",
    "quiet",
    "replace_str",
    "sort_by",
    "sort_by_reverse",
    # Native tg cannot reproduce these output-ordering post-processes byte-for-byte, so a
    # non-default value must REFUSE delegation and fall through to the Python/backend path:
    # sort_files is applied in-backend (ripgrep_backend.py / rust_backend.py) and rank_bm25
    # drives the BM25 rerank at the end of the search flow (both bypassed by a delegated
    # sys.exit). See tests/unit/test_native_delegation_field_coverage.py (round-4 #25).
    "sort_files",
    "rank_bm25",
    # semantic_rank: same class as rank_bm25 above -- native tg has no dense/RRF hybrid leg, so
    # delegating a --semantic search would drop the hybrid rerank entirely.
    "semantic_rank",
    "trim",
    "with_filename",
    "no_filename",
    "count_matches",
    "debug",
    "no_ignore_messages",
    "no_messages",
    "messages",
    "stats",
    "trace",
    "list_files",
    "generate",
    "no_config",
    "pcre2_version",
    "type_list",
    "format_type",
    "ast",
    "lang",
    "ltl",
)


_SEARCH_COMMAND_TAIL_EXIT_CODE_POLICIES: tuple[_TailExitCodePolicy, ...] = (
    _TailExitCodePolicy(
        name="result_incomplete",
        trigger_fields=frozenset(),
        mirrored_in_native_at=(
            "rust_core/src/main.rs: walk_was_incomplete()/incomplete_envelope_fields() are "
            "consulted at every native plain-text exit site (run_native_search_with_optional_"
            "rg_fallback's `if stats.walk_errors > 0 { std::process::exit(2) }` and "
            "emit_multi_pattern_native_results's identical guard) -- manually verified during "
            "the task 22 investigation, 2026-07-31."
        ),
    ),
    _TailExitCodePolicy(
        name="gpu_request_unhonoured",
        trigger_fields=frozenset({"gpu_device_ids"}),
        route_specific_reason=(
            "RETIRED as an exit-code rule, 2026-08-01 (backlog #22 / PR #868). An unhonoured "
            "explicit --gpu-device-ids request does NOT independently force exit 2, so there is no "
            "tail-only exit policy here to mirror. docs/CONTRACTS.md section 4 defines `2` as "
            "INCOMPLETE -- a TRUNCATED SCAN -- and that search runs to completion over every file "
            "it was asked about, returning correct results computed on the CPU. Which processor "
            "did the work is a routing fact, not an incompleteness; the contract's own precedents "
            "(an output-only cap stays 0; `tg imports --deadline` is a documented no-op) both go "
            "this way. The signal lives in the --json envelope instead -- gpu_evidence_status / "
            "gpu_proof / native_gpu_unavailable / not_gpu_proof_reason -- which is strictly more "
            "informative than a coarse exit code. This entry is KEPT rather than deleted so the "
            "decision is discoverable from the registry, and so a future reader who reaches for "
            "exit 2 here finds the reasoning that already rejected it."
        ),
    ),
)


def _can_delegate_to_native_tg_search(
    config: SearchConfig,
    *,
    ndjson: bool,
    files_mode: bool,
    files_with_matches: bool,
    files_without_match: bool,
    format_type: str,
) -> bool:
    from tensor_grep.core.config import SearchConfig

    if files_mode or files_with_matches or files_without_match or format_type != "rg":
        return False

    defaults = SearchConfig()
    for field_name in _NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS:
        if getattr(config, field_name) != getattr(defaults, field_name):
            return False

    return config.force_cpu or config.json_mode or ndjson or bool(config.gpu_device_ids)


def _build_native_tg_search_command(
    native_binary: Path,
    *,
    pattern: str,
    paths: list[str],
    config: SearchConfig,
    ndjson: bool,
) -> list[str]:
    command = [str(native_binary), "search"]

    if config.force_cpu:
        command.append("--cpu")
    elif config.gpu_device_ids:
        command.extend([
            "--gpu-device-ids",
            ",".join(str(device_id) for device_id in config.gpu_device_ids),
        ])

    if config.ignore_case:
        command.append("-i")
    if config.case_sensitive:
        command.append("-s")
    if config.fixed_strings:
        command.append("-F")
    if config.invert_match:
        command.append("-v")
    if config.count:
        command.append("-c")
    # Forward an EXPLICIT line-number choice only. The native subprocess inherits tg's stdout, so its
    # own tty heuristic already matches tg's auto decision; we only need to forward when the user
    # explicitly set --line-number/-n or --no-line-number/-N (otherwise that choice is dropped).
    if config.line_number_explicit:
        command.append("-n" if config.line_number else "-N")
    if config.column:
        command.append("--column")
    if config.context is not None:
        command.extend(["-C", str(config.context)])
    if config.max_count is not None:
        command.extend(["-m", str(config.max_count)])
    if config.path_separator is not None:
        command.extend(["--path-separator", config.path_separator])
    if config.vimgrep:
        command.append("--vimgrep")
    if config.word_regexp:
        command.append("-w")
    for current_glob in config.glob or []:
        command.extend(["-g", current_glob])
    if config.no_ignore:
        command.append("--no-ignore")
    if config.json_mode:
        command.append("--json")
    if ndjson:
        command.append("--ndjson")

    # End-of-options sentinel (CWE-88 / the MCP-276 class, AGENTS.md). This builder was the
    # "remaining tg sweep" item that AGENTS.md tracks by name, and it stayed unfixed because the
    # comment that used to live here was FALSE: it claimed the native `search` positionals carry
    # clap `allow_hyphen_values`. Only `-e/--regexp` does (rust_core/src/main.rs:686); `pattern`
    # (:690-691) and `path` (:693-695) do not.
    #
    # Consequence without the sentinel: a dash-leading pattern is parsed by the native binary as a
    # FLAG, so the intended path slides into pattern position and the search runs against a scope
    # the caller never chose -- and still exits 0. Wrong scope with no error, which is the silent
    # half and the reason this matters more than a crash would.
    #
    # UNCONDITIONAL, deliberately. A conditional form (emit `--` only when the pattern starts with
    # `-`) looks equivalent and leaves exactly that silent path-promotion case exposed. Matches the
    # sibling builders: ripgrep_backend.py's `cmd.append("--")` and mcp_server.py's.
    command.append("--")
    command.extend([pattern, *paths])
    return command


def _delegate_to_native_tg_search(
    native_binary: Path,
    *,
    pattern: str,
    paths: list[str],
    config: SearchConfig,
    ndjson: bool,
) -> int:
    command = _build_native_tg_search_command(
        native_binary,
        pattern=pattern,
        paths=paths,
        config=config,
        ndjson=ndjson,
    )
    # Bound the native-delegation subprocess the same way the bootstrap passthrough twin does
    # (bootstrap.py `_streaming_passthrough_returncode`): a hung native search must not hang the
    # CLI forever, and `TimeoutExpired` must become a clean exit 124 (coreutils `timeout`
    # convention), not an uncaught traceback (H5 audit).
    from tensor_grep.cli.frontdoor_hops import child_env_or_refusal
    from tensor_grep.cli.subprocess_policy import configured_ripgrep_timeout_seconds

    child_env, refusal = child_env_or_refusal()
    if child_env is None:
        return refusal
    try:
        completed = _self.subprocess.run(
            command, check=False, timeout=configured_ripgrep_timeout_seconds(), env=child_env
        )
    except _self.subprocess.TimeoutExpired:
        sys.stderr.write(
            "tensor-grep: native search exceeded the configured timeout and was stopped. For a "
            "large repo, scope the search to a path (e.g. `tg search PATTERN src/`), or raise "
            "TG_RG_TIMEOUT_SECONDS (or TG_SIDECAR_TIMEOUT_MS when set).\n"
        )
        return 124
    except OSError as exc:
        sys.stderr.write(
            f"tensor-grep: could not start native search ({exc}); output cannot be trusted.\n"
        )
        return 2
    return int(completed.returncode)


def _collect_candidate_files(
    scanner: DirectoryScanner, paths: list[str]
) -> tuple[list[str], set[str]]:
    ordered = []
    seen = set()
    for p in paths:
        for current_file in scanner.walk(p):
            if current_file not in seen:
                seen.add(current_file)
                ordered.append(current_file)
    return ordered, seen


def _can_passthrough_rg(
    config: SearchConfig,
    *,
    format_type: str,
    explicit_rg_format: bool,
    json_mode: bool,
    ndjson_mode: bool,
    files_mode: bool,
    files_with_matches: bool,
    files_without_match: bool,
    only_matching: bool,
    stats_mode: bool,
) -> bool:
    rg_json_passthrough = bool(json_mode and explicit_rg_format)
    # Keep passthrough only for modes where rg semantics are fully compatible
    # with tensor-grep output and feature behavior.
    return bool(
        not config.ast
        and not config.ltl
        and not config.force_cpu
        and not config.rank_bm25
        and not config.semantic_rank
        # An explicit --gpu-device-ids request must reach Pipeline, which raises loudly when GPU
        # can't be honored (the "never silently downgrade to CPU" contract). rg-passthrough would
        # run plain CPU rg with exit 0 and no fallback_reason — a silent downgrade. (round-5 Q9)
        and not config.gpu_device_ids
        and format_type == "rg"
        and (not json_mode or rg_json_passthrough)
        and not ndjson_mode
        and not (files_mode and json_mode)
        and not (rg_json_passthrough and stats_mode)
        and not (rg_json_passthrough and (config.count or config.count_matches))
        and not (rg_json_passthrough and (files_with_matches or files_without_match))
        and not (rg_json_passthrough and config.replace_str is not None)
        and not (rg_json_passthrough and config.passthru)
        and not (files_with_matches and (config.count or config.count_matches))
    )


_PLAIN_JSON_INCOMPATIBLE_RENDER_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--passthru", "--passthrough"),
    ("--heading", "--no-heading"),
    ("--trim", "--no-trim"),
    ("-b", "--byte-offset", "--no-byte-offset"),
    ("-M", "--max-columns"),
    ("--max-columns-preview", "--no-max-columns-preview"),
    ("--context-separator", "--no-context-separator"),
    ("--field-context-separator",),
    ("--field-match-separator",),
    ("-p", "--pretty"),
)


def _plain_json_incompatible_render_flags(argv: list[str] | None = None) -> list[str]:
    """Return the render-only flag spellings the user passed that the aggregate
    plain-``--json`` path cannot honor. Detection is argv-based because some flags
    (notably ``--heading``) share their default with the SearchConfig default and so
    cannot be recovered from the parsed config alone."""
    tokens = list(sys.argv[1:] if argv is None else argv)
    if tokens and tokens[0] == "search":
        tokens = tokens[1:]
    # Stop at an explicit end-of-options marker so a literal "--passthru" *pattern*
    # after "--" is never mistaken for the flag.
    seen: set[str] = set()
    flagged: list[str] = []
    for token in tokens:
        if token == "--":
            break
        base = token.split("=", 1)[0]
        for group in _PLAIN_JSON_INCOMPATIBLE_RENDER_FLAGS:
            if base in group and group[0] not in seen:
                seen.add(group[0])
                flagged.append(group[0])
    return flagged


def _scope_filtered(config: Any) -> bool:
    """`--glob`/`--iglob`/`--type`/`--type-not`/`--max-depth` ARE a chosen scope: a defaulted-
    PATH scope note would misdescribe such a search (see the is_empty branch in search)."""
    return bool(
        config.max_depth is not None
        or config.glob
        or config.iglob
        or config.file_type
        or config.type_not
    )


def _selected_route_supports_rg_passthrough(
    *,
    selected_backend_name: str,
    selected_backend_reason: str,
    selected_gpu_device_ids: list[int],
    selected_gpu_chunk_plan_mb: list[tuple[int, int]],
) -> bool:
    if selected_backend_name != "RipgrepBackend":
        return False
    if selected_gpu_device_ids or selected_gpu_chunk_plan_mb:
        return False
    return not selected_backend_reason.startswith("gpu_")


def _generate_shell_completion_script(*, generator: str, prog_name: str = "tg") -> str:
    shell_by_generator = {
        "complete-bash": "bash",
        "complete-zsh": "zsh",
        "complete-fish": "fish",
        "complete-powershell": "powershell",
    }
    shell = shell_by_generator.get(generator)
    if shell is None:
        supported_values = ", ".join(shell_by_generator)
        raise typer.BadParameter(
            f"Unsupported --generate value '{generator}'. Supported values: {supported_values}"
        )

    complete_var = f"_{prog_name.replace('-', '_').upper()}_COMPLETE"
    from typer._completion_shared import get_completion_script

    return str(get_completion_script(prog_name=prog_name, complete_var=complete_var, shell=shell))


def _run_rg_compatible_info_action(flag: str, unavailable_message: str) -> None:
    from tensor_grep.cli.frontdoor_hops import child_env_or_refusal

    child_env, refusal = child_env_or_refusal()
    if child_env is None:
        raise typer.Exit(refusal)
    # TG_REEXEC_GUARD: the native door spawned this process; never hand back to it (contract A).
    native = None if os.environ.get("TG_REEXEC_GUARD") else _self.resolve_native_tg_binary()
    candidates = [native, _self.resolve_ripgrep_binary()]
    last_completed: subprocess.CompletedProcess[str] | None = None
    for candidate in candidates:
        if not candidate or not candidate.exists():
            continue
        completed = subprocess.run(
            [str(candidate), flag],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
        last_completed = completed
        if completed.returncode == 0:
            if completed.stdout:
                typer.echo(completed.stdout.rstrip("\n\r"))
            if completed.stderr:
                typer.echo(completed.stderr.rstrip("\n\r"), err=True)
            raise typer.Exit(0)
    if flag == "--type-list" and last_completed is None:
        typer.echo("\n".join(_BUILTIN_TYPE_LIST))
        raise typer.Exit(0)
    if last_completed is not None:
        output = last_completed.stderr.strip() or last_completed.stdout.strip()
        if output:
            typer.echo(output, err=True)
        raise typer.Exit(int(last_completed.returncode or 1))
    typer.echo(unavailable_message, err=True)
    raise typer.Exit(1)


def _normalize_string_list(value: object, fallback: list[str]) -> list[str]:
    if value is None:
        return fallback
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return fallback


def _parse_gpu_device_ids_cli(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    parsed: list[int] = []
    seen: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise typer.BadParameter(
                f"Invalid GPU device id '{token}'. Use comma-separated integers, e.g. 0,1."
            ) from exc
        if value < 0:
            raise typer.BadParameter(
                f"Invalid GPU device id '{token}'. Device IDs must be non-negative."
            )
        if value in seen:
            continue
        seen.add(value)
        parsed.append(value)
    if not parsed:
        raise typer.BadParameter(
            "No valid GPU device IDs provided. Use comma-separated integers, e.g. 0,1."
        )
    return parsed


def _warn_unavailable_gpu_device_ids(gpu_device_ids: list[int] | None) -> None:
    """P0-3 (#171): WARN (never hard-fail) when a requested --gpu-device-ids id is absent from
    the local DeviceDetector inventory, so id 99 no longer silently CPU-falls-back
    indistinguishably from id 0.

    This is deliberately advisory only. A CPU-only native `tg` build cannot enumerate CUDA
    devices at all -- DeviceDetector fails closed to an empty inventory in that case -- and an
    empty inventory means "cannot verify", not "every id is invalid", so this stays silent
    rather than warn on every single invocation on a machine with no local NVML/torch signal.
    The native Rust classifier (classify_gpu_route_failure's "invalid CUDA device id" arm) owns
    the authoritative rejection on an actual CUDA-enabled build; this is the best-effort,
    fail-open Python-side signal for everyone else.
    """
    if not gpu_device_ids:
        return
    try:
        from tensor_grep.core.hardware.device_detect import DeviceDetector

        available_ids = DeviceDetector().enumerate_device_ids()
    except Exception:
        return
    if not available_ids:
        return
    missing_ids = [device_id for device_id in gpu_device_ids if device_id not in available_ids]
    if not missing_ids:
        return
    missing_text = ", ".join(str(device_id) for device_id in missing_ids)
    available_text = ", ".join(str(device_id) for device_id in available_ids)
    typer.echo(
        f"warning: --gpu-device-ids requested {missing_text} not present in the local GPU "
        f"device inventory (available: {available_text}); the search will still run and may "
        "silently fall back to CPU for the missing id(s)",
        err=True,
    )


def _selected_gpu_execution_defaults(
    gpu_device_ids: list[int], gpu_chunk_plan_mb: list[tuple[int, int]]
) -> tuple[bool, int]:
    if gpu_device_ids:
        worker_count = len(dict.fromkeys(gpu_device_ids))
    else:
        worker_count = len(dict.fromkeys(device_id for device_id, _ in gpu_chunk_plan_mb))
    if worker_count <= 0:
        return False, 0
    return worker_count > 1, worker_count
