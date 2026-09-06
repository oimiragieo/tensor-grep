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
    tg_session_list,
    tg_session_open,
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


def test_incomplete_envelope_present_on_session_open() -> None:
    """P3 gap-fix: tg_session_open previously bypassed the injector entirely
    (bare `json.dumps`), so `incomplete` was absent from its success response."""
    _assert_incomplete_envelope(tg_session_open("."), "tg_session_open")


def test_incomplete_envelope_present_on_session_list() -> None:
    """P3 gap-fix: tg_session_list previously bypassed the injector entirely
    (bare `json.dumps`), so `incomplete` was absent from its success response."""
    _assert_incomplete_envelope(tg_session_list("."), "tg_session_list")


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


def test_incomplete_envelope_derives_from_partial_flag() -> None:
    """tg_query's multi-root aggregate stamps `partial: true` + `omitted_roots` (not
    `result_incomplete` or `truncated`) when the shared deadline truncates the root fan-out.
    Codex Sol delta-verification audit 2026-09-06 CRITICAL finding: unified_incomplete_envelope
    ignored both fields, so a genuinely partial multi-root response reported incomplete.status=False."""
    raw = json.dumps({
        "results_by_root": {"a": {}, "b": {}},
        "omitted_roots": ["c"],
        "partial": True,
    })
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    assert stamped["incomplete"]["status"] is True
    assert stamped["partial"] is True  # legacy field untouched


def test_incomplete_envelope_aggregates_nested_results_by_root() -> None:
    """A tg_query aggregate can have NO top-level partial/omitted_roots (every root answered
    within budget) while one CHILD root's own response is itself incomplete (e.g. that root hit
    its own scan cap). The parent envelope must surface that, not just its own top-level fields."""
    raw = json.dumps({
        "results_by_root": {
            "a": {"incomplete": {"status": False, "cause": None, "budget_remediable": False}},
            "b": {"incomplete": {"status": True, "cause": "scan_limit", "budget_remediable": True}},
        },
    })
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    assert stamped["incomplete"]["status"] is True


def test_incomplete_envelope_derives_status_from_unreadable_paths() -> None:
    """Round-3 Codex Sol final-verification audit finding: build_repo_map stamps a top-level
    `unreadable_paths: {count, sample}` key (repo_map.py, deliberately separate from scan_limit
    since "raise the budget" is the wrong remedy for a permission-denied directory) with NO
    accompanying result_incomplete/truncated/partial sibling. tg_repo_map injects that dict
    verbatim, so a repo walk that hit an unreadable path reported incomplete.status=False."""
    raw = json.dumps({
        "unreadable_paths": {"count": 2, "sample": ["/root/locked"]},
    })
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    assert stamped["incomplete"]["status"] is True
    assert stamped["incomplete"]["cause"] == "unreadable_path"
    # Not budget-remediable -- raising a scan cap does not fix a permission-denied path.
    assert stamped["incomplete"]["budget_remediable"] is False


def test_incomplete_envelope_derives_status_from_scan_limit_possibly_truncated_alone() -> None:
    """Round-3 Codex Sol final-verification audit finding: status was derived from
    result_incomplete/truncated/partial/nested children, but NEVER from
    scan_limit.possibly_truncated itself -- it only read scan_limit's cause/remediable fields
    AFTER status was already true from something else. tg_repo_map injects build_repo_map's
    raw dict verbatim (mcp_server.py tg_repo_map), and build_repo_map can emit a payload with
    ONLY nested scan_limit.possibly_truncated=True and no top-level truncated/partial/
    result_incomplete sibling -- exactly the production shape this test reproduces without the
    masking top-level truncated=True the prior (now-fixed) test accidentally included."""
    raw = json.dumps({
        "scan_limit": {
            "max_repo_files": 1000,
            "scanned_files": 1000,
            "possibly_truncated": True,
            "truncation_cause": "project-files",
            "budget_remediable": True,
        },
    })
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    assert stamped["incomplete"]["status"] is True, (
        f"scan_limit.possibly_truncated=True must drive status=True on its own: {stamped}"
    )
    assert stamped["incomplete"]["cause"] == "project-files"
    assert stamped["incomplete"]["budget_remediable"] is True


def test_incomplete_envelope_prefers_scan_limit_truncation_cause_over_generic_truncated() -> None:
    """Codex Sol delta-verification audit 2026-09-06 HIGH finding: scan_limit.truncation_cause
    (e.g. "scan_limit", "unreadable_path", "unknown" -- a fail-closed allowlist per AGENTS.md)
    and scan_limit.budget_remediable are the ACTIONABLE cause/remediation signal, but the unified
    envelope only ever fell back to a generic "truncated" cause and never read them, degrading
    an actionable cause into a useless one and always reporting non-remediable."""
    raw = json.dumps({
        "truncated": True,
        "scan_limit": {
            "max_repo_files": 1000,
            "scanned_files": 1000,
            "possibly_truncated": True,
            "truncation_cause": "unreadable_path",
            "budget_remediable": False,
        },
    })
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    assert stamped["incomplete"]["status"] is True
    assert stamped["incomplete"]["cause"] == "unreadable_path"
    assert stamped["incomplete"]["budget_remediable"] is False


def test_incomplete_envelope_surfaces_scan_limit_budget_remediable_true() -> None:
    """The other half of the same gap: a budget-cap truncation (scan_limit) IS remediable by
    raising max_repo_files, but the envelope's own budget_remediable never read scan_limit's
    more specific value -- it only ever checked the top-level (usually-absent) field."""
    raw = json.dumps({
        "truncated": True,
        "scan_limit": {
            "max_repo_files": 1000,
            "scanned_files": 1000,
            "possibly_truncated": True,
            "truncation_cause": "scan_limit",
            "budget_remediable": True,
        },
    })
    stamped = json.loads(_inject_mcp_contract_fields(raw))
    assert stamped["incomplete"]["cause"] == "scan_limit"
    assert stamped["incomplete"]["budget_remediable"] is True


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
