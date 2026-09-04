"""MCP search / AST search / devices / classify / scan-limit contracts."""

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

from tensor_grep.core.hardware.device_detect import DeviceInfo
from tensor_grep.core.hardware.device_inventory import DeviceInventory
from tensor_grep.core.result import MatchLine, SearchResult
from tests.unit.test_mcp_server_shared import (
    _StubScanner,
    _tg_search_rank_fixture,
    _tg_search_scan_limit_payload,
)


def test_tg_ast_search_accepts_ast_wrapper_backend():
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
        mock_scanner.return_value.walk.return_value = []

        out = mcp_server.tg_ast_search("def $A():", "python", ".", structured_json=False)

    # round-8 (audit #95): path="." is now confined+resolved to an absolute cwd path before
    # being echoed back, so only the message PREFIX (not the exact trailing path) is stable.
    assert out.startswith("No AST matches found for pattern in ")
    assert "Routing: backend=" in out


def test_tg_search_includes_routing_summary_in_non_empty_output():
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        total_files=1,
        total_matches=1,
    )

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "CuDFBackend"
        pipeline.selected_backend_reason = "gpu_explicit_ids_cudf"
        pipeline.selected_gpu_device_ids = [7, 3]
        pipeline.selected_gpu_chunk_plan_mb = [(7, 256), (3, 512)]
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search("ERROR", ".")

    payload = json.loads(out)
    assert payload["total_matches"] == 1
    assert payload["total_files"] == 1
    routing = payload["routing"]
    assert routing["backend"] == "CuDFBackend"
    assert routing["reason"] == "gpu_explicit_ids_cudf"
    assert routing["gpu_device_ids"] == [7, 3]
    assert routing["gpu_chunk_plan_mb"] == [[7, 256], [3, 512]]
    assert routing["distributed"] is True
    assert routing["workers"] == 2


def test_tg_search_context_rows_do_not_inflate_header_count():
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(
        matches=[
            MatchLine(line_number=1, text="before", file="a.log"),
            MatchLine(line_number=2, text="ERROR here", file="a.log"),
            MatchLine(line_number=3, text="after", file="a.log"),
        ],
        matched_file_paths=["a.log"],
        match_counts_by_file={"a.log": 1},
        total_files=1,
        total_matches=1,
        routing_backend="RipgrepBackend",
        routing_reason="rg_json",
    )

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "RipgrepBackend"
        pipeline.selected_backend_reason = "rg_json"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search("ERROR", ".", context=1)

    payload = json.loads(out)
    # total_matches counts actual matches, not context rows
    assert payload["total_matches"] == 1
    assert payload["total_files"] == 1
    # rendered_match_count includes context rows (3 total lines)
    assert payload["rendered_match_count"] == 3
    line_numbers = [m["line_number"] for m in payload["matches"]]
    assert line_numbers == [1, 2, 3]
    texts = [m["text"] for m in payload["matches"]]
    assert texts == ["before", "ERROR here", "after"]


def test_tg_search_accepts_query_alias_and_bounds_text_output():
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.cli import mcp_server

    backend = RipgrepBackend()
    backend.search = MagicMock(
        return_value=SearchResult(
            matches=[
                MatchLine(line_number=1, text="ERROR one", file="a.log"),
                MatchLine(line_number=2, text="ERROR two", file="a.log"),
                MatchLine(line_number=3, text="ERROR three", file="a.log"),
                MatchLine(line_number=1, text="ERROR four", file="b.log"),
            ],
            matched_file_paths=["a.log", "b.log"],
            total_files=2,
            total_matches=4,
        )
    )

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = backend
        pipeline.selected_backend_name = "RustCoreBackend"
        pipeline.selected_backend_reason = "native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search(
            pattern=None,
            query="ERROR",
            path=".",
            max_files=1,
            max_results=2,
        )

    backend.search.assert_called_once()
    assert backend.search.call_args.args[1] == "ERROR"
    payload = json.loads(out)
    assert payload["total_matches"] == 4
    assert payload["total_files"] == 2
    # bounded to 2 results across 1 file
    assert payload["rendered_match_count"] == 2
    assert payload["rendered_file_count"] == 1
    assert payload["truncated"] is True
    assert payload["omitted_matches"] == 2
    assert payload["omitted_files"] == 1
    rendered_files = {m["file"] for m in payload["matches"]}
    assert "a.log" in rendered_files
    assert "b.log" not in rendered_files
    rendered_texts = [m["text"] for m in payload["matches"]]
    assert "ERROR one" in rendered_texts
    assert "ERROR two" in rendered_texts
    assert "ERROR three" not in rendered_texts


