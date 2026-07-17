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


# ---------------------------------------------------------------------------------------
# #189 Item-2 DE-RISK: the rrf+cent arm (bench-only measurement; core/reranker.py untouched).
# ---------------------------------------------------------------------------------------


def _centrality_corpus_dir(mod: ModuleType) -> Path:
    return mod.DEFAULT_CORPUS_DIR.parent / "find_centrality_corpus"


def _centrality_golden_path(mod: ModuleType) -> Path:
    return mod.DEFAULT_GOLDEN_PATH.parent / "find_centrality_golden.jsonl"


def test_oracle_bidirectional_against_the_new_centrality_golden_set() -> None:
    """The PR receipt for the #189 Item-2 structural golden slice: `--validate-oracle` against
    the new committed corpus + golden queries must pass cleanly, same discipline as
    ``test_oracle_bidirectional_against_the_real_committed_golden_set`` above."""
    mod = _load_eval_module()

    exit_code = mod.main([
        "--validate-oracle",
        "--corpus",
        str(_centrality_corpus_dir(mod)),
        "--golden",
        str(_centrality_golden_path(mod)),
    ])

    assert exit_code == 0


def test_centrality_golden_has_expected_category_counts() -> None:
    """Regression guard for the golden set's designed shape (#189 Item-2 §1): 16 hub-discriminator
    (`central`) queries + 16 leaf-guard (`leaf`) queries, matching the design doc's "~15-20 each"
    target."""
    mod = _load_eval_module()

    queries = mod.load_golden_queries(_centrality_golden_path(mod))

    by_category: dict[str, int] = {}
    for query in queries:
        by_category[query.category] = by_category.get(query.category, 0) + 1
    assert by_category == {"central": 16, "leaf": 16}


def test_centrality_corpus_hubs_score_highest() -> None:
    """Regression guard for the corpus's DESIGNED import topology (#189 Item-2 §1): the corpus is
    built with 4 deliberate hubs (fan_in=6, fan_out=3, symbol density=10 each, by construction),
    3 shared utilities (fan_in=4), and 24 leaves (fan_in=0) -- every hub must score identically
    (19.0) and strictly above every non-hub file, or the corpus no longer has "room" for a
    centrality signal to fix anything (the whole premise of the §1 hub-discriminator queries)."""
    mod = _load_eval_module()
    corpus_dir = _centrality_corpus_dir(mod)

    centrality = mod.build_centrality_scores(corpus_dir)

    assert centrality is not None
    hubs = {
        "orders/fulfillment_core.py",
        "accounts/identity_core.py",
        "pricing/rate_core.py",
        "shipments/routing_core.py",
    }
    hub_scores = {centrality[hub] for hub in hubs}
    assert hub_scores == {19.0}, hub_scores
    non_hub_scores = [score for path, score in centrality.items() if path not in hubs]
    assert max(non_hub_scores) < min(hub_scores)


def test_build_centrality_scores_returns_none_never_raises_on_a_missing_dir(
    tmp_path: Path,
) -> None:
    """Mirrors :func:`build_dense_index`'s "recoverable unavailability degrades, never crashes"
    contract: a corpus_dir that ``build_repo_map`` cannot walk must degrade to ``None`` (which
    :func:`build_report` turns into an explicit rrf+cent skip-stub), never raise and never take
    down the whole harness run."""
    mod = _load_eval_module()
    missing = tmp_path / "does-not-exist"

    result = mod.build_centrality_scores(missing)

    assert result is None


def test_centrality_channel_ranking_excludes_zero_score_and_ties_by_index(
    tmp_path: Path,
) -> None:
    """Mirrors ``core/reranker.py``'s ``_path_channel_ranking`` contract: a chunk whose file
    scores 0 (or is absent from the centrality map) is EXCLUDED entirely -- never an arbitrary
    tie-broken low rank -- and files tied on score break by ascending chunk index."""
    mod = _load_eval_module()
    chunks = [
        mod.Chunk(file_path=str(tmp_path / "hub.py"), start_line=1, end_line=5, text="hub"),
        mod.Chunk(file_path=str(tmp_path / "leaf_a.py"), start_line=1, end_line=2, text="a"),
        mod.Chunk(file_path=str(tmp_path / "leaf_b.py"), start_line=1, end_line=2, text="b"),
        mod.Chunk(file_path=str(tmp_path / "zero.py"), start_line=1, end_line=2, text="z"),
    ]
    centrality = {"hub.py": 19.0, "leaf_a.py": 3.0, "leaf_b.py": 3.0, "zero.py": 0.0}

    ranking = mod._centrality_channel_ranking(chunks, tmp_path, centrality)

    # zero.py (index 3) never appears; hub.py (19.0) ranks first; leaf_a/leaf_b tie at 3.0 and
    # break by ascending chunk index (1 before 2).
    assert ranking == [0, 1, 2]


