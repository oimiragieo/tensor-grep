"""Tests for opt-in cAST structural AST chunking (TG_CHUNKER=structural) beside the default
line-window chunker -- see ``retrieval_chunker.py``'s module docstring for the fail-open contract
this file exercises.

PR-S1 (arxiv 2506.15655). The default (env unset) path MUST stay byte-identical to the pre-cAST
``chunk_file`` -- see ``test_default_chunk_file_is_byte_identical_to_line_windows`` -- this PR
does not flip the default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.core.retrieval_chunker import (
    MAX_CHUNKS,
    Chunk,
    chunk_file,
    chunk_file_structural,
    current_chunker_mode,
)


def _non_ws_len(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


TWO_FUNCS_SRC = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"


# ---------------------------------------------------------------------------
# Gate 1: function-boundary chunking (a function that fits the budget is not split mid-way).
# ---------------------------------------------------------------------------


def test_structural_chunks_at_function_boundaries(tmp_path: Path) -> None:
    src = tmp_path / "two_funcs.py"
    src.write_text(TWO_FUNCS_SRC, encoding="utf-8")

    # A budget that fits either function alone but not both together forces a split at the
    # module level (into its two function_definition children) while leaving each function
    # intact (it individually fits, so _leaf_units stops recursing into it).
    budget = _non_ws_len(TWO_FUNCS_SRC) - 1
    chunks = chunk_file_structural(str(src), budget=budget)

    assert len(chunks) == 2
    assert chunks[0].text.strip().startswith("def foo")
    assert chunks[1].text.strip().startswith("def bar")
    # each function's own body line lands in the SAME chunk as its own header -- not split.
    assert "return 1" in chunks[0].text
    assert "return 2" in chunks[1].text
    assert "return 2" not in chunks[0].text
    assert "return 1" not in chunks[1].text


def test_structural_chunk_file_dispatch_matches_direct_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """chunk_file()'s TG_CHUNKER=structural dispatch is not a second implementation -- it must
    delegate to chunk_file_structural, mapping chunk_size/overlap onto budget/overlap_context via
    the module's documented ``_STRUCTURAL_CHARS_PER_LINE`` multiplier."""
    from tensor_grep.core.retrieval_chunker import _STRUCTURAL_CHARS_PER_LINE

    monkeypatch.setenv("TG_CHUNKER", "structural")
    src = tmp_path / "two_funcs.py"
    src.write_text(TWO_FUNCS_SRC, encoding="utf-8")

    chunk_size = 5
    overlap = 2
    via_chunk_file = chunk_file(str(src), chunk_size=chunk_size, overlap=overlap)
    direct = chunk_file_structural(
        str(src),
        budget=chunk_size * _STRUCTURAL_CHARS_PER_LINE,
        overlap_context=overlap,
    )
    assert via_chunk_file == direct


# ---------------------------------------------------------------------------
# Gate 2: an over-budget function gets split, and small adjacent siblings get merged back.
# ---------------------------------------------------------------------------


def test_structural_split_then_merge_on_oversized_function(tmp_path: Path) -> None:
    src = tmp_path / "many_stmts.py"
    src.write_text(
        "def foo():\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    e = 5\n    f = 6\n",
        encoding="utf-8",
    )
    full_text = src.read_text(encoding="utf-8")

    # Budget far smaller than the whole function -> must split (more than one chunk), but roomy
    # enough for exactly two single-statement lines to merge back together (proves the MERGE half
    # of split-then-merge actually ran, not just the split half).
    single_stmt_len = _non_ws_len("    a = 1\n")
    tiny_budget = 2 * single_stmt_len + 1
    chunks = chunk_file_structural(str(src), budget=tiny_budget)

    assert len(chunks) > 1, "an over-budget function must be split into more than one chunk"

    # No content is lost or duplicated-away: every statement line appears in exactly the chunks
    # that legitimately cover it, and the union of all chunk text reconstructs every line.
    all_lines_covered: set[str] = set()
    for c in chunks:
        for line in c.text.splitlines():
            if line.strip():
                all_lines_covered.add(line.strip())
    for line in full_text.splitlines():
        if line.strip():
            assert line.strip() in all_lines_covered

    # Merge actually coalesced *something*: fewer chunks than the number of individually-small
    # leaf fragments a naive per-token split would produce (7 statements + def/name/params/colon
    # header tokens => at least 10 raw leaf units for this file).
    assert len(chunks) < 10

    # At least one chunk holds more than a single statement line -- proof the merge step combined
    # adjacent small siblings rather than leaving every fragment as its own chunk.
    assert any(c.text.count(" = ") > 1 for c in chunks)


