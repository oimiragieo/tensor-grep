from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from tensor_grep.cli.progress import ProgressReporter

ARTIFACT_TAIL_LINE_LIMIT = 20
ARTIFACT_TAIL_LINE_CHAR_LIMIT = 4000
RELEASE_DOCS_GOVERNANCE_PATHS = (
    "AGENTS.md",
    "README.md",
    "SKILL.md",
    "docs/SESSION_HANDOFF.md",
    "docs/CONTINUATION_PLAN.md",
    "docs/CONTRACTS.md",
    "tests/unit/test_public_docs_governance.py",
)
RELEASE_DOCS_STAMP_COMMAND = "python scripts/stamp_release_assets.py"


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


def _bounded_tail_lines(
    text: str,
    *,
    line_limit: int = ARTIFACT_TAIL_LINE_LIMIT,
    char_limit: int = ARTIFACT_TAIL_LINE_CHAR_LIMIT,
) -> list[str]:
    tail: list[str] = []
    for line in text.splitlines()[-line_limit:]:
        if len(line) <= char_limit:
            tail.append(line)
        else:
            tail.append(f"{line[:char_limit]}... <truncated {len(line) - char_limit} chars>")
    return tail


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


def _build_world_class_readiness() -> dict[str, Any]:
    """Describe proof-gated surfaces that a passing dogfood gate does not promote."""
    return {
        "status": "not_claimed",
        "summary": (
            "PASS means the fast release-readiness gate passed; it is not proof "
            "that tensor-grep replaces rg, ast-grep, public GPU search, or "
            "production LSP-backed navigation."
        ),
        "limitations": [
            {
                "surface": "raw_cold_text_search",
                "status": "rg_remains_baseline",
                "required_evidence": (
                    "accepted benchmark artifacts showing semantic parity and "
                    "speed wins over rg for the declared workload class"
                ),
            },
            {
                "surface": "full_ast_grep_surface",
                "status": "validated_subset",
                "required_evidence": (
                    "implemented and tested parity for ast-grep run/scan/test/new "
                    "options before replacement claims"
                ),
            },
            {
                "surface": "public_gpu_acceleration",
                "status": "experimental_until_native_gpu_proof",
                "required_evidence": (
                    "declared workload class, NativeGpuBackend with sidecar_used=false, "
                    "1GB/5GB correctness, speed wins over both rg and tg_cpu, and "
                    "fair many-pattern comparison against rg -F -e ... -e ... when "
                    "claiming many-pattern GPU acceleration"
                ),
            },
            {
                "surface": "lsp_semantic_provider",
                "status": "experimental_until_lsp_proof",
                "required_evidence": (
                    "latency-bounded provider initialization and navigation payloads "
                    "with lsp_proof=true on accepted hardcase artifacts"
                ),
            },
            {
                "surface": "agent_target_selection_metrics",
                "status": "missing_enterprise_accuracy_gate",
                "required_evidence": (
                    "accepted target-selection metrics such as top-k hit rate, MRR, "
                    "false-primary rate, validation-command precision, and ambiguity "
                    "handling on mixed-language and noisy-repo hardcases"
                ),
            },
        ],
    }


def _parse_git_status_path(line: str) -> str:
    raw_path = line[3:].strip() if len(line) > 3 else line.strip()
    if " -> " in raw_path:
        raw_path = raw_path.rsplit(" -> ", 1)[1]
    return raw_path.strip('"')


