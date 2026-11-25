"""Content-hashed (SHA-256) incremental AST/symbol cache and Python import target resolver."""

from __future__ import annotations

import ast
import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CachedFileEntry:
    """Content-hashed AST extraction summary for a single Python source file."""

    path: str
    sha256: str
    imports: list[dict[str, Any]]
    symbols: list[dict[str, Any]]


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def _normalized_path_str(path_str: str) -> Path:
    p = Path(path_str)
    try:
        return p.resolve()
    except OSError:
        return p.absolute()


def _normalized_path(path: Path | str) -> Path:
    return _normalized_path_str(str(path))


def _extract_imports_and_symbols(
    tree: ast.Module,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    imports: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    "module": alias.name,
                    "line": int(node.lineno),
                    "level": 0,
                    "names": [],
                })
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names if alias.name != "*"]
            if node.module:
                imports.append({
                    "module": node.module,
                    "line": int(node.lineno),
                    "level": int(node.level or 0),
                    "names": names,
                })
            elif node.level:
                for name in names:
                    imports.append({
                        "module": name,
                        "line": int(node.lineno),
                        "level": int(node.level),
                        "names": [],
                    })
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            symbols.append({
                "name": node.name,
                "kind": kind,
                "line": int(node.lineno),
            })
    symbols.sort(key=lambda s: (int(s["line"]), str(s["name"])))
    return imports, symbols


class IncrementalSymbolCache:
    """Thread-safe SHA-256 content-hashed AST and symbol/import cache with JSON persistence."""

    def __init__(self, cache_path: Path | str | None = None) -> None:
        self._cache_path: Path | None = Path(cache_path) if cache_path is not None else None
        self._lock = threading.Lock()
        self._ast_cache: dict[str, tuple[str, ast.Module]] = {}
        self._entries: dict[str, CachedFileEntry] = {}
        self.hits = 0
        self.misses = 0
        if self._cache_path is not None and self._cache_path.is_file():
            self.load()

    def _read_source_and_digest(self, path: Path, source: str | None) -> tuple[str, str]:
        if source is not None:
            raw = source.encode("utf-8")
            return source, sha256_bytes(raw)
        raw = path.read_bytes()
        return raw.decode("utf-8", errors="replace"), sha256_bytes(raw)

    def get_ast(self, path: Path | str, source: str | None = None) -> tuple[ast.Module, bool]:
        """Return `(ast.Module, cache_hit)` for `path`, invalidating automatically on SHA-256 change."""
        resolved = _normalized_path(path)
        key = str(resolved)
        text, digest = self._read_source_and_digest(resolved, source)
        with self._lock:
            cached = self._ast_cache.get(key)
            if cached is not None and cached[0] == digest:
                self.hits += 1
                return cached[1], True
        tree = ast.parse(text, filename=key)
        with self._lock:
            self._ast_cache[key] = (digest, tree)
            self.misses += 1
        return tree, False

    def get_file_entry(
        self, path: Path | str, source: str | None = None
    ) -> tuple[CachedFileEntry, bool]:
        """Return `(CachedFileEntry, cache_hit)` for `path`, recomputing only when SHA-256 changes."""
        resolved = _normalized_path(path)
        key = str(resolved)
        text, digest = self._read_source_and_digest(resolved, source)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and cached.sha256 == digest:
                self.hits += 1
                return cached, True
        tree = ast.parse(text, filename=key)
        imports, symbols = _extract_imports_and_symbols(tree)
        entry = CachedFileEntry(path=key, sha256=digest, imports=imports, symbols=symbols)
        with self._lock:
            self._ast_cache[key] = (digest, tree)
            self._entries[key] = entry
            self.misses += 1
        return entry, False

    def invalidate(self, path: Path | str) -> None:
        """Evict cached AST and symbol entries for `path`."""
        key = str(_normalized_path(path))
        with self._lock:
            self._ast_cache.pop(key, None)
            self._entries.pop(key, None)

    def clear(self) -> None:
        """Clear all in-memory cached entries and counters."""
        with self._lock:
            self._ast_cache.clear()
            self._entries.clear()
            self.hits = 0
            self.misses = 0

    def save(self) -> None:
        """Persist symbol/import entries atomically to `cache_path`."""
        from tensor_grep.cli._index_lock import atomic_write_json

        if self._cache_path is None:
            return
        with self._lock:
            payload = {
                "version": 1,
                "entries": {k: asdict(v) for k, v in self._entries.items()},
            }
            atomic_write_json(self._cache_path, payload)

    def load(self) -> None:
        """Load persisted symbol/import entries from `cache_path` if valid."""
        if self._cache_path is None or not self._cache_path.is_file():
            return
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return
        entries_raw = raw.get("entries")
        if not isinstance(entries_raw, dict):
            return
        loaded: dict[str, CachedFileEntry] = {}
        for key, item in entries_raw.items():
            if not isinstance(item, dict):
                continue
            sha = item.get("sha256")
            imports = item.get("imports")
            symbols = item.get("symbols")
            if isinstance(sha, str) and isinstance(imports, list) and isinstance(symbols, list):
                loaded[str(key)] = CachedFileEntry(
                    path=str(item.get("path", key)),
                    sha256=sha,
                    imports=imports,
                    symbols=symbols,
                )
        with self._lock:
            self._entries.update(loaded)


