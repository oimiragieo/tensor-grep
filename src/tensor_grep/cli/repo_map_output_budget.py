"""Output-budget trimming and context rendering, lifted out of `repo_map`.

These are the payload-shaping passes that run AFTER the symbol graph is built: per-command
output caps (repo map, source blocks, symbol fields, blast radius) and the token-budgeted
context renderer's part scoring, sorting and string assembly. Split out of `repo_map.py` under
docs/design/2026-08-19-split-floor-escape.md.

`_render_context_string_and_sections` deliberately stays in `repo_map`: the test suite
monkeypatches it there, so a moved copy would be the unpatched one.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.cli.incompleteness import budget_remediable
from tensor_grep.cli.repo_map_lang_js import (
    _js_ast_omitted_relative_lines as _js_ast_omitted_relative_lines,
)
from tensor_grep.cli.repo_map_lang_python import (
    _python_ast_omitted_relative_lines as _python_ast_omitted_relative_lines,
)
from tensor_grep.cli.repo_map_lang_rust import (
    _rust_ast_omitted_relative_lines as _rust_ast_omitted_relative_lines,
)

# Route A late binding (docs/design/2026-08-19-split-floor-escape.md). `_self` is
# `tensor_grep.cli.repo_map`, NOT this module: the test suite patches names there, and a
# bare call resolved through this file's globals would run the unpatched original while the
# test still passed. A plain import would be circular (repo_map imports this module at its
# top), so the runtime branch resolves through sys.modules on each attribute read. The
# TYPE_CHECKING branch never executes; it is what keeps mypy on the real signatures.
if TYPE_CHECKING:
    from tensor_grep.cli import repo_map as _self
else:

    class _RepoMapProxy:
        """Late-binding view of `tensor_grep.cli.repo_map`."""

        __slots__ = ()

        def __getattr__(self, name: str) -> Any:
            module = sys.modules.get("tensor_grep.cli.repo_map")
            if module is None:
                module = importlib.import_module("tensor_grep.cli.repo_map")
            return getattr(module, name)

    _self = _RepoMapProxy()


def apply_repo_map_output_limits(
    payload: dict[str, Any],
    *,
    max_files: int | None = None,
) -> dict[str, Any]:
    if max_files is None:
        return payload

    normalized_max_files = max(1, int(max_files))
    limited = dict(payload)
    original_files = [str(current) for current in payload.get("files", [])]
    selected_files = original_files[:normalized_max_files]
    selected_file_set = set(selected_files)
    original_tests = [str(current) for current in payload.get("tests", [])]
    selected_tests = original_tests[:normalized_max_files]
    selected_test_set = set(selected_tests)

    limited["files"] = selected_files
    limited["tests"] = selected_tests
    limited["symbols"] = [
        dict(symbol)
        for symbol in payload.get("symbols", [])
        if str(symbol.get("file", "")) in selected_file_set
    ]
    limited["imports"] = [
        dict(entry)
        for entry in payload.get("imports", [])
        if str(entry.get("file", "")) in selected_file_set
    ]
    for key in ("file_matches", "file_summaries", "sources"):
        if key in payload:
            limited[key] = [
                dict(entry)
                for entry in payload.get(key, [])
                if str(entry.get("path", entry.get("file", ""))) in selected_file_set
            ]
    if "test_matches" in payload:
        limited["test_matches"] = [
            dict(entry)
            for entry in payload.get("test_matches", [])
            if str(entry.get("path", entry.get("file", ""))) in selected_test_set
        ]
    if "related_paths" in payload:
        allowed_related_paths = selected_file_set | selected_test_set
        limited["related_paths"] = [
            str(path)
            for path in payload.get("related_paths", [])
            if str(path) in allowed_related_paths
        ]
    _output_capped = len(original_files) > normalized_max_files
    limited["output_limit"] = {
        "max_files": normalized_max_files,
        "emitted_files": len(selected_files),
        "original_files": len(original_files),
        # output_limit operates on files already filtered by the repo-map walk,
        # so these are always project files; possibly_truncated is accurate here.
        "possibly_truncated": _output_capped,
        "truncation_cause": "project-files" if _output_capped else None,
        # Unlike the two scan_limit blocks, "truncated" and "capped" are the SAME condition here,
        # so this gate is not the narrow one. The value is DERIVED from the cause rather than
        # hardcoded True: output_limit only ever reports `project-files` today, but deriving it
        # means a future cause cannot silently inherit "just raise the limit".
        **({"budget_remediable": budget_remediable("project-files")} if _output_capped else {}),
    }
    return limited


def _apply_context_token_budget(payload: dict[str, Any], max_tokens: int | None) -> dict[str, Any]:
    """Bound the serialized context pack to ~``max_tokens`` so it stays prompt-injection-ready.

    FILE-DRIVEN + coherent: reduces the ranked-file count via ``apply_repo_map_output_limits`` (which
    keeps each retained file WITH its symbols/imports/matches consistently), so the bounded pack is a
    smaller top-ranked slice, never a file list gutted of its symbols. Adapts to file size -- a repo
    of huge files fits fewer, a repo of small files fits more. ``max_tokens`` of ``None`` / ``<= 0``
    is a no-op (unbounded opt-out). Records ``token_budget`` honestly.
    """
    if max_tokens is None or max_tokens <= 0:
        return payload
    estimated = _self._estimate_payload_tokens(payload)
    if estimated <= max_tokens:
        capped = dict(payload)
        capped["token_budget"] = {
            "max_tokens": max_tokens,
            "estimated_tokens": estimated,
            "truncated": False,
        }
        return capped
    file_count = len(payload.get("files", []))
    capped = payload
    while file_count > 1 and estimated > max_tokens:
        # Proportional first guess, then strictly shrink so we always make progress.
        guess = max(1, min(file_count - 1, file_count * max_tokens // max(estimated, 1)))
        capped = apply_repo_map_output_limits(payload, max_files=guess)
        estimated = _self._estimate_payload_tokens(capped)
        file_count = guess
    if capped is payload:  # over budget even before shrinking (single-file pack); take the top file
        capped = apply_repo_map_output_limits(payload, max_files=1)
        estimated = _self._estimate_payload_tokens(capped)
    capped = dict(capped)
    capped["token_budget"] = {
        "max_tokens": max_tokens,
        "estimated_tokens": estimated,
        "truncated": True,
    }
    return capped


def _apply_symbol_token_budget(
    payload: dict[str, Any],
    max_tokens: int | None,
    *,
    primary_field: str,
    companion_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Bound a defs/refs/callers/impact payload to ~``max_tokens`` (design #96 item 4).

    Modeled on ``_apply_context_token_budget``'s serialize-then-measure approach, but with an
    ANSWER-FIRST shrink order: SECONDARY fields (``tests``, ``related_paths`` -- whichever are
    present; each field's ``{field}_matches`` companion, e.g. impact's ``test_matches``, is
    cleared alongside it so the real bloat source is not left untouched) are cleared FIRST since
    they are supporting context, not the answer itself. Only if the payload is STILL over budget
    after zeroing every secondary field is the PRIMARY answer array (``primary_field`` --
    ``definitions``/``references``/``callers``/``files``) trimmed, and that is flagged distinctly
    (``token_budget.primary_truncated``/``primary_omitted``) so an agent trusting "here are all N
    callers" can tell N was cut for space, not because there were only N. ``companion_fields``
    (e.g. impact's ``file_matches``, which shares ``files``'s exact order/length by construction)
    are sliced to the same length as the trimmed primary array so the two never disagree.

    ``max_tokens`` of None/<=0 is a no-op (unbounded opt-out), matching
    ``_apply_context_token_budget``. This is an OUTPUT-cap, never a scan-truncation signal: it
    must never set ``result_incomplete``/``partial``/``caller_scan_limit`` (design #96 contract
    safety section) -- achieved simply by never touching those keys.
    """
    if max_tokens is None or max_tokens <= 0:
        return payload
    estimated = _self._estimate_payload_tokens(payload)
    if estimated <= max_tokens:
        capped = dict(payload)
        capped["token_budget"] = {
            "max_tokens": max_tokens,
            "estimated_tokens": estimated,
            "truncated": False,
            "primary_truncated": False,
        }
        return capped

    capped = dict(payload)
    secondary_trimmed: list[str] = []
    for field_name in _self._SYMBOL_TOKEN_BUDGET_SECONDARY_FIELDS:
        if estimated <= max_tokens:
            break
        current_value = capped.get(field_name)
        if isinstance(current_value, list) and current_value:
            capped[field_name] = []
            companion = f"{field_name}_matches"
            if isinstance(capped.get(companion), list):
                capped[companion] = []
            secondary_trimmed.append(field_name)
            estimated = _self._estimate_payload_tokens(capped)

    primary_truncated = False
    primary_omitted = 0
    if estimated > max_tokens:
        primary_list = list(capped.get(primary_field) or [])
        original_primary_count = len(primary_list)
        count = original_primary_count
        # Floor at 1, never 0 (mirrors _apply_context_token_budget's file-shrink floor): trimming
        # the primary answer array all the way to an EMPTY list is indistinguishable from a
        # genuine "not found" (the exact "confident false zero" this codebase's own
        # _scan_truncation_warning docstring calls "the single most dangerous output for a
        # refactor-safety tool") -- _emit_symbol_command_result reads an empty primary field as
        # not_found and exits 1, which would silently relabel a budget trim as an absence.
        while count > 1 and estimated > max_tokens:
            # Proportional first guess, then strictly shrink so we always make progress (mirrors
            # _apply_context_token_budget's file-shrink loop).
            guess = max(1, min(count - 1, count * max_tokens // max(estimated, 1)))
            capped[primary_field] = primary_list[:guess]
            estimated = _self._estimate_payload_tokens(capped)
            count = guess
        # count/original_primary_count already <=1 (0 or 1 entries): nothing left to trim without
        # zeroing the answer out, so best-effort stop here even if still over budget -- keeping a
        # truthful non-empty answer outranks strictly honoring the token cap.
        new_primary_len = len(capped.get(primary_field) or [])
        primary_omitted = max(0, original_primary_count - new_primary_len)
        primary_truncated = primary_omitted > 0
        if primary_truncated:
            surviving_primary = capped.get(primary_field) or []
            # Filter by PATH MEMBERSHIP (not index/length slicing): a companion like impact's
            # `file_matches` is not guaranteed to stay index-aligned with `files` once the CLI
            # layer has post-processed the primary field (e.g. impact's own caller-merge step
            # appends extra file paths to `files` with no matching `file_matches` entry) -- a
            # length-slice would silently keep the WRONG entries in that case.
            if surviving_primary and all(isinstance(item, str) for item in surviving_primary):
                surviving_set = set(surviving_primary)
                for companion in companion_fields:
                    companion_value = capped.get(companion)
                    if isinstance(companion_value, list):
                        capped[companion] = [
                            entry
                            for entry in companion_value
                            if not (isinstance(entry, dict) and "path" in entry)
                            or str(entry["path"]) in surviving_set
                        ]
            else:
                for companion in companion_fields:
                    companion_value = capped.get(companion)
                    if isinstance(companion_value, list):
                        capped[companion] = companion_value[:new_primary_len]

    capped["token_budget"] = {
        "max_tokens": max_tokens,
        "estimated_tokens": estimated,
        "truncated": True,
        "secondary_fields_trimmed": secondary_trimmed,
        "primary_truncated": primary_truncated,
        "primary_omitted": primary_omitted,
    }
    return capped


def _render_context_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"kind": "query", "text": f"Query: {payload['query']}"}]
    file_matches_by_path = {str(match["path"]): match for match in payload.get("file_matches", [])}
    test_matches_by_path = {str(match["path"]): match for match in payload.get("test_matches", [])}
    symbol_scores_by_key = {
        (str(symbol["file"]), str(symbol["name"])): int(symbol.get("score", 0))
        for symbol in payload.get("symbols", [])
    }
    tests = [str(current) for current in payload.get("tests", [])]
    if tests:
        test_lines = ["Tests:", *[f"- {current}" for current in tests[:3]]]
        parts.append({
            "kind": "tests",
            "text": "\n".join(test_lines),
            "paths": tests[:3],
            "provenance": {
                "matches": [
                    {
                        "path": current,
                        "score": int(test_matches_by_path.get(current, {}).get("score", 0)),
                        "graph_score": test_matches_by_path.get(current, {}).get("graph_score"),
                        "reasons": list(test_matches_by_path.get(current, {}).get("reasons", [])),
                    }
                    for current in tests[:3]
                ]
            },
        })

    sources_by_file: dict[str, list[dict[str, Any]]] = {}
    for source in payload.get("sources", []):
        current = str(source["file"])
        current_sources = sources_by_file.setdefault(current, [])
        current_sources.append(source)

    max_files = int(payload.get("max_files", 3))
    summaries = list(payload.get("file_summaries", []))[:max_files]
    summarized_paths = {str(summary["path"]) for summary in summaries}
    for current in [str(path) for path in payload.get("files", [])[:max_files]]:
        if current in summarized_paths or current not in sources_by_file:
            continue
        summaries.append({"path": current, "symbols": []})
        summarized_paths.add(current)

    for summary in summaries:
        current_path = str(summary["path"])
        summary_lines = [f"File: {current_path}", "Summary:"]
        for symbol in summary.get("symbols", [])[: int(payload.get("max_symbols_per_file", 6))]:
            summary_lines.append(f"- {symbol['kind']} {symbol['name']} @ line {symbol['line']}")
        file_match = file_matches_by_path.get(current_path, {})
        parts.append({
            "kind": "summary",
            "path": current_path,
            "text": "\n".join(summary_lines),
            "provenance": {
                "path": current_path,
                "score": int(file_match.get("score", 0)),
                "graph_score": file_match.get("graph_score"),
                "reasons": list(file_match.get("reasons", [])),
            },
        })
        for source in sources_by_file.get(current_path, [])[:2]:
            file_match = file_matches_by_path.get(current_path, {})
            symbol_name = str(source["name"])
            parts.append({
                "kind": "source",
                "path": current_path,
                "symbol": symbol_name,
                "provenance": {
                    "path": current_path,
                    "symbol": symbol_name,
                    "score": int(file_match.get("score", 0)),
                    "graph_score": file_match.get("graph_score"),
                    "reasons": list(file_match.get("reasons", [])),
                    "symbol_score": symbol_scores_by_key.get((current_path, symbol_name), 0),
                },
                "text": (
                    "Source:\n```text\n"
                    f"{str(source.get('rendered_source', source['source'])).rstrip()}\n```"
                ),
            })
    return parts