def test_structural_merged_chunks_respect_budget_reasonably(tmp_path: Path) -> None:
    """Every merged chunk's own non-whitespace length should be within a small constant factor of
    the requested budget (guards against the merge step degenerating into "put everything back in
    one chunk")."""
    src = tmp_path / "many_stmts.py"
    src.write_text(
        "def foo():\n" + "".join(f"    v{i} = {i}\n" for i in range(20)),
        encoding="utf-8",
    )
    budget = 30
    chunks = chunk_file_structural(str(src), budget=budget)
    assert len(chunks) > 1
    for c in chunks:
        # A single statement is at most ~10 non-ws chars here, so no merged group should run away
        # to many times the budget.
        assert _non_ws_len(c.text) <= budget + 40


# ---------------------------------------------------------------------------
# Gate 3: fail-open -- no grammar, and syntactically-broken files, degrade to chunk_file().
# ---------------------------------------------------------------------------


def test_structural_fails_open_on_no_grammar_suffix(tmp_path: Path) -> None:
    src = tmp_path / "notes.txt"
    src.write_text("some\nplain\ntext\nfile\n", encoding="utf-8")

    structural = chunk_file_structural(str(src))
    plain = chunk_file(str(src))
    assert structural == plain


def test_structural_fails_open_on_syntax_error(tmp_path: Path) -> None:
    src = tmp_path / "broken.py"
    src.write_text("def foo(:\n    pass\n", encoding="utf-8")

    structural = chunk_file_structural(str(src))
    plain = chunk_file(str(src))
    assert structural == plain


def test_structural_fails_open_on_empty_file_like_chunk_file(tmp_path: Path) -> None:
    src = tmp_path / "empty.py"
    src.write_text("", encoding="utf-8")
    assert chunk_file_structural(str(src)) == []
    assert chunk_file_structural(str(src)) == chunk_file(str(src))


def test_structural_fails_open_on_unreadable_file() -> None:
    assert chunk_file_structural("does-not-exist.py") == chunk_file("does-not-exist.py")
    assert chunk_file_structural("does-not-exist.py") == []


# ---------------------------------------------------------------------------
# Gate 4: DEFAULT (flag unset) is byte-identical to today's chunk_file.
# ---------------------------------------------------------------------------


def test_default_chunker_mode_is_fixed_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_CHUNKER", raising=False)
    assert current_chunker_mode() == "fixed-window"


def test_default_chunker_mode_ignores_unrecognized_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TG_CHUNKER", "banana")
    assert current_chunker_mode() == "fixed-window"


def test_default_chunk_file_is_byte_identical_to_line_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TG_CHUNKER", raising=False)
    src = tmp_path / "sample.py"
    lines = [f"line_{i}\n" for i in range(50)]
    src.write_text("".join(lines), encoding="utf-8")

    chunks = chunk_file(str(src), chunk_size=20, overlap=5)

    assert chunks[0] == Chunk(
        file_path=str(src), start_line=1, end_line=20, text="".join(lines[0:20])
    )
    assert chunks[1] == Chunk(
        file_path=str(src), start_line=16, end_line=35, text="".join(lines[15:35])
    )
    assert len(chunks) == 3


def test_unrecognized_chunker_value_is_also_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "two_funcs.py"
    src.write_text(TWO_FUNCS_SRC, encoding="utf-8")

    monkeypatch.delenv("TG_CHUNKER", raising=False)
    default_chunks = chunk_file(str(src))

    monkeypatch.setenv("TG_CHUNKER", "banana")
    other_chunks = chunk_file(str(src))

    assert default_chunks == other_chunks


# ---------------------------------------------------------------------------
# Gate 5: Chunk shape / line-numbering identical to chunk_file (1-based, inclusive).
# ---------------------------------------------------------------------------


