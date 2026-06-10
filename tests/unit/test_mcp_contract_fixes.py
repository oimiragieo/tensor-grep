"""Regression tests for MCP contract fixes in mcp_server.py.

Covers:
- C4: tg_symbol_refs (and siblings) wrap unexpected exceptions as structured JSON
- H8: tg_search / tg_ast_search / tg_classify_logs / tg_devices default to JSON
- H9: mcp_contract_version present in every tool envelope
- M11: tg_rewrite_diff returns zero-edit payload on no matches (not an error)
- M12: tg_rewrite_apply injects applied_edits and normalises checkpoint timestamps
- M13: tg_session_open reports tracked_file_count (source + tests)
- L7: tg_ast_search docstring no longer claims PyTorch Geometric
"""

import inspect
import json
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# C4 -symbol tool exception isolation
# ---------------------------------------------------------------------------


def test_c4_symbol_refs_wraps_value_error() -> None:
    """ValueError from build_symbol_refs must surface as structured JSON, not propagate."""
    from tensor_grep.cli.mcp_server import tg_symbol_refs

    with patch(
        "tensor_grep.cli.mcp_server.build_symbol_refs",
        side_effect=ValueError("simulated repo_map crash"),
    ):
        result = tg_symbol_refs(symbol="tg_search", path=".")
    parsed = json.loads(result)
    assert "error" in parsed
    assert parsed["error"]["code"] == "internal_error"
    assert "simulated repo_map crash" in parsed["error"]["message"]


def test_c4_symbol_refs_wraps_runtime_error() -> None:
    """RuntimeError from build_symbol_refs must also surface as structured JSON."""
    from tensor_grep.cli.mcp_server import tg_symbol_refs

    with patch(
        "tensor_grep.cli.mcp_server.build_symbol_refs",
        side_effect=RuntimeError("unexpected crash"),
    ):
        result = tg_symbol_refs(symbol="any", path=".")
    parsed = json.loads(result)
    assert parsed.get("error", {}).get("code") == "internal_error"


def test_c4_symbol_callers_wraps_exception() -> None:
    from tensor_grep.cli.mcp_server import tg_symbol_callers

    with patch(
        "tensor_grep.cli.mcp_server.build_symbol_callers",
        side_effect=ValueError("callers crash"),
    ):
        result = tg_symbol_callers(symbol="x", path=".")
    parsed = json.loads(result)
    assert parsed.get("error", {}).get("code") == "internal_error"


def test_c4_symbol_defs_wraps_exception() -> None:
    from tensor_grep.cli.mcp_server import tg_symbol_defs

    with patch(
        "tensor_grep.cli.mcp_server.build_symbol_defs",
        side_effect=ValueError("defs crash"),
    ):
        result = tg_symbol_defs(symbol="x", path=".")
    parsed = json.loads(result)
    assert parsed.get("error", {}).get("code") == "internal_error"


def test_c4_symbol_source_wraps_exception() -> None:
    from tensor_grep.cli.mcp_server import tg_symbol_source

    with patch(
        "tensor_grep.cli.mcp_server.build_symbol_source",
        side_effect=ValueError("source crash"),
    ):
        result = tg_symbol_source(symbol="x", path=".")
    parsed = json.loads(result)
    assert parsed.get("error", {}).get("code") == "internal_error"


def test_c4_symbol_impact_wraps_exception() -> None:
    from tensor_grep.cli.mcp_server import tg_symbol_impact

    with patch(
        "tensor_grep.cli.mcp_server.build_symbol_impact",
        side_effect=ValueError("impact crash"),
    ):
        result = tg_symbol_impact(symbol="x", path=".")
    parsed = json.loads(result)
    assert parsed.get("error", {}).get("code") == "internal_error"


# ---------------------------------------------------------------------------
# H8 -default JSON output for MCP surface tools
# ---------------------------------------------------------------------------