def _apply_source_output_budget(
    sources: list[dict[str, Any]],
    *,
    max_tokens: int | None,
    max_render_chars: int | None,
    _profiling_collector: _self._ProfileCollector | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    normalized_max_tokens = max_tokens if max_tokens is not None and max_tokens > 0 else None
    normalized_max_chars = (
        max_render_chars if max_render_chars is not None and max_render_chars > 0 else None
    )
    if normalized_max_tokens is None and normalized_max_chars is None:
        return sources, None, []

    budgeted_sources: list[dict[str, Any]] = []
    omitted_sections: list[dict[str, Any]] = []
    remaining_tokens = normalized_max_tokens
    remaining_chars = normalized_max_chars
    original_token_total = 0
    emitted_token_total = 0
    original_char_total = 0
    emitted_char_total = 0
    truncated_sources = 0
    omitted_sources = 0
    omitted_line_count = 0

    for source in sources:
        rendered_source = str(source.get("rendered_source", source.get("source", "")))
        original_tokens = _self._estimate_tokens(
            rendered_source,
            _profiling_collector=_profiling_collector,
        )
        original_token_total += original_tokens
        original_char_total += len(rendered_source)
        original_line_count = len(rendered_source.splitlines())
        if (remaining_tokens is not None and remaining_tokens <= 0) or (
            remaining_chars is not None and remaining_chars <= 0
        ):
            omitted_sources += 1
            omitted_line_count += original_line_count
            omitted_sections.append({
                "kind": "source_payload",
                "file": str(source.get("file", "")),
                "symbol": source.get("name"),
                "score": 0,
                "reason": "source_budget_exhausted",
                "omitted_line_count": original_line_count,
                "token_estimate": original_tokens,
            })
            continue

        truncated_source, selected_lines, truncated = _self._truncate_source_text_to_budget(
            rendered_source,
            max_tokens=remaining_tokens,
            max_chars=remaining_chars,
            _profiling_collector=_profiling_collector,
        )
        emitted_tokens = _self._estimate_tokens(
            truncated_source,
            _profiling_collector=_profiling_collector,
        )
        emitted_token_total += emitted_tokens
        emitted_char_total += len(truncated_source)
        if remaining_tokens is not None:
            remaining_tokens = max(0, remaining_tokens - emitted_tokens)
        if remaining_chars is not None:
            remaining_chars = max(0, remaining_chars - len(truncated_source))

        budgeted = dict(source)
        budgeted["rendered_source"] = truncated_source
        if "source" in budgeted:
            budgeted["source"] = truncated_source
        if truncated:
            truncated_sources += 1
            omitted_lines = max(0, original_line_count - len(selected_lines))
            omitted_line_count += omitted_lines
            budgeted["line_map"] = _self._line_map_for_budgeted_lines(
                _self._list_of_dicts(source.get("line_map")),
                selected_lines,
            )
            diagnostics = dict(budgeted.get("render_diagnostics", {}))
            diagnostics["budget_removed_line_count"] = omitted_lines
            diagnostics["rendered_line_count"] = len(selected_lines)
            budgeted["render_diagnostics"] = diagnostics
            omitted_sections.append({
                "kind": "source_payload",
                "file": str(source.get("file", "")),
                "symbol": source.get("name"),
                "score": 0,
                "reason": "source_budget",
                "omitted_line_count": omitted_lines,
                "token_estimate": original_tokens,
                "emitted_token_estimate": emitted_tokens,
            })
        budgeted["source_budget"] = {
            "max_tokens": normalized_max_tokens,
            "max_render_chars": normalized_max_chars,
            "original_token_estimate": original_tokens,
            "emitted_token_estimate": emitted_tokens,
            "original_char_count": len(rendered_source),
            "emitted_char_count": len(truncated_source),
            "truncated": truncated,
        }
        budgeted_sources.append(budgeted)

    summary = {
        "max_tokens": normalized_max_tokens,
        "max_render_chars": normalized_max_chars,
        "original_token_estimate": original_token_total,
        "emitted_token_estimate": emitted_token_total,
        "original_char_count": original_char_total,
        "emitted_char_count": emitted_char_total,
        "truncated_sources": truncated_sources,
        "omitted_sources": omitted_sources,
        "omitted_line_count": omitted_line_count,
        "possibly_truncated": bool(truncated_sources or omitted_sources),
    }
    return budgeted_sources, summary, omitted_sections


def _render_part_score(part: dict[str, Any]) -> int:
    provenance = part.get("provenance", {})
    if not isinstance(provenance, dict):
        return 0
    if "score" in provenance:
        return int(provenance.get("score", 0))
    matches = provenance.get("matches", [])
    if not isinstance(matches, list):
        return 0
    return max(
        (int(match.get("score", 0)) for match in matches if isinstance(match, dict)),
        default=0,
    )


def _render_part_path(part: dict[str, Any]) -> str | None:
    current_path = part.get("path")
    if current_path:
        return str(current_path)
    paths = part.get("paths", [])
    if isinstance(paths, list) and paths:
        return str(paths[0])
    return None


def _render_part_sort_key(
    part: dict[str, Any],
    *,
    primary_file: str | None,
    original_index: int,
) -> tuple[int, int, int, str, str, int]:
    kind = str(part.get("kind", ""))
    path = _render_part_path(part) or ""
    is_primary = primary_file is not None and path == primary_file
    kind_priority = (
        {
            "source": 0,
            "summary": 1,
            "tests": 2,
        }
        if is_primary
        else {
            "summary": 0,
            "source": 1,
            "tests": 2,
        }
    ).get(kind, 3)
    return (
        0 if is_primary else 1,
        -_render_part_score(part),
        kind_priority,
        path,
        str(part.get("symbol", "")),
        original_index,
    )


def _render_source_block(
    source: dict[str, Any],
    *,
    render_profile: str,
    optimize_context: bool,
    _profiling_collector: _self._ProfileCollector | None = None,
) -> dict[str, Any]:
    with _self._profiling_phase(_profiling_collector, "source_rendering"):
        block = str(source.get("source", ""))
        path = Path(str(source["file"]))
        normalized_profile = _self._normalize_render_profile(render_profile, optimize_context)
        diagnostics = {
            "original_line_count": 0,
            "rendered_line_count": 0,
            "removed_line_count": 0,
            "removed_comment_lines": 0,
            "removed_blank_lines": 0,
            "removed_docstring_lines": 0,
            "removed_boilerplate_lines": 0,
            "js_jsdoc_removed": 0,
            "ts_type_imports_removed": 0,
            "rust_doc_comments_removed": 0,
            "rust_attributes_removed": 0,
        }
        line_map: list[dict[str, int]] = []

        original_lines = block.splitlines()
        diagnostics["original_line_count"] = len(original_lines)
        if normalized_profile == "full":
            rendered_source = block
            if original_lines:
                line_map.append({
                    "rendered_start_line": 1,
                    "rendered_end_line": len(original_lines),
                    "original_start_line": int(source["start_line"]),
                    "original_end_line": int(source["end_line"]),
                })
            diagnostics["rendered_line_count"] = len(original_lines)
        else:
            kept_lines: list[str] = []
            current_segment: dict[str, int] | None = None
            rendered_line_number = 1
            original_start = int(source["start_line"])
            omitted_docstring_lines: set[int] = set()
            omitted_boilerplate_lines: set[int] = set()
            omitted_jsdoc_lines: set[int] = set()
            omitted_ts_type_import_lines: set[int] = set()
            omitted_rust_doc_comment_lines: set[int] = set()
            omitted_rust_attribute_lines: set[int] = set()
            if path.suffix == ".py":
                omitted_docstring_lines, omitted_boilerplate_lines = (
                    _python_ast_omitted_relative_lines(
                        block, normalized_profile, strip_docstrings=optimize_context
                    )
                )
            elif path.suffix in _self._TS_SUFFIXES:
                omitted_jsdoc_lines, omitted_ts_type_import_lines = (
                    _self._ts_ast_omitted_relative_lines(block)
                )
            elif path.suffix in _self._JS_TS_SUFFIXES:
                omitted_jsdoc_lines = _js_ast_omitted_relative_lines(block)
            elif path.suffix in _self._RUST_SUFFIXES:
                omitted_rust_doc_comment_lines, omitted_rust_attribute_lines = (
                    _rust_ast_omitted_relative_lines(block)
                )
            for index, line in enumerate(original_lines):
                original_line_number = original_start + index
                relative_line_number = index + 1
                if not line.strip():
                    diagnostics["removed_blank_lines"] += 1
                    continue
                if relative_line_number in omitted_jsdoc_lines:
                    diagnostics["removed_comment_lines"] += 1
                    diagnostics["js_jsdoc_removed"] += 1
                    continue
                if relative_line_number in omitted_ts_type_import_lines:
                    diagnostics["ts_type_imports_removed"] += 1
                    continue
                if relative_line_number in omitted_rust_doc_comment_lines:
                    diagnostics["removed_comment_lines"] += 1
                    diagnostics["rust_doc_comments_removed"] += 1
                    continue
                if relative_line_number in omitted_rust_attribute_lines:
                    diagnostics["removed_boilerplate_lines"] += 1
                    diagnostics["rust_attributes_removed"] += 1
                    continue
                if _self._is_comment_line(path, line):
                    diagnostics["removed_comment_lines"] += 1
                    continue
                if relative_line_number in omitted_docstring_lines:
                    diagnostics["removed_docstring_lines"] += 1
                    continue
                if relative_line_number in omitted_boilerplate_lines:
                    diagnostics["removed_boilerplate_lines"] += 1
                    continue

                kept_lines.append(line)
                if (
                    current_segment is None
                    or original_line_number != current_segment["original_end_line"] + 1
                ):
                    current_segment = {
                        "rendered_start_line": rendered_line_number,
                        "rendered_end_line": rendered_line_number,
                        "original_start_line": original_line_number,
                        "original_end_line": original_line_number,
                    }
                    line_map.append(current_segment)
                else:
                    current_segment["rendered_end_line"] = rendered_line_number
                    current_segment["original_end_line"] = original_line_number
                rendered_line_number += 1

            rendered_source = "\n".join(kept_lines)
            if kept_lines and block.endswith("\n"):
                rendered_source += "\n"
            diagnostics["rendered_line_count"] = len(kept_lines)
            diagnostics["removed_line_count"] = (
                diagnostics["original_line_count"] - diagnostics["rendered_line_count"]
            )

    rendered = dict(source)
    rendered["render_profile"] = normalized_profile
    rendered["optimize_context"] = optimize_context
    rendered["rendered_source"] = rendered_source
    rendered["line_map"] = line_map
    rendered["render_diagnostics"] = diagnostics
    return rendered


def _apply_symbol_field_output_limit(
    payload: dict[str, Any],
    *,
    field_name: str,
    max_count: int | None,
) -> dict[str, Any]:
    """Cap ``payload[field_name]`` (a flat list) to ``max_count`` entries, stamping ``output_limit``.

    Generalizes ``_apply_blast_radius_output_limits``'s tests-cap + ``output_limit`` stamping
    (design #96 item 2) to any flat-list field -- giving defs/refs/callers/impact a DEDICATED
    ``--max-tests`` instead of blast-radius's conflated ``--max-files``, and leaving the helper
    ``field_name``-generic so a follow-up can reuse it for ``import_graph_consumers``.

    Deliberately field-NAME-scoped output_limit keys (``{field_name}_truncated``, e.g.
    ``tests_truncated`` -- never blast-radius's own ``callers_truncated``/``files_truncated``
    names, which ``main._scan_truncation_warning`` DOES recognize as a SCAN truncation). An
    output cap here is a COMPLETE analysis capped for display and must stay exit-0 (design #96
    contract-safety section; see ``main._scan_incomplete``'s docstring for the scan-vs-output-cap
    split this deliberately avoids colliding with).

    ``max_count=None`` is a no-op: the field and ``output_limit`` are left untouched, so an
    uncapped library/MCP caller sees byte-identical output to before this cap existed (mirrors
    ``_apply_context_token_budget``'s ``None``-is-unbounded contract).
    """
    if max_count is None:
        return payload
    normalized_max = max(0, int(max_count))
    original = list(payload.get(field_name) or [])
    capped_list = original[:normalized_max]
    payload[field_name] = capped_list
    output_limit = dict(payload.get("output_limit") or {})
    output_limit[f"max_{field_name}"] = normalized_max
    output_limit[f"{field_name}_truncated"] = len(capped_list) < len(original)
    output_limit[f"total_{field_name}"] = len(original)
    output_limit[f"returned_{field_name}"] = len(capped_list)
    output_limit[f"omitted_{field_name}"] = max(0, len(original) - len(capped_list))
    payload["output_limit"] = output_limit
    return payload


def _apply_blast_radius_output_limits(
    payload: dict[str, Any],
    *,
    max_callers: int | None = None,
    max_files: int | None = None,
) -> dict[str, Any]:
    normalized_max_callers = max(1, int(max_callers)) if max_callers is not None else None
    normalized_max_files = max(1, int(max_files)) if max_files is not None else None
    if normalized_max_callers is None and normalized_max_files is None:
        return payload

    limited = dict(payload)
    original_callers = _self._list_of_dicts(payload.get("callers"))
    original_files = _self._list_of_strings(payload.get("files"))
    original_import_consumers = _self._list_of_dicts(payload.get("import_graph_consumers"))

    if normalized_max_callers is not None:
        limited["callers"] = original_callers[:normalized_max_callers]
        limited["caller_tree"] = _self._list_of_dicts(payload.get("caller_tree"))[
            :normalized_max_callers
        ]

    if normalized_max_files is not None:
        selected_files = original_files[:normalized_max_files]
        selected_file_set = set(selected_files)
        limited["files"] = selected_files
        limited["affected_files"] = list(selected_files)
        limited["file_matches"] = [
            current
            for current in _self._list_of_dicts(payload.get("file_matches"))
            if str(current.get("path")) in selected_file_set
        ][:normalized_max_files]
        limited["file_summaries"] = [
            {
                **current,
                "symbols": [
                    compact_symbol
                    for compact_symbol in (
                        _self._compact_symbol_record(symbol)
                        for symbol in _self._list_of_dicts(current.get("symbols"))
                    )
                    if compact_symbol is not None
                ][: _self._BLAST_RADIUS_LIMITED_SYMBOLS_PER_FILE],
            }
            for current in _self._list_of_dicts(payload.get("file_summaries"))
            if str(current.get("path")) in selected_file_set
        ][:normalized_max_files]
        limited["tests"] = _self._list_of_strings(payload.get("tests"))[:normalized_max_files]
        selected_test_set = set(limited["tests"])
        limited["test_matches"] = [
            current
            for current in _self._list_of_dicts(payload.get("test_matches"))
            if str(current.get("path")) in selected_test_set
        ][:normalized_max_files]
        limited["related_paths"] = _self._list_of_strings(payload.get("related_paths"))[
            :normalized_max_files
        ]
        limited["symbols"] = [
            current
            for current in _self._list_of_dicts(payload.get("symbols"))
            if str(current.get("file")) in selected_file_set
        ]
        limited["imports"] = [
            current
            for current in _self._list_of_dicts(payload.get("imports"))
            if str(current.get("file")) in selected_file_set
        ]
        limited["import_graph_consumers"] = [
            current
            for current in original_import_consumers
            if str(current.get("file")) in selected_file_set
        ]
        limited["import_graph_consumer_files"] = sorted(
            dict.fromkeys(str(current["file"]) for current in limited["import_graph_consumers"])
        )
        limited["import_graph_consumer_count"] = len(limited["import_graph_consumers"])
        limited_caller_tree: list[dict[str, Any]] = []
        for current in _self._list_of_dicts(limited.get("caller_tree", payload.get("caller_tree"))):
            depth_files = [
                path
                for path in _self._list_of_strings(current.get("files"))
                if path in selected_file_set
            ][:normalized_max_files]
            if not depth_files:
                continue
            compact_level = dict(current)
            compact_level["files"] = depth_files
            limited_caller_tree.append(compact_level)
        limited["caller_tree"] = limited_caller_tree
        rendered_lines = [f"Blast radius for {payload.get('symbol', '')}:"]
        for current in limited_caller_tree:
            rendered_lines.append(f"Depth {current.get('depth')}:")
            rendered_lines.extend(
                f"- {path}" for path in _self._list_of_strings(current.get("files"))
            )
        limited["rendered_caller_tree"] = "\n".join(rendered_lines)
    elif "files" in limited:
        limited["affected_files"] = _self._list_of_strings(limited.get("files"))

    returned_import_consumers = _self._list_of_dicts(
        limited.get("import_graph_consumers", original_import_consumers)
    )
    limited["output_limit"] = {
        "max_callers": normalized_max_callers,
        "max_files": normalized_max_files,
        "callers_truncated": (
            normalized_max_callers is not None and len(original_callers) > normalized_max_callers
        ),
        "files_truncated": (
            normalized_max_files is not None and len(original_files) > normalized_max_files
        ),
        "import_consumers_truncated": (
            normalized_max_files is not None
            and len(returned_import_consumers) < len(original_import_consumers)
        ),
        "total_callers": len(original_callers),
        "returned_callers": len(_self._list_of_dicts(limited.get("callers"))),
        "omitted_callers": max(
            0, len(original_callers) - len(_self._list_of_dicts(limited.get("callers")))
        ),
        "total_files": len(original_files),
        "returned_files": len(_self._list_of_strings(limited.get("files"))),
        "omitted_files": max(
            0, len(original_files) - len(_self._list_of_strings(limited.get("files")))
        ),
        "total_import_consumers": len(original_import_consumers),
        "returned_import_consumers": len(returned_import_consumers),
        "omitted_import_consumers": max(
            0, len(original_import_consumers) - len(returned_import_consumers)
        ),
    }
    return limited