def test_tg_search_can_return_bounded_structured_json():
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.cli import mcp_server

    backend = RipgrepBackend()
    backend.search = MagicMock(
        return_value=SearchResult(
            matches=[
                MatchLine(line_number=1, text="ERROR one", file="a.log"),
                MatchLine(line_number=2, text="ERROR two", file="a.log"),
                MatchLine(line_number=1, text="ERROR three", file="b.log"),
            ],
            matched_file_paths=["a.log", "b.log"],
            total_files=2,
            total_matches=3,
            routing_backend="RipgrepBackend",
            routing_reason="rg_json",
        )
    )

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = backend
        pipeline.selected_backend_name = "RipgrepBackend"
        pipeline.selected_backend_reason = "rg_json"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search(
            "ERROR",
            ".",
            max_files=1,
            max_results=2,
            structured_json=True,
        )

    payload = json.loads(out)
    assert payload["pattern"] == "ERROR"
    # round-8 (audit #95): path="." is now confined+resolved to an absolute cwd path.
    assert payload["path"] == str(Path.cwd().resolve())
    assert payload["total_matches"] == 3
    assert payload["total_files"] == 2
    assert payload["rendered_match_count"] == 2
    assert payload["rendered_file_count"] == 1
    assert payload["truncated"] is True
    assert payload["omitted_matches"] == 1
    assert payload["omitted_files"] == 1
    assert payload["matches"] == [
        {"file": "a.log", "line_number": 1, "text": "ERROR one"},
        {"file": "a.log", "line_number": 2, "text": "ERROR two"},
    ]
    assert payload["routing"]["backend"] == "RipgrepBackend"


def test_tg_search_uses_single_aggregate_ripgrep_search_for_cli_parity():
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.cli import mcp_server

    backend = RipgrepBackend()
    backend.search = MagicMock(
        return_value=SearchResult(
            matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
            matched_file_paths=["a.log"],
            match_counts_by_file={"a.log": 1},
            total_files=1,
            total_matches=1,
            routing_backend="RipgrepBackend",
            routing_reason="rg_json",
        )
    )

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = backend
        pipeline.selected_backend_name = "RipgrepBackend"
        pipeline.selected_backend_reason = "rg_json"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.side_effect = AssertionError(
            "MCP rg search must not enumerate explicit files"
        )

        out = mcp_server.tg_search("ERROR", ".")

    backend.search.assert_called_once()
    # round-8 (audit #95): path="." is now confined+resolved to an absolute cwd path before
    # being forwarded to the backend.
    assert backend.search.call_args.args[:2] == (str(Path.cwd().resolve()), "ERROR")
    payload = json.loads(out)
    assert payload["total_matches"] == 1
    assert payload["total_files"] == 1
    assert payload["routing"]["backend"] == "RipgrepBackend"
    assert payload["routing"]["reason"] == "rg_json"


def test_tg_search_should_report_runtime_routing_override_when_backend_falls_back():
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        total_files=1,
        total_matches=1,
        routing_backend="CPUBackend",
        routing_reason="torch_regex_cpu_fallback",
        routing_gpu_device_ids=[],
        routing_gpu_chunk_plan_mb=[],
        routing_distributed=False,
        routing_worker_count=1,
    )

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_explicit_ids_torch"
        pipeline.selected_gpu_device_ids = [7, 3]
        pipeline.selected_gpu_chunk_plan_mb = [(7, 256), (3, 512)]
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search("ERROR.*timeout", ".")

    payload = json.loads(out)
    routing = payload["routing"]
    assert routing["backend"] == "CPUBackend"
    assert routing["reason"] == "torch_regex_cpu_fallback"
    assert routing["gpu_device_ids"] == []
    assert routing["gpu_chunk_plan_mb"] == []
    assert routing["distributed"] is False
    assert routing["workers"] == 1


def test_tg_search_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan():
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        total_files=1,
        total_matches=1,
        routing_backend="CuDFBackend",
        routing_reason="cudf_chunked_single_worker_plan",
        routing_gpu_device_ids=[3],
        routing_gpu_chunk_plan_mb=[(3, 1)],
        routing_distributed=False,
        routing_worker_count=1,
    )

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "CuDFBackend"
        pipeline.selected_backend_reason = "gpu_explicit_ids_cudf"
        pipeline.selected_gpu_device_ids = [7, 3]
        pipeline.selected_gpu_chunk_plan_mb = [(7, 256), (3, 512)]
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search("ERROR", ".")

    payload = json.loads(out)
    routing = payload["routing"]
    assert routing["backend"] == "CuDFBackend"
    assert routing["reason"] == "cudf_chunked_single_worker_plan"
    assert routing["gpu_device_ids"] == [3]
    assert routing["gpu_chunk_plan_mb"] == [[3, 1]]
    assert routing["distributed"] is False
    assert routing["workers"] == 1


