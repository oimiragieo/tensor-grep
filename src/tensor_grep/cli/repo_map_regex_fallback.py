"""The regex fallback extractor: what `repo_map` uses when no tree-sitter grammar is available.

`_regex_imports_and_symbols`, `_regex_references_and_calls` and `_regex_symbol_sources` are the
grammar-free tier of the symbol graph -- pattern-matched definitions, imports and call sites for
files whose language has no registered parser. Split out of `repo_map.py` under
docs/design/2026-08-19-split-floor-escape.md.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.cli.repo_map_lang_js import _js_ts_dynamic_import_hit as _js_ts_dynamic_import_hit

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


def _regex_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if path.suffix not in _self._JS_TS_SUFFIXES | _self._RUST_SUFFIXES:
        return [], []

    try:
        lines = _self._read_source_text_cached(str(path)).splitlines()
    except (OSError, UnicodeDecodeError):
        return [], []

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    for line_number, line in enumerate(lines, start=1):
        if path.suffix in _self._JS_TS_SUFFIXES:
            import_match = re.match(r'^\s*import\s+.*?from\s+["\']([^"\']+)["\']', line)
            export_from_match = re.match(r'^\s*export\s+.*?from\s+["\']([^"\']+)["\']', line)
            require_match = re.match(
                r"^\s*(?:const|let|var)\s+(?:\{[^}]+\}|[A-Za-z_][A-Za-z0-9_]*)"
                r'\s*=\s*require\(["\']([^"\']+)["\']\)',
                line,
            )
            class_match = re.match(
                r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            function_match = re.match(
                r"^\s*(?:export\s+)?(?:default\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            variable_function_match = re.match(
                r"^\s*(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)",
                line,
            )
            commonjs_export_function_match = re.match(
                r"^\s*(?:module\.)?exports\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)",
                line,
            )
            object_export_function_match = re.match(
                r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"
                r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)",
                line,
            )
            if import_match:
                imports.append(import_match.group(1))
            if export_from_match:
                imports.append(export_from_match.group(1))
            if require_match:
                imports.append(require_match.group(1))
            else:
                # #93 SUB-1: `import("x")` call-form and a require(...) not shaped like the
                # assignment-anchored regex above. Only the statically-resolvable literal is
                # useful to this alias-graph prefilter list -- an unresolved (non-literal) hit
                # has no name to add.
                dynamic_hit = _js_ts_dynamic_import_hit(line)
                if dynamic_hit is not None and dynamic_hit[0]:
                    imports.append(dynamic_hit[0])
            if class_match:
                end_line, _ = _self._extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _self._symbol_record(
                        name=class_match.group(1),
                        kind="class",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if function_match:
                end_line, _ = _self._extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _self._symbol_record(
                        name=function_match.group(1),
                        kind="function",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            for current_match in (
                variable_function_match,
                commonjs_export_function_match,
                object_export_function_match,
            ):
                if current_match is None:
                    continue
                end_line, _ = _self._extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _self._symbol_record(
                        name=current_match.group(1),
                        kind="function",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
        elif path.suffix in _self._RUST_SUFFIXES:
            use_match = re.match(r"^\s*use\s+([^;]+);", line)
            fn_match = re.match(
                r"^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            struct_match = re.match(
                r"^\s*(?:pub\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            enum_match = re.match(
                r"^\s*(?:pub\s+)?enum\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            trait_match = re.match(
                r"^\s*(?:pub\s+)?trait\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            if use_match:
                imports.append(use_match.group(1).strip())
            if fn_match:
                end_line, _ = _self._extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _self._symbol_record(
                        name=fn_match.group(1),
                        kind="function",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if struct_match:
                end_line, _ = _self._extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _self._symbol_record(
                        name=struct_match.group(1),
                        kind="struct",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if enum_match:
                end_line, _ = _self._extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _self._symbol_record(
                        name=enum_match.group(1),
                        kind="enum",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if trait_match:
                end_line, _ = _self._extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _self._symbol_record(
                        name=trait_match.group(1),
                        kind="trait",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )

    imports = sorted(dict.fromkeys(imports))
    return imports, _self._dedupe_symbol_records(symbols)


def _regex_references_and_calls(
    path: Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix not in _self._JS_TS_SUFFIXES | _self._RUST_SUFFIXES:
        return [], []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return [], []

    symbol_pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    call_pattern = re.compile(rf"(?:\b|\.|::){re.escape(symbol)}\s*\(")

    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def _strip_line_string_and_comment_noise(line: str, *, supports_template_strings: bool) -> str:
        cleaned: list[str] = []
        in_single = False
        in_double = False
        in_template = False
        escaped = False

        for index, char in enumerate(line):
            next_char = line[index + 1] if index + 1 < len(line) else ""
            if in_single:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "'":
                    in_single = False
                cleaned.append(" ")
                continue
            if in_double:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_double = False
                cleaned.append(" ")
                continue
            if in_template:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "`":
                    in_template = False
                cleaned.append(" ")
                continue
            if char == "/" and next_char == "/":
                break
            if char == "'":
                in_single = True
                cleaned.append(" ")
                continue
            if char == '"':
                in_double = True
                cleaned.append(" ")
                continue
            if supports_template_strings and char == "`":
                in_template = True
                cleaned.append(" ")
                continue
            cleaned.append(char)
        return "".join(cleaned)

    for line_number, line in enumerate(lines, start=1):
        if symbol_pattern.search(line):
            references.append({
                "name": symbol,
                "kind": "reference",
                "file": str(path),
                "line": line_number,
                "text": line,
            })
        supports_template_strings = path.suffix in _self._JS_TS_SUFFIXES
        sanitized_line = _strip_line_string_and_comment_noise(
            line, supports_template_strings=supports_template_strings
        )
        # Task 326: keep the historical "at most one call row per line" shape (downstream dedupe
        # keys on file+line), but require at least one occurrence on the line that is NOT a
        # declaration before emitting it.
        if any(
            _self._DEFINITION_KEYWORD_BEFORE_SYMBOL.search(sanitized_line[: call_match.start()])
            is None
            for call_match in call_pattern.finditer(sanitized_line)
        ):
            calls.append({
                "name": symbol,
                "kind": "call",
                "file": str(path),
                "line": line_number,
                "text": line,
            })

    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _regex_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix not in _self._JS_TS_SUFFIXES | _self._RUST_SUFFIXES:
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    if path.suffix in _self._JS_TS_SUFFIXES:
        escaped_symbol = re.escape(symbol)
        patterns = [
            (
                "class",
                re.compile(rf"^\s*(?:export\s+)?class\s+({escaped_symbol})\b"),
            ),
            (
                "function",
                re.compile(rf"^\s*(?:export\s+)?(?:default\s+)?function\s+({escaped_symbol})\b"),
            ),
            (
                "function",
                re.compile(
                    rf"^\s*(?:const|let|var)\s+({escaped_symbol})\s*=\s*"
                    r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)"
                ),
            ),
            (
                "function",
                re.compile(
                    rf"^\s*(?:module\.)?exports\.({escaped_symbol})\s*=\s*"
                    r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)"
                ),
            ),
            (
                "function",
                re.compile(
                    rf"^\s*({escaped_symbol})\s*:\s*"
                    r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_][A-Za-z0-9_]*\s*=>)"
                ),
            ),
        ]
    else:
        patterns = [
            (
                "function",
                re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+({re.escape(symbol)})\b"),
            ),
            (
                "struct",
                re.compile(rf"^\s*(?:pub\s+)?struct\s+({re.escape(symbol)})\b"),
            ),
            (
                "enum",
                re.compile(rf"^\s*(?:pub\s+)?enum\s+({re.escape(symbol)})\b"),
            ),
            (
                "trait",
                re.compile(rf"^\s*(?:pub\s+)?trait\s+({re.escape(symbol)})\b"),
            ),
        ]

    sources: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        matched_kind = None
        for kind, pattern in patterns:
            if pattern.match(line):
                matched_kind = kind
                break
        if matched_kind is None:
            continue

        end_line, block = _self._extract_braced_block(lines, line_number - 1)
        sources.append({
            "name": symbol,
            "kind": matched_kind,
            "file": str(path),
            "start_line": line_number,
            "end_line": end_line,
            "source": block,
        })

    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources
