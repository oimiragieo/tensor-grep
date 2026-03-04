from unittest.mock import MagicMock, patch

from tensor_grep.core.hardware.device_detect import DeviceInfo, Platform
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


class _FakeDeviceDetectorNoGpu:
    def list_devices(self):
        return []

    def get_platform(self):
        return Platform.WINDOWS

    def has_gpu(self):
        return False


class _FakeDeviceDetectorGpu:
    def list_devices(self):
        return [DeviceInfo(device_id=7, vram_capacity_mb=12288)]

    def get_platform(self):
        return Platform.WINDOWS

    def has_gpu(self):
        return True


def test_tg_devices_returns_no_gpu_message_when_empty():
    from tensor_grep.cli import mcp_server

    with patch("tensor_grep.cli.mcp_server.DeviceDetector", _FakeDeviceDetectorNoGpu):
        out = mcp_server.tg_devices()

    assert out == "No routable GPUs detected."


def test_tg_devices_can_emit_json_payload():
    import json

    from tensor_grep.cli import mcp_server

    with patch("tensor_grep.cli.mcp_server.DeviceDetector", _FakeDeviceDetectorGpu):
        out = mcp_server.tg_devices(json_output=True)

    payload = json.loads(out)
    assert payload["platform"] == "windows"
    assert payload["has_gpu"] is True
    assert payload["device_count"] == 1
    assert payload["devices"] == [{"device_id": 7, "vram_capacity_mb": 12288}]
