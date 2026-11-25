"""TDAD source-to-test dependency map, KG-AST commit risk scoring, and minimal change slices.

Implements three complementary pre-change and review-time intelligence primitives:
1. TDAD (arXiv:2603.17973): Source-to-test pre-change dependency map persisted under
   ``<repo_root>/.tg_cache/graph_v1/tdad_tests.sqlite3`` using confined I/O.
2. KG-AST (arXiv:2611.06950): Deterministic commit-risk scoring from churn/change size,
   graph fan-in/fan-out, co-change coupling, and symbol centrality, yielding prioritized tests.
3. Minimal Change-Directed Graph Slice (arXiv:2607.16024): Bounded deterministic subgraph
   extraction over changed files/symbols/callers/tests with SHA-256 digest binding.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from tensor_grep.io.confined import (
    prepare_directory,
    publish_confined,
    read_confined,
    validate_confined_parent_prefix,
)

TDAD_CACHE_REL_PATH = Path(".tg_cache") / "graph_v1" / "tdad_tests.sqlite3"
_TDAD_DB_LIMIT = 32 * 1024 * 1024
_TDAD_SOURCE_FILE_LIMIT = 16 * 1024 * 1024
_TDAD_SCHEMA_VERSION = 1
_SLICE_SCHEMA_VERSION = 1


def _norm_path(path_value: str | Path, *, repo_root: Path | None = None) -> str:
    raw = str(path_value).replace("\\", "/")
    if repo_root is not None:
        p = Path(path_value)
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(repo_root.resolve())
                return str(rel).replace("\\", "/").lstrip("./")
            except (OSError, ValueError):
                pass
    return raw.lstrip("./")


def _iter_import_records(
    raw_imports: Any,
    *,
    repo_root: Path | None = None,
) -> Iterator[tuple[str, Sequence[Any]]]:
    """Yield (normalized_file, import_targets) from either Mapping or list-of-records."""
    if isinstance(raw_imports, Mapping):
        for file_raw, targets in raw_imports.items():
            if isinstance(targets, Sequence) and not isinstance(targets, (str, bytes)):
                norm_file = _norm_path(str(file_raw), repo_root=repo_root)
                if norm_file:
                    yield norm_file, targets
    elif isinstance(raw_imports, Sequence) and not isinstance(raw_imports, (str, bytes)):
        for entry in raw_imports:
            if not isinstance(entry, Mapping):
                continue
            file_raw = entry.get("file")
            targets = entry.get("imports")
            if targets is None:
                targets = entry.get("targets")
            if file_raw and isinstance(targets, Sequence) and not isinstance(targets, (str, bytes)):
                norm_file = _norm_path(str(file_raw), repo_root=repo_root)
                if norm_file:
                    yield norm_file, targets


def _git_head_revision(repo_root: Path) -> str:
    git_dir = repo_root / ".git"
    try:
        head_raw = (
            read_confined(repo_root, git_dir / "HEAD", 4096).decode("utf-8", "replace").strip()
        )
        if not head_raw:
            return "no-git"
        if head_raw.startswith("ref:"):
            ref_rel = head_raw[4:].strip().lstrip("/")
            if not ref_rel or ".." in Path(ref_rel).parts:
                return "no-git"
            ref_path = git_dir / ref_rel
            try:
                rev = read_confined(repo_root, ref_path, 4096).decode("utf-8", "replace").strip()
                if rev:
                    return rev
            except (OSError, ValueError):
                return head_raw
        return head_raw
    except (OSError, ValueError):
        return "no-git"


def _collect_repo_map_file_paths(
    repo_map_payload: Mapping[str, Any],
    repo_root: Path,
) -> list[str]:
    files: set[str] = set()
    for key in ("files", "tests"):
        for item in repo_map_payload.get(key) or ():
            if isinstance(item, (str, Path)) and str(item):
                files.add(_norm_path(item, repo_root=repo_root))
    for imp_file, _ in _iter_import_records(repo_map_payload.get("imports"), repo_root=repo_root):
        files.add(imp_file)
    for sym in repo_map_payload.get("symbols") or ():
        if isinstance(sym, Mapping) and sym.get("file"):
            files.add(_norm_path(str(sym["file"]), repo_root=repo_root))
    return sorted(f for f in files if f)


def _compute_repo_fingerprint(repo_root: Path, tracked_files: Sequence[str]) -> str:
    resolved_root = Path(repo_root).expanduser().resolve()
    hasher = hashlib.blake2b(digest_size=32)
    hasher.update(_git_head_revision(resolved_root).encode("utf-8"))
    hasher.update(b"\0")
    for rel in sorted({_norm_path(f, repo_root=resolved_root) for f in tracked_files if f}):
        rel_path = Path(rel)
        if (
            rel_path.is_absolute()
            or bool(rel_path.drive)
            or bool(rel_path.root)
            or ".." in rel_path.parts
        ):
            hasher.update(f"{rel}:unconfined\n".encode())
            continue
        fp = resolved_root / rel_path
        try:
            validate_confined_parent_prefix(resolved_root, fp)
            is_link = fp.is_symlink()
        except (OSError, ValueError):
            hasher.update(f"{rel}:unconfined\n".encode())
            continue
        if is_link:
            hasher.update(f"{rel}:unconfined\n".encode())
            continue
        try:
            st = fp.lstat()
        except OSError:
            hasher.update(f"{rel}:missing\n".encode())
            continue
        try:
            content = read_confined(resolved_root, fp, _TDAD_SOURCE_FILE_LIMIT)
            content_tag = hashlib.blake2b(content, digest_size=32).hexdigest()
        except (OSError, ValueError):
            content_tag = "unreadable"
        entry = f"{rel}:{int(st.st_mtime_ns)}:{int(st.st_size)}:{content_tag}\n"
        hasher.update(entry.encode("utf-8"))
    return hasher.hexdigest()


def is_test_path(path_str: str) -> bool:
    """Return True if path matches standard test file/directory conventions."""
    p = Path(_norm_path(path_str))
    if any(part in ("tests", "test", "__tests__") for part in p.parts):
        return True
    name = p.name.lower()
    return name.startswith("test_") or name.endswith((
        "_test.py",
        "_test.go",
        "_test.rs",
        "_test.js",
        "_test.ts",
        ".test.js",
        ".test.ts",
        ".spec.js",
        ".spec.ts",
    ))


def _configure_sqlite(conn: sqlite3.Connection) -> None:
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _TDAD_DB_LIMIT)
    conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
    conn.setlimit(sqlite3.SQLITE_LIMIT_TRIGGER_DEPTH, 0)
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)


def _open_tdad_db(repo_root: Path, *, create_dirs: bool) -> tuple[sqlite3.Connection, Path, Path]:
    resolved_root = Path(repo_root).expanduser().resolve()
    db_path = resolved_root / TDAD_CACHE_REL_PATH
    if create_dirs:
        prepare_directory(resolved_root, db_path.parent)
        gitignore_path = resolved_root / ".tg_cache" / ".gitignore"
        if not gitignore_path.exists():
            publish_confined(resolved_root, gitignore_path, b"*\n", only_if_missing=True)
    else:
        validate_confined_parent_prefix(resolved_root, db_path)

    conn = sqlite3.connect(":memory:")
    _configure_sqlite(conn)
    try:
        loaded_from_disk = False
        if db_path.exists():
            try:
                raw_bytes = read_confined(resolved_root, db_path, _TDAD_DB_LIMIT)
                conn.deserialize(raw_bytes)
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if {"metadata", "source_test_deps"}.issubset(tables):
                    meta_cols = {
                        row[1] for row in conn.execute("PRAGMA table_info(metadata)").fetchall()
                    }
                    dep_cols = {
                        row[1]
                        for row in conn.execute("PRAGMA table_info(source_test_deps)").fetchall()
                    }
                    required_dep_cols = {
                        "source_file",
                        "source_symbol",
                        "test_file",
                        "edge_kind",
                        "weight",
                    }
                    if {"key", "value"}.issubset(meta_cols) and required_dep_cols.issubset(
                        dep_cols
                    ):
                        ver_row = conn.execute(
                            "SELECT value FROM metadata WHERE key = 'schema_version'"
                        ).fetchone()
                        if ver_row is not None and str(ver_row[0]) == str(_TDAD_SCHEMA_VERSION):
                            with conn:
                                conn.execute(
                                    "CREATE INDEX IF NOT EXISTS idx_tdad_src_file "
                                    "ON source_test_deps(source_file)"
                                )
                                conn.execute(
                                    "CREATE INDEX IF NOT EXISTS idx_tdad_src_sym "
                                    "ON source_test_deps(source_symbol)"
                                )
                            loaded_from_disk = True
            except (sqlite3.Error, ValueError, OSError):
                loaded_from_disk = False
            if not loaded_from_disk:
                conn.close()
                conn = sqlite3.connect(":memory:")
                _configure_sqlite(conn)
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_test_deps (
                    source_file TEXT NOT NULL,
                    source_symbol TEXT NOT NULL,
                    test_file TEXT NOT NULL,
                    edge_kind TEXT NOT NULL,
                    weight REAL NOT NULL,
                    PRIMARY KEY (source_file, source_symbol, test_file, edge_kind)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tdad_src_file ON source_test_deps(source_file)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tdad_src_sym ON source_test_deps(source_symbol)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES ('schema_version', ?)",
                (str(_TDAD_SCHEMA_VERSION),),
            )
    except BaseException:
        conn.close()
        raise
    return conn, resolved_root, db_path


def record_tdad_edges(
    repo_root: Path,
    edges: Iterable[tuple[str, str, str, str, float]],
    *,
    replace: bool = False,
    metadata_updates: Mapping[str, str] | None = None,
) -> int:
    """Persist explicit (source_file, source_symbol, test_file, edge_kind, weight) rows."""
    resolved_root = Path(repo_root).expanduser().resolve()
    rows = [
        (
            _norm_path(src_file, repo_root=resolved_root),
            str(src_sym),
            _norm_path(test_file, repo_root=resolved_root),
            str(edge_kind),
            float(weight),
        )
        for src_file, src_sym, test_file, edge_kind, weight in edges
        if src_file and test_file
    ]
    conn, resolved_root, db_path = _open_tdad_db(resolved_root, create_dirs=True)
    try:
        with conn:
            if replace:
                conn.execute("DELETE FROM source_test_deps")
            if rows:
                conn.executemany(
                    """
                    INSERT INTO source_test_deps(
                        source_file, source_symbol, test_file, edge_kind, weight
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source_file, source_symbol, test_file, edge_kind)
                    DO UPDATE SET weight = MAX(source_test_deps.weight, excluded.weight)
                    """,
                    rows,
                )
            if metadata_updates:
                conn.executemany(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                    [(str(k), str(v)) for k, v in metadata_updates.items()],
                )
        db_bytes = conn.serialize()
        if len(db_bytes) > _TDAD_DB_LIMIT:
            raise ValueError(
                f"TDAD cache database exceeds {_TDAD_DB_LIMIT} byte limit ({len(db_bytes)} bytes)"
            )
        publish_confined(resolved_root, db_path, db_bytes)
        return len(rows)
    finally:
        conn.close()


def _extract_tdad_edges_from_repo_map(
    repo_map_payload: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> list[tuple[str, str, str, str, float]]:
    """Derive source-to-test edges from repo_map files, symbols, imports, and tests."""
    raw_files = [
        _norm_path(str(f), repo_root=repo_root)
        for f in (repo_map_payload.get("files") or [])
        if isinstance(f, (str, Path))
    ]
    explicit_tests = {
        _norm_path(str(t), repo_root=repo_root)
        for t in (repo_map_payload.get("tests") or [])
        if isinstance(t, (str, Path))
    }
    import_files = {
        imp_file
        for imp_file, _ in _iter_import_records(
            repo_map_payload.get("imports"), repo_root=repo_root
        )
    }
    test_files = sorted(
        {f for f in raw_files if is_test_path(f)}
        | explicit_tests
        | {f for f in import_files if is_test_path(f)}
    )
    source_files = sorted(f for f in raw_files if f and f not in test_files and not is_test_path(f))

    # Build stem -> source_file lookup for naming-convention and import matching
    stem_to_sources: dict[str, list[str]] = {}
    for src in source_files:
        stem = Path(src).stem.lower()
        if stem and stem != "__init__":
            stem_to_sources.setdefault(stem, []).append(src)

    # Map symbol -> defining source files
    symbol_to_sources: dict[str, set[str]] = {}
    for sym in repo_map_payload.get("symbols") or []:
        if not isinstance(sym, Mapping):
            continue
        s_file = _norm_path(str(sym.get("file", "")), repo_root=repo_root)
        s_name = str(sym.get("name", ""))
        if s_file and s_name and not is_test_path(s_file):
            symbol_to_sources.setdefault(s_name, set()).add(s_file)

    edges: dict[tuple[str, str, str, str], float] = {}

    def add_edge(src_file: str, src_sym: str, test_file: str, kind: str, weight: float) -> None:
        key = (src_file, src_sym, test_file, kind)
        edges[key] = max(edges.get(key, 0.0), weight)

    # 1. Import edges from test files to source modules
    for t_file, targets in _iter_import_records(
        repo_map_payload.get("imports"), repo_root=repo_root
    ):
        if not is_test_path(t_file):
            continue
        for target in targets:
            if isinstance(target, Mapping):
                target_str = str(target.get("module") or target.get("name") or "").strip()
            else:
                target_str = str(target).strip()
            if not target_str:
                continue
            target_norm = _norm_path(target_str)
            dotted_parts = [p.lower() for p in target_str.replace("/", ".").split(".") if p]
            last_token = dotted_parts[-1] if dotted_parts else ""
            for src in source_files:
                if src == target_norm or src.endswith(f"/{target_norm}"):
                    add_edge(src, "", t_file, "import", 1.0)
            if last_token:
                for src in stem_to_sources.get(last_token, ()):
                    add_edge(src, "", t_file, "import", 0.9)
            for src in symbol_to_sources.get(target_str, ()):
                add_edge(src, target_str, t_file, "symbol_import", 1.0)
            if "." in target_str:
                sym_token = target_str.rsplit(".", 1)[-1]
                for src in symbol_to_sources.get(sym_token, ()):
                    src_stem = Path(src).stem.lower()
                    if src_stem in dotted_parts:
                        add_edge(src, sym_token, t_file, "symbol_import", 1.0)

    # 2. Naming convention coupling (e.g. foo.py <-> test_foo.py / foo_test.py)
    for t_file in test_files:
        t_stem = Path(t_file).stem.lower()
        candidates: list[str] = []
        if t_stem.startswith("test_"):
            candidates.append(t_stem[5:])
        for suffix in ("_test", ".test", ".spec"):
            if t_stem.endswith(suffix):
                candidates.append(t_stem[: -len(suffix)])
        for cand in candidates:
            for src in stem_to_sources.get(cand, ()):
                add_edge(src, "", t_file, "naming", 0.6)

    return [(k[0], k[1], k[2], k[3], w) for k, w in sorted(edges.items())]


def build_tdad_dependency_map(
    repo_root: Path,
    repo_map_payload: Mapping[str, Any] | None = None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """Build (and optionally persist) the TDAD source-to-test dependency map."""
    resolved_root = Path(repo_root).expanduser().resolve()
    payload = repo_map_payload
    if payload is None:
        from tensor_grep.cli import repo_map as _repo_map

        payload = _repo_map.build_repo_map(resolved_root)

    edges = _extract_tdad_edges_from_repo_map(payload, repo_root=resolved_root)
    tracked_files = _collect_repo_map_file_paths(payload, resolved_root)
    fingerprint = _compute_repo_fingerprint(resolved_root, tracked_files)
    persisted = False
    if persist and resolved_root.is_dir():
        record_tdad_edges(
            resolved_root,
            edges,
            replace=True,
            metadata_updates={
                "repo_fingerprint": fingerprint,
                "tracked_files": json.dumps(tracked_files, separators=(",", ":")),
            },
        )
        persisted = True

    return {
        "edge_count": len(edges),
        "persisted": persisted,
        "repo_fingerprint": fingerprint,
        "cache_path": str(resolved_root / TDAD_CACHE_REL_PATH).replace("\\", "/"),
    }


def _read_tdad_cache_state(
    resolved_root: Path,
) -> tuple[list[tuple[str, str, str, str, float]], str | None, list[str]]:
    conn, _, _ = _open_tdad_db(resolved_root, create_dirs=False)
    try:
        rows = conn.execute(
            "SELECT source_file, source_symbol, test_file, edge_kind, weight FROM source_test_deps"
        ).fetchall()
        meta_rows = dict(conn.execute("SELECT key, value FROM metadata").fetchall())
    finally:
        conn.close()
    stored_fp = meta_rows.get("repo_fingerprint")
    tracked_files: list[str] = []
    raw_tracked = meta_rows.get("tracked_files")
    if raw_tracked:
        try:
            parsed = json.loads(raw_tracked)
            if isinstance(parsed, list):
                tracked_files = [str(x) for x in parsed if isinstance(x, str)]
        except ValueError:
            tracked_files = []
    return rows, stored_fp, tracked_files


def ensure_tdad_dependency_map(
    repo_root: Path,
    repo_map_payload: Mapping[str, Any] | None = None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """Refresh TDAD cache only if missing or invalidated by revision/file-stat changes."""
    resolved_root = Path(repo_root).expanduser().resolve()
    db_path = resolved_root / TDAD_CACHE_REL_PATH
    if db_path.exists():
        rows, stored_fp, tracked_files = _read_tdad_cache_state(resolved_root)
        if stored_fp:
            files_to_check = (
                _collect_repo_map_file_paths(repo_map_payload, resolved_root)
                if repo_map_payload is not None
                else tracked_files
            )
            current_fp = _compute_repo_fingerprint(resolved_root, files_to_check)
            if current_fp == stored_fp:
                return {
                    "edge_count": len(rows),
                    "persisted": True,
                    "cache_hit": True,
                    "repo_fingerprint": stored_fp,
                    "cache_path": str(db_path).replace("\\", "/"),
                }
    result = build_tdad_dependency_map(
        resolved_root,
        repo_map_payload=repo_map_payload,
        persist=persist,
    )
    result["cache_hit"] = False
    return result


def query_tdad_tests(
    repo_root: Path,
    changed_files: Sequence[str],
    changed_symbols: Sequence[Mapping[str, Any]] | None = None,
    *,
    repo_map_payload: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> list[dict[str, Any]]:
    """Query covering test files for changed_files / changed_symbols from TDAD cache."""
    resolved_root = Path(repo_root).expanduser().resolve()
    db_path = resolved_root / TDAD_CACHE_REL_PATH
    norm_files = sorted({_norm_path(f, repo_root=resolved_root) for f in changed_files if f})
    sym_names = sorted({str(s.get("name", "")) for s in (changed_symbols or ()) if s.get("name")})

    if not norm_files and not sym_names:
        return []

    rows: list[tuple[str, str, str, str, float]] = []
    cache_stale = False
    has_fingerprint = False
    if db_path.exists():
        rows, stored_fp, tracked_files = _read_tdad_cache_state(resolved_root)
        if stored_fp:
            has_fingerprint = True
            files_to_check = (
                _collect_repo_map_file_paths(repo_map_payload, resolved_root)
                if repo_map_payload is not None
                else sorted(set(tracked_files) | set(norm_files))
            )
            current_fp = _compute_repo_fingerprint(resolved_root, files_to_check)
            if current_fp != stored_fp:
                cache_stale = True
                rows = []

    if cache_stale or (not db_path.exists()) or (not rows and not has_fingerprint):
        if repo_map_payload is not None:
            rows = _extract_tdad_edges_from_repo_map(repo_map_payload, repo_root=resolved_root)
            if persist and resolved_root.is_dir():
                tracked_files = _collect_repo_map_file_paths(repo_map_payload, resolved_root)
                fingerprint = _compute_repo_fingerprint(resolved_root, tracked_files)
                record_tdad_edges(
                    resolved_root,
                    rows,
                    replace=True,
                    metadata_updates={
                        "repo_fingerprint": fingerprint,
                        "tracked_files": json.dumps(tracked_files, separators=(",", ":")),
                    },
                )
        elif (cache_stale or (db_path.exists() and not has_fingerprint)) and resolved_root.is_dir():
            build_tdad_dependency_map(resolved_root, repo_map_payload=None, persist=persist)
            if db_path.exists():
                rows, _, _ = _read_tdad_cache_state(resolved_root)

    changed_file_set = set(norm_files)
    changed_sym_set = set(sym_names)
    aggregated: dict[str, dict[str, Any]] = {}

    for src_file, src_sym, test_file, edge_kind, weight in rows:
        file_hit = src_file in changed_file_set
        sym_hit = bool(src_sym and src_sym in changed_sym_set)
        if not file_hit and not sym_hit:
            continue
        entry = aggregated.setdefault(
            test_file,
            {
                "test_file": test_file,
                "score": 0.0,
                "reasons": set(),
                "matched_sources": set(),
                "matched_symbols": set(),
            },
        )
        entry["score"] += float(weight) * (1.25 if sym_hit and file_hit else 1.0)
        entry["reasons"].add(edge_kind)
        if src_file:
            entry["matched_sources"].add(src_file)
        if src_sym:
            entry["matched_symbols"].add(src_sym)

    # Include any changed test files directly with base weight 1.0
    for f in norm_files:
        if is_test_path(f):
            entry = aggregated.setdefault(
                f,
                {
                    "test_file": f,
                    "score": 0.0,
                    "reasons": set(),
                    "matched_sources": set(),
                    "matched_symbols": set(),
                },
            )
            entry["score"] = max(float(entry["score"]), 1.0)
            entry["reasons"].add("direct_test_change")

    results = [
        {
            "test_file": item["test_file"],
            "score": round(float(item["score"]), 4),
            "reasons": sorted(item["reasons"]),
            "matched_sources": sorted(item["matched_sources"]),
            "matched_symbols": sorted(item["matched_symbols"]),
        }
        for item in aggregated.values()
    ]
    results.sort(key=lambda item: (-float(item["score"]), str(item["test_file"])))
    return results


def score_kg_ast_commit_risk(
    changed_files: Sequence[str],
    changed_symbols: Sequence[Mapping[str, Any]],
    callers: Sequence[Mapping[str, Any]],
    affected_files: Sequence[str],
    tdad_tests: Sequence[Mapping[str, Any]],
    *,
    changed_line_counts: Mapping[str, int] | None = None,
    repo_map_payload: Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Compute deterministic KG-AST commit-risk features and prioritized test slice."""
    norm_changed = sorted({_norm_path(f, repo_root=repo_root) for f in changed_files if f})
    if not norm_changed:
        return {
            "risk_score": 0.0,
            "risk_level": "low",
            "features": {
                "churn_size": 0.0,
                "fan_in": 0.0,
                "fan_out": 0.0,
                "symbol_centrality": 0.0,
                "co_change_coupling": 0.0,
            },
            "prioritized_tests": [],
            "file_risks": [],
        }

    total_lines = (
        sum(max(0, int(changed_line_counts.get(f, 0))) for f in norm_changed)
        if changed_line_counts
        else len(norm_changed) * 10 + len(changed_symbols) * 5
    )
    churn_size = min(1.0, round(total_lines / 200.0, 4))

    caller_files = {
        _norm_path(str(c.get("file", "")), repo_root=repo_root) for c in callers if c.get("file")
    }
    fan_in = min(1.0, round(len(callers) / 10.0, 4))

    out_edges = 0
    if isinstance(repo_map_payload, Mapping):
        changed_set = set(norm_changed)
        for imp_file, targets in _iter_import_records(
            repo_map_payload.get("imports"), repo_root=repo_root
        ):
            if imp_file in changed_set or any(imp_file.endswith(f"/{cf}") for cf in changed_set):
                out_edges += len(targets)
    fan_out_count = max(len(set(affected_files) - set(norm_changed)), out_edges)
    fan_out = min(1.0, round(fan_out_count / 10.0, 4))

    sym_count = max(1, len(changed_symbols))
    symbol_centrality = min(
        1.0, round((len(caller_files) + len(callers) * 0.5) / (sym_count * 4.0), 4)
    )

    co_change_coupling = min(
        1.0,
        round((max(0, len(norm_changed) - 1) * 0.25) + (len(tdad_tests) * 0.15), 4),
    )

    risk_score = round(
        min(
            1.0,
            0.25 * churn_size
            + 0.30 * fan_in
            + 0.20 * fan_out
            + 0.15 * symbol_centrality
            + 0.10 * co_change_coupling,
        ),
        4,
    )
    if risk_score >= 0.75:
        risk_level = "critical"
    elif risk_score >= 0.45:
        risk_level = "high"
    elif risk_score >= 0.20:
        risk_level = "medium"
    else:
        risk_level = "low"

    # Per-file breakdown
    callers_by_file: dict[str, int] = {}
    for sym in changed_symbols:
        s_file = _norm_path(str(sym.get("file", "")))
        if s_file:
            callers_by_file[s_file] = callers_by_file.get(s_file, 0) + 1
    file_risks: list[dict[str, Any]] = []
    for f in norm_changed:
        sym_hits = callers_by_file.get(f, 0)
        f_score = round(
            min(1.0, (0.4 * churn_size) + (0.4 * fan_in) + (0.2 * min(1.0, sym_hits / 5.0))),
            4,
        )
        file_risks.append({"file": f, "risk_score": f_score, "changed_symbols": sym_hits})
    file_risks.sort(key=lambda item: (-float(item["risk_score"]), str(item["file"])))

    prioritized_tests = [
        str(item["test_file"])
        for item in tdad_tests
        if isinstance(item, Mapping) and item.get("test_file")
    ]
    for f in sorted(affected_files):
        nf = _norm_path(f)
        if is_test_path(nf) and nf not in prioritized_tests:
            prioritized_tests.append(nf)

    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "features": {
            "churn_size": churn_size,
            "fan_in": fan_in,
            "fan_out": fan_out,
            "symbol_centrality": symbol_centrality,
            "co_change_coupling": co_change_coupling,
        },
        "prioritized_tests": prioritized_tests,
        "file_risks": file_risks,
    }