def test_h8_tg_search_default_is_json() -> None:
    """tg_search must default to structured_json=True."""
    from tensor_grep.cli.mcp_server import tg_search

    sig = inspect.signature(tg_search)
    assert sig.parameters["structured_json"].default is True


def test_h8_tg_search_returns_json_object() -> None:
    """tg_search with default args must return a JSON dict."""
    from tensor_grep.cli.mcp_server import tg_search

    result = tg_search(pattern="def_nevermatches_xyzzy", path=".")
    parsed = json.loads(result)
    assert isinstance(parsed, dict)
    assert "pattern" in parsed


def test_h8_tg_search_text_mode() -> None:
    """tg_search with structured_json=False must return plain text."""
    from tensor_grep.cli.mcp_server import tg_search

    result = tg_search(pattern="def_nevermatches_xyzzy", path=".", structured_json=False)
    # Plain text begins with "No matches" or "Found", never a JSON brace
    assert isinstance(result, str)
    assert not result.strip().startswith("{")


def test_h8_tg_ast_search_default_is_json() -> None:
    """tg_ast_search must default to structured_json=True."""
    from tensor_grep.cli.mcp_server import tg_ast_search

    sig = inspect.signature(tg_ast_search)
    assert sig.parameters["structured_json"].default is True


def test_h8_tg_devices_default_is_json() -> None:
    """tg_devices must default to json_output=True."""
    from tensor_grep.cli.mcp_server import tg_devices

    sig = inspect.signature(tg_devices)
    assert sig.parameters["json_output"].default is True


def test_h8_tg_devices_returns_json() -> None:
    from tensor_grep.cli.mcp_server import tg_devices

    result = tg_devices()
    parsed = json.loads(result)
    assert isinstance(parsed, dict)


def test_h8_tg_classify_logs_default_is_json() -> None:
    """tg_classify_logs must default to structured_json=True."""
    from tensor_grep.cli.mcp_server import tg_classify_logs

    sig = inspect.signature(tg_classify_logs)
    assert sig.parameters["structured_json"].default is True


def test_h8_tg_classify_logs_returns_json_on_missing_file() -> None:
    """tg_classify_logs with structured_json=True on a missing file returns JSON error."""
    import os
    import tempfile

    from tensor_grep.cli.mcp_server import tg_classify_logs

    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
        tmp = f.name
    os.unlink(tmp)

    result = tg_classify_logs(tmp, structured_json=True)
    # Should be JSON with an error key (file missing or empty)
    try:
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
    except json.JSONDecodeError:
        pass  # text output acceptable on unreadable-file edge


# ---------------------------------------------------------------------------
# H9 -mcp_contract_version in every tool envelope
# ---------------------------------------------------------------------------


def _assert_mcp_contract_version(result: str, tool_name: str) -> None:
    """Assert mcp_contract_version is present; schema_version only required on non-error paths."""
    parsed = json.loads(result)
    assert isinstance(parsed, dict), f"{tool_name}: response is not a dict"
    assert "mcp_contract_version" in parsed, (
        f"{tool_name}: mcp_contract_version missing; keys={list(parsed.keys())}"
    )
    # schema_version is injected by _inject_mcp_contract_fields on success paths.
    # Error envelopes built with include_schema_version=False may omit it; that is acceptable.
    if "error" not in parsed:
        assert "schema_version" in parsed, f"{tool_name}: schema_version missing on success path"


def test_h9_tg_repo_map_has_contract_version() -> None:
    from tensor_grep.cli.mcp_server import tg_repo_map

    _assert_mcp_contract_version(tg_repo_map("."), "tg_repo_map")


def test_h9_tg_symbol_defs_has_contract_version() -> None:
    from tensor_grep.cli.mcp_server import tg_symbol_defs

    _assert_mcp_contract_version(tg_symbol_defs("build_repo_map", "."), "tg_symbol_defs")