def test_tg_search_count_matches_should_respect_total_files_without_materialized_matches():
    # M10: `structured_json` defaults True everywhere else on this tool; the plain-text count
    # summary asserted here now requires explicitly opting OUT via `structured_json=False`.
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.side_effect = [
        SearchResult(matches=[], total_files=1, total_matches=3),
        SearchResult(matches=[], total_files=0, total_matches=0),
    ]

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "RustCoreBackend"
        pipeline.selected_backend_reason = "rust_count"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log", "b.log"]

        out = mcp_server.tg_search("ERROR", ".", count_matches=True, structured_json=False)

    # round-8 (audit #95): path="." is now confined+resolved to an absolute cwd path, so only
    # the message PREFIX (not the exact trailing path) is stable.
    assert out.startswith("Found a total of 3 matches across 1 files in ")
    assert "Routing: backend=RustCoreBackend reason=rust_count" in out
    assert "gpu_device_ids=[]" in out
    assert "gpu_chunk_plan_mb=[]" in out
    assert "distributed=False" in out
    assert "workers=0" in out


def test_tg_search_count_matches_defaults_to_parseable_structured_json():
    # M10 (Fable MCP-surface audit): `count_matches=True` used to ALWAYS return plain text
    # regardless of `structured_json` (default True) -- a default caller's `json.loads()`
    # would raise. It must now honor the flag like every other branch of this tool.
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.side_effect = [
        SearchResult(matches=[], total_files=1, total_matches=3),
        SearchResult(matches=[], total_files=0, total_matches=0),
    ]

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "RustCoreBackend"
        pipeline.selected_backend_reason = "rust_count"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log", "b.log"]

        out = mcp_server.tg_search("ERROR", ".", count_matches=True)

    payload = json.loads(out)  # must not raise
    assert payload["total_matches"] == 3
    assert payload["total_files"] == 1
    assert payload["routing"]["backend"] == "RustCoreBackend"
    assert payload["routing"]["reason"] == "rust_count"


def test_tg_search_should_render_count_only_file_summary_without_materialized_matches():
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.side_effect = [
        SearchResult(
            matches=[],
            matched_file_paths=["a.log"],
            match_counts_by_file={"a.log": 3},
            total_files=1,
            total_matches=3,
        ),
        SearchResult(matches=[], total_files=0, total_matches=0),
    ]

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "RipgrepBackend"
        pipeline.selected_backend_reason = "rg_count"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log", "b.log"]

        out = mcp_server.tg_search("ERROR", ".")

    payload = json.loads(out)
    assert payload["total_matches"] == 3
    assert payload["total_files"] == 1
    # count-only result: no materialized match lines rendered
    assert payload["rendered_match_count"] == 0
    assert payload["omitted_matches"] == 3
    assert payload["omitted_files"] == 1
    assert payload["routing"]["backend"] == "RipgrepBackend"
    assert payload["routing"]["reason"] == "rg_count"


def test_tg_ast_search_should_render_count_only_file_summary_without_materialized_matches():
    from tensor_grep.cli import mcp_server

    fake_backend = type("AstGrepWrapperBackend", (), {"search": MagicMock()})()
    fake_backend.search.side_effect = [
        SearchResult(
            matches=[],
            matched_file_paths=["a.py"],
            match_counts_by_file={"a.py": 2},
            total_files=1,
            total_matches=2,
        ),
        SearchResult(matches=[], total_files=0, total_matches=0),
    ]

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

        out = mcp_server.tg_ast_search("def $A():", "python", ".")

    payload = json.loads(out)
    assert payload["total_matches"] == 2
    assert payload["total_files"] == 1
    # count-only result: no materialized match lines rendered
    assert payload["rendered_match_count"] == 0
    assert payload["omitted_matches"] == 2
    assert payload["omitted_files"] == 1
    assert payload["routing"]["backend"] == "AstGrepWrapperBackend"
    assert payload["routing"]["reason"] == "ast_grep_json"


# --- H3: PR #400 walk-deadline/fallback/broad-root-refusal ported to the MCP walk loops ---


