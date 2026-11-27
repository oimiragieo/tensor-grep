# ruff: noqa: E402
from __future__ import annotations

import ast as ast
import atexit
import os as os
import re
import sys as sys
import tempfile as tempfile  # re-exported test patch seam used by the extracted helper
import threading
import time as time
from collections import OrderedDict
from pathlib import Path as Path
from typing import TYPE_CHECKING, Any

from tensor_grep.cli import focused_rendering as _focused_rendering
from tensor_grep.cli import (
    lang_c as lang_c,
)
from tensor_grep.cli import (
    lang_cpp as lang_cpp,
)
from tensor_grep.cli import (
    lang_csharp as lang_csharp,
)
from tensor_grep.cli import (
    lang_go as lang_go,
)
from tensor_grep.cli import (
    lang_java as lang_java,
)
from tensor_grep.cli import (
    lang_php as lang_php,
)
from tensor_grep.cli import (
    lang_registry as lang_registry,
)
from tensor_grep.cli import (
    lang_suffixes as lang_suffixes,
)
from tensor_grep.cli import predictive_test_selector as _predictive_test_selector
from tensor_grep.cli import repo_map_fast_cache as _fast_cache
from tensor_grep.cli import repo_map_graph as _repo_map_graph
from tensor_grep.cli import repo_map_input_provenance as _input_provenance
from tensor_grep.cli import repo_map_ranking as _repo_map_ranking
from tensor_grep.cli import repo_map_shell_inert as _inert
from tensor_grep.cli.incompleteness import budget_remediable as budget_remediable
from tensor_grep.cli.lsp_external_provider import (
    ExternalLSPProviderManager as ExternalLSPProviderManager,
)
from tensor_grep.cli.lsp_external_provider import (
    LSPTransportError as LSPTransportError,
)
from tensor_grep.cli.lsp_session import proof_request as _proof_request
from tensor_grep.cli.repo_map_cache import (
    _MTIME_CACHE_CLEAR_REGISTRY as _MTIME_CACHE_CLEAR_REGISTRY,
)
from tensor_grep.cli.repo_map_cache import (
    _SOURCE_READ_CACHE_MAXSIZE as _SOURCE_READ_CACHE_MAXSIZE,
)
from tensor_grep.cli.repo_map_cache import (
    _mtime_aware_cache as _mtime_aware_cache,
)
from tensor_grep.cli.repo_map_cache import (
    _resolved_path_str as _resolved_path_str,
)
from tensor_grep.cli.repo_map_coverage_gaps import (
    _language_coverage_gap_remediation as _language_coverage_gap_remediation,
)
from tensor_grep.cli.repo_map_coverage_gaps import (
    _language_coverage_gaps_for_universe as _language_coverage_gaps_for_universe,
)
from tensor_grep.cli.repo_map_coverage_gaps import (
    answer_empty as answer_empty,
)
from tensor_grep.cli.repo_map_coverage_gaps import (
    apply_answer_gaps as apply_answer_gaps,
)
from tensor_grep.cli.repo_map_coverage_gaps import (
    apply_coverage_gap_incompleteness as apply_coverage_gap_incompleteness,
)
from tensor_grep.cli.repo_map_coverage_gaps import (
    attach_found_answer_gaps as attach_found_answer_gaps,
)
from tensor_grep.cli.repo_map_coverage_gaps import (
    attach_importer_coverage as attach_importer_coverage,
)
from tensor_grep.cli.repo_map_coverage_gaps import (
    attach_target_gaps as attach_target_gaps,
)
from tensor_grep.cli.repo_map_coverage_gaps import (
    copy_coverage_gap_state as copy_coverage_gap_state,
)
from tensor_grep.cli.repo_map_coverage_gaps import (
    inherit_coverage_gap as inherit_coverage_gap,
)
from tensor_grep.cli.repo_map_gitignore import (
    _gitignore_pattern_to_regex as _gitignore_pattern_to_regex,
)
from tensor_grep.cli.repo_map_gitignore import (
    _GitignoreMatcher as _GitignoreMatcher,
)
from tensor_grep.cli.repo_map_gitignore import (
    _load_gitignore_matcher as _load_gitignore_matcher,
)
from tensor_grep.cli.repo_map_lang_java import (
    _java_import_declaration_text as _java_import_declaration_text,
)
from tensor_grep.cli.repo_map_lang_java import (
    _java_imports_and_symbols as _java_imports_and_symbols,
)
from tensor_grep.cli.repo_map_lang_java import (
    _java_imports_with_lines as _java_imports_with_lines,
)
from tensor_grep.cli.repo_map_lang_java import (
    _java_parser_symbol_sources as _java_parser_symbol_sources,
)
from tensor_grep.cli.repo_map_lang_java import (
    _java_references_and_calls_for_registry as _java_references_and_calls_for_registry,
)
from tensor_grep.cli.repo_map_lang_js import (
    _javascript_node_test_file_command as _javascript_node_test_file_command,
)
from tensor_grep.cli.repo_map_lang_js import (
    _javascript_repo_fallback_command as _javascript_repo_fallback_command,
)
from tensor_grep.cli.repo_map_lang_js import (
    _javascript_runner_fallback_command as _javascript_runner_fallback_command,
)
from tensor_grep.cli.repo_map_lang_js import (
    _javascript_runner_file_command as _javascript_runner_file_command,
)
from tensor_grep.cli.repo_map_lang_js import (
    _javascript_runner_specific_command as _javascript_runner_specific_command,
)
from tensor_grep.cli.repo_map_lang_js import (
    _javascript_test_script_uses_node_test as _javascript_test_script_uses_node_test,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ast_omitted_relative_lines as _js_ast_omitted_relative_lines,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_candidate_files as _js_ts_candidate_files,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_default_export_name as _js_ts_default_export_name,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_default_import_bindings as _js_ts_default_import_bindings,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_dynamic_import_hit as _js_ts_dynamic_import_hit,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_file_imports_symbol_from_definition as _js_ts_file_imports_symbol_from_definition,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_has_import_bindings as _js_ts_has_import_bindings,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_import_match_details as _js_ts_import_match_details,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_import_update_target as _js_ts_import_update_target,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_imports_with_lines as _js_ts_imports_with_lines,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_module_candidates as _js_ts_module_candidates,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_module_match_details as _js_ts_module_match_details,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_module_matches_definition as _js_ts_module_matches_definition,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_named_import_bindings as _js_ts_named_import_bindings,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_namespace_import_bindings as _js_ts_namespace_import_bindings,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_parser_symbol_sources as _js_ts_parser_symbol_sources,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_parser_symbols as _js_ts_parser_symbols,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_provider_alias_calls as _js_ts_provider_alias_calls,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_references_and_calls as _js_ts_references_and_calls,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_references_and_calls_for_registry as _js_ts_references_and_calls_for_registry,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_repo_context as _js_ts_repo_context,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_resolve_exported_symbol as _js_ts_resolve_exported_symbol,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_resolve_imported_symbol as _js_ts_resolve_imported_symbol,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_symbol_name_node as _js_ts_symbol_name_node,
)
from tensor_grep.cli.repo_map_lang_js import (
    _js_ts_symbol_names as _js_ts_symbol_names,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_ast_omitted_relative_lines as _python_ast_omitted_relative_lines,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_candidate_roots as _python_candidate_roots,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_classify_ref_kind as _python_classify_ref_kind,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_decorator_qualname as _python_decorator_qualname,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_dynamic_import_call_is_relative as _python_dynamic_import_call_is_relative,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_dynamic_import_entry_for_call as _python_dynamic_import_entry_for_call,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_file_imports_symbol_from_definition as _python_file_imports_symbol_from_definition,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_import_update_target as _python_import_update_target,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_import_update_target_for_registry as _python_import_update_target_for_registry,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_imports_and_symbols as _python_imports_and_symbols,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_imports_with_lines as _python_imports_with_lines,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_module_candidates as _python_module_candidates,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_module_match_details as _python_module_match_details,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_module_matches_definition as _python_module_matches_definition,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_module_parts as _python_module_parts,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_parametrized_test_function_candidates as _python_parametrized_test_function_candidates,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_provider_alias_calls as _python_provider_alias_calls,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_provider_alias_calls_for_registry as _python_provider_alias_calls_for_registry,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_references_and_calls_for_registry as _python_references_and_calls_for_registry,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_relative_base_dir as _python_relative_base_dir,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_symbol_sources as _python_symbol_sources,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_arg_to_dir as _python_sys_path_arg_to_dir,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_dunder_file as _python_sys_path_dunder_file,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_file_dir_expr as _python_sys_path_file_dir_expr,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_file_dirname_expr as _python_sys_path_file_dirname_expr,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_file_parent_expr as _python_sys_path_file_parent_expr,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_hack_dirs as _python_sys_path_hack_dirs,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_hack_roots as _python_sys_path_hack_roots,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_here_aliases as _python_sys_path_here_aliases,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_insert_or_append_arg as _python_sys_path_insert_or_append_arg,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_join_suffix as _python_sys_path_join_suffix,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_os_path_call_args as _python_sys_path_os_path_call_args,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_static_str as _python_sys_path_static_str,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_sys_path_truediv_suffix as _python_sys_path_truediv_suffix,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_test_function_candidates as _python_test_function_candidates,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_ast_omitted_relative_lines as _rust_ast_omitted_relative_lines,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_crate_entry_for_path as _rust_crate_entry_for_path,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_file_imports_symbol_from_definition as _rust_file_imports_symbol_from_definition,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_file_references_symbol_from_definition as _rust_file_references_symbol_from_definition,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_impl_method_candidates as _rust_impl_method_candidates,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_impl_owner_type as _rust_impl_owner_type,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_import_update_target as _rust_import_update_target,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_imports_with_lines as _rust_imports_with_lines,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_mod_declarations as _rust_mod_declarations,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_module_base_dir as _rust_module_base_dir,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_module_candidates as _rust_module_candidates,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_module_file_for_declaration as _rust_module_file_for_declaration,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_module_match_details as _rust_module_match_details,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_module_matches_definition as _rust_module_matches_definition,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_module_tree_for_entry as _rust_module_tree_for_entry,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_module_tree_lookup as _rust_module_tree_lookup,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_parser_symbol_sources as _rust_parser_symbol_sources,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_parser_symbols as _rust_parser_symbols,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_partial_candidate_paths as _rust_partial_candidate_paths,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_provider_alias_calls as _rust_provider_alias_calls,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_references_and_calls as _rust_references_and_calls,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_references_and_calls_for_registry as _rust_references_and_calls_for_registry,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_repo_context as _rust_repo_context,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_resolve_use_binding as _rust_resolve_use_binding,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_symbol_reference_candidates as _rust_symbol_reference_candidates,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_test_attribute_kind as _rust_test_attribute_kind,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_test_function_candidates as _rust_test_function_candidates,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_test_function_candidates_from_source as _rust_test_function_candidates_from_source,
)
from tensor_grep.cli.repo_map_lang_rust import _rust_test_target as _rust_test_target
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_tokio_test_function_candidates as _rust_tokio_test_function_candidates,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_use_binding_match_details as _rust_use_binding_match_details,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_use_bindings as _rust_use_bindings,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_uses_nested_test_target as _rust_uses_nested_test_target,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_workspace_entry_for_crate as _rust_workspace_entry_for_crate,
)
from tensor_grep.cli.repo_map_output_budget import (
    _apply_blast_radius_output_limits as _apply_blast_radius_output_limits,
)
from tensor_grep.cli.repo_map_output_budget import (
    _apply_context_token_budget as _apply_context_token_budget,
)
from tensor_grep.cli.repo_map_output_budget import (
    _apply_source_output_budget as _apply_source_output_budget,
)
from tensor_grep.cli.repo_map_output_budget import (
    _apply_symbol_field_output_limit as _apply_symbol_field_output_limit,
)
from tensor_grep.cli.repo_map_output_budget import (
    _apply_symbol_token_budget as _apply_symbol_token_budget,
)
from tensor_grep.cli.repo_map_output_budget import (
    _render_context_parts as _render_context_parts,
)
from tensor_grep.cli.repo_map_output_budget import (
    _render_part_path as _render_part_path,
)
from tensor_grep.cli.repo_map_output_budget import (
    _render_part_score as _render_part_score,
)
from tensor_grep.cli.repo_map_output_budget import (
    _render_part_sort_key as _render_part_sort_key,
)
from tensor_grep.cli.repo_map_output_budget import (
    _render_source_block as _render_source_block,
)
from tensor_grep.cli.repo_map_output_budget import (
    apply_repo_map_output_limits as apply_repo_map_output_limits,
)
from tensor_grep.cli.repo_map_ranking import (
    _promote_filename_phrase as _promote_filename_phrase,
)
from tensor_grep.cli.repo_map_ranking import (
    _symbol_rank_key as _symbol_rank_key,
)
from tensor_grep.cli.repo_map_ranking import (
    _symbol_span_length as _symbol_span_length,
)
from tensor_grep.cli.repo_map_regex_fallback import (
    _regex_imports_and_symbols as _regex_imports_and_symbols,
)
from tensor_grep.cli.repo_map_regex_fallback import (
    _regex_references_and_calls as _regex_references_and_calls,
)
from tensor_grep.cli.repo_map_regex_fallback import (
    _regex_symbol_sources as _regex_symbol_sources,
)
from tensor_grep.cli.repo_map_test_paths import _is_test_file as _is_test_file
from tensor_grep.cli.symbol_blast_pruner import (
    prune_symbol_unreachable_importers as prune_symbol_unreachable_importers,
)
from tensor_grep.cli.symbols_cache import (
    SymbolGeneration as SymbolGeneration,
)
from tensor_grep.cli.symbols_cache import (
    authenticated_content_root as authenticated_content_root,
)
from tensor_grep.cli.symbols_cache import (
    content_receipts_active as content_receipts_active,
)
from tensor_grep.cli.symbols_cache import (
    read_content_bytes as read_content_bytes,
)
from tensor_grep.cli.symbols_cache import (
    record_content_absence as record_content_absence,
)
from tensor_grep.cli.symbols_cache import (
    record_content_receipt_digest as record_content_receipt_digest,
)
from tensor_grep.core.config import configured_positive_int as configured_positive_int
from tensor_grep.core.python_parse import parse_python as parse_python
from tensor_grep.core.retrieval_lexical import score_term_overlap as score_term_overlap
from tensor_grep.core.retrieval_lexical import split_terms as split_terms

