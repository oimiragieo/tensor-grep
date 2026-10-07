"""Exact definitions add evidence without changing the default ranking."""

from unittest.mock import Mock

import pytest

from tensor_grep.core.reranker import rank_chunks
from tensor_grep.core.retrieval_bm25 import Bm25Index
from tensor_grep.core.retrieval_chunker import Chunk


def _corpus():
    chunks = [
        Chunk("decoy.py", 1, 1, "# " + "target_func " * 12),
        Chunk("target.py", 1, 2, "def target_func(value):\n    return value\n"),
        Chunk("other.py", 1, 2, "def other(value):\n    return value\n"),
    ]
    dense = Mock()
    dense.query.return_value = [(0, 1.0), (1, 0.9), (2, 0.8)]
    return chunks, Bm25Index(chunks), dense


def test_existing_order_is_pinned_with_symbol_fusion_disabled(monkeypatch):
    monkeypatch.delenv("TG_RRF_SYMBOLS", raising=False)
    monkeypatch.delenv("TG_RRF_CHANNELS", raising=False)
    chunks, bm25, dense = _corpus()
    order, reason = rank_chunks(
        "target_func", chunks, bm25_index=bm25, dense_index=dense, late_reranker=None
    )
    assert order == [0, 1, 2]
    assert reason is None


def test_opt_in_promotes_definition_without_reordering_other_candidates(monkeypatch):
    monkeypatch.setenv("TG_RRF_SYMBOLS", "1")
    monkeypatch.delenv("TG_RRF_CHANNELS", raising=False)
    chunks, bm25, dense = _corpus()
    order, reason = rank_chunks(
        "target_func", chunks, bm25_index=bm25, dense_index=dense, late_reranker=None
    )
    assert order == [1, 0, 2]
    assert reason is None


def test_fusion_evidence_reports_actual_three_way_sum(monkeypatch):
    monkeypatch.setenv("TG_RRF_SYMBOLS", "1")
    monkeypatch.delenv("TG_RRF_CHANNELS", raising=False)
    chunks, bm25, dense = _corpus()
    evidence = {}
    order, reason = rank_chunks(
        "target_func",
        chunks,
        bm25_index=bm25,
        dense_index=dense,
        late_reranker=None,
        dense_weight=5,
        evidence=evidence,
    )
    assert order == [1, 0, 2]
    assert reason is None
    assert evidence["combine"] == "sum" and evidence["k"] == 60
    assert evidence["weights"] == {"bm25": 1.0, "dense": 1.0, "ast_symbols": 1.0, "path": 0.0}
    assert evidence["ast_symbols"]["matched_symbols"] == ["target_func"]
    assert evidence["ast_symbols"]["parsed_files"] == 3
    assert evidence["ast_symbols"]["ranked_chunks"] == 1


def test_non_symbol_query_preserves_existing_adaptive_fusion(monkeypatch):
    chunks, bm25, dense = _corpus()
    monkeypatch.delenv("TG_RRF_SYMBOLS", raising=False)
    baseline = rank_chunks(
        "conceptual phrase",
        chunks,
        bm25_index=bm25,
        dense_index=dense,
        late_reranker=None,
        dense_weight=5,
    )
    monkeypatch.setenv("TG_RRF_SYMBOLS", "1")
    evidence = {}
    actual = rank_chunks(
        "conceptual phrase",
        chunks,
        bm25_index=bm25,
        dense_index=dense,
        late_reranker=None,
        dense_weight=5,
        evidence=evidence,
    )
    assert actual == baseline
    assert evidence["combine"] == "max"
    assert evidence["weights"]["ast_symbols"] == 0
    assert evidence["weights"]["dense"] == 5


def test_comments_strings_and_calls_are_not_definitions():
    from tensor_grep.core.retrieval_symbols import symbol_ranking

    block = '# def target_func():\ntext = """def target_func():\n    pass\n"""\ntarget_func()\n'
    chunks = [
        Chunk("sample.py", 1, 3, "\n".join(block.splitlines()[:3])),
        Chunk("sample.py", 3, 5, "\n".join(block.splitlines()[2:])),
    ]
    order, evidence = symbol_ranking(chunks, "target_func")
    assert order == []
    assert evidence["parsed_files"] == 1
    assert evidence["skipped_files"] == {}


@pytest.mark.parametrize(
    ("chunks", "reason"),
    [
        ([Chunk("sample.py", 2, 3, "def target_func():\n    pass")], "incomplete_snapshot"),
        (
            [
                Chunk("sample.py", 1, 2, "def target_func():\n    pass"),
                Chunk("sample.py", 2, 2, "different"),
            ],
            "inconsistent_snapshot",
        ),
        ([Chunk("sample.py", 1, 1, "def target_func(:")], "incomplete_parse"),
    ],
)
def test_untrustworthy_snapshot_is_disclosed(chunks, reason):
    from tensor_grep.core.retrieval_symbols import symbol_ranking

    order, evidence = symbol_ranking(chunks, "target_func")
    assert order == []
    assert evidence["parsed_files"] == 0
    assert evidence["skipped_files"] == {reason: 1}


