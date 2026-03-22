from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

JSON_OUTPUT_VERSION = 1
ROUTING_BACKEND = "RepoMap"
ROUTING_REASON = "repo-map"
_SKIP_DIR_NAMES = {
    ".tensor-grep",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
}
_JS_TS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_RUST_SUFFIXES = {".rs"}


def _envelope(path: Path) -> dict[str, Any]:
    return {
        "version": JSON_OUTPUT_VERSION,
        "routing_backend": ROUTING_BACKEND,
        "routing_reason": ROUTING_REASON,
        "sidecar_used": False,
        "coverage": {
            "language_scope": "python-js-ts-rust",
            "symbol_navigation": "python-ast+parser-js-ts+heuristic-rust",
            "test_matching": "filename+import+graph-heuristic",
        },
        "path": str(path),
    }


def _is_test_file(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".test.js")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.js")
        or "tests" in path.parts
        or "__tests__" in path.parts
    )


def _iter_repo_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root.resolve()]

    files: list[Path] = []
    for current in root.rglob("*"):
        if not current.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in current.parts):
            continue
        files.append(current.resolve())
    return sorted(files)


def _python_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if path.suffix != ".py":
        return [], []

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return [], []

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
                for alias in node.names:
                    imports.append(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.ClassDef):
            symbols.append(
                {
                    "name": node.name,
                    "kind": "class",
                    "file": str(path),
                    "line": node.lineno,
                }
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                {
                    "name": node.name,
                    "kind": "function",
                    "file": str(path),
                    "line": node.lineno,
                }
            )

    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


@lru_cache(maxsize=1)
def _javascript_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_javascript
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_javascript.language())
    return tree_sitter.Parser(language)


@lru_cache(maxsize=2)
def _typescript_parser(*, tsx: bool) -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_typescript
    except ImportError:
        return None

    raw_language = (
        tree_sitter_typescript.language_tsx()
        if tsx
        else tree_sitter_typescript.language_typescript()
    )
    language = tree_sitter.Language(raw_language)
    return tree_sitter.Parser(language)


