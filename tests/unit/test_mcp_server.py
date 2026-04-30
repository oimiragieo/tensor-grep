import asyncio
import hashlib
import hmac
import json
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

from tensor_grep.core.hardware.device_detect import DeviceInfo
from tensor_grep.core.hardware.device_inventory import DeviceInventory
from tensor_grep.core.result import MatchLine, SearchResult


def _canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return json.dumps(canonical, indent=2).encode("utf-8")


def _write_audit_manifest(
    path: Path,
    *,
    previous_manifest_sha256: str | None = None,
    project_root: Path | None = None,
    signing_key: bytes | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-03-23T12:00:00Z",
        "lang": "python",
        "path": str(project_root or path.parent),
        "plan_total_edits": 1,
        "applied_edit_ids": ["edit-1"],
        "checkpoint": None,
        "validation": None,
        "files": [
            {
                "path": "src/sample.py",
                "edit_ids": ["edit-1"],
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
            }
        ],
        "previous_manifest_sha256": previous_manifest_sha256,
    }
    payload["manifest_sha256"] = hashlib.sha256(_canonical_manifest_bytes(payload)).hexdigest()
    if signing_key is not None:
        payload["signature"] = {
            "kind": "hmac-sha256",
            "key_path": str(path.with_suffix(".key")),
            "value": hmac.new(
                signing_key,
                _canonical_manifest_bytes(payload),
                hashlib.sha256,
            ).hexdigest(),
        }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _write_scan_results(path: Path) -> dict[str, object]:
    payload = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "builtin-ruleset-scan",
        "sidecar_used": False,
        "ruleset": "auth-safe",
        "rule_count": 1,
        "matched_rules": 1,
        "total_matches": 1,
        "findings": [
            {
                "rule_id": "python-eval",
                "language": "python",
                "severity": "high",
                "matches": 1,
                "files": ["src/sample.py"],
                "evidence": [{"file": "src/sample.py", "match_count": 1}],
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _assert_audit_manifest_envelope(payload: dict[str, object], *, routing_reason: str) -> None:
    assert payload["version"] == 1
    assert payload["routing_backend"] == "AuditManifest"
    assert payload["routing_reason"] == routing_reason
    assert payload["sidecar_used"] is False


def _assert_enriched_edit_plan_seed(
    edit_plan_seed: dict[str, object],
    *,
    primary_file: Path | None = None,
    primary_symbol_name: str | None = None,
) -> None:
    if primary_file is not None:
        assert edit_plan_seed["primary_file"] == str(primary_file.resolve())
    else:
        assert isinstance(edit_plan_seed["primary_file"], str)
    if primary_symbol_name is not None:
        assert edit_plan_seed["primary_symbol"]["name"] == primary_symbol_name
    else:
        assert isinstance(edit_plan_seed["primary_symbol"]["name"], str)
    assert {"start_line", "end_line"} <= set(edit_plan_seed["primary_span"])
    assert edit_plan_seed["primary_span"]["start_line"] >= 1
    assert (
        edit_plan_seed["primary_span"]["end_line"] >= edit_plan_seed["primary_span"]["start_line"]
    )
    assert isinstance(edit_plan_seed["related_spans"], list)
    for related_span in edit_plan_seed["related_spans"]:
        assert {"file", "symbol", "start_line", "end_line", "depth", "score", "reasons"} <= set(
            related_span
        )
        assert related_span["end_line"] >= related_span["start_line"]
    assert isinstance(edit_plan_seed["dependent_files"], list)
    assert isinstance(edit_plan_seed["edit_ordering"], list)
    if primary_file is not None:
        assert edit_plan_seed["edit_ordering"][0] == str(primary_file.resolve())
    else:
        assert all(isinstance(path, str) for path in edit_plan_seed["edit_ordering"])
    assert 0.0 <= edit_plan_seed["rollback_risk"] <= 1.0
    assert {
        "import_resolution_quality",
        "parser_backed_count",
        "heuristic_count",
    } <= set(edit_plan_seed["dependency_trust"])
    assert edit_plan_seed["dependency_trust"]["import_resolution_quality"] in {
        "strong",
        "moderate",
        "weak",
    }
    assert edit_plan_seed["dependency_trust"]["parser_backed_count"] >= 0
    assert edit_plan_seed["dependency_trust"]["heuristic_count"] >= 0
    assert isinstance(edit_plan_seed["plan_trust_summary"], str)
    assert edit_plan_seed["plan_trust_summary"]
    assert isinstance(edit_plan_seed["validation_plan"], list)
    assert edit_plan_seed["validation_plan"]
    for step in edit_plan_seed["validation_plan"]:
        assert {"command", "scope", "runner", "confidence"} <= set(step)
        assert step["scope"] in {"symbol", "file", "repo"}
        assert 0.0 <= step["confidence"] <= 1.0


def _assert_navigation_pack(
    navigation_pack: dict[str, object],
    *,
    primary_file: Path | None = None,
    primary_symbol_name: str | None = None,
) -> None:
    assert {
        "primary_target",
        "follow_up_reads",
        "parallel_read_groups",
        "related_tests",
        "validation_commands",
        "edit_ordering",
        "rollback_risk",
    } <= set(navigation_pack)
    primary_target = navigation_pack["primary_target"]
    assert {"file", "symbol", "start_line", "end_line", "mention_ref", "reasons"} <= set(
        primary_target
    )
    if primary_file is not None:
        assert primary_target["file"] == str(primary_file.resolve())
    else:
        assert isinstance(primary_target["file"], str)
    if primary_symbol_name is not None:
        assert primary_target["symbol"] == primary_symbol_name
    else:
        assert isinstance(primary_target["symbol"], str)
    assert primary_target["mention_ref"].startswith(primary_target["file"])
    assert "#L" in primary_target["mention_ref"]
    assert isinstance(navigation_pack["follow_up_reads"], list)
    assert navigation_pack["follow_up_reads"]
    for item in navigation_pack["follow_up_reads"]:
        assert {
            "file",
            "symbol",
            "start_line",
            "end_line",
            "mention_ref",
            "role",
            "rationale",
        } <= set(item)
        assert item["mention_ref"].startswith(item["file"])
        assert "#L" in item["mention_ref"]
        assert item["role"] in {"primary", "related", "test"}
    assert isinstance(navigation_pack["related_tests"], list)
    assert isinstance(navigation_pack["validation_commands"], list)
    assert navigation_pack["validation_commands"]
    assert isinstance(navigation_pack["parallel_read_groups"], list)
    assert navigation_pack["parallel_read_groups"]
    expected_phase = 0
    for group in navigation_pack["parallel_read_groups"]:
        assert {"phase", "label", "can_parallelize", "mentions", "files", "roles"} <= set(group)
        assert group["phase"] == expected_phase
        expected_phase += 1
        assert group["label"] in {"primary", "related", "test"}
        assert isinstance(group["can_parallelize"], bool)
        assert isinstance(group["mentions"], list)
        assert group["mentions"]
        assert isinstance(group["files"], list)
        assert group["files"]
        assert isinstance(group["roles"], list)
        assert group["roles"]
    assert isinstance(navigation_pack["edit_ordering"], list)
    assert 0.0 <= navigation_pack["rollback_risk"] <= 1.0


def _without_profiling(payload: dict[str, object]) -> dict[str, object]:
    cleaned = dict(payload)
    cleaned.pop("_profiling", None)
    return cleaned


def _mcp_tool_names() -> set[str]:
    from tensor_grep.cli import mcp_server

    return {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}


def _call_mcp_tool_text(name: str, arguments: dict[str, object]) -> str:
    from tensor_grep.cli import mcp_server

    content, data = asyncio.run(mcp_server.mcp.call_tool(name, arguments))
    assert data["result"] == content[0].text
    return content[0].text


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


def test_tg_edit_plan_exposes_ranking_quality_and_coverage_summary(tmp_path: Path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_edit_plan("create invoice", str(project)))

    assert payload["ranking_quality"] in {"strong", "moderate", "weak"}
    assert {"heuristic_fields", "parser_backed_fields", "graph_completeness"} <= set(
        payload["coverage_summary"]
    )
    assert {"parser_backed", "graph_derived", "heuristic"} <= set(
        payload["coverage_summary"]["evidence_counts"]
    )
    assert {"parser_backed", "graph_derived", "heuristic"} <= set(
        payload["coverage_summary"]["evidence_ratios"]
    )
    assert payload["coverage_summary"]["evidence_counts"]["parser_backed"] >= 1
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["candidate_edit_targets"]["ranking_quality"] == payload["ranking_quality"]
    assert payload["candidate_edit_targets"]["coverage_summary"] == payload["coverage_summary"]
    assert payload["edit_plan_seed"]["dependency_trust"]["import_resolution_quality"] in {
        "strong",
        "moderate",
        "weak",
    }
    assert payload["edit_plan_seed"]["plan_trust_summary"]


def test_tg_session_context_supports_auto_refresh_alias(tmp_path: Path):
    from tensor_grep.cli import mcp_server, session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    opened = session_store.open_session(str(project))
    module_path.write_text("def create_invoice():\n    return 2\n", encoding="utf-8")

    payload = json.loads(
        mcp_server.tg_session_context(
            opened.session_id,
            "invoice",
            str(project),
            auto_refresh=True,
        )
    )

    assert payload["routing_reason"] == "session-context"
    assert payload["files"] == [str(module_path.resolve())]


def test_tg_session_context_returns_uniform_error_detail(tmp_path: Path):
    from tensor_grep.cli import mcp_server

    missing_root = tmp_path / "missing"
    payload = json.loads(
        mcp_server.tg_session_context("session-missing", "invoice", str(missing_root))
    )

    assert payload["error"]["code"] == "invalid_input"
    assert "detail" in payload["error"]


def test_tg_session_lifecycle_errors_return_uniform_error_detail(tmp_path: Path):
    from tensor_grep.cli import mcp_server

    with (
        patch(
            "tensor_grep.cli.session_store.open_session", side_effect=RuntimeError("open failed")
        ),
        patch(
            "tensor_grep.cli.session_store.list_sessions", side_effect=RuntimeError("list failed")
        ),
        patch("tensor_grep.cli.session_store.get_session", side_effect=RuntimeError("show failed")),
        patch(
            "tensor_grep.cli.session_store.refresh_session",
            side_effect=RuntimeError("refresh failed"),
        ),
    ):
        opened = json.loads(mcp_server.tg_session_open(str(tmp_path)))
        listed = json.loads(mcp_server.tg_session_list(str(tmp_path)))
        shown = json.loads(mcp_server.tg_session_show("session-missing", str(tmp_path)))
        refreshed = json.loads(mcp_server.tg_session_refresh("session-missing", str(tmp_path)))

        for payload in (opened, listed, shown, refreshed):
            assert payload["error"]["code"] == "invalid_input"
            assert "detail" in payload["error"]


def test_tg_rulesets_returns_builtin_ruleset_metadata():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_rulesets())
    assert payload["routing_reason"] == "builtin-rulesets"
    rulesets = {ruleset["name"]: ruleset for ruleset in payload["rulesets"]}
    assert set(rulesets) == {
        "auth-safe",
        "crypto-safe",
        "deserialization-safe",
        "secrets-basic",
        "subprocess-safe",
        "tls-safe",
    }
    assert rulesets["auth-safe"]["category"] == "security"
    assert "python" in rulesets["auth-safe"]["languages"]


def test_tg_ruleset_scan_returns_structured_findings(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
    Path("b.py").write_text("ok\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_ruleset_scan("crypto-safe", path=".", language="python"))

    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["ruleset"] == "crypto-safe"
    assert payload["rule_count"] == 2
    assert payload["matched_rules"] == 1
    assert payload["total_matches"] == 1
    assert payload["findings"][0]["rule_id"] == "python-hashlib-md5"
    assert payload["findings"][0]["severity"] == "high"
    assert "hashlib.md5" in payload["findings"][0]["message"]
    assert (
        payload["findings"][0]["fingerprint"]
        == hashlib.sha256(
            json.dumps(
                {
                    "rule_id": "python-hashlib-md5",
                    "language": "python",
                    "files": ["a.py"],
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )
    assert payload["findings"][0]["files"] == ["a.py"]
    assert payload["findings"][0]["evidence"] == [{"file": "a.py", "match_count": 1}]


def test_tg_ruleset_scan_can_emit_evidence_snippets(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
    Path("b.py").write_text("ok\n", encoding="utf-8")

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe",
            path=".",
            language="python",
            include_evidence_snippets=True,
            max_evidence_snippets_per_file=1,
            max_evidence_snippet_chars=12,
        )
    )

    assert payload["findings"][0]["evidence"][0]["snippets"] == [
        {"text": "hashlib.md5(", "truncated": True}
    ]


def test_tg_ruleset_scan_can_compare_and_write_baseline(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
    Path("baseline.json").write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "ruleset-scan-baseline",
                "ruleset": "crypto-safe",
                "language": "python",
                "fingerprints": [
                    hashlib.sha256(
                        json.dumps(
                            {
                                "rule_id": "python-hashlib-md5",
                                "language": "python",
                                "files": ["a.py"],
                            },
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe",
            path=".",
            language="python",
            baseline_path="baseline.json",
            write_baseline="written-baseline.json",
        )
    )
    written = json.loads(Path("written-baseline.json").read_text(encoding="utf-8"))

    assert payload["findings"][0]["status"] == "existing"
    assert payload["baseline"]["existing_findings"] == 1
    assert payload["baseline_written"]["count"] == 1
    assert written["fingerprints"] == [payload["findings"][0]["fingerprint"]]


def test_tg_ruleset_scan_can_apply_suppressions(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "rule_id": "python-hashlib-md5",
                "language": "python",
                "files": ["a.py"],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    Path("suppressions.json").write_text(
        json.dumps(
            {"version": 1, "kind": "ruleset-scan-suppressions", "fingerprints": [fingerprint]},
            indent=2,
        ),
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe",
            path=".",
            language="python",
            suppressions_path="suppressions.json",
        )
    )

    assert payload["findings"][0]["status"] == "suppressed"
    assert payload["suppressions"]["suppressed_findings"] == 1


def test_tg_ruleset_scan_can_write_suppressions(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe",
            path=".",
            language="python",
            write_suppressions="written-suppressions.json",
            justification="Approved suppression for fixture coverage.",
        )
    )
    written = json.loads(Path("written-suppressions.json").read_text(encoding="utf-8"))

    assert payload["suppressions_written"]["count"] == 1
    assert written["entries"][0]["fingerprint"] == payload["findings"][0]["fingerprint"]
    assert written["entries"][0]["justification"] == "Approved suppression for fixture coverage."
    assert written["entries"][0]["created_at"].endswith("Z")


def test_tg_ruleset_scan_write_suppressions_requires_justification(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe",
            path=".",
            language="python",
            write_suppressions="written-suppressions.json",
        )
    )

    assert payload["error"]["code"] == "invalid_input"
    assert "justification" in payload["error"]["message"]


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
        patch("tensor_grep.cli.mcp_server.resolve_native_tg_binary", return_value=Path("tg.exe")),
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


def test_tg_mcp_capabilities_is_registered_and_reports_no_native_runtime(monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(mcp_server, "_embedded_rewrite_available", lambda: True, raising=False)

    assert "tg_mcp_capabilities" in _mcp_tool_names()

    payload = json.loads(_call_mcp_tool_text("tg_mcp_capabilities", {}))

    assert payload["version"] == 1
    assert payload["routing_backend"] == "MCPRuntime"
    assert payload["routing_reason"] == "mcp-capabilities"
    assert payload["sidecar_used"] is False
    assert payload["native_tg"] == {"available": False, "path": None}
    assert payload["embedded_rewrite"] == {"available": True}

    tools = {tool["name"]: tool for tool in payload["tools"]}
    assert tools["tg_mcp_capabilities"]["mode"] == "python-local"
    assert tools["tg_rewrite_plan"]["mode"] == "embedded-safe"
    assert tools["tg_rewrite_apply"]["mode"] == "embedded-safe"
    assert tools["tg_rewrite_apply"]["native_required_options"] == [
        "verify",
        "checkpoint",
        "audit_manifest",
        "audit_signing_key",
        "lint_cmd",
        "test_cmd",
    ]
    assert tools["tg_rewrite_diff"]["mode"] == "native-required"
    assert tools["tg_index_search"]["mode"] == "native-required"


def test_tg_mcp_capabilities_registry_covers_public_tools(monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: Path("tg.exe"))
    monkeypatch.setattr(mcp_server, "_embedded_rewrite_available", lambda: False, raising=False)

    payload = json.loads(mcp_server.tg_mcp_capabilities())
    capability_names = {tool["name"] for tool in payload["tools"]}

    assert capability_names == set(mcp_server._MCP_TOOL_CAPABILITIES)
    assert capability_names == _mcp_tool_names()
    assert payload["native_tg"] == {"available": True, "path": "tg.exe"}
    assert payload["embedded_rewrite"] == {"available": False}


def test_tg_mcp_capabilities_reports_bad_native_override(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    missing_binary = tmp_path / "missing-tg.exe"
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(missing_binary))
    mcp_server.resolve_native_tg_binary.cache_clear()
    try:
        payload = json.loads(mcp_server.tg_mcp_capabilities())
    finally:
        mcp_server.resolve_native_tg_binary.cache_clear()

    assert payload["native_tg"]["available"] is False
    assert payload["native_tg"]["path"] is None
    assert "Configured binary" in payload["native_tg"]["error"]


def test_tg_rewrite_plan_uses_embedded_fallback_without_native_binary(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    expected = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "total_edits": 0,
        "edits": [],
    }

    def fake_embedded_rewrite_json(**kwargs):
        assert kwargs["mode"] == "plan"
        return json.dumps(expected)

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(mcp_server, "_execute_embedded_rewrite_json", fake_embedded_rewrite_json)

    out = mcp_server.tg_rewrite_plan(
        pattern="def $F(): pass",
        replacement="def $F(): ...",
        lang="python",
        path=str(tmp_path),
    )

    assert json.loads(out) == expected


def test_tg_rewrite_plan_reports_unavailable_without_native_or_embedded(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(mcp_server, "_embedded_rewrite_available", lambda: False)

    payload = json.loads(
        mcp_server.tg_rewrite_plan(
            pattern="def $F(): pass",
            replacement="def $F(): ...",
            lang="python",
            path=str(tmp_path),
        )
    )

    assert payload["routing_backend"] == "AstBackend"
    assert payload["routing_reason"] == "native-tg-unavailable"
    assert payload["tool"] == "tg_rewrite_plan"
    assert payload["error"]["code"] == "unavailable"
    assert "TG_NATIVE_TG_BINARY" in payload["error"]["remediation"]


def test_tg_rewrite_apply_verify_returns_unavailable_without_native_binary(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(mcp_server, "_embedded_rewrite_available", lambda: True)

    payload = json.loads(
        mcp_server.tg_rewrite_apply(
            pattern="def $F(): pass",
            replacement="def $F(): ...",
            lang="python",
            path=str(tmp_path),
            verify=True,
        )
    )

    assert payload["routing_backend"] == "AstBackend"
    assert payload["routing_reason"] == "native-tg-unavailable"
    assert payload["tool"] == "tg_rewrite_apply"
    assert payload["error"]["code"] == "unavailable"
    assert "verify" in payload["error"]["message"]
    assert "TG_NATIVE_TG_BINARY" in payload["error"]["remediation"]


def test_tg_rewrite_diff_returns_unavailable_without_native_binary(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)

    payload = json.loads(
        mcp_server.tg_rewrite_diff(
            pattern="def $F(): pass",
            replacement="def $F(): ...",
            lang="python",
            path=str(tmp_path),
        )
    )

    assert payload["routing_backend"] == "AstBackend"
    assert payload["routing_reason"] == "native-tg-unavailable"
    assert payload["tool"] == "tg_rewrite_diff"
    assert payload["error"]["code"] == "unavailable"
    assert "standalone native tg binary" in payload["error"]["message"]
    assert "TG_NATIVE_TG_BINARY" in payload["error"]["remediation"]


def test_tg_rewrite_diff_returns_unavailable_for_bad_native_override(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(tmp_path / "missing-tg.exe"))
    mcp_server.resolve_native_tg_binary.cache_clear()
    try:
        payload = json.loads(
            mcp_server.tg_rewrite_diff(
                pattern="def $F(): pass",
                replacement="def $F(): ...",
                lang="python",
                path=str(tmp_path),
            )
        )
    finally:
        mcp_server.resolve_native_tg_binary.cache_clear()

    assert payload["routing_reason"] == "native-tg-unavailable"
    assert payload["tool"] == "tg_rewrite_diff"
    assert payload["error"]["code"] == "unavailable"


def test_tg_index_search_returns_unavailable_without_native_binary(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)

    payload = json.loads(mcp_server.tg_index_search(pattern="ERROR", path=str(tmp_path)))

    assert payload["routing_backend"] == "TrigramIndex"
    assert payload["routing_reason"] == "native-tg-unavailable"
    assert payload["tool"] == "tg_index_search"
    assert payload["query"] == "ERROR"
    assert payload["path"] == str(tmp_path)
    assert payload["error"]["code"] == "unavailable"
    assert "standalone native tg binary" in payload["error"]["message"]
    assert "TG_NATIVE_TG_BINARY" in payload["error"]["remediation"]


def test_execute_rewrite_apply_json_should_use_embedded_rust_when_native_binary_missing(
    monkeypatch, tmp_path: Path
):
    from tensor_grep.cli import mcp_server

    source = tmp_path / "sample.py"
    source.write_text("def add(x, y): return x + y\n", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)

    payload_json, exit_code = mcp_server.execute_rewrite_apply_json(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path=str(source),
    )

    payload = json.loads(payload_json)
    assert exit_code == 0
    assert payload["plan"]["total_edits"] == 1
    assert payload["plan"]["edits"][0]["replacement_text"] == "lambda x, y: x + y"
    assert source.read_text(encoding="utf-8") == "lambda x, y: x + y\n"


def test_execute_rewrite_plan_json_should_restore_windows_variadic_metavar_escaping(
    monkeypatch, tmp_path: Path
):
    from tensor_grep.cli import mcp_server

    source = tmp_path / "sample.py"
    source.write_text("def add(x, y): return x + y\n", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)

    payload_json, exit_code = mcp_server.execute_rewrite_plan_json(
        pattern="def $F($$ARGS): return $EXPR",
        replacement="lambda $$ARGS: $EXPR",
        lang="python",
        path=str(source),
    )

    payload = json.loads(payload_json)
    assert exit_code == 0
    assert payload["total_edits"] == 1
    assert payload["edits"][0]["replacement_text"] == "lambda x, y: x + y"


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
        patch("tensor_grep.cli.mcp_server.resolve_native_tg_binary", return_value=Path("tg.exe")),
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
        patch("tensor_grep.cli.mcp_server.resolve_native_tg_binary", return_value=Path("tg.exe")),
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


def test_tg_rewrite_apply_supports_optional_policy_parameter(tmp_path):
    from tensor_grep.cli import mcp_server

    policy_path = tmp_path / "apply-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "lint_cmd": None,
                "test_cmd": None,
                "ruleset_scan": None,
                "on_failure": "warn",
            }
        ),
        encoding="utf-8",
    )

    payload = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "plan": {"total_edits": 1},
        "verification": {"total_edits": 1, "verified": 1, "mismatches": []},
        "policy_result": {
            "policy_path": str(policy_path.resolve()),
            "checks": [],
            "all_passed": True,
            "action_taken": "none",
        },
    }

    with patch(
        "tensor_grep.cli.mcp_server.execute_rewrite_apply_json",
        return_value=(json.dumps(payload), 0),
    ) as mock_execute:
        out = mcp_server.tg_rewrite_apply(
            pattern="def $F($$$ARGS): return $EXPR",
            replacement="lambda $$$ARGS: $EXPR",
            lang="python",
            path="src",
            policy=str(policy_path),
        )

    parsed = json.loads(out)
    assert parsed == payload
    assert mock_execute.call_args.kwargs["policy"] == str(policy_path)


def test_tg_rewrite_apply_returns_structured_invalid_policy_error(tmp_path):
    from tensor_grep.cli import mcp_server

    policy_path = tmp_path / "apply-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "lint_cmd": None,
                "test_cmd": None,
                "ruleset_scan": None,
            }
        ),
        encoding="utf-8",
    )

    out = mcp_server.tg_rewrite_apply(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path=str(tmp_path),
        policy=str(policy_path),
    )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_policy"
    assert parsed["error"]["details"]
    assert any(detail["field"] == "on_failure" for detail in parsed["error"]["details"])


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
        patch("tensor_grep.cli.mcp_server.resolve_native_tg_binary", return_value=Path("tg.exe")),
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


def test_tg_rewrite_apply_supports_optional_audit_manifest_flag():
    from tensor_grep.cli import mcp_server

    payload = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "audit_manifest": {
            "path": "C:/repo/rewrite-audit.json",
            "file_count": 1,
            "applied_edit_count": 1,
            "signed": False,
            "signature_kind": None,
        },
        "plan": {"total_edits": 1},
        "verification": None,
        "validation": None,
    }

    with (
        patch("tensor_grep.cli.mcp_server.resolve_native_tg_binary", return_value=Path("tg.exe")),
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
            audit_manifest="C:/repo/rewrite-audit.json",
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
        "--audit-manifest",
        "C:/repo/rewrite-audit.json",
        "--json",
        "def $F($$$ARGS): return $EXPR",
        "src",
    ]


