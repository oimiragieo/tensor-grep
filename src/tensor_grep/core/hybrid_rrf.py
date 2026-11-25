"""Local-first hybrid search index using SQLite FTS5 + adaptive IDF-weighted RRF."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover - pure-Python fallback is always available
    np = None  # type: ignore[assignment]

_IDENTIFIER_TOKEN_RE = re.compile(r"[A-Za-z0-9_]*[_.A-Z0-9][A-Za-z0-9_]*")


def escape_fts5_query(query: str) -> str:
    """Quote each whitespace-delimited term as an FTS5 string literal.

    Strips embedded NUL bytes and doubles embedded ``"`` characters so Boolean
    operators (``AND``, ``OR``, ``NOT``) and punctuation (``*``, ``:``, ``-``,
    ``^``) in code queries cannot trigger FTS5 syntax errors.
    """
    tokens = query.replace("\x00", "").split()
    if not tokens:
        return ""
    escaped_tokens = [f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens]
    return " ".join(escaped_tokens)


def adaptive_idf_rrf_score(
    fts_rank: int | None,
    vec_rank: int | None,
    *,
    idf_weight: float,
    k: int = 60,
) -> float:
    """Compute adaptive IDF-weighted Reciprocal Rank Fusion score.

    Uses ``w_f / (k + r_f) + w_v / (k + r_v)`` where ``w_f`` is clamped from
    ``idf_weight`` into ``[0.0, 1.0]`` and ``w_v = 1.0 - w_f``. Higher
    ``idf_weight`` favors exact lexical/symbol precision; lower ``idf_weight``
    favors semantic vector similarity.
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    w_f = max(0.0, min(1.0, float(idf_weight)))
    w_v = 1.0 - w_f
    score = 0.0
    if fts_rank is not None and fts_rank >= 1:
        score += w_f / (k + fts_rank)
    if vec_rank is not None and vec_rank >= 1:
        score += w_v / (k + vec_rank)
    return score


def _estimate_query_idf_weight(
    query: str,
    *,
    total_docs: int,
    fts_hits: int,
) -> float:
    """Estimate adaptive FTS vs vector weight in [0.2, 0.85] from query shape and selectivity."""
    tokens = query.split()
    if not tokens:
        return 0.5
    if total_docs > 0 and fts_hits > 0:
        selectivity_idf = math.log1p(total_docs / fts_hits) / math.log1p(max(total_docs, 1))
    else:
        selectivity_idf = 0.35
    has_identifier_cue = any(
        "_" in tok or "." in tok or any(c.isdigit() for c in tok) for tok in tokens
    )
    if len(tokens) == 1 or has_identifier_cue:
        base = 0.65
    elif len(tokens) >= 4:
        base = 0.30
    else:
        base = 0.45
    blended = 0.6 * base + 0.4 * selectivity_idf
    return max(0.2, min(0.85, blended))