def _build_release_docs_worktree_status(root: Path) -> dict[str, Any]:
    repo_root = root.expanduser().resolve()
    command = [
        "git",
        "-C",
        str(repo_root),
        "status",
        "--porcelain",
        "--",
        *RELEASE_DOCS_GOVERNANCE_PATHS,
    ]
    try:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return {
            "status": "unknown",
            "read_only": True,
            "tracked_paths": list(RELEASE_DOCS_GOVERNANCE_PATHS),
            "dirty_paths": [],
            "reason": str(exc),
        }
    if completed.returncode != 0:
        return {
            "status": "unknown",
            "read_only": True,
            "tracked_paths": list(RELEASE_DOCS_GOVERNANCE_PATHS),
            "dirty_paths": [],
            "stderr_tail": _bounded_tail_lines(completed.stderr),
        }
    dirty_paths = [
        _parse_git_status_path(line) for line in completed.stdout.splitlines() if line.strip()
    ]
    return {
        "status": "dirty" if dirty_paths else "clean",
        "read_only": True,
        "tracked_paths": list(RELEASE_DOCS_GOVERNANCE_PATHS),
        "dirty_paths": sorted(dirty_paths),
    }


def _build_write_policy(repo_root: Path, output: Path | None) -> dict[str, Any]:
    allowed_writes = [
        str((repo_root / "artifacts" / "agent_readiness").resolve()),
    ]
    if output is not None:
        allowed_writes.append(str(output.expanduser().resolve()))
    return {
        "mode": "read_only_except_explicit_output_and_readiness_probes",
        "allowed_writes": allowed_writes,
        "tracked_release_docs_mutation": "not_performed",
        "release_docs_stamp_command": RELEASE_DOCS_STAMP_COMMAND,
        "summary": (
            "tg dogfood validates readiness and only writes agent-readiness probe "
            "artifacts plus the explicit --output artifact when requested; release-doc "
            "stamping is a separate manual or release-workflow step."
        ),
    }


def _installed_tensor_grep_version() -> str | None:
    try:
        return version("tensor-grep")
    except PackageNotFoundError:
        return None


def _build_public_self_check_readiness(
    *,
    repo_root: Path,
    readiness_script: Path,
    expected_version: str | None,
) -> tuple[int, dict[str, Any]]:
    results: list[dict[str, Any]] = []
    results.append({
        "name": "public-package-import",
        "status": "passed",
        "message": "tensor_grep dogfood module is importable and running from the installed package",
    })
    installed_version = _installed_tensor_grep_version()
    if installed_version is None:
        results.append({
            "name": "public-package-metadata",
            "status": "skipped",
            "message": "tensor-grep package metadata is unavailable in this environment",
        })
    elif expected_version is not None and installed_version != expected_version:
        results.append({
            "name": "public-package-metadata",
            "status": "failed",
            "message": (
                f"installed tensor-grep version {installed_version} does not match "
                f"expected version {expected_version}"
            ),
        })
    else:
        results.append({
            "name": "public-package-metadata",
            "status": "passed",
            "message": f"installed tensor-grep package version {installed_version}",
        })

    results.append({
        "name": "repo-agent-readiness-script",
        "status": "skipped",
        "message": (
            f"repo-only checks are unavailable because {readiness_script} does not exist; "
            "point --root at a tensor-grep source checkout to run scripts/agent_readiness.py"
        ),
    })
    summary = {
        "passed": sum(1 for result in results if result["status"] == "passed"),
        "failed": sum(1 for result in results if result["status"] == "failed"),
        "skipped": sum(1 for result in results if result["status"] == "skipped"),
    }
    returncode = 1 if summary["failed"] else 0
    return returncode, {
        "artifact": "agent_readiness_report",
        "mode": "public-self-check",
        "root": str(repo_root),
        "expected_version": expected_version,
        "summary": summary,
        "results": results,
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
            returncode, agent_readiness = _build_public_self_check_readiness(
                repo_root=repo_root,
                readiness_script=readiness_script,
                expected_version=expected_version,
            )
            command = []
            stdout = ""
            stderr = ""
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
                            "stdout_tail": _bounded_tail_lines(stdout),
                            "stderr_tail": _bounded_tail_lines(stderr),
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
        "world_class_readiness": _build_world_class_readiness(),
        "write_policy": _build_write_policy(repo_root, output),
        "release_docs_worktree": _build_release_docs_worktree_status(repo_root),
        "stderr_tail": _bounded_tail_lines(stderr),
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return returncode, report