_authenticated_scan_paths_under = _input_provenance.authenticated_scan_paths_under
_file_size_with_receipt = _input_provenance.file_size_with_receipt
_glob_dirs_with_receipt = _input_provenance.glob_dirs_with_receipt
_is_dir_with_receipt = _input_provenance.is_dir_with_receipt
_is_file_with_receipt = _input_provenance.is_file_with_receipt
_path_exists_with_receipt = _input_provenance.path_exists_with_receipt
_read_text_with_receipt = _input_provenance.read_text_with_receipt
_resolved_input_path = _input_provenance.resolved_input_path
_validation_repo_root = _input_provenance.validation_repo_root
_focused_rendering = _focused_rendering
_predictive_test_selector = _predictive_test_selector
_fast_cache = _fast_cache
_repo_map_graph = _repo_map_graph
_input_provenance = _input_provenance
_repo_map_ranking = _repo_map_ranking
_inert = _inert
_proof_request = _proof_request

# Read this module object through late-bound
# attribute reads. A BARE call to a monkeypatched name resolves through THIS module's
# production runs the unpatched original. `_self.NAME(...)` resolves at CALL time.
#
# The two branches are load-bearing. At runtime only `sys.modules[__name__]` works (the
# module is mid-import, so importing itself by name would be circular), but that is typed
# `ModuleType`, whose `__getattr__` returns `Any` -- under a single-branch form every
# converted call returns Any and mypy raises `no-any-return` at each concrete-returning
# caller. The TYPE_CHECKING branch never executes; it exists so the checker resolves
# `_self` to this module and keeps every real signature.
#
# scripts/bare_call_ratchet.py pins the remaining bare-call count and fails if it grows.
if TYPE_CHECKING:
    from tensor_grep.cli import repo_map as _self
else:
    _self = sys.modules[__name__]

