"""T5/T6: the late-interaction (MaxSim) rerank stage wired into `tg search --semantic` behind
`TG_LATE_RERANK=1` (design doc docs/plans/design-tensor-grep-late-rerank-2026-07-09.md, "The
seam" + "Fail-closed contract").

Mirrors the CliRunner-based patterns in tests/integration/test_semantic_search_flag.py: every
test here also monkeypatches the DENSE leg to a known-clean, always-succeeding state, so every
assertion is isolated to the LATE stage specifically rather than confounded by whatever the real
environment's dense-leg availability happens to be. No `rerank` extra (onnxruntime/tokenizers) or
fetched model is required -- `load_late_reranker` is monkeypatched to return a `LateReranker`
wired with a deterministic stub encoder (or to raise, for the fail-closed paths).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from typer.testing import CliRunner

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.cli.main import app
from tensor_grep.core.retrieval_late import LateReranker


class _FakeDenseModel:
    """A working dense-leg stand-in so the dense leg always contributes cleanly (no fallback
    reason of its own) -- isolates every assertion below to the LATE stage."""

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)


def _write_corpus(tmp_path: Path) -> None:
    # dense.py mentions "invoice" 3x (a strong BM25/dense signal); sparse.py mentions it once, in
    # a comment (a weak signal). Both legs naturally favor dense.py -- tests that need an
    # OBSERVABLE reorder deliberately invert this via the injected late encoder.
    dense = tmp_path / "dense.py"
    dense.write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )
    sparse = tmp_path / "sparse.py"
    sparse.write_text("# one passing invoice mention\nx = 1\n", encoding="utf-8")


def _stub_dense_clean(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.load_dense_model", lambda _dir: _FakeDenseModel()
    )


def _prefer_sparse_encode(text: str) -> np.ndarray:
    """Deliberately inverts the natural BM25/dense preference (see `_write_corpus`) so a
    reorder's effect is observable regardless of the exact natural fused order: the query text
    and sparse.py's chunk both align (dot=1.0); dense.py's chunk is orthogonal (dot=0.0)."""
    if text == "invoice" or "passing invoice mention" in text:
        return np.array([[1.0, 0.0]], dtype=np.float32)
    return np.array([[0.0, 1.0]], dtype=np.float32)


def test_rerank_env_off_is_noop(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T5: with `TG_LATE_RERANK` unset (the default), the late stage must be a TRUE no-op -- not
    merely "happens to produce the same order", but never even PROBED. Spy on `late_available`
    (raising if called) to prove the gate short-circuits before any late-rerank code runs at
    all."""
    monkeypatch.delenv("TG_LATE_RERANK", raising=False)
    _stub_dense_clean(monkeypatch)
    _write_corpus(tmp_path)

    def _must_not_be_called() -> tuple[bool, str | None]:
        raise AssertionError("late_available() must not be called when TG_LATE_RERANK is unset")

    monkeypatch.setattr("tensor_grep.core.retrieval_late.late_available", _must_not_be_called)

    result = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])

    assert result.exception is None, f"late_available() was called: {result.exception!r}"
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload.get("rank_fallback_reason") is None


