"""MCP AST search and log classification tools."""

from __future__ import annotations

import itertools
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cpu_backend import (
    compute_native_walk_deadline,
)
from tensor_grep.cli import mcp_arg_validation as _av
from tensor_grep.cli import mcp_search_bounds as _bounds
from tensor_grep.cli import mcp_tool_contract as _tool_contract
from tensor_grep.cli.incompleteness import (
    incomplete_class_fragment as _incomplete_class_fragment,
)
from tensor_grep.cli.mcp.query import (
    _apply_selected_gpu_defaults,
    _finalize_aggregate_result,
    _merge_count_metadata,
    _merge_runtime_routing,
    _routing_payload,
    _routing_summary,
)
from tensor_grep.cli.mcp.security import (
    _broad_root_scan_refusal_result,
    _confine_mcp_path,
    _confine_read_path,
    _log_tool_exception,
    _mcp_root,
    _meta_invalid_input_error,
    _sanitized_tool_error,
    _sanitized_tool_error_text,
)
from tensor_grep.cli.mcp.server import (
    _DEFAULT_MCP_REPO_SCAN_LIMIT,
    _register_legacy_tool,
    _register_tool,
)
from tensor_grep.cli.mcp_path_errors import PathConfinementError
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.core.result import SearchResult

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

_AST_UNAVAILABLE = "AstBackend is not available on this system. Requires ast-grep/tree-sitter."


def _ast_error_result(code: str, message: str, pattern: str, lang: str, path: str, sj: bool) -> str:
    """`tg_ast_search` error response: code `unavailable` (ast-grep/tree-sitter absent) or
    `invalid_input` (malformed AST pattern, E-03)."""
    if not sj:
        return f"{'Error' if code == 'unavailable' else 'AST search failed'}: {message}"
    error = {"code": code, "message": message}
    payload = {"pattern": pattern, "lang": lang, "path": path, "error": error}
    # M14: error envelopes crossed the wire un-stamped.
    return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))


