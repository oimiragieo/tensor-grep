"""Independent Task 2A Round-60 ownership registry (closed-world census).

This module is the mandatory ownership source of truth for every named Python
and Rust Task 2A contract, including normal-route controls. The closed-world
manifest proof must compare three independent populations:

1. ``TASK2A_OWNED_PYTHON_NODE_IDS`` / ``TASK2A_OWNED_RUST_NODE_IDS`` — static,
   mandatory, complete registries (never derived from AST or the manifest).
2. AST ownership markers on the four Python family files (concrete param IDs
   expanded).
3. ``task2a_windows_node_manifest.json`` node IDs.

Deleting a marker, a static registry row, or a manifest row must each fail
independently; deleting any two must also fail against the third source. The
static Python registry must not silently shrink when AST markers or manifest
rows are thinned.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "task2a_windows_node_manifest.json"

# Marker attribute stamped on every owned Python contract (decorator or attr).
OWNED_ATTR = "_task2a_owned"
WINDOWS_REQUIRED_ATTR = "_task2a_windows_required"

# Four independently runnable RED families.
PYTHON_FAMILY_FILES: tuple[str, ...] = (
    "tests/unit/test_installer_shim_receipt_v1.py",
    "tests/unit/test_search_input_ledger_round60.py",
    "tests/unit/test_win32_path_domain_round60.py",
    "tests/unit/test_native_ci_receipt_v1.py",
)

# Complete static mandatory registry of every owned concrete Python node ID
# across all four families. Order matches the manifest. Never derive this from
# AST markers or the manifest — a thinned marker/manifest must disagree here.
TASK2A_OWNED_PYTHON_NODE_IDS: tuple[str, ...] = (
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_accepts_valid_receipt_positive_control",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[bool_version]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[string_version]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[arbitrary_schema]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[digest_alphabet]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[digest_length]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[list_identity]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[dict_identity]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[bool_identity]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_refuses_corrupt_duplicate_unknown_oversized_deep",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_parser_refuses_install_command_digest_only_receipt",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_no_ambient_discover_magic_without_protected_state",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_protected_state_injection_is_sole_positive_authority",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_cng_binding_negative_and_positive_via_primitives",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_opened_directory_same_identity_aliases_positive",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_junction_reparse_and_wrong_identity_negative",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_txr_happy_path_sequence_create_open_write_commit",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_txr_failure_arms_rollback_without_fallback[unsupported]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_txr_failure_arms_rollback_without_fallback[race]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_txr_failure_arms_rollback_without_fallback[commit]",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_txr_exact_close_ownership_success_baseexc_cleanup_failure",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_install_ps1_path_mutation_is_txr_only_no_cas_fallback",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_windows_programdata_protected_root_integration",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_programdata_sddl_dacl_parser_platform_neutral_vectors",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_sddl_garbage_unknown_inherit_only_reject_contract",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_protected_root_open_close_ownership_without_acl_bool",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_discover_closes_protected_root_on_success_handoff",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_discover_closes_protected_root_on_exception",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_discover_closes_protected_root_on_base_exception",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_discover_closes_protected_root_cleanup_failure_preserves_original",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_discover_closes_protected_root_cleanup_failure_on_success",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_discover_closes_protected_root_idempotent",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_no_production_global_txr_fault_hook",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_txr_per_call_fault_isolation_event_gated",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_cng_export_positive_control_and_refuse_invalid_flag_any_error",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_windows_cng_exportable_positive_control_then_non_exportable",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_windows_cng_sign_verify_integration",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_windows_nofollow_leaf_directory_integration",
    "python::tests/unit/test_installer_shim_receipt_v1.py::test_windows_txr_registry_integration",
    "python::tests/unit/test_search_input_ledger_round60.py::test_fixture_pins_round60_caps",
    "python::tests/unit/test_search_input_ledger_round60.py::test_ledger_installed_before_bootstrap_door",
    "python::tests/unit/test_search_input_ledger_round60.py::test_ledger_installed_before_full_cli_door",
    "python::tests/unit/test_search_input_ledger_round60.py::test_per_file_bytes_inclusive_cap[per_file_cap_minus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_per_file_bytes_inclusive_cap[per_file_cap]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_per_file_bytes_inclusive_cap[per_file_cap_plus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_combined_file_count_inclusive_cap[files_cap_minus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_combined_file_count_inclusive_cap[files_cap]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_combined_file_count_inclusive_cap[files_cap_plus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_combined_decoded_bytes_inclusive_cap[agg_bytes_cap_minus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_combined_decoded_bytes_inclusive_cap[agg_bytes_cap]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_combined_decoded_bytes_inclusive_cap[agg_bytes_cap_plus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_per_rule_bytes_inclusive_cap[rule_bytes_cap_minus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_per_rule_bytes_inclusive_cap[rule_bytes_cap]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_per_rule_bytes_inclusive_cap[rule_bytes_cap_plus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_pattern_total_inclusive_cap[patterns_cap_minus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_pattern_total_inclusive_cap[patterns_cap]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_pattern_total_inclusive_cap[patterns_cap_plus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_ignore_total_inclusive_cap[ignores_cap_minus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_ignore_total_inclusive_cap[ignores_cap]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_ignore_total_inclusive_cap[ignores_cap_plus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_split_counter_patterns_and_ignores_are_independent",
    "python::tests/unit/test_search_input_ledger_round60.py::test_compiled_live_memory_inclusive_cap[compiled_mem_cap_minus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_compiled_live_memory_inclusive_cap[compiled_mem_cap]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_compiled_live_memory_inclusive_cap[compiled_mem_cap_plus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_matcher_transitions_inclusive_cap[transitions_cap_minus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_matcher_transitions_inclusive_cap[transitions_cap]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_matcher_transitions_inclusive_cap[transitions_cap_plus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_deadline_inclusive_cap[deadline_cap_minus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_deadline_inclusive_cap[deadline_cap]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_deadline_inclusive_cap[deadline_cap_plus_1]",
    "python::tests/unit/test_search_input_ledger_round60.py::test_uninstrumented_pcre2_refused_on_bootstrap",
    "python::tests/unit/test_search_input_ledger_round60.py::test_uninstrumented_pcre2_refused_on_full_cli",
    "python::tests/unit/test_search_input_ledger_round60.py::test_below_cap_non_pcre2_bootstrap_starts_producer_once",
    "python::tests/unit/test_search_input_ledger_round60.py::test_below_cap_non_pcre2_full_cli_starts_producer_once",
    "python::tests/unit/test_search_input_ledger_round60.py::test_producer_hook_does_not_self_attest_before_actual_start",
    "python::tests/unit/test_search_input_ledger_round60.py::test_pattern_file_refuses_unbounded_read_before_ledger",
    "python::tests/unit/test_win32_path_domain_round60.py::test_offline_wintrust_flags_and_microsoft_root_policy",
    "python::tests/unit/test_win32_path_domain_round60.py::test_job_is_kill_on_close_and_non_breakaway",
    "python::tests/unit/test_win32_path_domain_round60.py::test_linux_must_not_fabricate_system32_handles",
    "python::tests/unit/test_win32_path_domain_round60.py::test_system32_identity_rejects_systemroot_poison",
    "python::tests/unit/test_win32_path_domain_round60.py::test_held_file_embedded_and_catalog_controls",
    "python::tests/unit/test_win32_path_domain_round60.py::test_offline_network_canary_blocks_online_retrieval",
    "python::tests/unit/test_win32_path_domain_round60.py::test_same_organization_foreign_chain_is_not_trust",
    "python::tests/unit/test_win32_path_domain_round60.py::test_untrusted_catalog_reason_exact",
    "python::tests/unit/test_win32_path_domain_round60.py::test_catalog_member_hash_mismatch_reason_exact",
    "python::tests/unit/test_win32_path_domain_round60.py::test_parent_and_leaf_identity_swaps_fail_closed",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_descendant_breakaway_orchestration",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_descendant_breakaway_windows_integration",
    "python::tests/unit/test_win32_path_domain_round60.py::test_job_heartbeat_rejects_parent_forgeable_and_multiline_ambiguity",
    "python::tests/unit/test_win32_path_domain_round60.py::test_default_job_cleanup_independently_proven",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_fault_after_job_assignment",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_fault_after_resume",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_fault_after_image_query",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_fault_after_pipe_worker_setup",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_fault_after_default_factory[job_assignment]",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_fault_after_default_factory[resume]",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_fault_after_default_factory[image_query]",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_fault_after_default_factory[pipe_worker_setup]",
    "python::tests/unit/test_win32_path_domain_round60.py::test_resolve_system32_identity_closes_on_success",
    "python::tests/unit/test_win32_path_domain_round60.py::test_resolve_system32_identity_closes_on_partial_leaf_failure",
    "python::tests/unit/test_win32_path_domain_round60.py::test_resolve_system32_identity_closes_on_partial_identity_failure",
    "python::tests/unit/test_win32_path_domain_round60.py::test_suspended_job_fixture_close_ownership_idempotent",
    "python::tests/unit/test_win32_path_domain_round60.py::test_retained_close_state_excluded_from_equality_and_hash",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_manifest_command_digests_recompute_and_closed_world_nodes",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_manifest_is_static_without_live_run_ids",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_accepts_valid_receipt_positive_control",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[bool_version]",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[string_version]",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[arbitrary_schema]",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[digest_alphabet]",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[digest_uppercase]",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[digest_length]",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[duplicate_nodes]",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[list_field]",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[dict_field]",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_schema_type_value_length_negatives[bool_field]",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_parser_refuses_duplicate_unknown_oversized",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_derive_live_actions_tuple_from_environ",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_runner_owned_nonrecursive_leaf",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_exact_current_run_positive_receipt",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_seeded_current_run_directory_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_caller_supplied_claims_refused",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_clearance_refuses_without_live_immutable_sha_actions_run",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_cross_attempt_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_manifest_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_binary_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_source_tree_attribution_cannot_satisfy_wheel_or_installer_proof",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_wheel_attribution_without_raw_wheel_artifact_is_false",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_installer_attribution_without_raw_installer_artifact_is_false",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_wheel_attribution_with_source_created_bytes_is_not_publication",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_wheel_attribution_digest_mismatch_is_false",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_installer_attribution_with_source_created_bytes_is_not_publication",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_installer_attribution_digest_mismatch_is_false",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_source_tree_mismatch_against_wheel_expected_is_false",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_census_skipped_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_census_extra_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_census_duplicate_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_junit_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_rust_list_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_argv_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_stdout_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_stderr_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_exit_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_run_attempt_mismatch_isolated_predicate",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_artifact_namespace_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_wrong_job_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_binary_pre_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_binary_post_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_workflow_run_id_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_runner_identity_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_command_digest_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_attribution_drift_rejected",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_scripts_fail_closed_until_live_binding",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_cargo_selected_binary_exact_target_and_executable",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_cargo_selected_binary_missing_duplicate_wrong_executable",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_python_runner_refuses_unowned_node",
    "python::tests/unit/test_native_ci_receipt_v1.py::test_rust_runner_requires_manifest_ownership_before_list",
)

# Rust / integration / lib leaves that cannot carry a Python marker. Listed
# here so deleting a Rust leaf from the manifest OR this registry fails closed.
TASK2A_OWNED_RUST_NODE_IDS: tuple[str, ...] = (
    # Integration process-level direct-native doors (CARGO_BIN_EXE_tg valid).
    "rust::task2a_direct_native_round60::pattern_file_search_input_limit_direct_native",
    "rust::task2a_direct_native_round60::pattern_file_bytes_search_input_limit_direct_native",
    "rust::task2a_direct_native_round60::pattern_file_below_cap_native_json_success",
    "rust::task2a_direct_native_round60::pcre2_search_input_limit_direct_native",
    "rust::task2a_direct_native_round60::below_cap_non_pcre2_direct_native_json_success",
    # Lib doors (native→rg / native→sidecar).
    "rust::rg_passthrough::tests::execute_ripgrep_search_pcre2_search_input_limit",
    "rust::rg_passthrough::tests::execute_ripgrep_search_below_cap_non_pcre2_starts_rg_once",
    "rust::python_sidecar::tests::early_passthrough_pcre2_format_json_search_input_limit",
    "rust::python_sidecar::tests::early_passthrough_below_cap_non_pcre2_starts_sidecar_once",
    # Matcher-construction leaf (exact count after successful build).
    "rust::native_search::tests::run_native_search_leaf_matcher_construction_exactly_once",
    # PCRE2 / below-cap construction oracles (HIGH#3 — must stay inside the census).
    "rust::native_search::tests::pcre2_direct_native_route_zero_matcher_constructions_before_refusal",
    "rust::native_search::tests::below_cap_direct_native_route_one_matcher_construction",
)


def task2a_owned(fn):  # type: ignore[no-untyped-def]
    """Decorator: stamp an independent ownership marker on a contract test."""
    setattr(fn, OWNED_ATTR, True)
    return fn


def _decorator_names(node: ast.AST) -> list[str]:
    names: list[str] = []
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return names
    for deco in node.decorator_list:
        if isinstance(deco, ast.Name):
            names.append(deco.id)
        elif isinstance(deco, ast.Attribute):
            names.append(deco.attr)
        elif isinstance(deco, ast.Call):
            if isinstance(deco.func, ast.Name):
                names.append(deco.func.id)
            elif isinstance(deco.func, ast.Attribute):
                names.append(deco.func.attr)
    return names


def _param_ids_from_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str] | None:
    """Expand pytest.mark.parametrize concrete IDs when present; else None."""
    for deco in node.decorator_list:
        if not isinstance(deco, ast.Call):
            continue
        func = deco.func
        is_parametrize = (isinstance(func, ast.Attribute) and func.attr == "parametrize") or (
            isinstance(func, ast.Name) and func.id == "parametrize"
        )
        if not is_parametrize:
            continue
        # pytest.mark.parametrize("arg", [..], ids=[...]) or parametrize("arg", [("a",), ...])
        ids_kw = next((kw for kw in deco.keywords if kw.arg == "ids"), None)
        if ids_kw is not None and isinstance(ids_kw.value, (ast.List, ast.Tuple)):
            out: list[str] = []
            for elt in ids_kw.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    out.append(elt.value)
            if out:
                return out
        # Fallback: second positional arg list of constants / tuples of constants.
        if len(deco.args) >= 2 and isinstance(deco.args[1], (ast.List, ast.Tuple)):
            out = []
            for elt in deco.args[1].elts:
                if isinstance(elt, ast.Constant):
                    out.append(str(elt.value))
                elif isinstance(elt, (ast.List, ast.Tuple)) and elt.elts:
                    first = elt.elts[0]
                    if isinstance(first, ast.Constant):
                        out.append(str(first.value))
            if out:
                return out
    return None


def collect_owned_python_ids_from_ast(path: Path) -> list[str]:
    """Derive owned node IDs from markers in one family file (preserves order)."""
    rel = path.relative_to(REPO_ROOT).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    owned: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        deco_names = _decorator_names(node)
        marked = (
            OWNED_ATTR.lstrip("_") in deco_names
            or "task2a_owned" in deco_names
            or "windows_required" in deco_names
            or "_windows_required" in deco_names
        )
        if not marked:
            continue
        base = f"python::{rel}::{node.name}"
        param_ids = _param_ids_from_decorators(node)
        if param_ids:
            for pid in param_ids:
                owned.append(f"{base}[{pid}]")
        else:
            owned.append(base)
    return owned


def owned_python_ids_from_ast() -> list[str]:
    """AST-marker population across all four families (preserves duplicates)."""
    population: list[str] = []
    for rel in PYTHON_FAMILY_FILES:
        population.extend(collect_owned_python_ids_from_ast(REPO_ROOT / rel))
    return population


def independently_derived_owned_population() -> list[str]:
    """Independently derive owned population from AST markers + Rust registry.

    Python membership comes ONLY from AST markers (never from the static
    registry). Preserves duplicates so a duplicate-sensitive census can fail.
    """
    population = owned_python_ids_from_ast()
    population.extend(TASK2A_OWNED_RUST_NODE_IDS)
    return population


def registry_canonical_unique_ids() -> list[str]:
    """Canonical registry: static Python + static Rust (no AST derivation).

    Dedupes while preserving first-seen order. Callers that need deletion-
    intolerant equality must also run ``assert_closed_world_ownership``, which
    checks duplicates on the original lists before any dedupe.
    """
    combined = list(TASK2A_OWNED_PYTHON_NODE_IDS) + list(TASK2A_OWNED_RUST_NODE_IDS)
    return list(dict.fromkeys(combined))


def load_manifest_node_ids() -> list[str]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return [str(n["id"]) for n in payload["nodes"]]


def _dupes(items: list[str]) -> list[str]:
    return sorted({x for x in items if items.count(x) > 1})


def assert_closed_world_ownership() -> None:
    """Fail if static registry, AST markers, or manifest disagree.

    Duplicate checks run on the original lists before any dedupe. Populations are
    compared with ``Counter`` equality (duplicate-preserving). Static Python must
    equal the AST marker population; static Python+Rust must equal the manifest.
    Thinning any one (or any two) sources must fail against the rest.
    """
    from collections import Counter

    static_python = list(TASK2A_OWNED_PYTHON_NODE_IDS)
    static_rust = list(TASK2A_OWNED_RUST_NODE_IDS)
    static_all = static_python + static_rust
    ast_python = owned_python_ids_from_ast()
    manifest_ids = load_manifest_node_ids()

    assert len(static_python) == len(set(static_python)), (
        f"static Python ownership registry has duplicate IDs: {_dupes(static_python)}"
    )
    assert len(static_rust) == len(set(static_rust)), (
        f"static Rust ownership registry has duplicate IDs: {_dupes(static_rust)}"
    )
    assert len(static_all) == len(set(static_all)), (
        f"static ownership registry has duplicate IDs: {_dupes(static_all)}"
    )
    assert len(ast_python) == len(set(ast_python)), (
        f"AST ownership markers have duplicate IDs: {_dupes(ast_python)}"
    )
    assert len(manifest_ids) == len(set(manifest_ids)), (
        f"manifest has duplicate node IDs: {_dupes(manifest_ids)}"
    )

    assert Counter(static_python) == Counter(ast_python), (
        "static Python registry != AST marker population "
        f"(registry_only={sorted(set(static_python) - set(ast_python))} "
        f"ast_only={sorted(set(ast_python) - set(static_python))})"
    )
    assert Counter(static_all) == Counter(manifest_ids), (
        "static registry != manifest population "
        f"(registry_only={sorted(set(static_all) - set(manifest_ids))} "
        f"manifest_only={sorted(set(manifest_ids) - set(static_all))})"
    )
    derived = independently_derived_owned_population()
    assert Counter(derived) == Counter(manifest_ids), (
        "AST+Rust derived population != manifest "
        f"(derived_only={sorted(set(derived) - set(manifest_ids))} "
        f"manifest_only={sorted(set(manifest_ids) - set(derived))})"
    )
    assert Counter(derived) == Counter(static_all), (
        "AST+Rust derived population != static registry "
        f"(derived_only={sorted(set(derived) - set(static_all))} "
        f"registry_only={sorted(set(static_all) - set(derived))})"
    )
