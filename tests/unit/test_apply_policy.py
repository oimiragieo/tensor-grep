from __future__ import annotations

import json
from pathlib import Path

import pytest

from tensor_grep.cli.checkpoint_store import create_checkpoint


def _write_policy(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "apply-policy.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _base_rewrite_payload(*, checkpoint: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "checkpoint": checkpoint,
        "plan": {"total_edits": 1},
        "verification": {"total_edits": 1, "verified": 1, "mismatches": []},
        "validation": None,
    }


def _scan_baseline_payload(scan_root: Path) -> dict[str, object]:
    from tensor_grep.cli.main import _run_ast_scan_payload
    from tensor_grep.cli.rule_packs import resolve_rule_pack

    _meta, rules = resolve_rule_pack("auth-safe", "python")
    return _run_ast_scan_payload(
        {
            "config_path": "builtin:auth-safe",
            "root_dir": scan_root,
            "rule_dirs": [],
            "test_dirs": [],
            "language": "python",
        },
        rules,
        routing_reason="builtin-ruleset-scan",
        ruleset_name="auth-safe",
    )


def _write_baseline(path: Path, scan_root: Path) -> None:
    payload = _scan_baseline_payload(scan_root)
    fingerprints = sorted(
        finding["fingerprint"] for finding in payload["findings"] if int(finding["matches"]) > 0
    )
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "ruleset-scan-baseline",
                "ruleset": "auth-safe",
                "language": "python",
                "fingerprints": fingerprints,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (
            {
                "lint_cmd": None,
                "test_cmd": None,
                "ruleset_scan": None,
                "on_failure": "warn",
            },
            "version",
        ),
        (
            {
                "version": 1,
                "test_cmd": None,
                "ruleset_scan": None,
                "on_failure": "warn",
            },
            "lint_cmd",
        ),
        (
            {
                "version": 1,
                "lint_cmd": None,
                "ruleset_scan": None,
                "on_failure": "warn",
            },
            "test_cmd",
        ),
        (
            {
                "version": 1,
                "lint_cmd": None,
                "test_cmd": None,
                "on_failure": "warn",
            },
            "ruleset_scan",
        ),
        (
            {
                "version": 1,
                "lint_cmd": None,
                "test_cmd": None,
                "ruleset_scan": None,
            },
            "on_failure",
        ),
    ],
)
def test_load_apply_policy_rejects_missing_required_keys(
    tmp_path: Path, payload: dict[str, object], field: str
) -> None:
    from tensor_grep.cli.apply_policy import PolicyValidationError, load_apply_policy

    policy_path = _write_policy(tmp_path, payload)

    with pytest.raises(PolicyValidationError) as excinfo:
        load_apply_policy(str(policy_path))

    assert any(detail["field"] == field for detail in excinfo.value.details)


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        (
            {
                "version": 2,
                "lint_cmd": None,
                "test_cmd": None,
                "ruleset_scan": None,
                "on_failure": "warn",
            },
            "version",
        ),
        (
            {
                "version": 1,
                "lint_cmd": 123,
                "test_cmd": None,
                "ruleset_scan": None,
                "on_failure": "warn",
            },
            "lint_cmd",
        ),
        (
            {
                "version": 1,
                "lint_cmd": None,
                "test_cmd": 123,
                "ruleset_scan": None,
                "on_failure": "warn",
            },
            "test_cmd",
        ),
        (
            {
                "version": 1,
                "lint_cmd": None,
                "test_cmd": None,
                "ruleset_scan": None,
                "on_failure": "explode",
            },
            "on_failure",
        ),
        (
            {
                "version": 1,
                "lint_cmd": None,
                "test_cmd": None,
                "ruleset_scan": None,
                "on_failure": "warn",
                "timeout": 0,
            },
            "timeout",
        ),
        (
            {
                "version": 1,
                "lint_cmd": None,
                "test_cmd": None,
                "ruleset_scan": [],
                "on_failure": "warn",
            },
            "ruleset_scan",
        ),
    ],
)
def test_load_apply_policy_rejects_invalid_field_types(
    tmp_path: Path, payload: dict[str, object], field: str
) -> None:
    from tensor_grep.cli.apply_policy import PolicyValidationError, load_apply_policy

    policy_path = _write_policy(tmp_path, payload)

    with pytest.raises(PolicyValidationError) as excinfo:
        load_apply_policy(str(policy_path))

    assert any(detail["field"] == field for detail in excinfo.value.details)


