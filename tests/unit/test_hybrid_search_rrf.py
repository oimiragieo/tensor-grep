from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.core.hybrid_rrf import (
    HybridSQLiteIndex,
    adaptive_idf_rrf_score,
    escape_fts5_query,
)
from tensor_grep.core.reranker import hybrid_rerank
from tensor_grep.core.retrieval_chunker import Chunk


def test_escape_fts5_query_neutralizes_boolean_and_punctuation_operators() -> None:
    assert escape_fts5_query("   ") == ""
    escaped = escape_fts5_query('foo AND bar OR NOT baz:qux* "quoted" (a-b)^2')
    assert escaped == '"foo" "AND" "bar" "OR" "NOT" "baz:qux*" """quoted""" "(a-b)^2"'

    with HybridSQLiteIndex(":memory:") as index:
        index.upsert_documents([
            ("d1", "foo AND bar baz:qux* (a-b)^2", None),
            ("d2", "unrelated helper function", None),
        ])
        for hostile in (
            "AND OR NOT",
            "baz:qux*",
            '"unclosed',
            "foo -bar +baz ^2",
            "(a AND b) OR c:",
        ):
            # Must never raise sqlite3.OperationalError from FTS5 syntax injection.
            results = index.search(hostile)
            assert isinstance(results, list)

        matched = index.search("baz:qux*")
        assert [row["doc_id"] for row in matched] == ["d1"]


def test_adaptive_idf_rrf_score_favors_fts_on_high_idf_and_vec_on_low_idf() -> None:
    assert adaptive_idf_rrf_score(None, None, idf_weight=0.5) == 0.0
    with pytest.raises(ValueError, match="k must be positive"):
        adaptive_idf_rrf_score(1, 1, idf_weight=0.5, k=0)

    high_idf_fts = adaptive_idf_rrf_score(1, None, idf_weight=0.9)
    high_idf_vec = adaptive_idf_rrf_score(None, 1, idf_weight=0.9)
    assert high_idf_fts > high_idf_vec

    low_idf_fts = adaptive_idf_rrf_score(1, None, idf_weight=0.1)
    low_idf_vec = adaptive_idf_rrf_score(None, 1, idf_weight=0.1)
    assert low_idf_vec > low_idf_fts


def test_hybrid_sqlite_index_wal_incremental_upsert_and_remove_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "index" / "hybrid.sqlite3"
    with HybridSQLiteIndex(db_path) as index:
        mode = index._conn.execute("PRAGMA journal_mode").fetchone()
        assert mode is not None and str(mode[0]).lower() == "wal"

        index.upsert_documents([
            ("doc_a", "def parse_invoice_total(amount): return amount", [1.0, 0.0]),
            ("doc_b", "compute billing summary for customer", [0.9, 0.1]),
            ("doc_c", "render sidebar navigation menu", [0.0, 1.0]),
        ])

        # High IDF exact symbol query favors doc_a.
        sym_hits = index.search(
            "parse_invoice_total",
            query_vec=[0.8, 0.2],
            idf_weight=0.9,
            limit=3,
        )
        assert sym_hits[0]["doc_id"] == "doc_a"

        # Update doc_a so old token no longer matches, and prune doc_c.
        index.upsert_documents([("doc_a", "def compute_tax_rate(): return 0.1", [0.1, 0.9])])
        removed = index.remove_missing(["doc_a", "doc_b"])
        assert removed == 1

        assert index.search("parse_invoice_total") == []
        assert index.search("sidebar") == []
        assert [row["doc_id"] for row in index.search("compute_tax_rate")] == ["doc_a"]


def test_hybrid_rerank_and_tg_find_hybrid_cli(tmp_path: Path) -> None:
    chunks = [
        Chunk(file_path="a.py", start_line=1, end_line=2, text="generic helper code"),
        Chunk(file_path="b.py", start_line=1, end_line=2, text="def unique_symbol_alpha(): pass"),
    ]
    order = hybrid_rerank("unique_symbol_alpha", chunks)
    assert order == [1]

    (tmp_path / "alpha.py").write_text(
        "def unique_symbol_alpha() -> int:\n    return 42\n", encoding="utf-8"
    )
    (tmp_path / "beta.py").write_text(
        "def unrelated_helper() -> int:\n    return 0\n", encoding="utf-8"
    )

    runner = CliRunner()
    res = runner.invoke(
        app,
        ["find", "--hybrid", "--json", "unique_symbol_alpha", str(tmp_path)],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["matches"]
    assert payload["matches"][0]["file"].endswith("alpha.py")


def test_escaped_query_does_not_leak_sqlite_error() -> None:
    # Ensure direct sqlite3 FTS5 execution with escaped query works even for quote-only input.
    escaped = escape_fts5_query('"')
    assert escaped == '""""'
    with HybridSQLiteIndex(":memory:") as index:
        index.upsert_documents([("d1", "plain text", None)])
        try:
            index.search('"')
        except sqlite3.OperationalError as exc:  # pragma: no cover - regression guard
            pytest.fail(f"FTS5 query raised OperationalError: {exc}")
