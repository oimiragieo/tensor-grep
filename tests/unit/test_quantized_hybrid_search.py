"""Unit tests for 1-bit Heaviside quantized vector blobs and Hamming-then-rerank search."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from tensor_grep.core.hybrid_rrf import (
    HYBRID_SCHEMA_VERSION,
    HybridSQLiteIndex,
    hamming_similarity_1bit,
    pack_heaviside_1bit,
    unpack_heaviside_1bit,
)


def test_heaviside_1bit_blob_roundtrip_and_size_reduction() -> None:
    vec = [0.75, -0.25, 0.0, -1.5, 2.0, -0.001, 0.5, -0.5, 1.25, -3.0, 0.1]
    blob = pack_heaviside_1bit(vec)
    assert blob is not None
    # 4-byte uint32 dim header + ceil(11 / 8) = 2 bytes = 6 bytes total
    assert len(blob) == 6
    assert unpack_heaviside_1bit(blob) == [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1]

    # Empty vector roundtrip
    empty_blob = pack_heaviside_1bit([])
    assert empty_blob is not None
    assert unpack_heaviside_1bit(empty_blob) == []

    # Size reduction for 384-dim embedding: 52 bytes vs 1536 (f32) / 768 (f16)
    emb_384 = [((-1.0) ** i) * (0.1 + (i % 7) * 0.05) for i in range(384)]
    blob_384 = pack_heaviside_1bit(emb_384)
    assert blob_384 is not None
    assert len(blob_384) == 4 + 48
    assert len(blob_384) < (384 * 2) // 10  # >10x smaller than float16

    with pytest.raises(ValueError, match="corrupt 1-bit"):
        unpack_heaviside_1bit(b"\x01\x02")
    with pytest.raises(ValueError, match="corrupt 1-bit"):
        unpack_heaviside_1bit(b"\x10\x00\x00\x00\xff")


def test_hamming_preview_8x_rerank_preserves_recall_on_large_corpus() -> None:
    # Build 80 docs (> 32 preview floor) with 64-dim embeddings so 8x preview (limit=3 -> 32)
    # filters the corpus via 1-bit Hamming before exact cosine rerank.
    dim = 64
    query_vec = [1.0 if i < 16 else -1.0 for i in range(dim)]
    docs: list[tuple[str, str, list[float]]] = []
    for idx in range(80):
        if idx == 42:
            # Exact match
            emb = [1.0 if i < 16 else -1.0 for i in range(dim)]
        elif idx == 17:
            # Strong match (1 sign flip)
            emb = [1.0 if i < 15 else -1.0 for i in range(dim)]
        else:
            # Orthogonal / negative matches
            emb = [-1.0 if i < 16 else (1.0 if (i + idx) % 2 == 0 else -1.0) for i in range(dim)]
        docs.append((f"doc_{idx:02d}", f"module_{idx} helper code", emb))

    with HybridSQLiteIndex(":memory:") as index:
        index.upsert_documents(docs)
        hits = index.search(
            "nonexistent_lexical_token", query_vec=query_vec, limit=3, idf_weight=0.0
        )
        assert len(hits) == 3
        assert hits[0]["doc_id"] == "doc_42"
        assert hits[1]["doc_id"] == "doc_17"


def test_quantize_only_mode_shrinks_sqlite_blob_footprint(tmp_path: Path) -> None:
    dim = 256
    emb_target = [1.0 if i % 2 == 0 else -1.0 for i in range(dim)]
    emb_other = [-1.0 if i % 2 == 0 else 1.0 for i in range(dim)]

    db_path = tmp_path / "quantized.sqlite3"
    with HybridSQLiteIndex(db_path) as index:
        index.upsert_documents(
            [
                ("d_match", "alpha symbol", emb_target),
                ("d_other", "beta symbol", emb_other),
            ],
            quantize_only=True,
        )
        row = index._conn.execute(
            "SELECT embedding_blob, length(binary_blob) FROM documents WHERE doc_id = 'd_match'"
        ).fetchone()
        assert row is not None
        assert row[0] is None
        assert int(row[1]) == 4 + (dim // 8)  # 36 bytes vs 512 bytes f16 / 1024 bytes f32

        hits = index.search("missing_term", query_vec=emb_target, limit=2, idf_weight=0.0)
        assert [h["doc_id"] for h in hits] == ["d_match", "d_other"]

    sim_same = hamming_similarity_1bit(
        pack_heaviside_1bit(emb_target) or b"",
        pack_heaviside_1bit(emb_target) or b"",
    )
    sim_opp = hamming_similarity_1bit(
        pack_heaviside_1bit(emb_target) or b"",
        pack_heaviside_1bit(emb_other) or b"",
    )
    assert sim_same is not None and math.isclose(sim_same, 1.0)
    assert sim_opp is not None and math.isclose(sim_opp, -1.0)


def test_legacy_v2_schema_without_binary_blob_upgrades_cleanly(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_v2.sqlite3"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY,
                doc_id TEXT UNIQUE NOT NULL,
                content TEXT NOT NULL,
                alias_tokens TEXT NOT NULL DEFAULT '',
                content_hash TEXT,
                embedding_blob BLOB
            );
            CREATE VIRTUAL TABLE documents_fts USING fts5(
                doc_id UNINDEXED,
                content,
                alias_tokens,
                content='documents',
                content_rowid='id',
                tokenize="unicode61"
            );
            CREATE TRIGGER documents_ai AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, doc_id, content, alias_tokens)
                VALUES (new.id, new.doc_id, new.content, new.alias_tokens);
            END;
            CREATE TRIGGER documents_ad AFTER DELETE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, doc_id, content, alias_tokens)
                VALUES ('delete', old.id, old.doc_id, old.content, old.alias_tokens);
            END;
            CREATE TRIGGER documents_au AFTER UPDATE OF doc_id, content, alias_tokens ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, doc_id, content, alias_tokens)
                VALUES ('delete', old.id, old.doc_id, old.content, old.alias_tokens);
                INSERT INTO documents_fts(rowid, doc_id, content, alias_tokens)
                VALUES (new.id, new.doc_id, new.content, new.alias_tokens);
            END;
            PRAGMA user_version = 2;
            """
        )
        conn.commit()
    finally:
        conn.close()

    with HybridSQLiteIndex(db_path) as index:
        ver = index._conn.execute("PRAGMA user_version").fetchone()
        assert ver == (HYBRID_SCHEMA_VERSION,)
        cols = {str(r[1]) for r in index._conn.execute("PRAGMA table_info(documents)").fetchall()}
        assert "binary_blob" in cols
        index.upsert_documents([("d1", "hello world", [1.0, 0.0, 1.0, 0.0])])
        assert index.search("hello", query_vec=[1.0, 0.0, 1.0, 0.0], limit=1)[0]["doc_id"] == "d1"