JSON_OUTPUT_VERSION = 1
ROUTING_BACKEND = "RepoMap"
ROUTING_REASON = "repo-map"
# backlog #1 : raised 512 -> 2000 so ROUTING commands
# (edit-plan/agent/context-render/defs/orient/session-open/MCP fallbacks) stop misrouting on
# repos >512 files -- a file past the old cap never entered the map at all, so the right file
# could not be found (dogfood-proven: edit-plan "API retry" -> wrong file at 512, correct at
# 2000). Measured cold repo-map-build cost: 512=1.48s, 2000=3.51s (OK), 4000=10.35s
# (superlinear -- do NOT raise past 2000 without re-measuring). This constant is ALSO the
# default for `main.py`'s shared `_DEFAULT_AGENT_REPO_SCAN_LIMIT` CLI option default (kept as a
# separate literal there deliberately -- see the comment at its definition) which feeds BOTH
# routing commands AND the caller-scan commands (callers/refs/blast-radius/impact); raising it
# is safe for caller-scan latency ONLY because CALLER_SCAN_FILE_CEILING below bounds their
# actual per-file work independently of how large the map is.
DEFAULT_AGENT_REPO_MAP_LIMIT = 2000
# backlog #1 chokepoint (the review's winning insight over "repoint every option default",
# which leaks -- e.g. session_store.py's stored-session blast-radius calls
# build_symbol_blast_radius_from_map directly on a full session repo_map with NO max_repo_files
# passthrough, so a per-command option default cannot reach it). The caller-scan functions
# (build_symbol_callers_from_map, build_symbol_blast_radius_from_map via its callers-scan,
# build_symbol_refs_from_map) do a slow per-file prefilter + re-parse whose wall-clock scales
# with the file universe size (task #52: ~100s on a 1941-file repo at the old 512 cap). This
# ceiling bounds that ONE hot loop at a single internal chokepoint, so it stays fast regardless
# of DEFAULT_AGENT_REPO_MAP_LIMIT / an explicit --max-repo-files / a stored session map's size.
# backlog #57 (2026-07-09): raised 512 -> 2000, matching DEFAULT_AGENT_REPO_MAP_LIMIT above, now
# that #478 threaded a --deadline hard-bound through every caller-scan loop and closed the #52
# unbounded-hang risk that originally kept this ceiling frozen below the map default. 2000 is a
# deliberate knee (see the map-build cost note above), not a removal of the cap: the ceiling
# stays a hard backstop for a --max-repo-files-raised mega-repo and for the flag-less
# (--deadline omitted) default path, which still has no other wall-clock bound. Raising past
# 2000 needs fresh cost data plus confirming _PARSE_PRODUCT_CACHE_MAXSIZE (below) still exceeds
# it -- otherwise the shared parse-cache guarantee silently thrashes via FIFO eviction. When the
# scan is capped below the map's true file count, the payload is marked result_incomplete (see
# _CALLER_SCAN_CEILING_REMEDIATION) so the exit-2 truncation-honesty contract still fires.
CALLER_SCAN_FILE_CEILING = 2000
# F1-review HIGH fix (task#52 shape, 2026-07-06): _order_caller_scan_candidates probes
# _file_may_contain_literal_symbol (a stat + cached read_bytes) across the caller-scan file
# UNIVERSE to decide ordering, BEFORE _cap_caller_scan_files slices to CALLER_SCAN_FILE_CEILING.
# Left unbounded, that probe pays O(map-size) I/O regardless of the 2000 ceiling or --deadline --
# exactly the pathology CALLER_SCAN_FILE_CEILING exists to prevent, just one step earlier in the
# pipeline. This ceiling bounds the PROBE itself (belt); the deadline check threaded into the
# same loop is the suspenders. 4x the scan ceiling so a normal (<=2000-file) map's ordering pass
# is never truncated in practice -- it only bites when --max-repo-files is raised well past the
# default, which is exactly the case this fix targets.
CALLER_SCAN_ORDER_PROBE_CEILING = 4 * CALLER_SCAN_FILE_CEILING
# How often (in probed files) the ordering pass re-checks the deadline. Checking every file would
# add a time.monotonic() call per file; checking too rarely risks overrunning a tight --deadline
# by a wide margin. 64 is a compromise consistent with other bounded-scan loops in this module.
_CALLER_SCAN_ORDER_PROBE_DEADLINE_STRIDE = 64
_DEFAULT_LSP_OPERATION_BUDGET_SECONDS = 10.0
_LSP_OPERATION_BUDGET_ENV_VAR = "TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS"
# Path-component names considered vendor/cache for truncation_cause classification.
# These are checked against every component of a returned file's relative path so
# that files nested inside a vendor dir that wasn't excluded at the walk level (e.g.
# because the vendor dir sits deeper than the root scan entry points) are still
# correctly identified as non-project files.
_JS_TS_SUFFIXES = set(lang_suffixes.JS_TS_SUFFIXES)
_TS_SUFFIXES = set(lang_suffixes.TS_SUFFIXES)
_RUST_SUFFIXES = {".rs"}
_JAVA_SUFFIXES = {".java"}
# Top-10 language campaign (Phase 2, C++): matches lang_cpp.py's LanguageSpec.suffixes AND
# _provider_language_for_path's pre-existing "cpp" assignment exactly -- ".h" is claimed by C++
# (not C), see lang_cpp.py's module docstring for the header-ambiguity rationale.
_CPP_SUFFIXES = {".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
_TEST_DIR_NAMES = {"__tests__", "spec", "specs", "test", "tests"}
_RENDER_PROFILES = {"full", "compact", "llm", "focused"}
_JS_RUNNER_ORDER = ("jest", "vitest", "mocha")
_DEFAULT_EDIT_PLAN_MAX_DEPTH = 3
_VALIDATION_RUNNER_SCAN_LIMIT = 512
_SOURCE_FALLBACK_SCAN_LIMIT = 8
_DIRECT_VALIDATION_SYMBOL_SCAN_LIMIT = 64
_DIRECT_VALIDATION_TEST_SCAN_LIMIT = 256
_GRAPH_PAGERANK_SEED_FILE_LIMIT = 64
_EDIT_PLAN_BLAST_RADIUS_FILE_MULTIPLIER = 4
_FRAMEWORK_TEST_PATTERN_SMALL_TEST_LIMIT = 128
_SYMBOL_LITERAL_SEED_SCAN_LIMIT = 4096
_SYMBOL_LITERAL_SEED_MAX_FILES = 16
_SYMBOL_LITERAL_SEED_MAX_BYTES = 2_000_000
_BLAST_RADIUS_LIMITED_SYMBOLS_PER_FILE = 3
_REPO_CONTEXT_CACHE_MAX_ROOTS_ENV = "TENSOR_GREP_REPO_CONTEXT_CACHE_MAX_ROOTS"
_DEFAULT_REPO_CONTEXT_CACHE_MAX_ROOTS = 32
# O2: per-file byte cap for AST parsing in build_repo_map.  Files larger than
# this are skipped (imports/symbols returned empty) to bound RSS spikes from
# multi-MB bundled/generated files.  Override with env var.
_MAX_PARSE_BYTES_ENV = "TENSOR_GREP_MAX_PARSE_BYTES"
_DEFAULT_MAX_PARSE_BYTES = 2_000_000
# Total-resident-bytes budget for the content-addressed AST parse cache (see
# _cached_ast_parse below).  Bounds DAEMON MEMORY -- independent of _MAX_PARSE_BYTES_ENV
# above, which only bounds a single file's eligibility to be parsed/cached at all.
# Override with env var.
_AST_CACHE_BYTES_ENV = "TENSOR_GREP_AST_CACHE_BYTES"
_DEFAULT_AST_CACHE_BYTES = 64 * 1024 * 1024  # 64 MiB
_JS_TS_REPO_CONTEXTS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_RUST_REPO_CONTEXTS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_EXTERNAL_LSP_PROVIDER_MANAGER = ExternalLSPProviderManager()
atexit.register(_EXTERNAL_LSP_PROVIDER_MANAGER.stop_all)


_configured_positive_int = configured_positive_int


# dogfood 1.28.3 feature #3: a machine-readable remediation carried as a top-level `scan_remediation`
# sibling of `scan_limit`, so a JSON-consuming agent gets the actionable next step without parsing the
# stderr warning (a truncated scan with a zero/small count otherwise reads as a real "not found"
# answer -- a silent-truncation trap). Kept OUT of the scan_limit dict on purpose: scan_limit is a
# stable exact-shape contract (facts), scan_remediation is the advice. Non-null only when the scan
# actually dropped project files.

# backlog #1 chokepoint: distinct from _SCAN_LIMIT_TRUNCATED_REMEDIATION above (which fires when
# the repo-MAP itself was truncated before this symbol resolved). This fires when the map was
# complete but the caller-scan's OWN internal ceiling (CALLER_SCAN_FILE_CEILING) bounded how many
# of the map's files were actually walked for callers/references -- a resolved symbol may still
# be missing callers that live past the ceiling.

# Task 332: the DEADLINE twin of _SCAN_LIMIT_TRUNCATED_REMEDIATION above. Same fact -- the scan
# stopped before it finished, so a zero or small count is not trustworthy -- reached by the other
# cause, and therefore needing a different knob in the advice (--deadline, not --max-repo-files).
# Kept as its own constant rather than reusing the count-cap string, because telling an agent to
# "raise --max-repo-files" when the file cap was never the binding constraint sends it to a knob
# that cannot fix its problem.


# Bounded sample cap for unreadable-path diagnostics -- mirrors
# `directory_scanner._MAX_UNREADABLE_PATH_SAMPLE` (task #276 slice 1): a tree with thousands of
# denied dirs must not accumulate an unbounded sample list, even though the true count is tracked
# in full.
_MAX_REPO_WALK_UNREADABLE_PATH_SAMPLE = 5


# Fix A: caller_scan (build_symbol_callers_from_map) calls _file_may_contain_literal_symbol
# and _file_may_import_symbol_definition on every candidate file, each doing its OWN
# path.read_bytes() -- a doubled full-file read per candidate, profiled at ~90% of wall time on
# a real repo (see AGENTS.md / dogfood notes). Route both through one mtime-aware cached read so
# the bytes are read once per (path, mtime, size) no matter how many callers need them.
#
# Guard 2: _file_may_import_symbol_definition itself is NOT decorated with @_mtime_aware_cache
# (its 2nd arg, definition_files: list[str], is unhashable -- decorating it directly would raise
# TypeError on every call). Only the byte-read is cached, via this dedicated helper.
#
# Guard 4: bound both the entry count (maxsize) and the per-entry size (byte cap) so this
# cache cannot be dominated by one giant generated file sitting in memory. Files above the cap
# bypass the cache entirely and are read directly, mirroring _SYMBOL_LITERAL_SEED_MAX_BYTES.


# PERF increment 1 : every JS/TS/Rust symbol/ref/caller
# extractor independently did its own `path.read_text(...)` + `parser.parse(...)` on the SAME
# file -- caller_scan and edit-plan seeding can re-parse one file up to 3x per symbol lookup
# (_js_ts_parser_symbols during repo-map build, _js_ts_references_and_calls during caller_scan,
# _js_ts_import_update_target during edit-plan seeding -- the last one alone profiled at ~26% of
# edit_plan wall time). These two functions share ONE read + ONE parse per (path, mtime, size)
# across all of them via the same @_mtime_aware_cache seam Fix A/B7 already use.
#
# CRITICAL PARITY: _read_source_text_cached uses Path.read_text (universal-newline \r\n -> \n
# translation) -- NOT a decode() of the raw-bytes cache above -- because that translated text is
# exactly what every tree-sitter consumer .encode()'s and parses today. A raw-bytes .decode()
# would leave \r\n intact and shift every node's byte offset on a CRLF file (silent wrong output
# on Windows-authored sources). Do not "simplify" this into a decode of _read_source_cached.


# backlog #57 companion fix (2026-07-09): must stay >= CALLER_SCAN_FILE_CEILING (2000). This
# cache is FIFO (insertion-order eviction, not LRU -- see _mtime_aware_cache above), so if it were
# smaller than the caller-scan ceiling, a single callers/refs/blast-radius pass over a
# >maxsize-eligible-file repo would silently thrash: files parsed early in the scan get evicted
# before later consumers (refs after callers, or a second symbol lookup) can reuse them, quietly
# defeating the "one parse per file, shared across every symbol/ref/caller extractor" guarantee
# this cache exists to provide. 2048 keeps headroom above the 2000 ceiling.
_PARSE_PRODUCT_CACHE_MAXSIZE = 2048


# ---------------------------------------------------------------------------
# Content-addressed, memory-bounded AST parse cache backing _cached_ast_parse below.
#
# Was `@lru_cache(maxsize=2048)` -- an ENTRY-COUNT bound, not a memory bound. 2048 cached ASTs
# of large files is unbounded resident-memory growth in the long-lived warm daemon (external-
# audit finding: an OOM/DoS vector). Replaced with a hand-rolled LRU bounded by TOTAL CACHED
# SOURCE BYTES (`_ast_cache_byte_budget()`, env-configurable, default 64 MiB), evicting
# least-recently-used entries on insert -- same eviction shape as
# `_remember_repo_context`/`_get_repo_context_cache_entry` above, just keyed by cumulative bytes
# instead of entry count.
# ---------------------------------------------------------------------------


_ast_cache: OrderedDict[str, tuple[ast.Module, int]] = OrderedDict()
_ast_cache_lock = threading.Lock()
_ast_cache_stats: dict[str, int] = {"hits": 0, "misses": 0, "evictions": 0, "current_bytes": 0}


_tree_sitter_node_text = lang_registry._tree_sitter_node_text


_symbol_record = lang_registry._symbol_record


_RUST_USE_SEGMENT_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*|\*)$")


