from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tensor_grep.cli.progress import ProgressReporter


def _json_from_stdout(stdout: str) -> dict[str, Any]:
    stripped = stdout.strip()
    if not stripped:
        raise ValueError("agent-readiness emitted empty stdout")
    starts = [idx for idx in (stripped.find("{"), stripped.find("[")) if idx >= 0]
    if not starts:
        raise ValueError("agent-readiness stdout did not contain JSON")
    payload, _offset = json.JSONDecoder().raw_decode(stripped[min(starts) :])
    if not isinstance(payload, dict):
        raise ValueError("agent-readiness JSON must be an object")
    return payload


def _build_verdict(agent_readiness: dict[str, Any], returncode: int) -> dict[str, Any]:
    summary = agent_readiness.get("summary")
    failed = 1
    if isinstance(summary, dict):
        raw_failed = summary.get("failed", 0)
        failed = int(raw_failed) if isinstance(raw_failed, int) else 1
    failed_checks = [
        str(result.get("name"))
        for result in agent_readiness.get("results", [])
        if isinstance(result, dict) and result.get("status") == "failed"
    ]
    status = "PASS" if returncode == 0 and failed == 0 else "FAIL"
    return {
        "status": status,
        "failed_checks": failed_checks,
        "summary": "agent-readiness passed" if status == "PASS" else "agent-readiness failed",
    }


def run_dogfood_readiness(
    *,
    root: Path,
    output: Path | None = None,
    expected_version: str | None = None,
    include_shell_probes: bool = True,
    include_wsl_probe: bool = True,
    progress_mode: str = "auto",
    progress_interval_s: float = 30.0,
    json_output: bool = False,
) -> tuple[int, dict[str, Any]]:
    repo_root = root.expanduser().resolve()
    readiness_script = repo_root / "scripts" / "agent_readiness.py"
    command = [
        sys.executable,
        str(readiness_script),
        "--root",
        str(repo_root),
        "--json",
    ]
    if expected_version:
        command.extend(["--expected-version", expected_version])
    if not include_shell_probes:
        command.append("--no-shell-probes")
    if not include_wsl_probe:
        command.append("--no-wsl-probe")

    progress = ProgressReporter(
        mode=progress_mode,
        interval_s=progress_interval_s,
        json_output=json_output,
    )
    with progress.phase("agent-readiness"):
        if not readiness_script.exists():
            agent_readiness = {
                "artifact": "agent_readiness_report",
                "root": str(repo_root),
                "summary": {"passed": 0, "failed": 1, "skipped": 0},
                "results": [
                    {
                        "name": "agent-readiness-script",
                        "status": "failed",
                        "message": f"missing readiness script: {readiness_script}",
                    }
                ],
            }
            returncode = 1
            stdout = ""
            stderr = f"missing readiness script: {readiness_script}"
        else:
            env = dict(os.environ)
            env.setdefault("PYTHONUTF8", "1")
            completed = subprocess.run(
                command,
                cwd=repo_root,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            returncode = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            try:
                agent_readiness = _json_from_stdout(stdout)
            except ValueError as exc:
                agent_readiness = {
                    "artifact": "agent_readiness_report",
                    "root": str(repo_root),
                    "summary": {"passed": 0, "failed": 1, "skipped": 0},
                    "results": [
                        {
                            "name": "agent-readiness-json",
                            "status": "failed",
                            "message": str(exc),
                            "stdout_tail": stdout.splitlines()[-20:],
                            "stderr_tail": stderr.splitlines()[-20:],
                        }
                    ],
                }
                returncode = 1

    report = {
        "artifact": "dogfood_readiness_report",
        "dogfood_version": 1,
        "root": str(repo_root),
        "command": command,
        "agent_readiness": agent_readiness,
        "verdict": _build_verdict(agent_readiness, returncode),
        "stderr_tail": stderr.splitlines()[-20:],
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return returncode, report