def test_load_apply_policy_defaults_timeout_to_120(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import load_apply_policy

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "warn",
        },
    )

    policy = load_apply_policy(str(policy_path))

    assert policy.timeout == 120


def test_load_apply_policy_rejects_enabled_ruleset_scan_without_pack(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import PolicyValidationError, load_apply_policy

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": {"enabled": True, "language": "python", "baseline": None},
            "on_failure": "warn",
        },
    )

    with pytest.raises(PolicyValidationError) as excinfo:
        load_apply_policy(str(policy_path))

    assert any(detail["field"] == "ruleset_scan.pack" for detail in excinfo.value.details)


def test_load_apply_policy_rejects_enabled_ruleset_scan_without_language(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import PolicyValidationError, load_apply_policy

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": {"enabled": True, "pack": "auth-safe", "baseline": None},
            "on_failure": "warn",
        },
    )

    with pytest.raises(PolicyValidationError) as excinfo:
        load_apply_policy(str(policy_path))

    assert any(detail["field"] == "ruleset_scan.language" for detail in excinfo.value.details)


def test_load_apply_policy_resolves_relative_baseline_against_policy_file(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import load_apply_policy

    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({
            "version": 1,
            "kind": "ruleset-scan-baseline",
            "ruleset": "auth-safe",
            "language": "python",
            "fingerprints": [],
        }),
        encoding="utf-8",
    )
    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": {
                "enabled": True,
                "pack": "auth-safe",
                "language": "python",
                "baseline": "baseline.json",
            },
            "on_failure": "warn",
        },
    )

    policy = load_apply_policy(str(policy_path))

    assert policy.ruleset_scan is not None
    assert policy.ruleset_scan.baseline == str(baseline_path.resolve())


def test_evaluate_apply_policy_reports_success_for_all_null_checks(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "warn",
        },
    )
    policy = load_apply_policy(str(policy_path))

    payload, exit_code = evaluate_apply_policy(
        _base_rewrite_payload(),
        policy,
        path=str(tmp_path),
    )

    assert exit_code == 0
    assert payload["policy_result"]["checks"] == []
    assert payload["policy_result"]["all_passed"] is True
    assert payload["policy_result"]["action_taken"] == "none"


def test_evaluate_apply_policy_skips_disabled_ruleset_scan(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": {"enabled": False, "pack": "auth-safe", "language": "python"},
            "on_failure": "warn",
        },
    )
    policy = load_apply_policy(str(policy_path))

    payload, exit_code = evaluate_apply_policy(
        _base_rewrite_payload(),
        policy,
        path=str(tmp_path),
    )

    assert exit_code == 0
    assert payload["policy_result"]["checks"] == []
    assert "scan_result" not in payload


def test_evaluate_apply_policy_warn_mode_runs_all_checks_in_order(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    calls: list[str] = []
    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": "lint",
            "test_cmd": "test",
            "ruleset_scan": {"enabled": True, "pack": "auth-safe", "language": "python"},
            "on_failure": "warn",
            "timeout": 9,
        },
    )
    policy = load_apply_policy(str(policy_path))

    def fake_run(name: str, command: str, cwd: Path, timeout: int) -> dict[str, object]:
        calls.append(name)
        assert cwd == tmp_path.resolve()
        assert timeout == 9
        return {
            "passed": name == "test",
            "detail": f"{command} completed",
            "exit_code": 0 if name == "test" else 3,
        }

    def fake_scan(_scan_policy, target_path: Path, working_root: Path) -> dict[str, object]:
        calls.append("scan")
        assert target_path == tmp_path.resolve()
        assert working_root == tmp_path.resolve()
        return {"passed": False, "detail": "1 new finding", "new_findings": 1}

    payload, exit_code = evaluate_apply_policy(
        _base_rewrite_payload(),
        policy,
        path=str(tmp_path),
        run_command_fn=fake_run,
        scan_runner_fn=fake_scan,
    )

    assert calls == ["lint", "test", "scan"]
    assert exit_code == 0
    assert payload["policy_result"]["all_passed"] is False
    assert payload["policy_result"]["action_taken"] == "warn"
    assert payload["lint_result"]["passed"] is False
    assert payload["test_result"]["passed"] is True
    assert payload["scan_result"]["new_findings"] == 1


