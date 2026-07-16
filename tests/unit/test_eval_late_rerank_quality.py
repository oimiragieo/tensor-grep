"""Tests for the T8 golden-set retrieval-quality harness (tg find Wave 1, #189).

Bakes in the 4 mandatory must-fixes from the adversarial review
(``tg_find_review_ledger.md``, "WAVE 1"): E1 (oracle blind spot), E2 (corpus-hardness gate),
E3 (the `--corpus` override is optional/non-gating), E4 (per-query paired win/loss/tie report).

Loaded via ``importlib.util.spec_from_file_location`` -- the same pattern
``test_bm25_benchmark_gate.py`` and ``test_benchmark_scripts.py`` already use for testing a
``benchmarks/*.py`` script that is not part of the installed ``tensor_grep`` package (this repo's
pytest config sets ``--import-mode=importlib``, so a plain ``import benchmarks.xxx`` is not
available without this).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest


def _load_eval_module() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "eval_late_rerank_quality", root / "benchmarks" / "eval_late_rerank_quality.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeDenseModel:
    """Deterministic dense-encoder stand-in (mirrors
    ``test_search_semantic_rerank.py``'s ``_FakeDenseModel``) -- lets a test force the dense/rrf/
    rrf+maxsim arms to be actually SCORED without the `semantic` extra or a fetched model."""

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)


def _fake_late_encode(_text: str) -> np.ndarray:
    return np.ones((2, 4), dtype=np.float32)


# ---------------------------------------------------------------------------------------
# Governance: the harness must use retrieval_scoring.py's own functions, never a reimplementation.
# ---------------------------------------------------------------------------------------


def test_metrics_are_retrieval_scoring_functions() -> None:
    mod = _load_eval_module()
    from tensor_grep.core import retrieval_scoring

    assert mod.recall_at_k is retrieval_scoring.recall_at_k
    assert mod.precision_at_k is retrieval_scoring.precision_at_k
    assert mod.mean_reciprocal_rank_at_k is retrieval_scoring.mean_reciprocal_rank_at_k
    assert mod.ndcg_at_k is retrieval_scoring.ndcg_at_k


# ---------------------------------------------------------------------------------------
# E1: oracle blind spot.
# ---------------------------------------------------------------------------------------


def test_empty_gold_label_is_loud(tmp_path: Path) -> None:
    """A golden query with an empty `relevant` set must be REFUSED at load time -- otherwise
    recall_at_k/ndcg_at_k's vacuous-truth branch (retrieval_scoring.py:8-9,29-30) would silently
    score it as a perfect 1.0 for every arm, forever."""
    mod = _load_eval_module()
    bad_golden = tmp_path / "bad.jsonl"
    bad_golden.write_text(
        json.dumps({"id": "q1", "query": "does something", "category": "concept", "relevant": []})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(mod.GoldenSetError, match="non-empty"):
        mod.load_golden_queries(bad_golden)


def test_missing_relevant_field_is_also_loud(tmp_path: Path) -> None:
    mod = _load_eval_module()
    bad_golden = tmp_path / "bad.jsonl"
    bad_golden.write_text(
        json.dumps({"id": "q1", "query": "does something", "category": "concept"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(mod.GoldenSetError, match="non-empty"):
        mod.load_golden_queries(bad_golden)


def test_reversed_ceiling_is_zero_when_corpus_has_room() -> None:
    mod = _load_eval_module()
    assert mod._reversed_ceiling(relevant_count=2, corpus_size=50, top_k=10) == 0.0


def test_reversed_ceiling_is_positive_when_corpus_too_small_for_a_clean_worst_case() -> None:
    mod = _load_eval_module()
    # 5 relevant out of 8 total files, top_k=10: only 3 irrelevant slots exist, so 2 relevant
    # items are unavoidably forced into the top-10 window even in the WORST placement -- the
    # ceiling must reflect that forced placement's ndcg, not a blind 0.0 that could never be met.
    ceiling = mod._reversed_ceiling(relevant_count=5, corpus_size=8, top_k=10)
    assert ceiling > 0.0


def test_oracle_bidirectional_direct_scenario() -> None:
    """Direct, hand-verified exercise of validate_oracle: a GOLD ranking scores ndcg@10==1.0
    exactly; REVERSED and EMPTY rankings score at/below the documented achievable ceiling (which
    is exactly 0.0 here, since this corpus comfortably exceeds top_k+relevant_count)."""
    mod = _load_eval_module()
    queries = [
        mod.GoldenQuery(
            id="q1", query="find x", category="concept", relevant_files=frozenset({"a.py"})
        ),
        mod.GoldenQuery(
            id="q2", query="find y", category="behavior", relevant_files=frozenset({"b.py", "c.py"})
        ),
    ]
    corpus_files = [f"f{i}.py" for i in range(20)] + ["a.py", "b.py", "c.py"]

    failures = mod.validate_oracle(queries, corpus_files, top_k=10)

    assert failures == []


def test_oracle_bidirectional_catches_a_broken_metric() -> None:
    """If ndcg_at_k regressed to score a REVERSED ranking above its ceiling, validate_oracle must
    report it rather than silently pass -- proven by monkeypatching a fake 'always perfect'
    ndcg function into the loaded module and confirming validate_oracle then reports a failure."""
    mod = _load_eval_module()
    queries = [
        mod.GoldenQuery(
            id="q1", query="find x", category="concept", relevant_files=frozenset({"a.py"})
        ),
    ]
    corpus_files = [f"f{i}.py" for i in range(20)] + ["a.py"]

    original_ndcg = mod.ndcg_at_k
    try:
        mod.ndcg_at_k = lambda ranked, relevant, *, top_k: 1.0  # vacuously "always perfect"
        failures = mod.validate_oracle(queries, corpus_files, top_k=10)
    finally:
        mod.ndcg_at_k = original_ndcg

    assert failures, "a broken always-1.0 metric must be caught by the reversed/empty checks"
    assert any("REVERSED" in failure for failure in failures)


def test_oracle_bidirectional_against_the_real_committed_golden_set() -> None:
    """The PR receipt: `--validate-oracle` against the real committed corpus + golden queries
    must pass cleanly -- this IS the exact invocation attached to the PR as evidence."""
    mod = _load_eval_module()

    exit_code = mod.main(["--validate-oracle"])

    assert exit_code == 0


# ---------------------------------------------------------------------------------------
# E3: --corpus is optional and non-gating, but loud when given-and-missing.
# ---------------------------------------------------------------------------------------


def test_missing_corpus_is_loud(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_eval_module()
    missing = tmp_path / "does-not-exist"

    exit_code = mod.main(["--corpus", str(missing)])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_default_corpus_used_when_no_override_given() -> None:
    mod = _load_eval_module()
    assert mod.DEFAULT_CORPUS_DIR.is_dir()
    files = mod.load_corpus_files(mod.DEFAULT_CORPUS_DIR)
    assert len(files) >= 50  # "~50-80 files" per the plan


# ---------------------------------------------------------------------------------------
# E2: corpus-hardness gate, machine-checked (not just asserted in a docstring).
# ---------------------------------------------------------------------------------------


def test_corpus_hardness_bm25_near_floor() -> None:
    """BM25-alone must score NEAR FLOOR on the committed vocab-mismatch corpus -- unlike
    ``eval_bm25_quality.py``'s own corpus (which saturates BM25 at recall@k >= 0.60 by design),
    this corpus is constructed so BM25 alone is a WEAK baseline.

    Calibrated 2026-07-16 against the committed corpus/golden set (74 files, 40 queries): the
    real measured baseline is recall@10=0.2500, ndcg@10=0.1093 (verified zero-content-token
    overlap between every query and its own target file). The thresholds below hold roughly a
    2x safety margin above that measurement so a trivial future corpus edit does not flake this
    test, while still failing loudly if the corpus drifts back toward BM25-easy.
    """
    mod = _load_eval_module()
    queries = mod.load_golden_queries(mod.DEFAULT_GOLDEN_PATH)

    report = mod.build_report(
        queries,
        mod.DEFAULT_CORPUS_DIR,
        (5, 10),
        dense_reason_override="test: dense leg forced off to isolate the BM25-alone measurement",
    )

    bm25 = report.arms["bm25"]
    assert bm25.status == "scored"
    mean_recall10 = bm25.mean("recall@10")
    mean_ndcg10 = bm25.mean("ndcg@10")

    assert mean_recall10 <= 0.50, (
        f"BM25 recall@10={mean_recall10:.4f} is not near-floor -- the corpus is too BM25-friendly"
    )
    assert mean_ndcg10 <= 0.30, (
        f"BM25 ndcg@10={mean_ndcg10:.4f} is not near-floor -- the corpus is too BM25-friendly"
    )


def test_easy_naive_corpus_is_not_near_floor_sanity_check(tmp_path: Path) -> None:
    """Negative control proving the E2 test above actually discriminates: a NAIVE corpus where
    the query text is copied straight into the target file scores nowhere near floor -- so the
    near-floor result on the real corpus is a property of careful construction, not an artifact
    of the metric or a corpus too small to score anything."""
    mod = _load_eval_module()
    easy_dir = tmp_path / "easy_corpus"
    easy_dir.mkdir()
    (easy_dir / "target.py").write_text(
        "def verify_login_credentials():\n    return True\n", encoding="utf-8"
    )
    for i in range(10):
        (easy_dir / f"distractor_{i}.py").write_text(
            f"def helper_{i}():\n    pass\n", encoding="utf-8"
        )

    query = mod.GoldenQuery(
        id="easy-1",
        query="verify login credentials",
        category="concept",
        relevant_files=frozenset({"target.py"}),
    )
    report = mod.build_report([query], easy_dir, (5, 10), dense_reason_override="test: bm25-only")

    assert report.arms["bm25"].mean("ndcg@10") == pytest.approx(1.0)


# ---------------------------------------------------------------------------------------
# E4: per-query paired win/loss/tie report; refuses a comparison over a skipped arm.
# ---------------------------------------------------------------------------------------


def test_arm_skipped_vs_scored_distinct() -> None:
    mod = _load_eval_module()
    all_queries = mod.load_golden_queries(mod.DEFAULT_GOLDEN_PATH)
    queries = all_queries[:6]  # a small subset keeps this test fast

    corpus_files = mod.load_corpus_files(mod.DEFAULT_CORPUS_DIR)
    chunks = []
    for path in corpus_files:
        chunks.extend(mod.chunk_file(path))
    dense_index = mod.DenseIndex(chunks, _FakeDenseModel())
    late_reranker = mod.LateReranker(encode=_fake_late_encode)

    report = mod.build_report(
        queries,
        mod.DEFAULT_CORPUS_DIR,
        (5, 10),
        dense_index_override=dense_index,
        late_reranker_override=late_reranker,
    )

    assert report.arms["bm25"].status == "scored"
    assert report.arms["dense"].status == "scored"
    assert report.arms["rrf"].status == "scored"
    assert report.arms["rrf+maxsim"].status == "scored"
    assert report.arms["find"].status == "skipped"
    assert report.arms["find"].reason == mod.SKIP_AWAITING_WAVE2
    assert report.arms["find+stack"].status == "skipped"
    assert report.arms["find+stack"].reason == mod.SKIP_AWAITING_WAVE2

    # a paired comparison across two SCORED arms works and covers every query exactly once...
    comparison = mod.paired_comparison(report.arms["bm25"], report.arms["dense"], "ndcg@10")
    assert comparison.wins_a + comparison.wins_b + comparison.ties == len(queries)
    assert {row[0] for row in comparison.per_query} == {q.id for q in queries}

    # ...but the identical call against a SKIPPED arm is refused outright, never fabricated.
    with pytest.raises(mod.GoldenSetError, match="skipped"):
        mod.paired_comparison(report.arms["bm25"], report.arms["find"], "ndcg@10")
    with pytest.raises(mod.GoldenSetError, match="skipped"):
        mod.paired_comparison(report.arms["find"], report.arms["find+stack"], "ndcg@10")


def test_dense_rrf_and_maxsim_skip_cleanly_without_the_optional_extras() -> None:
    """In THIS environment (CI installs only [dev], per test_retrieval_dense.py's own
    docstring), model2vec/onnxruntime are not installed -- dense/rrf/rrf+maxsim must degrade to
    a clean, specific SKIPPED status, never crash and never silently score as though they ran."""
    mod = _load_eval_module()
    queries = mod.load_golden_queries(mod.DEFAULT_GOLDEN_PATH)[:2]

    report = mod.build_report(queries, mod.DEFAULT_CORPUS_DIR, (5, 10))

    assert report.arms["bm25"].status == "scored"
    for name in ("dense", "rrf", "rrf+maxsim"):
        arm = report.arms[name]
        assert arm.status == "skipped"
        assert arm.reason  # a specific, human-readable reason, never a bare None/empty string


def test_render_report_never_prints_a_verdict_over_skipped_arms() -> None:
    mod = _load_eval_module()
    queries = mod.load_golden_queries(mod.DEFAULT_GOLDEN_PATH)[:2]

    report = mod.build_report(queries, mod.DEFAULT_CORPUS_DIR, (5, 10))
    text = mod.render_report(report, (5, 10))

    assert "not computed" in text  # only bm25 is scored -> no >=2-arm verdict is possible
    assert "find" in text
    assert mod.SKIP_AWAITING_WAVE2 in text


# ---------------------------------------------------------------------------------------
# Determinism: --runs N must produce byte-identical rendered output.
# ---------------------------------------------------------------------------------------


def test_three_runs_identical(capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_eval_module()

    exit_code = mod.main(["--runs", "3"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "3/3 runs byte-identical." in captured.out


def test_build_report_is_deterministic_across_repeated_calls() -> None:
    mod = _load_eval_module()
    queries = mod.load_golden_queries(mod.DEFAULT_GOLDEN_PATH)[:5]

    first = mod.render_report(mod.build_report(queries, mod.DEFAULT_CORPUS_DIR, (5, 10)), (5, 10))
    second = mod.render_report(mod.build_report(queries, mod.DEFAULT_CORPUS_DIR, (5, 10)), (5, 10))

    assert first == second
