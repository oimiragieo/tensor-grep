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

HYBRID_SCHEMA_VERSION = 3
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


_MAX_BLOB_BYTES = 1024 * 1024
_MAX_RECONSTRUCT_BYTES = 16 * 1024 * 1024


def _coerce_blob(raw: object) -> bytes | None:
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return None
    if len(raw) > _MAX_BLOB_BYTES:
        return None
    return bytes(raw)


def _unpack_embedding_py(blob: object) -> list[float]:
    raw = _coerce_blob(blob)
    if raw is None:
        raise ValueError("corrupt float16 embedding blob")
    if len(raw) % 2 != 0:
        raise ValueError(f"corrupt float16 embedding length: {len(raw)}")
    count = len(raw) // 2
    if count == 0:
        return []
    return list(struct.unpack(f"<{count}e", raw))


def pack_heaviside_1bit(emb: Sequence[float] | None) -> bytes | None:
    """Pack a float vector into a 4-byte dimension header + Heaviside 1-bit packed bytes."""
    if emb is None:
        return None
    dim = len(emb)
    header = struct.pack("<I", dim)
    if dim == 0:
        return header
    if np is not None:
        arr = np.asarray(emb, dtype=np.float64)
        bits = np.where(np.isfinite(arr) & (arr >= 0.0), 1, 0).astype(np.uint8)
        packed = np.packbits(bits, bitorder="little")
        return header + bytes(packed.tobytes())
    buf = bytearray((dim + 7) // 8)
    for idx, val in enumerate(emb):
        fval = float(val)
        if math.isfinite(fval) and fval >= 0.0:
            buf[idx >> 3] |= 1 << (idx & 7)
    return header + bytes(buf)


def unpack_heaviside_1bit(blob: object) -> list[int]:
    """Unpack a Heaviside 1-bit packed BLOB back into ``{0, 1}`` bits."""
    raw = _coerce_blob(blob)
    if raw is None or len(raw) < 4:
        raise ValueError(
            f"corrupt 1-bit embedding header length: {len(raw) if raw is not None else 'invalid'}"
        )
    (dim,) = struct.unpack("<I", raw[:4])
    payload = raw[4:]
    expected_bytes = (dim + 7) // 8
    if len(payload) != expected_bytes:
        raise ValueError(
            f"corrupt 1-bit embedding payload length: expected {expected_bytes}, got {len(payload)}"
        )
    if dim == 0:
        return []
    if dim % 8 != 0 and (payload[-1] & (~((1 << (dim % 8)) - 1) & 0xFF)) != 0:
        raise ValueError("corrupt 1-bit embedding non-zero padding bits")
    if np is not None:
        unpacked = np.unpackbits(np.frombuffer(payload, dtype=np.uint8), bitorder="little")
        return [int(b) for b in unpacked[:dim]]
    return [(payload[idx >> 3] >> (idx & 7)) & 1 for idx in range(dim)]


def hamming_similarity_1bit(a_blob: object, b_blob: object) -> float | None:
    """Return normalized Hamming similarity in ``[-1.0, 1.0]`` for two 1-bit blobs."""
    raw_a = _coerce_blob(a_blob)
    raw_b = _coerce_blob(b_blob)
    if raw_a is None or raw_b is None or len(raw_a) < 4 or len(raw_b) < 4:
        return None
    (dim_a,) = struct.unpack("<I", raw_a[:4])
    (dim_b,) = struct.unpack("<I", raw_b[:4])
    if dim_a != dim_b or dim_a == 0:
        return None
    pay_a = raw_a[4:]
    pay_b = raw_b[4:]
    expected_bytes = (dim_a + 7) // 8
    if len(pay_a) != expected_bytes or len(pay_b) != expected_bytes:
        return None
    if dim_a % 8 != 0:
        unused_mask = ~((1 << (dim_a % 8)) - 1) & 0xFF
        if (pay_a[-1] & unused_mask) != 0 or (pay_b[-1] & unused_mask) != 0:
            return None
    int_a = int.from_bytes(pay_a, "little")
    int_b = int.from_bytes(pay_b, "little")
    dist = (int_a ^ int_b).bit_count()
    return float(1.0 - (2.0 * dist / int(dim_a)))


class HybridSQLiteIndex:
    """Single-file SQLite WAL index combining FTS5 and vector cosine similarity."""

    def __init__(self, db_path: str | Path) -> None:
        self._is_memory = str(db_path) == ":memory:"
        self.db_path = Path(db_path) if not self._is_memory else Path(":memory:")
        self._content_cache: dict[str, str] = {}
        self._dirty: bool = False
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
            doc_cols = (
                {str(r[1]) for r in self._conn.execute("PRAGMA table_info(documents)").fetchall()}
                if "documents" in existing_tables
                else set()
            )
            expected_doc_cols = {
                "id",
                "doc_id",
                "file_id",
                "start_line",
                "end_line",
                "inline_content",
                "content_hash",
                "alias_tokens",
                "embedding_blob",
                "binary_blob",
            }
            incomplete_schema = bool(existing_tables) and not (
                {"files", "documents", "documents_fts"} <= existing_tables
                and {"documents_ad"} <= existing_triggers
                and doc_cols == expected_doc_cols
            )
            if (user_ver != HYBRID_SCHEMA_VERSION or incomplete_schema) and (
                existing_tables & {"files", "documents", "documents_fts", "meta"}
            ):
                self._conn.execute("DROP TRIGGER IF EXISTS documents_ai")
                self._conn.execute("DROP TRIGGER IF EXISTS documents_ad")
                self._conn.execute("DROP TRIGGER IF EXISTS documents_au")
                self._conn.execute("DROP TABLE IF EXISTS documents_fts")
                self._conn.execute("DROP TABLE IF EXISTS documents")
                self._conn.execute("DROP TABLE IF EXISTS files")
                self._conn.execute("DROP TABLE IF EXISTS meta")
                self._dirty = True
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY,
                    file_path TEXT UNIQUE NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY,
                    doc_id TEXT UNIQUE NOT NULL,
                    file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
                    start_line INTEGER NOT NULL DEFAULT 0,
                    end_line INTEGER NOT NULL DEFAULT 0,
                    inline_content TEXT,
                    content_hash TEXT,
                    alias_tokens TEXT NOT NULL DEFAULT '',
                    embedding_blob BLOB,
                    binary_blob BLOB
                )
                """
            )
            if sqlite3.sqlite_version_info >= (3, 43, 0):
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                        content,
                        alias_tokens,
                        content='',
                        contentless_delete=1,
                        tokenize="unicode61"
                    )
                    """
                )
            else:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                        content,
                        alias_tokens,
                        tokenize="unicode61"
                    )
                    """
                )
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                    DELETE FROM documents_fts WHERE rowid = old.id;
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
        *,
        quantize_only: bool = False,
    ) -> int:
        """Insert or update documents and their FTS5 + optional vector entries."""
        normalized: list[tuple[str, str, str, int, int, str, bytes | None, bytes | None]] = []
        for item in docs:
            if isinstance(item, Mapping):
                doc_id = str(item.get("doc_id", item.get("id", "")))
                content = str(item.get("content", item.get("text", "")))
                file_path = str(item.get("file_path", "") or "")
                start_line = int(item.get("start_line", 0) or 0)
                end_line = int(item.get("end_line", 0) or 0)
                emb = item.get("embedding")
            else:
                doc_id, content, emb = item
                doc_id = str(doc_id)
                content = str(content)
                file_path = ""
                start_line = 0
                end_line = 0
            c_hash = _content_hash(content)
            emb_blob = None if quantize_only else _pack_embedding(emb)
            bin_blob = pack_heaviside_1bit(emb)
            normalized.append((
                doc_id,
                content,
                file_path,
                start_line,
                end_line,
                c_hash,
                emb_blob,
                bin_blob,
            ))

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            file_id_by_path: dict[str, int] = {
                str(r[1]): int(r[0])
                for r in self._conn.execute("SELECT id, file_path FROM files").fetchall()
            }
            existing_rows = self._conn.execute(
                "SELECT id, doc_id, file_id, start_line, end_line, content_hash, "
                "length(embedding_blob), length(binary_blob), inline_content IS NOT NULL "
                "FROM documents"
            ).fetchall()
            existing: dict[
                str,
                tuple[
                    int,
                    int | None,
                    int,
                    int,
                    str | None,
                    int | None,
                    int | None,
                    bytes | None,
                    bool,
                ],
            ] = {
                str(r[1]): (
                    int(r[0]),
                    int(r[2]) if r[2] is not None else None,
                    int(r[3]),
                    int(r[4]),
                    str(r[5]) if r[5] is not None else None,
                    int(r[6]) if r[6] is not None else None,
                    int(r[7]) if r[7] is not None else None,
                    None,
                    bool(r[8]),
                )
                for r in existing_rows
            }
            for (
                doc_id,
                content,
                file_path,
                start_line,
                end_line,
                c_hash,
                emb_blob,
                bin_blob,
            ) in normalized:
                file_id: int | None = None
                if file_path:
                    file_id = file_id_by_path.get(file_path)
                    if file_id is None:
                        cur_file = self._conn.execute(
                            "INSERT INTO files (file_path) VALUES (?)",
                            (file_path,),
                        )
                        if cur_file.lastrowid is None:
                            raise RuntimeError("failed to insert file_path")
                        file_id = int(cur_file.lastrowid)
                        file_id_by_path[file_path] = file_id
                        self._dirty = True
                emb_len = len(emb_blob) if emb_blob is not None else None
                bin_len = len(bin_blob) if bin_blob is not None else None
                can_reconstruct = bool(
                    file_id is not None and start_line > 0 and end_line >= start_line
                )
                if can_reconstruct and file_path:
                    try:
                        if Path(file_path).lstat().st_size > _MAX_RECONSTRUCT_BYTES:
                            can_reconstruct = False
                    except OSError:
                        pass
                needs_inline = not can_reconstruct
                prev = existing.get(doc_id)
                if prev is not None:
                    (
                        prev_row_id,
                        prev_file_id,
                        prev_start,
                        prev_end,
                        prev_hash,
                        prev_len,
                        prev_bin_len,
                        prev_blob,
                        prev_has_inline,
                    ) = prev
                    if (
                        prev_hash == c_hash
                        and prev_file_id == file_id
                        and prev_start == start_line
                        and prev_end == end_line
                        and prev_has_inline == needs_inline
                    ):
                        if (
                            emb_blob is None
                            and prev_len is None
                            and bin_blob is None
                            and prev_bin_len is None
                        ):
                            continue
                        if prev_len == emb_len and prev_bin_len == bin_len:
                            if prev_blob is None:
                                row = self._conn.execute(
                                    "SELECT embedding_blob, binary_blob FROM documents WHERE id = ?",
                                    (prev_row_id,),
                                ).fetchone()
                                prev_blob = _coerce_blob(row[0]) if row is not None else None
                                prev_bin = _coerce_blob(row[1]) if row is not None else None
                            else:
                                prev_bin = bin_blob
                            if prev_blob == emb_blob and prev_bin == bin_blob:
                                existing[doc_id] = (
                                    prev_row_id,
                                    file_id,
                                    start_line,
                                    end_line,
                                    c_hash,
                                    emb_len,
                                    bin_len,
                                    prev_blob,
                                    prev_has_inline,
                                )
                                continue
                        self._conn.execute(
                            "UPDATE documents SET embedding_blob = ?, binary_blob = ? WHERE id = ?",
                            (emb_blob, bin_blob, prev_row_id),
                        )
                        self._dirty = True
                        existing[doc_id] = (
                            prev_row_id,
                            file_id,
                            start_line,
                            end_line,
                            c_hash,
                            emb_len,
                            bin_len,
                            emb_blob,
                            prev_has_inline,
                        )
                        continue
                aliases = _extract_alias_tokens(content)
                inline_val = content if needs_inline else None
                if prev is not None:
                    row_id = prev[0]
                    self._conn.execute("DELETE FROM documents_fts WHERE rowid = ?", (row_id,))
                    self._conn.execute(
                        """
                        UPDATE documents
                        SET file_id = ?, start_line = ?, end_line = ?, inline_content = ?,
                            alias_tokens = ?, content_hash = ?, embedding_blob = ?, binary_blob = ?
                        WHERE id = ?
                        """,
                        (
                            file_id,
                            start_line,
                            end_line,
                            inline_val,
                            aliases,
                            c_hash,
                            emb_blob,
                            bin_blob,
                            row_id,
                        ),
                    )
                else:
                    cur = self._conn.execute(
                        """
                        INSERT INTO documents (
                            doc_id, file_id, start_line, end_line, inline_content, alias_tokens,
                            content_hash, embedding_blob, binary_blob
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            doc_id,
                            file_id,
                            start_line,
                            end_line,
                            inline_val,
                            aliases,
                            c_hash,
                            emb_blob,
                            bin_blob,
                        ),
                    )
                    if cur.lastrowid is None:
                        raise RuntimeError("failed to insert document")
                    row_id = int(cur.lastrowid)
                self._conn.execute(
                    "INSERT INTO documents_fts(rowid, content, alias_tokens) VALUES (?, ?, ?)",
                    (row_id, content, aliases),
                )
                self._dirty = True
                existing[doc_id] = (
                    row_id,
                    file_id,
                    start_line,
                    end_line,
                    c_hash,
                    emb_len,
                    bin_len,
                    emb_blob,
                    needs_inline,
                )
            self._conn.commit()
            for doc_id, content, *_ in normalized:
                self._content_cache[doc_id] = content
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
        stale = [(row_id, doc_id) for row_id, doc_id in existing if doc_id not in active]
        if not stale:
            return 0
        with self._conn:
            for row_id, doc_id in stale:
                self._conn.execute("DELETE FROM documents WHERE id = ?", (row_id,))
                self._content_cache.pop(doc_id, None)
            self._conn.execute(
                "DELETE FROM files WHERE id NOT IN "
                "(SELECT DISTINCT file_id FROM documents WHERE file_id IS NOT NULL)"
            )
            self._dirty = True
        return len(stale)

    def _fts_rankings(self, query: str, *, limit: int = 20) -> dict[str, int]:
        escaped_and = escape_fts5_query(query, mode="and")
        if not escaped_and:
            return {}
        and_rows = self._conn.execute(
            """
            SELECT d.doc_id
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?
            ORDER BY bm25(documents_fts) ASC, d.doc_id ASC
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
            SELECT d.doc_id, bm25(documents_fts), d.alias_tokens
            FROM documents_fts
            JOIN documents d ON d.id = documents_fts.rowid
            WHERE documents_fts MATCH ?
            """,
            (escaped_or,),
        ).fetchall()
        and_ids = [str(row[0]) for row in and_rows]
        seen_and = set(and_ids)
        scored_or: list[tuple[float, str]] = []
        for doc_id, bm25_score, _alias_tokens in or_rows:
            doc_key = str(doc_id)
            if doc_key in seen_and:
                continue
            scored_or.append((float(bm25_score), doc_key))
        scored_or.sort()
        ordered = and_ids + [doc_id for _bm25, doc_id in scored_or]
        return {doc_id: rank for rank, doc_id in enumerate(ordered, start=1)}

    def _vector_rankings(
        self,
        query_vec: Sequence[float] | None,
        *,
        limit: int | None = None,
    ) -> dict[str, int]:
        if query_vec is None:
            return {}
        q_list = [float(v) for v in query_vec]
        if not q_list:
            return {}
        rows = self._conn.execute(
            "SELECT doc_id, embedding_blob, binary_blob FROM documents "
            "WHERE embedding_blob IS NOT NULL OR binary_blob IS NOT NULL"
        ).fetchall()
        if not rows:
            return {}

        q_bin = pack_heaviside_1bit(q_list)
        preview_k = max(limit * 16, 64) if limit is not None and limit > 0 else len(rows)

        # Stage 1: Fast 1-bit Hamming recall preview when binary_blob is present.
        has_all_binary = q_bin is not None and all(r[2] is not None for r in rows)
        survivors: list[tuple[str, bytes | None, float | None]] | None = None
        if has_all_binary and q_bin is not None and len(rows) > preview_k:
            hamming_scored: list[tuple[float, str, bytes | None]] = []
            for doc_id, emb_blob, bin_blob in rows:
                h_sim = hamming_similarity_1bit(q_bin, bin_blob)
                if h_sim is None:
                    if _coerce_blob(emb_blob) is not None:
                        has_all_binary = False
                        break
                    continue
                hamming_scored.append((h_sim, str(doc_id), _coerce_blob(emb_blob)))
            if has_all_binary:
                hamming_scored.sort(key=lambda item: (-item[0], item[1]))
                # Include all candidates tied at the Hamming cutoff so lexicographic doc_id
                # ordering cannot silently drop strong cosine matches before Stage 2 reranking.
                if len(hamming_scored) > preview_k:
                    cutoff_h_sim = hamming_scored[preview_k - 1][0]
                    survivors = [
                        (doc_id, emb_blob, h_sim)
                        for idx, (h_sim, doc_id, emb_blob) in enumerate(hamming_scored)
                        if idx < preview_k or h_sim >= cutoff_h_sim
                    ]
                else:
                    survivors = [
                        (doc_id, emb_blob, h_sim) for h_sim, doc_id, emb_blob in hamming_scored
                    ]
        if survivors is None:
            survivors = [
                (
                    str(doc_id),
                    _coerce_blob(emb_blob),
                    hamming_similarity_1bit(q_bin, bin_blob)
                    if (q_bin is not None and bin_blob is not None)
                    else None,
                )
                for doc_id, emb_blob, bin_blob in rows
            ]

        # Stage 2: Exact cosine rerank on survivors (or Hamming fallback if quantize_only=True).
        scored: list[tuple[str, float]] = []
        if np is not None:
            q_arr = np.asarray(q_list, dtype=np.float64)
            q_norm = float(np.linalg.norm(q_arr))
            if q_norm == 0.0:
                return {}
            for doc_id, emb_blob, h_sim in survivors:
                if emb_blob is None:
                    if h_sim is not None:
                        scored.append((doc_id, h_sim))
                    continue
                if len(emb_blob) % 2 != 0:
                    if h_sim is not None:
                        scored.append((doc_id, h_sim))
                    continue
                try:
                    vec = np.frombuffer(emb_blob, dtype="<f2").astype(np.float64)
                except (ValueError, TypeError):
                    if h_sim is not None:
                        scored.append((doc_id, h_sim))
                    continue
                if vec.shape != q_arr.shape:
                    continue
                v_norm = float(np.linalg.norm(vec))
                sim = float(np.dot(q_arr, vec) / (q_norm * v_norm)) if v_norm > 0.0 else 0.0
                scored.append((doc_id, sim))
        else:
            for doc_id, emb_blob, h_sim in survivors:
                if emb_blob is None:
                    if h_sim is not None:
                        scored.append((doc_id, h_sim))
                    continue
                try:
                    vec_py = _unpack_embedding_py(emb_blob)
                    sim = _cosine_similarity_py(q_list, vec_py)
                except (ValueError, TypeError):
                    if h_sim is not None:
                        scored.append((doc_id, h_sim))
                    continue
                scored.append((doc_id, sim))

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
        vec_ranks = self._vector_rankings(query_vec, limit=limit)
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

        missing_ids = [doc_id for _, doc_id, _, _ in top if doc_id not in self._content_cache]
        reconstructed: dict[str, str] = {}
        if missing_ids:
            import os

            from tensor_grep.core.retrieval_chunker import decode_text_with_bom
            from tensor_grep.io.confined import validate_confined_parent_prefix
            from tensor_grep.io.root_probe import open_regular_nontraversing_fd

            placeholders = ",".join("?" for _ in missing_ids)
            rows = self._conn.execute(
                f"""
                SELECT d.doc_id, f.file_path, d.start_line, d.end_line, d.content_hash, d.inline_content
                FROM documents d
                LEFT JOIN files f ON f.id = d.file_id
                WHERE d.doc_id IN ({placeholders})
                """,
                missing_ids,
            ).fetchall()
            confinement_root: Path | None = None
            if not self._is_memory:
                db_p = self.db_path.resolve()
                if (
                    len(db_p.parts) >= 4
                    and db_p.parent.name == "hybrid_v1"
                    and db_p.parent.parent.name == ".tg_cache"
                ):
                    confinement_root = db_p.parent.parent.parent
            file_lines_cache: dict[str, list[str]] = {}
            for row_doc_id, file_path_str, start_line, end_line, expected_hash, inline_val in rows:
                if not expected_hash:
                    continue
                if inline_val is not None:
                    inline_text = str(inline_val)
                    if _content_hash(inline_text) == str(expected_hash):
                        reconstructed[str(row_doc_id)] = inline_text
                    continue
                if not file_path_str or int(start_line) <= 0 or int(end_line) < int(start_line):
                    continue
                lines = file_lines_cache.get(file_path_str)
                if lines is None:
                    lines = []
                    try:
                        src_path = Path(file_path_str)
                        if confinement_root is not None:
                            validate_confined_parent_prefix(confinement_root, src_path)
                        fd = open_regular_nontraversing_fd(src_path)
                        if fd is not None:
                            with os.fdopen(fd, "rb") as handle:
                                raw_bytes = handle.read(_MAX_RECONSTRUCT_BYTES + 1)
                            if len(raw_bytes) <= _MAX_RECONSTRUCT_BYTES:
                                lines = decode_text_with_bom(raw_bytes).splitlines(keepends=True)
                    except OSError:
                        lines = []
                    file_lines_cache[file_path_str] = lines
                s_idx = max(0, int(start_line) - 1)
                e_idx = max(s_idx, int(end_line))
                chunk_text = "".join(lines[s_idx:e_idx])
                if _content_hash(chunk_text) != str(expected_hash):
                    continue
                reconstructed[str(row_doc_id)] = chunk_text

        return [
            {
                "doc_id": doc_id,
                "score": score,
                "fts_rank": f_rank,
                "vec_rank": v_rank,
                "content": self._content_cache.get(doc_id)
                if doc_id in self._content_cache
                else reconstructed.get(doc_id, ""),
            }
            for score, doc_id, f_rank, v_rank in top
        ]
