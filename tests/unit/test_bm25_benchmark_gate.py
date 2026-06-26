"""Tests for the BM25 quality benchmark + the v2 (dense-embedding) gate."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


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


def test_run_eval_returns_metrics_in_range(tmp_path: Path) -> None:
    mod = _load_eval_module()
    queries = mod.build_default_corpus(tmp_path)
    metrics = mod.run_eval(tmp_path, queries, top_k=10)
    assert 0.0 <= metrics.recall_at_k <= 1.0
    assert 0.0 <= metrics.mrr_at_k <= 1.0


def test_default_corpus_meets_bm25_baseline_gate(tmp_path: Path) -> None:
    # The synthetic corpus is keyword-discriminating, so BM25 must clear the v2 gate floor.
    mod = _load_eval_module()
    queries = mod.build_default_corpus(tmp_path)
    metrics = mod.run_eval(tmp_path, queries, top_k=3)
    assert metrics.recall_at_k >= mod.V2_GATE_RECALL
