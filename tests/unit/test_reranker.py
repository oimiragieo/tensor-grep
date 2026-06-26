"""Tests for BM25 re-ranking of an existing SearchResult."""

from pathlib import Path

from tensor_grep.core.reranker import rerank_by_bm25
from tensor_grep.core.result import MatchLine, SearchResult


def test_rerank_orders_by_bm25_score(tmp_path: Path) -> None:
    f1 = tmp_path / "a.py"
    f1.write_text("def parse_invoice():\n    return total\n", encoding="utf-8")
    f2 = tmp_path / "b.py"
    f2.write_text("def helper():\n    return 0\n", encoding="utf-8")
    result = SearchResult(
        matches=[
            MatchLine(line_number=1, text="def helper():", file=str(f2)),
            MatchLine(line_number=1, text="def parse_invoice():", file=str(f1)),
        ],
        total_matches=2,
    )

    out = rerank_by_bm25(result, "parse invoice", [str(f1), str(f2)])

    assert out.matches[0].file == str(f1)  # the parse_invoice match ranks first
    assert out.total_matches == 2  # non-match fields preserved


def test_rerank_unmatched_files_sink_to_end(tmp_path: Path) -> None:
    f1 = tmp_path / "a.py"
    f1.write_text("def parse_invoice(): pass\n", encoding="utf-8")
    f2 = tmp_path / "b.py"
    f2.write_text("xyz = 1\n", encoding="utf-8")
    result = SearchResult(
        matches=[
            MatchLine(line_number=1, text="def parse_invoice(): pass", file=str(f1)),
            MatchLine(line_number=1, text="xyz = 1", file=str(f2)),
        ],
        total_matches=2,
    )

    out = rerank_by_bm25(result, "invoice", [str(f1), str(f2)])

    assert out.matches[0].file == str(f1)
    assert out.matches[1].file == str(f2)  # zero-score match sinks to the end


def test_rerank_empty_result_is_safe() -> None:
    out = rerank_by_bm25(SearchResult(), "anything", [])
    assert out.matches == []


def test_rerank_preserves_other_fields(tmp_path: Path) -> None:
    f1 = tmp_path / "a.py"
    f1.write_text("def foo(): pass\n", encoding="utf-8")
    result = SearchResult(
        matches=[MatchLine(line_number=1, text="def foo(): pass", file=str(f1))],
        total_matches=1,
        routing_backend="cpu",
    )

    out = rerank_by_bm25(result, "foo", [str(f1)])

    assert out.routing_backend == "cpu"
    assert out.total_matches == 1