def test_evaluate_apply_policy_fail_mode_stops_at_first_failure(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    calls: list[str] = []
    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": "lint",
            "test_cmd": "test",
            "ruleset_scan": {"enabled": True, "pack": "auth-safe", "language": "python"},
            "on_failure": "fail",
        },
    )
    policy = load_apply_policy(str(policy_path))

    def fake_run(name: str, _command: str, _cwd: Path, _timeout: int) -> dict[str, object]:
        calls.append(name)
        return {"passed": False, "detail": f"{name} failed", "exit_code": 5}

    def fake_scan(_scan_policy, _target_path: Path, _working_root: Path) -> dict[str, object]:
        calls.append("scan")
        return {"passed": True, "detail": "ok", "new_findings": 0}

    payload, exit_code = evaluate_apply_policy(
        _base_rewrite_payload(),
        policy,
        path=str(tmp_path),
        run_command_fn=fake_run,
        scan_runner_fn=fake_scan,
    )

    assert calls == ["lint"]
    assert exit_code == 1
    assert payload["policy_result"]["checks"] == [
        {"name": "lint", "passed": False, "detail": "lint failed", "exit_code": 5}
    ]
    assert payload["policy_result"]["action_taken"] == "fail"
    assert "test_result" not in payload
    assert "scan_result" not in payload


def test_evaluate_apply_policy_marks_timed_out_command_as_failure(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": "lint",
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "fail",
            "timeout": 1,
        },
    )
    policy = load_apply_policy(str(policy_path))

    def fake_run(_name: str, _command: str, _cwd: Path, timeout: int) -> dict[str, object]:
        assert timeout == 1
        return {
            "passed": False,
            "detail": "Command timed out after 1s.",
            "timed_out": True,
        }

    payload, exit_code = evaluate_apply_policy(
        _base_rewrite_payload(),
        policy,
        path=str(tmp_path),
        run_command_fn=fake_run,
    )

    assert exit_code == 1
    assert payload["lint_result"]["timed_out"] is True
    assert payload["policy_result"]["checks"][0]["timed_out"] is True


def test_evaluate_apply_policy_rollback_restores_checkpointed_files(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    project = tmp_path / "project"
    project.mkdir()
    source_file = project / "sample.py"
    source_file.write_text("print('before')\n", encoding="utf-8")
    checkpoint = create_checkpoint(str(project))
    source_file.write_text("print('after')\n", encoding="utf-8")

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": "lint",
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "rollback",
        },
    )
    policy = load_apply_policy(str(policy_path))

    payload, exit_code = evaluate_apply_policy(
        _base_rewrite_payload(checkpoint=checkpoint.__dict__),
        policy,
        path=str(project),
        run_command_fn=lambda *_args: {
            "passed": False,
            "detail": "lint failed",
            "exit_code": 4,
        },
    )

    assert exit_code == 1
    assert source_file.read_text(encoding="utf-8") == "print('before')\n"
    assert payload["rollback"]["performed"] is True
    assert payload["rollback"]["checkpoint_id"] == checkpoint.checkpoint_id
    assert payload["policy_result"]["action_taken"] == "rollback"


def test_evaluate_apply_policy_real_ruleset_scan_fails_without_baseline(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    source_file = tmp_path / "sample.py"
    source_file.write_text("eval(user_input)\n", encoding="utf-8")
    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": {"enabled": True, "pack": "auth-safe", "language": "python"},
            "on_failure": "fail",
        },
    )
    policy = load_apply_policy(str(policy_path))

    payload, exit_code = evaluate_apply_policy(
        _base_rewrite_payload(),
        policy,
        path=str(source_file),
    )

    assert exit_code == 1
    assert payload["scan_result"]["new_findings"] >= 1
    assert payload["policy_result"]["checks"] == [
        {
            "name": "scan",
            "passed": False,
            "detail": payload["scan_result"]["detail"],
        }
    ]


def test_evaluate_apply_policy_real_ruleset_scan_honors_baseline(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    source_file = tmp_path / "sample.py"
    source_file.write_text("eval(user_input)\n", encoding="utf-8")
    baseline_path = tmp_path / "baseline.json"
    _write_baseline(baseline_path, source_file)
    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": {
                "enabled": True,
                "pack": "auth-safe",
                "language": "python",
                "baseline": str(baseline_path),
            },
            "on_failure": "fail",
        },
    )
    policy = load_apply_policy(str(policy_path))

    payload, exit_code = evaluate_apply_policy(
        _base_rewrite_payload(),
        policy,
        path=str(source_file),
    )

    assert exit_code == 0
    assert payload["scan_result"]["new_findings"] == 0
    assert payload["policy_result"]["all_passed"] is True


