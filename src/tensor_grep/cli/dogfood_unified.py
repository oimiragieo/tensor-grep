"""Installed-artifact dogfood orchestration without checkout-script dependencies."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from tensor_grep.cli.checkout_environment import _read
from tensor_grep.cli.dogfood import (
    _build_verdict,
    _json_from_stdout,
    _write_json_atomic,
    run_dogfood_readiness,
)


def run_unified_dogfood(
    *, features: bool = False, all_checks: bool = False, **kwargs: Any
) -> tuple[int, dict[str, Any]]:
    if features and all_checks:
        raise ValueError("--features and --all are mutually exclusive")
    if not features and not all_checks:
        return run_dogfood_readiness(**kwargs)
    output = kwargs.pop("output", None)
    root = Path(kwargs["root"]).expanduser().resolve()
    timeout = kwargs.get("timeout_s") or 900.0
    expected_version_source = (
        "explicit" if kwargs.get("expected_version") else "installed-package-metadata"
    )
    checkout_version_error = None
    if not kwargs.get("expected_version"):
        try:
            project = tomllib.loads(_read(root / "pyproject.toml", root)).get("project", {})
            if str(project.get("name", "")).replace("_", "-").lower() == "tensor-grep":
                candidate_version = project.get("version")
                if isinstance(candidate_version, str):
                    kwargs["expected_version"] = candidate_version
                    expected_version_source = "selected-checkout-pyproject"
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError, RecursionError) as exc:
            # No trusted selected-checkout version; the runner still checks installed metadata.
            checkout_version_error = str(exc)
    env = dict(os.environ)
    # The caller's explicit selection always wins over PATH discovery in the packaged runner.
    selected = env.get("TG_BIN") or env.get("TG_NATIVE_TG_BINARY")
    if selected:
        env["TG_BIN"] = selected
    command = [
        sys.executable,
        "-I",
        "-m",
        "tensor_grep.cli.dogfood_features",
        "--timeout-s",
        str(timeout),
    ]
    if kwargs.get("expected_version"):
        command.extend(["--expected-version", kwargs["expected_version"]])
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout + 15,
            check=False,
        )
        feature_report = _json_from_stdout(completed.stdout)
        code = completed.returncode
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        feature_report = {
            "results": [
                {
                    "name": "packaged-feature-runner",
                    "status": "timed_out"
                    if isinstance(exc, subprocess.TimeoutExpired)
                    else "failed",
                    "message": str(exc),
                }
            ],
            "summary": {"failed": 1},
        }
        code = 1
    results = list(feature_report["results"])
    report: dict[str, Any] = {
        "artifact": "dogfood_readiness_report",
        "mode": "all" if all_checks else "features",
        "root": str(root),
        "feature_dogfood": feature_report,
        "expected_version": kwargs.get("expected_version"),
        "expected_version_source": expected_version_source,
        "checkout_version_error": checkout_version_error,
    }
    # Refuse mismatched or unverifiable artifacts before any checkout readiness checks.
    if all_checks and feature_report.get("artifact_identity"):
        readiness_code, readiness = run_dogfood_readiness(output=None, **kwargs)
        code = 1 if code != 0 or readiness_code != 0 else 0
        report.update(readiness)
        results.extend(readiness["agent_readiness"].get("results", []))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(result["name"], []).append(result)
    severity = {"passed": 0, "skipped": 1, "unavailable": 1, "timed_out": 3, "failed": 4}
    deduplicated = {}
    for name, evidence in grouped.items():
        worst: dict[str, Any] = max(
            evidence, key=lambda item: severity.get(item.get("status", "failed"), 4)
        )
        chosen = worst.copy()
        if len(evidence) > 1:
            chosen["duplicate_evidence"] = evidence
        deduplicated[name] = chosen
    results = list(deduplicated.values())
    summary = {
        status: sum(result.get("status") == status for result in results)
        for status in ("passed", "failed", "timed_out", "skipped", "unavailable")
    }
    merged = {"results": results, "summary": summary}
    report.update({
        "mode": "all" if all_checks else "features",
        "selected_checks": list(deduplicated),
        "agent_readiness": merged,
        "verdict": _build_verdict(merged, code),
        "tested_refusals": [
            result["name"] for result in results if result.get("tested_behavior") == "refusal"
        ],
        "tested_fallbacks": [
            result["name"] for result in results if result.get("tested_behavior") == "fallback"
        ],
        "unavailable_capabilities": [
            result["name"] for result in results if result.get("status") == "unavailable"
        ],
    })
    if output is not None:
        _write_json_atomic(output, report)
    return code, report
