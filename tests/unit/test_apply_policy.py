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
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "repo"
    repo.mkdir()
    fake_abs = str(tmp_path / "trusted-bin" / "ruff")
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
    """Regression (adversarial security gate): a repo-local shadow that is a SYMLINK into a subdir
    must still be rejected. The guard compares os.path.abspath, NOT Path.resolve -- resolve()
    follows the symlink and would put the parent (repo/tools) outside cwd, slipping the parent==cwd
    check and spawning the payload."""
    import pytest as _pytest

    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    tools = repo / "tools"
    tools.mkdir()
    payload = _write_fake_executable(tools, "evil", "pwned")
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

    assert result["passed"] is False, "a symlink shadow resolving into the repo must be rejected"
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