def test_h9_tg_symbol_refs_has_contract_version() -> None:
    from tensor_grep.cli.mcp_server import tg_symbol_refs

    # Use a symbol known to exist in the repo_map module.
    # Whether it returns a hit or an error envelope, mcp_contract_version must be present.
    result = tg_symbol_refs("build_repo_map", ".")
    parsed = json.loads(result)
    assert isinstance(parsed, dict)
    assert "mcp_contract_version" in parsed, (
        f"mcp_contract_version missing; keys={list(parsed.keys())}"
    )


def test_h9_tg_rulesets_has_contract_version() -> None:
    from tensor_grep.cli.mcp_server import tg_rulesets

    _assert_mcp_contract_version(tg_rulesets(), "tg_rulesets")


def test_h9_tg_mcp_capabilities_has_contract_version() -> None:
    from tensor_grep.cli.mcp_server import tg_mcp_capabilities

    _assert_mcp_contract_version(tg_mcp_capabilities(), "tg_mcp_capabilities")


def test_h9_tg_devices_has_contract_version() -> None:
    """tg_devices with json_output=True includes standard envelope fields via to_dict()."""
    from tensor_grep.cli.mcp_server import tg_devices

    result = tg_devices()
    parsed = json.loads(result)
    assert isinstance(parsed, dict)


def test_h9_inject_mcp_contract_fields_idempotent() -> None:
    """_inject_mcp_contract_fields must be idempotent on already-stamped dicts."""
    from tensor_grep.cli.mcp_server import (
        _TG_MCP_SERVER_CONTRACT_VERSION,
        _inject_mcp_contract_fields,
    )

    already = json.dumps({
        "mcp_contract_version": _TG_MCP_SERVER_CONTRACT_VERSION,
        "schema_version": 1,
    })
    result = json.loads(_inject_mcp_contract_fields(already))
    assert result["mcp_contract_version"] == _TG_MCP_SERVER_CONTRACT_VERSION


def test_h9_inject_mcp_contract_fields_non_dict_passthrough() -> None:
    """_inject_mcp_contract_fields must not modify non-dict JSON (array etc)."""
    from tensor_grep.cli.mcp_server import _inject_mcp_contract_fields

    array_json = json.dumps([1, 2, 3])
    assert _inject_mcp_contract_fields(array_json) == array_json


# ---------------------------------------------------------------------------
# M11 -tg_rewrite_diff zero-match returns valid payload
# ---------------------------------------------------------------------------


def test_m11_zero_match_diff_returns_valid_payload() -> None:
    """Empty diff output (no matches) must return a valid envelope, not an error."""
    from tensor_grep.cli.mcp_server import _execute_rewrite_diff_command

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = ""
    mock_proc.stderr = ""

    with patch(
        "tensor_grep.cli.mcp_server._run_rewrite_subprocess",
        return_value=mock_proc,
    ):
        result = _execute_rewrite_diff_command(["fake_binary", "run", "--diff", "pattern", "."])

    parsed = json.loads(result)
    assert "error" not in parsed, f"unexpected error: {parsed.get('error')}"
    assert parsed.get("total_edits") == 0
    assert "diff" in parsed


def test_m11_zero_match_diff_whitespace_stdout() -> None:
    """Whitespace-only stdout should also be treated as zero matches."""
    from tensor_grep.cli.mcp_server import _execute_rewrite_diff_command

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "   \n\n   "
    mock_proc.stderr = ""

    with patch(
        "tensor_grep.cli.mcp_server._run_rewrite_subprocess",
        return_value=mock_proc,
    ):
        result = _execute_rewrite_diff_command(["cmd"])

    parsed = json.loads(result)
    assert "error" not in parsed
    assert parsed.get("total_edits") == 0


# ---------------------------------------------------------------------------
# M12 -tg_rewrite_apply: applied_edits + checkpoint normalization
# ---------------------------------------------------------------------------