def test_tg_search_backend_execution_error_falls_back_to_cpu_and_keeps_partial_results():
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.cli import mcp_server

    fault = BackendExecutionError("native panic")
    fake_backend = MagicMock()
    fake_backend.search.side_effect = [
        SearchResult(
            matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
            matched_file_paths=["a.log"],
            total_files=1,
            total_matches=1,
        ),
        fault,
    ]
    cpu_fallback_result = SearchResult(
        matches=[MatchLine(line_number=2, text="ERROR too", file="b.log")],
        matched_file_paths=["b.log"],
        total_files=1,
        total_matches=1,
    )

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
        patch(
            "tensor_grep.cli.mcp_server._search_with_cpu_fallback",
            return_value=cpu_fallback_result,
        ) as mock_fallback,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log", "b.log"]

        out = mcp_server.tg_search("ERROR", ".")

    mock_fallback.assert_called_once()
    assert mock_fallback.call_args.args[0] == "b.log"
    assert mock_fallback.call_args.args[3] is fault
    payload = json.loads(out)
    # Both the pre-fault match AND the CPU-fallback's match survive -- a mid-walk fault
    # must never discard results already collected (the pre-fix behavior: the outer
    # `except Exception` swallowed everything).
    assert payload["total_matches"] == 2
    assert {m["file"] for m in payload["matches"]} == {"a.log", "b.log"}


def test_tg_search_walk_deadline_exceeded_preserves_partial_results_and_flags_incomplete():
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        matched_file_paths=["a.log"],
        total_files=1,
        total_matches=1,
    )

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
        patch(
            "tensor_grep.cli.mcp_server.native_walk_deadline_exceeded",
            side_effect=[False, True],
        ),
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log", "b.log", "c.log"]

        out = mcp_server.tg_search("ERROR", ".")

    # Only the first file was searched before the (mocked) deadline tripped.
    assert fake_backend.search.call_count == 1
    payload = json.loads(out)
    assert payload["total_matches"] == 1
    assert payload["result_incomplete"] is True
    assert "deadline" in payload["incomplete_reason"]
    assert payload["truncated"] is True


def test_tg_search_refuses_vendored_root_scan_with_actionable_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "dep.py").write_text("x = 1\n", encoding="utf-8")

    fake_backend = MagicMock()  # a generic non-RipgrepBackend double

    with patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline:
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []

        out = mcp_server.tg_search("ERROR", str(tmp_path))

    fake_backend.search.assert_not_called()
    payload = json.loads(out)
    assert payload["error"]["code"] == "broad_scan_refused"
    assert "vendor" in payload["error"]["message"]
    assert payload["result_incomplete"] is True
    assert payload["truncated"] is True


def test_tg_search_refuses_large_root_scan_for_non_ripgrep_backend():
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    many_files = [f"file_{i}.log" for i in range(2000)]

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = many_files

        out = mcp_server.tg_search("ERROR", ".")

    fake_backend.search.assert_not_called()
    payload = json.loads(out)
    assert payload["error"]["code"] == "broad_scan_refused"
    assert "1500" in payload["error"]["message"]


def test_tg_search_refuses_glob_with_default_path_on_large_root():
    """Bug #88 (dogfood v1.54.0): a `glob` filter narrows WHICH files match, it does not
    bound how much of the tree must be walked to find them. The MCP `path` parameter defaults
    to "." at the Python level, indistinguishable from an omitted argument, so `glob` alone
    must not exempt a `path="."` call from the large-root refusal -- otherwise a bare
    `tg_search(pattern=..., glob=...)` MCP call walks/searches an oversized root unbounded,
    the same shape as the CLI's bare `tg search --glob ... PATTERN` hang."""
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    many_files = [f"file_{i}.py" for i in range(2000)]

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = many_files

        out = mcp_server.tg_search("ERROR", ".", glob="*.py")

    fake_backend.search.assert_not_called()
    payload = json.loads(out)
    assert payload["error"]["code"] == "broad_scan_refused"
    assert "1500" in payload["error"]["message"]


def test_tg_search_rank_reranks_by_bm25(monkeypatch):
    """audit #95 Part 2: `rank` mirrors main.py's `--rank`/`--bm25` post-processing --
    inserted after _finalize_aggregate_result, before the empty/count/full-result branches
    (main.py's `elif config.rank_bm25 and all_results.matches:` ordering)."""
    from tensor_grep.cli import mcp_server

    fake_backend = _tg_search_rank_fixture()
    reranked = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        matched_file_paths=["a.log"],
        total_files=1,
        total_matches=1,
    )
    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
        patch("tensor_grep.core.reranker.rerank_by_bm25", return_value=reranked) as mock_rerank,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search("ERROR", ".", rank=True)

    mock_rerank.assert_called_once()
    assert mock_rerank.call_args.args[0].total_matches == 1
    assert mock_rerank.call_args.args[1] == "ERROR"
    assert mock_rerank.call_args.args[2] == ["a.log"]
    payload = json.loads(out)
    assert payload["total_matches"] == 1