# PATH A Stage 2: Java's kind map (shared between the symbol extractor below and
# `_java_parser_symbol_sources`, mirroring the two Rust functions above that each inline their
# own copy). classes/interfaces/enums/records -> "class"; methods/constructors -> "function",
# matching the Python convention (`_python_imports_and_symbols` above: ClassDef -> "class",
# every FunctionDef/AsyncFunctionDef -> "function") rather than Go's separate "method" kind.

# Verified against the real tree-sitter-java grammar (0.23.5): an `import_declaration` node has
# NO `name` field (unlike class/method/etc.) -- its children are the `import` keyword, an
# optional `static` keyword, a `scoped_identifier`/`identifier` (optionally followed by a
# trailing `.` + `asterisk` for a wildcard import), and the closing `;`. Reconstructing the
# dotted name via node-text + strip is therefore simpler and more robust than chasing child
# field names, and naturally preserves a trailing `.*` for a wildcard import.
_JAVA_IMPORT_STRIP_RE = re.compile(r"^import\s+(?:static\s+)?(.*?)\s*;?\s*$", re.DOTALL)


# Task 326: `fn NAME(` / `function NAME(` is a DECLARATION, not a call site. The tree-sitter arm
# of this same feature already refuses to count it -- `_is_definition_identifier` below excludes
# `function_item`/`struct_item`/`enum_item`/`trait_item` -- but this regex fallback did not, so a
# published wheel (which ships no tree-sitter Rust grammar, making THIS the arm real users reach)
# reported every declaration as a call at `resolution_confidence: 1.0`. Found by an external
# dogfood of v1.98.27; the guard existing on one arm and not the other is what made it survive.
#
# Deliberately a NARROW deny-list of unambiguous declaration keywords, NOT an allow-list of call
# shapes. The usual fail-closed rule (enumerate the safe cases, reject the rest) inverts here
# because the two error directions are not symmetric: over-suppressing DROPS real call sites, and
# a missing caller silently understates impact, which is worse than a rare surplus row a reader
# can see and dismiss. Anything this pattern does not recognise therefore stays a call.
#
# `\b` anchors `fn` as a whole token so an identifier merely ENDING in those letters
# (`spawn_fn collect(...)`) is not mistaken for a declaration; `\*?` admits `function* name(`.
_DEFINITION_KEYWORD_BEFORE_SYMBOL = re.compile(r"\b(?:fn|function)\s*\*?\s*$")


# F7 fix: `as_expression`/`satisfies_expression` are handled by dedicated positional logic below
# (only the TYPE-side child is "type") -- they must NOT sit in the generic ancestor-walk set above,
# which would (and used to) mislabel the runtime VALUE operand (`Widget as unknown` -> `Widget`)
# as "type" merely for having one of these as an ancestor.
_JS_TS_AS_SATISFIES_TYPES: set[str] = {"as_expression", "satisfies_expression"}

# F17 fix: construction/render call sites -- `new Widget()` and JSX (`<Widget/>`,
# `<Widget>...</Widget>`) -- are call-like uses of the symbol, not bare "value" mentions.


# ---------------------------------------------------------------------------
# PATH A Stage 0: language-extractor registry registration.
#
# Registers the CURRENT four languages (python, javascript, typescript, rust) with
# lang_registry by WRAPPING the functions defined above UNCHANGED. This is a pure-parity
# refactor: every dispatch seam that used to test `path.suffix in _JS_TS_SUFFIXES` /
# `_RUST_SUFFIXES` / `== ".py"` directly now asks `lang_registry.spec_for_path(path)` instead,
# but the underlying per-language logic those specs point to is untouched.
#
# javascript and typescript are two separate LanguageSpec entries (distinct language_id,
# distinct suffixes) but both wrap the SAME `_js_ts_*` functions -- those functions already
# branch on the .ts/.tsx suffix internally (parser/tsx selection), so registering the pair
# twice changes nothing observable; only which suffix routes to which spec entry differs,
# mirroring how `_target_language_for_path`/`_provider_language_for_path` already distinguish
# "javascript" from "typescript" today.
# ---------------------------------------------------------------------------


# Task #250 gate NIT-1: a REAL thinness gate, not just "decorated as a command + calls
# something" -- an independent review review on #693 found that shape alone is an EMERGENT ranking
# property, not a structural guarantee: `search_command` (cli/main.py, the real ~1500-line
# implementation of `tg search` itself) is ALSO a `.command`-decorated function that calls dozens
# of other names, and would be treated identically to a genuine one-line passthrough if any one of
# its many callees ever happened to surface as a top-4 alternative for some query. The swap must
# only fire on a function that is STRUCTURALLY small, not merely "decorated + calls the right
# name". Calibrated against the two known genuine dispatcher shapes in this repo -- `ledger_claim`
# (10 top-level body statements, 14 distinct callee names, docstring excluded from both counts) and
# `ledger_release` (6 / 10) -- versus the one known fat command, `search_command` (89 / 104). Both
# real dispatchers sit at or under 15 on each axis; `search_command` sits 6-10x over. 20 leaves
# roughly 2x headroom above the largest observed genuine dispatcher on each axis while staying far
# below any real implementation function measured in this repo.
_THIN_DISPATCHER_MAX_BODY_STATEMENTS = 20
_THIN_DISPATCHER_MAX_CALL_TARGETS = 20


# Task #254 . Two small,
# additive signals layered onto the flat, no-IDF `_score_symbol` scorer (the known-weak point
# named in the tensor-grep-architecture-contract skill): a soft test-file demotion (heuristic 2)
# and an exact word-boundary bonus (heuristic 3). A third candidate signal from the same research
# pass -- "weight definitions above references" -- was investigated and found to have NO live
# insertion point here: `payload["symbols"]` (this scorer's entire input population, traced
# end-to-end through `_python_imports_and_symbols`/`_js_ts_parser_symbols`/`_rust_parser_symbols`/
# `_regex_imports_and_symbols`) is ALREADY exclusively AST/regex-matched DEFINITIONS (kind in
# {class, function, method, struct, enum, trait}). References/call-sites are produced by a
# structurally separate pipeline (`_python_references_and_calls` and its JS/TS/Rust/regex
# siblings) that feeds `tg refs`/`tg callers`/blast-radius and never reaches `payload["symbols"]`
# or this scorer -- a "definitions above references" bonus here would be permanently-inert dead
# code (it could never fire on real data), not a real signal, so it was deliberately not added.
_TEST_SHADOW_PENALTY = 2


# Directory-index reverse-importers fix (tg importers correctness gap, express@4.21.1 dogfood):
# the "magic" directory-index/package-init stems `_definition_module_parts` (:3200) already
# strips for BARE/absolute specifier matching. Rust `mod.rs` is deliberately EXCLUDED for two
# reasons: SCOPE (the reported gap is JS/TS `index` + the symmetric Python `__init__`) AND
# PRECISION -- adding "mod" would extend the accepted bare-specifier false-positive surface
# documented on `_reverse_importer_extra_aliases` (below) to Rust as well.
_DIRECTORY_INDEX_STEMS = frozenset({"index", "__init__"})


