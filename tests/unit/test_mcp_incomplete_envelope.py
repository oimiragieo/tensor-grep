"""P3 -- unified `incomplete` envelope across every MCP tool response.

Adds a consistent additive top-level field to every tool JSON envelope:

    "incomplete": {"status": bool, "cause": str | None, "budget_remediable": bool}

so a client can check ONE field regardless of which tool it called, instead of
knowing each tool's surface-specific incompleteness vocabulary
(`result_incomplete` / `incomplete_reason` / `truncation_cause` / ...).

This must be purely additive: existing `result_incomplete`, `incomplete_reason`,
`incomplete_reason_class`, `truncation_cause`, and `budget_remediable` fields must
be left untouched (zero regressions on the existing partial-results contract
documented in the tensor-grep-architecture-contract skill).
"""

import json

from tensor_grep.cli.mcp_server import (
    _TG_MCP_SERVER_CONTRACT_VERSION,
    _inject_mcp_contract_fields,
    tg_mcp_capabilities,
    tg_repo_map,
)


def _assert_incomplete_envelope(result: str, tool_name: str) -> dict:
    parsed = json.loads(result)
    assert isinstance(parsed, dict), f"{tool_name}: response is not a dict"
    assert "incomplete" in parsed, f"{tool_name}: incomplete envelope missing; keys={list(parsed)}"
    envelope = parsed["incomplete"]
    assert isinstance(envelope, dict), f"{tool_name}: incomplete must be an object"
    assert set(envelope) == {"status", "cause", "budget_remediable"}, (
        f"{tool_name}: unexpected incomplete envelope keys={list(envelope)}"
    )
    assert isinstance(envelope["status"], bool)
    assert envelope["cause"] is None or isinstance(envelope["cause"], str)
    assert isinstance(envelope["budget_remediable"], bool)
    return parsed


def test_incomplete_envelope_present_on_normally_complete_tool() -> None:
    """A tool with no incompleteness concept still gets the default-false envelope."""
    parsed = _assert_incomplete_envelope(tg_mcp_capabilities(), "tg_mcp_capabilities")
    assert parsed["incomplete"] == {
        "status": False,
        "cause": None,
        "budget_remediable": False,
    }


def test_incomplete_envelope_present_on_repo_map() -> None:
    _assert_incomplete_envelope(tg_repo_map("."), "tg_repo_map")


def test_incomplete_envelope_derives_from_result_incomplete_true() -> None:
    """When a payload already carries `result_incomplete: true`, the unified
    envelope must reflect status=True and surface `incomplete_reason` as cause,
    while leaving the legacy fields byte-identical."""
    raw = json.dumps({
        "result_incomplete": True,
        "incomplete_reason": "scan hit max_repo_files",
        "incomplete_reason_class": "scan_limit",
    })
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    # Legacy fields untouched.
    assert stamped["result_incomplete"] is True
    assert stamped["incomplete_reason"] == "scan hit max_repo_files"
    assert stamped["incomplete_reason_class"] == "scan_limit"
    # New unified envelope derived from them.
    assert stamped["incomplete"]["status"] is True
    assert stamped["incomplete"]["cause"] == "scan hit max_repo_files"


def test_incomplete_envelope_derives_from_truncated_when_result_incomplete_absent() -> None:
    """A tool can signal incompleteness via `truncated: true` (a scan-capped result) WITHOUT
    also setting `result_incomplete` -- e.g. tg_search's no-match envelope stamps both
    `truncated` (from a separate scan_capped derivation) and `result_incomplete` from a
    DIFFERENT source (all_results.result_incomplete), and the two can disagree. Codex Sol audit
    2026-09-06 CRITICAL finding: keying unified_incomplete_envelope on result_incomplete alone
    silently reports a genuinely truncated/capped response as incomplete.status=False."""
    raw = json.dumps({
        "result_incomplete": False,
        "truncated": True,
        "omitted_matches": 12,
    })
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    assert stamped["incomplete"]["status"] is True
    assert stamped["truncated"] is True  # legacy field untouched


def test_incomplete_envelope_derives_budget_remediable() -> None:
    raw = json.dumps({
        "result_incomplete": True,
        "incomplete_reason": "hit scan_limit",
        "budget_remediable": True,
    })
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    assert stamped["incomplete"]["status"] is True
    assert stamped["incomplete"]["budget_remediable"] is True


def test_incomplete_envelope_defaults_false_when_no_signal() -> None:
    raw = json.dumps({"pattern": "foo", "matches": []})
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    assert stamped["incomplete"] == {
        "status": False,
        "cause": None,
        "budget_remediable": False,
    }


def test_incomplete_envelope_does_not_overwrite_existing_key() -> None:
    """If a payload already set its own `incomplete` dict (future tool), the
    injector must not clobber an explicitly-set True status with a false default."""
    raw = json.dumps({"incomplete": {"status": True, "cause": "x", "budget_remediable": True}})
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    assert stamped["incomplete"] == {"status": True, "cause": "x", "budget_remediable": True}


def test_incomplete_envelope_non_dict_passthrough() -> None:
    array_json = json.dumps([1, 2, 3])
    assert _inject_mcp_contract_fields(array_json) == array_json


def test_contract_version_bumped_for_incomplete_envelope() -> None:
    """This is an additive top-level field change; the contract version must
    have moved past 1.7.0 (the version this envelope was introduced against)."""
    major, minor, _patch = (int(x) for x in _TG_MCP_SERVER_CONTRACT_VERSION.split("."))
    assert (major, minor) >= (1, 8), (
        f"_TG_MCP_SERVER_CONTRACT_VERSION={_TG_MCP_SERVER_CONTRACT_VERSION} was not bumped"
    )