def test_tg_search_rank_corpus_cap_sets_fallback_reason(tmp_path, monkeypatch):
    """#128d (backlog cluster-1 P0-CORRECTNESS, MED-1): the MCP `rank=True` path funnels through
    the SAME reranker.py chokepoint as the CLI --rank path (cli/main.py:7222-7225 /
    cli/mcp_server.py:4258-4263 both call rerank_by_bm25 unmodified) -- a matched set exceeding
    the total chunk cap must bound chunking AND surface rank_fallback_reason in the MCP JSON
    envelope too. Unlike test_tg_search_rank_reranks_by_bm25 above, rerank_by_bm25 is NOT mocked
    here -- it runs for real (against real tmp_path files) to prove the cap actually applies on
    this call site, not just that the function gets called."""
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_RANK_CORPUS_CHUNK_CAP", "1")
    monkeypatch.chdir(tmp_path)  # an in-root path so _confine_mcp_path's confinement check passes

    files = []
    for i in range(3):
        f = tmp_path / f"f{i}.py"
        f.write_text(f"def make_invoice_{i}(x):\n    return x\n", encoding="utf-8")
        files.append(str(f))

    def _search_result_for(current_file, *_args, **_kwargs):
        return SearchResult(
            matches=[MatchLine(line_number=1, text="def make_invoice", file=current_file)],
            matched_file_paths=[current_file],
            total_files=1,
            total_matches=1,
        )

    fake_backend = MagicMock()
    fake_backend.search.side_effect = _search_result_for

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = files

        out = mcp_server.tg_search("invoice", str(tmp_path), rank=True)

    payload = json.loads(out)
    assert payload["total_matches"] == 3, f"expected all 3 matches preserved, got: {payload}"
    assert "corpus cap" in payload.get("rank_fallback_reason", "")


def test_tg_search_semantic_applies_hybrid_rerank(monkeypatch):
    from tensor_grep.cli import mcp_server

    fake_backend = _tg_search_rank_fixture()
    reranked = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        matched_file_paths=["a.log"],
        total_files=1,
        total_matches=1,
        rank_fallback_reason=None,
    )
    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
        patch(
            "tensor_grep.cli.mcp_server._apply_semantic_rerank", return_value=reranked
        ) as mock_semantic,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search("ERROR", ".", semantic=True)

    mock_semantic.assert_called_once()
    assert mock_semantic.call_args.args[1] == "ERROR"
    payload = json.loads(out)
    assert payload["total_matches"] == 1


def test_tg_search_semantic_takes_priority_over_rank_when_both_set(monkeypatch):
    """Mirrors main.py's `if config.semantic_rank: ... elif config.rank_bm25:` ordering --
    semantic wins when both flags are requested; the BM25-only path must not also fire."""
    from tensor_grep.cli import mcp_server

    fake_backend = _tg_search_rank_fixture()
    reranked = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        matched_file_paths=["a.log"],
        total_files=1,
        total_matches=1,
    )
    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
        patch(
            "tensor_grep.cli.mcp_server._apply_semantic_rerank", return_value=reranked
        ) as mock_semantic,
        patch("tensor_grep.core.reranker.rerank_by_bm25") as mock_bm25,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log"]

        mcp_server.tg_search("ERROR", ".", rank=True, semantic=True)

    mock_semantic.assert_called_once()
    mock_bm25.assert_not_called()


def test_tg_search_semantic_backend_execution_error_returns_distinguishable_error(monkeypatch):
    """Must catch BackendExecutionError EXPLICITLY (mirrors main.py's search_command boundary)
    -- a genuine dense-backend fault (corrupt model dir) must surface as a distinguishable
    structured error, not fall through to the generic internal_error catch-all at the bottom
    of tg_search (which would lose the fail-closed signal an agent needs to tell "the backend
    itself broke" apart from "some other internal_error")."""
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.cli import mcp_server

    fake_backend = _tg_search_rank_fixture()
    fault = BackendExecutionError("corrupt dense model directory")

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
        patch("tensor_grep.cli.mcp_server._apply_semantic_rerank", side_effect=fault),
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search("ERROR", ".", semantic=True)

    payload = json.loads(out)
    assert payload["error"]["code"] == "semantic_backend_error"
    assert payload["error"]["code"] != "internal_error"
    assert "BackendExecutionError" in payload["error"]["message"]
    assert "corrupt dense model directory" not in payload["error"]["message"]


def test_tg_search_semantic_probes_fallback_reason_on_empty_matches(monkeypatch):
    """F16 parity (main.py _set_semantic_rank_fallback_reason): even a 0-match search must
    still probe dense-leg availability so rank_fallback_reason is set whenever the leg is
    unavailable, regardless of match count."""
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(matches=[], total_files=0, total_matches=0)

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
        patch("tensor_grep.cli.mcp_server._set_semantic_rank_fallback_reason") as mock_probe,
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log"]

        mcp_server.tg_search("NOPE", ".", semantic=True)

    mock_probe.assert_called_once()


