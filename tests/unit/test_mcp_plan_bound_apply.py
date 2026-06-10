"""Tests for plan-bound apply / TOCTOU hardening and the richer rewrite error codes.

Covers audit findings:
  A1 - tg_rewrite_plan emits a stable ``plan_digest``; tg_rewrite_apply refuses
       to write when an optional ``expected_plan_digest`` / ``expected_match_count``
       no longer matches the current tree (code="plan_drift"), while staying fully
       back-compatible when the expectation parameters are omitted.
  A2 - native rewrite failures map to distinct codes (pattern_error / io_error /
       native_internal_error) with a ``retryable`` hint instead of collapsing to
       ``invalid_input``.
  A4 - every tool JSON envelope embeds ``mcp_contract_version``.

These tests mock the native subprocess / embedded engine so the digest, drift, and
error-classification logic runs without the compiled rust_core extension. The few
checks that need the real embedded engine are marked and skipped when it is absent
(they still run in CI, which builds the extension).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from tensor_grep.cli import mcp_server


def _sample_plan_payload() -> dict[str, object]:
    return {
        "version": 1,
        "mcp_contract_version": mcp_server._TG_MCP_SERVER_CONTRACT_VERSION,
        "schema_version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "pattern": "def $F(): pass",
        "replacement": "def $F(): ...",
        "lang": "python",
        "total_files_scanned": 1,
        "total_edits": 1,
        "edits": [
            {
                "id": "e0000:a.py:0-13",
                "file": "C:/tmp/a.py",
                "planned_mtime_ns": 1,
                "line": 1,
                "byte_range": {"start": 0, "end": 13},
                "original_text": "def f(): pass",
                "replacement_text": "def f(): ...",
                "metavar_env": {"F": "f"},
            }
        ],
    }


# ---------------------------------------------------------------------------
# A1 - plan_digest stability and sensitivity
# ---------------------------------------------------------------------------


def test_plan_digest_is_stable_for_unchanged_tree() -> None:
    plan = _sample_plan_payload()
    first = mcp_server._compute_plan_digest(plan)
    second = mcp_server._compute_plan_digest(copy.deepcopy(plan))

    assert isinstance(first, str)
    assert first == second


def test_plan_digest_changes_when_target_file_content_changes() -> None:
    plan = _sample_plan_payload()
    baseline = mcp_server._compute_plan_digest(plan)

    drifted = copy.deepcopy(plan)
    # Simulate the pre-image text at the edit site changing in the tree.
    drifted["edits"][0]["original_text"] = "def f():    pass  # edited"
    drifted_digest = mcp_server._compute_plan_digest(drifted)

    assert drifted_digest is not None
    assert drifted_digest != baseline


def test_plan_digest_changes_when_byte_range_changes() -> None:
    plan = _sample_plan_payload()
    baseline = mcp_server._compute_plan_digest(plan)

    drifted = copy.deepcopy(plan)
    drifted["edits"][0]["byte_range"]["end"] = 999
    assert mcp_server._compute_plan_digest(drifted) != baseline


def test_plan_digest_changes_when_edit_set_changes() -> None:
    plan = _sample_plan_payload()
    baseline = mcp_server._compute_plan_digest(plan)

    drifted = copy.deepcopy(plan)
    drifted["edits"].append({
        "id": "e0001:b.py:0-4",
        "file": "C:/tmp/b.py",
        "byte_range": {"start": 0, "end": 4},
        "original_text": "pass",
        "replacement_text": "...",
    })
    assert mcp_server._compute_plan_digest(drifted) != baseline


def test_plan_digest_is_insensitive_to_edit_ordering() -> None:
    plan = _sample_plan_payload()
    plan["edits"].append({
        "id": "e0001:b.py:0-4",
        "file": "C:/tmp/b.py",
        "byte_range": {"start": 0, "end": 4},
        "original_text": "pass",
        "replacement_text": "...",
    })
    reordered = copy.deepcopy(plan)
    reordered["edits"].reverse()

    assert mcp_server._compute_plan_digest(plan) == mcp_server._compute_plan_digest(reordered)


def test_plan_digest_normalizes_whitespace_and_language_case() -> None:
    plan = _sample_plan_payload()
    baseline = mcp_server._compute_plan_digest(plan)

    normalized = copy.deepcopy(plan)
    normalized["pattern"] = "  def $F(): pass  "
    normalized["replacement"] = "\tdef $F(): ...\n"
    normalized["lang"] = "PYTHON"

    assert mcp_server._compute_plan_digest(normalized) == baseline


def test_plan_digest_none_for_errors_and_unparseable_plans() -> None:
    assert mcp_server._compute_plan_digest({"error": {"code": "invalid_input"}}) is None
    assert mcp_server._compute_plan_digest({"pattern": "x", "edits": "not-a-list"}) is None
    assert mcp_server._compute_plan_digest("not a dict") is None


def test_execute_rewrite_plan_json_stamps_digest_and_match_count() -> None:
    plan = _sample_plan_payload()

    with (
        patch.object(mcp_server, "_validate_rewrite_inputs", return_value=None),
        patch.object(mcp_server, "_produce_rewrite_plan_json", return_value=json.dumps(plan)),
    ):
        out, exit_code = mcp_server.execute_rewrite_plan_json(
            pattern="def $F(): pass",
            replacement="def $F(): ...",
            lang="python",
            path=".",
        )

    parsed = json.loads(out)
    assert exit_code == 0
    assert parsed["plan_digest"] == mcp_server._compute_plan_digest(plan)
    assert parsed["match_count"] == 1
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION


def test_execute_rewrite_plan_json_does_not_stamp_digest_on_error() -> None:
    error_json = mcp_server._rewrite_error("boom", code="pattern_error", retryable=False)

    with (
        patch.object(mcp_server, "_validate_rewrite_inputs", return_value=None),
        patch.object(mcp_server, "_produce_rewrite_plan_json", return_value=error_json),
    ):
        out, exit_code = mcp_server.execute_rewrite_plan_json(
            pattern="def $F(): pass",
            replacement="def $F(): ...",
            lang="python",
            path=".",
        )

    parsed = json.loads(out)
    assert exit_code == 1
    assert "plan_digest" not in parsed
    assert parsed["error"]["code"] == "pattern_error"


# ---------------------------------------------------------------------------
# A1 - plan-bound apply (drift detection)
# ---------------------------------------------------------------------------


def _patched_apply_env(plan_payload: dict[str, object], apply_result: str):
    """Patch the apply path to use a fixed plan and a recording apply executor."""
    return (
        patch.object(mcp_server, "_validate_rewrite_inputs", return_value=None),
        patch.object(
            mcp_server, "_produce_rewrite_plan_json", return_value=json.dumps(plan_payload)
        ),
        patch.object(mcp_server, "_resolve_native_tg_binary_for_mcp", return_value=(None, None)),
        patch.object(mcp_server, "_embedded_rewrite_available", return_value=True),
        patch.object(mcp_server, "_execute_embedded_rewrite_json", return_value=apply_result),
    )


def _apply_success_json() -> str:
    return json.dumps({
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "plan": {"total_edits": 1},
        "applied": 1,
    })


def test_apply_with_matching_digest_proceeds() -> None:
    plan = _sample_plan_payload()
    digest = mcp_server._compute_plan_digest(plan)
    patches = _patched_apply_env(plan, _apply_success_json())

    with patches[0], patches[1], patches[2], patches[3], patches[4] as apply_mock:
        out, exit_code = mcp_server.execute_rewrite_apply_json(
            pattern="def $F(): pass",
            replacement="def $F(): ...",
            lang="python",
            path=".",
            expected_plan_digest=digest,
        )

    parsed = json.loads(out)
    assert exit_code == 0
    assert "error" not in parsed
    assert apply_mock.called  # the apply was actually executed


def test_apply_with_stale_digest_returns_plan_drift_and_does_not_apply() -> None:
    plan = _sample_plan_payload()
    patches = _patched_apply_env(plan, _apply_success_json())

    with patches[0], patches[1], patches[2], patches[3], patches[4] as apply_mock:
        out, exit_code = mcp_server.execute_rewrite_apply_json(
            pattern="def $F(): pass",
            replacement="def $F(): ...",
            lang="python",
            path=".",
            expected_plan_digest="deadbeef" * 8,
        )

    parsed = json.loads(out)
    assert exit_code == 1
    assert parsed["error"]["code"] == "plan_drift"
    assert parsed["error"]["retryable"] is False
    assert parsed["error"]["details"][0]["reason"] == "digest_mismatch"
    assert parsed["error"]["details"][0]["expected_plan_digest"] == "deadbeef" * 8
    assert parsed["error"]["details"][0]["actual_plan_digest"] == mcp_server._compute_plan_digest(
        plan
    )
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # The native apply executor must never have been reached: no files modified.
    assert not apply_mock.called


def test_apply_with_mismatched_match_count_returns_plan_drift_and_does_not_apply() -> None:
    plan = _sample_plan_payload()
    patches = _patched_apply_env(plan, _apply_success_json())

    with patches[0], patches[1], patches[2], patches[3], patches[4] as apply_mock:
        out, exit_code = mcp_server.execute_rewrite_apply_json(
            pattern="def $F(): pass",
            replacement="def $F(): ...",
            lang="python",
            path=".",
            expected_match_count=7,
        )

    parsed = json.loads(out)
    assert exit_code == 1
    assert parsed["error"]["code"] == "plan_drift"
    assert parsed["error"]["details"][0]["reason"] == "match_count_mismatch"
    assert parsed["error"]["details"][0]["expected_match_count"] == "7"
    assert parsed["error"]["details"][0]["actual_match_count"] == "1"
    assert not apply_mock.called


def test_apply_refuses_when_plan_recompute_is_unavailable() -> None:
    # The live re-plan fails (e.g. native unavailable); we cannot confirm the tree
    # still matches, so the apply must be refused rather than written blindly.
    plan_error = mcp_server._native_unavailable_error(
        tool="tg_rewrite_plan",
        payload=mcp_server._rewrite_envelope(),
    )
    patches = _patched_apply_env(_sample_plan_payload(), _apply_success_json())

    with (
        patches[0],
        patch.object(mcp_server, "_produce_rewrite_plan_json", return_value=plan_error),
        patches[2],
        patches[3],
        patches[4] as apply_mock,
    ):
        out, exit_code = mcp_server.execute_rewrite_apply_json(
            pattern="def $F(): pass",
            replacement="def $F(): ...",
            lang="python",
            path=".",
            expected_plan_digest="a" * 64,
        )

    parsed = json.loads(out)
    assert exit_code == 1
    assert parsed["error"]["code"] == "plan_drift"
    assert parsed["error"]["retryable"] is True
    assert parsed["error"]["details"][0]["reason"] == "plan_unavailable"
    assert not apply_mock.called


def test_apply_without_expectation_params_is_backcompat() -> None:
    plan = _sample_plan_payload()
    patches = _patched_apply_env(plan, _apply_success_json())

    with (
        patches[0],
        patch.object(
            mcp_server, "_produce_rewrite_plan_json", return_value=json.dumps(plan)
        ) as plan_mock,
        patches[2],
        patches[3],
        patches[4] as apply_mock,
    ):
        out, exit_code = mcp_server.execute_rewrite_apply_json(
            pattern="def $F(): pass",
            replacement="def $F(): ...",
            lang="python",
            path=".",
        )

    parsed = json.loads(out)
    assert exit_code == 0
    assert "error" not in parsed
    assert apply_mock.called
    # No expectation parameters -> the plan must NOT be recomputed (no extra work,
    # identical behavior to the historical apply path).
    assert not plan_mock.called


def test_tg_rewrite_apply_tool_exposes_optional_expectation_params() -> None:
    import inspect

    signature = inspect.signature(mcp_server.tg_rewrite_apply)
    assert "expected_plan_digest" in signature.parameters
    assert "expected_match_count" in signature.parameters
    # Both must default to None so existing callers are unaffected.
    assert signature.parameters["expected_plan_digest"].default is None
    assert signature.parameters["expected_match_count"].default is None


# ---------------------------------------------------------------------------
# A2 - distinct native error codes + retryable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stderr", "expected_code", "expected_retryable"),
    [
        ("error: invalid pattern: cannot parse", "pattern_error", False),
        ("Error: failed to parse rewrite template", "pattern_error", False),
        ("unsupported language: cobol", "pattern_error", False),
        ("Error: No such file or directory (os error 2)", "io_error", True),
        ("permission denied while writing file", "io_error", True),
        ("thread 'main' panicked at src/backend_ast.rs:42", "native_internal_error", True),
        ("internal error: index out of bounds", "native_internal_error", True),
        ("some entirely unrecognized failure text", "invalid_input", False),
    ],
)
def test_classify_native_rewrite_failure(
    stderr: str, expected_code: str, expected_retryable: bool
) -> None:
    code, retryable = mcp_server._classify_native_rewrite_failure(stderr, returncode=1)
    assert code == expected_code
    assert retryable is expected_retryable


def test_execute_rewrite_json_command_maps_pattern_error() -> None:
    completed = CompletedProcess(
        args=["tg.exe"],
        returncode=2,
        stdout="",
        stderr="error: invalid pattern: unexpected token",
    )
    with patch.object(mcp_server, "_run_rewrite_subprocess", return_value=completed):
        out = mcp_server._execute_rewrite_json_command(["tg.exe"])

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "pattern_error"
    assert parsed["error"]["retryable"] is False
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION


def test_execute_rewrite_json_command_maps_io_error_as_retryable() -> None:
    completed = CompletedProcess(
        args=["tg.exe"],
        returncode=1,
        stdout="",
        stderr="Error: Permission denied (os error 13)",
    )
    with patch.object(mcp_server, "_run_rewrite_subprocess", return_value=completed):
        out = mcp_server._execute_rewrite_json_command(["tg.exe"])

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "io_error"
    assert parsed["error"]["retryable"] is True


def test_execute_rewrite_json_command_maps_internal_panic_as_retryable() -> None:
    completed = CompletedProcess(
        args=["tg.exe"],
        returncode=101,
        stdout="",
        stderr="thread 'main' panicked at 'unwrap on None', src/main.rs:7",
    )
    with patch.object(mcp_server, "_run_rewrite_subprocess", return_value=completed):
        out = mcp_server._execute_rewrite_json_command(["tg.exe"])

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "native_internal_error"
    assert parsed["error"]["retryable"] is True


def test_execute_rewrite_json_command_unavailable_is_retryable() -> None:
    with patch.object(
        mcp_server, "_run_rewrite_subprocess", side_effect=FileNotFoundError("tg.exe missing")
    ):
        out = mcp_server._execute_rewrite_json_command(["tg.exe"])

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "unavailable"
    assert parsed["error"]["retryable"] is True


def test_unrecognized_failure_preserves_invalid_input_code() -> None:
    # Back-compat: callers may key on the historical "invalid_input" code for
    # unrecognized non-zero exits, so it must remain the default.
    completed = CompletedProcess(
        args=["tg.exe"],
        returncode=3,
        stdout="",
        stderr="weird unexpected message with no known signature",
    )
    with patch.object(mcp_server, "_run_rewrite_subprocess", return_value=completed):
        out = mcp_server._execute_rewrite_json_command(["tg.exe"])

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_input"
    assert parsed["error"]["retryable"] is False


# ---------------------------------------------------------------------------
# A4 - mcp_contract_version on every envelope builder
# ---------------------------------------------------------------------------


def test_rewrite_envelope_carries_contract_version() -> None:
    envelope = mcp_server._rewrite_envelope()
    assert envelope["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # The data-shape version is still present and independent.
    assert "version" in envelope
    assert "schema_version" in envelope


def test_error_envelopes_carry_contract_version() -> None:
    builders = [
        mcp_server._rewrite_error("x", code="invalid_input"),
        mcp_server._index_search_error("x", code="unavailable", pattern="p", path="."),
        mcp_server._audit_manifest_error("x", code="not_found"),
        mcp_server._audit_history_error("x", code="not_found"),
        mcp_server._audit_diff_error("x", code="not_found"),
        mcp_server._review_bundle_error("x", code="invalid_input", routing_reason="rb"),
        mcp_server._ruleset_scan_error("x", code="invalid_input", ruleset="r", path="."),
        mcp_server._agent_capsule_error("x", code="invalid_input", query="q", path="."),
        mcp_server._session_error_payload(session_id="s", path=".", code="c", message="m"),
        mcp_server._session_exception_payload(path=".", message="m"),
    ]
    for raw in builders:
        parsed = json.loads(raw)
        assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION


def test_capabilities_payload_carries_contract_version() -> None:
    payload = mcp_server._mcp_capabilities_payload()
    assert payload["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION
    # cli_version (package version) stays distinct from the contract version.
    assert "cli_version" in payload


def test_normalized_rewrite_payload_carries_contract_version() -> None:
    raw = mcp_server._normalize_rewrite_json_payload({"total_edits": 0, "edits": []})
    parsed = json.loads(raw)
    assert parsed["mcp_contract_version"] == mcp_server._TG_MCP_SERVER_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# A1 - full plan -> apply loop against the real embedded engine (CI only)
# ---------------------------------------------------------------------------


def _embedded_engine_available() -> bool:
    try:
        from tensor_grep.rust_core import (  # noqa: F401
            ast_rewrite_apply_json,
            ast_rewrite_plan_json,
        )
    except Exception:
        return False
    return True


@pytest.mark.skipif(
    not _embedded_engine_available(),
    reason="requires the compiled rust_core embedded rewrite engine",
)
def test_real_plan_digest_binds_apply_to_current_tree(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def add(x, y): return x + y\n", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)

    plan_json, plan_exit = mcp_server.execute_rewrite_plan_json(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path=str(source),
    )
    plan = json.loads(plan_json)
    assert plan_exit == 0
    digest = plan["plan_digest"]
    assert isinstance(digest, str) and digest

    # Mutate the tree so the previously-reviewed plan no longer matches.
    source.write_text("def add(x, y):\n    return x - y\n", encoding="utf-8")

    out, exit_code = mcp_server.execute_rewrite_apply_json(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path=str(source),
        expected_plan_digest=digest,
    )
    parsed = json.loads(out)

    assert exit_code == 1
    assert parsed["error"]["code"] == "plan_drift"
    # The file must be untouched by the refused apply.
    assert source.read_text(encoding="utf-8") == "def add(x, y):\n    return x - y\n"


@pytest.mark.skipif(
    not _embedded_engine_available(),
    reason="requires the compiled rust_core embedded rewrite engine",
)
def test_real_plan_digest_allows_apply_on_unchanged_tree(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def add(x, y): return x + y\n", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "resolve_native_tg_binary", lambda: None)

    plan_json, _ = mcp_server.execute_rewrite_plan_json(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path=str(source),
    )
    digest = json.loads(plan_json)["plan_digest"]

    out, exit_code = mcp_server.execute_rewrite_apply_json(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path=str(source),
        expected_plan_digest=digest,
    )
    parsed = json.loads(out)

    assert exit_code == 0
    assert "error" not in parsed
    assert source.read_text(encoding="utf-8") == "lambda x, y: x + y\n"
