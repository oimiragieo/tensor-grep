import os

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
import time as time
from typing import TYPE_CHECKING

import typer

import tensor_grep.cli.error_handling as _mod_1
import tensor_grep.cli.managed_runtime_upgrade as _managed_runtime_upgrade_mod
import tensor_grep.cli.output_envelope as _mod_0
import tensor_grep.cli.routers.agent as _mod_6
import tensor_grep.cli.routers.agent_planning as _mod_7
import tensor_grep.cli.routers.ast_security as _mod_8
import tensor_grep.cli.routers.evidence as _mod_10
import tensor_grep.cli.routers.find as _mod_4
import tensor_grep.cli.routers.ledger as _mod_11
import tensor_grep.cli.routers.navigate as _mod_5
import tensor_grep.cli.routers.ops as _mod_12
import tensor_grep.cli.routers.search as _mod_3
import tensor_grep.cli.routers.search_helpers as _mod_2
import tensor_grep.cli.routers.state as _mod_9
import tensor_grep.cli.routers.upgrade as _mod_13
from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import ast_scan as _ast_scan
from tensor_grep.cli import backend_fallback as _backend_fallback
from tensor_grep.cli import doctor_payload as _doctor_payload
from tensor_grep.cli import doctor_report as _doctor_report
from tensor_grep.cli import native_frontdoor as _native_frontdoor
from tensor_grep.cli import windows_launcher as _windows_launcher
from tensor_grep.cli._index_lock import atomic_write_bytes_anchored as atomic_write_bytes_anchored
from tensor_grep.cli.completeness_output import (
    _output_limit_note as _output_limit_note,
)
from tensor_grep.cli.dogfood_command import dogfood
from tensor_grep.cli.investigation_options import (
    _note_late_rerank_degraded as _note_late_rerank_degraded,
)
from tensor_grep.cli.investigation_options import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)
from tensor_grep.cli.output_envelope import (
    _EvidenceGroup as _EvidenceGroup,
)
from tensor_grep.cli.output_envelope import (
    _TailExitCodePolicy as _TailExitCodePolicy,
)
from tensor_grep.cli.prepare_service import _build_prepare_payload as _build_prepare_payload
from tensor_grep.cli.repair_env import repair_env_command
from tensor_grep.cli.root_app import create_root_app
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
from tensor_grep.cli.session_resume_service import session_prepare_cmd, session_resume_cmd
from tensor_grep.cli.sql_query import sql_command

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
)

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
if TYPE_CHECKING:
    from tensor_grep.cli import main as _self
else:
    _self = sys.modules[__name__]

if TYPE_CHECKING:
    pass

# Re-exports from the modules split out of this file on 2026-08-20 (see
# cli/_main_binding.py). EVERY moved name is
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
_doctor_tg_foreign_remediation = _doctor_payload._doctor_tg_foreign_remediation
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
app = create_root_app()
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
session_app.command("prepare")(session_prepare_cmd)
session_app.command("resume")(session_resume_cmd)

_search_with_cpu_fallback = _backend_fallback.search_with_cpu_fallback

