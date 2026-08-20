"""Tests for the BM25 quality benchmark + the v2 (dense-embedding) gate."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_eval_module() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "eval_bm25_quality", root / "benchmarks" / "eval_bm25_quality.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass field-type resolution can find the module.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_eval_query_dataclass() -> None:
    mod = _load_eval_module()
    q = mod.EvalQuery(query="parse invoice", relevant_files={"invoice.py"})
    assert q.query == "parse invoice"
    assert q.relevant_files == {"invoice.py"}


def test_run_eval_pins_exact_metrics_on_the_deterministic_corpus(tmp_path: Path) -> None:
    """Pin the exact metrics, because the range this test used to assert CANNOT FAIL.

    It previously asserted only:

        assert 0.0 <= metrics.recall_at_k <= 1.0
        assert 0.0 <= metrics.mrr_at_k   <= 1.0

    Both are mathematically guaranteed by the implementations in
    `src/tensor_grep/core/retrieval_scoring.py`:

      * `recall_at_k` returns `len(set(ranked[:k]) & relevant) / len(relevant)`. The numerator
        is an INTERSECTION with `relevant`, so it can never exceed the denominator; the
        empty-`relevant` branch returns exactly 1.0.
      * `mean_reciprocal_rank_at_k` returns `1.0 / index` with `index >= 1`, else 0.0.

    So the old assertions held for every possible input, including a total retrieval collapse
    to 0.0 -- and the measured values sit at the TOP of that range (1.0), which is the worst
    place for a range check to live: everything a regression could do stays inside it.

    `build_default_corpus` is deterministic (verified identical across repeated runs), so the
    honest check is the exact value. A BM25 regression now moves a number this test reads.
    """
    mod = _load_eval_module()
    queries = mod.build_default_corpus(tmp_path)
    assert len(queries) == 10, "corpus size changed; re-derive the pinned metrics below"

    metrics = mod.run_eval(tmp_path, queries, top_k=10)

    assert metrics.recall_at_k == 1.0, (
        f"recall@10 on the deterministic corpus is pinned at 1.0, got {metrics.recall_at_k}"
    )
    assert metrics.mrr_at_k == 1.0, (
        f"mrr@10 on the deterministic corpus is pinned at 1.0, got {metrics.mrr_at_k}"
    )
    # `approx` for this one ONLY, and not as a loosening: precision@10 is a mean of 1/10
    # terms, and 0.1 is not exactly representable in binary floating point. py3.12 summed to
    # exactly 0.1 while py3.11 produced 0.09999999999999999, so an `==` here fails on the
    # interpreter's summation order rather than on retrieval quality -- a red arm for the
    # wrong reason, which is its own kind of useless check.
    #
    # recall and mrr stay EXACT: both are 1.0, which IS exactly representable, and both held
    # on every interpreter in CI. Do not relax them to match this line.
    #
    # The tolerance is tight enough to remain discriminating: any real regression moves
    # precision by a whole 1/10 step, ~9 orders of magnitude larger than rel=1e-9.
    assert metrics.precision_at_k == pytest.approx(0.1, rel=1e-9), (
        "precision@10 is pinned at 0.1 -- one relevant file per query over a top-10 window. "
        f"got {metrics.precision_at_k}"
    )


def test_default_corpus_meets_bm25_baseline_gate(tmp_path: Path) -> None:
    # The synthetic corpus is keyword-discriminating, so BM25 must clear the v2 gate floor.
    mod = _load_eval_module()
    queries = mod.build_default_corpus(tmp_path)
    metrics = mod.run_eval(tmp_path, queries, top_k=3)
    assert metrics.recall_at_k >= mod.V2_GATE_RECALL
