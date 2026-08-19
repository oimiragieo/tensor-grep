from __future__ import annotations

import os
from typing import Any

from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_OUTBOUND_DEPENDENCIES_ENV as _CAPSULE_OUTBOUND_DEPENDENCIES_ENV,
)
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_OUTBOUND_DEPENDENCY_CALL_TOKEN_RE as _CAPSULE_OUTBOUND_DEPENDENCY_CALL_TOKEN_RE,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_dict as _as_dict,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_list_of_dicts as _as_list_of_dicts,
)


def _capsule_outbound_dependencies_enabled() -> bool:
    """Opt-IN flag (default OFF) -- same polarity as `_capsule_lsp_confidence_boost_enabled` and the
    other retrieval-quality features (channelized RRF, cAST chunker): DAR ships default-off pending a
    measured golden-set win, so the capsule stays byte-identical for every user until they opt in via
    `TG_CAPSULE_OUTBOUND_DEPS` in `{"1", "true", "yes", "on"}` (case-insensitive).
    """
    raw = os.environ.get(_CAPSULE_OUTBOUND_DEPENDENCIES_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _outbound_dependency_import_tails(imports: list[str]) -> dict[str, str]:
    """tail (last dotted segment) -> the qualified import string it came from.

    Only DOTTED import strings corroborate a candidate: `from src.tax import compute_tax`
    produces "src.tax.compute_tax" (tail "compute_tax") in
    `repo_map._imports_and_symbols_for_path`'s output. A bare top-level `import module` entry
    (no dot) is deliberately excluded here -- "drop bare third-party/stdlib module strings": a
    bare module name alone (e.g. `import requests`) is too weak a signal and would otherwise let
    an unrelated third-party package corroborate a same-named local call token.
    """
    tails: dict[str, str] = {}
    for raw in imports:
        text = str(raw)
        if "." not in text:
            continue
        tail = text.rsplit(".", 1)[-1]
        if tail and tail not in tails:
            tails[tail] = text
    return tails


def _outbound_dependency_selected_symbol_locations(
    payload: dict[str, Any],
    *,
    exclude_file: str,
) -> dict[str, dict[str, Any]]:
    """symbol name -> {file, line, kind, provenance} for symbols defined in OTHER selected files.

    `file_summaries` and `candidate_edit_targets.symbols` are the two survivors of compact
    rendering (see module-level DAR comment above) and both are already scoped to files the
    ranking SELECTED -- exactly the corroboration DAR needs: a call token only counts as an
    outbound dependency when it resolves inside a file the agent is already looking at, never an
    arbitrary whole-repo symbol.
    """
    locations: dict[str, dict[str, Any]] = {}
    for summary in _as_list_of_dicts(payload.get("file_summaries")):
        file_path = str(summary.get("path") or "")
        if not file_path or file_path == exclude_file:
            continue
        for symbol in _as_list_of_dicts(summary.get("symbols")):
            name = str(symbol.get("name") or "")
            if not name or name in locations:
                continue
            raw_line = symbol.get("line") or 1
            try:
                line = max(1, int(str(raw_line)))
            except (TypeError, ValueError):
                line = 1
            locations[name] = {
                "file": file_path,
                "line": line,
                "kind": str(symbol.get("kind") or "unknown"),
                "provenance": "parser-backed",
            }

    candidate_targets = _as_dict(payload.get("candidate_edit_targets"))
    for symbol in _as_list_of_dicts(candidate_targets.get("symbols")):
        name = str(symbol.get("name") or "")
        file_path = str(symbol.get("file") or "")
        if not name or not file_path or file_path == exclude_file or name in locations:
            continue
        raw_line = symbol.get("line") or symbol.get("start_line") or 1
        try:
            line = max(1, int(str(raw_line)))
        except (TypeError, ValueError):
            line = 1
        locations[name] = {
            "file": file_path,
            "line": line,
            "kind": str(symbol.get("kind") or "unknown"),
            "provenance": str(symbol.get("provenance") or "parser-backed"),
        }
    return locations


def _outbound_dependency_call_tokens(source: str, start_line: int) -> list[tuple[str, int]]:
    """First-use `(name, line)` pairs for `name(` call-shaped tokens in `source`, source order.

    First occurrence per NAME wins -- feeds the "tie-break first-use line" selection rule.
    """
    seen: set[str] = set()
    tokens: list[tuple[str, int]] = []
    for offset, line_text in enumerate(source.splitlines()):
        for match in _CAPSULE_OUTBOUND_DEPENDENCY_CALL_TOKEN_RE.finditer(line_text):
            name = match.group(1)
            if name in seen:
                continue
            seen.add(name)
            tokens.append((name, start_line + offset))
    return tokens


def _outbound_dependency_line_preview(source: str, start_line: int, line: int) -> str:
    lines = source.splitlines()
    index = line - start_line
    if 0 <= index < len(lines):
        return lines[index].strip()
    return ""
