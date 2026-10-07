"""Bounded exact-definition ranking over the existing retrieval snapshot."""

from __future__ import annotations

import ast
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tensor_grep.core.result import SearchResult
from tensor_grep.core.retrieval_chunker import Chunk, _structural_parser_for_path

_MAX_CHUNKS = 100_000
_MAX_FILES = 256
_MAX_FILE_CHARS = 1_000_000
_MAX_TOTAL_CHARS = 8_000_000
_MAX_FILE_LINES = 10_000
_MAX_TOTAL_LINES = 100_000
_MAX_NODES = 20_000
_DECLARATIONS = {
    "function_definition",
    "class_definition",
    "function_declaration",
    "class_declaration",
    "method_definition",
    "method_declaration",
    "generator_function_declaration",
    "function_item",
    "struct_item",
    "enum_item",
    "trait_item",
    "type_spec",
}


def enabled() -> bool:
    return os.environ.get("TG_RRF_SYMBOLS") == "1"


def evidence_options(result: SearchResult) -> dict[str, Any]:
    if not enabled():
        return {}
    result.rank_fusion = {}
    return {"evidence": result.rank_fusion}


def _snapshot(chunks: list[Chunk]) -> tuple[str, str | None]:
    lines: dict[int, str] = {}
    for chunk in chunks:
        if chunk.start_line < 1 or chunk.end_line > _MAX_FILE_LINES:
            return "", "line_limit"
        content = chunk.text.splitlines()
        if len(content) != chunk.end_line - chunk.start_line + 1:
            return "", "inconsistent_snapshot"
        for number, line in enumerate(content, chunk.start_line):
            if number in lines and lines[number] != line:
                return "", "inconsistent_snapshot"
            lines[number] = line
    if not lines or min(lines) != 1 or max(lines) != len(lines):
        return "", "incomplete_snapshot"
    return "\n".join(lines[number] for number in range(1, len(lines) + 1)), None


def _definitions(path: str, text: str) -> tuple[list[tuple[str, int]], str | None]:
    definitions: list[tuple[str, int]] = []
    if Path(path).suffix.lower() == ".py":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return [], "incomplete_parse"
        for count, node in enumerate(ast.walk(tree)):
            if count >= _MAX_NODES:
                return [], "node_limit"
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definitions.append((node.name, node.lineno))
        return definitions, None
    try:
        parser = _structural_parser_for_path(path)
        if parser is None:
            return [], "grammar_unavailable"
        root = type(parser)(parser.language).parse(text.encode("utf-8")).root_node
    except (ImportError, ValueError, RuntimeError, TypeError, AttributeError, UnicodeError):
        return [], "parse_unavailable"
    if root.has_error:
        return [], "incomplete_parse"
    pending = [root]
    count = 0
    while pending:
        node = pending.pop()
        count += 1
        if count + len(pending) + node.named_child_count > _MAX_NODES:
            return [], "node_limit"
        if node.type in _DECLARATIONS:
            name = node.child_by_field_name("name")
            if name is not None:
                definitions.append((name.text.decode("utf-8"), name.start_point[0] + 1))
        pending.extend(reversed(node.named_children))
    return definitions, None


def symbol_ranking(chunks: list[Chunk], query: str) -> tuple[list[int], dict[str, Any]]:
    """Reconstruct overlapping chunks, refusing gaps/conflicts instead of reopening files.

    Only declaration header lines contribute, so comments, strings, calls, and arbitrary
    identifier occurrences cannot masquerade as definition evidence. Unsupported files
    remain eligible in the lexical/dense legs and their missing AST evidence is disclosed.
    """
    evidence: dict[str, Any] = {
        "name": "ast_symbols",
        "evidence": "parser-backed",
        "parsed_files": 0,
        "skipped_files": {},
        "ranked_chunks": 0,
        "matched_symbols": [],
    }
    if len(chunks) > _MAX_CHUNKS or len(query) > 4096:
        evidence["unavailable_reason"] = "symbol_input_limit"
        return [], evidence
    names = set(re.findall(r"[$\w]+", query))
    groups: dict[str, list[int]] = defaultdict(list)
    for index, chunk in enumerate(chunks):
        groups[chunk.file_path].append(index)
    skipped: Counter[str] = Counter()
    scores: Counter[int] = Counter()
    matched_names: set[str] = set()
    total_chars = total_lines = 0
    for file_number, (path, indices) in enumerate(groups.items()):
        if file_number >= _MAX_FILES:
            skipped["file_limit"] += 1
            continue
        file_chunks = [chunks[index] for index in indices]
        size = sum(len(chunk.text) for chunk in file_chunks)
        line_count = sum(max(0, chunk.end_line - chunk.start_line + 1) for chunk in file_chunks)
        if size > _MAX_FILE_CHARS or total_chars + size > _MAX_TOTAL_CHARS:
            skipped["character_limit"] += 1
            continue
        if total_lines + line_count > _MAX_TOTAL_LINES:
            skipped["line_limit"] += 1
            continue
        total_chars += size
        total_lines += line_count
        snapshot, reason = _snapshot(file_chunks)
        if reason:
            skipped[reason] += 1
            continue
        definitions, reason = _definitions(path, snapshot)
        if reason:
            skipped[reason] += 1
            continue
        evidence["parsed_files"] += 1
        matched_lines: Counter[int] = Counter()
        for name, line in definitions:
            if name in names:
                matched_names.add(name)
                matched_lines[line] += 1
        # Walk the bounded chunk lines once instead of definitions x chunks.
        for index in indices:
            score = sum(
                matched_lines[line]
                for line in range(chunks[index].start_line, chunks[index].end_line + 1)
            )
            if score:
                scores[index] = score
    ranking = sorted(scores, key=lambda index: (-scores[index], index))
    evidence.update(
        skipped_files=dict(skipped),
        ranked_chunks=len(ranking),
        matched_symbols=sorted(matched_names),
    )
    return ranking, evidence


def fallback_reason(evidence: dict[str, Any]) -> str | None:
    if evidence.get("unavailable_reason"):
        return f"AST symbol ranking unavailable: {evidence['unavailable_reason']}"
    skipped = evidence.get("skipped_files", {})
    if skipped:
        reasons = ", ".join(f"{reason}={count}" for reason, count in sorted(skipped.items()))
        return f"AST symbol ranking incomplete: {reasons}"
    return None
