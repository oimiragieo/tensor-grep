"""Tests for Wave 4 Track Q (IMP-05): compact 15-tool default surface & < 12 KB TSCG schema budget."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_MAX_TOOLS_LIST_BYTES = 12 * 1024


def _probe_tools_list(env_overrides: dict[str, str] | None = None) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(repo_root / "src")}
    env.pop("TG_MCP_LEGACY_TOOLS", None)
    if env_overrides:
        env.update(env_overrides)
    script = (
        "import asyncio, json; from tensor_grep.cli import mcp_server as m; "
        "tools = asyncio.run(m.mcp.list_tools()); "
        "payload = [t.model_dump(mode='json', by_alias=True, exclude_none=True) for t in tools]; "
        "std_raw = json.dumps({'tools': payload}).encode('utf-8'); "
        "min_raw = json.dumps({'tools': payload}, separators=(',', ':')).encode('utf-8'); "
        "print(json.dumps({"
        "'legacy_enabled': m._legacy_tools_enabled(), "
        "'count': len(tools), "
        "'names': [t.name for t in tools], "
        "'std_bytes': len(std_raw), "
        "'min_bytes': len(min_raw), "
        "'output_schemas_ok': all("
        "'properties' in t['outputSchema'] and 'required' in t['outputSchema'] "
        "for t in payload if 'outputSchema' in t)"
        "}))"
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
    return dict(json.loads(done.stdout))


def test_default_surface_is_compact_15_tools_under_12kb() -> None:
    info = _probe_tools_list()
    assert info["legacy_enabled"] is False
    assert info["count"] == 15
    names = set(info["names"])  # type: ignore[arg-type]
    assert "tg_batch" in names
    assert "tg_discover_tools" in names
    assert "tg_symbol_defs" not in names
    assert int(info["min_bytes"]) < _MAX_TOOLS_LIST_BYTES  # type: ignore[arg-type]
    assert int(info["std_bytes"]) < _MAX_TOOLS_LIST_BYTES  # type: ignore[arg-type]
    assert info["output_schemas_ok"] is True


def test_legacy_tools_opt_in_exposes_full_61_tools() -> None:
    info = _probe_tools_list({"TG_MCP_LEGACY_TOOLS": "1"})
    assert info["legacy_enabled"] is True
    assert info["count"] == 61
    names = set(info["names"])  # type: ignore[arg-type]
    assert "tg_symbol_defs" in names
    assert "tg_search" in names
    assert "tg_discover_tools" in names


def test_mutating_tools_preserve_safety_warnings_and_title_property_survives() -> None:
    from tensor_grep.cli.mcp_compact import _COMPACT_TOOL_DESCRIPTIONS, _strip_property_defaults

    assert "mutates" in _COMPACT_TOOL_DESCRIPTIONS["tg_rewrite"].lower()
    assert "plan" in _COMPACT_TOOL_DESCRIPTIONS["tg_rewrite"].lower()
    assert "verify" in _COMPACT_TOOL_DESCRIPTIONS["tg_rewrite"].lower()
    assert "mutates" in _COMPACT_TOOL_DESCRIPTIONS["tg_checkpoint"].lower()
    assert "gate" in _COMPACT_TOOL_DESCRIPTIONS["tg_audit"].lower()
    assert "mutates" in _COMPACT_TOOL_DESCRIPTIONS["tg_session"].lower()

    schema = {
        "type": "object",
        "title": "RootSchema",
        "properties": {
            "title": {"type": "string", "title": "Title", "default": None},
            "other": {"type": "integer", "title": "Other"},
        },
    }
    _strip_property_defaults(schema)
    assert "title" not in schema
    assert "title" in schema["properties"]
    assert schema["properties"]["title"] == {"type": "string"}
    assert schema["properties"]["other"] == {"type": "integer"}