def test_structural_chunk_shape_matches_chunk_dataclass(tmp_path: Path) -> None:
    src = tmp_path / "two_funcs.py"
    src.write_text(TWO_FUNCS_SRC, encoding="utf-8")

    chunks = chunk_file_structural(str(src), budget=_non_ws_len(TWO_FUNCS_SRC) - 1)
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.file_path == str(src)
        assert isinstance(c.start_line, int)
        assert isinstance(c.end_line, int)
        assert c.start_line >= 1
        assert c.end_line >= c.start_line
        # text must be exactly the source lines [start_line, end_line] (1-based inclusive).
        source_lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
        assert c.text == "".join(source_lines[c.start_line - 1 : c.end_line])


def test_structural_chunk_is_frozen(tmp_path: Path) -> None:
    src = tmp_path / "two_funcs.py"
    src.write_text(TWO_FUNCS_SRC, encoding="utf-8")
    chunks = chunk_file_structural(str(src), budget=_non_ws_len(TWO_FUNCS_SRC) - 1)
    assert chunks
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        chunks[0].start_line = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Gate 6: the MAX_CHUNKS loud guard still fires.
# ---------------------------------------------------------------------------


def test_max_chunks_guard_still_fires_on_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TG_CHUNKER", raising=False)
    monkeypatch.setattr("tensor_grep.core.retrieval_chunker.MAX_CHUNKS", 5)
    src = tmp_path / "big.txt"
    src.write_text("\n".join(str(i) for i in range(40)), encoding="utf-8")

    with pytest.raises(RuntimeError, match="MAX_CHUNKS"):
        chunk_file(str(src), chunk_size=1, overlap=0)


def test_max_chunks_guard_fires_via_structural_fail_open_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pathological file that would blow the guard in line-window mode still raises when
    TG_CHUNKER=structural routes a no-grammar file through the fail-open fallback (which ignores
    the caller's requested chunk_size/overlap and uses chunk_file's own safe defaults -- MAX_CHUNKS
    patched to 1 guarantees the guard fires regardless of that default window size)."""
    monkeypatch.setenv("TG_CHUNKER", "structural")
    monkeypatch.setattr("tensor_grep.core.retrieval_chunker.MAX_CHUNKS", 1)
    src = tmp_path / "big.txt"  # .txt has no tree-sitter grammar registered -> fails open
    src.write_text("\n".join(str(i) for i in range(40)), encoding="utf-8")

    with pytest.raises(RuntimeError, match="MAX_CHUNKS"):
        chunk_file(str(src), chunk_size=1, overlap=0)


def test_structural_budget_pathology_falls_open_to_max_chunks_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A structural chunk count that would itself exceed MAX_CHUNKS falls open to the line-window
    fallback, whose own guard fires (never raised directly from the structural path)."""
    monkeypatch.setattr("tensor_grep.core.retrieval_chunker.MAX_CHUNKS", 2)
    src = tmp_path / "many_stmts.py"
    src.write_text(
        "def foo():\n" + "".join(f"    v{i} = {i}\n" for i in range(60)),
        encoding="utf-8",
    )

    # budget=0 forces every leaf token into its own unit and prevents any merge (nothing fits a
    # zero budget), producing many single-token chunks -- comfortably over MAX_CHUNKS=2. The
    # structural path must not raise directly; it falls open to chunk_file's line-window default
    # (chunk_size=30, overlap=5), which -- on this 61-line file -- ALSO exceeds MAX_CHUNKS=2 and
    # raises, so the guard still fires end-to-end.
    with pytest.raises(RuntimeError, match="MAX_CHUNKS"):
        chunk_file_structural(str(src), budget=0)


def test_structural_never_raises_for_expected_fail_open_cases(tmp_path: Path) -> None:
    """Sanity check on the "never raise" contract for the cases that are NOT budget pathologies:
    missing grammar, syntax error, unreadable/empty file must all return quietly."""
    txt = tmp_path / "notes.txt"
    txt.write_text("plain text\n", encoding="utf-8")
    chunk_file_structural(str(txt))  # must not raise

    broken = tmp_path / "broken.py"
    broken.write_text("def foo(:\n    pass\n", encoding="utf-8")
    chunk_file_structural(str(broken))  # must not raise

    empty = tmp_path / "empty.py"
    empty.write_text("", encoding="utf-8")
    chunk_file_structural(str(empty))  # must not raise

    chunk_file_structural("does-not-exist.py")  # must not raise


def test_max_chunks_constant_unchanged() -> None:
    assert MAX_CHUNKS == 100_000