def test_rerank_budget_exceeded_degrades_with_reason(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T6: a stub late reranker that sleeps past `TG_RERANK_BUDGET_MS` must degrade to the plain
    RRF order (identical to the TG_LATE_RERANK-off baseline) and set `rank_fallback_reason`,
    never silently apply a slow reorder and never crash."""
    monkeypatch.setenv("TG_LATE_RERANK", "1")
    monkeypatch.setenv("TG_RERANK_BUDGET_MS", "1")
    _stub_dense_clean(monkeypatch)
    _write_corpus(tmp_path)
    monkeypatch.setattr("tensor_grep.core.retrieval_late.late_available", lambda: (True, None))

    def _slow_encode(text: str) -> np.ndarray:
        time.sleep(0.05)  # 50ms, comfortably over the 1ms budget
        return np.array([[1.0, 0.0]], dtype=np.float32)

    monkeypatch.setattr(
        "tensor_grep.core.retrieval_late.load_late_reranker",
        lambda *a, **kw: LateReranker(encode=_slow_encode),
    )

    result = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "budget exceeded" in result.stderr
    assert "tg:" in result.stderr

    payload = json.loads(result.stdout)
    assert payload.get("rank_fallback_reason") is not None
    assert "budget exceeded" in payload["rank_fallback_reason"]

    # Fail-closed to the plain RRF order: same file order as a TG_LATE_RERANK-off baseline over
    # the same corpus/query.
    monkeypatch.delenv("TG_LATE_RERANK", raising=False)
    baseline = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])
    baseline_payload = json.loads(baseline.stdout)
    assert [m["file"] for m in payload["matches"]] == [
        m["file"] for m in baseline_payload["matches"]
    ]


def test_corrupt_late_model_exits_2(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T6: an UNRECOVERABLE BackendExecutionError from `load_late_reranker` (e.g. a corrupt
    model directory) must exit cleanly with a `tg:` message and exit code 2, never a raw
    traceback -- mirrors the dense leg's identical contract
    (test_search_semantic_corrupt_model_dir_exits_cleanly_json)."""
    monkeypatch.setenv("TG_LATE_RERANK", "1")
    _stub_dense_clean(monkeypatch)
    _write_corpus(tmp_path)
    monkeypatch.setattr("tensor_grep.core.retrieval_late.late_available", lambda: (True, None))

    def _raise_corrupt(*_args: object, **_kwargs: object) -> LateReranker:
        raise BackendExecutionError(
            "late rerank model at <dir> failed to load (corrupt or incompatible): boom"
        )

    monkeypatch.setattr("tensor_grep.core.retrieval_late.load_late_reranker", _raise_corrupt)

    result = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit), (
        f"expected a clean sys.exit(2), got a raw traceback: {result.exception!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "boom" in payload["detail"]


def test_corrupt_late_model_exits_2_text_mode(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T6, text-mode variant: same clean `tg:`-prefixed message + exit 2, no traceback."""
    monkeypatch.setenv("TG_LATE_RERANK", "1")
    _stub_dense_clean(monkeypatch)
    _write_corpus(tmp_path)
    monkeypatch.setattr("tensor_grep.core.retrieval_late.late_available", lambda: (True, None))

    def _raise_corrupt(*_args: object, **_kwargs: object) -> LateReranker:
        raise BackendExecutionError(
            "late rerank model at <dir> failed to load (corrupt or incompatible): boom"
        )

    monkeypatch.setattr("tensor_grep.core.retrieval_late.load_late_reranker", _raise_corrupt)

    result = CliRunner().invoke(app, ["search", "--semantic", "invoice", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit), (
        f"expected a clean sys.exit(2), got a raw traceback: {result.exception!r}"
    )
    assert "tg: late rerank model at <dir> failed to load" in result.stderr


def test_rerank_ran_xor_fallback_reason_set(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T6 bidirectional invariant: when TG_LATE_RERANK=1 was active, EITHER the order provably
    changed (reason untouched, i.e. None) XOR `rank_fallback_reason` is non-None. No third
    state. Exercises both branches against the SAME env-off baseline."""
    _stub_dense_clean(monkeypatch)
    _write_corpus(tmp_path)

    monkeypatch.delenv("TG_LATE_RERANK", raising=False)
    baseline = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])
    assert baseline.exit_code == 0, baseline.output
    baseline_files = [m["file"] for m in json.loads(baseline.stdout)["matches"]]

    # Branch A: the late stage runs successfully and (deliberately) reorders.
    monkeypatch.setenv("TG_LATE_RERANK", "1")
    monkeypatch.setattr("tensor_grep.core.retrieval_late.late_available", lambda: (True, None))
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_late.load_late_reranker",
        lambda *a, **kw: LateReranker(encode=_prefer_sparse_encode),
    )
    reordered = CliRunner().invoke(
        app, ["search", "--semantic", "--json", "invoice", str(tmp_path)]
    )
    assert reordered.exit_code == 0, reordered.output
    reordered_payload = json.loads(reordered.stdout)
    reordered_files = [m["file"] for m in reordered_payload["matches"]]
    assert reordered_files != baseline_files, "expected the late stage to observably reorder"

    # Branch B: the late stage is unavailable -> degrades to the baseline (RRF) order.
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_late.late_available",
        lambda: (False, "late rerank unavailable: onnxruntime not installed -- test stub"),
    )
    degraded = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])
    assert degraded.exit_code == 0, degraded.output
    degraded_payload = json.loads(degraded.stdout)
    degraded_files = [m["file"] for m in degraded_payload["matches"]]
    assert degraded_files == baseline_files, "expected a degrade to fall back to the RRF order"

    # The invariant itself, stated as a literal XOR for both branches.
    for files, payload in (
        (reordered_files, reordered_payload),
        (degraded_files, degraded_payload),
    ):
        order_provably_changed = files != baseline_files
        reason_set = payload.get("rank_fallback_reason") is not None
        assert order_provably_changed != reason_set, (
            "bidirectional invariant violated: "
            f"order_changed={order_provably_changed}, reason_set={reason_set}"
        )
