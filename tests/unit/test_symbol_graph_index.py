"""Tests for SymbolGraphIndex (Wave 3 CL 3.2) and _python_candidate_roots memoization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map, repo_map_lang_python
from tensor_grep.cli.repo_map_cache import _MTIME_CACHE_CLEAR_REGISTRY
from tensor_grep.cli.symbol_graph_index import (
    GRAPH_CACHE_REL_PATH,
    STDLIB_BUILTIN_NOISE,
    SymbolGraphIndex,
)


def test_symbol_graph_index_recursive_callers_multihop() -> None:
    with SymbolGraphIndex(merkle_root="root-1") as index:
        inserted = index.insert_edges([
            {
                "src_file": "a.py",
                "src_symbol": "A",
                "dst_file": "b.py",
                "dst_symbol": "B",
                "edge_kind": "call",
                "line": 10,
                "provenance": "python-ast",
            },
            {
                "src_file": "b.py",
                "src_symbol": "B",
                "dst_file": "c.py",
                "dst_symbol": "C",
                "edge_kind": "call",
                "line": 22,
                "provenance": "python-ast",
            },
        ])
        assert inserted == 2

        depth1 = index.recursive_callers("C", max_depth=1)
        assert [(row["src_symbol"], row["depth"]) for row in depth1] == [("B", 1)]

        depth3 = index.recursive_callers("C", max_depth=3)
        assert [(row["src_symbol"], row["depth"]) for row in depth3] == [("B", 1), ("A", 2)]


def test_symbol_graph_index_cycle_safety() -> None:
    with SymbolGraphIndex() as index:
        index.insert_edges([
            ("a.py", "A", "b.py", "B", "call", 5, "python-ast"),
            ("b.py", "B", "a.py", "A", "call", 9, "python-ast"),
            ("a.py", "A", "a.py", "A", "call", 12, "python-ast"),
        ])

        results = index.recursive_callers("A", max_depth=5)
        callers = {(row["src_file"], row["src_symbol"], row["depth"]) for row in results}
        assert ("b.py", "B", 1) in callers
        assert ("a.py", "A", 1) in callers
        assert len(results) <= 2


def test_symbol_graph_index_filters_stdlib_builtin_noise() -> None:
    for expected_noise in ("len", "str", "int", "print", "isinstance", "append", "get", "items"):
        assert expected_noise in STDLIB_BUILTIN_NOISE

    with SymbolGraphIndex() as index:
        inserted = index.insert_edges([
            ("a.py", "A", "", "len", "call", 1, "python-ast"),
            ("a.py", "A", "", "print", "call", 2, "python-ast"),
            ("a.py", "A", "", "append", "call", 3, "python-ast"),
            ("a.py", "A", "repo.py", "get", "call", 4, "python-ast"),
            ("a.py", "A", "b.py", "domain_helper", "call", 5, "python-ast"),
        ])
        assert inserted == 2
        assert index.recursive_callers("len") == []
        assert [row["src_symbol"] for row in index.recursive_callers("get")] == ["A"]
        assert [row["src_symbol"] for row in index.recursive_callers("domain_helper")] == ["A"]


def test_symbol_graph_index_serialize_deserialize_and_merkle_validation() -> None:
    with SymbolGraphIndex(merkle_root="merkle-abc") as index:
        index.insert_edges([
            ("a.py", "A", "b.py", "B", "call", 7, "python-ast"),
            ("b.py", "B", "c.py", "C", "call", 14, "python-ast"),
        ])
        blob = index.serialize()

    with SymbolGraphIndex.deserialize(blob, expected_merkle_root="merkle-abc") as restored:
        assert restored.merkle_root == "merkle-abc"
        assert [(row["src_symbol"], row["depth"]) for row in restored.recursive_callers("C")] == [
            ("B", 1),
            ("A", 2),
        ]

    with pytest.raises(ValueError, match="merkle_root mismatch"):
        SymbolGraphIndex.deserialize(blob, expected_merkle_root="merkle-other")

    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        SymbolGraphIndex.deserialize(b"not-a-sqlite-db")


def test_symbol_graph_index_cache_persistence_under_tg_cache(tmp_path: Path) -> None:
    with SymbolGraphIndex(merkle_root="merkle-disk") as index:
        index.insert_edges([("a.py", "A", "b.py", "B", "call", 3, "python-ast")])
        saved = index.save_to_cache(tmp_path)

    assert saved == tmp_path / GRAPH_CACHE_REL_PATH
    assert saved.is_file()

    loaded = SymbolGraphIndex.load_from_cache(tmp_path, expected_merkle_root="merkle-disk")
    assert loaded is not None
    try:
        assert [row["src_symbol"] for row in loaded.recursive_callers("B")] == ["A"]
    finally:
        loaded.close()

    assert SymbolGraphIndex.load_from_cache(tmp_path, expected_merkle_root="stale-merkle") is None


def test_python_candidate_roots_memoization_and_cache_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "pkg"
    sub = pkg / "sub"
    sub.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (sub / "__init__.py").write_text("", encoding="utf-8")
    importer = sub / "mod.py"
    importer.write_text("import os\n", encoding="utf-8")

    repo_map._clear_all_source_caches()
    calls = 0
    original_hack_roots = repo_map_lang_python._python_sys_path_hack_roots

    def counting_hack_roots(path: Path, root: Path | str | None) -> tuple[Path, ...]:
        nonlocal calls
        calls += 1
        return original_hack_roots(path, root)

    monkeypatch.setattr(repo_map_lang_python, "_python_sys_path_hack_roots", counting_hack_roots)

    first = repo_map_lang_python._python_candidate_roots(importer, tmp_path)
    second = repo_map_lang_python._python_candidate_roots(importer, tmp_path)

    assert first == second
    assert first is not second
    assert calls == 1
    assert repo_map_lang_python._cached_python_candidate_roots.cache_info().hits >= 1

    assert repo_map_lang_python._cached_python_candidate_roots.cache_clear in (
        _MTIME_CACHE_CLEAR_REGISTRY
    )
    repo_map._clear_all_source_caches()
    assert repo_map_lang_python._cached_python_candidate_roots.cache_info().currsize == 0

    third = repo_map_lang_python._python_candidate_roots(importer, tmp_path)
    assert third == first
    assert calls == 2