def test_tg_search_rank_fallback_reason_surfaces_in_json_payload(monkeypatch):
    from tensor_grep.cli import mcp_server

    fake_backend = _tg_search_rank_fixture()
    reranked = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        matched_file_paths=["a.log"],
        total_files=1,
        total_matches=1,
        rank_fallback_reason="semantic ranking unavailable: the `semantic` extra is not installed",
    )
    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
        patch("tensor_grep.cli.mcp_server._apply_semantic_rerank", return_value=reranked),
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "TorchBackend"
        pipeline.selected_backend_reason = "gpu_native"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.walk.return_value = ["a.log"]

        out = mcp_server.tg_search("ERROR", ".", semantic=True)

    payload = json.loads(out)
    assert payload["rank_fallback_reason"] == (
        "semantic ranking unavailable: the `semantic` extra is not installed"
    )


def test_tg_search_docstring_does_not_oversell_gpu():
    """The docstring previously read 'high-speed GPU or CPU engine', overselling a paused,
    non-default, usually-dormant GPU path (architecture-contract known-weak-point #2: GPU is
    slower than CPU with no promotion-ready path; auto-GPU stays dormant when rg is
    installed). Mirror the CLI's own qualified phrasing ('with GPU acceleration when
    applicable') instead of an unqualified speed claim."""
    from tensor_grep.cli import mcp_server

    doc = mcp_server.tg_search.__doc__ or ""
    assert "high-speed GPU" not in doc
    assert "when applicable" in doc.lower()


def test_tg_ast_search_backend_execution_error_skips_file_and_keeps_partial_results():
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.cli import mcp_server

    fake_backend = type("AstGrepWrapperBackend", (), {"search": MagicMock()})()
    fake_backend.search.side_effect = [
        SearchResult(
            matches=[MatchLine(line_number=1, text="def foo(): pass", file="a.py")],
            matched_file_paths=["a.py"],
            total_files=1,
            total_matches=1,
        ),
        BackendExecutionError("ast-grep panic"),
    ]

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

        out = mcp_server.tg_ast_search("def $A():", "python", ".")

    payload = json.loads(out)
    # The first file's match survives; the faulted file is skipped (never silently
    # swapped to a regex-only CPU backend, which would misinterpret the AST pattern).
    assert payload["total_matches"] == 1
    assert payload["result_incomplete"] is True
    assert "b.py" in payload["incomplete_reason"]


def test_tg_ast_search_returns_structured_unavailable_when_pipeline_construction_raises(
    tmp_path, monkeypatch
):
    """Regression (CI 2026-07-10, PR #484): ``Pipeline(ast=True)`` construction itself raises
    ``ConfigurationError`` when the ast-grep/tree-sitter deps are absent for the pattern (e.g. a
    Linux runner without ast-grep) -- EARLIER than the backend-type check. tg_ast_search must
    fail closed with a STRUCTURED ``unavailable`` error, never let it escape as a raw FastMCP
    ToolError (Backend Fail-Closed Contract). Without the catch, a valid in-root call raised,
    which broke the confinement ratchet's positive (in-root-accepted) probe on Linux CI."""
    from tensor_grep.cli import mcp_server
    from tensor_grep.core.pipeline import ConfigurationError

    monkeypatch.chdir(tmp_path)  # an in-root path so the confinement check passes first
    with patch(
        "tensor_grep.cli.mcp_server.Pipeline",
        side_effect=ConfigurationError(
            "Explicit AST search requires AST dependencies: ast-grep wrapper backend is required"
        ),
    ):
        out = mcp_server.tg_ast_search("def $A():", "python", ".")

    payload = json.loads(out)
    assert payload["error"]["code"] == "unavailable"
    assert "not available" in payload["error"]["message"]