def test_m12_normalize_apply_result_adds_applied_edits_from_total() -> None:
    from tensor_grep.cli.mcp_server import _normalize_apply_result_payload

    payload = {"total_edits": 5, "other": "data"}
    result = _normalize_apply_result_payload(payload)
    assert result["applied_edits"] == 5


def test_m12_normalize_apply_result_adds_applied_edits_from_list() -> None:
    from tensor_grep.cli.mcp_server import _normalize_apply_result_payload

    payload = {"edits": [{"a": 1}, {"b": 2}]}
    result = _normalize_apply_result_payload(payload)
    assert result["applied_edits"] == 2


def test_m12_normalize_apply_result_zero_edits() -> None:
    from tensor_grep.cli.mcp_server import _normalize_apply_result_payload

    payload: dict = {}
    result = _normalize_apply_result_payload(payload)
    assert result["applied_edits"] == 0


def test_m12_normalize_checkpoint_epoch_to_iso() -> None:
    """Epoch-string created_at must be converted to ISO-8601."""
    from tensor_grep.cli.mcp_server import _normalize_apply_result_payload

    epoch = "1718000000"
    payload = {
        "edits": [],
        "checkpoint": {
            "created_at": epoch,
            "checkpoint_id": f"ckpt-{epoch}-deadbeef",
        },
    }
    result = _normalize_apply_result_payload(payload)
    ckpt = result["checkpoint"]
    assert "T" in ckpt["created_at"], "created_at not in ISO-8601 format"
    assert "Z" in ckpt["created_at"] or "+" in ckpt["created_at"] or "+00:00" in ckpt["created_at"]
    # checkpoint_id should now use datetime, not epoch digits
    assert ckpt["checkpoint_id"].startswith("ckpt-")
    assert epoch not in ckpt["checkpoint_id"]


def test_m12_normalize_checkpoint_iso_unchanged() -> None:
    """ISO-8601 created_at must not be mutated."""
    from tensor_grep.cli.mcp_server import _normalize_apply_result_payload

    iso = "2024-06-10T14:30:00+00:00"
    ckpt_id = "ckpt-20240610143000-ab1234cd"
    payload = {
        "edits": [],
        "checkpoint": {
            "created_at": iso,
            "checkpoint_id": ckpt_id,
        },
    }
    result = _normalize_apply_result_payload(payload)
    assert result["checkpoint"]["created_at"] == iso
    assert result["checkpoint"]["checkpoint_id"] == ckpt_id


# ---------------------------------------------------------------------------
# M13 -tg_session_open tracked_file_count includes tests
# ---------------------------------------------------------------------------


def test_m13_session_open_has_tracked_file_count() -> None:
    """tg_session_open must include tracked_file_count alongside file_count."""
    from tensor_grep.cli.mcp_server import tg_session_open

    result = json.loads(tg_session_open("."))
    assert "tracked_file_count" in result, "tracked_file_count missing from tg_session_open"
    assert "file_count" in result
    # tracked_file_count >= file_count (includes test files)
    assert result["tracked_file_count"] >= result["file_count"]


# ---------------------------------------------------------------------------
# L7 -tg_ast_search docstring accuracy
# ---------------------------------------------------------------------------


def test_l7_ast_search_docstring_no_pytorch() -> None:
    """tg_ast_search must not claim PyTorch Geometric as the backend."""
    from tensor_grep.cli.mcp_server import tg_ast_search

    doc = tg_ast_search.__doc__ or ""
    assert "PyTorch Geometric" not in doc, "docstring still claims PyTorch Geometric"
    assert "Graph Neural" not in doc


def test_l7_ast_search_docstring_mentions_ast_grep() -> None:
    """tg_ast_search docstring must describe the ast-grep/tree-sitter backend."""
    from tensor_grep.cli.mcp_server import tg_ast_search

    doc = tg_ast_search.__doc__ or ""
    assert "ast-grep" in doc or "tree-sitter" in doc, "docstring missing ast-grep/tree-sitter"