# opt10 #3 (deep-dive): on the callers/impact/refs paths, _build_context_pack_from_map's
# `test_source_files` slice (below, via `_test_source_limit`) was fed by callers that never passed
# a limit, so `_context_tests` ran its O(len(tests) * len(source_files)) alias-rebuild
# (`_test_graph_score`, repo_map.py:8084) against the FULL ranked-files list even though those 3
# paths discard everything but `test_matches[:1]` (`_ranking_quality`, repo_map.py:7341/7347) or a
# rarely-hit `fallback_tests=` last resort (`_relevant_tests_for_symbol`, repo_map.py:4049-4050).
# Reuses CALLER_SCAN_FILE_CEILING (repo_map.py:176) -- the SAME ceiling refs/callers already accept
# for their own reference/caller-scan file universe (`_cap_caller_scan_files`) -- rather than a new
# magic number, so the test-scorer is no MORE unbounded than the scan it feeds already tolerates.
# Ranking-parity (test_matches[:1] / ranking_quality / ordering byte-identical) is regression-tested
# in test_context_tests_source_limit_and_deadline.py.
_CONTEXT_TESTS_SOURCE_FILE_CEILING = CALLER_SCAN_FILE_CEILING


# Default output-token budget for the `tg context` CLI (dogfood v1.19.9: an UNBOUNDED pack ballooned
# to >1MB â€” "blows any context window"). The pack is for prompt injection, so bound it by default at
# the CLI layer only; build_context_pack itself defaults to unbounded so library callers
# (session/edit-plan/mcp) are unchanged unless they opt in.
_DEFAULT_CONTEXT_MAX_TOKENS = 16000


# Secondary (supporting-context) fields trimmed BEFORE the primary answer array when a
# defs/refs/callers/impact payload exceeds --max-tokens (design #96, answer-first shrink order).
_SYMBOL_TOKEN_BUDGET_SECONDARY_FIELDS: tuple[str, ...] = ("tests", "related_paths", "imports")


_ROOT_TEST_DIR_NAMES = ("test", "tests", "__tests__")


_COMPACT_CONTEXT_RENDER_PROFILES = {"compact", "llm"}
_COMPACT_CONTEXT_RENDER_OMITTED_KEYS = ("symbols", "imports", "related_paths")


_GETATTR_ARG_PREFIX_RE = re.compile(r"getattr\s*\([^()]*,\s*$")


# #74 capability: `tg imports FILE` / `tg importers FILE [ROOT]` -- the scoped file-dependency
# primitive. The benchmark that motivated this (docs/benchmarks.md P4) showed `tg` losing to
# plain grep ~10x on file-dependency lookups because the only existing primitive was the
# whole-repo `tg map` (~53K tokens on a mid-size repo); these two commands answer "what does
# this ONE file import" (O(1) parse, no scan) and "who imports this ONE file" (bounded reverse
# lookup) directly, at ~1-2K tokens.
_PROJECT_ROOT_MARKERS = (".git", "pyproject.toml", "package.json", "Cargo.toml", "tsconfig.json")


_REVERSE_IMPORTER_TIER_SAME_ANCESTRY = 0
_REVERSE_IMPORTER_TIER_SAME_PROJECT = 1
_REVERSE_IMPORTER_TIER_SAME_LANGUAGE = 2
_REVERSE_IMPORTER_TIER_OTHER = 3

from tensor_grep.cli.navigation import constants as _navigation_constants

_SKIP_DIR_NAMES = _navigation_constants._SKIP_DIR_NAMES
_VENDOR_CACHE_DIR_COMPONENTS = _navigation_constants._VENDOR_CACHE_DIR_COMPONENTS
_SOURCE_FIRST_DIR_NAMES = _navigation_constants._SOURCE_FIRST_DIR_NAMES
_SOURCE_FIRST_SUFFIXES = _navigation_constants._SOURCE_FIRST_SUFFIXES
_QUERY_TERM_SYNONYMS = _navigation_constants._QUERY_TERM_SYNONYMS
_RUST_TEST_FN_PATTERN = _navigation_constants._RUST_TEST_FN_PATTERN
_SCAN_LIMIT_TRUNCATED_REMEDIATION = _navigation_constants._SCAN_LIMIT_TRUNCATED_REMEDIATION
_CALLER_SCAN_CEILING_REMEDIATION = _navigation_constants._CALLER_SCAN_CEILING_REMEDIATION
_DEADLINE_TRUNCATED_REMEDIATION = _navigation_constants._DEADLINE_TRUNCATED_REMEDIATION
_CLEAN_SYMBOL_NAME_RE = _navigation_constants._CLEAN_SYMBOL_NAME_RE
_FALLBACK_SOURCE_SUFFIXES = _navigation_constants._FALLBACK_SOURCE_SUFFIXES
_CODE_CONTEXT_SUFFIXES = _navigation_constants._CODE_CONTEXT_SUFFIXES
_CONTEXT_FILE_SUFFIXES = _navigation_constants._CONTEXT_FILE_SUFFIXES
_NON_CODE_HIDDEN_SUFFIXES = _navigation_constants._NON_CODE_HIDDEN_SUFFIXES
_JAVA_SYMBOL_KIND_MAP = _navigation_constants._JAVA_SYMBOL_KIND_MAP
_JS_TS_TYPE_ANCESTOR_TYPES = _navigation_constants._JS_TS_TYPE_ANCESTOR_TYPES
_JS_TS_CONSTRUCTOR_CALL_ANCESTOR_TYPES = (
    _navigation_constants._JS_TS_CONSTRUCTOR_CALL_ANCESTOR_TYPES
)
_QUERY_LANGUAGE_ALIASES = _navigation_constants._QUERY_LANGUAGE_ALIASES
_LLM_CONTEXT_RENDER_OMITTED_KEYS = _navigation_constants._LLM_CONTEXT_RENDER_OMITTED_KEYS
_SUPPORTED_FILE_DEPENDENCY_LANGUAGES = _navigation_constants._SUPPORTED_FILE_DEPENDENCY_LANGUAGES

from tensor_grep.cli.agent import context_pack as _agent_context_pack
from tensor_grep.cli.agent import context_render as _agent_context_render
from tensor_grep.cli.agent import context_render_budget as _agent_context_render_budget
from tensor_grep.cli.agent import edit_plan as _agent_edit_plan
from tensor_grep.cli.agent import edit_plan_spans as _agent_edit_plan_spans
from tensor_grep.cli.agent import ranking as _agent_ranking
from tensor_grep.cli.agent import validation_plan as _agent_validation_plan
from tensor_grep.cli.agent.context_pack import _module_aliases_for_path as _module_aliases_for_path
from tensor_grep.cli.agent.context_pack import (
    _reverse_importer_extra_aliases as _reverse_importer_extra_aliases,
)
from tensor_grep.cli.navigation import blast_radius as _navigation_blast_radius
from tensor_grep.cli.navigation import imports as _navigation_imports
from tensor_grep.cli.navigation import lsp_provider as _navigation_lsp_provider
from tensor_grep.cli.navigation import parsers_lang as _navigation_parsers_lang
from tensor_grep.cli.navigation import parsers_registry as _navigation_parsers_registry
from tensor_grep.cli.navigation import references_and_callers as _navigation_references_and_callers
from tensor_grep.cli.navigation import source_cache as _navigation_source_cache
from tensor_grep.cli.navigation import symbol_graph as _navigation_symbol_graph
from tensor_grep.cli.navigation import walker as _navigation_walker