_DEADLINE_REMEDY = _mod_0._DEADLINE_REMEDY
_MAX_NATIVE_ASSET_DOWNLOAD_BYTES = _mod_0._MAX_NATIVE_ASSET_DOWNLOAD_BYTES
_MERMAID_INCOMPLETE_LABEL_LIMIT = _mod_0._MERMAID_INCOMPLETE_LABEL_LIMIT
_TRUNCATION_REMEDY = _mod_0._TRUNCATION_REMEDY
_VERSION_UNAVAILABLE_SENTINEL = _mod_0._VERSION_UNAVAILABLE_SENTINEL
_ZERO_CALLERS_CAVEAT = _mod_0._ZERO_CALLERS_CAVEAT
_annotate_result_completeness = _mod_0._annotate_result_completeness
_apply_defs_class_filter = _mod_0._apply_defs_class_filter
_attach_symbol_omissions = _mod_0._attach_symbol_omissions
_cli_deadline_monotonic = _mod_0._cli_deadline_monotonic
_cli_package_version = _mod_0._cli_package_version
_completeness_caveat_lines = _mod_0._completeness_caveat_lines
_daemon_blast_radius_no_match_is_unreliable = _mod_0._daemon_blast_radius_no_match_is_unreliable
_deadline_option = _mod_0._deadline_option
_deadline_truncation_message = _mod_0._deadline_truncation_message
_echo_symbol_location_rows = _mod_0._echo_symbol_location_rows
_emit_scan_incompleteness_banner = _mod_0._emit_scan_incompleteness_banner
_emit_symbol_command_result = _mod_0._emit_symbol_command_result
_format_symbol_location_row = _mod_0._format_symbol_location_row
_json_output_version = _mod_0._json_output_version
_looks_like_binary_path = _mod_0._looks_like_binary_path
_maybe_swap_reversed_positionals = _mod_0._maybe_swap_reversed_positionals
_maybe_swap_reversed_session_path = _mod_0._maybe_swap_reversed_session_path
_mermaid_incomplete_label = _mod_0._mermaid_incomplete_label
_mermaid_label = _mod_0._mermaid_label
_mermaid_relpath = _mod_0._mermaid_relpath
_ordered_path_output = _mod_0._ordered_path_output
_path_has_hidden_component = _mod_0._path_has_hidden_component
_path_output_sort_key = _mod_0._path_output_sort_key
_positive_int = _mod_0._positive_int
_print_version = _mod_0._print_version
_query_deprecation_warning = _mod_0._query_deprecation_warning
_read_project_version_fallback = _mod_0._read_project_version_fallback
_render_blast_radius_mermaid = _mod_0._render_blast_radius_mermaid
_resolve_path_and_query = _mod_0._resolve_path_and_query
_resolve_path_and_symbol = _mod_0._resolve_path_and_symbol
_safe_stdout_line = _mod_0._safe_stdout_line
_scan_incomplete = _mod_0._scan_incomplete
_scan_truncation_warning = _mod_0._scan_truncation_warning
_stdout_isatty = _mod_0._stdout_isatty
_symbol_not_found_claim = _mod_0._symbol_not_found_claim
_symbol_payload_has_no_results = _mod_0._symbol_payload_has_no_results
_truncation_message = _mod_0._truncation_message
_version_detail_lines = _mod_0._version_detail_lines
_with_schema_version = _mod_0._with_schema_version
_write_path_list = _mod_0._write_path_list

_LARGE_ROOT_SCAN_FILE_CEILING = _mod_1._LARGE_ROOT_SCAN_FILE_CEILING
_LEADING_INLINE_FLAG_RE = _mod_1._LEADING_INLINE_FLAG_RE
_UNBOUNDED_VENDORED_ROOT_DIR_NAMES = _mod_1._UNBOUNDED_VENDORED_ROOT_DIR_NAMES
_audit_diff_error_payload = _mod_1._audit_diff_error_payload
_audit_history_error_payload = _mod_1._audit_history_error_payload
_combine_multi_patterns = _mod_1._combine_multi_patterns
_config_with_guarded_broad_root_globs = _mod_1._config_with_guarded_broad_root_globs
_eligible_for_pcre2_inline_flag_fallback = _mod_1._eligible_for_pcre2_inline_flag_fallback
_emit_broad_scan_refusal = _mod_1._emit_broad_scan_refusal
_emit_ledger_error = _mod_1._emit_ledger_error
_emit_search_error_json = _mod_1._emit_search_error_json
_engine_is_explicit_pcre2 = _mod_1._engine_is_explicit_pcre2
_evidence_error_payload = _mod_1._evidence_error_payload
_exit_invalid_regex = _mod_1._exit_invalid_regex
_exit_search_error = _mod_1._exit_search_error
_format_broad_generated_scan_error = _mod_1._format_broad_generated_scan_error
_format_broad_workspace_scan_error = _mod_1._format_broad_workspace_scan_error
_format_unbounded_large_root_scan_error = _mod_1._format_unbounded_large_root_scan_error
_format_unbounded_vendored_root_scan_error = _mod_1._format_unbounded_vendored_root_scan_error
_has_generated_scan_bound = _mod_1._has_generated_scan_bound
_has_walk_scope_bound = _mod_1._has_walk_scope_bound
_implicit_glob_search_walk_exceeds_ceiling = _mod_1._implicit_glob_search_walk_exceeds_ceiling
_invalid_regex_remediation = _mod_1._invalid_regex_remediation
_is_inline_flag_regex_error = _mod_1._is_inline_flag_regex_error
_is_invalid_regex_error = _mod_1._is_invalid_regex_error
_ledger_error_payload = _mod_1._ledger_error_payload
_pcre2_fallback_backend_available = _mod_1._pcre2_fallback_backend_available
_read_patterns_from_file_list = _mod_1._read_patterns_from_file_list
_review_bundle_error_payload = _mod_1._review_bundle_error_payload
_root_top_level_vendored_dir_names = _mod_1._root_top_level_vendored_dir_names
_scope_leading_inline_flag = _mod_1._scope_leading_inline_flag
_search_error_payload = _mod_1._search_error_payload
_search_paths_include_guarded_broad_root = _mod_1._search_paths_include_guarded_broad_root
_should_refuse_unbounded_generated_scan = _mod_1._should_refuse_unbounded_generated_scan
_should_refuse_unbounded_large_root_scan = _mod_1._should_refuse_unbounded_large_root_scan
_should_refuse_unbounded_vendored_root_scan = _mod_1._should_refuse_unbounded_vendored_root_scan
_should_refuse_unbounded_workspace_root_scan = _mod_1._should_refuse_unbounded_workspace_root_scan
_sum_total_bytes = _mod_1._sum_total_bytes
_validate_search_regex = _mod_1._validate_search_regex
_workspace_project_child_names = _mod_1._workspace_project_child_names

