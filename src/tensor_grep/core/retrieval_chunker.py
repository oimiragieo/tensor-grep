"""Split source files into newline-aligned, overlapping chunks for BM25/semantic ranking.

Per-chunk (not per-line, not per-file) is the granularity the design council settled on: it keeps
the vector/posting count bounded while preserving intra-file locality. No AST is required for v1 --
chunks are plain line windows. A loud MAX_CHUNKS guard prevents a pathological repo from OOM-ing.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_CHUNKS: int = 100_000


@dataclass(frozen=True)
class Chunk:
    """A contiguous window of a file. Line numbers are 1-based and inclusive."""

    file_path: str
    start_line: int
    end_line: int
    text: str


def chunk_file(
    file_path: str,
    *,
    chunk_size: int = 30,
    overlap: int = 5,
) -> list[Chunk]:
    """Split ``file_path`` into ~``chunk_size``-line windows that overlap by ``overlap`` lines.

    Returns an empty list for unreadable or empty files. Raises ``RuntimeError`` if the file would
    produce more than :data:`MAX_CHUNKS` chunks (a loud failure beats a silent OOM).
    """
    try:
        with open(file_path, encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
    except OSError:
        return []

    lines = raw.splitlines(keepends=True)
    total = len(lines)
    if total == 0:
        return []

    step = max(1, chunk_size - overlap)
    chunks: list[Chunk] = []
    start = 0  # 0-based index into ``lines``
    while start < total:
        end = min(start + chunk_size, total)  # exclusive
        chunks.append(
            Chunk(
                file_path=file_path,
                start_line=start + 1,
                end_line=end,
                text="".join(lines[start:end]),
            )
        )
        if len(chunks) > MAX_CHUNKS:
            raise RuntimeError(
                f"MAX_CHUNKS ({MAX_CHUNKS}) exceeded while chunking {file_path!r}. "
                "Use a larger chunk_size or scope the search to fewer files."
            )
        if end == total:
            break
        start += step

    return chunks