@_register_legacy_tool  # type: ignore
@_bounds.bounded_response
def tg_ast_search(
    pattern: str,
    lang: str,
    path: str = ".",
    structured_json: bool = True,
    max_repo_files: int = _DEFAULT_MCP_REPO_SCAN_LIMIT,
) -> str:
    """
    Search source code structurally using the ast-grep/tree-sitter backend.
    Ignores whitespace and formatting, matching the true AST structure.

    Args:
        pattern: AST pattern to search for (e.g. 'if ($A) { return $B; }').
        lang: Language to parse (e.g. 'python', 'javascript').
        path: Directory or file to search.
        structured_json: Return bounded structured JSON (default true). Set to false for
            plain-text output.
        max_repo_files: Maximum files the directory walk parses before the scan is
            capped (protects against an unscoped full-monorepo AST parse).
    """
    # Preserve whether the caller defaulted path before confinement makes it absolute.
    try:
        paths_defaulted = path == "."

        # Confine the primary path to the MCP root before any scan.
        try:
            path = str(_confine_mcp_path(path, label="path"))
        except PathConfinementError as exc:
            if structured_json:
                # M14: confinement-refusal envelope crossed the wire un-stamped.
                return _self._inject_mcp_contract_fields(
                    json.dumps(
                        {
                            "pattern": pattern,
                            "lang": lang,
                            "path": "[refused]",
                            "error": {"code": "invalid_input", "message": str(exc)},
                        },
                        indent=2,
                    )
                )
            return f"AST search failed: {exc}"

        lang_error = _av.unsupported_ast_language_message(lang)
        if lang_error is not None:
            return _ast_error_result(
                "invalid_input", lang_error, pattern, lang, path, structured_json
            )
        if not Path(path).exists():
            return _ast_error_result(
                "invalid_input", "AST input path not found.", pattern, lang, path, structured_json
            )

        normalized_max_repo_files = max(1, int(max_repo_files))
        config = SearchConfig(ast=True, lang=lang, no_messages=True)
        try:
            pipeline = _self.Pipeline(config=config)
            backend = pipeline.get_backend()
        except ConfigurationError as exc:
            _log_tool_exception("tg_ast_search", exc)
            # Fail closed with a STRUCTURED "unavailable" error instead of letting the
            # ConfigurationError escape as an unwrapped FastMCP ToolError (Backend Fail-Closed
            # Contract). `Pipeline(ast=True)` construction itself raises when the ast-grep /
            # tree-sitter deps are absent for this pattern (e.g. a Linux runner without ast-grep),
            # which is EARLIER than the backend-type check below -- mirror that branch's response
            # so a valid in-root path returns a clean "unavailable" rather than a raw exception.
            return _ast_error_result(
                "unavailable", _AST_UNAVAILABLE, pattern, lang, path, structured_json
            )

        backend_name = type(backend).__name__
        if backend_name not in {"AstBackend", "AstGrepWrapperBackend"}:
            return _ast_error_result(
                "unavailable", _AST_UNAVAILABLE, pattern, lang, path, structured_json
            )

        all_results = SearchResult(matches=[], total_files=0, total_matches=0)
        all_results.routing_backend = getattr(
            pipeline, "selected_backend_name", backend.__class__.__name__
        )
        all_results.routing_reason = getattr(pipeline, "selected_backend_reason", "unknown")
        all_results.routing_gpu_device_ids = list(
            getattr(pipeline, "selected_gpu_device_ids", []) or []
        )
        all_results.routing_gpu_chunk_plan_mb = list(
            getattr(pipeline, "selected_gpu_chunk_plan_mb", []) or []
        )
        all_results.fallback_reason = getattr(pipeline, "fallback_reason", None)
        try:
            # H3: apply tg_search's walk/deadline safeguards; AST always uses the large-root probe.
            refusal_message, _scanner, walker = _self._mcp_broad_root_scan_refusal(
                path,
                config,
                normalized_max_repo_files=normalized_max_repo_files,
                check_large_root=True,
                paths_defaulted=paths_defaulted,
            )
            if refusal_message is not None:
                return _broad_root_scan_refusal_result(
                    refusal_message,
                    pattern=pattern,
                    path=path,
                    lang=lang,
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
                        "native AST search exceeded the wall-clock deadline and was stopped; "
                        "returning partial results. Scope the search to a smaller path, or "
                        "lower max_repo_files."
                    )
                    all_results.incomplete_reason_class = "deadline"
                    break
                try:
                    result = backend.search(current_file, pattern, config=config)
                except BackendExecutionError as exc:
                    # Unlike `tg_search`'s regex CPU-fallback, there is no equivalent
                    # same-contract fallback engine for an AST query (CPUBackend does not
                    # understand `config.ast`/`config.lang` and would silently degrade to a
                    # nonsensical plain-text match on the AST pattern string -- exactly the
                    # "silently swap engines for a contract flag" failure the Backend
                    # Fail-Closed Contract forbids). Skip just this file, keep every match
                    # already collected, and mark the result explicitly incomplete instead.
                    sys.stderr.write(
                        f"tensor-grep-mcp: tg_ast_search backend failed on {current_file} "
                        f"({exc}); skipping file, keeping partial AST results.\n"
                    )
                    all_results.result_incomplete = True
                    if not all_results.incomplete_reason:
                        all_results.incomplete_reason = (
                            f"AST backend failed on one or more files (first: {current_file}); "
                            "returning partial results."
                        )
                    # `incomplete_reason_class` is DELIBERATELY NOT SET here. The closed vocabulary is
                    # exactly {unreadable_path, scan_limit, deadline, timeout} (docs/CONTRACTS.md), and
                    # a backend parse/exec failure is none of them. Labelling it with the nearest-
                    # looking member -- `unreadable_path` is the tempting one -- would tell an agent the
                    # PATH was the problem and send it to fix file permissions for a backend bug.
                    #
                    # The vocabulary is an allow-list, so "cannot classify" means EMIT NOTHING, not
                    # "invent a member". `result_incomplete` is still true, so the caller still knows
                    # the answer is partial; it just cannot branch on remediability, which is the
                    # honest state of affairs. A consumer must therefore treat an ABSENT class as
                    # "unclassified", never as "complete" -- see the contract note added with this
                    # change.
                    files_scanned += 1
                    continue
                files_scanned += 1
                all_results.matches.extend(result.matches)
                all_results.matched_file_paths.extend(result.matched_file_paths)
                _merge_count_metadata(all_results, result)
                all_results.total_matches += result.total_matches
                if result.total_files > 0 or result.total_matches > 0:
                    all_results.total_files += 1
                _merge_runtime_routing(all_results, result)
            # NOTE: unlike `tg_search`, this does NOT OR in `scanner.scan_truncated` -- doing so
            # was tried and reverted (it broke `test_mcp_ast_search_reports_no_cap_hit_when_under_the_limit`:
            # a bare `MagicMock()` scanner auto-vivifies a truthy `.scan_truncated` attribute unless a
            # test explicitly stubs it False, which is out of scope for this fix).
            scan_limit_payload = {
                "max_repo_files": normalized_max_repo_files,
                "scanned_files": files_scanned,
                "possibly_truncated": scan_capped,
            }

            _apply_selected_gpu_defaults(
                all_results=all_results,
                selected_backend_name=getattr(
                    pipeline, "selected_backend_name", backend.__class__.__name__
                ),
                selected_backend_reason=getattr(pipeline, "selected_backend_reason", "unknown"),
            )
            _finalize_aggregate_result(all_results)

            if all_results.total_matches == 0:
                # E-03: ast-grep exits 0 with `[]` for a malformed pattern; ask it ONLY on zero
                # matches (a warned pattern that still matches is returned normally).
                warn = getattr(backend, "pattern_warning", None)
                problem = warn(pattern, config) if callable(warn) else None
                if problem:
                    message = f"Invalid AST pattern for language {lang!r}: {problem}"
                    return _ast_error_result(
                        "invalid_input", message, pattern, lang, path, structured_json
                    )

            if all_results.is_empty:
                if structured_json:
                    # M14: no-match envelope crossed the wire un-stamped.
                    return _self._inject_mcp_contract_fields(
                        json.dumps(
                            {
                                "pattern": pattern,
                                "lang": lang,
                                "path": path,
                                "total_matches": 0,
                                "total_files": all_results.total_files,
                                "rendered_match_count": 0,
                                "rendered_file_count": 0,
                                "matches": [],
                                "truncated": scan_capped,
                                "omitted_matches": 0,
                                "omitted_files": 0,
                                "result_incomplete": all_results.result_incomplete,
                                "incomplete_reason": all_results.incomplete_reason,
                                **_incomplete_class_fragment(all_results),
                                "scan_limit": scan_limit_payload,
                                "routing": _routing_payload(all_results),
                            },
                            indent=2,
                        )
                    )
                capped_note = (
                    f"\nScan capped at {normalized_max_repo_files} files; results may be incomplete."
                    if scan_capped
                    else ""
                )
                return (
                    f"No AST matches found for pattern in {path}.\n{_routing_summary(all_results)}"
                    f"{capped_note}"
                )

            # Group by file
            by_file: dict[str, list[Any]] = {}
            for match in all_results.matches:
                if match.file not in by_file:
                    by_file[match.file] = []
                by_file[match.file].append(match)

            if structured_json:
                rendered_file_limit = 15
                rendered_result_limit = 150
                rendered_by_file: dict[str, list[Any]] = {}
                rendered_match_count = 0
                for filepath, matches in by_file.items():
                    if len(rendered_by_file) >= rendered_file_limit:
                        break
                    rendered_by_file[filepath] = []
                    for match in matches:
                        if rendered_match_count >= rendered_result_limit:
                            break
                        rendered_by_file[filepath].append(match)
                        rendered_match_count += 1
                rendered_file_count = len(rendered_by_file)
                omitted_matches = max(0, all_results.total_matches - rendered_match_count)
                omitted_files = max(0, all_results.total_files - rendered_file_count)
                payload_matches, byte_cap = _bounds._render_rows(rendered_by_file, all_results)
                if byte_cap:
                    omitted_matches, omitted_files, rendered_file_count = byte_cap
                ast_payload: dict[str, Any] = {
                    "pattern": pattern,
                    "lang": lang,
                    "path": path,
                    "total_matches": all_results.total_matches,
                    "total_files": all_results.total_files,
                    "rendered_match_count": len(payload_matches),
                    "rendered_file_count": rendered_file_count,
                    "matches": payload_matches,
                    "truncated": omitted_matches > 0 or omitted_files > 0 or scan_capped,
                    "omitted_matches": omitted_matches,
                    "omitted_files": omitted_files,
                    "result_incomplete": all_results.result_incomplete,
                    "incomplete_reason": all_results.incomplete_reason,
                    **_incomplete_class_fragment(all_results),
                    "scan_limit": scan_limit_payload,
                    "routing": _routing_payload(all_results),
                }
                if byte_cap:
                    ast_payload["output_truncated"] = True
                # M14: results envelope crossed the wire un-stamped.
                return _self._inject_mcp_contract_fields(json.dumps(ast_payload, indent=2))

            output = [
                f"Found {all_results.total_matches} structural AST matches across {all_results.total_files} files:",
                _routing_summary(all_results),
            ]
            if scan_capped:
                output.append(
                    f"Scan capped at {normalized_max_repo_files} files; results may be incomplete."
                )

            if by_file:
                _bounds._render_ast_plain(output, by_file, all_results.total_matches)
            elif all_results.match_counts_by_file:
                for filepath, count in list(all_results.match_counts_by_file.items())[:15]:
                    output.append(f"\n{filepath}:")
                    output.append(f"  count={count}")
                if len(all_results.match_counts_by_file) > 15:
                    output.append(
                        f"\n... and {len(all_results.match_counts_by_file) - 15} more files."
                    )
            elif all_results.matched_file_paths:
                for filepath in all_results.matched_file_paths[:15]:
                    output.append(f"\n{filepath}:")
                if len(all_results.matched_file_paths) > 15:
                    output.append(
                        f"\n... and {len(all_results.matched_file_paths) - 15} more files."
                    )

            return "\n".join(_bounds._cap_output_lines(output))

        except Exception as e:
            if structured_json:
                # M14: exception envelope crossed the wire un-stamped.
                return _self._inject_mcp_contract_fields(
                    json.dumps(
                        {
                            "pattern": pattern,
                            "lang": lang,
                            "path": path,
                            "error": _sanitized_tool_error("tg_ast_search", e),
                        },
                        indent=2,
                    )
                )
            return _sanitized_tool_error_text("tg_ast_search", e)
    except Exception as exc:
        _log_tool_exception("tg_ast_search", exc)
        return _sanitized_tool_error_text("tg_ast_search", exc)


