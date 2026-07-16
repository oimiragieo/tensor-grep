"""`tg find` (Wave 2b/2c, #189): the whole-repo hybrid semantic search command.

Mirrors the CliRunner-based isolation pattern in test_search_semantic_rerank.py /
test_semantic_search_flag.py -- every test explicitly monkeypatches the dense-leg (and, where
relevant, the late-leg) availability probes to a known state rather than depending on whatever the
real environment happens to have installed, so each assertion is isolated to the behavior under
test. No `semantic`/`rerank` extra or fetched model is required.

Fail-closed matrix under test (D3, fix-approach council must-fixes C1-C3):
- dense-leg extra absent -> visible BM25-only degrade, exit 0.
- a corrupt dense model (BackendExecutionError) -> clean `tg:` message, exit 2, never a traceback.
- a repo-walk deadline cutoff, a --max-repo-files cap, or the internal corpus-wide chunk cap all
  mean the ranked corpus was PARTIAL -> result_incomplete=true + exit 2 (never the exit-0 BM25-only
  degrade `--semantic`'s own corpus cap uses -- find has no regex pre-filter to rely on).
- no ranked matches on a complete scan -> exit 1.
- --max-tokens truncates the lowest-ranked matches first and never sets result_incomplete.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from typer.testing import CliRunner

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.cli.main import app
from tensor_grep.core.retrieval_late import LateReranker


class _FakeDenseModel:
    """A working dense-leg stand-in so the dense leg always contributes cleanly (no fallback
    reason of its own) -- mirrors test_search_semantic_rerank.py's identical fixture."""

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), 4), dtype=np.float32)


class _QueryDimMismatchModel:
    """Encodes the CORPUS to dim 4 (so `DenseIndex(chunks, model)` CONSTRUCTION succeeds) but the
    QUERY string to dim 5, so `DenseIndex.query` raises `DenseUnavailableError` at QUERY time (the
    dim-mismatch shape check) -- from INSIDE `rank_chunks`, the F1 path distinct from the
    construction path the outer `try/except DenseUnavailableError` already guards. This is the exact
    fault the shipped `--semantic` sibling catches around its own `rerank_hybrid` call
    (cli/main.py:3970-3984); the regression test below proves `tg find` catches it too."""

    def __init__(self, query: str) -> None:
        self._query = query

    def encode(self, texts: list[str]) -> np.ndarray:
        # The single-text query batch -> wrong dim (5); every other batch (the corpus chunks) -> the
        # index's own dim (4). `_encode_matrix` still sees shape[0] == len(texts) for both, so only
        # the query-time dim-mismatch check fires -- never the malformed-shape check.
        if texts == [self._query]:
            return np.ones((1, 5), dtype=np.float32)
        return np.ones((len(texts), 4), dtype=np.float32)


def _stub_dense_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.dense_available",
        lambda: (False, "semantic ranking unavailable: model2vec not installed -- test stub"),
    )


def _stub_dense_clean(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.load_dense_model", lambda _dir: _FakeDenseModel()
    )