_import_alias_candidates = _agent_context_pack._import_alias_candidates
_import_graph_bonus = _agent_context_pack._import_graph_bonus
_reverse_import_distances = _agent_context_pack._reverse_import_distances
_reverse_importers = _agent_context_pack._reverse_importers
_personalized_reverse_import_pagerank = _agent_context_pack._personalized_reverse_import_pagerank
_test_import_bonus = _agent_context_pack._test_import_bonus
_test_graph_score = _agent_context_pack._test_graph_score
_context_tests = _agent_context_pack._context_tests
_build_context_pack_from_map = _agent_context_pack._build_context_pack_from_map
_estimate_payload_tokens = _agent_context_pack._estimate_payload_tokens
build_context_pack = _agent_context_pack.build_context_pack
build_context_pack_json = _agent_context_pack.build_context_pack_json
build_context_pack_from_map = _agent_context_pack.build_context_pack_from_map
_list_of_dicts = _agent_context_render._list_of_dicts
_list_of_strings = _agent_context_render._list_of_strings
_compact_symbol_record = _agent_context_render._compact_symbol_record
_compact_span_record = _agent_context_render._compact_span_record
_compact_suggested_edit = _agent_context_render._compact_suggested_edit
_compact_edit_plan_seed = _agent_context_render._compact_edit_plan_seed
_compact_navigation_group = _agent_context_render._compact_navigation_group
_compact_navigation_pack = _agent_context_render._compact_navigation_pack
_compact_candidate_edit_targets = _agent_context_render._compact_candidate_edit_targets
_compact_context_sources = _agent_context_render._compact_context_sources
_compact_context_sections = _agent_context_render._compact_context_sections
_top_level_validation_commands = _agent_context_render._top_level_validation_commands
_top_level_suggested_validation_commands = (
    _agent_context_render._top_level_suggested_validation_commands
)
_top_level_validation_plan = _agent_context_render._top_level_validation_plan
_edit_plan_confidence_and_ask = _agent_context_render._edit_plan_confidence_and_ask
_compact_context_render_payload = _agent_context_render._compact_context_render_payload
_downgrade_ranking_quality = _agent_context_render._downgrade_ranking_quality
_int_or_none = _agent_context_render._int_or_none
_span_bounds = _agent_context_render._span_bounds
_line_map_overlaps_span = _agent_context_render._line_map_overlaps_span
_line_map_covers_span = _agent_context_render._line_map_covers_span
_source_includes_primary_symbol = _agent_context_render._source_includes_primary_symbol
_source_truncates_primary_symbol = _agent_context_render._source_truncates_primary_symbol
_section_includes_primary_symbol = _agent_context_render._section_includes_primary_symbol
_ensure_primary_source_in_sources = _agent_context_render._ensure_primary_source_in_sources
_apply_context_consistency_invariants = _agent_context_render._apply_context_consistency_invariants
build_context_render_from_map = _agent_context_render.build_context_render_from_map
build_context_render_json = _agent_context_render.build_context_render_json
_estimate_tokens_for_len = _agent_context_render_budget._estimate_tokens_for_len
_estimate_tokens = _agent_context_render_budget._estimate_tokens
_line_map_for_budgeted_lines = _agent_context_render_budget._line_map_for_budgeted_lines
_source_text_within_budget = _agent_context_render_budget._source_text_within_budget
_truncate_source_text_to_budget = _agent_context_render_budget._truncate_source_text_to_budget
_prepare_render_part = _agent_context_render_budget._prepare_render_part
_omitted_section_record = _agent_context_render_budget._omitted_section_record
_render_context_string_and_sections = (
    _agent_context_render_budget._render_context_string_and_sections
)
_normalize_render_profile = _agent_context_render_budget._normalize_render_profile
_is_comment_line = _agent_context_render_budget._is_comment_line
_ts_ast_omitted_relative_lines = _agent_context_render_budget._ts_ast_omitted_relative_lines
_confidence_from_score = _agent_context_render_budget._confidence_from_score
_derive_seed_overall = _agent_context_render_budget._derive_seed_overall
_primary_span_for_symbol = _agent_context_render_budget._primary_span_for_symbol
build_context_render = _agent_context_render_budget.build_context_render
_fallback_file_source = _agent_context_render_budget._fallback_file_source
_preferred_edit_anchor_symbol = _agent_edit_plan._preferred_edit_anchor_symbol
_symbol_is_unrequested_marker_helper = _agent_edit_plan._symbol_is_unrequested_marker_helper
_symbol_name_mentions_marker = _agent_edit_plan._symbol_name_mentions_marker
_non_marker_implementation_candidate = _agent_edit_plan._non_marker_implementation_candidate
_promote_substantive_symbol_for_edit_seed = (
    _agent_edit_plan._promote_substantive_symbol_for_edit_seed
)
_should_build_edit_plan_blast_radius = _agent_edit_plan._should_build_edit_plan_blast_radius
_with_lsp_primary_symbol_evidence = _agent_edit_plan._with_lsp_primary_symbol_evidence
_scoped_repo_map_for_edit_plan_blast_radius = (
    _agent_edit_plan._scoped_repo_map_for_edit_plan_blast_radius
)
_rollback_risk_from_blast_radius = _agent_edit_plan._rollback_risk_from_blast_radius
_is_distinctive_identifier = _agent_edit_plan._is_distinctive_identifier
_exact_query_match_primary_symbol = _agent_edit_plan._exact_query_match_primary_symbol
_build_edit_plan_seed = _agent_edit_plan._build_edit_plan_seed
_sorted_ranked_symbols = _agent_edit_plan._sorted_ranked_symbols
_attach_edit_plan_headline_aliases = _agent_edit_plan._attach_edit_plan_headline_aliases
_attach_edit_plan_metadata = _agent_edit_plan._attach_edit_plan_metadata
_attach_lightweight_navigation_metadata = _agent_edit_plan._attach_lightweight_navigation_metadata
build_context_edit_plan = _agent_edit_plan.build_context_edit_plan
build_context_edit_plan_from_map = _agent_edit_plan.build_context_edit_plan_from_map
build_context_edit_plan_json = _agent_edit_plan.build_context_edit_plan_json
_symbol_sort_key = _agent_edit_plan_spans._symbol_sort_key
_symbols_for_file = _agent_edit_plan_spans._symbols_for_file
_enclosing_symbol_for_line = _agent_edit_plan_spans._enclosing_symbol_for_line
_related_span_record = _agent_edit_plan_spans._related_span_record
_ordered_dependent_file_matches = _agent_edit_plan_spans._ordered_dependent_file_matches
_narrow_python_depth_two_dependency_matches = (
    _agent_edit_plan_spans._narrow_python_depth_two_dependency_matches
)
_related_spans_from_blast_radius = _agent_edit_plan_spans._related_spans_from_blast_radius
_candidate_edit_spans = _agent_edit_plan_spans._candidate_edit_spans
_mention_ref = _agent_edit_plan_spans._mention_ref
_navigation_pack = _agent_edit_plan_spans._navigation_pack
_deterministic_edit_ordering = _agent_edit_plan_spans._deterministic_edit_ordering
_suggested_edit_confidence = _agent_edit_plan_spans._suggested_edit_confidence
_import_update_rationale = _agent_edit_plan_spans._import_update_rationale
_caller_update_rationale = _agent_edit_plan_spans._caller_update_rationale
_suggested_edit_base_entry = _agent_edit_plan_spans._suggested_edit_base_entry
_caller_update_targets_for_span = _agent_edit_plan_spans._caller_update_targets_for_span
_ambiguous_suggested_edit_alternatives = (
    _agent_edit_plan_spans._ambiguous_suggested_edit_alternatives
)
_suggested_edit_priority = _agent_edit_plan_spans._suggested_edit_priority
_deduplicate_suggested_edits = _agent_edit_plan_spans._deduplicate_suggested_edits
_capped_suggested_edits = _agent_edit_plan_spans._capped_suggested_edits
_suggested_edits_from_related_spans = _agent_edit_plan_spans._suggested_edits_from_related_spans
_query_terms = _agent_ranking._query_terms
_symbol_query_terms = _agent_ranking._symbol_query_terms
_query_language_hints = _agent_ranking._query_language_hints
_target_language_for_path = _agent_ranking._target_language_for_path
_path_matches_query_language_hints = _agent_ranking._path_matches_query_language_hints
_query_file_name_hint_score = _agent_ranking._query_file_name_hint_score
_score_text_terms = _agent_ranking._score_text_terms
_is_cli_command_module_path = _agent_ranking._is_cli_command_module_path
_thin_cli_dispatcher_call_targets = _agent_ranking._thin_cli_dispatcher_call_targets
_symbol_lookup_key = _agent_ranking._symbol_lookup_key
_symbol_name_matches_query_exactly = _agent_ranking._symbol_name_matches_query_exactly
_symbol_name_matches_query_bridge = _agent_ranking._symbol_name_matches_query_bridge
_symbol_name_terms_cover_query = _agent_ranking._symbol_name_terms_cover_query
_score_file_path = _agent_ranking._score_file_path
_non_test_definition_names = _agent_ranking._non_test_definition_names
_symbol_name_exact_boundary_bonus = _agent_ranking._symbol_name_exact_boundary_bonus
_score_symbol = _agent_ranking._score_symbol
_score_import_entry = _agent_ranking._score_import_entry
_score_file_source_terms = _agent_ranking._score_file_source_terms
_append_reason = _agent_ranking._append_reason
_provenance_from_reasons = _agent_ranking._provenance_from_reasons
_span_rationale = _agent_ranking._span_rationale
_ranking_quality = _agent_ranking._ranking_quality
_reference_kind_counts = _agent_ranking._reference_kind_counts
_coverage_summary = _agent_ranking._coverage_summary
_graph_trust_summary = _agent_ranking._graph_trust_summary
_downgrade_graph_trust_summary_for_coverage_gaps = (
    _agent_ranking._downgrade_graph_trust_summary_for_coverage_gaps
)
_is_parser_backed_provenance = _agent_ranking._is_parser_backed_provenance
_is_heuristic_provenance = _agent_ranking._is_heuristic_provenance
_dependency_trust = _agent_ranking._dependency_trust
_plan_trust_summary = _agent_ranking._plan_trust_summary
_match_record = _agent_ranking._match_record
_file_summaries = _agent_ranking._file_summaries
_source_tokens = _agent_ranking._source_tokens
_read_package_json = _agent_validation_plan._read_package_json
_infer_js_package_manager = _agent_validation_plan._infer_js_package_manager
_package_test_script_command = _agent_validation_plan._package_test_script_command
_package_test_script = _agent_validation_plan._package_test_script
_package_json_dependency_names = _agent_validation_plan._package_json_dependency_names
_ts_jest_configured = _agent_validation_plan._ts_jest_configured
_detect_validation_runners_from_root = _agent_validation_plan._detect_validation_runners_from_root
from tensor_grep.cli.agent.validation_plan import (
    _cached_detect_validation_runners as _cached_detect_validation_runners,
)

