import dataclasses
import json
import math
import os
import re

# `shutil` is no longer used by this module's own code -- the managed-front-door install that
# used it moved to cli/native_frontdoor.py -- but the test suite reaches `main.shutil` to patch
# `which`, so the attribute has to survive. `import x as x` is the explicit-re-export form,
# which F401 does not delete.
import shutil as shutil

# `subprocess` is a PATCH TARGET on this module: tests do `monkeypatch.setattr(main,
# "subprocess", fake)` to stub external process launches. The extracted siblings read it back
# as `_self.subprocess`, so it must be an EXPLICIT re-export -- mypy runs with
# `implicit_reexport = false`, under which a plain `import subprocess` binds privately.
import subprocess as subprocess
import sys
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

import click
import typer
from typer.core import TyperGroup

from tensor_grep.cli import ast_scan as _ast_scan
from tensor_grep.cli import doctor_payload as _doctor_payload
from tensor_grep.cli import doctor_report as _doctor_report
from tensor_grep.cli import native_frontdoor as _native_frontdoor
from tensor_grep.cli import windows_launcher as _windows_launcher
from tensor_grep.cli._index_lock import atomic_write_bytes_anchored
from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.cli.prepare_service import (
    _build_prepare_payload,
)
from tensor_grep.cli.runtime_paths import (
    _native_tg_version as _native_tg_version,
)
from tensor_grep.cli.runtime_paths import (
    _native_tg_version_matches as _native_tg_version_matches,
)
from tensor_grep.cli.runtime_paths import (
    env_flag_disabled,
    env_flag_enabled,
)
from tensor_grep.cli.runtime_paths import (
    inspect_native_tg_binary as inspect_native_tg_binary,
)
from tensor_grep.cli.runtime_paths import (
    is_cross_domain_native_binary as is_cross_domain_native_binary,
)

# Re-exported, not used here: a front-door checksum test reads `main.native_frontdoor_metadata_path`.
from tensor_grep.cli.runtime_paths import (
    native_frontdoor_metadata_path as native_frontdoor_metadata_path,
)
from tensor_grep.cli.runtime_paths import (
    resolve_native_tg_binary as resolve_native_tg_binary,
)
from tensor_grep.cli.runtime_paths import (
    resolve_ripgrep_binary as resolve_ripgrep_binary,
)
from tensor_grep.cli.runtime_paths import (
    translate_path_for_windows_binary as translate_path_for_windows_binary,
)
from tensor_grep.core import result as _JSON_OUTPUT_VERSION_CONTRACT
from tensor_grep.core.observability import nvtx_range
from tensor_grep.core.retrieval_chunker import MAX_CHUNKS

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
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
    IMPLICIT_SEARCH_WALK_FILE_CEILING,
    UNBOUNDED_VENDORED_ROOT_DIR_NAMES,
)
from tensor_grep.sidecar import DEFAULT_CLASSIFY_MAX_LINES

# Route A (docs/design/2026-08-19-split-floor-escape.md): this module object, for late
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
if TYPE_CHECKING:
    from tensor_grep.cli import main as _self
else:
    _self = sys.modules[__name__]

if TYPE_CHECKING:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import MatchLine, SearchResult
    from tensor_grep.core.retrieval_chunker import Chunk
    from tensor_grep.io.directory_scanner import DirectoryScanner

# Re-exports from the modules split out of this file on 2026-08-20 (see
# docs/design/2026-08-19-split-floor-escape.md and cli/_main_binding.py). EVERY moved name is
# rebound here, not only the ones a scan can prove are read. The test suite patches many of
# them on `main` and the moved code reads them back through `_self`, so the patch target must
# not move with the code -- and tests reach them through local aliases, so 'nothing references
# it' would be a claim about the scan, not about the code. Rebinding all of them keeps
# `main.<name>` exactly the surface it was before the split. Plain assignment rather than
# `from ... import x as x` only because isort expands the aliased form to three lines each.

# --- from cli/ast_scan.py ----------------------------------------------------
_apply_ruleset_baseline = _ast_scan._apply_ruleset_baseline
_build_rulesets_payload = _ast_scan._build_rulesets_payload
_describe_ast_backend_mode = _ast_scan._describe_ast_backend_mode
_describe_ast_backend_modes = _ast_scan._describe_ast_backend_modes
_filter_ast_rule_specs = _ast_scan._filter_ast_rule_specs
_inline_suppression_targets = _ast_scan._inline_suppression_targets
_load_inline_rule_specs = _ast_scan._load_inline_rule_specs
_load_ruleset_baseline = _ast_scan._load_ruleset_baseline
_load_ruleset_suppressions = _ast_scan._load_ruleset_suppressions
_load_sg_project_config = _ast_scan._load_sg_project_config
_load_yaml_dict = _ast_scan._load_yaml_dict
_occurrence_has_inline_suppression = _ast_scan._occurrence_has_inline_suppression
_regex_rule_targets_file = _ast_scan._regex_rule_targets_file
_resolve_ruleset_source_path = _ast_scan._resolve_ruleset_source_path
_ruleset_files_match = _ast_scan._ruleset_files_match
_ruleset_finding_fingerprint = _ast_scan._ruleset_finding_fingerprint
_ruleset_suppression_timestamp = _ast_scan._ruleset_suppression_timestamp
_run_ast_scan_payload = _ast_scan._run_ast_scan_payload
_select_ast_backend_for_pattern = _ast_scan._select_ast_backend_for_pattern
_suppression_entry_matches = _ast_scan._suppression_entry_matches
_truncate_evidence_snippet = _ast_scan._truncate_evidence_snippet
_write_json_refuse_symlink = _ast_scan._write_json_refuse_symlink

# --- from cli/doctor_report.py -----------------------------------------------
_DOCTOR_GPU_PROBE_DEFAULT_FAILURE_STATUS = _doctor_report._DOCTOR_GPU_PROBE_DEFAULT_FAILURE_STATUS
_DOCTOR_GPU_PROBE_FAILURE_STATUS_BY_NATIVE_ERROR_KIND = (
    _doctor_report._DOCTOR_GPU_PROBE_FAILURE_STATUS_BY_NATIVE_ERROR_KIND
)
_DOCTOR_GPU_PROBE_PATH_NOT_FOUND_STATUS_CROSS_DOMAIN = (
    _doctor_report._DOCTOR_GPU_PROBE_PATH_NOT_FOUND_STATUS_CROSS_DOMAIN
)
_DOCTOR_GPU_PROBE_PATH_NOT_FOUND_STATUS_SAME_DOMAIN = (
    _doctor_report._DOCTOR_GPU_PROBE_PATH_NOT_FOUND_STATUS_SAME_DOMAIN
)
_DOCTOR_LSP_WORKSPACE_ERROR_MARKERS = _doctor_report._DOCTOR_LSP_WORKSPACE_ERROR_MARKERS
_DOCTOR_RUST_ANALYZER_MISSING_COMPONENT_TOOLCHAIN_RE = (
    _doctor_report._DOCTOR_RUST_ANALYZER_MISSING_COMPONENT_TOOLCHAIN_RE
)
_DOCTOR_VERSION_NOT_PROVIDED = _doctor_report._DOCTOR_VERSION_NOT_PROVIDED
_ROUTE_ORDER = _doctor_report._ROUTE_ORDER
_any_route_unverifiable = _doctor_report._any_route_unverifiable
_doctor_apply_lsp_missing_component_remediation = (
    _doctor_report._doctor_apply_lsp_missing_component_remediation
)
_doctor_apply_lsp_rust_analyzer_remediation = (
    _doctor_report._doctor_apply_lsp_rust_analyzer_remediation
)
_doctor_apply_lsp_workspace_warnings = _doctor_report._doctor_apply_lsp_workspace_warnings
_doctor_ast_cache_status = _doctor_report._doctor_ast_cache_status
_doctor_ast_grep_status = _doctor_report._doctor_ast_grep_status
_doctor_dense_model_status = _doctor_report._doctor_dense_model_status
_doctor_downgrade_lsp_workspace_proof = _doctor_report._doctor_downgrade_lsp_workspace_proof
_doctor_fresh_shell_path_tg_candidates = _doctor_report._doctor_fresh_shell_path_tg_candidates
_doctor_fresh_shell_path_value = _doctor_report._doctor_fresh_shell_path_value
_doctor_gpu_probe_failure_status = _doctor_report._doctor_gpu_probe_failure_status
_doctor_gpu_probe_native_error_kind = _doctor_report._doctor_gpu_probe_native_error_kind
_doctor_gpu_search_runtime_probe = _doctor_report._doctor_gpu_search_runtime_probe
_doctor_gpu_status = _doctor_report._doctor_gpu_status
_doctor_gpu_tier_installed = _doctor_report._doctor_gpu_tier_installed
_doctor_gpu_tier_usable = _doctor_report._doctor_gpu_tier_usable
_doctor_installation_health = _doctor_report._doctor_installation_health
_doctor_installed_behind_pypi = _doctor_report._doctor_installed_behind_pypi
_doctor_installed_version = _doctor_report._doctor_installed_version
_doctor_lsp_languages = _doctor_report._doctor_lsp_languages
_doctor_lsp_missing_rust_analyzer_component_lines = (
    _doctor_report._doctor_lsp_missing_rust_analyzer_component_lines
)
_doctor_lsp_probe_timeout_seconds = _doctor_report._doctor_lsp_probe_timeout_seconds
_doctor_lsp_provider_statuses = _doctor_report._doctor_lsp_provider_statuses
_doctor_lsp_providers_by_language = _doctor_report._doctor_lsp_providers_by_language
_doctor_lsp_workspace_error_lines = _doctor_report._doctor_lsp_workspace_error_lines
_doctor_mcp_stdio_launcher_warning = _doctor_report._doctor_mcp_stdio_launcher_warning
_doctor_native_frontdoor_flavor_mismatch_note = (
    _doctor_report._doctor_native_frontdoor_flavor_mismatch_note
)
_doctor_native_tg_binary_kind = _doctor_report._doctor_native_tg_binary_kind
_doctor_path_list_separator = _doctor_report._doctor_path_list_separator
_doctor_path_tg_candidates = _doctor_report._doctor_path_tg_candidates
_doctor_path_tg_launcher_warning = _doctor_report._doctor_path_tg_launcher_warning
_doctor_python_subprocess_path_tg_candidate = (
    _doctor_report._doctor_python_subprocess_path_tg_candidate
)
_doctor_resident_worker_status = _doctor_report._doctor_resident_worker_status
_doctor_route_version_matches = _doctor_report._doctor_route_version_matches
_doctor_rust_analyzer_missing_component_remediation = (
    _doctor_report._doctor_rust_analyzer_missing_component_remediation
)
_doctor_rust_binary_remediation = _doctor_report._doctor_rust_binary_remediation
_doctor_rust_binary_version = _doctor_report._doctor_rust_binary_version
_doctor_rust_binary_version_matches = _doctor_report._doctor_rust_binary_version_matches
_doctor_rust_binary_version_status = _doctor_report._doctor_rust_binary_version_status
_doctor_rust_binary_warning = _doctor_report._doctor_rust_binary_warning
_doctor_rust_core_extension_available = _doctor_report._doctor_rust_core_extension_available
_doctor_session_daemon_autostart_status = _doctor_report._doctor_session_daemon_autostart_status
_doctor_session_daemon_status = _doctor_report._doctor_session_daemon_status
_doctor_shadow_launchers = _doctor_report._doctor_shadow_launchers
_doctor_shell_escaping_guidance = _doctor_report._doctor_shell_escaping_guidance
_doctor_skipped_native_tg_binaries = _doctor_report._doctor_skipped_native_tg_binaries
_doctor_tg_candidate_version = _doctor_report._doctor_tg_candidate_version
_doctor_tg_foreign_remediation = _doctor_report._doctor_tg_foreign_remediation
_doctor_tg_foreign_warning = _doctor_report._doctor_tg_foreign_warning
_doctor_tg_launcher_kind = _doctor_report._doctor_tg_launcher_kind
_doctor_tg_version_looks_like_tensor_grep = _doctor_report._doctor_tg_version_looks_like_tensor_grep
_doctor_version_compare = _doctor_report._doctor_version_compare
_doctor_version_tuple = _doctor_report._doctor_version_tuple
_doctor_windows_registry_path_value = _doctor_report._doctor_windows_registry_path_value
_restart_session_daemon_after_upgrade = _doctor_report._restart_session_daemon_after_upgrade
_upgrade_running_session_daemon_snapshot = _doctor_report._upgrade_running_session_daemon_snapshot

# --- from cli/doctor_payload.py ----------------------------------------------
_build_doctor_payload = _doctor_payload._build_doctor_payload
_render_doctor_payload = _doctor_payload._render_doctor_payload

# --- from cli/native_frontdoor.py --------------------------------------------
_NATIVE_FRONTDOOR_FLAVOR_ENV = _native_frontdoor._NATIVE_FRONTDOOR_FLAVOR_ENV
_NATIVE_FRONTDOOR_REQUESTED_FLAVOR_ENV = _native_frontdoor._NATIVE_FRONTDOOR_REQUESTED_FLAVOR_ENV
_NativeFrontdoorAssetCandidate = _native_frontdoor._NativeFrontdoorAssetCandidate
_NativeFrontdoorInstallResult = _native_frontdoor._NativeFrontdoorInstallResult
_PYPI_JSON_URL = _native_frontdoor._PYPI_JSON_URL
_PYPI_SIMPLE_ANCHOR_RE = _native_frontdoor._PYPI_SIMPLE_ANCHOR_RE
_PYPI_SIMPLE_URL = _native_frontdoor._PYPI_SIMPLE_URL
_PYPI_SIMPLE_VERSION_RE = _native_frontdoor._PYPI_SIMPLE_VERSION_RE
_WindowsStalePythonLauncher = _native_frontdoor._WindowsStalePythonLauncher
_WindowsUnownedPythonLauncher = _native_frontdoor._WindowsUnownedPythonLauncher
_candidate_versions_from_pip_index = _native_frontdoor._candidate_versions_from_pip_index
_candidate_versions_from_pip_index_output = (
    _native_frontdoor._candidate_versions_from_pip_index_output
)
_candidate_versions_from_pypi_json = _native_frontdoor._candidate_versions_from_pypi_json
_candidate_versions_from_pypi_simple_index = (
    _native_frontdoor._candidate_versions_from_pypi_simple_index
)
_download_native_frontdoor_asset = _native_frontdoor._download_native_frontdoor_asset
_expected_asset_sha256 = _native_frontdoor._expected_asset_sha256
_fetch_native_frontdoor_checksums = _native_frontdoor._fetch_native_frontdoor_checksums
_highest_tensor_grep_version = _native_frontdoor._highest_tensor_grep_version
_install_release_native_frontdoor = _native_frontdoor._install_release_native_frontdoor
_is_version_newer = _native_frontdoor._is_version_newer
_latest_pypi_tensor_grep_version = _native_frontdoor._latest_pypi_tensor_grep_version
_managed_native_frontdoor_path = _native_frontdoor._managed_native_frontdoor_path
_managed_native_frontdoor_path_from_env = _native_frontdoor._managed_native_frontdoor_path_from_env
_native_frontdoor_asset_candidates = _native_frontdoor._native_frontdoor_asset_candidates
_native_frontdoor_checksum_error = _native_frontdoor._native_frontdoor_checksum_error
_native_frontdoor_checksums_url = _native_frontdoor._native_frontdoor_checksums_url
_native_frontdoor_downgrade_reason = _native_frontdoor._native_frontdoor_downgrade_reason
_native_frontdoor_download_candidates = _native_frontdoor._native_frontdoor_download_candidates
_native_frontdoor_download_error_for_flavor = (
    _native_frontdoor._native_frontdoor_download_error_for_flavor
)
_normalize_native_frontdoor_flavor = _native_frontdoor._normalize_native_frontdoor_flavor
_requested_native_frontdoor_flavor = _native_frontdoor._requested_native_frontdoor_flavor
_verify_target_python_tensor_grep_version = (
    _native_frontdoor._verify_target_python_tensor_grep_version
)
_version_sort_key = _native_frontdoor._version_sort_key
_windows_managed_native_bin_dir = _native_frontdoor._windows_managed_native_bin_dir
_write_native_frontdoor_metadata = _native_frontdoor._write_native_frontdoor_metadata

# --- from cli/windows_launcher.py --------------------------------------------
_WINDOWS_EXE_BRIDGE_MARKER = _windows_launcher._WINDOWS_EXE_BRIDGE_MARKER
_WINDOWS_EXE_BRIDGE_MARKER_CONTENT = _windows_launcher._WINDOWS_EXE_BRIDGE_MARKER_CONTENT
_ensure_windows_managed_native_first_on_path = (
    _windows_launcher._ensure_windows_managed_native_first_on_path
)
_looks_like_windows_file_lock_error = _windows_launcher._looks_like_windows_file_lock_error
_refresh_managed_native_frontdoor = _windows_launcher._refresh_managed_native_frontdoor
_refresh_windows_tensor_grep_com_bridges = (
    _windows_launcher._refresh_windows_tensor_grep_com_bridges
)
_refreshed_com_bridge_message = _windows_launcher._refreshed_com_bridge_message
_remove_windows_stale_tensor_grep_python_launchers = (
    _windows_launcher._remove_windows_stale_tensor_grep_python_launchers
)
_repair_windows_python_subprocess_launcher = (
    _windows_launcher._repair_windows_python_subprocess_launcher
)
_same_path = _windows_launcher._same_path
_schedule_windows_native_frontdoor_refresh = (
    _windows_launcher._schedule_windows_native_frontdoor_refresh
)
_set_windows_user_path_value = _windows_launcher._set_windows_user_path_value
_windows_exe_bridge_marker_path = _windows_launcher._windows_exe_bridge_marker_path
_windows_managed_compat_shim_dirs = _windows_launcher._windows_managed_compat_shim_dirs
_windows_path_part_key = _windows_launcher._windows_path_part_key
_windows_path_parts = _windows_launcher._windows_path_parts
_windows_prepend_path_part = _windows_launcher._windows_prepend_path_part
_windows_python_install_scripts_executable = (
    _windows_launcher._windows_python_install_scripts_executable
)
_windows_python_scripts_tensor_grep_package_version = (
    _windows_launcher._windows_python_scripts_tensor_grep_package_version
)
_windows_python_subprocess_resolution_blocker = (
    _windows_launcher._windows_python_subprocess_resolution_blocker
)
_windows_stale_tensor_grep_com_bridges = _windows_launcher._windows_stale_tensor_grep_com_bridges
_windows_tensor_grep_python_launcher_scan = (
    _windows_launcher._windows_tensor_grep_python_launcher_scan
)
_windows_user_path_value = _windows_launcher._windows_user_path_value
_write_windows_exe_bridge_marker = _windows_launcher._write_windows_exe_bridge_marker

# backlog #1 (Fable+thinktank plan, 2026-07-06): kept numerically in sync with
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
_BUILTIN_TYPE_LIST = (
    "asm: *.asm, *.s, *.S",
    "c: *.c, *.h",
    "cpp: *.cc, *.cpp, *.cxx, *.hpp, *.hh, *.hxx",
    "csharp: *.cs",
    "css: *.css",
    "go: *.go",
    "html: *.htm, *.html",
    "java: *.java",
    "javascript: *.js, *.jsx, *.mjs, *.cjs",
    "json: *.json, *.jsonl",
    "kotlin: *.kt, *.kts",
    "lua: *.lua",
    "markdown: *.md, *.markdown",
    "php: *.php",
    "python: *.py, *.pyi",
    "rust: *.rs",
    "swift: *.swift",
    "toml: *.toml",
    "typescript: *.ts, *.tsx",
    "yaml: *.yml, *.yaml",
)

app = typer.Typer(
    help="""tensor-grep (tg) - Fast text, AST, indexed, and GPU-aware search CLI

Search code and large datasets with ripgrep-compatible text search, native AST search/rewrite,
persisted repeated-query acceleration, and optional GPU routing.

**Common usage**
- `tg PATTERN [PATH ...]`
- `tg search [OPTIONS] PATTERN [PATH ...]`
- `tg run PATTERN [PATH]`
- `tg agent PATH "change invoice tax"`
- `tg scan --config sgconfig.yml`
- `tg doctor --with-lsp`
- `tg dogfood --output artifacts/dogfood_readiness.json`
- `tg repair-launcher`
- `tg mcp`

**AI workflows**
- `tg map PATH`
- `tg context-render PATH "invoice flow"`
- `tg edit-plan PATH "add retry with tests"`
- `tg agent PATH "change behavior" --json`
- `tg blast-radius PATH create_invoice --json`  (caller graph; `blast-radius-render` = prose bundle)
- `tg session open PATH`
- `tg session daemon start PATH`

**Agent contracts**
- `tg agent` emits primary targets, alternative targets, snippets, validation_commands, rollback metadata, confidence, optional gpu_acceleration route evidence, and ask-before-editing guidance.
- `tg agent --gpu-device-ids 0,1 --json` runs an opt-in native GPU evidence scan; sidecar-routed GPU results are reported as unsupported.
- `context-render` and `edit-plan` also expose top-level validation_commands.
- Validation command templates can quote `$file` or `{file}` placeholders; the command is split into a program and arguments and spawned directly (no shell), so the file path is passed as a single argument and shell constructs (pipes, `&&`, redirects, `cmd`/`sh` builtins) are not interpreted. Applied rewrites run placeholder commands once per edited file.

**Search and safety**
- Use `--format rg --sort path` for deterministic ripgrep-shaped text output.
- The search surface is a validated common rg-compatible subset, not a full ripgrep replacement.
- Use `--format rg --json` for ripgrep JSON Lines events; plain `--json` is tensor-grep aggregate JSON.
- Direct generated-root, broad file-list, and multi-project workspace-root scans are refused unless scoped with paths, `--glob`, `--type`, `--max-depth`, or explicit `--allow-broad-generated-scan`; project-root `--no-ignore` content searches follow ripgrep.
- On Windows, PowerShell double quotes expand $NAME before `tg` receives literal patterns; use single quotes or escape `$`. In `cmd.exe`, quote or caret-escape metacharacters such as `|` and `&`.
- `--smart-case`, `--hidden`, `--max-depth`, and `--text` are honored by structured CPU and sidecar search; native GPU falls back when a requested switch changes semantics it cannot safely execute yet.
- `--gpu-device-ids` pins selected GPUs for explicit search, benchmark, and agent evidence probes; GPU remains experimental until 1GB/5GB correctness and speed beat both `rg` and `tg_cpu`.
- `classify` is local by default; set `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` to opt into CyBERT/Triton.

**Notes**
- Bare patterns and option-first common search flags are treated as `tg search`, including `tg -t js PATTERN PATH` and `tg --count-matches PATTERN PATH`.
- Use `tg search --help` for the current validated rg-compatible flag subset.
- `tg run --help` for AST rewrite flags.
- Lexical repo-map retrieval bridges camelCase, snake_case, and source-term planning queries.
- Use `tg doctor --json` for system, GPU, cache, daemon, and launcher diagnostics including path_tg_first_launcher_kind and fresh_shell_path_tg_first_launcher_kind.
- Use `tg repair-launcher` to remove verified or self-identifying tensor-grep Python Scripts launchers that shadow the managed native front door; add `--allow-foreign-rename` only for a foreign `tg.exe` that you own and want tensor-grep to back up.
- Use `tg session --help` for cached edit-loop and daemon commands; daemon-routed edit-plan/context requests keep a short connect probe, a longer work response timeout, and byte-bounded response-cache stats.

**Environment overrides**
- `TG_SIDECAR_PYTHON`: Path to the Python executable used for sidecar-backed commands.
- `TG_NATIVE_TG_BINARY`: Path to the native front door used by Python-backed commands.
- `TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR`: Set to `nvidia` to prefer NVIDIA release-native front-door assets, with CPU fallback.
- `TG_RG_PATH`: Path to the ripgrep executable used for text-search passthrough.
- `TG_FORCE_CPU`: Force CPU routing for search commands.
- `TG_SIDECAR_TIMEOUT_MS`: Timeout for sidecar-backed commands.
- `TG_HELP_PROBE_TIMEOUT_MS`: Timeout for the native front door's `--help` passthrough probe to this rich Python help before it falls back to the condensed native help (default 3000ms).
- `TENSOR_GREP_DEVICE_IDS`: Comma-separated GPU IDs available to tensor-grep.
- `TENSOR_GREP_CLASSIFY_PROVIDER`: Set to `cybert` to opt into CyBERT/Triton classification.
- `TENSOR_GREP_TRITON_TIMEOUT_SECONDS`: Timeout for Triton-backed NLP probes.
- `TG_MCP_ALLOW_VALIDATION_COMMANDS`: Set to `1` to let the `tg mcp` server's `tg_rewrite_apply` tool accept and shell-execute `lint_cmd` / `test_cmd`; default off (such requests are rejected with `code="unsupported_option"`).
- `TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS`: Total per-command budget for optional external LSP provider requests before native fallback.
- `TENSOR_GREP_CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_STRING_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_AST_QUERY_CACHE_MAX_ENTRIES`, `TENSOR_GREP_AST_NODE_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_REPO_CONTEXT_CACHE_MAX_ROOTS`: Bound long-lived in-process search and repo-context caches.
- `TENSOR_GREP_SESSION_RESPONSE_CACHE_MAX_BYTES`, `TENSOR_GREP_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES`, `TENSOR_GREP_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES`: Bound agent-loop response and LSP provider caches.
- `TG_SESSION_DAEMON_AUTOSTART`: Default-ON warm-daemon fast path for `defs`/`impact`/`refs`/`callers`/`blast-radius` (probes a running `tg session daemon`; auto-spawns one non-blocking on a miss, so only the first call per root pays the cold-start cost). Set to `0`/`false`/`no`/`off` to opt back out to the always-cold path; always forced off when `CI` or `GITHUB_ACTIONS` is set. Querying N distinct repo roots with this on can leave up to N resident daemons; each self-shuts-down after `TG_SESSION_DAEMON_IDLE_SECONDS` (900s default) of inactivity.""",
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="markdown",
)
checkpoint_app = typer.Typer(
    help="Create, list, and undo edit checkpoints.",
    no_args_is_help=True,
)
session_app = typer.Typer(
    help="Open and reuse cached repository-map sessions.",
    no_args_is_help=True,
)
session_daemon_app = typer.Typer(
    help="Run and inspect the warm localhost session daemon.",
    no_args_is_help=True,
)
review_bundle_app = typer.Typer(
    help="Create and verify enterprise review bundles.",
    no_args_is_help=True,
)


class _EvidenceGroup(TyperGroup):
    """Nudge `tg evidence <path>` toward the `emit` subcommand.

    Dogfood trap (v1.61.2): an agent reaches for `tg evidence <PATH> <query>` by
    analogy with `tg defs`/`tg orient` (which take a path directly), but `evidence`
    is a command GROUP whose only action is `emit`. Click's default
    "No such command 'src/...'" is correct (exit 2) but unhelpful -- when the unknown
    subcommand looks like a filesystem path, append the concrete fix so the caller
    does not have to re-read `--help`.
    """

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        try:
            return super().resolve_command(ctx, args)
        except click.exceptions.UsageError as exc:
            token = args[0] if args else ""
            if (
                token
                and not token.startswith("-")
                and ("/" in token or "\\" in token or Path(token).exists())
            ):
                # Re-raise a NEW UsageError with the hint appended -- `UsageError.message` is a
                # Final attribute (cannot be reassigned in place); a fresh error carrying the same
                # `ctx` renders identically (Usage + "Try --help" + Error:) and keeps exit code 2.
                raise click.exceptions.UsageError(
                    f"{exc.format_message()}\n"
                    "Hint: `tg evidence` is a command group; its receipt action is "
                    f"`emit`. Did you mean `tg evidence emit {token}`? "
                    "(run `tg evidence emit --help`)",
                    ctx=exc.ctx,
                ) from exc
            raise


evidence_app = typer.Typer(
    cls=_EvidenceGroup,
    help="Emit a versioned EvidenceReceipt aggregating tg's existing outputs.",
    no_args_is_help=True,
)

ledger_app = typer.Typer(
    help="EXPERIMENTAL: advisory, code-scoped agent-to-agent coordination. Slice 1 (claim/"
    "release/list) never blocks an edit; Slice 2 (record/find) is content-addressed artifact "
    "reuse with revision-freshness. PATH SCOPING (Slice 1): claim/release/list all "
    "canonicalize to the SAME repository root regardless of which subtree PATH names -- `list` "
    "rolls up (listing a broader/ancestor PATH, e.g. the default `.`, shows every live claim "
    "scoped to it or to any descendant subtree, each tagged with its own `scope`); `release` "
    "keeps exact `--claim-id`/`--symbol` matching but names live claims elsewhere when nothing "
    "matches. Surface and JSON schema may change in a minor release.",
    no_args_is_help=True,
)

session_app.add_typer(session_daemon_app, name="daemon")


# A3 (PR #1070): distinguishable from a real version so SARIF can disclose degradation.
_VERSION_UNAVAILABLE_SENTINEL = "0.0.0-unavailable"


def _read_project_version_fallback() -> str:
    try:
        pyproject_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
        for line in pyproject_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version = "):
                return stripped.split('"', 2)[1]
    except Exception:
        pass
    return _VERSION_UNAVAILABLE_SENTINEL


def _cli_package_version() -> str:
    try:
        from importlib.metadata import version

        return version("tensor-grep")
    except Exception:
        return _self._read_project_version_fallback()


_MAX_NATIVE_ASSET_DOWNLOAD_BYTES = 512 * 1024 * 1024


def _version_detail_lines() -> tuple[str, ...]:
    return (
        "",
        "features:+gpu-cudf,+gpu-torch,+rust-core",
        "simd(compile):+SSE2,-SSSE3,-AVX2",
        "simd(runtime):+SSE2,+SSSE3,+AVX2",
        "",
        "Arrow Zero-Copy IPC is available",
    )


def _print_version(*, verbose: bool = False) -> None:
    print(f"tensor-grep {_cli_package_version()}")
    if verbose:
        for line in _version_detail_lines():
            print(line)


def _json_output_version() -> int:
    """Wire-schema version for the ``--json`` envelope (DC-001).

    Reads the shipped literal rather than scraping ``rust_core/src/main.rs``:
    that scrape resolved through ``parents[3]`` -- the repo root in a dev
    checkout, but the directory above ``site-packages`` in a wheel, where
    ``rust_core/`` is absent, so it caught ``OSError`` and silently returned a
    hardcoded 1 forever. See ``core.result.JSON_OUTPUT_VERSION``; cross-pinned
    by ``tests/unit/test_json_output_version_pin.py``."""
    return _JSON_OUTPUT_VERSION_CONTRACT.JSON_OUTPUT_VERSION


def _with_schema_version(payload: dict[str, Any], *, version: int | None = None) -> dict[str, Any]:
    stamped = dict(payload)
    resolved_version = stamped.get(
        "version", version if version is not None else _json_output_version()
    )
    stamped.setdefault("version", resolved_version)
    stamped.setdefault("schema_version", resolved_version)
    return stamped


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


@dataclasses.dataclass(frozen=True)
class _TailExitCodePolicy:
    """One exit-code RULE that `search_command`'s Python TAIL applies -- i.e. code that only
    runs when a request did NOT take the native-delegation exit
    (``sys.exit(_delegate_to_native_tg_search(...))``, above ``_can_delegate_to_native_tg_search``
    in this module).

    Task 22 investigation: PR #868 added a tail-only exit-code rule
    (``gpu_request_unhonoured``) keyed on a `SearchConfig` field (``gpu_device_ids``) that is
    ELIGIBLE for delegation rather than refused, so a request that triggers the rule can also
    take the OTHER route -- where whether the native binary applies the identical rule is a
    completely separate question this module cannot answer by inspection. This registry makes
    every such rule an explicit, reviewed decision instead of a silent assumption; see
    ``tests/unit/test_search_command_tail_exit_policy_route_parity.py``, which enumerates it and
    fails loudly on an unclassified entry.
    """

    name: str
    """Human-readable identifier for the rule (matches the predicate/variable name in
    `search_command`, e.g. ``"gpu_request_unhonoured"``)."""

    trigger_fields: frozenset[str]
    """`SearchConfig` field name(s) that must be non-default for this rule to ever fire. An
    empty frozenset means the rule can fire on ANY request regardless of flags (e.g. a walk
    error can happen on a plain search too), so route parity is unconditionally required."""

    mirrored_in_native_at: str | None = None
    """A citation into the Rust source (e.g. ``"rust_core/src/main.rs:13064
    walk_was_incomplete()"``) where a human manually verified the native binary applies the SAME
    rule. Exactly one of this and `route_specific_reason` must be set -- never both, never
    neither."""

    route_specific_reason: str | None = None
    """Non-empty iff this rule is knowingly Python-route-only (unverified, or verified NOT to
    hold, on the native binary) -- must name the tracking issue/PR so the gap stays discoverable
    rather than silently assumed. Exactly one of this and `mirrored_in_native_at` must be set."""


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
    config: "SearchConfig",
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
    config: "SearchConfig",
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
    config: "SearchConfig",
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
    from tensor_grep.cli.subprocess_policy import configured_ripgrep_timeout_seconds

    try:
        completed = subprocess.run(
            command, check=False, timeout=configured_ripgrep_timeout_seconds()
        )
    except subprocess.TimeoutExpired:
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
    scanner: "DirectoryScanner", paths: list[str]
) -> tuple[list[str], set[str]]:
    ordered = []
    seen = set()
    for p in paths:
        for current_file in scanner.walk(p):
            if current_file not in seen:
                seen.add(current_file)
                ordered.append(current_file)
    return ordered, seen


def _write_path_list(paths: list[str], *, use_nul: bool) -> None:
    if not paths:
        return
    if use_nul:
        payload = b"\x00".join(os.fsencode(path) for path in paths) + b"\x00"
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return
    _safe_stdout_line("\n".join(paths))


def _path_output_sort_key(path: str) -> str:
    return path.replace("\\", "/").lower()


def _ordered_path_output(paths: list[str], config: "SearchConfig") -> list[str]:
    if config.sort_by == "path":
        return sorted(paths, key=_path_output_sort_key)
    if config.sort_by_reverse == "path":
        return sorted(paths, key=_path_output_sort_key, reverse=True)
    return paths


def _looks_like_binary_path(path: str) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return b"\0" in handle.read(8192)
    except OSError:
        return False


def _path_has_hidden_component(path: str) -> bool:
    return any(part.startswith(".") and part not in {".", ".."} for part in Path(path).parts)


def _safe_stdout_line(text: str) -> None:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None and encoding and "utf" not in encoding and not text.isascii():
        buffer.write(f"{text}\n".encode("utf-8", errors="replace"))
        flush = getattr(buffer, "flush", None)
        if callable(flush):
            flush()
        return
    try:
        print(text)
    except UnicodeEncodeError:
        payload = f"{text}\n".encode("utf-8", errors="replace")
        if buffer is not None:
            buffer.write(payload)
            flush = getattr(buffer, "flush", None)
            if callable(flush):
                flush()
            return
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        escaped_text = f"{text}\n".encode(encoding, errors="backslashreplace").decode(
            encoding, errors="ignore"
        )
        sys.stdout.write(escaped_text)
        flush = getattr(sys.stdout, "flush", None)
        if callable(flush):
            flush()


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


def _search_with_cpu_fallback(
    current_file: str,
    pattern: str,
    config: "SearchConfig",
    exc: Exception,
) -> "SearchResult":
    """Retry a failed native-backend search on the always-available CPU backend.

    A runtime backend failure (native panic, IO/encoding error, version skew, GPU/OOM
    fault) must never surface to the user as a clean no-match. The CPU backend is pure
    Python and always available, so it is the safe last-resort engine; the override is
    announced on stderr so it is observable rather than silent (audit B2/I1).
    """
    from tensor_grep.backends.cpu_backend import CPUBackend

    sys.stderr.write(
        f"tensor-grep: search backend failed on {current_file} ({exc}); "
        "retried on the CPU backend.\n"
    )
    return CPUBackend().search(current_file, pattern, config=config)


# F5 (Fable audit MED): retrieval_chunker.MAX_CHUNKS bounds a single chunk_file() call (per FILE).
# A matched-file set of many small files can still blow past a sane CORPUS-wide total even though
# no single file trips the per-file guard, so DenseIndex.__init__'s single-batch encode would face
# unbounded memory. Cap the CORPUS total here too, sharing the same threshold as the per-file guard
# (no separate magic number to keep in sync).
_SEMANTIC_CORPUS_CHUNK_CAP = MAX_CHUNKS


def _set_semantic_rank_fallback_reason(all_results: "SearchResult") -> None:
    """Probe dense-leg availability and set ``rank_fallback_reason`` (F16, Fable audit LOW).

    Used for the 0-match `--semantic` case: with no matches there is nothing to rerank, so the
    full :func:`_apply_semantic_rerank` path (chunking, model load) is skipped entirely -- but the
    availability probe must still run so the JSON envelope stays honest (a dense-unavailable
    search must report that even when it happens to find zero matches, not silently omit
    ``rank_fallback_reason``).
    """
    from tensor_grep.core.retrieval_dense import dense_available

    available, unavailable_reason = dense_available()
    if not available:
        all_results.rank_fallback_reason = unavailable_reason
        sys.stderr.write(f"tg: {unavailable_reason}\n")


def _note_late_rerank_degraded(all_results: "SearchResult", reason: str) -> None:
    """Append (or set) ``rank_fallback_reason`` for a RECOVERABLE late-rerank degrade (the
    ``rerank`` extra absent, or the model not fetched) and echo the same ``tg:``-prefixed stderr
    line the dense leg uses (T6, design doc "Fail-closed contract"). Appends rather than
    overwrites so a simultaneous dense-leg degrade is never clobbered -- both signals must survive
    on the returned envelope.
    """
    if all_results.rank_fallback_reason:
        all_results.rank_fallback_reason = f"{all_results.rank_fallback_reason}; {reason}"
    else:
        all_results.rank_fallback_reason = reason
    sys.stderr.write(f"tg: {reason}\n")