def test_definition_evidence_uses_snapshot_not_changed_file(tmp_path):
    from tensor_grep.core.retrieval_symbols import symbol_ranking

    path = tmp_path / "sample.py"
    path.write_text("def unrelated():\n    pass\n", encoding="utf-8")
    chunks = [Chunk(str(path), 1, 2, "def target_func():\n    pass\n")]
    assert symbol_ranking(chunks, "target_func")[0] == [0]
    assert symbol_ranking(chunks, "TARGET_FUNC")[0] == []
    assert symbol_ranking(chunks, "target_function")[0] == []


def test_resource_limit_precedes_parsing(monkeypatch):
    from tensor_grep.core import retrieval_symbols

    monkeypatch.setattr(retrieval_symbols, "_MAX_FILE_CHARS", 10)
    parser = Mock(side_effect=AssertionError("must not parse oversized source"))
    monkeypatch.setattr(retrieval_symbols, "_definitions", parser)
    order, evidence = retrieval_symbols.symbol_ranking(_corpus()[0], "target_func")
    assert order == []
    assert evidence["skipped_files"] == {"character_limit": 3}
    parser.assert_not_called()


def test_optional_grammar_failure_keeps_other_legs(monkeypatch):
    from tensor_grep.core import retrieval_symbols

    monkeypatch.setenv("TG_RRF_SYMBOLS", "1")
    monkeypatch.setattr(retrieval_symbols, "_structural_parser_for_path", lambda _: None)
    chunks = [Chunk("sample.js", 1, 1, "function target_func() {}")]
    order, reason = rank_chunks(
        "target_func", chunks, bm25_index=Bm25Index(chunks), dense_index=None, late_reranker=None
    )
    assert order == [0]
    assert reason == "AST symbol ranking incomplete: grammar_unavailable=1"


def test_path_leg_keeps_its_weight_with_symbol_fusion(monkeypatch):
    monkeypatch.setenv("TG_RRF_SYMBOLS", "1")
    monkeypatch.setenv("TG_RRF_CHANNELS", "1")
    chunks = [Chunk("target_func.py", 1, 2, "def target_func():\n    pass")]
    evidence = {}
    assert rank_chunks(
        "target_func",
        chunks,
        bm25_index=Bm25Index(chunks),
        dense_index=None,
        late_reranker=None,
        evidence=evidence,
    )[0] == [0]
    assert evidence["weights"] == {"bm25": 1.0, "dense": 0.0, "path": 1.5, "ast_symbols": 1.0}


def test_model_execution_fault_is_not_hidden_by_ast(monkeypatch):
    from tensor_grep.backends.base import BackendExecutionError

    monkeypatch.setenv("TG_RRF_SYMBOLS", "1")
    chunks, bm25, dense = _corpus()
    dense.query.side_effect = BackendExecutionError("broken model")
    with pytest.raises(BackendExecutionError, match="broken model"):
        rank_chunks("target_func", chunks, bm25_index=bm25, dense_index=dense, late_reranker=None)


@pytest.mark.parametrize(
    ("suffix", "source"),
    [
        (".js", "function target_func() {}"),
        (".ts", "function target_func(): void {}"),
        (".tsx", "function target_func() { return <div/>; }"),
        (".rs", "fn target_func() {}"),
        (".go", "package main\nfunc target_func() {}"),
    ],
)
def test_supported_optional_grammar_or_explicit_fallback(suffix, source):
    from tensor_grep.core import retrieval_symbols

    path = "sample" + suffix
    available = retrieval_symbols._structural_parser_for_path(path) is not None
    order, evidence = retrieval_symbols.symbol_ranking(
        [Chunk(path, 1, len(source.splitlines()), source)], "target_func"
    )
    if available:
        assert order == [0]
        assert evidence["skipped_files"] == {}
    else:
        assert order == []
        assert evidence["skipped_files"] == {"grammar_unavailable": 1}


@pytest.mark.parametrize("path_channel", ["0", "1"])
def test_bm25_without_definition_keeps_grep_ties(monkeypatch, path_channel):
    from tensor_grep.core.reranker import rerank_by_bm25
    from tensor_grep.core.result import MatchLine, SearchResult

    chunks = [Chunk("needle.py", 1, 1, "# needle"), Chunk("b.py", 1, 1, "# needle")]
    result = SearchResult(
        matches=[MatchLine(1, "# needle", "b.py"), MatchLine(1, "# needle", "needle.py")]
    )
    monkeypatch.setenv("TG_RRF_CHANNELS", path_channel)
    monkeypatch.delenv("TG_RRF_SYMBOLS", raising=False)
    baseline = rerank_by_bm25(result, "needle", [], index=Bm25Index(chunks))
    monkeypatch.setenv("TG_RRF_SYMBOLS", "1")
    actual = rerank_by_bm25(result, "needle", [], index=Bm25Index(chunks))
    assert actual.matches == baseline.matches == result.matches
    assert actual.rank_fusion["method"] == "bm25"
    assert actual.rank_fusion["ast_symbols"]["ranked_chunks"] == 0
