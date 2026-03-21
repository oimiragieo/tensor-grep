import json
from pathlib import Path
from subprocess import CompletedProcess
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

    assert "Routing: backend=CuDFBackend reason=cudf_chunked_single_worker_plan" in out
    assert "gpu_device_ids=[3]" in out
    assert "gpu_chunk_plan_mb=[(3, 1)]" in out
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

    assert out.startswith("Found a total of 3 matches across 1 files in ..")
    assert "Routing: backend=RustCoreBackend reason=rust_count" in out
    assert "gpu_device_ids=[]" in out
    assert "gpu_chunk_plan_mb=[]" in out
    assert "distributed=False" in out
    assert "workers=0" in out


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

    assert "Found 2 structural AST matches across 1 files:" in out
    assert "\na.py:" in out
    assert "  count=2" in out


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


def test_tg_rewrite_plan_returns_native_plan_json_shape():
    from tensor_grep.cli import mcp_server

    payload = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "pattern": "def $F($$$ARGS): return $EXPR",
        "replacement": "lambda $$$ARGS: $EXPR",
        "lang": "python",
        "total_files_scanned": 1,
        "total_edits": 1,
        "edits": [
            {
                "id": "e0000:file.py:0-27",
                "file": "C:/tmp/file.py",
                "planned_mtime_ns": 1,
                "line": 1,
                "byte_range": {"start": 0, "end": 27},
                "original_text": "def add(x, y): return x + y",
                "replacement_text": "lambda x, y: x + y",
                "metavar_env": {"F": "add", "ARGS": "x, y", "EXPR": "x + y"},
            }
        ],
    }

    with (
        patch("tensor_grep.cli.mcp_server._resolve_native_tg_binary", return_value=Path("tg.exe")),
        patch(
            "tensor_grep.cli.mcp_server.subprocess.run",
            return_value=CompletedProcess(
                args=["tg.exe"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            ),
        ) as mock_run,
    ):
        out = mcp_server.tg_rewrite_plan(
            pattern="def $F($$$ARGS): return $EXPR",
            replacement="lambda $$$ARGS: $EXPR",
            lang="python",
            path="src",
        )

    parsed = json.loads(out)
    assert parsed == payload
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--json",
        "def $F($$$ARGS): return $EXPR",
        "src",
    ]


def test_tg_rewrite_apply_supports_optional_verify_flag():
    from tensor_grep.cli import mcp_server

    payload = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "plan": {"total_edits": 1},
        "verification": {"total_edits": 1, "verified": 1, "mismatches": []},
    }

    with (
        patch("tensor_grep.cli.mcp_server._resolve_native_tg_binary", return_value=Path("tg.exe")),
        patch(
            "tensor_grep.cli.mcp_server.subprocess.run",
            return_value=CompletedProcess(
                args=["tg.exe"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            ),
        ) as mock_run,
    ):
        out = mcp_server.tg_rewrite_apply(
            pattern="def $F($$$ARGS): return $EXPR",
            replacement="lambda $$$ARGS: $EXPR",
            lang="python",
            path="src",
            verify=True,
        )

    parsed = json.loads(out)
    assert parsed == payload
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--verify",
        "--json",
        "def $F($$$ARGS): return $EXPR",
        "src",
    ]


def test_tg_rewrite_apply_supports_optional_validation_commands():
    from tensor_grep.cli import mcp_server

    payload = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "plan": {"total_edits": 1},
        "verification": {"total_edits": 1, "verified": 1, "mismatches": []},
        "validation": {
            "success": True,
            "commands": [
                {
                    "kind": "lint",
                    "command": "echo lint-ok",
                    "success": True,
                    "exit_code": 0,
                    "stdout": "lint-ok\n",
                    "stderr": "",
                },
                {
                    "kind": "test",
                    "command": "echo test-ok",
                    "success": True,
                    "exit_code": 0,
                    "stdout": "test-ok\n",
                    "stderr": "",
                },
            ],
        },
    }

    with (
        patch("tensor_grep.cli.mcp_server._resolve_native_tg_binary", return_value=Path("tg.exe")),
        patch(
            "tensor_grep.cli.mcp_server.subprocess.run",
            return_value=CompletedProcess(
                args=["tg.exe"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            ),
        ) as mock_run,
    ):
        out = mcp_server.tg_rewrite_apply(
            pattern="def $F($$$ARGS): return $EXPR",
            replacement="lambda $$$ARGS: $EXPR",
            lang="python",
            path="src",
            verify=True,
            lint_cmd="echo lint-ok",
            test_cmd="echo test-ok",
        )

    parsed = json.loads(out)
    assert parsed == payload
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--verify",
        "--lint-cmd",
        "echo lint-ok",
        "--test-cmd",
        "echo test-ok",
        "--json",
        "def $F($$$ARGS): return $EXPR",
        "src",
    ]