_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS = _mod_2._NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS
_PLAIN_JSON_INCOMPATIBLE_RENDER_FLAGS = _mod_2._PLAIN_JSON_INCOMPATIBLE_RENDER_FLAGS
_SEARCH_COMMAND_TAIL_EXIT_CODE_POLICIES = _mod_2._SEARCH_COMMAND_TAIL_EXIT_CODE_POLICIES
_build_native_tg_search_command = _mod_2._build_native_tg_search_command
_can_delegate_to_native_tg_search = _mod_2._can_delegate_to_native_tg_search
_can_passthrough_rg = _mod_2._can_passthrough_rg
_collect_candidate_files = _mod_2._collect_candidate_files
_delegate_to_native_tg_search = _mod_2._delegate_to_native_tg_search
_generate_shell_completion_script = _mod_2._generate_shell_completion_script
_normalize_string_list = _mod_2._normalize_string_list
_parse_gpu_device_ids_cli = _mod_2._parse_gpu_device_ids_cli
_plain_json_incompatible_render_flags = _mod_2._plain_json_incompatible_render_flags
_run_rg_compatible_info_action = _mod_2._run_rg_compatible_info_action
_scope_filtered = _mod_2._scope_filtered
_selected_gpu_execution_defaults = _mod_2._selected_gpu_execution_defaults
_selected_route_supports_rg_passthrough = _mod_2._selected_route_supports_rg_passthrough
_warn_unavailable_gpu_device_ids = _mod_2._warn_unavailable_gpu_device_ids

search_command = _mod_3.search_command

_DENSE_FETCH_FRIENDLY_HINT = _mod_4._DENSE_FETCH_FRIENDLY_HINT
_DENSE_FETCH_RAW_HINT = _mod_4._DENSE_FETCH_RAW_HINT
_FIND_CORPUS_CHUNK_CAP = _mod_4._FIND_CORPUS_CHUNK_CAP
_FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT = _mod_4._FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT
_FIND_DENSE_WEIGHT_DEFAULT = _mod_4._FIND_DENSE_WEIGHT_DEFAULT
_FIND_DENSE_WEIGHT_ENV = _mod_4._FIND_DENSE_WEIGHT_ENV
_SEMANTIC_CORPUS_CHUNK_CAP = _mod_4._SEMANTIC_CORPUS_CHUNK_CAP
_apply_semantic_rerank = _mod_4._apply_semantic_rerank
_execute_find = _mod_4._execute_find
_find_combine_mode = _mod_4._find_combine_mode
_find_dense_weight = _mod_4._find_dense_weight
_find_is_single_token_query = _mod_4._find_is_single_token_query
_find_representative_line = _mod_4._find_representative_line
_friendly_dense_unavailable_message = _mod_4._friendly_dense_unavailable_message
find = _mod_4.find

blast_radius = _mod_5.blast_radius
blast_radius_plan = _mod_5.blast_radius_plan
blast_radius_render = _mod_5.blast_radius_render
callers = _mod_5.callers
defs = _mod_5.defs
file_api = _mod_5.file_api
impact = _mod_5.impact
importers = _mod_5.importers
imports = _mod_5.imports
refs = _mod_5.refs
source = _mod_5.source

