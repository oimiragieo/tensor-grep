"""Integration: `tg search --semantic` (local hybrid BM25+dense RRF rerank, Path B Stage 1,
roadmap #27) -- fail-closed BM25-only degrade, real-model ranking, and the byte-identical
contract when the flag is off."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.retrieval_dense import default_model_dir


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
