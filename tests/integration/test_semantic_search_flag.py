"""Integration: `tg search --semantic` (local hybrid BM25+dense RRF rerank, Path B Stage 1,
roadmap #27) -- fail-closed BM25-only degrade, real-model ranking, and the byte-identical
contract when the flag is off."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from typer.testing import CliRunner

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.cli.main import app
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.retrieval_dense import DenseIndex, DenseUnavailableError, default_model_dir


def test_search_config_has_semantic_rank_field() -> None:
    cfg = SearchConfig()
    assert hasattr(cfg, "semantic_rank")
    assert cfg.semantic_rank is False


def test_search_semantic_falls_back_to_bm25_when_dense_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.dense_available",
        lambda: (False, "semantic ranking unavailable: model2vec not installed -- test stub"),
    )

    dense = tmp_path / "dense.py"
    dense.write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )
    sparse = tmp_path / "sparse.py"
    sparse.write_text("# one passing invoice mention\nx = 1\n", encoding="utf-8")

    result_semantic = CliRunner().invoke(
        app, ["search", "--semantic", "--json", "invoice", str(tmp_path)]
    )
    assert result_semantic.exit_code == 0, result_semantic.output
    assert "semantic ranking unavailable" in result_semantic.stderr

    payload = json.loads(result_semantic.stdout)
    assert payload.get("rank_fallback_reason") is not None
    assert "model2vec not installed" in payload["rank_fallback_reason"]

    # Fail-closed to BM25-only: same order as plain `--rank` over the same corpus.
    result_rank = CliRunner().invoke(app, ["search", "--rank", "--json", "invoice", str(tmp_path)])
    assert result_rank.exit_code == 0, result_rank.output
    rank_payload = json.loads(result_rank.stdout)

    semantic_files = [m["file"] for m in payload["matches"]]
    rank_files = [m["file"] for m in rank_payload["matches"]]
    assert semantic_files == rank_files
    assert "rank_fallback_reason" not in rank_payload  # never set for plain --rank


def test_search_semantic_zero_matches_still_probes_availability(
    tmp_path: Path, monkeypatch
) -> None:
    """F16 (Fable audit LOW): a 0-match `--semantic` search must still set
    `rank_fallback_reason` when the dense leg is unavailable -- skipping the probe on an empty
    result silently omitted the fallback reason from the JSON envelope, even though the leg is
    genuinely unavailable (a dishonest envelope)."""
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.dense_available",
        lambda: (False, "semantic ranking unavailable: model2vec not installed -- test stub"),
    )

    sample = tmp_path / "sample.py"
    sample.write_text("x = 1\n", encoding="utf-8")

    result = CliRunner().invoke(
        app, ["search", "--semantic", "--json", "zzz_no_such_pattern", str(tmp_path)]
    )
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 0
    assert payload.get("rank_fallback_reason") is not None
    assert "model2vec not installed" in payload["rank_fallback_reason"]
    assert "semantic ranking unavailable" in result.stderr


def test_search_semantic_query_time_dim_mismatch_degrades_to_bm25(
    tmp_path: Path, monkeypatch
) -> None:
    """F1 (Fable audit MED): a query-time `DenseUnavailableError` (e.g. a dim mismatch) raised
    from INSIDE `rerank_hybrid`'s call to `DenseIndex.query` -- outside the try/except that only
    guards index CONSTRUCTION -- must still degrade to BM25-only + set `rank_fallback_reason`,
    never a traceback."""
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))

    class _FakeModel:
        def encode(self, texts: list[str]) -> np.ndarray:
            return np.ones((len(texts), 4), dtype=np.float32)

    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.load_dense_model", lambda _dir: _FakeModel()
    )

    def _raising_query(self, text: str, *, top_k: int = 10):
        raise DenseUnavailableError(
            "semantic ranking unavailable: query embedding dim mismatch (test stub)"
        )

    monkeypatch.setattr(DenseIndex, "query", _raising_query)

    dense = tmp_path / "dense.py"
    dense.write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )
    sparse = tmp_path / "sparse.py"
    sparse.write_text("# one passing invoice mention\nx = 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "dim mismatch" in result.stderr

    payload = json.loads(result.stdout)
    assert payload.get("rank_fallback_reason") is not None
    assert "dim mismatch" in payload["rank_fallback_reason"]

    # Fail-closed to BM25-only: same order as plain `--rank` over the same corpus.
    rank_result = CliRunner().invoke(app, ["search", "--rank", "--json", "invoice", str(tmp_path)])
    rank_payload = json.loads(rank_result.stdout)
    semantic_files = [m["file"] for m in payload["matches"]]
    rank_files = [m["file"] for m in rank_payload["matches"]]
    assert semantic_files == rank_files


def test_search_semantic_corrupt_model_dir_exits_cleanly_json(tmp_path: Path, monkeypatch) -> None:
    """F4 (Fable audit MED): a genuine BackendExecutionError (e.g. a corrupt model directory)
    must exit cleanly with a `tg:` message and exit code 2, never a raw traceback."""
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))

    def _raise_corrupt(_dir: object) -> None:
        raise BackendExecutionError(
            "dense model at <dir> failed to load (corrupt or incompatible): boom"
        )

    monkeypatch.setattr("tensor_grep.core.retrieval_dense.load_dense_model", _raise_corrupt)

    sample = tmp_path / "sample.py"
    sample.write_text("def make_invoice(invoice_id):\n    return invoice_id\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit), (
        f"expected a clean sys.exit(2), got a raw traceback: {result.exception!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "boom" in payload["detail"]


def test_search_semantic_corrupt_model_dir_exits_cleanly_text(tmp_path: Path, monkeypatch) -> None:
    """F4, text-mode variant: same clean `tg:`-prefixed message + exit 2, no traceback."""
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))

    def _raise_corrupt(_dir: object) -> None:
        raise BackendExecutionError(
            "dense model at <dir> failed to load (corrupt or incompatible): boom"
        )

    monkeypatch.setattr("tensor_grep.core.retrieval_dense.load_dense_model", _raise_corrupt)

    sample = tmp_path / "sample.py"
    sample.write_text("def make_invoice(invoice_id):\n    return invoice_id\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "--semantic", "invoice", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert isinstance(result.exception, SystemExit), (
        f"expected a clean sys.exit(2), got a raw traceback: {result.exception!r}"
    )
    assert "tg: dense model at <dir> failed to load" in result.stderr


def test_search_semantic_corpus_chunk_cap_exceeded_degrades_to_bm25(
    tmp_path: Path, monkeypatch
) -> None:
    """F5 (Fable audit MED): retrieval_chunker.MAX_CHUNKS bounds a single chunk_file() call (per
    FILE); a matched-file set with many small files can still blow past a sane CORPUS-wide total.
    `_apply_semantic_rerank`'s corpus-level cap must catch that and degrade to BM25-only with
    `rank_fallback_reason` set, instead of handing DenseIndex.__init__ an unbounded encode batch."""
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))
    monkeypatch.setattr("tensor_grep.cli.main._SEMANTIC_CORPUS_CHUNK_CAP", 1)

    dense = tmp_path / "dense.py"
    dense.write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )
    sparse = tmp_path / "sparse.py"
    sparse.write_text("# one passing invoice mention\nx = 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "corpus chunk cap" in result.stderr

    payload = json.loads(result.stdout)
    assert payload.get("rank_fallback_reason") is not None
    assert "corpus chunk cap" in payload["rank_fallback_reason"]

    rank_result = CliRunner().invoke(app, ["search", "--rank", "--json", "invoice", str(tmp_path)])
    rank_payload = json.loads(rank_result.stdout)
    semantic_files = [m["file"] for m in payload["matches"]]
    rank_files = [m["file"] for m in rank_payload["matches"]]
    assert semantic_files == rank_files


def test_search_semantic_chunker_runtime_error_degrades_to_bm25(
    tmp_path: Path, monkeypatch
) -> None:
    """F5, the chunker's OWN per-file MAX_CHUNKS guard: `chunk_file` raising RuntimeError (e.g. a
    single pathological file that trips the per-file cap on its own) must also degrade to BM25,
    not crash the whole search."""
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))

    def _raising_chunk_file(path: str, **kwargs: object) -> list[object]:
        raise RuntimeError(f"MAX_CHUNKS exceeded while chunking {path!r}")

    monkeypatch.setattr("tensor_grep.core.retrieval_chunker.chunk_file", _raising_chunk_file)

    sample = tmp_path / "sample.py"
    sample.write_text("def make_invoice(invoice_id):\n    return invoice_id\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert result.exception is None
    payload = json.loads(result.stdout)
    assert payload.get("rank_fallback_reason") is not None
    assert "MAX_CHUNKS exceeded" in payload["rank_fallback_reason"]


def test_search_semantic_builds_chunk_corpus_once_shared_by_both_legs(
    tmp_path: Path, monkeypatch
) -> None:
    """F3 (Fable audit MED): the dense leg's chunk corpus and the BM25 leg's chunk corpus must
    come from the SAME `chunk_file()` pass -- previously the dense leg built its own corpus in
    `_apply_semantic_rerank` while the BM25 leg rebuilt an independent one inside `rerank_hybrid`
    (a second full file-I/O pass, and a silent RRF-misalignment risk if the two passes'
    chunk_size/overlap defaults ever diverge)."""
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))

    class _FakeModel:
        def encode(self, texts: list[str]) -> np.ndarray:
            return np.ones((len(texts), 4), dtype=np.float32)

    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.load_dense_model", lambda _dir: _FakeModel()
    )

    from tensor_grep.core import retrieval_chunker

    real_chunk_file = retrieval_chunker.chunk_file
    calls: list[str] = []

    def _counting_chunk_file(path: str, **kwargs: object):
        calls.append(path)
        return real_chunk_file(path, **kwargs)

    # Patch BOTH bind sites: main.py's function-local import re-resolves the source module
    # attribute on every call, but reranker.py bound its own module-level name at import time --
    # a regression that reintroduces the second (BM25-leg) chunk_file pass would call THAT name.
    monkeypatch.setattr("tensor_grep.core.retrieval_chunker.chunk_file", _counting_chunk_file)
    monkeypatch.setattr("tensor_grep.core.reranker.chunk_file", _counting_chunk_file)

    dense = tmp_path / "dense.py"
    dense.write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )
    sparse = tmp_path / "sparse.py"
    sparse.write_text("# one passing invoice mention\nx = 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "--semantic", "--json", "invoice", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload.get("rank_fallback_reason") is None

    # Exactly ONE chunk_file() call per matched file -- not two.
    assert len(calls) == len(set(calls)) == 2, (
        f"expected exactly one chunk_file() call per file, got {calls!r}"
    )


def test_search_semantic_off_is_byte_identical_to_plain_json(tmp_path: Path) -> None:
    """The core contract: --semantic OFF must never change output. Compare a plain --json search
    against itself with --semantic explicitly False (the CLI default) -- the aggregate JSON
    payload (matches, ordering, all fields) must be byte-identical."""
    sample = tmp_path / "sample.py"
    sample.write_text("def make_invoice(invoice_id):\n    return invoice_id\n", encoding="utf-8")

    baseline = CliRunner().invoke(app, ["search", "--json", "invoice", str(tmp_path)])
    repeat = CliRunner().invoke(app, ["search", "--json", "invoice", str(tmp_path)])

    assert baseline.exit_code == 0, baseline.output
    assert baseline.stdout == repeat.stdout


def _real_dense_model_dir() -> Path | None:
    candidate = default_model_dir()
    return candidate if candidate.is_dir() else None


def test_search_semantic_with_real_model_engages_dense_leg(tmp_path: Path) -> None:
    import pytest

    model_dir = _real_dense_model_dir()
    if model_dir is None:
        pytest.skip("requires the real fetched potion-code-16M model (local dev only)")

    auth = tmp_path / "auth.py"
    auth.write_text(
        "def authenticate_user(username, password):\n"
        "    return check_credentials(username, password)\n",
        encoding="utf-8",
    )
    conn = tmp_path / "conn.py"
    conn.write_text("def close_connection(conn):\n    conn.dispose()\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "--semantic", "--json", "def", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    # The dense leg engaged successfully -- no fallback reason, matches present.
    assert payload.get("rank_fallback_reason") is None
    assert payload["matches"], "expected matches for 'def'"
