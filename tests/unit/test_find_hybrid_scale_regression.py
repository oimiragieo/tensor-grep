from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.core import reranker as reranker_mod
from tensor_grep.core.hybrid_rrf import (
    HYBRID_SCHEMA_VERSION,
    HybridSQLiteIndex,
    _unpack_embedding_py,
    escape_fts5_query,
    split_code_identifiers,
)
from tensor_grep.core.reranker import HYBRID_INDEX_REL_PATH, hybrid_rerank
from tensor_grep.core.retrieval_chunker import Chunk


def test_contentless_offset_fts5_schema_user_version_3_and_compact_blobs(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite3"
    with HybridSQLiteIndex(db_path) as index:
        ver_row = index._conn.execute("PRAGMA user_version").fetchone()
        assert ver_row is not None and int(ver_row[0]) == HYBRID_SCHEMA_VERSION == 3

        ddl_row = index._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'documents_fts'"
        ).fetchone()
        assert ddl_row is not None
        ddl = str(ddl_row[0])
        assert "content=''" in ddl
        assert "contentless_delete=1" in ddl

        doc_cols = {
            str(r[1]) for r in index._conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        assert {"file_id", "start_line", "end_line", "content_hash", "alias_tokens"} <= doc_cols
        assert "content" not in doc_cols

        triggers = {
            str(r[0])
            for r in index._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
        assert {"documents_ad"} <= triggers

        index.upsert_documents([
            {
                "doc_id": "d1",
                "content": "def calculate_sales_tax(amount): return amount * 0.08",
                "file_path": "billing/tax.py",
                "start_line": 10,
                "end_line": 12,
                "embedding": [0.5, 0.5, 0.0, 0.0],
            }
        ])
        row = index._conn.execute(
            "SELECT d.content_hash, typeof(d.embedding_blob), length(d.embedding_blob), "
            "d.start_line, d.end_line, f.file_path "
            "FROM documents d JOIN files f ON f.id = d.file_id WHERE d.doc_id = 'd1'"
        ).fetchone()
        assert row is not None
        assert len(str(row[0])) == 32  # BLAKE2b-128 hex digest
        assert row[1] == "blob"
        assert int(row[2]) == 8  # 4 x float16 = 8 bytes
        assert int(row[3]) == 10
        assert int(row[4]) == 12
        assert row[5] == "billing/tax.py"

        # Unchanged upsert is a no-op (same hash and rowid preserved).
        rowid_before = index._conn.execute(
            "SELECT id FROM documents WHERE doc_id = 'd1'"
        ).fetchone()
        index.upsert_documents([
            {
                "doc_id": "d1",
                "content": "def calculate_sales_tax(amount): return amount * 0.08",
                "file_path": "billing/tax.py",
                "start_line": 10,
                "end_line": 12,
                "embedding": [0.5, 0.5, 0.0, 0.0],
            }
        ])
        rowid_after = index._conn.execute("SELECT id FROM documents WHERE doc_id = 'd1'").fetchone()
        assert rowid_before == rowid_after


def test_multiword_nl_query_recall_and_identifier_splitting() -> None:
    assert split_code_identifiers("calculateSalesTax") == ["calculate", "sales", "tax"]
    assert split_code_identifiers("tg_cache") == ["tg", "cache"]
    assert '"tg_cache"' in escape_fts5_query("checkpoint excluding tg_cache", mode="or")
    assert '"tg"' in escape_fts5_query("checkpoint excluding tg_cache", mode="or")
    assert '"cache"' in escape_fts5_query("checkpoint excluding tg_cache", mode="or")

    with HybridSQLiteIndex(":memory:") as index:
        index.upsert_documents([
            ("c1", "def compute_tax(): # calculate sales tax for order\n    return 0.07", None),
            ("c2", "def sales_report(): # aggregate monthly sales totals\n    return []", None),
            ("c3", "def tax_rate(): # regional tax schedule\n    return 0.05", None),
            (
                "c4",
                "def checkpoint_sync(): # write checkpoint excluding tg_cache dir\n    pass",
                None,
            ),
            ("c5", "def unrelated(): return 1", None),
        ])
        hits = index.search("calculate sales tax", limit=10)
        assert len(hits) >= 3
        assert hits[0]["doc_id"] == "c1"

        ckpt_hits = index.search("checkpoint excluding tg_cache", limit=10)
        assert ckpt_hits
        assert ckpt_hits[0]["doc_id"] == "c4"


def test_consecutive_run1_and_run2_cli_success(tmp_path: Path) -> None:
    (tmp_path / "billing.py").write_text(
        "def calculate_sales_tax(total: float) -> float:\n"
        "    # Calculate sales tax from subtotal\n"
        "    return total * 0.08\n",
        encoding="utf-8",
    )
    (tmp_path / "reports.py").write_text(
        "def summarize_sales() -> int:\n    # Summarize sales volume\n    return 100\n",
        encoding="utf-8",
    )
    (tmp_path / "rates.py").write_text(
        "def local_tax_rate() -> float:\n    # Default tax rate\n    return 0.06\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    run1 = runner.invoke(
        app,
        ["find", "--hybrid", "--json", "calculate sales tax", str(tmp_path)],
    )
    assert run1.exit_code == 0, run1.output
    payload1 = json.loads(run1.stdout)
    assert len(payload1["matches"]) >= 3
    assert (tmp_path / HYBRID_INDEX_REL_PATH).exists()

    run2 = runner.invoke(
        app,
        ["find", "--hybrid", "--json", "calculate sales tax", str(tmp_path)],
    )
    assert run2.exit_code == 0, run2.output
    payload2 = json.loads(run2.stdout)
    assert len(payload2["matches"]) >= 3
    assert payload2.get("rank_fallback_reason") == payload1.get("rank_fallback_reason")


def test_oversized_corrupt_and_legacy_hybrid_index_graceful_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunks = [
        Chunk(file_path="a.py", start_line=1, end_line=2, text="def calculate_sales_tax(): pass"),
        Chunk(file_path="b.py", start_line=1, end_line=2, text="def helper_fn(): pass"),
    ]
    db_path = tmp_path / HYBRID_INDEX_REL_PATH

    # Positive control: valid v2 cache is reused with no rebuild telemetry.
    assert hybrid_rerank("calculate_sales_tax", chunks, cache_root=tmp_path) == [0]
    telemetry_clean: dict[str, object] = {}
    assert hybrid_rerank(
        "calculate_sales_tax", chunks, cache_root=tmp_path, telemetry=telemetry_clean
    ) == [0]
    assert telemetry_clean == {}

    # Corrupt index -> graceful rebuild.
    db_path.write_bytes(b"not a valid sqlite database at all")
    telemetry_corrupt: dict[str, object] = {}
    assert hybrid_rerank(
        "calculate_sales_tax", chunks, cache_root=tmp_path, telemetry=telemetry_corrupt
    ) == [0]
    assert telemetry_corrupt.get("hybrid_index_rebuilt_reason") == "corrupt_index"

    # Actual legacy v1 on-disk schema (no content_hash / embedding_blob, standalone FTS5)
    # must rebuild cleanly without OperationalError in both HybridSQLiteIndex and hybrid_rerank.
    db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT UNIQUE NOT NULL, content TEXT NOT NULL, embedding TEXT)"
    )
    conn.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(doc_id UNINDEXED, content)")
    conn.execute("INSERT INTO documents (doc_id, content) VALUES ('old', 'legacy doc')")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    telemetry_legacy: dict[str, object] = {}
    assert hybrid_rerank(
        "calculate_sales_tax", chunks, cache_root=tmp_path, telemetry=telemetry_legacy
    ) == [0]
    assert telemetry_legacy.get("hybrid_index_rebuilt_reason") == "legacy_schema"

    # Direct HybridSQLiteIndex open on a v1 schema also migrates cleanly instead of false migration.
    direct_v1 = tmp_path / "direct_v1.sqlite3"
    conn = sqlite3.connect(str(direct_v1))
    conn.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, doc_id TEXT UNIQUE NOT NULL, content TEXT NOT NULL)"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    with HybridSQLiteIndex(direct_v1) as idx:
        assert idx.upsert_documents([("0", "def calculate_sales_tax(): pass", None)]) == 1
        assert idx._conn.execute("PRAGMA user_version").fetchone()[0] == HYBRID_SCHEMA_VERSION

    # Future user_version=99 -> schema_version_mismatch rebuild instead of silent downgrade.
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    telemetry_future: dict[str, object] = {}
    assert hybrid_rerank(
        "calculate_sales_tax", chunks, cache_root=tmp_path, telemetry=telemetry_future
    ) == [0]
    assert telemetry_future.get("hybrid_index_rebuilt_reason") == "schema_version_mismatch"

    # Oversized index (> MAX_HYBRID_INDEX_BYTES) -> graceful rebuild, no OSError crash,
    # and publish skipped if serialized DB still exceeds cap.
    monkeypatch.setattr(reranker_mod, "MAX_HYBRID_INDEX_BYTES", 64)
    telemetry_oversized: dict[str, object] = {}
    assert hybrid_rerank(
        "calculate_sales_tax", chunks, cache_root=tmp_path, telemetry=telemetry_oversized
    ) == [0]
    assert telemetry_oversized.get("hybrid_index_rebuilt_reason") == "exceeds_size_cap"
    assert telemetry_oversized.get("hybrid_cache_skipped") == "exceeds_size_cap"