_detect_validation_runners = _agent_validation_plan._detect_validation_runners
_relative_validation_path = _agent_validation_plan._relative_validation_path
_shell_safe_arg = _agent_validation_plan._shell_safe_arg
_candidate_terms = _agent_validation_plan._candidate_terms
_best_test_function_candidate = _agent_validation_plan._best_test_function_candidate
from tensor_grep.cli.agent.validation_plan import (
    _javascript_test_function_candidates as _javascript_test_function_candidates,
)

_framework_test_function_candidates = _agent_validation_plan._framework_test_function_candidates
_framework_test_pattern_bonus = _agent_validation_plan._framework_test_pattern_bonus
from tensor_grep.cli.agent.validation_plan import (
    _javascript_test_file_uses_node_test as _javascript_test_file_uses_node_test,
)

_nearest_cargo_manifest_for_path = _agent_validation_plan._nearest_cargo_manifest_for_path
_cargo_test_command_for_primary_file = _agent_validation_plan._cargo_test_command_for_primary_file
_primary_language_fallback_validation_steps = (
    _agent_validation_plan._primary_language_fallback_validation_steps
)
_has_python_validation_fallback_evidence = (
    _agent_validation_plan._has_python_validation_fallback_evidence
)
_suggested_validation_command_candidates = (
    _agent_validation_plan._suggested_validation_command_candidates
)
_suggested_validation_command_for_primary_file = (
    _agent_validation_plan._suggested_validation_command_for_primary_file
)
_without_heuristic_repo_cargo_fallback = (
    _agent_validation_plan._without_heuristic_repo_cargo_fallback
)
_ensure_primary_language_validation_fallback = (
    _agent_validation_plan._ensure_primary_language_validation_fallback
)
_validation_commands_for_tests = _agent_validation_plan._validation_commands_for_tests
from tensor_grep.cli.agent.validation_plan import (
    _raw_validation_plan_for_tests as _raw_validation_plan_for_tests,
)

_validation_runner_languages = _agent_validation_plan._validation_runner_languages
_validation_step_matches_primary_language = (
    _agent_validation_plan._validation_step_matches_primary_language
)
_align_validation_plan_for_primary_language = (
    _agent_validation_plan._align_validation_plan_for_primary_language
)
from tensor_grep.cli.agent.validation_plan import (
    _validation_plan_and_alignment_for_tests as _validation_plan_and_alignment_for_tests,
)

_validation_plan_for_tests = _agent_validation_plan._validation_plan_for_tests
_blast_radius_no_match_is_possibly_truncated = (
    _navigation_blast_radius._blast_radius_no_match_is_possibly_truncated
)
build_symbol_blast_radius = _navigation_blast_radius.build_symbol_blast_radius
build_symbol_blast_radius_from_map = _navigation_blast_radius.build_symbol_blast_radius_from_map
build_symbol_blast_radius_json = _navigation_blast_radius.build_symbol_blast_radius_json
build_symbol_blast_radius_plan = _navigation_blast_radius.build_symbol_blast_radius_plan
build_symbol_blast_radius_plan_from_map = (
    _navigation_blast_radius.build_symbol_blast_radius_plan_from_map
)
build_symbol_blast_radius_plan_json = _navigation_blast_radius.build_symbol_blast_radius_plan_json
build_symbol_blast_radius_render = _navigation_blast_radius.build_symbol_blast_radius_render
build_symbol_blast_radius_render_from_map = (
    _navigation_blast_radius.build_symbol_blast_radius_render_from_map
)
_infer_project_root = _navigation_imports._infer_project_root
_resolve_raw_import_entry = _navigation_imports._resolve_raw_import_entry
build_file_imports = _navigation_imports.build_file_imports
_confirm_import_edges = _navigation_imports._confirm_import_edges
_tier_reverse_importer_candidates = _navigation_imports._tier_reverse_importer_candidates
build_file_importers_from_map = _navigation_imports.build_file_importers_from_map
build_file_importers = _navigation_imports.build_file_importers
_normalize_semantic_provider = _navigation_lsp_provider._normalize_semantic_provider
_language_for_path = _navigation_lsp_provider._language_for_path
_provider_language_for_path = _navigation_lsp_provider._provider_language_for_path
_path_from_lsp_file_uri = _navigation_lsp_provider._path_from_lsp_file_uri
_lsp_symbol_kind_name = _navigation_lsp_provider._lsp_symbol_kind_name
_symbol_character_in_file = _navigation_lsp_provider._symbol_character_in_file
_provider_languages_for_symbol = _navigation_lsp_provider._provider_languages_for_symbol
_provider_status_snapshot = _navigation_lsp_provider._provider_status_snapshot
_configured_lsp_operation_budget_seconds = (
    _navigation_lsp_provider._configured_lsp_operation_budget_seconds
)
_wait_for_lsp_readiness = _navigation_lsp_provider._wait_for_lsp_readiness
_lsp_operation_deadline = _navigation_lsp_provider._lsp_operation_deadline
_remaining_lsp_budget_seconds = _navigation_lsp_provider._remaining_lsp_budget_seconds
_run_lsp_with_operation_budget = _navigation_lsp_provider._run_lsp_with_operation_budget
_attach_lsp_evidence_status = _navigation_lsp_provider._attach_lsp_evidence_status
_is_lsp_proof_row = _navigation_lsp_provider._is_lsp_proof_row
_merge_navigation_duplicate = _navigation_lsp_provider._merge_navigation_duplicate
_lsp_proof_row_count = _navigation_lsp_provider._lsp_proof_row_count
_copy_lsp_evidence_status = _navigation_lsp_provider._copy_lsp_evidence_status
_merge_agreement_status = _navigation_lsp_provider._merge_agreement_status
_default_provider_metadata = _navigation_lsp_provider._default_provider_metadata
_external_workspace_symbols = _navigation_lsp_provider._external_workspace_symbols
_iter_lsp_definition_locations = _navigation_lsp_provider._iter_lsp_definition_locations
_dedupe_lsp_definition_rows = _navigation_lsp_provider._dedupe_lsp_definition_rows
_definition_file_display_path = _navigation_lsp_provider._definition_file_display_path
_definition_file_dedupe_key = _navigation_lsp_provider._definition_file_dedupe_key
_definition_dedupe_key = _navigation_lsp_provider._definition_dedupe_key
_merge_definition_duplicate = _navigation_lsp_provider._merge_definition_duplicate
_dedupe_definition_rows = _navigation_lsp_provider._dedupe_definition_rows
_external_definitions = _navigation_lsp_provider._external_definitions
_external_references = _navigation_lsp_provider._external_references
_is_clean_symbol_name = _navigation_parsers_lang._is_clean_symbol_name
_node_has_ancestor_type = _navigation_parsers_lang._node_has_ancestor_type
_line_span_from_offsets = _navigation_parsers_lang._line_span_from_offsets
_normalized_repo_root = _navigation_parsers_lang._normalized_repo_root
_dedupe_labels = _navigation_parsers_lang._dedupe_labels
_parse_js_ts_tsconfig = _navigation_parsers_lang._parse_js_ts_tsconfig
_prime_js_ts_repo_context = _navigation_parsers_lang._prime_js_ts_repo_context
_expand_js_ts_tsconfig_target = _navigation_parsers_lang._expand_js_ts_tsconfig_target
_split_top_level_list = _navigation_parsers_lang._split_top_level_list
_is_valid_rust_use_path = _navigation_parsers_lang._is_valid_rust_use_path
_flatten_rust_use_items = _navigation_parsers_lang._flatten_rust_use_items
_build_rust_module_tree = _navigation_parsers_lang._build_rust_module_tree
_normalize_rust_crate_name = _navigation_parsers_lang._normalize_rust_crate_name
_parse_rust_workspace_members = _navigation_parsers_lang._parse_rust_workspace_members
_prime_rust_repo_context = _navigation_parsers_lang._prime_rust_repo_context
_extract_rust_impl_block = _navigation_parsers_lang._extract_rust_impl_block
_definition_module_parts = _navigation_parsers_lang._definition_module_parts
_normalized_module_parts = _navigation_parsers_lang._normalized_module_parts
_module_path_matches_definition = _navigation_parsers_lang._module_path_matches_definition
from tensor_grep.cli.navigation.parsers_lang import (
    _file_imports_symbol_from_definition as _file_imports_symbol_from_definition,
)

