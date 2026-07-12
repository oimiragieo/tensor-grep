"""Integration: `tg search --rank` re-orders results by BM25 over a synthetic corpus."""

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.core.config import SearchConfig


def test_search_config_has_rank_bm25_field() -> None:
    cfg = SearchConfig()
    assert hasattr(cfg, "rank_bm25")
    assert cfg.rank_bm25 is False


def test_search_rank_reorders_by_bm25(tmp_path: Path) -> None:
    dense = tmp_path / "dense.py"
    dense.write_text(
        "def make_invoice(invoice_id):\n    invoice = invoice_id\n    return invoice\n",
        encoding="utf-8",
    )
    sparse = tmp_path / "sparse.py"
    sparse.write_text("# one passing invoice mention\nx = 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "--rank", "--json", "invoice", str(tmp_path)])
    assert result.exit_code == 0, result.output

    # `--rank` refuses native delegation (round-4 #25) so the BM25 rerank runs in-process
    # and the JSON is emitted via typer.echo -> CliRunner's captured stdout (NOT the fd-level
    # stream a delegated native subprocess would have written to).
    payload = json.loads(result.stdout)
    matches = payload.get("matches", [])
    assert matches, f"expected matches for 'invoice', got: {payload}"

    # The invoice-dense file's matches must rank ahead of the sparse file's.
    files_in_order = [m["file"] for m in matches]
    assert files_in_order[0] == str(dense)
    assert str(sparse) in files_in_order  # the sparse match is still present, just lower
    assert files_in_order.index(str(dense)) < files_in_order.index(str(sparse))


def test_search_rank_corpus_cap_sets_fallback_reason_and_bounds_chunking(
    tmp_path: Path, monkeypatch
) -> None:
    """#128d (backlog cluster-1 P0-CORRECTNESS, MED-1): plain `tg search --rank` -- unlike
    `--semantic`, already capped via #527/A2 -- previously chunked the ENTIRE matched-file set
    with NO total bound (only the per-FILE MAX_CHUNKS guard existed). This is the real end-to-end
    CLI proof of the reranker.py chokepoint fix: TG_RANK_CORPUS_CHUNK_CAP set below the corpus's
    chunk total must stop chunking early, surface `rank_fallback_reason` in the JSON envelope
    (mirroring the semantic path's cap-fallback contract), and never drop a match."""
    monkeypatch.setenv("TG_RANK_CORPUS_CHUNK_CAP", "1")

    from tensor_grep.core import retrieval_chunker

    real_chunk_file = retrieval_chunker.chunk_file
    calls: list[str] = []

    def _counting_chunk_file(path: str, **kwargs: object):
        calls.append(str(path))
        return real_chunk_file(path, **kwargs)

    # Patch reranker.py's bound name -- the same site plain --rank actually calls through.
    monkeypatch.setattr("tensor_grep.core.reranker.chunk_file", _counting_chunk_file)

    for i in range(3):
        (tmp_path / f"f{i}.py").write_text(
            f"def make_invoice_{i}(x):\n    return x\n", encoding="utf-8"
        )

    result = CliRunner().invoke(app, ["search", "--rank", "--json", "invoice", str(tmp_path)])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    matches = payload.get("matches", [])
    assert len(matches) == 3, f"expected all 3 matches preserved, got: {payload}"
    assert "corpus cap" in payload.get("rank_fallback_reason", "")
    # Cap=1: file 1 chunked (1 chunk, 1<=1) then file 2 (2>1) trips -- file 3 never chunked.
    assert len(calls) == 2, f"chunking did not stop after the cap tripped: {calls}"