def test_tg_rewrite_apply_records_generated_audit_manifest_in_history_index(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    manifest_path = audit_dir / "rewrite-audit.json"
    manifest_payload = _write_audit_manifest(manifest_path, project_root=project)
    payload = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "audit_manifest": {
            "path": str(manifest_path),
            "file_count": 1,
            "applied_edit_count": 1,
            "signed": False,
            "signature_kind": None,
        },
        "plan": {"total_edits": 1},
        "verification": None,
        "validation": None,
    }

    with (
        patch("tensor_grep.cli.mcp_server.resolve_native_tg_binary", return_value=Path("tg.exe")),
        patch(
            "tensor_grep.cli.mcp_server.subprocess.run",
            return_value=CompletedProcess(
                args=["tg.exe"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            ),
        ),
    ):
        out = mcp_server.tg_rewrite_apply(
            pattern="def $F($$$ARGS): return $EXPR",
            replacement="lambda $$$ARGS: $EXPR",
            lang="python",
            path=str(project),
            audit_manifest=str(manifest_path),
        )

    assert json.loads(out) == payload
    index_payload = json.loads((audit_dir / "index.json").read_text(encoding="utf-8"))
    assert index_payload["version"] == 1
    assert index_payload["manifests"] == [
        {
            "manifest_sha256": manifest_payload["manifest_sha256"],
            "kind": "rewrite-audit-manifest",
            "created_at": "2026-03-23T12:00:00Z",
            "file_path": str(manifest_path.resolve()),
            "previous_manifest_sha256": None,
        }
    ]


def test_tg_rewrite_apply_supports_optional_audit_signing_key_flag():
    from tensor_grep.cli import mcp_server

    payload = {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "audit_manifest": {
            "path": "C:/repo/rewrite-audit.json",
            "file_count": 1,
            "applied_edit_count": 1,
            "signed": True,
            "signature_kind": "hmac-sha256",
        },
        "plan": {"total_edits": 1},
        "verification": None,
        "validation": None,
    }

    with (
        patch("tensor_grep.cli.mcp_server.resolve_native_tg_binary", return_value=Path("tg.exe")),
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
            audit_manifest="C:/repo/rewrite-audit.json",
            audit_signing_key="C:/repo/audit.key",
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
        "--audit-manifest",
        "C:/repo/rewrite-audit.json",
        "--audit-signing-key",
        "C:/repo/audit.key",
        "--json",
        "def $F($$$ARGS): return $EXPR",
        "src",
    ]


def test_tg_audit_manifest_verify_supports_signed_manifests(tmp_path):
    from tensor_grep.cli import mcp_server

    manifest_path = tmp_path / "rewrite-audit.json"
    signing_key_path = tmp_path / "audit.key"
    signing_key = b"top-secret"
    signing_key_path.write_bytes(signing_key)
    payload = _write_audit_manifest(manifest_path, signing_key=signing_key)

    out = mcp_server.tg_audit_manifest_verify(
        str(manifest_path),
        signing_key=str(signing_key_path),
    )

    parsed = json.loads(out)
    assert parsed["routing_reason"] == "audit-manifest-verify"
    assert parsed["manifest_sha256"] == payload["manifest_sha256"]
    assert parsed["checks"] == {
        "digest_valid": True,
        "chain_valid": True,
        "signature_valid": True,
    }
    assert parsed["valid"] is True
    assert parsed["errors"] == []


def test_tg_audit_history_matches_cli_json_schema(tmp_path):
    from tensor_grep.cli import audit_manifest, mcp_server

    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    first_payload = _write_audit_manifest(audit_dir / "first.json")
    _write_audit_manifest(
        audit_dir / "second.json",
        previous_manifest_sha256=str(first_payload["manifest_sha256"]),
    )

    payload = json.loads(mcp_server.tg_audit_history(str(project)))

    _assert_audit_manifest_envelope(payload, routing_reason="audit-manifest-history")
    assert payload["history"] == audit_manifest.list_audit_history(project)


def test_tg_audit_history_returns_empty_array_for_empty_directory(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    (project / ".tensor-grep" / "audit").mkdir(parents=True)

    payload = json.loads(mcp_server.tg_audit_history(str(project)))

    _assert_audit_manifest_envelope(payload, routing_reason="audit-manifest-history")
    assert payload["history"] == []


def test_tg_audit_diff_matches_cli_json_schema(tmp_path):
    from tensor_grep.cli import audit_manifest, mcp_server

    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path)
    right_payload = _write_audit_manifest(right_path)
    right_payload["kind"] = "rewrite-plan-manifest"
    right_payload["reviewer"] = "alice"
    right_payload["files"][0]["after_sha256"] = "c" * 64
    right_payload["manifest_sha256"] = hashlib.sha256(
        _canonical_manifest_bytes(right_payload)
    ).hexdigest()
    right_path.write_text(json.dumps(right_payload, indent=2), encoding="utf-8")

    payload = json.loads(mcp_server.tg_audit_diff(str(left_path), str(right_path)))

    _assert_audit_manifest_envelope(payload, routing_reason="audit-manifest-diff")
    assert payload["added"] == {"reviewer": "alice"}
    assert payload["removed"] == {}
    assert (
        payload["changed"] == audit_manifest.diff_audit_manifests(left_path, right_path)["changed"]
    )


def test_tg_audit_diff_reports_not_found(tmp_path):
    from tensor_grep.cli import mcp_server

    missing_left = tmp_path / "missing-left.json"
    missing_right = tmp_path / "missing-right.json"

    out = mcp_server.tg_audit_diff(str(missing_left), str(missing_right))

    parsed = json.loads(out)
    assert parsed["routing_reason"] == "audit-manifest-diff"
    assert parsed["error"]["code"] == "not_found"


def test_tg_audit_diff_reports_invalid_json(tmp_path):
    from tensor_grep.cli import mcp_server

    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path)
    right_path.write_text("{not valid json", encoding="utf-8")

    out = mcp_server.tg_audit_diff(str(left_path), str(right_path))

    parsed = json.loads(out)
    assert parsed["routing_reason"] == "audit-manifest-diff"
    assert parsed["error"]["code"] == "invalid_json"