def _is_within_root(candidate: Path, root: Path | None) -> bool:
    if root is None:
        return True
    try:
        return candidate.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _probe_module_file(base_dir: Path, parts: list[str], root: Path | None) -> Path | None:
    if not parts:
        init_file = base_dir / "__init__.py"
        if init_file.is_file() and _is_within_root(init_file, root):
            return _normalized_path(init_file)
        return None
    py_file = base_dir.joinpath(*parts).with_suffix(".py")
    if py_file.is_file() and _is_within_root(py_file, root):
        return _normalized_path(py_file)
    pkg_init = base_dir.joinpath(*parts, "__init__.py")
    if pkg_init.is_file() and _is_within_root(pkg_init, root):
        return _normalized_path(pkg_init)
    return None


def _candidate_package_roots(importer: Path, root: Path | None) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path | None) -> None:
        if p is None:
            return
        norm = _normalized_path(p)
        key = str(norm)
        if key not in seen:
            seen.add(key)
            roots.append(norm)

    if root is not None:
        _add(root)
        src_dir = root / "src"
        if src_dir.is_dir():
            _add(src_dir)

    current = importer.parent
    _add(current)
    if root is not None and _is_within_root(current, root):
        while current != root and current.parent != current:
            current = current.parent
            _add(current)

    pkg_top = importer.parent
    while (pkg_top / "__init__.py").is_file():
        parent = pkg_top.parent
        if parent == pkg_top:
            break
        pkg_top = parent
    _add(pkg_top)
    return roots


def resolve_python_import_target(
    importer_path: Path | str,
    module_name: str,
    repo_root: Path | str | None = None,
    *,
    level: int = 0,
    imported_names: tuple[str, ...] | list[str] = (),
) -> Path | None:
    """Resolve an intra-package or relative Python import to a local `.py` file path.

    Handles both relative imports (`from . import detail_tier`, `from ..cli import detail_tier`)
    and intra-package absolute imports (`from tensor_grep.cli import detail_tier`,
    `import tensor_grep.cli.detail_tier`), returning `None` when no local file exists.
    """
    importer = _normalized_path(importer_path)
    root = _normalized_path(repo_root) if repo_root is not None else None
    parts = [part for part in module_name.split(".") if part]
    clean_names = [name for name in imported_names if name and name != "*"]

    if level > 0:
        base_dir = importer.parent
        for _ in range(max(0, level - 1)):
            base_dir = base_dir.parent
        for name in clean_names:
            hit = _probe_module_file(base_dir, [*parts, name], root)
            if hit is not None:
                return hit
        return _probe_module_file(base_dir, parts, root)

    if not parts:
        return None

    for search_root in _candidate_package_roots(importer, root):
        for name in clean_names:
            hit = _probe_module_file(search_root, [*parts, name], root)
            if hit is not None:
                return hit
        hit = _probe_module_file(search_root, parts, root)
        if hit is not None:
            return hit
    return None


_DEFAULT_SYMBOL_CACHE = IncrementalSymbolCache()


def get_default_symbol_cache() -> IncrementalSymbolCache:
    """Return the process-wide default `IncrementalSymbolCache` instance."""
    return _DEFAULT_SYMBOL_CACHE
