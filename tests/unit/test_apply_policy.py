from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli.checkpoint_store import create_checkpoint


def _write_policy(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "apply-policy.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _policy_command(*args: object) -> str:
    argv = [str(arg) for arg in args]
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return " ".join(shlex.quote(arg) for arg in argv)


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


def _has_ast_grep_binary() -> bool:
    return any(shutil.which(name) is not None for name in ("ast-grep", "ast-grep.exe", "sg"))


def _write_fake_executable(directory: Path, name: str, echo: str) -> Path:
    """Create a minimal, genuinely-executable stub named ``name`` in ``directory``.

    Cross-platform so the shadow-executable tests (audit #35) are deterministic on
    any box, per the test-path-resolved-binary-trap lesson: never resolve a real
    system binary in a security test. Windows resolves commands via PATHEXT (a
    bare ``name`` needs an extension like ``.cmd``); POSIX resolves by the exact
    filename plus the executable permission bit.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        target = directory / f"{name}.cmd"
        target.write_text(f"@echo {echo}\n", encoding="utf-8")
    else:
        target = directory / name
        target.write_text(f"#!/bin/sh\necho {echo}\n", encoding="utf-8")
        target.chmod(0o755)
    return target


def _skip_if_real_ruleset_scan_fixture_is_unsupported(source_file: Path) -> None:
    try:
        payload = _scan_baseline_payload(source_file)
    except RuntimeError as exc:
        pytest.skip(f"real ruleset scan fixture unsupported on this backend: {exc}")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        pytest.skip("real ruleset scan fixture unsupported on this backend")
    if not any(
        isinstance(finding, dict)
        and int(finding.get("matches", 0)) > 0
        and finding.get("status") in {None, "new"}
        for finding in findings
    ):
        pytest.skip("real ruleset scan fixture unsupported on this backend")


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


def test_load_apply_policy_rejects_policy_lint_cmd_when_not_allowed(
    tmp_path: Path,
) -> None:
    """Audit HIGH (RCE): a policy FILE carrying lint_cmd/test_cmd bypassed the MCP
    TG_MCP_ALLOW_VALIDATION_COMMANDS gate because enforcement lived only in the MCP
    wrapper, never in load_apply_policy. With allow_validation_commands=False the
    module must FAIL CLOSED before any command can run."""
    from tensor_grep.cli.apply_policy import (
        PolicyCommandsNotAllowedError,
        load_apply_policy,
    )

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": "echo pwned",
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "warn",
        },
    )

    with pytest.raises(PolicyCommandsNotAllowedError):
        load_apply_policy(str(policy_path), allow_validation_commands=False)


def test_load_apply_policy_rejects_policy_test_cmd_when_not_allowed(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli.apply_policy import (
        PolicyCommandsNotAllowedError,
        load_apply_policy,
    )

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": "echo pwned",
            "ruleset_scan": None,
            "on_failure": "warn",
        },
    )

    with pytest.raises(PolicyCommandsNotAllowedError):
        load_apply_policy(str(policy_path), allow_validation_commands=False)


def test_load_apply_policy_allows_validation_commands_when_permitted(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli.apply_policy import load_apply_policy

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": "echo ok",
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "warn",
        },
    )

    policy = load_apply_policy(str(policy_path), allow_validation_commands=True)
    assert policy.lint_cmd == "echo ok"


def test_load_apply_policy_permits_ruleset_only_policy_without_validation_commands(
    tmp_path: Path,
) -> None:
    """A policy that runs only a (safe) ruleset scan + rollback must NOT be
    over-blocked when validation commands are disabled — only lint_cmd/test_cmd are
    the shell-exec sink, so a blanket ``policy is not None`` rejection would regress
    the safe rollback/scan-only use case."""
    from tensor_grep.cli.apply_policy import load_apply_policy

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": {"enabled": False},
            "on_failure": "rollback",
        },
    )

    policy = load_apply_policy(str(policy_path), allow_validation_commands=False)
    assert policy.lint_cmd is None
    assert policy.test_cmd is None


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


def test_load_apply_policy_rejects_baseline_outside_policy_dir(tmp_path: Path) -> None:
    # Round-7 fresh-eyes: an absolute (or ..-escaping) baseline that leaves the policy directory
    # must be refused -- otherwise _load_json_object reads an arbitrary JSON file, a disclosure
    # primitive when the policy file itself is untrusted.
    from tensor_grep.cli.apply_policy import PolicyValidationError, load_apply_policy

    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.json"
    secret.write_text(json.dumps({"token": "s3cr3t"}), encoding="utf-8")

    policy_root = tmp_path / "repo"
    policy_root.mkdir()
    policy_path = _write_policy(
        policy_root,
        {
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": {
                "enabled": True,
                "pack": "auth-safe",
                "language": "python",
                "baseline": str(secret),  # absolute path OUTSIDE the policy directory
            },
            "on_failure": "warn",
        },
    )

    with pytest.raises(PolicyValidationError) as excinfo:
        load_apply_policy(str(policy_path))
    messages = " ".join(detail.get("message", "") for detail in excinfo.value.details)
    assert "within the policy directory" in messages


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


def test_evaluate_apply_policy_expands_file_placeholders_per_unique_edit(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    project = tmp_path / "project"
    project.mkdir()
    first = project / "src" / "one.py"
    second = project / "src" / "file with spaces.py"
    first.parent.mkdir()
    first.write_text("print('one')\n", encoding="utf-8")
    second.write_text("print('two')\n", encoding="utf-8")
    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": "lint --file {file}",
            "test_cmd": "test $file",
            "ruleset_scan": None,
            "on_failure": "warn",
            "timeout": 7,
        },
    )
    policy = load_apply_policy(str(policy_path))
    rewrite_payload = {
        **_base_rewrite_payload(),
        "edits": [
            {"file": str(first.resolve())},
            {"file": str(second.resolve())},
            {"file": str(first.resolve())},
        ],
    }
    calls: list[tuple[str, str, Path, int]] = []

    def fake_run(name: str, command: str, cwd: Path, timeout: int) -> dict[str, object]:
        calls.append((name, command, cwd, timeout))
        return {"passed": True, "detail": f"{name} ok", "exit_code": 0}

    payload, exit_code = evaluate_apply_policy(
        rewrite_payload,
        policy,
        path=str(project),
        run_command_fn=fake_run,
    )

    assert exit_code == 0
    assert [(name, cwd, timeout) for name, _command, cwd, timeout in calls] == [
        ("lint", project.resolve(), 7),
        ("lint", project.resolve(), 7),
        ("test", project.resolve(), 7),
        ("test", project.resolve(), 7),
    ]
    assert calls[0][1] == _policy_command("lint", "--file", "src/one.py")
    assert calls[1][1] == _policy_command("lint", "--file", "src/file with spaces.py")
    assert calls[2][1] == _policy_command("test", "src/one.py")
    assert calls[3][1] == _policy_command("test", "src/file with spaces.py")
    assert payload["lint_result"]["file_count"] == 2
    assert payload["test_result"]["file_count"] == 2
    assert payload["policy_result"]["checks"] == [
        {
            "name": "lint",
            "passed": True,
            "detail": "lint command succeeded for 2 file(s).",
        },
        {
            "name": "test",
            "passed": True,
            "detail": "test command succeeded for 2 file(s).",
        },
    ]


def test_evaluate_apply_policy_file_placeholder_without_edited_file_fails(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": "lint {file}",
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "fail",
        },
    )
    policy = load_apply_policy(str(policy_path))

    payload, exit_code = evaluate_apply_policy(
        _base_rewrite_payload(),
        policy,
        path=str(tmp_path),
    )

    assert exit_code == 1
    assert payload["lint_result"]["passed"] is False
    assert "requires at least one edited file" in str(payload["lint_result"]["detail"])
    assert payload["policy_result"]["action_taken"] == "fail"


def test_evaluate_apply_policy_placeholder_stops_at_first_failure_for_fail_policy(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    project = tmp_path / "project"
    project.mkdir()
    files = [project / "a.py", project / "b.py", project / "c.py"]
    for current in files:
        current.write_text("print('x')\n", encoding="utf-8")
    policy_path = _write_policy(
        tmp_path,
        {
            "version": 1,
            "lint_cmd": "lint {file}",
            "test_cmd": None,
            "ruleset_scan": None,
            "on_failure": "fail",
        },
    )
    policy = load_apply_policy(str(policy_path))
    rewrite_payload = {
        **_base_rewrite_payload(),
        "edits": [{"file": str(current.resolve())} for current in files],
    }
    calls: list[str] = []

    def fake_run(_name: str, command: str, _cwd: Path, _timeout: int) -> dict[str, object]:
        calls.append(command)
        if command.endswith("b.py"):
            return {"passed": False, "detail": "lint failed", "exit_code": 9}
        return {"passed": True, "detail": "lint ok", "exit_code": 0}

    payload, exit_code = evaluate_apply_policy(
        rewrite_payload,
        policy,
        path=str(project),
        run_command_fn=fake_run,
    )

    assert exit_code == 1
    assert calls == ["lint a.py", "lint b.py"]
    assert payload["lint_result"]["passed"] is False
    assert payload["lint_result"]["failed_count"] == 1
    assert payload["lint_result"]["results"][-1]["file"] == "b.py"
    assert payload["policy_result"]["action_taken"] == "fail"


def test_run_policy_command_does_not_interpret_shell_metacharacters(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import _run_policy_command

    first_code = "from pathlib import Path; Path('first.txt').write_text('ok', encoding='utf-8')"
    second_code = "from pathlib import Path; Path('second.txt').write_text('bad', encoding='utf-8')"
    command = (
        f"{_policy_command(sys.executable, '-c', first_code)} && "
        f"{_policy_command(sys.executable, '-c', second_code)}"
    )

    result = _run_policy_command("lint", command, tmp_path, timeout=10)

    assert result["passed"] is False
    assert "shell control operator" in str(result["detail"])
    assert not (tmp_path / "first.txt").exists()
    assert not (tmp_path / "second.txt").exists()


def test_run_policy_command_preserves_quoted_paths_and_arguments(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import _run_policy_command

    script_dir = tmp_path / "scripts with spaces"
    script_dir.mkdir()
    script_path = script_dir / "write argument.py"
    script_path.write_text(
        "\n".join([
            "import sys",
            "from pathlib import Path",
            "Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')",
        ]),
        encoding="utf-8",
    )
    output_dir = tmp_path / "output with spaces"
    output_dir.mkdir()
    output_path = output_dir / "result file.txt"

    command = _policy_command(
        sys.executable,
        script_path,
        output_path,
        "argument with spaces && literal",
    )

    result = _run_policy_command("test", command, tmp_path, timeout=10)

    assert result == {"passed": True, "detail": "test command succeeded.", "exit_code": 0}
    assert output_path.read_text(encoding="utf-8") == "argument with spaces && literal"


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


def test_evaluate_apply_policy_rollback_without_checkpoint_reports_unavailable(
    tmp_path: Path,
) -> None:
    """H8: on_failure="rollback" with no usable checkpoint must NOT claim a rollback that
    never happened. _rollback_summary returns {"performed": False} when the rewrite payload
    carries no checkpoint_id -- action_taken must reflect that (not silently report
    "rollback" while the failed edit is still on disk)."""
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    project = tmp_path / "project"
    project.mkdir()
    source_file = project / "sample.py"
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
        _base_rewrite_payload(checkpoint=None),
        policy,
        path=str(project),
        run_command_fn=lambda *_args: {
            "passed": False,
            "detail": "lint failed",
            "exit_code": 4,
        },
    )

    assert exit_code == 1
    # No checkpoint was ever created, so the file must be untouched -- there is nothing to
    # roll back to, and the receipt must say so rather than claim a rollback occurred.
    assert source_file.read_text(encoding="utf-8") == "print('after')\n"
    assert payload["rollback"]["performed"] is False
    assert payload["policy_result"]["action_taken"] == "rollback_unavailable"


@pytest.mark.skipif(not _has_ast_grep_binary(), reason="requires ast-grep binary")
def test_evaluate_apply_policy_real_ruleset_scan_fails_without_baseline(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    source_file = tmp_path / "sample.py"
    source_file.write_text("eval(user_input)\n", encoding="utf-8")
    _skip_if_real_ruleset_scan_fixture_is_unsupported(source_file)
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


@pytest.mark.skipif(not _has_ast_grep_binary(), reason="requires ast-grep binary")
def test_evaluate_apply_policy_real_ruleset_scan_honors_baseline(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import evaluate_apply_policy, load_apply_policy

    source_file = tmp_path / "sample.py"
    source_file.write_text("eval(user_input)\n", encoding="utf-8")
    _skip_if_real_ruleset_scan_fixture_is_unsupported(source_file)
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
    payload = json.loads(capsys.readouterr().out)
    assert payload["policy_result"] == {"all_passed": True}
    # audit M3: every tg run --json shape now carries version/schema_version/mode.
    assert payload["mode"] == "apply"
    assert payload["schema_version"] == 1


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
    payload = json.loads(capsys.readouterr().out)
    assert payload["policy_result"] == {"all_passed": False}
    assert payload["mode"] == "apply"


def test_run_policy_command_fails_closed_when_executable_missing_from_path(
    tmp_path: Path,
) -> None:
    # CWE-427: a policy command whose executable is not on PATH must fail closed, never run.
    from tensor_grep.cli.apply_policy import _run_policy_command

    result = _run_policy_command(
        "lint", "tg-definitely-nonexistent-tool-xyz --check", tmp_path, timeout=10
    )
    assert result["passed"] is False
    assert "not found on PATH" in str(result["detail"])


def test_run_policy_command_resolves_relative_executable_to_absolute(
    tmp_path: Path, monkeypatch
) -> None:
    # CWE-427: a relative argv[0] must be resolved to an absolute PATH binary before spawning,
    # so Windows CreateProcess never searches the untrusted target-repo cwd for a shadow tool.
    #
    # `repo` (the cwd/untrusted_root passed to _run_policy_command) is a SUBDIRECTORY of
    # tmp_path, and the trusted binary lives at a SIBLING path (tmp_path/trusted-bin) --
    # genuinely outside `repo`. Before the H2 beneath-or-equal fix this test passed `tmp_path`
    # itself as cwd with the "trusted" binary nested one level under it, which the old
    # immediate-parent-equality guard happened not to flag; under the corrected beneath-or-equal
    # guard that fixture would itself BE the nested-shadow vulnerability, not a legitimate
    # outside-root binary, so it must live outside the untrusted root to keep testing what its
    # name says it tests.
    #
    # #126: the confinement check now also canonicalizes the resolved executable's parent
    # directory (Path.resolve(strict=True), to catch 8.3/junction/\\?\ aliasing) rather than
    # comparing purely lexical strings, so "trusted-bin" must be a real, resolvable directory --
    # a mocked shutil.which() pointing at a path whose parent doesn't exist on disk no longer
    # models a legitimate resolved binary (in reality shutil.which() never returns a path whose
    # parent is missing).
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "repo"
    repo.mkdir()
    trusted_bin = tmp_path / "trusted-bin"
    trusted_bin.mkdir()
    fake_abs = str(trusted_bin / "ruff")
    Path(fake_abs).write_text("", encoding="utf-8")
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: fake_abs if cmd == "ruff" else None,
    )
    captured: dict[str, object] = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        import subprocess as _sp

        return _sp.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(apply_policy.subprocess, "run", _fake_run)
    result = apply_policy._run_policy_command("lint", "ruff check .", repo, timeout=10)

    assert captured["argv"][0] == fake_abs  # absolute, PATH-resolved — not a cwd search
    assert result["passed"] is True


def test_run_policy_command_rejects_repo_local_shadow_executable(
    tmp_path: Path, monkeypatch
) -> None:
    """audit #35 (CWE-427, Py3.11/Windows cwd shadow-exe): even when shutil.which()
    resolves argv[0] to a path whose PARENT directory is the untrusted target repo
    root -- exactly what happens on Python < 3.12 + Windows, where shutil.which()
    unconditionally re-inserts the current working directory into its search
    regardless of the `path=` kwarg we pass (cpython#91558) -- the resolution must
    be rejected fail-closed, never spawned. This directly exercises the
    parent-equals-untrusted-root guard, independent of any real OS/Python-version
    shutil.which quirk.
    """
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    shadow = _write_fake_executable(repo, "pytest", "shadow-pwned")

    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: str(shadow) if cmd == "pytest" else None,
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    assert result["passed"] is False
    assert "refusing a repo-local executable shadow" in str(result["detail"])
    assert str(shadow) in str(result["detail"])
    assert not spawn_calls  # the shadow binary must never be spawned


def test_run_policy_command_does_not_execute_planted_repo_local_cwd_shadow(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end (audit #35): plant a `pytest` shadow at the untrusted repo root,
    chdir into that repo, and point PATH at a trusted-but-empty directory (so the
    only way `pytest` could resolve is via an implicit/leftover cwd search -- the
    exact vector under test). Real shutil.which() behavior differs by OS/Python
    version, so either outcome is fail-closed and acceptable: "not found on PATH"
    (cwd never searched) or "refusing a repo-local executable shadow" (cwd searched, guard rejects it).
    What must NEVER happen, on any box, is the shadow binary being spawned.
    """
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    _write_fake_executable(repo, "pytest", "shadow-pwned")

    trusted_empty_dir = tmp_path / "trusted-empty"
    trusted_empty_dir.mkdir()
    monkeypatch.setenv("PATH", str(trusted_empty_dir))
    monkeypatch.chdir(repo)

    spawn_calls: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        spawn_calls.append(list(argv))
        import subprocess as _sp

        return _sp.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(apply_policy.subprocess, "run", _fake_run)

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls
    assert "not found on PATH" in str(
        result["detail"]
    ) or "refusing a repo-local executable shadow" in str(result["detail"])


def test_run_policy_command_prefers_trusted_path_entry_over_cwd_shadow(
    tmp_path: Path, monkeypatch
) -> None:
    """The real tool, when it lives on a trusted PATH directory (not cwd), must be
    the one resolved and spawned -- the untrusted repo's same-named shadow must
    never win just because CreateProcess/shutil.which might also search cwd."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    _write_fake_executable(repo, "pytest", "shadow-pwned")

    trusted_dir = tmp_path / "trusted-bin"
    real_tool = _write_fake_executable(trusted_dir, "pytest", "real-tool")

    monkeypatch.setenv("PATH", str(trusted_dir))
    monkeypatch.chdir(repo)

    captured: dict[str, object] = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        import subprocess as _sp

        return _sp.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(apply_policy.subprocess, "run", _fake_run)

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    # Deterministic across Windows cwd-search regimes (NoDefaultCurrentDirectoryInExePath set or
    # not): the untrusted repo shadow must NEVER be spawned. Either the trusted-PATH tool resolves
    # and spawns (passed True, e.g. this dev box), or which() found the cwd shadow and the guard
    # rejected it (passed False, nothing spawned, e.g. a default Windows box) -- both are correct
    # security outcomes; ONLY a spawned repo shadow is a failure. (Asserting passed is True here
    # was env-dependent and false-failed on default Windows -- the PATH-resolved-binary trap.)
    if captured.get("argv") is not None:
        spawned = Path(str(captured["argv"][0]))
        assert spawned.parent.resolve() != repo.resolve(), f"repo shadow was spawned: {spawned}"
        assert spawned.resolve() == real_tool.resolve()
    else:
        assert result["passed"] is False and "shadow" in str(result["detail"]).lower()


def test_run_policy_command_rejects_symlink_shadow_resolving_outside_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression (adversarial security gate, #453): a repo-local shadow that is a SYMLINK whose
    TARGET points OUTSIDE the repo must still be rejected, because the guard confines on
    os.path.abspath (a purely lexical normalization that does NOT follow the symlink's final
    component), NOT Path.resolve (which follows the link out of the repo).

    This test's whole job is to go RED if the guard is ever regressed from abspath to .resolve()
    -- the exact change that would re-open the #453 RCE. That property is only pinned when the
    symlink target is OUTSIDE the repo:

      * CURRENT (correct) abspath guard: `shutil.which` yields the in-repo link `<repo>/ruff.cmd`;
        abspath keeps it at `<repo>/ruff.cmd` (link not followed) -> BENEATH repo -> REJECTED
        (this test passes).
      * REGRESSED .resolve() guard: the link resolves to `<tmp>/outside/evil.cmd` -> NOT beneath
        repo -> the beneath-or-equal check would NOT reject -> it would reach subprocess.run and
        the shadow would be spawned (this test would FAIL on the assertions below).

    (An earlier revision of this test pointed the target at `<repo>/tools/evil` -- INSIDE the
    repo -- which under the beneath-or-equal guard is beneath repo in BOTH the abspath and the
    resolve forms, so it passed regardless of which the guard used and no longer pinned the
    abspath-vs-resolve property. The target must live outside the repo to keep this a real
    regression guard.)"""
    import pytest as _pytest

    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    # Target OUTSIDE the repo: only .resolve() (which follows the link) would land here; abspath
    # keeps the resolved path at the in-repo link location.
    outside = tmp_path / "outside"
    payload = _write_fake_executable(outside, "evil", "pwned")
    shadow = repo / "ruff.cmd"
    try:
        shadow.symlink_to(payload)
    except (OSError, NotImplementedError):
        _pytest.skip("symlink creation not permitted on this box")

    trusted_empty = tmp_path / "trusted-empty"
    trusted_empty.mkdir()
    monkeypatch.setenv("PATH", str(trusted_empty))
    monkeypatch.chdir(repo)
    # Simulate the Windows cwd search returning the in-repo symlink shadow.
    monkeypatch.setattr(apply_policy.shutil, "which", lambda _name, path=None: str(shadow))

    spawn_calls: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        spawn_calls.append(list(argv))
        import subprocess as _sp

        return _sp.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(apply_policy.subprocess, "run", _fake_run)

    result = apply_policy._run_policy_command("test", "ruff --check", repo, timeout=10)

    assert result["passed"] is False, (
        "the in-repo symlink shadow must be rejected on its abspath (un-followed) location; a "
        "regression to .resolve() would follow the link outside the repo and let it spawn"
    )
    assert not spawn_calls, "the symlink shadow must never be spawned"
    assert "refusing a repo-local executable shadow" in str(result["detail"])


def test_run_policy_command_rejects_nested_repo_shadow_executable(
    tmp_path: Path, monkeypatch
) -> None:
    """H2 (CWE-427 refinement, codex audit): the shadow-executable guard used to reject a
    resolved executable ONLY when its IMMEDIATE parent equaled the untrusted repo root
    exactly (`resolved_path.parent == untrusted_root`). A binary resolved to
    `<repo>/nested/dir/tool.cmd` has `.parent == <repo>/nested/dir`, which never equals
    `<repo>` -- so the old guard let it through to subprocess.run. The guard must reject
    ANY resolution beneath the untrusted root, at any depth, not just a direct child."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    nested = repo / "nested" / "dir"
    shadow = _write_fake_executable(nested, "pytest", "shadow-pwned")

    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: str(shadow) if cmd == "pytest" else None,
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    assert result["passed"] is False, "a nested repo-local shadow must be rejected"
    assert not spawn_calls, "the nested shadow binary must never be spawned"
    assert "refusing a repo-local executable shadow" in str(result["detail"])
    assert str(shadow) in str(result["detail"])


def test_run_policy_command_rejects_deeply_nested_repo_shadow_executable(
    tmp_path: Path, monkeypatch
) -> None:
    """Same as above at greater depth (node_modules/.bin-style), proving the guard is a
    true beneath-or-equal confinement check and not merely a two-level special case."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    nested = repo / "a" / "b" / "c" / "d"
    shadow = _write_fake_executable(nested, "pytest", "shadow-pwned")

    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: str(shadow) if cmd == "pytest" else None,
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls
    assert "refusing a repo-local executable shadow" in str(result["detail"])


def test_abspath_beneath_or_equal_matches_root_and_nested_descendants(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import _abspath_beneath_or_equal

    root = tmp_path / "repo"
    assert _abspath_beneath_or_equal(root, root) is True
    assert _abspath_beneath_or_equal(root / "tool.exe", root) is True
    assert _abspath_beneath_or_equal(root / "nested" / "dir" / "tool.exe", root) is True


def test_abspath_beneath_or_equal_rejects_unrelated_and_sibling_prefix_paths(
    tmp_path: Path,
) -> None:
    """A sibling directory that merely shares a string PREFIX with the root (e.g.
    `repo-other` vs `repo`) must NOT be treated as beneath it -- a naive
    ``str.startswith(root)`` without a path-separator boundary would wrongly match this."""
    from tensor_grep.cli.apply_policy import _abspath_beneath_or_equal

    root = tmp_path / "repo"
    assert _abspath_beneath_or_equal(tmp_path / "unrelated" / "tool.exe", root) is False
    assert _abspath_beneath_or_equal(tmp_path / "repo-other" / "tool.exe", root) is False
    assert _abspath_beneath_or_equal(tmp_path / "repository" / "tool.exe", root) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows case-insensitive path semantics")
def test_abspath_beneath_or_equal_is_case_insensitive_on_windows(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import _abspath_beneath_or_equal

    root = tmp_path / "repo"
    upper = Path(str(tmp_path).upper()) / "REPO" / "NESTED" / "tool.EXE"
    assert _abspath_beneath_or_equal(upper, root) is True


def test_search_path_without_cwd_strips_entries_nested_under_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """Widen (H2 follow-up, codex audit): _search_path_without_cwd previously stripped only
    PATH entries that resolved EXACTLY to cwd. A PATH entry NESTED under cwd (e.g. the
    extremely common `<repo>/node_modules/.bin` or `<repo>/.venv/Scripts`) must also be
    stripped -- an untrusted repo's own dependency-manager shim directory should not be
    handed to shutil.which() as a searchable, quasi-trusted PATH entry."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "repo"
    repo.mkdir()
    nested_bin = repo / "node_modules" / ".bin"
    nested_bin.mkdir(parents=True)
    venv_scripts = repo / ".venv" / "Scripts"
    venv_scripts.mkdir(parents=True)
    unrelated = tmp_path / "trusted-bin"
    unrelated.mkdir()

    monkeypatch.chdir(repo)
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(nested_bin), str(venv_scripts), str(unrelated), str(repo)]),
    )

    filtered = apply_policy._search_path_without_cwd()
    filtered_entries = filtered.split(os.pathsep) if filtered else []

    assert str(nested_bin) not in filtered_entries
    assert str(venv_scripts) not in filtered_entries
    assert str(repo) not in filtered_entries  # pre-existing exact-match behavior, still holds
    assert str(unrelated) in filtered_entries


def test_search_path_without_cwd_keeps_sibling_prefix_directory(
    tmp_path: Path, monkeypatch
) -> None:
    """A PATH entry that merely shares a string prefix with cwd (e.g. `repo-tools` next to
    `repo`) must NOT be stripped -- mirrors the separator-boundary check on the executable
    guard, applied here to the PATH-filtering defense-in-depth layer."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "repo"
    repo.mkdir()
    sibling_prefix = tmp_path / "repo-tools"
    sibling_prefix.mkdir()

    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", str(sibling_prefix))

    filtered = apply_policy._search_path_without_cwd()
    filtered_entries = filtered.split(os.pathsep) if filtered else []
    assert str(sibling_prefix) in filtered_entries


# --- #126: canonicalize the exec parent before the apply_policy confinement decision ---
#
# commit e10c91d (H2, #509) fixed the beneath-or-equal depth bug but explicitly deferred this:
# "Guard logic unchanged; the parent-canonicalization hardening (8.3/junction/\?\ edges) is
# intentionally NOT applied here -- it is a tracked fast-follow." resolved_path in
# _run_policy_command is built via os.path.abspath (a purely LEXICAL normalization, deliberately
# NOT Path.resolve() -- see _abspath_beneath_or_equal's docstring and the #453 regression test
# above) so that a repo-local shadow which is itself a symlink is judged by where it lexically
# sits, never by where it points. But that same lexical-only comparison can be BYPASSED in the
# opposite direction: an 8.3 short name (PROGRA~1 vs "Program Files"), an NTFS junction alias, or
# an explicit \\?\ / \\?\UNC\ extended-length prefix can all spell a location INSIDE the untrusted
# repo differently from the repo root's own canonical string, so the plain startswith-style check
# silently returns False (not beneath) for a path that IS beneath -- fail OPEN.


def test_strip_extended_length_prefix_removes_prefix() -> None:
    r"""Pure string-level unit test (platform-agnostic): the \\?\ extended-length prefix is
    preserved verbatim by both os.path.abspath and Path.resolve() (verified empirically --
    neither adds nor strips it), so it must be stripped explicitly before a normcase/normpath
    confinement comparison."""
    from tensor_grep.cli.apply_policy import _strip_extended_length_prefix

    assert _strip_extended_length_prefix("\\\\?\\C:\\repo\\evil.cmd") == "C:\\repo\\evil.cmd"


def test_strip_extended_length_prefix_removes_unc_prefix() -> None:
    from tensor_grep.cli.apply_policy import _strip_extended_length_prefix

    assert (
        _strip_extended_length_prefix("\\\\?\\UNC\\server\\share\\evil.cmd")
        == "\\\\server\\share\\evil.cmd"
    )


def test_strip_extended_length_prefix_is_a_noop_for_normal_paths() -> None:
    from tensor_grep.cli.apply_policy import _strip_extended_length_prefix

    assert _strip_extended_length_prefix("C:\\repo\\evil.cmd") == "C:\\repo\\evil.cmd"
    assert _strip_extended_length_prefix("/home/user/repo/evil") == "/home/user/repo/evil"


@pytest.mark.skipif(
    sys.platform != "win32", reason="\\\\?\\ extended-length prefix is Windows-only"
)
def test_abspath_beneath_or_equal_matches_extended_length_prefixed_spelling(
    tmp_path: Path,
) -> None:
    r"""A \\?\-prefixed spelling of a path INSIDE root must still compare beneath root -- the
    exact fail-open #126 closes: before the fix these two spellings of the identical location
    compared as unrelated strings."""
    from tensor_grep.cli.apply_policy import _abspath_beneath_or_equal

    root = tmp_path / "repo"
    prefixed = Path("\\\\?\\" + str(root) + "\\evil.cmd")
    assert _abspath_beneath_or_equal(prefixed, root) is True


@pytest.mark.skipif(
    sys.platform != "win32", reason="\\\\?\\ extended-length prefix is Windows-only"
)
def test_abspath_beneath_or_equal_extended_length_prefix_no_false_positive(
    tmp_path: Path,
) -> None:
    r"""Stripping the \\?\ prefix must not make an outside-root path look beneath root."""
    from tensor_grep.cli.apply_policy import _abspath_beneath_or_equal

    root = tmp_path / "repo"
    prefixed_unrelated = Path("\\\\?\\" + str(tmp_path / "unrelated") + "\\evil.cmd")
    assert _abspath_beneath_or_equal(prefixed_unrelated, root) is False


def test_canonicalize_exec_parent_fails_closed_on_nonexistent_parent(tmp_path: Path) -> None:
    """Canonicalization failure (a parent directory that doesn't resolve) must return None,
    never a best-effort guess -- callers fail closed (deny) on None."""
    from tensor_grep.cli.apply_policy import _canonicalize_exec_parent

    missing = tmp_path / "does-not-exist-xyz" / "tool.cmd"
    assert _canonicalize_exec_parent(missing) is None


def test_canonicalize_exec_parent_does_not_dereference_the_leaf_symlink(tmp_path: Path) -> None:
    """#453 preservation: _canonicalize_exec_parent must resolve ONLY the executable's parent
    directory, never the leaf itself. If the leaf is a symlink whose target is OUTSIDE the repo,
    canonicalizing it must NOT follow the link there -- that is exactly the #453 RCE (a
    repo-local shadow that is a symlink escaping confinement by resolving outside the repo)."""
    from tensor_grep.cli.apply_policy import _canonicalize_exec_parent

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    payload = _write_fake_executable(outside, "evil", "pwned")
    shadow = repo / "ruff.cmd"
    try:
        shadow.symlink_to(payload)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this box")

    canonical = _canonicalize_exec_parent(shadow)

    assert canonical is not None
    assert canonical.parent == repo.resolve()
    assert canonical.name == "ruff.cmd"


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 short names are a Windows-only concept")
def test_canonicalize_exec_parent_expands_real_8dot3_short_name() -> None:
    """Uses the OS-provided `C:\\PROGRA~1` short name for `C:\\Program Files`, present on every
    Windows install and readable without admin/write access -- unlike forcing FRESH 8.3
    generation on a throwaway test directory, which needs `fsutil 8dot3name set <path> 0` (an
    elevated volume handle; verified 'Error 5: Access is denied' without admin on this dev box).
    """
    from tensor_grep.cli.apply_policy import _canonicalize_exec_parent

    short_form_parent = Path(r"C:\PROGRA~1")
    if not short_form_parent.is_dir():
        pytest.skip("C:\\PROGRA~1 8.3 alias is not available on this box")

    canonical = _canonicalize_exec_parent(short_form_parent / "tool.cmd")

    assert canonical is not None
    assert "PROGRA~1" not in str(canonical)
    assert canonical.parent == Path(r"C:\Program Files").resolve()
    assert canonical.name == "tool.cmd"


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are a Windows-only concept")
def test_canonicalize_exec_parent_resolves_real_junction(tmp_path: Path) -> None:
    """`mklink /J` (a directory junction) needs no admin privilege, unlike a symlink -- this
    reproduces the real reparse-point mechanism end-to-end."""
    from tensor_grep.cli.apply_policy import _canonicalize_exec_parent

    real_target = tmp_path / "real-target"
    real_target.mkdir()
    junction = tmp_path / "junction-alias"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real_target)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"junction creation not permitted on this box: {proc.stderr}")

    canonical = _canonicalize_exec_parent(junction / "tool.cmd")

    assert canonical is not None
    assert canonical.parent == real_target.resolve()
    assert canonical.name == "tool.cmd"
    assert "junction-alias" not in str(canonical)


def test_run_policy_command_rejects_extended_length_prefix_shadow_inside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    r"""#126 bidirectional oracle (\\?\ edge), fully real: the policy's own command string
    spells argv[0] with an explicit \\?\ extended-length prefix pointing at a real repo-local
    shadow. shutil.which() treats an already-path-shaped argv[0] as-is (no PATH search, no
    canonicalization -- verified empirically), so the prefix survives all the way to the
    confinement check. Pre-fix this compares as an unrelated string (fail open, spawned);
    post-fix _abspath_beneath_or_equal strips the prefix before comparing (denied)."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    shadow = _write_fake_executable(repo, "evil", "shadow-pwned")

    extended_prefixed = "\\\\?\\" + str(shadow.resolve())
    command = _policy_command(extended_prefixed, "--check")

    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", command, repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls, "the \\?\\-prefixed repo-local shadow must never be spawned"
    assert "refusing a repo-local executable shadow" in str(result["detail"])


def test_run_policy_command_rejects_junction_alias_shadow_inside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    """#126 bidirectional oracle (junction edge), fully real: a repo-local shadow is resolved
    via an NTFS junction alias that lives OUTSIDE the repo but points AT it, so its lexical
    string never starts with the repo root's string even though it names the same on-disk
    file. Pre-fix: fail open (spawned). Post-fix: _canonicalize_exec_parent resolves through
    the junction and the OR-guard denies it."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    shadow = _write_fake_executable(repo, "pytest", "shadow-pwned")

    alias = tmp_path / "alias-outside-repo"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(alias), str(repo)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"junction creation not permitted on this box: {proc.stderr}")

    aliased_shadow = str(alias / shadow.name)
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: aliased_shadow if cmd == "pytest" else None,
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls, "the junction-aliased repo-local shadow must never be spawned"
    assert "refusing a repo-local executable shadow" in str(result["detail"])


def test_run_policy_command_allows_junction_alias_that_resolves_outside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    """No regression: a LEGITIMATE trusted binary reached via a junction alias, where both the
    alias and its real target are genuinely outside the untrusted repo, must still be allowed
    to run -- canonicalization must not manufacture a false positive."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()

    real_trusted = tmp_path / "real-trusted-bin"
    real_tool = _write_fake_executable(real_trusted, "ruff", "real-tool")
    trusted_alias = tmp_path / "trusted-alias"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(trusted_alias), str(real_trusted)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"junction creation not permitted on this box: {proc.stderr}")

    aliased_tool = str(trusted_alias / real_tool.name)
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: aliased_tool if cmd == "ruff" else None,
    )
    captured: dict[str, object] = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        import subprocess as _sp

        return _sp.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(apply_policy.subprocess, "run", _fake_run)

    result = apply_policy._run_policy_command("lint", "ruff check .", repo, timeout=10)

    assert result["passed"] is True
    assert captured.get("argv") is not None


def test_run_policy_command_rejects_8dot3_alias_shadow_inside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    """#126 bidirectional oracle (8.3 edge). A fully real reproduction needs FRESH NTFS 8.3
    short-name generation forced on a throwaway test directory (`fsutil 8dot3name set <path>
    0`), which needs an elevated volume handle -- verified 'Error 5: Access is denied' without
    admin on this dev box, and 8dot3 generation is disabled by default for new directories on
    modern Windows anyway. The real mechanism (Path.resolve(strict=True) expanding an 8.3 alias)
    is proven directly against the OS's own always-present `C:\\PROGRA~1` short name in
    test_canonicalize_exec_parent_expands_real_8dot3_short_name. This test proves the
    _run_policy_command WIRING side: shutil.which() returns an 8.3-shaped short alias (the exact
    string shape GetShortPathNameW produces: a truncated stem + `~1`) of a real repo-local
    shadow, and _canonicalize_exec_parent is monkeypatched to return exactly what a real
    Path.resolve(strict=True) call resolves an 8.3 alias to (its own proven behavior) -- so the
    only new thing under test here is that _run_policy_command's OR-guard denies when the
    CANONICAL form (not the raw lexical form) lands inside the repo."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo-with-a-long-name"
    shadow = _write_fake_executable(repo, "pytest", "shadow-pwned")

    short_alias = str(tmp_path / "UNTRUS~1" / "pytest.cmd")
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: short_alias if cmd == "pytest" else None,
    )
    monkeypatch.setattr(
        apply_policy,
        "_canonicalize_exec_parent",
        lambda executable_path: shadow.resolve(),
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls, "the 8.3-aliased repo-local shadow must never be spawned"
    assert "refusing a repo-local executable shadow" in str(result["detail"])


def test_run_policy_command_denies_when_exec_parent_canonicalization_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """Fail closed: when _canonicalize_exec_parent cannot resolve the executable's parent for
    any reason, _run_policy_command must deny, never fall back to allowing the command through
    on the raw (un-canonicalized) comparison alone."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    trusted = tmp_path / "trusted-bin"
    real_tool = _write_fake_executable(trusted, "ruff", "real-tool")

    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: str(real_tool) if cmd == "ruff" else None,
    )
    monkeypatch.setattr(apply_policy, "_canonicalize_exec_parent", lambda executable_path: None)
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("lint", "ruff check .", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls
    assert "could not canonicalize" in str(result["detail"])


# --- #126 Opus re-gate: the 4th same-class edge -- UNC / network-share smuggling ---
#
# The gate reproduced a fourth bypass END-TO-END against the real _run_policy_command: a UNC
# loopback admin-share spelling of the identical in-repo shadow gets SPAWNED. argv[0] spellings of
# <untrusted-repo>\evil.cmd like \\127.0.0.1\C$\...\untrusted-repo\evil.cmd, \\localhost\C$\...,
# and \\?\UNC\127.0.0.1\C$\... all pass the guard and reach subprocess.run because Path.resolve()
# does NOT map the \\host\C$\... admin-share namespace back to C:\..., so _canonicalize_exec_parent
# returns a still-UNC parent and neither the raw-stripped nor the canonical beneath-compare starts
# with the repo-root string. A UNC-spelled executable can never be confined to a LOCAL drive-letter
# repo root by a string comparison, so _run_policy_command now refuses any UNC executable path
# outright (fail closed). Verified empirically: a legitimate local tool always resolves to a
# drive-letter path (C:\..., and \\?\C:\...->C:\... after the ext-length strip), never a UNC prefix.


@pytest.mark.parametrize(
    "unc_prefix",
    [
        "\\\\127.0.0.1\\C$\\",  # loopback IP admin share
        "\\\\localhost\\C$\\",  # loopback hostname admin share
        "\\\\?\\UNC\\127.0.0.1\\C$\\",  # extended-length UNC form of the same
    ],
)
def test_run_policy_command_rejects_unc_admin_share_shadow_inside_repo(
    tmp_path: Path, monkeypatch, unc_prefix: str
) -> None:
    r"""#126 bidirectional oracle (UNC / admin-share edge). A repo-local shadow is reached via a
    UNC loopback admin-share (C$) spelling of the identical on-disk file. Pre-fix: fail open
    (Path.resolve() leaves the parent UNC, so no beneath-compare matches -> spawned). Post-fix:
    _run_policy_command refuses any UNC-spelled executable outright (it can never be confined to
    a local drive-letter repo root).

    Platform-agnostic on purpose: the refusal is a pure string check on argv[0]'s shape (does it
    resolve to a \\... UNC path), which behaves identically on any OS -- no live C$ share or admin
    token is needed to exercise the guard, only to exploit the underlying bypass. shutil.which()
    returns an already-path-shaped argv[0] unchanged (no PATH search, no canonicalization), so the
    UNC spelling reaches the guard verbatim."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    shadow = _write_fake_executable(repo, "pytest", "shadow-pwned")

    # Build the UNC spelling of the SAME on-disk shadow. Only the drive-letter tail is reused; the
    # UNC prefix (loopback admin share) is what Path.resolve() cannot fold back to a local path.
    drive_tail = str(shadow.resolve())[3:]  # strip "C:\" -> "Users\...\untrusted-repo\pytest.cmd"
    unc_shadow = unc_prefix + drive_tail
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: unc_shadow if cmd == "pytest" else None,
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls, "a UNC-spelled repo-local shadow must never be spawned"
    assert "UNC/network-share" in str(result["detail"])