def _cosine_similarity_py(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class HybridSQLiteIndex:
    """Single-file SQLite WAL index combining FTS5 and vector cosine similarity."""

    def __init__(self, db_path: str | Path) -> None:
        self._is_memory = str(db_path) == ":memory:"
        self.db_path = Path(db_path) if not self._is_memory else Path(":memory:")
        if not self._is_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        try:
            if not self._is_memory:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
        except BaseException:
            self._conn.close()
            raise

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY,
                    doc_id TEXT UNIQUE NOT NULL,
                    content TEXT NOT NULL,
                    embedding_json TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    doc_id UNINDEXED,
                    content
                )
                """
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> HybridSQLiteIndex:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def upsert_documents(
        self,
        docs: Sequence[tuple[str, str, Sequence[float] | None] | Mapping[str, Any]],
    ) -> int:
        """Insert or update documents and their FTS5 + optional vector entries."""
        normalized: list[tuple[str, str, str | None]] = []
        for item in docs:
            if isinstance(item, Mapping):
                doc_id = str(item.get("doc_id", item.get("id", "")))
                content = str(item.get("content", item.get("text", "")))
                emb = item.get("embedding")
            else:
                doc_id, content, emb = item
                doc_id = str(doc_id)
                content = str(content)
            emb_json = json.dumps([float(v) for v in emb]) if emb is not None else None
            normalized.append((doc_id, content, emb_json))

        with self._conn:
            for doc_id, content, emb_json in normalized:
                self._conn.execute(
                    """
                    INSERT INTO documents (doc_id, content, embedding_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(doc_id) DO UPDATE SET
                        content = excluded.content,
                        embedding_json = excluded.embedding_json
                    """,
                    (doc_id, content, emb_json),
                )
                row = self._conn.execute(
                    "SELECT id FROM documents WHERE doc_id = ?", (doc_id,)
                ).fetchone()
                if row is None:  # pragma: no cover - defensive invariant
                    raise sqlite3.DatabaseError(f"Missing rowid for doc_id={doc_id!r}")
                row_id = int(row[0])
                self._conn.execute("DELETE FROM documents_fts WHERE rowid = ?", (row_id,))
                self._conn.execute(
                    "INSERT INTO documents_fts (rowid, doc_id, content) VALUES (?, ?, ?)",
                    (row_id, doc_id, content),
                )
        return len(normalized)

    def remove_missing(self, active_doc_ids: Iterable[str]) -> int:
        """Delete any indexed documents whose ``doc_id`` is not in ``active_doc_ids``."""
        active = {str(doc_id) for doc_id in active_doc_ids}
        existing = [
            (int(row[0]), str(row[1]))
            for row in self._conn.execute("SELECT id, doc_id FROM documents").fetchall()
        ]
        stale_ids = [row_id for row_id, doc_id in existing if doc_id not in active]
        if not stale_ids:
            return 0
        with self._conn:
            for row_id in stale_ids:
                self._conn.execute("DELETE FROM documents WHERE id = ?", (row_id,))
                self._conn.execute("DELETE FROM documents_fts WHERE rowid = ?", (row_id,))
        return len(stale_ids)

    def _fts_rankings(self, query: str) -> dict[str, int]:
        escaped = escape_fts5_query(query)
        if not escaped:
            return {}
        rows = self._conn.execute(
            """
            SELECT doc_id
            FROM documents_fts
            WHERE documents_fts MATCH ?
            ORDER BY bm25(documents_fts) ASC, doc_id ASC
            """,
            (escaped,),
        ).fetchall()
        return {str(row[0]): rank for rank, row in enumerate(rows, start=1)}

    def _vector_rankings(self, query_vec: Sequence[float] | None) -> dict[str, int]:
        if query_vec is None:
            return {}
        q_list = [float(v) for v in query_vec]
        if not q_list:
            return {}
        rows = self._conn.execute(
            "SELECT doc_id, embedding_json FROM documents WHERE embedding_json IS NOT NULL"
        ).fetchall()
        if not rows:
            return {}

        scored: list[tuple[str, float]] = []
        if np is not None:
            q_arr = np.asarray(q_list, dtype=np.float64)
            q_norm = float(np.linalg.norm(q_arr))
            if q_norm == 0.0:
                return {}
            for doc_id, emb_json in rows:
                vec = np.asarray(json.loads(str(emb_json)), dtype=np.float64)
                if vec.shape != q_arr.shape:
                    continue
                v_norm = float(np.linalg.norm(vec))
                sim = float(np.dot(q_arr, vec) / (q_norm * v_norm)) if v_norm > 0.0 else 0.0
                scored.append((str(doc_id), sim))
        else:
            for doc_id, emb_json in rows:
                vec_py = [float(v) for v in json.loads(str(emb_json))]
                sim = _cosine_similarity_py(q_list, vec_py)
                scored.append((str(doc_id), sim))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return {doc_id: rank for rank, (doc_id, _sim) in enumerate(scored, start=1)}

    def search(
        self,
        query: str,
        query_vec: Sequence[float] | None = None,
        limit: int = 20,
        *,
        k: int = 60,
        idf_weight: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run hybrid FTS5 + vector search fused with adaptive IDF-weighted RRF."""
        if limit <= 0:
            return []
        fts_ranks = self._fts_rankings(query)
        vec_ranks = self._vector_rankings(query_vec)
        if not fts_ranks and not vec_ranks:
            return []

        total_row = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        total_docs = int(total_row[0]) if total_row is not None else 0
        resolved_idf_weight = (
            _estimate_query_idf_weight(query, total_docs=total_docs, fts_hits=len(fts_ranks))
            if idf_weight is None
            else float(idf_weight)
        )

        candidate_ids = set(fts_ranks) | set(vec_ranks)
        scored: list[tuple[float, str, int | None, int | None]] = []
        for doc_id in candidate_ids:
            f_rank = fts_ranks.get(doc_id)
            v_rank = vec_ranks.get(doc_id)
            score = adaptive_idf_rrf_score(
                f_rank,
                v_rank,
                idf_weight=resolved_idf_weight,
                k=k,
            )
            scored.append((score, doc_id, f_rank, v_rank))

        scored.sort(key=lambda item: (-item[0], item[1]))
        top = scored[:limit]
        placeholders = ",".join("?" for _ in top)
        content_rows = self._conn.execute(
            f"SELECT doc_id, content FROM documents WHERE doc_id IN ({placeholders})",
            [item[1] for item in top],
        ).fetchall()
        content_by_id = {str(row[0]): str(row[1]) for row in content_rows}

        return [
            {
                "doc_id": doc_id,
                "score": score,
                "fts_rank": f_rank,
                "vec_rank": v_rank,
                "content": content_by_id.get(doc_id, ""),
            }
            for score, doc_id, f_rank, v_rank in top
        ]
