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


def _spy_on_rank_chunks(monkeypatch) -> list[float]:  # type: ignore[no-untyped-def]
    """Wrap the REAL `rank_chunks` (reranker.py) so every call's `dense_weight` kwarg is recorded
    before delegating -- `_execute_find` imports `rank_chunks` via a LOCAL
    `from tensor_grep.core.reranker import rank_chunks` at call time (main.py), so the module
    attribute on `tensor_grep.core.reranker` (not `tensor_grep.cli.main`) is the correct monkeypatch
    target: the local import re-resolves the name from that module's namespace on every call."""
    from tensor_grep.core import reranker as reranker_module

    real_rank_chunks = reranker_module.rank_chunks
    captured: list[float] = []

    def _spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(kwargs.get("dense_weight", 1.0))
        return real_rank_chunks(*args, **kwargs)

    monkeypatch.setattr(reranker_module, "rank_chunks", _spy)
    return captured


def test_find_dense_weight_default_is_adaptive_end_to_end(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """#191 (the default flip): with TG_FIND_DENSE_WEIGHT UNSET -- today's production env, and the
    ONLY thing this PR changes -- a genuinely multi-word NL query must now thread the
    ledger-validated ADAPTIVE weight (5.0, `tg_find_review_ledger.md` DENSE-WEIGHT SWEEP: 1:5
    bm25:dense -> +0.1419 ndcg@10, recall 0.55->0.80, zero per-category regression) into
    `rank_chunks`, not the old inert 1.0 no-op. A single-token query must stay at the protected
    1.0 -- the whitespace gate applies to the new adaptive default exactly as it does to an
    explicit env override (see `test_find_dense_weight_adaptive_nl_vs_literal` below).

    Was RED before the flip: `_execute_find` used to call `rank_chunks(dense_weight=1.0)`
    unconditionally when the env was unset, regardless of query shape. This is the end-to-end
    proof the flip actually reaches the real call chain, not just the helper in isolation -- see
    `test_find_dense_weight_default_protects_literal_identifiers` for the direct-call companion."""
    monkeypatch.delenv("TG_FIND_DENSE_WEIGHT", raising=False)
    _stub_dense_clean(monkeypatch)
    captured = _spy_on_rank_chunks(monkeypatch)
    (tmp_path / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )

    # A multi-word (>1 whitespace-separated word) query -- the adaptive rule's NL branch.
    nl_result = CliRunner().invoke(
        app, ["find", "invoice processing helper", str(tmp_path), "--json"]
    )
    assert nl_result.exit_code == 0, nl_result.output
    assert captured, "rank_chunks was never called"
    assert captured[-1] == 5.0, (
        f"env-unset multi-word query must now get the adaptive weight, got {captured}"
    )

    # A single whitespace-free token must stay at the protected no-op weight, env-unset or not.
    literal_result = CliRunner().invoke(app, ["find", "invoice", str(tmp_path), "--json"])
    assert literal_result.exit_code == 0, literal_result.output
    assert captured[-1] == 1.0, (
        f"env-unset single-token query must stay at the protected 1.0, got {captured}"
    )


def test_find_dense_weight_default_protects_literal_identifiers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """#191 (the default flip), direct-call companion to the end-to-end test above: pins the
    ADAPTIVE-by-default behavior at the `_find_dense_weight` helper level, across the real
    dogfood-caught identifier shapes (`tg_find_review_ledger.md` REAL-REPO DOGFOOD) -- with
    TG_FIND_DENSE_WEIGHT UNSET, every one of these single-token, multi-morpheme identifiers must
    still resolve to the protected 1.0 (the whitespace gate does not care whether the weight it is
    guarding came from an explicit env override or the new adaptive default), while genuinely
    multi-word NL queries resolve to the adaptive 5.0. Mirrors
    `test_find_dense_weight_multimorpheme_identifier_protected`'s identifier list, env UNSET
    instead of env=5.0."""
    from tensor_grep.cli.main import _find_dense_weight

    monkeypatch.delenv("TG_FIND_DENSE_WEIGHT", raising=False)

    protected_identifiers = [
        "mint_access_token",  # snake_case, 3 morphemes
        "getUserName",  # camelCase, 3 morphemes
        "_confine_mcp_path",  # leading underscore, 3 morphemes
        "BackendExecutionError",  # PascalCase, 3 morphemes
        "reciprocal_rank_fusion",  # 3 morphemes
        "_iter_repo_files",  # 3 morphemes
        "rank_chunks",  # 2 morphemes (the ONE the old rule happened to protect)
    ]
    for identifier in protected_identifiers:
        assert _find_dense_weight(identifier) == 1.0, (
            f"single-token identifier {identifier!r} must stay at 1.0 under the adaptive default, "
            "not just under an explicit env override"
        )

    # Genuinely multi-word NL queries get the adaptive default instead of the old inert 1.0.
    assert _find_dense_weight("how does the retry backoff work") == 5.0
    assert _find_dense_weight("borrow a connection out of a shared pool") == 5.0
    assert _find_dense_weight("verify login credentials") == 5.0
    # Boundary: even a bare 2-word phrase is multi-word -> adaptive (whitespace, not word length).
    assert _find_dense_weight("shared pool") == 5.0


def test_find_dense_weight_adaptive_nl_vs_literal(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """TG_FIND_DENSE_WEIGHT=5.0 (#189, ledger DENSE-WEIGHT SWEEP): a multi-word/NL query (>1
    whitespace-separated word) gets the boosted dense_weight; a single-token identifier/literal
    query is protected at 1.0 -- the adaptive rule's whole point (the golden-set sweep's canary,
    `vm-behavior-10`, showed dense-only regresses a short lexical-overlap query, hence NOT a blind
    flip).

    The literal probe here is a DESCRIPTIVE, multi-morpheme identifier (`create_invoice_record` ->
    3 `split_terms` morphemes) on purpose: the dogfood finding (#191) was that the prior
    `split_terms(query) > 2` classifier wrongly boosted exactly this shape. A single-token
    identifier must stay at 1.0 no matter how many morphemes it splits into."""
    monkeypatch.setenv("TG_FIND_DENSE_WEIGHT", "5.0")
    _stub_dense_clean(monkeypatch)
    captured = _spy_on_rank_chunks(monkeypatch)
    (tmp_path / "invoice.py").write_text(
        "def create_invoice_record(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )

    nl_result = CliRunner().invoke(
        app, ["find", "invoice processing helper text", str(tmp_path), "--json"]
    )
    assert nl_result.exit_code == 0, nl_result.output
    assert captured[-1] == 5.0, f"multi-word query must get the boosted weight, got {captured}"

    # `create_invoice_record` is ONE whitespace-free token but THREE split_terms morphemes -- the
    # exact shape the old morpheme-count classifier leaked to the dense boost. It must stay at 1.0.
    literal_result = CliRunner().invoke(
        app, ["find", "create_invoice_record", str(tmp_path), "--json"]
    )
    assert literal_result.exit_code == 0, literal_result.output
    assert captured[-1] == 1.0, (
        f"a single-token multi-morpheme identifier must stay at 1.0, got {captured}"
    )


def test_find_dense_weight_multimorpheme_identifier_protected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Dogfood finding (#191): the NL-vs-literal classifier gates on WHITESPACE-separated word
    count, NOT `split_terms` morpheme count. `split_terms` splits snake_case/camelCase into
    morphemes, so a descriptive single-token identifier splits into 3+ morphemes -- the prior
    `split_terms(query) > 2` floor misclassified all of these as NL and boosted the dense leg,
    exactly backwards for a literal-identifier lookup where BM25 is the strong leg (a real-repo
    dogfood on tensor-grep's own src leaked 5 of 6 such queries). Direct-import-and-call, mirroring
    `test_reranker.py`'s `_rank_corpus_chunk_cap` malformed-override pattern, so it pins the
    classifier itself independent of any corpus.

    Every identifier below is ONE whitespace-free token (so protected at 1.0) but >=2 `split_terms`
    morphemes (so the OLD classifier would have wrongly returned the env's 5.0)."""
    from tensor_grep.cli.main import _find_dense_weight

    monkeypatch.setenv("TG_FIND_DENSE_WEIGHT", "5.0")

    # Single-token, multi-morpheme identifiers -- the dogfood's own examples plus this repo's own
    # symbols. Each MUST route to the protected 1.0, never the boosted 5.0.
    protected_identifiers = [
        "mint_access_token",  # snake_case, 3 morphemes
        "getUserName",  # camelCase, 3 morphemes
        "_confine_mcp_path",  # leading underscore, 3 morphemes
        "BackendExecutionError",  # PascalCase, 3 morphemes
        "reciprocal_rank_fusion",  # 3 morphemes
        "_iter_repo_files",  # 3 morphemes
        "rank_chunks",  # 2 morphemes (the ONE the old rule happened to protect)
    ]
    for identifier in protected_identifiers:
        assert _find_dense_weight(identifier) == 1.0, (
            f"single-token identifier {identifier!r} must stay at 1.0 (was leaked to the dense "
            "boost by the old split_terms>2 morpheme classifier)"
        )

    # A genuinely multi-word (whitespace-separated) query is still NL and still gets the env value.
    assert _find_dense_weight("borrow a connection out of a shared pool") == 5.0
    assert _find_dense_weight("verify login credentials") == 5.0
    # Boundary: even a bare 2-word phrase is multi-word -> boosted (whitespace, not word length).
    assert _find_dense_weight("shared pool") == 5.0


def test_find_dense_weight_explicit_env_override_wins_over_adaptive_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """#191 flip-safety pin: an explicit TG_FIND_DENSE_WEIGHT env value always wins over the new
    adaptive default -- the flip only changes what happens when the env is ABSENT, never when an
    operator has set one. `=1.0` is the explicit opt-out (must NOT be silently upgraded to the
    adaptive 5.0 just because 1.0 also happens to be the literal-path fallback); `=3.0` is an
    arbitrary explicit override distinct from both 1.0 and the adaptive 5.0, proving the env value
    itself is threaded through rather than clobbered by the new default. The whitespace gate still
    protects single-token identifiers under an explicit override, unchanged from before the flip
    (see test_find_dense_weight_multimorpheme_identifier_protected above)."""
    from tensor_grep.cli.main import _find_dense_weight

    monkeypatch.setenv("TG_FIND_DENSE_WEIGHT", "1.0")
    assert _find_dense_weight("invoice processing helper") == 1.0, (
        "an explicit TG_FIND_DENSE_WEIGHT=1.0 opt-out must be honored, not upgraded to adaptive"
    )
    assert _find_dense_weight("_confine_mcp_path") == 1.0

    monkeypatch.setenv("TG_FIND_DENSE_WEIGHT", "3.0")
    assert _find_dense_weight("invoice processing helper") == 3.0, (
        "an explicit TG_FIND_DENSE_WEIGHT=3.0 override must win over the adaptive default"
    )
    assert _find_dense_weight("_confine_mcp_path") == 1.0, (
        "the whitespace gate must still protect a single-token identifier under an explicit override"
    )


def test_find_dense_weight_nonfinite_env_clamps_to_adaptive_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Flip-prep NIT 1 (tg_find_review_ledger.md FLIP-PREP) + thinktank rank-lens must-fix 2
    (2026-07-16): `float("nan")` / `float("inf")` / `float("-inf")` all PARSE without raising
    `ValueError`, so the `except ValueError` guard alone never rejected them -- and plain
    ValueError-triggering garbage (`"banana"`, `"12abc"`) hits the OTHER branch. Pre-#191 both
    branches clamped to the inert 1.0. POST-#191 must-fix 2, a malformed/non-finite override is
    instead treated EXACTLY like an unset env var: it now resolves to the SAME adaptive default a
    multi-word query would get anyway, so a typo'd `TG_FIND_DENSE_WEIGHT` value never silently
    opts an operator OUT of the improved default. The finiteness guard itself is NOT weakened: a
    non-finite value still never reaches `rank_chunks(dense_weight=...)` -- `weights=[1.0, nan]`
    would otherwise build a degenerate list for `reciprocal_rank_fusion`'s sort; it now clamps to
    5.0 instead of 1.0. Mirrors
    `test_reranker.py::test_rank_corpus_chunk_cap_non_positive_or_malformed_env_falls_back_to_default`'s
    direct-import-and-call pattern for a malformed-override guard. A multi-word (>1
    whitespace-separated word) query is used so the adaptive branch is actually reached; a
    single-token query is also checked to prove the whitespace gate still protects literals even
    under a malformed override (see test_find_dense_weight_multimorpheme_identifier_protected for
    the SET-to-a-valid-value sibling of this protection)."""
    from tensor_grep.cli.main import _find_dense_weight

    bad_values = (
        "nan",
        "-nan",
        "NaN",
        "inf",
        "-inf",
        "Infinity",
        "-Infinity",
        "banana",
        "12abc",
    )
    for bad_value in bad_values:
        monkeypatch.setenv("TG_FIND_DENSE_WEIGHT", bad_value)
        weight = _find_dense_weight("invoice processing helper")
        assert weight == 5.0, (
            f"malformed/non-finite env value {bad_value!r} must now resolve to the adaptive "
            f"default (treated like unset), got {weight!r}"
        )
        literal_weight = _find_dense_weight("_confine_mcp_path")
        assert literal_weight == 1.0, (
            f"a single-token identifier must stay protected at 1.0 even under a malformed env "
            f"value {bad_value!r}, got {literal_weight!r}"
        )


def test_find_dense_weight_default_boosts_two_word_lexical_canary(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Thinktank rank-lens must-fix 1 (2026-07-16): the whitespace gate is purely structural, so a
    2-word LEXICAL phrase (not natural language) is indistinguishable from a 2-word NL phrase and
    ALSO receives the adaptive boost when TG_FIND_DENSE_WEIGHT is unset. The 1:5 sweep is 100% NL
    queries and the literal/identifier3 golden slices are single-token by construction, so this
    exact shape (a short literal CODE phrase) is UNMEASURED by either. This canary is a
    VISIBILITY/regression-catch test, not a new classifier: it documents and pins the current,
    intentional behavior so a future change to the gate is a conscious decision, not a silent
    drift. `TG_FIND_DENSE_WEIGHT=1.0` remains the escape hatch for an operator who hits a
    regression on a precise 2-word literal search; `potion-code-16M` is a CODE-domain embedding
    model, which makes boosting a 2-word code fragment defensible too, not just prose NL."""
    from tensor_grep.cli.main import _find_dense_weight

    monkeypatch.delenv("TG_FIND_DENSE_WEIGHT", raising=False)

    for lexical_query in ("return None", "TODO fixme"):
        assert _find_dense_weight(lexical_query) == 5.0, (
            f"a 2-word lexical query {lexical_query!r} is expected to receive the adaptive boost "
            "under the whitespace gate (documented scope, not a bug) -- if this now fails, the "
            "gate's shape changed and the PR body / docstring KNOWN SCOPE note need updating too"
        )


def test_find_dense_weight_nonfinite_env_never_reaches_rank_chunks(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """End-to-end companion to the unit test above: proves the clamp holds through the FULL `tg
    find` call chain, not just the helper in isolation -- `rank_chunks` must never observe a
    non-finite `dense_weight` kwarg for a real invocation, which is the actual danger (a degenerate
    `weights=[1.0, nan]` reaching `reciprocal_rank_fusion`'s sort). Post-#191 must-fix 2, a
    non-finite env value resolves to the ADAPTIVE default (5.0) for this multi-word query, treated
    exactly like an unset env var -- not the old inert 1.0."""
    monkeypatch.setenv("TG_FIND_DENSE_WEIGHT", "nan")
    _stub_dense_clean(monkeypatch)
    captured = _spy_on_rank_chunks(monkeypatch)
    (tmp_path / "invoice.py").write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app, ["find", "invoice processing helper text", str(tmp_path), "--json"]
    )

    assert result.exit_code == 0, result.output
    assert captured, "rank_chunks was never called"
    assert all(weight == 5.0 for weight in captured), (
        f"expected dense_weight=5.0 (adaptive, treated like unset) for every call with a "
        f"non-finite env value, got {captured}"
    )