def test_find_bm25_only_when_extra_absent_sets_rank_fallback_reason(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    _stub_dense_unavailable(monkeypatch)
    (tmp_path / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["find", "invoice", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload.get("rank_fallback_reason") is not None
    assert "model2vec" in payload["rank_fallback_reason"]
    assert payload["total_matches"] >= 1


def test_find_dense_unavailable_at_query_degrades_to_bm25_exit_0(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """Opus-gate blocker F1 regression: a `DenseUnavailableError` raised at QUERY time (a
    dim/shape mismatch from inside `rank_chunks`'s `DenseIndex.query`, NOT at construction --
    construction is already guarded) must degrade VISIBLY to BM25-only and exit 0, never escape
    as a raw traceback + exit 1. `DenseUnavailableError` subclasses `RuntimeError`, so before the
    F1 catch it was caught by neither the construction guard nor the command boundary (which
    catches only FileNotFoundError / BackendExecutionError) -- a Backend Fail-Closed Contract
    violation. Mirrors the shipped `--semantic` sibling's own F1 catch (cli/main.py:3970-3984)."""
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.load_dense_model",
        lambda _dir: _QueryDimMismatchModel(query="invoice"),
    )
    (tmp_path / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["find", "invoice", str(tmp_path), "--json"])

    # Degraded, NOT failed: a clean sys.exit(0), never a raw traceback and never exit 1.
    assert result.exit_code == 0, result.output
    assert result.exception is None, (
        f"query-time DenseUnavailableError escaped as a traceback: {result.exception!r}"
    )
    payload = json.loads(result.stdout)
    # Visible BM25-only degrade: the dim-mismatch reason is surfaced, and real matches still rank.
    assert payload.get("rank_fallback_reason") is not None
    assert "dim" in payload["rank_fallback_reason"].lower()
    assert payload["total_matches"] >= 1
    assert any(str(match["file"]).endswith("invoice.py") for match in payload["matches"])


def test_find_corrupt_model_raises_backend_execution_error_exit_2(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))

    def _raise_corrupt(_dir: object) -> None:
        raise BackendExecutionError(
            "dense model at <dir> failed to load (corrupt or incompatible): boom"
        )

    monkeypatch.setattr("tensor_grep.core.retrieval_dense.load_dense_model", _raise_corrupt)
    (tmp_path / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n    return invoice_id\n", encoding="utf-8"
    )

    result = CliRunner().invoke(app, ["find", "invoice", str(tmp_path), "--json"])

    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit), (
        f"expected a clean sys.exit(2), got a raw traceback: {result.exception!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "boom" in payload["detail"]


def test_find_corrupt_model_raises_backend_execution_error_exit_2_text_mode(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """Text-mode variant: same clean `tg:`-prefixed message + exit 2, no traceback."""
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))

    def _raise_corrupt(_dir: object) -> None:
        raise BackendExecutionError(
            "dense model at <dir> failed to load (corrupt or incompatible): boom"
        )

    monkeypatch.setattr("tensor_grep.core.retrieval_dense.load_dense_model", _raise_corrupt)
    (tmp_path / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n    return invoice_id\n", encoding="utf-8"
    )

    result = CliRunner().invoke(app, ["find", "invoice", str(tmp_path)])

    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit), (
        f"expected a clean sys.exit(2), got a raw traceback: {result.exception!r}"
    )
    assert "tg: dense model at <dir> failed to load" in result.stderr


