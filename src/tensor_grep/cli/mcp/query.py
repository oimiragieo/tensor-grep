"""MCP text search, find, and unified tg_query meta-tool."""

from __future__ import annotations

import json
import sys
import time
from typing import TYPE_CHECKING, Any, Literal

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cpu_backend import (
    compute_native_walk_deadline,
)
from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.cli import mcp_arg_validation as _av
from tensor_grep.cli import mcp_search_bounds as _bounds
from tensor_grep.cli import mcp_search_scope as _search_scope
from tensor_grep.cli import mcp_tool_contract as _tool_contract
from tensor_grep.cli.citations import (
    result_citations_json,
    serialize_mcp_find_result,
    serialize_workspace_citations,
)
from tensor_grep.cli.incompleteness import (
    incomplete_class_fragment as _incomplete_class_fragment,
)
from tensor_grep.cli.mcp.security import (
    _broad_root_scan_refusal_result,
    _confine_mcp_path,
    _envelope_base,
    _log_tool_exception,
    _mcp_broad_root_scan_refusal,
    _mcp_root,
    _meta_confinement_error,
    _meta_envelope,
    _meta_invalid_input_error,
    _meta_missing_param_error,
    _meta_unknown_action_error,
    _meta_workspace_roots_cap_error,
    _safe_exception_class_name,
    _sanitized_tool_error,
    _sanitized_tool_error_text,
)
from tensor_grep.cli.mcp.server import (
    _DEFAULT_MCP_FIND_MAX_TOKENS,
    _DEFAULT_MCP_REPO_SCAN_LIMIT,
    _register_legacy_tool,
    _register_tool,
)
from tensor_grep.cli.mcp_path_errors import PathConfinementError
from tensor_grep.cli.repo_map import (
    _deadline_monotonic_from_seconds,
)
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.core.result import SearchResult, merge_runtime_routing

AuditAction = _tool_contract.AuditAction
CheckpointAction = _tool_contract.CheckpointAction
ContextAction = _tool_contract.ContextAction
ExploreAction = _tool_contract.ExploreAction
ImpactAction = _tool_contract.ImpactAction
NavigateAction = _tool_contract.NavigateAction
QueryAction = _tool_contract.QueryAction
RewriteAction = _tool_contract.RewriteAction
ScanAction = _tool_contract.ScanAction
SessionAction = _tool_contract.SessionAction
_TG_AUDIT_ACTIONS = _tool_contract.TG_AUDIT_ACTIONS
_TG_CHECKPOINT_ACTIONS = _tool_contract.TG_CHECKPOINT_ACTIONS
_TG_CONTEXT_ACTIONS = _tool_contract.TG_CONTEXT_ACTIONS
_TG_EXPLORE_ACTIONS = _tool_contract.TG_EXPLORE_ACTIONS
_TG_IMPACT_ACTIONS = _tool_contract.TG_IMPACT_ACTIONS
_TG_NAVIGATE_ACTIONS = _tool_contract.TG_NAVIGATE_ACTIONS
_TG_QUERY_ACTIONS = _tool_contract.TG_QUERY_ACTIONS
_TG_REWRITE_ACTIONS = _tool_contract.TG_REWRITE_ACTIONS
_TG_SCAN_ACTIONS = _tool_contract.TG_SCAN_ACTIONS
_TG_SESSION_ACTIONS = _tool_contract.TG_SESSION_ACTIONS
register_mcp_tool = _tool_contract.register_mcp_tool

if TYPE_CHECKING:
    from tensor_grep.cli import mcp_server as _self
else:
    _self = sys.modules["tensor_grep.cli.mcp_server"]

_FIND_ROUTING_BACKEND = "HybridRank"
_FIND_ROUTING_REASON = "tg-find"


def _routing_summary(result: SearchResult) -> str:
    return (
        "Routing: "
        f"backend={result.routing_backend or 'unknown'} "
        f"reason={result.routing_reason or 'unknown'} "
        f"gpu_device_ids={result.routing_gpu_device_ids} "
        f"gpu_chunk_plan_mb={result.routing_gpu_chunk_plan_mb} "
        f"distributed={result.routing_distributed} "
        f"workers={result.routing_worker_count}"
    )


def _routing_payload(result: SearchResult) -> dict[str, object]:
    return {
        "backend": result.routing_backend or "unknown",
        "reason": result.routing_reason or "unknown",
        "gpu_device_ids": result.routing_gpu_device_ids,
        "gpu_chunk_plan_mb": result.routing_gpu_chunk_plan_mb,
        "distributed": result.routing_distributed,
        "workers": result.routing_worker_count,
    }


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


def _merge_runtime_routing(all_results: SearchResult, result: SearchResult) -> None:
    merge_runtime_routing(all_results, result)
    if result.fallback_reason is not None:
        all_results.fallback_reason = result.fallback_reason


def _merge_count_metadata(all_results: SearchResult, result: SearchResult) -> None:
    for file_path, count in result.match_counts_by_file.items():
        all_results.match_counts_by_file[file_path] = (
            all_results.match_counts_by_file.get(file_path, 0) + count
        )


