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
_RENDER_PROFILES = {"full", "compact", "llm"}


def _envelope(path: Path) -> dict[str, Any]:
    return {
        "version": JSON_OUTPUT_VERSION,
        "routing_backend": ROUTING_BACKEND,
        "routing_reason": ROUTING_REASON,
        "sidecar_used": False,
        "coverage": {
            "language_scope": "python-js-ts-rust",
            "symbol_navigation": "python-ast+parser-js-ts-rust",
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

    for symbol_node in ast.walk(tree):
        if isinstance(symbol_node, ast.ClassDef):
            symbols.append(
                _symbol_record(
                    name=symbol_node.name,
                    kind="class",
                    file=path,
                    start_line=symbol_node.lineno,
                    end_line=getattr(symbol_node, "end_lineno", symbol_node.lineno),
                )
            )
        elif isinstance(symbol_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                _symbol_record(
                    name=symbol_node.name,
                    kind="function",
                    file=path,
                    start_line=symbol_node.lineno,
                    end_line=getattr(symbol_node, "end_lineno", symbol_node.lineno),
                )
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


@lru_cache(maxsize=1)
def _rust_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_rust
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_rust.language())
    return tree_sitter.Parser(language)


def _symbol_record(
    *,
    name: str,
    kind: str,
    file: Path,
    start_line: int,
    end_line: int | None = None,
) -> dict[str, Any]:
    normalized_end_line = start_line if end_line is None else end_line
    return {
        "name": name,
        "kind": kind,
        "file": str(file),
        "line": start_line,
        "start_line": start_line,
        "end_line": normalized_end_line,
    }


def _node_has_ancestor_type(node: Any, ancestor_types: set[str]) -> bool:
    current = getattr(node, "parent", None)
    while current is not None:
        if current.type in ancestor_types:
            return True
        current = getattr(current, "parent", None)
    return False


def _js_ts_named_import_bindings(source: str) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    pattern = re.compile(
        r"(?:import|export)\s+(?:type\s+)?\{(?P<specifiers>[^}]+)\}\s*from\s*[\"'](?P<module>[^\"']+)[\"']",
        re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(source):
        module_name = match.group("module").strip()
        specifiers = match.group("specifiers")
        for raw_specifier in specifiers.split(","):
            specifier = raw_specifier.strip()
            if not specifier:
                continue
            if " as " in specifier:
                imported, local = (part.strip() for part in specifier.split(" as ", 1))
            else:
                imported = specifier
                local = specifier
            if imported and local:
                bindings.append(
                    {
                        "module": module_name,
                        "imported": imported,
                        "local": local,
                    }
                )
    return bindings


def _split_top_level_list(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    brace_depth = 0
    for char in text:
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        if char == "," and brace_depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def _flatten_rust_use_items(expression: str, prefix: str = "") -> list[str]:
    normalized = expression.strip()
    if not normalized:
        return []

    brace_index = normalized.find("{")
    if brace_index < 0:
        return [f"{prefix}::{normalized}".strip(":") if prefix else normalized]

    prefix_part = normalized[:brace_index].rstrip(":").strip()
    combined_prefix = prefix
    if prefix_part:
        combined_prefix = f"{prefix}::{prefix_part}".strip(":") if prefix else prefix_part

    closing_index = normalized.rfind("}")
    if closing_index < 0:
        return [combined_prefix] if combined_prefix else []
    inner = normalized[brace_index + 1 : closing_index]

    flattened: list[str] = []
    for item in _split_top_level_list(inner):
        if item == "self":
            if combined_prefix:
                flattened.append(combined_prefix)
            continue
        if "{" in item:
            flattened.extend(_flatten_rust_use_items(item, combined_prefix))
            continue
        flattened.append(f"{combined_prefix}::{item}".strip(":") if combined_prefix else item)
    return flattened


def _rust_use_bindings(source: str) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    pattern = re.compile(r"(?:pub\s+)?use\s+([^;]+);", re.MULTILINE | re.DOTALL)
    for match in pattern.finditer(source):
        for item in _flatten_rust_use_items(match.group(1)):
            normalized = item.strip()
            if not normalized:
                continue
            if normalized.endswith("::*"):
                bindings.append(
                    {
                        "module": normalized[:-3].strip(),
                        "wildcard": True,
                    }
                )
                continue

            if " as " in normalized:
                imported_path, local_name = (part.strip() for part in normalized.rsplit(" as ", 1))
            else:
                imported_path = normalized
                local_name = normalized.rsplit("::", 1)[-1].strip()

            if "::" in imported_path:
                module_name, imported_name = imported_path.rsplit("::", 1)
            else:
                module_name = ""
                imported_name = imported_path

            bindings.append(
                {
                    "module": module_name.strip(),
                    "imported": imported_name.strip(),
                    "local": local_name.strip(),
                    "wildcard": False,
                }
            )
    return bindings


def _definition_module_parts(path: str) -> list[str]:
    raw_parts = [part.lower() for part in Path(path).with_suffix("").parts if part]
    parts = [part for part in raw_parts if part not in {".", ".."} and not part.endswith(":\\")]
    if not parts:
        return []
    if parts[-1] in {"__init__", "index", "mod"} and len(parts) > 1:
        parts = parts[:-1]
    return parts


def _normalized_module_parts(module_name: str) -> list[str]:
    parts = [part.lower() for part in re.split(r"[^A-Za-z0-9_]+", module_name) if part]
    while parts and parts[0] in {"crate", "self", "super"}:
        parts = parts[1:]
    return parts


def _module_path_matches_definition(module_name: str, definition_path: str) -> bool:
    module_parts = _normalized_module_parts(module_name)
    definition_parts = _definition_module_parts(definition_path)
    if not module_parts or not definition_parts:
        return False
    return definition_parts[-len(module_parts) :] == module_parts


def _file_imports_symbol_from_definition(
    file_path: Path,
    symbol: str,
    definition_path: str,
) -> bool:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    if file_path.suffix == ".py":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return False

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(_module_path_matches_definition(alias.name, definition_path) for alias in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom):
                if not node.module or not _module_path_matches_definition(node.module, definition_path):
                    continue
                if any(
                    alias.name in {"*", symbol} or alias.asname == symbol for alias in node.names
                ):
                    return True
        return False

    if file_path.suffix in _JS_TS_SUFFIXES:
        bindings = _js_ts_named_import_bindings(source)
        return any(
            binding["imported"] == symbol
            and _module_path_matches_definition(binding["module"], definition_path)
            for binding in bindings
        )

    if file_path.suffix in _RUST_SUFFIXES:
        bindings = _rust_use_bindings(source)
        return any(
            _module_path_matches_definition(str(binding.get("module", "")), definition_path)
            and (
                bool(binding.get("wildcard"))
                or str(binding.get("imported", "")) == symbol
                or str(binding.get("local", "")) == symbol
            )
            for binding in bindings
        )

    return False


def _preferred_definition_files(repo_map: dict[str, Any], symbol: str) -> list[str]:
    definitions = [
        dict(current)
        for current in repo_map.get("symbols", [])
        if str(current.get("name")) == symbol
    ]
    definition_files = list(
        dict.fromkeys(str(current["file"]) for current in definitions)
    )
    if len(definition_files) <= 1:
        return definition_files

    scores = dict.fromkeys(definition_files, 0)
    for current in _iter_repo_files(Path(repo_map["path"])):
        current_path = str(current)
        if current_path in scores:
            continue
        for definition_file in definition_files:
            if _file_imports_symbol_from_definition(current, symbol, definition_file):
                scores[definition_file] += 2 if _is_test_file(current) else 1

    preferred = [current for current, score in scores.items() if score > 0]
    return preferred or definition_files


def _relevant_tests_for_symbol(
    repo_map: dict[str, Any],
    symbol: str,
    definition_files: list[str],
    *,
    caller_files: list[str] | None = None,
    fallback_tests: list[str] | None = None,
) -> list[str]:
    tests = [str(current) for current in repo_map.get("tests", [])]
    caller_set = set(caller_files or [])
    related: list[str] = []
    for current in tests:
        if current in caller_set:
            related.append(current)
            continue
        path = Path(current)
        if any(
            _file_imports_symbol_from_definition(path, symbol, definition_file)
            for definition_file in definition_files
        ):
            related.append(current)
    if related:
        return related
    if fallback_tests:
        return list(dict.fromkeys(str(current) for current in fallback_tests))
    return []


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
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=class_match.group(1),
                        kind="class",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if function_match:
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=function_match.group(1),
                        kind="function",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
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
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=fn_match.group(1),
                        kind="function",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if struct_match:
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=struct_match.group(1),
                        kind="struct",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if enum_match:
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=enum_match.group(1),
                        kind="enum",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )
            if trait_match:
                end_line, _ = _extract_braced_block(lines, line_number - 1)
                symbols.append(
                    _symbol_record(
                        name=trait_match.group(1),
                        kind="trait",
                        file=path,
                        start_line=line_number,
                        end_line=end_line,
                    )
                )

    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


def _js_ts_parser_symbols(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _JS_TS_SUFFIXES:
        return []

    if path.suffix in {".ts", ".tsx"}:
        parser = _typescript_parser(tsx=path.suffix == ".tsx")
    else:
        parser = _javascript_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    tree = parser.parse(source.encode("utf-8"))
    symbols: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return source[node.start_byte : node.end_byte]

    def _walk(node: Any) -> None:
        if node.type in {"function_declaration", "class_declaration"}:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                symbols.append(
                    _symbol_record(
                        name=_node_text(name_node),
                        kind="class" if node.type == "class_declaration" else "function",
                        file=path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                    )
                )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return symbols


def _rust_parser_symbols(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _RUST_SUFFIXES:
        return []

    parser = _rust_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    tree = parser.parse(source.encode("utf-8"))
    symbols: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return source[node.start_byte : node.end_byte]

    def _walk(node: Any) -> None:
        kind_map = {
            "function_item": "function",
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "trait",
        }
        if node.type in kind_map:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for child in node.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None:
                symbols.append(
                    _symbol_record(
                        name=_node_text(name_node),
                        kind=kind_map[node.type],
                        file=path,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                    )
                )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return symbols


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
    alias_names = {
        binding["local"]
        for binding in _js_ts_named_import_bindings(source)
        if binding["imported"] == symbol
    }

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
        node_text = _node_text(node) if node_type in {"identifier", "property_identifier"} else ""
        matched_identifier = node_text == symbol or (
            node_type == "identifier" and node_text in alias_names
        )
        if matched_identifier:
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
                    function_name = _node_text(function_node)
                    matched = function_name == symbol or (
                        function_node.type == "identifier" and function_name in alias_names
                    )
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


def _rust_references_and_calls(
    path: Path, symbol: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix not in _RUST_SUFFIXES:
        return [], []

    parser = _rust_parser()
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
    bindings = _rust_use_bindings(source)
    local_names = {
        str(binding["local"])
        for binding in bindings
        if not bool(binding.get("wildcard")) and str(binding.get("imported", "")) == symbol
    }
    if any(bool(binding.get("wildcard")) for binding in bindings):
        local_names.add(symbol)

    def _node_text(node: Any) -> str:
        return source[node.start_byte : node.end_byte]

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    def _is_definition_identifier(node: Any) -> bool:
        parent = node.parent
        if parent is None:
            return False
        return bool(parent.type in {"function_item", "struct_item", "enum_item", "trait_item"})

    def _walk(node: Any) -> None:
        node_type = node.type
        if node_type == "identifier":
            node_text = _node_text(node)
            if (
                (node_text == symbol or node_text in local_names)
                and not _is_definition_identifier(node)
                and not _node_has_ancestor_type(node, {"use_declaration"})
            ):
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
                if function_node.type == "identifier":
                    function_name = _node_text(function_node)
                    matched = function_name == symbol or function_name in local_names
                elif function_node.type == "field_expression":
                    field_node = function_node.child_by_field_name("field")
                    matched = bool(field_node is not None and _node_text(field_node) == symbol)
                elif function_node.type == "scoped_identifier":
                    name_node = function_node.child_by_field_name("name")
                    matched = bool(name_node is not None and _node_text(name_node) == symbol)
            if matched:
                references.append(
                    {
                        "name": symbol,
                        "kind": "reference",
                        "file": str(path),
                        "line": node.start_point[0] + 1,
                        "text": _line_text(node),
                    }
                )
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

    symbol_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol
    ]
    symbol_nodes.sort(key=lambda current: (current.lineno, getattr(current, "end_lineno", current.lineno)))
    for node in symbol_nodes:
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


def _js_ts_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix not in _JS_TS_SUFFIXES:
        return []

    if path.suffix in {".ts", ".tsx"}:
        parser = _typescript_parser(tsx=path.suffix == ".tsx")
    else:
        parser = _javascript_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    tree = parser.parse(source.encode("utf-8"))
    sources: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return source[node.start_byte : node.end_byte]

    def _walk(node: Any) -> None:
        if node.type in {"function_declaration", "class_declaration"}:
            name_node = node.child_by_field_name("name")
            if name_node is not None and _node_text(name_node) == symbol:
                block = _node_text(node)
                if block and not block.endswith("\n"):
                    block = f"{block}\n"
                sources.append(
                    {
                        "name": symbol,
                        "kind": "class" if node.type == "class_declaration" else "function",
                        "file": str(path),
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "source": block,
                    }
                )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def _rust_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix not in _RUST_SUFFIXES:
        return []

    parser = _rust_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    tree = parser.parse(source.encode("utf-8"))
    sources: list[dict[str, Any]] = []
    kind_map = {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
    }

    def _node_text(node: Any) -> str:
        return source[node.start_byte : node.end_byte]

    def _walk(node: Any) -> None:
        if node.type in kind_map:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for child in node.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None and _node_text(name_node) == symbol:
                block = _node_text(node)
                if block and not block.endswith("\n"):
                    block = f"{block}\n"
                sources.append(
                    {
                        "name": symbol,
                        "kind": kind_map[node.type],
                        "file": str(path),
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "source": block,
                    }
                )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
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
        if current.suffix in _JS_TS_SUFFIXES:
            current_imports, _ = _regex_imports_and_symbols(current)
            current_symbols = _js_ts_parser_symbols(current)
            if not current_symbols:
                _, current_symbols = _regex_imports_and_symbols(current)
        elif current.suffix in _RUST_SUFFIXES:
            current_imports, _ = _regex_imports_and_symbols(current)
            current_symbols = _rust_parser_symbols(current)
            if not current_symbols:
                _, current_symbols = _regex_imports_and_symbols(current)
        elif not current_imports and not current_symbols:
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


def _append_reason(reason_map: dict[str, list[str]], path: str, reason: str) -> None:
    current = reason_map.setdefault(path, [])
    if reason not in current:
        current.append(reason)


def _match_record(
    path: str,
    score: int,
    reasons: list[str],
    graph_score: float | None = None,
) -> dict[str, Any]:
    payload = {
        "path": path,
        "score": score,
        "reasons": list(reasons),
    }
    if graph_score is not None:
        payload["graph_score"] = round(graph_score, 6)
    return payload


def _file_summaries(symbols: list[dict[str, Any]], ranked_files: list[str]) -> list[dict[str, Any]]:
    symbols_by_file: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        current_path = str(symbol["file"])
        current_symbols = symbols_by_file.setdefault(current_path, [])
        current_symbols.append(
            {
                "name": str(symbol["name"]),
                "kind": str(symbol["kind"]),
                "line": int(symbol["line"]),
            }
        )
    for current_symbols in symbols_by_file.values():
        current_symbols.sort(key=lambda item: (int(item["line"]), str(item["kind"]), str(item["name"])))

    summaries: list[dict[str, Any]] = []
    for current in ranked_files:
        file_symbols = symbols_by_file.get(str(current), [])
        if not file_symbols:
            continue
        summaries.append({"path": str(current), "symbols": file_symbols})
    return summaries


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


def _reverse_importers(
    all_files: list[str],
    imports_by_file: dict[str, list[str]],
) -> dict[str, set[str]]:
    aliases_by_file = {current: _module_aliases_for_path(current) for current in all_files}
    reverse: dict[str, set[str]] = {current: set() for current in all_files}
    for importer in all_files:
        for import_name in imports_by_file.get(importer, []):
            lowered = import_name.lower()
            for current, aliases in aliases_by_file.items():
                if current == importer:
                    continue
                if any(alias and alias in lowered for alias in aliases):
                    reverse[current].add(importer)
    return reverse


def _personalized_reverse_import_pagerank(
    seed_files: list[str],
    all_files: list[str],
    reverse_importers: dict[str, set[str]],
    *,
    alpha: float = 0.85,
    iterations: int = 12,
) -> dict[str, float]:
    if not seed_files:
        return {}

    unique_seeds = [current for current in seed_files if current in set(all_files)]
    if not unique_seeds:
        return {}

    seed_set = set(unique_seeds)
    seed_weight = 1.0 / len(unique_seeds)
    personalization = {
        current: (seed_weight if current in seed_set else 0.0) for current in all_files
    }
    ranks = dict(personalization)
    for _ in range(iterations):
        updated = {
            current: (1.0 - alpha) * personalization[current] for current in all_files
        }
        for current in all_files:
            outgoing = sorted(reverse_importers.get(current, set()))
            if outgoing:
                share = alpha * ranks[current] / len(outgoing)
                for importer in outgoing:
                    updated[importer] = updated.get(importer, 0.0) + share
                continue
            spill = alpha * ranks[current] / len(unique_seeds)
            for seed in unique_seeds:
                updated[seed] = updated.get(seed, 0.0) + spill
        ranks = updated
    return {current: rank for current, rank in ranks.items() if rank > 0.0}


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


def _test_graph_score(
    test_path: str,
    source_files: list[str],
    imports_by_file: dict[str, list[str]],
    graph_scores: dict[str, float],
    file_scores: dict[str, int],
) -> float:
    aliases_by_file = {current: _module_aliases_for_path(current) for current in source_files}
    score = 0.0
    matched_files: set[str] = set()
    max_file_score = max(file_scores.values(), default=0)
    for import_name in imports_by_file.get(test_path, []):
        lowered = import_name.lower()
        for current, aliases in aliases_by_file.items():
            if current in matched_files:
                continue
            if any(alias and alias in lowered for alias in aliases):
                matched_files.add(current)
                score += graph_scores.get(current, 0.0)
                if max_file_score > 0:
                    score += file_scores.get(current, 0) / max_file_score
    return score


def _context_tests(
    source_files: list[str],
    tests: list[str],
    terms: list[str],
    imports_by_file: dict[str, list[str]],
    file_distances: dict[str, int],
    graph_scores: dict[str, float],
    file_scores: dict[str, int],
) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    source_stems = {Path(current).stem.lower() for current in source_files}
    source_tokens = _source_tokens(source_files)
    for current in tests:
        score = _score_file_path(current, terms)
        reasons: list[str] = []
        if score > 0:
            reasons.append("path")
        stem = Path(current).stem.lower().removeprefix("test_")
        if stem in source_stems:
            score += 2
            reasons.append("filename")
        import_bonus = _test_import_bonus(current, source_tokens, imports_by_file, file_distances)
        score += import_bonus
        if import_bonus > 0:
            reasons.append("test-graph")
        graph_score = _test_graph_score(
            current,
            source_files,
            imports_by_file,
            graph_scores,
            file_scores,
        )
        if graph_score > 0.0:
            reasons.append("graph-centrality")
            score += max(1, round(graph_score * 10))
        if score > 0:
            related.append(_match_record(current, score, reasons, graph_score if graph_score > 0.0 else None))
    related.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return related


def _build_context_pack_from_map(payload: dict[str, Any], query: str) -> dict[str, Any]:
    terms = _query_terms(query)
    all_symbols = [dict(symbol) for symbol in payload["symbols"]]
    imports_by_file = {
        str(entry["file"]): [str(item) for item in entry["imports"]] for entry in payload["imports"]
    }
    file_scores = {str(current): _score_file_path(str(current), terms) for current in payload["files"]}
    file_reasons: dict[str, list[str]] = {}
    for current, score in file_scores.items():
        if score > 0:
            _append_reason(file_reasons, current, "path")

    scored_symbols: list[dict[str, Any]] = []
    for symbol in payload["symbols"]:
        score = _score_symbol(symbol, terms)
        if score <= 0:
            continue
        scored_symbol = dict(symbol)
        scored_symbol["score"] = score
        current_path = str(scored_symbol["file"])
        _append_reason(file_reasons, current_path, "definition")
        if _score_text_terms(str(scored_symbol["name"]), terms) > 0:
            _append_reason(file_reasons, current_path, "symbol")
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
        _append_reason(file_reasons, current, "import")

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

    all_files = [str(current) for current in payload["files"]]
    dependency_aliases = {
        current: _module_aliases_for_path(current) for current in dependency_seed_files
    }
    file_distances = _reverse_import_distances(
        dependency_seed_files,
        all_files,
        imports_by_file,
    )
    reverse_importers = _reverse_importers(all_files, imports_by_file)
    for current in payload["files"]:
        current_path = str(current)
        if current_path in dependency_seed_files:
            continue
        import_graph_bonus = _import_graph_bonus(
            current_path,
            dependency_aliases,
            imports_by_file,
        )
        file_scores[current_path] = file_scores.get(current_path, 0) + import_graph_bonus
        if import_graph_bonus > 0:
            _append_reason(file_reasons, current_path, "import-graph")
        if current_path in file_distances:
            file_scores[current_path] = file_scores.get(current_path, 0) + max(1, 5 - file_distances[current_path])
            _append_reason(file_reasons, current_path, "import-graph")
    graph_seed_files: list[str] = []
    for symbol in scored_symbols:
        current = str(symbol["file"])
        if current not in graph_seed_files:
            graph_seed_files.append(current)
    if not graph_seed_files:
        graph_seed_files = list(dependency_seed_files)

    graph_scores = _personalized_reverse_import_pagerank(
        graph_seed_files,
        all_files,
        reverse_importers,
    )
    for current_path in set(dependency_seed_files) | set(file_distances):
        graph_score = graph_scores.get(current_path, 0.0)
        if graph_score <= 0.0:
            continue
        file_scores[current_path] = file_scores.get(current_path, 0) + max(1, round(graph_score * 10))
        _append_reason(file_reasons, current_path, "graph-centrality")

    scored_files = [(score, path) for path, score in file_scores.items() if score > 0]
    scored_files.sort(key=lambda item: (-item[0], item[1]))
    ranked_files = [path for _, path in scored_files]
    file_matches = [
        _match_record(path, score, file_reasons.get(path, []), graph_scores.get(path))
        for score, path in scored_files
    ]
    if not ranked_files:
        for symbol in scored_symbols:
            current = str(symbol["file"])
            if current not in ranked_files:
                ranked_files.append(current)
        for entry in scored_imports:
            current = str(entry["file"])
            if current not in ranked_files:
                ranked_files.append(current)
    test_matches = _context_tests(
        ranked_files,
        payload["tests"],
        terms,
        imports_by_file,
        file_distances,
        graph_scores,
        file_scores,
    )
    ranked_tests = [str(item["path"]) for item in test_matches]

    related_paths = []
    for current in ranked_files:
        related_paths.append(current)
    for current in ranked_tests:
        if current not in related_paths:
            related_paths.append(current)

    payload["routing_reason"] = "context-pack"
    payload["query"] = query
    payload["files"] = ranked_files
    payload["file_matches"] = file_matches
    payload["file_summaries"] = _file_summaries(all_symbols, ranked_files)
    payload["symbols"] = scored_symbols
    payload["imports"] = scored_imports
    payload["tests"] = ranked_tests
    payload["test_matches"] = test_matches
    payload["related_paths"] = related_paths
    return payload


def build_context_pack(query: str, path: str | Path = ".") -> dict[str, Any]:
    payload = build_repo_map(path)
    return _build_context_pack_from_map(payload, query)


def build_context_pack_json(query: str, path: str | Path = ".") -> str:
    return json.dumps(build_context_pack(query, path), indent=2)


def _render_context_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"kind": "query", "text": f"Query: {payload['query']}"}]
    file_matches_by_path = {
        str(match["path"]): match for match in payload.get("file_matches", [])
    }
    test_matches_by_path = {
        str(match["path"]): match for match in payload.get("test_matches", [])
    }
    symbol_scores_by_key = {
        (str(symbol["file"]), str(symbol["name"])): int(symbol.get("score", 0))
        for symbol in payload.get("symbols", [])
    }
    tests = [str(current) for current in payload.get("tests", [])]
    if tests:
        test_lines = ["Tests:", *[f"- {current}" for current in tests[:3]]]
        parts.append(
            {
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
            }
        )

    sources_by_file: dict[str, list[dict[str, Any]]] = {}
    for source in payload.get("sources", []):
        current = str(source["file"])
        current_sources = sources_by_file.setdefault(current, [])
        current_sources.append(source)

    for summary in payload.get("file_summaries", [])[: int(payload.get("max_files", 3))]:
        current_path = str(summary["path"])
        summary_lines = [f"File: {current_path}", "Summary:"]
        for symbol in summary.get("symbols", [])[: int(payload.get("max_symbols_per_file", 6))]:
            summary_lines.append(f"- {symbol['kind']} {symbol['name']} @ line {symbol['line']}")
        file_match = file_matches_by_path.get(current_path, {})
        parts.append(
            {
                "kind": "summary",
                "path": current_path,
                "text": "\n".join(summary_lines),
                "provenance": {
                    "path": current_path,
                    "score": int(file_match.get("score", 0)),
                    "graph_score": file_match.get("graph_score"),
                    "reasons": list(file_match.get("reasons", [])),
                },
            }
        )
        for source in sources_by_file.get(current_path, [])[:2]:
            file_match = file_matches_by_path.get(current_path, {})
            symbol_name = str(source["name"])
            parts.append(
                {
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
                }
            )
    return parts


def _render_context_string_and_sections(
    payload: dict[str, Any],
    *,
    max_render_chars: int | None = None,
) -> tuple[str, list[dict[str, Any]], bool]:
    parts = _render_context_parts(payload)
    sections: list[dict[str, Any]] = []
    rendered_parts: list[str] = []
    offset = 0
    truncated = False
    for part in parts:
        text = str(part["text"]).strip()
        if not text:
            continue
        prefix = "" if not rendered_parts else "\n\n"
        chunk = f"{prefix}{text}"
        if max_render_chars is not None and max_render_chars > 0 and offset + len(chunk) > max_render_chars:
            remaining = max_render_chars - offset
            if remaining > 0:
                chunk = chunk[:remaining]
                rendered_parts.append(chunk)
                sections.append(
                    {
                        "kind": str(part["kind"]),
                        "start": offset,
                        "end": offset + len(chunk),
                        **{key: value for key, value in part.items() if key != "text"},
                    }
                )
                offset += len(chunk)
            truncated = True
            break
        rendered_parts.append(chunk)
        sections.append(
            {
                "kind": str(part["kind"]),
                "start": offset,
                "end": offset + len(chunk),
                **{key: value for key, value in part.items() if key != "text"},
            }
        )
        offset += len(chunk)
    return "".join(rendered_parts).rstrip(), sections, truncated


def _normalize_render_profile(render_profile: str, optimize_context: bool) -> str:
    profile = render_profile.strip().lower() or "full"
    if profile not in _RENDER_PROFILES:
        raise ValueError(f"Unsupported render profile: {render_profile}")
    if optimize_context and profile == "full":
        return "compact"
    return profile


def _is_comment_line(path: Path, line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if path.suffix == ".py":
        return stripped.startswith("#")
    if path.suffix in _JS_TS_SUFFIXES | _RUST_SUFFIXES:
        return (
            stripped.startswith("//")
            or stripped.startswith("/*")
            or stripped.startswith("*")
            or stripped.startswith("*/")
        )
    return False


def _python_ast_omitted_relative_lines(block: str) -> tuple[set[int], set[int]]:
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return set(), set()

    docstring_lines: set[int] = set()
    boilerplate_lines: set[int] = set()

    for node in tree.body:
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        first_value = getattr(first, "value", None)
        if (
            isinstance(first, ast.Expr)
            and isinstance(first_value, ast.Constant)
            and isinstance(first_value.value, str)
        ):
            end_lineno = getattr(first, "end_lineno", first.lineno)
            docstring_lines.update(range(first.lineno, end_lineno + 1))
        if len(body) == 2 and any(isinstance(child, ast.Pass) for child in body):
            for child in body:
                if isinstance(child, ast.Pass):
                    end_lineno = getattr(child, "end_lineno", child.lineno)
                    boilerplate_lines.update(range(child.lineno, end_lineno + 1))

    return docstring_lines, boilerplate_lines


def _render_source_block(
    source: dict[str, Any],
    *,
    render_profile: str,
    optimize_context: bool,
) -> dict[str, Any]:
    block = str(source.get("source", ""))
    path = Path(str(source["file"]))
    normalized_profile = _normalize_render_profile(render_profile, optimize_context)
    diagnostics = {
        "original_line_count": 0,
        "rendered_line_count": 0,
        "removed_line_count": 0,
        "removed_comment_lines": 0,
        "removed_blank_lines": 0,
        "removed_docstring_lines": 0,
        "removed_boilerplate_lines": 0,
    }
    line_map: list[dict[str, int]] = []

    original_lines = block.splitlines()
    diagnostics["original_line_count"] = len(original_lines)
    if normalized_profile == "full":
        rendered_source = block
        if original_lines:
            line_map.append(
                {
                    "rendered_start_line": 1,
                    "rendered_end_line": len(original_lines),
                    "original_start_line": int(source["start_line"]),
                    "original_end_line": int(source["end_line"]),
                }
            )
        diagnostics["rendered_line_count"] = len(original_lines)
    else:
        kept_lines: list[str] = []
        current_segment: dict[str, int] | None = None
        rendered_line_number = 1
        original_start = int(source["start_line"])
        omitted_docstring_lines: set[int] = set()
        omitted_boilerplate_lines: set[int] = set()
        if path.suffix == ".py":
            omitted_docstring_lines, omitted_boilerplate_lines = _python_ast_omitted_relative_lines(
                block
            )
        for index, line in enumerate(original_lines):
            original_line_number = original_start + index
            relative_line_number = index + 1
            if not line.strip():
                diagnostics["removed_blank_lines"] += 1
                continue
            if _is_comment_line(path, line):
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


def _confidence_from_score(score: int) -> float:
    if score <= 0:
        return 0.0
    return round(min(1.0, 0.35 + (score / 20.0)), 3)


def _primary_span_for_symbol(symbol: dict[str, Any] | None) -> dict[str, int] | None:
    if symbol is None:
        return None
    start_line = symbol.get("start_line", symbol.get("line"))
    end_line = symbol.get("end_line", start_line)
    if not isinstance(start_line, int) or not isinstance(end_line, int):
        return None
    return {
        "start_line": start_line,
        "end_line": end_line,
    }


def _validation_commands_for_tests(tests: list[str]) -> list[str]:
    commands: list[str] = []
    for current in tests:
        path = Path(current)
        if path.suffix == ".py":
            commands.append(f"uv run pytest {path} -q")
    return commands


def build_context_render(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
) -> dict[str, Any]:
    repo_map = build_repo_map(path)
    return build_context_render_from_map(
        repo_map,
        query,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        optimize_context=optimize_context,
        render_profile=render_profile,
    )


def build_context_render_from_map(
    repo_map: dict[str, Any],
    query: str,
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
) -> dict[str, Any]:
    context_payload = build_context_pack_from_map(repo_map, query)
    normalized_profile = _normalize_render_profile(render_profile, optimize_context)
    max_files = max(1, max_files)
    max_sources = max(1, max_sources)
    max_symbols_per_file = max(1, max_symbols_per_file)
    top_files = {str(current) for current in context_payload.get("files", [])[:max_files]}
    sources: list[dict[str, Any]] = []
    seen_symbols: set[tuple[str, str]] = set()
    for symbol in context_payload.get("symbols", []):
        current_file = str(symbol["file"])
        if current_file not in top_files:
            continue
        symbol_key = (current_file, str(symbol["name"]))
        if symbol_key in seen_symbols:
            continue
        seen_symbols.add(symbol_key)
        symbol_sources = build_symbol_source_from_map(repo_map, str(symbol["name"])).get("sources", [])
        for source in symbol_sources:
            if str(source["file"]) != current_file:
                continue
            sources.append(
                _render_source_block(
                    source,
                    render_profile=normalized_profile,
                    optimize_context=optimize_context,
                )
            )
            break
        if len(sources) >= max_sources:
            break

    payload = dict(context_payload)
    payload["routing_reason"] = "context-render"
    payload["files"] = list(payload.get("files", []))[:max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:max_files]
    payload["file_summaries"] = [
        {
            "path": str(summary["path"]),
            "symbols": list(summary.get("symbols", []))[:max_symbols_per_file],
        }
        for summary in list(payload.get("file_summaries", []))[:max_files]
    ]
    payload["sources"] = sources
    payload["max_files"] = max_files
    payload["max_sources"] = max_sources
    payload["max_symbols_per_file"] = max_symbols_per_file
    payload["max_render_chars"] = max_render_chars
    payload["optimize_context"] = optimize_context
    payload["render_profile"] = normalized_profile
    rendered_context, sections, truncated = _render_context_string_and_sections(
        payload,
        max_render_chars=max_render_chars,
    )
    payload["rendered_context"] = rendered_context
    payload["sections"] = sections
    payload["truncated"] = truncated
    ranked_symbols = sorted(
        payload.get("symbols", []),
        key=lambda symbol: (
            -int(symbol.get("score", 0)),
            0 if str(symbol.get("kind")) == "function" else 1,
            str(symbol.get("file")),
            int(symbol.get("line", 0)),
            str(symbol.get("name")),
        ),
    )
    payload["candidate_edit_targets"] = {
        "files": list(payload.get("files", []))[:max_files],
        "symbols": ranked_symbols[:max_sources],
        "tests": list(payload.get("tests", []))[:max_files],
    }
    primary_file = next(iter(payload.get("files", [])), None)
    primary_symbol = None
    if primary_file is not None:
        primary_file_symbols = [
            symbol for symbol in ranked_symbols if str(symbol.get("file")) == str(primary_file)
        ]
        primary_symbol = next(
            iter(primary_file_symbols),
            None,
        )
    if primary_symbol is None:
        primary_symbol = next(iter(ranked_symbols), None)
    primary_file_match = next(
        (
            match
            for match in payload.get("file_matches", [])
            if str(match.get("path")) == str(primary_file)
        ),
        payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
    )
    primary_test = next(iter(payload.get("tests", [])), None)
    primary_test_match = next(
        (
            match
            for match in payload.get("test_matches", [])
            if str(match.get("path")) == str(primary_test)
        ),
        payload.get("test_matches", [{}])[0] if payload.get("test_matches") else {},
    )
    validation_tests = list(payload.get("tests", []))[: max(1, min(max_files, 3))]
    payload["edit_plan_seed"] = {
        "primary_file": primary_file,
        "primary_symbol": primary_symbol,
        "primary_span": _primary_span_for_symbol(primary_symbol),
        "primary_test": primary_test,
        "validation_tests": validation_tests,
        "validation_commands": _validation_commands_for_tests(validation_tests),
        "reasons": list(primary_file_match.get("reasons", [])),
        "confidence": {
            "file": _confidence_from_score(int(primary_file_match.get("score", 0))),
            "symbol": _confidence_from_score(int(primary_symbol.get("score", 0)))
            if primary_symbol is not None
            else 0.0,
            "test": _confidence_from_score(int(primary_test_match.get("score", 0))),
        },
    }
    return payload


def build_context_render_json(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
) -> str:
    return json.dumps(
        build_context_render(
            query,
            path,
            max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            optimize_context=optimize_context,
            render_profile=render_profile,
        ),
        indent=2,
    )


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
        if not current_sources and current_path.suffix in _JS_TS_SUFFIXES:
            current_sources = _js_ts_parser_symbol_sources(current_path, symbol)
        if not current_sources and current_path.suffix in _RUST_SUFFIXES:
            current_sources = _rust_parser_symbol_sources(current_path, symbol)
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
    preferred_definition_files = _preferred_definition_files(repo_map, symbol)
    preferred_definition_file_set = set(preferred_definition_files)
    definitions = [
        dict(current)
        for current in defs_payload["definitions"]
        if str(current["file"]) in preferred_definition_file_set
    ] or [dict(current) for current in defs_payload["definitions"]]
    definition_files = [str(current["file"]) for current in definitions]
    unselected_definition_files = {
        str(current)
        for current in defs_payload["files"]
        if str(current) not in set(definition_files)
    }

    impacted_files: list[str] = []
    import_files = [str(entry["file"]) for entry in context_payload["imports"]]
    for current in [*definition_files, *context_payload["files"], *import_files]:
        if current in unselected_definition_files:
            continue
        if current not in impacted_files:
            impacted_files.append(current)

    related_tests = _relevant_tests_for_symbol(
        repo_map,
        symbol,
        definition_files,
        fallback_tests=list(context_payload.get("tests", [])),
    )

    file_matches_by_path: dict[str, dict[str, Any]] = {
        str(item["path"]): {
            "path": str(item["path"]),
            "score": int(item["score"]),
            "reasons": list(item["reasons"]),
            **(
                {"graph_score": float(item["graph_score"])}
                if "graph_score" in item
                else {}
            ),
        }
        for item in context_payload.get("file_matches", [])
    }
    for current in definition_files:
        entry = file_matches_by_path.setdefault(
            str(current),
            {"path": str(current), "score": 0, "reasons": []},
        )
        if "definition" not in entry["reasons"]:
            entry["reasons"].append("definition")
    for current in import_files:
        entry = file_matches_by_path.setdefault(
            str(current),
            {"path": str(current), "score": 0, "reasons": []},
        )
        if "import" not in entry["reasons"]:
            entry["reasons"].append("import")

    test_matches_by_path: dict[str, dict[str, Any]] = {
        str(item["path"]): {
            "path": str(item["path"]),
            "score": int(item["score"]),
            "reasons": list(item["reasons"]),
            **(
                {"graph_score": float(item["graph_score"])}
                if "graph_score" in item
                else {}
            ),
        }
        for item in context_payload.get("test_matches", [])
    }
    for current in related_tests:
        test_matches_by_path.setdefault(
            str(current),
            {"path": str(current), "score": 1, "reasons": ["test-graph"]},
        )

    related_paths: list[str] = []
    for current in [*impacted_files, *related_tests]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _envelope(Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-impact"
    payload["symbol"] = symbol
    payload["definitions"] = definitions
    payload["files"] = impacted_files
    payload["file_matches"] = [file_matches_by_path[str(current)] for current in impacted_files]
    payload["file_summaries"] = _file_summaries(repo_map.get("symbols", []), impacted_files)
    payload["tests"] = related_tests
    payload["test_matches"] = [test_matches_by_path[str(current)] for current in related_tests]
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
        if current.suffix == ".py":
            current_refs, _ = _python_references_and_calls(current, symbol)
        elif current.suffix in _JS_TS_SUFFIXES:
            current_refs, _ = _js_ts_references_and_calls(current, symbol)
            if not current_refs:
                current_refs, _ = _regex_references_and_calls(current, symbol)
        elif current.suffix in _RUST_SUFFIXES:
            current_refs, current_calls = _rust_references_and_calls(current, symbol)
            if not current_refs and not current_calls:
                current_refs, current_calls = _regex_references_and_calls(current, symbol)
            rust_call_refs = [
                {
                    "name": str(call["name"]),
                    "kind": "reference",
                    "file": str(call["file"]),
                    "line": int(call["line"]),
                    "text": str(call["text"]),
                }
                for call in current_calls
            ]
            current_refs.extend(rust_call_refs)
        else:
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
    preferred_definition_files = _preferred_definition_files(repo_map, symbol)
    preferred_definition_file_set = set(preferred_definition_files)
    definitions = [
        dict(current)
        for current in defs_payload["definitions"]
        if str(current["file"]) in preferred_definition_file_set
    ] or [dict(current) for current in defs_payload["definitions"]]
    definition_files = [str(current["file"]) for current in definitions]
    calls: list[dict[str, Any]] = []
    for current in _iter_repo_files(Path(defs_payload["path"])):
        if current.suffix == ".py":
            _, current_calls = _python_references_and_calls(current, symbol)
        elif current.suffix in _JS_TS_SUFFIXES:
            _, current_calls = _js_ts_references_and_calls(current, symbol)
            if not current_calls:
                _, current_calls = _regex_references_and_calls(current, symbol)
        elif current.suffix in _RUST_SUFFIXES:
            _, current_calls = _rust_references_and_calls(current, symbol)
            if not current_calls:
                _, current_calls = _regex_references_and_calls(current, symbol)
        else:
            _, current_calls = _regex_references_and_calls(current, symbol)
        calls.extend(current_calls)

    caller_files = sorted(dict.fromkeys(str(current["file"]) for current in calls))
    context_payload = build_context_pack_from_map(repo_map, symbol)
    related_tests = _relevant_tests_for_symbol(
        repo_map,
        symbol,
        definition_files,
        caller_files=caller_files,
        fallback_tests=list(context_payload.get("tests", [])),
    )

    related_paths: list[str] = []
    for related_path in [*definition_files, *caller_files, *related_tests]:
        if related_path not in related_paths:
            related_paths.append(related_path)

    payload = _envelope(Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-callers"
    payload["symbol"] = symbol
    payload["definitions"] = definitions
    payload["callers"] = calls
    payload["files"] = caller_files
    payload["tests"] = related_tests
    payload["imports"] = context_payload["imports"]
    payload["symbols"] = context_payload["symbols"]
    payload["related_paths"] = related_paths
    return payload


def build_symbol_callers_json(symbol: str, path: str | Path = ".") -> str:
    return json.dumps(build_symbol_callers(symbol, path), indent=2)


def build_symbol_blast_radius(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
) -> dict[str, Any]:
    repo_map = build_repo_map(path)
    return build_symbol_blast_radius_from_map(repo_map, symbol, max_depth=max_depth)


def build_symbol_blast_radius_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    max_depth: int = 3,
) -> dict[str, Any]:
    defs_payload = build_symbol_defs_from_map(repo_map, symbol)
    callers_payload = build_symbol_callers_from_map(repo_map, symbol)
    impact_payload = build_symbol_impact_from_map(repo_map, symbol)
    preferred_definition_files = _preferred_definition_files(repo_map, symbol)
    preferred_definition_file_set = set(preferred_definition_files)
    definitions = [
        dict(current)
        for current in defs_payload["definitions"]
        if str(current["file"]) in preferred_definition_file_set
    ] or [dict(current) for current in defs_payload["definitions"]]

    normalized_depth = max(0, int(max_depth))
    all_files = [str(current) for current in repo_map.get("files", [])]
    imports_by_file = {
        str(current["file"]): list(
            dict.fromkeys(
                str(import_name) for import_name in current.get("imports", []) if import_name
            )
        )
        for current in repo_map.get("imports", [])
    }
    reverse_importers = _reverse_importers(all_files, imports_by_file)
    definition_files = [str(current["file"]) for current in definitions]
    dependency_distances = _reverse_import_distances(definition_files, all_files, imports_by_file)
    reverse_graph_scores = _personalized_reverse_import_pagerank(
        definition_files,
        all_files,
        reverse_importers,
    )

    direct_callers = [dict(current) for current in callers_payload.get("callers", [])]
    caller_files = sorted(dict.fromkeys(str(current["file"]) for current in direct_callers))

    file_matches_by_path: dict[str, dict[str, Any]] = {}
    file_depths: dict[str, int] = {}

    def _merge_file_match(
        current_path: str,
        *,
        depth: int,
        score: int,
        reasons: list[str],
        graph_score: float | None = None,
    ) -> None:
        entry = file_matches_by_path.setdefault(
            current_path,
            {
                "path": current_path,
                "depth": depth,
                "score": 0,
                "reasons": [],
            },
        )
        entry["depth"] = min(int(entry.get("depth", depth)), depth)
        entry["score"] = max(int(entry.get("score", 0)), score)
        for reason in reasons:
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
        if graph_score is not None and graph_score > float(entry.get("graph_score", 0.0)):
            entry["graph_score"] = round(graph_score, 6)
        file_depths[current_path] = min(file_depths.get(current_path, depth), depth)

    for current in definition_files:
        current_match: dict[str, Any] = next(
            (
                item
                for item in impact_payload.get("file_matches", [])
                if str(item.get("path")) == current
            ),
            {},
        )
        _merge_file_match(
            current,
            depth=0,
            score=max(1, int(current_match.get("score", 0))),
            reasons=["definition", *list(current_match.get("reasons", []))],
            graph_score=(
                float(current_match["graph_score"])
                if "graph_score" in current_match
                else reverse_graph_scores.get(current)
            ),
        )

    for current in caller_files:
        _merge_file_match(
            current,
            depth=1 if current not in definition_files else 0,
            score=max(
                1,
                int(
                    next(
                        (
                            item.get("score", 0)
                            for item in impact_payload.get("file_matches", [])
                            if str(item.get("path")) == current
                        ),
                        0,
                    )
                ),
            ),
            reasons=["caller"],
            graph_score=reverse_graph_scores.get(current),
        )

    for current, depth in dependency_distances.items():
        if depth > normalized_depth:
            continue
        reasons = ["graph-depth"]
        if current in caller_files and "caller" not in reasons:
            reasons.append("caller")
        if current in definition_files and "definition" not in reasons:
            reasons.append("definition")
        graph_score = reverse_graph_scores.get(current, 0.0)
        score = max(1, round(graph_score * 10))
        _merge_file_match(
            current,
            depth=depth,
            score=score,
            reasons=reasons,
            graph_score=graph_score if graph_score > 0.0 else None,
        )

    ranked_files = sorted(
        file_matches_by_path.values(),
        key=lambda item: (
            int(item.get("depth", normalized_depth + 1)),
            -int(item.get("score", 0)),
            -float(item.get("graph_score", 0.0)),
            str(item.get("path", "")),
        ),
    )
    radius_files = [str(item["path"]) for item in ranked_files]

    test_match_lookup = {
        str(item["path"]): dict(item) for item in impact_payload.get("test_matches", [])
    }
    related_tests: list[str] = []
    for current in impact_payload.get("tests", []):
        test_entry = test_match_lookup.get(str(current), {})
        reasons = list(test_entry.get("reasons", []))
        if "blast-radius" not in reasons:
            reasons.append("blast-radius")
        graph_score = float(test_entry.get("graph_score", 0.0))
        score = int(test_entry.get("score", 0))
        coverage_hits = 0
        current_imports = imports_by_file.get(str(current), [])
        for source_file in radius_files:
            aliases = _module_aliases_for_path(source_file)
            if any(alias and alias in import_name.lower() for alias in aliases for import_name in current_imports):
                coverage_hits += 1
        if coverage_hits <= 0 and not any(reason in {"import-graph", "graph-centrality"} for reason in reasons):
            continue
        score += coverage_hits
        related_tests.append(str(current))
        test_match_lookup[str(current)] = {
            "path": str(current),
            "score": score,
            "reasons": reasons,
            **({"graph_score": graph_score} if graph_score > 0.0 else {}),
        }

    caller_tree: list[dict[str, Any]] = []
    rendered_lines = [f"Blast radius for {symbol}:"]
    for depth in range(0, normalized_depth + 1):
        depth_files = [
            str(item["path"]) for item in ranked_files if int(item.get("depth", normalized_depth + 1)) == depth
        ]
        if not depth_files:
            continue
        caller_tree.append({"depth": depth, "files": depth_files})
        rendered_lines.append(f"Depth {depth}:")
        rendered_lines.extend(f"- {current}" for current in depth_files)

    related_paths: list[str] = []
    for current in [*radius_files, *related_tests]:
        if current not in related_paths:
            related_paths.append(current)

    payload = _envelope(Path(defs_payload["path"]))
    payload["routing_reason"] = "symbol-blast-radius"
    payload["symbol"] = symbol
    payload["max_depth"] = normalized_depth
    payload["definitions"] = definitions
    payload["callers"] = direct_callers
    payload["files"] = radius_files
    payload["file_matches"] = ranked_files
    payload["file_summaries"] = _file_summaries(repo_map.get("symbols", []), radius_files)
    payload["tests"] = related_tests
    payload["test_matches"] = [test_match_lookup[str(current)] for current in related_tests]
    payload["caller_tree"] = caller_tree
    payload["rendered_caller_tree"] = "\n".join(rendered_lines)
    payload["imports"] = impact_payload["imports"]
    payload["symbols"] = impact_payload["symbols"]
    payload["related_paths"] = related_paths
    return payload


def build_symbol_blast_radius_json(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
) -> str:
    return json.dumps(
        build_symbol_blast_radius(symbol, path, max_depth=max_depth),
        indent=2,
    )


def build_symbol_blast_radius_render(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
) -> dict[str, Any]:
    repo_map = build_repo_map(path)
    return build_symbol_blast_radius_render_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        optimize_context=optimize_context,
        render_profile=render_profile,
    )


def build_symbol_blast_radius_render_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
) -> dict[str, Any]:
    radius_payload = build_symbol_blast_radius_from_map(repo_map, symbol, max_depth=max_depth)
    normalized_profile = _normalize_render_profile(render_profile, optimize_context)
    max_files = max(1, max_files)
    max_sources = max(1, max_sources)
    max_symbols_per_file = max(1, max_symbols_per_file)

    top_files = {str(current) for current in radius_payload.get("files", [])[:max_files]}
    sources: list[dict[str, Any]] = []
    seen_symbols: set[tuple[str, str]] = set()
    ranked_symbols = sorted(
        radius_payload.get("symbols", []),
        key=lambda current: (
            -int(current.get("score", 0)),
            0 if str(current.get("kind")) == "function" else 1,
            str(current.get("file")),
            int(current.get("line", 0)),
            str(current.get("name")),
        ),
    )
    for current_symbol in ranked_symbols:
        current_file = str(current_symbol["file"])
        if current_file not in top_files:
            continue
        symbol_key = (current_file, str(current_symbol["name"]))
        if symbol_key in seen_symbols:
            continue
        seen_symbols.add(symbol_key)
        symbol_sources = build_symbol_source_from_map(repo_map, str(current_symbol["name"])).get(
            "sources", []
        )
        for source in symbol_sources:
            if str(source["file"]) != current_file:
                continue
            sources.append(
                _render_source_block(
                    source,
                    render_profile=normalized_profile,
                    optimize_context=optimize_context,
                )
            )
            break
        if len(sources) >= max_sources:
            break

    payload = dict(radius_payload)
    payload["routing_reason"] = "symbol-blast-radius-render"
    payload["query"] = f"blast radius: {symbol}"
    payload["files"] = list(payload.get("files", []))[:max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:max_files]
    payload["file_summaries"] = [
        {
            "path": str(summary["path"]),
            "symbols": list(summary.get("symbols", []))[:max_symbols_per_file],
        }
        for summary in list(payload.get("file_summaries", []))[:max_files]
    ]
    payload["sources"] = sources
    payload["max_files"] = max_files
    payload["max_sources"] = max_sources
    payload["max_symbols_per_file"] = max_symbols_per_file
    payload["max_render_chars"] = max_render_chars
    payload["optimize_context"] = optimize_context
    payload["render_profile"] = normalized_profile
    rendered_context, sections, truncated = _render_context_string_and_sections(
        payload,
        max_render_chars=max_render_chars,
    )
    payload["rendered_context"] = rendered_context
    payload["sections"] = sections
    payload["truncated"] = truncated
    payload["candidate_edit_targets"] = {
        "files": list(payload.get("files", []))[:max_files],
        "symbols": ranked_symbols[:max_sources],
        "tests": list(payload.get("tests", []))[:max_files],
    }
    primary_file = next(iter(payload.get("files", [])), None)
    primary_symbol = None
    if primary_file is not None:
        primary_file_symbols = [
            current for current in ranked_symbols if str(current.get("file")) == str(primary_file)
        ]
        primary_symbol = next(iter(primary_file_symbols), None)
    if primary_symbol is None:
        primary_symbol = next(iter(ranked_symbols), None)
    primary_file_match = next(
        (
            match
            for match in payload.get("file_matches", [])
            if str(match.get("path")) == str(primary_file)
        ),
        payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
    )
    primary_test = next(iter(payload.get("tests", [])), None)
    primary_test_match = next(
        (
            match
            for match in payload.get("test_matches", [])
            if str(match.get("path")) == str(primary_test)
        ),
        payload.get("test_matches", [{}])[0] if payload.get("test_matches") else {},
    )
    validation_tests = list(payload.get("tests", []))[: max(1, min(max_files, 3))]
    payload["edit_plan_seed"] = {
        "primary_file": primary_file,
        "primary_symbol": primary_symbol,
        "primary_span": _primary_span_for_symbol(primary_symbol),
        "primary_test": primary_test,
        "validation_tests": validation_tests,
        "validation_commands": _validation_commands_for_tests(validation_tests),
        "reasons": list(primary_file_match.get("reasons", [])),
        "confidence": {
            "file": _confidence_from_score(int(primary_file_match.get("score", 0))),
            "symbol": _confidence_from_score(int(primary_symbol.get("score", 0)))
            if primary_symbol is not None
            else 0.0,
            "test": _confidence_from_score(int(primary_test_match.get("score", 0))),
        },
    }
    return payload


def build_symbol_blast_radius_render_json(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
) -> str:
    return json.dumps(
        build_symbol_blast_radius_render(
            symbol,
            path,
            max_depth=max_depth,
            max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            optimize_context=optimize_context,
            render_profile=render_profile,
        ),
        indent=2,
    )




