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
from collections.abc import Iterable, Mapping, Sequence
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
_TDAD_SCHEMA_VERSION = 1
_SLICE_SCHEMA_VERSION = 1


def _norm_path(path_value: str | Path) -> str:
    return str(path_value).replace("\\", "/").lstrip("./")


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
        if db_path.exists():
            conn.deserialize(read_confined(resolved_root, db_path, _TDAD_DB_LIMIT))
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
) -> int:
    """Persist explicit (source_file, source_symbol, test_file, edge_kind, weight) rows."""
    rows = [
        (
            _norm_path(src_file),
            str(src_sym),
            _norm_path(test_file),
            str(edge_kind),
            float(weight),
        )
        for src_file, src_sym, test_file, edge_kind, weight in edges
        if src_file and test_file
    ]
    conn, resolved_root, db_path = _open_tdad_db(repo_root, create_dirs=True)
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
        publish_confined(resolved_root, db_path, conn.serialize())
        return len(rows)
    finally:
        conn.close()


def _extract_tdad_edges_from_repo_map(
    repo_map_payload: Mapping[str, Any],
) -> list[tuple[str, str, str, str, float]]:
    """Derive source-to-test edges from repo_map files, symbols, imports, and tests."""
    raw_files = [
        _norm_path(str(f))
        for f in (repo_map_payload.get("files") or [])
        if isinstance(f, (str, Path))
    ]
    explicit_tests = {
        _norm_path(str(t))
        for t in (repo_map_payload.get("tests") or [])
        if isinstance(t, (str, Path))
    }
    test_files = sorted({f for f in raw_files if is_test_path(f)} | explicit_tests)
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
        s_file = _norm_path(str(sym.get("file", "")))
        s_name = str(sym.get("name", ""))
        if s_file and s_name and not is_test_path(s_file):
            symbol_to_sources.setdefault(s_name, set()).add(s_file)

    edges: dict[tuple[str, str, str, str], float] = {}

    def add_edge(src_file: str, src_sym: str, test_file: str, kind: str, weight: float) -> None:
        key = (src_file, src_sym, test_file, kind)
        edges[key] = max(edges.get(key, 0.0), weight)

    # 1. Import edges from test files to source modules
    raw_imports = repo_map_payload.get("imports")
    if isinstance(raw_imports, Mapping):
        for test_file_raw, targets in raw_imports.items():
            t_file = _norm_path(str(test_file_raw))
            if not is_test_path(t_file) or not isinstance(targets, Sequence):
                continue
            for target in targets:
                target_str = str(target).strip()
                if not target_str:
                    continue
                target_norm = _norm_path(target_str)
                last_token = target_str.replace("/", ".").split(".")[-1].lower()
                for src in source_files:
                    if src == target_norm or src.endswith(f"/{target_norm}"):
                        add_edge(src, "", t_file, "import", 1.0)
                for src in stem_to_sources.get(last_token, ()):
                    add_edge(src, "", t_file, "import", 0.9)
                for src in symbol_to_sources.get(target_str, ()):
                    add_edge(src, target_str, t_file, "symbol_import", 1.0)

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

    edges = _extract_tdad_edges_from_repo_map(payload)
    persisted = False
    if persist and resolved_root.is_dir():
        record_tdad_edges(resolved_root, edges, replace=True)
        persisted = True

    return {
        "edge_count": len(edges),
        "persisted": persisted,
        "cache_path": str(resolved_root / TDAD_CACHE_REL_PATH).replace("\\", "/"),
    }


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
    norm_files = sorted({_norm_path(f) for f in changed_files if f})
    sym_names = sorted({str(s.get("name", "")) for s in (changed_symbols or ()) if s.get("name")})

    if not norm_files and not sym_names:
        return []

    rows: list[tuple[str, str, str, str, float]] = []
    if db_path.exists():
        conn, _, _ = _open_tdad_db(resolved_root, create_dirs=False)
        try:
            row_count_row = conn.execute("SELECT COUNT(*) FROM source_test_deps").fetchone()
            if row_count_row and int(row_count_row[0]) > 0:
                rows = conn.execute(
                    "SELECT source_file, source_symbol, test_file, edge_kind, weight "
                    "FROM source_test_deps"
                ).fetchall()
        finally:
            conn.close()

    if not rows and repo_map_payload is not None:
        rows = _extract_tdad_edges_from_repo_map(repo_map_payload)
        if persist and resolved_root.is_dir() and rows:
            record_tdad_edges(resolved_root, rows, replace=True)

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
) -> dict[str, Any]:
    """Compute deterministic KG-AST commit-risk features and prioritized test slice."""
    norm_changed = sorted({_norm_path(f) for f in changed_files if f})
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

    caller_files = {_norm_path(str(c.get("file", ""))) for c in callers if c.get("file")}
    fan_in = min(1.0, round(len(callers) / 10.0, 4))

    out_edges = 0
    if isinstance(repo_map_payload, Mapping):
        raw_imports = repo_map_payload.get("imports")
        if isinstance(raw_imports, Mapping):
            for f in norm_changed:
                targets = raw_imports.get(f) or raw_imports.get(f"./{f}") or []
                if isinstance(targets, Sequence):
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

    norm_changed = sorted({_norm_path(f) for f in changed_files if f})
    for f in norm_changed:
        add_node(f"file:{f}", "source", f)

    for sym in changed_symbols:
        s_file = _norm_path(str(sym.get("file", "")))
        s_name = str(sym.get("name", ""))
        if not s_file or not s_name:
            continue
        sym_id = f"sym:{s_file}::{s_name}"
        add_node(f"file:{s_file}", "source", s_file)
        add_node(sym_id, "symbol", s_file, s_name)
        edges_set.add((f"file:{s_file}", sym_id, "defines"))

    for caller in callers:
        c_file = _norm_path(str(caller.get("file", "")))
        c_sym = str(caller.get("caller", caller.get("name", "")))
        target_sym = str(caller.get("target", caller.get("callee", "")))
        if not c_file:
            continue
        c_id = f"sym:{c_file}::{c_sym}" if c_sym else f"file:{c_file}"
        add_node(c_id, "symbol" if c_sym else "source", c_file, c_sym)
        if target_sym:
            for sym in changed_symbols:
                if str(sym.get("name", "")) == target_sym:
                    s_file = _norm_path(str(sym.get("file", "")))
                    edges_set.add((c_id, f"sym:{s_file}::{target_sym}", "calls"))
        elif norm_changed:
            edges_set.add((c_id, f"file:{norm_changed[0]}", "calls"))

    for t in tdad_tests:
        t_file = _norm_path(str(t.get("test_file", "")))
        if not t_file:
            continue
        t_id = f"test:{t_file}"
        add_node(t_id, "test", t_file)
        matched = t.get("matched_sources") or norm_changed[:1]
        for src in matched:
            nsrc = _norm_path(str(src))
            if nsrc:
                add_node(f"file:{nsrc}", "source", nsrc)
                edges_set.add((t_id, f"file:{nsrc}", "covers"))

    for f in sorted({_norm_path(af) for af in affected_files if af}):
        if is_test_path(f):
            add_node(f"test:{f}", "test", f)
            if norm_changed:
                edges_set.add((f"test:{f}", f"file:{norm_changed[0]}", "covers"))

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
    )
    slice_bundle = extract_change_directed_slice(
        changed_files,
        changed_symbols,
        callers,
        affected_files,
        tdad_tests,
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