def test_run_policy_command_rejects_unc_share_even_when_outside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    r"""A UNC path is refused REGARDLESS of what it points to -- even a genuinely off-box
    \\fileserver\share\tool.exe. A network-share executable can't be confined to (or reasoned
    about against) the local repo root by a string comparison at all, so it fails closed. This
    also documents the (rare, acceptable) behavior change: a policy that deliberately invoked a
    tool off a mapped UNC share would now be refused; the security posture (never run an
    unconfinable network binary against an untrusted checkout) wins over that niche convenience."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    unc_tool = "\\\\fileserver\\tools\\ruff.exe"
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: unc_tool if cmd == "ruff" else None,
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("lint", "ruff check .", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls
    assert "UNC/network-share" in str(result["detail"])


def test_run_policy_command_allows_local_drive_letter_tool_not_flagged_as_unc(
    tmp_path: Path, monkeypatch
) -> None:
    r"""No regression from the UNC guard: an ordinary local drive-letter tool (C:\... or its
    \\?\C:\... extended-length form, which strips to C:\...) is NOT a UNC path and still runs."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    trusted = tmp_path / "trusted-bin"
    real_tool = _write_fake_executable(trusted, "ruff", "real-tool")

    # Exercise BOTH the plain and the \\?\-extended local spelling; both must be allowed.
    for which_return in (str(real_tool), "\\\\?\\" + str(real_tool.resolve())):
        monkeypatch.setattr(
            apply_policy.shutil,
            "which",
            lambda cmd, path=None, _r=which_return: _r if cmd == "ruff" else None,
        )
        captured: dict[str, object] = {}

        def _fake_run(argv, _cap=captured, **kwargs):
            _cap["argv"] = list(argv)
            import subprocess as _sp

            return _sp.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(apply_policy.subprocess, "run", _fake_run)

        result = apply_policy._run_policy_command("lint", "ruff check .", repo, timeout=10)

        assert result["passed"] is True, f"local tool spelled {which_return!r} must run"
        assert captured.get("argv") is not None
        assert "UNC" not in str(result["detail"])
