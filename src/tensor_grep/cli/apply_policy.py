from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tensor_grep.cli.checkpoint_store import undo_checkpoint


@dataclass(frozen=True)
class RulesetScanPolicy:
    enabled: bool
    pack: str | None
    language: str | None
    baseline: str | None = None


@dataclass(frozen=True)
class ApplyPolicy:
    path: str
    version: int
    lint_cmd: str | None
    test_cmd: str | None
    ruleset_scan: RulesetScanPolicy | None
    on_failure: str
    timeout: int


class PolicyValidationError(ValueError):
    def __init__(self, message: str, *, details: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.details = details


CommandRunner = Callable[[str, str, Path, int], dict[str, object]]
ScanRunner = Callable[[RulesetScanPolicy, Path, Path], dict[str, object]]

_FAILURE_ACTIONS = {"rollback", "warn", "fail"}


def _policy_validation_error(*details: dict[str, str]) -> PolicyValidationError:
    return PolicyValidationError("Invalid apply policy.", details=list(details))


def _resolved_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _coerce_optional_string(
    value: object,
    *,
    field: str,
    allow_empty: bool = False,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _policy_validation_error({"field": field, "message": "must be a string or null"})
    if not value.strip() and not allow_empty:
        raise _policy_validation_error({"field": field, "message": "must not be empty"})
    return value


def _coerce_positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _policy_validation_error({"field": field, "message": "must be a positive integer"})
    return value


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _policy_validation_error({
            "field": "$",
            "message": f"must be valid JSON: {exc.msg}",
        }) from exc
    if not isinstance(payload, dict):
        raise _policy_validation_error({"field": "$", "message": "must be a JSON object"})
    return payload


def _validate_ruleset_scan(
    value: object,
    *,
    policy_dir: Path,
) -> RulesetScanPolicy | None:
    from tensor_grep.cli.rule_packs import resolve_rule_pack

    if value is None:
        return None
    if not isinstance(value, dict):
        raise _policy_validation_error({
            "field": "ruleset_scan",
            "message": "must be an object or null",
        })

    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise _policy_validation_error({
            "field": "ruleset_scan.enabled",
            "message": "must be a boolean",
        })

    pack_value = value.get("pack")
    language_value = value.get("language")
    baseline_value = value.get("baseline")

    pack = None
    language = None
    if enabled or pack_value is not None:
        pack = _coerce_optional_string(pack_value, field="ruleset_scan.pack")
    if enabled or language_value is not None:
        language = _coerce_optional_string(language_value, field="ruleset_scan.language")

    baseline = _coerce_optional_string(
        baseline_value,
        field="ruleset_scan.baseline",
        allow_empty=False,
    )
    if baseline is not None:
        baseline_path = Path(baseline)
        if not baseline_path.is_absolute():
            baseline_path = (policy_dir / baseline_path).resolve()
        if not baseline_path.exists():
            raise _policy_validation_error({
                "field": "ruleset_scan.baseline",
                "message": f"baseline path does not exist: {baseline_path}",
            })
        _load_json_object(baseline_path)
        baseline = str(baseline_path)

    if enabled:
        if pack is None:
            raise _policy_validation_error({
                "field": "ruleset_scan.pack",
                "message": "must be provided when enabled",
            })
        if language is None:
            raise _policy_validation_error({
                "field": "ruleset_scan.language",
                "message": "must be provided when enabled",
            })
        try:
            resolve_rule_pack(pack, language)
        except ValueError as exc:
            raise _policy_validation_error({
                "field": "ruleset_scan.pack",
                "message": str(exc),
            }) from exc

    return RulesetScanPolicy(
        enabled=enabled,
        pack=pack,
        language=language,
        baseline=baseline,
    )


def load_apply_policy(
    policy_path: str,
    *,
    legacy_lint_cmd: str | None = None,
    legacy_test_cmd: str | None = None,
) -> ApplyPolicy:
    path = _resolved_path(policy_path)
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")

    payload = _load_json_object(path)
    required_fields = ["version", "lint_cmd", "test_cmd", "ruleset_scan", "on_failure"]
    missing_fields = [
        {"field": field, "message": "is required"}
        for field in required_fields
        if field not in payload
    ]
    if missing_fields:
        raise PolicyValidationError("Invalid apply policy.", details=missing_fields)

    version = payload.get("version")
    if version != 1:
        raise _policy_validation_error({"field": "version", "message": "must equal 1"})

    lint_cmd = _coerce_optional_string(payload.get("lint_cmd"), field="lint_cmd") or legacy_lint_cmd
    test_cmd = _coerce_optional_string(payload.get("test_cmd"), field="test_cmd") or legacy_test_cmd

    on_failure = payload.get("on_failure")
    if not isinstance(on_failure, str) or on_failure not in _FAILURE_ACTIONS:
        raise _policy_validation_error({
            "field": "on_failure",
            "message": "must be one of rollback, warn, or fail",
        })

    timeout = (
        120
        if "timeout" not in payload
        else _coerce_positive_int(payload.get("timeout"), field="timeout")
    )
    ruleset_scan = _validate_ruleset_scan(payload.get("ruleset_scan"), policy_dir=path.parent)

    return ApplyPolicy(
        path=str(path),
        version=1,
        lint_cmd=lint_cmd,
        test_cmd=test_cmd,
        ruleset_scan=ruleset_scan,
        on_failure=on_failure,
        timeout=timeout,
    )


def _policy_root(path: str | Path, payload: dict[str, object]) -> Path:
    checkpoint_payload = payload.get("checkpoint")
    if isinstance(checkpoint_payload, dict):
        root = checkpoint_payload.get("root")
        if isinstance(root, str) and root.strip():
            return _resolved_path(root)
    resolved = _resolved_path(path)
    return resolved if resolved.is_dir() else resolved.parent


def _command_result(
    *,
    passed: bool,
    detail: str,
    exit_code: int | None = None,
    timed_out: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {"passed": passed, "detail": detail}
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if timed_out:
        payload["timed_out"] = True
    return payload


def _summarize_command_output(name: str, stdout: str, stderr: str, exit_code: int) -> str:
    summary = stderr.strip() or stdout.strip()
    if summary:
        return summary
    return f"{name} command failed with exit code {exit_code}."


def _run_policy_command(name: str, command: str, cwd: Path, timeout: int) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _command_result(
            passed=False,
            detail=f"Command timed out after {timeout}s.",
            timed_out=True,
        )

    if completed.returncode == 0:
        return _command_result(passed=True, detail=f"{name} command succeeded.", exit_code=0)

    return _command_result(
        passed=False,
        detail=_summarize_command_output(
            name,
            completed.stdout or "",
            completed.stderr or "",
            completed.returncode,
        ),
        exit_code=completed.returncode,
    )


def _run_ruleset_scan_policy(
    policy: RulesetScanPolicy,
    target_path: Path,
    _working_root: Path,
) -> dict[str, object]:
    from tensor_grep.cli.main import _run_ast_scan_payload
    from tensor_grep.cli.rule_packs import resolve_rule_pack

    if not policy.enabled:
        return _command_result(passed=True, detail="Ruleset scan disabled.")

    assert policy.pack is not None
    assert policy.language is not None
    ruleset_meta, rules = resolve_rule_pack(policy.pack, policy.language)
    scan_root = target_path if target_path.is_dir() else target_path.parent
    payload = _run_ast_scan_payload(
        {
            "config_path": f"builtin:{ruleset_meta['name']}",
            "root_dir": scan_root,
            "rule_dirs": [],
            "test_dirs": [],
            "language": ruleset_meta["language"],
        },
        rules,
        routing_reason="builtin-ruleset-scan",
        ruleset_name=ruleset_meta["name"],
        baseline_path=policy.baseline,
    )
    raw_backends = payload.get("backends")
    backend_items = raw_backends if isinstance(raw_backends, list) else []
    backend_names = [item for item in backend_items if isinstance(item, str) and item.strip()]
    if backend_names and not {
        "AstBackend",
        "AstGrepWrapperBackend",
    }.intersection(backend_names):
        result = _command_result(
            passed=False,
            detail=(
                "Ruleset scan requires an AST backend; "
                f"resolved {', '.join(sorted(backend_names))}."
            ),
        )
        result["new_findings"] = 0
        result["ruleset"] = ruleset_meta["name"]
        result["language"] = ruleset_meta["language"]
        if policy.baseline is not None:
            result["baseline_path"] = policy.baseline
        return result
    baseline_summary = payload.get("baseline")
    if isinstance(baseline_summary, dict):
        new_findings = int(baseline_summary.get("new_findings", 0))
    else:
        findings = payload.get("findings")
        finding_rows = findings if isinstance(findings, list) else []
        new_findings = sum(
            1
            for finding in finding_rows
            if isinstance(finding, dict) and finding.get("status") == "new"
        )
    passed = new_findings == 0
    detail = (
        f"No new findings for ruleset {ruleset_meta['name']}."
        if passed
        else f"{new_findings} new finding(s) detected by ruleset {ruleset_meta['name']}."
    )
    result = _command_result(passed=passed, detail=detail)
    result["new_findings"] = new_findings
    result["ruleset"] = ruleset_meta["name"]
    result["language"] = ruleset_meta["language"]
    if policy.baseline is not None:
        result["baseline_path"] = policy.baseline
    return result


def _check_row(name: str, result: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": name,
        "passed": bool(result["passed"]),
        "detail": str(result["detail"]),
    }
    if "exit_code" in result:
        payload["exit_code"] = result["exit_code"]
    if result.get("timed_out"):
        payload["timed_out"] = True
    return payload


def _rollback_summary(
    *,
    payload: dict[str, object],
    working_root: Path,
) -> dict[str, object]:
    checkpoint_payload = payload.get("checkpoint")
    checkpoint_id = None
    if isinstance(checkpoint_payload, dict):
        raw_checkpoint_id = checkpoint_payload.get("checkpoint_id")
        if isinstance(raw_checkpoint_id, str) and raw_checkpoint_id.strip():
            checkpoint_id = raw_checkpoint_id
    if checkpoint_id is None:
        return {"performed": False}

    undone = undo_checkpoint(checkpoint_id, str(working_root))
    return {
        "performed": True,
        "checkpoint_id": checkpoint_id,
        "restored_files": undone.restored_files,
        "removed_paths": undone.removed_paths,
        "mode": undone.mode,
        "root": undone.root,
    }


def evaluate_apply_policy(
    rewrite_payload: dict[str, object],
    policy: ApplyPolicy,
    *,
    path: str,
    run_command_fn: CommandRunner | None = None,
    scan_runner_fn: ScanRunner | None = None,
) -> tuple[dict[str, object], int]:
    payload = dict(rewrite_payload)
    working_root = _policy_root(path, payload)
    target_path = _resolved_path(path)
    run_command = run_command_fn or _run_policy_command
    scan_runner = scan_runner_fn or _run_ruleset_scan_policy

    checks: list[dict[str, object]] = []
    failures = False

    def run_and_record(name: str, result: dict[str, object], *, top_level_key: str) -> bool:
        nonlocal failures
        payload[top_level_key] = dict(result)
        checks.append(_check_row(name, result))
        failed = not bool(result["passed"])
        failures = failures or failed
        return failed

    if policy.lint_cmd is not None:
        if run_and_record(
            "lint",
            run_command("lint", policy.lint_cmd, working_root, policy.timeout),
            top_level_key="lint_result",
        ) and policy.on_failure in {"rollback", "fail"}:
            pass
        else:
            if policy.test_cmd is not None:
                if run_and_record(
                    "test",
                    run_command("test", policy.test_cmd, working_root, policy.timeout),
                    top_level_key="test_result",
                ) and policy.on_failure in {"rollback", "fail"}:
                    pass
                else:
                    if policy.ruleset_scan is not None and policy.ruleset_scan.enabled:
                        run_and_record(
                            "scan",
                            scan_runner(policy.ruleset_scan, target_path, working_root),
                            top_level_key="scan_result",
                        )
            elif policy.ruleset_scan is not None and policy.ruleset_scan.enabled:
                run_and_record(
                    "scan",
                    scan_runner(policy.ruleset_scan, target_path, working_root),
                    top_level_key="scan_result",
                )
    else:
        if policy.test_cmd is not None:
            if run_and_record(
                "test",
                run_command("test", policy.test_cmd, working_root, policy.timeout),
                top_level_key="test_result",
            ) and policy.on_failure in {"rollback", "fail"}:
                pass
            else:
                if policy.ruleset_scan is not None and policy.ruleset_scan.enabled:
                    run_and_record(
                        "scan",
                        scan_runner(policy.ruleset_scan, target_path, working_root),
                        top_level_key="scan_result",
                    )
        elif policy.ruleset_scan is not None and policy.ruleset_scan.enabled:
            run_and_record(
                "scan",
                scan_runner(policy.ruleset_scan, target_path, working_root),
                top_level_key="scan_result",
            )

    if failures:
        if policy.on_failure == "warn":
            action_taken = "warn"
            exit_code = 0
        elif policy.on_failure == "rollback":
            payload["rollback"] = _rollback_summary(payload=payload, working_root=working_root)
            action_taken = "rollback"
            exit_code = 1
        else:
            action_taken = "fail"
            exit_code = 1
    else:
        action_taken = "none"
        exit_code = 0
        if policy.on_failure == "rollback" and "checkpoint" in payload:
            checkpoint_payload = payload.get("checkpoint")
            checkpoint_id = None
            if isinstance(checkpoint_payload, dict):
                raw_checkpoint_id = checkpoint_payload.get("checkpoint_id")
                if isinstance(raw_checkpoint_id, str) and raw_checkpoint_id.strip():
                    checkpoint_id = raw_checkpoint_id
            if checkpoint_id is not None:
                payload["rollback"] = {"performed": False, "checkpoint_id": checkpoint_id}

    payload["policy_result"] = {
        "policy_path": policy.path,
        "checks": checks,
        "all_passed": not failures,
        "action_taken": action_taken,
    }
    return payload, exit_code