def _apply_semantic_rerank(all_results: "SearchResult", pattern: str) -> "SearchResult":
    """Apply the `--semantic` hybrid (BM25 + dense RRF [+ late MaxSim]) rerank, fail-closed to
    BM25-only.

    The dense leg is best-effort: when the `semantic` extra is absent, the model has not been
    fetched, the model produces a malformed/mismatched embedding, or a query-time dense fault
    occurs (F1: e.g. a dim mismatch raised from inside `rerank_hybrid`'s call to
    `DenseIndex.query`), this degrades VISIBLY to a BM25-only rerank (stderr warning +
    ``rank_fallback_reason`` set) -- it never silently returns unranked output and never mislabels
    BM25-only output as "semantic". A genuine backend fault (e.g. a corrupt model directory)
    raises ``BackendExecutionError`` instead of degrading, per the Backend Fail-Closed Contract --
    that is NOT caught here; the caller (the `search` command) must catch it and exit cleanly
    (F4).

    T5/T6 (design doc "The seam" + "Fail-closed contract"): when ``TG_LATE_RERANK=1`` is set, a
    late-interaction (MaxSim) reranker is built here (``late_available()`` probe, then
    ``load_late_reranker()``) and passed into the PRIMARY ``rerank_hybrid`` call only -- never
    into any of the BM25-only degrade retries below, since those already mean the whole hybrid
    stage bypassed the late stage too (each appends "; late rerank skipped" to its
    ``rank_fallback_reason`` when late rerank was requested). A RECOVERABLE late-leg failure
    (extra absent, model not fetched) degrades here exactly like the dense leg; an UNRECOVERABLE
    ``BackendExecutionError`` (e.g. a corrupt model directory) deliberately propagates, same as
    the dense leg's.
    """
    from tensor_grep.core.reranker import rerank_hybrid
    from tensor_grep.core.retrieval_bm25 import Bm25Index
    from tensor_grep.core.retrieval_chunker import Chunk, chunk_file
    from tensor_grep.core.retrieval_dense import (
        DenseIndex,
        DenseUnavailableError,
        default_model_dir,
        dense_available,
        load_dense_model,
    )

    late_rerank_requested = os.environ.get("TG_LATE_RERANK") == "1"

    def _maybe_append_late_skip(reason: str) -> str:
        """The 3 upstream degrade paths below all BYPASS the late stage entirely (it is only ever
        wired into the PRIMARY rerank_hybrid call further down) -- when late rerank was
        requested, say so explicitly rather than leaving the envelope silently ambiguous about
        why no late reorder happened (T6)."""
        return f"{reason}; late rerank skipped" if late_rerank_requested else reason

    dense_index = None
    available, unavailable_reason = dense_available()
    if not available:
        all_results.rank_fallback_reason = unavailable_reason
        sys.stderr.write(f"tg: {unavailable_reason}\n")

    # F3 (Fable audit MED): build the chunk corpus ONCE and share it between the BM25 and dense
    # legs. Previously the dense leg's corpus was built here while the BM25 leg rebuilt its own
    # corpus from scratch inside `rerank_hybrid` (bm25_index=None) -- a second full file-I/O pass,
    # and a silent RRF-misalignment risk if the two passes' chunk_size/overlap defaults ever
    # diverge.
    chunks: list[Chunk] = []
    for path in all_results.matched_file_paths:
        try:
            file_chunks = chunk_file(path)
        except RuntimeError as exc:
            # F5: retrieval_chunker's own MAX_CHUNKS guard is PER FILE; a single pathological file
            # can still trip it on its own even before the corpus-wide cap below is reached. Either
            # way, degrade to BM25-only (using whatever we already have -- discard the corpus, let
            # rerank_hybrid rebuild its own BM25-only chunks) rather than crash the whole search.
            all_results.rank_fallback_reason = _maybe_append_late_skip(str(exc))
            sys.stderr.write(f"tg: {exc}\n")
            return rerank_hybrid(
                all_results,
                pattern,
                all_results.matched_file_paths,
                # A2 (external audit 2026-07-11): reuse the chunks accumulated so far (already bounded
                # by the corpus cap / the file that raised) -- passing a prebuilt index stops
                # rerank_hybrid re-reading + re-chunking the FULL corpus UNCAPPED, which turned this
                # safety guard into the expensive op it exists to prevent. Mirrors the F1 retry below.
                bm25_index=Bm25Index(chunks),
                dense_index=None,
            )
        chunks.extend(file_chunks)
        if len(chunks) > _SEMANTIC_CORPUS_CHUNK_CAP:
            reason = (
                "semantic ranking unavailable: corpus chunk cap "
                f"({_SEMANTIC_CORPUS_CHUNK_CAP}) exceeded across the matched file set -- narrow "
                "the search to fewer files for a semantic rerank"
            )
            all_results.rank_fallback_reason = _maybe_append_late_skip(reason)
            sys.stderr.write(f"tg: {reason}\n")
            return rerank_hybrid(
                all_results,
                pattern,
                all_results.matched_file_paths,
                # A2 (external audit 2026-07-11): reuse the chunks accumulated so far (already bounded
                # by the corpus cap / the file that raised) -- passing a prebuilt index stops
                # rerank_hybrid re-reading + re-chunking the FULL corpus UNCAPPED, which turned this
                # safety guard into the expensive op it exists to prevent. Mirrors the F1 retry below.
                bm25_index=Bm25Index(chunks),
                dense_index=None,
            )

    bm25_index = Bm25Index(chunks)

    if available:
        try:
            model = load_dense_model(default_model_dir())
            dense_index = DenseIndex(chunks, model)
        except DenseUnavailableError as exc:
            # v1.92.1 dogfood item 3: rewrite the raw module-CLI fetch hint into the friendly
            # `tg install-dense` one-shot -- mirrors `tg find`'s identical treatment below
            # (`_friendly_dense_unavailable_message`); previously only `tg find` got this, so
            # `tg search --semantic`'s "model not fetched" degrade still showed the raw
            # `python -m tensor_grep.core.retrieval_dense --fetch` command. A no-op for any
            # DenseUnavailableError that doesn't carry the raw hint (e.g. a malformed-shape
            # message never mentions fetch).
            message = _friendly_dense_unavailable_message(exc)
            all_results.rank_fallback_reason = message
            sys.stderr.write(f"tg: {message}\n")
        # BackendExecutionError (e.g. a corrupt model directory) deliberately propagates: that is
        # an unrecoverable fault the CLI boundary must catch and exit on (F4), not degrade here.

    late_reranker = None
    if late_rerank_requested:
        from tensor_grep.core.retrieval_late import (
            LateRerankUnavailableError,
            late_available,
            load_late_reranker,
        )

        late_ok, late_reason = late_available()
        if not late_ok:
            _note_late_rerank_degraded(all_results, late_reason or "late rerank unavailable")
        else:
            try:
                late_reranker = load_late_reranker()
            except LateRerankUnavailableError as exc:
                _note_late_rerank_degraded(all_results, str(exc))
            # BackendExecutionError (e.g. a corrupt model directory) deliberately propagates: an
            # unrecoverable fault the CLI boundary must catch and exit on, not degrade here --
            # mirrors the dense leg immediately above.

    try:
        return rerank_hybrid(
            all_results,
            pattern,
            all_results.matched_file_paths,
            bm25_index=bm25_index,
            dense_index=dense_index,
            late_reranker=late_reranker,
        )
    except DenseUnavailableError as exc:
        # F1: a query-time dense fault (e.g. a dim mismatch) is raised from INSIDE rerank_hybrid's
        # call to `DenseIndex.query`, outside the try/except above (which only guards index
        # construction). Degrade to BM25-only here too -- reuse the SAME bm25_index (no second
        # chunk pass) rather than let it traceback. This also BYPASSES the late stage (it is only
        # ever wired into the primary call above, never into this retry).
        all_results.rank_fallback_reason = _maybe_append_late_skip(str(exc))
        sys.stderr.write(f"tg: {exc}\n")
        return rerank_hybrid(
            all_results,
            pattern,
            all_results.matched_file_paths,
            bm25_index=bm25_index,
            dense_index=None,
        )


# tg find (Wave 2b/2c, #189): the whole-repo hybrid semantic search command. Shares the exact
# leg-construction/degrade scaffold `_apply_semantic_rerank` uses above (dense-leg availability
# probe, late-rerank env gate, BackendExecutionError propagation) but differs in one load-bearing
# way: `--semantic`'s corpus is already regex-prefiltered by `search`'s matched-file set, so a
# corpus-cap trip there is a benign BM25-only degrade (exit 0). `tg find` walks the WHOLE repo with
# no prefilter, so a corpus-cap/deadline/max-repo-files trip here means the ranking covers only
# PART of the repo -- that must surface as `result_incomplete` + exit 2 (fix-approach council
# must-fix C2), never a silent exit-0 degrade.
_FIND_CORPUS_CHUNK_CAP = MAX_CHUNKS

# CEO#7: the raw fetch-command hint baked into retrieval_dense.py's `DenseUnavailableError` ("not
# fetched" case) predates the one-shot `tg install-dense` command -- see
# `_friendly_dense_unavailable_message` below.
_DENSE_FETCH_RAW_HINT = "python -m tensor_grep.core.retrieval_dense --fetch"
_DENSE_FETCH_FRIENDLY_HINT = "tg install-dense"


def _friendly_dense_unavailable_message(exc: BaseException) -> str:
    """CLI-boundary rewrite of the dense leg's raw fetch-command hint into the one-shot
    `tg install-dense` command (CEO#7) a real `tg find` user should run instead.

    Deliberately a string substitution at the CALL SITE, not an edit to the library exception
    message itself (`retrieval_dense.py`'s `DenseUnavailableError` "not fetched" message is pinned
    verbatim -- including the raw `python -m tensor_grep.core.retrieval_dense --fetch` command --
    by `test_retrieval_dense_fetch.py::test_not_fetched_error_names_the_new_fetch_command`, which
    documents that message as the module-CLI-only front door). A no-op when the raw hint is absent
    (e.g. a dim-mismatch or malformed-shape `DenseUnavailableError`, which never mentions fetch).
    """
    return str(exc).replace(_DENSE_FETCH_RAW_HINT, _DENSE_FETCH_FRIENDLY_HINT)


# #189 (ledger DENSE-WEIGHT SWEEP, agent a8580b6e 2026-07-16): the golden-set sweep measured a
# real ndcg@10/recall lift (1:5 bm25:dense -> +0.1419 ndcg@10, recall 0.55->0.80, ZERO
# per-category regression) on the 40 NL vocab-mismatch golden queries -- but that set is 100% NL
# and cannot see the opposite failure mode: a short/lexical query (the canary `vm-behavior-10`)
# where BM25 is the stronger leg and boosting dense regresses it. `TG_FIND_DENSE_WEIGHT` is the
# knob (see `core/reranker.py`'s `rank_chunks(dense_weight=...)`); `_find_dense_weight` below is
# the guard that keeps the boost scoped to the query shape the sweep actually validated.
# #191 (THE FLIP): the env-unset default is no longer the inert 1.0 no-op. With the classifier
# hardened (whitespace gate, below) and the flip-prep NITs closed (nan/inf clamp, 3-token
# identifier re-sweep), env-unset now applies the SAME adaptive rule a valid explicit override
# would: a multi-word NL query gets `_FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT` (5.0, the ledger-swept
# 1:5 bm25:dense ratio); a single whitespace-free token stays at the protected 1.0. A malformed or
# non-finite override (a typo, `nan`, `inf`) is now treated exactly like unset -- it resolves to
# the SAME adaptive default rather than silently opting a typo'd operator OUT of the improved
# default (thinktank rank-lens must-fix, 2026-07-16). An operator who wants the OLD equal-weight
# fusion back must set `TG_FIND_DENSE_WEIGHT=1.0` explicitly.
_FIND_DENSE_WEIGHT_ENV = "TG_FIND_DENSE_WEIGHT"
_FIND_DENSE_WEIGHT_DEFAULT = 1.0
# The ledger-validated adaptive weight (#191, DENSE-WEIGHT SWEEP): encodes the swept 1:5
# bm25:dense ratio -- `rank_chunks` fixes the bm25 leg at 1.0, so `dense_weight=5.0` IS the 1:5
# ratio (tg_find_review_ledger.md DENSE-WEIGHT SWEEP). This is now the env-UNSET (and
# env-malformed/non-finite) resolution for a genuinely multi-word query; a single-token query
# still resolves to `_FIND_DENSE_WEIGHT_DEFAULT` above via the whitespace gate.
_FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT = 5.0
# Whitespace-based NL-vs-literal classifier (dogfood finding, #191): a query with MORE THAN ONE
# whitespace-separated word is NL/multi-word and gets the adaptive weight; a single whitespace-free
# token is a literal identifier (a symbol name, a grep-style search) and stays at 1.0. The prior
# `split_terms(query) > 2` morpheme-count floor was WRONG: `split_terms` splits snake_case/camelCase
# into MORPHEMES, so a descriptive single-token identifier (`_confine_mcp_path`, `getUserName`,
# `BackendExecutionError`, `reciprocal_rank_fusion` -- all 3 morphemes) counted as "NL" and leaked
# to the dense boost. A real-repo dogfood on tensor-grep's own src caught this: 5 of 6 literal
# identifier queries were misclassified; only `rank_chunks` (2 morphemes) stayed protected. Every
# literal query in the dogfood was a single whitespace-free token and every NL query had 6+
# space-separated words -- whitespace is the clean separator the morpheme floor couldn't exploit.


def _find_dense_weight(query: str) -> float:
    """Query-adaptive `dense_weight` for `tg find`'s `rank_chunks` calls ONLY (#189, flipped ON by
    #191): with `TG_FIND_DENSE_WEIGHT` unset -- or set to something malformed/unparseable/
    non-finite (see flip-prep NIT 1 below) -- a genuinely multi-word NL query now gets the
    ledger-validated `_FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT` (5.0) instead of the old inert 1.0
    no-op. A single whitespace-free token ALWAYS stays at the protected
    `_FIND_DENSE_WEIGHT_DEFAULT` (1.0), regardless of the env var's state -- the whitespace gate
    below applies uniformly to the adaptive default, a malformed/non-finite fallback, AND an
    explicit override alike.

    When the env var IS set to a valid FINITE float, that EXPLICIT value replaces the adaptive
    default for a multi-word query -- `TG_FIND_DENSE_WEIGHT=1.0` is the explicit opt-out back to
    the old equal-weight fusion; any other finite value (e.g. `=3.0`) is honored verbatim. A
    single whitespace-free token (a bare identifier, a function/class/symbol name, a grep-style
    literal search -- `tg find`'s own "literal" and "identifier3" golden slices,
    `benchmarks/datasets/literal_golden.jsonl` + `identifier3_golden.jsonl`) instead routes to 1.0
    (the un-boosted 1:1 fusion) NO MATTER what the env var says: the sweep's canary case proved
    BM25 is the stronger leg for a lexical query, so boosting dense would risk regressing it --
    this is the guard that keeps the knob scoped to where it was actually measured to help.

    Whitespace, NOT morphemes (dogfood finding, #191): the classifier gates on whitespace-separated
    word count, never `split_terms(query)` morpheme count. `split_terms` splits snake_case/camelCase
    into morphemes, so a descriptive single-token identifier like `mint_access_token` /
    `getUserName` / `_confine_mcp_path` splits into 3+ morphemes and the old `> 2` floor
    misclassified it as NL -- exactly backwards for a literal-identifier lookup where BM25 is the
    strong leg. A real-repo dogfood on tensor-grep's own src caught the leak (5 of 6 literal
    identifier queries wrongly boosted, only the 2-morpheme `rank_chunks` protected); the
    whitespace gate is the dogfood's own recommended fix.

    KNOWN SCOPE (thinktank rank-lens, 2026-07-16): the whitespace gate is purely structural -- a
    2-word LEXICAL phrase (e.g. `"return None"`, `"TODO fixme"`) is indistinguishable from a
    2-word NL phrase and ALSO receives the adaptive boost. The 1:5 sweep is 100% NL queries and
    the literal/identifier3 golden slices are single-token by construction, so this exact shape is
    UNMEASURED. This is a deliberate non-goal, not an oversight: building a lexical-vs-NL content
    classifier here would repeat the exact mistake the morpheme classifier made (a content-based
    heuristic that silently misclassifies real queries) -- see
    `test_find_dense_weight_default_boosts_two_word_lexical_canary` for the regression-catching
    canary this scope decision needs. `TG_FIND_DENSE_WEIGHT=1.0` remains the escape hatch for a
    precise 2-word literal search; `potion-code-16M` is a CODE-domain embedding model, which makes
    boosting a 2-word code fragment defensible too, not just prose NL.

    Flip-prep NIT 1 (tg_find_review_ledger.md FLIP-PREP): `float("nan")` / `float("inf")` /
    `float("-inf")` all PARSE successfully -- `ValueError` alone never catches them, and `nan` in
    particular compares unequal to everything (including itself), so a downstream `!= 1.0` check
    would treat it as "non-default" too. `math.isfinite` rejects `nan` and both infinities the same
    way the `except ValueError` branch below rejects outright garbage -- both now fall through to
    the SAME adaptive-default resolution as an unset env var (#191 must-fix 2: a typo must not
    silently opt an operator OUT of the improved default), rather than being clamped to the old
    1.0. Either way, a non-finite value never reaches `rank_chunks(dense_weight=...)`:
    `weights=[1.0, nan]` would otherwise build a degenerate list for `reciprocal_rank_fusion`'s
    sort.
    """
    raw = os.environ.get(_FIND_DENSE_WEIGHT_ENV)
    weight = _FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT
    if raw:
        try:
            explicit = float(raw)
        except ValueError:
            explicit = None
        if explicit is not None and math.isfinite(explicit):
            weight = explicit
        # else: malformed or non-finite -- treated exactly like an unset env var (falls through to
        # the adaptive default above), per #191 must-fix 2.

    # A single whitespace-free token (or an empty/whitespace-only query, whose `split()` -> []) is a
    # literal identifier -> stay at the protected 1.0, regardless of the env var's state. Only a
    # genuinely multi-word (space-separated) query is NL and gets the resolved `weight` above.
    # Whitespace, not `split_terms` morphemes -- see the module comment above `_FIND_DENSE_WEIGHT_ENV`
    # for the dogfood finding (#191) this fixes.
    if _find_is_single_token_query(query):
        return _FIND_DENSE_WEIGHT_DEFAULT
    return weight


def _find_is_single_token_query(query: str) -> bool:
    """Shared literal/identifier-query predicate for `tg find`'s two query-adaptive knobs --
    `_find_dense_weight`'s dense_weight (above) AND `_find_combine_mode`'s RRF combine mode
    (below). A single whitespace-free token (or an empty/whitespace-only query, whose ``split()``
    -> ``[]``) is a literal identifier lookup; anything with 2+ whitespace-separated words is NL.
    Whitespace, not `split_terms` morphemes -- see the module comment above
    `_FIND_DENSE_WEIGHT_ENV` for the #191 dogfood finding this rule fixes. Factored out to a single
    canonical definition so the two knobs can never silently disagree on what counts as literal.
    """
    return len(query.split()) <= 1


def _find_combine_mode(query: str) -> Literal["sum", "max"]:
    """Query-adaptive RRF `combine` mode for `tg find`'s `rank_chunks` calls (accuracy-leg
    max-fusion regression fix, Opus-gate finding on PR #717): `combine="max"` (`rank_chunks`'s own
    default, see its docstring) lifts genuinely multi-word/NL queries (ndcg@10 +62.6% on the frozen
    40-query golden set) but REGRESSES single-token literal/identifier lookups -- measured on
    `benchmarks/datasets/literal_golden.jsonl` (10 queries, `dense_weight=1.0` as
    `_find_dense_weight` already protects them at): sum scores a perfect 1.0 ndcg@10 (as it always
    has); max drops to 0.9631, a real -0.0369 regression. Mechanism: a literal query's true answer
    is frequently ranked #1 by BOTH the bm25 leg and the dense leg independently -- `"sum"`'s
    per-leg-agreement bonus is exactly the confirming signal `"max"` discards, letting a
    single-leg-only competitor tie the true answer's now-undiscounted best-single-term score.

    Routes on the EXACT SAME whitespace predicate `_find_dense_weight` already uses
    (`_find_is_single_token_query`) so the two adaptive knobs can never disagree on what counts as
    literal: a single whitespace-free token -> `"sum"` (byte-identical to the pre-max-flip
    behavior, recovering the regression); a genuinely multi-word query -> `"max"` (keeps the NL
    win, including composed with the adaptive `dense_weight=5.0`, which reaches the dense-alone
    ndcg@10 ceiling exactly). This is scoped to `tg find` only -- `rerank_hybrid` (the `tg search
    --semantic` path) never passes `combine`, so it is unaffected and stays on the plain "max"
    default.
    """
    return "sum" if _find_is_single_token_query(query) else "max"


def _find_representative_line(chunk: "Chunk", query_terms: set[str]) -> tuple[int, str]:
    """Pick ONE line within `chunk` to surface as the synthesized `MatchLine` (D2): the line with
    the most `split_terms` overlap with the query, ties broken by the FIRST such line (the `>`
    comparison below never replaces an already-found best on an equal score) -- deterministic
    regardless of repeated content within the chunk.

    Falls back to the chunk's first line (or an empty string for a pathologically empty chunk)
    when the query has no terms at all or the chunk somehow has no lines -- `chunk_file` never
    returns an empty-text chunk in practice, but this stays total rather than raising on it.
    """
    from tensor_grep.core.retrieval_lexical import split_terms

    lines = chunk.text.splitlines()
    if not lines:
        return chunk.start_line, ""

    best_index = 0
    best_overlap = -1
    for index, line_text in enumerate(lines):
        overlap = len(query_terms & set(split_terms(line_text))) if query_terms else 0
        if overlap > best_overlap:
            best_overlap = overlap
            best_index = index

    return chunk.start_line + best_index, lines[best_index]


def _execute_find(
    query: str,
    path: str,
    *,
    limit: int,
    max_repo_files: int,
    max_tokens: int,
    deadline: float | None,
) -> "SearchResult":
    """The `tg find` pipeline: whole-repo walk -> chunk -> BM25 [+ dense] [+ late MaxSim] rank via
    the shared `rank_chunks` core (`core/reranker.py`) -> `--limit` -> token-budget fit -> a
    synthesized `SearchResult` (one representative `MatchLine` per selected chunk, D2).

    Fail-closed matrix (D3, fix-approach council must-fixes C1-C3):
    - dense/late-leg extra absent, model not fetched, or a recoverable degrade -> visible BM25-only
      (or pre-late-stage) fallback: `rank_fallback_reason` set + a `tg:`-prefixed stderr line, exit
      0. Mirrors `_apply_semantic_rerank`'s dense/late scaffold (same `dense_available` /
      `late_available` probes, same construction-time degrades) INCLUDING its query-time F1 catch:
      a `DenseUnavailableError` raised from INSIDE `rank_chunks`'s `DenseIndex.query` (a dim/shape
      mismatch, distinct from the construction path) is caught around the `rank_chunks` call and
      degrades to a BM25-only re-run, so it stays a visible exit-0 degrade rather than escaping as a
      raw traceback (`DenseUnavailableError` subclasses `RuntimeError`, so neither the construction
      guard nor the command boundary would otherwise catch it).
    - a genuine unrecoverable backend fault (`BackendExecutionError` from a corrupt model directory
      or an encode-time crash) is NOT caught here -- it propagates to the command boundary (C1),
      which must catch it and exit 2 with a clean `tg:` message, never a raw traceback.
    - a repo walk capped by `--max-repo-files`, a `--deadline` cutoff (walk or chunk phase), a
      per-file chunk() `RuntimeError`, or the corpus-wide chunk cap all mean the ranked corpus was
      PARTIAL (no regex prefilter narrowed it first, unlike `--semantic`) -- each sets
      `result_incomplete=True` + appends a human-readable `incomplete_reason` (C2); the caller
      exits 2 for these, never the exit-0 BM25-only degrade `--semantic`'s own corpus cap uses.
    - `--limit` and `--max-tokens` are OUTPUT caps on an otherwise-complete ranking (mirrors
      `_scan_incomplete`'s output-cap-stays-0 carve-out, repo_map.py) -- they truncate the
      lowest-ranked matches first and never set `result_incomplete`; at least one match survives
      the token budget even if it alone exceeds `max_tokens` (the "confident false zero" floor
      `_apply_context_token_budget` documents at repo_map.py:8221).
    """
    from tensor_grep.cli.repo_map import (
        _deadline_monotonic_from_seconds,
        _DeadlineBreakFlag,
        _iter_repo_files,
        _UnreadablePathFlag,
    )
    from tensor_grep.cli.repo_map import _estimate_tokens as _repo_map_estimate_tokens
    from tensor_grep.core.reranker import rank_chunks
    from tensor_grep.core.result import MatchLine, SearchResult
    from tensor_grep.core.retrieval_bm25 import Bm25Index
    from tensor_grep.core.retrieval_chunker import Chunk, chunk_file
    from tensor_grep.core.retrieval_dense import (
        DenseIndex,
        DenseUnavailableError,
        default_model_dir,
        dense_available,
        load_dense_model,
    )
    from tensor_grep.core.retrieval_lexical import split_terms

    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")

    result = SearchResult()
    incomplete_reasons: list[str] = []
    # Task #276 slice 1 (+ #284): `tg find` can accumulate MULTIPLE heterogeneous incomplete
    # causes -- walk deadline, --max-repo-files cap, an UNREADABLE subtree, chunking deadline,
    # chunk-count cap, and a per-file parse/read error that doesn't map onto the closed
    # vocabulary at all -- into one `incomplete_reasons` list. `incomplete_reason_class` is a
    # single field, so first-cause-wins (mirrors `SearchResult`'s own merge convention):
    # classify whichever of the causes below actually fits the closed vocabulary, and leave it
    # `None` (never emitted -- see `json_fmt._routing_envelope`) if only an unclassifiable
    # per-file error occurred.
    #
    # This list is NOT a completeness guarantee. Read it as "the causes wired so far", never as
    # "the causes that exist" -- #284 was exactly this defect: the unreadable-path cause was
    # missing from BOTH the code and this comment, so a reader who saw the other causes handled
    # correctly would reasonably infer coverage that did not exist. If you add a cause, add it
    # here too; if you find one that is NOT here, it is unwired, not impossible.
    incomplete_reason_class: str | None = None

    deadline_monotonic = _deadline_monotonic_from_seconds(deadline)
    walk_deadline_hit = _DeadlineBreakFlag()
    walk_unreadable_hit = _UnreadablePathFlag()
    all_files = _iter_repo_files(
        root,
        max_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=walk_deadline_hit,
        unreadable_hit=walk_unreadable_hit,
    )

    if walk_deadline_hit.hit:
        reason = (
            f"repo walk exceeded the {deadline:g}s deadline after {len(all_files)} files -- "
            "ranking covers a partial corpus"
        )
        incomplete_reasons.append(reason)
        incomplete_reason_class = "deadline"
        sys.stderr.write(f"tg: {reason}\n")
    elif len(all_files) >= max_repo_files:
        reason = (
            f"repo walk capped at --max-repo-files={max_repo_files}; the repo may hold more "
            "files -- raise --max-repo-files to widen coverage"
        )
        incomplete_reasons.append(reason)
        incomplete_reason_class = "scan_limit"
        sys.stderr.write(f"tg: {reason}\n")

    if walk_unreadable_hit.hit:
        # Task #284: an unreadable subtree is an INDEPENDENT cause, not an alternative to the two
        # above -- it can co-occur with either, so this is a separate `if`, never an `elif`. It is
        # also the one cause in this block that is NOT budget-remediable, which is exactly why it
        # must be reported even when a budget cause already claimed
        # `incomplete_reason_class`: otherwise the reader gets WRONG-KNOB advice ("raise
        # --max-repo-files") with no hint that a bigger budget cannot make those paths readable.
        # Same reasoning, and the same shape, as the entry-cap clarifier in `search_command`.
        sample = ", ".join(walk_unreadable_hit.sample) or "an unreadable path"
        reason = (
            f"repo walk skipped {walk_unreadable_hit.count} unreadable path(s) (e.g. {sample}) -- "
            "ranking covers a partial corpus. More budget will not fix this: the path(s) need to "
            "become readable, or scope the search away from them"
        )
        incomplete_reasons.append(reason)
        # First-cause-wins, matching this function's documented convention above: only claim the
        # single `incomplete_reason_class` field if a budget cause has not already taken it. The
        # human-readable `incomplete_reasons` list carries the full picture either way.
        if incomplete_reason_class is None:
            incomplete_reason_class = "unreadable_path"
        sys.stderr.write(f"tg: {reason}\n")

    # C2: chunk with a per-file RuntimeError guard + a corpus-wide cap, mirroring
    # `_apply_semantic_rerank`'s chunk-building loop (main.py:3886-3927) in SHAPE only -- the
    # ACTION on trip deliberately differs (see the docstring above): note partial coverage and
    # keep going / stop, never force a bm25-only retry the way the regex-prefiltered `--semantic`
    # path does.
    chunks: list[Chunk] = []
    chunked_file_count = 0
    for file_path in all_files:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            reason = (
                f"chunking exceeded the {deadline:g}s deadline after {chunked_file_count} of "
                f"{len(all_files)} files -- ranking covers a partial corpus"
            )
            incomplete_reasons.append(reason)
            if incomplete_reason_class is None:
                incomplete_reason_class = "deadline"
            sys.stderr.write(f"tg: {reason}\n")
            break
        try:
            file_chunks = chunk_file(str(file_path))
        except RuntimeError as exc:
            # A per-file chunk/parse failure doesn't cleanly map onto the closed vocabulary
            # (it could be a read error, a decode error, or a genuine parser bug) -- leave
            # `incomplete_reason_class` unset (not overwritten) if this is the only cause, so
            # the field stays absent rather than guessed.
            reason = f"skipped {file_path}: {exc}"
            incomplete_reasons.append(reason)
            sys.stderr.write(f"tg: {reason}\n")
            continue
        chunks.extend(file_chunks)
        chunked_file_count += 1
        if len(chunks) > _FIND_CORPUS_CHUNK_CAP:
            reason = (
                f"find corpus chunk cap ({_FIND_CORPUS_CHUNK_CAP}) reached over "
                f"{chunked_file_count} of {len(all_files)} files -- ranking covers a partial corpus"
            )
            incomplete_reasons.append(reason)
            if incomplete_reason_class is None:
                incomplete_reason_class = "scan_limit"
            sys.stderr.write(f"tg: {reason}\n")
            break

    if incomplete_reasons:
        result.result_incomplete = True
        result.incomplete_reason = "; ".join(incomplete_reasons)
        result.incomplete_reason_class = incomplete_reason_class

    if not chunks:
        return result

    bm25_index = Bm25Index(chunks)

    dense_index = None
    available, unavailable_reason = dense_available()
    if not available:
        result.rank_fallback_reason = unavailable_reason
        sys.stderr.write(f"tg: {unavailable_reason}\n")
    else:
        try:
            model = load_dense_model(default_model_dir())
            dense_index = DenseIndex(chunks, model)
        except DenseUnavailableError as exc:
            message = _friendly_dense_unavailable_message(exc)
            result.rank_fallback_reason = message
            sys.stderr.write(f"tg: {message}\n")
        # BackendExecutionError (e.g. a corrupt model directory) deliberately propagates -- the
        # command boundary (C1) must catch it and exit 2, never degrade here.

    # Name WHAT RAN: both fields are `required`/minLength-1 in the envelope `tg find` reuses and
    # were emitted null. `rank_fallback_reason` says WHY the dense leg is absent; these say which.
    result.routing_backend = "HybridFindBackend" if dense_index else "Bm25FindBackend"
    result.routing_reason = "find_bm25_dense_rrf" if dense_index else "find_bm25_only"

    late_reranker = None
    if os.environ.get("TG_LATE_RERANK") == "1":
        from tensor_grep.core.retrieval_late import (
            LateRerankUnavailableError,
            late_available,
            load_late_reranker,
        )

        late_ok, late_reason = late_available()
        if not late_ok:
            _note_late_rerank_degraded(result, late_reason or "late rerank unavailable")
        else:
            try:
                late_reranker = load_late_reranker()
            except LateRerankUnavailableError as exc:
                _note_late_rerank_degraded(result, str(exc))
            # BackendExecutionError deliberately propagates -- mirrors the dense leg immediately
            # above and `_apply_semantic_rerank`'s identical contract.

    try:
        fused_order, late_fallback_reason = rank_chunks(
            query,
            chunks,
            bm25_index=bm25_index,
            dense_index=dense_index,
            late_reranker=late_reranker,
            dense_weight=_find_dense_weight(query),
            combine=_find_combine_mode(query),
        )
    except DenseUnavailableError as exc:
        # F1 (Opus-gate blocker; mirrors `_apply_semantic_rerank`'s own query-time catch,
        # main.py:3970-3984): a dense fault raised at QUERY time from INSIDE `rank_chunks`'s call to
        # `DenseIndex.query` (e.g. a dim/shape mismatch) is NOT the DenseIndex CONSTRUCTION path
        # guarded above. `DenseUnavailableError` subclasses `RuntimeError`, so without this it would
        # escape both here AND the command boundary (which catches only FileNotFoundError /
        # BackendExecutionError) as a raw traceback + exit 1 -- a Backend Fail-Closed Contract
        # violation. Degrade VISIBLY to BM25-only (reuse the SAME bm25_index -- no second chunk
        # pass) and re-run; this also bypasses the late stage, which is only ever wired into the
        # primary call above and fed off the (now-dropped) dense fusion.
        degrade_reason = str(exc)
        result.rank_fallback_reason = (
            f"{result.rank_fallback_reason}; {degrade_reason}"
            if result.rank_fallback_reason
            else degrade_reason
        )
        sys.stderr.write(f"tg: {exc}\n")
        fused_order, late_fallback_reason = rank_chunks(
            query,
            chunks,
            bm25_index=bm25_index,
            dense_index=None,
            late_reranker=None,
            dense_weight=_find_dense_weight(query),
            combine=_find_combine_mode(query),
        )
    if late_fallback_reason:
        result.rank_fallback_reason = (
            f"{result.rank_fallback_reason}; {late_fallback_reason}"
            if result.rank_fallback_reason
            else late_fallback_reason
        )

    selected = fused_order[: max(0, limit)]
    query_terms = set(split_terms(query))
    matches: list[MatchLine] = []
    for chunk_index in selected:
        chunk = chunks[chunk_index]
        line_number, line_text = _find_representative_line(chunk, query_terms)
        matches.append(MatchLine(line_number=line_number, text=line_text, file=chunk.file_path))

    # Output-only budget fit (never touches result_incomplete -- see docstring): truncate the
    # LOWEST-ranked matches first (the tail of the already best-first `matches` list), floored at 1
    # survivor so a real hit is never trimmed down to a "confident false zero".
    if max_tokens > 0 and matches:
        budgeted: list[MatchLine] = []
        running_tokens = 0
        for match in matches:
            match_tokens = _repo_map_estimate_tokens(match.text)
            if budgeted and running_tokens + match_tokens > max_tokens:
                break
            budgeted.append(match)
            running_tokens += match_tokens
        matches = budgeted or matches[:1]

    result.matches = matches
    result.total_matches = len(matches)
    matched_paths = sorted({match.file for match in matches})
    result.matched_file_paths = matched_paths
    result.total_files = len(matched_paths)
    for match in matches:
        result.match_counts_by_file[match.file] = result.match_counts_by_file.get(match.file, 0) + 1

    return result


def _deadline_option(help_text: str) -> Any:
    """The `--deadline` option, declared once.

    This flag was declared **20 separate times** in this file, each an identical 10-line
    `typer.Option(None, "--deadline", min=0.1, help=...)` differing only in its help string --
    204 lines of pure duplication in a module the file-size ratchet forbids from growing. That
    duplication is what made adding the flag to a 21st command (`blast-radius-render`, PR #1102)
    impossible: the ratchet was correctly refusing a file whose bulk was copy-paste, and the
    honest fix was to delete the duplication rather than shave the new declaration until it fit.

    `help_text` is a REQUIRED parameter, not a shared default. Each command's wording is
    user-facing and pinned by contract tests; collapsing 12 distinct explanations into one generic
    sentence would be a behaviour change wearing a refactor's name. The duplication worth removing
    was the option MACHINERY -- the wording is not duplication, it is content.

    Keeping `min=0.1` here makes the floor uniform by construction instead of by 20 people
    remembering it.
    """
    return typer.Option(None, "--deadline", min=0.1, help=help_text)