_CLASSIFY_TEXT_MAX_BYTES = 2 * 1024 * 1024
_CLASSIFY_ANOMALY_LINE_MAX_CHARS = 512
_CLASSIFY_LINE_PATTERN = re.compile(r"[^\r\n]+(?:\r\n|\r|\n)?|(?:\r\n|\r|\n)")


@_register_tool  # type: ignore
def tg_classify_logs(
    file_path: str | None = None,
    structured_json: bool = True,
    text: str | None = None,
) -> str:
    """Classify anomalies in a log file or inline text (heuristics default; CyBERT if TENSOR_GREP_CLASSIFY_PROVIDER=cybert)."""
    # round-7 security (audit #81 #1): confine file_path to the project root (cwd) before any
    # read -- unconfined it is an arbitrary-file-read/exfil primitive (FallbackReader will
    # happily read .env/keys/anything locally readable, and up to 20 heuristic-flagged lines
    # are echoed back verbatim in anomalies[].text below). Forward the RESOLVED path so the
    # downstream read below sees the same anchor-validated location this check validated.
    try:
        if (file_path is None and text is None) or (file_path is not None and text is not None):
            message = "Provide exactly one of file_path or text."
            if structured_json:
                return _meta_invalid_input_error("tg_classify_logs", "classify_logs", message)
            return f"Error: {message}"

        target_label = "inline text"
        if file_path is not None:
            try:
                file_path = str(_confine_read_path(file_path, _mcp_root(), label="file_path"))
            except PathConfinementError as exc:
                if structured_json:
                    # M14: confinement-refusal envelope crossed the wire un-stamped.
                    return _self._inject_mcp_contract_fields(
                        json.dumps(
                            {
                                "file_path": "[refused]",
                                "error": {"code": "invalid_input", "message": str(exc)},
                            },
                            indent=2,
                        )
                    )
                return f"Error: {exc}"
            target_label = file_path

        try:
            from tensor_grep.io.reader_fallback import FallbackReader
            from tensor_grep.sidecar import (
                DEFAULT_CLASSIFY_MAX_LINES,
                _apply_classify_line_budget,
                _classify_lines_with_metadata,
            )

            if text is not None:
                if (
                    len(text) > _CLASSIFY_TEXT_MAX_BYTES
                    or len(text.encode("utf-8", errors="replace")) > _CLASSIFY_TEXT_MAX_BYTES
                ):
                    message = (
                        f"text input exceeds maximum size of {_CLASSIFY_TEXT_MAX_BYTES} bytes."
                    )
                    if structured_json:
                        return _meta_invalid_input_error(
                            "tg_classify_logs", "classify_logs", message
                        )
                    return f"Error: {message}"
                lines = list(
                    itertools.islice(
                        (m.group(0) for m in _CLASSIFY_LINE_PATTERN.finditer(text)),
                        DEFAULT_CLASSIFY_MAX_LINES + 1,
                    )
                )
                if not lines:
                    message = "text input is empty."
                    if structured_json:
                        return _meta_invalid_input_error(
                            "tg_classify_logs", "classify_logs", message
                        )
                    return f"Error: {message}"
            else:
                assert file_path is not None
                reader = FallbackReader()
                # round-7 security (audit #81 #1): bound the read BEFORE materializing. read_lines()
                # is a generator; previously `list(reader.read_lines(file_path))` fully materialized
                # the entire file into memory before the DEFAULT_CLASSIFY_MAX_LINES budget was applied
                # below -- an unbounded-memory DoS on a large (or attacker-influenceable) file. Cap the
                # read one line past the budget via itertools.islice so `_apply_classify_line_budget`
                # can still report `truncated=True` accurately without reading the rest of the file.
                lines = list(
                    itertools.islice(reader.read_lines(file_path), DEFAULT_CLASSIFY_MAX_LINES + 1)
                )
                if not lines:
                    if structured_json:
                        # M14: empty/unreadable envelope crossed the wire un-stamped.
                        return _self._inject_mcp_contract_fields(
                            json.dumps(
                                {
                                    "file_path": file_path,
                                    "error": {
                                        "code": "invalid_input",
                                        "message": f"File {file_path} is empty or unreadable.",
                                    },
                                },
                                indent=2,
                            )
                        )
                    return f"Error: File {file_path} is empty or unreadable."

            budgeted_lines, line_budget = _apply_classify_line_budget(
                lines,
                DEFAULT_CLASSIFY_MAX_LINES,
            )
            results, backend_metadata = _classify_lines_with_metadata(budgeted_lines)
            provider_used = backend_metadata.get("provider_used", "heuristic")
            provider_status = backend_metadata.get("provider_status", "local")

            warnings_or_errors = []
            for i, r in enumerate(results):
                if r["label"] in ("warn", "error", "critical") and r["confidence"] > 0.8:
                    line_text = budgeted_lines[i].strip()
                    if len(line_text) > _CLASSIFY_ANOMALY_LINE_MAX_CHARS:
                        line_text = line_text[: _CLASSIFY_ANOMALY_LINE_MAX_CHARS - 3] + "..."
                    warnings_or_errors.append((
                        line_text,
                        r["label"],
                        r["confidence"],
                    ))

            if structured_json:
                # M14: the success envelope crossed the wire un-stamped.
                payload: dict[str, Any] = {
                    "provider": provider_used,
                    "provider_status": provider_status,
                    "sample_lines": line_budget["emitted_lines"],
                    "total_lines": line_budget["total_lines"],
                    # Round-3 audit finding: sample_lines/total_lines are numeric
                    # fields a client must compare itself; stamp the same explicit
                    # top-level truncated boolean every other capped tool already
                    # carries, so unified_incomplete_envelope's generic check works.
                    "truncated": line_budget["emitted_lines"] < line_budget["total_lines"],
                    "anomaly_count": len(warnings_or_errors),
                    "anomalies": [
                        {"label": label, "confidence": conf, "text": line_text}
                        for line_text, label, conf in warnings_or_errors[:20]
                    ],
                }
                if file_path is not None:
                    payload["file_path"] = file_path
                else:
                    payload["source"] = "text"
                return _self._inject_mcp_contract_fields(json.dumps(payload, indent=2))

            if not warnings_or_errors:
                return f"No severe anomalies detected in {target_label}. All logs appear nominal."

            output = [
                (
                    f"Log Classification for {target_label} "
                    f"(provider={provider_used}, status={provider_status}, "
                    f"sample={line_budget['emitted_lines']}/{line_budget['total_lines']} lines):"
                )
            ]
            output.append(f"\nDetected {len(warnings_or_errors)} High-Confidence Anomalies:")
            for text, label, conf in warnings_or_errors[:20]:  # Limit output
                output.append(f"[{label.upper()}] ({conf:.2f}) {text}")

            return "\n".join(output)

        except Exception as e:
            if structured_json:
                # M14: exception envelope crossed the wire un-stamped.
                return _self._inject_mcp_contract_fields(
                    json.dumps(
                        {
                            "file_path": file_path,
                            "error": _sanitized_tool_error("tg_classify_logs", e),
                        },
                        indent=2,
                    )
                )
            return _sanitized_tool_error_text("tg_classify_logs", e)
    except Exception as exc:
        _log_tool_exception("tg_classify_logs", exc)
        return _sanitized_tool_error_text("tg_classify_logs", exc)
