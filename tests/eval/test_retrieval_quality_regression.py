"""Accuracy-leg regression protection (#7): the ndcg@10 floor for `tg find`'s SHIPPED
dense-weight config (the #191/#634 flip, live in production since v1.93.2).

WHY THIS EXISTS: the dense-weight flip that lifted `tg find`'s NL ndcg@10 from 0.3047 to 0.4466
(recall@10 0.55 -> 0.80) shipped straight to production with ZERO regression protection --
``benchmarks/eval_late_rerank_quality.py``'s own ``run_rrf_arm`` called RRF with no weights (the
OLD 1:1 pre-flip fusion) until this same change added the ``rrf_shipped`` arm (see that module's
``SHIPPED_DENSE_WEIGHT`` constant and ``run_rrf_arm``'s ``dense_weight`` parameter). This test is
the actual regression gate: it fails loudly if a future refactor silently reverts the shipped
weighting back toward 1:1 fusion.

Requires the REAL ``semantic`` extra (``model2vec`` + a fetched dense model at
``~/.tensor-grep/models/potion-code-16M`` or ``TG_SEMANTIC_MODEL_DIR``) -- SKIPPED (not failed)
when unavailable, mirroring ``build_dense_index``'s own graceful-degrade contract. No CI workflow
currently installs the ``semantic`` extra + fetches the model (``ci.yml`` installs ``[dev,ast]``
only; ``benchmark.yml`` installs ``[dev,ast]`` (+ optionally ``[bench,nlp]`` for the UNRELATED GPU
suite) -- neither pulls in ``model2vec`` or fetches ``potion-code-16M``), so wiring this into any
existing workflow would mean adding a brand-new network-dependent install step, not reusing a
cheap existing slot. Per this campaign's own low-risk/benchmarks-only scoping for #7, this stays
OPT-IN-LOCAL: ``eval`` marker, excluded from the default ``-m "not eval"`` CI sweep
(``pyproject.toml``'s ``markers``), same discipline as ``tests/eval/test_agent_accuracy.py``.

Run explicitly (mirrors that file's own documented invocation):

    uv run --no-sync pytest tests/eval/test_retrieval_quality_regression.py -m eval -v -s

Bidirectional-oracle proof (the corrupted-weight side of this gate, PR-body evidence): the SAME
measurement, forced back to the pre-#7/pre-flip 1:1 fusion, must FAIL this floor --

    TG_EVAL_FLOOR_DENSE_WEIGHT=1.0 uv run --no-sync pytest \\
        tests/eval/test_retrieval_quality_regression.py -m eval -v -s

FLOOR JUSTIFICATION: measured ndcg@10 at the shipped weight (5.0) is 0.4466; at the corrupted
weight (1.0, the old fusion) it is 0.3047 -- a 0.1419 gap between "healthy" and "silently
reverted". The harness is proven BYTE-DETERMINISTIC across repeated in-process runs against the
real model + real corpus (``test_three_runs_identical`` /
``test_build_report_is_deterministic_across_repeated_calls`` in
``tests/unit/test_eval_late_rerank_quality.py``, 3/3 byte-identical), so there is effectively zero
run-to-run NOISE to absorb -- the floor below is sized for margin against FUTURE incidental drift
(a golden-set/corpus edit, a ``model2vec`` version bump subtly shifting embeddings), not run
noise. 0.40 sits ~0.047 below the measured 0.4466 (room for that incidental drift) while remaining
~0.095 *above* the corrupted value -- more than 2x the margin on the low side -- so it cannot
accidentally pass a silent reversion to 1:1 fusion.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = [pytest.mark.eval, pytest.mark.slow]

# Safely below the measured 0.4466 (see module docstring's FLOOR JUSTIFICATION) -- must be
# cleared by tg find's shipped dense-weight config on the committed 40-query NL golden set.
_NDCG10_FLOOR = 0.40


def _load_eval_module() -> ModuleType:
    """Mirrors ``tests/unit/test_eval_late_rerank_quality.py``'s own loader (this repo's
    established pattern for testing a ``benchmarks/*.py`` script that is not part of the
    installed ``tensor_grep`` package under ``--import-mode=importlib``)."""
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "eval_late_rerank_quality", root / "benchmarks" / "eval_late_rerank_quality.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_shipped_dense_weight_config_meets_ndcg10_floor() -> None:
    """Measures the REAL ``rrf`` arm (bm25 + dense fused via RRF) on the committed 40-query
    golden set at the SHIPPED weight and asserts ndcg@10 clears the regression floor.

    ``TG_EVAL_FLOOR_DENSE_WEIGHT`` (test-only escape hatch, read ONLY by this test -- never by any
    production code) lets this exact assertion be re-run against a DIFFERENT weight for the
    bidirectional-oracle proof without a second copy of this test; see the module docstring's
    invocation examples. Defaults to ``SHIPPED_DENSE_WEIGHT`` (what actually ships).
    """
    mod = _load_eval_module()

    queries = mod.load_golden_queries(mod.DEFAULT_GOLDEN_PATH)
    corpus_files = mod.load_corpus_files(mod.DEFAULT_CORPUS_DIR)
    chunks = []
    for path in corpus_files:
        chunks.extend(mod.chunk_file(path))

    dense_index, reason = mod.build_dense_index(chunks)
    if dense_index is None:
        pytest.skip(
            "dense leg unavailable -- cannot measure the real shipped-config ndcg@10 (run "
            f"`tg install-dense`, or pip install 'tensor-grep[semantic]' and fetch the model): "
            f"{reason}"
        )

    weight = float(os.environ.get("TG_EVAL_FLOOR_DENSE_WEIGHT", mod.SHIPPED_DENSE_WEIGHT))
    bm25_index = mod.Bm25Index(chunks)
    arm = mod.run_rrf_arm(
        chunks,
        mod.DEFAULT_CORPUS_DIR,
        queries,
        (10,),
        bm25_index,
        dense_index,
        dense_weight=weight,
        name="rrf_floor_check",
    )
    assert arm.status == "scored"
    ndcg10 = arm.mean("ndcg@10")

    print(f"\nrrf ndcg@10={ndcg10:.4f} at dense_weight={weight} (floor={_NDCG10_FLOOR})")
    assert ndcg10 >= _NDCG10_FLOOR, (
        f"rrf ndcg@10={ndcg10:.4f} at dense_weight={weight} fell below the regression floor "
        f"{_NDCG10_FLOOR} on the {len(queries)}-query golden set -- the accuracy-leg dense-weight "
        "flip (#191/#634, shipped in v1.93.2) may have silently regressed. See this module's "
        "docstring for the bidirectional-oracle proof (TG_EVAL_FLOOR_DENSE_WEIGHT=1.0 must FAIL "
        "this same assertion) and the floor's justification."
    )