@app.command()
def find(
    query: str = typer.Argument(..., help="Natural-language or keyword query to search for."),
    path: str = typer.Argument(".", help="Root directory (or single file) to search."),
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum ranked chunks to return."),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before ranking.",
    ),
    max_tokens: int = typer.Option(
        4000,
        "--max-tokens",
        min=0,
        help=(
            "Bound the result set to ~N tokens, dropping the lowest-ranked matches first "
            "(0 = unbounded)."
        ),
    ),
    deadline: float | None = _deadline_option(
        "Stop the repo walk/chunk phase after N seconds and return ranked results over the partial corpus scanned so far (result_incomplete=true, exit 2) instead of running unbounded."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    ndjson: bool = typer.Option(
        False, "--ndjson", help="Emit newline-delimited JSON, one object per match."
    ),
) -> None:
    """EXPERIMENTAL: whole-repo hybrid semantic search (BM25 + local CPU dense-embedding
    relevance, RRF-fused), ranked file:line results.

    MAXSIM LATE RERANK IS NOT REACHABLE BY A DOCUMENTED PATH, and this docstring used to advertise
    it as though it were ("[+ optional MaxSim late rerank]"). Stating the honest version instead,
    because a capability the artifact claims and no install path reaches is the same class of
    dishonesty as a stamped-but-unpublished version:

      * it needs the `rerank` extra, NOT the `semantic` extra that `tg install-dense` installs;
      * its model is fetched by `python -m tensor_grep.core.retrieval_late --fetch`, which no `tg`
        command invokes;
      * its only control is the undocumented env var `TG_LATE_RERANK=1` -- there is no flag;
      * and the stage is deliberately HELD as measurably regressing on the retrieval-quality
        benchmark (`docs/BACKLOG.md`), so this is a hold, not an oversight.

    Do not re-add it to the advertised feature list without an install path a user can follow and
    a benchmark result that justifies the stage. See task #15.

    Unlike `tg search --rank`/`--semantic` (which re-rank an EXISTING regex match set), `tg find`
    walks and ranks the WHOLE repo -- no pattern pre-filter, so it can surface content a
    vocabulary-mismatched regex would miss. No API key, no GPU. The dense leg requires the
    `semantic` extra and a fetched model; falls back to BM25-only (visibly, never silently) when
    either is missing -- a BM25-only `tg find` is still a fully supported mode. Bounded by default
    (`--max-repo-files`, `--deadline`, an internal corpus-wide chunk cap): a truncated scan is
    marked `result_incomplete` and exits 2 rather than silently reporting a partial repo as
    complete. Does not offer `--format rg` -- this is not a grep-parity surface.
    """
    from tensor_grep.backends.base import BackendExecutionError

    try:
        result = _execute_find(
            query,
            path,
            limit=limit,
            max_repo_files=max_repo_files,
            max_tokens=max_tokens,
            deadline=deadline,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except BackendExecutionError as exc:
        # C1: mirror `search`'s command-boundary catch (main.py ~7528-7537) -- `_execute_find`
        # deliberately does not catch this (see its docstring); a corrupt model / encode-time
        # fault must exit cleanly here, never a raw traceback.
        if json_output:
            _emit_search_error_json("find_backend_error", str(exc))
        else:
            typer.echo(f"tg: {exc}", err=True)
        sys.exit(2)

    if result.is_empty:
        if json_output:
            from tensor_grep.cli.formatters.json_fmt import JsonFormatter

            _safe_stdout_line(JsonFormatter().format(result))
        # C3: replicate search's hand-written exit block (main.py ~7643-7679) -- the SearchResult
        # envelope buys the JSON fields, not the exit codes.
        sys.exit(2 if result.result_incomplete else 1)

    formatter: OutputFormatter
    if ndjson:
        from tensor_grep.cli.formatters.json_fmt import NdjsonFormatter

        formatter = NdjsonFormatter()
    elif json_output:
        from tensor_grep.cli.formatters.json_fmt import JsonFormatter

        formatter = JsonFormatter()
    else:
        from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
        from tensor_grep.core.config import SearchConfig

        # `with_filename=True` unconditionally: unlike `search` (whose matches are usually already
        # scoped to a query'd area), `find` ranks across the whole repo, so the file is always
        # relevant context even when every top match happens to land in one file.
        formatter = RipgrepFormatter(config=SearchConfig(with_filename=True))

    _safe_stdout_line(formatter.format(result))
    if result.result_incomplete:
        sys.exit(2)


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


def _engine_is_explicit_pcre2(config: "SearchConfig") -> bool:
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


def _eligible_for_pcre2_inline_flag_fallback(config: "SearchConfig") -> bool:
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


def _validate_search_regex(pattern: str, config: "SearchConfig") -> None:
    if config.fixed_strings or _engine_is_explicit_pcre2(config):
        return

    flags = 0
    if config.ignore_case or (config.smart_case and pattern.islower()):
        flags |= re.IGNORECASE

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


def _config_with_guarded_broad_root_globs(config: "SearchConfig") -> "SearchConfig":
    existing_globs = list(config.glob or [])
    for glob in _GUARDED_BROAD_ROOT_RG_GLOBS:
        if glob not in existing_globs:
            existing_globs.append(glob)
    return replace(config, glob=existing_globs)


def _has_generated_scan_bound(config: "SearchConfig") -> bool:
    return bool(
        config.max_depth is not None
        or config.glob
        or config.iglob
        or config.file_type
        or config.type_not
    )


# Bug #88 (dogfood v1.54.0): `_has_generated_scan_bound` above answers "does this query have
# ANY scope-narrowing flag" -- correct for `_should_refuse_unbounded_generated_scan` (its own
# purpose: is a `--no-ignore` scan of a generated dir intentional), but WRONG when reused as
# the escape hatch for the workspace-root / vendored-root / large-root-ceiling guards below.
# `--glob`/`--iglob`/`--type`/`--type-not` only filter WHICH already-encountered files count
# as candidates -- they do not reduce how much of the tree must be walked to find them, unlike
# `--max-depth`, which genuinely bounds the walk itself. Treating a bare `--glob` as "already
# bounded" let a `tg search --glob X PATTERN` with NO explicit PATH auto-scope to an entire
# workspace/vendored/oversized root with all three refusal guards silently disabled -- exactly
# the shape reported in bug #88. The fix: `--glob`/`--iglob`/`--type`/`--type-not` remain a
# valid escape hatch ONLY when the caller also typed an explicit PATH (a deliberate, scoped
# root deliberately narrowed further by a file filter); when PATH was left to default, only
# `--max-depth` (or `--allow-broad-generated-scan`) may bypass these three guards.
def _has_walk_scope_bound(config: "SearchConfig", *, paths_defaulted: bool) -> bool:
    if config.max_depth is not None:
        return True
    if paths_defaulted:
        return False
    return bool(config.glob or config.iglob or config.file_type or config.type_not)


def _path_has_project_marker(path: Path) -> bool:
    for marker in _BROAD_WORKSPACE_PROJECT_MARKERS:
        try:
            if (path / marker).exists():
                return True
        except OSError:
            continue
    return False


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
    config: "SearchConfig",
    *,
    allow_broad_generated_scan: bool,
    paths_defaulted: bool,
) -> tuple[bool, list[str]]:
    if allow_broad_generated_scan or _has_walk_scope_bound(config, paths_defaulted=paths_defaulted):
        return False, []
    project_dirs = _workspace_project_child_names(paths)
    return bool(project_dirs), project_dirs


# Critical unscoped-search-hang fix C: heavy vendored/index directories that can sit at the
# TOP LEVEL of a single project root -- a root `_workspace_project_child_names` never flags
# on their account because that guard only fires on independently-MARKED children (a
# `.git`/`pyproject.toml`/etc. of their own), and a single huge vendored repo's own
# `node_modules`/`external_repos`/etc. is not itself marked that way (item #154 raised the
# marked-root threshold from a flat skip to >= 8 marked children, but a bare vendored dir still
# never counts as one). That single huge vendored repo always slips past that guard.
# Deliberately EXCLUDES tg's own index/reference dirs (`.tensor-grep`, `_tg_refs`,
# `.tg_semantic_index`): those are already (a) skipped by repo_map's walk (Fix A), (b)
# normally `.gitignore`d so DirectoryScanner's default walk never descends into them, and
# (c) bounded by Fix B's wall-clock deadline if they ever are walked. Including them here
# was verified (real dogfood run) to make this guard refuse EVERY unscoped default-path
# search from tensor-grep's own repo root -- a `.tensor-grep/` cache dir is a completely
# normal thing for any tg-managed repo to have, not a "genuinely pathological root".
# Review finding H1 (2026-07-05): also EXCLUDES any dir already walker-skipped by
# `DirectoryScanner`'s `_GENERATED_DIR_NAMES` (currently just `node_modules` of the four
# above) -- the native walker already hard-skips it, and `rg` respects `.gitignore` (where
# `node_modules` almost always lives) plus Fix B's per-file deadline, so that dir was
# ALREADY bounded and this refusal was a pure false positive that exit-2'd every ordinary
# Node/React repo's unscoped search. Imported (not hardcoded) from `io/directory_scanner.py`
# so this set and `cli/bootstrap.py`'s front-door mirror can never drift out of sync.
_UNBOUNDED_VENDORED_ROOT_DIR_NAMES = UNBOUNDED_VENDORED_ROOT_DIR_NAMES


def _root_top_level_vendored_dir_names(paths: list[str]) -> list[str]:
    """O(top-level-entries) probe: never walks -- only `Path.iterdir()` one level deep."""
    found: set[str] = set()
    vendored_names = {name.lower() for name in _UNBOUNDED_VENDORED_ROOT_DIR_NAMES}
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = Path(raw_path)
        try:
            if not path.is_dir():
                continue
            for child in path.iterdir():
                if child.is_dir() and child.name.lower() in vendored_names:
                    found.add(child.name)
        except OSError:
            continue
    return sorted(found, key=lambda item: item.lower())


def _should_refuse_unbounded_vendored_root_scan(
    paths: list[str],
    config: "SearchConfig",
    *,
    allow_broad_generated_scan: bool,
    paths_defaulted: bool,
) -> tuple[bool, list[str]]:
    if allow_broad_generated_scan or _has_walk_scope_bound(config, paths_defaulted=paths_defaulted):
        return False, []
    vendored_dirs = _root_top_level_vendored_dir_names(paths)
    return bool(vendored_dirs), vendored_dirs


def _should_refuse_unbounded_generated_scan(
    paths: list[str],
    config: "SearchConfig",
    *,
    allow_broad_generated_scan: bool,
    files_mode: bool,
) -> tuple[bool, list[str]]:
    if allow_broad_generated_scan or _has_generated_scan_bound(config):
        return False, []
    if not (
        (files_mode and config.hidden)
        or config.no_ignore
        or config.no_ignore_files
        or config.no_ignore_vcs
        or config.unrestricted > 0
    ):
        return False, []
    from tensor_grep.cli.scan_guardrails import generated_scan_dir_names

    generated_dirs = generated_scan_dir_names(
        paths, _BROAD_GENERATED_SCAN_DIR_NAMES, include_child_dirs=files_mode
    )
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


# F6: an unscoped `tg search` on a large SINGLE-project, non-vendored root matches NEITHER
# the workspace guard above (needs >=3 sibling project dirs) NOR the vendored-root guard
# (needs a top-level vendored dir name) -- it falls through both. When the Pipeline then
# selects anything other than `RipgrepBackend` (the one branch that hands ALL candidates to
# a single native call), the per-file Python loop a few lines below has no bound other than
# the wall-clock deadline (Fix B, `cli/main.py`'s native-walk-deadline check) -- so a big
# candidate set grinds through that full deadline instead of failing fast (dogfood v1.42.0).
#
# This guard is checked using the candidate count the real search ALREADY collected (never
# a second scan of its own -- that would just be the unbounded work this guard exists to
# avoid), and fires BEFORE the slow per-file loop starts.
#
# Bug #88 (dogfood v1.54.0): this ceiling is evaluated on the ACTUAL post-filter candidate
# count, so a `--glob`/`--type`/`--iglob` filter is already fully reflected in
# `candidate_file_count` -- it never needs its own bypass here (unlike the workspace/vendored
# guards, which are cheap top-level probes that never see the real count). Bypassing on
# `--glob` alone (the pre-fix `_has_generated_scan_bound` check) defeated this guard for
# exactly the bare-`--glob`-no-PATH shape it exists to catch; see `_has_walk_scope_bound`.
#
# Item #105-parity: imported (not hardcoded) from `io/directory_scanner.py` so this ceiling and
# `cli/bootstrap.py`'s front-door mirror `_search_paths_include_oversized_implicit_root` can
# never drift out of sync -- the same single-source-of-truth pattern already used above for
# `UNBOUNDED_VENDORED_ROOT_DIR_NAMES` / `BROAD_WORKSPACE_PROJECT_MARKERS`.
_LARGE_ROOT_SCAN_FILE_CEILING = IMPLICIT_SEARCH_WALK_FILE_CEILING