def _regex_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    if path.suffix not in _JS_TS_SUFFIXES | _RUST_SUFFIXES:
        return [], []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return [], []

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    for line_number, line in enumerate(lines, start=1):
        if path.suffix in _JS_TS_SUFFIXES:
            import_match = re.match(r'^\s*import\s+.*?from\s+["\']([^"\']+)["\']', line)
            export_from_match = re.match(r'^\s*export\s+.*?from\s+["\']([^"\']+)["\']', line)
            class_match = re.match(r"^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            function_match = re.match(
                r"^\s*(?:export\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            if import_match:
                imports.append(import_match.group(1))
            if export_from_match:
                imports.append(export_from_match.group(1))
            if class_match:
                symbols.append(
                    {
                        "name": class_match.group(1),
                        "kind": "class",
                        "file": str(path),
                        "line": line_number,
                    }
                )
            if function_match:
                symbols.append(
                    {
                        "name": function_match.group(1),
                        "kind": "function",
                        "file": str(path),
                        "line": line_number,
                    }
                )
        elif path.suffix in _RUST_SUFFIXES:
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
                symbols.append(
                    {
                        "name": fn_match.group(1),
                        "kind": "function",
                        "file": str(path),
                        "line": line_number,
                    }
                )
            if struct_match:
                symbols.append(
                    {
                        "name": struct_match.group(1),
                        "kind": "struct",
                        "file": str(path),
                        "line": line_number,
                    }
                )
            if enum_match:
                symbols.append(
                    {
                        "name": enum_match.group(1),
                        "kind": "enum",
                        "file": str(path),
                        "line": line_number,
                    }
                )
            if trait_match:
                symbols.append(
                    {
                        "name": trait_match.group(1),
                        "kind": "trait",
                        "file": str(path),
                        "line": line_number,
                    }
                )

    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


def _python_references_and_calls(
    path: Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix != ".py":
        return [], []

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return [], []

    lines = source.splitlines()
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    class Visitor(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            if node.id == symbol:
                references.append(
                    {
                        "name": symbol,
                        "kind": "reference",
                        "file": str(path),
                        "line": node.lineno,
                        "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
                    }
                )
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr == symbol:
                references.append(
                    {
                        "name": symbol,
                        "kind": "reference",
                        "file": str(path),
                        "line": node.lineno,
                        "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
                    }
                )
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            matched = False
            if isinstance(node.func, ast.Name) and node.func.id == symbol:
                matched = True
            elif isinstance(node.func, ast.Attribute) and node.func.attr == symbol:
                matched = True
            if matched:
                calls.append(
                    {
                        "name": symbol,
                        "kind": "call",
                        "file": str(path),
                        "line": node.lineno,
                        "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
                    }
                )
            self.generic_visit(node)

    Visitor().visit(tree)
    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _regex_references_and_calls(
    path: Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix not in _JS_TS_SUFFIXES | _RUST_SUFFIXES:
        return [], []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return [], []

    symbol_pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    call_pattern = re.compile(rf"(?:\b|\.|::){re.escape(symbol)}\s*\(")

    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    for line_number, line in enumerate(lines, start=1):
        if symbol_pattern.search(line):
            references.append(
                {
                    "name": symbol,
                    "kind": "reference",
                    "file": str(path),
                    "line": line_number,
                    "text": line,
                }
            )
        if call_pattern.search(line):
            calls.append(
                {
                    "name": symbol,
                    "kind": "call",
                    "file": str(path),
                    "line": line_number,
                    "text": line,
                }
            )

    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _js_ts_references_and_calls(
    path: Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix not in _JS_TS_SUFFIXES:
        return [], []

    if path.suffix in {".ts", ".tsx"}:
        parser = _typescript_parser(tsx=path.suffix == ".tsx")
    else:
        parser = _javascript_parser()
    if parser is None:
        return [], []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    tree = parser.parse(source.encode("utf-8"))
    lines = source.splitlines()
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return source[node.start_byte : node.end_byte]

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    def _is_definition_identifier(node: Any) -> bool:
        parent = node.parent
        if parent is None:
            return False
        if parent.type in {
            "function_declaration",
            "class_declaration",
            "method_definition",
            "generator_function_declaration",
        }:
            return True
        return bool(parent.type == "import_specifier")

    def _walk(node: Any) -> None:
        node_type = node.type
        if node_type in {"identifier", "property_identifier"} and _node_text(node) == symbol:
            if not _is_definition_identifier(node):
                references.append(
                    {
                        "name": symbol,
                        "kind": "reference",
                        "file": str(path),
                        "line": node.start_point[0] + 1,
                        "text": _line_text(node),
                    }
                )
        elif node_type == "call_expression":
            function_node = node.child_by_field_name("function")
            matched = False
            if function_node is not None:
                if function_node.type in {"identifier", "property_identifier"}:
                    matched = _node_text(function_node) == symbol
                elif function_node.type == "member_expression":
                    property_node = function_node.child_by_field_name("property")
                    matched = bool(
                        property_node is not None and _node_text(property_node) == symbol
                    )
            if matched:
                calls.append(
                    {
                        "name": symbol,
                        "kind": "call",
                        "file": str(path),
                        "line": node.start_point[0] + 1,
                        "text": _line_text(node),
                    }
                )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _python_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix != ".py":
        return []

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    sources: list[dict[str, Any]] = []

    for node in tree.body:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != symbol:
            continue
        end_lineno = getattr(node, "end_lineno", node.lineno)
        block = "\n".join(lines[node.lineno - 1 : end_lineno])
        if block:
            block = f"{block}\n"
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        sources.append(
            {
                "name": symbol,
                "kind": kind,
                "file": str(path),
                "start_line": node.lineno,
                "end_line": end_lineno,
                "source": block,
            }
        )

    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def _extract_braced_block(lines: list[str], start_index: int) -> tuple[int, str]:
    start_line = lines[start_index]
    start_line_num = start_index + 1
    brace_balance = start_line.count("{") - start_line.count("}")
    if brace_balance <= 0:
        return start_line_num, f"{start_line}\n"

    block_lines = [start_line]
    end_index = start_index
    for current_index in range(start_index + 1, len(lines)):
        current_line = lines[current_index]
        block_lines.append(current_line)
        brace_balance += current_line.count("{") - current_line.count("}")
        end_index = current_index
        if brace_balance <= 0:
            break
    return end_index + 1, "\n".join(block_lines) + "\n"


def _regex_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix not in _JS_TS_SUFFIXES | _RUST_SUFFIXES:
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    if path.suffix in _JS_TS_SUFFIXES:
        patterns = [
            (
                "class",
                re.compile(
                    rf"^\s*(?:export\s+)?class\s+({re.escape(symbol)})\b"
                ),
            ),
            (
                "function",
                re.compile(
                    rf"^\s*(?:export\s+)?function\s+({re.escape(symbol)})\b"
                ),
            ),
        ]
    else:
        patterns = [
            (
                "function",
                re.compile(
                    rf"^\s*(?:pub(?:\([^)]*\))?\s+)?fn\s+({re.escape(symbol)})\b"
                ),
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

        end_line, block = _extract_braced_block(lines, line_number - 1)
        sources.append(
            {
                "name": symbol,
                "kind": matched_kind,
                "file": str(path),
                "start_line": line_number,
                "end_line": end_line,
                "source": block,
            }
        )

    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def build_repo_map(path: str | Path = ".") -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")

    payload = _envelope(root)
    all_files = _iter_repo_files(root)
    tests = [str(current) for current in all_files if _is_test_file(current)]
    source_files = [str(current) for current in all_files if not _is_test_file(current)]

    imports: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    for current in all_files:
        current_imports, current_symbols = _python_imports_and_symbols(current)
        if not current_imports and not current_symbols:
            current_imports, current_symbols = _regex_imports_and_symbols(current)
        if current_imports:
            imports.append({"file": str(current), "imports": current_imports})
        symbols.extend(current_symbols)

    payload["files"] = source_files
    payload["symbols"] = symbols
    payload["imports"] = imports
    payload["tests"] = tests
    payload["related_paths"] = sorted(dict.fromkeys([*source_files, *tests]))
    return payload


def build_repo_map_json(path: str | Path = ".") -> str:
    return json.dumps(build_repo_map(path), indent=2)


def _query_terms(query: str) -> list[str]:
    return [term for term in re.split(r"[^A-Za-z0-9_]+", query.lower()) if term]


def _score_text_terms(text: str, terms: list[str]) -> int:
    haystack = text.lower()
    return sum(1 for term in terms if term in haystack)


def _score_file_path(path: str, terms: list[str]) -> int:
    return _score_text_terms(Path(path).name, terms) + _score_text_terms(path, terms)


def _score_symbol(symbol: dict[str, Any], terms: list[str]) -> int:
    return (
        _score_text_terms(str(symbol["name"]), terms) * 3
        + _score_text_terms(str(symbol["kind"]), terms)
        + _score_file_path(str(symbol["file"]), terms)
    )


def _score_import_entry(entry: dict[str, Any], terms: list[str]) -> int:
    imports_joined = " ".join(str(item) for item in entry["imports"])
    return (
        _score_file_path(str(entry["file"]), terms) + _score_text_terms(imports_joined, terms) * 2
    )


def _source_tokens(source_files: list[str]) -> set[str]:
    tokens: set[str] = set()
    for current in source_files:
        path = Path(current)
        stem = path.stem.lower()
        tokens.add(stem)
        for part in re.split(r"[^A-Za-z0-9_]+", stem):
            if part:
                tokens.add(part)
    return tokens


def _module_aliases_for_path(path: str) -> set[str]:
    current = Path(path)
    aliases = {current.stem.lower()}
    parts = [part.lower() for part in current.with_suffix("").parts]
    if parts:
        aliases.add(".".join(parts))
    if len(parts) > 1:
        aliases.add(".".join(parts[-2:]))
    return {alias for alias in aliases if alias}


def _import_graph_bonus(
    file_path: str,
    dependency_aliases: dict[str, set[str]],
    imports_by_file: dict[str, list[str]],
) -> int:
    bonus = 0
    for import_name in imports_by_file.get(file_path, []):
        lowered = import_name.lower()
        for aliases in dependency_aliases.values():
            if any(alias and alias in lowered for alias in aliases):
                bonus += 4
                break
    return bonus


def _reverse_import_distances(
    seed_files: list[str],
    all_files: list[str],
    imports_by_file: dict[str, list[str]],
) -> dict[str, int]:
    distances: dict[str, int] = {}
    frontier = list(seed_files)
    seen = set(seed_files)

    for depth in range(1, 4):
        dependency_aliases = {current: _module_aliases_for_path(current) for current in frontier}
        next_frontier: list[str] = []
        for current in all_files:
            if current in seen:
                continue
            bonus = _import_graph_bonus(current, dependency_aliases, imports_by_file)
            if bonus <= 0:
                continue
            distances[current] = depth
            seen.add(current)
            next_frontier.append(current)
        if not next_frontier:
            break
        frontier = next_frontier
    return distances


def _dependency_ranked_files(
    ranked_files: list[str],
    all_files: list[str],
    imports_by_file: dict[str, list[str]],
) -> list[str]:
    if not ranked_files:
        return []

    boosted: list[tuple[int, str]] = []
    distances = _reverse_import_distances(ranked_files, all_files, imports_by_file)
    for current, depth in distances.items():
        bonus = max(1, 5 - depth)
        boosted.append((bonus, current))

    boosted.sort(key=lambda item: (-item[0], item[1]))
    for _, current in boosted:
        ranked_files.append(current)
    return ranked_files


def _test_import_bonus(
    test_path: str,
    source_tokens: set[str],
    imports_by_file: dict[str, list[str]],
    file_distances: dict[str, int],
) -> int:
    bonus = 0
    for import_name in imports_by_file.get(test_path, []):
        lowered = import_name.lower()
        if any(token and token in lowered for token in source_tokens):
            bonus += 3
        for file_path, depth in file_distances.items():
            aliases = _module_aliases_for_path(file_path)
            if any(alias and alias in lowered for alias in aliases):
                bonus += max(1, 4 - depth)
                break
    return bonus


def _context_tests(
    source_files: list[str],
    tests: list[str],
    terms: list[str],
    imports_by_file: dict[str, list[str]],
    file_distances: dict[str, int],
) -> list[str]:
    related: list[tuple[int, str]] = []
    source_stems = {Path(current).stem.lower() for current in source_files}
    source_tokens = _source_tokens(source_files)
    for current in tests:
        score = _score_file_path(current, terms)
        stem = Path(current).stem.lower().removeprefix("test_")
        if stem in source_stems:
            score += 2
        score += _test_import_bonus(current, source_tokens, imports_by_file, file_distances)
        if score > 0:
            related.append((score, current))
    related.sort(key=lambda item: (-item[0], item[1]))
    return [path for _, path in related]


def _build_context_pack_from_map(payload: dict[str, Any], query: str) -> dict[str, Any]:
    terms = _query_terms(query)
    imports_by_file = {
        str(entry["file"]): [str(item) for item in entry["imports"]] for entry in payload["imports"]
    }
    file_scores = {str(current): _score_file_path(str(current), terms) for current in payload["files"]}

    scored_symbols: list[dict[str, Any]] = []
    for symbol in payload["symbols"]:
        score = _score_symbol(symbol, terms)
        if score <= 0:
            continue
        scored_symbol = dict(symbol)
        scored_symbol["score"] = score
        scored_symbols.append(scored_symbol)
    scored_symbols.sort(
        key=lambda item: (
            -int(item["score"]),
            str(item["file"]),
            int(item["line"]),
            str(item["name"]),
        )
    )
    for symbol in scored_symbols:
        current = str(symbol["file"])
        file_scores[current] = file_scores.get(current, 0) + int(symbol["score"]) * 2

    scored_imports: list[dict[str, Any]] = []
    for entry in payload["imports"]:
        score = _score_import_entry(entry, terms)
        if score <= 0:
            continue
        scored_entry = dict(entry)
        scored_entry["score"] = score
        scored_imports.append(scored_entry)
    scored_imports.sort(key=lambda item: (-int(item["score"]), str(item["file"])))
    for entry in scored_imports:
        current = str(entry["file"])
        file_scores[current] = file_scores.get(current, 0) + int(entry["score"]) * 2

    dependency_seed_files: list[str] = []
    for symbol in scored_symbols:
        current = str(symbol["file"])
        if current not in dependency_seed_files:
            dependency_seed_files.append(current)
    for entry in scored_imports:
        current = str(entry["file"])
        if current not in dependency_seed_files:
            dependency_seed_files.append(current)
    if not dependency_seed_files:
        dependency_seed_files = [path for path in payload["files"] if _score_file_path(str(path), terms) > 0]

    dependency_aliases = {
        current: _module_aliases_for_path(current) for current in dependency_seed_files
    }
    file_distances = _reverse_import_distances(
        dependency_seed_files,
        [str(current) for current in payload["files"]],
        imports_by_file,
    )
    for current in payload["files"]:
        current_path = str(current)
        if current_path in dependency_seed_files:
            continue
        file_scores[current_path] = file_scores.get(current_path, 0) + _import_graph_bonus(
            current_path,
            dependency_aliases,
            imports_by_file,
        )
        if current_path in file_distances:
            file_scores[current_path] = file_scores.get(current_path, 0) + max(
                1, 5 - file_distances[current_path]
            )

    scored_files = [(score, path) for path, score in file_scores.items() if score > 0]
    scored_files.sort(key=lambda item: (-item[0], item[1]))
    ranked_files = [path for _, path in scored_files]
    if not ranked_files:
        for symbol in scored_symbols:
            current = str(symbol["file"])
            if current not in ranked_files:
                ranked_files.append(current)
        for entry in scored_imports:
            current = str(entry["file"])
            if current not in ranked_files:
                ranked_files.append(current)
    ranked_tests = _context_tests(ranked_files, payload["tests"], terms, imports_by_file, file_distances)

    related_paths = []
    for current in ranked_files:
        related_paths.append(current)
    for current in ranked_tests:
        if current not in related_paths:
            related_paths.append(current)

    payload["routing_reason"] = "context-pack"
    payload["query"] = query
    payload["files"] = ranked_files
    payload["symbols"] = scored_symbols
    payload["imports"] = scored_imports
    payload["tests"] = ranked_tests
    payload["related_paths"] = related_paths
    return payload


def build_context_pack(query: str, path: str | Path = ".") -> dict[str, Any]:
    payload = build_repo_map(path)
    return _build_context_pack_from_map(payload, query)


def build_context_pack_json(query: str, path: str | Path = ".") -> str:
    return json.dumps(build_context_pack(query, path), indent=2)


def build_context_pack_from_map(repo_map: dict[str, Any], query: str) -> dict[str, Any]:
    payload = dict(repo_map)
    payload["files"] = list(repo_map.get("files", []))
    payload["symbols"] = [dict(symbol) for symbol in repo_map.get("symbols", [])]
    payload["imports"] = [dict(entry) for entry in repo_map.get("imports", [])]
    payload["tests"] = list(repo_map.get("tests", []))
    payload["related_paths"] = list(repo_map.get("related_paths", []))
    return _build_context_pack_from_map(payload, query)


def build_symbol_defs(symbol: str, path: str | Path = ".") -> dict[str, Any]:
    payload = build_repo_map(path)
    return build_symbol_defs_from_map(payload, symbol)


def build_symbol_defs_from_map(repo_map: dict[str, Any], symbol: str) -> dict[str, Any]:
    payload = dict(repo_map)
    payload["files"] = list(repo_map.get("files", []))
    payload["symbols"] = [dict(current) for current in repo_map.get("symbols", [])]
    payload["imports"] = [dict(current) for current in repo_map.get("imports", [])]
    payload["tests"] = list(repo_map.get("tests", []))
    payload["related_paths"] = list(repo_map.get("related_paths", []))
    definitions = [
        dict(current) for current in payload["symbols"] if str(current["name"]) == symbol
    ]
    definitions.sort(
        key=lambda item: (
            str(item["file"]),
            int(item["line"]),
            str(item["kind"]),
            str(item["name"]),
        )
    )

    definition_files = [str(current["file"]) for current in definitions]
    related_paths = []
    for current in [*definition_files, *payload["tests"]]:
        if current not in related_paths:
            related_paths.append(current)

    payload["routing_reason"] = "symbol-defs"
    payload["symbol"] = symbol
    payload["definitions"] = definitions
    payload["files"] = sorted(dict.fromkeys(definition_files))
    payload["related_paths"] = related_paths
    return payload


def build_symbol_defs_json(symbol: str, path: str | Path = ".") -> str:
    return json.dumps(build_symbol_defs(symbol, path), indent=2)


def build_symbol_source(symbol: str, path: str | Path = ".") -> dict[str, Any]:
    repo_map = build_repo_map(path)
    return build_symbol_source_from_map(repo_map, symbol)


def build_symbol_source_from_map(repo_map: dict[str, Any], symbol: str) -> dict[str, Any]:
    defs_payload = build_symbol_defs_from_map(repo_map, symbol)
    sources: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for definition in defs_payload["definitions"]:
        current_path = Path(str(definition["file"]))
        if str(current_path) in seen_files:
            continue
        seen_files.add(str(current_path))
        current_sources = _python_symbol_sources(current_path, symbol)
        if not current_sources:
            current_sources = _regex_symbol_sources(current_path, symbol)
        sources.extend(current_sources)

    related_paths: list[str] = []
    for current in [*defs_payload["files"], *defs_payload["tests"]]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _envelope(Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-source"
    payload["symbol"] = symbol
    payload["definitions"] = defs_payload["definitions"]
    payload["files"] = defs_payload["files"]
    payload["symbols"] = defs_payload["symbols"]
    payload["imports"] = defs_payload["imports"]
    payload["tests"] = defs_payload["tests"]
    payload["related_paths"] = related_paths
    payload["sources"] = sources
    return payload


def build_symbol_source_json(symbol: str, path: str | Path = ".") -> str:
    return json.dumps(build_symbol_source(symbol, path), indent=2)


def build_symbol_impact(symbol: str, path: str | Path = ".") -> dict[str, Any]:
    payload = build_repo_map(path)
    return build_symbol_impact_from_map(payload, symbol)


def build_symbol_impact_from_map(repo_map: dict[str, Any], symbol: str) -> dict[str, Any]:
    defs_payload = build_symbol_defs_from_map(repo_map, symbol)
    context_payload = build_context_pack_from_map(repo_map, symbol)

    impacted_files: list[str] = []
    import_files = [str(entry["file"]) for entry in context_payload["imports"]]
    for current in [*defs_payload["files"], *context_payload["files"], *import_files]:
        if current not in impacted_files:
            impacted_files.append(current)

    related_tests: list[str] = []
    for current in [*context_payload["tests"], *defs_payload["tests"]]:
        if current not in related_tests:
            related_tests.append(current)

    related_paths: list[str] = []
    for current in [*impacted_files, *related_tests]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _envelope(Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-impact"
    payload["symbol"] = symbol
    payload["definitions"] = defs_payload["definitions"]
    payload["files"] = impacted_files
    payload["tests"] = related_tests
    payload["imports"] = context_payload["imports"]
    payload["symbols"] = context_payload["symbols"]
    payload["related_paths"] = related_paths
    return payload


def build_symbol_impact_json(symbol: str, path: str | Path = ".") -> str:
    return json.dumps(build_symbol_impact(symbol, path), indent=2)


def build_symbol_refs(symbol: str, path: str | Path = ".") -> dict[str, Any]:
    repo_map = build_repo_map(path)
    return build_symbol_refs_from_map(repo_map, symbol)


def build_symbol_refs_from_map(repo_map: dict[str, Any], symbol: str) -> dict[str, Any]:
    payload = build_symbol_defs_from_map(repo_map, symbol)
    references: list[dict[str, Any]] = []
    for current in _iter_repo_files(Path(payload["path"])):
        current_refs, _ = _python_references_and_calls(current, symbol)
        if not current_refs:
            current_refs, _ = _js_ts_references_and_calls(current, symbol)
        if not current_refs:
            current_refs, _ = _regex_references_and_calls(current, symbol)
        references.extend(current_refs)

    referenced_files = sorted(dict.fromkeys(str(current["file"]) for current in references))
    related_paths: list[str] = []
    for current in [*payload["files"], *referenced_files, *payload["tests"]]:
        if current not in related_paths:
            related_paths.append(current)

    payload["routing_reason"] = "symbol-refs"
    payload["references"] = references
    payload["files"] = referenced_files
    payload["related_paths"] = related_paths
    return payload


def build_symbol_refs_json(symbol: str, path: str | Path = ".") -> str:
    return json.dumps(build_symbol_refs(symbol, path), indent=2)


def build_symbol_callers(symbol: str, path: str | Path = ".") -> dict[str, Any]:
    repo_map = build_repo_map(path)
    return build_symbol_callers_from_map(repo_map, symbol)


def build_symbol_callers_from_map(repo_map: dict[str, Any], symbol: str) -> dict[str, Any]:
    defs_payload = build_symbol_defs_from_map(repo_map, symbol)
    calls: list[dict[str, Any]] = []
    for current in _iter_repo_files(Path(defs_payload["path"])):
        _, current_calls = _python_references_and_calls(current, symbol)
        if not current_calls:
            _, current_calls = _js_ts_references_and_calls(current, symbol)
        if not current_calls:
            _, current_calls = _regex_references_and_calls(current, symbol)
        calls.extend(current_calls)

    caller_files = sorted(dict.fromkeys(str(current["file"]) for current in calls))
    context_payload = build_context_pack_from_map(repo_map, symbol)
    related_tests: list[str] = []
    for current in [*context_payload["tests"], *defs_payload["tests"]]:
        if current not in related_tests:
            related_tests.append(current)

    related_paths: list[str] = []
    for current in [*defs_payload["files"], *caller_files, *related_tests]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _envelope(Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-callers"
    payload["symbol"] = symbol
    payload["definitions"] = defs_payload["definitions"]
    payload["callers"] = calls
    payload["files"] = caller_files
    payload["tests"] = related_tests
    payload["imports"] = context_payload["imports"]
    payload["symbols"] = context_payload["symbols"]
    payload["related_paths"] = related_paths
    return payload


def build_symbol_callers_json(symbol: str, path: str | Path = ".") -> str:
    return json.dumps(build_symbol_callers(symbol, path), indent=2)