def _apply_selected_gpu_defaults(
    *,
    all_results: SearchResult,
    selected_backend_name: str,
    selected_backend_reason: str,
) -> None:
    runtime_override_active = (
        all_results.routing_backend is not None
        and all_results.routing_backend != selected_backend_name
    ) or (
        all_results.routing_reason is not None
        and all_results.routing_reason != selected_backend_reason
    )
    if runtime_override_active:
        return
    if all_results.routing_worker_count != 0:
        return
    if not (all_results.routing_gpu_device_ids or all_results.routing_gpu_chunk_plan_mb):
        return
    (
        all_results.routing_distributed,
        all_results.routing_worker_count,
    ) = _selected_gpu_execution_defaults(
        list(all_results.routing_gpu_device_ids),
        list(all_results.routing_gpu_chunk_plan_mb),
    )


def _finalize_aggregate_result(all_results: SearchResult) -> None:
    all_results.matched_file_paths = sorted(
        set(all_results.matched_file_paths).union(match.file for match in all_results.matches)
    )
    if not all_results.match_counts_by_file and all_results.matches:
        for match in all_results.matches:
            all_results.match_counts_by_file[match.file] = (
                all_results.match_counts_by_file.get(match.file, 0) + 1
            )


def _find_invalid_input(query: str, path: str, message: str) -> str:
    payload = _envelope_base(
        routing_backend=_FIND_ROUTING_BACKEND,
        routing_reason=_FIND_ROUTING_REASON,
        include_schema_version=False,
    )
    payload.update(query=query, path=path, error={"code": "invalid_input", "message": message})
    return json.dumps(payload, indent=2)