def test_hamming_preview_preserves_strong_cosine_match_across_hamming_ties() -> None:
    # 100 docs (> preview_k=64 for limit=3) all share the exact same sign pattern as query_vec
    # (1-bit Hamming similarity = 1.0 for every doc). doc_99 has the highest cosine similarity
    # and must not be dropped by preview_k truncation + doc_id tie-breaking.
    dim = 16
    query_vec = [1.0] * dim
    docs: list[tuple[str, str, list[float]]] = []
    for idx in range(100):
        if idx == 99:
            emb = [1.0] * dim  # cosine = 1.0
        else:
            # Same all-positive sign pattern, skewed magnitude -> lower cosine (~0.25)
            emb = [10.0 if i == 0 else 0.01 for i in range(dim)]
        docs.append((f"doc_{idx:02d}", f"symbol_{idx}", emb))

    with HybridSQLiteIndex(":memory:") as index:
        index.upsert_documents(docs)
        hits = index.search(
            "nonexistent_lexical_token", query_vec=query_vec, limit=3, idf_weight=0.0
        )
        assert len(hits) == 3
        assert hits[0]["doc_id"] == "doc_99"


def test_corrupt_sqlite_blobs_and_oversized_inputs_fail_safely() -> None:
    valid_bin = pack_heaviside_1bit([1.0, 0.0, 1.0, 0.0])
    assert valid_bin is not None
    assert hamming_similarity_1bit("not-bytes", valid_bin) is None
    assert hamming_similarity_1bit(10**12, valid_bin) is None
    assert hamming_similarity_1bit(b"\x00" * (1024 * 1024 + 1), valid_bin) is None

    with pytest.raises(ValueError, match="corrupt 1-bit"):
        unpack_heaviside_1bit("not-bytes")
    with pytest.raises(ValueError, match="corrupt 1-bit"):
        unpack_heaviside_1bit(10**12)

    with HybridSQLiteIndex(":memory:") as index:
        index.upsert_documents([("valid_doc", "clean symbol", [1.0, 0.0, 1.0, 0.0])])
        index._conn.execute(
            "INSERT INTO documents (doc_id, content, alias_tokens, content_hash, embedding_blob, binary_blob) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("corrupt_text", "bad binary text", "", "h1", b"\x01\x02\x03", "text-not-blob"),
        )
        index._conn.execute(
            "INSERT INTO documents (doc_id, content, alias_tokens, content_hash, embedding_blob, binary_blob) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("corrupt_int", "bad binary int", "", "h2", "text-emb", 999999999999),
        )
        index._conn.commit()

        hits = index.search(
            "nonexistent_lexical_token",
            query_vec=[1.0, 0.0, 1.0, 0.0],
            limit=5,
            idf_weight=0.0,
        )
        assert [h["doc_id"] for h in hits] == ["valid_doc"]