def test_run_rrf_centrality_arm_is_byte_identical_to_rrf_when_centrality_is_empty() -> None:
    """The design doc's "flat map -> EMPTY leg, not noise" contract (#189 Item-2 §2, restated in
    §4's arm contract): when the centrality signal is empty (e.g. a corpus with no resolvable
    import graph, like the base ``find_golden_corpus``), ``rrf+cent`` must score EXACTLY the same
    as plain ``rrf`` for every query -- the added leg contributes nothing; it must never silently
    perturb tie-breaking or inject noise into an otherwise-unchanged fusion."""
    mod = _load_eval_module()
    queries = mod.load_golden_queries(mod.DEFAULT_GOLDEN_PATH)[:8]
    corpus_files = mod.load_corpus_files(mod.DEFAULT_CORPUS_DIR)
    chunks = []
    for path in corpus_files:
        chunks.extend(mod.chunk_file(path))
    dense_index = mod.DenseIndex(chunks, _FakeDenseModel())

    report = mod.build_report(
        queries,
        mod.DEFAULT_CORPUS_DIR,
        (5, 10),
        dense_index_override=dense_index,
        centrality_override={},
    )

    assert report.arms["rrf"].status == "scored"
    assert report.arms["rrf+cent"].status == "scored"
    assert report.arms["rrf"].per_query == report.arms["rrf+cent"].per_query


def test_build_report_gates_rrf_cent_on_dense_availability() -> None:
    """``rrf+cent`` fuses bm25+dense+centrality -- it needs the dense leg exactly like ``rrf``
    does, so it must skip (never crash, never silently score with a missing leg) on the same
    precondition, with a reason that says why."""
    mod = _load_eval_module()
    queries = mod.load_golden_queries(mod.DEFAULT_GOLDEN_PATH)[:2]

    report = mod.build_report(
        queries,
        mod.DEFAULT_CORPUS_DIR,
        (5, 10),
        dense_reason_override="test: dense leg forced off to isolate the gating check",
    )

    assert report.arms["rrf+cent"].status == "skipped"
    assert report.arms["rrf+cent"].reason
    assert "dense leg unavailable" in report.arms["rrf+cent"].reason


def test_run_rrf_centrality_arm_promotes_a_high_centrality_file() -> None:
    """Direct exercise of the RRF math, compared against the plain ``rrf`` arm: a file the
    bm25/dense legs rank dead LAST (a 3-way tie breaks by ascending chunk index, so ``high.py``
    at index 2 sinks to the bottom on both legs) is pulled UP the fused order once a strong
    centrality score is attached to it -- proving the weighted third leg is actually wired into
    the fusion, not a dead parameter. A comparison against the same query's plain-``rrf`` score
    (rather than a hand-derived absolute number) is the robust assertion here: RRF fuses on RANK,
    not raw score magnitude, so a 100x centrality-score advantage does not automatically win
    outright against two legs that AGREE on the opposite order -- exactly the "weight must stay
    sub-dominant" caveat :data:`CENTRALITY_WEIGHT` documents."""
    mod = _load_eval_module()
    chunks = [
        mod.Chunk(file_path="/corpus/low.py", start_line=1, end_line=1, text="zzz"),
        mod.Chunk(file_path="/corpus/mid.py", start_line=1, end_line=1, text="zzz"),
        mod.Chunk(file_path="/corpus/high.py", start_line=1, end_line=1, text="zzz"),
    ]
    corpus_dir = Path("/corpus")
    query = mod.GoldenQuery(
        id="q1", query="zzz", category="central", relevant_files=frozenset({"high.py"})
    )
    bm25_index = mod.Bm25Index(chunks)  # every chunk has identical text -> a 3-way tie
    dense_index = mod.DenseIndex(chunks, _FakeDenseModel())  # also a tie (identical fake vectors)
    # high.py is ranked LAST on both the bm25 and dense legs (a 3-way tie keeps ascending index
    # order, and high.py is index 2), but it is BY FAR the highest-centrality file.
    centrality = {"low.py": 1.0, "mid.py": 1.0, "high.py": 100.0}

    baseline = mod.run_rrf_arm(chunks, corpus_dir, [query], (5, 10), bm25_index, dense_index)
    boosted = mod.run_rrf_centrality_arm(
        chunks, corpus_dir, [query], (5, 10), bm25_index, dense_index, centrality
    )

    assert baseline.status == "scored"
    assert boosted.status == "scored"
    assert boosted.per_query["q1"]["ndcg@10"] > baseline.per_query["q1"]["ndcg@10"]