@_register_legacy_tool  # type: ignore
def tg_find(
    query: str,
    path: str = ".",
    limit: int = 10,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    max_tokens: int = _DEFAULT_MCP_FIND_MAX_TOKENS,
    deadline: float | None = None,
    grounding: Literal["off", "local", "registry"] = "local",
    rerank: Literal["off", "auto", "cross-encoder"] = "off",
    citations: bool = False,
) -> str:
    """
    Whole-repo hybrid semantic search with optional installed native cross-encoder ordering.

    Walks and ranks the repository without a pattern pre-filter. Dense ranking requires the
    `semantic` extra and fetched model; missing assets disclose BM25 fallback through
    `rank_fallback_reason`. Scan/file/chunk bounds disclose `result_incomplete` and its cause.

    Args:
        query: Natural-language or keyword query to rank the corpus against.
        path: Root directory (or single file) to search. Confined to the MCP server root
            (cwd, or TG_MCP_ROOT if set); a path that legitimately lives outside it must be
            copied in first (fail-closed, not a silent drop).
        limit: Maximum ranked chunks to return.
        max_repo_files: Maximum repo files to scan before ranking.
        max_tokens: Bound the result set to ~N tokens, dropping the lowest-ranked matches
            first (0 = unbounded). Defaults to the same value the CLI's `tg find --max-tokens`
            uses (a guard test pins the two equal).
        deadline: Optional wall-clock budget in seconds for the repo walk/chunk phase. When
            exceeded, ranking covers only the partial corpus scanned so far and the response
            is marked `result_incomplete=true` instead of running unbounded.
        grounding: off, local (default), or registry (explicit bounded network requests).
        rerank: off (default), auto (disclosed unavailable fallback), or cross-encoder (refusal).
    """
    # Confine the scan root before deriving or walking secondary paths.
    try:
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            return _find_invalid_input(query, "[refused]", str(exc))

        refusal = _av.investigation_options_refusal(grounding, rerank) or _av.tg_find_refusal(
            query, limit
        )
        if refusal is not None:
            return _find_invalid_input(query, path, refusal)

        try:
            result = _self._execute_find(
                query,
                path,
                limit=limit,
                max_repo_files=max_repo_files,
                max_tokens=max_tokens,
                deadline=deadline,
                grounding=grounding,
                rerank=rerank,
            )
        except ConfigurationError as exc:
            return _find_invalid_input(
                query, path, _sanitized_tool_error("tg_find", exc, code="invalid_input")["message"]
            )
        except FileNotFoundError as exc:
            _log_tool_exception("tg_find", exc)
            payload = _envelope_base(
                routing_backend=_FIND_ROUTING_BACKEND,
                routing_reason=_FIND_ROUTING_REASON,
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = path
            payload["error"] = {"code": "invalid_input", "message": f"Path not found: {path}"}
            return json.dumps(payload, indent=2)
        except BackendExecutionError as exc:
            # Preserve execution failures and sanitize private diagnostic paths on the wire.
            payload = _envelope_base(
                routing_backend=_FIND_ROUTING_BACKEND,
                routing_reason=_FIND_ROUTING_REASON,
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = path
            payload["error"] = _sanitized_tool_error(
                "tg_find",
                exc,
                code="find_backend_error",
            )
            return json.dumps(payload, indent=2)
        except Exception as exc:  # S2: propagate as a structured, SANITIZED error -- never raw
            payload = _envelope_base(
                routing_backend=_FIND_ROUTING_BACKEND,
                routing_reason=_FIND_ROUTING_REASON,
                include_schema_version=False,
            )
            payload["query"] = query
            payload["path"] = path
            payload["error"] = _sanitized_tool_error("tg_find", exc)
            return json.dumps(payload, indent=2)

        # Stamp routing on this MCP result without changing the CLI's shared pipeline output.
        result.routing_backend = _FIND_ROUTING_BACKEND
        result.routing_reason = _FIND_ROUTING_REASON

        return serialize_mcp_find_result(
            result,
            path,
            query,
            citations=citations,
            inject_contract_fields=_self._inject_mcp_contract_fields,
        )
    except Exception as exc:
        _log_tool_exception("tg_find", exc)
        return _sanitized_tool_error_text("tg_find", exc)


_search_invalid_input_response = _av.search_invalid_input_response


@_register_legacy_tool  # type: ignore
@_bounds.bounded_response
def tg_search(
    pattern: str | None = None,
    path: str = ".",
    case_sensitive: bool = False,
    ignore_case: bool = False,
    fixed_strings: bool = False,
    word_regexp: bool = False,
    context: int | None = None,
    max_count: int | None = None,
    max_results: int | None = None,
    max_files: int | None = None,
    count_matches: bool = False,
    glob: str | None = None,
    type_filter: str | None = None,
    query: str | None = None,
    structured_json: bool = True,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    rank: bool = False,
    semantic: bool = False,
    citations: bool = False,
) -> str:
    """
    Search files for a regex or literal pattern and return bounded structured JSON. Routing,
    when applicable: ripgrep by default; count_matches uses the Rust backend; fixed_strings may
    use StringZilla when installed; without rg, the Rust backend, then a Python fallback
    (experimental GPU heuristic only when rg is absent). Optional BM25 `rank` / `semantic` re-rank.

    Args:
        pattern: A regular expression or exact string used for searching.
        query: Alias for pattern, accepted for agent callers that use query-shaped tools.
        path: A file or directory to search. Defaults to current directory.
        case_sensitive: Execute the search case sensitively.
        ignore_case: Search case insensitively (-i).
        fixed_strings: Treat pattern as a literal string instead of regex (-F).
        word_regexp: Only show matches surrounded by word boundaries (-w).
        context: Show NUM lines before and after each match (-C).
        max_count: Limit the number of matching lines per file (-m).
        max_results: Maximum materialized result rows to return. Defaults to 150.
        max_files: Maximum files to render. Defaults to 15.
        count_matches: Just count the matches using ultra-fast Rust backend (-c).
        glob: Include/exclude files matching glob (e.g. '*.py').
        type_filter: Only search files matching TYPE (e.g. 'py', 'js').
        structured_json: Return bounded structured JSON (default true). Set to false for
            plain-text output.
        max_repo_files: Maximum files the directory walk searches when the backend is not
            ripgrep (rg absent / GPU / hybrid / python-regex). Ignored when RipgrepBackend
            handles the whole-path search natively. Protects against an unscoped full-repo
            per-file search loop.
        rank: Re-rank results by BM25 lexical relevance to the query terms instead of grep
            order (pure-CPU ranking; no API key, no model download).
        semantic: Re-rank results by a hybrid of BM25 + local CPU dense-embedding relevance
            (RRF fusion), instead of grep order. No API key, no GPU. Requires the `semantic`
            extra and a fetched model; falls back to BM25-only (visibly, never silently, via
            `rank_fallback_reason`) when either is missing. Takes priority over `rank` when
            both are set.
    """
    try:
        search_pattern = pattern or query or ""

        # Meta dispatch carries original scope evidence alongside the confined absolute path.
        paths_defaulted = _search_scope.paths_defaulted(path)

        # round-8 security (audit #95 gate): confine the primary path/root param to the MCP root
        # before any scan -- see tg_repo_map for the systemic-finding rationale.
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            return _search_invalid_input_response(
                search_pattern, str(exc), path="[refused]", structured_json=structured_json
            )

        invalid_arg = _av.search_arg_error(search_pattern, context, max_count, type_filter)
        if invalid_arg is not None:
            return _search_invalid_input_response(
                search_pattern, invalid_arg, path=path, structured_json=structured_json
            )

        rendered_file_limit = max(0, max_files if max_files is not None else 15)
        rendered_result_limit = min(
            max(0, max_results if max_results is not None else 150), _bounds._MCP_MAX_RENDERED_ROWS
        )
        normalized_max_repo_files = max(1, int(max_repo_files))
        config = SearchConfig(
            case_sensitive=case_sensitive,
            ignore_case=ignore_case,
            fixed_strings=fixed_strings,
            word_regexp=word_regexp,
            context=context,
            max_count=max_count,
            count=count_matches,
            glob=[glob] if glob else None,
            file_type=[type_filter] if type_filter else None,
            no_messages=True,
        )

        pipeline = _self.Pipeline(config=config)
        backend = pipeline.get_backend()
        selected_backend_name = getattr(
            pipeline, "selected_backend_name", backend.__class__.__name__
        )
        selected_backend_reason = getattr(pipeline, "selected_backend_reason", "unknown")

        all_results = SearchResult(matches=[], total_files=0, total_matches=0)
        all_results.routing_backend = selected_backend_name
        all_results.routing_reason = selected_backend_reason
        all_results.routing_gpu_device_ids = list(
            getattr(pipeline, "selected_gpu_device_ids", []) or []
        )
        all_results.routing_gpu_chunk_plan_mb = list(
            getattr(pipeline, "selected_gpu_chunk_plan_mb", []) or []
        )
        all_results.fallback_reason = getattr(pipeline, "fallback_reason", None)
        scan_limit_payload: dict[str, Any] | None = None
        try:
            if isinstance(backend, RipgrepBackend):
                all_results = backend.search(path, search_pattern, config=config)
                all_results.routing_backend = all_results.routing_backend or selected_backend_name
                all_results.routing_reason = all_results.routing_reason or selected_backend_reason
            else:
                # H3 : before PR #400's fix landed here, this walk had
                # NO per-file wall-clock deadline, no BackendExecutionError fallback, and no
                # broad/vendored/large-root refusal -- an unscoped root could hang, and a mid-walk
                # backend fault fell through to the outer `except Exception` below and discarded
                # every match already collected. All three are now ported from the CLI (imported,
                # not reimplemented) so the two surfaces can't drift again.
                refusal_message, scanner, walker = _mcp_broad_root_scan_refusal(
                    path,
                    config,
                    normalized_max_repo_files=normalized_max_repo_files,
                    check_large_root=True,
                    paths_defaulted=paths_defaulted,
                )
                if refusal_message is not None:
                    return _broad_root_scan_refusal_result(
                        refusal_message,
                        pattern=search_pattern,
                        path=path,
                        structured_json=structured_json,
                    )
                files_scanned = 0
                scan_capped = False
                native_walk_deadline = compute_native_walk_deadline()
                for current_file in walker:
                    if files_scanned >= normalized_max_repo_files:
                        scan_capped = True
                        break
                    if _self.native_walk_deadline_exceeded(native_walk_deadline):
                        scan_capped = True
                        all_results.result_incomplete = True
                        all_results.incomplete_reason = (
                            "native search exceeded the wall-clock deadline and was stopped; "
                            "returning partial results. Scope the search to a smaller path, or "
                            "lower max_repo_files."
                        )
                        all_results.incomplete_reason_class = "deadline"
                        break
                    try:
                        result = backend.search(current_file, search_pattern, config=config)
                    except BackendExecutionError as exc:
                        # A native backend failed at runtime; retry on the always-available CPU
                        # backend so the search returns correct partial results instead of
                        # silently discarding everything collected so far (audit B2/I1, ported).
                        result = _self._search_with_cpu_fallback(
                            current_file, search_pattern, config, exc, backend
                        )
                    files_scanned += 1
                    all_results.matches.extend(result.matches)
                    all_results.matched_file_paths.extend(result.matched_file_paths)
                    _merge_count_metadata(all_results, result)
                    all_results.total_matches += result.total_matches
                    if result.total_files > 0 or result.total_matches > 0:
                        all_results.total_files += 1
                    _merge_runtime_routing(all_results, result)
                files_scanned or _av.probe_backend(backend, search_pattern, config)
                # The 200k-entry DirectoryScanner traversal budget (Q14) is a separate,
                # coarser defensive cap than max_repo_files -- it can trip first and
                # truncate the walk below max_repo_files without ever hitting the
                # per-file counter above, so OR it into possibly_truncated too. Coerce to a
                # real bool: a mocked scanner in a test can auto-vivify a truthy non-bool.
                scanner_truncated = bool(getattr(scanner, "scan_truncated", False))
                scan_capped = scan_capped or scanner_truncated
                scan_limit_payload = {
                    "max_repo_files": normalized_max_repo_files,
                    "scanned_files": files_scanned,
                    "possibly_truncated": scan_capped,
                }
                if scanner_truncated:
                    # Task #283. The comment above was written when `scan_truncated` could ONLY mean
                    # a BUDGET cap, which is what made folding it into a `max_repo_files`-shaped
                    # payload correct. #276 slice 1 (c0c3404) widened it to ALSO mean "the walk hit
                    # an unreadable path" -- a cause no budget increase can fix. Without the fields
                    # below, a single permission-denied directory surfaces to an MCP client as
                    # `scan_limit.possibly_truncated` next to a `max_repo_files` number, i.e. exactly
                    # the WRONG-KNOB advice that #276 exists to eliminate on the CLI surface,
                    # recreated one surface over.
                    #
                    # `possibly_truncated` stays honest either way (the scan really was truncated);
                    # these say WHY, and whether spending more budget can help.
                    cause = getattr(scanner, "scan_truncation_cause", None)
                    # Fail CLOSED on an unrecognised cause: report it as unknown and do NOT claim it
                    # is budget-remediable. A new cause the client cannot interpret must never
                    # default to "raise the limit" -- that is the allow-list rule (AGENTS.md).
                    if cause == "max-scan-entries":
                        scan_limit_payload["truncation_cause"] = "scan_limit"
                        scan_limit_payload["budget_remediable"] = True
                    elif cause == "unreadable_path":
                        scan_limit_payload["truncation_cause"] = "unreadable_path"
                        scan_limit_payload["budget_remediable"] = False
                    else:
                        scan_limit_payload["truncation_cause"] = "unknown"
                        scan_limit_payload["budget_remediable"] = False
                    unreadable_count = int(getattr(scanner, "unreadable_path_count", 0) or 0)
                    if unreadable_count:
                        # Reported even when the cause is the budget cap: the cap can fire AFTER a
                        # separate unreadable-path truncation, and the count must not go unmentioned
                        # just because the cap also tripped. Mirrors `search_command`'s CLI clarifier.
                        scan_limit_payload["unreadable_path_count"] = unreadable_count

            _apply_selected_gpu_defaults(
                all_results=all_results,
                selected_backend_name=selected_backend_name,
                selected_backend_reason=selected_backend_reason,
            )
            _self._finalize_aggregate_result(all_results)

            # audit #95 Part 2: mirror main.py search_command's --rank/--semantic post-processing
            # (`if config.semantic_rank: ... elif config.rank_bm25 and all_results.matches:`) so an
            # MCP agent caller gets the same BM25/hybrid-semantic relevance reordering as the CLI.
            # Applied once here, before the empty/count/full-result branches below, so every render
            # path sees the same reranked all_results -- matches main.py's ordering (rerank runs
            # before any output-mode branching, not duplicated per branch).
            if semantic:
                if all_results.matches:
                    try:
                        all_results = _self._apply_semantic_rerank(all_results, search_pattern)
                    except BackendExecutionError as exc:
                        # Backend Fail-Closed Contract boundary (mirrors main.py's search_command):
                        # _apply_semantic_rerank deliberately does NOT catch a genuine dense-backend
                        # fault (e.g. a corrupt model directory) -- it must surface here as a
                        # distinguishable structured error, never fall through to the generic
                        # internal_error catch-all at the bottom of this tool, which would lose the
                        # fail-closed signal (an agent needs to tell "the backend broke" apart from
                        # an ordinary internal_error).
                        if structured_json:
                            error_payload = {
                                "pattern": search_pattern,
                                "path": path,
                                "error": _sanitized_tool_error(
                                    "tg_search",
                                    exc,
                                    code="semantic_backend_error",
                                ),
                            }
                            # M14: error envelope crossed the wire un-stamped.
                            return _self._inject_mcp_contract_fields(
                                json.dumps(error_payload, indent=2)
                            )
                        _log_tool_exception("tg_search", exc)
                        cls_name = _safe_exception_class_name(exc)
                        return (
                            f"Search failed: semantic backend error ({cls_name}). "
                            "See server logs for detail."
                        )
                else:
                    # F16 parity (main.py _set_semantic_rank_fallback_reason): probe dense-leg
                    # availability even on a 0-match search so rank_fallback_reason is set whenever
                    # the leg is unavailable, regardless of match count.
                    _self._set_semantic_rank_fallback_reason(all_results)
            elif rank and all_results.matches:
                from tensor_grep.core.reranker import rerank_by_bm25

                all_results = rerank_by_bm25(
                    all_results, search_pattern, all_results.matched_file_paths
                )

            if citations:
                return result_citations_json(
                    all_results,
                    path,
                    scan_limit=scan_limit_payload,
                    max_results=rendered_result_limit,
                    max_files=rendered_file_limit,
                )

            empty_scan_capped = bool(
                scan_limit_payload and scan_limit_payload["possibly_truncated"]
            )
            if all_results.is_empty:
                if structured_json:
                    payload: dict[str, Any] = {
                        "pattern": search_pattern,
                        "path": path,
                        "total_matches": 0,
                        "total_files": all_results.total_files,
                        "rendered_match_count": 0,
                        "rendered_file_count": 0,
                        "matches": [],
                        "truncated": empty_scan_capped,
                        "omitted_matches": 0,
                        "omitted_files": 0,
                        # Partial results (rg exit 2 soft error, or a mid-walk deadline/fault) --
                        # top-level so an agent caller can't read a truncated result as complete.
                        "result_incomplete": all_results.result_incomplete,
                        "incomplete_reason": all_results.incomplete_reason,
                        **_incomplete_class_fragment(all_results),
                        "routing": _routing_payload(all_results),
                    }
                    if scan_limit_payload is not None:
                        payload["scan_limit"] = scan_limit_payload
                    # `--semantic` fail-closed degrade signal (audit #95 Part 2): emitted ONLY when
                    # set, mirroring json_fmt.py's own "omitted entirely, not null" rule so every
                    # OTHER (non-rank/non-semantic) search's envelope shape stays byte-identical.
                    if all_results.rank_fallback_reason:
                        payload["rank_fallback_reason"] = all_results.rank_fallback_reason
                    # M14: the no-match structured envelope crossed the wire un-stamped.
                    return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))
                capped_note = (
                    f"\nScan capped at {normalized_max_repo_files} files; results may be incomplete."
                    if empty_scan_capped
                    else ""
                )
                return (
                    f"No matches found for '{search_pattern}' in {path}.\n{_routing_summary(all_results)}"
                    f"{capped_note}"
                )

            if count_matches:
                # M10 : this branch used to ALWAYS return plain text,
                # ignoring `structured_json` (default True) -- a default caller doing
                # `json.loads()` on the response would fail. Honor the flag like every other
                # branch of this tool.
                if structured_json:
                    count_payload = {
                        "pattern": search_pattern,
                        "path": path,
                        "total_matches": all_results.total_matches,
                        "total_files": all_results.total_files,
                        # Same top-level truncated field the is_empty branch above already
                        # stamps from this exact value -- omitting it here (pre-fix) let a
                        # genuinely capped count-mode scan report incomplete.status=False,
                        # since result_incomplete alone stays False on a max_repo_files cap.
                        "truncated": empty_scan_capped,
                        "result_incomplete": all_results.result_incomplete,
                        "incomplete_reason": all_results.incomplete_reason,
                        **_incomplete_class_fragment(all_results),
                        "routing": _routing_payload(all_results),
                    }
                    if scan_limit_payload is not None:
                        count_payload["scan_limit"] = scan_limit_payload
                    if all_results.rank_fallback_reason:
                        count_payload["rank_fallback_reason"] = all_results.rank_fallback_reason
                    # M14: the count envelope crossed the wire un-stamped.
                    return _self._inject_mcp_contract_fields(json.dumps(count_payload, indent=2))
                capped_note = (
                    f"\nScan capped at {normalized_max_repo_files} files; results may be incomplete."
                    if empty_scan_capped
                    else ""
                )
                return (
                    f"Found a total of {all_results.total_matches} matches across {all_results.total_files} files in {path}.\n"
                    f"{_routing_summary(all_results)}{capped_note}"
                )

            by_file: dict[str, list[Any]] = {}
            for match in all_results.matches:
                if match.file not in by_file:
                    by_file[match.file] = []
                by_file[match.file].append(match)

            rendered_by_file: dict[str, list[Any]] = {}
            rendered_match_count = 0
            if by_file:
                for filepath, matches in by_file.items():
                    if filepath not in rendered_by_file:
                        if len(rendered_by_file) >= rendered_file_limit:
                            continue
                        rendered_by_file[filepath] = []
                    for match in matches:
                        if rendered_match_count >= rendered_result_limit:
                            break
                        rendered_by_file[filepath].append(match)
                        rendered_match_count += 1
                    if rendered_match_count >= rendered_result_limit:
                        break

            rendered_file_count = len(rendered_by_file)
            omitted_matches = max(0, all_results.total_matches - rendered_match_count)
            omitted_files = max(0, all_results.total_files - rendered_file_count)
            scan_capped = bool(scan_limit_payload and scan_limit_payload["possibly_truncated"])
            truncated = omitted_matches > 0 or omitted_files > 0 or scan_capped

            if structured_json:
                payload_matches, byte_cap = _bounds._render_rows(rendered_by_file, all_results)
                if byte_cap:
                    omitted_matches, omitted_files, rendered_file_count = byte_cap
                    truncated = True
                payload = {
                    "pattern": search_pattern,
                    "path": path,
                    "total_matches": all_results.total_matches,
                    "total_files": all_results.total_files,
                    "rendered_match_count": len(payload_matches),
                    "rendered_file_count": rendered_file_count,
                    "matches": payload_matches,
                    "truncated": truncated,
                    "omitted_matches": omitted_matches,
                    "omitted_files": omitted_files,
                    # Partial results (rg exit 2 soft error) â€” top-level so an agent caller can't
                    # read a truncated result as complete (suppression != absence).
                    "result_incomplete": all_results.result_incomplete,
                    "incomplete_reason": all_results.incomplete_reason,
                    **_incomplete_class_fragment(all_results),
                    "routing": _routing_payload(all_results),
                }
                if scan_limit_payload is not None:
                    payload["scan_limit"] = scan_limit_payload
                if all_results.rank_fallback_reason:
                    payload["rank_fallback_reason"] = all_results.rank_fallback_reason
                if all_results.rank_fusion is not None:
                    payload["rank_fusion"] = all_results.rank_fusion
                if byte_cap or _bounds._hit_rendering_ceiling(max_results, all_results):
                    payload["output_truncated"] = True
                # M14: the results envelope crossed the wire un-stamped.
                return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))

            # Format the results into a readable string for the LLM
            output = [
                f"Found {all_results.total_matches} matches across {all_results.total_files} files:",
                _routing_summary(all_results),
            ]

            if rendered_by_file:
                rendered_match_count, rendered_file_count, plain_capped = _bounds._render_plain(
                    output, rendered_by_file
                )
                if plain_capped:
                    omitted_matches = max(0, all_results.total_matches - rendered_match_count)
                    omitted_files = max(0, all_results.total_files - rendered_file_count)
                    truncated = True

                if truncated:
                    output.append(
                        f"\n... output truncated to {rendered_match_count} results across "
                        f"{rendered_file_count} files; omitted {omitted_matches} matches "
                        f"across {omitted_files} files."
                    )
                if scan_capped:
                    output.append(
                        f"\nScan capped at {normalized_max_repo_files} files; results may be incomplete."
                    )
            elif all_results.match_counts_by_file:
                rendered_counts = list(all_results.match_counts_by_file.items())[
                    :rendered_file_limit
                ]
                for filepath, count in rendered_counts:
                    output.append(f"\n{filepath}:")
                    output.append(f"  count={count}")
                omitted_count_files = max(
                    0, len(all_results.match_counts_by_file) - len(rendered_counts)
                )
                if omitted_count_files:
                    output.append(f"\n... and {omitted_count_files} more files.")
            elif all_results.matched_file_paths:
                rendered_paths = all_results.matched_file_paths[:rendered_file_limit]
                for filepath in rendered_paths:
                    output.append(f"\n{filepath}:")
                omitted_path_files = max(
                    0, len(all_results.matched_file_paths) - len(rendered_paths)
                )
                if omitted_path_files:
                    output.append(f"\n... and {omitted_path_files} more files.")

            return _bounds.emit_mcp_plain_search_output(
                output,
                result_incomplete=all_results.result_incomplete,
                incomplete_reason=all_results.incomplete_reason,
            )

        except _av.SEARCH_ERRORS as e:
            invalid = _av.search_error_message(e)
            if invalid is None:
                return _sanitized_tool_error_text("tg_search", e)
            return _search_invalid_input_response(
                search_pattern, invalid, path=path, structured_json=structured_json
            )
        except Exception as e:
            return _sanitized_tool_error_text("tg_search", e)
    except Exception as exc:
        _log_tool_exception("tg_search", exc)
        return _sanitized_tool_error_text("tg_search", exc)


