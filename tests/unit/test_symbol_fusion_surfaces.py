"""Verify ranking evidence survives the CLI, formatter, and confined MCP boundaries."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import mcp_server
from tensor_grep.cli.main import app


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_RRF_SYMBOLS", "1")
    monkeypatch.delenv("TG_RRF_CHANNELS", raising=False)
    monkeypatch.setenv("TG_SESSION_DAEMON_AUTOSTART", "0")
    monkeypatch.setattr(
        "tensor_grep.core.retrieval_dense.dense_available",
        lambda: (False, "semantic ranking unavailable: controlled test"),
    )
    (tmp_path / "decoy.py").write_text("# " + "target_func " * 12 + "\n", encoding="utf-8")
    (tmp_path / "target.py").write_text("def target_func():\n    pass\n", encoding="utf-8")
    return tmp_path


def _assert_evidence(payload):
    assert Path(payload["matches"][0]["file"]).name == "target.py"
    fusion = payload["rank_fusion"]
    assert fusion["ast_symbols"]["matched_symbols"] == ["target_func"]
    assert fusion["dense"]["available"] is False
    assert fusion["weights"]["ast_symbols"] == 1.0


@pytest.mark.parametrize("command", [["find"], ["search", "--rank"], ["search", "--semantic"]])
def test_cli_ranked_json_surfaces(corpus, command):
    result = CliRunner().invoke(app, [*command, "target_func", str(corpus), "--json"])
    assert result.exit_code == 0, result.output
    _assert_evidence(json.loads(result.stdout))


def test_search_ndjson_keeps_rank_evidence(corpus):
    result = CliRunner().invoke(app, ["search", "--rank", "target_func", str(corpus), "--ndjson"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert Path(rows[0]["file"]).name == "target.py"
    assert all(
        row["rank_fusion"]["ast_symbols"]["matched_symbols"] == ["target_func"] for row in rows
    )


@pytest.mark.parametrize("tool", ["find", "search"])
def test_mcp_ranked_surfaces(corpus, monkeypatch, tool):
    monkeypatch.chdir(corpus)
    if tool == "find":
        payload = json.loads(mcp_server.tg_find("target_func", path=str(corpus)))
    else:
        payload = json.loads(mcp_server.tg_search("target_func", path=str(corpus), rank=True))
    _assert_evidence(payload)


def test_default_output_has_no_opt_in_evidence(corpus, monkeypatch):
    monkeypatch.delenv("TG_RRF_SYMBOLS")
    result = CliRunner().invoke(app, ["find", "target_func", str(corpus), "--json"])
    assert result.exit_code == 0, result.output
    assert "rank_fusion" not in json.loads(result.stdout)


def test_find_labels_name_actual_ast_and_dense_legs(corpus):
    result = CliRunner().invoke(app, ["find", "target_func", str(corpus), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "SymbolHybridFindBackend"
    assert payload["routing_reason"] == "find_bm25_ast_rrf"
    assert payload["install_state"] == "ast_bm25_ready (dense unavailable; run tg install-dense)"


def test_find_without_exact_symbol_keeps_default_labels(corpus):
    result = CliRunner().invoke(app, ["find", "target", str(corpus), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "Bm25FindBackend"
    assert payload["routing_reason"] == "find_bm25_only"


@pytest.mark.parametrize("failure", [False, True])
def test_find_labels_follow_query_time_dense_outcome(corpus, monkeypatch, failure):
    import numpy as np

    class Model:
        def encode(self, texts):
            dim = 5 if failure and texts == ["target_func"] else 4
            return np.ones((len(texts), dim), dtype=np.float32)

    monkeypatch.setattr("tensor_grep.core.retrieval_dense.dense_available", lambda: (True, None))
    monkeypatch.setattr("tensor_grep.core.retrieval_dense.load_dense_model", lambda _: Model())
    result = CliRunner().invoke(app, ["find", "target_func", str(corpus), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_backend"] == "SymbolHybridFindBackend"
    assert payload["routing_reason"] == (
        "find_bm25_ast_rrf" if failure else "find_bm25_dense_ast_rrf"
    )
    assert payload["rank_fusion"]["dense"]["available"] is not failure
    if failure:
        assert "dim" in payload["rank_fallback_reason"]
