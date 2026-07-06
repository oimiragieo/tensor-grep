"""TDD coverage for Cluster A of the MCP unbounded-scan audit (cursor+thinktank).

Several MCP tools used to call `build_repo_map` / the `build_symbol_*` builders with an
implicit `max_repo_files=None` (no cap), which lets an MCP agent trigger a full-repo
walk/parse on a large monorepo. `tg_symbol_impact` already forwarded
`_DEFAULT_MCP_REPO_SCAN_LIMIT` correctly (see `test_mcp_context_render_exposes_and_forwards_max_repo_files`
in test_profiling_cli_mcp.py for the sibling pattern this file mirrors). These tests assert
that every remaining unbounded MCP symbol/AST tool now exposes and forwards the same cap.
"""

from __future__ import annotations

import inspect
import json

import pytest


def test_mcp_symbol_defs_exposes_and_forwards_max_repo_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import mcp_server

    signature = inspect.signature(mcp_server.tg_symbol_defs)
    assert signature.parameters["max_repo_files"].default == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT

    captured: dict[str, object] = {}

    def fake_build_symbol_defs(symbol: str, path: str, **kwargs: object) -> dict[str, object]:
        captured["symbol"] = symbol
        captured["path"] = path
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "build_symbol_defs", fake_build_symbol_defs)

    payload = json.loads(mcp_server.tg_symbol_defs("create_invoice", ".", max_repo_files=17))

    assert payload["ok"] is True
    assert captured["symbol"] == "create_invoice"
    assert captured["path"] == "."
    assert captured["max_repo_files"] == 17


def test_mcp_symbol_refs_exposes_and_forwards_max_repo_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import mcp_server

    signature = inspect.signature(mcp_server.tg_symbol_refs)
    assert signature.parameters["max_repo_files"].default == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT

    captured: dict[str, object] = {}

    def fake_build_symbol_refs(symbol: str, path: str, **kwargs: object) -> dict[str, object]:
        captured["symbol"] = symbol
        captured["path"] = path
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "build_symbol_refs", fake_build_symbol_refs)

    payload = json.loads(mcp_server.tg_symbol_refs("create_invoice", ".", max_repo_files=19))

    assert payload["ok"] is True
    assert captured["symbol"] == "create_invoice"
    assert captured["path"] == "."
    assert captured["max_repo_files"] == 19


def test_mcp_symbol_callers_exposes_and_forwards_max_repo_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import mcp_server

    signature = inspect.signature(mcp_server.tg_symbol_callers)
    assert signature.parameters["max_repo_files"].default == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT

    captured: dict[str, object] = {}

    def fake_build_symbol_callers(symbol: str, path: str, **kwargs: object) -> dict[str, object]:
        captured["symbol"] = symbol
        captured["path"] = path
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "build_symbol_callers", fake_build_symbol_callers)

    payload = json.loads(mcp_server.tg_symbol_callers("create_invoice", ".", max_repo_files=23))

    assert payload["ok"] is True
    assert captured["symbol"] == "create_invoice"
    assert captured["path"] == "."
    assert captured["max_repo_files"] == 23


def test_mcp_symbol_blast_radius_exposes_and_forwards_max_repo_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import mcp_server

    signature = inspect.signature(mcp_server.tg_symbol_blast_radius)
    assert signature.parameters["max_repo_files"].default == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT

    captured: dict[str, object] = {}

    def fake_build_symbol_blast_radius(
        symbol: str, path: str, **kwargs: object
    ) -> dict[str, object]:
        captured["symbol"] = symbol
        captured["path"] = path
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "build_symbol_blast_radius", fake_build_symbol_blast_radius)

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius("create_invoice", ".", max_repo_files=29)
    )

    assert payload["ok"] is True
    assert captured["symbol"] == "create_invoice"
    assert captured["path"] == "."
    assert captured["max_repo_files"] == 29