_MAX_WORKSPACE_ROOTS = 8


def _tg_query_dispatch(
    action: str,
    *,
    pattern: str | None,
    query: str | None,
    lang: str | None,
    path: str,
    case_sensitive: bool,
    ignore_case: bool,
    fixed_strings: bool,
    word_regexp: bool,
    context: int | None,
    max_count: int | None,
    max_results: int | None,
    max_files: int | None,
    count_matches: bool,
    glob: str | None,
    type_filter: str | None,
    structured_json: bool,
    max_repo_files: int,
    rank: bool,
    semantic: bool,
    limit: int,
    max_tokens: int | None,
    deadline: float | None,
    paths_defaulted: bool = False,
    grounding: str = "local",
    rerank: str = "off",
    citations: bool = False,
) -> str:
    """Single-root dispatch core for `tg_query`, shared by the direct call and the per-root
    `workspace_roots` loop below. Assumes `path` is ALREADY confined."""
    if action in {"text", "search"}:
        search_pattern = pattern if pattern is not None else query
        return _search_scope.call(
            _self.tg_search,
            paths_defaulted=paths_defaulted,
            pattern=search_pattern,
            path=path,
            case_sensitive=case_sensitive,
            ignore_case=ignore_case,
            fixed_strings=fixed_strings,
            word_regexp=word_regexp,
            context=context,
            max_count=max_count,
            max_results=max_results,
            max_files=max_files,
            count_matches=count_matches,
            glob=glob,
            type_filter=type_filter,
            query=query,
            structured_json=structured_json,
            max_repo_files=max_repo_files,
            rank=rank,
            semantic=semantic,
            citations=citations,
        )
    if action == "ast":
        if pattern is None or lang is None:
            return _meta_missing_param_error("tg_query", action, "pattern and lang")
        return _self.tg_ast_search(
            pattern=pattern,
            lang=lang,
            path=path,
            structured_json=structured_json,
            max_repo_files=max_repo_files,
        )
    if action == "find":
        query_text = query if query is not None else pattern
        if query_text is None:
            return _meta_missing_param_error("tg_query", action, "query")
        effective_max_tokens = (
            max_tokens if max_tokens is not None else _DEFAULT_MCP_FIND_MAX_TOKENS
        )
        return _self.tg_find(
            query=query_text,
            path=path,
            limit=limit,
            max_repo_files=max_repo_files,
            max_tokens=effective_max_tokens,
            deadline=deadline,
            grounding=grounding,
            rerank=rerank,
            citations=citations,
        )
    if action == "index":
        if pattern is None:
            return _meta_missing_param_error("tg_query", action, "pattern")
        return _self.tg_index_search(pattern=pattern, path=path)
    return _meta_unknown_action_error("tg_query", action, _TG_QUERY_ACTIONS)