def test_ruleset_scan_uses_parent_directory_for_file_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli.apply_policy import RulesetScanPolicy, _run_ruleset_scan_policy

    source_file = tmp_path / "sample.py"
    source_file.write_text("eval(user_input)\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def _fake_run_ast_scan_payload(project_cfg, rules, **kwargs):
        captured["project_cfg"] = project_cfg
        captured["rules"] = rules
        captured["kwargs"] = kwargs
        return {"findings": [], "baseline": {"new_findings": 0}}

    monkeypatch.setattr("tensor_grep.cli.main._run_ast_scan_payload", _fake_run_ast_scan_payload)
    monkeypatch.setattr(
        "tensor_grep.cli.rule_packs.resolve_rule_pack",
        lambda pack, language: ({"name": pack, "language": language}, [{"id": "x"}]),
    )

    result = _run_ruleset_scan_policy(
        RulesetScanPolicy(enabled=True, pack="auth-safe", language="python", baseline=None),
        source_file,
        tmp_path,
    )

    assert result["passed"] is True
    assert captured["project_cfg"]["root_dir"] == source_file.parent


def test_ruleset_scan_fails_closed_when_no_ast_backend_is_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli.apply_policy import RulesetScanPolicy, _run_ruleset_scan_policy

    source_file = tmp_path / "sample.py"
    source_file.write_text("eval(user_input)\n", encoding="utf-8")

    monkeypatch.setattr(
        "tensor_grep.cli.main._run_ast_scan_payload",
        lambda *_args, **_kwargs: {
            "routing_backend": "AstBackend",
            "backends": ["CpuBackend"],
            "findings": [],
        },
    )
    monkeypatch.setattr(
        "tensor_grep.cli.rule_packs.resolve_rule_pack",
        lambda pack, language: ({"name": pack, "language": language}, [{"id": "x"}]),
    )

    result = _run_ruleset_scan_policy(
        RulesetScanPolicy(enabled=True, pack="auth-safe", language="python", baseline=None),
        source_file,
        tmp_path,
    )

    assert result["passed"] is False
    assert result["new_findings"] == 0
    assert "requires an AST backend" in result["detail"]


def test_ast_workflow_run_command_requires_apply_when_policy_is_set(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tensor_grep.cli.ast_workflows import run_command

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "warn",
        },
    )

    exit_code = run_command(
        "def $F($$$ARGS): return $EXPR",
        str(tmp_path),
        rewrite="lambda $$$ARGS: $EXPR",
        lang="python",
        policy=str(policy_path),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--policy requires --apply" in captured.err


def test_ast_workflow_main_entry_emits_json_for_policy_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tensor_grep.cli import ast_workflows

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "warn",
        },
    )

    monkeypatch.setattr(
        ast_workflows,
        "execute_rewrite_apply_json",
        lambda **_kwargs: ('{"policy_result":{"all_passed":true}}', 0),
    )

    with pytest.raises(SystemExit) as excinfo:
        ast_workflows.main_entry([
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--policy",
            str(policy_path),
            "--json",
            "def $F($$$ARGS): return $EXPR",
            str(tmp_path),
        ])

    assert excinfo.value.code == 0
    assert '{"policy_result":{"all_passed":true}}' in capsys.readouterr().out


def test_ast_workflow_main_entry_preserves_nonzero_exit_for_policy_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tensor_grep.cli import ast_workflows

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": "lint",
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "fail",
        },
    )

    monkeypatch.setattr(
        ast_workflows,
        "execute_rewrite_apply_json",
        lambda **_kwargs: ('{"policy_result":{"all_passed":false}}', 1),
    )

    with pytest.raises(SystemExit) as excinfo:
        ast_workflows.main_entry([
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--apply",
            "--policy",
            str(policy_path),
            "--json",
            "def $F($$$ARGS): return $EXPR",
            str(tmp_path),
        ])

    assert excinfo.value.code == 1
    assert '{"policy_result":{"all_passed":false}}' in capsys.readouterr().out