def _canonical_slice_bytes(slice_body: Mapping[str, Any]) -> bytes:
    return json.dumps(slice_body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def extract_change_directed_slice(
    changed_files: Sequence[str],
    changed_symbols: Sequence[Mapping[str, Any]],
    callers: Sequence[Mapping[str, Any]],
    affected_files: Sequence[str],
    tdad_tests: Sequence[Mapping[str, Any]],
    *,
    max_nodes: int = 128,
    max_edges: int = 256,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Extract a minimal deterministic change-directed graph slice with SHA-256 digest."""
    nodes_map: dict[str, dict[str, str]] = {}
    edges_set: set[tuple[str, str, str]] = set()

    def add_node(node_id: str, kind: str, file_path: str, symbol: str = "") -> None:
        if node_id not in nodes_map:
            nodes_map[node_id] = {
                "id": node_id,
                "kind": kind,
                "file": file_path,
                "symbol": symbol,
            }

    norm_changed = sorted({_norm_path(f, repo_root=repo_root) for f in changed_files if f})
    norm_changed_set = set(norm_changed)
    for f in norm_changed:
        add_node(f"file:{f}", "source", f)

    symbol_files_by_name: dict[str, list[str]] = {}
    for sym in changed_symbols:
        s_file = _norm_path(str(sym.get("file", "")), repo_root=repo_root)
        s_name = str(sym.get("name", ""))
        if not s_file or not s_name:
            continue
        sym_id = f"sym:{s_file}::{s_name}"
        add_node(f"file:{s_file}", "source", s_file)
        add_node(sym_id, "symbol", s_file, s_name)
        edges_set.add((f"file:{s_file}", sym_id, "defines"))
        symbol_files_by_name.setdefault(s_name, []).append(s_file)

    for caller in callers:
        c_file = _norm_path(str(caller.get("file", "")), repo_root=repo_root)
        c_sym = str(caller.get("caller", caller.get("name", "")))
        target_sym = str(caller.get("target", caller.get("callee", caller.get("symbol", ""))))
        raw_target_file = caller.get("target_file")
        target_file = (
            _norm_path(str(raw_target_file), repo_root=repo_root) if raw_target_file else ""
        )
        if not c_file:
            continue
        c_id = f"sym:{c_file}::{c_sym}" if c_sym else f"file:{c_file}"
        matched_edge = False
        if target_sym:
            for s_file in symbol_files_by_name.get(target_sym, ()):
                if target_file and s_file != target_file:
                    continue
                add_node(c_id, "symbol" if c_sym else "source", c_file, c_sym)
                edges_set.add((c_id, f"sym:{s_file}::{target_sym}", "calls"))
                matched_edge = True
        if not matched_edge and target_file and target_file in norm_changed_set:
            add_node(c_id, "symbol" if c_sym else "source", c_file, c_sym)
            edges_set.add((c_id, f"file:{target_file}", "calls"))

    for t in tdad_tests:
        t_file = _norm_path(str(t.get("test_file", "")), repo_root=repo_root)
        if not t_file:
            continue
        t_id = f"test:{t_file}"
        add_node(t_id, "test", t_file)
        matched = t.get("matched_sources") or ()
        for src in matched:
            nsrc = _norm_path(str(src), repo_root=repo_root)
            if nsrc:
                add_node(f"file:{nsrc}", "source", nsrc)
                edges_set.add((t_id, f"file:{nsrc}", "covers"))
        if not matched:
            for sym_name in t.get("matched_symbols") or ():
                for s_file in symbol_files_by_name.get(str(sym_name), ()):
                    add_node(f"file:{s_file}", "source", s_file)
                    edges_set.add((t_id, f"file:{s_file}", "covers"))

    for f in sorted({_norm_path(af, repo_root=repo_root) for af in affected_files if af}):
        if is_test_path(f):
            add_node(f"test:{f}", "test", f)

    sorted_nodes = [nodes_map[k] for k in sorted(nodes_map)]
    sorted_edges = [{"src": s, "dst": d, "kind": k} for s, d, k in sorted(edges_set)]
    truncated = len(sorted_nodes) > max_nodes or len(sorted_edges) > max_edges
    bounded_nodes = sorted_nodes[:max_nodes]
    kept_ids = {n["id"] for n in bounded_nodes}
    bounded_edges = [e for e in sorted_edges if e["src"] in kept_ids and e["dst"] in kept_ids][
        :max_edges
    ]

    slice_body: dict[str, Any] = {
        "schema_version": _SLICE_SCHEMA_VERSION,
        "changed_files": norm_changed,
        "nodes": bounded_nodes,
        "edges": bounded_edges,
        "truncated": truncated,
    }
    slice_digest = hashlib.sha256(_canonical_slice_bytes(slice_body)).hexdigest()
    return {"slice": slice_body, "slice_digest": slice_digest}


def verify_change_slice_digest(change_slice: Mapping[str, Any], slice_digest: str) -> bool:
    """Verify a change-directed graph slice against its SHA-256 slice_digest."""
    if (
        not isinstance(change_slice, Mapping)
        or not isinstance(slice_digest, str)
        or not slice_digest
    ):
        return False
    actual = hashlib.sha256(_canonical_slice_bytes(change_slice)).hexdigest()
    return hmac.compare_digest(
        actual.encode("utf-8", "surrogatepass"),
        slice_digest.encode("utf-8", "surrogatepass"),
    )


def enrich_diff_impact_payload(
    payload: dict[str, Any],
    *,
    root: Path,
    repo_map_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach additive tdad_tests, kg_ast_risk, change_slice, and slice_digest fields."""
    changed_files = [str(f) for f in (payload.get("changed_files") or ())]
    changed_symbols = [s for s in (payload.get("changed_symbols") or ()) if isinstance(s, Mapping)]
    callers = [c for c in (payload.get("callers") or ()) if isinstance(c, Mapping)]
    affected_files = [str(f) for f in (payload.get("affected_files") or ())]

    tdad_tests = query_tdad_tests(
        root,
        changed_files,
        changed_symbols,
        repo_map_payload=repo_map_payload,
        persist=True,
    )
    kg_ast_risk = score_kg_ast_commit_risk(
        changed_files,
        changed_symbols,
        callers,
        affected_files,
        tdad_tests,
        repo_map_payload=repo_map_payload,
        repo_root=root,
    )
    slice_bundle = extract_change_directed_slice(
        changed_files,
        changed_symbols,
        callers,
        affected_files,
        tdad_tests,
        repo_root=root,
    )
    payload["tdad_tests"] = tdad_tests
    payload["kg_ast_risk"] = kg_ast_risk
    payload["change_slice"] = slice_bundle["slice"]
    payload["slice_digest"] = slice_bundle["slice_digest"]
    return payload


def build_change_slice_from_manifest(
    manifest: Mapping[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    """Build a minimal change-directed graph slice from an audit manifest for review bundles."""
    raw_files = manifest.get("files")
    changed_files: list[str] = []
    if isinstance(raw_files, Sequence):
        for item in raw_files:
            if isinstance(item, Mapping) and item.get("path"):
                changed_files.append(_norm_path(str(item["path"])))
            elif isinstance(item, str):
                changed_files.append(_norm_path(item))
    changed_files = sorted(set(changed_files))
    tdad_tests = (
        query_tdad_tests(root, changed_files, persist=False)
        if changed_files and root.is_dir()
        else []
    )
    return extract_change_directed_slice(
        changed_files,
        [],
        [],
        [str(t["test_file"]) for t in tdad_tests],
        tdad_tests,
    )