@_register_tool  # type: ignore
def tg_query(
    action: QueryAction,
    pattern: str | None = None,
    query: str | None = None,
    lang: str | None = None,
    path: str = ".",
    case_sensitive: bool = False,
    ignore_case: bool = False,
    fixed_strings: bool = False,
    word_regexp: bool = False,
    context: int | None = None,
    max_count: int | None = None,
    max_results: int | None = None,
    max_files: int | None = None,
    count_matches: bool = False,
    glob: str | None = None,
    type_filter: str | None = None,
    structured_json: bool = True,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
    rank: bool = False,
    semantic: bool = False,
    citations: bool = False,
    limit: int = 10,
    max_tokens: int | None = None,
    deadline: float | None = None,
    workspace_roots: list[str] | None = None,
    grounding: Literal["off", "local", "registry"] = "local",
    rerank: Literal["off", "auto", "cross-encoder"] = "off",
) -> str:
    """Code search: "search" (regex/literal), "ast" (pattern+lang), "find" (semantic), "index" (native trigram). Optional workspace_roots (<=8)."""
    try:
        try:
            confined_path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            return _meta_confinement_error("tg_query", action, exc)

        refusal = _av.investigation_options_refusal(grounding, rerank)
        if refusal is not None:
            return _meta_invalid_input_error("tg_query", action, refusal)
        confined_roots: list[tuple[str, bool]] | None = None
        if workspace_roots:
            max_roots = _self._MAX_WORKSPACE_ROOTS
            if len(workspace_roots) > max_roots:
                return _meta_workspace_roots_cap_error(
                    "tg_query",
                    action,
                    count=len(workspace_roots),
                    cap=max_roots,
                )
            try:
                confined_roots = [
                    (str(_confine_mcp_path(root, label="workspace_roots")), root == ".")
                    for root in workspace_roots
                ]
            except PathConfinementError as exc:
                return _meta_confinement_error("tg_query", action, exc)

        try:
            if confined_roots is None:
                return _self._tg_query_dispatch(
                    action,
                    pattern=pattern,
                    query=query,
                    lang=lang,
                    path=confined_path,
                    paths_defaulted=path == ".",
                    case_sensitive=case_sensitive,
                    ignore_case=ignore_case,
                    fixed_strings=fixed_strings,
                    word_regexp=word_regexp,
                    context=context,
                    max_count=max_count,
                    max_results=max_results,
                    max_files=max_files,
                    count_matches=count_matches,
                    glob=glob,
                    type_filter=type_filter,
                    structured_json=structured_json,
                    max_repo_files=max_repo_files,
                    rank=rank,
                    semantic=semantic,
                    citations=citations,
                    limit=limit,
                    max_tokens=max_tokens,
                    deadline=deadline,
                    grounding=grounding,
                    rerank=rerank,
                )

            results_by_root: dict[str, Any] = {}
            omitted_roots: list[str] = []
            # Share one absolute deadline across roots; later roots get only the remaining budget.
            loop_deadline_monotonic = _deadline_monotonic_from_seconds(deadline)
            for root, paths_defaulted in confined_roots:
                if (
                    loop_deadline_monotonic is not None
                    and time.monotonic() >= loop_deadline_monotonic
                ):
                    # The shared budget is already spent -- skip (never dispatch) rather than
                    # silently granting this root its own fresh deadline or silently dropping it
                    # from the response with no signal (reported via omitted_roots/partial below).
                    omitted_roots.append(root)
                    continue
                root_deadline = (
                    None
                    if loop_deadline_monotonic is None
                    else max(0.1, loop_deadline_monotonic - time.monotonic())
                )
                single_text = _self._tg_query_dispatch(
                    action,
                    pattern=pattern,
                    query=query,
                    lang=lang,
                    path=root,
                    paths_defaulted=paths_defaulted,
                    case_sensitive=case_sensitive,
                    ignore_case=ignore_case,
                    fixed_strings=fixed_strings,
                    word_regexp=word_regexp,
                    context=context,
                    max_count=max_count,
                    max_results=max_results,
                    max_files=max_files,
                    count_matches=count_matches,
                    glob=glob,
                    type_filter=type_filter,
                    structured_json=structured_json,
                    max_repo_files=max_repo_files,
                    rank=rank,
                    semantic=semantic,
                    citations=citations,
                    limit=limit,
                    max_tokens=max_tokens,
                    deadline=root_deadline,
                    grounding=grounding,
                    rerank=rerank,
                )
                try:
                    results_by_root[root] = json.loads(single_text)
                except json.JSONDecodeError:
                    results_by_root[root] = single_text

            if citations and action in {"text", "search", "find"}:
                return serialize_workspace_citations(results_by_root, omitted_roots, _mcp_root())

            extra: dict[str, Any] = {
                "path": confined_path,
                "workspace_roots": [root for root, _ in confined_roots],
                "results_by_root": results_by_root,
            }
            if omitted_roots:
                # Schema-additive: only present when the shared deadline actually truncated the
                # root fan-out, so a call that never hits the budget keeps the exact pre-existing
                # response shape.
                extra["omitted_roots"] = omitted_roots
                extra["partial"] = True
            payload = _meta_envelope(
                tool="tg_query",
                action=action,
                extra=extra,
            )
            return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))
        except Exception as exc:
            payload = _meta_envelope(tool="tg_query", action=action)
            payload["error"] = _sanitized_tool_error("tg_query", exc)
            return json.dumps(payload, indent=2)
    except Exception as exc:
        _log_tool_exception("tg_query", exc)
        return _sanitized_tool_error_text("tg_query", exc)