_import_names_reference_symbol_definition = (
    _navigation_parsers_lang._import_names_reference_symbol_definition
)
_direct_validation_import_count_from_repo_map = (
    _navigation_parsers_lang._direct_validation_import_count_from_repo_map
)
_import_update_target = _navigation_parsers_lang._import_update_target
_source_line_text = _navigation_parsers_lang._source_line_text
_import_graph_resolution_confidence = _navigation_parsers_lang._import_graph_resolution_confidence
_build_import_graph_consumers_from_map = (
    _navigation_parsers_lang._build_import_graph_consumers_from_map
)
_preferred_definition_files = _navigation_parsers_lang._preferred_definition_files
_relevant_tests_for_symbol = _navigation_parsers_lang._relevant_tests_for_symbol
_related_test_stem = _navigation_parsers_lang._related_test_stem
_discover_validation_tests_for_primary_file = (
    _navigation_parsers_lang._discover_validation_tests_for_primary_file
)
_dedupe_symbol_records = _navigation_parsers_lang._dedupe_symbol_records
_python_references_and_calls = _navigation_parsers_lang._python_references_and_calls
_js_ts_classify_ref_kind = _navigation_parsers_registry._js_ts_classify_ref_kind
_rust_classify_ref_kind = _navigation_parsers_registry._rust_classify_ref_kind
_extract_braced_block = _navigation_parsers_registry._extract_braced_block
_javascript_imports_and_symbols = _navigation_parsers_registry._javascript_imports_and_symbols
_typescript_imports_and_symbols = _navigation_parsers_registry._typescript_imports_and_symbols
_rust_imports_and_symbols = _navigation_parsers_registry._rust_imports_and_symbols
_go_references_and_calls_for_registry = (
    _navigation_parsers_registry._go_references_and_calls_for_registry
)
_csharp_references_and_calls_for_registry = (
    _navigation_parsers_registry._csharp_references_and_calls_for_registry
)
_php_references_and_calls_for_registry = (
    _navigation_parsers_registry._php_references_and_calls_for_registry
)
_c_references_and_calls_for_registry = (
    _navigation_parsers_registry._c_references_and_calls_for_registry
)
_cpp_references_and_calls_for_registry = (
    _navigation_parsers_registry._cpp_references_and_calls_for_registry
)
_references_and_calls_for_path = _navigation_parsers_registry._references_and_calls_for_path
_prime_all_language_repo_contexts = _navigation_parsers_registry._prime_all_language_repo_contexts
_imports_and_symbols_for_path = _navigation_parsers_registry._imports_and_symbols_for_path
_imports_with_lines_for_path = _navigation_parsers_registry._imports_with_lines_for_path
_group_symbols_by_file = _navigation_parsers_registry._group_symbols_by_file
_normalized_changeset_paths = _navigation_parsers_registry._normalized_changeset_paths
_dedupe_symbol_references = _navigation_references_and_callers._dedupe_symbol_references
_classify_string_reference = _navigation_references_and_callers._classify_string_reference
_string_literal_references = _navigation_references_and_callers._string_literal_references
build_symbol_refs = _navigation_references_and_callers.build_symbol_refs
build_symbol_refs_from_map = _navigation_references_and_callers.build_symbol_refs_from_map
build_symbol_refs_json = _navigation_references_and_callers.build_symbol_refs_json
build_symbol_callers = _navigation_references_and_callers.build_symbol_callers
build_symbol_callers_from_map = _navigation_references_and_callers.build_symbol_callers_from_map
build_symbol_callers_json = _navigation_references_and_callers.build_symbol_callers_json
_mtime_key = _navigation_source_cache._mtime_key
_clear_all_source_caches = _navigation_source_cache._clear_all_source_caches
_read_source_cached = _navigation_source_cache._read_source_cached
from tensor_grep.cli.navigation.source_cache import (
    _read_source_cached_bounded as _read_source_cached_bounded,
)

_read_source_text_cached = _navigation_source_cache._read_source_text_cached
from tensor_grep.cli.navigation.source_cache import (
    _read_source_text_cached_bounded as _read_source_text_cached_bounded,
)

_parser_for_source_suffix = _navigation_source_cache._parser_for_source_suffix
_parse_source_uncached = _navigation_source_cache._parse_source_uncached
_parsed_source_and_tree = _navigation_source_cache._parsed_source_and_tree
from tensor_grep.cli.navigation.source_cache import (
    _parsed_source_and_tree_bounded as _parsed_source_and_tree_bounded,
)

_file_may_contain_literal_symbol = _navigation_source_cache._file_may_contain_literal_symbol
_file_may_import_symbol_definition = _navigation_source_cache._file_may_import_symbol_definition
_literal_symbol_seed_files = _navigation_source_cache._literal_symbol_seed_files
_repo_map_root_dir = _navigation_source_cache._repo_map_root_dir
_repo_map_file_and_test_universe = _navigation_source_cache._repo_map_file_and_test_universe
_repo_map_file_universe = _navigation_source_cache._repo_map_file_universe
_interleave_proportionally = _navigation_source_cache._interleave_proportionally
_order_caller_scan_candidates = _navigation_source_cache._order_caller_scan_candidates
_cap_caller_scan_files = _navigation_source_cache._cap_caller_scan_files
_is_python_dynamic_import_call = _navigation_source_cache._is_python_dynamic_import_call
_AstCacheInfo = _navigation_source_cache._AstCacheInfo
_cached_ast_parse = _navigation_source_cache._cached_ast_parse
_ast_cache_info = _navigation_source_cache._ast_cache_info
_ast_cache_clear = _navigation_source_cache._ast_cache_clear
from tensor_grep.cli.navigation.source_cache import _java_parser as _java_parser
from tensor_grep.cli.navigation.source_cache import _javascript_parser as _javascript_parser
from tensor_grep.cli.navigation.source_cache import _rust_parser as _rust_parser
from tensor_grep.cli.navigation.source_cache import _typescript_parser as _typescript_parser

_symbol_navigation_provenance_for_path = (
    _navigation_source_cache._symbol_navigation_provenance_for_path
)
build_repo_map = _navigation_symbol_graph.build_repo_map
build_repo_map_incremental = _navigation_symbol_graph.build_repo_map_incremental
build_repo_map_json = _navigation_symbol_graph.build_repo_map_json
_enclosing_class_for_definition = _navigation_symbol_graph._enclosing_class_for_definition
_definition_confidence_score = _navigation_symbol_graph._definition_confidence_score
build_symbol_defs = _navigation_symbol_graph.build_symbol_defs
build_symbol_defs_from_map = _navigation_symbol_graph.build_symbol_defs_from_map
build_symbol_defs_json = _navigation_symbol_graph.build_symbol_defs_json
build_symbol_source = _navigation_symbol_graph.build_symbol_source
build_symbol_source_from_map = _navigation_symbol_graph.build_symbol_source_from_map
build_symbol_source_json = _navigation_symbol_graph.build_symbol_source_json
build_symbol_impact = _navigation_symbol_graph.build_symbol_impact
build_symbol_impact_from_map = _navigation_symbol_graph.build_symbol_impact_from_map
build_symbol_impact_json = _navigation_symbol_graph.build_symbol_impact_json
_repo_context_cache_max_roots = _navigation_walker._repo_context_cache_max_roots
_max_parse_bytes = _navigation_walker._max_parse_bytes
_ast_cache_byte_budget = _navigation_walker._ast_cache_byte_budget
_remember_repo_context = _navigation_walker._remember_repo_context
_get_repo_context_cache_entry = _navigation_walker._get_repo_context_cache_entry
_ValidationRunnerInfo = _navigation_walker._ValidationRunnerInfo
_ProfilePhase = _navigation_walker._ProfilePhase
_ProfileCollector = _navigation_walker._ProfileCollector
_profiling_phase = _navigation_walker._profiling_phase
_resolve_profiling_collector = _navigation_walker._resolve_profiling_collector
_attach_profiling = _navigation_walker._attach_profiling
_language_scope_descriptor = _navigation_walker._language_scope_descriptor
_symbol_navigation_descriptor = _navigation_walker._symbol_navigation_descriptor
_envelope = _navigation_walker._envelope
_mark_result_incomplete = _navigation_walker._mark_result_incomplete
_copy_scan_limit = _navigation_walker._copy_scan_limit
_stamp_partial_status = _navigation_walker._stamp_partial_status
_copy_partial_signal = _navigation_walker._copy_partial_signal
_scan_did_not_finish = _navigation_walker._scan_did_not_finish
_deadline_monotonic_from_seconds = _navigation_walker._deadline_monotonic_from_seconds
_TestScanCounts = _navigation_walker._TestScanCounts
_DeadlineBreakFlag = _navigation_walker._DeadlineBreakFlag
_UnreadablePathFlag = _navigation_walker._UnreadablePathFlag
_repo_walk_dir_sort_key = _navigation_walker._repo_walk_dir_sort_key
_repo_walk_file_sort_key = _navigation_walker._repo_walk_file_sort_key
_repo_walk_path_sort_key = _navigation_walker._repo_walk_path_sort_key
_repo_walk_bucket_sort_key = _navigation_walker._repo_walk_bucket_sort_key
_should_skip_repo_dir = _navigation_walker._should_skip_repo_dir
_path_has_vendor_component = _navigation_walker._path_has_vendor_component
_scan_limit_cause = _navigation_walker._scan_limit_cause
_stack_ignored = _navigation_walker._stack_ignored
_iter_repo_bucket_files = _navigation_walker._iter_repo_bucket_files
_iter_repo_files = _navigation_walker._iter_repo_files
_safe_resolve = _navigation_walker._safe_resolve
_looks_like_binary_file = _navigation_walker._looks_like_binary_file
_is_hidden_non_code_file = _navigation_walker._is_hidden_non_code_file
_is_repo_context_file = _navigation_walker._is_repo_context_file
_path_is_relative_to = _navigation_walker._path_is_relative_to
_precomputed_validation_files_for_root = _navigation_walker._precomputed_validation_files_for_root
_repo_map_validation_file_paths = _navigation_walker._repo_map_validation_file_paths
_literal_symbol_candidate = _navigation_walker._literal_symbol_candidate
_file_contains_literal_symbol = _navigation_walker._file_contains_literal_symbol

_navigation_parsers_registry._register_repo_map_languages()