def test_tg_rewrite_apply_supports_optional_checkpoint_flag():
    from tensor_grep.cli import mcp_server

    payload = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "checkpoint": {
            "checkpoint_id": "ckpt-123",
            "mode": "filesystem-snapshot",
            "root": "C:/repo",
            "created_at": "1234567890",
            "file_count": 1,
        },
        "plan": {"total_edits": 1},
        "verification": None,
        "validation": None,
    }

    with (
        patch("tensor_grep.cli.mcp_server._resolve_native_tg_binary", return_value=Path("tg.exe")),
        patch(
            "tensor_grep.cli.mcp_server.subprocess.run",
            return_value=CompletedProcess(
                args=["tg.exe"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            ),
        ) as mock_run,
    ):
        out = mcp_server.tg_rewrite_apply(
            pattern="def $F($$$ARGS): return $EXPR",
            replacement="lambda $$$ARGS: $EXPR",
            lang="python",
            path="src",
            checkpoint=True,
        )

    parsed = json.loads(out)
    assert parsed == payload
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--checkpoint",
        "--json",
        "def $F($$$ARGS): return $EXPR",
        "src",
    ]


def test_tg_checkpoint_mcp_tools_wrap_checkpoint_store(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    project.mkdir()
    target = project / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")

    created = json.loads(mcp_server.tg_checkpoint_create(str(project)))
    checkpoint_id = created["checkpoint_id"]
    assert checkpoint_id.startswith("ckpt-")
    assert created["file_count"] == 1

    listing = json.loads(mcp_server.tg_checkpoint_list(str(project)))
    assert listing["version"] == 1
    assert listing["checkpoints"][0]["checkpoint_id"] == checkpoint_id

    target.write_text("value = 2\n", encoding="utf-8")
    restored = json.loads(mcp_server.tg_checkpoint_undo(checkpoint_id, str(project)))
    assert restored["checkpoint_id"] == checkpoint_id
    assert restored["restored_files"] == 1
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_tg_session_mcp_tools_wrap_session_store(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "sample.py").write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]
    assert opened["file_count"] == 1

    listing = json.loads(mcp_server.tg_session_list(str(project)))
    assert listing["version"] == 1
    assert listing["sessions"][0]["session_id"] == session_id

    shown = json.loads(mcp_server.tg_session_show(session_id, str(project)))
    assert shown["session_id"] == session_id
    assert shown["repo_map"]["files"] == [str((src_dir / "sample.py").resolve())]

    context = json.loads(mcp_server.tg_session_context(session_id, "add", str(project)))
    assert context["session_id"] == session_id
    assert context["routing_reason"] == "session-context"
    assert context["coverage"]["language_scope"] == "python-js-ts-rust"
    assert context["coverage"]["symbol_navigation"] == "python-ast+heuristic-js-ts-rust"
    assert context["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert context["files"] == [str((src_dir / "sample.py").resolve())]


def test_tg_session_refresh_updates_cached_session_payload(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    second_path = src_dir / "billing.py"
    second_path.write_text("def issue_invoice():\n    return 2\n", encoding="utf-8")

    refreshed = json.loads(mcp_server.tg_session_refresh(session_id, str(project)))
    assert refreshed["session_id"] == session_id
    assert refreshed["file_count"] == 2
    assert isinstance(refreshed["refreshed_at"], str)
    assert refreshed["refreshed_at"]

    shown = json.loads(mcp_server.tg_session_show(session_id, str(project)))
    assert str(second_path.resolve()) in shown["repo_map"]["files"]


def test_tg_session_context_reports_stale_session_until_refreshed(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    sample_path.write_text("def add(x):\n    return x + 1\n", encoding="utf-8")

    stale = json.loads(mcp_server.tg_session_context(session_id, "add", str(project)))
    assert stale["error"]["code"] == "invalid_input"
    assert "changed on disk" in stale["error"]["message"]

    refreshed = json.loads(mcp_server.tg_session_refresh(session_id, str(project)))
    assert refreshed["session_id"] == session_id

    context = json.loads(mcp_server.tg_session_context(session_id, "add", str(project)))
    assert context["session_id"] == session_id
    assert context["routing_reason"] == "session-context"


def test_tg_rewrite_diff_wraps_unified_diff_with_routing_metadata():
    from tensor_grep.cli import mcp_server

    diff_preview = "--- a/file.py\n+++ b/file.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"

    with (
        patch("tensor_grep.cli.mcp_server._resolve_native_tg_binary", return_value=Path("tg.exe")),
        patch(
            "tensor_grep.cli.mcp_server.subprocess.run",
            return_value=CompletedProcess(
                args=["tg.exe"],
                returncode=0,
                stdout=diff_preview,
                stderr="",
            ),
        ) as mock_run,
    ):
        out = mcp_server.tg_rewrite_diff(
            pattern="def $F($$$ARGS): return $EXPR",
            replacement="lambda $$$ARGS: $EXPR",
            lang="python",
            path="src",
        )

    parsed = json.loads(out)
    assert parsed["routing_backend"] == "AstBackend"
    assert parsed["routing_reason"] == "ast-native"
    assert parsed["sidecar_used"] is False
    assert parsed["diff"] == diff_preview
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--diff",
        "def $F($$$ARGS): return $EXPR",
        "src",
    ]


def test_tg_rewrite_plan_returns_structured_error_for_missing_path():
    from tensor_grep.cli import mcp_server

    out = mcp_server.tg_rewrite_plan(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path="C:/definitely-missing-for-mcp-server-tests",
    )

    parsed = json.loads(out)
    assert parsed["routing_backend"] == "AstBackend"
    assert parsed["routing_reason"] == "ast-native"
    assert parsed["error"]["code"] == "invalid_input"
    assert "Path not found" in parsed["error"]["message"]
    assert "Traceback" not in parsed["error"]["message"]


def test_tg_index_search_returns_native_index_search_json_shape():
    from tensor_grep.cli import mcp_server

    payload = {
        "version": 1,
        "routing_backend": "TrigramIndex",
        "routing_reason": "index-accelerated",
        "sidecar_used": False,
        "query": "ERROR",
        "path": "src",
        "total_matches": 1,
        "matches": [
            {
                "file": "C:/tmp/sample.log",
                "line": 2,
                "text": "ERROR database failed",
            }
        ],
    }

    with (
        patch("tensor_grep.cli.mcp_server._resolve_native_tg_binary", return_value=Path("tg.exe")),
        patch(
            "tensor_grep.cli.mcp_server.subprocess.run",
            return_value=CompletedProcess(
                args=["tg.exe"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            ),
        ) as mock_run,
    ):
        out = mcp_server.tg_index_search(pattern="ERROR", path="src")

    parsed = json.loads(out)
    assert parsed == payload
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "search",
        "--index",
        "--json",
        "ERROR",
        "src",
    ]


def test_tg_index_search_returns_structured_error_for_missing_path():
    from tensor_grep.cli import mcp_server

    out = mcp_server.tg_index_search(
        pattern="ERROR",
        path="C:/definitely-missing-for-mcp-server-tests",
    )

    parsed = json.loads(out)
    assert parsed["routing_backend"] == "TrigramIndex"
    assert parsed["routing_reason"] == "index-accelerated"
    assert parsed["error"]["code"] == "invalid_input"
    assert "Path not found" in parsed["error"]["message"]
    assert "Traceback" not in parsed["error"]["message"]


def test_tg_repo_map_returns_json_inventory(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "sample.py"
    module_path.write_text(
        "import pathlib\n\nclass Widget:\n    pass\n\ndef add(x, y):\n    return x + y\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_sample.py"
    test_path.write_text("from src.sample import add\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_repo_map(str(project)))

    assert payload["version"] == 1
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "repo-map"
    assert payload["sidecar_used"] is False
    assert payload["coverage"]["language_scope"] == "python-js-ts-rust"
    assert payload["path"] == str(project.resolve())
    assert str(module_path.resolve()) in payload["files"]
    assert str(test_path.resolve()) in payload["tests"]
    assert any(
        symbol["name"] == "Widget"
        and symbol["kind"] == "class"
        and symbol["file"] == str(module_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "add"
        and symbol["kind"] == "function"
        and symbol["file"] == str(module_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        entry["file"] == str(module_path.resolve()) and "pathlib" in entry["imports"]
        for entry in payload["imports"]
    )
    assert str(module_path.resolve()) in payload["related_paths"]


def test_tg_repo_map_includes_typescript_and_rust_inventory(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        'import { money } from "./money";\n'
        "export class PaymentService {}\n"
        "export function createInvoice(total: number) {\n"
        "  return money(total);\n"
        "}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "use crate::payments::create_invoice;\n\n"
        "pub struct Invoice {}\n\n"
        "pub fn issue_invoice() -> Invoice {\n"
        "    let _ = create_invoice();\n"
        "    Invoice {}\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_repo_map(str(project)))

    assert payload["coverage"]["language_scope"] == "python-js-ts-rust"
    assert any(
        symbol["name"] == "PaymentService"
        and symbol["kind"] == "class"
        and symbol["file"] == str(ts_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "createInvoice"
        and symbol["kind"] == "function"
        and symbol["file"] == str(ts_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "Invoice"
        and symbol["kind"] == "struct"
        and symbol["file"] == str(rust_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        symbol["name"] == "issue_invoice"
        and symbol["kind"] == "function"
        and symbol["file"] == str(rust_path.resolve())
        for symbol in payload["symbols"]
    )
    assert any(
        entry["file"] == str(ts_path.resolve()) and "./money" in entry["imports"]
        for entry in payload["imports"]
    )
    assert any(
        entry["file"] == str(rust_path.resolve())
        and "crate::payments::create_invoice" in entry["imports"]
        for entry in payload["imports"]
    )


def test_tg_context_pack_returns_ranked_inventory(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "import decimal\n\n"
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "users.py"
    other_path.write_text("def load_user(user_id):\n    return user_id\n", encoding="utf-8")
    test_path = tests_dir / "test_payments.py"
    test_path.write_text("from src.payments import create_invoice\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_context_pack("invoice payment", str(project)))

    assert payload["version"] == 1
    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "context-pack"
    assert payload["sidecar_used"] is False
    assert payload["coverage"]["symbol_navigation"] == "python-ast+heuristic-js-ts-rust"
    assert payload["query"] == "invoice payment"
    assert payload["path"] == str(project.resolve())
    assert payload["files"][0] == str(module_path.resolve())


def test_tg_symbol_defs_returns_exact_definition_matches(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_defs("create_invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-defs"
    assert payload["coverage"]["language_scope"] == "python-js-ts-rust"
    assert payload["symbol"] == "create_invoice"
    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["file"] == str(module_path.resolve())


def test_tg_symbol_defs_can_find_rust_and_typescript_symbols(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )

    ts_payload = json.loads(mcp_server.tg_symbol_defs("createInvoice", str(project)))
    rust_payload = json.loads(mcp_server.tg_symbol_defs("issue_invoice", str(project)))

    assert ts_payload["coverage"]["language_scope"] == "python-js-ts-rust"
    assert ts_payload["definitions"][0]["file"] == str(ts_path.resolve())
    assert ts_payload["definitions"][0]["kind"] == "function"
    assert rust_payload["definitions"][0]["file"] == str(rust_path.resolve())
    assert rust_payload["definitions"][0]["kind"] == "function"


def test_tg_symbol_source_returns_exact_python_function_body(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n"
        "    subtotal = total + tax\n"
        "    return subtotal\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_source("create_invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-source"
    assert payload["symbol"] == "create_invoice"
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["sources"][0]["start_line"] == 1
    assert payload["sources"][0]["end_line"] == 3
    assert "subtotal = total + tax" in payload["sources"][0]["source"]


def test_tg_symbol_source_can_extract_typescript_and_rust_blocks(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "export function createInvoice(total: number) {\n"
        "  const subtotal = total + 1;\n"
        "  return subtotal;\n"
        "}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "pub fn issue_invoice() -> usize {\n"
        "    let subtotal = 1;\n"
        "    subtotal\n"
        "}\n",
        encoding="utf-8",
    )

    ts_payload = json.loads(mcp_server.tg_symbol_source("createInvoice", str(project)))
    rust_payload = json.loads(mcp_server.tg_symbol_source("issue_invoice", str(project)))

    assert ts_payload["sources"][0]["file"] == str(ts_path.resolve())
    assert "const subtotal = total + 1;" in ts_payload["sources"][0]["source"]
    assert rust_payload["sources"][0]["file"] == str(rust_path.resolve())
    assert "let subtotal = 1;" in rust_payload["sources"][0]["source"]


def test_tg_symbol_impact_returns_related_files_and_tests(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_impact("create_invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-impact"
    assert payload["coverage"]["symbol_navigation"] == "python-ast+heuristic-js-ts-rust"
    assert payload["symbol"] == "create_invoice"
    assert payload["files"][0] == str(module_path.resolve())
    assert str(other_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())


def test_tg_symbol_impact_prefers_import_linked_typescript_and_rust_tests(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "export function createInvoice(total: number) {\n"
        "  return total;\n"
        "}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "pub fn issue_invoice() -> usize {\n"
        "    1\n"
        "}\n",
        encoding="utf-8",
    )
    ts_test_path = tests_dir / "invoice_flow.spec.ts"
    ts_test_path.write_text(
        'import { createInvoice } from "../src/payments";\n'
        "test('invoice', () => expect(createInvoice(1)).toBe(1));\n",
        encoding="utf-8",
    )
    rust_test_path = tests_dir / "integration_checks.rs"
    rust_test_path.write_text(
        "use crate::billing::issue_invoice;\n\n"
        "#[test]\n"
        "fn invoice_smoke() {\n"
        "    assert_eq!(issue_invoice(), 1);\n"
        "}\n",
        encoding="utf-8",
    )

    ts_payload = json.loads(mcp_server.tg_symbol_impact("createInvoice", str(project)))
    rust_payload = json.loads(mcp_server.tg_symbol_impact("issue_invoice", str(project)))

    assert ts_payload["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert ts_payload["tests"][0] == str(ts_test_path.resolve())
    assert rust_payload["tests"][0] == str(rust_test_path.resolve())


def test_tg_symbol_impact_prefers_import_linked_source_files_over_name_only_matches(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    notes_dir = project / "notes"
    src_dir.mkdir(parents=True)
    notes_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    importer_path = src_dir / "billing.py"
    importer_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def bill():\n"
        "    return create_invoice(1, 2)\n",
        encoding="utf-8",
    )
    noisy_path = notes_dir / "invoice_notes.py"
    noisy_path.write_text("def placeholder():\n    return 'invoice'\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_symbol_impact("create_invoice", str(project)))

    assert payload["files"][0] == str(module_path.resolve())
    assert payload["files"][1] == str(importer_path.resolve())
    assert str(noisy_path.resolve()) not in payload["files"][:2]


def test_tg_context_pack_prefers_import_linked_files_for_ranked_symbol_queries(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    notes_dir = project / "notes"
    src_dir.mkdir(parents=True)
    notes_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    importer_path = src_dir / "billing.py"
    importer_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def bill():\n"
        "    return create_invoice(1, 2)\n",
        encoding="utf-8",
    )
    noisy_path = notes_dir / "invoice_notes.py"
    noisy_path.write_text("def placeholder():\n    return 'invoice'\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_context_pack("create invoice", str(project)))

    assert payload["files"][0] == str(module_path.resolve())
    assert payload["files"][1] == str(importer_path.resolve())
    assert str(noisy_path.resolve()) not in payload["files"][:2]


def test_tg_symbol_refs_returns_python_reference_sites(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\nresult = create_invoice(10, 2)\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_refs("create_invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-refs"
    assert payload["coverage"]["symbol_navigation"] == "python-ast+heuristic-js-ts-rust"
    assert any(ref["file"] == str(other_path.resolve()) for ref in payload["references"])


def test_tg_symbol_refs_and_callers_include_typescript_and_rust_heuristics(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "export function createInvoice(total: number) {\n"
        "  return total;\n"
        "}\n\n"
        "export function renderInvoice() {\n"
        "  return createInvoice(10);\n"
        "}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "pub fn issue_invoice() -> usize {\n"
        "    1\n"
        "}\n\n"
        "pub fn settle_invoice() -> usize {\n"
        "    issue_invoice()\n"
        "}\n",
        encoding="utf-8",
    )

    ts_refs = json.loads(mcp_server.tg_symbol_refs("createInvoice", str(project)))
    ts_callers = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))
    rust_refs = json.loads(mcp_server.tg_symbol_refs("issue_invoice", str(project)))
    rust_callers = json.loads(mcp_server.tg_symbol_callers("issue_invoice", str(project)))

    assert ts_refs["coverage"]["symbol_navigation"] == "python-ast+heuristic-js-ts-rust"
    assert any(ref["file"] == str(ts_path.resolve()) for ref in ts_refs["references"])
    assert any(caller["file"] == str(ts_path.resolve()) for caller in ts_callers["callers"])
    assert any(ref["file"] == str(rust_path.resolve()) for ref in rust_refs["references"])
    assert any(caller["file"] == str(rust_path.resolve()) for caller in rust_callers["callers"])


def test_tg_symbol_callers_returns_python_call_sites(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\nassert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("create_invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-callers"
    assert payload["coverage"]["symbol_navigation"] == "python-ast+heuristic-js-ts-rust"
    assert payload["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert any(caller["file"] == str(other_path.resolve()) for caller in payload["callers"])
    assert payload["tests"][0] == str(test_path.resolve())
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(
        symbol["name"] == "create_invoice" and symbol["score"] > 0 for symbol in payload["symbols"]
    )
    assert payload["related_paths"][0] == str(module_path.resolve())
    assert str(other_path.resolve()) not in payload["related_paths"][:1]


def test_tg_symbol_callers_prefers_import_linked_typescript_tests(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "export function createInvoice(total: number) {\n"
        "  return total;\n"
        "}\n\n"
        "export function renderInvoice() {\n"
        "  return createInvoice(10);\n"
        "}\n",
        encoding="utf-8",
    )
    ts_test_path = tests_dir / "invoice_flow.spec.ts"
    ts_test_path.write_text(
        'import { createInvoice } from "../src/payments";\n'
        "test('invoice', () => expect(createInvoice(1)).toBe(1));\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    assert payload["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert any(caller["file"] == str(ts_path.resolve()) for caller in payload["callers"])
    assert payload["tests"][0] == str(ts_test_path.resolve())


def test_tg_symbol_impact_can_rank_tests_through_transitive_import_chain(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    payments_path = src_dir / "payments.ts"
    payments_path.write_text(
        "export function createInvoice(total: number) {\n"
        "  return total;\n"
        "}\n",
        encoding="utf-8",
    )
    workflow_path = src_dir / "workflow.ts"
    workflow_path.write_text(
        'import { createInvoice } from "./payments";\n\n'
        "export function runWorkflow() {\n"
        "  return createInvoice(1);\n"
        "}\n",
        encoding="utf-8",
    )
    ui_path = src_dir / "ui.ts"
    ui_path.write_text(
        'import { runWorkflow } from "./workflow";\n\n'
        "export function renderInvoice() {\n"
        "  return runWorkflow();\n"
        "}\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "ui_flow.spec.ts"
    test_path.write_text(
        'import { renderInvoice } from "../src/ui";\n'
        "test('invoice', () => expect(renderInvoice()).toBe(1));\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_impact("createInvoice", str(project)))

    assert payload["tests"][0] == str(test_path.resolve())