def test_tg_ast_search_fails_closed_for_metavariable_pattern_when_wrapper_unavailable(
    tmp_path, monkeypatch
):
    """Regression (#141 council-correction): unlike the sibling test above (which mocks
    ``Pipeline`` itself), this drives the REAL ``Pipeline`` with a genuine ast-grep metavariable
    pattern (``$NAME``) so the fail-closed refusal is proven end-to-end at the MCP entry path --
    ``Pipeline.__init__``, ``_supports_native_ast_pattern``, and
    ``_raise_explicit_ast_configuration_error`` (core/pipeline.py ~52-60, ~230-233) all run for
    real. Only the backend AVAILABILITY probes are stubbed (same technique as
    tests/unit/test_pipeline.py), with the native AstBackend left AVAILABLE to prove its presence
    never lets a metavariable pattern silently mis-route to it. Note: ``tg_ast_search``'s own
    ``Pipeline(config=config)`` construction (cli/mcp_server.py ~4629) never threads
    ``query_pattern`` into the ``SearchConfig`` it builds, so ``_supports_native_ast_pattern`` is
    unconditionally ``False`` there -- every AST pattern via this MCP tool requires the wrapper at
    this construction step, native ``AstBackend`` is structurally unreachable through it regardless
    of the caller's pattern. ``tg_ast_search`` (cli/mcp_server.py ~4630-4653) must catch the
    resulting ``ConfigurationError`` and return the structured "unavailable" JSON error, never a
    raw exception (Backend Fail-Closed Contract)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)  # an in-root path so the confinement check passes first

    class _StubProbeBackend:
        def is_available(self):
            return True

    class _AvailableAstBackend:
        def is_available(self):
            return True

    class _UnavailableAstGrepWrapperBackend:
        def is_available(self):
            return False

    monkeypatch.setattr("tensor_grep.core.pipeline.RipgrepBackend", _StubProbeBackend)
    monkeypatch.setattr("tensor_grep.core.pipeline.RustCoreBackend", _StubProbeBackend)
    monkeypatch.setattr("tensor_grep.backends.ast_backend.AstBackend", _AvailableAstBackend)
    monkeypatch.setattr(
        "tensor_grep.backends.ast_wrapper_backend.AstGrepWrapperBackend",
        _UnavailableAstGrepWrapperBackend,
    )

    out = mcp_server.tg_ast_search("$NAME", "python", ".")

    payload = json.loads(out)
    assert payload["error"]["code"] == "unavailable"
    assert "not available" in payload["error"]["message"]


def test_tg_ast_search_refuses_vendored_root_scan_with_actionable_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    (tmp_path / "third_party").mkdir()

    fake_backend = type("AstGrepWrapperBackend", (), {"search": MagicMock()})()

    with patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline:
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "AstGrepWrapperBackend"
        pipeline.selected_backend_reason = "ast_grep_json"
        pipeline.selected_gpu_device_ids = []
        pipeline.selected_gpu_chunk_plan_mb = []

        out = mcp_server.tg_ast_search("def $A():", "python", str(tmp_path))

    fake_backend.search.assert_not_called()
    payload = json.loads(out)
    assert payload["error"]["code"] == "broad_scan_refused"
    assert payload["lang"] == "python"
    assert "third_party" in payload["error"]["message"]


def test_tg_devices_returns_no_gpu_message_when_empty():
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.collect_device_inventory",
        return_value=DeviceInventory(
            platform="windows",
            has_gpu=False,
            device_count=0,
            routable_device_ids=[],
            devices=[],
        ),
    ):
        out = mcp_server.tg_devices()

    # default is json_output=True; parse the JSON and assert no-GPU fields
    payload = json.loads(out)
    assert payload["has_gpu"] is False
    assert payload["device_count"] == 0
    assert payload["devices"] == []


def test_tg_devices_can_emit_json_payload():
    import json

    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.collect_device_inventory",
        return_value=DeviceInventory(
            platform="windows",
            has_gpu=True,
            device_count=1,
            routable_device_ids=[7],
            devices=[DeviceInfo(device_id=7, vram_capacity_mb=12288)],
        ),
    ):
        out = mcp_server.tg_devices(json_output=True)

    payload = json.loads(out)
    assert payload["platform"] == "windows"
    assert payload["has_gpu"] is True
    assert payload["device_count"] == 1
    assert payload["devices"] == [{"device_id": 7, "vram_capacity_mb": 12288}]


def test_tg_devices_text_mode_returns_human_inventory_lines():
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.collect_device_inventory",
        return_value=DeviceInventory(
            platform="windows",
            has_gpu=True,
            device_count=2,
            routable_device_ids=[7, 3],
            devices=[
                DeviceInfo(device_id=7, vram_capacity_mb=12288),
                DeviceInfo(device_id=3, vram_capacity_mb=24576),
            ],
        ),
    ):
        out = mcp_server.tg_devices(json_output=False)

    assert "Detected 2 routable GPU(s):" in out
    assert "- gpu:7 vram_mb=12288" in out
    assert "- gpu:3 vram_mb=24576" in out


def test_tg_classify_logs_defaults_to_local_heuristics(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    class _ExplodingBackend:
        def __init__(self) -> None:
            raise AssertionError("MCP classify should not probe CyBERT by default")

    monkeypatch.chdir(tmp_path)  # cwd = the read-path confinement anchor (audit #81 #1)
    log_path = tmp_path / "app.log"
    log_path.write_text("INFO startup ok\nERROR database failed\n", encoding="utf-8")
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "tensor_grep.backends.cybert_backend",
        types.SimpleNamespace(CybertBackend=_ExplodingBackend),
    )

    out = mcp_server.tg_classify_logs(str(log_path))

    # default is structured_json=True; parse JSON and assert equivalent fields
    payload = json.loads(out)
    assert payload["provider"] == "heuristic"
    assert payload["provider_status"] == "local"
    anomaly_texts = [a["text"] for a in payload["anomalies"]]
    assert any("database failed" in t for t in anomaly_texts)
    anomaly_labels = [a["label"] for a in payload["anomalies"]]
    assert any("error" in lbl.lower() for lbl in anomaly_labels)


def test_tg_repo_map_defaults_to_shared_mcp_repo_scan_limit(tmp_path, monkeypatch):
    """audit #114: tg_repo_map's signature hardcoded `max_repo_files: int | None = 512`
    while every sibling MCP scan tool (tg_symbol_defs, tg_edit_plan, tg_context_pack, etc.)
    defaults to the shared `_DEFAULT_MCP_REPO_SCAN_LIMIT` (2000). The effective-limit calc
    `max_repo_files or DEFAULT_AGENT_REPO_MAP_LIMIT` only reaches 2000 when a caller
    EXPLICITLY passes `None` -- the 512 signature default was truthy, so omitting the param
    (the normal agent-call case) silently capped the scan at 512 instead of 2000. Pin the
    argument actually forwarded to build_repo_map so this cannot regress to a hardcoded
    literal that drifts from the shared constant."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("def add(x, y):\n    return x + y\n", encoding="utf-8")

    seen: dict[str, object] = {}
    real_build_repo_map = mcp_server.build_repo_map

    def _spy_build_repo_map(path, max_repo_files=None, **kwargs):
        seen["max_repo_files"] = max_repo_files
        return real_build_repo_map(path, max_repo_files=max_repo_files, **kwargs)

    monkeypatch.setattr(mcp_server, "build_repo_map", _spy_build_repo_map)

    payload = json.loads(mcp_server.tg_repo_map(str(project)))

    assert seen["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT
    assert payload["scan_limit"]["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT


def test_tg_search_scan_limit_marks_unreadable_cause_as_not_budget_remediable():
    """Task #283: #276 slice 1 widened `scan_truncated` to ALSO mean "hit an unreadable path",
    but this payload is `max_repo_files`-shaped, so a single permission-denied directory used to
    surface as `possibly_truncated` beside a budget number -- the WRONG-KNOB advice #276 exists
    to eliminate on the CLI, recreated on the MCP surface. The cause must be stated, and it must
    say that more budget cannot fix it."""
    scan_limit = _tg_search_scan_limit_payload(
        _StubScanner(truncated=True, cause="unreadable_path", unreadable_count=3)
    )

    assert scan_limit["possibly_truncated"] is True
    assert scan_limit["truncation_cause"] == "unreadable_path"
    assert scan_limit["budget_remediable"] is False
    assert scan_limit["unreadable_path_count"] == 3


def test_tg_search_scan_limit_marks_budget_cap_as_budget_remediable():
    """Control arm. A REAL budget cap must still report itself as budget-remediable -- otherwise
    the test above would pass even if the code blanket-labelled every truncation
    non-remediable, and a check that cannot distinguish the arms is not verification."""
    scan_limit = _tg_search_scan_limit_payload(
        _StubScanner(truncated=True, cause="max-scan-entries")
    )

    assert scan_limit["possibly_truncated"] is True
    assert scan_limit["truncation_cause"] == "scan_limit"
    assert scan_limit["budget_remediable"] is True
    assert "unreadable_path_count" not in scan_limit


def test_tg_search_scan_limit_fails_closed_on_an_unrecognised_truncation_cause():
    """An unknown cause must NOT default to budget-remediable. Guidance about whether a signal
    can be trusted is an allow-list, never a deny-list: a cause this code has never heard of
    must never be answered with "raise the limit"."""
    scan_limit = _tg_search_scan_limit_payload(_StubScanner(truncated=True, cause="something-new"))

    assert scan_limit["truncation_cause"] == "unknown"
    assert scan_limit["budget_remediable"] is False


def test_tg_search_scan_limit_omits_cause_fields_on_a_complete_scan():
    """Complete scans stay byte-identical: no cause, no remediability claim."""
    scan_limit = _tg_search_scan_limit_payload(_StubScanner(truncated=False, cause=None))

    assert "truncation_cause" not in scan_limit
    assert "budget_remediable" not in scan_limit
