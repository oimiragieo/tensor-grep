"""Persisted chunk-BM25 semantic index, kept SEPARATE from the Rust TGI v3 .tg_index.

Layout under the index dir (default ``<root>/.tg_semantic_index/``, overridable via
``TG_SEMANTIC_INDEX_DIR``):
- ``bm25_chunks.json`` -- the chunk corpus ``[{file_path, start_line, end_line, text}, ...]``
- ``bm25_meta.json``   -- ``{fingerprint, file_count, chunk_count, version, files}``

Staleness mirrors (does not duplicate) the Rust index.rs mtime semantics: a fingerprint over the
indexed files' paths + mtimes is stored at build time and re-checked at load; a mismatch warns and
returns ``None`` so the caller falls back to the unranked path rather than serving stale results.

NOTE: this persisted-index acceleration is NOT yet wired into the CLI. The live ``tg search --rank``
path ranks in-memory via :mod:`tensor_grep.core.reranker`; there is intentionally no ``tg index``
command yet. These helpers are the building blocks for a future indexed-acceleration command — the
stderr messages therefore describe the in-memory fallback rather than instructing a command that
does not exist.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from tensor_grep.core.retrieval_bm25 import Bm25Index
from tensor_grep.core.retrieval_chunker import Chunk, chunk_file

INDEX_VERSION: int = 1
_CHUNKS_NAME = "bm25_chunks.json"
_META_NAME = "bm25_meta.json"


def default_index_dir(root: str | None = None) -> Path:
    """Resolve the semantic index directory: ``TG_SEMANTIC_INDEX_DIR`` or ``<root>/.tg_semantic_index``."""
    env = os.environ.get("TG_SEMANTIC_INDEX_DIR")
    if env:
        return Path(env)
    base = Path(root) if root else Path.cwd()
    return base / ".tg_semantic_index"


def compute_fingerprint(file_paths: list[str]) -> str:
    """SHA-256 over sorted file paths + their mtimes; missing files hash as a sentinel."""
    digest = hashlib.sha256()
    for path in sorted(file_paths):
        digest.update(path.encode("utf-8", errors="replace"))
        try:
            digest.update(str(os.stat(path).st_mtime_ns).encode("ascii"))
        except OSError:
            digest.update(b"\x00missing")
    return digest.hexdigest()


def build_and_save(
    file_paths: list[str],
    index_dir: Path,
    *,
    chunk_size: int = 30,
    overlap: int = 5,
) -> Bm25Index:
    """Chunk all files, build a :class:`Bm25Index`, and persist it (+ fingerprint) to ``index_dir``."""
    chunks: list[Chunk] = []
    for path in file_paths:
        chunks.extend(chunk_file(path, chunk_size=chunk_size, overlap=overlap))
    index = Bm25Index(chunks)

    index_dir.mkdir(parents=True, exist_ok=True)
    chunk_records = [
        {
            "file_path": c.file_path,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "text": c.text,
        }
        for c in chunks
    ]
    (index_dir / _CHUNKS_NAME).write_text(json.dumps(chunk_records), encoding="utf-8")
    meta = {
        "fingerprint": compute_fingerprint(file_paths),
        "file_count": len(file_paths),
        "chunk_count": len(chunks),
        "version": INDEX_VERSION,
        "files": sorted(file_paths),
    }
    (index_dir / _META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    return index


def load_or_warn(index_dir: Path) -> Bm25Index | None:
    """Load the persisted index, or warn to stderr and return ``None`` if missing or stale."""
    chunks_path = index_dir / _CHUNKS_NAME
    meta_path = index_dir / _META_NAME
    if not chunks_path.is_file() or not meta_path.is_file():
        print(
            f"tg: no persisted semantic index at {index_dir}; falling back to in-memory ranking.",
            file=sys.stderr,
        )
        return None

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    current = compute_fingerprint(list(meta.get("files", [])))
    if current != meta.get("fingerprint"):
        print(
            f"tg: semantic index at {index_dir} is stale (files changed since indexing); "
            "falling back to in-memory ranking.",
            file=sys.stderr,
        )
        return None

    chunk_records = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunks = [
        Chunk(
            file_path=r["file_path"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            text=r["text"],
        )
        for r in chunk_records
    ]
    return Bm25Index(chunks)
