"""Local-first hybrid search index using SQLite FTS5 + adaptive IDF-weighted RRF."""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover - pure-Python fallback is always available
    np = None  # type: ignore[assignment]

HYBRID_SCHEMA_VERSION = 2
_IDENTIFIER_TOKEN_RE = re.compile(r"^[A-Za-z0-9_]+$")
_CAMEL_ACRONYM_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")


def split_code_identifiers(token: str) -> list[str]:
    """Split ``camelCase`` / ``snake_case`` identifier tokens into lowercase sub-terms."""
    if not token or not _IDENTIFIER_TOKEN_RE.match(token):
        return [token] if token else []
    normalized = _CAMEL_ACRONYM_RE.sub(r"\1 \2", token)
    normalized = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", normalized)
    normalized = normalized.replace("_", " ")
    parts = [part.lower() for part in normalized.split() if part]
    return parts or [token.lower()]


def escape_fts5_query(query: str, mode: str = "and") -> str:
    """Quote each whitespace-delimited term as an FTS5 string literal.

    Strips embedded NUL bytes and doubles embedded ``"`` characters so Boolean
    operators (``AND``, ``OR``, ``NOT``) and punctuation (``*``, ``:``, ``-``,
    ``^``) in code queries cannot trigger FTS5 syntax errors.
    """
    if mode not in {"and", "or"}:
        raise ValueError(f"mode must be 'and' or 'or', got {mode!r}")
    tokens = query.replace("\x00", "").split()
    if not tokens:
        return ""
    if mode == "and":
        expanded_and: list[str] = []
        seen_and: set[str] = set()
        for token in tokens:
            parts = (
                split_code_identifiers(token)
                if (_IDENTIFIER_TOKEN_RE.match(token) and any(c.isupper() for c in token))
                else [token]
            )
            cands = parts if len(parts) > 1 else [token]
            for part in cands:
                if part not in seen_and:
                    seen_and.add(part)
                    expanded_and.append(part)
        escaped_tokens = [f'"{tok.replace(chr(34), chr(34) * 2)}"' for tok in expanded_and]
        return " ".join(escaped_tokens)

    expanded: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        candidates = [token]
        if _IDENTIFIER_TOKEN_RE.match(token):
            for part in split_code_identifiers(token):
                if part not in candidates:
                    candidates.append(part)
        for cand in candidates:
            if cand not in seen:
                seen.add(cand)
                expanded.append(f'"{cand.replace(chr(34), chr(34) * 2)}"')
    return " OR ".join(expanded)


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


def _content_hash(content: str) -> str:
    return hashlib.blake2b(
        content.encode("utf-8", errors="surrogatepass"), digest_size=16
    ).hexdigest()


_F16_MAX = 65504.0


def _clamp_f16(val: float) -> float:
    fval = float(val)
    if not math.isfinite(fval):
        return 0.0
    return max(-_F16_MAX, min(_F16_MAX, fval))


def _pack_embedding(emb: Sequence[float] | None) -> bytes | None:
    if emb is None:
        return None
    if np is not None:
        arr = np.asarray(emb, dtype=np.float64)
        arr = np.where(np.isfinite(arr), np.clip(arr, -_F16_MAX, _F16_MAX), 0.0)
        return bytes(arr.astype("<f2").tobytes())
    vals = [_clamp_f16(v) for v in emb]
    return struct.pack(f"<{len(vals)}e", *vals)


def _extract_alias_tokens(content: str) -> str:
    phrases: list[str] = []
    seen_phrases: set[str] = set()
    for raw_tok in re.findall(r"[A-Za-z0-9_]+", content):
        if any(c.isupper() for c in raw_tok):
            parts = split_code_identifiers(raw_tok)
            if len(parts) > 1:
                phrase = " ".join(parts)
                if phrase not in seen_phrases:
                    seen_phrases.add(phrase)
                    phrases.append(phrase)
    return " ".join(phrases)