def test_corrupt_binary_blob_falls_back_to_exact_cosine_when_over_preview_k() -> None:
    # 65 non-target rows + 1 exact-match target row (66 > preview_k=64 for limit=3).
    # Corrupting target's binary_blob while keeping embedding_blob intact must fall back
    # to exact cosine scoring instead of dropping target during Hamming preselection.
    dim = 16
    query_vec = [1.0 if i < 8 else -1.0 for i in range(dim)]
    docs: list[tuple[str, str, list[float]]] = [
        (
            f"doc_{idx:02d}",
            f"filler_symbol_{idx}",
            [-1.0 if i < 8 else 1.0 for i in range(dim)],
        )
        for idx in range(65)
    ]
    docs.append(("target", "exact_target_symbol", list(query_vec)))

    with HybridSQLiteIndex(":memory:") as index:
        index.upsert_documents(docs)
        index._conn.execute(
            "UPDATE documents SET binary_blob = ? WHERE doc_id = ?",
            (b"\x01\x02", "target"),
        )
        index._conn.commit()

        hits = index.search(
            "nonexistent_lexical_token",
            query_vec=query_vec,
            limit=3,
            idf_weight=0.0,
        )
        assert len(hits) == 3
        assert hits[0]["doc_id"] == "target"


def test_nonzero_padding_bits_rejected_and_triggers_cosine_fallback() -> None:
    query_vec = [1.0]
    q_bin = pack_heaviside_1bit(query_vec)
    assert q_bin is not None
    tampered = b"\x01\x00\x00\x00\xff"
    assert hamming_similarity_1bit(q_bin, tampered) is None
    assert hamming_similarity_1bit(tampered, q_bin) is None
    with pytest.raises(ValueError, match="corrupt 1-bit"):
        unpack_heaviside_1bit(tampered)

    docs: list[tuple[str, str, list[float]]] = [
        (f"doc_{idx:02d}", f"filler_symbol_{idx}", [-1.0]) for idx in range(65)
    ]
    docs.append(("target", "exact_target_symbol", list(query_vec)))

    with HybridSQLiteIndex(":memory:") as index:
        index.upsert_documents(docs)
        index._conn.execute(
            "UPDATE documents SET binary_blob = ? WHERE doc_id = ?",
            (tampered, "target"),
        )
        index._conn.commit()

        hits = index.search(
            "nonexistent_lexical_token",
            query_vec=query_vec,
            limit=3,
            idf_weight=0.0,
        )
        assert len(hits) == 3
        assert hits[0]["doc_id"] == "target"
