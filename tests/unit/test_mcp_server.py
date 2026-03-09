from unittest.mock import MagicMock, patch

from tensor_grep.core.hardware.device_detect import DeviceInfo
from tensor_grep.core.hardware.device_inventory import DeviceInventory
from tensor_grep.core.result import MatchLine, SearchResult


def test_tg_ast_search_accepts_ast_wrapper_backend():
    from tensor_grep.cli import mcp_server

    fake_backend = type("AstGrepWrapperBackend", (), {"search": MagicMock()})()

    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        mock_pipeline.return_value.get_backend.return_value = fake_backend
        mock_scanner.return_value.walk.return_value = []

        out = mcp_server.tg_ast_search("def $A():", "python", ".")

    assert out.startswith("No AST matches found for pattern in ..")
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

    assert "Found 1 matches across 1 files:" in out
    assert "Routing: backend=CuDFBackend reason=gpu_explicit_ids_cudf" in out
    assert "gpu_device_ids=[7, 3]" in out
    assert "gpu_chunk_plan_mb=[(7, 256), (3, 512)]" in out
    assert "distributed=True" in out
    assert "workers=2" in out


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

    assert "Routing: backend=CPUBackend reason=torch_regex_cpu_fallback" in out
    assert "gpu_device_ids=[]" in out
    assert "gpu_chunk_plan_mb=[]" in out
    assert "distributed=False" in out
    assert "workers=1" in out


def test_tg_search_count_matches_should_respect_total_files_without_materialized_matches():
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

    assert out == "Found a total of 3 matches across 1 files in .."


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

    assert "Found 3 matches across 1 files:" in out
    assert "\na.log:" in out
    assert "  count=3" in out


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

    assert out == "No routable GPUs detected."


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
