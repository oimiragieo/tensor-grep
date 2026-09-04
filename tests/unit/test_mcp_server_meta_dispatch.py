"""MCP meta-tool dispatch, legacy-tool flag, and capabilities contracts."""

import json
from importlib.metadata import version
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.unit.test_mcp_server_shared import (
    _EXPECTED_META_TOOL_NAMES,
    _EXPECTED_SINGLETON_TOOL_NAMES,
    _call_mcp_tool_text,
    _mcp_tool_names,
    _run_mcp_flag_probe_subprocess,
)


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
    assert (
        options.server_version == "1.7.0"
    )  # task 336: budget_remediable on the repo_map-backed wire
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
    assert "Native binary not found: FileNotFoundError" in payload["native_tg"]["error"]


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
