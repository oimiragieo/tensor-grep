"""Opt-in, parser-bounded excerpts; elision markers are never source coordinates."""

from __future__ import annotations

import re
import textwrap
from typing import Any

from tensor_grep.core.retrieval_chunker import _structural_parser_for_path

_MAX_SOURCE_CHARS = 1_000_000
_MAX_QUERY_CHARS = 4096
_MAX_NODES = 4096
_DECLARATIONS = {
    "function_definition",
    "class_definition",
    "function_declaration",
    "class_declaration",
    "method_definition",
    "method_declaration",
    "generator_function_declaration",
    "function_item",
    "impl_item",
    "trait_item",
    "arrow_function",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z_][a-z_0-9]*", text.lower()))


def _body(root: Any) -> Any | None:
    """Find the outer declaration body, without descending into sibling bodies."""
    pending = [root]
    visited = 0
    while pending and visited < _MAX_NODES:
        node = pending.pop()
        visited += 1
        body = node.child_by_field_name("body") if node.type in _DECLARATIONS else None
        if body is not None:
            return body
        if len(pending) + node.named_child_count > _MAX_NODES:
            return None
        pending.extend(reversed(node.named_children))
    return None


def _span(node: Any, line_count: int) -> range:
    end = node.end_point[0] + (node.end_point[1] > 0)
    return range(max(0, node.start_point[0]), min(line_count, end))


def focus_source(source: dict[str, Any], query: str | None) -> dict[str, Any]:
    """Keep matching complete body statements and surrounding declaration text.

    This is lexical selection using AST boundaries, not data-flow analysis. Unsupported
    languages, incomplete parses and non-local queries retain the entire source block.
    No files are read: the caller's already selected snapshot is the only source of text.
    """
    result = dict(source)
    block = str(source.get("source", ""))
    focus: dict[str, Any] = {
        "selection": "lexical_terms_with_ast_statement_boundaries",
        "omitted_line_count": 0,
        "omitted_ranges": [],
        "fallback_reason": None,
    }
    result["focus"] = focus

    def fallback(reason: str) -> dict[str, Any]:
        focus["fallback_reason"] = reason
        return result

    if (
        len(block) > _MAX_SOURCE_CHARS
        or block.count("\n") > 10_000
        or len(query or "") > _MAX_QUERY_CHARS
    ):
        return fallback("focus_input_limit")
    lines = block.splitlines()
    if not query or not (terms := _tokens(query)):
        return fallback("no_query_terms")
    if len(terms) > 64:
        return fallback("focus_input_limit")
    try:
        parser = _structural_parser_for_path(str(source.get("file", "")))
        if parser is None:
            return fallback("grammar_unavailable")
        # The grammar provider caches parsers. Give this render its own parser so
        # concurrent MCP requests never mutate a shared parser's state.
        tree = type(parser)(parser.language).parse(textwrap.dedent(block).encode("utf-8"))
    except (ImportError, ValueError, RuntimeError, UnicodeError, TypeError, AttributeError):
        return fallback("parse_unavailable")
    if tree.root_node.has_error:
        return fallback("incomplete_parse")
    body = _body(tree.root_node)
    if body is None:
        return fallback("no_declaration_body")
    children = body.named_children
    if len(children) == 1 and children[0].type == "statement_list":
        children = children[0].named_children
    if not children or len(children) > _MAX_NODES:
        return fallback("no_bounded_body")
    matched: set[int] = set()
    protected: set[int] = set()
    body_lines: set[int] = set()
    first_statement = True
    for child in children:
        span = set(_span(child, len(lines)))
        body_lines.update(span)
        # Leading Python docstrings and comments (including JSDoc/Rust docs) survive.
        is_doc = child.type.endswith("comment") or (
            first_statement
            and child.type == "expression_statement"
            and any(node.type in {"string", "concatenated_string"} for node in child.named_children)
        )
        if is_doc:
            protected.update(span)
        elif terms & _tokens("\n".join(lines[i] for i in sorted(span))):
            matched.update(span)
        if not child.type.endswith("comment"):
            first_statement = False
    if not matched:
        return fallback("no_body_match")
    # Only elide whole sibling statements. Signatures, decorators, surrounding text,
    # closing braces and all source lines outside the declaration body are retained.
    kept = (set(range(len(lines))) - body_lines) | matched | protected
    # A signature or closing brace can share a line with a statement.
    kept.update(range(0, min(len(lines), body.start_point[0] + (body.start_point[1] == 0))))
    if not str(source.get("file", "")).lower().endswith(".py"):
        kept.add(body.start_point[0])
        kept.add(min(len(lines) - 1, body.end_point[0]))
    omitted = set(range(len(lines))) - kept
    if not omitted:
        return fallback("all_body_statements_match")
    start = int(source["start_line"])
    rendered: list[str] = []
    line_map: list[dict[str, int]] = []
    ranges: list[dict[str, int]] = []
    position = 0
    while position < len(lines):
        if position in omitted:
            end = position + 1
            while end < len(lines) and end in omitted:
                end += 1
            ranges.append({"start_line": start + position, "end_line": start + end - 1})
            rendered.append(
                f"... [{end - position} lines elided; source {start + position}-{start + end - 1}] ..."
            )
            position = end
            continue
        rendered.append(lines[position])
        rendered_line = len(rendered)
        original_line = start + position
        if (
            line_map
            and line_map[-1]["rendered_end_line"] == rendered_line - 1
            and line_map[-1]["original_end_line"] == original_line - 1
        ):
            line_map[-1]["rendered_end_line"] = rendered_line
            line_map[-1]["original_end_line"] = original_line
        else:
            line_map.append({
                "rendered_start_line": rendered_line,
                "rendered_end_line": rendered_line,
                "original_start_line": original_line,
                "original_end_line": original_line,
            })
        position += 1
    text = "\n".join(rendered) + ("\n" if block.endswith("\n") else "")
    if len(text) >= len(block):
        return fallback("no_size_reduction")
    result.update(source=text, rendered_source=text, line_map=line_map)
    focus.update(omitted_line_count=len(omitted), omitted_ranges=ranges)
    focus["full_source_read"] = [
        "tg",
        "source",
        str(source["file"]),
        str(source.get("name", "")),
        "--json",
    ]
    diagnostics = dict(result.get("render_diagnostics", {}))
    diagnostics.update(removed_line_count=len(omitted), rendered_line_count=len(rendered))
    result["render_diagnostics"] = diagnostics
    return result


def focus_sources(sources: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    return [focus_source(source, query) for source in sources]


def primary_omission_reason(payload: dict[str, Any], primary_file: str) -> str:
    if any(
        source.get("file") == primary_file and source.get("focus", {}).get("omitted_line_count", 0)
        for source in payload.get("sources", [])
    ):
        return "primary_symbol_elided_by_focused_profile"
    return "primary_symbol_truncated_by_source_budget"


def omissions(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "source_payload",
            "file": source.get("file"),
            "symbol": source.get("name"),
            "reason": "focused_profile",
            "omitted_line_count": source["focus"]["omitted_line_count"],
            "omitted_ranges": source["focus"]["omitted_ranges"],
        }
        for source in sources
        if source.get("focus", {}).get("omitted_line_count", 0)
    ]
