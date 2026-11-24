from __future__ import annotations

import os
from pathlib import Path

from tensor_grep.cli.main import _execute_find, _find_representative_line
from tensor_grep.core.reranker import select_deduplicated_find_matches
from tensor_grep.core.retrieval_bm25 import Bm25Index
from tensor_grep.core.retrieval_chunker import Chunk, chunk_file
from tensor_grep.core.retrieval_lexical import split_terms


def test_select_deduplicated_find_matches_skips_overlapping_duplicates_and_fills_limit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "sample.py"
    chunks = [
        Chunk(
            file_path=str(target),
            start_line=1,
            end_line=5,
            text="def alpha():\n    return needle_token\n    pass\n",
        ),
        Chunk(
            file_path=os.path.join(str(tmp_path), ".", "sample.py"),
            start_line=2,
            end_line=6,
            text="    return needle_token\n    pass\n    x = 1\n",
        ),
        Chunk(
            file_path=str(target),
            start_line=10,
            end_line=12,
            text="def beta():\n    return needle_token\n",
        ),
        Chunk(
            file_path=str(target),
            start_line=20,
            end_line=22,
            text="def gamma():\n    return needle_token\n",
        ),
    ]
    matches, selected = select_deduplicated_find_matches(
        chunks,
        [0, 1, 2, 3],
        set(split_terms("needle_token")),
        limit=2,
        why_ranked=True,
        find_representative_line=_find_representative_line,
    )

    assert len(matches) == 2
    assert selected == [0, 2]
    keys = [(os.path.normcase(os.path.abspath(m.file)), m.line_number) for m in matches]
    assert len(set(keys)) == len(keys)
    assert keys[0][1] == 2
    assert keys[1][1] == 11
    assert matches[0].why_ranked is not None
    assert "lines 1-5" in matches[0].why_ranked


def test_execute_find_deduplicates_overlapping_chunks_and_preserves_counts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TG_DENSE_UNAVAILABLE", "1")
    py_file = tmp_path / "overlap_target.py"
    # Build a file >35 lines so line-window chunking (size=30, overlap=5) creates overlapping
    # chunks around lines 26-30 containing the single high-scoring token.
    lines = [f"def filler_{i}():\n    return {i}\n" for i in range(13)]
    # Line 27 is inside both chunk 0 (lines 1-30) and chunk 1 (lines 26-55).
    lines.append("def unique_overlap_anchor():\n    return 'unique_overlap_anchor'\n")
    lines.extend([f"def tail_{i}():\n    return {i}\n" for i in range(15)])
    py_file.write_text("".join(lines), encoding="utf-8")

    result = _execute_find(
        "unique_overlap_anchor",
        str(tmp_path),
        limit=5,
        max_repo_files=100,
        max_tokens=0,
        deadline=None,
        rerank="auto",
    )

    keys = [(os.path.normcase(os.path.abspath(m.file)), m.line_number) for m in result.matches]
    assert len(keys) == len(set(keys))
    assert result.total_matches == len(result.matches) == sum(result.match_counts_by_file.values())
    assert len(result.rerank_texts) == len(result.matches)


def test_structural_cast_chunker_integrates_with_select_deduplicated_find_matches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TG_CHUNKER", "structural")
    py_file = tmp_path / "cast_module.py"
    py_file.write_text(
        "def first_handler(token_alpha):\n"
        "    return token_alpha + 1\n\n"
        "def second_handler(token_alpha):\n"
        "    return token_alpha + 2\n",
        encoding="utf-8",
    )

    chunks = chunk_file(str(py_file), chunk_size=1, overlap=0)
    assert len(chunks) >= 2

    bm25 = Bm25Index(chunks)
    ranked = [idx for idx, _score in bm25.query("token_alpha", top_k=len(chunks))]
    query_terms = set(split_terms("token_alpha"))
    matches, selected = select_deduplicated_find_matches(
        chunks,
        ranked,
        query_terms,
        limit=5,
        why_ranked=False,
        find_representative_line=_find_representative_line,
    )

    assert len(matches) >= 1
    assert len(matches) == len(selected)
    keys = [(os.path.normcase(os.path.abspath(m.file)), m.line_number) for m in matches]
    assert len(keys) == len(set(keys))