def _unpack_embedding_py(blob: bytes | memoryview) -> list[float]:
    raw = bytes(blob)
    if len(raw) % 2 != 0:
        raise ValueError(f"corrupt float16 embedding length: {len(raw)}")
    count = len(raw) // 2
    if count == 0:
        return []
    return list(struct.unpack(f"<{count}e", raw))


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
            ver_row = self._conn.execute("PRAGMA user_version").fetchone()
            user_ver = int(ver_row[0]) if ver_row is not None else 0
            existing_tables = {
                str(r[0])
                for r in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            existing_triggers = {
                str(r[0])
                for r in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall()
            }
            incomplete_schema = bool(existing_tables) and not (
                {"documents", "documents_fts"} <= existing_tables
                and {"documents_ai", "documents_ad", "documents_au"} <= existing_triggers
            )
            if (user_ver != HYBRID_SCHEMA_VERSION or incomplete_schema) and (
                existing_tables & {"documents", "documents_fts", "meta"}
            ):
                self._conn.execute("DROP TRIGGER IF EXISTS documents_ai")
                self._conn.execute("DROP TRIGGER IF EXISTS documents_ad")
                self._conn.execute("DROP TRIGGER IF EXISTS documents_au")
                self._conn.execute("DROP TABLE IF EXISTS documents_fts")
                self._conn.execute("DROP TABLE IF EXISTS documents")
                self._conn.execute("DROP TABLE IF EXISTS meta")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY,
                    doc_id TEXT UNIQUE NOT NULL,
                    content TEXT NOT NULL,
                    alias_tokens TEXT NOT NULL DEFAULT '',
                    content_hash TEXT,
                    embedding_blob BLOB
                )
                """
            )
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    doc_id UNINDEXED,
                    content,
                    alias_tokens,
                    content='documents',
                    content_rowid='id',
                    tokenize="unicode61"
                )
                """
            )
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                    INSERT INTO documents_fts(rowid, doc_id, content, alias_tokens)
                    VALUES (new.id, new.doc_id, new.content, new.alias_tokens);
                END
                """
            )
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                    INSERT INTO documents_fts(documents_fts, rowid, doc_id, content, alias_tokens)
                    VALUES ('delete', old.id, old.doc_id, old.content, old.alias_tokens);
                END
                """
            )
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE OF doc_id, content, alias_tokens ON documents BEGIN
                    INSERT INTO documents_fts(documents_fts, rowid, doc_id, content, alias_tokens)
                    VALUES ('delete', old.id, old.doc_id, old.content, old.alias_tokens);
                    INSERT INTO documents_fts(rowid, doc_id, content, alias_tokens)
                    VALUES (new.id, new.doc_id, new.content, new.alias_tokens);
                END
                """
            )
            self._conn.execute(f"PRAGMA user_version = {HYBRID_SCHEMA_VERSION}")

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
        normalized: list[tuple[str, str, str, bytes | None]] = []
        for item in docs:
            if isinstance(item, Mapping):
                doc_id = str(item.get("doc_id", item.get("id", "")))
                content = str(item.get("content", item.get("text", "")))
                emb = item.get("embedding")
            else:
                doc_id, content, emb = item
                doc_id = str(doc_id)
                content = str(content)
            c_hash = _content_hash(content)
            emb_blob = _pack_embedding(emb)
            normalized.append((doc_id, content, c_hash, emb_blob))

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing_rows = self._conn.execute(
                "SELECT doc_id, content_hash, length(embedding_blob) FROM documents"
            ).fetchall()
            existing: dict[str, tuple[str | None, int | None, bytes | None]] = {
                str(r[0]): (
                    str(r[1]) if r[1] is not None else None,
                    int(r[2]) if r[2] is not None else None,
                    None,
                )
                for r in existing_rows
            }
            for doc_id, content, c_hash, emb_blob in normalized:
                emb_len = len(emb_blob) if emb_blob is not None else None
                prev = existing.get(doc_id)
                if prev is not None:
                    prev_hash, prev_len, prev_blob = prev
                    if prev_hash == c_hash:
                        if emb_blob is None and prev_len is None:
                            continue
                        if emb_blob is not None and prev_len == emb_len:
                            if prev_blob is None:
                                row = self._conn.execute(
                                    "SELECT embedding_blob FROM documents WHERE doc_id = ?",
                                    (doc_id,),
                                ).fetchone()
                                prev_blob = (
                                    bytes(row[0])
                                    if row is not None and row[0] is not None
                                    else None
                                )
                            if prev_blob == emb_blob:
                                existing[doc_id] = (c_hash, emb_len, prev_blob)
                                continue
                        self._conn.execute(
                            "UPDATE documents SET embedding_blob = ? WHERE doc_id = ?",
                            (emb_blob, doc_id),
                        )
                        existing[doc_id] = (c_hash, emb_len, emb_blob)
                        continue
                aliases = _extract_alias_tokens(content)
                self._conn.execute(
                    """
                    INSERT INTO documents (doc_id, content, alias_tokens, content_hash, embedding_blob)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(doc_id) DO UPDATE SET
                        content = excluded.content,
                        alias_tokens = excluded.alias_tokens,
                        content_hash = excluded.content_hash,
                        embedding_blob = excluded.embedding_blob
                    """,
                    (doc_id, content, aliases, c_hash, emb_blob),
                )
                existing[doc_id] = (c_hash, emb_len, emb_blob)
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
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
        return len(stale_ids)

    def _fts_rankings(self, query: str, *, limit: int = 20) -> dict[str, int]:
        escaped_and = escape_fts5_query(query, mode="and")
        if not escaped_and:
            return {}
        and_rows = self._conn.execute(
            """
            SELECT doc_id
            FROM documents_fts
            WHERE documents_fts MATCH ?
            ORDER BY bm25(documents_fts) ASC, doc_id ASC
            """,
            (escaped_and,),
        ).fetchall()
        tokens = query.replace("\x00", "").split()
        min_hits = 1 if len(tokens) <= 1 else (3 if limit >= 3 else max(1, limit))
        if len(and_rows) >= min_hits:
            return {str(row[0]): rank for rank, row in enumerate(and_rows, start=1)}

        escaped_or = escape_fts5_query(query, mode="or")
        if not escaped_or:
            return {str(row[0]): rank for rank, row in enumerate(and_rows, start=1)}
        or_rows = self._conn.execute(
            """
            SELECT doc_id, bm25(documents_fts), content
            FROM documents_fts
            WHERE documents_fts MATCH ?
            """,
            (escaped_or,),
        ).fetchall()
        and_ids = [str(row[0]) for row in and_rows]
        seen_and = set(and_ids)
        query_terms = {
            part.lower()
            for raw_tok in query.replace("\x00", "").split()
            for part in split_code_identifiers(raw_tok)
        }

        scored_or: list[tuple[int, float, str]] = []
        for doc_id, bm25_score, content in or_rows:
            doc_key = str(doc_id)
            if doc_key in seen_and:
                continue
            doc_tokens = {
                part.lower()
                for raw_tok in re.findall(r"[A-Za-z0-9_]+", str(content))
                for part in split_code_identifiers(raw_tok)
            }
            coverage = len(query_terms & doc_tokens)
            scored_or.append((-coverage, float(bm25_score), doc_key))
        scored_or.sort()
        ordered = and_ids + [doc_id for _neg_cov, _bm25, doc_id in scored_or]
        return {doc_id: rank for rank, doc_id in enumerate(ordered, start=1)}

    def _vector_rankings(self, query_vec: Sequence[float] | None) -> dict[str, int]:
        if query_vec is None:
            return {}
        q_list = [float(v) for v in query_vec]
        if not q_list:
            return {}
        rows = self._conn.execute(
            "SELECT doc_id, embedding_blob FROM documents WHERE embedding_blob IS NOT NULL"
        ).fetchall()
        if not rows:
            return {}

        scored: list[tuple[str, float]] = []
        if np is not None:
            q_arr = np.asarray(q_list, dtype=np.float64)
            q_norm = float(np.linalg.norm(q_arr))
            if q_norm == 0.0:
                return {}
            for doc_id, emb_blob in rows:
                vec = np.frombuffer(bytes(emb_blob), dtype="<f2").astype(np.float64)
                if vec.shape != q_arr.shape:
                    continue
                v_norm = float(np.linalg.norm(vec))
                sim = float(np.dot(q_arr, vec) / (q_norm * v_norm)) if v_norm > 0.0 else 0.0
                scored.append((str(doc_id), sim))
        else:
            for doc_id, emb_blob in rows:
                vec_py = _unpack_embedding_py(emb_blob)
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
        fts_ranks = self._fts_rankings(query, limit=limit)
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
