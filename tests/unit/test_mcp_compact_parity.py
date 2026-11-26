"""Tests for DF-09: MCP compact mode, capabilities summary, and TSCG schema tightening."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tensor_grep.cli import mcp_compact, mcp_server


@pytest.fixture
def sample_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def compute_total(items: list[int]) -> int:\n    return sum(items)\n",
        encoding="utf-8",
    )
    (repo / "test_app.py").write_text(
        "from app import compute_total\n\n\ndef test_compute_total() -> None:\n    assert compute_total([1, 2]) == 3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    return repo


@pytest.mark.parametrize(
    ("action", "expected_key"),
    [
        ("capsule", "primary_target"),
        ("pack", "files"),
        ("edit_plan", "plan"),
        ("render", "rendered_context"),
    ],
)
def test_tg_context_compact_bounds_output_and_preserves_deliverable(
    sample_repo: Path, action: str, expected_key: str
) -> None:
    raw = mcp_server.tg_context(
        action=action,  # type: ignore[arg-type]
        query="compute_total",
        path=str(sample_repo),
        compact=True,
    )
    assert len(raw.encode("utf-8")) <= 4000
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    assert "error" not in payload
    assert payload.get("mcp_contract_version") == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert expected_key in payload
    if action == "edit_plan":
        assert "candidate_edit_targets" in payload


def test_tg_context_compact_preserves_pack_render_edit_plan_and_envelope_on_large_payload() -> None:
    # Pack: navigation_pack preserved, verbose diagnostics omitted -> compact_truncated=True
    pack_payload = {
        "mcp_contract_version": "1.26.0",
        "routing_backend": "RepoMap",
        "routing_reason": "context-pack",
        "sidecar_used": False,
        "result_incomplete": False,
        "incomplete_reason": None,
        "navigation_pack": {"files": ["app.py"], "score": 0.99},
        "coverage": {"files": ["x" * 200 for _ in range(30)]},
        "symbol_cache": {"entries": list(range(100))},
    }
    compacted_pack = json.loads(
        mcp_compact.compact_mcp_json_result(json.dumps(pack_payload), action="pack")
    )
    assert compacted_pack["navigation_pack"] == {"files": ["app.py"], "score": 0.99}
    assert compacted_pack["compact_truncated"] is True
    for k in (
        "mcp_contract_version",
        "routing_backend",
        "routing_reason",
        "sidecar_used",
        "result_incomplete",
        "incomplete_reason",
    ):
        assert k in compacted_pack

    # Render: oversized rendered_context is trimmed cleanly <= 4000 bytes, envelope preserved
    render_payload = {
        "mcp_contract_version": "1.26.0",
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "sidecar_used": False,
        "result_incomplete": True,
        "incomplete_reason": "budget_cap",
        "error": {"code": "partial", "message": "trimmed"},
        "rendered_context": "def f(): pass\n" * 600,
        "coverage": {"blob": "y" * 2000},
    }
    raw_render = mcp_compact.compact_mcp_json_result(json.dumps(render_payload), action="render")
    assert len(raw_render.encode("utf-8")) <= 4000
    compacted_render = json.loads(raw_render)
    assert compacted_render["compact_truncated"] is True
    assert isinstance(compacted_render["rendered_context"], str)
    assert compacted_render["rendered_context"]
    assert compacted_render["error"] == {"code": "partial", "message": "trimmed"}
    assert compacted_render["result_incomplete"] is True
    assert compacted_render["incomplete_reason"] == "budget_cap"

    # Edit plan: plan & candidate_edit_targets preserved even under heavy secondary bloat
    edit_plan_payload = {
        "mcp_contract_version": "1.26.0",
        "routing_backend": "RepoMap",
        "routing_reason": "context-edit-plan",
        "sidecar_used": False,
        "plan": {"steps": ["edit app.py"]},
        "candidate_edit_targets": [{"file": "app.py", "score": 1.0}],
        "snippets": ["z" * 500 for _ in range(20)],
    }
    raw_plan = mcp_compact.compact_mcp_json_result(
        json.dumps(edit_plan_payload), action="edit_plan"
    )
    assert len(raw_plan.encode("utf-8")) <= 4000
    compacted_plan = json.loads(raw_plan)
    assert compacted_plan["compact_truncated"] is True
    assert compacted_plan["plan"] == {"steps": ["edit app.py"]}
    assert compacted_plan["candidate_edit_targets"] == [{"file": "app.py", "score": 1.0}]


def test_compact_mcp_json_result_bounds_non_dict_and_malformed_json() -> None:
    malformed = "not-json " * 1000
    out_malformed = mcp_compact.compact_mcp_json_result(malformed)
    assert len(out_malformed.encode("utf-8")) <= 4000
    parsed_malformed = json.loads(out_malformed)
    assert parsed_malformed["compact_truncated"] is True

    huge_list = json.dumps(["item_" + ("x" * 100) for _ in range(100)])
    out_list = mcp_compact.compact_mcp_json_result(huge_list)
    assert len(out_list.encode("utf-8")) <= 4000
    parsed_list = json.loads(out_list)
    assert parsed_list["compact_truncated"] is True


def test_tg_context_compact_rejects_citations_cleanly(sample_repo: Path) -> None:
    raw = mcp_server.tg_context(
        action="capsule",
        query="compute_total",
        path=str(sample_repo),
        compact=True,
        citations=True,
    )
    payload = json.loads(raw)
    assert payload["error"]["code"] == "invalid_input"
    assert "citations" in payload["error"]["message"]


@pytest.mark.parametrize(
    ("action", "expected_key"),
    [
        ("orient", "central_files"),
        ("repo_map", "files"),
        ("doctor", "installation_health"),
        ("devices", "devices"),
    ],
)
def test_tg_explore_compact_bounds_all_actions_to_4000_bytes(
    sample_repo: Path, action: str, expected_key: str
) -> None:
    raw = mcp_server.tg_explore(
        action=action,  # type: ignore[arg-type]
        path=str(sample_repo),
        compact=True,
    )
    assert len(raw.encode("utf-8")) <= 4000
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    assert "error" not in payload
    assert payload.get("mcp_contract_version") == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert expected_key in payload


def test_tg_explore_compact_rejects_plain_text_devices(sample_repo: Path) -> None:
    raw = mcp_server.tg_explore(
        action="devices",
        path=str(sample_repo),
        json_output=False,
        compact=True,
    )
    payload = json.loads(raw)
    assert payload["error"]["code"] == "invalid_input"
    assert "json_output=False" in payload["error"]["message"]


def test_tg_mcp_capabilities_summary_under_3072_bytes() -> None:
    raw = mcp_server.tg_mcp_capabilities(summary=True)
    assert len(raw.encode("utf-8")) <= mcp_compact.CAPABILITIES_SUMMARY_MAX_BYTES
    payload = json.loads(raw)
    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    assert isinstance(payload["cli_version"], str) and payload["cli_version"]
    assert isinstance(payload["native_available"], bool)
    assert isinstance(payload["meta_tools"], list) and len(payload["meta_tools"]) == 10
    assert isinstance(payload["legacy_tools_enabled"], bool)
    assert "tools" not in payload

    full_payload = json.loads(mcp_server.tg_mcp_capabilities(summary=False))
    assert isinstance(full_payload["tools"], list) and len(full_payload["tools"]) >= 15


def test_build_capabilities_summary_enforces_3072_byte_cap() -> None:
    summary = mcp_compact.build_capabilities_summary_payload(
        version=1,
        mcp_contract_version="1.26.0",
        cli_version="1.141.0",
        native_available=True,
        embedded_rewrite_available=True,
        legacy_tools_enabled=True,
        total_tools=500,
        meta_tools=[f"tg_meta_{i}" for i in range(50)],
        singleton_tools=[f"tg_singleton_very_long_tool_name_{i}" for i in range(200)],
    )
    assert summary["compact_truncated"] is True
    assert len(json.dumps(summary, indent=2).encode("utf-8")) <= (
        mcp_compact.CAPABILITIES_SUMMARY_MAX_BYTES
    )


def test_tscg_schema_tightening_preserves_types_defaults_and_constraints() -> None:
    import os
    import subprocess
    import sys

    tools = asyncio.run(mcp_server.mcp.list_tools())
    by_name = {t.name: t.model_dump(mode="json", exclude_none=True) for t in tools}
    assert "tg_context" in by_name
    ctx_props = by_name["tg_context"]["inputSchema"]["properties"]
    assert ctx_props["compact"]["default"] is False
    assert ctx_props["compact"]["type"] == "boolean"
    assert ctx_props["grounding"]["enum"] == ["off", "local", "registry"]
    assert ctx_props["query"]["type"] == ["string", "null"]

    # Verify compact surface (TG_MCP_LEGACY_TOOLS=0) applies TSCG tightening without losing semantics.
    repo_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src"), "TG_MCP_LEGACY_TOOLS": "0"}
    script = (
        "import asyncio, json; from tensor_grep.cli import mcp_server as m; "
        "tools = {t.name: t.model_dump(mode='json', exclude_none=True) "
        "for t in asyncio.run(m.mcp.list_tools())}; "
        "print(json.dumps({'query': tools['tg_context']['inputSchema']['properties']['query'], "
        "'tg_batch_desc': tools['tg_batch']['description']}))"
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    compact_info = json.loads(done.stdout)
    assert compact_info["query"]["type"] == ["string", "null"]
    assert "title" not in compact_info["query"]
    batch_desc = compact_info["tg_batch_desc"]
    assert "result_incomplete=true" in batch_desc
    assert "120 seconds" in batch_desc
    assert "Invalid top-level requests" in batch_desc

    # Non-null branches with constraints (minimum, enum, pattern) must retain anyOf.
    schema = {
        "properties": {
            "simple_nullable": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "constrained_int": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
            "constrained_enum": {
                "anyOf": [{"type": "string", "enum": ["a", "b"]}, {"type": "null"}]
            },
        }
    }
    mcp_compact._collapse_nullable_anyof(schema)
    assert schema["properties"]["simple_nullable"] == {"type": ["string", "null"]}
    assert "anyOf" in schema["properties"]["constrained_int"]
    assert "anyOf" in schema["properties"]["constrained_enum"]


def test_build_capabilities_summary_enforces_cap_with_oversized_contract_version() -> None:
    summary = mcp_compact.build_capabilities_summary_payload(
        version=1,
        mcp_contract_version="v" * 5000,
        cli_version="c" * 5000,
        native_available=True,
        embedded_rewrite_available=True,
        legacy_tools_enabled=True,
        total_tools=500,
        meta_tools=[f"tg_meta_{i}" for i in range(20)],
        singleton_tools=[f"tg_singleton_{i}" for i in range(20)],
    )
    assert summary["compact_truncated"] is True
    wired = dict(summary)
    wired.setdefault("incomplete", {"status": False, "cause": None, "budget_remediable": False})
    assert len(json.dumps(wired, indent=2).encode("utf-8")) <= (
        mcp_compact.CAPABILITIES_SUMMARY_MAX_BYTES
    )


def test_tg_mcp_capabilities_summary_enforces_cap_with_oversized_contract_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "_TG_MCP_SERVER_CONTRACT_VERSION", "v" * 5000)
    raw = mcp_server.tg_mcp_capabilities(summary=True)
    assert len(raw.encode("utf-8")) <= mcp_compact.CAPABILITIES_SUMMARY_MAX_BYTES
    payload = json.loads(raw)
    assert payload["compact_truncated"] is True


def test_compact_mcp_json_result_records_omissions_for_filtered_actionable_evidence() -> None:
    payload = {
        "mcp_contract_version": "1.26.0",
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "sidecar_used": False,
        "rendered_context": "def f(): pass",
        "candidate_edit_targets": [
            {"file": "a.py", "symbol": "f"},
            {"file": "b.py", "symbol": "g"},
        ],
    }
    raw = mcp_compact.compact_mcp_json_result(json.dumps(payload), action="render")
    compacted = json.loads(raw)
    assert compacted["compact_truncated"] is True
    assert "candidate_edit_targets" not in compacted
    assert compacted["omissions"]["compact_omitted"]["candidate_edit_targets"] == 2
    assert compacted["omissions"]["omitted_count"] == 2


def test_compact_mcp_json_result_retains_mandatory_envelope_in_extreme_fallback() -> None:
    payload = {
        "mcp_contract_version": "1.26.0",
        "version": 1,
        "schema_version": 1,
        "routing_backend": "RepoMap",
        "routing_reason": "r" * 2000,
        "sidecar_used": False,
        "error": {"code": "huge_error", "message": "e" * 3000},
        "result_incomplete": True,
        "incomplete_reason": "i" * 2000,
        "incomplete": {"status": True, "cause": "c" * 2000, "budget_remediable": True},
        "primary_target": {"file": "a.py", "symbol": "fn", "blob": "x" * 5000},
    }
    raw = mcp_compact.compact_mcp_json_result(json.dumps(payload), action="capsule")
    assert len(raw.encode("utf-8")) <= mcp_compact.COMPACT_RESULT_MAX_BYTES
    compacted = json.loads(raw)
    for key in (
        "mcp_contract_version",
        "version",
        "schema_version",
        "routing_backend",
        "routing_reason",
        "sidecar_used",
        "error",
        "result_incomplete",
        "incomplete_reason",
        "incomplete",
        "compact_truncated",
    ):
        assert key in compacted
    assert compacted["compact_truncated"] is True
    assert compacted["omissions"]["compact_omitted"]["primary_target"] == 1
    assert compacted["omissions"]["omitted_count"] == 1


def test_compact_mcp_json_result_omits_whole_evidence_entries_without_slicing() -> None:
    targets = [
        {"file": f"src/mod_{i}.py", "symbol": f"sym_{i}", "score": 1.0 - i * 0.01}
        for i in range(80)
    ]
    payload = {
        "mcp_contract_version": "1.26.0",
        "routing_backend": "RepoMap",
        "routing_reason": "context-edit-plan",
        "sidecar_used": False,
        "primary_target": {"file": "src/mod_0.py", "symbol": "sym_0", "line": 12},
        "candidate_edit_targets": targets,
        "rollback": {"checkpoint_recommended": True, "command": "git checkout -- src/mod_0.py"},
        "call_site_evidence": {"status": "collected", "returned_call_sites": 3},
        "context_consistency": {"consistent": True, "score": 1.0},
    }
    raw = mcp_compact.compact_mcp_json_result(json.dumps(payload), action="edit_plan")
    assert len(raw.encode("utf-8")) <= mcp_compact.COMPACT_RESULT_MAX_BYTES
    compacted = json.loads(raw)
    assert compacted["compact_truncated"] is True
    assert compacted["primary_target"] == payload["primary_target"]
    assert compacted["rollback"] == payload["rollback"]
    assert compacted["call_site_evidence"] == payload["call_site_evidence"]
    assert compacted["context_consistency"] == payload["context_consistency"]
    assert 0 < len(compacted["candidate_edit_targets"]) < len(targets)
    assert (
        compacted["candidate_edit_targets"] == targets[: len(compacted["candidate_edit_targets"])]
    )
    assert compacted["omissions"]["compact_omitted"]["candidate_edit_targets"] == (
        len(targets) - len(compacted["candidate_edit_targets"])
    )


def test_tighten_description_text_preserves_mid_sentence_optional() -> None:
    assert mcp_compact._tighten_description_text("Optional. Filter pattern.") == "Filter pattern."
    assert mcp_compact._tighten_description_text("Optional: Filter pattern.") == "Filter pattern."
    assert mcp_compact._tighten_description_text("Filter pattern (optional)") == "Filter pattern"
    assert (
        mcp_compact._tighten_description_text("Provide an optional filter pattern.")
        == "Provide an optional filter pattern."
    )


def test_compact_mcp_json_result_prioritizes_compact_budget_omissions_in_extreme_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "mcp_contract_version": "1.26.0",
        "version": 1,
        "schema_version": 1,
        "routing_backend": "RepoMap",
        "routing_reason": "\U0001f600" * 2000,
        "sidecar_used": False,
        "error": {"code": "huge_error", "message": "\U0001f600" * 3000},
        "result_incomplete": True,
        "incomplete_reason": "\U0001f600" * 2000,
        "incomplete": {"status": True, "cause": "\U0001f600" * 2000, "budget_remediable": True},
        "omitted_sections": [
            {"section": f"pre_{i}", "omitted_count": 1, "reason": "token_budget"} for i in range(6)
        ],
        "primary_target": {"file": "a.py", "symbol": "fn", "blob": "x" * 5000},
    }
    raw = mcp_compact.compact_mcp_json_result(json.dumps(payload), action="capsule")
    assert len(raw.encode("utf-8")) <= mcp_compact.COMPACT_RESULT_MAX_BYTES
    compacted = json.loads(raw)
    assert compacted["compact_truncated"] is True
    budget_entries = [
        e
        for e in compacted.get("omitted_sections", [])
        if isinstance(e, dict) and e.get("reason") == "compact_budget"
    ]
    assert budget_entries and budget_entries[0]["section"] == "primary_target"
    assert budget_entries[0]["omitted_count"] == 1

    monkeypatch.setattr(mcp_compact, "COMPACT_RESULT_MAX_BYTES", 400)
    raw_zero = mcp_compact.compact_mcp_json_result(json.dumps(payload), action="capsule")
    assert len(raw_zero.encode("utf-8")) <= 400
    compacted_zero = json.loads(raw_zero)
    zero_entries = [
        e
        for e in compacted_zero.get("omitted_sections", [])
        if isinstance(e, dict) and e.get("reason") == "compact_budget"
    ]
    assert zero_entries and zero_entries[0]["section"] == "primary_target"
    assert zero_entries[0]["omitted_count"] == 1
