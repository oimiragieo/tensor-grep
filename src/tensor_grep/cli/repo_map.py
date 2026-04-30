from __future__ import annotations

import ast
import json
import math
import os
import re
import threading
import time
import tomllib
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, NamedTuple

from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager, LSPTransportError
from tensor_grep.core.retrieval_lexical import score_term_overlap, split_terms

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
_TS_SUFFIXES = {".ts", ".tsx"}
_RUST_SUFFIXES = {".rs"}
_RENDER_PROFILES = {"full", "compact", "llm"}
_JS_RUNNER_ORDER = ("jest", "vitest", "mocha")
_DEFAULT_EDIT_PLAN_MAX_DEPTH = 3
_VALIDATION_RUNNER_SCAN_LIMIT = 512
_SOURCE_FALLBACK_SCAN_LIMIT = 8
_RUST_TEST_FN_PATTERN = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\b"
)
_JS_TS_REPO_CONTEXTS: dict[str, dict[str, Any]] = {}
_RUST_REPO_CONTEXTS: dict[str, dict[str, Any]] = {}
_EXTERNAL_LSP_PROVIDER_MANAGER = ExternalLSPProviderManager()


class _ValidationRunnerInfo(NamedTuple):
    has_python: bool
    has_rust: bool
    has_javascript: bool
    js_runners: tuple[str, ...]
    ts_runners: tuple[str, ...]
    js_fallback_command: str | None


class _ProfilePhase:
    def __init__(self, collector: _ProfileCollector, name: str) -> None:
        self._collector = collector
        self._name = name
        self._start = 0.0

    def __enter__(self) -> None:
        self._collector._push(self._name)
        self._start = time.perf_counter()
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> Literal[False]:
        try:
            elapsed = max(0.0, time.perf_counter() - self._start)
            self._collector._record(self._name, elapsed)
        finally:
            self._collector._pop()
        return False


class _ProfileCollector:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._local = threading.local()
        self._lock = threading.Lock()
        self._phase_totals: dict[str, float] = {}
        self._phase_calls: dict[str, int] = {}
        self._phase_order: list[str] = []

    def _stack(self) -> list[str]:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    def _push(self, name: str) -> None:
        self._stack().append(name)

    def _pop(self) -> None:
        stack = self._stack()
        if stack:
            stack.pop()

    def _record(self, name: str, elapsed: float) -> None:
        with self._lock:
            if name not in self._phase_totals:
                self._phase_totals[name] = 0.0
                self._phase_calls[name] = 0
                self._phase_order.append(name)
            self._phase_totals[name] += elapsed
            self._phase_calls[name] += 1

    def phase(self, name: str) -> _ProfilePhase | nullcontext[None]:
        if not self.enabled:
            return nullcontext()
        return _ProfilePhase(self, name)

    def result(self) -> dict[str, Any]:
        with self._lock:
            phases: list[dict[str, Any]] = [
                {
                    "name": name,
                    "elapsed_s": float(self._phase_totals[name]),
                    "calls": int(self._phase_calls[name]),
                }
                for name in self._phase_order
            ]
            total_elapsed = float(sum(self._phase_totals[name] for name in self._phase_order))
        breakdown_pct = (
            {
                str(phase["name"]): (float(phase["elapsed_s"]) / total_elapsed) * 100.0
                for phase in phases
            }
            if total_elapsed > 0.0
            else {}
        )
        return {
            "phases": phases,
            "total_elapsed_s": total_elapsed,
            "breakdown_pct": breakdown_pct,
        }


def _profiling_phase(
    collector: _ProfileCollector | None,
    name: str,
) -> _ProfilePhase | nullcontext[None]:
    if collector is None:
        return nullcontext()
    return collector.phase(name)


def _resolve_profiling_collector(
    *,
    profile: bool,
    collector: _ProfileCollector | None,
) -> _ProfileCollector | None:
    if collector is not None:
        return collector
    if profile:
        return _ProfileCollector()
    return None


def _attach_profiling(
    payload: dict[str, Any],
    collector: _ProfileCollector | None,
) -> dict[str, Any]:
    if collector is not None and collector.enabled:
        payload["_profiling"] = collector.result()
    else:
        payload.pop("_profiling", None)
    return payload


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