def test_empty_result_hybrid_json_is_bounded(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["typer>=0.12", "numpy>=2.0"]\n',
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    runner = CliRunner()
    res = runner.invoke(
        app,
        ["find", "--hybrid", "--json", "zzzz_nonexistent_token_xyz", str(tmp_path)],
    )
    assert res.exit_code == 1, res.output
    assert len(res.stdout.encode("utf-8")) <= 2048
    payload = json.loads(res.stdout)
    assert payload["total_matches"] == 0
    assert "dependency_grounding" not in payload


def test_bidirectional_identifier_matching_and_odd_blob_validation(tmp_path: Path) -> None:
    db_path = tmp_path / "bi.sqlite3"
    with HybridSQLiteIndex(db_path) as index:
        index.upsert_documents([
            ("snake", "def parse_http_response(): pass", None),
            ("camel", "parseOther HTTPResponse parseHTTPResponse", None),
        ])
        assert {r["doc_id"] for r in index.search("parse_http_response", limit=5)} == {
            "snake",
            "camel",
        }
        assert {r["doc_id"] for r in index.search("parseHTTPResponse", limit=5)} == {
            "snake",
            "camel",
        }
        assert {r["doc_id"] for r in index.search("parse http response", limit=5)} == {
            "snake",
            "camel",
        }

    with pytest.raises(ValueError, match="corrupt float16"):
        _unpack_embedding_py(b"\x01")


def test_disk_backed_offset_sqlite_scale_1834_files_under_15mib(tmp_path: Path) -> None:
    chunks: list[Chunk] = []
    for file_idx in range(1834):
        file_path = f"pkg/mod_{file_idx // 50}/file_{file_idx}.py"
        for chunk_idx in range(3):
            start = chunk_idx * 25 + 1
            end = start + 24
            marker = (
                "def unique_scale_target_symbol(): return 99\n"
                if file_idx == 1337 and chunk_idx == 1
                else f"def worker_{file_idx}_{chunk_idx}(x, y): return x + y + {file_idx}\n"
            )
            body = marker + f"# module {file_idx} section {chunk_idx} shared_helper_logic\n" * 6
            chunks.append(Chunk(file_path=file_path, start_line=start, end_line=end, text=body))

    order = hybrid_rerank("unique_scale_target_symbol", chunks, cache_root=tmp_path)
    assert order[0] == 1337 * 3 + 1

    db_path = tmp_path / HYBRID_INDEX_REL_PATH
    assert db_path.exists()
    assert db_path.stat().st_size <= 15 * 1024 * 1024

    conn = sqlite3.connect(str(db_path))
    try:
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
        assert "content" not in cols
        file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert file_count == 1834
        assert doc_count == 1834 * 3
    finally:
        conn.close()


def test_hybrid_cache_anchors_to_git_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True)
    sub_dir = repo_root / "src" / "pkg"
    sub_dir.mkdir(parents=True)

    chunks = [
        Chunk(
            file_path=str(sub_dir / "mod.py"),
            start_line=1,
            end_line=1,
            text="def anchored_fn(): pass",
        )
    ]
    assert hybrid_rerank("anchored_fn", chunks, cache_root=sub_dir) == [0]
    assert (repo_root / HYBRID_INDEX_REL_PATH).exists()
    assert not (sub_dir / HYBRID_INDEX_REL_PATH).exists()


def test_post_open_corrupt_fts_triggers_graceful_rebuild(tmp_path: Path) -> None:
    chunks = [
        Chunk(file_path="a.py", start_line=1, end_line=2, text="def calculate_sales_tax(): pass")
    ]
    assert hybrid_rerank("calculate_sales_tax", chunks, cache_root=tmp_path) == [0]
    db_path = tmp_path / HYBRID_INDEX_REL_PATH
    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TRIGGER documents_ad")
    conn.commit()
    conn.close()

    telemetry: dict[str, object] = {}
    assert hybrid_rerank(
        "calculate_sales_tax", chunks, cache_root=tmp_path, telemetry=telemetry
    ) == [0]
    assert telemetry.get("hybrid_index_rebuilt_reason") == "corrupt_index"

    # Non-cache scoring parameter errors (e.g. k=0) must raise directly without corrupt_index rebuild.
    telemetry_param: dict[str, object] = {}
    with pytest.raises(ValueError):
        hybrid_rerank(
            "calculate_sales_tax", chunks, k=0, cache_root=tmp_path, telemetry=telemetry_param
        )
    assert "hybrid_index_rebuilt_reason" not in telemetry_param