def _should_refuse_unbounded_large_root_scan(
    candidate_file_count: int,
    config: "SearchConfig",
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


# Bug #88 (dogfood v1.54.1 re-harvest): the native-binary front door's implicit-`--glob`-no-PATH
# WALK guard (`implicit_search_walk_exceeds_ceiling`, rust_core/src/main.rs) needs a Python-CLI
# mirror, because the full CLI reaches this bug through a DIFFERENT door: `--glob` is a
# `_TG_ONLY_SEARCH_FLAG`, so `cli/bootstrap.py`'s launcher routes a bare `tg search --glob X
# PATTERN` to `_run_full_cli()`, which then hands the whole implicit-`.` walk to the rg
# passthrough (`RipgrepBackend.search_passthrough`) BEFORE `_should_refuse_unbounded_large_root_scan`
# (that guard only runs on the slow per-file Python loop, never on the rg-passthrough fast path).
# On a large single-project root whose top level carries a project marker (e.g. a workspace dir
# with a `package.json`), the workspace-root guard SKIPS it and the vendored-root guard finds no
# top-level vendored dir, so the search sailed straight into an unbounded rg walk (dogfood repro:
# `tg search "function" --glob "*"` on `C:/dev/projects` streamed 487k lines past 60s).
#
# Like the native probe this counts files the walker VISITS -- NOT post-glob matches: a file glob
# does not prune the walk, so a SELECTIVE glob (`*.rs` in a huge JS tree) would sail under a
# match-count ceiling yet still force the full unbounded walk. The glob/type filters are stripped
# from the probe config so `DirectoryScanner.walk` yields every walked file; `--max-depth` /
# ignore / hidden are kept because they genuinely bound how much of the tree is walked. The pull
# is bounded to `ceiling + 1` files (never a full-tree enumeration).
def _implicit_glob_search_walk_exceeds_ceiling(
    paths: list[str],
    config: "SearchConfig",
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


def _can_passthrough_rg(
    config: "SearchConfig",
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
        and not only_matching
        and not (rg_json_passthrough and stats_mode)
        and not (rg_json_passthrough and (config.count or config.count_matches))
        and not (rg_json_passthrough and (files_with_matches or files_without_match))
        and not (rg_json_passthrough and config.replace_str is not None)
        and not (rg_json_passthrough and config.passthru)
        and not (files_with_matches and (config.count or config.count_matches))
    )


def _explicit_rg_format_requested(
    argv: list[str] | None = None,
    *,
    format_value: str | None = None,
) -> bool:
    del format_value
    tokens = list(sys.argv[1:] if argv is None else argv)
    if argv is None:
        if not tokens or tokens[0] != "search":
            return False
        tokens = tokens[1:]
    elif tokens and tokens[0] == "search":
        tokens = tokens[1:]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--format":
            index += 1
            return index < len(tokens) and tokens[index] == "rg"
        if token.startswith("--format="):
            return token.split("=", 1)[1] == "rg"
        index += 1
    return False


# Render-only ripgrep flags that shape *text* output. The tensor-grep aggregate
# `--json` object has no place to put them, so `_build_cmd` silently drops them when
# `json_mode` is set. Worse, the front-door launcher can respawn the search child in
# text-render mode while expecting JSON, spawning an rg child whose pipe is never
# drained -> deadlock (audit C3). Detect them up front and fail fast with a structured
# error instead of either silently ignoring the user's intent or risking the hang.
# Maps the user-facing flag spelling -> the SearchConfig attribute that records it.
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
    candidates = [_self.resolve_native_tg_binary(), _self.resolve_ripgrep_binary()]
    last_completed: subprocess.CompletedProcess[str] | None = None
    for candidate in candidates:
        if not candidate or not candidate.exists():
            continue
        completed = subprocess.run([str(candidate), flag], capture_output=True, text=True)
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


def _replace_lines(
    matches: list["MatchLine"], pattern: str, config: "SearchConfig"
) -> list["MatchLine"]:
    if config.replace_str is None:
        return matches

    flags = 0
    if config.ignore_case or (config.smart_case and pattern.islower()):
        flags |= re.IGNORECASE

    if config.fixed_strings:
        regex = re.compile(re.escape(pattern), flags)
    elif config.line_regexp:
        regex = re.compile(f"^{pattern}$", flags)
    elif config.word_regexp:
        regex = re.compile(rf"\b{pattern}\b", flags)
    else:
        regex = re.compile(pattern, flags)

    extracted: list[MatchLine] = []
    for match in matches:
        replacement = config.replace_str
        if config.fixed_strings and "$" not in replacement:
            flags_val = flags
            if flags_val & re.IGNORECASE:
                new_text = re.sub(
                    re.escape(pattern),
                    replacement.replace("\\", r"\\"),
                    match.text,
                    flags=re.IGNORECASE,
                )
            else:
                new_text = match.text.replace(pattern, replacement)
            extracted.append(replace(match, text=new_text))
            continue
        if regex is not None:

            def _expand_match(current: re.Match[str], replacement: str = replacement) -> str:
                return _expand_ripgrep_replacement(replacement, current)

            new_text = regex.sub(
                _expand_match,
                match.text,
            )
        else:
            new_text = match.text
        extracted.append(replace(match, text=new_text))
    return extracted


def _expand_ripgrep_replacement(template: str, match: re.Match[str]) -> str:
    def _is_ascii_digit(char: str) -> bool:
        return "0" <= char <= "9"

    def _is_ascii_ref_char(char: str) -> bool:
        return char == "_" or ("0" <= char <= "9") or ("A" <= char <= "Z") or ("a" <= char <= "z")

    def _resolve_token(token: str) -> str:
        if not token:
            return ""
        try:
            if all(_is_ascii_digit(char) for char in token):
                group_value = match.group(int(token))
            else:
                group_value = match.group(token)
        except Exception:
            return ""
        return "" if group_value is None else str(group_value)

    result: list[str] = []
    index = 0
    while index < len(template):
        char = template[index]
        if char != "$" or index + 1 >= len(template):
            result.append(char)
            index += 1
            continue

        next_char = template[index + 1]
        if next_char == "$":
            result.append("$")
            index += 2
            continue

        if next_char == "{":
            end_index = template.find("}", index + 2)
            if end_index != -1:
                result.append(_resolve_token(template[index + 2 : end_index]))
                index = end_index + 1
                continue

        if _is_ascii_ref_char(next_char):
            end_index = index + 2
            while end_index < len(template) and _is_ascii_ref_char(template[end_index]):
                end_index += 1
            result.append(_resolve_token(template[index + 1 : end_index]))
            index = end_index
            continue

        result.append("$")
        index += 1

    return "".join(result)


def _only_matching_lines(
    matches: list["MatchLine"], pattern: str, config: "SearchConfig"
) -> list["MatchLine"]:
    flags = 0
    if config.ignore_case or (config.smart_case and pattern.islower()):
        flags |= re.IGNORECASE

    if config.fixed_strings:
        regex = re.compile(re.escape(pattern), flags)
    elif config.line_regexp:
        regex = re.compile(f"^{pattern}$", flags)
    elif config.word_regexp:
        regex = re.compile(rf"\b{pattern}\b", flags)
    else:
        regex = re.compile(pattern, flags)

    extracted: list[MatchLine] = []
    for match in matches:
        for token in regex.findall(match.text):
            if isinstance(token, tuple):
                token = "".join(token)
            token_text = str(token)
            if token_text:
                extracted.append(replace(match, text=token_text))
    return extracted


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


@app.command(
    name="search",
    help="""Search files for a regex pattern. GPU routing is experimental and opt-in via --gpu-device-ids; CPU/ripgrep is the default and the current speed baseline.
The stable text-search contract is the validated common rg-compatible subset documented in docs/CONTRACTS.md.
Use --format rg --json when a tool needs ripgrep JSON Lines events; plain --json is tensor-grep aggregate JSON.

**Other Available Subcommands:**
- `tg calibrate`: Measure CPU vs GPU crossover thresholds
- `tg devices`: Print routable GPU device IDs and VRAM inventory
- `tg mcp`: Start the AI-assistant Model Context Protocol (MCP) server
- `tg classify`: Run log classification with local heuristics by default, or CyBERT when explicitly enabled
- `tg run`: Run a validated AST slice for structural search and guarded rewrites
- `tg scan` / `tg test` / `tg lsp`: Auxiliary AST workflows
- `tg upgrade` / `tg update`: Upgrade tensor-grep in place
""",
)
def search_command(
    # POSITIONAL ARGUMENTS
    positionals: list[str] | None = typer.Argument(
        None,
        help="PATTERN followed by file paths, or just file paths when --files is set.",
    ),
    # INPUT OPTIONS
    regexp: list[str] | None = typer.Option(
        None, "-e", "--regexp", help="A pattern to search for. Can be provided multiple times."
    ),
    file: list[str] | None = typer.Option(
        None,
        "-f",
        "--file",
        help="Search for patterns from the given file, with one pattern per line.",
    ),
    pre: str | None = typer.Option(
        None, "--pre", help="For each input PATH, search standard output of COMMAND PATH."
    ),
    no_pre: bool = typer.Option(False, "--no-pre", help="Disable any configured --pre command."),
    pre_glob: list[str] | None = typer.Option(
        None, "--pre-glob", help="Only run --pre command on files matching this glob."
    ),
    search_zip: bool = typer.Option(
        False, "-z", "--search-zip", help="Search in compressed files (gzip, bzip2, xz, lz4, etc)."
    ),
    no_search_zip: bool = typer.Option(
        False, "--no-search-zip", help="Do not search compressed files."
    ),
    # SEARCH OPTIONS
    case_sensitive: bool = typer.Option(
        False, "-s", "--case-sensitive", help="Execute the search case sensitively."
    ),
    crlf: bool = typer.Option(
        False, "--crlf", help="Treat CRLF as a line terminator instead of just LF."
    ),
    no_crlf: bool = typer.Option(
        False, "--no-crlf", help="Do not treat CRLF specially; useful for config overrides."
    ),
    dfa_size_limit: str | None = typer.Option(
        None, "--dfa-size-limit", help="The upper size limit of the regex DFA."
    ),
    encoding: str = typer.Option(
        "auto", "-E", "--encoding", help="Specify the text encoding (e.g., auto, none, utf-8)."
    ),
    no_encoding: bool = typer.Option(
        False, "--no-encoding", help="Disable configured explicit encoding."
    ),
    engine: str = typer.Option(
        "default", "--engine", help="Regex engine to use: 'default', 'pcre2', or 'auto'."
    ),
    fixed_strings: bool = typer.Option(
        False, "-F", "--fixed-strings", help="Treat all patterns as literals instead of regex."
    ),
    no_fixed_strings: bool = typer.Option(
        False, "--no-fixed-strings", help="Disable fixed-string mode."
    ),
    ignore_case: bool = typer.Option(
        False, "-i", "--ignore-case", help="Search case insensitively."
    ),
    invert_match: bool = typer.Option(
        False, "-v", "--invert-match", help="Invert matching (print lines that don't match)."
    ),
    no_invert_match: bool = typer.Option(
        False, "--no-invert-match", help="Disable inverted matching."
    ),
    line_regexp: bool = typer.Option(
        False, "-x", "--line-regexp", help="Only show matches surrounded by line boundaries."
    ),
    max_count: int | None = typer.Option(
        None, "-m", "--max-count", help="Limit the number of matching lines per file."
    ),
    mmap: bool = typer.Option(
        True, "--mmap", help="Search using memory maps when possible (enabled by default)."
    ),
    no_mmap: bool = typer.Option(False, "--no-mmap", help="Do not use memory maps."),
    multiline: bool = typer.Option(
        False, "-U", "--multiline", help="Enable searching across multiple lines."
    ),
    no_multiline: bool = typer.Option(False, "--no-multiline", help="Disable multiline mode."),
    multiline_dotall: bool = typer.Option(
        False, "--multiline-dotall", help="Enable 'dot all' mode in multiline searches."
    ),
    no_multiline_dotall: bool = typer.Option(
        False, "--no-multiline-dotall", help="Disable multiline dot-all mode."
    ),
    auto_hybrid_regex: bool = typer.Option(
        False,
        "--auto-hybrid-regex",
        help="Use ripgrep's hybrid regex engine selection when rg passthrough is selected.",
    ),
    no_auto_hybrid_regex: bool = typer.Option(
        False,
        "--no-auto-hybrid-regex",
        help="Disable ripgrep's hybrid regex engine selection; useful for config overrides.",
    ),
    unicode: bool = typer.Option(
        False, "--unicode", help="Enable Unicode mode for regex. This is the default."
    ),
    pcre2_unicode: bool = typer.Option(
        False,
        "--pcre2-unicode",
        help="Enable PCRE2 Unicode mode. Alias of --unicode in ripgrep.",
    ),
    no_pcre2_unicode: bool = typer.Option(
        False, "--no-pcre2-unicode", help="Disable PCRE2 Unicode mode."
    ),
    no_unicode: bool = typer.Option(False, "--no-unicode", help="Disable Unicode mode for regex."),
    null_data: bool = typer.Option(
        False, "--null-data", help="Use NUL as a line terminator instead of \\n."
    ),
    pcre2: bool = typer.Option(False, "-P", "--pcre2", help="Use the PCRE2 regex engine."),
    no_pcre2: bool = typer.Option(False, "--no-pcre2", help="Disable PCRE2 regex mode."),
    regex_size_limit: str | None = typer.Option(
        None, "--regex-size-limit", help="Size limit of the compiled regex."
    ),
    smart_case: bool = typer.Option(
        False, "-S", "--smart-case", help="Search case insensitively if pattern is all lowercase."
    ),
    stop_on_nonmatch: bool = typer.Option(
        False,
        "--stop-on-nonmatch",
        help="Stop reading file once a non-matching line is encountered after a match.",
    ),
    text: bool = typer.Option(
        False, "-a", "--text", help="Search binary files as if they were text."
    ),
    no_text: bool = typer.Option(False, "--no-text", help="Do not search binary files as text."),
    threads: int = typer.Option(
        0, "-j", "--threads", help="Approximate number of threads to use (0 = auto)."
    ),
    word_regexp: bool = typer.Option(
        False, "-w", "--word-regexp", help="Only show matches surrounded by word boundaries."
    ),
    # FILTER OPTIONS
    binary: bool = typer.Option(
        False, "--binary", help="Search binary files (don't stop on NUL byte)."
    ),
    no_binary: bool = typer.Option(
        False, "--no-binary", help="Do not search binary files unless --text is set."
    ),
    follow: bool = typer.Option(False, "-L", "--follow", help="Follow symbolic links."),
    no_follow: bool = typer.Option(
        False, "--no-follow", help="Do not follow symbolic links; useful for config overrides."
    ),
    glob: list[str] | None = typer.Option(
        None, "-g", "--glob", help="Include/exclude files matching glob."
    ),
    glob_case_insensitive: bool = typer.Option(
        False, "--glob-case-insensitive", help="Process glob patterns case insensitively."
    ),
    no_glob_case_insensitive: bool = typer.Option(
        False,
        "--no-glob-case-insensitive",
        help="Process glob patterns case sensitively; useful for config overrides.",
    ),
    hidden: bool = typer.Option(
        False, "-.", "--hidden", help="Search hidden files and directories."
    ),
    iglob: list[str] | None = typer.Option(
        None, "--iglob", help="Include/exclude files matching glob (case-insensitive)."
    ),
    ignore_file: list[str] | None = typer.Option(
        None, "--ignore-file", help="Path to gitignore formatted rules file."
    ),
    ignore_file_case_insensitive: bool = typer.Option(
        False, "--ignore-file-case-insensitive", help="Process ignore files case insensitively."
    ),
    no_ignore_file_case_insensitive: bool = typer.Option(
        False,
        "--no-ignore-file-case-insensitive",
        help="Process ignore files case sensitively; useful for config overrides.",
    ),
    max_depth: int | None = typer.Option(
        None, "-d", "--max-depth", "--maxdepth", help="Limit depth of directory traversal."
    ),
    max_filesize: str | None = typer.Option(
        None, "--max-filesize", help="Ignore files larger than this size."
    ),
    no_ignore: bool = typer.Option(
        False, "--no-ignore", help="Don't respect ignore files (.gitignore, .rgignore, etc)."
    ),
    ignore: bool = typer.Option(
        False, "--ignore", help="Respect ignore files; useful for overriding ripgrep config."
    ),
    no_ignore_dot: bool = typer.Option(
        False, "--no-ignore-dot", help="Don't respect .ignore or .rgignore files."
    ),
    ignore_dot: bool = typer.Option(
        False, "--ignore-dot", help="Respect .ignore and .rgignore files."
    ),
    no_ignore_exclude: bool = typer.Option(
        False, "--no-ignore-exclude", help="Don't respect .git/info/exclude."
    ),
    ignore_exclude: bool = typer.Option(
        False, "--ignore-exclude", help="Respect .git/info/exclude."
    ),
    no_ignore_files: bool = typer.Option(
        False, "--no-ignore-files", help="Ignore any --ignore-file flags."
    ),
    ignore_files: bool = typer.Option(False, "--ignore-files", help="Respect --ignore-file flags."),
    no_ignore_global: bool = typer.Option(
        False, "--no-ignore-global", help="Don't respect global gitignore."
    ),
    ignore_global: bool = typer.Option(
        False, "--ignore-global", help="Respect global gitignore files."
    ),
    ignore_messages: bool = typer.Option(
        False, "--ignore-messages", help="Show ignore file parsing errors."
    ),
    no_ignore_parent: bool = typer.Option(
        False, "--no-ignore-parent", help="Don't respect ignore files in parent directories."
    ),
    ignore_parent: bool = typer.Option(
        False, "--ignore-parent", help="Respect ignore files in parent directories."
    ),
    ignore_vcs: bool = typer.Option(
        False, "--ignore-vcs", help="Respect source control ignore files."
    ),
    no_ignore_vcs: bool = typer.Option(
        False, "--no-ignore-vcs", help="Don't respect source control ignore files (.gitignore)."
    ),
    no_require_git: bool = typer.Option(
        False, "--no-require-git", help="Respect .gitignore even outside of git repos."
    ),
    require_git: bool = typer.Option(
        False,
        "--require-git",
        help="Require a git repo before respecting git ignore rules.",
    ),
    no_hidden: bool = typer.Option(
        False, "--no-hidden", help="Do not search hidden files and directories."
    ),
    one_file_system: bool = typer.Option(
        False, "--one-file-system", help="Don't cross file system boundaries."
    ),
    no_one_file_system: bool = typer.Option(
        False, "--no-one-file-system", help="Allow crossing file system boundaries."
    ),
    type: list[str] | None = typer.Option(
        None, "-t", "--type", help="Only search files matching TYPE."
    ),
    type_not: list[str] | None = typer.Option(
        None, "-T", "--type-not", help="Do not search files matching TYPE."
    ),
    type_add: list[str] | None = typer.Option(
        None, "--type-add", help="Add a new glob for a file type."
    ),
    type_clear: str | None = typer.Option(None, "--type-clear", help="Clear globs for TYPE."),
    unrestricted: int = typer.Option(
        0, "-u", "--unrestricted", count=True, help="Reduce smart filtering (repeat up to 3 times)."
    ),
    # OUTPUT OPTIONS
    after_context: int | None = typer.Option(
        None, "-A", "--after-context", help="Show NUM lines after each match."
    ),
    before_context: int | None = typer.Option(
        None, "-B", "--before-context", help="Show NUM lines before each match."
    ),
    block_buffered: bool = typer.Option(False, "--block-buffered", help="Force block buffering."),
    no_block_buffered: bool = typer.Option(
        False, "--no-block-buffered", help="Disable forced block buffering."
    ),
    byte_offset: bool = typer.Option(
        False, "-b", "--byte-offset", help="Print 0-based byte offset before each output line."
    ),
    no_byte_offset: bool = typer.Option(
        False, "--no-byte-offset", help="Do not print byte offsets."
    ),
    color: str = typer.Option(
        "auto", "--color", help="When to use colors: never, auto, always, ansi."
    ),
    colors: list[str] | None = typer.Option(
        None, "--colors", help="Color settings for output (e.g. 'match:fg:magenta')."
    ),
    column: bool = typer.Option(False, "--column", help="Show column numbers (1-based)."),
    no_column: bool = typer.Option(False, "--no-column", help="Do not show column numbers."),
    context: int | None = typer.Option(
        None, "-C", "--context", help="Show NUM lines before and after each match."
    ),
    context_separator: str = typer.Option(
        "--", "--context-separator", help="String used to separate non-contiguous context lines."
    ),
    no_context_separator: bool = typer.Option(
        False, "--no-context-separator", help="Disable explicit context separators."
    ),
    field_context_separator: str = typer.Option(
        "-", "--field-context-separator", help="Set the field context separator."
    ),
    field_match_separator: str = typer.Option(
        ":", "--field-match-separator", help="Set the field match separator."
    ),
    heading: bool = typer.Option(
        True, "--heading", help="Print file path above clusters of matches."
    ),
    hostname_bin: str | None = typer.Option(
        None, "--hostname-bin", help="Executable to determine system hostname."
    ),
    hyperlink_format: str | None = typer.Option(
        None, "--hyperlink-format", help="Format of hyperlinks to use."
    ),
    include_zero: bool = typer.Option(
        False, "--include-zero", help="Print zero match counts with -c."
    ),
    no_include_zero: bool = typer.Option(
        False, "--no-include-zero", help="Do not print zero match counts with -c."
    ),
    line_buffered: bool = typer.Option(False, "--line-buffered", help="Force line buffering."),
    no_line_buffered: bool = typer.Option(
        False, "--no-line-buffered", help="Disable forced line buffering."
    ),
    line_number: bool | None = typer.Option(
        None, "-n", "--line-number", help="Show line numbers (1-based)."
    ),
    no_line_number: bool = typer.Option(
        False, "-N", "--no-line-number", help="Suppress line numbers."
    ),
    max_columns: int | None = typer.Option(
        None, "-M", "--max-columns", help="Omit lines longer than this limit."
    ),
    max_columns_preview: bool = typer.Option(
        False, "--max-columns-preview", help="Preview lines exceeding max column limit."
    ),
    no_max_columns_preview: bool = typer.Option(
        False, "--no-max-columns-preview", help="Do not preview lines exceeding max column limit."
    ),
    null: bool = typer.Option(False, "-0", "--null", help="Follow file paths with a NUL byte."),
    only_matching: bool = typer.Option(
        False, "-o", "--only-matching", help="Print only the matched parts of a line."
    ),
    path_separator: str | None = typer.Option(
        None, "--path-separator", help="Path separator to use."
    ),
    passthru: bool = typer.Option(
        False,
        "--passthru",
        "--passthrough",
        help="Print both matching and non-matching lines.",
    ),
    pretty: bool = typer.Option(
        False, "-p", "--pretty", help="Alias for --color=always --heading --line-number."
    ),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Do not print anything to stdout."),
    replace: str | None = typer.Option(
        None,
        "-r",
        "--replace",
        help="Replace every match with the given text. Supports capture groups (e.g., $1).",
    ),
    sort: str = typer.Option(
        "none", "--sort", help="Sort results (none, path, modified, accessed, created)."
    ),
    sortr: str = typer.Option("none", "--sortr", help="Sort results in reverse order."),
    sort_files: bool = typer.Option(
        False,
        "--sort-files",
        help="Deprecated ripgrep alias for --sort path; disables parallel traversal.",
    ),
    trim: bool = typer.Option(False, "--trim", help="Remove leading ASCII whitespace from output."),
    no_trim: bool = typer.Option(
        False, "--no-trim", help="Do not remove leading ASCII whitespace from output."
    ),
    vimgrep: bool = typer.Option(
        False,
        "--vimgrep",
        help="Print results with every match on its own line (line/column numbers).",
    ),
    with_filename: bool = typer.Option(
        False, "-H", "--with-filename", help="Print file path for each matching line."
    ),
    no_filename: bool = typer.Option(
        False, "-I", "--no-filename", help="Never print the file path."
    ),
    # OUTPUT MODES
    count: bool = typer.Option(
        False, "-c", "--count", help="Show only the number of matching lines per file."
    ),
    count_matches: bool = typer.Option(
        False, "--count-matches", help="Show only the total number of matches per file."
    ),
    files_with_matches: bool = typer.Option(
        False, "-l", "--files-with-matches", help="Print only paths with at least one match."
    ),
    files_without_match: bool = typer.Option(
        False, "--files-without-match", help="Print paths containing zero matches."
    ),
    json: bool = typer.Option(
        False,
        "--json",
        help=(
            "Print results as one tensor-grep aggregate JSON object, not rg JSON Lines. "
            "Use --format rg --json for ripgrep JSON Lines or --ndjson for tensor-grep streaming output."
        ),
    ),
    rank: bool = typer.Option(
        False,
        "--rank",
        "--bm25",
        help=(
            "Re-rank results by BM25 lexical relevance to the query terms instead of grep order "
            "(pure-CPU ranking; no API key, no model download)."
        ),
    ),
    semantic: bool = typer.Option(
        False,
        "--semantic",
        help=(
            "Re-rank results by a hybrid of BM25 + local CPU dense-embedding relevance (RRF "
            "fusion), instead of grep order. No API key, no GPU. Requires the `semantic` extra "
            "and a fetched model; falls back to BM25-only (visibly, never silently) when "
            "either is missing."
        ),
    ),
    no_json: bool = typer.Option(
        False, "--no-json", help="Disable ripgrep JSON Lines when overriding rg config."
    ),
    ndjson: bool = typer.Option(
        False,
        "--ndjson",
        help="Print tensor-grep newline-delimited JSON rows, not the rg event schema.",
    ),
    # LOGGING OPTIONS
    debug: bool = typer.Option(False, "--debug", help="Show debug messages."),
    no_ignore_messages: bool = typer.Option(
        False, "--no-ignore-messages", help="Suppress ignore file parsing errors."
    ),
    no_messages: bool = typer.Option(
        False, "--no-messages", help="Suppress some error messages (like failed file opens)."
    ),
    messages: bool = typer.Option(
        False, "--messages", help="Show normal diagnostic messages; overrides ripgrep config."
    ),
    stats: bool = typer.Option(False, "--stats", help="Print aggregate statistics."),
    no_stats: bool = typer.Option(False, "--no-stats", help="Do not print aggregate statistics."),
    trace: bool = typer.Option(False, "--trace", help="Show exhaustive trace messages."),
    # OTHER BEHAVIORS
    files: bool = typer.Option(
        False, "--files", help="Print files that would be searched and exit."
    ),
    generate: str | None = typer.Option(
        None,
        "--generate",
        help=(
            "Generate shell completion output "
            "(complete-bash, complete-zsh, complete-fish, complete-powershell)."
        ),
    ),
    no_config: bool = typer.Option(False, "--no-config", help="Never read configuration files."),
    pcre2_version: bool = typer.Option(
        False, "--pcre2-version", help="Print PCRE2 version and exit."
    ),
    type_list: bool = typer.Option(
        False, "--type-list", help="Show all supported file types and exit."
    ),
    version: bool = typer.Option(False, "-V", "--version", help="Show tensor-grep version."),
    # TENSOR-GREP SPECIFIC
    cpu: bool = typer.Option(
        False,
        "--cpu",
        "--force-cpu",
        help="Force CPU fallback (tensor-grep specific).",
    ),
    format_type: str = typer.Option(
        "rg",
        "--format",
        help="Output format: rg, json, table, or csv. Use rg for exact ripgrep-style text output.",
    ),
    ast: bool = typer.Option(
        False,
        "--ast",
        help="Parse files into ASTs and search structurally using tree-sitter.",
    ),
    lang: str | None = typer.Option(
        None,
        "--lang",
        help="Explicitly define language grammar for --ast (e.g. python, javascript).",
    ),
    ltl: bool = typer.Option(
        False,
        "--ltl",
        help="Interpret PATTERN as a temporal query (supports: 'A -> eventually B').",
    ),
    gpu_device_ids: str | None = typer.Option(
        None,
        "--gpu-device-ids",
        help="Comma-separated GPU IDs to pin this search request to (e.g. 0,1).",
    ),
    allow_broad_generated_scan: bool = typer.Option(
        False,
        "--allow-broad-generated-scan",
        help=(
            "Permit unbounded file-list/search scans through generated, cache, dependency, "
            "or multi-project workspace roots. Prefer scoped paths, --glob, --type, or "
            "--max-depth for agent runs."
        ),
    ),
    enrich_ast: bool = typer.Option(
        False,
        "--enrich-ast",
        help="Enrich code search matches with the enclosing AST container (function/class) using tree-sitter.",
    ),
) -> None:
    """
    Search files for a regex pattern. GPU routing is experimental and opt-in via --gpu-device-ids; CPU/ripgrep is the default and the current speed baseline.
    The stable text-search contract is the validated rg-compatible surface documented in docs/CONTRACTS.md.
    """
    # Perf (#94 Part-B): lazy-imported here (not at module top) so non-search commands don't pay
    # for backends.base's transitive core.config/core.result chain. search_command is the sole
    # runtime user of BackendExecutionError in this module (except-clauses further below), and
    # this sits before any loop, so it costs exactly one import per invocation, not per iteration.
    from tensor_grep.backends.base import BackendExecutionError

    # Just forward to CPU backend for now as a stub.
    # Note: Full flag wiring will require mapping these dozens of parameters into the Pipeline/Core components.
    args = positionals or []
    pattern = ""
    regexp_patterns = regexp or []
    if generate is not None:
        typer.echo(_generate_shell_completion_script(generator=generate))
        raise typer.Exit(0)
    if version:
        typer.echo(f"tensor-grep {_cli_package_version()}")
        raise typer.Exit(0)
    if pcre2_version:
        _run_rg_compatible_info_action(
            "--pcre2-version",
            "PCRE2 version unavailable: no native tg or ripgrep binary found.",
        )
    if type_list:
        _run_rg_compatible_info_action(
            "--type-list",
            "Type list unavailable: no native tg or ripgrep binary found.",
        )
    if files:
        paths_to_search = args or ["."]
        paths_defaulted = not args
    elif regexp_patterns:
        pattern = regexp_patterns[0]
        if pattern == "":
            _exit_search_error(
                "empty_pattern",
                "PATTERN must not be empty.",
                json_mode=json,
            )
        paths_to_search = args or ["."]
        paths_defaulted = not args
    elif file:
        pattern = ""
        paths_to_search = args or ["."]
        paths_defaulted = not args
    else:
        if not args:
            typer.echo("Error: Please provide a PATTERN to search.", err=True)
            sys.exit(1)
        pattern = args[0]
        if pattern == "":
            _exit_search_error(
                "empty_pattern",
                "PATTERN must not be empty.",
                json_mode=json,
            )
        paths_to_search = args[1:] or ["."]
        paths_defaulted = not args[1:]

    # `-f/--file` (patterns-from-file) and multiple `-e/--regexp` never build a real combined-pattern
    # regex -- `pattern` above is simply "" when the `elif file:` branch above actually ran (bool(file)
    # AND no regexp given, since `elif regexp_patterns:` takes priority over `elif file:` and would make
    # `-f` a dead flag), or regexp_patterns[0] (silently drops the rest) when multiple `-e` were given.
    # -o/-r/--rank/--semantic all operate on that single `pattern` string, so combining them previously
    # either silently returned zero matches (-o against pattern="") or reranked/replaced against the
    # wrong text. The multi-pattern combine feature was scoped OUT (#441 closed); reject the combo up
    # front instead, mirroring the plain-`--json` render-flag guard above (audit #5/#20). Excludes
    # `--files` mode (a distinct, unrelated file-listing path) and a single `-e` alongside an
    # otherwise-dead `-f` (regexp_patterns already wins there, so `pattern` is real).
    multi_pattern_source = not files and (
        (not regexp_patterns and bool(file)) or len(regexp_patterns) > 1
    )
    if multi_pattern_source:
        conflicting_flags = [
            spelling
            for present, spelling in (
                (only_matching, "-o/--only-matching"),
                (replace is not None, "-r/--replace"),
                (rank, "--rank/--bm25"),
                (semantic, "--semantic"),
            )
            if present
        ]
        if conflicting_flags:
            flag_list = " and ".join(conflicting_flags)
            source = "multiple -e/--regexp patterns" if len(regexp_patterns) > 1 else "-f/--file"
            _exit_search_error(
                "unsupported_flag",
                (
                    f"{flag_list} not supported with {source} (no single combined-pattern regex "
                    "is built from them); drop the flag(s), or provide a single -e/--regexp pattern."
                ),
                json_mode=json,
                exit_code=2,
            )

    if not files:
        missing_paths = [
            path for path in paths_to_search if path != "-" and not Path(path).exists()
        ]
        if missing_paths:
            if json:
                detail = "search path does not exist: " + ", ".join(missing_paths)
                _exit_search_error("path_not_found", detail, json_mode=True)
            else:
                for missing_path in missing_paths:
                    typer.echo(
                        f"Error: search path does not exist: {missing_path}",
                        err=True,
                    )
                sys.exit(2)

    # Capture whether the user explicitly chose a line-number mode BEFORE auto-resolving (so native
    # delegation can forward only an explicit -n/-N and leave the auto case to the native binary).
    line_number_explicit = bool(no_line_number) or line_number is True
    if no_line_number:
        line_number = False
    elif line_number is None:
        line_number = sys.stdout.isatty()

    from tensor_grep.core.config import SearchConfig

    parsed_gpu_device_ids = _parse_gpu_device_ids_cli(gpu_device_ids)
    _warn_unavailable_gpu_device_ids(parsed_gpu_device_ids)

    effective_force_cpu = cpu or env_flag_enabled("TG_FORCE_CPU")
    implicit_with_filename = (
        not no_filename
        and not effective_force_cpu
        and not json
        and not ndjson
        and not only_matching
        and not parsed_gpu_device_ids
        and replace is None
        and (
            len(paths_to_search) > 1
            or any(path != "-" and Path(path).is_dir() for path in paths_to_search)
        )
    )

    config = SearchConfig(
        rank_bm25=rank,
        semantic_rank=semantic,
        regexp=regexp,
        file_patterns=file,
        pre=pre,
        no_pre=no_pre,
        pre_glob=pre_glob,
        search_zip=search_zip,
        no_search_zip=no_search_zip,
        case_sensitive=case_sensitive,
        crlf=crlf,
        no_crlf=no_crlf,
        dfa_size_limit=dfa_size_limit,
        encoding=encoding,
        no_encoding=no_encoding,
        engine=engine,
        fixed_strings=fixed_strings,
        no_fixed_strings=no_fixed_strings,
        ignore_case=ignore_case,
        invert_match=invert_match,
        no_invert_match=no_invert_match,
        line_regexp=line_regexp,
        max_count=max_count,
        mmap=mmap,
        no_mmap=no_mmap,
        multiline=multiline,
        no_multiline=no_multiline,
        multiline_dotall=multiline_dotall,
        no_multiline_dotall=no_multiline_dotall,
        auto_hybrid_regex=auto_hybrid_regex,
        no_auto_hybrid_regex=no_auto_hybrid_regex,
        no_unicode=no_unicode,
        unicode=unicode,
        pcre2_unicode=pcre2_unicode,
        no_pcre2_unicode=no_pcre2_unicode,
        null_data=null_data,
        pcre2=pcre2,
        no_pcre2=no_pcre2,
        regex_size_limit=regex_size_limit,
        smart_case=smart_case,
        stop_on_nonmatch=stop_on_nonmatch,
        text=text,
        no_text=no_text,
        threads=threads,
        word_regexp=word_regexp,
        binary=binary,
        no_binary=no_binary,
        follow=follow,
        no_follow=no_follow,
        glob=glob,
        glob_case_insensitive=glob_case_insensitive,
        no_glob_case_insensitive=no_glob_case_insensitive,
        hidden=hidden,
        iglob=iglob,
        ignore_file=ignore_file,
        ignore_file_case_insensitive=ignore_file_case_insensitive,
        no_ignore_file_case_insensitive=no_ignore_file_case_insensitive,
        max_depth=max_depth,
        max_filesize=max_filesize,
        ignore=ignore,
        no_ignore=no_ignore,
        ignore_dot=ignore_dot,
        no_ignore_dot=no_ignore_dot,
        ignore_exclude=ignore_exclude,
        no_ignore_exclude=no_ignore_exclude,
        ignore_files=ignore_files,
        no_ignore_files=no_ignore_files,
        ignore_global=ignore_global,
        no_ignore_global=no_ignore_global,
        ignore_parent=ignore_parent,
        no_ignore_parent=no_ignore_parent,
        ignore_vcs=ignore_vcs,
        no_ignore_vcs=no_ignore_vcs,
        no_require_git=no_require_git,
        require_git=require_git,
        no_hidden=no_hidden,
        one_file_system=one_file_system,
        no_one_file_system=no_one_file_system,
        file_type=type,
        type_not=type_not,
        type_add=type_add,
        type_clear=type_clear,
        unrestricted=unrestricted,
        after_context=after_context,
        before_context=before_context,
        block_buffered=block_buffered,
        no_block_buffered=no_block_buffered,
        byte_offset=byte_offset,
        no_byte_offset=no_byte_offset,
        color=color,
        colors=colors,
        column=column,
        no_column=no_column,
        context=context,
        context_separator=context_separator,
        no_context_separator=no_context_separator,
        field_context_separator=field_context_separator,
        field_match_separator=field_match_separator,
        heading=heading,
        hostname_bin=hostname_bin,
        hyperlink_format=hyperlink_format,
        include_zero=include_zero,
        no_include_zero=no_include_zero,
        line_buffered=line_buffered,
        no_line_buffered=no_line_buffered,
        line_number=line_number,
        line_number_explicit=line_number_explicit,
        max_columns=max_columns,
        max_columns_preview=max_columns_preview,
        no_max_columns_preview=no_max_columns_preview,
        null=null,
        only_matching=only_matching,
        path_separator=path_separator,
        passthru=passthru,
        pretty=pretty,
        quiet=quiet,
        replace_str=replace,
        sort_by=sort,
        sort_by_reverse=sortr,
        sort_files=sort_files,
        trim=trim,
        no_trim=no_trim,
        vimgrep=vimgrep,
        with_filename=with_filename or implicit_with_filename,
        no_filename=no_filename,
        count=count,
        count_matches=count_matches,
        files_with_matches=files_with_matches,
        files_without_match=files_without_match,
        json_mode=json,
        no_json=no_json,
        debug=debug,
        ignore_messages=ignore_messages,
        no_ignore_messages=no_ignore_messages,
        no_messages=no_messages,
        messages=messages,
        stats=stats,
        no_stats=no_stats,
        trace=trace,
        list_files=files,
        generate=generate,
        no_config=no_config,
        pcre2_version=pcre2_version,
        type_list=type_list,
        force_cpu=effective_force_cpu,
        format_type=format_type,
        ast=ast,
        lang=lang,
        ltl=ltl,
        query_pattern=pattern,
        gpu_device_ids=parsed_gpu_device_ids,
    )
    if not files:
        # audit #69 (re-do of #441, this time with a Windows golden from the start):
        # `multi_pattern_source` already excludes -e-and-f-together (a single -e still makes
        # -f a dead flag, pinned by
        # test_search_single_regexp_with_unused_file_option_and_only_matching_still_works)
        # and excludes -o/-r/--rank/--semantic (rejected with exit 2 above), so this is
        # exactly the plain-search shape that used to silently drop every pattern but the
        # first (multiple -e) or never read the file at all (-f alone). The multiple-`-e`
        # sub-case combines EAGERLY here -- no I/O, and the rg-routed passthrough path is
        # untouched by it either way (see the combine step below). The `-f`-alone sub-case is
        # handled LATER, only once the search is confirmed to not be rg-passthrough
        # (deliberately deferred: an eager read here broke
        # test_python_search_treats_file_option_as_pattern_file_not_regex, where real `rg`
        # itself must read the `-f` file on the passthrough path, never tg).
        combined_multi_patterns: list[str] | None = (
            list(regexp_patterns) if multi_pattern_source and len(regexp_patterns) > 1 else None
        )
        try:
            patterns_to_validate = (
                combined_multi_patterns
                if combined_multi_patterns is not None
                else (regexp_patterns if regexp_patterns else [pattern])
            )
            for regex_pattern in patterns_to_validate:
                _validate_search_regex(regex_pattern, config)
        except Exception as exc:
            if _is_invalid_regex_error(exc):
                # M14b: a mid-pattern inline flag group (e.g. `start(?s).*end`) is rejected
                # by the default Rust/`re` engine but accepted by PCRE2. When the user did
                # not explicitly pick a non-PCRE2 engine, retry transparently under PCRE2
                # instead of erroring, and announce the switch on stderr so it is observable.
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
        if combined_multi_patterns is not None:
            # Build one rg-parity OR-alternation and let 100% of the existing
            # single-pattern machinery (CPUBackend, the Rust FFI, native-binary delegation)
            # treat it exactly like a hand-typed `-e "foo|bar"` -- the rg-ROUTED passthrough
            # path is untouched by this (it reads `config.regexp`/`config.file_patterns`
            # directly and builds its own rg argv; see ripgrep_backend.py:788). `-F`
            # multi-literal is `re.escape`'d per branch, so `fixed_strings` must be cleared
            # here or the combined alternation string would be re-literal-matched whole.
            pattern = _combine_multi_patterns(
                combined_multi_patterns, fixed_strings=config.fixed_strings
            )
            config = dataclasses.replace(config, query_pattern=pattern, fixed_strings=False)
    guarded_broad_root = _search_paths_include_guarded_broad_root(paths_to_search)
    explicit_hidden_search_root = not config.hidden and any(
        _path_has_hidden_component(path) for path in paths_to_search
    )
    refuse_generated_scan, generated_scan_dirs = _should_refuse_unbounded_generated_scan(
        paths_to_search,
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        files_mode=files,
    )
    if refuse_generated_scan:
        _emit_broad_scan_refusal(
            _format_broad_generated_scan_error(generated_scan_dirs),
            json_output=json,
            path=str(paths_to_search[0]) if paths_to_search else ".",
        )
        raise typer.Exit(2)
    refuse_workspace_scan, workspace_project_dirs = _should_refuse_unbounded_workspace_root_scan(
        paths_to_search,
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        paths_defaulted=paths_defaulted,
    )
    if refuse_workspace_scan:
        _emit_broad_scan_refusal(
            _format_broad_workspace_scan_error(workspace_project_dirs),
            json_output=json,
            path=str(paths_to_search[0]) if paths_to_search else ".",
            incomplete_reason_class="workspace_root_refused",
            error_code="workspace_root_refused",
        )
        raise typer.Exit(2)
    refuse_vendored_scan, vendored_root_dirs = _should_refuse_unbounded_vendored_root_scan(
        paths_to_search,
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        paths_defaulted=paths_defaulted,
    )
    if refuse_vendored_scan:
        _emit_broad_scan_refusal(
            _format_unbounded_vendored_root_scan_error(vendored_root_dirs),
            json_output=json,
            path=str(paths_to_search[0]) if paths_to_search else ".",
        )
        raise typer.Exit(2)

    # Bug #88 (dogfood v1.54.1 re-harvest): an implicit-path `--glob`/`--type` search that the
    # workspace/vendored guards above did not catch (a large single-project root whose top level
    # carries a project marker, e.g. a workspace dir with a package.json) would otherwise hand the
    # whole unbounded `.` walk to the rg passthrough / native delegation below. Mirror the native
    # binary's WALK-ceiling guard here so the full CLI refuses fast too. Gated on `paths_defaulted`
    # (an explicit, deliberately-scoped PATH still runs uninhibited -- Trap #3); `--max-depth` and
    # `--allow-broad-generated-scan` bypass it (a genuinely bounded walk / an opt-in override).
    #
    # P0-1 (dogfood + external audit 2026-07-11): fire for an unscoped search that carries NO
    # glob/type filter too, not just the glob/type combo. The plain fast-path search is already
    # bounded upstream -- the bootstrap front door delegates it to the native binary (whose own
    # walk-ceiling guard refuses) or to rg passthrough -- so a bare `tg search PATTERN` never
    # reaches this Python guard when a native/rg engine exists. The gap this closes is the
    # FULL-CLI path: a query that carries a TG-only flag (`--rank`/`--semantic`/`--cpu`, ...) is
    # forced to the full CLI where NO fast native/rg engine can serve it, and if no glob/type
    # rode along, the old gate let it fall through to the unbounded per-file Python loop and burn
    # the wall-clock deadline (dogfood-reproduced: `tg search PATTERN --rank` on a >1500-file
    # unscoped root did the full walk instead of refusing). The probe strips glob/type anyway, so
    # it counts every walked file (early-stopping at the ceiling) regardless of the filter flags.
    if (
        paths_defaulted
        and not allow_broad_generated_scan
        and config.max_depth is None
        and _implicit_glob_search_walk_exceeds_ceiling(
            paths_to_search, config, _LARGE_ROOT_SCAN_FILE_CEILING
        )
    ):
        _emit_broad_scan_refusal(
            _format_unbounded_large_root_scan_error(_LARGE_ROOT_SCAN_FILE_CEILING),
            json_output=json,
            path=str(paths_to_search[0]) if paths_to_search else ".",
        )
        raise typer.Exit(2)

    explicit_rg_format = _explicit_rg_format_requested(format_value=format_type)
    # C3: plain `--json` emits one aggregate object and cannot render ripgrep's
    # text-shaping flags. Honoring them is impossible and silently dropping them is a
    # footgun that also lets the front-door launcher spawn an undrained text-render
    # child (-> deadlock). Fail fast and deterministically before any child is spawned.
    if json and not explicit_rg_format:
        # Detect from PARSED typer params (not sys.argv): reading sys.argv mis-fires when
        # the typer app is invoked in-process (e.g. CliRunner under pytest, whose argv
        # carries -p/--pretty-looking flags). The ambiguous-default flags (--heading and
        # the separators) are caught for the real CLI by the bootstrap launcher guard
        # (_json_aggregate_blocks_passthrough), so the secondary net here only needs the
        # unambiguously-set render flags (audit C3).
        incompatible_render_flags = [
            spelling
            for present, spelling in (
                (passthru, "--passthru"),
                (trim, "--trim"),
                (byte_offset, "-b"),
                (max_columns is not None, "-M"),
                (max_columns_preview, "--max-columns-preview"),
                (pretty, "-p"),
            )
            if present
        ]
        if incompatible_render_flags:
            flag_list = ", ".join(incompatible_render_flags)
            _exit_search_error(
                "unsupported_flag",
                (
                    f"flag(s) {flag_list} not supported with plain --json; "
                    "use --format rg --json for ripgrep JSON Lines that carry render "
                    "metadata, or drop the flag(s)."
                ),
                json_mode=True,
                exit_code=2,
            )
    native_tg_binary = _self.resolve_native_tg_binary()
    if (
        native_tg_binary is not None
        and not guarded_broad_root
        and not explicit_hidden_search_root
        and not (json and explicit_rg_format)
        and not enrich_ast
        and _self._can_delegate_to_native_tg_search(
            config,
            ndjson=ndjson,
            files_mode=files,
            files_with_matches=files_with_matches,
            files_without_match=files_without_match,
            format_type=format_type,
        )
    ):
        sys.exit(
            _self._delegate_to_native_tg_search(
                native_tg_binary,
                pattern=pattern,
                paths=paths_to_search,
                config=config,
                ndjson=ndjson,
            )
        )
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.io.directory_scanner import DirectoryScanner

    rg_backend = RipgrepBackend()
    rg_is_available = rg_backend.is_available()
    if config.count_matches and not rg_is_available:
        # Backend Fail-Closed Contract (AGENTS.md) / task #121: `--count-matches` reports
        # ripgrep's OCCURRENCE count (every match on a line counts separately), which is
        # semantically different from `-c`/`--count`'s LINE count (one per matching line,
        # regardless of how many times the pattern occurs on it). Every fallback engine
        # that can serve a query without rg -- RustCoreBackend AND CPUBackend -- is
        # LINE-granular only: neither ever emits more than one match per line, so a
        # `--count-matches` request routed to either would silently report a LINE count
        # mislabeled as an occurrence count (verified live: a 3-occurrence line undercounts
        # to 1). Unlike the default/--json search degrade (a like-for-like engine swap: the
        # native engine's match set IS what tg's own aggregate model already uses), there is
        # no fallback engine here that can serve the SAME semantics rg provides, so silently
        # "degrading" would be silent-wrong-output, not a graceful degrade. Refuse cleanly
        # instead -- mirroring the identical refusal the native `--index` fast path already
        # applies to `count_matches` for the same reason (`IndexFlagPolicy::Refuse` in
        # rust_core/src/main.rs) -- rather than let it through as a wrong number. `-c`/
        # `--count` is unaffected: its line-count contract is exactly what the fallback
        # engines already provide correctly (Pipeline's `count_rust_fast_path`).
        _exit_search_error(
            "count_matches_requires_ripgrep",
            (
                "--count-matches reports ripgrep's per-occurrence match count, which "
                "requires the 'rg' binary; rg was not found (checked TG_RG_PATH, PATH, "
                "and the bundled fallback). Install ripgrep "
                "(https://github.com/BurntSushi/ripgrep#installation), set TG_RG_PATH, "
                "or use --count (-c) for a line count that works without rg."
            ),
            json_mode=json,
        )
    # P5·H2 (audit verdict / codex Finding 1): `-l`/`--files-with-matches`/`--files-without-match`
    # are RAW PATH-OUTPUT modes. The tensor-grep aggregate `--json`/`--ndjson` envelope has no
    # place for a bare path list, and the files emitters below (main.py ~the `if files_with_matches:`
    # / `if files_without_match:` blocks) would print plain paths with exit 0 while the caller asked
    # for structured output -- the JSON contract silently dropped (verified live:
    # `tg search --json -l PAT DIR` emits the path, exit 0). Fail closed, mirroring the native
    # `exit_native_structured_flag_dropped` refusal (rust_core/src/main.rs) and the
    # `count_matches_requires_ripgrep` refusal above (same `_exit_search_error` contract).
    # `--format rg --json` (rg passthrough) is deliberately excluded: `_can_passthrough_rg` returns
    # False for files + rg_json_passthrough, so that route never reaches these emitters as an
    # additive same-JSON contract issue (its raw-paths behavior is pre-existing and tracked
    # separately). The exemption REQUIRES `not ndjson`: `--ndjson` has no rg-passthrough twin
    # (rg's `--json` events are not the tensor-grep ndjson schema), so `--json --ndjson --format rg
    # -l` must still refuse rather than drop the ndjson contract (codex round-3 finding). Plain
    # `-l`/`--files-with-matches` WITHOUT --json/--ndjson keeps working (it
    # routes to rg passthrough below; pinned by test_cli_modes.py::test_files_with_matches_*).
    if (
        (json or ndjson)
        and not (json and explicit_rg_format and not ndjson)
        and (files_with_matches or files_without_match)
    ):
        flag_list = ", ".join(
            spelling
            for present, spelling in (
                (files_with_matches, "-l/--files-with-matches"),
                (files_without_match, "--files-without-match"),
            )
            if present
        )
        _exit_search_error(
            "unsupported_flag",
            (
                f"{flag_list} is a raw path-output mode that cannot be expressed inside the "
                "tensor-grep aggregate --json/--ndjson envelope and would be silently dropped; "
                f"refusing rather than silently ignoring it. Drop --json/--ndjson (or {flag_list}) "
                "to get the raw path list."
            ),
            json_mode=json,
        )
    can_passthrough_rg = (
        not guarded_broad_root
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

    scanner = DirectoryScanner(config)
    candidate_files_ordered, candidate_files_set = _collect_candidate_files(
        scanner, paths_to_search
    )
    config.input_total_bytes = _sum_total_bytes(candidate_files_ordered)

    from tensor_grep.core.pipeline import ConfigurationError, Pipeline
    from tensor_grep.core.result import SearchResult, merge_runtime_routing

    try:
        pipeline = Pipeline(force_cpu=effective_force_cpu, config=config)
    except ConfigurationError as exc:
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
            _format_unbounded_large_root_scan_error(_LARGE_ROOT_SCAN_FILE_CEILING),
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

    def _record_matched_file(file_path: str | None) -> None:
        if not file_path or file_path in matched_file_paths:
            return
        matched_file_paths.add(file_path)
        matched_file_paths_ordered.append(file_path)

    def _merge_runtime_routing(result: SearchResult) -> None:
        merge_runtime_routing(all_results, result)
        if result.fallback_reason is not None:
            all_results.fallback_reason = result.fallback_reason

    def _merge_count_metadata(result: SearchResult) -> None:
        for file_path, count in result.match_counts_by_file.items():
            all_results.match_counts_by_file[file_path] = (
                all_results.match_counts_by_file.get(file_path, 0) + count
            )

    # RipgrepBackend optimization: passing all paths natively
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
        # Critical unscoped-search-hang fix (B): the native (CPU/Torch) engine has no
        # internal per-file timeout -- unlike the RipgrepBackend branch above, which is
        # bounded by the rg subprocess's own `configured_ripgrep_timeout_seconds()` timeout.
        # A search that can't route through rg (native `--json` aggregate, `--rank`,
        # tensor-only flags, or rg absent from PATH) would otherwise walk
        # `candidate_files_ordered` with NO limit at all and could hang until manually
        # killed on a large/unscoped tree. Check the SAME wall-clock budget once per FILE
        # (never per match -- that would be too fine-grained to bound a pathological single
        # file) and, on expiry, stop and return whatever was found so far as an explicitly
        # incomplete (never silently empty, never a raw crash) result.
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
                except BackendExecutionError as exc:
                    # A native backend failed at runtime; retry once on the always-
                    # available CPU backend so the search returns correct results instead
                    # of a false no-match or a crash (audit B2/I1).
                    result = _search_with_cpu_fallback(current_file, pattern, config, exc)
                except Exception as exc:
                    if _is_invalid_regex_error(exc):
                        _exit_invalid_regex(exc, json_mode=json)
                    raise
                if span is not None:
                    span.set_attribute("matches", result.total_matches)
            all_results.matches.extend(result.matches)
            for matched_path in result.matched_file_paths:
                _record_matched_file(matched_path)
            _merge_count_metadata(result)
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

    if config.replace_str is not None:
        all_results.matches = _replace_lines(all_results.matches, pattern, config)

    if only_matching:
        all_results.matches = _only_matching_lines(all_results.matches, pattern, config)
        all_results.total_matches = len(all_results.matches)
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
                # F4 (Fable audit MED): a genuine dense-backend fault (e.g. a corrupt model
                # directory) must exit cleanly with a `tg:` message, never a raw traceback --
                # `_apply_semantic_rerank` deliberately does NOT catch this (see its docstring);
                # this is the CLI boundary the Backend Fail-Closed Contract requires.
                if json:
                    _emit_search_error_json("semantic_backend_error", str(exc))
                else:
                    typer.echo(f"tg: {exc}", err=True)
                sys.exit(2)
        else:
            # F16 (Fable audit LOW): probe dense-leg availability even on a 0-match search so
            # `rank_fallback_reason` is set whenever the leg is unavailable, regardless of match
            # count -- skipping the probe here silently made the JSON envelope dishonest.
            _set_semantic_rank_fallback_reason(all_results)
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
        scope_filtered = bool(
            config.max_depth is not None
            or config.glob
            or config.iglob
            or config.file_type
            or config.type_not
        )
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
        sys.exit(2 if exit_incomplete else 1)

    if quiet:
        _emit_stats()
        sys.exit(2 if exit_incomplete else 0)

    if enrich_ast and all_results.matches:
        from tensor_grep.cli.ast_enrichment import (
            AST_ENRICH_FILE_LIMIT,
            enrich_match_with_container,
        )
        from tensor_grep.core.result import MatchLine

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
                    m = MatchLine(
                        line_number=m.line_number,
                        text=m.text,
                        file=m.file,
                        start_byte=m.start_byte,
                        end_byte=m.end_byte,
                        range=m.range,
                        meta_variables=m.meta_variables,
                        submatches=m.submatches,
                        container=container,
                    )
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


@app.command()
def calibrate(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a structured JSON result, including a machine-readable "
        "calibration_status skip signal when GPU calibration cannot run, instead of the "
        "default human-readable output. Forwarded to the native tg binary; does not change "
        "the exit code.",
    ),
) -> None:
    """Measure CPU vs GPU crossover thresholds using the native Rust binary."""
    native_tg_binary = _self.resolve_native_tg_binary()
    if native_tg_binary is None:
        # audit L10: calibrate is unsupported without the native binary (and on CPU-only
        # boxes the native binary itself exits non-zero when CUDA is unavailable). tg's
        # convention is exit 1 for runtime/unsupported errors, not exit 2 (usage errors).
        # P0-4 (GPU Phase-0 honesty, #596) named a remediation here so this wasn't a dead end.
        # CEO dogfood follow-up (v1.76.6): #596's "if published ... falls back to CPU when it
        # is not" framing still invited TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia + `tg
        # upgrade` as an obtainable GPU path, but no NVIDIA-enabled asset has ever shipped (the
        # release profile that builds one is held off) -- a permanent dead end dressed up as
        # honest advice. State the evergreen, structural fact instead (GPU needs a
        # CUDA-enabled build, which isn't present here) without claiming an upgrade will --or
        # won't-- fetch one; `tg doctor` is the live way to check once a binary exists.
        # #182 NIT-1: the v1.76.6 revision still name-dropped
        # TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia in a "confirm before relying on" aside --
        # dropped here so this Python wrapper matches the Rust side (crossover.rs), whose
        # detect_device_name test forbids that override as an obtainable path. This
        # wrapper uses inherited stdio for the real `calibrate` subprocess below and must NOT
        # capture its stderr (that would break streaming), so only THIS wrapper-owned
        # missing-binary message gets touched; the native binary's own calibrate-failure
        # remediation is Rust-owned (crossover.rs) and follows the same discipline there.
        # v20 dogfood (GPU honesty / harness-misread, additive): a --json caller gets a
        # structured stdout signal too, so a harness can tell "binary missing" apart from a
        # generic exit-1 failure the same way it can now tell the Rust side's no-cuda-build
        # skip apart from a genuine calibration failure. The human-readable stderr message
        # below is unchanged either way (still exit 1, still the same remediation text).
        if json_output:
            _safe_stdout_line(json.dumps({"calibration_status": "native_binary_unavailable"}))
        typer.echo(
            "Error: native tg binary not found for calibrate command.\n"
            "Run 'tg upgrade' to install the native tg binary that calibrate requires. GPU "
            "(CUDA) acceleration is experimental and is not shipped in any current build; run "
            "'tg doctor' after upgrading to check this install's native flavor.",
            err=True,
        )
        raise typer.Exit(1)

    argv = [str(native_tg_binary), "calibrate"]
    if json_output:
        argv.append("--json")
    completed = subprocess.run(argv, check=False)
    raise typer.Exit(int(completed.returncode))


@app.command()
def devices(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit device inventory as JSON for automation.",
    ),
    format_type: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """Print routable GPU device IDs and VRAM inventory."""
    import json

    from tensor_grep.core.hardware.device_inventory import collect_device_inventory

    normalized_format = format_type.lower().strip()
    if json_output:
        normalized_format = "json"
    if normalized_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be one of: text, json")

    inventory = collect_device_inventory()
    payload = inventory.to_dict()

    if normalized_format == "json":
        print(json.dumps(_with_schema_version(payload)))
        return

    if not inventory.devices:
        typer.echo("No routable GPUs detected.")
        return

    typer.echo(f"Detected {inventory.device_count} routable GPU(s):")
    for device in inventory.devices:
        typer.echo(f"- gpu:{device.device_id} vram_mb={device.vram_capacity_mb}")


@app.command()
def map(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    max_files: int | None = typer.Option(
        None, "--max-files", min=1, help="Maximum source files to include in output."
    ),
    max_repo_files: int | None = typer.Option(
        512,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning. Defaults to the agent-safe 512-file cap.",
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
        # CLI consistency fix (CEO v1.71.3 dogfood): `--deadline` used to be undefined on `tg map`
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
    # helper used (build_repo_map then apply_repo_map_output_limits, json.dumps(indent=2)) so JSON
    # stays byte-identical, and gate on it so both json and text branches share the scan-truncation
    # contract -- output the full payload FIRST, then exit 2 if the scan itself was capped (an
    # output-only cap from --max-files stays exit 0).
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        _emit_scan_incompleteness_banner(payload)
        typer.echo(f"Repository map for {payload['path']}")
        typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")
        typer.echo(f"symbols={len(payload['symbols'])} imports={len(payload['imports'])}")

    if _scan_incomplete(payload):
        raise typer.Exit(2)


@app.command()
def inventory(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    max_repo_files: int = typer.Option(
        # Literal mirrors inventory.DEFAULT_MAX_INVENTORY_FILES (kept literal so the heavy
        # repo_map import stays lazy, matching `map`'s 512 pattern); a guard test pins them.
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


@app.command(name="docs-coverage")
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
            # CEO v1.72.1 dogfood M1: a --deadline-truncated scan is INCOMPLETE -- exit 2, checked
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


def _cli_deadline_monotonic(deadline_seconds: float | None) -> float | None:
    """Anchor an absolute ``time.monotonic()`` deadline at CLI command entry (closes the #197/#200
    front-door residual). Mirrors ``repo_map._deadline_monotonic_from_seconds``'s formula, but is
    meant to be called from the TOP of a deadline-bearing command body -- before the lazy builder
    import, path/query resolution, GPU-id parsing, and the warm-daemon gate -- so that front-door
    time is budgeted the same way the underlying repo scan already is. Kept as a tiny, import-free
    helper (uses only the module-level ``time`` already imported at the top of this file) so calling
    it early never forces an eager heavy import.

    The remaining process-startup prefix (interpreter boot + Typer/Click argument dispatch) BEFORE
    Python even reaches this line is a separate, irreducible ~100-200ms budget gap that no CLI
    command body can account for -- documented in the ``--deadline`` help text and
    docs/CONTRACTS.md rather than fixed here."""
    if deadline_seconds is None:
        return None
    return time.monotonic() + deadline_seconds


@app.command()
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
    json_output: bool = typer.Option(False, "--json", help="Emit the capsule as JSON"),
) -> None:
    """Emit a one-call codebase orientation capsule (central files, entry points, AST snippets)."""
    # Anchor deadline_monotonic at CLI command entry (closes the #197/#200 front-door residual):
    # computed here, BEFORE the lazy orient_capsule import and the daemon gate below, so front-door
    # time counts against an explicit --deadline the same way the underlying scan already does.
    # CLI consistency fix (CEO v1.71.3 dogfood): `--deadline` used to be undefined on `tg orient`
    # (Click "No such option" exit-2).
    effective_deadline = None if no_deadline else deadline
    deadline_monotonic = _cli_deadline_monotonic(effective_deadline)

    from tensor_grep.cli.orient_capsule import build_orient_capsule

    # Task #108 (Tier-2 daemon moat): probe BEFORE the try block -- a daemon hit is already a
    # ready-built dict (no filesystem call left that could raise FileNotFoundError/ValueError), so
    # it does not need the cold path's exception handling. A miss/error/mismatch falls open to the
    # unchanged cold path below (fail-open contract). Skipped entirely when a --deadline was
    # requested (a warm session's cached repo_map cannot honor a fresh per-request scan deadline),
    # mirroring refs/callers/impact/blast-radius's own daemon gate.
    daemon_payload = (
        _maybe_orient_via_running_daemon(
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
        typer.echo(json.dumps(payload, indent=2))
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


@app.command()
def codemap(
    path: str = typer.Argument(".", help="Directory to render a browsable code map for"),
    out: str | None = typer.Option(
        None,
        "--out",
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
    # Anchor deadline_monotonic at CLI command entry (closes the #197/#200 front-door residual):
    # computed here, BEFORE the lazy codemap import, so import cost counts against the budget for
    # the (non-`--check`) scanning path below. --check is a read-only freshness check with no scan/
    # deadline of its own; computing this unconditionally here is a cheap no-op for that branch.
    effective_deadline = None if no_deadline else deadline
    deadline_monotonic = _cli_deadline_monotonic(effective_deadline)

    from tensor_grep.cli.codemap import build_codemap, check_codemap_freshness

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
    except (FileNotFoundError, NotADirectoryError) as exc:
        typer.echo(str(exc), err=True)
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


@app.command()
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
        # CLI consistency fix (CEO v1.71.3 dogfood): `--deadline` used to be undefined on
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
        _emit_scan_incompleteness_banner(payload)
        typer.echo(f"Context pack for {payload['path']}")
        typer.echo(f"query={payload['query']}")
        typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")
        typer.echo(f"symbols={len(payload['symbols'])} imports={len(payload['imports'])}")

    if _scan_incomplete(payload):
        raise typer.Exit(2)


def _daemon_directory_path(path: str) -> str | None:
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except OSError:
        return None
    if resolved.is_file():
        return None
    return str(resolved)


def _session_daemon_autostart_enabled() -> bool:
    """TG_SESSION_DAEMON_AUTOSTART opt-out for the default Tier-1 warm-daemon fast path.

    Task #94 PR-1 (the conscious default flip flagged by the original Part A comment; cleared
    after #498 landed the daemon response-cache correctness fix docs/BACKLOG.md's #94 entry
    gated the flip on). DEFAULT ON: unset -- or any value other than an explicit falsy token
    (``0``/``false``/``no``/``off``, see ``env_flag_disabled`` in runtime_paths.py) -- routes
    defs/impact/refs/callers/blast-radius through a running ``tg session daemon``, non-blocking
    auto-spawning one on a miss. This is the ~20x warm-vs-cold latency win: the cold path pays a
    6-33s repo-map build on every call. Set the flag to an explicit falsy token to opt back out
    to the always-cold path, byte-for-byte unchanged from before this PR.

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
    """Task #108 (Tier-2 daemon moat): fail-open warm-daemon fast path for `tg orient`, mirroring
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
    """Task #108 (Tier-2 daemon moat): fail-open warm-daemon fast path for `tg agent`, mirroring
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


@app.command(name="context-render")
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
        help="Render profile: full, compact, or llm. Defaults to llm for JSON and full for text.",
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
        "Stop after N seconds, measured from CLI command entry (not just the underlying repo scan -- excludes only the ~100-200ms interpreter-startup/dispatch prefix before this command body runs), and return a partial bundle (partial=true, deadline_limit) with whatever was found so far, instead of running unbounded. Pass --no-deadline to keep the (already default) unbounded behavior explicit."
    ),
    no_deadline: bool = typer.Option(
        False,
        "--no-deadline",
        help="Accepted for command-surface parity with codemap; a no-op since context-render "
        "already defaults to an unbounded --deadline.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready repository context bundle for edit planning."""
    # Anchor deadline_monotonic at CLI command entry (closes the #197/#200 front-door residual):
    # computed here, BEFORE the lazy repo_map import, path resolution, and the daemon gate below,
    # so front-door time counts against an explicit --deadline the same way the scan already does.
    # CLI consistency fix (CEO v1.71.3 dogfood): `--deadline` used to be undefined on
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
            _maybe_context_render_via_running_daemon(
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
            if effective_deadline is None
            else None
        )
        if daemon_payload is not None:
            # Output-before-exit (Cluster B, 2026-07-06): the warm-daemon path must honor the same
            # exit-2-on-scan-truncation contract as the cold path below -- a truncated daemon payload
            # still prints in full, then exits 2, instead of a silent exit 0 that reads as complete.
            if json_output:
                if daemon_payload.get("render_profile") == "llm":
                    typer.echo(json.dumps(daemon_payload, separators=(",", ":")))
                else:
                    typer.echo(json.dumps(daemon_payload, indent=2))
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
    if json_output:
        if payload.get("render_profile") == "llm":
            typer.echo(json.dumps(payload, separators=(",", ":")))
        else:
            typer.echo(json.dumps(payload, indent=2))
    else:
        _emit_scan_incompleteness_banner(payload)
        typer.echo(payload["rendered_context"])

    if _scan_incomplete(payload):
        raise typer.Exit(2)


# v1.81.6 dogfood finding #1 (CEO-relayed, both dogfood reports flagged it as the #1 agent
# confusion): `tg agent --deadline N` can exit 2 with `partial: true` / a deadline-type
# `partial_reason` while `confidence.overall` is high and `ask_user_before_editing.required` is
# false -- a genuinely USABLE answer that merely stopped collecting SECONDARY evidence (the
# call-site rescue scan / outbound-dependency preview / the final wall-clock backstop in
# `agent_capsule.build_agent_capsule_from_map`, all AFTER the primary-target ranking/render already
# completed) before the deadline. An agent keying on the exit code alone misreads this as a hard
# failure. `tg agent` is scoped ONLY here (not `edit-plan`/`map`/`context-render`/`blast-radius`,
# which share `_scan_incomplete` but not this stderr note) per the finding's exact ask.
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


@app.command(name="agent")
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
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return an actionable context capsule for agents before editing."""
    # Anchor deadline_monotonic at CLI command entry (closes the #197/#200 front-door residual):
    # computed here, BEFORE the lazy agent_capsule import, path resolution, GPU-id parsing, and the
    # daemon gate below, so front-door time counts against an EXPLICIT --deadline the same way the
    # underlying scan already does. Deliberately based on `effective_deadline` (needs only the raw
    # deadline/no_deadline params, no import) rather than the F4 `cold_deadline_seconds` default
    # further down: that generous 60s cold-path-only fallback needs DEFAULT_AGENT_CLI_DEADLINE_SECONDS
    # from the lazy import and only ever applies once we already know the daemon path was not taken,
    # so it keeps its own existing (later) anchor point there -- this fix's scope is the EXPLICIT
    # --deadline case, where anchoring here already closes the entire front-door gap. The
    # irreducible interpreter-boot + Typer/Click dispatch prefix before Python even reaches this
    # line remains undocumented here (~100-200ms, see the --deadline help text below).
    effective_deadline = None if no_deadline else deadline
    deadline_monotonic = _cli_deadline_monotonic(effective_deadline)

    from tensor_grep.cli.agent_capsule import (
        DEFAULT_AGENT_CLI_DEADLINE_SECONDS,
        build_agent_capsule,
    )

    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="agent",
        )
        parsed_gpu_device_ids = _parse_gpu_device_ids_cli(gpu_device_ids)
        _warn_unavailable_gpu_device_ids(parsed_gpu_device_ids)
        # Task #108 (Tier-2 daemon moat): mirrors edit-plan's daemon-payload gate (:8452-8478
        # below) -- print the full daemon payload through the SAME json/text branches and the SAME
        # exit-2-on-scan-truncation contract as the cold path, then return early. A miss/error/
        # mismatch (including the TRAP A `daemon_evidence_unreliable` sentinel) falls open to the
        # unchanged cold build below. Skipped entirely when a --deadline was requested (a warm
        # session's cached repo_map cannot honor a fresh per-request scan deadline), mirroring
        # refs/callers/impact/blast-radius's own daemon gate.
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
            else None
        )
        if daemon_payload is not None:
            if json_output:
                typer.echo(json.dumps(daemon_payload, ensure_ascii=False, indent=2))
            else:
                _emit_scan_incompleteness_banner(daemon_payload)
                payload = daemon_payload
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
            if _scan_incomplete(daemon_payload):
                deadline_partial_note = _agent_trustworthy_deadline_partial_note(daemon_payload)
                if deadline_partial_note is not None:
                    typer.echo(deadline_partial_note, err=True)
                raise typer.Exit(2)
            return

        # dogfood finding 1 (F4): default the COLD path's --deadline to 60s (mirrors codemap's
        # #153) so a whole-repo `tg agent` call with no explicit --deadline still terminates in
        # bounded time. Deliberately computed HERE, AFTER the warm-daemon gate above -- and from
        # the RAW `deadline`/`no_deadline` params, never by reusing `effective_deadline` -- because
        # `effective_deadline` intentionally conflates "no --deadline was given" with "--no-deadline
        # was given" so the gate above treats them identically (a warm session's cached repo_map
        # cannot honor a fresh per-request deadline either way). Collapsing this 60s default into
        # THAT variable, or defaulting it on the typer.Option itself, would make effective_deadline
        # never None on a default call, silently skipping the daemon probe on every single one of
        # them -- the #108 moat.
        cold_deadline_seconds = effective_deadline
        if cold_deadline_seconds is None and not no_deadline:
            cold_deadline_seconds = DEFAULT_AGENT_CLI_DEADLINE_SECONDS
            # The early anchor above only fires for an EXPLICIT --deadline (effective_deadline was
            # non-None); this F4 default is a separate cold-path-only fallback that only exists once
            # we already know the daemon path was not taken, so it gets its own anchor here (mirrors
            # this same variable's pre-fix anchor point, just hoisted from inside build_agent_capsule
            # to right after the daemon-miss decision -- not a regression for this implicit case,
            # and not #197/#200's target: that residual is about a small EXPLICIT --deadline, not
            # this generous default).
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
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Cold path (PR-1 1D, mirrors the context-render cold path :7486-7499): build the payload once
    # and dump it here for both the json and text branches -- BYTE-IDENTICAL to the old
    # `build_agent_capsule_json` serialization (`ensure_ascii=False, indent=2`) -- so they share
    # ONE scan-truncation gate below. Output the full payload FIRST, then exit 2 if the SCAN itself
    # (not just the capsule's own render/token output budget) was capped -- `tg agent` was
    # previously the only command in this family that never gated on `_scan_incomplete`.
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
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

    if _scan_incomplete(payload):
        deadline_partial_note = _agent_trustworthy_deadline_partial_note(payload)
        if deadline_partial_note is not None:
            typer.echo(deadline_partial_note, err=True)
        raise typer.Exit(2)


@app.command(name="edit-plan")
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
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a machine-readable edit-planning bundle without rendered source text."""
    # Anchor deadline_monotonic at CLI command entry (closes the #197/#200 front-door residual):
    # computed here, BEFORE the lazy repo_map import, path resolution, and the daemon gate below,
    # so front-door time counts against an explicit --deadline the same way the scan already does.
    # CLI consistency fix (CEO v1.71.3 dogfood): `--deadline` used to be undefined on
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
        daemon_payload = (
            _maybe_edit_plan_via_running_daemon(
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
                typer.echo(json.dumps(daemon_payload, indent=2))
            else:
                _emit_scan_incompleteness_banner(daemon_payload)
                payload = daemon_payload
                typer.echo(f"Edit plan for {payload['path']}")
                typer.echo(f"query={payload['query']}")
                typer.echo(
                    f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
                )
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
        typer.echo(json.dumps(payload, indent=2))
    else:
        _emit_scan_incompleteness_banner(payload)
        typer.echo(f"Edit plan for {payload['path']}")
        typer.echo(f"query={payload['query']}")
        typer.echo(
            f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
        )

    if _scan_incomplete(payload):
        raise typer.Exit(2)


_ROUTE_TEST_CONFIDENCE_WARNING_THRESHOLD = 0.75
# When both routes AGREE on the primary target, a sub-threshold confidence reflects ranking-score
# calibration, not routing doubt -- it is demoted to an additive `note`, not a `warning`. But
# agreement is not correctness (context-render + edit-plan share the upstream ranker, so they can
# agree on the same WRONG file); if BOTH confidences fall below this floor, keep the warning as the
# correlated-error tell.
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


@app.command(name="route-test")
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
        True, "--json/--text", help="Emit machine-readable JSON output (default)."
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


@app.command(name="prepare")
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
    json_output: bool = typer.Option(
        True, "--json/--text", help="Emit machine-readable JSON output (default)."
    ),
    out: str | None = typer.Option(
        None,
        "--out",
        help=(
            "Also persist the full capsule JSON to FILE, byte-identical to the --json stdout "
            "payload regardless of --text, so `tg evidence emit --capsule FILE` can reuse it "
            "without a manual save. Refuses to write through a pre-existing symlink."
        ),
    ),
) -> None:
    """One-call edit-readiness capsule: primary target, confidence, a callers/blast-radius
    floor, validation commands, and claim/evidence coordination hooks -- the single call meant
    to replace the orient -> search -> agent -> route-test -> callers -> evidence -> ledger loop.
    """
    # Anchor deadline_monotonic at CLI command entry (mirrors route-test/agent's #197/#200 fix):
    # computed BEFORE path/query resolution so front-door time counts against the bound the same
    # way the underlying capsule build + blast-radius floor scan already do.
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
        payload = _build_prepare_payload(
            path=resolved_path,
            query=resolved_query,
            claim=claim,
            deadline_monotonic=deadline_monotonic,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Output-before-exit (mirrors context-render/edit-plan/agent/route-test): print the full
    # payload FIRST, then exit 2 if either the capsule or the blast-radius floor was truncated --
    # never a silent exit 0 that reads as a complete, full-confidence result.
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        primary = payload.get("primary_target", {})
        floor = payload.get("blast_radius_floor", {})
        claim_hook = payload.get("coordination", {}).get("claim", {})
        # `prepare` already ended with `partial=true partial_reason=...`, but only for the
        # `partial` cause and only AFTER every line it qualifies. The banner covers every cause
        # and leads. The key=value line stays: it is a structured field in a key=value listing,
        # not prose duplicating prose (contrast `codemap`, where adding a banner beside its
        # existing `PARTIAL:` sentence would have said the same thing twice in the same register).
        _emit_scan_incompleteness_banner(payload)
        typer.echo(f"Prepare for {payload['path']}")
        typer.echo(f"query={payload['query']}")
        typer.echo(f"primary={primary.get('file')}#L{primary.get('line')} {primary.get('symbol')}")
        typer.echo(f"confidence={payload.get('confidence', {}).get('overall', 0)}")
        typer.echo(
            f"ask_required={bool(payload.get('ask_user_before_editing', {}).get('required'))}"
        )
        typer.echo(
            "blast_radius_floor="
            f"{floor.get('callers_count', 0)} callers (source={floor.get('source')})"
        )
        typer.echo(f"validation={len(payload.get('validation_commands', []))} commands")
        typer.echo(f"claim_submitted={bool(claim_hook.get('submitted'))}")
        if payload.get("partial"):
            typer.echo(f"partial=true partial_reason={payload.get('partial_reason')}")

    if out is not None:
        # v1.92.1 dogfood feature 4: persist the FULL capsule JSON regardless of --text, so the
        # file always works with `tg evidence emit --capsule FILE` (a text-mode summary would
        # not). Written AFTER stdout above (never before) -- mirrors this command's own
        # "output-before-exit" contract: an --out failure must not hide the payload that was
        # already computed. Reuses the house atomic-write helper
        # (_index_lock.atomic_write_json via session_store._write_json_atomic) with the exact
        # same symlink-precheck-then-resolve shape `tg evidence emit --out` uses
        # (main.py's evidence_emit, "audit C4 / CWE-59") rather than a bare open().write().
        from tensor_grep.cli.session_store import _write_json_atomic

        try:
            # C4/CWE-59: check for a symlink BEFORE `.resolve()` -- resolving first would
            # follow the symlink to its real target and make `is_symlink()` on the result
            # always False, silently defeating `_write_json_atomic`'s own symlink guard. This
            # also refuses a DANGLING symlink (a broken target still makes `is_symlink()` True
            # regardless of whether the target exists). A destination that is an existing
            # directory is refused too, via the OSError `atomic_write_bytes`'s own
            # `os.replace` raises when a file cannot replace a directory -- caught by the same
            # `except OSError` below, never a raw traceback.
            expanded_out = Path(out).expanduser()
            if expanded_out.is_symlink():
                raise OSError(
                    f"Refusing to write the prepare capsule through a symlink: {expanded_out}"
                )
            resolved_out = expanded_out.resolve()
            _write_json_atomic(resolved_out, payload)
        except OSError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    if _scan_incomplete(payload):
        raise typer.Exit(2)


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _format_symbol_location_row(row: dict[str, Any]) -> str:
    file_name = str(row.get("file", "")).strip()
    if not file_name:
        return ""

    line = _positive_int(row.get("line", row.get("start_line")))
    location = file_name if line is None else f"{file_name}:{line}"
    column = _positive_int(row.get("column", row.get("col", row.get("start_column"))))
    if line is not None and column is not None:
        location = f"{location}:{column}"

    details: list[str] = []
    kind = str(row.get("kind", "")).strip()
    name = str(row.get("name", "")).strip()
    text = " ".join(str(row.get("text", "")).strip().split())
    if kind:
        details.append(kind)
    if name:
        details.append(name)
    if text:
        details.append(f"| {text}")
    if not details:
        return location
    return f"{location} {' '.join(details)}"


def _echo_symbol_location_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        rendered = _format_symbol_location_row(row)
        if rendered:
            typer.echo(rendered)


def _apply_defs_class_filter(payload: dict[str, Any], class_filter: str) -> None:
    """Filter ``payload['definitions']`` in place to those whose enclosing class matches
    ``class_filter`` (case-insensitive exact match), disambiguating common method names
    such as ``search`` (audit L3-cli).

    Each definition carries a ``class`` field (enclosing class name, or ``None`` for
    module-level/free functions) populated by ``build_symbol_defs`` in repo_map.py. The
    filter and the requested value are recorded as additive top-level fields so JSON
    consumers can see that a narrowing was applied; the existing keys are left intact.
    """
    target = class_filter.strip().casefold()
    definitions = payload.get("definitions") or []
    filtered = [
        definition
        for definition in definitions
        if str(definition.get("class") or "").casefold() == target
    ]
    payload["definitions"] = filtered
    payload["class_filter"] = class_filter
    payload["class_filter_matched"] = len(filtered)


def _symbol_payload_has_no_results(payload: dict[str, Any], result_key: str) -> bool:
    """Whether a symbol-command payload found nothing for the requested symbol.

    A payload is empty either when the resolver flagged ``no_match`` or when its
    primary result collection is empty. Used to honor rg's no-match exit convention
    for the symbol commands (audit L1).
    """
    if payload.get("no_match"):
        return True
    return not payload.get(result_key)


def _symbol_not_found_claim(payload: dict[str, Any], result_key: str) -> bool:
    """Whether the payload may affirmatively claim the symbol is ABSENT (task 327).

    ``not_found`` is a positive claim -- "we looked and it is not there" -- and rg's exit-1
    convention is built on it. A scan cut short by ``--deadline`` or a ``--max-repo-files`` cap
    never finished looking, so an empty result from it proves nothing. An external dogfood of
    v1.98.27 caught ``tg refs ... --deadline 0.1 --json`` reporting ``files_scanned: 0`` and
    ``not_found: true`` in the same payload: the confident false zero this command's own emitter
    docstring exists to prevent, surviving in the one field the completeness machinery never
    covered.

    The truncation predicate is ``_scan_incomplete`` -- NOT a fresh check -- because that gate is
    already where "the scan-vs-output-cap contract is defined exactly once". A second notion of
    incompleteness here could drift from the exit-code gate and reintroduce exactly the
    inconsistency this fixes. It also gets the OUTPUT-cap boundary right for free: an output cap
    is a complete analysis capped for display, so it must NOT suppress ``not_found``.

    Exit codes are unaffected: ``_scan_incomplete``-true payloads already exit 2 on a branch
    evaluated before ``not_found`` is consulted, so this only changes what the FIELD says to a
    caller reading the JSON.
    """
    return _symbol_payload_has_no_results(payload, result_key) and not _scan_incomplete(payload)


_ZERO_CALLERS_CAVEAT = (
    "0 callers in the static call graph does not mean this symbol is dead code. Dynamic "
    "dispatch (getattr / decorators / string-keyed registries), test files, re-exports, and "
    "cross-repo callers can be invisible to the graph. Cross-check with `tg refs` or grep "
    "before treating it as unused."
)


_TRUNCATION_REMEDY = (
    "A zero or small count here is NOT trustworthy. Remedy: scope to a subdirectory, raise "
    "--max-repo-files / --max-callers / --max-files, or warm the index with "
    "`tg session daemon start`."
)


def _truncation_message(what: str) -> str:
    # ASCII-only (no em-dash): the warning prints to Windows consoles where cp1252 mojibakes it.
    return f"INCOMPLETE RESULT: {what}, so callers/definitions may be missing. {_TRUNCATION_REMEDY}"


# A deadline needs its OWN remedy. `_TRUNCATION_REMEDY` names the budget knobs, and every one of
# them is the wrong dial for a scan that ran out of TIME -- raising --max-repo-files lets it read
# MORE files inside the same expired budget, which cannot help and reads as actionable advice.
# Wrong-knob remediation is the failure #762 fixed on the MCP surface and #822 fixed on --mermaid;
# reusing the budget string here would have reintroduced it in the same commit that closes the
# deadline gap. The last sentence says so explicitly rather than leaving it inferable.
_DEADLINE_REMEDY = (
    "A zero or small count here is NOT trustworthy. Remedy: raise --deadline, scope to a "
    "subdirectory, or warm the index with `tg session daemon start`. Raising --max-repo-files "
    "does NOT help here -- the scan ran out of TIME, not budget."
)


def _deadline_truncation_message(what: str) -> str:
    # ASCII-only, same reason as _truncation_message.
    return f"INCOMPLETE RESULT: {what}, so callers/definitions may be missing. {_DEADLINE_REMEDY}"


def _scan_truncation_warning(payload: dict[str, Any]) -> str | None:
    """Human warning when a result was truncated before covering the project (P0).

    A truncated result that drops project files can return a confident-looking zero (or small
    count) that renders identically to a real one — the single most dangerous output for a
    refactor-safety tool, since it greenlights deleting live code. The payload already knows;
    this projects it into the default output so an incomplete result can never look complete.
    Handles all four shapes production emits: the repo-scan cap
    (``scan_limit.possibly_truncated`` — callers/refs/impact), the caller-scan ceiling
    (``caller_scan_limit.possibly_truncated`` — F1: a COMPLETE repo-map whose own internal
    CALLER_SCAN_FILE_CEILING still bounded how many of its files were walked for callers/refs),
    the repo-map output cap (``output_limit.possibly_truncated`` — map/context), and the
    blast-radius output cap (``output_limit.callers_truncated`` / ``files_truncated``). Returns
    None when complete.
    """
    for key in ("scan_limit", "caller_scan_limit", "output_limit"):
        limit = payload.get(key)
        if not (isinstance(limit, dict) and limit.get("possibly_truncated")):
            continue
        if key == "caller_scan_limit":
            ceiling = limit.get("ceiling", "?")
            files_total = limit.get("files_total", "?")
            return _truncation_message(
                f"caller-scan bounded to the first {ceiling} of {files_total} mapped files; "
                "narrow the PATH or raise --max-repo-files for full coverage"
            )
        scanned = limit.get("scanned_files", limit.get("emitted_files", "?"))
        cap = limit.get("max_repo_files", limit.get("max_files", "?"))
        return _truncation_message(
            f"the scan stopped at a {cap}-file cap (scanned {scanned}) and dropped project files"
        )
    output_limit = payload.get("output_limit")
    if isinstance(output_limit, dict) and (
        output_limit.get("callers_truncated") or output_limit.get("files_truncated")
    ):
        dropped: list[str] = []
        if output_limit.get("callers_truncated"):
            omitted = output_limit.get(
                "omitted_callers",
                max(
                    0,
                    int(output_limit.get("total_callers", 0))
                    - int(output_limit.get("returned_callers", 0)),
                ),
            )
            dropped.append(f"{omitted} caller(s)")
        if output_limit.get("files_truncated"):
            omitted_files = max(
                0,
                int(output_limit.get("total_files", 0))
                - int(output_limit.get("returned_files", 0)),
            )
            dropped.append(f"{omitted_files} file(s)")
        return _truncation_message(f"output was capped, omitting {' and '.join(dropped)}")
    # THE DEADLINE SHAPE -- a third cause this function could not see, and the largest ABSENT case
    # in the disclosure class. A `--deadline` cutoff sets `partial` / `deadline_limit`, never a
    # `*_limit.possibly_truncated`, so every branch above missed it and this returned None. Meanwhile
    # `_scan_incomplete` DOES fire on `partial`, so the process exited 2 while stdout said nothing.
    # Measured as a paired arm through `blast_radius`, one variable moving:
    #     ARM A  scan_limit cap        exit 2 + "warning: INCOMPLETE RESULT: ..."
    #     ARM B  partial + deadline    exit 2 + nothing at all
    # Exit 2 with silent stdout is worse than a mispositioned warning: a reader who never sees a
    # line has nothing to be late about.
    #
    # Written LAST so it cannot mask a more specific cause -- a payload carrying both a file cap and
    # a deadline still reports the cap, which names the actionable knob.
    #
    # Predicate deliberately MIRRORS `repo_map._scan_did_not_finish` / `_scan_incomplete` rather than
    # adding a fourth private notion of "truncated". Re-deriving truncation narrowly IS this defect
    # class (task 332 swept 3 of 3 readers for exactly this), so a new definition would guarantee
    # the next drift.
    deadline_limit = payload.get("deadline_limit")
    deadline_hit = isinstance(deadline_limit, dict) and deadline_limit.get("deadline_exceeded")
    if deadline_hit or payload.get("partial"):
        scanned = None
        if isinstance(deadline_limit, dict):
            scanned = deadline_limit.get("files_scanned")
            total = deadline_limit.get("files_total")
            if scanned is not None and total is not None:
                return _deadline_truncation_message(
                    f"the --deadline elapsed after {scanned} of {total} files"
                )
        return _deadline_truncation_message("the scan stopped early at its --deadline")
    # FAIL-CLOSED TAIL -- the class fix, of which the deadline branch above is one instance.
    #
    # Two predicates decide two halves of the same contract: `_scan_incomplete` decides the EXIT
    # CODE, this function decides the MESSAGE. Nothing made them agree, so any cause reaching one
    # and not the other exits 2 in silence. That is not hypothetical -- it was true of TWO fields
    # on `origin/main`, and only one of them was the deadline gap this commit set out to close:
    #
    #     scan_limit cap          exit2=True  discloses=True
    #     caller_scan_limit       exit2=True  discloses=True
    #     partial (deadline)      exit2=True  discloses=False   <- the reported gap
    #     caller_scan_truncated   exit2=True  discloses=False   <- found while fixing it
    #
    # Enumerating causes is what produced the gap in the first place (each branch above was added
    # when its cause arrived, and the next cause arrived without one). So the fix is structural:
    # ask the EXIT GATE. If it considers this scan truncated and nothing above described why, say
    # so generically rather than returning None. A vague warning is recoverable; silence beside
    # exit 2 is the failure this whole surface exists to prevent.
    #
    # Deliberately LAST, so every specific message above still wins and keeps naming its knob.
    # This is the floor, not the answer -- a new cause should still get its own branch.
    if _scan_incomplete(payload):
        return _truncation_message("the scan did not finish")
    return None


def _scan_incomplete(payload: dict[str, Any]) -> bool:
    """Whether a payload's SCAN (not output) was truncated.

    The shared exit-2 gate for the daemon/render fast-paths (``map``, ``context-render``,
    ``edit-plan``, ``blast-radius-render``, incl. their warm-daemon routes; Cluster B, 2026-07-06)
    and the ``blast-radius`` command. An OUTPUT cap (``output_limit.*`` -- ``--max-callers``,
    ``--max-files``) is a COMPLETE analysis capped only for display and must stay exit 0, so this
    checks ONLY ``scan_limit`` / ``caller_scan_limit`` ``possibly_truncated``, ``partial`` (a
    ``--deadline`` cutoff), and ``caller_scan_truncated`` (the ``CALLER_SCAN_FILE_CEILING``) --
    NEVER ``result_incomplete``, which ``_annotate_result_completeness`` also sets on an output cap
    (that would silently flip an output-cap-only invocation to exit 2 and break the
    output-cap-stays-0 pins).
    """
    for key in ("scan_limit", "caller_scan_limit"):
        limit = payload.get(key)
        if isinstance(limit, dict) and limit.get("possibly_truncated"):
            return True
    return bool(payload.get("partial") or payload.get("caller_scan_truncated"))


def _annotate_result_completeness(
    payload: dict[str, Any], *, result_key: str | None = None
) -> tuple[str | None, bool]:
    """Set additive ``result_incomplete`` + ``caveat`` on a symbol payload.

    Returns ``(caveat_text_or_None, is_truncation)``. Truncation (P0) supersedes the
    "zero callers != dead code" caveat (P7), which applies only to a resolved ``callers`` result.
    Shared by the symbol-command emitter and the blast-radius command (which has its own output).
    """
    truncation = _scan_truncation_warning(payload)
    payload["result_incomplete"] = bool(payload.get("result_incomplete")) or (
        truncation is not None
    )
    caveat = truncation
    if (
        caveat is None
        and result_key == "callers"
        and not payload.get("no_match")
        and not payload.get("callers")
    ):
        caveat = _ZERO_CALLERS_CAVEAT
    if caveat is not None:
        payload["caveat"] = caveat
    return caveat, truncation is not None


def _completeness_caveat_lines(
    caveat: str | None, *, is_truncation: bool
) -> tuple[str | None, str | None]:
    """Split a completeness caveat into ``(leading_banner, trailing_note)`` for text output.

    An INCOMPLETENESS warning must be read BEFORE the data it qualifies; an advisory note is
    commentary and reads correctly after. A trailing ``[PARTIAL]``-style marker is the easiest
    thing to emit and the most ignored -- a model consuming the text output treats the prefix
    as the document and a trailing line as a footnote, so a truncated caller-set gets trusted
    as exhaustive anyway (the exact wrong-refactor risk the exit-2 gate exists to prevent).
    The zero-callers caveat (P7) is the opposite shape: the result IS complete, the note only
    warns against over-reading it, so it stays trailing. That asymmetry is the point.

    Defined once, here, so the THREE emitters wired to it cannot drift into different orderings:
    ``_emit_symbol_command_result``, the ``blast-radius`` counts block, and
    ``_render_blast_radius_mermaid``. JSON output is unaffected: ``caveat`` is a field there, and
    field order carries no such reading bias.

    Three is the count of emitters CONVERTED to this ORDERING helper, not of emitters that
    disclose at all: the leading-banner path (``_emit_scan_incompleteness_banner``) now covers
    the payload-emitting commands -- derive the current membership from that function's call
    sites (grep ``_emit_scan_incompleteness_banner(``), never from this sentence. Commands
    still trailing their disclosure (if any) are whatever that grep does NOT reach; re-derive,
    do not enumerate here. Stated this way because two earlier enumerations here already rotted:
    one claimed ``code-map``/``route-test``/``session open``/``agent`` all trail disclosure
    (``route-test`` and ``agent`` are wired now), the other claimed
    ``map``/``context``/``context-render``/``edit-plan``/``blast-radius-render``/
    ``blast-radius-plan`` "say nothing in text at all" (all six are wired now too). An
    enumeration in prose rots the moment the set grows; a grep does not.
    """
    if caveat is None:
        return None, None
    if is_truncation:
        return f"warning: {caveat}", None
    return None, f"note: {caveat}"


def _emit_scan_incompleteness_banner(payload: dict[str, Any]) -> bool:
    """Print the leading disclosure for a payload whose SCAN did not finish. Returns whether it did.

    THE INVARIANT: a command that exits ``2`` must have SAID something on stdout. Thirteen of the
    fourteen ``_scan_incomplete`` gates in this module raised ``typer.Exit(2)`` over text output
    that read exactly like a complete result -- ``codemap`` was the only one that disclosed. An
    agent branching on the exit code was fine; every human, and every agent reading the text, was
    told a truncated answer was the whole answer.

    TEXT PATH ONLY. Call this inside the ``else`` of an ``if json_output`` block, never beside the
    ``json.dumps``: a stray line on the JSON route breaks ``json.loads`` on stdout, which is the
    failure this whole surface exists to prevent. JSON carries the same fact as ``result_incomplete``
    / ``caveat`` fields, where position and prose are irrelevant.

    LEADING, by the task #329 rule -- a disclosure must be read BEFORE the data it qualifies. Emits
    nothing for a complete payload, so existing output stays byte-identical.

    The message comes from the shared ``_scan_truncation_warning`` so these commands cannot drift
    into their own vocabulary or their own idea of which knob to suggest.
    """
    if not _scan_incomplete(payload):
        return False
    caveat = _scan_truncation_warning(payload)
    leading, _ = _completeness_caveat_lines(caveat, is_truncation=True)
    if leading is None:
        return False
    typer.echo(leading)
    return True


def _attach_symbol_omissions(
    payload: dict[str, Any],
    *,
    command_name: str,
    path: str,
    symbol: str,
    max_tests: int | None,
    max_tokens: int | None,
    primary_field: str,
) -> None:
    """Stamp an additive, agent-facing ``omissions`` envelope (design #96 item 3).

    Mirrors ``agent_capsule.py``'s ``omissions:{token_budget, omitted_section_count,
    omitted_sections[], follow_up_reads[]}`` shape (``agent_capsule.py:2262-2267``) as a sibling
    key on defs/refs/callers/impact, summarizing what the tests-cap (``output_limit``, set by
    ``repo_map._apply_symbol_field_output_limit``) and the token budget (``token_budget``, set by
    ``repo_map._apply_symbol_token_budget``) trimmed. Unlike the capsule's follow-up reads (which
    point at a DIFFERENT command to read more source), the follow-up pointer here is
    SELF-referential: re-run this SAME command with a bigger ``--max-tests``/``--max-tokens``,
    since there is nothing else to point at. ALWAYS present (even with nothing omitted, in which
    case ``omitted_sections``/``follow_up_reads`` are simply empty) so the shape is stable at v1.

    Purely additive/descriptive: never reads or writes ``result_incomplete``/``partial``/
    ``caller_scan_limit``, so it cannot affect the scan-truncation exit-2 contract.
    """
    omitted_sections: list[dict[str, Any]] = []
    retry_argv: list[str] = ["tg", command_name, path, symbol, "--json"]
    retry_needed = False

    output_limit = payload.get("output_limit")
    if isinstance(output_limit, dict) and output_limit.get("tests_truncated"):
        omitted_sections.append({
            "section": "tests",
            "omitted_count": int(output_limit.get("omitted_tests", 0)),
            "reason": "max-tests cap",
        })
        retry_argv.extend(["--max-tests", str(output_limit.get("total_tests", 0))])
        retry_needed = True

    token_budget = payload.get("token_budget")
    if isinstance(token_budget, dict) and token_budget.get("primary_truncated"):
        omitted_sections.append({
            "section": primary_field,
            "omitted_count": int(token_budget.get("primary_omitted", 0)),
            "reason": "max-tokens budget",
        })
        retry_argv.extend(["--max-tokens", "0"])
        retry_needed = True

    follow_up_reads: list[dict[str, Any]] = []
    if retry_needed:
        follow_up_reads.append({
            "file": None,
            "symbol": symbol,
            "role": "retry-bigger-budget",
            "command": subprocess.list2cmdline(retry_argv),
            "argv": retry_argv,
        })

    payload["omissions"] = {
        "token_budget": max_tokens,
        "max_tests": max_tests,
        "omitted_section_count": len(omitted_sections),
        "omitted_sections": omitted_sections,
        "follow_up_reads": follow_up_reads,
    }


def _emit_symbol_command_result(
    payload: dict[str, Any],
    *,
    result_key: str,
    json_output: bool,
    emit_text: Callable[[dict[str, Any]], None],
) -> None:
    """Emit a symbol-command payload and honor the no-match exit convention (L1).

    When the symbol resolved to zero results we annotate the payload with
    ``not_found: true`` (additive JSON field) and exit 1, mirroring how ``rg`` exits 1
    on no match, while still emitting a valid JSON object for ``--json`` consumers.

    Two additive completeness signals are surfaced in BOTH the JSON and the default text
    output so an incomplete answer can never look complete (validated on real repos):

    * ``result_incomplete`` (+ a loud ``caveat``) when the scan was truncated before covering
      the project — the dangerous "confident false zero" (P0).
    * for ``callers``, the "zero callers != dead code" caveat (P7) when a symbol resolved but
      has no callers on a complete scan — dynamic dispatch / tests / re-exports stay invisible.

    The truncation warning supersedes the generic caveat (incompleteness is the real story), and
    leads the text output rather than trailing it (see ``_completeness_caveat_lines``).
    """
    not_found = _symbol_not_found_claim(payload, result_key)
    payload["not_found"] = not_found
    caveat, is_truncation = _annotate_result_completeness(payload, result_key=result_key)
    # FAIL-CLOSED COUPLING between the message and the exit code below.
    #
    # The exit gate fires on `partial` / `result_incomplete`. The caveat above is computed by
    # `_annotate_result_completeness` from a DIFFERENT set of signals, so a payload that arrives
    # with `result_incomplete` ALREADY SET by the command itself produced no caveat at all.
    # Measured on `TENSOR_GREP_MAX_PARSE_BYTES=10 tg imports <file>`: exit 2, stdout
    # `imports=0 resolved=0 external=0 unresolved=0`, and ZERO bytes of stderr, while the `--json`
    # arm carried `result_incomplete: true` plus the real reason. A confident false zero with no
    # signal on the text path is precisely the failure this surface exists to prevent, and it was
    # invisible to the never-silent ratchet because that keys on `_scan_incomplete`, which
    # deliberately does not read `result_incomplete`.
    #
    # Deriving the fallback from the SAME predicate as the exit makes the two impossible to
    # disagree by construction, rather than by two lists staying in sync. Exit behaviour is
    # untouched -- this only guarantees an exit is never silent.
    if caveat is None and (payload.get("partial") or payload.get("result_incomplete")):
        reason = payload.get("incomplete_reason")
        caveat = _truncation_message(
            str(reason) if reason else "the result is incomplete and may be missing entries"
        )
        is_truncation = True
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)
        if leading is not None:
            typer.echo(leading)
        emit_text(payload)
        if trailing is not None:
            typer.echo(trailing)
    # Exit-code contract (council-verified B, 2026-07-05): a deadline/scan-truncated result is INCOMPLETE
    # and must NOT read as complete (0) nor as a genuine not-found (1). Exit 2 -- REGARDLESS of whether
    # results were found -- mirrors `tg search`'s result_incomplete convention (see the search command) so
    # an agent sees ONE contract across every command, never trusts a truncated caller-set as exhaustive
    # (a wrong blast-radius/refactor decision), and can distinguish "ran out of budget/cap, retry with
    # more" from "genuinely absent". A found-but-truncated result exiting 0 was tried (#399) and overturned
    # by a UNANIMOUS design council: truncation trumps found. The "every big-repo query exits 2" friction
    # is a DEFAULT-CAP miscalibration (512, entangled with the slow TS caller re-parse), to fix separately
    # -- NOT a reason to fork the contract in two. `--deadline` sets `partial`; a --max-repo-files cap sets
    # `result_incomplete`; either -> exit 2.
    if payload.get("partial") or payload.get("result_incomplete"):
        raise typer.Exit(2)
    if not_found:
        raise typer.Exit(1)


def _maybe_swap_reversed_positionals(
    *,
    path: str,
    value: str,
    command_name: str,
    value_label: str,
) -> tuple[str, str]:
    """Auto-correct a reversed ``<VALUE> <PATH>`` invocation.

    Agents (and grep muscle memory, and older docs) frequently call these
    commands as ``tg <command> <SYMBOL> <PATH>`` instead of the canonical
    path-first ``tg <command> <PATH> <SYMBOL>``. When that happens the first
    positional is not an existing path but the second one is, which previously
    produced an opaque ``Path not found: <SYMBOL>`` error. Detect that exact
    case and transparently swap, emitting a hint so the caller can learn the
    canonical order. The swap only fires when the first arg is definitively not
    a path AND the second arg definitively is, so a legitimate ``<PATH>
    <VALUE>`` call (where the value happens to share a name with a real path)
    is never disturbed.
    """
    if Path(path).expanduser().exists():
        return path, value
    if not Path(value).expanduser().exists():
        return path, value
    typer.echo(
        f"Warning: '{path}' is not an existing path but '{value}' is; "
        f"interpreting as `tg {command_name} <PATH> <{value_label}>` "
        f"(path={value!r}, {value_label.lower()}={path!r}). "
        f"Pass <PATH> before <{value_label}> to silence this hint.",
        err=True,
    )
    return value, path


def _maybe_swap_reversed_session_path(
    *,
    session_id: str,
    path: str,
    command_name: str,
) -> tuple[str, str]:
    """Auto-correct ``tg session <command> <PATH> <SESSION_ID> ...``.

    Session commands are the one user-facing surface where the stable session
    identifier must lead the path. Agents commonly transpose this after using
    the path-first top-level commands. Only swap when the first positional is
    an existing path and the second positional resolves to an existing session
    under that path, so ordinary session-first calls remain untouched.
    """
    if not Path(session_id).expanduser().exists():
        return session_id, path
    if Path(path).expanduser().exists():
        return session_id, path
    try:
        from tensor_grep.cli.session_store import get_session

        get_session(path, session_id)
    except Exception:
        return session_id, path
    typer.echo(
        f"Warning: '{session_id}' is an existing path and '{path}' is an existing "
        f"session for it; interpreting as `tg session {command_name} <SESSION_ID> "
        f"<PATH> <QUERY>`. Pass <SESSION_ID> before <PATH> to silence this hint.",
        err=True,
    )
    return path, session_id


def _resolve_path_and_symbol(
    *,
    path: str,
    symbol_arg: str | None,
    symbol_option: str | None,
    command_name: str,
) -> tuple[str, str]:
    if symbol_arg is not None and symbol_option is not None:
        raise ValueError("Use either positional SYMBOL or --symbol, not both.")
    if symbol_option is not None:
        typer.echo(
            "Warning: --symbol is deprecated for "
            f"tg {command_name}; pass SYMBOL as a positional instead "
            f"(shorthand `tg {command_name} <SYMBOL>` with PATH defaulting to '.', or "
            f"`tg {command_name} <PATH> <SYMBOL>` to scope a large repo). "
            "The --symbol form remains accepted for backward compatibility.",
            err=True,
        )
        return path, symbol_option
    if symbol_arg is not None:
        return _maybe_swap_reversed_positionals(
            path=path,
            value=symbol_arg,
            command_name=command_name,
            value_label="SYMBOL",
        )
    if path != "." and not Path(path).expanduser().exists():
        return ".", path
    raise ValueError("Missing symbol. Use positional SYMBOL or --symbol SYMBOL.")


def _resolve_path_and_query(
    *,
    path: str,
    query_arg: str | None,
    query_option: str | None,
    command_name: str,
) -> tuple[str, str]:
    if query_arg is not None and query_option is not None:
        raise ValueError("Use either positional QUERY or --query, not both.")
    if query_option is not None:
        typer.echo(
            "Warning: --query is deprecated for "
            f"tg {command_name}; use a positional QUERY form instead. "
            "The --query form remains accepted during the 1.13.x deprecation cycle "
            "and will not be removed before 1.14.0.",
            err=True,
        )
        return path, query_option
    if query_arg is not None:
        return _maybe_swap_reversed_positionals(
            path=path,
            value=query_arg,
            command_name=command_name,
            value_label="QUERY",
        )
    if path != "." and not Path(path).expanduser().exists():
        return ".", path
    raise ValueError("Missing query. Use positional QUERY or --query QUERY.")


@app.command()
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
        # CLI consistency fix (CEO v1.71.3 dogfood): `--deadline` used to be undefined on `tg defs`
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
        raise typer.Exit(1) from exc

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
    )


@app.command()
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
        # CEO v1.72.1 dogfood M1: `--deadline` used to be undefined on `tg source` (Click "No such
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
        raise typer.Exit(1) from exc

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Source for {current['symbol']} in {current['path']}")
        typer.echo(f"sources={len(current['sources'])} files={len(current['files'])}")

    _emit_symbol_command_result(
        payload,
        result_key="sources",
        json_output=json_output,
        emit_text=_emit_text,
    )


@app.command()
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
        raise typer.Exit(1) from exc

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
    )


@app.command()
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
        raise typer.Exit(1) from exc

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
    )


@app.command()
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
        raise typer.Exit(1) from exc

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
    )


@app.command()
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

    CEO v1.72.1 dogfood M1: `--deadline` is accepted as a documented NO-OP for command-surface
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


@app.command()
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


def _mermaid_label(text: str) -> str:
    """Neutralize characters that would break a quoted Mermaid node/edge label."""
    return text.replace("\\", "/").replace('"', "'")


_MERMAID_INCOMPLETE_LABEL_LIMIT = 110


def _mermaid_incomplete_label(banner: str) -> str:
    """The disclosure banner reduced to something a DIAGRAM BOX can carry.

    A node label is read in a picture, not a terminal, so it needs the CAUSE and nothing else:
    the ``warning:`` prefix is a log convention that means nothing inside a box, and the remedy
    sentence is long enough to distort the graph it is warning about. Both stay one line up in
    the ``%%`` comment, which is where a source reader looks and where the full text belongs.

    Bounded deliberately. The cause clause is producer-controlled (a caller-scan message names two
    counts and a path-narrowing hint) and an unbounded label would let the worst-truncated graph
    render worst -- the incompleteness signal degrading exactly when it matters most, which is the
    defect inverted. Yes, this truncates a truncation notice; the ellipsis says so, and the
    untruncated text is one line above.
    """
    cause = " ".join(banner.split())
    for prefix in ("warning: ", "note: "):
        if cause.startswith(prefix):
            cause = cause[len(prefix) :]
            break
    cause = cause.split(", so ", 1)[0].split(". ", 1)[0]
    if len(cause) > _MERMAID_INCOMPLETE_LABEL_LIMIT:
        cause = cause[: _MERMAID_INCOMPLETE_LABEL_LIMIT - 3].rstrip() + "..."
    return cause


def _mermaid_relpath(file_path: str, root: str) -> str:
    """A short forward-slashed path for a Mermaid node (relative to root when it stays inside)."""
    forward = file_path.replace("\\", "/")
    try:
        rel = os.path.relpath(file_path, root).replace("\\", "/")
        if rel and not rel.startswith(".."):
            return rel
    except (ValueError, OSError):
        pass
    return os.path.basename(forward) or forward


def _render_blast_radius_mermaid(payload: dict[str, Any]) -> str:
    """Render a blast-radius payload's exact call sites (``callers[]``) as a Mermaid ``graph TD``.

    Only DIRECT callers are drawn (each unique caller file --> the symbol), because they carry
    exact file+line evidence. The depth-layered ``caller_tree`` has no exact file-to-file edges,
    so inventing them would lie to the reader (the agent-native contract). Output is deterministic
    (sorted nodes) so it is diff-friendly for doc generators.
    """
    symbol = str(payload.get("symbol", "symbol"))
    root = str(payload.get("path") or ".")
    callers = cast(list[dict[str, Any]], payload.get("callers") or [])
    grouped: dict[str, list[int]] = {}
    for caller in callers:
        raw = caller.get("file")
        if not raw:
            continue
        entry = grouped.setdefault(_mermaid_relpath(str(raw), root), [])
        line_no = caller.get("line")
        if isinstance(line_no, int):
            entry.append(line_no)
    lines = ["graph TD"]
    # Task #329's law, crossed to this twin: a truncation disclosure must be read BEFORE the data
    # it qualifies. A `%%` note after the nodes is a footnote -- a reader who has already traced
    # the graph formed the answer several lines ago. `graph TD` is the diagram-type DECLARATION,
    # not payload, so it stays line 1 and the banner becomes the first CONTENT line. (An existing
    # contract test already pins `payload["mermaid"].startswith("graph TD")`, so this is also the
    # only option that does not break a shipped promise.) What keeps the line from reading as
    # mermaid's `%%{...}%%` DIRECTIVE form is the space AFTER `%%`, guaranteed by the `warning: `
    # prefix `_completeness_caveat_lines` always emits -- not the indentation, which is cosmetic.
    #
    # The text comes from the shared _scan_truncation_warning/_completeness_caveat_lines pair
    # rather than a hardcoded literal, which fixes two further defects the old line carried: it
    # said `note:` for a TRUNCATION (inverting the warning-vs-advisory split this command defines
    # one function above), and it advised "raise --max-callers/--max-files" for EVERY cause --
    # naming the only two knobs that cannot lift a --max-repo-files scan cap or a caller-scan
    # ceiling. Wrong-knob remediation advice is the failure #762 fixed on the MCP surface.
    truncation = _scan_truncation_warning(payload)
    if truncation is None and payload.get("result_incomplete"):
        # An incompleteness stamped upstream that carries no scan_limit/output_limit of its own
        # still owes the reader a disclosure; falling through silently would trade a MISPOSITIONED
        # warning for an ABSENT one, which is the worse half of this same class.
        truncation = _truncation_message("the result was truncated")
    leading, _ = _completeness_caveat_lines(truncation, is_truncation=truncation is not None)
    if leading is not None:
        # Flattened because a `%%` comment ends at the newline: an embedded one would close the
        # comment and turn the remainder into live graph statements. The deleted literal was a
        # fixed string and could not carry one; this line interpolates payload-derived values
        # (`scan_limit.max_repo_files`, `caller_scan_limit.ceiling`, the `output_limit` counts).
        # Every COLD path types those as int, so no reachable injection was found -- but the
        # warm/daemon payload (`_maybe_symbol_command_via_running_daemon`) is parsed JSON with no
        # field typing, so this is cheap fail-closed hardening rather than a proven vector, and
        # `_mermaid_label` already sanitizes node text on exactly this reasoning.
        lines.append(f"  %% {' '.join(leading.split())}")
        # AND a real NODE, because the comment above is invisible in a RENDERED diagram.
        # Mermaid's own flowchart docs say comments "will be ignored by the parser", so `%%`
        # serves the agent reading the source and nobody looking at the picture -- who is
        # precisely the reader most likely to trust a caller graph at a glance. Verified against
        # the upstream docs rather than assumed; the earlier fix that moved this disclosure to
        # LEAD improved the source-reading case only.
        #
        # Declared with no edge, so `-->` counts and the "no invented edges" guard are untouched;
        # emitted only when incomplete, so a complete graph stays byte-identical. The node carries
        # the CAUSE clause (up to the first sentence break) rather than the full text -- the
        # remedy sentence is long enough to distort a diagram, and it remains one line up in the
        # comment for whoever is reading the source.
        lines.append(f'  tg_incomplete["{_mermaid_label(_mermaid_incomplete_label(leading))}"]')
    lines.append(f'  target["{_mermaid_label(symbol)}"]')
    for idx, rel in enumerate(sorted(grouped)):
        node = f"n{idx}"
        lines.append(f'  {node}["{_mermaid_label(rel)}"]')
        call_lines = sorted(grouped[rel])
        if len(call_lines) == 1:
            lines.append(f"  {node} -->|L{call_lines[0]}| target")
        elif call_lines:
            lines.append(f"  {node} -->|{len(call_lines)} calls| target")
        else:
            lines.append(f"  {node} --> target")
    if not grouped:
        # Deliberately still TRAILING, and not swept into the leading banner above: this is the
        # advisory half of the split. The scan COMPLETED and genuinely found nothing, so the line
        # is commentary on a trustworthy result rather than a qualifier on an untrustworthy one.
        lines.append(f"  %% no callers found for {symbol}")
    return "\n".join(lines)


def _daemon_blast_radius_no_match_is_unreliable(payload: dict[str, Any]) -> bool:
    """audit #107 (#94 flip blocker): True iff a warm/daemon blast_radius payload is a no_match on
    a possibly_truncated map -- the one case where the daemon-served
    build_symbol_blast_radius_from_map (repo_map.py, no literal-seed rescue) can disagree with
    what the cold build_symbol_blast_radius (repo_map.py, which DOES retry via
    _literal_symbol_seed_files) would find. The symbol may simply sit outside the daemon
    session's scan window, so a no_match here is unreliable and the caller should fall through to
    cold instead of trusting it.

    Deliberately narrow: only fires on no_match AND possibly_truncated together. A warm no_match
    on a COMPLETE map is a real miss -- falling back to cold there would defeat the daemon
    speedup for every genuine no-match, not just the truncated-and-wrong ones.

    Task #108: delegates to repo_map._blast_radius_no_match_is_possibly_truncated, the ONE shared
    definition of this condition (also used by build_symbol_blast_radius's own literal-seed-rescue
    trigger and the Tier-2 daemon agent-capsule's call-site-evidence collector) so all three arms
    agree on exactly when a no_match is trustworthy instead of drifting independently.
    """
    from tensor_grep.cli.repo_map import _blast_radius_no_match_is_possibly_truncated

    return _blast_radius_no_match_is_possibly_truncated(payload)


@app.command(name="blast-radius")
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
        raise typer.Exit(1) from exc

    # Honor rg's no-match exit convention (audit #12): a typo'd/nonexistent symbol previously exited
    # 0 with an empty callers list -- on a refactor-safety command that reads as "resolved, zero
    # impact" instead of "never found". Compute + stamp BEFORE any output path (mirrors
    # _emit_symbol_command_result) so json/text/mermaid all see the same additive `not_found` field.
    not_found = _symbol_not_found_claim(payload, "callers")
    payload["not_found"] = not_found
    # Annotate completeness BEFORE any output path so mermaid/json/text all see result_incomplete and
    # honor the shared exit contract (cursor review 1.40.0): a --deadline partial or output-cap
    # truncation must exit 2, never a silent exit 0 that reads as complete. (The mermaid renderer also
    # reads payload.result_incomplete for its `%% truncated` comment.)
    caveat, is_truncation = _annotate_result_completeness(payload, result_key="callers")
    # Exit 2 ONLY for SCAN incompleteness (--deadline partial, or a --max-repo-files scan cap) -- the
    # analysis didn't finish. An OUTPUT cap (--max-callers/--max-files) is a COMPLETE analysis with a
    # capped display (callers_truncated/files_truncated) and stays exit 0: the agent raises the cap for
    # more. So gate on scan-truncation, NOT result_incomplete (which _annotate also sets on output cap).
    # A SCAN-truncated blast radius is INCOMPLETE regardless of whether callers were found -> exit 2
    # (council-verified B, 2026-07-05; found-but-truncated->0 was tried in #399 and overturned). A
    # truncated caller-set silently trusted as exhaustive is exactly the wrong-refactor risk this gate
    # exists to prevent. `caller_scan_truncated` = the backlog-#1 caller-scan ceiling
    # (CALLER_SCAN_FILE_CEILING) dropped files the 2000-map covers -> a SCAN truncation (exit 2),
    # distinct from an output cap. Without this the ceiling would silently exit 0 with a caller-set
    # truncated at 512 (Fable final review of #405). `_scan_incomplete` is the shared gate reused by
    # every daemon/render fast-path (map, context-render, edit-plan, blast-radius-render; Cluster B,
    # 2026-07-06) so the scan-vs-output-cap contract is defined exactly once.
    incomplete = _scan_incomplete(payload)

    if mermaid_output and json_output:
        # task #164: `--json --mermaid` together used to let mermaid short-circuit json (an
        # agent asking for both got only the human diagram and a `json.loads` on stdout raised).
        # Embed, don't refuse: fold the rendered mermaid text into the JSON payload so one call
        # returns both the machine graph and the diagram.
        payload["mermaid"] = _render_blast_radius_mermaid(payload)
        typer.echo(json.dumps(payload, indent=2))
    elif mermaid_output:
        typer.echo(_render_blast_radius_mermaid(payload))
    elif json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        # Same leading-banner-vs-trailing-note split as _emit_symbol_command_result: a truncation
        # warning has to be read BEFORE the counts it qualifies (an agent reading
        # `callers=3` first has already formed the answer by the time a trailing line lands).
        leading, trailing = _completeness_caveat_lines(caveat, is_truncation=is_truncation)
        if leading is not None:
            typer.echo(leading)
        typer.echo(f"Blast radius for {payload['symbol']} in {payload['path']}")
        typer.echo(
            f"definitions={len(payload['definitions'])} callers={len(payload['callers'])} "
            f"files={len(payload['files'])} tests={len(payload['tests'])} "
            f"import_consumers={len(payload.get('import_graph_consumers', []))}"
        )
        if trailing is not None:
            typer.echo(trailing)

    # Exit-order: a SCAN truncation (2) always wins over a genuine no-match (1) -- a truncated scan
    # never had the chance to find the symbol, so "not found" is not yet a trustworthy answer.
    if incomplete:
        raise typer.Exit(2)
    if not_found:
        raise typer.Exit(1)


@app.command(name="blast-radius-render")
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
        help="Render profile: full, compact, or llm. Defaults to llm for JSON and full for text.",
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
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready blast-radius bundle for a symbol.

    Emits PROSE for pasting into a prompt. For the machine-readable caller graph
    (callers/caller_tree/affected_files/blast_radius_score), use
    `tg blast-radius SYMBOL --json` instead -- it is faster and agent-consumable.
    """
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
        raise typer.Exit(1) from exc

    # Cold path (Cluster B, 2026-07-06): build the payload once and dump it here (byte-identical to
    # the old build_symbol_blast_radius_render_json helper: json.dumps(payload, indent=2)) so both
    # json and text branches share the same scan-truncation gate below -- output the full payload
    # FIRST, then exit 2 if the scan itself (not just the output) was capped.
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        _emit_scan_incompleteness_banner(payload)
        typer.echo(payload["rendered_context"])

    if _scan_incomplete(payload):
        raise typer.Exit(2)


@app.command(name="blast-radius-plan")
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
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a machine-readable blast-radius planning bundle without rendered source text.

    Like `blast-radius --json` but shaped as an edit/action plan (no source snippets).
    """
    from tensor_grep.cli.repo_map import build_symbol_blast_radius_plan

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="blast-radius-plan",
        )
        # CEO v1.72.1 dogfood M1: `--deadline` used to be undefined on `tg blast-radius-plan`
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
        raise typer.Exit(1) from exc

    # F14 (Fable audit MED): output the payload FIRST, then gate on the shared _scan_incomplete
    # contract -- mirrors blast-radius/map/context-render/edit-plan/blast-radius-render (Cluster B,
    # 2026-07-06). This payload is built from build_symbol_blast_radius_from_map and carries the
    # exact scan_limit/caller_scan_truncated markers the gate checks; without this, a scan-truncated
    # plan exited 0 while the sibling `blast-radius` command exits 2 on identical truncation.
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        _emit_scan_incompleteness_banner(payload)
        typer.echo(f"Blast radius plan for {payload['symbol']} in {payload['path']}")
        typer.echo(
            f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
        )

    if _scan_incomplete(payload):
        raise typer.Exit(2)


@app.command(name="diff-impact")
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


@session_app.command("open")
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
        raise typer.Exit(1) from exc

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


@session_daemon_app.command("start")
def session_daemon_start(
    path: str = typer.Argument(".", help="File or directory rooted at the daemon scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Start or reuse a warm localhost session daemon for the current root."""
    from tensor_grep.cli.session_daemon import start_session_daemon

    try:
        payload = start_session_daemon(path)
    except Exception as exc:
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


@session_daemon_app.command("status")
def session_daemon_status(
    path: str = typer.Argument(".", help="File or directory rooted at the daemon scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Show daemon status for the current root."""
    from tensor_grep.cli.session_daemon import get_session_daemon_status

    try:
        payload = get_session_daemon_status(path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    if payload.get("running"):
        typer.echo(
            f"Session daemon running on {payload['host']}:{payload['port']} pid={payload['pid']}"
        )
        if payload.get("response_cache_scope"):
            typer.echo(f"response_cache_scope={payload['response_cache_scope']}")
    else:
        typer.echo("Session daemon not running")


@session_daemon_app.command("stop")
def session_daemon_stop(
    path: str = typer.Argument(".", help="File or directory rooted at the daemon scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Stop the warm localhost session daemon for the current root."""
    from tensor_grep.cli.session_daemon import stop_session_daemon

    try:
        payload = stop_session_daemon(path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo("Session daemon stopped" if payload.get("stopped") else "Session daemon not running")


@session_app.command("list")
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


@session_app.command("show")
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


@session_app.command("refresh")
def session_refresh(
    session_id: str = typer.Argument(..., help="Session ID to refresh."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Refresh a cached session after file changes."""
    from tensor_grep.cli.session_store import refresh_session

    try:
        payload = refresh_session(session_id, path)
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


@session_app.command("context")
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


@session_app.command("context-render")
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
        help="Render profile: full, compact, or llm. Defaults to llm for JSON and full for text.",
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


@session_app.command("edit-plan")
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


@session_app.command("blast-radius")
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


@session_app.command("importers")
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


@session_app.command("blast-radius-render")
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
        help="Render profile: full, compact, or llm.",
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


@session_app.command("blast-radius-plan")
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


@session_app.command("serve")
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


@checkpoint_app.command("create")
def checkpoint_create(
    path: str = typer.Argument(".", help="File or directory rooted at the checkpoint scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Create a checkpoint for the current editable tree."""
    from tensor_grep.cli.checkpoint_store import create_checkpoint

    try:
        payload = create_checkpoint(path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload.__dict__, version=1), indent=2))
        return

    typer.echo(
        f"Created checkpoint {payload.checkpoint_id} ({payload.mode}, files={payload.file_count})"
    )
    typer.echo(f"Undo command: {payload.undo_command}")


@checkpoint_app.command("list")
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
                typer.echo(
                    f"  {record['checkpoint_id']}  {record['mode']}  "
                    f"{record['created_at']}  files={record['file_count']}"
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
        typer.echo(
            f"{record['checkpoint_id']}  {record['mode']}  "
            f"{record['created_at']}  files={record['file_count']}"
        )


@checkpoint_app.command("undo")
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
        CheckpointUndoUnsafeError,
        resolve_latest_checkpoint,
        undo_checkpoint,
    )

    if path == "--json":
        json_output = True
        path = "."

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
        # This handler once labelled EVERY undo failure `checkpoint_not_found`. Two independent
        # fixes corrected that, and both are folded in here:
        #   #297 -- an undo ABORTED because it could not be reverted safely. The checkpoint was
        #     found and is perfectly good; the working tree is what blocked it. Reporting
        #     not-found sent the reader hunting a missing checkpoint instead of at the unreadable
        #     file named in `detail`.
        #   #298 -- a CORRUPT checkpoint, where the record was found perfectly well but its
        #     snapshot blobs are missing or unreadable. Reporting not-found hid the only fact
        #     that matters: the snapshot cannot restore your tree.
        # #297 deliberately left the corrupt case alone ("a JSON-contract change, filed
        # separately rather than smuggled into a data-loss fix") -- that separate filing is #298,
        # and this is where the two meet. `tests/unit/test_checkpoint_atomic_undo.py` describes
        # the mislabel as the symptom of the OLD buggy shape, so until now the code contradicted
        # its own test's prose.
        error_code = "checkpoint_not_found"
        if isinstance(exc, CheckpointCorruptError):
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


@app.command()
def classify(
    file_path: str | None = typer.Argument(
        None, help="The log file to classify (omit when using --stdin or --text)."
    ),
    format_type: str = typer.Option("json", "--format", help="Output format"),
    max_lines: int = typer.Option(
        DEFAULT_CLASSIFY_MAX_LINES,
        "--max-lines",
        help="Maximum input lines to emit in JSON output (0 disables the cap).",
    ),
    stdin_flag: bool = typer.Option(
        False,
        "--stdin",
        help="Read the text to classify from stdin instead of a file "
        "(mutually exclusive with --text).",
    ),
    text: str | None = typer.Option(
        None,
        "--text",
        help="Classify a literal string instead of a file or stdin "
        "(mutually exclusive with --stdin).",
    ),
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

    if format_type == "json":
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
        for r in results:
            print(f"{r['label']} ({r['confidence']:.2f})")


@app.command()
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


@app.command(name="audit")
def audit_help() -> None:
    """Audit command entry points: audit-verify, audit-history, audit-diff, review-bundle."""
    typer.echo("Audit commands:")
    typer.echo("  tg audit-verify MANIFEST [--json]")
    typer.echo("  tg audit-history [PATH] [--json]")
    typer.echo("  tg audit-diff PREVIOUS CURRENT [--json]")
    typer.echo("  tg review-bundle create --manifest MANIFEST [--json]")
    typer.echo("  tg review-bundle verify BUNDLE [--json]")


@app.command()
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
) -> None:
    """Scan code with tensor-grep's bounded AST rule/config surface."""
    from tensor_grep.backends.ast_backend import normalize_ast_language
    from tensor_grep.cli.rule_packs import resolve_rule_pack
    from tensor_grep.cli.scan_guardrails import BroadScanRefusedError

    inline_source_count = sum(item is not None for item in (ruleset, inline_rules, rule_file))
    if inline_source_count > 1:
        typer.echo("Error: --rule, --inline-rules, and --ruleset are mutually exclusive.", err=True)
        sys.exit(1)
    if rule_file is not None and filter_regex is not None:
        typer.echo("Error: --filter is incompatible with --rule.", err=True)
        sys.exit(1)
    scan_paths = list(paths or [])
    if scan_paths and path != ".":
        typer.echo("Error: positional PATHS are incompatible with --path.", err=True)
        sys.exit(1)
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
    if ruleset:
        ruleset_language = normalize_ast_language(language) if language is not None else None
        try:
            ruleset_meta, rules = resolve_rule_pack(ruleset, ruleset_language)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        try:
            # --filter was previously honored only for the sgconfig project-scan path (below) and
            # explicitly rejected for --rule -- silently no-op'd here, so a --ruleset run always
            # scanned every rule in the pack regardless of --filter (audit #22).
            rules = _filter_ast_rule_specs(rules, filter_regex)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        project_cfg: dict[str, object] = {
            "config_path": f"builtin:{ruleset_meta['name']}",
            "root_dir": Path(effective_scan_paths[0]).resolve(),
            "rule_dirs": [],
            "test_dirs": [],
            "language": ruleset_meta["language"],
        }
        scan_banner = (
            "Scanning project using built-in ruleset "
            f"{ruleset_meta['name']} ({ruleset_meta['language']})"
        )
        routing_reason = "builtin-ruleset-scan"
    elif rule_file is not None:
        rule_path = Path(rule_file).expanduser().resolve()
        try:
            rules = _load_inline_rule_specs(
                rule_path.read_text(encoding="utf-8"),
                default_language=language,
            )
        except OSError as exc:
            typer.echo(f"Error: failed to read rule file {rule_path}: {exc}", err=True)
            sys.exit(1)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        if not rules:
            typer.echo(f"Error: No valid rule was found in {rule_path}.", err=True)
            sys.exit(1)
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
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        try:
            # Same uniform --filter application as --ruleset above (audit #22): previously silently
            # ignored here, so a --inline-rules run always scanned every parsed rule regardless of
            # --filter.
            rules = _filter_ast_rule_specs(rules, filter_regex)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        if not rules:
            typer.echo("Error: No valid inline rules were found.", err=True)
            sys.exit(1)
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
        except (FileNotFoundError, ValueError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        try:
            rules = _filter_ast_rule_specs(rules, filter_regex)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)

        if not rules:
            typer.echo(
                "Error: No valid rules found after applying configuration and filters.",
                err=True,
            )
            sys.exit(1)
        scan_banner = "Scanning project using adaptive AST routing"
        routing_reason = "ast-project-scan"
        project_scan_fast_path = True
        if scan_paths:
            project_scan_fast_path = False

    if not json_output and not sarif:
        typer.echo(f"{scan_banner} based on {project_cfg['config_path']}...")
    try:
        payload = _self._run_ast_scan_payload(
            project_cfg,
            rules,
            routing_reason=routing_reason,
            scan_paths=scan_paths or None,
            candidate_files=candidate_files,
            project_scan_fast_path=project_scan_fast_path,
            ruleset_name=ruleset_meta["name"] if ruleset else None,
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
        typer.echo(str(exc), err=True)
        sys.exit(2)
    except (ValueError, RuntimeError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        sys.exit(1)
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
        return

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    # Task #299 gave `scan` a `partial` / `partial_reason` / `remediation` / `unreadable_paths`
    # payload, and #310's SARIF output carries it in-band -- but the DEFAULT text renderer read
    # none of them. Measured on the shipped v1.101.4 against an ACL-denied fixture (denial
    # asserted to bite first): `--json` reported `partial: true` with a 2-path `unreadable_paths`
    # sample, while the same invocation's stdout printed `Scan completed. total_matches=2` and
    # exited 0 -- silent about two secrets in a file no rule ever opened. On a SECURITY ruleset
    # that is the worst shape in the product: an unread file is indistinguishable from a clean one.
    #
    # LEADING, not trailing. `codemap` is the sibling that already reads `remediation`, but it
    # prints its `PARTIAL:` line AFTER the counts -- that ordering is exactly the task #329 defect
    # and is not the half of the precedent worth copying. A disclosure must be read BEFORE the
    # total it qualifies, or the reader has already formed the answer.
    #
    # The exit code deliberately stays 0; see the SARIF block above. `tg scan` has returned 0 on a
    # disclosed partial since #299, and flipping it is a contract change with its own consumers,
    # not a drive-by on a rendering fix.
    if payload.get("partial"):
        remediation = str(payload.get("remediation") or "").strip()
        typer.echo(
            f"warning: INCOMPLETE SCAN: {remediation}"
            if remediation
            else "warning: INCOMPLETE SCAN: part of the scope could not be read, so this result "
            "does NOT prove those files are clean."
        )

    for finding in cast(list[dict[str, object]], payload["findings"]):
        typer.echo(
            f"[scan] rule={finding['rule_id']} lang={finding['language']} "
            f"matches={finding['matches']} files={len(cast(list[str], finding['files']))}"
        )

    typer.echo(
        "Scan completed. "
        f"rules={payload['rule_count']} matched_rules={payload['matched_rules']} "
        f"total_matches={payload['total_matches']} "
        f"backends={','.join(cast(list[str], payload['backends'])) or 'none'}"
    )
    if payload.get("baseline"):
        baseline_summary = cast(dict[str, object], payload["baseline"])
        typer.echo(
            "Baseline compared. "
            f"new={baseline_summary['new_findings']} "
            f"existing={baseline_summary['existing_findings']} "
            f"resolved={baseline_summary['resolved_findings']}"
        )
    if payload.get("baseline_written"):
        baseline_written = cast(dict[str, object], payload["baseline_written"])
        typer.echo(
            f"Baseline written to {baseline_written['path']} (count={baseline_written['count']})."
        )
    if payload.get("suppressions"):
        suppressions_summary = cast(dict[str, object], payload["suppressions"])
        if suppressions_summary.get("path"):
            typer.echo(
                f"Suppressions applied from {suppressions_summary['path']} "
                f"(suppressed={suppressions_summary['suppressed_findings']})."
            )
        if suppressions_summary.get("inline_suppressed_findings"):
            typer.echo(
                "Inline suppressions applied "
                f"(suppressed={suppressions_summary['inline_suppressed_findings']})."
            )
        for warning in cast(list[str], suppressions_summary.get("warnings", [])):
            typer.echo(f"Warning: {warning}", err=True)
    if payload.get("suppressions_written"):
        suppressions_written = cast(dict[str, object], payload["suppressions_written"])
        typer.echo(
            f"Suppressions written to {suppressions_written['path']} "
            f"(count={suppressions_written['count']})."
        )


@app.command()
def test(
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config"
    ),
) -> None:
    """Test structural rules in tensor-grep's bounded AST workflow slice."""
    from tensor_grep.cli import ast_workflows

    exit_code = ast_workflows.test_command(config)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


def _validate_ast_new_name(name: str) -> None:
    if not name.strip() or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"Invalid item name {name!r}; use a bare scaffold identifier.")


def _write_ast_project_scaffold(base_dir: Path, lang: str) -> Path:
    import yaml

    config_path = base_dir / "sgconfig.yml"
    if config_path.exists():
        raise FileExistsError(f"Project already initialized ({config_path} exists).")

    config_data = {
        "ruleDirs": ["rules"],
        "testDirs": ["tests"],
        "utilsDir": "utils",
        "language": lang,
    }

    base_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes_anchored(
        config_path, yaml.dump(config_data).encode("utf-8"), mode=0o644, replace=False
    )

    rules_dir = base_dir / "rules"
    tests_dir = base_dir / "tests"
    rules_dir.mkdir(exist_ok=True)
    tests_dir.mkdir(exist_ok=True)
    atomic_write_bytes_anchored(
        rules_dir / "sample-rule.yml",
        f"id: sample-rule\nlanguage: {lang}\nrule:\n  pattern: 'print($$$ARGS)'\n".encode(),
        mode=0o644,
        replace=False,
    )
    atomic_write_bytes_anchored(
        tests_dir / "sample-test.yml",
        b'id: sample-test\nruleId: sample-rule\nvalid:\n  - "pass"\ninvalid:\n'
        b'  - "print(\\"hello\\")"\n',
        mode=0o644,
        replace=False,
    )
    return config_path


@app.command()
def new(
    command: str | None = typer.Argument(
        None,
        help="Scaffold kind for tensor-grep's bounded AST workflow: project, rule, test, or util.",
    ),
    name: str | None = typer.Argument(None, help="Name for project/rule/test/util scaffolds."),
    lang: str = typer.Option("python", "--lang", "-l", help="Language for generated items."),
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
        if scaffold_kind == "project":
            project_dir = base_dir
            if name is not None:
                _validate_ast_new_name(name)
                project_dir = base_dir / name
            config_path = _write_ast_project_scaffold(project_dir, lang)
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
            project_cfg = _load_sg_project_config(config)

        if scaffold_kind == "rule":
            target_dir = (
                cast(Path, project_cfg["root_dir"]) / cast(list[str], project_cfg["rule_dirs"])[0]
                if project_cfg is not None
                else base_dir / "rules"
            )
            target_path = target_dir / f"{name}.yml"
            contents = f"id: {name}\nlanguage: {lang}\nrule:\n  pattern: ''\n"
        elif scaffold_kind == "test":
            target_dir = (
                cast(Path, project_cfg["root_dir"]) / cast(list[str], project_cfg["test_dirs"])[0]
                if project_cfg is not None
                else base_dir / "tests"
            )
            target_path = target_dir / f"{name}.yml"
            contents = f"id: {name}\nruleId: {name}\nvalid:\n  - ''\ninvalid: []\n"
        else:
            target_dir = (
                cast(Path, project_cfg["root_dir"]) / cast(str, project_cfg["utils_dir"])
                if project_cfg is not None
                else base_dir / "utils"
            )
            target_path = target_dir / f"{name}.yml"
            contents = f"id: {name}\npattern: ''\n"

        if target_path.exists():
            raise FileExistsError(f"Scaffold target already exists: {target_path}")
        target_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes_anchored(
            target_path, contents.encode("utf-8"), mode=0o644, replace=False
        )
    except (FileExistsError, ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Created {scaffold_kind} scaffold in {target_path}.")


@app.command(name="dogfood")
def dogfood(
    root: Path = typer.Option(Path("."), "--root", help="Repository root to validate."),
    output: Path | None = typer.Option(None, "--output", help="Optional JSON report path."),
    expected_version: str | None = typer.Option(
        None, "--expected-version", help="Expected tensor-grep version. Defaults to pyproject."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    progress: str = typer.Option(
        "auto",
        "--progress",
        help="Progress reporting mode: auto, always, or never. Emits to stderr only.",
    ),
    progress_interval_s: float = typer.Option(
        30.0,
        "--progress-interval-s",
        help="Seconds between progress heartbeats for the active phase.",
    ),
    timeout_s: float = typer.Option(
        170.0,
        "--timeout-s",
        help="Maximum seconds for the nested agent-readiness process before partial failure output.",
    ),
    no_shell_probes: bool = typer.Option(
        False, "--no-shell-probes", help="Skip public shell version probes."
    ),
    no_wsl_probe: bool = typer.Option(False, "--no-wsl-probe", help="Skip the optional WSL probe."),
) -> None:
    """Run the agent-readiness dogfood gate; writes only explicit --output and a sibling readiness report."""
    from tensor_grep.cli.dogfood import run_dogfood_readiness
    from tensor_grep.cli.progress import normalize_progress_mode

    try:
        progress_mode = normalize_progress_mode(progress)
        if progress_interval_s <= 0:
            raise ValueError("progress interval must be greater than 0")
        if timeout_s <= 0:
            raise ValueError("dogfood timeout must be greater than 0")
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    exit_code, report = run_dogfood_readiness(
        root=root,
        output=output,
        expected_version=expected_version,
        include_shell_probes=not no_shell_probes,
        include_wsl_probe=not no_wsl_probe,
        progress_mode=progress_mode,
        progress_interval_s=progress_interval_s,
        json_output=json_output,
        timeout_s=timeout_s,
    )
    if json_output:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = cast(dict[str, object], report["agent_readiness"]).get("summary")
        if not isinstance(summary, dict):
            summary = {}
        verdict = cast(dict[str, object], report["verdict"])
        typer.echo(f"Dogfood verdict: {verdict['status']}")
        typer.echo(
            "agent-readiness: "
            f"passed={summary.get('passed', 0)} "
            f"failed={summary.get('failed', 0)} "
            f"skipped={summary.get('skipped', 0)}"
        )
        world_class_readiness = report.get("world_class_readiness")
        if isinstance(world_class_readiness, dict):
            typer.echo(f"world-class claim: {world_class_readiness.get('status', 'unknown')}")
        if output is not None:
            typer.echo(f"report: {output}")
        failed_checks = verdict.get("failed_checks")
        if isinstance(failed_checks, list) and failed_checks:
            typer.echo("failed checks: " + ", ".join(str(check) for check in failed_checks))
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@app.command()
def lsp(
    provider: str = typer.Option(
        "native",
        "--provider",
        help=(
            "Experimental semantic provider mode. native=repo-map only, "
            "lsp=external provider only, hybrid=merge both. Invalid modes "
            "fail before the server starts."
        ),
    ),
    debug_trace_language: str | None = typer.Option(
        None,
        "--debug-trace",
        help=(
            "Run a one-shot external-provider health probe for LANGUAGE and emit "
            "JSON-RPC trace diagnostics instead of starting the tg LSP server."
        ),
    ),
    path: Path = typer.Option(
        Path("."),
        "--path",
        help="Workspace root for --debug-trace probes.",
    ),
    probe_timeout_seconds: float | None = typer.Option(
        None,
        "--probe-timeout-seconds",
        help="Override the external-provider request timeout for --debug-trace.",
    ),
) -> None:
    """Start the structural search language server.

    Examples:
      tg lsp
      tg lsp --provider native
      tg lsp --provider lsp
      tg lsp --provider hybrid
      tg lsp --debug-trace python --path .

    External LSP providers are experimental semantic evidence. Provider
    availability means the binary was found, not that initialization or
    navigation requests have succeeded.

    The provider mode is also exposed to editor clients through the
    `TG_LSP_PROVIDER` environment variable.
    """
    import os

    normalized_provider = provider.strip().lower()
    if normalized_provider not in {"native", "lsp", "hybrid"}:
        typer.echo(
            "Unsupported LSP provider mode; expected one of: native, lsp, hybrid",
            err=True,
        )
        raise typer.Exit(code=2)
    if debug_trace_language is not None:
        from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager

        payload = ExternalLSPProviderManager().provider_debug_trace(
            language=debug_trace_language,
            workspace_root=path,
            probe_timeout_seconds=probe_timeout_seconds,
        )
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        status = cast(dict[str, Any], payload.get("status", {}))
        if status.get("health_status") != "ready":
            raise typer.Exit(code=1)
        return
    # The LSP server needs the optional `ast` extra (pygls/lsprotocol). Import it lazily and
    # fail closed with an actionable message rather than leaking a ModuleNotFoundError traceback
    # (item #159). Provider validation and --debug-trace above deliberately run first: they must
    # work on a bare `pip install tensor-grep` without the extra.
    try:
        from tensor_grep.cli.lsp_server import run_lsp
    except ImportError as exc:
        typer.echo(
            "tg lsp requires the optional 'ast' extra (language-server support). "
            'Install it with: pip install "tensor-grep[ast]"',
            err=True,
        )
        raise typer.Exit(code=1) from exc
    os.environ["TG_LSP_PROVIDER"] = normalized_provider
    run_lsp()


@app.command(name="lsp-setup")
def lsp_setup(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    include_toolchain_providers: bool = typer.Option(
        False,
        "--include-toolchain-providers",
        help=(
            "Also install/copy rust-analyzer, gopls, and csharp-ls using local "
            "toolchains. Off by default to avoid mutating external toolchains during "
            "normal installs."
        ),
    ),
) -> None:
    """Install managed external LSP providers.

    Setup availability does not prove semantic navigation. Use
    `tg doctor --with-lsp --json` and inspect health_status / health_check plus
    navigation lsp_proof fields before treating LSP evidence as dependable.
    """
    from tensor_grep.cli.lsp_provider_setup import (
        install_managed_lsp_providers,
        supported_lsp_languages,
    )

    payload = install_managed_lsp_providers(
        python_executable=sys.executable,
        managed_root=None,
        include_toolchain_providers=include_toolchain_providers,
    )
    has_install_errors = bool(payload.get("install_errors"))
    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        if has_install_errors:
            raise typer.Exit(code=1)
        return
    if has_install_errors:
        typer.echo(
            f"Managed external LSP provider setup completed with errors under {payload['managed_provider_root']}"
        )
    else:
        typer.echo(
            f"Managed external LSP provider setup complete under {payload['managed_provider_root']}"
        )
    providers = cast(dict[str, dict[str, Any]], payload["providers"])
    for language in supported_lsp_languages():
        provider = providers.get(language, {})
        command = provider.get("command") or []
        source = provider.get("command_source", "missing")
        availability = "available" if provider.get("available") else "missing"
        command_text = " ".join(str(part) for part in command) if command else "missing"
        install_error = provider.get("install_error")
        suffix = f", error={install_error}" if install_error else ""
        typer.echo(f"  {language}: {command_text} [{source}, {availability}{suffix}]")
    if has_install_errors:
        raise typer.Exit(code=1)


app.add_typer(checkpoint_app, name="checkpoint")
app.add_typer(session_app, name="session")
app.add_typer(review_bundle_app, name="review-bundle")
app.add_typer(evidence_app, name="evidence")
app.add_typer(ledger_app, name="ledger")


@app.command(name="mcp")
def mcp_server() -> None:
    """Start the Model Context Protocol (MCP) server for AI assistants"""
    from tensor_grep.cli.mcp_server import run_mcp_server

    run_mcp_server()


@app.command(name="repair-launcher")
def repair_launcher(
    allow_foreign_rename: bool = typer.Option(
        False,
        "--allow-foreign-rename",
        help=(
            "Move aside the first foreign Windows tg.exe selected by Python subprocess "
            "resolution and replace it with the managed tensor-grep native front door. "
            "Use only when you own that foreign command."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Repair Windows Python subprocess tg resolution.

    Removes verified or self-identifying tensor-grep Python Scripts entrypoints
    that shadow the managed native front door. Use --allow-foreign-rename only
    for a foreign tg.exe that you own and want tensor-grep to back up.
    """
    payload = _repair_windows_python_subprocess_launcher(allow_foreign_rename=allow_foreign_rename)
    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
    else:
        typer.echo(payload["message"])
        if payload.get("backup_path"):
            typer.echo(f"backup_path: {payload['backup_path']}")
        if payload.get("replaced_path"):
            typer.echo(f"replaced_path: {payload['replaced_path']}")
        if payload.get("post_repair_version"):
            typer.echo(f"post_repair_version: {payload['post_repair_version']}")

    if str(payload.get("status") or "").startswith(("blocked", "failed")):
        raise typer.Exit(code=1)


@app.command()
def doctor(
    path: str = typer.Argument(".", help="Workspace root to inspect."),
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    with_lsp: bool = typer.Option(
        True,
        "--with-lsp/--no-lsp",
        help=(
            "Include external LSP provider diagnostics. Provider availability is "
            "not navigation proof; inspect health_status and health_check."
        ),
    ),
) -> None:
    """Print system, GPU, cache, AST, daemon, shell-escaping, and provider-proof diagnostics.

    Reports Windows shell guidance for PowerShell literal patterns and cmd.exe metacharacters.
    """
    payload = _self._build_doctor_payload(path, config=config, with_lsp=with_lsp)
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(_render_doctor_payload(payload))


def _is_uv_tool_managed_python(executable: str) -> bool:
    """True when `executable` belongs to a `uv tool install`-managed tool venv (path under
    `.../uv/tools/`). Such launchers live in an isolated venv that `uv pip`/`pip install` into that
    same interpreter cannot upgrade correctly; the source-aware path is `uv tool install --force`
    (audit #2 — matches the WSL uv-tool pin that stranded tg at a stale version)."""
    return "/uv/tools/" in executable.replace("\\", "/").lower()


# Module-level (not nested in `upgrade()`) so `tg install-dense` can reuse the identical
# uv-tool -> uv pip -> pip cascade for the `semantic` extra install step, including the
# uv-tool-managed-python trap handling (audit #2) -- CEO#7.
def _upgrade_attempts(package_spec: str) -> list[tuple[str, list[str]]]:
    pip_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-cache-dir",
        package_spec,
    ]
    attempts: list[tuple[str, list[str]]] = [
        (
            "uv",
            [
                "uv",
                "pip",
                "install",
                "--python",
                sys.executable,
                "--upgrade",
                "--refresh-package",
                "tensor-grep",
                package_spec,
            ],
        ),
        ("pip", pip_cmd),
    ]
    # A uv-tool-managed launcher must be upgraded via the uv-tool front door, not `uv pip`/`pip`
    # into its isolated interpreter — try it first when detected (audit #2).
    if _is_uv_tool_managed_python(sys.executable):
        attempts.insert(0, ("uv-tool", ["uv", "tool", "install", "--force", package_spec]))
    return attempts


def _run_upgrade(
    attempts: list[tuple[str, list[str]]],
) -> tuple[subprocess.CompletedProcess[str], str]:
    errors: list[str] = []
    for label, cmd in attempts:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result, label
        except FileNotFoundError as e:
            errors.append(f"{label}: {e}")
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            stdout = (e.stdout or "").strip()
            combined = stderr or stdout or str(e)
            errors.append(f"{label}: {combined}")
            if label == "pip" and "No module named pip" in combined:
                try:
                    subprocess.run(
                        [sys.executable, "-m", "ensurepip", "--upgrade"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    return result, "pip+ensurepip"
                except FileNotFoundError as ee:
                    errors.append(f"ensurepip: {ee}")
                except subprocess.CalledProcessError as ee:
                    ee_stderr = (ee.stderr or "").strip()
                    ee_stdout = (ee.stdout or "").strip()
                    errors.append(f"ensurepip: {ee_stderr or ee_stdout or str(ee)}")
    raise RuntimeError("; ".join(errors))


@app.command()
def upgrade() -> None:
    """Upgrade tensor-grep to the latest version published on PyPI."""
    import importlib.metadata

    def _looks_like_windows_self_update_lock(message: str) -> bool:
        lowered = message.lower()
        return (
            "winerror 32" in lowered
            or "os error 32" in lowered
            or "being used by another process" in lowered
        )

    def _schedule_windows_self_upgrade(
        attempts: list[tuple[str, list[str]]],
        expected_version: str,
        *,
        native_path: Path | None = None,
        native_assets: list[dict[str, str]] | None = None,
        bridge_paths: list[Path] | None = None,
        daemon_root: str | None = None,
    ) -> Path:
        import textwrap

        native_asset_payload = json.dumps(native_assets or [])
        bridge_payload = json.dumps([str(path) for path in bridge_paths or []])
        helper_code = textwrap.dedent(
            """
            import hashlib
            import json
            import os
            import shutil
            import subprocess
            import sys
            import time
            import urllib.request
            from pathlib import Path
            from uuid import uuid4

            parent_pid = int(sys.argv[1])
            log_path = Path(sys.argv[2])
            attempts = json.loads(sys.argv[3])
            expected_version = sys.argv[4]
            native_path_arg = sys.argv[5]
            native_assets = json.loads(sys.argv[6])
            bridge_paths = [Path(path) for path in json.loads(sys.argv[7])]
            daemon_root = sys.argv[8] if len(sys.argv) > 8 else ""
            native_path = Path(native_path_arg) if native_path_arg else None
            log_path.parent.mkdir(parents=True, exist_ok=True)

            for _ in range(300):
                try:
                    subprocess.run(
                        [
                            "powershell",
                            "-NoProfile",
                            "-Command",
                            f"Get-Process -Id {parent_pid} -ErrorAction Stop | Out-Null",
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                except subprocess.CalledProcessError:
                    break
                time.sleep(0.1)

            def _run_attempts() -> tuple[bool, str, str]:
                errors: list[str] = []
                for label, cmd in attempts:
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                        output = "\\n".join(
                            part
                            for part in (
                                (result.stdout or "").strip(),
                                (result.stderr or "").strip(),
                            )
                            if part
                        )
                        return True, label, output
                    except FileNotFoundError as exc:
                        errors.append(f"{label}: {exc}")
                    except subprocess.CalledProcessError as exc:
                        stderr = (exc.stderr or "").strip()
                        stdout = (exc.stdout or "").strip()
                        combined = stderr or stdout or str(exc)
                        errors.append(f"{label}: {combined}")
                        if label == "pip" and "No module named pip" in combined:
                            try:
                                subprocess.run(
                                    [sys.executable, "-m", "ensurepip", "--upgrade"],
                                    capture_output=True,
                                    text=True,
                                    check=True,
                                )
                                result = subprocess.run(
                                    cmd,
                                    capture_output=True,
                                    text=True,
                                    check=True,
                                )
                                output = "\\n".join(
                                    part
                                    for part in (
                                        (result.stdout or "").strip(),
                                        (result.stderr or "").strip(),
                                    )
                                    if part
                                )
                                return True, "pip+ensurepip", output
                            except FileNotFoundError as ensurepip_exc:
                                errors.append(f"ensurepip: {ensurepip_exc}")
                            except subprocess.CalledProcessError as ensurepip_exc:
                                ensure_stderr = (ensurepip_exc.stderr or "").strip()
                                ensure_stdout = (ensurepip_exc.stdout or "").strip()
                                errors.append(
                                    f"ensurepip: {ensure_stderr or ensure_stdout or str(ensurepip_exc)}"
                                )
                return False, "", "; ".join(errors)

            def _verify_installed_version(expected_version: str) -> tuple[bool, str]:
                probe_code = (
                    "import importlib.metadata as m; "
                    "import tensor_grep; "
                    "print(m.version('tensor-grep'))"
                )
                try:
                    result = subprocess.run(
                        [sys.executable, "-c", probe_code],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                except FileNotFoundError as exc:
                    return False, f"post-upgrade verification failed: {exc}"
                except subprocess.CalledProcessError as exc:
                    stderr = (exc.stderr or "").strip()
                    stdout = (exc.stdout or "").strip()
                    combined = stderr or stdout or str(exc)
                    return False, f"post-upgrade verification failed: {combined}"
                version = (result.stdout or "").strip().splitlines()
                if not version:
                    return False, "post-upgrade verification failed: no tensor-grep version reported"
                installed_version = version[-1].strip()
                if expected_version and installed_version != expected_version:
                    return (
                        False,
                        "post-upgrade verification failed: expected tensor-grep "
                        + expected_version
                        + " but target Python reports "
                        + installed_version,
                    )
                return True, installed_version

            def _version(path: Path) -> str:
                result = subprocess.run([str(path), "--version"], capture_output=True, text=True)
                if result.returncode != 0:
                    return ""
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line:
                        return line
                return ""

            def _version_matches(version_text: str) -> bool:
                return bool(expected_version and expected_version in version_text)

            def _same_path(left: Path, right: Path) -> bool:
                try:
                    return left.resolve() == right.resolve()
                except OSError:
                    return left == right

            def _python_scripts_launcher_python(candidate: Path) -> Path | None:
                if candidate.name.lower() != "tg.exe":
                    return None
                if candidate.parent.name.lower() != "scripts":
                    return None
                parts = tuple(part.lower() for part in candidate.parts)
                if ".tensor-grep" in parts or ".venv" in parts or "venv" in parts:
                    return None
                python_executable = candidate.parent.parent / "python.exe"
                if not python_executable.is_file():
                    return None
                return python_executable

            def _package_owns_launcher(
                python_executable: Path,
                launcher_path: Path,
            ) -> str:
                try:
                    result = subprocess.run(
                        [
                            str(python_executable),
                            "-m",
                            "pip",
                            "show",
                            "-f",
                            "tensor-grep",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                except Exception:
                    return ""
                if result.returncode != 0:
                    return ""
                location: Path | None = None
                version = ""
                files_started = False
                files: list[str] = []
                for raw_line in result.stdout.splitlines():
                    line = raw_line.rstrip()
                    if line.startswith("Location:"):
                        value = line.split(":", 1)[1].strip()
                        if value:
                            location = Path(value)
                    elif line.startswith("Version:"):
                        version = line.split(":", 1)[1].strip()
                    elif line.strip() == "Files:":
                        files_started = True
                    elif files_started:
                        value = line.strip()
                        if value:
                            files.append(value)
                if location is None:
                    return ""
                try:
                    resolved_launcher = launcher_path.resolve()
                except OSError:
                    resolved_launcher = launcher_path
                for relative_file in files:
                    try:
                        resolved_file = (location / relative_file).resolve()
                    except OSError:
                        resolved_file = location / relative_file
                    if _same_path(resolved_file, resolved_launcher):
                        return version or "installed"
                return ""

            def _cleanup_stale_python_launchers() -> str:
                if not expected_version or native_path is None:
                    return ""
                removed: list[str] = []
                failed: list[str] = []
                seen: set[str] = set()
                native_seen = False
                for entry in os.environ.get("PATH", "").split(os.pathsep):
                    if not entry:
                        continue
                    candidate = Path(entry.strip('"')) / "tg.exe"
                    if _same_path(candidate, native_path):
                        native_seen = True
                        continue
                    python_executable = _python_scripts_launcher_python(candidate)
                    if python_executable is None:
                        continue
                    try:
                        key = str(candidate.resolve()).lower()
                    except OSError:
                        key = str(candidate).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    version = _version(candidate)
                    if _version_matches(version) and native_seen:
                        continue
                    if version and not version.strip().lower().startswith("tensor-grep "):
                        continue
                    package_version = _package_owns_launcher(python_executable, candidate)
                    if not package_version:
                        continue
                    reason = version or "tensor-grep package " + package_version
                    try:
                        result = subprocess.run(
                            [
                                str(python_executable),
                                "-m",
                                "pip",
                                "uninstall",
                                "-y",
                                "tensor-grep",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=120,
                        )
                        if result.returncode != 0:
                            error = (result.stderr or result.stdout or "").strip()
                            raise RuntimeError(
                                "pip uninstall tensor-grep failed"
                                + (": " + error if error else "")
                            )
                        candidate.unlink(missing_ok=True)
                        if candidate.exists():
                            raise OSError("launcher still exists after cleanup")
                        removed.append("- " + str(candidate) + " (" + reason + ")")
                    except Exception as exc:
                        failed.append("- " + str(candidate) + " (" + reason + "): " + str(exc))
                sections: list[str] = []
                if removed:
                    sections.append(
                        "Removed stale tensor-grep Python package launchers from PATH:\\n"
                        + "\\n".join(removed)
                    )
                if failed:
                    sections.append(
                        "WARNING: stale tensor-grep Python package launchers remain "
                        "ahead of managed native tg.exe:\\n"
                        + "\\n".join(failed)
                    )
                return "\\n".join(sections)

            def _refresh_native_frontdoor_and_bridges() -> str:
                # refresh native front door, stale PATH copies, and stale Python launchers after locked self-upgrade
                if not expected_version or native_path is None:
                    return ""

                messages: list[str] = []
                native_path.parent.mkdir(parents=True, exist_ok=True)
                current_native_version = _version(native_path) if native_path.is_file() else ""
                if not _version_matches(current_native_version):
                    if not native_assets:
                        raise RuntimeError(
                            "no release-native front-door asset is available for this platform"
                        )
                    errors: list[str] = []
                    for _ in range(120):
                        refreshed = False
                        for asset in native_assets:
                            url = asset.get("url", "")
                            flavor = asset.get("flavor", "unknown")
                            temp_path = native_path.with_name(
                                native_path.name + ".download-" + uuid4().hex
                            )
                            try:
                                try:

                                    def _cap(block_num, block_size, total_size):
                                        if block_num * block_size > 512 * 1024 * 1024:
                                            raise RuntimeError("native asset download exceeded 512MB")

                                    # O_EXCL claims the temp name as a regular file first; urlretrieve's 'wb'
                                    # FOLLOWS a symlink and the payload is an executable. Full rationale in
                                    # _download_native_frontdoor_asset (found by the #859 ratchet).
                                    _fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                                    os.close(_fd)
                                    urllib.request.urlretrieve(url, temp_path, reporthook=_cap)
                                except Exception as exc:
                                    errors.append(f"{flavor} asset unavailable: {exc}")
                                    continue
                                sha256 = asset.get("sha256", "")
                                if not sha256:
                                    errors.append(
                                        f"{flavor} asset has no published checksum; "
                                        "refusing to install unverified binary"
                                    )
                                    continue
                                actual_sha256 = hashlib.sha256(temp_path.read_bytes()).hexdigest().lower()
                                if actual_sha256 != sha256.lower():
                                    errors.append(
                                        f"{flavor} asset checksum mismatch "
                                        f"(expected {sha256}, got {actual_sha256})"
                                    )
                                    continue
                                temp_version = _version(temp_path)
                                if not _version_matches(temp_version):
                                    raise RuntimeError(
                                        "downloaded native tg front door reported "
                                        + (temp_version or "no version")
                                    )
                                os.replace(temp_path, native_path)
                                installed_native_version = _version(native_path)
                                if not _version_matches(installed_native_version):
                                    raise RuntimeError(
                                        "installed native tg front door reported "
                                        + (installed_native_version or "no version")
                                    )
                                messages.append(
                                    "Native tg front-door refresh completed.\\n"
                                    + "Verified "
                                    + installed_native_version
                                    + ".\\nNative asset flavor: "
                                    + flavor
                                    + "."
                                )
                                refreshed = True
                                break
                            except Exception as exc:
                                errors.append(str(exc))
                            finally:
                                try:
                                    temp_path.unlink()
                                except FileNotFoundError:
                                    pass
                        if refreshed:
                            break
                        time.sleep(0.5)
                    else:
                        raise RuntimeError(
                            "native tg front-door refresh failed: " + "; ".join(errors[-10:])
                        )

                refreshed_bridges: list[str] = []
                for bridge_path in bridge_paths:
                    shutil.copy2(native_path, bridge_path)
                    bridge_version = _version(bridge_path)
                    if not _version_matches(bridge_version):
                        raise RuntimeError(
                            "refreshed PATH tensor-grep front-door copy reported "
                            + (bridge_version or "no version")
                            + " for "
                            + str(bridge_path)
                        )
                    refreshed_bridges.append(str(bridge_path))
                if refreshed_bridges:
                    messages.append(
                        "Refreshed PATH tensor-grep front-door copies:\\n"
                        + "\\n".join(refreshed_bridges)
                    )
                cleanup_payload = _cleanup_stale_python_launchers()
                if cleanup_payload:
                    messages.append(cleanup_payload)
                return "\\n".join(messages)

            def _restart_session_daemon_after_upgrade() -> str:
                if not daemon_root:
                    return ""
                # End-of-options sentinel (CWE-88), INLINE and duplicated, deliberately.
                #
                # This block is inside `helper_code = textwrap.dedent(...)` -- a standalone script
                # written to disk and run by the Windows scheduler. It cannot import tg, so a
                # shared `_session_daemon_argv` helper would NameError at runtime. A first cut
                # extracted one and was caught by
                # `test_upgrade_scheduled_windows_helper_restarts_preexisting_session_daemon`,
                # which pins the generated script's TEXT. Refactoring code that lives inside a
                # generated string is not the same as refactoring code.
                #
                # `daemon_root` is caller-influenced: it is the PATH the user gave
                # `tg session daemon start <PATH>`, persisted in daemon state and round-tripped
                # back here. The callee is a real Typer/Click command whose `path` argument
                # DEFAULTS TO "." -- so a dash-leading root consumed as an option does NOT error;
                # the daemon is started or queried at the CWD instead. Silent wrong scope.
                #
                # `--json` sits BEFORE the sentinel: everything after `--` is a positional, so
                # leaving the flag there would hand Click a second positional instead.
                status_command = [
                    sys.executable,
                    "-m",
                    "tensor_grep.cli.main",
                    "session",
                    "daemon",
                    "status",
                    "--json",
                    "--",
                    daemon_root,
                ]
                start_command = [
                    sys.executable,
                    "-m",
                    "tensor_grep.cli.main",
                    "session",
                    "daemon",
                    "start",
                    "--json",
                    "--",
                    daemon_root,
                ]
                try:
                    status = subprocess.run(
                        status_command,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if status.returncode == 0:
                        try:
                            if json.loads(status.stdout).get("running") is True:
                                return ""
                        except json.JSONDecodeError:
                            pass
                    started = subprocess.run(
                        start_command,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if started.returncode == 0:
                        return "Session daemon restarted after scheduled upgrade for " + daemon_root + "."
                    error = (started.stderr or started.stdout or "").strip()
                    return (
                        "WARNING: session daemon was running before scheduled upgrade but "
                        "restart failed for "
                        + daemon_root
                        + (": " + error if error else ".")
                    )
                except Exception as exc:
                    return (
                        "WARNING: session daemon was running before scheduled upgrade but "
                        "restart failed for "
                        + daemon_root
                        + ": "
                        + str(exc)
                    )

            ok, method, payload = _run_attempts()
            if ok:
                verified, version = _verify_installed_version(expected_version)
                if not verified:
                    log_path.write_text(
                        "Scheduled tensor-grep upgrade failed.\\n" + version,
                        encoding="utf-8",
                    )
                    raise SystemExit(1)
                try:
                    native_payload = _refresh_native_frontdoor_and_bridges()
                except Exception as exc:
                    log_path.write_text(
                        "Scheduled tensor-grep upgrade failed.\\n"
                        + "post-upgrade native front-door refresh failed: "
                        + str(exc),
                        encoding="utf-8",
                    )
                    raise SystemExit(1)
                text = "Scheduled tensor-grep upgrade completed via " + method + "."
                text += "\\nVerified tensor-grep " + version + "."
                if native_payload:
                    text += "\\n" + native_payload
                daemon_payload = _restart_session_daemon_after_upgrade()
                if daemon_payload:
                    text += "\\n" + daemon_payload
                if payload:
                    text += "\\n" + payload
                log_path.write_text(text, encoding="utf-8")
                raise SystemExit(0)

            log_path.write_text(
                "Scheduled tensor-grep upgrade failed.\\n" + payload,
                encoding="utf-8",
            )
            raise SystemExit(1)
            """
        ).strip()

        log_path = Path.home() / ".tensor-grep" / "logs" / f"upgrade-{uuid4().hex}.log"
        creationflags = 0
        for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
            creationflags |= int(getattr(subprocess, flag_name, 0))
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                helper_code,
                str(os.getpid()),
                str(log_path),
                json.dumps(attempts),
                expected_version,
                str(native_path) if native_path is not None else "",
                native_asset_payload,
                bridge_payload,
                daemon_root or "",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        return log_path

    def _installed_version() -> str | None:
        try:
            return importlib.metadata.version("tensor-grep")
        except importlib.metadata.PackageNotFoundError:
            return None

    typer.echo("Upgrading tensor-grep to the latest version...")

    try:
        daemon_snapshot = _upgrade_running_session_daemon_snapshot()
        previous_version = _installed_version()
        latest_version = _self._latest_pypi_tensor_grep_version()
        exact_latest_requested = False
        if latest_version is not None and (
            previous_version is None
            or latest_version == previous_version
            or _is_version_newer(latest_version, previous_version)
        ):
            package_spec = f"tensor-grep=={latest_version}"
            exact_latest_requested = True
        else:
            package_spec = "tensor-grep"
        attempts = _upgrade_attempts(package_spec)
        result, method = _run_upgrade(attempts)
        current_version = _verify_target_python_tensor_grep_version(sys.executable)
        if (
            exact_latest_requested
            and latest_version is not None
            and current_version != latest_version
        ):
            raise RuntimeError(
                "post-upgrade verification failed: expected tensor-grep "
                f"{latest_version} from PyPI but target Python reports {current_version}"
            )
        native_refresh_message = _refresh_managed_native_frontdoor(current_version)
        output = "\n".join(
            part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part
        )
        if (
            latest_version is not None
            and current_version == previous_version
            and current_version == latest_version
        ):
            typer.echo(f"tensor-grep is already at the latest PyPI version ({current_version}).")
        elif current_version == previous_version:
            if latest_version is None:
                typer.echo(
                    "tensor-grep install completed, but the latest PyPI version could not be "
                    f"verified; installed version is {current_version}."
                )
            elif _is_version_newer(current_version, latest_version):
                typer.echo(
                    f"tensor-grep {current_version} is installed; PyPI metadata reported "
                    f"{latest_version}, so no downgrade was attempted."
                )
            elif "Requirement already satisfied" in output:
                typer.echo(f"tensor-grep is already installed ({current_version}).")
            else:
                typer.echo(f"tensor-grep remains installed at {current_version}.")
        else:
            typer.echo(f"Successfully upgraded tensor-grep via {method}!")
            if output:
                typer.echo(output)
        if native_refresh_message:
            typer.echo(native_refresh_message)
        daemon_restart_message = _restart_session_daemon_after_upgrade(daemon_snapshot)
        if daemon_restart_message:
            typer.echo(daemon_restart_message)

    except RuntimeError as e:
        if _looks_like_windows_self_update_lock(str(e)):
            previous_version = _installed_version()
            latest_version = _self._latest_pypi_tensor_grep_version()
            expected_version = ""
            if latest_version is not None and (
                previous_version is None
                or latest_version == previous_version
                or _is_version_newer(latest_version, previous_version)
            ):
                package_spec = f"tensor-grep=={latest_version}"
                expected_version = latest_version
            else:
                package_spec = "tensor-grep"
            native_path = _managed_native_frontdoor_path()
            path_order_message = (
                _self._ensure_windows_managed_native_first_on_path(native_path)
                if native_path is not None
                else None
            )
            if expected_version:
                # Audit HIGH (2026-06-28): embed the expected sha256 into each payload
                # entry on the parent side so the detached helper can verify each
                # download WITHOUT importing main.py.  Fail-closed: skip any candidate
                # whose sha256 can't be resolved; refuse to schedule if none remain.
                _native_checksums = _self._fetch_native_frontdoor_checksums(expected_version)
                if _native_checksums is None:
                    raise RuntimeError(
                        "release-native front-door asset refresh refused: could not fetch "
                        f"CHECKSUMS.txt for v{expected_version}; refusing to schedule "
                        "an unverified native binary refresh"
                    ) from None
                native_assets = []
                for _cand, _url in _self._native_frontdoor_download_candidates(expected_version):
                    _sha256 = _self._expected_asset_sha256(_native_checksums, _cand.asset_name)
                    if _sha256 is None:
                        continue
                    native_assets.append({
                        "url": _url,
                        "flavor": _cand.flavor,
                        "asset_name": _cand.asset_name,
                        "sha256": _sha256,
                    })
                if not native_assets:
                    raise RuntimeError(
                        "no release-native front-door asset is available for this platform"
                    ) from None
            else:
                native_assets = []
            bridge_paths = (
                _self._windows_stale_tensor_grep_com_bridges(expected_version, native_path)
                if expected_version and native_path is not None
                else []
            )
            log_path = _schedule_windows_self_upgrade(
                _upgrade_attempts(package_spec),
                expected_version,
                native_path=native_path,
                native_assets=native_assets,
                bridge_paths=bridge_paths,
                daemon_root=(
                    str(daemon_snapshot.get("root"))
                    if isinstance(daemon_snapshot, dict) and daemon_snapshot.get("root")
                    else None
                ),
            )
            typer.echo(
                "Windows is still using tg.exe, so the upgrade was scheduled in the background."
            )
            typer.echo("Wait a few seconds, then run `tg --version` again.")
            typer.echo(f"Upgrade log: {log_path}")
            if path_order_message:
                typer.echo(path_order_message)
            return
        typer.echo("Error occurred while upgrading tensor-grep.", err=True)
        typer.echo(str(e), err=True)
        sys.exit(1)


# CEO#7 (P1 -- "semantic find that works out of the box"): `tg find` / `tg search --semantic`
# degrade to BM25-only until the `semantic` extra (model2vec + numpy, both torch/GPU-free) is
# installed AND the potion-code-16M model has been fetched. `tg install-dense` is the one-shot,
# explicitly opt-in command that does both steps; it deliberately does NOT run automatically from
# `tg find` (that is Option 3, out of scope here) and the model is NOT bundled into the wheel
# (a separate, CEO-gated packaging decision).
_INSTALL_DENSE_PACKAGE_SPEC = "tensor-grep[semantic]"


def _run_install_dense() -> dict[str, Any]:
    """Do the actual `tg install-dense` work: install the `semantic` extra via the same
    uv-tool -> uv pip -> pip cascade `tg upgrade` uses (`_upgrade_attempts`/`_run_upgrade`, module
    level above), then run the hardened, checksum-pinned, deadline-bounded model fetch
    (`retrieval_dense.fetch_dense_model` -- never hand-rolled here).

    Never raises: every failure mode (the pip cascade exhausted, a network error, a checksum
    mismatch, or the fetch's own wall-clock deadline) is captured into the returned payload so the
    command layer picks exit code + rendering uniformly for both `--json` and text mode. Fail-
    closed -- a failed fetch leaves no partial model directory, per `fetch_dense_model`'s own
    atomic verify-before-install contract (retrieval_dense.py); a failed pip step never attempts
    the network fetch at all (`fetch_model` is reported "skipped", not silently retried).
    """
    from tensor_grep.core.retrieval_dense import default_model_dir, fetch_dense_model

    steps: dict[str, dict[str, Any]] = {}

    try:
        attempts = _upgrade_attempts(_INSTALL_DENSE_PACKAGE_SPEC)
        result, method = _run_upgrade(attempts)
        output = "\n".join(
            part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part
        )
        steps["pip_install"] = {"status": "ok", "method": method, "detail": output or None}
    except RuntimeError as exc:
        steps["pip_install"] = {"status": "failed", "method": None, "detail": str(exc)}
        steps["fetch_model"] = {
            "status": "skipped",
            "dir": None,
            "detail": "skipped: installing the semantic extra failed",
        }
        return {
            "ok": False,
            "steps": steps,
            "dense_model_dir": None,
            "message": (
                f"tg install-dense failed: could not install {_INSTALL_DENSE_PACKAGE_SPEC} "
                f"({steps['pip_install']['detail']})"
            ),
        }

    try:
        dest = fetch_dense_model()
    except Exception as exc:
        # `fetch_dense_model` documents BackendExecutionError for every download/checksum/deadline
        # failure (retrieval_dense.py), but this command boundary catches broadly so a lower-level
        # unwrapped OSError (e.g. permission denied creating the cache dir) also exits cleanly
        # with a message instead of a raw traceback -- fail-closed never means fail-silent.
        steps["fetch_model"] = {
            "status": "failed",
            "dir": str(default_model_dir()),
            "detail": str(exc),
        }
        return {
            "ok": False,
            "steps": steps,
            "dense_model_dir": None,
            "message": f"tg install-dense failed: dense model fetch failed ({exc})",
        }

    steps["fetch_model"] = {"status": "ok", "dir": str(dest), "detail": None}
    return {
        "ok": True,
        "steps": steps,
        "dense_model_dir": str(dest),
        "message": f"tg install-dense complete: the dense semantic leg is ready (model at {dest}).",
    }


@app.command(name="install-dense")
def install_dense(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """One-shot install of the `tg find` / `tg search --semantic` dense-embedding leg.

    Installs the `semantic` extra (model2vec + numpy -- pure CPU/numpy, no torch or GPU
    dependency) via the same uv-tool -> uv pip -> pip cascade `tg upgrade` uses, then fetches the
    checksum-pinned potion-code-16M model (~65MB) to the local model cache
    (`~/.tensor-grep/models/potion-code-16M`, or `TG_SEMANTIC_MODEL_DIR` if set). Never runs
    automatically -- `tg find` and `tg search --semantic` degrade visibly to BM25-only until this
    has been run once. On any failure (pip install failure, network error, or a checksum mismatch
    against the pinned HuggingFace revision) this exits non-zero with a clear message and leaves
    no partial model directory behind.
    """
    payload = _run_install_dense()
    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
    else:
        typer.echo(payload["message"])
        pip_step = payload["steps"].get("pip_install", {})
        typer.echo(f"  pip_install: {pip_step.get('status')} (method={pip_step.get('method')})")
        fetch_step = payload["steps"].get("fetch_model", {})
        typer.echo(f"  fetch_model: {fetch_step.get('status')} (dir={fetch_step.get('dir')})")
        if pip_step.get("status") == "failed" and pip_step.get("detail"):
            typer.echo(f"  pip_install detail: {pip_step['detail']}", err=True)
        if fetch_step.get("status") == "failed" and fetch_step.get("detail"):
            typer.echo(f"  fetch_model detail: {fetch_step['detail']}", err=True)
    if not payload["ok"]:
        raise typer.Exit(code=1)


@app.command(name="install")
def install_command(
    target: str = typer.Option(
        "all",
        "--target",
        "-t",
        help="Agent target to configure: claude, cursor, codex, opencode, qwen, or all.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate configuration without writing files."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm installation without prompting."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Configure AI coding agents to use tensor-grep's built-in MCP server."""
    from tensor_grep.cli.agent_installer import install_command as _impl

    _impl(target=target, dry_run=dry_run, yes=yes, json_output=json_output)


@app.command(name="uninstall")
def uninstall_command(
    target: str = typer.Option(
        "all",
        "--target",
        "-t",
        help="Agent target to remove: claude, cursor, codex, opencode, qwen, or all.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate removal without writing files."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Confirm uninstallation without prompting."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Remove tensor-grep MCP integration and search guidance from AI coding agents."""
    from tensor_grep.cli.agent_installer import uninstall_command as _impl

    _impl(target=target, dry_run=dry_run, yes=yes, json_output=json_output)


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


@app.command(name="audit-verify")
def audit_verify(
    manifest_path: str = typer.Argument(..., help="Path to the rewrite audit manifest JSON file."),
    signing_key: str | None = typer.Option(
        None,
        "--signing-key",
        help="Optional HMAC signing key path for signed manifests.",
    ),
    previous_manifest: str | None = typer.Option(
        None,
        "--previous-manifest",
        help="Optional previous manifest path for validating manifest chaining.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON verification output.",
    ),
) -> None:
    """Verify a rewrite audit manifest digest, chain, and optional signature."""
    from tensor_grep.cli.audit_manifest import (
        verify_audit_manifest,
        verify_audit_manifest_json,
    )

    try:
        if json_output:
            json_text = verify_audit_manifest_json(
                manifest_path,
                signing_key=signing_key,
                previous_manifest=previous_manifest,
            )
            typer.echo(json_text)
            # Mirror the text path: a tampered/invalid manifest must exit 1 even in
            # --json mode (audit H1), so callers can gate on the process status.
            if not json.loads(json_text).get("valid", False):
                raise typer.Exit(code=1)
            return

        payload = verify_audit_manifest(
            manifest_path,
            signing_key=signing_key,
            previous_manifest=previous_manifest,
        )
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Manifest: {payload['manifest_path']}")
    typer.echo(f"valid={payload['valid']}")
    checks = payload["checks"]
    typer.echo(
        "checks="
        f"digest:{checks['digest_valid']} "
        f"chain:{checks['chain_valid']} "
        f"signature:{checks['signature_valid']}"
    )
    for error in payload["errors"]:
        typer.echo(f"- {error}")
    if not payload["valid"]:
        raise typer.Exit(code=1)


@app.command(name="audit-history")
def audit_history(
    path: str = typer.Argument(".", help="Project root to inspect for audit manifests."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON history output.",
    ),
) -> None:
    """List known audit manifests in newest-first chain order."""
    from tensor_grep.cli.audit_manifest import list_audit_history, list_audit_history_payload

    try:
        if json_output:
            typer.echo(json.dumps(list_audit_history_payload(path), indent=2))
            return
        payload = list_audit_history(path)
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_history_error_payload(str(exc), code="not_found"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_history_error_payload(str(exc), code="invalid_input"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_history_error_payload(str(exc), code="internal_error"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    for entry in payload:
        annotations: list[str] = []
        if entry["missing_timestamp"]:
            annotations.append("missing_timestamp")
        if entry["chain_gap"]:
            annotations.append("chain_gap")
        if entry["signature_kind"] is not None:
            annotations.append(f"signature={entry['signature_kind']}")
        created_at = entry["created_at"] or "<missing>"
        suffix = f" [{' '.join(annotations)}]" if annotations else ""
        typer.echo(f"{created_at}  {entry['manifest_sha256']}  {entry['file_path']}{suffix}")


@app.command(name="audit-diff")
def audit_diff(
    previous_manifest: str = typer.Argument(
        ..., help="Path to the previous audit manifest JSON file."
    ),
    current_manifest: str = typer.Argument(
        ..., help="Path to the current audit manifest JSON file."
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON diff output.",
    ),
) -> None:
    """Compute a semantic diff between two audit manifests."""
    from tensor_grep.cli.audit_manifest import diff_audit_manifests, diff_audit_manifests_payload

    try:
        if json_output:
            typer.echo(
                json.dumps(
                    diff_audit_manifests_payload(previous_manifest, current_manifest), indent=2
                )
            )
            return
        payload = diff_audit_manifests(previous_manifest, current_manifest)
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(json.dumps(_audit_diff_error_payload(str(exc), code="not_found"), indent=2))
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_diff_error_payload(str(exc), code="invalid_json"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _audit_diff_error_payload(str(exc), code="internal_error"),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Audit diff: {previous_manifest} -> {current_manifest}")
    for section_name in ("added", "removed", "changed"):
        typer.echo(f"{section_name.capitalize()}:")
        section = payload[section_name]
        if not section:
            typer.echo("  (none)")
            continue
        for key, value in section.items():
            if section_name == "changed":
                typer.echo(f"  {key}:")
                typer.echo(f"    old: {json.dumps(value['old'], sort_keys=True)}")
                typer.echo(f"    new: {json.dumps(value['new'], sort_keys=True)}")
                continue
            typer.echo(f"  {key}: {json.dumps(value, sort_keys=True)}")


@review_bundle_app.command("create")
def review_bundle_create(
    manifest_path: str = typer.Option(
        ...,
        "--manifest",
        help="Path to the rewrite audit manifest JSON file.",
    ),
    scan_path: str | None = typer.Option(
        None,
        "--scan",
        help="Optional path to the ruleset scan JSON file.",
    ),
    checkpoint_id: str | None = typer.Option(
        None,
        "--checkpoint-id",
        help="Optional checkpoint ID to include in the bundle.",
    ),
    previous_manifest: str | None = typer.Option(
        None,
        "--previous-manifest",
        help="Optional previous audit manifest JSON for diff generation.",
    ),
    receipt: list[str] | None = typer.Option(
        None,
        "--receipt",
        help="Path to a signed EvidenceReceipt JSON file (from `tg evidence emit --sign`) to "
        "embed in the bundle. Repeatable. Pair with `tg review-bundle verify --against` in CI to "
        "gate the PR on receipt signature/trust/freshness.",
    ),
    output_path: str | None = typer.Option(
        None,
        "--output",
        help="Optional file path where the review bundle JSON should be written.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the review bundle as structured JSON.",
    ),
) -> None:
    """Create a review bundle for enterprise change review."""
    from tensor_grep.cli.audit_manifest import create_review_bundle, create_review_bundle_json
    from tensor_grep.cli.evidence_signing import EvidenceSigningError

    try:
        if json_output:
            typer.echo(
                create_review_bundle_json(
                    manifest_path,
                    scan_path=scan_path,
                    checkpoint_id=checkpoint_id,
                    previous_manifest=previous_manifest,
                    receipt_paths=receipt,
                    output_path=output_path,
                )
            )
            return
        payload = create_review_bundle(
            manifest_path,
            scan_path=scan_path,
            checkpoint_id=checkpoint_id,
            previous_manifest=previous_manifest,
            receipt_paths=receipt,
            output_path=output_path,
        )
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="not_found",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="invalid_json",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except EvidenceSigningError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="invalid_receipt",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="internal_error",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    included_components = [
        component
        for component in (
            "audit_manifest",
            "scan_results",
            "checkpoint_metadata",
            "diff",
            "evidence_receipts",
        )
        if payload[component] is not None
    ]
    target = output_path or "<not written>"
    typer.echo(
        f"Created review bundle {target} "
        f"(components={','.join(included_components)}, bundle_sha256={payload['bundle_sha256']})"
    )


@review_bundle_app.command("verify")
def review_bundle_verify(
    bundle_path: str = typer.Argument(..., help="Path to the review bundle JSON file."),
    signing_key: str | None = typer.Option(
        None,
        "--signing-key",
        help="Optional HMAC signing key path to verify the embedded manifest's signature.",
    ),
    against: str | None = typer.Option(
        None,
        "--against",
        help="Git ref each embedded evidence receipt's revision must match (commit_sha equal, "
        "dirty=false) to be considered fresh; an unresolvable ref fails closed. In GitHub Actions "
        "use the pull_request event's head SHA (github.event.pull_request.head.sha) -- NEVER the "
        "workflow's own GITHUB_SHA, which resolves to the merge commit, not the PR head, and "
        "would read every receipt as stale on every PR.",
    ),
    trusted_key: list[str] | None = typer.Option(
        None,
        "--trusted-key",
        help="A pinned base64 Ed25519 public key (from `tg evidence pubkey`) to trust when "
        "verifying embedded evidence_receipts. Repeatable. Falls back to "
        "TG_EVIDENCE_TRUSTED_KEYS (comma-separated).",
    ),
    require_trusted: bool = typer.Option(
        False,
        "--require-trusted",
        help="Fail closed (valid=false) unless every embedded receipt's key matches a "
        "--trusted-key. Without this flag, an untrusted key is still reported (key_trusted=false) "
        "but does not by itself fail `valid`.",
    ),
    min_receipts: int = typer.Option(
        0,
        "--min-receipts",
        help="Fail closed (valid=false) unless at least N embedded evidence receipts are "
        "present, well-formed, and pass signature/trust/freshness verification. Default 0 = no "
        "enforcement (back-compat: a receipt-less bundle still verifies valid=true). Without "
        "this, a bundle with its receipts stripped and checksums recomputed passes with NO "
        "evidence -- this is the org's real policy lever to require genuine, current evidence.",
    ),
    expect_key: list[str] | None = typer.Option(
        None,
        "--expect-key",
        help="A key_id (from `tg evidence pubkey`, e.g. sha256:<hex>) that must sign at least "
        "one valid embedded receipt. Repeatable; ALL named key_ids must each be represented by a "
        "valid receipt, or verification fails closed (valid=false). Pins WHICH signer(s) are "
        "required, distinct from --trusted-key which pins which signers are cryptographically "
        "trusted at all.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured verification JSON.",
    ),
) -> None:
    """Verify review bundle integrity and component checksums."""
    from tensor_grep.cli.audit_manifest import verify_review_bundle, verify_review_bundle_json
    from tensor_grep.cli.evidence_signing import resolve_trusted_public_keys

    try:
        trusted_keys = resolve_trusted_public_keys(trusted_key)
        # Least-surprise guard, mirrors `tg evidence verify`: supplying a specific trusted key
        # strongly implies intent to ENFORCE it, but without --require-trusted an untrusted
        # embedded receipt key still yields valid=true for that receipt (only key_trusted=false).
        # Warn VISIBLY on stderr (never touching --json stdout, never silently changing `valid`).
        if trusted_keys and not require_trusted:
            typer.echo(
                "warning: --trusted-key/TG_EVIDENCE_TRUSTED_KEYS supplied without "
                "--require-trusted; an untrusted embedded receipt key will still report "
                "valid=true for that receipt. Pass --require-trusted to enforce.",
                err=True,
            )
        if json_output:
            json_text = verify_review_bundle_json(
                bundle_path,
                signing_key=signing_key,
                against=against,
                trusted_public_keys=trusted_keys,
                require_trusted=require_trusted,
                min_receipts=min_receipts,
                expect_key_ids=expect_key,
            )
            typer.echo(json_text)
            # Mirror the text path: a tampered/invalid bundle must exit 1 even in
            # --json mode (audit H1) so callers can gate on the process status.
            if not json.loads(json_text).get("valid", False):
                raise typer.Exit(code=1)
            return
        payload = verify_review_bundle(
            bundle_path,
            signing_key=signing_key,
            against=against,
            trusted_public_keys=trusted_keys,
            require_trusted=require_trusted,
            min_receipts=min_receipts,
            expect_key_ids=expect_key,
        )
    except typer.Exit:
        raise
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="not_found",
                        routing_reason="review-bundle-verify",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="invalid_json",
                        routing_reason="review-bundle-verify",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="internal_error",
                        routing_reason="review-bundle-verify",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Review bundle: {payload['bundle_path']}")
    typer.echo(f"valid={payload['valid']}")
    for component, check in cast(dict[str, dict[str, object]], payload["checks"]).items():
        typer.echo(
            f"{component}: valid={check['valid']} "
            f"expected={check['expected']} actual={check['actual']}"
        )
    bundle_integrity = cast(dict[str, object], payload["bundle_integrity"])
    typer.echo(
        "bundle_integrity="
        f"{bundle_integrity['valid']} "
        f"expected={bundle_integrity['expected']} actual={bundle_integrity['actual']}"
    )
    against_check = payload.get("against")
    if isinstance(against_check, dict):
        typer.echo(
            f"against={against_check['ref']} valid={against_check['valid']} "
            f"resolved_commit_sha={against_check['resolved_commit_sha']}"
        )
        if against_check.get("error"):
            typer.echo(f"  against_error: {against_check['error']}")
    for receipt_check in cast(list[dict[str, object]], payload.get("receipts") or []):
        typer.echo(
            f"receipt[{receipt_check['index']}]: valid={receipt_check['valid']} "
            f"receipt_sha256={receipt_check.get('receipt_sha256')}"
        )
        freshness = receipt_check.get("freshness")
        if isinstance(freshness, dict) and freshness.get("error"):
            typer.echo(f"  freshness_error: {freshness['error']}")
        signature = receipt_check.get("signature")
        if isinstance(signature, dict) and signature.get("errors"):
            for signature_error in cast(list[object], signature["errors"]):
                typer.echo(f"  signature_error: {signature_error}")
    policy = payload.get("policy")
    if isinstance(policy, dict):
        typer.echo(
            f"policy: min_receipts={policy['min_receipts']} "
            f"valid_receipt_count={policy['valid_receipt_count']} "
            f"expect_key_ids={policy['expect_key_ids']} valid={policy['valid']}"
        )
        for policy_reason in cast(list[object], policy.get("reasons") or []):
            typer.echo(f"  policy_reason: {policy_reason}")
    if not payload["valid"]:
        raise typer.Exit(code=1)


def _evidence_error_payload(message: str, *, code: str, routing_reason: str) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "EvidenceReceipt",
        "routing_reason": routing_reason,
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


@evidence_app.command("emit")
def evidence_emit(
    path: str = typer.Argument(
        ".", help="Repository path to bind the receipt's revision identity to."
    ),
    query: str | None = typer.Option(None, "--query", help="Symbol/query this receipt is about."),
    manifest_path: str | None = typer.Option(
        None,
        "--manifest",
        help="Path to a prior rewrite-audit-manifest JSON (changes/validation-outcomes/rollback).",
    ),
    capsule_path: str | None = typer.Option(
        None,
        "--capsule",
        help="Path to a prior `tg agent --json` capsule (blast-radius/ambiguity/confidence).",
    ),
    checkpoint_id: str | None = typer.Option(
        None,
        "--checkpoint-id",
        help="Optional checkpoint ID for rollback info when --manifest has no checkpoint block.",
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent-id",
        help="Caller-supplied agent identifier, recorded verbatim (never inferred). "
        "Falls back to TG_EVIDENCE_AGENT_ID.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Caller-supplied model identifier, recorded verbatim (never inferred). "
        "Falls back to TG_EVIDENCE_MODEL.",
    ),
    cost_json: str | None = typer.Option(
        None,
        "--cost-json",
        help="Path to caller-supplied cost JSON, recorded verbatim (never inferred). "
        "Falls back to TG_EVIDENCE_COST_JSON.",
    ),
    recompute: bool = typer.Option(
        False,
        "--recompute",
        help="OPT-IN: recompute blast-radius for --query instead of aggregating only. "
        "OFF by default (performance contract: no re-scan unless explicitly requested).",
    ),
    sign: bool = typer.Option(
        False,
        "--sign",
        help="Ed25519-sign the receipt. FAILS CLOSED: a non-zero exit and NO receipt is written "
        "(to --out or stdout) if no signing key resolves -- never emits unsigned when --sign was "
        "requested.",
    ),
    signing_key: str | None = typer.Option(
        None,
        "--signing-key",
        help="Path to the Ed25519 private key used with --sign. Falls back to "
        "TG_EVIDENCE_SIGNING_KEY, then ~/.tensor-grep/keys/evidence_ed25519.key.",
    ),
    previous: str | None = typer.Option(
        None,
        "--previous",
        help="Path to a prior receipt to chain to; attaches previous_receipt_sha256 "
        "(independent of --sign).",
    ),
    output_path: str | None = typer.Option(
        None, "--out", help="Optional file path where the receipt JSON should be written."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the receipt as structured JSON to stdout."
    ),
) -> None:
    """Emit a versioned EvidenceReceipt aggregating tg's existing outputs (no re-scan)."""
    from tensor_grep.cli.evidence_receipt import build_evidence_receipt
    from tensor_grep.cli.evidence_signing import EvidenceSigningError

    try:
        receipt = build_evidence_receipt(
            path,
            query=query,
            manifest_path=manifest_path,
            capsule_path=capsule_path,
            checkpoint_id=checkpoint_id,
            agent_id=agent_id,
            model=model,
            cost_json_path=cost_json,
            recompute=recompute,
            sign=sign,
            signing_key_path=signing_key,
            previous_receipt_path=previous,
        )
    except EvidenceSigningError as exc:
        # Backend Fail-Closed Contract: --sign with no resolvable key (or a broken crypto
        # install) lands here -- NEVER falls through to the write-to-`--out`/stdout step below.
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="signing_error", routing_reason="evidence-receipt-emit"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="internal_error", routing_reason="evidence-receipt-emit"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if output_path is not None:
        from tensor_grep.cli.session_store import _write_json_atomic

        try:
            # audit C4 / CWE-59: check for a symlink BEFORE `.resolve()` -- resolving first
            # would follow the symlink to its real target and make `is_symlink()` on the
            # result always False, silently defeating `_write_json_atomic`'s own symlink guard
            # (mirrors evidence_signing.generate_keypair's identical ordering fix).
            # `_write_json_atomic` also makes this write atomic (temp file + fsync +
            # os.replace) instead of the previous bare `write_text`, which could leave a
            # truncated receipt on a crash mid-write.
            expanded_output = Path(output_path).expanduser()
            if expanded_output.is_symlink():
                raise OSError(
                    f"Refusing to write the evidence receipt through a symlink: {expanded_output}"
                )
            resolved_output = expanded_output.resolve()
            _write_json_atomic(resolved_output, receipt)
        except OSError as exc:
            # Backend Fail-Closed Contract: never leak a raw traceback for a refused/failed
            # write -- report the same structured error shape the two except blocks above use.
            if json_output:
                typer.echo(
                    json.dumps(
                        _evidence_error_payload(
                            str(exc), code="write_error", routing_reason="evidence-receipt-emit"
                        ),
                        indent=2,
                    )
                )
            else:
                typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(receipt, indent=2))
        return

    target = output_path or "<stdout only>"
    revision = cast(dict[str, object], receipt.get("revision", {}))
    typer.echo(f"Evidence receipt ({target}):")
    typer.echo(f"  commit_sha={revision.get('commit_sha', '<unavailable>')}")
    typer.echo(f"  dirty={revision.get('dirty', '<unavailable>')}")
    for block_name in ("scope", "blast_radius", "confidence", "validation", "changes", "caller"):
        block = receipt.get(block_name)
        status = block.get("status", "unknown") if isinstance(block, dict) else "unknown"
        typer.echo(f"  {block_name}.status={status}")
    signing_block = receipt.get("signing")
    if isinstance(signing_block, dict):
        typer.echo(f"  signed=True key_id={signing_block.get('key_id')}")
    else:
        typer.echo(f"  signed=False receipt_sha256={receipt.get('receipt_sha256')}")


@evidence_app.command("verify")
def evidence_verify(
    receipt_path: str = typer.Argument(..., help="Path to an EvidenceReceipt JSON file to verify."),
    trusted_key: list[str] | None = typer.Option(
        None,
        "--trusted-key",
        help="A pinned base64 Ed25519 public key (from `tg evidence pubkey`) to trust. "
        "Repeatable. Falls back to TG_EVIDENCE_TRUSTED_KEYS (comma-separated).",
    ),
    require_trusted: bool = typer.Option(
        False,
        "--require-trusted",
        help="Fail closed (valid=false) unless the embedded key matches a --trusted-key. "
        "Without this flag, an untrusted key is still reported (key_trusted=false) but does not "
        "by itself fail `valid`.",
    ),
    previous_path: str | None = typer.Option(
        None,
        "--previous",
        help="Path to the prior receipt this one should chain to; checks previous_receipt_sha256.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the verify result as JSON."),
) -> None:
    """Verify an EvidenceReceipt's digest, signature, trust, and (optional) chain link."""
    from tensor_grep.cli.evidence_receipt import verify_evidence_receipt
    from tensor_grep.cli.evidence_signing import EvidenceSigningError, resolve_trusted_public_keys

    try:
        trusted_keys = resolve_trusted_public_keys(trusted_key)
        # Least-surprise guard: supplying a specific trusted key strongly implies intent to ENFORCE
        # it, but without --require-trusted an embedded attacker key still yields valid=true (only
        # key_trusted=false). Warn VISIBLY on stderr (never touching --json stdout, never silently
        # changing `valid`) so the caller notices the un-enforced trust. ASCII-only (Windows cp1252).
        if trusted_keys and not require_trusted:
            typer.echo(
                "warning: --trusted-key/TG_EVIDENCE_TRUSTED_KEYS supplied without "
                "--require-trusted; an untrusted key will still report valid=true. Pass "
                "--require-trusted to enforce.",
                err=True,
            )
        payload = verify_evidence_receipt(
            receipt_path,
            trusted_public_keys=trusted_keys,
            require_trusted=require_trusted,
            previous_receipt_path=previous_path,
        )
    except EvidenceSigningError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="signing_error", routing_reason="evidence-receipt-verify"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="internal_error", routing_reason="evidence-receipt-verify"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"Evidence receipt: {payload['receipt_path']}")
        typer.echo(f"  valid={payload['valid']}")
        typer.echo(f"  signed={payload['signed']}")
        checks = cast(dict[str, object], payload["checks"])
        typer.echo(
            f"  digest_valid={checks['digest_valid']} signature_valid={checks['signature_valid']} "
            f"key_trusted={checks['key_trusted']}"
        )
        if payload.get("key_id"):
            typer.echo(f"  key_id={payload['key_id']}")
        chain = payload.get("chain")
        if isinstance(chain, dict):
            typer.echo(f"  chain_valid={chain['chain_valid']}")
            # Surface WHY the chain failed (e.g. an oversized/missing --previous file, or a digest
            # mismatch) in text mode too, not only in --json -- otherwise a bounded-read refusal or
            # a mismatch reads as a bare `chain_valid=False` with no actionable reason.
            chain_error = chain.get("chain_error")
            if chain_error:
                typer.echo(f"  chain_error: {chain_error}")
        for error in cast(list[object], payload.get("errors") or []):
            typer.echo(f"  error: {error}")

    if not payload["valid"]:
        raise typer.Exit(code=1)


@evidence_app.command("keygen")
def evidence_keygen(
    out_path: str | None = typer.Option(
        None,
        "--out",
        help="Where to write the private key. Defaults to TG_EVIDENCE_SIGNING_KEY or "
        "~/.tensor-grep/keys/evidence_ed25519.key.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing key file at the target path."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the keygen result as JSON."),
) -> None:
    """Generate a new Ed25519 signing keypair for `tg evidence emit --sign`."""
    from tensor_grep.cli.evidence_receipt import keygen_evidence_receipt
    from tensor_grep.cli.evidence_signing import EvidenceSigningError

    try:
        payload = keygen_evidence_receipt(out_path, force=force)
    except EvidenceSigningError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="signing_error", routing_reason="evidence-receipt-keygen"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"Private key: {payload['private_key_path']}")
    typer.echo(f"Public key:  {payload['public_key_path']}")
    typer.echo(f"key_id={payload['key_id']}")
    typer.echo(f"public_key={payload['public_key']}")
    typer.echo("Register the key_id/public_key with your downstream verifier (e.g. gotcontext).")


@evidence_app.command("pubkey")
def evidence_pubkey(
    signing_key: str | None = typer.Option(
        None,
        "--signing-key",
        help="Path to the private key file. Defaults to TG_EVIDENCE_SIGNING_KEY or "
        "~/.tensor-grep/keys/evidence_ed25519.key.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the pubkey result as JSON."),
) -> None:
    """Print the public key + key_id for the resolved signing key (for verifier registration)."""
    from tensor_grep.cli.evidence_receipt import pubkey_evidence_receipt
    from tensor_grep.cli.evidence_signing import EvidenceSigningError

    try:
        payload = pubkey_evidence_receipt(signing_key)
    except EvidenceSigningError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="signing_error", routing_reason="evidence-receipt-pubkey"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"key_id={payload['key_id']}")
    typer.echo(f"public_key={payload['public_key']}")


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


@ledger_app.command("claim")
def ledger_claim(
    path: str = typer.Argument(
        ".",
        help="Repository subtree this claim is scoped to (e.g. 'core/hooks', or '.' for the "
        "whole repo). Recorded as the claim's `scope` -- claim/list/release all canonicalize "
        "to the SAME repository root regardless of which subtree PATH names, so `tg ledger "
        "list` (broader/ancestor PATH, e.g. the default '.') rolls this claim up. See "
        "`tg ledger --help`.",
    ),
    symbol: list[str] = typer.Option(
        [], "--symbol", help="Symbol name to claim. Repeatable (--symbol A --symbol B)."
    ),
    files: str | None = typer.Option(
        None,
        "--files",
        help="Comma-separated root-relative file paths to claim (e.g. --files a.py,b.py).",
    ),
    intent: str = typer.Option(
        "edit", "--intent", help='Caller-declared intent, e.g. "edit", "review".'
    ),
    ttl: int | None = typer.Option(
        None,
        "--ttl",
        help="Claim TTL in seconds. Defaults to TG_LEDGER_CLAIM_TTL_SECONDS or 900.",
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent-id",
        help="Caller-supplied agent identifier, recorded verbatim (never inferred). Falls "
        "back to TG_LEDGER_AGENT_ID, then TG_EVIDENCE_AGENT_ID. Do not put secrets here.",
    ),
    note: str | None = typer.Option(
        None,
        "--note",
        help="Free-text note recorded verbatim. Do not put secrets here.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """EXPERIMENTAL (Slice 1): record an advisory claim on symbols/files and report live
    overlaps from other agents. Surface and JSON schema may change in a minor release.

    PATH SCOPING: PATH is this claim's `scope`, not an isolated storage location -- claim/
    list/release all canonicalize to the SAME repository root (the nearest `.git` ancestor)
    regardless of which subtree PATH names, so `tg ledger list` (default PATH `.`) always
    rolls up every live claim in the repo, each tagged with its own `scope`.

    ADVISORY ONLY: this never blocks an edit. It always exits 0 on success -- even when
    other agents hold live overlapping claims, which are reported in `overlaps` for the
    caller to act on. It exits 2 only on a fail-closed condition (lock timeout, a `--files`
    entry outside the repo root, or a write failure); nothing is written when that happens.
    """
    from tensor_grep.cli import _index_lock, ledger_store

    file_list = [item.strip() for item in files.split(",") if item.strip()] if files else []

    try:
        result = ledger_store.submit_claim(
            path,
            symbols=list(symbol),
            files=file_list,
            intent=intent,
            note=note,
            ttl_seconds=ttl,
            agent_id=agent_id,
        )
    except ledger_store.LedgerError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="fail_closed", routing_reason="ledger-claim"
        )
        raise typer.Exit(code=2) from exc
    except _index_lock.IndexLockTimeoutError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="lock_timeout", routing_reason="ledger-claim"
        )
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="write_error", routing_reason="ledger-claim"
        )
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="internal_error", routing_reason="ledger-claim"
        )
        raise typer.Exit(code=2) from exc

    payload = {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": "ledger-claim",
        "sidecar_used": False,
        "ledger_schema_version": ledger_store.LEDGER_SCHEMA_VERSION,
        "advisory": True,
        "claim": result["claim"],
        "overlaps": result["overlaps"],
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    claim = cast(dict[str, object], result["claim"])
    overlaps = cast(list[dict[str, object]], result["overlaps"])
    typer.echo(f"Claim {claim['claim_id']} recorded for agent={claim['agent_id']}")
    typer.echo(f"  scope={claim['scope']} symbols={claim['symbols']} files={claim['files']}")
    typer.echo(f"  expires_at={claim['expires_at']}")
    if overlaps:
        typer.echo(f"  overlaps: {len(overlaps)} live claim(s) from other agents")
        for overlap in overlaps:
            typer.echo(
                f"    {overlap['claim_id']} agent={overlap['agent_id']} "
                f"intent={overlap['intent']} expires_at={overlap['expires_at']}"
            )
    else:
        typer.echo("  overlaps: none")


@ledger_app.command("release")
def ledger_release(
    path: str = typer.Argument(
        ".",
        help="Repository subtree used to resolve the SHARED repo-wide claims index (claim/"
        "list/release all canonicalize to the SAME repository root regardless of which "
        "subtree PATH names). PATH does NOT filter which claim gets released -- "
        "`--claim-id`/`--symbol` do that. See `tg ledger --help`.",
    ),
    claim_id: str | None = typer.Option(
        None, "--claim-id", help="Release the claim with this exact ID."
    ),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Release the caller's own live claim(s) covering this symbol.",
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent-id",
        help="Caller-supplied agent identifier used to scope --symbol release. Falls back "
        "to TG_LEDGER_AGENT_ID, then TG_EVIDENCE_AGENT_ID.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """EXPERIMENTAL (Slice 1): release a claim by exact `--claim-id` (any caller who knows
    the id) or by `--symbol` (scoped to the resolved `--agent-id`'s own claims only). Surface
    and JSON schema may change in a minor release.

    PATH SCOPING: PATH only resolves WHICH repository's shared claims index this call
    operates on (canonicalized to the nearest `.git` ancestor regardless of subtree) -- it
    never filters which claim matches. Always exits 0 on success, including when nothing
    matched -- releasing an already-expired or unknown claim is not an error, and when
    nothing matches, the response names any live claims elsewhere in the repository
    (`unmatched_reason`/`live_claims_elsewhere`) so a wrong `--claim-id`/`--symbol` is
    self-diagnosing instead of a silent no-op. Exits 2 only on a fail-closed condition (lock
    timeout or a write failure)."""
    from tensor_grep.cli import _index_lock, ledger_store

    try:
        result = ledger_store.release_claim(
            path, claim_id=claim_id, symbol=symbol, agent_id=agent_id
        )
    except ledger_store.LedgerError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="fail_closed", routing_reason="ledger-release"
        )
        raise typer.Exit(code=2) from exc
    except _index_lock.IndexLockTimeoutError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="lock_timeout", routing_reason="ledger-release"
        )
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="write_error", routing_reason="ledger-release"
        )
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="internal_error", routing_reason="ledger-release"
        )
        raise typer.Exit(code=2) from exc

    released = cast(list[dict[str, object]], result["released"])
    live_claims_elsewhere = cast(list[dict[str, object]], result["live_claims_elsewhere"])
    payload = {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": "ledger-release",
        "sidecar_used": False,
        "ledger_schema_version": ledger_store.LEDGER_SCHEMA_VERSION,
        "advisory": True,
        "released": released,
        "released_count": result["released_count"],
        "listed_scope": result["listed_scope"],
        "unmatched_reason": result["unmatched_reason"],
        "live_claims_elsewhere": live_claims_elsewhere,
        "live_claims_elsewhere_count": result["live_claims_elsewhere_count"],
        "live_claims_elsewhere_truncated": result["live_claims_elsewhere_truncated"],
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    if not released:
        typer.echo(f"No matching live claim found (listed_scope={result['listed_scope']!r}).")
        if live_claims_elsewhere:
            typer.echo(
                f"  {result['live_claims_elsewhere_count']} live claim(s) exist elsewhere in "
                "this repository:"
            )
            for entry in live_claims_elsewhere:
                typer.echo(
                    f"    {entry['claim_id']} agent={entry['agent_id']} scope={entry['scope']} "
                    f"symbols={entry['symbols']}"
                )
            if result["live_claims_elsewhere_truncated"]:
                typer.echo("    ... (truncated)")
        else:
            typer.echo("  No live claims exist for this repository.")
        return
    for entry in released:
        typer.echo(
            f"Released {entry['claim_id']} (agent={entry['agent_id']}, scope={entry['scope']})"
        )


@ledger_app.command("list")
def ledger_list(
    path: str = typer.Argument(
        ".",
        help="Repository subtree to list claims within. ROLLS UP: listing a broader/ancestor "
        "path (e.g. '.', the default) shows every live claim scoped to it OR to any "
        "descendant subtree -- e.g. `tg ledger claim core/hooks` shows up in `tg ledger "
        "list` (default '.') and in `tg ledger list core`, but not in `tg ledger list docs`. "
        "See `tg ledger --help`.",
    ),
    symbol: str | None = typer.Option(
        None, "--symbol", help="Filter to live claims covering this symbol."
    ),
    agent_id: str | None = typer.Option(
        None, "--agent-id", help="Filter to live claims from this agent."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """EXPERIMENTAL (Slice 1): list live (non-expired) claims scoped to PATH or to any of its
    descendant subtrees (rollup -- each returned claim carries its own `scope`). Surface and
    JSON schema may change in a minor release. Always exits 0, including an empty result --
    an empty claims list is a normal outcome, not a not-found error."""
    from tensor_grep.cli import ledger_store

    try:
        result = ledger_store.list_claims(path, symbol=symbol, agent_id=agent_id)
    except ledger_store.LedgerError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="fail_closed", routing_reason="ledger-list"
        )
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="read_error", routing_reason="ledger-list"
        )
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="internal_error", routing_reason="ledger-list"
        )
        raise typer.Exit(code=2) from exc

    claims = cast(list[dict[str, object]], result["claims"])
    payload = {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": "ledger-list",
        "sidecar_used": False,
        "ledger_schema_version": ledger_store.LEDGER_SCHEMA_VERSION,
        "advisory": True,
        "claims": claims,
        "count": result["count"],
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    if not claims:
        typer.echo(f"No live claims (listed path={path!r}, includes descendant subtrees).")
        return
    typer.echo(f"Live claims within path={path!r} (includes descendant subtrees):")
    for entry in claims:
        typer.echo(
            f"{entry['claim_id']}  agent={entry['agent_id']}  scope={entry.get('scope')}  "
            f"symbols={entry.get('symbols')}  files={entry.get('files')}  "
            f"expires_at={entry.get('expires_at')}"
        )


@ledger_app.command("record")
def ledger_record(
    path: str = typer.Argument(".", help="Repository path the finding is scoped to."),
    receipt: str | None = typer.Option(
        None,
        "--receipt",
        help="Path to the evidence-receipt/blast-radius/context-pack/repo-map artifact JSON "
        "to ingest.",
    ),
    artifact_kind: str = typer.Option(
        "evidence-receipt",
        "--artifact-kind",
        help="One of: blast-radius, evidence-receipt, context-pack, repo-map.",
    ),
    symbol: str | None = typer.Option(
        None, "--symbol", help="Symbol name or query string this artifact answers."
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent-id",
        help="Caller-supplied agent identifier, recorded verbatim (never inferred). Falls "
        "back to TG_LEDGER_AGENT_ID, then TG_EVIDENCE_AGENT_ID. Do not put secrets here.",
    ),
    ttl: int | None = typer.Option(
        None,
        "--ttl",
        help="Finding TTL in seconds. Defaults to TG_LEDGER_FINDING_TTL_SECONDS or 86400 (24h).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """EXPERIMENTAL (Slice 2): ingest an evidence-receipt/blast-radius/context-pack/repo-map
    artifact JSON as a content-addressed finding pointer a sibling agent can `tg ledger find`
    and reuse instead of recomputing. Surface and JSON schema may change in a minor release.

    The artifact is stored once at `findings/blobs/<receipt_sha256>.json`, content-addressed
    by `receipt_sha256` (the same digest `tg evidence` uses) -- recording an identical artifact
    twice dedupes to the same blob. Exits 0 on success. Exits 2 only on a fail-closed condition
    (missing `--receipt`, an invalid `--artifact-kind`, a missing/oversized/non-JSON artifact
    file, a lock timeout, or a write failure); nothing is written when that happens.
    """
    from tensor_grep.cli import _index_lock, ledger_store

    try:
        result = ledger_store.record_finding(
            path,
            receipt_path=receipt,
            artifact_kind=artifact_kind,
            symbol=symbol,
            agent_id=agent_id,
            ttl_seconds=ttl,
        )
    except ledger_store.LedgerError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="fail_closed", routing_reason="ledger-record"
        )
        raise typer.Exit(code=2) from exc
    except _index_lock.IndexLockTimeoutError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="lock_timeout", routing_reason="ledger-record"
        )
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="write_error", routing_reason="ledger-record"
        )
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="internal_error", routing_reason="ledger-record"
        )
        raise typer.Exit(code=2) from exc

    payload = {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": "ledger-record",
        "sidecar_used": False,
        "ledger_schema_version": ledger_store.LEDGER_SCHEMA_VERSION,
        "advisory": True,
        "finding": result["finding"],
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    finding = cast(dict[str, object], result["finding"])
    typer.echo(
        f"Finding {finding['finding_id']} recorded (artifact_kind={finding['artifact_kind']})"
    )
    typer.echo(f"  symbol={finding.get('symbol')} receipt_sha256={finding['receipt_sha256']}")
    typer.echo(f"  signed={finding['signed']} expires_at={finding['expires_at']}")


@ledger_app.command("find")
def ledger_find(
    path: str = typer.Argument(".", help="Repository path the findings are scoped to."),
    symbol: str | None = typer.Option(
        None, "--symbol", help="Symbol name or query string to look up."
    ),
    artifact_kind: str | None = typer.Option(
        None,
        "--artifact-kind",
        help="Restrict to one of: blast-radius, evidence-receipt, context-pack, repo-map.",
    ),
    fresh_only: bool = typer.Option(
        False,
        "--fresh-only",
        help="Return only findings whose captured revision matches the current repo state.",
    ),
    trusted_key: list[str] | None = typer.Option(
        None,
        "--trusted-key",
        help="A pinned base64 Ed25519 public key (from `tg evidence pubkey`) to trust when "
        "checking a signed finding's key_trusted. Repeatable. Falls back to "
        "TG_EVIDENCE_TRUSTED_KEYS (comma-separated).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """EXPERIMENTAL (Slice 2): look up previously recorded findings for `--symbol` so a
    sibling agent can reuse a prior artifact instead of recomputing it -- `if tg ledger find
    PATH --symbol S --fresh-only; then reuse; else compute; fi`. Surface and JSON schema may
    change in a minor release.

    Exit-code contract (3-state, mirrors the `tg defs`/`callers`/`blast-radius` symbol-command
    contract -- distinct from claim/release/list, which are 0/2 only): `0` = at least one
    returned finding is `fresh` (its captured revision matches the current repo state -- safe
    to reuse); `1` = no returned finding is fresh (recompute) -- covers both "nothing matched"
    and "matches exist but none are fresh"; `2` = a fail-closed condition fired (missing
    `--symbol`, a corrupt/oversized index, or a tampered/unreadable blob -- a corrupted finding
    is refused, never silently served or silently skipped). Every finding actually returned is
    integrity-checked against its recorded `receipt_sha256` before being served, regardless of
    exit code.
    """
    from tensor_grep.cli import ledger_store
    from tensor_grep.cli.evidence_signing import resolve_trusted_public_keys

    resolved_trusted_keys = resolve_trusted_public_keys(trusted_key)

    try:
        result = ledger_store.find_findings(
            path,
            symbol=symbol or "",
            artifact_kind=artifact_kind,
            fresh_only=fresh_only,
            trusted_public_keys=resolved_trusted_keys or None,
        )
    except ledger_store.LedgerError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="fail_closed", routing_reason="ledger-find"
        )
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="read_error", routing_reason="ledger-find"
        )
        raise typer.Exit(code=2) from exc
    except Exception as exc:
        _emit_ledger_error(
            exc, json_output=json_output, code="internal_error", routing_reason="ledger-find"
        )
        raise typer.Exit(code=2) from exc

    findings = cast(list[dict[str, object]], result["findings"])
    any_fresh = bool(result["any_fresh"])
    exit_code = 0 if any_fresh else 1
    payload = {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "Ledger",
        "routing_reason": "ledger-find",
        "sidecar_used": False,
        "ledger_schema_version": ledger_store.LEDGER_SCHEMA_VERSION,
        "advisory": True,
        "findings": findings,
        "count": result["count"],
        "any_fresh": any_fresh,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        raise typer.Exit(code=exit_code)

    if not findings:
        typer.echo("No matching findings.")
    else:
        for entry in findings:
            typer.echo(
                f"{entry['finding_id']}  fresh={entry['fresh']}  "
                f"artifact_kind={entry['artifact_kind']}  agent={entry['agent_id']}  "
                f"receipt_sha256={entry['receipt_sha256']}"
            )
    raise typer.Exit(code=exit_code)


@app.command("update")
def update() -> None:
    """Alias for upgrade."""
    _self.upgrade()


@app.command(name="ast-info")
def ast_info(
    json_output: bool = typer.Option(
        False, "--json", help="Output supported AST languages as JSON."
    ),
) -> None:
    """List supported AST language identifiers."""
    from tensor_grep.cli.ast_workflows import ast_info_command

    ast_info_command(json_output=json_output)


@app.command(
    name="run",
    help=(
        "Run a validated AST slice for structural search and guarded rewrites. "
        "PowerShell users should single-quote AST patterns containing $ captures, "
        "for example 'def $NAME($$$ARGS): $$$BODY'. When the ast-grep `sg` binary is on "
        "PATH, the pattern is delegated to it verbatim (full $NAME/$$$ARGS/--selector/"
        "--strictness compatibility); without `sg`, a native-shaped pattern still runs "
        "through tg's own tree-sitter backend, but a $-metavariable pattern needs `sg` and "
        "fails with a clean error instead of a mistranslated result."
    ),
)
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


@app.command(hidden=True)
def worker(
    port: int | None = typer.Option(None, "--port", help="Port to bind the TCP worker."),
    stop: bool = typer.Option(False, "--stop", help="Stop the active resident worker."),
) -> None:
    """Internal command to manage the experimental Resident AST Worker."""
    native_tg_binary = _self.resolve_native_tg_binary()
    if native_tg_binary is None:
        typer.echo("Error: native tg binary not found for worker command.", err=True)
        raise typer.Exit(2)

    cmd = [str(native_tg_binary), "worker"]
    if port is not None:
        cmd.extend(["--port", str(port)])
    if stop:
        cmd.append("--stop")

    completed = subprocess.run(cmd, check=False)
    raise typer.Exit(int(completed.returncode))


def main_entry() -> None:
    import sys

    # Emulate ripgrep's top-level help behavior and transparent drop-in compatibility.
    # Typer requires an explicit subcommand (like `tg search pattern`).
    # To act exactly like ripgrep (`rg pattern`), we dynamically inject the `search`
    # subcommand into sys.argv if the user didn't provide any recognized subcommand.

    # Check for version flag first
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V", "--pcre2-version"):
        first_arg = sys.argv[1]

        if first_arg == "--pcre2-version":
            candidates = [_self.resolve_native_tg_binary(), _self.resolve_ripgrep_binary()]
            last_completed: subprocess.CompletedProcess[str] | None = None
            for candidate in candidates:
                if not candidate or not candidate.exists():
                    continue
                completed = subprocess.run(
                    [str(candidate), "--pcre2-version"], capture_output=True, text=True
                )
                last_completed = completed
                if completed.returncode == 0:
                    print(completed.stdout.strip())
                    sys.exit(0)
            if last_completed is not None:
                output = last_completed.stderr.strip() or last_completed.stdout.strip()
                if output:
                    print(output, file=sys.stderr)
                sys.exit(last_completed.returncode or 1)
            print(
                "PCRE2 version unavailable: no native tg or ripgrep binary found.",
                file=sys.stderr,
            )
            sys.exit(1)

        _print_version(verbose=any(arg in {"--verbose", "-v"} for arg in sys.argv[2:]))
        sys.exit(0)

    from tensor_grep.cli.commands import KNOWN_COMMANDS as _KNOWN_COMMANDS

    known_commands = _KNOWN_COMMANDS

    if len(sys.argv) == 1:
        _self.app(args=["--help"], prog_name="tg", windows_expand_args=False)
        return

    if len(sys.argv) > 1:
        first_arg = sys.argv[1]
        if (
            first_arg not in ("--help", "-h")
            and first_arg not in known_commands
            and not first_arg.startswith("--typer-")
        ):
            sys.argv.insert(1, "search")

    _self.app(prog_name="tg", windows_expand_args=False)


if __name__ == "__main__":
    main_entry()