def _iter_repo_files(
    root: Path,
    *,
    max_files: int | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> list[Path]:
    with _profiling_phase(_profiling_collector, "file_walk"):
        if root.is_file():
            return [root.resolve()]

        files: list[Path] = []
        normalized_root = root.resolve()
        for current_root, dirnames, filenames in os.walk(normalized_root, topdown=True):
            dirnames[:] = sorted(current for current in dirnames if current not in _SKIP_DIR_NAMES)
            current_root_path = Path(current_root)
            for filename in sorted(filenames):
                current = current_root_path / filename
                try:
                    is_file = current.is_file()
                except OSError:
                    continue
                if not is_file:
                    continue
                files.append(current)
                if max_files is not None and len(files) >= max(1, max_files):
                    return files
        return files


def _repo_map_file_universe(repo_map: dict[str, Any]) -> list[Path]:
    root = Path(str(repo_map["path"])).expanduser().resolve()
    base = root if root.is_dir() else root.parent
    files: list[Path] = []
    seen: set[str] = set()
    for key in ("files", "tests"):
        for raw_path in repo_map.get(key, []) or []:
            current = Path(str(raw_path)).expanduser()
            if not current.is_absolute():
                current = base / current
            normalized = os.path.abspath(str(current))
            if normalized in seen:
                continue
            seen.add(normalized)
            files.append(Path(normalized))
    return files


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


def _symbol_navigation_provenance_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return "python-ast"
    if suffix in _JS_TS_SUFFIXES:
        return (
            "tree-sitter"
            if (
                _typescript_parser(tsx=suffix == ".tsx") is not None
                if suffix in _TS_SUFFIXES
                else _javascript_parser() is not None
            )
            else "regex-heuristic"
        )
    if suffix in _RUST_SUFFIXES:
        return "tree-sitter" if _rust_parser() is not None else "regex-heuristic"
    return "heuristic"


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


def _line_span_from_offsets(source: str, start_offset: int, end_offset: int) -> tuple[int, int]:
    start_line = source.count("\n", 0, max(0, start_offset)) + 1
    normalized_end = max(0, end_offset - 1)
    end_line = source.count("\n", 0, normalized_end) + 1
    return start_line, max(start_line, end_line)


def _js_ts_named_import_bindings(source: str) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    pattern = re.compile(
        r"(?P<statement_kind>import|export)\s+(?:type\s+)?\{(?P<specifiers>[^}]+)\}\s*from\s*[\"'](?P<module>[^\"']+)[\"']",
        re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(source):
        start_line, end_line = _line_span_from_offsets(source, match.start(), match.end())
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
                bindings.append(
                    {
                        "module": module_name,
                        "imported": imported,
                        "local": local,
                        "statement_kind": statement_kind,
                        "start_line": start_line,
                        "end_line": end_line,
                    }
                )
    return bindings


def _js_ts_namespace_import_bindings(source: str) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    pattern = re.compile(
        r"""(?x)
        import\s+\*\s+as\s+(?P<local>[A-Za-z_][A-Za-z0-9_]*)\s+from\s*["'](?P<module>[^"']+)["']
        """
    )
    for match in pattern.finditer(source):
        bindings.append(
            {
                "module": match.group("module").strip(),
                "local": match.group("local").strip(),
            }
        )
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
        start_line, end_line = _line_span_from_offsets(source, match.start(), match.end())
        bindings.append(
            {
                "module": match.group("module").strip(),
                "local": match.group("local").strip(),
                "start_line": start_line,
                "end_line": end_line,
            }
        )
    return bindings


def _normalized_repo_root(repo_root: Path | str | None) -> Path | None:
    if repo_root is None:
        return None
    return Path(str(repo_root)).expanduser().resolve()


def _dedupe_labels(labels: list[str]) -> list[str]:
    ordered: list[str] = []
    for label in labels:
        normalized = str(label).strip()
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _parse_js_ts_tsconfig(root: Path) -> dict[str, Any]:
    tsconfig_path = root / "tsconfig.json"
    payload: dict[str, Any] = {
        "exists": False,
        "base_url": None,
        "paths": [],
    }
    if not tsconfig_path.exists():
        return payload

    try:
        parsed = json.loads(tsconfig_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return payload

    compiler_options = parsed.get("compilerOptions", {})
    if not isinstance(compiler_options, dict):
        return payload

    payload["exists"] = True
    base_url = compiler_options.get("baseUrl")
    if isinstance(base_url, str) and base_url.strip():
        payload["base_url"] = str((root / base_url).resolve())

    raw_paths = compiler_options.get("paths", {})
    if isinstance(raw_paths, dict):
        normalized_paths: list[dict[str, Any]] = []
        for pattern, targets in raw_paths.items():
            if not isinstance(pattern, str) or not pattern.strip():
                continue
            if isinstance(targets, str):
                target_list = [targets]
            elif isinstance(targets, list):
                target_list = [str(target) for target in targets if isinstance(target, str)]
            else:
                continue
            if not target_list:
                continue
            normalized_paths.append({"pattern": pattern, "targets": target_list})
        payload["paths"] = normalized_paths
    return payload


def _prime_js_ts_repo_context(root: Path) -> dict[str, Any]:
    normalized_root = root.expanduser().resolve()
    context = {
        "root": str(normalized_root),
        "tsconfig": _parse_js_ts_tsconfig(normalized_root),
        "re_export_cache": {},
    }
    _JS_TS_REPO_CONTEXTS[str(normalized_root)] = context
    return context


def _js_ts_repo_context(repo_root: Path | str | None) -> dict[str, Any]:
    normalized_root = _normalized_repo_root(repo_root)
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
    cached = _JS_TS_REPO_CONTEXTS.get(str(normalized_root))
    if cached is not None:
        return cached
    return _prime_js_ts_repo_context(normalized_root)


def _js_ts_candidate_files(base: Path) -> list[Path]:
    normalized_base = base.resolve()
    candidates: list[Path] = []
    if normalized_base.suffix in _JS_TS_SUFFIXES:
        candidates.append(normalized_base)
    else:
        candidates.extend(
            (normalized_base.with_suffix(suffix)).resolve() for suffix in sorted(_JS_TS_SUFFIXES)
        )
        candidates.extend(
            ((normalized_base / "index").with_suffix(suffix)).resolve()
            for suffix in sorted(_JS_TS_SUFFIXES)
        )
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        current = str(candidate)
        if current not in seen:
            deduped.append(candidate)
            seen.add(current)
    return deduped


def _expand_js_ts_tsconfig_target(
    module_name: str,
    pattern: str,
    target: str,
) -> str | None:
    if "*" not in pattern:
        return target if module_name == pattern else None
    prefix, suffix = pattern.split("*", 1)
    if not module_name.startswith(prefix):
        return None
    if suffix and not module_name.endswith(suffix):
        return None
    token_end = len(module_name) - len(suffix) if suffix else len(module_name)
    token = module_name[len(prefix) : token_end]
    return target.replace("*", token, 1)


def _js_ts_module_candidates(
    importer_path: Path,
    module_name: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    if module_name.startswith("."):
        base = (importer_path.parent / module_name).resolve()
        return {
            "paths": _js_ts_candidate_files(base),
            "provenance": [],
            "confidence": 1.0,
        }

    context = _js_ts_repo_context(repo_root)
    tsconfig = context.get("tsconfig", {})
    base_dir = Path(
        str(tsconfig.get("base_url") or context.get("root") or importer_path.parent.resolve())
    ).resolve()

    for current in tsconfig.get("paths", []):
        pattern = str(current.get("pattern", ""))
        targets = [str(target) for target in current.get("targets", []) if target]
        for target in targets:
            expanded = _expand_js_ts_tsconfig_target(module_name, pattern, target)
            if expanded is None:
                continue
            return {
                "paths": _js_ts_candidate_files((base_dir / expanded).resolve()),
                "provenance": ["tsconfig-path-alias"],
                "confidence": 0.88,
            }

    if tsconfig.get("base_url"):
        return {
            "paths": _js_ts_candidate_files((base_dir / module_name).resolve()),
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
    resolved_definition = str(Path(definition_path).resolve())
    if any(str(candidate) == resolved_definition for candidate in candidate_info["paths"]):
        return {
            "matched": True,
            "provenance": list(candidate_info["provenance"]),
            "confidence": float(candidate_info["confidence"] or 1.0),
        }

    if not module_name.startswith(".") and _module_path_matches_definition(
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
        _, symbols = _regex_imports_and_symbols(path)
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
    normalized_root = _normalized_repo_root(repo_root)
    normalized_module = module_path.expanduser().resolve()
    if normalized_module.suffix not in _JS_TS_SUFFIXES:
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
            provenance = _dedupe_labels(
                [
                    *list(candidate_info.get("provenance", [])),
                    *list(nested.get("provenance", [])),
                    "re-export-chain",
                ]
            )
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
        provenance = _dedupe_labels(
            [
                *list(candidate_info.get("provenance", [])),
                *list(resolved.get("provenance", [])),
            ]
        )
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
    resolved_definition = str(Path(definition_path).resolve())
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
        start_line, end_line = _line_span_from_offsets(source, match.start(), match.end())
        for item in _flatten_rust_use_items(match.group(1)):
            normalized = item.strip()
            if not normalized:
                continue
            if normalized.endswith("::*"):
                bindings.append(
                    {
                        "module": normalized[:-3].strip(),
                        "wildcard": True,
                        "start_line": start_line,
                        "end_line": end_line,
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
                    "path": imported_path.strip(),
                    "wildcard": False,
                    "start_line": start_line,
                    "end_line": end_line,
                }
            )
    return bindings


def _rust_mod_declarations(source: str) -> list[str]:
    pattern = re.compile(
        r"^\s*(?:pub\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$",
        re.MULTILINE,
    )
    return [match.group(1).strip() for match in pattern.finditer(source)]


def _rust_module_base_dir(module_file: Path) -> Path:
    if module_file.name in {"lib.rs", "main.rs", "mod.rs"}:
        return module_file.parent.resolve()
    return (module_file.parent / module_file.stem).resolve()


def _rust_module_file_for_declaration(module_file: Path, module_name: str) -> Path | None:
    base_dir = _rust_module_base_dir(module_file)
    candidates = [
        (base_dir / f"{module_name}.rs").resolve(),
        (base_dir / module_name / "mod.rs").resolve(),
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _build_rust_module_tree(
    entry_path: Path,
    *,
    _prefix: tuple[str, ...] = (),
    _visited: set[str] | None = None,
) -> dict[str, str]:
    normalized_entry = entry_path.expanduser().resolve()
    visited = set() if _visited is None else set(_visited)
    current_key = str(normalized_entry)
    if current_key in visited:
        return {}
    visited.add(current_key)

    try:
        source = normalized_entry.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    module_tree: dict[str, str] = {}
    for module_name in _rust_mod_declarations(source):
        module_file = _rust_module_file_for_declaration(normalized_entry, module_name)
        if module_file is None:
            continue
        module_parts = (*_prefix, module_name)
        module_tree["::".join(module_parts)] = str(module_file)
        module_tree.update(
            _build_rust_module_tree(
                module_file,
                _prefix=module_parts,
                _visited=visited,
            )
        )
    return module_tree


def _normalize_rust_crate_name(name: str) -> str:
    return str(name).strip().replace("-", "_")


def _parse_rust_workspace_members(root: Path) -> dict[str, Any]:
    cargo_toml = root / "Cargo.toml"
    payload: dict[str, Any] = {
        "exists": False,
        "members": {},
    }
    if not cargo_toml.is_file():
        return payload

    try:
        parsed = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return payload

    workspace = parsed.get("workspace", {})
    if not isinstance(workspace, dict):
        return payload

    raw_members = workspace.get("members", [])
    member_patterns = [raw_members] if isinstance(raw_members, str) else raw_members
    if not isinstance(member_patterns, list):
        return payload

    members: dict[str, str] = {}
    for member_pattern in member_patterns:
        if not isinstance(member_pattern, str) or not member_pattern.strip():
            continue
        has_glob = any(token in member_pattern for token in {"*", "?", "["})
        member_dirs = (
            [candidate for candidate in root.glob(member_pattern) if candidate.is_dir()]
            if has_glob
            else [root / member_pattern]
        )
        for member_dir in member_dirs:
            normalized_dir = member_dir.expanduser().resolve()
            member_cargo = normalized_dir / "Cargo.toml"
            if not member_cargo.is_file():
                continue
            crate_name = _normalize_rust_crate_name(normalized_dir.name)
            try:
                member_parsed = tomllib.loads(member_cargo.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                member_parsed = {}
            package = member_parsed.get("package", {})
            if isinstance(package, dict):
                package_name = package.get("name")
                if isinstance(package_name, str) and package_name.strip():
                    crate_name = _normalize_rust_crate_name(package_name)
            entry_path = (normalized_dir / "src" / "lib.rs").resolve()
            if not entry_path.is_file():
                fallback_entry = (normalized_dir / "src" / "main.rs").resolve()
                if fallback_entry.is_file():
                    entry_path = fallback_entry
                else:
                    continue
            members[crate_name] = str(entry_path)
    if members:
        payload["exists"] = True
        payload["members"] = members
    return payload


def _prime_rust_repo_context(root: Path) -> dict[str, Any]:
    normalized_root = root.expanduser().resolve()
    context = {
        "root": str(normalized_root),
        "workspace": _parse_rust_workspace_members(normalized_root),
        "mod_tree_cache": {},
    }
    _RUST_REPO_CONTEXTS[str(normalized_root)] = context
    return context


def _rust_repo_context(repo_root: Path | str | None) -> dict[str, Any]:
    normalized_root = _normalized_repo_root(repo_root)
    if normalized_root is None:
        return {
            "root": None,
            "workspace": {
                "exists": False,
                "members": {},
            },
            "mod_tree_cache": {},
        }
    cached = _RUST_REPO_CONTEXTS.get(str(normalized_root))
    if cached is not None:
        return cached
    return _prime_rust_repo_context(normalized_root)


def _rust_crate_entry_for_path(path: Path) -> Path | None:
    normalized_path = path.expanduser().resolve()
    candidates = [normalized_path.parent, *normalized_path.parents]
    src_root = next((parent for parent in candidates if parent.name == "src"), None)
    if src_root is None:
        return None
    lib_path = (src_root / "lib.rs").resolve()
    if lib_path.is_file():
        return lib_path
    main_path = (src_root / "main.rs").resolve()
    if main_path.is_file():
        return main_path
    if normalized_path.name in {"lib.rs", "main.rs"} and normalized_path.parent == src_root:
        return normalized_path
    return None


def _rust_module_tree_for_entry(
    entry_path: Path,
    repo_root: Path | str | None = None,
) -> dict[str, str]:
    normalized_entry = entry_path.expanduser().resolve()
    context = _rust_repo_context(repo_root if repo_root is not None else normalized_entry.parent)
    cache_key = str(normalized_entry)
    cached = context["mod_tree_cache"].get(cache_key)
    if cached is not None:
        return dict(cached)
    module_tree = _build_rust_module_tree(normalized_entry)
    context["mod_tree_cache"][cache_key] = dict(module_tree)
    return module_tree


def _rust_module_path_for_definition(
    definition_path: Path,
    entry_path: Path,
) -> tuple[str, ...] | None:
    normalized_definition = definition_path.expanduser().resolve()
    normalized_entry = entry_path.expanduser().resolve()
    if normalized_definition == normalized_entry:
        return ()
    try:
        relative_path = normalized_definition.relative_to(normalized_entry.parent)
    except ValueError:
        return None
    if relative_path.suffix != ".rs":
        return None
    parts = list(relative_path.parts)
    if parts[-1] in {"lib.rs", "main.rs"}:
        return ()
    if parts[-1] == "mod.rs":
        parts = parts[:-1]
    else:
        parts[-1] = Path(parts[-1]).stem
    return tuple(part for part in parts if part)


def _rust_workspace_entry_for_crate(
    crate_name: str,
    repo_root: Path | str | None = None,
) -> Path | None:
    context = _rust_repo_context(repo_root)
    members = context.get("workspace", {}).get("members", {})
    member_path = members.get(_normalize_rust_crate_name(crate_name))
    return Path(str(member_path)).expanduser().resolve() if member_path else None


def _rust_module_tree_lookup(
    entry_path: Path,
    module_parts: list[str],
    repo_root: Path | str | None = None,
) -> Path | None:
    if not module_parts:
        return entry_path.expanduser().resolve()
    module_tree = _rust_module_tree_for_entry(entry_path, repo_root)
    resolved = module_tree.get("::".join(module_parts))
    return Path(resolved).expanduser().resolve() if resolved else None


def _rust_partial_candidate_paths(
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    normalized_root = _normalized_repo_root(repo_root)
    if normalized_root is None:
        return []

    normalized_definition = Path(definition_path).expanduser().resolve()
    inferred_candidates: list[dict[str, Any]] = []
    module_parts = [part.strip() for part in module_name.split("::") if part.strip()]
    if not module_parts or module_parts[0] in {"crate", "self", "super"}:
        return inferred_candidates

    external_entry = (normalized_root / module_parts[0] / "src" / "lib.rs").resolve()
    if not external_entry.is_file():
        fallback_entry = (normalized_root / module_parts[0] / "src" / "main.rs").resolve()
        if fallback_entry.is_file():
            external_entry = fallback_entry
        else:
            return inferred_candidates

    candidate_path = _rust_module_tree_lookup(external_entry, module_parts[1:], normalized_root)
    if candidate_path is None and not module_parts[1:]:
        candidate_path = external_entry
    if candidate_path is not None and candidate_path == normalized_definition:
        provenance = ["partial-resolution"]
        if module_parts[1:]:
            provenance.append("mod-declaration")
        inferred_candidates.append(
            {
                "path": str(candidate_path),
                "provenance": provenance,
                "confidence": 0.2,
            }
        )
    return inferred_candidates


def _rust_module_candidates(
    importer_path: Path,
    module_name: str,
    repo_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    parts = [part.strip() for part in module_name.split("::") if part.strip()]
    if not parts:
        return []

    normalized_root = _normalized_repo_root(repo_root)
    normalized_importer = importer_path.expanduser().resolve()
    candidates: list[dict[str, Any]] = []

    def _add_candidate(path: Path | None, provenance: list[str], confidence: float) -> None:
        if path is None:
            return
        resolved_path = str(path.expanduser().resolve())
        if any(str(current["path"]) == resolved_path for current in candidates):
            return
        candidates.append(
            {
                "path": resolved_path,
                "provenance": list(provenance),
                "confidence": float(confidence),
            }
        )

    crate_entry = _rust_crate_entry_for_path(normalized_importer)
    if parts[0] == "crate" and crate_entry is not None:
        _add_candidate(
            _rust_module_tree_lookup(crate_entry, parts[1:], normalized_root),
            ["mod-declaration"] if parts[1:] else [],
            0.95,
        )
    elif normalized_root is not None:
        workspace_entry = _rust_workspace_entry_for_crate(parts[0], normalized_root)
        if workspace_entry is not None:
            _add_candidate(
                _rust_module_tree_lookup(workspace_entry, parts[1:], normalized_root),
                ["workspace-crate", *(["mod-declaration"] if parts[1:] else [])],
                0.92,
            )

    start = normalized_importer.parent
    while start.name == "":
        start = start.parent

    heuristic_parts = list(parts)
    if heuristic_parts[0] == "crate":
        crate_root = next(
            (
                parent
                for parent in [normalized_importer.parent, *normalized_importer.parents]
                if parent.name == "src"
            ),
            None,
        )
        if crate_root is not None:
            start = crate_root.resolve()
        heuristic_parts = heuristic_parts[1:]
    else:
        while heuristic_parts and heuristic_parts[0] == "super":
            start = start.parent
            heuristic_parts = heuristic_parts[1:]
        if heuristic_parts and heuristic_parts[0] == "self":
            heuristic_parts = heuristic_parts[1:]

    if heuristic_parts:
        base = start.joinpath(*heuristic_parts).resolve()
        _add_candidate(base.with_suffix(".rs"), [], 1.0)
        _add_candidate(base / "mod.rs", [], 1.0)

    return candidates


def _rust_module_match_details(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    resolved_definition = str(Path(definition_path).expanduser().resolve())
    module_parts = [part.strip() for part in module_name.split("::") if part.strip()]
    for candidate in _rust_module_candidates(importer_path, module_name, repo_root):
        if str(candidate.get("path")) == resolved_definition:
            return {
                "matched": True,
                "provenance": list(candidate.get("provenance", [])),
                "confidence": float(candidate.get("confidence", 1.0)),
            }

    for candidate in _rust_partial_candidate_paths(module_name, definition_path, repo_root):
        if str(candidate.get("path")) == resolved_definition:
            return {
                "matched": True,
                "provenance": list(candidate.get("provenance", [])),
                "confidence": float(candidate.get("confidence", 0.2)),
            }

    if module_parts and module_parts[0] in {"crate", "self", "super"}:
        return {"matched": False, "provenance": [], "confidence": 0.0}
    if module_parts and _rust_workspace_entry_for_crate(module_parts[0], repo_root) is not None:
        return {"matched": False, "provenance": [], "confidence": 0.0}
    if _module_path_matches_definition(module_name, definition_path):
        return {
            "matched": True,
            "provenance": ["partial-resolution"],
            "confidence": 0.2,
        }
    return {"matched": False, "provenance": [], "confidence": 0.0}


def _rust_use_binding_match_details(
    importer_path: Path,
    binding: dict[str, Any],
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    imported_name = str(binding.get("imported", ""))
    local_name = str(binding.get("local", ""))
    definition_stem = Path(definition_path).with_suffix("").name.lower()
    if not (
        bool(binding.get("wildcard"))
        or imported_name.lower() == symbol.lower()
        or local_name.lower() == symbol.lower()
        or imported_name.lower() == definition_stem
    ):
        return None

    for module_name in [str(binding.get("module", "")), str(binding.get("path", ""))]:
        if not module_name:
            continue
        details = _rust_module_match_details(
            importer_path,
            module_name,
            definition_path,
            repo_root,
        )
        if details["matched"]:
            return {
                "provenance": list(details.get("provenance", [])),
                "confidence": float(details.get("confidence", 1.0)),
            }
    return None


def _extract_rust_impl_block(source: str, start_index: int) -> str:
    depth = 0
    block_start = -1
    for index in range(start_index, len(source)):
        current = source[index]
        if current == "{":
            if depth == 0:
                block_start = index + 1
            depth += 1
        elif current == "}":
            if depth == 0:
                break
            depth -= 1
            if depth == 0 and block_start >= 0:
                return source[block_start:index]
    return ""


@lru_cache(maxsize=256)
def _rust_impl_method_candidates(definition_path: str, symbol: str) -> tuple[str, ...]:
    path = Path(definition_path)
    if path.suffix not in _RUST_SUFFIXES:
        return ()
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()

    impl_pattern = re.compile(
        rf"\bimpl(?:\s*<[^{{>]*>)?\s+{re.escape(symbol)}(?:\s*<[^{{>]*>)?\s*\{{",
        re.MULTILINE,
    )
    method_pattern = re.compile(r"(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)")
    candidates: list[str] = []
    for match in impl_pattern.finditer(source):
        block = _extract_rust_impl_block(source, match.end() - 1)
        if not block:
            continue
        candidates.extend(method_match.group(1) for method_match in method_pattern.finditer(block))
    return tuple(dict.fromkeys(candidates))


@lru_cache(maxsize=256)
def _rust_impl_owner_type(definition_path: str, line_number: int) -> str | None:
    path = Path(definition_path)
    if path.suffix not in _RUST_SUFFIXES:
        return None
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    impl_pattern = re.compile(
        r"\bimpl(?:\s*<[^{}>]*>)?\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^{}>]*>)?\s*\{",
        re.MULTILINE,
    )
    for match in impl_pattern.finditer(source):
        start_line = source.count("\n", 0, match.start()) + 1
        block = _extract_rust_impl_block(source, match.end() - 1)
        if not block:
            continue
        end_index = match.end() - 1 + len(block) + 1
        end_line = source.count("\n", 0, end_index) + 1
        if start_line <= line_number <= end_line:
            return match.group(1)
    return None


@lru_cache(maxsize=512)
def _rust_symbol_reference_candidates(definition_path: str, symbol: str) -> tuple[str, ...]:
    candidates = [symbol]
    candidates.extend(_rust_impl_method_candidates(definition_path, symbol))
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def _rust_file_references_symbol_from_definition(
    file_path: Path,
    symbol: str,
    definition_path: str,
) -> bool:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    reference_candidates = _rust_symbol_reference_candidates(definition_path, symbol)
    if not reference_candidates:
        return False

    for candidate in reference_candidates:
        if re.search(rf"\b{re.escape(candidate)}\b", source):
            return True
        if re.search(rf"(?:\.|::){re.escape(candidate)}\s*\(", source):
            return True
    return False


def _rust_resolve_use_binding(
    importer_path: Path,
    binding: dict[str, Any],
    symbol: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    imported_name = str(binding.get("imported", ""))
    local_name = str(binding.get("local", ""))
    wildcard = bool(binding.get("wildcard"))

    module_names = [str(binding.get("module", "")), str(binding.get("path", ""))]
    for module_name in module_names:
        if not module_name:
            continue
        for candidate in _rust_module_candidates(importer_path, module_name, repo_root):
            if not str(candidate.get("path")):
                continue
            candidate_path = Path(str(candidate["path"]))
            if (
                wildcard
                or imported_name.lower() == symbol.lower()
                or local_name.lower() == symbol.lower()
            ):
                return {
                    "symbol": symbol,
                    "definition_file": str(candidate_path),
                    "provenance": list(candidate.get("provenance", [])),
                    "confidence": float(candidate.get("confidence", 1.0)),
                }
            try:
                candidate_source = candidate_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for nested_binding in _rust_use_bindings(candidate_source):
                nested_imported = str(nested_binding.get("imported", ""))
                nested_local = str(nested_binding.get("local", ""))
                if imported_name.lower() not in {nested_imported.lower(), nested_local.lower()}:
                    continue
                nested_resolved = _rust_resolve_use_binding(
                    candidate_path, nested_binding, symbol, repo_root
                )
                if nested_resolved is None:
                    continue
                return {
                    "symbol": symbol,
                    "definition_file": str(nested_resolved.get("definition_file", candidate_path)),
                    "provenance": list(candidate.get("provenance", []))
                    + list(nested_resolved.get("provenance", [])),
                    "confidence": min(
                        float(candidate.get("confidence", 1.0)),
                        float(nested_resolved.get("confidence", 1.0)),
                    ),
                }
    return None


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


def _rust_module_matches_definition(
    importer_path: Path,
    module_name: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    return bool(
        _rust_module_match_details(
            importer_path,
            module_name,
            definition_path,
            repo_root,
        )["matched"]
    )


def _file_imports_symbol_from_definition(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
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
                if any(
                    _module_path_matches_definition(alias.name, definition_path)
                    for alias in node.names
                ):
                    return True
            elif isinstance(node, ast.ImportFrom):
                if not node.module or not _module_path_matches_definition(
                    node.module, definition_path
                ):
                    continue
                if any(
                    alias.name in {"*", symbol} or alias.asname == symbol for alias in node.names
                ):
                    return True
        return False

    if file_path.suffix in _JS_TS_SUFFIXES:
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

    if file_path.suffix in _RUST_SUFFIXES:
        bindings = _rust_use_bindings(source)
        if any(
            _rust_use_binding_match_details(
                file_path,
                binding,
                symbol,
                definition_path,
                repo_root,
            )
            is not None
            for binding in bindings
        ):
            return True
        if _is_test_file(file_path):
            return _rust_file_references_symbol_from_definition(file_path, symbol, definition_path)
        return False

    return False


def _python_import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
) -> dict[str, Any] | None:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_path_matches_definition(alias.name, definition_path):
                    return {
                        "start_line": int(node.lineno),
                        "end_line": int(getattr(node, "end_lineno", node.lineno)),
                        "module": alias.name,
                        "provenance": "parser-backed",
                    }
        elif isinstance(node, ast.ImportFrom):
            if not node.module or not _module_path_matches_definition(node.module, definition_path):
                continue
            if any(alias.name in {"*", symbol} or alias.asname == symbol for alias in node.names):
                return {
                    "start_line": int(node.lineno),
                    "end_line": int(getattr(node, "end_lineno", node.lineno)),
                    "module": node.module,
                    "provenance": "parser-backed",
                }
    return None


def _js_ts_import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    parser = (
        _typescript_parser(tsx=file_path.suffix == ".tsx")
        if file_path.suffix in _TS_SUFFIXES
        else _javascript_parser()
    )
    if parser is not None:
        tree = parser.parse(source.encode("utf-8"))
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


def _rust_import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    for binding in _rust_use_bindings(source):
        if (
            _rust_use_binding_match_details(
                file_path,
                binding,
                symbol,
                definition_path,
                repo_root,
            )
            is None
        ):
            continue
        return {
            "start_line": int(binding.get("start_line", 0)),
            "end_line": int(binding.get("end_line", binding.get("start_line", 0))),
            "module": str(binding.get("module", "")) or str(binding.get("path", "")),
            "provenance": "heuristic",
        }
    return None


def _import_update_target(
    file_path: Path,
    symbol: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> dict[str, Any] | None:
    if str(file_path.resolve()) == str(Path(definition_path).resolve()):
        return None
    if file_path.suffix == ".py":
        return _python_import_update_target(file_path, symbol, definition_path)
    if file_path.suffix in _JS_TS_SUFFIXES:
        return _js_ts_import_update_target(file_path, symbol, definition_path, repo_root)
    if file_path.suffix in _RUST_SUFFIXES:
        return _rust_import_update_target(file_path, symbol, definition_path, repo_root)
    return None


def _preferred_definition_files(repo_map: dict[str, Any], symbol: str) -> list[str]:
    repo_root = Path(str(repo_map["path"])).resolve()
    definitions = [
        dict(current)
        for current in repo_map.get("symbols", [])
        if str(current.get("name")) == symbol
    ]
    definition_files = list(dict.fromkeys(str(current["file"]) for current in definitions))
    if len(definition_files) <= 1:
        return definition_files

    scores = dict.fromkeys(definition_files, 0)
    for current in _repo_map_file_universe(repo_map):
        current_path = str(current)
        if current_path in scores:
            continue
        for definition_file in definition_files:
            if _file_imports_symbol_from_definition(
                current,
                symbol,
                definition_file,
                repo_root,
            ):
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
    _profiling_collector: _ProfileCollector | None = None,
) -> list[str]:
    repo_root = Path(str(repo_map["path"])).resolve()
    tests = [str(current) for current in repo_map.get("tests", [])]
    caller_set = set(caller_files or [])
    symbol_candidates = [symbol]
    for definition_file in definition_files:
        definition_symbol = next(
            (
                current
                for current in repo_map.get("symbols", [])
                if str(current.get("file")) == str(definition_file)
                and str(current.get("name")) == symbol
            ),
            None,
        )
        if not definition_symbol:
            continue
        if str(definition_file).endswith(".rs"):
            owner_type = _rust_impl_owner_type(
                str(definition_file),
                int(definition_symbol.get("line", definition_symbol.get("start_line", 0)) or 0),
            )
            if owner_type and owner_type not in symbol_candidates:
                symbol_candidates.append(owner_type)
    if caller_files:
        source_files = list(
            dict.fromkeys([*(str(current) for current in caller_files or []), *definition_files])
        )
        all_files = [str(current) for current in repo_map.get("files", [])]
        imports_by_file = {
            str(entry["file"]): [str(item) for item in entry["imports"]]
            for entry in repo_map.get("imports", [])
        }
        reverse_importers = _reverse_importers(
            all_files,
            imports_by_file,
            _profiling_collector=_profiling_collector,
        )
        file_distances = _reverse_import_distances(
            source_files,
            all_files,
            imports_by_file,
            _profiling_collector=_profiling_collector,
        )
        graph_scores = _personalized_reverse_import_pagerank(
            source_files,
            all_files,
            reverse_importers,
            _profiling_collector=_profiling_collector,
        )
        file_scores: dict[str, int] = {}
        for current in source_files:
            file_scores[current] = max(file_scores.get(current, 0), 5)
        for current, depth in file_distances.items():
            file_scores[current] = max(file_scores.get(current, 0), max(1, 5 - int(depth)))

        test_matches = _context_tests(
            source_files,
            tests,
            _query_terms(symbol),
            imports_by_file,
            file_distances,
            graph_scores,
            file_scores,
            raw_query=symbol,
        )
        direct_definition_tests = []
        for current in tests:
            path = Path(current)
            if any(
                _file_imports_symbol_from_definition(
                    path,
                    candidate_symbol,
                    definition_file,
                    repo_root,
                )
                for candidate_symbol in symbol_candidates
                for definition_file in definition_files
            ):
                direct_definition_tests.append(current)

        ranked = [str(current["path"]) for current in test_matches]
        ordered: list[str] = []
        for current in [*direct_definition_tests, *ranked]:
            if current in caller_set and current not in ordered:
                ordered.append(current)
            elif current not in ordered:
                ordered.append(current)
        if ordered:
            return ordered
    related: list[str] = []
    for current in tests:
        if current in caller_set:
            related.append(current)
            continue
        path = Path(current)
        if any(
            _file_imports_symbol_from_definition(
                path,
                candidate_symbol,
                definition_file,
                repo_root,
            )
            for candidate_symbol in symbol_candidates
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
            class_match = re.match(
                r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)",
                line,
            )
            function_match = re.match(
                r"^\s*(?:export\s+)?(?:default\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
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


def _python_provider_alias_calls(path: Path, symbol: str) -> list[dict[str, Any]]:
    if path.suffix != ".py":
        return []

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    alias_names = {symbol}

    def _binding_name(value: ast.AST) -> str | None:
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            return value.attr
        return None

    def _assignment_targets(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            names: list[str] = []
            for current in target.elts:
                names.extend(_assignment_targets(current))
            return names
        return []

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for imported_alias in node.names:
                    imported_name = str(imported_alias.name).split(".")[-1]
                    if imported_name not in alias_names:
                        continue
                    local_name = imported_alias.asname or imported_name
                    if local_name and local_name not in alias_names:
                        alias_names.add(local_name)
                        changed = True
            elif isinstance(node, ast.Import):
                for imported_alias in node.names:
                    imported_name = str(imported_alias.name).split(".")[-1]
                    if imported_name not in alias_names:
                        continue
                    local_name = imported_alias.asname or imported_name
                    if local_name and local_name not in alias_names:
                        alias_names.add(local_name)
                        changed = True
            elif isinstance(node, ast.Assign):
                binding_name = _binding_name(node.value)
                if binding_name not in alias_names:
                    continue
                for target_name in (
                    _assignment_targets(node.targets[0])
                    if len(node.targets) == 1
                    else [name for target in node.targets for name in _assignment_targets(target)]
                ):
                    if target_name and target_name not in alias_names:
                        alias_names.add(target_name)
                        changed = True
            elif isinstance(node, ast.AnnAssign):
                binding_name = _binding_name(node.value) if node.value is not None else None
                if binding_name not in alias_names:
                    continue
                for target_name in _assignment_targets(node.target):
                    if target_name and target_name not in alias_names:
                        alias_names.add(target_name)
                        changed = True

    calls: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        alias_name = _binding_name(node.func)
        if alias_name not in alias_names:
            continue
        calls.append(
            {
                "name": symbol,
                "kind": "call",
                "file": str(path),
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "text": lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else "",
                "alias": alias_name,
            }
        )

    calls.sort(key=lambda item: (item["file"], item["line"], item.get("alias", ""), item["text"]))
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
            references.append(
                {
                    "name": symbol,
                    "kind": "reference",
                    "file": str(path),
                    "line": line_number,
                    "text": line,
                }
            )
        supports_template_strings = path.suffix in _JS_TS_SUFFIXES
        sanitized_line = _strip_line_string_and_comment_noise(
            line, supports_template_strings=supports_template_strings
        )
        if call_pattern.search(sanitized_line):
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
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
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

    def _node_text(node: Any) -> str:
        return source[node.start_byte : node.end_byte]

    def _line_text(node: Any) -> str:
        line_index = node.start_point[0]
        return lines[line_index] if 0 <= line_index < len(lines) else ""

    def _is_definition_identifier(node: Any) -> bool:
        parent = node.parent
        if parent is None:
            return False
        if _node_has_ancestor_type(node, {"import_statement"}):
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
                references.append(
                    {
                        "name": symbol,
                        "kind": "reference",
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
                    }
                )
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
                calls.append(
                    {
                        "name": symbol,
                        "kind": "call",
                        "file": str(path),
                        "line": node.start_point[0] + 1,
                        "text": _line_text(node),
                        **(
                            {
                                "resolution_provenance": list(
                                    alias_resolution.get("provenance", [])
                                ),
                                "resolution_confidence": float(
                                    alias_resolution.get("confidence", 0.95)
                                ),
                            }
                            if alias_resolution
                            else {}
                        ),
                    }
                )
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
    if path.suffix not in _JS_TS_SUFFIXES:
        return []

    try:
        source = path.read_text(encoding="utf-8")
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
            if not re.search(rf"\b{re.escape(alias_name)}\s*\(", sanitized_line):
                continue
            alias_resolution = alias_resolution_by_name.get(alias_name, {})
            calls.append(
                {
                    "name": symbol,
                    "kind": "call",
                    "file": str(path),
                    "line": line_number,
                    "end_line": line_number,
                    "text": line,
                    "alias": alias_name,
                    "resolution_provenance": list(alias_resolution.get("provenance", [])),
                    "resolution_confidence": float(alias_resolution.get("confidence", 0.95)),
                }
            )
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


def _rust_references_and_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
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
    local_name_resolution_by_name: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        resolved_import = _rust_resolve_use_binding(path, binding, symbol, repo_root)
        if resolved_import is None:
            continue
        local_name = str(binding.get("local", "") or binding.get("imported", "") or symbol)
        local_name_resolution_by_name[local_name] = dict(resolved_import)
        if bool(binding.get("wildcard")):
            local_name_resolution_by_name.setdefault(symbol, dict(resolved_import))
    local_names = {name for name in local_name_resolution_by_name if name}

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
            alias_resolution = local_name_resolution_by_name.get(node_text)
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
                        **(
                            {
                                "resolution_provenance": list(
                                    alias_resolution.get("provenance", [])
                                ),
                                "resolution_confidence": float(
                                    alias_resolution.get("confidence", 0.95)
                                ),
                            }
                            if alias_resolution
                            else {}
                        ),
                    }
                )
        elif node_type == "call_expression":
            function_node = node.child_by_field_name("function")
            matched = False
            call_resolution: dict[str, Any] | None = None
            if function_node is not None:
                if function_node.type == "identifier":
                    function_name = _node_text(function_node)
                    matched = function_name == symbol or function_name in local_names
                    call_resolution = local_name_resolution_by_name.get(function_name)
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
                        **(
                            {
                                "resolution_provenance": list(
                                    call_resolution.get("provenance", [])
                                ),
                                "resolution_confidence": float(
                                    call_resolution.get("confidence", 0.95)
                                ),
                            }
                            if call_resolution
                            else {}
                        ),
                    }
                )
                calls.append(
                    {
                        "name": symbol,
                        "kind": "call",
                        "file": str(path),
                        "line": node.start_point[0] + 1,
                        "text": _line_text(node),
                        **(
                            {
                                "resolution_provenance": list(
                                    call_resolution.get("provenance", [])
                                ),
                                "resolution_confidence": float(
                                    call_resolution.get("confidence", 0.95)
                                ),
                            }
                            if call_resolution
                            else {}
                        ),
                    }
                )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    references.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    calls.sort(key=lambda item: (item["file"], item["line"], item["text"]))
    return references, calls


def _rust_provider_alias_calls(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    include_assignment_wrappers: bool = False,
) -> list[dict[str, Any]]:
    if path.suffix not in _RUST_SUFFIXES:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    lines = source.splitlines()
    bindings = _rust_use_bindings(source)
    alias_resolution_by_name: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        resolved_import = _rust_resolve_use_binding(path, binding, symbol, repo_root)
        if resolved_import is None:
            continue
        local_name = str(binding.get("local", "") or binding.get("imported", "") or symbol)
        alias_resolution_by_name[local_name] = dict(resolved_import)
        if bool(binding.get("wildcard")):
            alias_resolution_by_name.setdefault(symbol, dict(resolved_import))
    alias_names = {name for name in alias_resolution_by_name if name}

    def _strip_rust_string_and_comment_noise(line: str) -> str:
        cleaned: list[str] = []
        in_single = False
        in_double = False
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
            cleaned.append(char)
        return "".join(cleaned)

    if include_assignment_wrappers:
        assignment_pattern = re.compile(
            r"\blet\s+(?:mut\s+)?(?P<local>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>[A-Za-z_][A-Za-z0-9_:]*)\b"
        )
        changed = True
        while changed:
            changed = False
            for line in lines:
                match = assignment_pattern.search(line)
                if match is None:
                    continue
                value_name = match.group("value").split("::")[-1]
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
        sanitized_line = _strip_rust_string_and_comment_noise(line)
        for alias_name in sorted(alias_names):
            if not re.search(rf"\b{re.escape(alias_name)}\s*\(", sanitized_line):
                continue
            alias_resolution = alias_resolution_by_name.get(alias_name, {})
            calls.append(
                {
                    "name": symbol,
                    "kind": "call",
                    "file": str(path),
                    "line": line_number,
                    "end_line": line_number,
                    "text": line,
                    "alias": alias_name,
                    "resolution_provenance": list(alias_resolution.get("provenance", [])),
                    "resolution_confidence": float(alias_resolution.get("confidence", 0.95)),
                }
            )
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
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == symbol
    ]
    symbol_nodes.sort(
        key=lambda current: (current.lineno, getattr(current, "end_lineno", current.lineno))
    )
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
                re.compile(rf"^\s*(?:export\s+)?class\s+({re.escape(symbol)})\b"),
            ),
            (
                "function",
                re.compile(rf"^\s*(?:export\s+)?function\s+({re.escape(symbol)})\b"),
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


def _imports_and_symbols_for_path(
    path: Path,
    *,
    _profiling_collector: _ProfileCollector | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    with _profiling_phase(_profiling_collector, "file_parse"):
        current_imports, current_symbols = _python_imports_and_symbols(path)
        if path.suffix in _JS_TS_SUFFIXES:
            current_imports, _ = _regex_imports_and_symbols(path)
            current_symbols = _js_ts_parser_symbols(path)
            if not current_symbols:
                _, current_symbols = _regex_imports_and_symbols(path)
        elif path.suffix in _RUST_SUFFIXES:
            current_imports, _ = _regex_imports_and_symbols(path)
            current_symbols = _rust_parser_symbols(path)
            if not current_symbols:
                _, current_symbols = _regex_imports_and_symbols(path)
        elif not current_imports and not current_symbols:
            current_imports, current_symbols = _regex_imports_and_symbols(path)
        return current_imports, current_symbols


def _group_symbols_by_file(symbols: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        current_path = str(symbol["file"])
        current_symbols = grouped.setdefault(current_path, [])
        current_symbols.append(dict(symbol))
    for current_symbols in grouped.values():
        current_symbols.sort(
            key=lambda item: (item["file"], item["line"], item["kind"], item["name"])
        )
    return grouped


def _normalized_changeset_paths(
    root: Path,
    changeset: dict[str, Any],
) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for key in ("added", "modified", "removed"):
        entries: list[str] = []
        for raw_path in changeset.get(key, []) or []:
            current_path = Path(str(raw_path)).expanduser()
            if not current_path.is_absolute():
                current_path = root / current_path
            entries.append(str(current_path.resolve()))
        normalized[key] = sorted(dict.fromkeys(entries))
    return normalized


def build_repo_map(
    path: str | Path = ".",
    *,
    max_repo_files: int | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")

    with _profiling_phase(_profiling_collector, "repo_map_build"):
        context_root = root if root.is_dir() else root.parent
        _prime_js_ts_repo_context(context_root)
        _prime_rust_repo_context(context_root)
        payload = _envelope(root)
        if _profiling_collector is None:
            all_files = _iter_repo_files(root, max_files=max_repo_files)
        else:
            all_files = _iter_repo_files(
                root,
                max_files=max_repo_files,
                _profiling_collector=_profiling_collector,
            )
        tests = [str(current) for current in all_files if _is_test_file(current)]
        source_files = [str(current) for current in all_files if not _is_test_file(current)]

        imports: list[dict[str, Any]] = []
        symbols: list[dict[str, Any]] = []
        for current in all_files:
            if _profiling_collector is None:
                current_imports, current_symbols = _imports_and_symbols_for_path(current)
            else:
                current_imports, current_symbols = _imports_and_symbols_for_path(
                    current,
                    _profiling_collector=_profiling_collector,
                )
            if current_imports:
                imports.append(
                    {
                        "file": str(current),
                        "imports": current_imports,
                        "provenance": _symbol_navigation_provenance_for_path(str(current)),
                    }
                )
            symbols.extend(current_symbols)

        payload["files"] = source_files
        payload["symbols"] = symbols
        payload["imports"] = imports
        payload["tests"] = tests
        payload["related_paths"] = sorted(dict.fromkeys([*source_files, *tests]))
    return _attach_profiling(payload, _profiling_collector)


def build_repo_map_incremental(
    previous_map: dict[str, Any],
    changeset: dict[str, Any],
) -> dict[str, Any]:
    root = Path(str(previous_map.get("path", "."))).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")

    context_root = root if root.is_dir() else root.parent
    _prime_js_ts_repo_context(context_root)
    _prime_rust_repo_context(context_root)
    normalized_changeset = _normalized_changeset_paths(root, changeset)
    changed_files = set(normalized_changeset["added"]) | set(normalized_changeset["modified"])
    previous_paths = {
        str(Path(str(current)).expanduser().resolve())
        for current in (
            list(previous_map.get("related_paths", []))
            or [*previous_map.get("files", []), *previous_map.get("tests", [])]
        )
    }
    previous_imports_by_file = {
        str(Path(str(entry["file"])).expanduser().resolve()): [
            str(item) for item in entry["imports"]
        ]
        for entry in previous_map.get("imports", [])
    }
    previous_symbols_by_file = _group_symbols_by_file(
        [dict(symbol) for symbol in previous_map.get("symbols", [])]
    )

    all_files = _iter_repo_files(root)
    current_files_by_path = {str(current): current for current in all_files}
    parsed_imports_by_file: dict[str, list[str]] = {}
    parsed_symbols_by_file: dict[str, list[dict[str, Any]]] = {}

    for current_path in sorted(changed_files | (set(current_files_by_path) - previous_paths)):
        path_obj = current_files_by_path.get(current_path)
        if path_obj is None:
            continue
        current_imports, current_symbols = _imports_and_symbols_for_path(path_obj)
        parsed_imports_by_file[current_path] = current_imports
        parsed_symbols_by_file[current_path] = current_symbols

    payload = _envelope(root)
    tests = [str(current) for current in all_files if _is_test_file(current)]
    source_files = [str(current) for current in all_files if not _is_test_file(current)]
    imports: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    for current in all_files:
        current_path = str(current)
        current_imports = (
            parsed_imports_by_file[current_path]
            if current_path in parsed_imports_by_file
            else previous_imports_by_file.get(current_path, [])
        )
        current_symbols = (
            parsed_symbols_by_file[current_path]
            if current_path in parsed_symbols_by_file
            else previous_symbols_by_file.get(current_path, [])
        )
        if current_imports:
            imports.append(
                {
                    "file": current_path,
                    "imports": current_imports,
                    "provenance": _symbol_navigation_provenance_for_path(current_path),
                }
            )
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
    return [token.lower() for token in re.findall(r"[A-Za-z0-9_]+", query) if token]


def _symbol_query_terms(query: str) -> list[str]:
    expanded_terms: list[str] = []
    for raw_token in re.findall(r"[A-Za-z0-9_]+", query):
        normalized = raw_token.lower()
        if normalized and normalized not in expanded_terms:
            expanded_terms.append(normalized)
        if "_" in raw_token:
            continue
        for term in split_terms(raw_token):
            if term not in expanded_terms:
                expanded_terms.append(term)
    return expanded_terms


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


def _score_file_source_terms(path: str, terms: list[str]) -> int:
    try:
        source = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return score_term_overlap(terms, source)


def _append_reason(reason_map: dict[str, list[str]], path: str, reason: str) -> None:
    current = reason_map.setdefault(path, [])
    if reason not in current:
        current.append(reason)


def _provenance_from_reasons(reasons: list[str]) -> list[str]:
    provenance: list[str] = []
    for reason in reasons:
        if reason in {"definition", "symbol", "import", "caller"}:
            label = "parser-backed"
        elif reason in {"import-graph", "graph-depth", "graph-centrality", "test-graph"}:
            label = "graph-derived"
        elif reason == "framework-pattern":
            label = "framework-pattern"
        elif reason in {"filename", "path"}:
            label = "filename-convention"
        else:
            label = "heuristic"
        if label not in provenance:
            provenance.append(label)
    return provenance or ["heuristic"]


def _span_rationale(symbol_name: str, reasons: list[str], depth: int) -> str:
    reason_text = ", ".join(reasons) if reasons else "ranked relevance"
    if "caller" in reasons:
        return f"Selected {symbol_name} because it directly calls the target symbol and sits at depth {depth}."
    if "definition" in reasons:
        return f"Selected {symbol_name} because it contains the defining behavior for the ranked edit target."
    return f"Selected {symbol_name} because it is connected by {reason_text} at depth {depth}."


def _ranking_quality(
    file_matches: list[dict[str, Any]],
    test_matches: list[dict[str, Any]],
) -> str:
    candidates = [
        int(current.get("score", 0))
        for current in [*file_matches[:2], *test_matches[:1]]
        if current
    ]
    if not candidates:
        return "weak"
    top = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else 0
    if top >= 10 and (top - runner_up) >= 3:
        return "strong"
    if top >= 5:
        return "moderate"
    return "weak"


def _coverage_summary(payload: dict[str, Any]) -> dict[str, Any]:
    coverage = dict(payload.get("coverage", {}))
    language_scope = str(coverage.get("language_scope", ""))
    evidence_counts = {
        "parser_backed": 0,
        "graph_derived": 0,
        "heuristic": 0,
    }

    def add_label(label: str) -> None:
        normalized = label.strip().lower()
        if normalized in {"python-ast", "tree-sitter", "parser-backed"}:
            evidence_counts["parser_backed"] += 1
        elif normalized == "graph-derived":
            evidence_counts["graph_derived"] += 1
        elif normalized in {
            "regex-heuristic",
            "heuristic",
            "filename-convention",
            "framework-pattern",
        }:
            evidence_counts["heuristic"] += 1

    def add_from_value(value: Any) -> None:
        if isinstance(value, str):
            add_label(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add_label(item)
        elif isinstance(value, dict):
            provenance = value.get("provenance")
            if provenance is not None:
                add_from_value(provenance)

    for group_name in (
        "file_matches",
        "test_matches",
        "imports",
        "definitions",
        "references",
        "callers",
    ):
        for item in payload.get(group_name, []):
            if isinstance(item, dict):
                add_from_value(item.get("provenance"))
                if group_name == "test_matches":
                    add_from_value(item.get("association"))
    for item in payload.get("caller_tree", []):
        if isinstance(item, dict):
            add_from_value(item.get("provenance"))
    total_evidence = sum(evidence_counts.values())
    if total_evidence > 0:
        evidence_ratios = {
            key: round(value / total_evidence, 6) for key, value in evidence_counts.items()
        }
    else:
        evidence_ratios = {
            "parser_backed": 0.0,
            "graph_derived": 0.0,
            "heuristic": 0.0,
        }
    return {
        "language_scope": language_scope,
        "parser_backed_fields": [
            "defs",
            "source",
            "refs",
            "callers",
        ],
        "heuristic_fields": [
            "test-matching",
            "graph-ranking",
        ],
        "graph_completeness": str(payload.get("graph_completeness", "moderate")),
        "evidence_counts": evidence_counts,
        "evidence_ratios": evidence_ratios,
    }


def _graph_trust_summary(caller_tree: list[dict[str, Any]]) -> dict[str, Any]:
    parser_backed = 0
    heuristic = 0
    provenance: list[str] = []
    max_confidence_rank = 0
    confidence_order = {"weak": 1, "moderate": 2, "strong": 3}
    for level in caller_tree:
        edge_summary = level.get("edge_summary", {})
        if not isinstance(edge_summary, dict):
            continue
        counts = edge_summary.get("evidence_counts", {})
        if isinstance(counts, dict):
            parser_backed += int(counts.get("parser_backed", 0))
            heuristic += int(counts.get("heuristic", 0))
        for label in edge_summary.get("provenance", []):
            if isinstance(label, str) and label not in provenance:
                provenance.append(label)
        confidence = str(edge_summary.get("confidence", "weak"))
        max_confidence_rank = max(max_confidence_rank, confidence_order.get(confidence, 1))
    rank_to_confidence = {value: key for key, value in confidence_order.items()}
    return {
        "edge_kind": "reverse-import",
        "confidence": rank_to_confidence.get(max_confidence_rank, "weak"),
        "provenance": provenance or ["graph-derived"],
        "depth_count": len(caller_tree),
        "evidence_counts": {
            "parser_backed": parser_backed,
            "heuristic": heuristic,
        },
    }


def _is_parser_backed_provenance(label: str) -> bool:
    return label.strip().lower() in {"python-ast", "tree-sitter", "parser-backed"}


def _is_heuristic_provenance(label: str) -> bool:
    return label.strip().lower() in {"regex-heuristic", "heuristic", "filename-convention"}


def _dependency_trust(
    repo_map: dict[str, Any],
    dependent_files: list[str],
) -> dict[str, Any]:
    import_provenance_by_file = {
        str(current["file"]): str(
            current.get("provenance", _symbol_navigation_provenance_for_path(str(current["file"])))
        )
        for current in repo_map.get("imports", [])
        if current.get("file")
    }
    parser_backed_count = 0
    heuristic_count = 0
    for current in dict.fromkeys(str(path) for path in dependent_files if path):
        provenance = import_provenance_by_file.get(current, "")
        if _is_parser_backed_provenance(provenance):
            parser_backed_count += 1
        elif _is_heuristic_provenance(provenance):
            heuristic_count += 1
    if parser_backed_count > 0 and heuristic_count == 0:
        import_resolution_quality = "strong"
    elif parser_backed_count > 0:
        import_resolution_quality = "moderate"
    else:
        import_resolution_quality = "weak"
    return {
        "import_resolution_quality": import_resolution_quality,
        "parser_backed_count": parser_backed_count,
        "heuristic_count": heuristic_count,
    }


def _plan_trust_summary(
    ranking_quality: str,
    coverage_summary: dict[str, Any],
    dependency_trust: dict[str, Any],
) -> str:
    evidence_counts = dict(coverage_summary.get("evidence_counts", {}))
    return (
        f"Ranking {ranking_quality}; coverage {coverage_summary.get('graph_completeness', 'moderate')} "
        f"(parser_backed={int(evidence_counts.get('parser_backed', 0))}, "
        f"graph_derived={int(evidence_counts.get('graph_derived', 0))}, "
        f"heuristic={int(evidence_counts.get('heuristic', 0))}); dependency trust "
        f"{dependency_trust.get('import_resolution_quality', 'weak')} "
        f"(parser_backed={int(dependency_trust.get('parser_backed_count', 0))}, "
        f"heuristic={int(dependency_trust.get('heuristic_count', 0))})."
    )


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
        "provenance": _provenance_from_reasons(reasons),
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
        current_symbols.sort(
            key=lambda item: (int(item["line"]), str(item["kind"]), str(item["name"]))
        )

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
    *,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, int]:
    with _profiling_phase(_profiling_collector, "graph_bfs"):
        distances: dict[str, int] = {}
        frontier = list(seed_files)
        seen = set(seed_files)

        for depth in range(1, 4):
            dependency_aliases = {
                current: _module_aliases_for_path(current) for current in frontier
            }
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
    *,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, set[str]]:
    with _profiling_phase(_profiling_collector, "graph_construction"):
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
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, float]:
    with _profiling_phase(_profiling_collector, "graph_pagerank"):
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
            updated = {current: (1.0 - alpha) * personalization[current] for current in all_files}
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
    *,
    raw_query: str | None = None,
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
        framework_bonus = _framework_test_pattern_bonus(current, terms, raw_query=raw_query)
        score += framework_bonus
        if framework_bonus > 0:
            reasons.append("framework-pattern")
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
            if import_bonus > 0 and stem in source_stems:
                edge_kind = "hybrid"
            elif import_bonus > 0:
                edge_kind = "import-graph"
            elif framework_bonus > 0:
                edge_kind = "framework-pattern"
            elif stem in source_stems:
                edge_kind = "filename"
            else:
                edge_kind = "path"
            if import_bonus > 0 or graph_score > 0.0:
                confidence = "strong" if import_bonus > 0 and graph_score > 0.0 else "moderate"
            elif framework_bonus > 0:
                confidence = "moderate" if framework_bonus >= 3 else "weak"
            else:
                confidence = "weak"
            match = _match_record(
                current, score, reasons, graph_score if graph_score > 0.0 else None
            )
            match["association"] = {
                "edge_kind": edge_kind,
                "confidence": confidence,
                "provenance": _provenance_from_reasons(reasons),
            }
            related.append(match)
    related.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return related


def _build_context_pack_from_map(
    payload: dict[str, Any],
    query: str,
    *,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    with _profiling_phase(_profiling_collector, "context_scoring"):
        terms = _query_terms(query)
        symbol_terms = _symbol_query_terms(query)
        all_symbols = [dict(symbol) for symbol in payload["symbols"]]
        imports_by_file = {
            str(entry["file"]): [str(item) for item in entry["imports"]]
            for entry in payload["imports"]
        }
        file_scores = {
            str(current): _score_file_path(str(current), terms) for current in payload["files"]
        }
        file_reasons: dict[str, list[str]] = {}
        for current, score in file_scores.items():
            if score > 0:
                _append_reason(file_reasons, current, "path")

        scored_symbols: list[dict[str, Any]] = []
        for symbol in payload["symbols"]:
            score = _score_symbol(symbol, symbol_terms)
            if score <= 0:
                continue
            scored_symbol = dict(symbol)
            scored_symbol["score"] = score
            current_path = str(scored_symbol["file"])
            _append_reason(file_reasons, current_path, "definition")
            if _score_text_terms(str(scored_symbol["name"]), symbol_terms) > 0:
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
            scored_entry["provenance"] = str(
                entry.get("provenance", _symbol_navigation_provenance_for_path(str(entry["file"])))
            )
            scored_entry["score"] = score
            scored_imports.append(scored_entry)
        scored_imports.sort(key=lambda item: (-int(item["score"]), str(item["file"])))
        for entry in scored_imports:
            current = str(entry["file"])
            file_scores[current] = file_scores.get(current, 0) + int(entry["score"]) * 2
            _append_reason(file_reasons, current, "import")

        source_candidates: list[str] = []
        if not scored_symbols and not scored_imports:
            source_candidates = [
                current
                for current, score in sorted(
                    file_scores.items(),
                    key=lambda item: (-int(item[1]), str(item[0])),
                )
                if score > 0
            ][:_SOURCE_FALLBACK_SCAN_LIMIT]
            if not source_candidates:
                source_candidates = [str(current) for current in payload["files"]]
        for current_path in source_candidates:
            source_score = _score_file_source_terms(current_path, terms)
            if source_score <= 0:
                continue
            file_scores[current_path] = file_scores.get(current_path, 0) + source_score * 2
            _append_reason(file_reasons, current_path, "source")

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
            dependency_seed_files = [
                path for path in payload["files"] if _score_file_path(str(path), terms) > 0
            ]

        all_files = [str(current) for current in payload["files"]]
        dependency_aliases = {
            current: _module_aliases_for_path(current) for current in dependency_seed_files
        }
        file_distances = _reverse_import_distances(
            dependency_seed_files,
            all_files,
            imports_by_file,
            _profiling_collector=_profiling_collector,
        )
        reverse_importers = _reverse_importers(
            all_files,
            imports_by_file,
            _profiling_collector=_profiling_collector,
        )
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
                file_scores[current_path] = file_scores.get(current_path, 0) + max(
                    1, 5 - file_distances[current_path]
                )
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
            _profiling_collector=_profiling_collector,
        )
        for current_path in set(dependency_seed_files) | set(file_distances):
            graph_score = graph_scores.get(current_path, 0.0)
            if graph_score <= 0.0:
                continue
            file_scores[current_path] = file_scores.get(current_path, 0) + max(
                1, round(graph_score * 10)
            )
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
            raw_query=query,
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
    payload["ranking_quality"] = _ranking_quality(file_matches, test_matches)
    payload["coverage_summary"] = _coverage_summary(payload)
    return payload


def build_context_pack(
    query: str,
    path: str | Path = ".",
    *,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    payload = build_repo_map(path, _profiling_collector=_profiling_collector)
    return build_context_pack_from_map(
        payload,
        query,
        _profiling_collector=_profiling_collector,
    )


def build_context_pack_json(query: str, path: str | Path = ".") -> str:
    return json.dumps(build_context_pack(query, path), indent=2)


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
                            "reasons": list(
                                test_matches_by_path.get(current, {}).get("reasons", [])
                            ),
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


def _estimate_tokens(
    text: str,
    *,
    _profiling_collector: _ProfileCollector | None = None,
) -> int:
    with _profiling_phase(_profiling_collector, "token_estimation"):
        if not text:
            return 0
        return max(1, math.ceil(len(text) / 3.5))


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
    kind_priority = {
        "summary": 0,
        "source": 1,
        "tests": 2,
    }.get(kind, 3)
    return (
        0 if primary_file is not None and path == primary_file else 1,
        -_render_part_score(part),
        kind_priority,
        path,
        str(part.get("symbol", "")),
        original_index,
    )


def _prepare_render_part(
    part: dict[str, Any],
    *,
    has_prior_content: bool,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any] | None:
    text = str(part.get("text", "")).strip()
    if not text:
        return None
    prefix = "" if not has_prior_content else "\n\n"
    chunk = f"{prefix}{text}"
    prepared = {key: value for key, value in part.items() if key != "text"}
    prepared["text"] = text
    prepared["chunk"] = chunk
    prepared["score"] = _render_part_score(part)
    prepared["token_estimate"] = _estimate_tokens(
        chunk,
        _profiling_collector=_profiling_collector,
    )
    return prepared


def _omitted_section_record(part: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": str(part.get("kind", "")),
        "file": _render_part_path(part),
        "symbol": part.get("symbol"),
        "score": int(part.get("score", 0)),
        "token_estimate": int(part.get("token_estimate", 0)),
    }


def _render_context_string_and_sections(
    payload: dict[str, Any],
    *,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> tuple[str, list[dict[str, Any]], bool, int, list[dict[str, Any]]]:
    with _profiling_phase(_profiling_collector, "render_packing"):
        max_render_chars = (
            max_render_chars if max_render_chars is not None and max_render_chars > 0 else None
        )
        resolved_max_tokens = max_tokens if max_tokens is not None else payload.get("max_tokens")
        max_tokens = (
            int(resolved_max_tokens)
            if resolved_max_tokens is not None and int(resolved_max_tokens) > 0
            else None
        )
        _ = model if model is not None else payload.get("model")
        primary_file = None
        edit_plan_seed = payload.get("edit_plan_seed")
        if isinstance(edit_plan_seed, dict):
            current_primary_file = edit_plan_seed.get("primary_file")
            if current_primary_file:
                primary_file = str(current_primary_file)
        if primary_file is None:
            files = payload.get("files", [])
            if isinstance(files, list) and files:
                primary_file = str(files[0])

        raw_parts = _render_context_parts(payload)
        query_parts: list[dict[str, Any]] = []
        ranked_parts: list[tuple[dict[str, Any], int]] = []
        for index, part in enumerate(raw_parts):
            if str(part.get("kind", "")) == "query":
                query_parts.append(part)
                continue
            ranked_parts.append((part, index))
        ranked_parts.sort(
            key=lambda item: _render_part_sort_key(
                item[0],
                primary_file=primary_file,
                original_index=item[1],
            )
        )
        parts = [*query_parts, *[part for part, _ in ranked_parts]]

        selected_parts: list[dict[str, Any]] = []
        omitted_parts: list[dict[str, Any]] = []
        non_query_selected = 0
        estimated_total_tokens = 0
        budget_exhausted = False

        for part in parts:
            prepared = _prepare_render_part(
                part,
                has_prior_content=bool(selected_parts),
                _profiling_collector=_profiling_collector,
            )
            if prepared is None:
                continue
            if str(prepared.get("kind", "")) == "query":
                selected_parts.append(prepared)
                estimated_total_tokens += int(prepared["token_estimate"])
                continue
            if budget_exhausted:
                omitted_parts.append(prepared)
                continue
            current_token_estimate = estimated_total_tokens + int(prepared["token_estimate"])
            if max_tokens is None or current_token_estimate <= max_tokens:
                selected_parts.append(prepared)
                estimated_total_tokens = current_token_estimate
                non_query_selected += 1
                continue
            if non_query_selected == 0 and estimated_total_tokens <= max_tokens:
                selected_parts.append(prepared)
                estimated_total_tokens = current_token_estimate
                non_query_selected += 1
                budget_exhausted = True
                continue
            omitted_parts.append(prepared)

        sections: list[dict[str, Any]] = []
        rendered_parts: list[str] = []
        offset = 0
        total_token_estimate = 0
        truncated = bool(omitted_parts)
        char_omitted_parts: list[dict[str, Any]] = []
        for index, part in enumerate(selected_parts):
            chunk = str(part["chunk"])
            if max_render_chars is not None and offset + len(chunk) > max_render_chars:
                remaining = max_render_chars - offset
                partially_rendered_current = False
                if offset == 0 and remaining > 0:
                    chunk = chunk[:remaining]
                    section_token_estimate = _estimate_tokens(
                        chunk,
                        _profiling_collector=_profiling_collector,
                    )
                    rendered_parts.append(chunk)
                    sections.append(
                        {
                            "kind": str(part["kind"]),
                            "start": offset,
                            "end": offset + len(chunk),
                            "token_estimate": section_token_estimate,
                            **{
                                key: value
                                for key, value in part.items()
                                if key not in {"text", "chunk", "score", "token_estimate"}
                            },
                        }
                    )
                    offset += len(chunk)
                    total_token_estimate += section_token_estimate
                    partially_rendered_current = True
                char_omitted_parts = selected_parts[
                    index + (1 if partially_rendered_current else 0) :
                ]
                truncated = True
                break
            rendered_parts.append(chunk)
            section_token_estimate = int(part["token_estimate"])
            sections.append(
                {
                    "kind": str(part["kind"]),
                    "start": offset,
                    "end": offset + len(chunk),
                    "token_estimate": section_token_estimate,
                    **{
                        key: value
                        for key, value in part.items()
                        if key not in {"text", "chunk", "score", "token_estimate"}
                    },
                }
            )
            offset += len(chunk)
            total_token_estimate += section_token_estimate
        omitted_sections = [
            _omitted_section_record(part) for part in [*char_omitted_parts, *omitted_parts]
        ]
        return (
            "".join(rendered_parts).rstrip(),
            sections,
            truncated,
            total_token_estimate,
            omitted_sections,
        )


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


def _python_ast_omitted_relative_lines(
    block: str, profile: str = "compact", strip_docstrings: bool = True
) -> tuple[set[int], set[int]]:
    try:
        tree = ast.parse(block)
    except SyntaxError:
        return set(), set()

    docstring_lines: set[int] = set()
    boilerplate_lines: set[int] = set()

    def _walk_and_strip(nodes: list[ast.stmt], parent: ast.AST | None = None) -> None:
        if not nodes:
            return

        # Check if the first node in this body is a docstring
        first = nodes[0]
        first_value = getattr(first, "value", None)
        is_docstring = (
            isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and isinstance(first, ast.Expr)
            and isinstance(first_value, ast.Constant)
            and isinstance(first_value.value, str)
        )

        if profile == "llm":
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Skeletonize: Keep signature, maybe keep docstring, omit rest of body
                start_omit = first.lineno
                if is_docstring:
                    if strip_docstrings:
                        first_end = getattr(first, "end_lineno", first.lineno)
                        docstring_lines.update(range(first.lineno, first_end + 1))
                        start_omit = first_end + 1
                    else:
                        start_omit = getattr(first, "end_lineno", first.lineno) + 1

                parent_end = getattr(parent, "end_lineno", parent.lineno)
                if parent_end >= start_omit:
                    boilerplate_lines.update(range(start_omit, parent_end + 1))
                return  # Don't recurse into omitted body

        # Compact profile stripping
        if is_docstring:
            end_lineno = getattr(first, "end_lineno", first.lineno)
            docstring_lines.update(range(first.lineno, end_lineno + 1))

        if profile == "compact":
            # Strip 'pass' if it's the only node or only node after docstring
            if len(nodes) == 1 or (len(nodes) == 2 and is_docstring):
                last = nodes[-1]
                if isinstance(last, ast.Pass):
                    end_lineno = getattr(last, "end_lineno", last.lineno)
                    boilerplate_lines.update(range(last.lineno, end_lineno + 1))

        # Recurse into all nodes to find nested functions/classes
        for node in nodes:
            node_body = getattr(node, "body", None)
            if node_body and isinstance(node_body, list):
                _walk_and_strip(node_body, parent=node)

    _walk_and_strip(tree.body)
    return docstring_lines, boilerplate_lines


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


def _ts_ast_omitted_relative_lines(block: str) -> tuple[set[int], set[int]]:
    jsdoc_lines = _js_ast_omitted_relative_lines(block)
    type_import_lines: set[int] = set()
    in_type_import = False

    for line_number, line in enumerate(block.splitlines(), start=1):
        stripped = line.strip()
        if not in_type_import:
            if not stripped.startswith("import type"):
                continue
            in_type_import = True

        type_import_lines.add(line_number)
        if ";" in stripped:
            in_type_import = False

    return jsdoc_lines, type_import_lines


def _rust_ast_omitted_relative_lines(block: str) -> tuple[set[int], set[int]]:
    doc_comment_lines: set[int] = set()
    attribute_lines: set[int] = set()
    in_attribute = False
    attribute_bracket_balance = 0

    for line_number, line in enumerate(block.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("///") or stripped.startswith("//!"):
            doc_comment_lines.add(line_number)

        if not in_attribute and re.match(r"^#\[\s*(derive|cfg|allow)\b", stripped):
            in_attribute = True
            attribute_bracket_balance = 0

        if in_attribute:
            attribute_lines.add(line_number)
            attribute_bracket_balance += line.count("[") - line.count("]")
            if attribute_bracket_balance <= 0:
                in_attribute = False

    return doc_comment_lines, attribute_lines


def _render_source_block(
    source: dict[str, Any],
    *,
    render_profile: str,
    optimize_context: bool,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    with _profiling_phase(_profiling_collector, "source_rendering"):
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
            elif path.suffix in _TS_SUFFIXES:
                omitted_jsdoc_lines, omitted_ts_type_import_lines = _ts_ast_omitted_relative_lines(
                    block
                )
            elif path.suffix in _JS_TS_SUFFIXES:
                omitted_jsdoc_lines = _js_ast_omitted_relative_lines(block)
            elif path.suffix in _RUST_SUFFIXES:
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


def _validation_repo_root(repo_root: str | Path) -> Path:
    root = Path(repo_root).expanduser().resolve()
    if root.is_file():
        root = root.parent
    markers = (
        "package.json",
        "package-lock.json",
        "pnpm-workspace.yaml",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Cargo.toml",
        "tsconfig.json",
    )
    boundary_markers = (
        "README.md",
        ".gitignore",
        "LICENSE",
        "AGENTS.md",
    )
    boundary_candidate: Path | None = None
    current = root
    while True:
        if any((current / marker).exists() for marker in markers):
            return current
        if any((current / marker).exists() for marker in boundary_markers):
            boundary_candidate = current
        if current.parent == current:
            break
        next_current = current.parent
        if boundary_candidate is not None and next_current == boundary_candidate.parent:
            break
        current = next_current
    return boundary_candidate or root


def _infer_js_package_manager(root: Path, package_json: dict[str, Any]) -> str:
    package_manager = package_json.get("packageManager")
    if isinstance(package_manager, str):
        normalized = package_manager.split("@", 1)[0].strip().lower()
        if normalized in {"npm", "pnpm", "yarn", "bun"}:
            return normalized
    if (root / "pnpm-lock.yaml").is_file() or (root / "pnpm-workspace.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    if (root / "bun.lockb").is_file():
        return "bun"
    return "npm"


def _javascript_repo_fallback_command(package_manager: str) -> str:
    if package_manager == "pnpm":
        return "pnpm test"
    if package_manager == "yarn":
        return "yarn test"
    if package_manager == "bun":
        return "bun test"
    return "npm test"


def _package_json_dependency_names(package_json: dict[str, Any]) -> set[str]:
    dependency_names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        current = package_json.get(key)
        if not isinstance(current, dict):
            continue
        dependency_names.update(str(name) for name in current.keys())
    return dependency_names


def _ts_jest_configured(
    repo_root: Path,
    package_json: dict[str, Any],
    package_text: str,
    dependency_names: set[str],
) -> bool:
    if "ts-jest" in dependency_names:
        return True
    if "ts-jest" in package_text:
        return True
    jest_config = package_json.get("jest")
    if jest_config is not None and "ts-jest" in json.dumps(jest_config, sort_keys=True):
        return True
    for config_name in (
        "jest.config.js",
        "jest.config.cjs",
        "jest.config.mjs",
        "jest.config.ts",
        "jest.config.json",
    ):
        config_path = repo_root / config_name
        if not config_path.is_file():
            continue
        try:
            if "ts-jest" in config_path.read_text(encoding="utf-8"):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def _detect_validation_runners_from_root(root: Path) -> _ValidationRunnerInfo:
    if not root.exists():
        return _ValidationRunnerInfo(False, False, False, (), (), None)

    all_files = _iter_repo_files(root, max_files=_VALIDATION_RUNNER_SCAN_LIMIT)
    has_python = any(current.suffix == ".py" for current in all_files)
    has_rust = (root / "Cargo.toml").is_file() or any(
        current.suffix in _RUST_SUFFIXES for current in all_files
    )
    has_javascript = any(current.suffix.lower() in _JS_TS_SUFFIXES for current in all_files)

    package_json: dict[str, Any] = {}
    package_text = ""
    package_json_path = root / "package.json"
    if package_json_path.is_file():
        try:
            package_text = package_json_path.read_text(encoding="utf-8")
            loaded = json.loads(package_text)
            if isinstance(loaded, dict):
                package_json = loaded
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            package_json = {}
            package_text = ""

    dependency_names = _package_json_dependency_names(package_json)
    js_runners = tuple(runner for runner in _JS_RUNNER_ORDER if runner in dependency_names)

    ts_runners: list[str] = []
    if "vitest" in dependency_names:
        ts_runners.append("vitest")
    if "jest" in dependency_names and _ts_jest_configured(
        root, package_json, package_text, dependency_names
    ):
        ts_runners.append("jest")
    if "mocha" in dependency_names and ("ts-node" in dependency_names or "tsx" in dependency_names):
        ts_runners.append("mocha")
    js_fallback_command = (
        _javascript_repo_fallback_command(_infer_js_package_manager(root, package_json))
        if has_javascript
        else None
    )

    return _ValidationRunnerInfo(
        has_python=has_python,
        has_rust=has_rust,
        has_javascript=has_javascript,
        js_runners=js_runners,
        ts_runners=tuple(ts_runners),
        js_fallback_command=js_fallback_command,
    )


@lru_cache(maxsize=64)
def _detect_validation_runners(repo_root: str) -> _ValidationRunnerInfo:
    root = _validation_repo_root(repo_root)
    return _detect_validation_runners_from_root(root)


def _relative_validation_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.name


def _append_unique_command(commands: list[str], command: str, seen: set[str]) -> None:
    if command in seen:
        return
    seen.add(command)
    commands.append(command)


def _shell_safe_arg(value: str) -> str:
    if not value:
        return '""'
    if any(char.isspace() for char in value) or '"' in value:
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _candidate_terms(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return _query_terms(normalized.replace("_", " "))


def _python_decorator_qualname(node: ast.AST) -> str | None:
    current = node
    if isinstance(current, ast.Call):
        current = current.func
    if isinstance(current, ast.Name):
        return current.id
    if isinstance(current, ast.Attribute):
        parent = _python_decorator_qualname(current.value)
        return f"{parent}.{current.attr}" if parent else current.attr
    return None


def _best_test_function_candidate(
    candidates: list[str],
    *,
    primary_symbol_name: str | None,
    query: str | None,
) -> str | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    symbol_terms = _candidate_terms(primary_symbol_name)
    query_terms = _candidate_terms(query)
    best_name: str | None = None
    best_score = 0
    for candidate in candidates:
        haystack = candidate.lower()
        score = 0
        if symbol_terms:
            if all(term in haystack for term in symbol_terms):
                score += 6
            score += sum(2 for term in symbol_terms if term in haystack)
        if query_terms:
            score += sum(1 for term in query_terms if term in haystack)
        if score > 0 and candidate.startswith("test_"):
            score += 1
        if score > best_score or (
            score == best_score
            and score > 0
            and best_name is not None
            and len(candidate) < len(best_name)
        ):
            best_name = candidate
            best_score = score
    if best_score <= 0:
        return None
    return best_name


@lru_cache(maxsize=256)
def _python_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ()

    candidates: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            candidates.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for member in node.body:
                if isinstance(
                    member, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and member.name.startswith("test"):
                    candidates.append(member.name)
    return tuple(dict.fromkeys(candidates))


@lru_cache(maxsize=256)
def _python_parametrized_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ()

    candidates: list[str] = []

    def visit_body(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                decorator_names = {
                    name
                    for decorator in node.decorator_list
                    if (name := _python_decorator_qualname(decorator))
                }
                if {
                    "pytest.mark.parametrize",
                    "mark.parametrize",
                } & decorator_names:
                    candidates.append(node.name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                visit_body(list(node.body))

    visit_body(list(tree.body))
    return tuple(dict.fromkeys(candidates))


def _rust_test_attribute_kind(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    after_hash = stripped[1:].lstrip()
    if not after_hash.startswith("[") or not after_hash.endswith("]"):
        return None
    inner = after_hash[1:-1].strip()
    attribute_name = inner.split("(", 1)[0].strip()
    if attribute_name in {"test", "tokio::test"}:
        return attribute_name
    return None


def _rust_test_function_candidates_from_source(
    source: str,
    *,
    tokio_only: bool,
) -> tuple[str, ...]:
    candidates: list[str] = []
    pending_test_attribute = False
    for line in source.splitlines():
        attribute_kind = _rust_test_attribute_kind(line)
        if not pending_test_attribute:
            if attribute_kind == "tokio::test" or (not tokio_only and attribute_kind == "test"):
                pending_test_attribute = True
            continue

        stripped = line.strip()
        if not stripped:
            continue
        if attribute_kind is not None:
            pending_test_attribute = attribute_kind == "tokio::test" or (
                not tokio_only and attribute_kind == "test"
            )
            continue
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        match = _RUST_TEST_FN_PATTERN.match(line)
        if match:
            candidates.append(match.group(1))
        pending_test_attribute = False

    return tuple(dict.fromkeys(candidates))


@lru_cache(maxsize=256)
def _rust_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    return _rust_test_function_candidates_from_source(source, tokio_only=False)


@lru_cache(maxsize=256)
def _rust_tokio_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    return _rust_test_function_candidates_from_source(source, tokio_only=True)


@lru_cache(maxsize=256)
def _javascript_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    describe_pattern = re.compile(r"""\bdescribe(?:\.(?:only|skip))?\s*\(\s*["']([^"']+)["']""")
    test_pattern = re.compile(
        r"""(?x)
        (?:
            \b(?:test|it)(?:\.(?:only|skip|todo|concurrent))?\s*\(\s*["']([^"']+)["']
            |
            \b(?:test|it)\.each\s*\([^)]*\)\s*\(\s*["']([^"']+)["']
            |
            \bDeno\.test\s*\(\s*["']([^"']+)["']
        )
        """
    )
    describe_candidates = [
        (match.start(), match.group(1)) for match in describe_pattern.finditer(source)
    ]
    candidates: list[str] = []
    for match in test_pattern.finditer(source):
        target_name = next((group for group in match.groups() if group), None)
        if not target_name:
            continue
        suite_name = None
        for position, candidate in describe_candidates:
            if position >= match.start():
                break
            suite_name = candidate
        if suite_name:
            candidates.append(f"{suite_name} {target_name}".strip())
        candidates.append(target_name)
    return tuple(dict.fromkeys(candidates))


def _framework_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = Path(test_path)
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _python_parametrized_test_function_candidates(test_path)
    if suffix in _JS_TS_SUFFIXES:
        return _javascript_test_function_candidates(test_path)
    if suffix in _RUST_SUFFIXES:
        return _rust_test_function_candidates(test_path)
    return ()


def _framework_test_pattern_bonus(
    test_path: str,
    terms: list[str],
    *,
    raw_query: str | None = None,
) -> int:
    candidates = _framework_test_function_candidates(test_path)
    if not candidates:
        return 0
    expanded_terms: list[str] = list(terms)
    for candidate_term in _candidate_terms(raw_query):
        if candidate_term not in expanded_terms:
            expanded_terms.append(candidate_term)
    if not expanded_terms:
        return 0
    return (
        max((_score_text_terms(candidate, expanded_terms) for candidate in candidates), default=0)
        * 3
    )


def _javascript_runner_file_command(runner: str, relative_path: str) -> str:
    if runner == "vitest":
        return f"npx vitest run {relative_path}"
    if runner == "mocha":
        return f"npx mocha {relative_path}"
    return f"npx jest {relative_path}"


def _javascript_runner_specific_command(runner: str, relative_path: str, test_filter: str) -> str:
    quoted_filter = _shell_safe_arg(test_filter)
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


def _rust_file_level_command(test_path: Path, repo_root: Path) -> str | None:
    try:
        relative = test_path.resolve().relative_to(repo_root)
    except ValueError:
        return None
    if relative.suffix != ".rs" or "tests" not in relative.parts:
        return None
    parts = list(relative.parts)
    tests_index = parts.index("tests")
    target_parts = parts[tests_index + 1 :]
    if not target_parts:
        return None
    if len(target_parts) == 1:
        target = Path(target_parts[0]).stem
    else:
        target = Path(target_parts[0]).stem
    if not target:
        return None
    return f"cargo test --test {target}"


def _rust_uses_nested_test_target(test_path: Path, repo_root: Path) -> bool:
    try:
        relative = test_path.resolve().relative_to(repo_root)
    except ValueError:
        return False
    if relative.suffix != ".rs" or "tests" not in relative.parts:
        return False
    parts = list(relative.parts)
    tests_index = parts.index("tests")
    return len(parts[tests_index + 1 :]) > 1


def _validation_commands_for_tests(
    tests: list[str],
    *,
    repo_root: str | Path,
    primary_test: str | None = None,
    primary_symbol: dict[str, Any] | None = None,
    query: str | None = None,
) -> list[str]:
    return [
        str(step["command"])
        for step in _validation_plan_for_tests(
            tests,
            repo_root=repo_root,
            primary_test=primary_test,
            primary_symbol=primary_symbol,
            query=query,
        )
    ]


def _validation_plan_for_tests(
    tests: list[str],
    *,
    repo_root: str | Path,
    primary_test: str | None = None,
    primary_symbol: dict[str, Any] | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    explicit_root = Path(repo_root).expanduser().resolve()
    if explicit_root.is_file():
        explicit_root = explicit_root.parent
    root = explicit_root if tests else _validation_repo_root(repo_root)
    if tests:
        detected = _detect_validation_runners_from_root(root)
    else:
        local_files = _iter_repo_files(explicit_root, max_files=_VALIDATION_RUNNER_SCAN_LIMIT)
        local_has_python = any(current.suffix == ".py" for current in local_files)
        local_has_rust = any(current.suffix in _RUST_SUFFIXES for current in local_files)
        local_has_javascript = any(
            current.suffix.lower() in _JS_TS_SUFFIXES for current in local_files
        )
        detected = (
            _detect_validation_runners_from_root(explicit_root)
            if local_has_python and not local_has_javascript and not local_has_rust
            else _detect_validation_runners(str(root))
        )
    has_detected_javascript = bool(
        detected.has_javascript
        or detected.js_runners
        or detected.ts_runners
        or detected.js_fallback_command
    )
    primary_symbol_name = (
        str(primary_symbol.get("name"))
        if isinstance(primary_symbol, dict) and primary_symbol.get("name")
        else None
    )
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    requested_javascript_runners: list[str] = []
    include_python_fallback = False
    include_rust_fallback = False

    def remember_runner(runner: str) -> None:
        if runner not in requested_javascript_runners:
            requested_javascript_runners.append(runner)

    def add_step(
        command: str,
        *,
        scope: str,
        runner: str,
        target: str | None = None,
        confidence: float,
    ) -> None:
        if command in seen:
            return
        seen.add(command)
        step: dict[str, Any] = {
            "command": command,
            "scope": scope,
            "runner": runner,
            "confidence": round(min(1.0, max(0.0, confidence)), 3),
        }
        if target:
            step["target"] = target
        plan.append(step)

    for current in tests:
        path = Path(current)
        suffix = path.suffix.lower()
        absolute_path = str(path.resolve())
        relative_path = _relative_validation_path(path, root)
        is_primary_test = primary_test is not None and absolute_path == str(
            Path(primary_test).resolve()
        )

        if suffix == ".py":
            include_python_fallback = True
            if is_primary_test:
                test_filter = _best_test_function_candidate(
                    list(_python_test_function_candidates(absolute_path)),
                    primary_symbol_name=primary_symbol_name,
                    query=query,
                )
                if test_filter:
                    add_step(
                        f"uv run pytest {relative_path} -k {test_filter} -q",
                        scope="symbol",
                        runner="pytest",
                        target=relative_path,
                        confidence=0.95,
                    )
            add_step(
                f"uv run pytest {relative_path} -q",
                scope="file",
                runner="pytest",
                target=relative_path,
                confidence=0.82,
            )
            continue

        if suffix in _TS_SUFFIXES:
            test_candidates = (
                list(_javascript_test_function_candidates(absolute_path)) if is_primary_test else []
            )
            test_filter = _best_test_function_candidate(
                test_candidates,
                primary_symbol_name=primary_symbol_name,
                query=query,
            )
            for runner in detected.ts_runners:
                remember_runner(runner)
                if test_filter:
                    add_step(
                        _javascript_runner_specific_command(runner, relative_path, test_filter),
                        scope="symbol",
                        runner=runner,
                        target=relative_path,
                        confidence=0.9,
                    )
                add_step(
                    _javascript_runner_file_command(runner, relative_path),
                    scope="file",
                    runner=runner,
                    target=relative_path,
                    confidence=0.78,
                )
            continue

        if suffix in _JS_TS_SUFFIXES:
            test_candidates = (
                list(_javascript_test_function_candidates(absolute_path)) if is_primary_test else []
            )
            test_filter = _best_test_function_candidate(
                test_candidates,
                primary_symbol_name=primary_symbol_name,
                query=query,
            )
            for runner in detected.js_runners:
                remember_runner(runner)
                if test_filter:
                    add_step(
                        _javascript_runner_specific_command(runner, relative_path, test_filter),
                        scope="symbol",
                        runner=runner,
                        target=relative_path,
                        confidence=0.9,
                    )
                add_step(
                    _javascript_runner_file_command(runner, relative_path),
                    scope="file",
                    runner=runner,
                    target=relative_path,
                    confidence=0.78,
                )
            continue

        if suffix in _RUST_SUFFIXES:
            include_rust_fallback = True
            file_level_command = _rust_file_level_command(path, root)
            if is_primary_test:
                test_filter = _best_test_function_candidate(
                    list(_rust_test_function_candidates(absolute_path)),
                    primary_symbol_name=primary_symbol_name,
                    query=query,
                )
                if test_filter:
                    targeted_command = (
                        f"{file_level_command} {test_filter}"
                        if file_level_command and _rust_uses_nested_test_target(path, root)
                        else f"cargo test {test_filter}"
                    )
                    add_step(
                        targeted_command,
                        scope="symbol",
                        runner="cargo",
                        target=relative_path,
                        confidence=0.88,
                    )
            if file_level_command:
                add_step(
                    file_level_command,
                    scope="file",
                    runner="cargo",
                    target=relative_path,
                    confidence=0.8,
                )
            continue

    if not tests:
        if has_detected_javascript:
            include_python_fallback = include_python_fallback
        else:
            include_python_fallback = include_python_fallback or detected.has_python
        include_rust_fallback = include_rust_fallback or detected.has_rust
        for runner in (*detected.js_runners, *detected.ts_runners):
            remember_runner(runner)

    if (
        not include_python_fallback
        and not include_rust_fallback
        and not requested_javascript_runners
    ):
        include_python_fallback = not has_detected_javascript and (
            detected.has_python or not detected.has_rust
        )
        include_rust_fallback = detected.has_rust
        for runner in (*detected.js_runners, *detected.ts_runners):
            remember_runner(runner)

    if include_python_fallback:
        add_step("uv run pytest -q", scope="repo", runner="pytest", confidence=0.55)
    for runner in requested_javascript_runners:
        add_step(
            _javascript_runner_fallback_command(runner),
            scope="repo",
            runner=runner,
            confidence=0.5,
        )
    if (
        detected.has_javascript
        and not requested_javascript_runners
        and detected.js_fallback_command
    ):
        add_step(
            detected.js_fallback_command,
            scope="repo",
            runner="javascript",
            confidence=0.45,
        )
    if include_rust_fallback:
        add_step("cargo test", scope="repo", runner="cargo", confidence=0.55)

    return plan


def _symbol_sort_key(symbol: dict[str, Any]) -> tuple[int, int, str, str]:
    start_line = int(symbol.get("start_line", symbol.get("line", 0)))
    end_line = int(symbol.get("end_line", start_line))
    return (
        start_line,
        end_line,
        str(symbol.get("kind", "")),
        str(symbol.get("name", "")),
    )


def _symbols_for_file(repo_map: dict[str, Any], file_path: str) -> list[dict[str, Any]]:
    symbols = [
        dict(current)
        for current in repo_map.get("symbols", [])
        if str(current.get("file")) == file_path
    ]
    symbols.sort(key=_symbol_sort_key)
    return symbols


def _enclosing_symbol_for_line(
    repo_map: dict[str, Any],
    file_path: str,
    line_number: int,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for symbol in _symbols_for_file(repo_map, file_path):
        start_line = symbol.get("start_line", symbol.get("line"))
        end_line = symbol.get("end_line", start_line)
        if not isinstance(start_line, int) or not isinstance(end_line, int):
            continue
        if start_line <= line_number <= end_line:
            candidates.append(symbol)
    if not candidates:
        return None
    candidates.sort(
        key=lambda symbol: (
            int(symbol.get("end_line", symbol.get("line", 0)))
            - int(symbol.get("start_line", symbol.get("line", 0))),
            *_symbol_sort_key(symbol),
        )
    )
    return candidates[0]


def _related_span_record(
    symbol: dict[str, Any],
    *,
    depth: int,
    score: int,
    reasons: list[str],
) -> dict[str, Any] | None:
    span = _primary_span_for_symbol(symbol)
    file_path = symbol.get("file")
    symbol_name = symbol.get("name")
    if span is None or not file_path or not symbol_name:
        return None
    return {
        "file": str(file_path),
        "symbol": str(symbol_name),
        "start_line": int(span["start_line"]),
        "end_line": int(span["end_line"]),
        "depth": int(depth),
        "score": int(score),
        "reasons": list(reasons),
        "provenance": _provenance_from_reasons(reasons),
        "rationale": _span_rationale(str(symbol_name), list(reasons), int(depth)),
    }


def _ordered_dependent_file_matches(
    radius_payload: dict[str, Any],
    *,
    primary_file: str | None,
    max_depth: int,
) -> list[dict[str, Any]]:
    definition_files = {
        str(current.get("file"))
        for current in radius_payload.get("definitions", [])
        if current.get("file")
    }
    if primary_file:
        definition_files.add(str(primary_file))

    matches: list[dict[str, Any]] = []
    for current in radius_payload.get("file_matches", []):
        current_path = str(current.get("path", ""))
        if not current_path or current_path in definition_files:
            continue
        if _is_test_file(Path(current_path)):
            continue
        depth = int(current.get("depth", max_depth + 1))
        if depth > max_depth:
            continue
        matches.append(
            {
                "path": current_path,
                "depth": depth,
                "score": int(current.get("score", 0)),
                "reasons": list(current.get("reasons", [])),
                "graph_score": float(current.get("graph_score", 0.0)),
            }
        )

    matches = _narrow_python_depth_two_dependency_matches(matches, primary_file=primary_file)

    matches.sort(
        key=lambda current: (
            int(current["depth"]),
            0 if "caller" in current["reasons"] else 1,
            -int(current["score"]),
            -float(current["graph_score"]),
            str(current["path"]),
        )
    )
    return matches


def _narrow_python_depth_two_dependency_matches(
    matches: list[dict[str, Any]],
    *,
    primary_file: str | None,
) -> list[dict[str, Any]]:
    if not primary_file:
        return matches
    primary_name = Path(primary_file).name
    if primary_name not in {"utils.py", "exceptions.py", "termui.py", "_compat.py", "core.py"}:
        return matches
    if primary_name in {"utils.py", "termui.py", "core.py"}:
        has_depth_one = any(int(current.get("depth", 0)) <= 1 for current in matches)
        if not has_depth_one:
            return matches
        return [current for current in matches if int(current.get("depth", 0)) <= 1]
    caller_backed = any("caller" in list(current.get("reasons", [])) for current in matches)
    if not caller_backed:
        return matches
    filtered: list[dict[str, Any]] = []
    for current in matches:
        reasons = list(current.get("reasons", []))
        depth = int(current.get("depth", 0))
        if "caller" not in reasons and depth > 1:
            continue
        filtered.append(current)
    return filtered


def _related_spans_from_blast_radius(
    repo_map: dict[str, Any],
    radius_payload: dict[str, Any],
    *,
    primary_symbol: dict[str, Any] | None,
    dependent_matches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    if primary_symbol is None:
        return [], 0

    primary_key = (
        str(primary_symbol.get("file", "")),
        str(primary_symbol.get("name", "")),
    )
    seen: set[tuple[str, str]] = {primary_key}
    related_spans: list[dict[str, Any]] = []
    caller_keys: set[tuple[str, str]] = set()
    match_by_path = {str(match["path"]): match for match in dependent_matches}

    def _add_symbol(
        symbol: dict[str, Any] | None,
        *,
        is_caller: bool = False,
        depth: int = 0,
        score: int = 0,
        reasons: list[str] | None = None,
    ) -> None:
        if symbol is None:
            return
        key = (str(symbol.get("file", "")), str(symbol.get("name", "")))
        if not key[0] or not key[1] or key in seen:
            return
        record = _related_span_record(
            symbol,
            depth=depth,
            score=score,
            reasons=list(reasons or []),
        )
        if record is None:
            return
        seen.add(key)
        related_spans.append(record)
        if is_caller:
            caller_keys.add(key)

    direct_callers = sorted(
        (
            dict(current)
            for current in radius_payload.get("callers", [])
            if current.get("file")
            and isinstance(current.get("line"), int)
            and not _is_test_file(Path(str(current.get("file"))))
        ),
        key=lambda current: (
            str(current.get("file", "")),
            int(current.get("line", 0)),
            str(current.get("text", "")),
        ),
    )
    for caller in direct_callers:
        caller_path = str(caller["file"])
        caller_match = match_by_path.get(
            caller_path,
            {
                "depth": 1,
                "score": 0,
                "reasons": ["caller"],
            },
        )
        _add_symbol(
            _enclosing_symbol_for_line(
                repo_map,
                caller_path,
                int(caller["line"]),
            ),
            is_caller=True,
            depth=int(caller_match.get("depth", 1)),
            score=int(caller_match.get("score", 0)),
            reasons=list(caller_match.get("reasons", ["caller"])),
        )

    for match in dependent_matches:
        for symbol in _symbols_for_file(repo_map, str(match["path"])):
            _add_symbol(
                symbol,
                depth=int(match.get("depth", 0)),
                score=int(match.get("score", 0)),
                reasons=list(match.get("reasons", [])),
            )
            break

    return related_spans, len(caller_keys)


def _candidate_edit_spans(
    *,
    primary_symbol: dict[str, Any] | None,
    primary_file_match: dict[str, Any],
    related_spans: list[dict[str, Any]],
    max_spans: int,
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    if primary_symbol is not None:
        span = _primary_span_for_symbol(primary_symbol)
        if span is not None and primary_symbol.get("file") and primary_symbol.get("name"):
            spans.append(
                {
                    "file": str(primary_symbol["file"]),
                    "symbol": str(primary_symbol["name"]),
                    "start_line": int(span["start_line"]),
                    "end_line": int(span["end_line"]),
                    "depth": 0,
                    "score": int(primary_file_match.get("score", 0))
                    + int(primary_symbol.get("score", 0)),
                    "reasons": list(primary_file_match.get("reasons", [])) or ["primary"],
                    "provenance": _provenance_from_reasons(
                        list(primary_file_match.get("reasons", [])) or ["primary"]
                    ),
                    "rationale": _span_rationale(
                        str(primary_symbol["name"]),
                        list(primary_file_match.get("reasons", [])) or ["primary"],
                        0,
                    ),
                }
            )
    spans.extend(dict(current) for current in related_spans)
    spans.sort(
        key=lambda current: (
            int(current.get("depth", 0)),
            -int(current.get("score", 0)),
            str(current.get("file", "")),
            int(current.get("start_line", 0)),
            str(current.get("symbol", "")),
        )
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for current in spans:
        key = (
            str(current.get("file", "")),
            str(current.get("symbol", "")),
            int(current.get("start_line", 0)),
            int(current.get("end_line", 0)),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(current)
        if len(deduped) >= max(1, max_spans):
            break
    return deduped


def _mention_ref(file_path: str, start_line: int, end_line: int) -> str:
    normalized_end = max(start_line, end_line)
    if normalized_end <= start_line:
        return f"{file_path}#L{start_line}"
    return f"{file_path}#L{start_line}-L{normalized_end}"


def _navigation_pack(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    max_reads: int,
) -> dict[str, Any]:
    seed = dict(payload.get("edit_plan_seed", {}))
    primary_file = str(seed.get("primary_file", "") or "")
    raw_primary_symbol = seed.get("primary_symbol")
    primary_symbol = dict(raw_primary_symbol) if isinstance(raw_primary_symbol, dict) else {}
    primary_symbol_name = str(primary_symbol.get("name", "") or "")
    raw_primary_span = seed.get("primary_span")
    primary_span = dict(raw_primary_span) if isinstance(raw_primary_span, dict) else {}
    primary_start = int(primary_span.get("start_line", 0) or 0)
    primary_end = int(primary_span.get("end_line", primary_start) or primary_start)
    primary_target = {
        "file": primary_file,
        "symbol": primary_symbol_name,
        "start_line": primary_start,
        "end_line": primary_end,
        "mention_ref": _mention_ref(primary_file, primary_start, primary_end)
        if primary_file and primary_start > 0
        else "",
        "reasons": list(seed.get("reasons", [])),
        "confidence": dict(seed.get("confidence", {})),
    }

    follow_up_reads: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()

    def add_read(entry: dict[str, Any], *, role: str) -> None:
        file_path = str(entry.get("file", "") or "")
        symbol_name = str(entry.get("symbol", "") or "")
        start_line = int(entry.get("start_line", 0) or 0)
        end_line = int(entry.get("end_line", start_line) or start_line)
        if not file_path or start_line <= 0 or end_line < start_line:
            return
        key = (file_path, symbol_name, start_line, end_line)
        if key in seen:
            return
        seen.add(key)
        follow_up_reads.append(
            {
                "file": file_path,
                "symbol": symbol_name,
                "start_line": start_line,
                "end_line": end_line,
                "mention_ref": _mention_ref(file_path, start_line, end_line),
                "role": role,
                "rationale": str(entry.get("rationale", "") or ""),
                "reasons": list(entry.get("reasons", [])),
                "provenance": list(entry.get("provenance", [])),
            }
        )

    if primary_file and primary_symbol_name and primary_start > 0:
        add_read(
            {
                "file": primary_file,
                "symbol": primary_symbol_name,
                "start_line": primary_start,
                "end_line": primary_end,
                "rationale": "Primary edit target selected from ranked symbols and file matches.",
                "reasons": list(seed.get("reasons", [])),
            },
            role="primary",
        )

    for span in list(payload.get("candidate_edit_targets", {}).get("spans", [])):
        span_file = str(span.get("file", "") or "")
        span_symbol = str(span.get("symbol", "") or "")
        role = (
            "primary"
            if span_file == primary_file
            and span_symbol == primary_symbol_name
            and int(span.get("start_line", 0) or 0) == primary_start
            else "related"
        )
        add_read(dict(span), role=role)
        if len(follow_up_reads) >= max(1, max_reads):
            break

    if len(follow_up_reads) < max(1, max_reads):
        validation_tests = [
            str(current) for current in seed.get("validation_tests", []) if str(current)
        ]
        for test_path in validation_tests:
            symbols = _symbols_for_file(repo_map, test_path)
            preferred = next(
                (current for current in symbols if str(current.get("name", "")).startswith("test")),
                symbols[0] if symbols else None,
            )
            if preferred is None:
                continue
            span = _primary_span_for_symbol(preferred)
            if span is None:
                continue
            add_read(
                {
                    "file": test_path,
                    "symbol": str(preferred.get("name", "") or ""),
                    "start_line": int(span["start_line"]),
                    "end_line": int(span["end_line"]),
                    "rationale": "Validation target likely to confirm the primary edit outcome.",
                    "reasons": ["validation-test"],
                    "provenance": ["filename-heuristic"],
                },
                role="test",
            )
            if len(follow_up_reads) >= max(1, max_reads):
                break

    grouped_reads: dict[str, list[dict[str, Any]]] = {"related": [], "test": []}
    for current in follow_up_reads:
        role = str(current.get("role", "") or "")
        if role == "related":
            grouped_reads["related"].append(current)
        elif role == "test":
            grouped_reads["test"].append(current)

    parallel_read_groups: list[dict[str, Any]] = []
    prefetched_reads: list[dict[str, Any]] = []
    if primary_file:
        try:
            primary_parent = Path(primary_file).resolve().parent
        except (OSError, ValueError):
            primary_parent = None
        if primary_parent is not None:
            for role in ("related", "test"):
                remaining_reads: list[dict[str, Any]] = []
                for current in grouped_reads[role]:
                    current_file = str(current.get("file", "") or "")
                    try:
                        current_parent = Path(current_file).resolve().parent
                    except (OSError, ValueError):
                        current_parent = None
                    if current_parent is not None and current_parent == primary_parent:
                        prefetched_reads.append(current)
                    else:
                        remaining_reads.append(current)
                grouped_reads[role] = remaining_reads
    if primary_target["mention_ref"]:
        primary_mentions = [str(primary_target["mention_ref"])]
        primary_files = [primary_file] if primary_file else []
        primary_roles = ["primary"]
        for current in prefetched_reads:
            mention = str(current.get("mention_ref", "") or "")
            file_path = str(current.get("file", "") or "")
            role = str(current.get("role", "") or "")
            if mention:
                primary_mentions.append(mention)
            if file_path:
                primary_files.append(file_path)
            if role:
                primary_roles.append(role)
        parallel_read_groups.append(
            {
                "phase": len(parallel_read_groups),
                "label": "primary",
                "can_parallelize": False,
                "mentions": primary_mentions,
                "files": primary_files,
                "roles": primary_roles,
            }
        )
    if grouped_reads["related"]:
        parallel_read_groups.append(
            {
                "phase": len(parallel_read_groups),
                "label": "related",
                "can_parallelize": True,
                "mentions": [
                    str(current.get("mention_ref", "") or "")
                    for current in grouped_reads["related"]
                ],
                "files": [
                    str(current.get("file", "") or "") for current in grouped_reads["related"]
                ],
                "roles": ["related"],
            }
        )
    if grouped_reads["test"]:
        parallel_read_groups.append(
            {
                "phase": len(parallel_read_groups),
                "label": "test",
                "can_parallelize": True,
                "mentions": [
                    str(current.get("mention_ref", "") or "") for current in grouped_reads["test"]
                ],
                "files": [str(current.get("file", "") or "") for current in grouped_reads["test"]],
                "roles": ["test"],
            }
        )

    return {
        "primary_target": primary_target,
        "follow_up_reads": follow_up_reads,
        "parallel_read_groups": parallel_read_groups,
        "related_tests": [
            str(current) for current in seed.get("validation_tests", []) if str(current)
        ],
        "validation_commands": [
            str(current) for current in seed.get("validation_commands", []) if str(current)
        ],
        "edit_ordering": [
            str(current) for current in seed.get("edit_ordering", []) if str(current)
        ],
        "rollback_risk": float(seed.get("rollback_risk", 0.0) or 0.0),
    }


def _deterministic_edit_ordering(
    primary_file: str | None,
    dependent_files: list[str],
    tests: list[str],
) -> list[str]:
    ordering: list[str] = []
    for current in [primary_file, *dependent_files, *tests]:
        if not current or current in ordering:
            continue
        ordering.append(str(current))
    return ordering


def _suggested_edit_confidence(score: int, provenance: str | None = None) -> float:
    if score <= 0:
        return 0.0
    normalized_provenance = str(provenance or "").strip().lower()
    adjusted_score = score
    if normalized_provenance in {"heuristic", "regex-heuristic", "filename-convention"}:
        adjusted_score = max(1, score - 3)
    elif normalized_provenance == "graph-derived":
        adjusted_score = max(1, score - 2)
    return _confidence_from_score(adjusted_score)


def _import_update_rationale(symbol: str, module_name: str) -> str:
    return (
        f"this file imports {symbol} from {module_name}; "
        f"if {symbol} changes, this import must be updated"
    )


def _caller_update_rationale(symbol: str, line_number: int) -> str:
    return (
        f"calls {symbol}() on line {line_number}; "
        f"if {symbol}'s signature changes, this call site must be updated"
    )


def _suggested_edit_base_entry(current: dict[str, Any]) -> dict[str, Any]:
    file_path = str(current.get("file", ""))
    reasons = list(current.get("reasons", []))
    edit_kind = "caller-update" if "caller" in reasons else "dependency-update"
    provenance = _symbol_navigation_provenance_for_path(file_path) if file_path else "heuristic"
    return {
        "file": file_path,
        "symbol": str(current.get("symbol", "")),
        "start_line": int(current.get("start_line", 0)),
        "end_line": int(current.get("end_line", 0)),
        "edit_kind": edit_kind,
        "rationale": str(
            current.get(
                "rationale",
                _span_rationale(
                    str(current.get("symbol", "")),
                    reasons,
                    int(current.get("depth", 0)),
                ),
            )
        ),
        "provenance": provenance,
        "confidence": _suggested_edit_confidence(int(current.get("score", 0)), provenance),
    }


def _caller_update_targets_for_span(
    current: dict[str, Any],
    callers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_file = str(current.get("file", ""))
    start_line = int(current.get("start_line", 0))
    end_line = int(current.get("end_line", start_line))
    if not current_file or start_line <= 0 or end_line < start_line:
        return []

    targets_by_line: dict[int, dict[str, Any]] = {}
    for caller in callers:
        if str(caller.get("file", "")) != current_file:
            continue
        line_number = int(caller.get("line", 0))
        if line_number < start_line or line_number > end_line or line_number <= 0:
            continue
        provenance = str(
            caller.get("provenance", _symbol_navigation_provenance_for_path(current_file))
        )
        candidate = {
            "start_line": line_number,
            "end_line": line_number,
            "provenance": provenance,
        }
        existing = targets_by_line.get(line_number)
        if existing is None or float(_suggested_edit_confidence(10, provenance)) > float(
            _suggested_edit_confidence(10, str(existing.get("provenance", "")))
        ):
            targets_by_line[line_number] = candidate

    return [targets_by_line[line] for line in sorted(targets_by_line)]


def _ambiguous_suggested_edit_alternatives(
    primary_symbol: dict[str, Any] | None,
    definitions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if primary_symbol is None:
        return []
    primary_name = str(primary_symbol.get("name", ""))
    primary_file = str(primary_symbol.get("file", ""))
    alternatives: dict[tuple[str, str], dict[str, str]] = {}
    for current in definitions:
        current_file = str(current.get("file", ""))
        current_name = str(current.get("name", ""))
        if not current_file or not current_name:
            continue
        if current_name != primary_name:
            continue
        if current_file == primary_file:
            continue
        key = (current_file, current_name)
        alternatives[key] = {"file": current_file, "symbol": current_name}
    return [alternatives[key] for key in sorted(alternatives)]


def _suggested_edit_priority(entry: dict[str, Any]) -> tuple[int, float, int, int, str, str]:
    edit_kind = str(entry.get("edit_kind", "dependency-update"))
    kind_priority = {
        "caller-update": 0,
        "import-update": 1,
        "dependency-update": 2,
    }.get(edit_kind, 3)
    start_line = int(entry.get("start_line", 0))
    end_line = int(entry.get("end_line", start_line))
    return (
        kind_priority,
        -float(entry.get("confidence", 0.0)),
        max(0, end_line - start_line),
        0 if bool(entry.get("ambiguous")) else 1,
        str(entry.get("symbol", "")),
        str(entry.get("rationale", "")),
    )


def _deduplicate_suggested_edits(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered_keys: list[tuple[str, int]] = []
    deduped: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in suggestions:
        file_path = str(entry.get("file", ""))
        start_line = int(entry.get("start_line", 0))
        if not file_path or start_line <= 0:
            continue
        key = (file_path, start_line)
        existing = deduped.get(key)
        if existing is None:
            ordered_keys.append(key)
            deduped[key] = dict(entry)
            continue
        if _suggested_edit_priority(entry) < _suggested_edit_priority(existing):
            deduped[key] = dict(entry)
    return [deduped[key] for key in ordered_keys]


def _suggested_edits_from_related_spans(
    related_spans: list[dict[str, Any]],
    *,
    primary_symbol: dict[str, Any] | None = None,
    definitions: list[dict[str, Any]] | None = None,
    callers: list[dict[str, Any]] | None = None,
    repo_root: Path | str | None = None,
    max_edits: int,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    processed_spans: list[dict[str, Any]] = []
    resolved_callers = [dict(current) for current in callers or []]
    resolved_definitions = [dict(current) for current in definitions or []]
    for current in related_spans:
        reasons = list(current.get("reasons", []))
        if "caller" not in reasons:
            suggestions.append(_suggested_edit_base_entry(current))
            processed_spans.append(current)
            continue

        current_file = str(current.get("file", ""))
        score = int(current.get("score", 0))
        call_targets = _caller_update_targets_for_span(current, resolved_callers)
        ambiguous_alternatives = _ambiguous_suggested_edit_alternatives(
            primary_symbol,
            resolved_definitions,
        )
        if call_targets and primary_symbol is not None:
            primary_name = str(primary_symbol.get("name", ""))
            for target in call_targets:
                entry: dict[str, Any] = {
                    "file": current_file,
                    "symbol": str(current.get("symbol", "")),
                    "start_line": int(target["start_line"]),
                    "end_line": int(target["end_line"]),
                    "edit_kind": "caller-update",
                    "rationale": _caller_update_rationale(
                        primary_name,
                        int(target["start_line"]),
                    ),
                    "provenance": str(target.get("provenance", "heuristic")),
                    "confidence": _suggested_edit_confidence(
                        score,
                        str(target.get("provenance", "heuristic")),
                    ),
                }
                if ambiguous_alternatives:
                    entry["ambiguous"] = True
                    entry["alternatives"] = ambiguous_alternatives
                suggestions.append(entry)
        else:
            fallback_entry = _suggested_edit_base_entry(current)
            if ambiguous_alternatives:
                fallback_entry["ambiguous"] = True
                fallback_entry["alternatives"] = ambiguous_alternatives
            suggestions.append(fallback_entry)
        processed_spans.append(current)

    if primary_symbol is None:
        return _deduplicate_suggested_edits(suggestions)

    primary_name = str(primary_symbol.get("name", ""))
    definition_path = str(primary_symbol.get("file", ""))
    if not primary_name or not definition_path:
        return _deduplicate_suggested_edits(suggestions)

    import_updates_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
    for current in processed_spans:
        current_file = str(current.get("file", ""))
        if not current_file:
            continue
        import_target = _import_update_target(
            Path(current_file),
            primary_name,
            definition_path,
            repo_root,
        )
        if import_target is None:
            continue
        start_line = int(import_target.get("start_line", 0))
        end_line = int(import_target.get("end_line", start_line))
        provenance = str(import_target.get("provenance", "heuristic"))
        module_name = str(import_target.get("module", Path(definition_path).stem))
        import_entry: dict[str, Any] = {
            "file": current_file,
            "symbol": primary_name,
            "start_line": start_line,
            "end_line": end_line,
            "edit_kind": "import-update",
            "rationale": _import_update_rationale(primary_name, module_name),
            "provenance": provenance,
            "confidence": _suggested_edit_confidence(int(current.get("score", 0)), provenance),
        }
        key = (current_file, start_line, end_line)
        existing = import_updates_by_key.get(key)
        if existing is None or float(import_entry["confidence"]) > float(existing["confidence"]):
            import_updates_by_key[key] = import_entry

    suggestions.extend(import_updates_by_key.values())
    return _deduplicate_suggested_edits(suggestions)


def _preferred_edit_anchor_symbol(
    primary_symbol: dict[str, Any] | None,
    ranked_symbols: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if primary_symbol is not None:
        primary_file = primary_symbol.get("file")
        if primary_file and not _is_test_file(Path(str(primary_file))):
            return primary_symbol
    for symbol in ranked_symbols:
        file_path = symbol.get("file")
        if file_path and not _is_test_file(Path(str(file_path))):
            return symbol
    return primary_symbol


def _rollback_risk_from_blast_radius(
    *,
    dependent_matches: list[dict[str, Any]],
    caller_symbol_count: int,
    test_count: int,
    max_depth: int,
) -> float:
    if not dependent_matches and caller_symbol_count <= 0:
        return 0.0

    normalized_max_depth = max(1, int(max_depth))
    observed_depth = max((int(current["depth"]) for current in dependent_matches), default=0)
    dependent_count = len(dependent_matches)
    depth_factor = min(1.0, observed_depth / normalized_max_depth)
    caller_factor = min(1.0, caller_symbol_count / 5.0)
    dependent_factor = min(1.0, dependent_count / 6.0)
    coverage_factor = min(1.0, test_count / max(1, dependent_count + 1))
    risk = (
        0.1
        + (0.3 * depth_factor)
        + (0.25 * caller_factor)
        + (0.15 * dependent_factor)
        - (0.25 * coverage_factor)
    )
    return round(min(1.0, max(0.0, risk)), 3)


def _build_edit_plan_seed(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    ranked_symbols: list[dict[str, Any]],
    query: str,
    max_files: int,
    max_depth: int = _DEFAULT_EDIT_PLAN_MAX_DEPTH,
    blast_radius_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    primary_symbol = next(iter(ranked_symbols), None)
    primary_file = next(iter(payload.get("files", [])), None)
    if primary_symbol is not None:
        preferred_files = _preferred_definition_files(repo_map, str(primary_symbol.get("name", "")))
        if preferred_files:
            primary_file = preferred_files[0]
        elif primary_symbol.get("file"):
            primary_file = str(primary_symbol["file"])
    if primary_symbol is None and primary_file is not None:
        primary_file_symbols = [
            current for current in ranked_symbols if str(current.get("file")) == str(primary_file)
        ]
        primary_symbol = next(iter(primary_file_symbols), None)

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

    dependent_files: list[str] = []
    related_spans: list[dict[str, Any]] = []
    caller_symbol_count = 0
    rollback_risk = 0.0
    edit_anchor_symbol = _preferred_edit_anchor_symbol(primary_symbol, ranked_symbols)
    edit_anchor_file = (
        str(edit_anchor_symbol.get("file"))
        if edit_anchor_symbol is not None and edit_anchor_symbol.get("file")
        else None
    )
    radius_payload = blast_radius_payload
    if edit_anchor_symbol is not None and radius_payload is None:
        edit_symbol_name = str(edit_anchor_symbol.get("name", ""))
        if edit_symbol_name:
            radius_payload = build_symbol_blast_radius_from_map(
                repo_map,
                edit_symbol_name,
                max_depth=max_depth,
            )
    if edit_anchor_symbol is not None and radius_payload is not None:
        dependent_matches = _ordered_dependent_file_matches(
            radius_payload,
            primary_file=edit_anchor_file,
            max_depth=max_depth,
        )
        dependent_files = [str(current["path"]) for current in dependent_matches]
        related_spans, caller_symbol_count = _related_spans_from_blast_radius(
            repo_map,
            radius_payload,
            primary_symbol=edit_anchor_symbol,
            dependent_matches=dependent_matches,
        )
        rollback_risk = _rollback_risk_from_blast_radius(
            dependent_matches=dependent_matches,
            caller_symbol_count=caller_symbol_count,
            test_count=len(radius_payload.get("tests", payload.get("tests", []))),
            max_depth=max_depth,
        )
    dependency_trust = _dependency_trust(repo_map, dependent_files)
    ranking_quality = str(
        payload.get(
            "ranking_quality",
            _ranking_quality(
                list(payload.get("file_matches", [])),
                list(payload.get("test_matches", [])),
            ),
        )
    )
    coverage_summary = dict(payload.get("coverage_summary", _coverage_summary(payload)))
    suggested_edit_primary_symbol = edit_anchor_symbol or primary_symbol
    suggested_edit_definitions = (
        list(radius_payload.get("definitions", []))
        if radius_payload is not None
        else ([suggested_edit_primary_symbol] if suggested_edit_primary_symbol is not None else [])
    )

    return {
        "primary_file": primary_file,
        "primary_symbol": primary_symbol,
        "primary_span": _primary_span_for_symbol(primary_symbol),
        "primary_test": primary_test,
        "validation_tests": validation_tests,
        "validation_commands": _validation_commands_for_tests(
            validation_tests,
            repo_root=payload.get("path", "."),
            primary_test=primary_test,
            primary_symbol=primary_symbol,
            query=query,
        ),
        "validation_plan": _validation_plan_for_tests(
            validation_tests,
            repo_root=payload.get("path", "."),
            primary_test=primary_test,
            primary_symbol=primary_symbol,
            query=query,
        ),
        "reasons": list(primary_file_match.get("reasons", [])),
        "confidence": {
            "file": _confidence_from_score(int(primary_file_match.get("score", 0))),
            "symbol": _confidence_from_score(int(primary_symbol.get("score", 0)))
            if primary_symbol is not None
            else 0.0,
            "test": _confidence_from_score(int(primary_test_match.get("score", 0))),
        },
        "related_spans": related_spans,
        "suggested_edits": _suggested_edits_from_related_spans(
            related_spans,
            primary_symbol=suggested_edit_primary_symbol,
            definitions=suggested_edit_definitions,
            callers=list(radius_payload.get("callers", [])) if radius_payload is not None else [],
            repo_root=Path(str(repo_map["path"])).resolve(),
            max_edits=max_files,
        ),
        "dependency_trust": dependency_trust,
        "plan_trust_summary": _plan_trust_summary(
            ranking_quality,
            coverage_summary,
            dependency_trust,
        ),
        "dependent_files": dependent_files,
        "edit_ordering": _deterministic_edit_ordering(
            edit_anchor_file or (str(primary_file) if primary_file is not None else None),
            dependent_files,
            [str(current) for current in payload.get("tests", [])],
        ),
        "rollback_risk": rollback_risk,
    }


def _sorted_ranked_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        symbols,
        key=lambda symbol: (
            -int(symbol.get("score", 0)),
            0 if str(symbol.get("kind")) == "function" else 1,
            str(symbol.get("file")),
            int(symbol.get("line", 0)),
            str(symbol.get("name")),
        ),
    )


def _attach_edit_plan_metadata(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    query: str,
    max_files: int,
    max_symbols: int,
    max_depth: int = _DEFAULT_EDIT_PLAN_MAX_DEPTH,
    blast_radius_payload: dict[str, Any] | None = None,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    with _profiling_phase(_profiling_collector, "edit_plan_assembly"):

        def _ordered_candidate_files(
            files: list[str],
            primary_file: str | None,
            *,
            limit: int,
        ) -> list[str]:
            if primary_file is None:
                return files[:limit]
            ordered = [current for current in files if current != primary_file]
            return [primary_file, *ordered][:limit]

        ranked_symbols = _sorted_ranked_symbols(list(payload.get("symbols", [])))
        edit_anchor_symbol = _preferred_edit_anchor_symbol(None, ranked_symbols)
        resolved_blast_radius_payload = blast_radius_payload
        if edit_anchor_symbol is not None and resolved_blast_radius_payload is None:
            edit_symbol_name = str(edit_anchor_symbol.get("name", ""))
            if edit_symbol_name:
                resolved_blast_radius_payload = build_symbol_blast_radius_from_map(
                    repo_map,
                    edit_symbol_name,
                    max_depth=max_depth,
                    _profiling_collector=_profiling_collector,
                )
        payload["edit_plan_seed"] = _build_edit_plan_seed(
            repo_map,
            payload,
            ranked_symbols=ranked_symbols,
            query=query,
            max_files=max_files,
            max_depth=max_depth,
            blast_radius_payload=resolved_blast_radius_payload,
        )
        payload["graph_trust_summary"] = (
            dict(resolved_blast_radius_payload.get("graph_trust_summary", {}))
            if resolved_blast_radius_payload is not None
            else _graph_trust_summary([])
        )
        primary_file = str(payload["edit_plan_seed"].get("primary_file") or "") or None
        payload["candidate_edit_targets"] = {
            "files": _ordered_candidate_files(
                list(payload.get("files", [])),
                primary_file,
                limit=max_files,
            ),
            "symbols": ranked_symbols[:max_symbols],
            "tests": list(payload.get("tests", []))[:max_files],
            "ranking_quality": str(
                payload.get(
                    "ranking_quality",
                    _ranking_quality(
                        list(payload.get("file_matches", [])),
                        list(payload.get("test_matches", [])),
                    ),
                )
            ),
            "coverage_summary": dict(payload.get("coverage_summary", _coverage_summary(payload))),
            "spans": _candidate_edit_spans(
                primary_symbol=payload["edit_plan_seed"].get("primary_symbol"),
                primary_file_match={},
                related_spans=[],
                max_spans=max(max_files, max_symbols),
            ),
        }
        primary_file_match = next(
            (
                match
                for match in payload.get("file_matches", [])
                if str(match.get("path")) == str(primary_file)
            ),
            payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
        )
        payload["candidate_edit_targets"]["spans"] = _candidate_edit_spans(
            primary_symbol=payload["edit_plan_seed"].get("primary_symbol"),
            primary_file_match=primary_file_match,
            related_spans=list(payload["edit_plan_seed"].get("related_spans", [])),
            max_spans=max(max_files, max_symbols),
        )
        payload["navigation_pack"] = _navigation_pack(
            repo_map,
            payload,
            max_reads=max(max_files, max_symbols),
        )
    return payload


def _attach_lightweight_navigation_metadata(
    repo_map: dict[str, Any],
    payload: dict[str, Any],
    *,
    query: str,
    max_files: int,
    max_symbols: int,
) -> dict[str, Any]:
    def _ordered_candidate_files(
        files: list[str],
        primary_file: str | None,
        *,
        limit: int,
    ) -> list[str]:
        if primary_file is None:
            return files[:limit]
        ordered = [current for current in files if current != primary_file]
        return [primary_file, *ordered][:limit]

    ranked_symbols = _sorted_ranked_symbols(list(payload.get("symbols", [])))
    primary_symbol = next(iter(ranked_symbols), None)
    primary_file = next(iter(payload.get("files", [])), None)
    if primary_symbol is not None:
        preferred_files = _preferred_definition_files(repo_map, str(primary_symbol.get("name", "")))
        if preferred_files:
            primary_file = preferred_files[0]
        elif primary_symbol.get("file"):
            primary_file = str(primary_symbol["file"])
    if primary_symbol is None and primary_file is not None:
        primary_file_symbols = [
            current for current in ranked_symbols if str(current.get("file")) == str(primary_file)
        ]
        primary_symbol = next(iter(primary_file_symbols), None)

    payload["edit_plan_seed"] = {}
    payload["edit_plan_seed_skipped"] = True
    payload["graph_trust_summary"] = _graph_trust_summary([])
    ranking_quality = str(
        payload.get(
            "ranking_quality",
            _ranking_quality(
                list(payload.get("file_matches", [])),
                list(payload.get("test_matches", [])),
            ),
        )
    )
    coverage_summary = dict(payload.get("coverage_summary", _coverage_summary(payload)))
    primary_file_match = next(
        (
            match
            for match in payload.get("file_matches", [])
            if str(match.get("path")) == str(primary_file)
        ),
        payload.get("file_matches", [{}])[0] if payload.get("file_matches") else {},
    )
    lightweight_seed = {
        "primary_file": primary_file,
        "primary_symbol": primary_symbol,
        "primary_span": _primary_span_for_symbol(primary_symbol),
        "primary_test": None,
        "validation_tests": [],
        "validation_commands": _validation_commands_for_tests(
            [],
            repo_root=payload.get("path", repo_map.get("path", ".")),
            primary_test=None,
            primary_symbol=primary_symbol,
            query=query,
        ),
        "validation_plan": _validation_plan_for_tests(
            [],
            repo_root=payload.get("path", repo_map.get("path", ".")),
            primary_test=None,
            primary_symbol=primary_symbol,
            query=query,
        ),
        "reasons": list(primary_file_match.get("reasons", [])),
        "confidence": {
            "file": _confidence_from_score(int(primary_file_match.get("score", 0))),
            "symbol": _confidence_from_score(int(primary_symbol.get("score", 0)))
            if primary_symbol is not None
            else 0.0,
            "test": 0.0,
        },
        "related_spans": [],
        "suggested_edits": [],
        "dependency_trust": _dependency_trust(repo_map, []),
        "plan_trust_summary": _plan_trust_summary(
            ranking_quality,
            coverage_summary,
            _dependency_trust(repo_map, []),
        ),
        "dependent_files": [],
        "edit_ordering": _deterministic_edit_ordering(
            str(primary_file) if primary_file is not None else None,
            [],
            [],
        ),
        "rollback_risk": 0.0,
    }
    lightweight_primary_symbol = dict(primary_symbol) if isinstance(primary_symbol, dict) else None
    payload["candidate_edit_targets"] = {
        "files": _ordered_candidate_files(
            list(payload.get("files", [])),
            str(primary_file) if primary_file is not None else None,
            limit=max_files,
        ),
        "symbols": ranked_symbols[:max_symbols],
        "tests": list(payload.get("tests", []))[:max_files],
        "ranking_quality": ranking_quality,
        "coverage_summary": coverage_summary,
        "spans": _candidate_edit_spans(
            primary_symbol=lightweight_primary_symbol,
            primary_file_match={},
            related_spans=[],
            max_spans=max(max_files, max_symbols),
        ),
    }
    payload["candidate_edit_targets"]["spans"] = _candidate_edit_spans(
        primary_symbol=lightweight_primary_symbol,
        primary_file_match=primary_file_match,
        related_spans=[],
        max_spans=max(max_files, max_symbols),
    )
    navigation_payload = dict(payload)
    navigation_payload["edit_plan_seed"] = lightweight_seed
    payload["navigation_pack"] = _navigation_pack(
        repo_map,
        navigation_payload,
        max_reads=max(max_files, max_symbols),
    )
    return payload


def build_context_edit_plan(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_symbols: int = 5,
    profile: bool = False,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    repo_map = build_repo_map(path, _profiling_collector=collector)
    return build_context_edit_plan_from_map(
        repo_map,
        query,
        max_files=max_files,
        max_symbols=max_symbols,
        profile=profile,
        _profiling_collector=collector,
    )


def build_context_edit_plan_from_map(
    repo_map: dict[str, Any],
    query: str,
    *,
    max_files: int = 3,
    max_symbols: int = 5,
    profile: bool = False,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    payload = build_context_pack_from_map(
        repo_map,
        query,
        _profiling_collector=collector,
    )
    normalized_max_files = max(1, max_files)
    normalized_max_symbols = max(1, max_symbols)
    payload["routing_reason"] = "context-edit-plan"
    payload["files"] = list(payload.get("files", []))[:normalized_max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:normalized_max_files]
    payload["file_summaries"] = list(payload.get("file_summaries", []))[:normalized_max_files]
    payload["tests"] = list(payload.get("tests", []))[:normalized_max_files]
    payload["test_matches"] = list(payload.get("test_matches", []))[:normalized_max_files]
    payload["symbols"] = _sorted_ranked_symbols(list(payload.get("symbols", [])))[
        :normalized_max_symbols
    ]
    payload["max_files"] = normalized_max_files
    payload["max_symbols"] = normalized_max_symbols
    payload = _attach_edit_plan_metadata(
        repo_map,
        payload,
        query=query,
        max_files=normalized_max_files,
        max_symbols=normalized_max_symbols,
        _profiling_collector=collector,
    )
    return _attach_profiling(payload, collector)


def build_context_edit_plan_json(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_symbols: int = 5,
    profile: bool = False,
) -> str:
    return json.dumps(
        build_context_edit_plan(
            query,
            path,
            max_files=max_files,
            max_symbols=max_symbols,
            profile=profile,
        ),
        indent=2,
    )


def build_context_render(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_repo_files: int | None = None,
    include_edit_plan_seed: bool = True,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    repo_map = build_repo_map(path, max_repo_files=max_repo_files, _profiling_collector=collector)
    return build_context_render_from_map(
        repo_map,
        query,
        max_files=max_files,
        include_edit_plan_seed=include_edit_plan_seed,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        max_tokens=max_tokens,
        model=model,
        optimize_context=optimize_context,
        render_profile=render_profile,
        profile=profile,
        _profiling_collector=collector,
    )


def build_context_render_from_map(
    repo_map: dict[str, Any],
    query: str,
    *,
    max_files: int = 3,
    include_edit_plan_seed: bool = True,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    context_payload = build_context_pack_from_map(
        repo_map,
        query,
        _profiling_collector=collector,
    )
    normalized_profile = _normalize_render_profile(render_profile, optimize_context)
    max_files = max(1, max_files)
    max_sources = max(1, max_sources)
    max_symbols_per_file = max(1, max_symbols_per_file)
    top_files = {str(current) for current in context_payload.get("files", [])[:max_files]}
    sources: list[dict[str, Any]] = []
    seen_symbols: set[tuple[str, str]] = set()
    seen_source_files: set[str] = set()
    for symbol in context_payload.get("symbols", []):
        current_file = str(symbol["file"])
        if current_file not in top_files:
            continue
        if current_file in seen_source_files:
            continue
        symbol_key = (current_file, str(symbol["name"]))
        if symbol_key in seen_symbols:
            continue
        seen_symbols.add(symbol_key)
        symbol_sources = build_symbol_source_from_map(
            repo_map,
            str(symbol["name"]),
            _profiling_collector=collector,
        ).get("sources", [])
        for source in symbol_sources:
            if str(source["file"]) != current_file:
                continue
            sources.append(
                _render_source_block(
                    source,
                    render_profile=normalized_profile,
                    optimize_context=optimize_context,
                    _profiling_collector=collector,
                )
            )
            seen_source_files.add(current_file)
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
    payload["max_tokens"] = max_tokens if max_tokens is not None and max_tokens > 0 else None
    payload["model"] = model
    payload["optimize_context"] = optimize_context
    payload["render_profile"] = normalized_profile
    if include_edit_plan_seed:
        payload = _attach_edit_plan_metadata(
            repo_map,
            payload,
            query=query,
            max_files=max_files,
            max_symbols=max_sources,
            max_depth=_DEFAULT_EDIT_PLAN_MAX_DEPTH,
            _profiling_collector=collector,
        )
    else:
        payload = _attach_lightweight_navigation_metadata(
            repo_map,
            payload,
            query=query,
            max_files=max_files,
            max_symbols=max_sources,
        )
    (
        rendered_context,
        sections,
        truncated,
        token_estimate,
        omitted_sections,
    ) = _render_context_string_and_sections(
        payload,
        max_render_chars=max_render_chars,
        _profiling_collector=collector,
    )
    payload["rendered_context"] = rendered_context
    payload["sections"] = sections
    payload["truncated"] = truncated
    payload["token_estimate"] = token_estimate
    payload["omitted_sections"] = omitted_sections
    return _attach_profiling(payload, collector)


def build_context_render_json(
    query: str,
    path: str | Path = ".",
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
) -> str:
    return json.dumps(
        build_context_render(
            query,
            path,
            max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            max_tokens=max_tokens,
            model=model,
            optimize_context=optimize_context,
            render_profile=render_profile,
            profile=profile,
        ),
        indent=2,
    )


def build_context_pack_from_map(
    repo_map: dict[str, Any],
    query: str,
    *,
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    payload = dict(repo_map)
    payload["files"] = list(repo_map.get("files", []))
    payload["symbols"] = [dict(symbol) for symbol in repo_map.get("symbols", [])]
    payload["imports"] = [dict(entry) for entry in repo_map.get("imports", [])]
    payload["tests"] = list(repo_map.get("tests", []))
    payload["related_paths"] = list(repo_map.get("related_paths", []))
    payload.pop("_profiling", None)
    payload = _build_context_pack_from_map(
        payload,
        query,
        _profiling_collector=_profiling_collector,
    )
    return _attach_profiling(payload, _profiling_collector)


def _normalize_semantic_provider(provider: str) -> str:
    normalized = str(provider).strip().lower() or "native"
    if normalized not in {"native", "lsp", "hybrid"}:
        return "native"
    return normalized


def _language_for_path(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in _JS_TS_SUFFIXES:
        return "javascript"
    if suffix in _RUST_SUFFIXES:
        return "rust"
    return "python"


def _lsp_symbol_kind_name(value: object) -> str:
    mapping = {
        5: "class",
        12: "function",
        6: "method",
        2: "module",
        3: "namespace",
        13: "variable",
        14: "constant",
        10: "enum",
        23: "struct",
        11: "interface",
    }
    if isinstance(value, int):
        return mapping.get(value, "symbol")
    return "symbol"


def _symbol_character_in_file(path: Path, line_number: int, symbol: str) -> int:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return 0
    if line_number <= 0 or line_number > len(lines):
        return 0
    line = lines[line_number - 1]
    return max(0, line.find(symbol))


def _provider_languages_for_symbol(
    repo_map: dict[str, Any],
    symbol: str,
    definitions: list[dict[str, Any]] | None = None,
) -> list[str]:
    languages: set[str] = set()
    for current in definitions or []:
        languages.add(_language_for_path(str(current.get("file", ""))))
    if languages:
        return sorted(languages)
    for current in repo_map.get("symbols", []):
        if str(current.get("name", "")) == symbol:
            languages.add(_language_for_path(str(current.get("file", ""))))
    return sorted(languages)


def _provider_status_snapshot(
    repo_root: Path,
    *,
    semantic_provider: str,
    languages: list[str],
    fallback_used: bool = False,
) -> dict[str, Any]:
    normalized_provider = _normalize_semantic_provider(semantic_provider)
    providers = [
        _EXTERNAL_LSP_PROVIDER_MANAGER.provider_status(language=language, workspace_root=repo_root)
        for language in languages
    ]
    return {
        "mode": normalized_provider,
        "fallback_used": bool(fallback_used),
        "providers": providers,
    }


def _merge_agreement_status(
    *,
    semantic_provider: str,
    native_count: int,
    lsp_count: int,
    merged_count: int,
    fallback_used: bool,
) -> dict[str, Any]:
    normalized_provider = _normalize_semantic_provider(semantic_provider)
    if normalized_provider == "native":
        agreement_status = "native-only"
    elif fallback_used and lsp_count == 0:
        agreement_status = "fallback-native"
    elif normalized_provider == "lsp":
        agreement_status = "lsp-only" if lsp_count > 0 else "native-fallback"
    elif native_count > 0 and lsp_count > 0 and merged_count == native_count == lsp_count:
        agreement_status = "agreed"
    elif native_count > 0 and lsp_count > 0:
        agreement_status = "diverged"
    elif lsp_count > 0:
        agreement_status = "lsp-only"
    else:
        agreement_status = "native-only"
    return {
        "mode": normalized_provider,
        "agreement_status": agreement_status,
        "native_count": native_count,
        "lsp_count": lsp_count,
        "merged_count": merged_count,
        "fallback_used": fallback_used,
    }


def _default_provider_metadata(
    repo_root: Path,
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str,
    definitions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_provider = _normalize_semantic_provider(semantic_provider)
    return (
        _merge_agreement_status(
            semantic_provider=normalized_provider,
            native_count=len(definitions or []),
            lsp_count=0,
            merged_count=len(definitions or []),
            fallback_used=normalized_provider == "lsp",
        ),
        _provider_status_snapshot(
            repo_root,
            semantic_provider=normalized_provider,
            languages=_provider_languages_for_symbol(repo_map, symbol, definitions),
            fallback_used=normalized_provider == "lsp",
        ),
    )


def _external_workspace_symbols(
    repo_root: Path,
    symbol: str,
    *,
    repo_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    languages = {
        _language_for_path(current.get("file", ""))
        for current in (repo_map or build_repo_map(repo_root)).get("symbols", [])
        if current.get("file")
    }
    for language in sorted(languages):
        try:
            client = _EXTERNAL_LSP_PROVIDER_MANAGER.get_client(
                language=language, workspace_root=repo_root
            )
            result = client.request("workspace/symbol", {"query": symbol})
        except (FileNotFoundError, LSPTransportError, ValueError):
            continue
        if not isinstance(result, list):
            continue
        for current in result:
            if not isinstance(current, dict) or str(current.get("name", "")) != symbol:
                continue
            location = current.get("location")
            if not isinstance(location, dict):
                continue
            payload_range = location.get("range")
            if not isinstance(payload_range, dict):
                continue
            payload_start = payload_range.get("start")
            payload_end = payload_range.get("end")
            if not isinstance(payload_start, dict) or not isinstance(payload_end, dict):
                continue
            uri = str(location.get("uri", ""))
            if not uri.startswith("file://"):
                continue
            file_path = Path(
                uri[8:] if uri.startswith("file:///") else uri.replace("file://", "", 1)
            )
            if len(str(file_path)) >= 2 and str(file_path)[1] == ":":
                resolved_path = Path(str(file_path))
            else:
                resolved_path = file_path
            matches.append(
                {
                    "name": symbol,
                    "kind": _lsp_symbol_kind_name(current.get("kind")),
                    "file": str(resolved_path.resolve()),
                    "line": int(payload_start.get("line") or 0) + 1,
                    "end_line": int(payload_end.get("line") or payload_start.get("line") or 0) + 1,
                    "provenance": f"lsp-{language}",
                }
            )
    matches.sort(key=lambda item: (str(item["file"]), int(item["line"]), str(item["kind"])))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for current in matches:
        key = (
            str(current["file"]),
            int(current["line"]),
            int(current["end_line"]),
            str(current["kind"]),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(current)
    return deduped


def _external_references(
    repo_root: Path, symbol: str, definitions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for definition in definitions:
        current_path = Path(str(definition["file"])).resolve()
        language = _language_for_path(current_path)
        try:
            client = _EXTERNAL_LSP_PROVIDER_MANAGER.get_client(
                language=language, workspace_root=repo_root
            )
            client.ensure_document(
                uri=current_path.as_uri(),
                text=current_path.read_text(encoding="utf-8"),
                language_id=language,
            )
            result = client.request(
                "textDocument/references",
                {
                    "textDocument": {"uri": current_path.as_uri()},
                    "position": {
                        "line": int(definition.get("line", 1)) - 1,
                        "character": _symbol_character_in_file(
                            current_path, int(definition.get("line", 1)), symbol
                        ),
                    },
                    "context": {"includeDeclaration": True},
                },
            )
        except (FileNotFoundError, OSError, UnicodeDecodeError, LSPTransportError, ValueError):
            continue
        if not isinstance(result, list):
            continue
        for current in result:
            if not isinstance(current, dict):
                continue
            location_range = current.get("range")
            if not isinstance(location_range, dict):
                continue
            start = location_range.get("start")
            end = location_range.get("end")
            if not isinstance(start, dict) or not isinstance(end, dict):
                continue
            uri = str(current.get("uri", ""))
            if not uri.startswith("file://"):
                continue
            file_path = Path(
                uri[8:] if uri.startswith("file:///") else uri.replace("file://", "", 1)
            )
            resolved_path = Path(str(file_path)).resolve()
            try:
                lines = resolved_path.read_text(encoding="utf-8").splitlines()
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                lines = []
            line_number = int(start.get("line", 0)) + 1
            text = lines[line_number - 1].strip() if 0 < line_number <= len(lines) else symbol
            references.append(
                {
                    "name": symbol,
                    "kind": "reference",
                    "file": str(resolved_path),
                    "line": line_number,
                    "end_line": int(end.get("line") or start.get("line") or 0) + 1,
                    "text": text,
                    "provenance": f"lsp-{language}",
                }
            )
    references.sort(key=lambda item: (str(item["file"]), int(item["line"])))
    deduped: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, int, int]] = set()
    for current in references:
        key = (str(current["file"]), int(current["line"]), int(current["end_line"]))
        if key in seen_refs:
            continue
        seen_refs.add(key)
        deduped.append(current)
    return deduped


def build_symbol_defs(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
) -> dict[str, Any]:
    payload = build_repo_map(path)
    return build_symbol_defs_from_map(payload, symbol, semantic_provider=semantic_provider)


def build_symbol_defs_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
) -> dict[str, Any]:
    payload = dict(repo_map)
    payload["files"] = list(repo_map.get("files", []))
    payload["symbols"] = [dict(current) for current in repo_map.get("symbols", [])]
    payload["imports"] = [dict(current) for current in repo_map.get("imports", [])]
    payload["tests"] = list(repo_map.get("tests", []))
    payload["related_paths"] = list(repo_map.get("related_paths", []))
    native_definitions = [
        {
            **dict(current),
            "provenance": _symbol_navigation_provenance_for_path(str(current["file"])),
        }
        for current in payload["symbols"]
        if str(current["name"]) == symbol
    ]
    native_definitions.sort(
        key=lambda item: (
            str(item["file"]),
            int(item["line"]),
            str(item["kind"]),
            str(item["name"]),
        )
    )
    normalized_provider = _normalize_semantic_provider(semantic_provider)
    definitions = [dict(current) for current in native_definitions]
    external_definitions: list[dict[str, Any]] = []
    fallback_used = False
    if normalized_provider != "native":
        external_definitions = _external_workspace_symbols(
            Path(str(repo_map["path"])).resolve(),
            symbol,
            repo_map=repo_map,
        )
        if normalized_provider == "lsp":
            fallback_used = not bool(external_definitions)
            definitions = external_definitions or definitions
        else:
            merged: dict[tuple[str, int, int, str], dict[str, Any]] = {}
            for current in [*external_definitions, *definitions]:
                key = (
                    str(current["file"]),
                    int(current["line"]),
                    int(current.get("end_line", current["line"])),
                    str(current.get("kind", "symbol")),
                )
                merged[key] = dict(current)
            definitions = list(merged.values())
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
    payload["graph_completeness"] = "strong"
    payload["semantic_provider"] = normalized_provider
    payload["provider_agreement"] = _merge_agreement_status(
        semantic_provider=normalized_provider,
        native_count=len(native_definitions),
        lsp_count=len(external_definitions),
        merged_count=len(definitions),
        fallback_used=fallback_used,
    )
    payload["provider_status"] = _provider_status_snapshot(
        Path(str(repo_map["path"])).resolve(),
        semantic_provider=normalized_provider,
        languages=_provider_languages_for_symbol(repo_map, symbol, definitions),
        fallback_used=fallback_used,
    )
    return payload


def build_symbol_defs_json(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
) -> str:
    return json.dumps(
        build_symbol_defs(symbol, path, semantic_provider=semantic_provider), indent=2
    )


def build_symbol_source(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    repo_map = build_repo_map(path, _profiling_collector=_profiling_collector)
    return build_symbol_source_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        _profiling_collector=_profiling_collector,
    )


def build_symbol_source_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    defs_payload = build_symbol_defs_from_map(repo_map, symbol, semantic_provider=semantic_provider)
    default_agreement, default_status = _default_provider_metadata(
        Path(str(repo_map["path"])).resolve(),
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        definitions=defs_payload.get("definitions"),
    )
    sources: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    with _profiling_phase(_profiling_collector, "source_extraction"):
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
    payload["semantic_provider"] = _normalize_semantic_provider(semantic_provider)
    payload["provider_agreement"] = dict(defs_payload.get("provider_agreement", default_agreement))
    payload["provider_status"] = dict(defs_payload.get("provider_status", default_status))
    return _attach_profiling(payload, _profiling_collector)


def build_symbol_source_json(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
) -> str:
    return json.dumps(
        build_symbol_source(symbol, path, semantic_provider=semantic_provider), indent=2
    )


def build_symbol_impact(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    payload = build_repo_map(path, _profiling_collector=_profiling_collector)
    return build_symbol_impact_from_map(
        payload,
        symbol,
        semantic_provider=semantic_provider,
        _profiling_collector=_profiling_collector,
    )


def build_symbol_impact_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    defs_payload = build_symbol_defs_from_map(repo_map, symbol, semantic_provider=semantic_provider)
    default_agreement, default_status = _default_provider_metadata(
        Path(str(repo_map["path"])).resolve(),
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        definitions=defs_payload.get("definitions"),
    )
    context_payload = build_context_pack_from_map(
        repo_map,
        symbol,
        _profiling_collector=_profiling_collector,
    )
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
        _profiling_collector=_profiling_collector,
    )

    file_matches_by_path: dict[str, dict[str, Any]] = {
        str(item["path"]): {
            "path": str(item["path"]),
            "score": int(item["score"]),
            "reasons": list(item["reasons"]),
            "provenance": list(item.get("provenance", [])),
            **({"graph_score": float(item["graph_score"])} if "graph_score" in item else {}),
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
            "provenance": list(item.get("provenance", [])),
            **({"association": dict(item["association"])} if "association" in item else {}),
            **({"graph_score": float(item["graph_score"])} if "graph_score" in item else {}),
        }
        for item in context_payload.get("test_matches", [])
    }
    for current in related_tests:
        test_matches_by_path.setdefault(
            str(current),
            {
                "path": str(current),
                "score": 1,
                "reasons": ["test-graph"],
                "provenance": _provenance_from_reasons(["test-graph"]),
                "association": {
                    "edge_kind": "import-graph",
                    "confidence": "moderate",
                    "provenance": _provenance_from_reasons(["test-graph"]),
                },
            },
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
    payload["ranking_quality"] = _ranking_quality(payload["file_matches"], payload["test_matches"])
    payload["coverage_summary"] = _coverage_summary(payload)
    payload["semantic_provider"] = _normalize_semantic_provider(semantic_provider)
    payload["provider_agreement"] = dict(defs_payload.get("provider_agreement", default_agreement))
    payload["provider_status"] = dict(defs_payload.get("provider_status", default_status))
    return _attach_profiling(payload, _profiling_collector)


def build_symbol_impact_json(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
) -> str:
    return json.dumps(
        build_symbol_impact(symbol, path, semantic_provider=semantic_provider), indent=2
    )


def build_symbol_refs(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
) -> dict[str, Any]:
    repo_map = build_repo_map(path)
    return build_symbol_refs_from_map(repo_map, symbol, semantic_provider=semantic_provider)


def build_symbol_refs_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
) -> dict[str, Any]:
    payload = build_symbol_defs_from_map(repo_map, symbol, semantic_provider=semantic_provider)
    context_payload = build_context_pack_from_map(repo_map, symbol)
    repo_root = Path(str(repo_map["path"])).resolve()
    bounded_files = _repo_map_file_universe(repo_map)
    bounded_file_set = {str(current) for current in bounded_files}
    references: list[dict[str, Any]] = []
    for current in bounded_files:
        current_provenance = _symbol_navigation_provenance_for_path(str(current))
        if current.suffix == ".py":
            current_refs, _ = _python_references_and_calls(current, symbol)
        elif current.suffix in _JS_TS_SUFFIXES:
            current_refs, current_calls = _js_ts_references_and_calls(current, symbol, repo_root)
            if not current_refs and not current_calls:
                current_calls = _js_ts_provider_alias_calls(current, symbol, repo_root)
            if not current_refs and not current_calls:
                current_refs, current_calls = _regex_references_and_calls(current, symbol)
            js_ts_call_refs = [
                {
                    "name": str(call["name"]),
                    "kind": "reference",
                    "file": str(call["file"]),
                    "line": int(call["line"]),
                    "text": str(call["text"]),
                    "provenance": current_provenance,
                    **(
                        {
                            "resolution_provenance": list(call.get("resolution_provenance", [])),
                            "resolution_confidence": float(call.get("resolution_confidence", 0.95)),
                        }
                        if "resolution_provenance" in call
                        else {}
                    ),
                }
                for call in current_calls
            ]
            current_refs.extend(js_ts_call_refs)
        elif current.suffix in _RUST_SUFFIXES:
            current_refs, current_calls = _rust_references_and_calls(current, symbol, repo_root)
            if not current_refs and not current_calls:
                current_calls = _rust_provider_alias_calls(current, symbol, repo_root)
            if not current_refs and not current_calls:
                current_refs, current_calls = _regex_references_and_calls(current, symbol)
            rust_call_refs = [
                {
                    "name": str(call["name"]),
                    "kind": "reference",
                    "file": str(call["file"]),
                    "line": int(call["line"]),
                    "text": str(call["text"]),
                    "provenance": current_provenance,
                    **(
                        {
                            "resolution_provenance": list(call.get("resolution_provenance", [])),
                            "resolution_confidence": float(call.get("resolution_confidence", 0.95)),
                        }
                        if "resolution_provenance" in call
                        else {}
                    ),
                }
                for call in current_calls
            ]
            current_refs.extend(rust_call_refs)
        else:
            current_refs, _ = _regex_references_and_calls(current, symbol)
        references.extend(
            {
                **dict(current_ref),
                "provenance": str(current_ref.get("provenance", current_provenance)),
            }
            for current_ref in current_refs
        )

    normalized_provider = _normalize_semantic_provider(semantic_provider)
    external_refs: list[dict[str, Any]] = []
    fallback_used = False
    if normalized_provider != "native":
        external_refs = [
            dict(current)
            for current in _external_references(
                repo_root, symbol, [dict(current) for current in payload["definitions"]]
            )
            if str(Path(str(current.get("file", ""))).expanduser().resolve()) in bounded_file_set
        ]
        if normalized_provider == "lsp":
            fallback_used = not bool(external_refs)
            references = external_refs or references
        else:
            merged_refs: dict[tuple[str, int, int], dict[str, Any]] = {}
            for current_ref in [*external_refs, *references]:
                key = (
                    str(current_ref["file"]),
                    int(current_ref["line"]),
                    int(current_ref.get("end_line", current_ref["line"])),
                )
                merged_refs[key] = dict(current_ref)
            references = list(merged_refs.values())
            references.sort(
                key=lambda item: (str(item["file"]), int(item["line"]), str(item.get("text", "")))
            )

    referenced_files = sorted(dict.fromkeys(str(current["file"]) for current in references))
    related_paths: list[str] = []
    for current in [*payload["files"], *referenced_files, *payload["tests"]]:
        if current not in related_paths:
            related_paths.append(current)

    payload["routing_reason"] = "symbol-refs"
    payload["references"] = references
    payload["files"] = referenced_files
    payload["related_paths"] = related_paths
    payload["graph_completeness"] = "moderate"
    payload["ranking_quality"] = _ranking_quality(
        context_payload["file_matches"],
        context_payload["test_matches"],
    )
    payload["coverage_summary"] = _coverage_summary(payload)
    payload["semantic_provider"] = normalized_provider
    payload["provider_agreement"] = _merge_agreement_status(
        semantic_provider=normalized_provider,
        native_count=len(
            [
                current
                for current in references
                if not str(current.get("provenance", "")).startswith("lsp-")
            ]
        ),
        lsp_count=len(external_refs),
        merged_count=len(references),
        fallback_used=fallback_used,
    )
    payload["provider_status"] = _provider_status_snapshot(
        repo_root,
        semantic_provider=normalized_provider,
        languages=_provider_languages_for_symbol(repo_map, symbol, payload["definitions"]),
        fallback_used=fallback_used,
    )
    return payload


def build_symbol_refs_json(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
) -> str:
    return json.dumps(
        build_symbol_refs(symbol, path, semantic_provider=semantic_provider), indent=2
    )


def build_symbol_callers(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    repo_map = build_repo_map(path, _profiling_collector=_profiling_collector)
    return build_symbol_callers_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        _profiling_collector=_profiling_collector,
    )


def build_symbol_callers_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    defs_payload = build_symbol_defs_from_map(repo_map, symbol, semantic_provider=semantic_provider)
    repo_root = Path(str(repo_map["path"])).resolve()
    bounded_files = _repo_map_file_universe(repo_map)
    bounded_file_set = {str(current) for current in bounded_files}
    preferred_definition_files = _preferred_definition_files(repo_map, symbol)
    preferred_definition_file_set = set(preferred_definition_files)
    definitions = [
        dict(current)
        for current in defs_payload["definitions"]
        if str(current["file"]) in preferred_definition_file_set
    ] or [dict(current) for current in defs_payload["definitions"]]
    definition_files = [str(current["file"]) for current in definitions]
    calls: list[dict[str, Any]] = []
    python_files: set[str] = set()
    with _profiling_phase(_profiling_collector, "caller_scan"):
        for current in bounded_files:
            if current.suffix == ".py":
                python_files.add(str(current))
                _, current_calls = _python_references_and_calls(current, symbol)
            elif current.suffix in _JS_TS_SUFFIXES:
                _, current_calls = _js_ts_references_and_calls(current, symbol, repo_root)
                if not current_calls:
                    current_calls = _js_ts_provider_alias_calls(current, symbol, repo_root)
                if not current_calls:
                    _, current_calls = _regex_references_and_calls(current, symbol)
            elif current.suffix in _RUST_SUFFIXES:
                _, current_calls = _rust_references_and_calls(current, symbol, repo_root)
                if not current_calls:
                    current_calls = _rust_provider_alias_calls(current, symbol, repo_root)
                if not current_calls:
                    _, current_calls = _regex_references_and_calls(current, symbol)
            else:
                _, current_calls = _regex_references_and_calls(current, symbol)
            for current_call in current_calls:
                call_payload = dict(current_call)
                call_payload["provenance"] = _symbol_navigation_provenance_for_path(
                    str(current_call["file"])
                )
                calls.append(call_payload)

    normalized_provider = _normalize_semantic_provider(semantic_provider)
    external_calls: list[dict[str, Any]] = []
    fallback_used = False
    if normalized_provider != "native":
        external_refs = [
            dict(current)
            for current in _external_references(
                repo_root, symbol, [dict(current) for current in defs_payload["definitions"]]
            )
            if str(Path(str(current.get("file", ""))).expanduser().resolve()) in bounded_file_set
        ]
        python_external_provenance = {
            str(current["file"]): str(
                current.get("provenance", f"lsp-{_language_for_path(Path(str(current['file'])))}")
            )
            for current in external_refs
            if str(current.get("file", "")).endswith(".py")
        }
        js_ts_external_provenance = {
            str(current["file"]): str(
                current.get("provenance", f"lsp-{_language_for_path(Path(str(current['file'])))}")
            )
            for current in external_refs
            if Path(str(current.get("file", ""))).suffix in _JS_TS_SUFFIXES
        }
        rust_external_provenance = {
            str(current["file"]): str(
                current.get("provenance", f"lsp-{_language_for_path(Path(str(current['file'])))}")
            )
            for current in external_refs
            if Path(str(current.get("file", ""))).suffix in _RUST_SUFFIXES
        }
        python_external_files = {
            str(current["file"])
            for current in external_refs
            if str(current.get("file", "")).endswith(".py")
        }
        js_ts_external_files = {
            str(current["file"])
            for current in external_refs
            if Path(str(current.get("file", ""))).suffix in _JS_TS_SUFFIXES
        }
        rust_external_files = {
            str(current["file"])
            for current in external_refs
            if Path(str(current.get("file", ""))).suffix in _RUST_SUFFIXES
        }
        for external_ref in external_refs:
            text = str(external_ref.get("text", ""))
            if f"{symbol}(" in text or f"{symbol}!" in text or symbol in text:
                external_calls.append(
                    {
                        **dict(external_ref),
                        "kind": "call",
                    }
                )
        for python_file in sorted(python_external_files):
            alias_calls = _python_provider_alias_calls(Path(python_file), symbol)
            for alias_call in alias_calls:
                external_calls.append(
                    {
                        **dict(alias_call),
                        "provenance": python_external_provenance.get(
                            python_file,
                            f"lsp-{_language_for_path(Path(python_file))}",
                        ),
                    }
                )
        for js_ts_file in sorted(js_ts_external_files):
            alias_calls = _js_ts_provider_alias_calls(
                Path(js_ts_file),
                symbol,
                repo_root,
                include_assignment_wrappers=True,
            )
            for alias_call in alias_calls:
                external_calls.append(
                    {
                        **dict(alias_call),
                        "provenance": js_ts_external_provenance.get(
                            js_ts_file,
                            f"lsp-{_language_for_path(Path(js_ts_file))}",
                        ),
                    }
                )
        for rust_file in sorted(rust_external_files):
            alias_calls = _rust_provider_alias_calls(
                Path(rust_file),
                symbol,
                repo_root,
                include_assignment_wrappers=True,
            )
            for alias_call in alias_calls:
                external_calls.append(
                    {
                        **dict(alias_call),
                        "provenance": rust_external_provenance.get(
                            rust_file,
                            f"lsp-{_language_for_path(Path(rust_file))}",
                        ),
                    }
                )
        if not external_calls:
            fallback_used = True
            for python_file in sorted(python_files):
                alias_calls = _python_provider_alias_calls(Path(python_file), symbol)
                for alias_call in alias_calls:
                    external_calls.append(
                        {
                            **dict(alias_call),
                            "provenance": f"lsp-{_language_for_path(Path(python_file))}-fallback",
                        }
                    )
            js_ts_files = sorted(
                str(current)
                for current in bounded_files
                if Path(str(current)).suffix in _JS_TS_SUFFIXES
            )
            for js_ts_file in js_ts_files:
                alias_calls = _js_ts_provider_alias_calls(
                    Path(js_ts_file),
                    symbol,
                    repo_root,
                    include_assignment_wrappers=True,
                )
                for alias_call in alias_calls:
                    external_calls.append(
                        {
                            **dict(alias_call),
                            "provenance": f"lsp-{_language_for_path(Path(js_ts_file))}-fallback",
                        }
                    )
            rust_files = sorted(
                str(current)
                for current in bounded_files
                if Path(str(current)).suffix in _RUST_SUFFIXES
            )
            for rust_file in rust_files:
                alias_calls = _rust_provider_alias_calls(
                    Path(rust_file),
                    symbol,
                    repo_root,
                    include_assignment_wrappers=True,
                )
                for alias_call in alias_calls:
                    external_calls.append(
                        {
                            **dict(alias_call),
                            "provenance": f"lsp-{_language_for_path(Path(rust_file))}-fallback",
                        }
                    )
        if normalized_provider == "lsp":
            calls = external_calls or calls
        else:
            merged_calls: dict[tuple[str, int, int], dict[str, Any]] = {}
            for current_call_entry in [*external_calls, *calls]:
                key = (
                    str(current_call_entry["file"]),
                    int(current_call_entry["line"]),
                    int(current_call_entry.get("end_line", current_call_entry["line"])),
                )
                merged_calls[key] = dict(current_call_entry)
            calls = list(merged_calls.values())
            calls.sort(
                key=lambda item: (str(item["file"]), int(item["line"]), str(item.get("text", "")))
            )

    definition_locations = {
        (
            str(current["file"]),
            int(current.get("line", current.get("start_line", 0)) or 0),
        )
        for current in definitions
    }
    calls = [
        dict(current)
        for current in calls
        if (str(current["file"]), int(current.get("line", 0) or 0)) not in definition_locations
    ]
    caller_files = sorted(dict.fromkeys(str(current["file"]) for current in calls))
    context_payload = build_context_pack_from_map(
        repo_map,
        symbol,
        _profiling_collector=_profiling_collector,
    )
    related_tests = _relevant_tests_for_symbol(
        repo_map,
        symbol,
        definition_files,
        caller_files=caller_files,
        fallback_tests=list(context_payload.get("tests", [])),
        _profiling_collector=_profiling_collector,
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
    payload["graph_completeness"] = "moderate"
    payload["ranking_quality"] = _ranking_quality(
        context_payload["file_matches"],
        context_payload["test_matches"],
    )
    payload["coverage_summary"] = _coverage_summary(payload)
    payload["semantic_provider"] = normalized_provider
    payload["provider_agreement"] = _merge_agreement_status(
        semantic_provider=normalized_provider,
        native_count=len(
            [
                current
                for current in calls
                if not str(current.get("provenance", "")).startswith("lsp-")
            ]
        ),
        lsp_count=len(external_calls),
        merged_count=len(calls),
        fallback_used=fallback_used,
    )
    payload["provider_status"] = _provider_status_snapshot(
        repo_root,
        semantic_provider=normalized_provider,
        languages=_provider_languages_for_symbol(repo_map, symbol, defs_payload["definitions"]),
        fallback_used=fallback_used,
    )
    return _attach_profiling(payload, _profiling_collector)


def build_symbol_callers_json(
    symbol: str,
    path: str | Path = ".",
    *,
    semantic_provider: str = "native",
) -> str:
    return json.dumps(
        build_symbol_callers(symbol, path, semantic_provider=semantic_provider), indent=2
    )


def build_symbol_blast_radius(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    repo_map = build_repo_map(path, _profiling_collector=_profiling_collector)
    return build_symbol_blast_radius_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        semantic_provider=semantic_provider,
        _profiling_collector=_profiling_collector,
    )


def build_symbol_blast_radius_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    max_depth: int = 3,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    defs_payload = build_symbol_defs_from_map(repo_map, symbol, semantic_provider=semantic_provider)
    default_agreement, default_status = _default_provider_metadata(
        Path(str(repo_map["path"])).resolve(),
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        definitions=defs_payload.get("definitions"),
    )
    callers_payload = build_symbol_callers_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        _profiling_collector=_profiling_collector,
    )
    impact_payload = build_symbol_impact_from_map(
        repo_map,
        symbol,
        semantic_provider=semantic_provider,
        _profiling_collector=_profiling_collector,
    )
    preferred_definition_files = _preferred_definition_files(repo_map, symbol)
    preferred_definition_file_set = set(preferred_definition_files)
    definitions = [
        {
            **dict(current),
            "provenance": str(
                current.get(
                    "provenance", _symbol_navigation_provenance_for_path(str(current["file"]))
                )
            ),
        }
        for current in defs_payload["definitions"]
        if str(current["file"]) in preferred_definition_file_set
    ] or [
        {
            **dict(current),
            "provenance": str(
                current.get(
                    "provenance", _symbol_navigation_provenance_for_path(str(current["file"]))
                )
            ),
        }
        for current in defs_payload["definitions"]
    ]

    normalized_depth = max(0, int(max_depth))
    all_files = [str(current) for current in repo_map.get("files", [])]
    import_provenance_by_file = {
        str(current["file"]): str(
            current.get("provenance", _symbol_navigation_provenance_for_path(str(current["file"])))
        )
        for current in repo_map.get("imports", [])
    }
    imports_by_file = {
        str(current["file"]): list(
            dict.fromkeys(
                str(import_name) for import_name in current.get("imports", []) if import_name
            )
        )
        for current in repo_map.get("imports", [])
    }
    reverse_importers = _reverse_importers(
        all_files,
        imports_by_file,
        _profiling_collector=_profiling_collector,
    )
    definition_files = [str(current["file"]) for current in definitions]
    dependency_distances = _reverse_import_distances(
        definition_files,
        all_files,
        imports_by_file,
        _profiling_collector=_profiling_collector,
    )
    reverse_graph_scores = _personalized_reverse_import_pagerank(
        definition_files,
        all_files,
        reverse_importers,
        _profiling_collector=_profiling_collector,
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
            if any(
                alias and alias in import_name.lower()
                for alias in aliases
                for import_name in current_imports
            ):
                coverage_hits += 1
        if coverage_hits <= 0 and not any(
            reason
            in {"import-graph", "graph-centrality", "framework-pattern", "test-graph", "filename"}
            for reason in reasons
        ):
            continue
        score += coverage_hits
        related_tests.append(str(current))
        updated_entry = dict(test_entry)
        updated_entry["path"] = str(current)
        updated_entry["score"] = score
        updated_entry["reasons"] = reasons
        if graph_score > 0.0:
            updated_entry["graph_score"] = graph_score
        test_match_lookup[str(current)] = updated_entry

    caller_tree: list[dict[str, Any]] = []
    rendered_lines = [f"Blast radius for {symbol}:"]
    for depth in range(0, normalized_depth + 1):
        depth_files = [
            str(item["path"])
            for item in ranked_files
            if int(item.get("depth", normalized_depth + 1)) == depth
        ]
        if not depth_files:
            continue
        parser_backed_edges = sum(
            1
            for current in depth_files
            if import_provenance_by_file.get(current) in {"python-ast", "tree-sitter"}
        )
        heuristic_edges = sum(
            1
            for current in depth_files
            if import_provenance_by_file.get(current) in {"regex-heuristic", "heuristic"}
        )
        edge_provenance = ["graph-derived"]
        if parser_backed_edges > 0:
            edge_provenance.append("parser-backed")
        if heuristic_edges > 0:
            edge_provenance.append("heuristic")
        if parser_backed_edges > 0 and heuristic_edges == 0:
            edge_confidence = "strong"
        elif parser_backed_edges > 0:
            edge_confidence = "moderate"
        else:
            edge_confidence = "weak"
        caller_tree.append(
            {
                "depth": depth,
                "files": depth_files,
                "provenance": edge_provenance,
                "graph_completeness": "moderate",
                "edge_summary": {
                    "edge_kind": "reverse-import",
                    "confidence": edge_confidence,
                    "provenance": edge_provenance,
                    "evidence_counts": {
                        "parser_backed": parser_backed_edges,
                        "heuristic": heuristic_edges,
                    },
                },
            }
        )
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
    payload["graph_trust_summary"] = _graph_trust_summary(caller_tree)
    payload["imports"] = impact_payload["imports"]
    payload["symbols"] = impact_payload["symbols"]
    payload["related_paths"] = related_paths
    payload["ranking_quality"] = _ranking_quality(payload["file_matches"], payload["test_matches"])
    payload["coverage_summary"] = _coverage_summary(payload)
    payload["semantic_provider"] = _normalize_semantic_provider(semantic_provider)
    payload["provider_agreement"] = dict(
        callers_payload.get("provider_agreement", default_agreement)
    )
    payload["provider_status"] = dict(callers_payload.get("provider_status", default_status))
    return _attach_profiling(payload, _profiling_collector)


def build_symbol_blast_radius_json(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    semantic_provider: str = "native",
) -> str:
    return json.dumps(
        build_symbol_blast_radius(
            symbol, path, max_depth=max_depth, semantic_provider=semantic_provider
        ),
        indent=2,
    )


def build_symbol_blast_radius_plan(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    repo_map = build_repo_map(path, _profiling_collector=_profiling_collector)
    return build_symbol_blast_radius_plan_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_symbols=max_symbols,
        semantic_provider=semantic_provider,
        _profiling_collector=_profiling_collector,
    )


def build_symbol_blast_radius_plan_from_map(
    repo_map: dict[str, Any],
    symbol: str,
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    payload = build_symbol_blast_radius_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        semantic_provider=semantic_provider,
        _profiling_collector=_profiling_collector,
    )
    normalized_max_files = max(1, max_files)
    normalized_max_symbols = max(1, max_symbols)
    payload["routing_reason"] = "symbol-blast-radius-plan"
    payload["files"] = list(payload.get("files", []))[:normalized_max_files]
    payload["file_matches"] = list(payload.get("file_matches", []))[:normalized_max_files]
    payload["file_summaries"] = list(payload.get("file_summaries", []))[:normalized_max_files]
    payload["tests"] = list(payload.get("tests", []))[:normalized_max_files]
    payload["test_matches"] = list(payload.get("test_matches", []))[:normalized_max_files]
    payload["symbols"] = _sorted_ranked_symbols(list(payload.get("symbols", [])))[
        :normalized_max_symbols
    ]
    payload["max_files"] = normalized_max_files
    payload["max_symbols"] = normalized_max_symbols
    payload = _attach_edit_plan_metadata(
        repo_map,
        payload,
        query=symbol,
        max_files=normalized_max_files,
        max_symbols=normalized_max_symbols,
        max_depth=max_depth,
        blast_radius_payload=payload,
        _profiling_collector=_profiling_collector,
    )
    return _attach_profiling(payload, _profiling_collector)


def build_symbol_blast_radius_plan_json(
    symbol: str,
    path: str | Path = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    semantic_provider: str = "native",
) -> str:
    return json.dumps(
        build_symbol_blast_radius_plan(
            symbol,
            path,
            max_depth=max_depth,
            max_files=max_files,
            max_symbols=max_symbols,
            semantic_provider=semantic_provider,
        ),
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
    profile: bool = False,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    repo_map = build_repo_map(path, _profiling_collector=collector)
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
        profile=profile,
        semantic_provider=semantic_provider,
        _profiling_collector=collector,
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
    profile: bool = False,
    semantic_provider: str = "native",
    _profiling_collector: _ProfileCollector | None = None,
) -> dict[str, Any]:
    collector = _resolve_profiling_collector(profile=profile, collector=_profiling_collector)
    radius_payload = build_symbol_blast_radius_from_map(
        repo_map,
        symbol,
        max_depth=max_depth,
        semantic_provider=semantic_provider,
        _profiling_collector=collector,
    )
    normalized_profile = _normalize_render_profile(render_profile, optimize_context)
    max_files = max(1, max_files)
    max_sources = max(1, max_sources)
    max_symbols_per_file = max(1, max_symbols_per_file)

    top_files = {str(current) for current in radius_payload.get("files", [])[:max_files]}
    sources: list[dict[str, Any]] = []
    seen_symbols: set[tuple[str, str]] = set()
    ranked_symbols = _sorted_ranked_symbols(list(radius_payload.get("symbols", [])))
    for current_symbol in ranked_symbols:
        current_file = str(current_symbol["file"])
        if current_file not in top_files:
            continue
        symbol_key = (current_file, str(current_symbol["name"]))
        if symbol_key in seen_symbols:
            continue
        seen_symbols.add(symbol_key)
        symbol_sources = build_symbol_source_from_map(
            repo_map,
            str(current_symbol["name"]),
            _profiling_collector=collector,
        ).get("sources", [])
        for source in symbol_sources:
            if str(source["file"]) != current_file:
                continue
            sources.append(
                _render_source_block(
                    source,
                    render_profile=normalized_profile,
                    optimize_context=optimize_context,
                    _profiling_collector=collector,
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
    (
        rendered_context,
        sections,
        truncated,
        token_estimate,
        omitted_sections,
    ) = _render_context_string_and_sections(
        payload,
        max_render_chars=max_render_chars,
        _profiling_collector=collector,
    )
    payload["rendered_context"] = rendered_context
    payload["sections"] = sections
    payload["truncated"] = truncated
    payload["token_estimate"] = token_estimate
    payload["omitted_sections"] = omitted_sections
    payload = _attach_edit_plan_metadata(
        repo_map,
        payload,
        query=symbol,
        max_files=max_files,
        max_symbols=max_sources,
        max_depth=max_depth,
        blast_radius_payload=radius_payload,
        _profiling_collector=collector,
    )
    return _attach_profiling(payload, collector)


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
    profile: bool = False,
    semantic_provider: str = "native",
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
            profile=profile,
            semantic_provider=semantic_provider,
        ),
        indent=2,
    )
