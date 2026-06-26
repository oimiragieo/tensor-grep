"""Tests for the BM25-first semantic-search building blocks (chunker, BM25 engine)."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tensor_grep.core.retrieval_chunker import Chunk, chunk_file


def test_chunk_file_returns_overlapping_line_chunks(tmp_path: Path) -> None:
    src = tmp_path / "sample.py"
    lines = [f"line_{i}\n" for i in range(50)]
    src.write_text("".join(lines), encoding="utf-8")

    chunks = chunk_file(str(src), chunk_size=20, overlap=5)

    # first chunk spans lines 1-20
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 20
    assert chunks[0].file_path == str(src)

    # second chunk starts at line 16 (20 - 5 overlap), spans 16-35
    assert chunks[1].start_line == 16
    assert chunks[1].end_line == 35

    # the chunks together cover every source line
    covered: set[int] = set()
    for c in chunks:
        covered.update(range(c.start_line, c.end_line + 1))
    assert set(range(1, 51)).issubset(covered)


def test_chunk_file_empty_returns_no_chunks(tmp_path: Path) -> None:
    src = tmp_path / "empty.py"
    src.write_text("", encoding="utf-8")
    assert chunk_file(str(src)) == []


def test_chunk_file_raises_loudly_when_chunk_count_exceeds_max(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch the cap small so the guard fires without building 100k chunks.
    monkeypatch.setattr("tensor_grep.core.retrieval_chunker.MAX_CHUNKS", 5)
    src = tmp_path / "big.txt"
    src.write_text("\n".join(str(i) for i in range(40)), encoding="utf-8")

    with pytest.raises(RuntimeError, match="MAX_CHUNKS"):
        chunk_file(str(src), chunk_size=1, overlap=0)


def test_chunk_is_frozen() -> None:
    c = Chunk(file_path="x.py", start_line=1, end_line=3, text="a\nb\nc\n")
    with pytest.raises(FrozenInstanceError):
        c.start_line = 2  # type: ignore[misc]