def test_tg_audit_manifest_verify_reports_invalid_input_for_empty_path():
    from tensor_grep.cli import mcp_server

    out = mcp_server.tg_audit_manifest_verify("")

    parsed = json.loads(out)
    assert parsed["routing_reason"] == "audit-manifest-verify"
    assert parsed["error"]["code"] == "invalid_input"


def test_tg_audit_manifest_verify_reports_chain_failure(tmp_path):
    from tensor_grep.cli import mcp_server

    previous_manifest_path = tmp_path / "previous-audit.json"
    _write_audit_manifest(previous_manifest_path)
    manifest_path = tmp_path / "rewrite-audit.json"
    wrong_previous = "f" * 64
    _write_audit_manifest(manifest_path, previous_manifest_sha256=wrong_previous)

    out = mcp_server.tg_audit_manifest_verify(
        str(manifest_path),
        previous_manifest=str(previous_manifest_path),
    )

    parsed = json.loads(out)
    assert parsed["checks"]["digest_valid"] is True
    assert parsed["checks"]["chain_valid"] is False
    assert parsed["checks"]["signature_valid"] is True
    assert parsed["valid"] is False
    assert "Previous manifest digest does not match previous_manifest_sha256." in parsed["errors"]


def test_tg_review_bundle_create_matches_bundle_schema(tmp_path):
    from tensor_grep.cli import mcp_server
    from tensor_grep.cli.checkpoint_store import create_checkpoint

    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "src" / "sample.py").write_text("print('hello')\n", encoding="utf-8")

    previous_path = audit_dir / "previous.json"
    previous_payload = _write_audit_manifest(previous_path, project_root=project)
    current_path = audit_dir / "current.json"
    _write_audit_manifest(
        current_path,
        previous_manifest_sha256=str(previous_payload["manifest_sha256"]),
        project_root=project,
    )
    scan_path = project / "scan.json"
    scan_payload = _write_scan_results(scan_path)
    checkpoint = create_checkpoint(str(project))

    out = mcp_server.tg_review_bundle_create(
        manifest_path=str(current_path),
        scan_path=str(scan_path),
        checkpoint_id=checkpoint.checkpoint_id,
        previous_manifest=str(previous_path),
    )

    parsed = json.loads(out)
    assert parsed["routing_reason"] == "review-bundle-create"
    assert parsed["scan_results"] == scan_payload
    assert parsed["checkpoint_metadata"]["checkpoint_id"] == checkpoint.checkpoint_id
    assert parsed["diff"]["changed"]["previous_manifest_sha256"] == {
        "old": None,
        "new": previous_payload["manifest_sha256"],
    }