def test_mcp_symbol_blast_radius_plan_exposes_and_forwards_max_repo_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import mcp_server, repo_map

    signature = inspect.signature(mcp_server.tg_symbol_blast_radius_plan)
    assert signature.parameters["max_repo_files"].default == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT

    captured: dict[str, object] = {}

    def fake_build_symbol_blast_radius_plan(
        symbol: str, path: str, **kwargs: object
    ) -> dict[str, object]:
        captured["symbol"] = symbol
        captured["path"] = path
        captured.update(kwargs)
        return {"ok": True}

    # tg_symbol_blast_radius_plan imports the builder lazily inside the function body on
    # every call, so the patch target is the source module attribute, not mcp_server's.
    monkeypatch.setattr(
        repo_map, "build_symbol_blast_radius_plan", fake_build_symbol_blast_radius_plan
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius_plan("create_invoice", ".", max_repo_files=31)
    )

    assert payload["ok"] is True
    assert captured["symbol"] == "create_invoice"
    assert captured["path"] == "."
    assert captured["max_repo_files"] == 31


def test_mcp_symbol_blast_radius_render_exposes_and_forwards_max_repo_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tensor_grep.cli import mcp_server

    signature = inspect.signature(mcp_server.tg_symbol_blast_radius_render)
    assert signature.parameters["max_repo_files"].default == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT

    captured: dict[str, object] = {}

    def fake_build_symbol_blast_radius_render(
        symbol: str, path: str, **kwargs: object
    ) -> dict[str, object]:
        captured["symbol"] = symbol
        captured["path"] = path
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        mcp_server, "build_symbol_blast_radius_render", fake_build_symbol_blast_radius_render
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius_render("create_invoice", ".", max_repo_files=37)
    )

    assert payload["ok"] is True
    assert captured["symbol"] == "create_invoice"
    assert captured["path"] == "."
    assert captured["max_repo_files"] == 37


def test_mcp_ast_search_exposes_max_repo_files_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from tensor_grep.cli import mcp_server

    signature = inspect.signature(mcp_server.tg_ast_search)
    assert signature.parameters["max_repo_files"].default == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT


def test_mcp_ast_search_bounds_the_directory_walk_instead_of_scanning_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MAP-BUILD/scan itself must be bounded: `tg_ast_search` must stop invoking the
    AST backend once `max_repo_files` files have been searched, instead of exhausting the
    full `scanner.walk()` iterator (an unscoped full-monorepo walk/parse == MCP DoS)."""
    from unittest.mock import MagicMock, patch

    from tensor_grep.cli import mcp_server
    from tensor_grep.core.result import SearchResult

    fake_backend = type("AstGrepWrapperBackend", (), {"search": MagicMock()})()
    fake_backend.search.return_value = SearchResult(matches=[], total_files=0, total_matches=0)

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "AstGrepWrapperBackend"
        pipeline.selected_backend_reason = "ast_grep_json"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.py", "b.py", "c.py", "d.py", "e.py"]

        out = mcp_server.tg_ast_search("def $A():", "python", ".", max_repo_files=2)

    payload = json.loads(out)
    # Only the first `max_repo_files` files reach the backend -- the walk is bounded, not
    # merely the rendered output.
    assert fake_backend.search.call_count == 2
    assert payload["scan_limit"]["max_repo_files"] == 2
    assert payload["scan_limit"]["scanned_files"] == 2
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert payload["truncated"] is True


def test_mcp_ast_search_reports_no_cap_hit_when_under_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock, patch

    from tensor_grep.cli import mcp_server
    from tensor_grep.core.result import SearchResult

    fake_backend = type("AstGrepWrapperBackend", (), {"search": MagicMock()})()
    fake_backend.search.return_value = SearchResult(matches=[], total_files=0, total_matches=0)

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "AstGrepWrapperBackend"
        pipeline.selected_backend_reason = "ast_grep_json"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.py", "b.py"]

        out = mcp_server.tg_ast_search("def $A():", "python", ".", max_repo_files=512)

    payload = json.loads(out)
    assert fake_backend.search.call_count == 2
    assert payload["scan_limit"]["scanned_files"] == 2
    assert payload["scan_limit"]["possibly_truncated"] is False
    assert payload["truncated"] is False