_daemon_directory_path = _mod_6._daemon_directory_path
_docs_scan_is_unreadable_truncated = _mod_6._docs_scan_is_unreadable_truncated
_maybe_agent_via_running_daemon = _mod_6._maybe_agent_via_running_daemon
_maybe_context_render_via_running_daemon = _mod_6._maybe_context_render_via_running_daemon
_maybe_edit_plan_via_running_daemon = _mod_6._maybe_edit_plan_via_running_daemon
_maybe_orient_via_running_daemon = _mod_6._maybe_orient_via_running_daemon
_maybe_symbol_command_via_running_daemon = _mod_6._maybe_symbol_command_via_running_daemon
_session_daemon_autostart_enabled = _mod_6._session_daemon_autostart_enabled
codemap = _mod_6.codemap
context = _mod_6.context
context_render = _mod_6.context_render
docs_coverage = _mod_6.docs_coverage
inventory = _mod_6.inventory
map = _mod_6.map
orient = _mod_6.orient

_AGENT_DEADLINE_PARTIAL_REASONS = _mod_7._AGENT_DEADLINE_PARTIAL_REASONS
_ROUTE_TEST_CONFIDENCE_FLOOR = _mod_7._ROUTE_TEST_CONFIDENCE_FLOOR
_ROUTE_TEST_CONFIDENCE_WARNING_THRESHOLD = _mod_7._ROUTE_TEST_CONFIDENCE_WARNING_THRESHOLD
_agent_trustworthy_deadline_partial_note = _mod_7._agent_trustworthy_deadline_partial_note
_build_route_test_payload = _mod_7._build_route_test_payload
_emit_agent_text = _mod_7._emit_agent_text
_route_test_confidence_score = _mod_7._route_test_confidence_score
_route_test_int = _mod_7._route_test_int
_route_test_normalized_file = _mod_7._route_test_normalized_file
_route_test_primary_target = _mod_7._route_test_primary_target
_route_test_validation_command_count = _mod_7._route_test_validation_command_count
agent = _mod_7.agent
diff_impact = _mod_7.diff_impact
edit_plan = _mod_7.edit_plan
prepare = _mod_7.prepare
route_test = _mod_7.route_test

_validate_ast_new_name = _mod_8._validate_ast_new_name
ast_info = _mod_8.ast_info
audit_help = _mod_8.audit_help
classify = _mod_8.classify
new = _mod_8.new
rulesets = _mod_8.rulesets
run = _mod_8.run
scan = _mod_8.scan
test = _mod_8.test

checkpoint_create = _mod_9.checkpoint_create
checkpoint_list = _mod_9.checkpoint_list
checkpoint_undo = _mod_9.checkpoint_undo
coverage = _mod_9.coverage
freshness = _mod_9.freshness
session_blast_radius_cmd = _mod_9.session_blast_radius_cmd
session_blast_radius_plan_cmd = _mod_9.session_blast_radius_plan_cmd
session_blast_radius_render_cmd = _mod_9.session_blast_radius_render_cmd
session_context_cmd = _mod_9.session_context_cmd
session_context_render_cmd = _mod_9.session_context_render_cmd
session_daemon_start = _mod_9.session_daemon_start
session_daemon_status = _mod_9.session_daemon_status
session_daemon_stop = _mod_9.session_daemon_stop
session_edit_plan_cmd = _mod_9.session_edit_plan_cmd
session_importers_cmd = _mod_9.session_importers_cmd
session_list = _mod_9.session_list
session_open = _mod_9.session_open
session_refresh = _mod_9.session_refresh
session_serve = _mod_9.session_serve
session_show = _mod_9.session_show

audit_diff = _mod_10.audit_diff
audit_history = _mod_10.audit_history
audit_verify = _mod_10.audit_verify
evidence_emit = _mod_10.evidence_emit
evidence_keygen = _mod_10.evidence_keygen
evidence_pubkey = _mod_10.evidence_pubkey
evidence_verify = _mod_10.evidence_verify
review_bundle_create = _mod_10.review_bundle_create
review_bundle_verify = _mod_10.review_bundle_verify

ledger_claim = _mod_11.ledger_claim
ledger_find = _mod_11.ledger_find
ledger_list = _mod_11.ledger_list
ledger_record = _mod_11.ledger_record
ledger_release = _mod_11.ledger_release

_INSTALL_DENSE_PACKAGE_SPEC = _mod_12._INSTALL_DENSE_PACKAGE_SPEC
_run_install_dense = _mod_12._run_install_dense
calibrate = _mod_12.calibrate
devices = _mod_12.devices
doctor = _mod_12.doctor
install_command = _mod_12.install_command
install_dense = _mod_12.install_dense
lsp = _mod_12.lsp
lsp_setup = _mod_12.lsp_setup
mcp_server = _mod_12.mcp_server
repair_launcher = _mod_12.repair_launcher
uninstall_command = _mod_12.uninstall_command
update = _mod_12.update
worker = _mod_12.worker