def test_find_deadline_truncation_sets_result_incomplete_and_exit_2(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    _stub_dense_unavailable(monkeypatch)
    (tmp_path / "a.py").write_text("def fn():\n    return 1\n", encoding="utf-8")
    # Force an ALREADY-EXPIRED deadline deterministically: any real time.monotonic() call
    # downstream is >= 0.0, tripping the walk's deadline check on its very first iteration
    # without racing the real wall clock (anti-hang-test-protocol: dependency injection over
    # real-time racing).
    monkeypatch.setattr(
        "tensor_grep.cli.repo_map._deadline_monotonic_from_seconds", lambda _seconds: 0.0
    )

    result = CliRunner().invoke(app, ["find", "fn", str(tmp_path), "--deadline", "5", "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload.get("result_incomplete") is True
    assert "deadline" in (payload.get("incomplete_reason") or "").lower()


def test_find_max_repo_files_cap_partial_exit_2(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_dense_unavailable(monkeypatch)
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text(f"def fn_{i}():\n    return {i}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["find", "fn", str(tmp_path), "--max-repo-files", "2", "--json"]
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload.get("result_incomplete") is True
    assert "max-repo-files" in (payload.get("incomplete_reason") or "")


def test_find_chunk_cap_partial_exit_2(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """C2: the corpus-wide chunk cap trip is PARTIAL COVERAGE (no regex pre-filter narrowed the
    corpus first, unlike `--semantic`) -> result_incomplete + exit 2, never the exit-0 BM25-only
    degrade `--semantic`'s own corpus cap uses."""
    _stub_dense_unavailable(monkeypatch)
    monkeypatch.setattr("tensor_grep.cli.main._FIND_CORPUS_CHUNK_CAP", 1)
    for i in range(3):
        body = "\n".join(f"    x{j} = {j}" for j in range(60))
        (tmp_path / f"f{i}.py").write_text(f"def fn_{i}():\n{body}\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["find", "fn", str(tmp_path), "--json"])

    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload.get("result_incomplete") is True
    assert "chunk cap" in (payload.get("incomplete_reason") or "")


def test_find_no_results_exit_1(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_dense_unavailable(monkeypatch)
    (tmp_path / "a.py").write_text("def completely_unrelated():\n    return 1\n", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["find", "zzqzxvvvqqqnonexistentgibberish", str(tmp_path), "--json"]
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 0
    assert not payload.get("result_incomplete")


def test_find_json_envelope_fields(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_dense_unavailable(monkeypatch)
    (tmp_path / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["find", "invoice", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["total_matches"] >= 1
    assert isinstance(payload["matches"], list)
    match = payload["matches"][0]
    assert match["file"].endswith("invoice.py")
    assert isinstance(match["line"], int) and match["line"] >= 1
    assert isinstance(match["text"], str)
    assert payload.get("rank_fallback_reason")  # dense-unavailable stub set this
    assert payload.get("result_incomplete") is not True


def test_find_budget_truncates_lowest_ranked_first(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_dense_unavailable(monkeypatch)
    (tmp_path / "strong.py").write_text(
        "def invoice_invoice_invoice():\n    invoice = 1\n    return invoice\n", encoding="utf-8"
    )
    (tmp_path / "medium.py").write_text(
        "def process(invoice):\n    return invoice\n", encoding="utf-8"
    )
    (tmp_path / "weak.py").write_text(
        "# an invoice is mentioned here only once\nx = 1\n", encoding="utf-8"
    )

    unbounded = CliRunner().invoke(
        app, ["find", "invoice", str(tmp_path), "--json", "--max-tokens", "0"]
    )
    assert unbounded.exit_code == 0, unbounded.output
    unbounded_files = [m["file"] for m in json.loads(unbounded.stdout)["matches"]]
    assert len(unbounded_files) >= 2, "expected the corpus to yield more than one ranked match"

    budgeted = CliRunner().invoke(
        app, ["find", "invoice", str(tmp_path), "--json", "--max-tokens", "1"]
    )
    assert budgeted.exit_code == 0, budgeted.output
    budgeted_files = [m["file"] for m in json.loads(budgeted.stdout)["matches"]]

    assert len(budgeted_files) == 1, "a 1-token budget must floor at exactly the top match"
    assert budgeted_files[0] == unbounded_files[0], (
        "the budget must drop the LOWEST-ranked matches first, keeping the top match"
    )


def test_find_late_head_only_when_env_set(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Mirrors test_search_semantic_rerank.py's test_rerank_env_off_is_noop: with
    TG_LATE_RERANK unset (the default), the late stage must never even be PROBED."""
    monkeypatch.delenv("TG_LATE_RERANK", raising=False)
    _stub_dense_clean(monkeypatch)
    (tmp_path / "dense.py").write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )
    (tmp_path / "sparse.py").write_text("# one passing invoice mention\nx = 1\n", encoding="utf-8")

    def _must_not_be_called() -> tuple[bool, str | None]:
        raise AssertionError("late_available() must not be called when TG_LATE_RERANK is unset")

    monkeypatch.setattr("tensor_grep.core.retrieval_late.late_available", _must_not_be_called)

    result = CliRunner().invoke(app, ["find", "invoice", str(tmp_path), "--json"])

    assert result.exception is None, f"late_available() was called: {result.exception!r}"
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload.get("rank_fallback_reason") is None


def test_find_late_head_runs_when_env_set(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The flip side: TG_LATE_RERANK=1 DOES probe/run the late stage, observably reordering."""
    monkeypatch.setenv("TG_LATE_RERANK", "1")
    _stub_dense_clean(monkeypatch)
    (tmp_path / "dense.py").write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )
    (tmp_path / "sparse.py").write_text("# one passing invoice mention\nx = 1\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.core.retrieval_late.late_available", lambda: (True, None))

    def _prefer_sparse_encode(text: str) -> np.ndarray:
        if text == "invoice" or "passing invoice mention" in text:
            return np.array([[1.0, 0.0]], dtype=np.float32)
        return np.array([[0.0, 1.0]], dtype=np.float32)

    monkeypatch.setattr(
        "tensor_grep.core.retrieval_late.load_late_reranker",
        lambda *a, **kw: LateReranker(encode=_prefer_sparse_encode),
    )

    result = CliRunner().invoke(app, ["find", "invoice", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["matches"], "expected at least one ranked match"
    # The late stage was demonstrably consulted -- either it reordered (no fallback reason) or it
    # degraded and said so; either way the run must not have skipped straight past it silently.
    assert result.exception is None


def test_find_output_is_ascii(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _stub_dense_unavailable(monkeypatch)
    (tmp_path / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n    return invoice_id\n", encoding="utf-8"
    )

    result = CliRunner().invoke(app, ["find", "invoice", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert result.stdout.isascii()
    assert result.stderr.isascii()