def test_tg_review_bundle_verify_reports_invalid_integrity(tmp_path):
    from tensor_grep.cli import audit_manifest as audit_manifest_module
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "src" / "sample.py").write_text("print('hello')\n", encoding="utf-8")
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest_module.create_review_bundle(manifest_path, output_path=bundle_path)

    tampered = json.loads(bundle_path.read_text(encoding="utf-8"))
    tampered["bundle_sha256"] = "0" * 64
    bundle_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")

    out = mcp_server.tg_review_bundle_verify(str(bundle_path))

    parsed = json.loads(out)
    assert parsed["routing_reason"] == "review-bundle-verify"
    assert parsed["checks"]["audit_manifest"]["valid"] is True
    assert parsed["bundle_integrity"]["valid"] is False
    assert parsed["valid"] is False


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
    assert context["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
    assert context["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert context["files"] == [str((src_dir / "sample.py").resolve())]


def test_tg_session_context_render_uses_cached_repo_map(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text(
        "def add(x):\n    return x + 1\n",
        encoding="utf-8",
    )

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    rendered = json.loads(mcp_server.tg_session_context_render(session_id, "add", str(project)))

    assert rendered["session_id"] == session_id
    assert rendered["routing_reason"] == "session-context-render"
    assert rendered["sources"][0]["name"] == "add"
    assert rendered["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert rendered["candidate_edit_targets"]["ranking_quality"] == rendered["ranking_quality"]
    assert rendered["candidate_edit_targets"]["coverage_summary"] == rendered["coverage_summary"]
    assert rendered["edit_plan_seed"]["validation_commands"] == ["uv run pytest -q"]
    _assert_enriched_edit_plan_seed(
        rendered["edit_plan_seed"],
        primary_file=sample_path,
        primary_symbol_name="add",
    )
    assert 0.0 <= rendered["edit_plan_seed"]["confidence"]["symbol"] <= 1.0
    assert "rendered_context" in rendered


def test_tg_session_context_render_profile_includes_profiling_without_changing_output(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text(
        "def add(x):\n    return x + 1\n",
        encoding="utf-8",
    )

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    baseline = json.loads(mcp_server.tg_session_context_render(session_id, "add", str(project)))
    profiled = json.loads(
        mcp_server.tg_session_context_render(
            session_id,
            "add",
            str(project),
            profile=True,
        )
    )

    assert "_profiling" not in baseline
    assert profiled["_profiling"]["phases"]
    assert _without_profiling(profiled) == baseline


def test_tg_session_blast_radius_uses_cached_repo_map(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    payload = json.loads(
        mcp_server.tg_session_blast_radius(
            session_id,
            "create_invoice",
            str(project),
            max_depth=1,
        )
    )

    assert payload["session_id"] == session_id
    assert payload["routing_reason"] == "session-blast-radius"
    assert payload["max_depth"] == 1
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert any(caller["file"] == str(service_path.resolve()) for caller in payload["callers"])
    assert payload["tests"][0] == str(test_path.resolve())
    assert "Depth 0:" in payload["rendered_caller_tree"]


def test_tg_symbol_blast_radius_render_returns_prompt_ready_radius_bundle(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius_render(
            "create_invoice",
            str(project),
            max_depth=1,
            max_render_chars=400,
        )
    )

    assert payload["routing_reason"] == "symbol-blast-radius-render"
    assert payload["symbol"] == "create_invoice"
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["sources"][0]["name"] == "create_invoice"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert "create_invoice" in payload["rendered_context"]


def test_tg_symbol_blast_radius_render_profile_includes_profiling_without_changing_output(
    tmp_path,
):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (src_dir / "payments.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )
    (src_dir / "service.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_service.py").write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    baseline = json.loads(
        mcp_server.tg_symbol_blast_radius_render(
            "create_invoice",
            str(project),
            max_depth=1,
            max_render_chars=400,
        )
    )
    profiled = json.loads(
        mcp_server.tg_symbol_blast_radius_render(
            "create_invoice",
            str(project),
            max_depth=1,
            max_render_chars=400,
            profile=True,
        )
    )

    assert "_profiling" not in baseline
    assert profiled["_profiling"]["phases"]
    assert _without_profiling(profiled) == baseline


def test_tg_symbol_blast_radius_plan_returns_machine_readable_bundle(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius_plan("create_invoice", str(project), max_depth=1)
    )

    assert payload["routing_reason"] == "symbol-blast-radius-plan"
    assert "rendered_context" not in payload
    assert "sources" not in payload
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["file"] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["symbol"] == "create_invoice"
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )


def test_tg_session_blast_radius_render_uses_cached_repo_map(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    payload = json.loads(
        mcp_server.tg_session_blast_radius_render(
            session_id,
            "create_invoice",
            str(project),
            max_depth=1,
            max_render_chars=400,
        )
    )

    assert payload["session_id"] == session_id
    assert payload["routing_reason"] == "session-blast-radius-render"
    assert payload["symbol"] == "create_invoice"
    assert payload["sources"][0]["name"] == "create_invoice"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert "create_invoice" in payload["rendered_context"]


def test_session_serve_render_commands_include_enriched_edit_plan_seed(tmp_path):
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_service.py").write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    session_id = session_store.open_session(str(project)).session_id
    stdin = StringIO(
        "\n".join(
            [
                json.dumps({"command": "context_render", "query": "create invoice"}),
                json.dumps(
                    {
                        "command": "blast_radius_render",
                        "symbol": "create_invoice",
                        "max_depth": 1,
                    }
                ),
            ]
        )
        + "\n"
    )
    stdout = StringIO()

    served = session_store.serve_session_stream(
        session_id,
        str(project),
        input_stream=stdin,
        output_stream=stdout,
    )

    assert served == 2
    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert responses[0]["routing_reason"] == "session-context-render"
    _assert_enriched_edit_plan_seed(responses[0]["edit_plan_seed"])
    assert responses[1]["routing_reason"] == "session-blast-radius-render"
    _assert_enriched_edit_plan_seed(
        responses[1]["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert str(service_path.resolve()) in responses[1]["edit_plan_seed"]["dependent_files"]


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


def test_tg_session_context_can_auto_refresh_stale_session(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    sample_path.write_text(
        "def add(x):\n    return x\n\ndef settle_invoice():\n    return add(1)\n",
        encoding="utf-8",
    )

    context = json.loads(
        mcp_server.tg_session_context(
            session_id,
            "settle invoice",
            str(project),
            refresh_on_stale=True,
        )
    )
    assert context["session_id"] == session_id
    assert context["routing_reason"] == "session-context"
    assert any(symbol["name"] == "settle_invoice" for symbol in context["symbols"])


def test_tg_rewrite_diff_wraps_unified_diff_with_routing_metadata():
    from tensor_grep.cli import mcp_server

    diff_preview = "--- a/file.py\n+++ b/file.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"

    with (
        patch("tensor_grep.cli.mcp_server.resolve_native_tg_binary", return_value=Path("tg.exe")),
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
        patch("tensor_grep.cli.mcp_server.resolve_native_tg_binary", return_value=Path("tg.exe")),
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
    assert payload["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
    assert payload["query"] == "invoice payment"
    assert payload["path"] == str(project.resolve())
    assert payload["files"][0] == str(module_path.resolve())


def test_tg_context_render_returns_prompt_ready_context(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_context_render("create invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "context-render"
    assert payload["files"][0] == str(module_path.resolve())
    assert payload["sources"][0]["name"] == "create_invoice"
    assert any(section["kind"] == "tests" for section in payload["sections"])
    assert any(section["kind"] == "source" for section in payload["sections"])
    summary_section = next(
        section for section in payload["sections"] if section["kind"] == "summary"
    )
    source_section = next(section for section in payload["sections"] if section["kind"] == "source")
    assert summary_section["provenance"]["path"] == str(module_path.resolve())
    assert "symbol" in summary_section["provenance"]["reasons"]
    assert source_section["provenance"]["symbol"] == "create_invoice"
    assert source_section["provenance"]["symbol_score"] >= 1
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["candidate_edit_targets"]["files"][0] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["symbols"][0]["name"] == "create_invoice"
    assert payload["candidate_edit_targets"]["ranking_quality"] == payload["ranking_quality"]
    assert payload["candidate_edit_targets"]["coverage_summary"] == payload["coverage_summary"]
    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["primary_symbol"]["name"] == "create_invoice"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    assert payload["edit_plan_seed"]["validation_tests"] == [str(test_path.resolve())]
    assert payload["edit_plan_seed"]["validation_commands"] == [
        "uv run pytest tests/test_payments.py -k test_create_invoice -q",
        "uv run pytest tests/test_payments.py -q",
        "uv run pytest -q",
    ]
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert payload["edit_plan_seed"]["confidence"]["file"] >= 0.5
    assert payload["edit_plan_seed"]["confidence"]["symbol"] >= 0.5
    assert payload["edit_plan_seed"]["confidence"]["test"] >= 0.5
    assert str(module_path.resolve()) in payload["rendered_context"]
    assert "create_invoice" in payload["rendered_context"]


def test_tg_context_render_profile_includes_profiling_without_changing_output(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (src_dir / "payments.py").write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    baseline = json.loads(mcp_server.tg_context_render("create invoice", str(project)))
    profiled = json.loads(
        mcp_server.tg_context_render(
            "create invoice",
            str(project),
            profile=True,
        )
    )

    assert "_profiling" not in baseline
    assert profiled["_profiling"]["phases"]
    assert _without_profiling(profiled) == baseline


def test_tg_context_render_includes_exact_caller_update_lines(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n"
        "\n"
        "def build_receipt(total):\n"
        "    first = create_invoice(total)\n"
        "    return create_invoice(first)\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_context_render("create invoice", str(project)))

    caller_updates = [
        dict(current)
        for current in payload["edit_plan_seed"]["suggested_edits"]
        if current["file"] == str(service_path.resolve())
        and current["edit_kind"] == "caller-update"
    ]
    assert [
        (entry["symbol"], entry["start_line"], entry["end_line"]) for entry in caller_updates
    ] == [
        ("build_receipt", 4, 4),
        ("build_receipt", 5, 5),
    ]
    for entry in caller_updates:
        assert entry["provenance"] == "python-ast"
        assert 0.0 < entry["confidence"] <= 1.0
        assert f"calls create_invoice() on line {entry['start_line']}" in entry["rationale"]


def test_tg_edit_plan_returns_machine_readable_plan_bundle(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_edit_plan("create invoice", str(project)))

    assert payload["routing_reason"] == "context-edit-plan"
    assert "rendered_context" not in payload
    assert "sources" not in payload
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["candidate_edit_targets"]["files"][0] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["file"] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["symbol"] == "create_invoice"
    assert payload["candidate_edit_targets"]["ranking_quality"] == payload["ranking_quality"]
    assert payload["candidate_edit_targets"]["coverage_summary"] == payload["coverage_summary"]
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    _assert_navigation_pack(
        payload["navigation_pack"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )


def test_tg_edit_plan_prefers_targeted_vitest_validation_commands(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (project / "package.json").write_text(
        json.dumps(
            {
                "name": "vitest-project",
                "devDependencies": {"vitest": "^1.0.0"},
            }
        ),
        encoding="utf-8",
    )
    module_path = src_dir / "payments.ts"
    module_path.write_text(
        "export function createInvoice(total: number, tax: number): number {\n"
        "  return total + tax;\n"
        "}\n",
        encoding="utf-8",
    )
    (tests_dir / "payments.test.ts").write_text(
        'import { describe, expect, test } from "vitest";\n'
        'import { createInvoice } from "../src/payments";\n\n'
        'describe("payments", () => {\n'
        '  test("createInvoice adds tax", () => {\n'
        "    expect(createInvoice(1, 2)).toBe(3);\n"
        "  });\n"
        "});\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_edit_plan("create invoice", str(project)))

    assert payload["candidate_edit_targets"]["spans"][0]["file"] == str(module_path.resolve())
    assert payload["edit_plan_seed"]["validation_plan"][0]["runner"] == "vitest"
    assert payload["edit_plan_seed"]["validation_plan"][0]["scope"] == "symbol"
    assert payload["edit_plan_seed"]["validation_plan"][0]["command"] == (
        'npx vitest run tests/payments.test.ts -t "createInvoice adds tax"'
    )
    assert payload["edit_plan_seed"]["validation_commands"][0] == (
        'npx vitest run tests/payments.test.ts -t "createInvoice adds tax"'
    )


def test_tg_context_render_honors_max_render_chars(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n"
        "    subtotal = total + tax\n"
        "    fee = subtotal + 5\n"
        "    grand_total = fee + 10\n"
        "    return grand_total\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_context_render("create invoice", str(project), max_render_chars=120)
    )

    assert payload["truncated"] is True
    assert payload["max_render_chars"] == 120
    assert len(payload["rendered_context"]) <= 120
    assert payload["sources"][0]["name"] == "create_invoice"


def test_tg_context_render_accepts_max_tokens_and_model(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n"
        "    subtotal = total + tax\n"
        "    fee = subtotal + 5\n"
        "    grand_total = fee + 10\n"
        "    return grand_total\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_context_render(
            "create invoice",
            str(project),
            max_files=1,
            max_sources=1,
            max_tokens=40,
            model="gpt-test",
        )
    )

    assert payload["files"][0] == str(module_path.resolve())
    assert payload["max_tokens"] == 40
    assert payload["model"] == "gpt-test"
    assert isinstance(payload["token_estimate"], int)
    assert all(isinstance(section["token_estimate"], int) for section in payload["sections"])
    assert payload["token_estimate"] <= 40 + max(
        (section["token_estimate"] for section in payload["sections"]),
        default=0,
    )


def test_tg_context_render_can_optimize_source_blocks_for_llm_use(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "# module comment\n"
        "\n"
        "def create_invoice(total, tax):\n"
        "    # subtotal comment\n"
        "    subtotal = total + tax\n"
        "\n"
        "    return subtotal\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_context_render(
            "create invoice",
            str(project),
            optimize_context=True,
            render_profile="llm",
        )
    )

    assert payload["optimize_context"] is True
    assert payload["render_profile"] == "llm"
    source = next(item for item in payload["sources"] if item["name"] == "create_invoice")
    assert "# subtotal comment" not in source["rendered_source"]
    assert "\n\n" not in source["rendered_source"]
    assert source["line_map"][0]["original_start_line"] == 3
    assert source["line_map"][0]["rendered_start_line"] == 1
    assert source["render_diagnostics"]["removed_comment_lines"] >= 1
    assert source["render_diagnostics"]["removed_blank_lines"] >= 1
    assert "create_invoice" in payload["rendered_context"]


def test_tg_context_render_strips_python_docstrings_and_pass_boilerplate(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "class PaymentService:\n"
        '    """Service docstring."""\n'
        "    pass\n\n"
        "def create_invoice(total, tax):\n"
        '    """Create an invoice."""\n'
        "    subtotal = total + tax\n"
        "    return subtotal\n",
        encoding="utf-8",
    )

    class_payload = json.loads(
        mcp_server.tg_context_render(
            "payment service",
            str(project),
            optimize_context=True,
            render_profile="compact",
        )
    )
    function_payload = json.loads(
        mcp_server.tg_context_render(
            "create invoice",
            str(project),
            optimize_context=True,
            render_profile="compact",
        )
    )

    payment_service = next(
        item for item in class_payload["sources"] if item["name"] == "PaymentService"
    )
    create_invoice = next(
        item for item in function_payload["sources"] if item["name"] == "create_invoice"
    )

    assert '"""Service docstring."""' not in payment_service["rendered_source"]
    assert "pass" not in payment_service["rendered_source"]
    assert payment_service["render_diagnostics"]["removed_docstring_lines"] >= 1
    assert payment_service["render_diagnostics"]["removed_boilerplate_lines"] >= 1

    assert '"""Create an invoice."""' not in create_invoice["rendered_source"]
    assert "subtotal = total + tax" in create_invoice["rendered_source"]
    assert create_invoice["line_map"][0]["original_start_line"] == 5
    assert create_invoice["line_map"][0]["rendered_start_line"] == 1


def test_tg_session_context_render_accepts_max_tokens_and_model(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    sample_path = src_dir / "sample.py"
    sample_path.write_text(
        "def add(x):\n    baseline = x + 1\n    return baseline\n",
        encoding="utf-8",
    )

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    rendered = json.loads(
        mcp_server.tg_session_context_render(
            session_id,
            "add",
            str(project),
            max_files=1,
            max_sources=1,
            max_tokens=32,
            model="gpt-test",
        )
    )

    assert rendered["session_id"] == session_id
    assert rendered["files"][0] == str(sample_path.resolve())
    assert rendered["max_tokens"] == 32
    assert rendered["model"] == "gpt-test"
    assert isinstance(rendered["token_estimate"], int)
    assert rendered["omitted_sections"] == [] or all(
        {"file", "symbol", "score", "token_estimate"} <= set(section)
        for section in rendered["omitted_sections"]
    )


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
    assert payload["definitions"][0]["provenance"] == "python-ast"
    assert payload["graph_completeness"] == "strong"


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
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
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
        "pub fn issue_invoice() -> usize {\n    let subtotal = 1;\n    subtotal\n}\n",
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
    assert payload["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
    assert payload["symbol"] == "create_invoice"
    assert payload["files"][0] == str(module_path.resolve())
    assert str(other_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(
        entry["file"] == str(other_path.resolve()) and entry["provenance"] == "python-ast"
        for entry in payload["imports"]
    )


def test_tg_symbol_impact_prefers_import_linked_typescript_and_rust_tests(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

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
        "from src.payments import create_invoice\n\ndef bill():\n    return create_invoice(1, 2)\n",
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
        "from src.payments import create_invoice\n\ndef bill():\n    return create_invoice(1, 2)\n",
        encoding="utf-8",
    )
    noisy_path = notes_dir / "invoice_notes.py"
    noisy_path.write_text("def placeholder():\n    return 'invoice'\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_context_pack("create invoice", str(project)))

    assert payload["files"][0] == str(module_path.resolve())
    assert payload["files"][1] == str(importer_path.resolve())
    assert str(noisy_path.resolve()) not in payload["files"][:2]
    assert payload["file_matches"][0]["path"] == str(module_path.resolve())
    assert "symbol" in payload["file_matches"][0]["reasons"]
    assert "definition" in payload["file_matches"][0]["reasons"]
    assert payload["file_matches"][1]["path"] == str(importer_path.resolve())
    assert "import" in payload["file_matches"][1]["reasons"]
    assert any(
        entry["file"] == str(importer_path.resolve()) and entry["provenance"] == "python-ast"
        for entry in payload["imports"]
    )
    assert payload["file_summaries"][0]["path"] == str(module_path.resolve())
    assert {item["name"] for item in payload["file_summaries"][0]["symbols"]} == {"create_invoice"}


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
    assert payload["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
    assert payload["graph_completeness"] == "moderate"
    assert any(ref["provenance"] == "python-ast" for ref in payload["references"])
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

    assert ts_refs["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
    assert any(ref["file"] == str(ts_path.resolve()) for ref in ts_refs["references"])
    assert any(
        ref["provenance"] in {"tree-sitter", "regex-heuristic"} for ref in ts_refs["references"]
    )
    assert any(caller["file"] == str(ts_path.resolve()) for caller in ts_callers["callers"])
    assert any(ref["file"] == str(rust_path.resolve()) for ref in rust_refs["references"])
    assert any(
        ref["provenance"] in {"tree-sitter", "regex-heuristic"} for ref in rust_refs["references"]
    )
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
    assert payload["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
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


def test_tg_symbol_blast_radius_returns_transitive_call_tree(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    api_path = src_dir / "api.py"
    api_path.write_text(
        "from src.service import build_invoice\n\n"
        "def post_invoice(total):\n"
        "    return build_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_api.py"
    test_path.write_text(
        "from src.api import post_invoice\n\n"
        "def test_post_invoice():\n"
        "    assert post_invoice(2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius("create_invoice", str(project), max_depth=2)
    )

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-blast-radius"
    assert payload["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
    assert payload["symbol"] == "create_invoice"
    assert payload["max_depth"] == 2
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["definitions"][0]["provenance"] == "python-ast"
    assert any(caller["file"] == str(service_path.resolve()) for caller in payload["callers"])
    assert any(caller["provenance"] == "python-ast" for caller in payload["callers"])
    assert payload["files"][0] == str(module_path.resolve())
    assert str(service_path.resolve()) in payload["files"]
    assert str(api_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(level["depth"] == 0 for level in payload["caller_tree"])
    assert any(level["depth"] == 1 for level in payload["caller_tree"])
    assert all("graph-derived" in level["provenance"] for level in payload["caller_tree"])
    assert all(level["graph_completeness"] == "moderate" for level in payload["caller_tree"])
    assert all(
        level["edge_summary"]["edge_kind"] == "reverse-import" for level in payload["caller_tree"]
    )
    assert all("confidence" in level["edge_summary"] for level in payload["caller_tree"])
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["graph_trust_summary"]["depth_count"] >= 1
    assert "graph-derived" in payload["graph_trust_summary"]["provenance"]
    assert "Depth 0:" in payload["rendered_caller_tree"]


def test_tg_symbol_impact_can_rank_tests_through_transitive_import_chain(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    payments_path = src_dir / "payments.ts"
    payments_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
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
    assert payload["test_matches"][0]["path"] == str(test_path.resolve())
    assert "test-graph" in payload["test_matches"][0]["reasons"]
    assert payload["test_matches"][0]["association"]["edge_kind"] in {"import-graph", "hybrid"}
    assert payload["test_matches"][0]["association"]["confidence"] in {"strong", "moderate"}


def test_tg_context_pack_prefers_more_central_importers_over_tied_leaf_importers(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    central_path = src_dir / "z_billing.py"
    central_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(1, 2)\n",
        encoding="utf-8",
    )
    leaf_path = src_dir / "a_cli.py"
    leaf_path.write_text(
        "from src.payments import create_invoice\n\ndef run():\n    return create_invoice(2, 3)\n",
        encoding="utf-8",
    )
    ui_path = src_dir / "ui.py"
    ui_path.write_text(
        "from src.z_billing import invoice_total\n\ndef render():\n    return invoice_total()\n",
        encoding="utf-8",
    )
    api_path = src_dir / "api.py"
    api_path.write_text(
        "from src.z_billing import invoice_total\n\ndef serve():\n    return invoice_total()\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_context_pack("create invoice", str(project)))

    assert payload["files"].index(str(central_path.resolve())) < payload["files"].index(
        str(leaf_path.resolve())
    )
    central_match = next(
        item for item in payload["file_matches"] if item["path"] == str(central_path.resolve())
    )
    leaf_match = next(
        item for item in payload["file_matches"] if item["path"] == str(leaf_path.resolve())
    )
    assert "graph-centrality" in central_match["reasons"]
    assert central_match["graph_score"] > leaf_match["graph_score"]


def test_tg_symbol_impact_prefers_tests_covering_more_central_files(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    (src_dir / "z_billing.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(1, 2)\n",
        encoding="utf-8",
    )
    (src_dir / "a_cli.py").write_text(
        "from src.payments import create_invoice\n\ndef run():\n    return create_invoice(2, 3)\n",
        encoding="utf-8",
    )
    (src_dir / "ui.py").write_text(
        "from src.z_billing import invoice_total\n\ndef render():\n    return invoice_total()\n",
        encoding="utf-8",
    )
    ui_test = tests_dir / "test_ui_flow.py"
    ui_test.write_text(
        "from src.ui import render\n\ndef test_render():\n    assert render() == 3\n",
        encoding="utf-8",
    )
    cli_test = tests_dir / "test_cli_flow.py"
    cli_test.write_text(
        "from src.a_cli import run\n\ndef test_run():\n    assert run() == 5\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_impact("create_invoice", str(project)))

    assert payload["tests"].index(str(ui_test.resolve())) < payload["tests"].index(
        str(cli_test.resolve())
    )
    ui_match = next(
        item for item in payload["test_matches"] if item["path"] == str(ui_test.resolve())
    )
    cli_match = next(
        item for item in payload["test_matches"] if item["path"] == str(cli_test.resolve())
    )
    assert ui_match["graph_score"] > cli_match["graph_score"]
    assert "graph-derived" in ui_match["association"]["provenance"]
    assert ui_match["association"]["confidence"] in {"strong", "moderate"}


def test_tg_symbol_callers_uses_parser_backed_javascript_calls_not_string_noise(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    api_path = src_dir / "payments.js"
    api_path.write_text(
        "export function createInvoice(total) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.js"
    consumer_path.write_text(
        'import { createInvoice } from "./payments";\n'
        'const note = "createInvoice(1)";\n'
        "// createInvoice(2)\n"
        "export function renderInvoice() {\n"
        "  return createInvoice(3);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    consumer_calls = [
        caller for caller in payload["callers"] if caller["file"] == str(consumer_path.resolve())
    ]
    assert len(consumer_calls) == 1
    assert consumer_calls[0]["line"] == 5


def test_tg_symbol_callers_uses_parser_backed_typescript_calls_not_string_noise(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    api_path = src_dir / "payments.ts"
    api_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.ts"
    consumer_path.write_text(
        'import { createInvoice } from "./payments";\n'
        'const note: string = "createInvoice(1)";\n'
        "// createInvoice(2)\n"
        "export function renderInvoice() {\n"
        "  return createInvoice(3);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    consumer_calls = [
        caller for caller in payload["callers"] if caller["file"] == str(consumer_path.resolve())
    ]
    assert len(consumer_calls) == 1
    assert consumer_calls[0]["line"] == 5


def test_tg_symbol_callers_uses_parser_backed_rust_calls_not_string_noise(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    api_path = src_dir / "billing.rs"
    api_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.rs"
    consumer_path.write_text(
        'const NOTE: &str = "issue_invoice()";\n'
        "// issue_invoice();\n"
        "pub fn render_invoice() -> usize {\n"
        "    issue_invoice()\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("issue_invoice", str(project)))

    consumer_calls = [
        caller for caller in payload["callers"] if caller["file"] == str(consumer_path.resolve())
    ]
    assert len(consumer_calls) == 1
    assert consumer_calls[0]["line"] == 4


def test_tg_symbol_callers_resolves_javascript_namespace_import_aliases(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    api_path = src_dir / "payments.js"
    api_path.write_text(
        "export function createInvoice(total) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.js"
    consumer_path.write_text(
        'import * as paymentsApi from "./payments";\n'
        "export function renderInvoice() {\n"
        "  return paymentsApi.createInvoice(3);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    consumer_calls = [
        caller for caller in payload["callers"] if caller["file"] == str(consumer_path.resolve())
    ]
    assert len(consumer_calls) == 1
    assert consumer_calls[0]["line"] == 3


def test_tg_symbol_callers_resolves_rust_module_alias_use_chains(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    api_path = src_dir / "billing.rs"
    api_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.rs"
    consumer_path.write_text(
        "use crate::billing as billing_api;\n\n"
        "pub fn render_invoice() -> usize {\n"
        "    billing_api::issue_invoice()\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("issue_invoice", str(project)))

    consumer_calls = [
        caller for caller in payload["callers"] if caller["file"] == str(consumer_path.resolve())
    ]
    assert len(consumer_calls) == 1
    assert consumer_calls[0]["line"] == 4


def test_tg_symbol_callers_prefers_typescript_definition_selected_by_namespace_import(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    admin_dir = src_dir / "admin"
    src_dir.mkdir(parents=True)
    admin_dir.mkdir()

    preferred_path = src_dir / "payments.ts"
    preferred_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    other_path = admin_dir / "payments.ts"
    other_path.write_text(
        "export function createInvoice(total: number) {\n  return total * 2;\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.ts"
    consumer_path.write_text(
        'import * as paymentsApi from "./payments";\n'
        "export function renderInvoice() {\n"
        "  return paymentsApi.createInvoice(3);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["name"] == "createInvoice"
    assert payload["definitions"][0]["kind"] == "function"
    assert payload["definitions"][0]["file"] == str(preferred_path.resolve())
    assert payload["definitions"][0]["line"] == 1
    assert any(caller["file"] == str(consumer_path.resolve()) for caller in payload["callers"])
    assert all(
        definition["file"] != str(other_path.resolve()) for definition in payload["definitions"]
    )


def test_tg_symbol_callers_prefers_rust_definition_selected_by_module_alias_use_chain(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    other_dir = src_dir / "other"
    src_dir.mkdir(parents=True)
    other_dir.mkdir()

    preferred_path = src_dir / "billing.rs"
    preferred_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )
    other_path = other_dir / "billing.rs"
    other_path.write_text(
        "pub fn issue_invoice() -> usize {\n    2\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.rs"
    consumer_path.write_text(
        "use crate::billing as billing_api;\n\n"
        "pub fn render_invoice() -> usize {\n"
        "    billing_api::issue_invoice()\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("issue_invoice", str(project)))

    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["name"] == "issue_invoice"
    assert payload["definitions"][0]["kind"] == "function"
    assert payload["definitions"][0]["file"] == str(preferred_path.resolve())
    assert payload["definitions"][0]["line"] == 1
    assert any(caller["file"] == str(consumer_path.resolve()) for caller in payload["callers"])
    assert all(
        definition["file"] != str(other_path.resolve()) for definition in payload["definitions"]
    )


def test_tg_symbol_callers_prefers_typescript_tests_importing_direct_callers(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.ts"
    module_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    ui_path = src_dir / "ui.ts"
    ui_path.write_text(
        'import { createInvoice } from "./payments";\n'
        "export function renderInvoice() {\n"
        "  return createInvoice(3);\n"
        "}\n",
        encoding="utf-8",
    )
    cli_path = src_dir / "cli.ts"
    cli_path.write_text(
        'import { renderInvoice } from "./ui";\n'
        "export function runCli() {\n"
        "  return renderInvoice();\n"
        "}\n",
        encoding="utf-8",
    )
    ui_test = tests_dir / "ui_flow.spec.ts"
    ui_test.write_text(
        'import { renderInvoice } from "../src/ui";\n'
        'test("invoice", () => expect(renderInvoice()).toBe(3));\n',
        encoding="utf-8",
    )
    cli_test = tests_dir / "cli_flow.spec.ts"
    cli_test.write_text(
        'import { runCli } from "../src/cli";\n'
        'test("invoice cli", () => expect(runCli()).toBe(3));\n',
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    assert payload["tests"].index(str(ui_test.resolve())) < payload["tests"].index(
        str(cli_test.resolve())
    )


def test_tg_symbol_callers_prefers_rust_tests_importing_direct_callers(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "billing.rs"
    module_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )
    ui_path = src_dir / "ui.rs"
    ui_path.write_text(
        "use crate::billing::issue_invoice;\n\n"
        "pub fn render_invoice() -> usize {\n"
        "    issue_invoice()\n"
        "}\n",
        encoding="utf-8",
    )
    cli_path = src_dir / "cli.rs"
    cli_path.write_text(
        "use crate::ui::render_invoice;\n\npub fn run_cli() -> usize {\n    render_invoice()\n}\n",
        encoding="utf-8",
    )
    ui_test = tests_dir / "ui_flow.rs"
    ui_test.write_text(
        "use crate::ui::render_invoice;\n\n"
        "#[test]\n"
        "fn renders_invoice() {\n"
        "    assert_eq!(render_invoice(), 1);\n"
        "}\n",
        encoding="utf-8",
    )
    cli_test = tests_dir / "cli_flow.rs"
    cli_test.write_text(
        "use crate::cli::run_cli;\n\n"
        "#[test]\n"
        "fn runs_invoice_cli() {\n"
        "    assert_eq!(run_cli(), 1);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("issue_invoice", str(project)))

    assert payload["tests"].index(str(ui_test.resolve())) < payload["tests"].index(
        str(cli_test.resolve())
    )


def test_tg_symbol_source_ignores_comment_noise_for_typescript_and_rust(tmp_path):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "// export function createInvoice() {}\n"
        "export function createInvoice(total: number) {\n"
        "  return total;\n"
        "}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "// pub fn issue_invoice() -> usize { 0 }\npub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )

    ts_payload = json.loads(mcp_server.tg_symbol_source("createInvoice", str(project)))
    rust_payload = json.loads(mcp_server.tg_symbol_source("issue_invoice", str(project)))

    assert ts_payload["sources"][0]["file"] == str(ts_path.resolve())
    assert ts_payload["sources"][0]["start_line"] == 2
    assert "return total;" in ts_payload["sources"][0]["source"]

    assert rust_payload["sources"][0]["file"] == str(rust_path.resolve())
    assert rust_payload["sources"][0]["start_line"] == 2
    assert "1" in rust_payload["sources"][0]["source"]
