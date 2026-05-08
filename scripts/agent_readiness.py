from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = os.name == "nt"


class ReadinessError(RuntimeError):
    """Raised when an agent-readiness check returns invalid output."""


Validator = Callable[[str, Path, str], None]


class Check(NamedTuple):
    name: str
    command: list[str]
    description: str
    timeout_s: int = 60
    validator: Validator | None = None
    required: bool = True


def read_project_version(repo_root: Path) -> str:
    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _json_from_stdout(stdout: str) -> Any:
    stripped = stdout.strip()
    if not stripped:
        raise ReadinessError("expected JSON output, got empty stdout")
    starts = [idx for idx in (stripped.find("{"), stripped.find("[")) if idx >= 0]
    if not starts:
        raise ReadinessError("expected JSON output, found no JSON object or array")
    decoder = json.JSONDecoder()
    payload, _offset = decoder.raw_decode(stripped[min(starts) :])
    return payload


def _norm_path(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\\", "/").rstrip("/").lower()


def validate_version_output(stdout: str, _repo_root: Path, expected_version: str) -> None:
    expected_lines = {f"tensor-grep {expected_version}", f"tg {expected_version}"}
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not expected_lines.intersection(lines):
        raise ReadinessError(
            f"expected one of {sorted(expected_lines)!r} in version output, got {lines or ['<empty>']}"
        )


def validate_windows_launcher_quoted_patterns(
    _stdout: str, repo_root: Path, _expected_version: str
) -> None:
    if not IS_WINDOWS:
        return

    probe_dir = repo_root / "artifacts" / "agent_readiness"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_file = probe_dir / "launcher_argv_probe.txt"
    probe_file.write_text("agent launcher sentinel\nplain text\n", encoding="utf-8")

    pattern = "agent no-such-phrase"
    tg_cmd = shutil.which("tg.cmd")
    if not tg_cmd:
        raise ReadinessError("could not resolve public tg.cmd for quoted-argument probe")

    cases = [
        (
            "cmd /c tg via Python subprocess.run([...])",
            ["cmd", "/c", "tg", "search", pattern, str(probe_file)],
        ),
        (
            "direct tg.cmd via Python subprocess.run([...])",
            [tg_cmd, "search", pattern, str(probe_file)],
        ),
    ]
    for label, command in cases:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if completed.returncode != 1 or stdout or "no-such-phrase" in stderr:
            raise ReadinessError(
                f"{label} did not preserve quoted multi-word no-match pattern; "
                f"exit={completed.returncode}, stdout={stdout or '<empty>'}, "
                f"stderr={stderr or '<empty>'}"
            )


def validate_doctor_payload(stdout: str, _repo_root: Path, expected_version: str) -> None:
    payload = _json_from_stdout(stdout)
    if not isinstance(payload, dict):
        raise ReadinessError("doctor JSON must be an object")
    if payload.get("version") != expected_version:
        raise ReadinessError(
            f"doctor version mismatch: expected {expected_version}, got {payload.get('version')}"
        )
    first_matches = payload.get("path_tg_first_version_matches")
    if first_matches is False:
        raise ReadinessError("doctor reports PATH first tg version does not match")
    backend = payload.get("search_acceleration_backend")
    if backend not in {
        "rust-core-extension",
        "native-standalone",
        "standalone-native-tg",
        "python",
    }:
        raise ReadinessError(f"unexpected search acceleration backend: {backend!r}")


def validate_context_render_payload(
    stdout: str, _repo_root: Path | None = None, *, expected_fragment: str
) -> None:
    payload = _json_from_stdout(stdout)
    if not isinstance(payload, dict):
        raise ReadinessError("context-render JSON must be an object")

    primary_file = _norm_path((payload.get("edit_plan_seed") or {}).get("primary_file"))
    if not primary_file:
        raise ReadinessError("context-render missing edit_plan_seed.primary_file")

    consistency = payload.get("context_consistency")
    if not isinstance(consistency, dict):
        raise ReadinessError("context-render missing context_consistency object")

    selected_files = {
        _norm_path(item.get("path")) for item in payload.get("files", []) if isinstance(item, dict)
    }
    selected_sources = {
        _norm_path(item.get("file") or item.get("path"))
        for item in payload.get("sources", [])
        if isinstance(item, dict)
    }
    follow_ups = {
        _norm_path(item.get("path") or item.get("file"))
        for item in (payload.get("navigation_pack") or {}).get("follow_up_reads", [])
        if isinstance(item, dict)
    }
    represented_paths = selected_files | selected_sources | follow_ups
    is_represented = any(
        path and (path == primary_file or path.endswith(primary_file)) for path in represented_paths
    )
    omitted_primary = consistency.get("omitted_primary_file")
    if not is_represented and not omitted_primary:
        raise ReadinessError(
            "edit_plan_seed.primary_file is not represented and no omission reason was emitted"
        )

    nav_file = _norm_path(
        ((payload.get("navigation_pack") or {}).get("primary_target") or {}).get("file")
    )
    if (
        nav_file
        and primary_file
        and nav_file != primary_file
        and not nav_file.endswith(primary_file)
    ):
        raise ReadinessError(
            "navigation_pack.primary_target contradicts edit_plan_seed.primary_file"
        )

    rendered_context = str(payload.get("rendered_context") or "")
    rendered_sources = "\n".join(
        str(item.get("rendered_source") or "")
        for item in payload.get("sources", [])
        if isinstance(item, dict)
    )
    if expected_fragment not in rendered_context and expected_fragment not in rendered_sources:
        raise ReadinessError(f"missing expected context fragment: {expected_fragment}")


def _validate_invoice_context(stdout: str, repo_root: Path, _expected_version: str) -> None:
    validate_context_render_payload(
        stdout, repo_root, expected_fragment="tax = subtotal * TAX_RATE"
    )


def validate_ast_info(stdout: str, _repo_root: Path, _expected_version: str) -> None:
    payload = _json_from_stdout(stdout)
    languages: object
    if isinstance(payload, dict):
        languages = payload.get("languages")
    else:
        languages = payload
    if not isinstance(languages, list) or "python" not in {str(item) for item in languages}:
        raise ReadinessError("ast-info JSON must expose the python language identifier")


def validate_ast_run(stdout: str, _repo_root: Path, _expected_version: str) -> None:
    payload = _json_from_stdout(stdout)
    text = json.dumps(payload, sort_keys=True)
    if "class" not in text and "name" not in text:
        raise ReadinessError("AST run smoke did not emit a class match payload")


def validate_docs_claims(_stdout: str, repo_root: Path, expected_version: str) -> None:
    required_docs = [
        repo_root / "AGENTS.md",
        repo_root / "README.md",
        repo_root / "SKILL.md",
        repo_root / "docs" / "SESSION_HANDOFF.md",
        repo_root / "docs" / "CONTINUATION_PLAN.md",
        repo_root / "docs" / "CONTRACTS.md",
    ]
    required_fragments = [
        f"v{expected_version}",
        "python scripts/agent_readiness.py",
        "context_consistency",
        "validated compatibility set",
        "broad generated-root scan",
        "rg` remains",
        "ast-grep",
    ]
    missing: list[str] = []
    for path in required_docs:
        content = path.read_text(encoding="utf-8")
        for fragment in required_fragments:
            if fragment not in content:
                missing.append(f"{path.relative_to(repo_root)} missing `{fragment}`")
    if missing:
        raise ReadinessError("; ".join(missing))


def build_check_plan(
    *,
    repo_root: Path,
    expected_version: str,
    include_shell_probes: bool,
    include_wsl_probe: bool,
) -> list[Check]:
    checks: list[Check] = []
    if include_shell_probes:
        powershell_probe = (
            ["powershell", "-NoProfile", "-Command", "tg --version"]
            if IS_WINDOWS
            else ["tg", "--version"]
        )
        checks.extend([
            Check(
                name="public-version-powershell",
                command=powershell_probe,
                description="Verify profiled shell public tg version.",
                timeout_s=30,
                validator=validate_version_output,
            ),
            Check(
                name="public-version-cmd",
                command=["cmd", "/c", "tg --version"],
                description="Verify cmd.exe public tg version.",
                timeout_s=30,
                validator=validate_version_output,
                required=False,
            ),
            Check(
                name="public-version-pwsh-noprofile",
                command=["pwsh", "-NoProfile", "-Command", "tg --version"],
                description="Verify unprofiled PowerShell public tg version.",
                timeout_s=30,
                validator=validate_version_output,
                required=False,
            ),
            Check(
                name="public-version-git-bash",
                command=["bash", "-lc", "command -v tg && tg --version"],
                description="Verify Git Bash/no-extension shim public tg version.",
                timeout_s=30,
                validator=validate_version_output,
                required=False,
            ),
        ])
        if include_wsl_probe:
            checks.append(
                Check(
                    name="public-version-wsl",
                    command=["wsl", "bash", "-lc", "command -v tg && tg --version"],
                    description="Verify WSL public tg shim version.",
                    timeout_s=30,
                    validator=validate_version_output,
                    required=False,
                )
            )
        if IS_WINDOWS:
            checks.append(
                Check(
                    name="public-windows-launcher-quoted-patterns",
                    command=[],
                    description=(
                        "Verify cmd.exe and direct tg.cmd preserve quoted multi-word "
                        "no-match patterns."
                    ),
                    timeout_s=30,
                    validator=validate_windows_launcher_quoted_patterns,
                )
            )

    checks.extend([
        Check(
            name="repo-doctor",
            command=["uv", "run", "tg", "doctor", "--json", "--no-lsp"],
            description="Verify repo tg doctor reports version and PATH parity.",
            timeout_s=90,
            validator=validate_doctor_payload,
        ),
        Check(
            name="context-render-trust",
            command=[
                "uv",
                "run",
                "tg",
                "context-render",
                "tests/unit/test_trust_planning.py",
                "--query",
                "invoice tax calculation",
                "--json",
            ],
            description="Verify context-render keeps primary target and body context useful.",
            timeout_s=90,
            validator=_validate_invoice_context,
        ),
        Check(
            name="rg-parity-edges",
            command=["uv", "run", "pytest", "tests/e2e/test_rg_parity_edges.py", "-q"],
            description="Verify deterministic rg parity edge cases.",
            timeout_s=180,
        ),
        Check(
            name="broad-generated-scan-guard",
            command=[
                "uv",
                "run",
                "pytest",
                "tests/unit/test_cli_modes.py",
                "-q",
                "-k",
                "broad_generated_root_scan",
            ],
            description="Verify broad generated-root file-list scans require bounds or opt-in.",
            timeout_s=120,
        ),
        Check(
            name="ast-info-json",
            command=["uv", "run", "tg", "ast-info", "--json"],
            description="Verify AST language inventory JSON is parseable.",
            timeout_s=60,
            validator=validate_ast_info,
        ),
        Check(
            name="ast-run-smoke",
            command=[
                "uv",
                "run",
                "tg",
                "run",
                "class $NAME: $$$BODY",
                "tests/unit/test_trust_planning.py",
                "--lang",
                "python",
                "--json",
            ],
            description="Verify AST run smoke through the repo CLI.",
            timeout_s=90,
            validator=validate_ast_run,
        ),
        Check(
            name="mcp-context-render-smoke",
            command=[
                "uv",
                "run",
                "pytest",
                "tests/unit/test_mcp_server.py",
                "-q",
                "-k",
                "test_tg_context_render_mcp_preserves_invoice_tax_body_and_primary_target",
            ],
            description="Verify MCP context-render preserves invoice tax body and target.",
            timeout_s=120,
        ),
        Check(
            name="docs-claim-check",
            command=[],
            description="Verify public docs keep the current positioning and gate command.",
            timeout_s=5,
            validator=validate_docs_claims,
        ),
    ])
    return checks


def _command_available(command: list[str]) -> bool:
    if not command:
        return True
    return shutil.which(command[0]) is not None


def run_check(check: Check, *, repo_root: Path, expected_version: str) -> dict[str, Any]:
    started = time.monotonic()
    if not _command_available(check.command):
        if check.required:
            status = "failed"
            message = f"required command not found: {check.command[0]}"
        else:
            status = "skipped"
            message = f"optional command not found: {check.command[0]}"
        return {
            "name": check.name,
            "status": status,
            "duration_s": round(time.monotonic() - started, 3),
            "command": check.command,
            "message": message,
        }

    stdout = ""
    stderr = ""
    returncode = 0
    try:
        if check.command:
            env = dict(os.environ)
            env.setdefault("PYTHONUTF8", "1")
            completed = subprocess.run(
                check.command,
                cwd=repo_root,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=check.timeout_s,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            returncode = completed.returncode
            if completed.returncode != 0:
                raise ReadinessError(
                    f"exit {completed.returncode}; stderr={stderr.strip() or '<empty>'}"
                )
        if check.validator is not None:
            check.validator(stdout, repo_root, expected_version)
    except subprocess.TimeoutExpired as exc:
        status = "failed"
        message = f"timed out after {check.timeout_s}s"
        stdout = str(exc.stdout or "")
        stderr = str(exc.stderr or "")
    except ReadinessError as exc:
        status = "failed"
        message = str(exc)
    else:
        status = "passed"
        message = "ok"

    return {
        "name": check.name,
        "status": status,
        "duration_s": round(time.monotonic() - started, 3),
        "command": check.command,
        "returncode": returncode,
        "message": message,
        "stdout_tail": stdout.splitlines()[-20:],
        "stderr_tail": stderr.splitlines()[-20:],
    }


def build_report(
    *,
    results: list[dict[str, Any]],
    expected_version: str,
    repo_root: Path,
) -> dict[str, Any]:
    return {
        "artifact": "agent_readiness_report",
        "expected_version": expected_version,
        "root": str(repo_root.resolve()),
        "summary": {
            "passed": sum(1 for result in results if result["status"] == "passed"),
            "failed": sum(1 for result in results if result["status"] == "failed"),
            "skipped": sum(1 for result in results if result["status"] == "skipped"),
        },
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the fast tensor-grep agent-readiness dogfood gate."
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root to validate.")
    parser.add_argument(
        "--expected-version", help="Expected tensor-grep version. Defaults to pyproject."
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout.")
    parser.add_argument(
        "--no-shell-probes", action="store_true", help="Skip public shell version probes."
    )
    parser.add_argument("--no-wsl-probe", action="store_true", help="Skip the optional WSL probe.")
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    expected_version = args.expected_version or read_project_version(repo_root)
    checks = build_check_plan(
        repo_root=repo_root,
        expected_version=expected_version,
        include_shell_probes=not args.no_shell_probes,
        include_wsl_probe=not args.no_wsl_probe,
    )

    results: list[dict[str, Any]] = []
    for check in checks:
        result = run_check(check, repo_root=repo_root, expected_version=expected_version)
        results.append(result)
        if not args.json:
            print(
                f"[{result['status'].upper()}] {check.name} "
                f"({result['duration_s']}s): {result['message']}"
            )

    report = build_report(results=results, expected_version=expected_version, repo_root=repo_root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
