"""JavaScript/TypeScript-specific import, module and symbol resolution extracted from `repo_map`.

Everything here is JS/TS dialect knowledge -- ESM/CJS import-binding extraction, tsconfig-aware
module candidate resolution, re-export chasing, default-export naming, dynamic `import()` hits,
and the test-runner command shapes -- plus the private helpers only those paths call. Split out
of `repo_map.py` under docs/design/2026-08-19-split-floor-escape.md.

`_javascript_parser`, `_js_ts_classify_ref_kind`, `_javascript_test_function_candidates` and
`_javascript_test_file_uses_node_test` deliberately stay in `repo_map`: the test suite
monkeypatches them there, so a moved copy would be the unpatched one.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.cli.repo_map_cache import _SOURCE_READ_CACHE_MAXSIZE as _SOURCE_READ_CACHE_MAXSIZE
from tensor_grep.cli.repo_map_cache import _mtime_aware_cache as _mtime_aware_cache
from tensor_grep.cli.repo_map_cache import _resolved_path_str as _resolved_path_str

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


@_mtime_aware_cache(maxsize=_SOURCE_READ_CACHE_MAXSIZE)
def _js_ts_has_import_bindings(path_str: str) -> bool:
    """True iff *path_str* has >=1 real JS/TS import binding (named/default/namespace).

    Backs the sound gate in _file_may_import_symbol_definition above. Fails OPEN (True) on a
    read error, matching the fail-open stat/read-error arms just above it (:1269-70, :1275-76)
    -- an unreadable file must never be silently excluded from the caller/import-graph scan.
    """
    try:
        source = _self._read_source_text_cached(path_str)
    except (OSError, UnicodeDecodeError):
        return True
    if any(
        str(binding.get("statement_kind", "import")) == "import"
        for binding in _js_ts_named_import_bindings(source)
    ):
        return True
    if _js_ts_default_import_bindings(source):
        return True
    return bool(_js_ts_namespace_import_bindings(source))


def _js_ts_named_import_bindings(source: str) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?P<statement_kind>import|export)\s+(?:type\s+)?\{(?P<specifiers>[^}]+)\}\s*from\s*[\"'](?P<module>[^\"']+)[\"']",
        re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(source):
        start_line, end_line = _self._line_span_from_offsets(source, match.start(), match.end())
        module_name = match.group("module").strip()
        specifiers = match.group("specifiers")
        statement_kind = match.group("statement_kind").strip()
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
                bindings.append({
                    "module": module_name,
                    "imported": imported,
                    "local": local,
                    "statement_kind": statement_kind,
                    "start_line": start_line,
                    "end_line": end_line,
                })
    return bindings


def _js_ts_namespace_import_bindings(source: str) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    pattern = re.compile(
        r"""(?x)
        import\s+\*\s+as\s+(?P<local>[A-Za-z_][A-Za-z0-9_]*)\s+from\s*["'](?P<module>[^"']+)["']
        """
    )
    for match in pattern.finditer(source):
        bindings.append({
            "module": match.group("module").strip(),
            "local": match.group("local").strip(),
        })
    return bindings


def _js_ts_default_import_bindings(source: str) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    pattern = re.compile(
        r"""(?x)
        import
        \s+
        (?!type\b)
        (?P<local>[A-Za-z_][A-Za-z0-9_]*)
        \s*
        (?:,\s*\{[^}]*\})?
        \s+from\s*["'](?P<module>[^"']+)["']
        """,
        re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(source):
        start_line, end_line = _self._line_span_from_offsets(source, match.start(), match.end())
        bindings.append({
            "module": match.group("module").strip(),
            "local": match.group("local").strip(),
            "start_line": start_line,
            "end_line": end_line,
        })
    return bindings


def _js_ts_repo_context(repo_root: Path | str | None) -> dict[str, Any]:
    normalized_root = _self._normalized_repo_root(repo_root)
    if normalized_root is None:
        return {
            "root": None,
            "tsconfig": {
                "exists": False,
                "base_url": None,
                "paths": [],
            },
            "re_export_cache": {},
        }
    cached = _self._get_repo_context_cache_entry(_self._JS_TS_REPO_CONTEXTS, str(normalized_root))
    if cached is not None:
        return cached
    return _self._prime_js_ts_repo_context(normalized_root)


def _js_ts_candidate_files(base: Path) -> list[Path]:
    # Fix B: called once per (importer, module) candidate lookup, and the same `base` string
    # recurs across many definition_file iterations in caller_scan -- route every resolve()
    # through the cached helper.
    normalized_base = Path(_resolved_path_str(str(base)))
    candidates: list[Path] = []
    if normalized_base.suffix in _self._JS_TS_SUFFIXES:
        candidates.append(normalized_base)
    else:
        candidates.extend(
            Path(_resolved_path_str(str(normalized_base.with_suffix(suffix))))
            for suffix in sorted(_self._JS_TS_SUFFIXES)
        )
        candidates.extend(
            Path(_resolved_path_str(str((normalized_base / "index").with_suffix(suffix))))
            for suffix in sorted(_self._JS_TS_SUFFIXES)
        )
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        current = str(candidate)
        if current not in seen:
            deduped.append(candidate)
            seen.add(current)
    return deduped


def _js_ts_module_candidates(
    importer_path: Path,
    module_name: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    if module_name.startswith("."):
        # Fix B: same (importer_path, module_name) pair recurs across many definition_file
        # iterations of caller_scan -- cache the resolve() by string.
        base = Path(_resolved_path_str(str(importer_path.parent / module_name)))
        return {
            "paths": _js_ts_candidate_files(base),
            "provenance": [],
            "confidence": 1.0,
        }

    context = _js_ts_repo_context(repo_root)
    tsconfig = context.get("tsconfig", {})
    base_dir_str = str(
        tsconfig.get("base_url")
        or context.get("root")
        or _resolved_path_str(str(importer_path.parent))
    )
    base_dir = Path(_resolved_path_str(base_dir_str))

    for current in tsconfig.get("paths", []):
        pattern = str(current.get("pattern", ""))
        targets = [str(target) for target in current.get("targets", []) if target]
        for target in targets:
            expanded = _self._expand_js_ts_tsconfig_target(module_name, pattern, target)
            if expanded is None:
                continue
            return {
                "paths": _js_ts_candidate_files(Path(_resolved_path_str(str(base_dir / expanded)))),
                "provenance": ["tsconfig-path-alias"],
                "confidence": 0.88,
            }

    if tsconfig.get("base_url"):
        return {
            "paths": _js_ts_candidate_files(Path(_resolved_path_str(str(base_dir / module_name)))),
            "provenance": ["tsconfig-base-url"],
            "confidence": 0.76,
        }

    return {"paths": [], "provenance": [], "confidence": 0.0}


def _js_ts_module_match_details(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    candidate_info = _js_ts_module_candidates(importer_path, module_name, repo_root)
    # Fix B: definition_path is constant for the entire outer symbol scan across every
    # candidate/importer pair -- avoid re-resolving it on every call.
    resolved_definition = _resolved_path_str(definition_path)
    if any(str(candidate) == resolved_definition for candidate in candidate_info["paths"]):
        return {
            "matched": True,
            "provenance": list(candidate_info["provenance"]),
            "confidence": float(candidate_info["confidence"] or 1.0),
        }

    if not module_name.startswith(".") and _self._module_path_matches_definition(
        module_name, definition_path
    ):
        return {
            "matched": True,
            "provenance": ["partial-resolution"],
            "confidence": 0.2,
        }

    return {"matched": False, "provenance": [], "confidence": 0.0}


def _js_ts_symbol_names(path: Path) -> set[str]:
    symbols = _js_ts_parser_symbols(path)
    if not symbols:
        _, symbols = _self._regex_imports_and_symbols(path)
    return {str(symbol.get("name", "")) for symbol in symbols if symbol.get("name")}


def _js_ts_default_export_name(source: str, path: Path) -> str | None:
    direct_patterns = [
        re.compile(
            r"export\s+default\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
            re.MULTILINE,
        ),
        re.compile(r"export\s+default\s+class\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
        re.compile(r"export\s+default\s+([A-Za-z_][A-Za-z0-9_]*)\s*;", re.MULTILINE),
    ]
    for pattern in direct_patterns:
        match = pattern.search(source)
        if not match:
            continue
        candidate = match.group(1).strip()
        if candidate in _js_ts_symbol_names(path):
            return candidate
    return None


def _js_ts_resolve_exported_symbol(
    module_path: Path,
    exported_name: str,
    repo_root: Path | str | None = None,
    *,
    _depth: int = 0,
    _visited: set[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    normalized_root = _self._normalized_repo_root(repo_root)
    # Fix B: this resolve() runs BEFORE the re_export_cache lookup below, so an uncached
    # resolve() here defeats that cache's purpose on repeat calls for the same module path.
    normalized_module = Path(_resolved_path_str(str(module_path.expanduser())))
    if normalized_module.suffix not in _self._JS_TS_SUFFIXES:
        return None

    context = _js_ts_repo_context(normalized_root)
    cache_key = (str(normalized_module), exported_name)
    cached = context["re_export_cache"].get(cache_key)
    if cached is not None:
        return dict(cached) if isinstance(cached, dict) else None

    visited = set() if _visited is None else set(_visited)
    if _depth >= 5 or cache_key in visited:
        context["re_export_cache"][cache_key] = None
        return None
    visited.add(cache_key)

    try:
        source = normalized_module.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        context["re_export_cache"][cache_key] = None
        return None

    if exported_name == "default":
        direct_default = _js_ts_default_export_name(source, normalized_module)
        if direct_default:
            result = {
                "symbol": direct_default,
                "definition_file": str(normalized_module),
                "provenance": ["default-import"],
                "confidence": 0.95,
            }
            context["re_export_cache"][cache_key] = dict(result)
            return result
    elif exported_name in _js_ts_symbol_names(normalized_module):
        result = {
            "symbol": exported_name,
            "definition_file": str(normalized_module),
            "provenance": [],
            "confidence": 0.95,
        }
        context["re_export_cache"][cache_key] = dict(result)
        return result

    for binding in _js_ts_named_import_bindings(source):
        if (
            str(binding.get("statement_kind", "")) != "export"
            or str(binding.get("local", "")) != exported_name
        ):
            continue
        candidate_info = _js_ts_module_candidates(
            normalized_module,
            str(binding.get("module", "")),
            normalized_root,
        )
        for candidate in candidate_info["paths"]:
            nested = _js_ts_resolve_exported_symbol(
                candidate,
                str(binding.get("imported", "")),
                normalized_root,
                _depth=_depth + 1,
                _visited=visited,
            )
            if nested is None:
                continue
            provenance = _self._dedupe_labels([
                *list(candidate_info.get("provenance", [])),
                *list(nested.get("provenance", [])),
                "re-export-chain",
            ])
            confidence = float(nested.get("confidence", 0.2))
            if float(candidate_info.get("confidence", 0.0)) > 0.0:
                confidence = min(confidence, float(candidate_info["confidence"]))
            result = {
                "symbol": str(nested.get("symbol", exported_name)),
                "definition_file": str(nested.get("definition_file", normalized_module)),
                "provenance": provenance,
                "confidence": round(confidence, 3),
            }
            context["re_export_cache"][cache_key] = dict(result)
            return result

    context["re_export_cache"][cache_key] = None
    return None


def _js_ts_resolve_imported_symbol(
    importer_path: Path,
    module_name: str,
    imported_name: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    candidate_info = _js_ts_module_candidates(importer_path, module_name, repo_root)
    for candidate in candidate_info["paths"]:
        resolved = _js_ts_resolve_exported_symbol(candidate, imported_name, repo_root)
        if resolved is None:
            continue
        provenance = _self._dedupe_labels([
            *list(candidate_info.get("provenance", [])),
            *list(resolved.get("provenance", [])),
        ])
        confidence = float(resolved.get("confidence", 0.2))
        if float(candidate_info.get("confidence", 0.0)) > 0.0:
            confidence = min(confidence, float(candidate_info["confidence"]))
        return {
            "symbol": str(resolved.get("symbol", imported_name)),
            "definition_file": str(resolved.get("definition_file", candidate)),
            "provenance": provenance,
            "confidence": round(confidence, 3),
        }
    return None


def _js_ts_import_match_details(
    importer_path: Path,
    *,
    module_name: str,
    imported_name: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
    is_default: bool = False,
) -> dict[str, Any] | None:
    # Fix B: definition_path is constant across every binding/candidate iteration of the outer
    # caller_scan any()-loop -- avoid re-resolving it per call.
    resolved_definition = _resolved_path_str(definition_path)
    resolved = _js_ts_resolve_imported_symbol(
        importer_path,
        module_name,
        "default" if is_default else imported_name,
        repo_root,
    )
    if resolved is not None:
        if (
            str(resolved.get("definition_file")) == resolved_definition
            and str(resolved.get("symbol")) == symbol
        ):
            return {
                "provenance": list(resolved.get("provenance", [])),
                "confidence": float(resolved.get("confidence", 0.95)),
            }
        return None

    if is_default:
        return None

    details = _js_ts_module_match_details(importer_path, module_name, definition_path, repo_root)
    if details["matched"] and imported_name == symbol:
        return {
            "provenance": list(details.get("provenance", [])),
            "confidence": float(details.get("confidence", 0.95)),
        }
    return None


def _js_ts_module_matches_definition(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    return bool(
        _js_ts_module_match_details(
            importer_path,
            module_name,
            definition_path,
            repo_root,
        )["matched"]
    )


def _js_ts_file_imports_symbol_from_definition(
    file_path: Path,
    source: str,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    bindings = _js_ts_named_import_bindings(source)
    default_bindings = _js_ts_default_import_bindings(source)
    namespace_bindings = _js_ts_namespace_import_bindings(source)
    return (
        any(
            _js_ts_import_match_details(
                file_path,
                module_name=str(binding["module"]),
                imported_name=str(binding["imported"]),
                symbol=symbol,
                definition_path=definition_path,
                repo_root=repo_root,
            )
            is not None
            for binding in bindings
            if str(binding.get("statement_kind", "import")) == "import"
        )
        or any(
            _js_ts_import_match_details(
                file_path,
                module_name=str(binding["module"]),
                imported_name="default",
                symbol=symbol,
                definition_path=definition_path,
                repo_root=repo_root,
                is_default=True,
            )
            is not None
            for binding in default_bindings
        )
        or any(
            _js_ts_module_matches_definition(
                file_path,
                binding["module"],
                definition_path,
                repo_root,
            )
            for binding in namespace_bindings
        )
    )


def _js_ts_import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    try:
        source = _self._read_source_text_cached(str(file_path))
    except (OSError, UnicodeDecodeError):
        return None

    # PERF increment 1 / read site 5 (Fable-designed, the "surprise 5th" site): this used to
    # re-read + re-parse the file on every (file, symbol, definition) pair -- edit-plan seeding
    # and _build_import_graph_consumers_from_map call it once per definition_file, profiled at
    # ~26% of edit_plan wall time. Share the parse product with every other JS/TS extractor via
    # the same (path, mtime, size)-keyed cache instead of parsing locally.
    parsed = _self._parsed_source_and_tree(str(file_path))
    if parsed is not None:
        _parsed_source, _source_bytes, tree = parsed
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type == "import_statement":
                statement = source[node.start_byte : node.end_byte]
                for binding in _js_ts_default_import_bindings(statement):
                    if (
                        _js_ts_import_match_details(
                            file_path,
                            module_name=str(binding.get("module", "")),
                            imported_name="default",
                            symbol=symbol,
                            definition_path=definition_path,
                            repo_root=repo_root,
                            is_default=True,
                        )
                        is not None
                    ):
                        return {
                            "start_line": int(node.start_point[0] + 1),
                            "end_line": int(node.end_point[0] + 1),
                            "module": str(binding.get("module", "")),
                            "provenance": "parser-backed",
                        }
                for binding in _js_ts_named_import_bindings(statement):
                    if (
                        str(binding.get("statement_kind", "import")) == "import"
                        and _js_ts_import_match_details(
                            file_path,
                            module_name=str(binding.get("module", "")),
                            imported_name=str(binding.get("imported", "")),
                            symbol=symbol,
                            definition_path=definition_path,
                            repo_root=repo_root,
                        )
                        is not None
                    ):
                        return {
                            "start_line": int(node.start_point[0] + 1),
                            "end_line": int(node.end_point[0] + 1),
                            "module": str(binding.get("module", "")),
                            "provenance": "parser-backed",
                        }
            stack.extend(reversed(node.children))

    for binding in _js_ts_default_import_bindings(source):
        if (
            _js_ts_import_match_details(
                file_path,
                module_name=str(binding.get("module", "")),
                imported_name="default",
                symbol=symbol,
                definition_path=definition_path,
                repo_root=repo_root,
                is_default=True,
            )
            is not None
        ):
            return {
                "start_line": int(binding.get("start_line", 0)),
                "end_line": int(binding.get("end_line", binding.get("start_line", 0))),
                "module": str(binding.get("module", "")),
                "provenance": "heuristic",
            }

    for binding in _js_ts_named_import_bindings(source):
        if (
            str(binding.get("statement_kind", "import")) == "import"
            and _js_ts_import_match_details(
                file_path,
                module_name=str(binding.get("module", "")),
                imported_name=str(binding.get("imported", "")),
                symbol=symbol,
                definition_path=definition_path,
                repo_root=repo_root,
            )
            is not None
        ):
            return {
                "start_line": int(binding.get("start_line", 0)),
                "end_line": int(binding.get("end_line", binding.get("start_line", 0))),
                "module": str(binding.get("module", "")),
                "provenance": "heuristic",
            }
    return None


def _js_ts_dynamic_import_hit(line: str) -> tuple[str, bool] | None:
    """Detect a dynamic ``import(...)`` call or a ``require(...)`` call NOT already covered by
    the assignment-anchored static regexes above (bare ``require("x");`` with no assignment,
    ``require(...)``/``import(...)`` used as a sub-expression, or either call form given a
    non-literal argument).

    Returns ``(module, dynamic_unresolved)`` -- ``module`` is ``""`` when the argument isn't a
    static string literal (a variable, template literal, or expression), and
    ``dynamic_unresolved`` is ``True`` in that case -- or ``None`` when neither call form is
    present on this line.

    #93 SUB-1 recall fix: this is ADDITIVE to the static regexes, never a replacement -- callers
    only consult this after their own assignment-anchored match comes back empty, so a plain
    ``const x = require("y")`` line is still reported exactly once (via the static path), not
    twice. Known limitation (accepted, same "precision over guessing" posture as the rest of
    this file's regex heuristics): a fully INDIRECT alias -- `const req = require; req("y");` --
    is not traced; there is no literal `require(`/`import(` call shape on the second line for
    this to match.
    """
    literal_match = re.search(r'\b(?:import|require)\s*\(\s*["\']([^"\']+)["\']\s*\)', line)
    if literal_match:
        return literal_match.group(1), False
    if re.search(r"\b(?:import|require)\s*\(", line):
        return "", True
    return None


def _js_ts_symbol_name_node(node: Any) -> Any | None:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return name_node
    for child in node.children:
        if child.type in {"identifier", "property_identifier", "private_property_identifier"}:
            return child
    return None


def _js_ts_parser_symbols(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _self._JS_TS_SUFFIXES:
        return []

    parsed = _self._parsed_source_and_tree(str(path))
    if parsed is None:
        return []
    _source, source_bytes, tree = parsed
    symbols: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _self._tree_sitter_node_text(source_bytes, node)

    kind_by_node_type = {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    }

    def _walk(node: Any) -> None:
        if node.type in kind_by_node_type:
            name_node = _js_ts_symbol_name_node(node)
            if name_node is not None:
                name = _node_text(name_node)
                if _self._is_clean_symbol_name(name):
                    symbols.append(
                        _self._symbol_record(
                            name=name,
                            kind=kind_by_node_type[node.type],
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


def _js_ts_references_and_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix not in _self._JS_TS_SUFFIXES:
        return [], []

    try:
        source = _self._read_source_text_cached(str(path))
    except (OSError, UnicodeDecodeError):
        return [], []

    # PERF increment 1 / Section B (Fable-designed): binding resolution only needs the source
    # TEXT (not a parse tree), so it now runs BEFORE the parse -- letting a symbol-absent file
    # skip tree-sitter parsing entirely below (the refs loop that follows has no prefilter,
    # unlike the caller-scan literal check, so this is the biggest single payoff in this file).
    # TRAP (do not reorder): this pass must run first so a renamed re-export
    # (`export {x as y} from "./mod"`) still triggers a parse even though the literal target
    # symbol name never appears in THIS file's text -- alias_names ends up non-empty and the
    # early-exit below correctly falls through to parsing. See test_js_ts_advanced_resolution.py
    # ::test_aliased_re_export_chain_resolves_to_original_definition.
    alias_resolution_by_name: dict[str, dict[str, Any]] = {}
    for binding in _js_ts_named_import_bindings(source):
        if str(binding.get("statement_kind", "import")) != "import":
            continue
        resolved_import = _js_ts_resolve_imported_symbol(
            path,
            str(binding.get("module", "")),
            str(binding.get("imported", "")),
            repo_root,
        )
        if resolved_import is not None:
            if str(resolved_import.get("symbol")) != symbol:
                continue
            alias_resolution_by_name[str(binding.get("local", ""))] = dict(resolved_import)
            continue
        if str(binding.get("imported", "")) == symbol:
            alias_resolution_by_name[str(binding.get("local", ""))] = {
                "provenance": [],
                "confidence": 0.95,
            }
    for binding in _js_ts_default_import_bindings(source):
        resolved_import = _js_ts_resolve_imported_symbol(
            path,
            str(binding.get("module", "")),
            "default",
            repo_root,
        )
        if resolved_import is None or str(resolved_import.get("symbol")) != symbol:
            continue
        alias_resolution_by_name[str(binding.get("local", ""))] = dict(resolved_import)
    alias_names = {name for name in alias_resolution_by_name if name}

    if symbol not in source and not alias_names:
        return [], []

    parsed = _self._parsed_source_and_tree(str(path))
    if parsed is None:
        return [], []
    parsed_source, source_bytes, tree = parsed
    # `lines` MUST come from the SAME read as `source_bytes`/`tree` (all three from the single
    # `_parsed_source_and_tree` product), NOT from the earlier `source` text read above: the two are
    # independent (path, mtime, size)-keyed cache lookups, so a file edited between them would leave
    # tree node line-indices (from the parse) indexing into stale `lines` -> wrong reported line
    # content / IndexError. The pre-parse text read keeps using `source` (a cheap heuristic gate).
    lines = parsed_source.splitlines()
    references: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _self._tree_sitter_node_text(source_bytes, node)

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    def _is_definition_identifier(node: Any) -> bool:
        parent = node.parent
        if parent is None:
            return False
        if _self._node_has_ancestor_type(node, {"import_statement"}):
            return True
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
                alias_reference_resolution = (
                    alias_resolution_by_name.get(node_text) if node_type == "identifier" else None
                )
                try:
                    ref_kind = _self._js_ts_classify_ref_kind(node)
                except Exception:
                    # F20: a classifier bug must only default THIS row to "value", never drop
                    # every reference in the file -- classify-only, so a failure here can never
                    # be allowed to look like a fail-closed backend error.
                    ref_kind = "value"
                references.append({
                    "name": symbol,
                    "kind": "reference",
                    "ref_kind": ref_kind,
                    "file": str(path),
                    "line": node.start_point[0] + 1,
                    "text": _line_text(node),
                    **(
                        {
                            "resolution_provenance": list(
                                alias_reference_resolution.get("provenance", [])
                            ),
                            "resolution_confidence": float(
                                alias_reference_resolution.get("confidence", 0.95)
                            ),
                        }
                        if alias_reference_resolution
                        else {}
                    ),
                })
        elif node_type == "call_expression":
            function_node = node.child_by_field_name("function")
            matched = False
            alias_resolution: dict[str, Any] | None = None
            if function_node is not None:
                if function_node.type in {"identifier", "property_identifier"}:
                    function_name = _node_text(function_node)
                    matched = function_name == symbol or (
                        function_node.type == "identifier" and function_name in alias_names
                    )
                    if function_node.type == "identifier":
                        alias_resolution = alias_resolution_by_name.get(function_name)
                elif function_node.type == "member_expression":
                    property_node = function_node.child_by_field_name("property")
                    matched = bool(
                        property_node is not None and _node_text(property_node) == symbol
                    )
            if matched:
                calls.append({
                    "name": symbol,
                    "kind": "call",
                    "ref_kind": "call",
                    "file": str(path),
                    "line": node.start_point[0] + 1,
                    "text": _line_text(node),
                    **(
                        {
                            "resolution_provenance": list(alias_resolution.get("provenance", [])),
                            "resolution_confidence": float(
                                alias_resolution.get("confidence", 0.95)
                            ),
                        }
                        if alias_resolution
                        else {}
                    ),
                })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _js_ts_provider_alias_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    include_assignment_wrappers: bool = False,
) -> list[dict[str, Any]]:
    if path.suffix not in _self._JS_TS_SUFFIXES:
        return []

    try:
        source = _self._read_source_text_cached(str(path))
    except (OSError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    alias_resolution_by_name: dict[str, dict[str, Any]] = {}
    for binding in _js_ts_named_import_bindings(source):
        if str(binding.get("statement_kind", "import")) != "import":
            continue
        resolved_import = _js_ts_resolve_imported_symbol(
            path,
            str(binding.get("module", "")),
            str(binding.get("imported", "")),
            repo_root,
        )
        if resolved_import is not None:
            if str(resolved_import.get("symbol")) != symbol:
                continue
            alias_resolution_by_name[str(binding.get("local", ""))] = dict(resolved_import)
            continue
        if str(binding.get("imported", "")) == symbol:
            alias_resolution_by_name[str(binding.get("local", ""))] = {
                "provenance": [],
                "confidence": 0.95,
            }
    for binding in _js_ts_default_import_bindings(source):
        resolved_import = _js_ts_resolve_imported_symbol(
            path,
            str(binding.get("module", "")),
            "default",
            repo_root,
        )
        if resolved_import is None or str(resolved_import.get("symbol")) != symbol:
            continue
        alias_resolution_by_name[str(binding.get("local", ""))] = dict(resolved_import)
    alias_names = {name for name in alias_resolution_by_name if name}

    def _strip_js_ts_string_and_comment_noise(line: str) -> str:
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
            if char == "`":
                in_template = True
                cleaned.append(" ")
                continue
            cleaned.append(char)
        return "".join(cleaned)

    if include_assignment_wrappers:
        assignment_pattern = re.compile(
            r"\b(?:const|let|var)\s+(?P<local>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[A-Za-z_][A-Za-z0-9_]*)\b"
        )
        changed = True
        while changed:
            changed = False
            for line in lines:
                match = assignment_pattern.search(line)
                if match is None:
                    continue
                value_name = match.group("value")
                local_name = match.group("local")
                if value_name not in alias_names or local_name in alias_names:
                    continue
                alias_names.add(local_name)
                alias_resolution_by_name[local_name] = dict(
                    alias_resolution_by_name.get(value_name, {})
                )
                changed = True

    calls: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        sanitized_line = _strip_js_ts_string_and_comment_noise(line)
        for alias_name in sorted(alias_names):
            alias_match = re.search(rf"\b{re.escape(alias_name)}\s*\(", sanitized_line)
            if alias_match is None:
                continue
            # Task 326 (REOPENED): `\bNAME\s*\(` also matches `fn NAME(` / `function NAME(` -- a
            # DECLARATION, not a call site. The first fix for this put the guard in
            # `_regex_references_and_calls`, which sits LATER in the fallback chain and is never
            # reached on a wheel install (no tree-sitter grammar -> the AST arm returns nothing ->
            # THIS arm answers and stops the chain), so the defect shipped in v1.99.1 with green
            # unit tests. Reuses the one shared `_DEFINITION_KEYWORD_BEFORE_SYMBOL` constant
            # rather than a second regex, so the arms cannot drift apart again.
            if _self._DEFINITION_KEYWORD_BEFORE_SYMBOL.search(
                sanitized_line[: alias_match.start()]
            ):
                continue
            alias_resolution = alias_resolution_by_name.get(alias_name, {})
            calls.append({
                "name": symbol,
                "kind": "call",
                "file": str(path),
                "line": line_number,
                "end_line": line_number,
                "text": line,
                "alias": alias_name,
                "resolution_provenance": list(alias_resolution.get("provenance", [])),
                "resolution_confidence": float(alias_resolution.get("confidence", 0.95)),
            })
    calls.sort(
        key=lambda item: (item["file"], item["line"], str(item.get("alias", "")), item["text"])
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for call_entry in calls:
        key = (
            str(call_entry["file"]),
            int(call_entry["line"]),
            int(call_entry.get("end_line", call_entry["line"])),
            str(call_entry.get("alias", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(call_entry)
    return deduped


def _js_ts_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix not in _self._JS_TS_SUFFIXES:
        return []

    if path.suffix in {".ts", ".tsx"}:
        parser = _self._typescript_parser(tsx=path.suffix == ".tsx")
    else:
        parser = _self._javascript_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    sources: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _self._tree_sitter_node_text(source_bytes, node)

    kind_by_node_type = {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    }

    def _walk(node: Any) -> None:
        if node.type in kind_by_node_type:
            name_node = _js_ts_symbol_name_node(node)
            if name_node is not None and _node_text(name_node) == symbol:
                block = _node_text(node)
                if block and not block.endswith("\n"):
                    block = f"{block}\n"
                sources.append({
                    "name": symbol,
                    "kind": kind_by_node_type[node.type],
                    "file": str(path),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "source": block,
                })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def _js_ts_references_and_calls_for_registry(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # definition_dirs is part of the uniform registry adapter signature (Go F25); JS/TS
    # ignores it.
    return _js_ts_references_and_calls(path, symbol, repo_root)


def _js_ts_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix not in _self._JS_TS_SUFFIXES:
        return []
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    if file_size > _self._max_parse_bytes():
        return []
    try:
        lines = _self._read_source_text_cached(str(path)).splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        import_match = re.match(r'^\s*import\s+.*?from\s+["\']([^"\']+)["\']', line)
        export_from_match = re.match(r'^\s*export\s+.*?from\s+["\']([^"\']+)["\']', line)
        require_match = re.match(
            r"^\s*(?:const|let|var)\s+(?:\{[^}]+\}|[A-Za-z_][A-Za-z0-9_]*)"
            r'\s*=\s*require\(["\']([^"\']+)["\']\)',
            line,
        )
        if import_match:
            entries.append({"module": import_match.group(1), "line": line_number})
        if export_from_match:
            entries.append({"module": export_from_match.group(1), "line": line_number})
        if require_match:
            entries.append({"module": require_match.group(1), "line": line_number})
        else:
            # #93 SUB-1: `import("x")` call-form and a require(...) not shaped like the
            # assignment-anchored regex above (bare, chained, or a sub-expression argument).
            dynamic_hit = _js_ts_dynamic_import_hit(line)
            if dynamic_hit is not None:
                module, dynamic_unresolved = dynamic_hit
                entries.append({
                    "module": module,
                    "line": line_number,
                    "dynamic": True,
                    "dynamic_unresolved": dynamic_unresolved,
                })
    return entries


def _js_ast_omitted_relative_lines(block: str) -> set[int]:
    jsdoc_lines: set[int] = set()
    in_jsdoc = False

    for line_number, line in enumerate(block.splitlines(), start=1):
        stripped = line.strip()
        if not in_jsdoc:
            if not stripped.startswith("/**"):
                continue
            in_jsdoc = True

        if in_jsdoc:
            jsdoc_lines.add(line_number)
            if "*/" in stripped:
                in_jsdoc = False

    return jsdoc_lines


def _javascript_repo_fallback_command(package_manager: str) -> str:
    if package_manager == "pnpm":
        return "pnpm test"
    if package_manager == "yarn":
        return "yarn test"
    if package_manager == "bun":
        return "bun test"
    return "npm test"


def _javascript_runner_file_command(runner: str, relative_path: str) -> str:
    if runner == "vitest":
        return f"npx vitest run {relative_path}"
    if runner == "mocha":
        return f"npx mocha {relative_path}"
    return f"npx jest {relative_path}"


def _javascript_runner_specific_command(runner: str, relative_path: str, test_filter: str) -> str:
    quoted_filter = _self._shell_safe_arg(test_filter)
    if runner == "vitest":
        return f"npx vitest run {relative_path} -t {quoted_filter}"
    if runner == "mocha":
        return f"npx mocha {relative_path} --grep {quoted_filter}"
    return f"npx jest {relative_path} --testNamePattern {quoted_filter}"


def _javascript_runner_fallback_command(runner: str) -> str:
    if runner == "vitest":
        return "npx vitest run"
    if runner == "mocha":
        return "npx mocha"
    return "npx jest"


def _javascript_node_test_file_command(relative_path: str) -> str:
    return f"node --test {relative_path}"


def _javascript_test_script_uses_node_test(test_script: str | None) -> bool:
    normalized = (test_script or "").strip().lower()
    return bool(normalized) and "node" in normalized and "--test" in normalized