_is_uv_tool_managed_python = _mod_13._is_uv_tool_managed_python
_managed_runtime_upgrade = _managed_runtime_upgrade_mod
_run_upgrade = _mod_13._run_upgrade
_upgrade_attempts = _mod_13._upgrade_attempts
upgrade = _mod_13.upgrade

# Register commands onto root and sub-Typer apps

app.command(name="dogfood")(dogfood)
app.command(name="repair-env")(repair_env_command)
app.command(name="sql")(sql_command)
app.add_typer(checkpoint_app, name="checkpoint")
app.add_typer(session_app, name="session")
app.add_typer(review_bundle_app, name="review-bundle")
app.add_typer(evidence_app, name="evidence")
app.add_typer(ledger_app, name="ledger")

_mod_3.register(app)
_mod_4.register(app)
_mod_5.register(app)
_mod_6.register(app)
_mod_7.register(app)
_mod_8.register(app)
_mod_9.register(app, checkpoint_app, session_app, session_daemon_app)
_mod_10.register(app, review_bundle_app, evidence_app)
_mod_11.register(ledger_app)
_mod_12.register(app)
_mod_13.register(app)


def main_entry() -> None:
    import sys

    # Emulate ripgrep's top-level help behavior and transparent drop-in compatibility.
    # Typer requires an explicit subcommand (like `tg search pattern`).
    # To act exactly like ripgrep (`rg pattern`), we dynamically inject the `search`
    # subcommand into sys.argv if the user didn't provide any recognized subcommand.

    # Check for --pcre2-version or --version/-V invocations first
    if len(sys.argv) > 1 and sys.argv[1] == "--pcre2-version":
        from tensor_grep.cli.frontdoor_hops import child_env_or_refusal

        pcre2_env, pcre2_refusal = child_env_or_refusal()
        if pcre2_env is None:
            sys.exit(pcre2_refusal)
        from tensor_grep.cli.subprocess_policy import (
            decode_diagnostic_output,
            decode_protocol_output,
        )

        # TG_REEXEC_GUARD: never hand back to the native door that spawned us (contract A).
        native = None if os.environ.get("TG_REEXEC_GUARD") else _self.resolve_native_tg_binary()
        candidates = [native, _self.resolve_ripgrep_binary()]
        last_completed: subprocess.CompletedProcess[bytes] | None = None
        for candidate in candidates:
            if not candidate or not candidate.exists():
                continue
            completed = subprocess.run(
                [str(candidate), "--pcre2-version"],
                capture_output=True,
                text=False,
                env=pcre2_env,
            )
            last_completed = completed
            if completed.returncode == 0:
                try:
                    output = decode_protocol_output(completed.stdout)
                except UnicodeDecodeError:
                    print("PCRE2 version output was not valid UTF-8.", file=sys.stderr)
                    sys.exit(1)
                print(output.strip())
                sys.exit(0)
        if last_completed is not None:
            output = decode_diagnostic_output(last_completed.stderr).strip() or (
                decode_diagnostic_output(last_completed.stdout).strip()
            )
            if output:
                print(output, file=sys.stderr)
            sys.exit(last_completed.returncode or 1)
        print(
            "PCRE2 version unavailable: no native tg or ripgrep binary found.",
            file=sys.stderr,
        )
        sys.exit(1)

    argv = sys.argv[1:]
    if argv and len(argv) <= 3 and ("--version" in argv or "-V" in argv):
        from tensor_grep.cli.version_info import version_invocation_verbose

        version_verbose = version_invocation_verbose(argv)
        if version_verbose is not None:
            _self._print_version(verbose=version_verbose)
            sys.exit(0)

    from tensor_grep.cli.commands import KNOWN_COMMANDS as _KNOWN_COMMANDS

    known_commands = _KNOWN_COMMANDS

    if len(sys.argv) == 1:
        _self.app(args=["--help"], prog_name="tg", windows_expand_args=False)
        return

    if len(sys.argv) > 1:
        first_arg = sys.argv[1]
        if (
            first_arg not in ("--help", "-h", "--help-all")
            and first_arg not in known_commands
            and not first_arg.startswith("--typer-")
        ):
            sys.argv.insert(1, "search")

    _self.app(prog_name="tg", windows_expand_args=False)


if __name__ == "__main__":
    main_entry()
