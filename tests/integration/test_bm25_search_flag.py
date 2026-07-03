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
