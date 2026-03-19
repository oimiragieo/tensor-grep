"""Formal Milestone 2 compatibility gate.

Runtime contract notes:
- Use `--no-ignore` for both `tg.exe` and `rg` because `bench_data/*.log` matches the
  repository's `*.log` ignore pattern.
- `rg.exe` is not expected to be on PATH on Windows. Resolve it from `TG_RG_PATH` first,
  then fall back to the bundled `benchmarks/rg.zip` archive.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

WINDOWS_RG_DIRNAME = "ripgrep-14.1.0-x86_64-pc-windows-msvc"


class CommandResult(NamedTuple):
    exit_code: int
    stdout: str
    stderr: str


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def default_schema_path() -> Path:
    return ROOT_DIR / "tests" / "schemas" / "tg_output.schema.json"


def resolve_bench_data_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_COMPAT_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "bench_data"


def extract_windows_rg_bundle(benchmarks_dir: Path) -> Path | None:
    archive = benchmarks_dir / "rg.zip"
    if not archive.exists():
        return None

    with zipfile.ZipFile(archive) as bundle:
        rg_member = next((name for name in bundle.namelist() if name.endswith("/rg.exe")), None)
        if rg_member is None:
            return None
        bundle.extractall(benchmarks_dir)

    extracted = benchmarks_dir / Path(rg_member)
    if extracted.exists():
        return extracted
    return None


def resolve_rg_binary() -> Path:
    if override := os.environ.get("TG_RG_PATH"):
        candidate = Path(override).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(
            f"TG_RG_PATH points to a missing ripgrep binary: {candidate}. "
            "Set TG_RG_PATH to rg.exe or keep benchmarks/rg.zip available."
        )

    for name in ("rg", "rg.exe"):
        if resolved := shutil.which(name):
            return Path(resolved)

    benchmarks_dir = Path(__file__).resolve().parent
    local = benchmarks_dir / WINDOWS_RG_DIRNAME / "rg.exe"
    if local.exists():
        return local

    if platform.system() == "Windows":
        if extracted := extract_windows_rg_bundle(benchmarks_dir):
            return extracted

    raise FileNotFoundError(
        "ripgrep binary not found. Set TG_RG_PATH to rg.exe or provide benchmarks/rg.zip."
    )


def build_scenarios(bench_data_dir: Path, tg_binary: Path, rg_binary: Path) -> list[dict[str, Any]]:
    return [
        {
            "name": "1. Simple String Match",
            "comparison": "sorted_lines",
            "rg_cmd": [str(rg_binary), "--no-ignore", "ERROR", str(bench_data_dir)],
            "tg_cmd": [
                str(tg_binary),
                "search",
                "--no-ignore",
                "ERROR",
                str(bench_data_dir),
            ],
        },
        {
            "name": "2. Case-Insensitive Match",
            "comparison": "sorted_lines",
            "rg_cmd": [str(rg_binary), "--no-ignore", "-i", "warning", str(bench_data_dir)],
            "tg_cmd": [
                str(tg_binary),
                "search",
                "--no-ignore",
                "-i",
                "warning",
                str(bench_data_dir),
            ],
        },
        {
            "name": "3. Regex Match",
            "comparison": "sorted_lines",
            "rg_cmd": [str(rg_binary), "--no-ignore", r"ERROR.*timeout", str(bench_data_dir)],
            "tg_cmd": [
                str(tg_binary),
                "search",
                "--no-ignore",
                r"ERROR.*timeout",
                str(bench_data_dir),
            ],
        },
        {
            "name": "4. Invert Match",
            "comparison": "sorted_lines",
            "rg_cmd": [str(rg_binary), "--no-ignore", "-v", "INFO", str(bench_data_dir)],
            "tg_cmd": [
                str(tg_binary),
                "search",
                "--no-ignore",
                "-v",
                "INFO",
                str(bench_data_dir),
            ],
        },
        {
            "name": "5. Count Matches",
            "comparison": "count",
            "rg_cmd": [str(rg_binary), "--no-ignore", "-c", "ERROR", str(bench_data_dir)],
            "tg_cmd": [
                str(tg_binary),
                "search",
                "--no-ignore",
                "-c",
                "ERROR",
                str(bench_data_dir),
            ],
        },
        {
            "name": "6. Context Lines (Before & After)",
            "comparison": "sorted_lines",
            "rg_cmd": [
                str(rg_binary),
                "--no-ignore",
                "-C",
                "2",
                "CRITICAL",
                str(bench_data_dir),
            ],
            "tg_cmd": [
                str(tg_binary),
                "search",
                "--no-ignore",
                "-C",
                "2",
                "CRITICAL",
                str(bench_data_dir),
            ],
        },
        {
            "name": "7. Max Count Limit",
            "comparison": "sorted_lines",
            "rg_cmd": [str(rg_binary), "--no-ignore", "-m", "5", "ERROR", str(bench_data_dir)],
            "tg_cmd": [
                str(tg_binary),
                "search",
                "--no-ignore",
                "-m",
                "5",
                "ERROR",
                str(bench_data_dir),
            ],
        },
        {
            "name": "8. File Glob Filtering",
            "comparison": "sorted_lines",
            "rg_cmd": [
                str(rg_binary),
                "--no-ignore",
                "-g",
                "*.log",
                "ERROR",
                str(bench_data_dir),
            ],
            "tg_cmd": [
                str(tg_binary),
                "search",
                "--no-ignore",
                "--glob=*.log",
                "ERROR",
                str(bench_data_dir),
            ],
        },
    ]


def run_command(
    cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None
) -> CommandResult:
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=cwd,
        env=env,
    )
    return CommandResult(
        exit_code=int(completed.returncode),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def normalize_lines(output: str) -> list[str]:
    normalized = output.replace("\r\n", "\n").replace("\\", "/")
    return sorted({line.strip() for line in normalized.splitlines() if line.strip()})


def extract_total_count(output: str) -> int:
    total = 0
    for line in output.replace("\r\n", "\n").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.isdigit():
            total += int(stripped)
            continue
        value = stripped.rsplit(":", 1)[-1]
        if value.isdigit():
            total += int(value)
    return total


def compare_scenario(
    scenario: dict[str, Any], rg_result: CommandResult, tg_result: CommandResult
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "name": scenario["name"],
        "comparison": scenario["comparison"],
        "rg_exit_code": rg_result.exit_code,
        "tg_exit_code": tg_result.exit_code,
    }

    if rg_result.exit_code != tg_result.exit_code:
        report["status"] = "FAIL"
        report["reason"] = "exit-code-mismatch"
        return report

    if scenario["comparison"] == "count":
        rg_count = extract_total_count(rg_result.stdout)
        tg_count = extract_total_count(tg_result.stdout)
        report["rg_count"] = rg_count
        report["tg_count"] = tg_count
        report["status"] = "PASS" if rg_count == tg_count else "FAIL"
        if report["status"] == "FAIL":
            report["reason"] = "count-mismatch"
        return report

    rg_lines = normalize_lines(rg_result.stdout)
    tg_lines = normalize_lines(tg_result.stdout)
    report["missing_lines"] = sorted(line for line in rg_lines if line not in tg_lines)
    report["extra_lines"] = sorted(line for line in tg_lines if line not in rg_lines)
    report["status"] = (
        "PASS" if not report["missing_lines"] and not report["extra_lines"] else "FAIL"
    )
    if report["status"] == "FAIL":
        report["reason"] = "sorted-line-diff"
    return report


def validate_json_instance(payload: dict[str, Any], schema_path: Path) -> None:
    try:
        from jsonschema import validate
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "jsonschema is required for routing metadata validation. "
            "Install it or run `python -m pip install jsonschema`."
        ) from exc

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validate(instance=payload, schema=schema)


def validate_routing_metadata(
    tg_binary: Path, bench_data_dir: Path, schema_path: Path, rg_binary: Path
) -> dict[str, Any]:
    env = os.environ.copy()
    env["TG_RG_PATH"] = str(rg_binary)
    command = [str(tg_binary), "--json", "ERROR", str(bench_data_dir)]
    result = run_command(command, env=env, cwd=ROOT_DIR)

    report: dict[str, Any] = {
        "command": command,
        "exit_code": result.exit_code,
        "valid": False,
        "schema_path": str(schema_path),
    }
    if result.exit_code != 0:
        report["error"] = result.stderr.strip() or "tg --json failed"
        return report

    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise TypeError("routing metadata output must be a JSON object")
        validate_json_instance(payload, schema_path)
    except Exception as exc:  # pragma: no cover - exercised via tests by monkeypatching
        report["error"] = str(exc)
        return report

    report["valid"] = True
    report["payload"] = payload
    return report


def run_pytest_suite() -> dict[str, Any]:
    command = ["uv", "run", "pytest", "-q"]
    result = run_command(command, cwd=ROOT_DIR)
    summary = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    return {
        "command": " ".join(command),
        "exit_code": result.exit_code,
        "summary": summary,
        "passed": result.exit_code == 0,
    }


def run_compat_suite(
    *, tg_binary: Path, rg_binary: Path, bench_data_dir: Path, schema_path: Path
) -> dict[str, Any]:
    env = os.environ.copy()
    env["TG_RG_PATH"] = str(rg_binary)

    scenario_reports: list[dict[str, Any]] = []
    for scenario in build_scenarios(bench_data_dir, tg_binary, rg_binary):
        rg_result = run_command(scenario["rg_cmd"], cwd=ROOT_DIR)
        tg_result = run_command(scenario["tg_cmd"], env=env, cwd=ROOT_DIR)
        scenario_reports.append(compare_scenario(scenario, rg_result, tg_result))

    routing_report = validate_routing_metadata(tg_binary, bench_data_dir, schema_path, rg_binary)
    pytest_report = run_pytest_suite()
    scenario_failures = sum(1 for scenario in scenario_reports if scenario["status"] != "PASS")
    all_passed = scenario_failures == 0 and routing_report["valid"] and pytest_report["passed"]

    return {
        "suite": "run_compat_checks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "tg_binary": str(tg_binary),
        "rg_binary": str(rg_binary),
        "bench_data_dir": str(bench_data_dir),
        "scenario_failures": scenario_failures,
        "routing_metadata": routing_report,
        "pytest": pytest_report,
        "scenarios": scenario_reports,
        "all_passed": all_passed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the CLI parity and compatibility gate. Both tools are forced to use "
            "--no-ignore so bench_data/*.log files are searched, and TG_RG_PATH/benchmarks/rg.zip "
            "are used to locate rg.exe on Windows."
        )
    )
    parser.add_argument(
        "--binary",
        default=str(default_binary_path()),
        help="Path to the tg executable to validate.",
    )
    parser.add_argument(
        "--bench-data-dir",
        default=str(resolve_bench_data_dir()),
        help="Path to the benchmark search corpus.",
    )
    parser.add_argument(
        "--schema",
        default=str(default_schema_path()),
        help="Path to the tg JSON routing metadata schema.",
    )
    parser.add_argument(
        "--output",
        help="Optional path for the compat JSON report. Defaults to artifacts/compat_report.json.",
    )
    return parser.parse_args()


def main() -> int:
    from tensor_grep.perf_guard import ensure_artifacts_dir, write_json

    args = parse_args()
    tg_binary = Path(args.binary)
    bench_data_dir = Path(args.bench_data_dir)
    schema_path = Path(args.schema)

    if not tg_binary.exists():
        print(f"tg binary not found: {tg_binary}", file=sys.stderr)
        return 2
    if not bench_data_dir.exists():
        print(f"Benchmark data directory not found: {bench_data_dir}", file=sys.stderr)
        return 2
    if not schema_path.exists():
        print(f"Schema file not found: {schema_path}", file=sys.stderr)
        return 2

    try:
        rg_binary = resolve_rg_binary()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    report = run_compat_suite(
        tg_binary=tg_binary,
        rg_binary=rg_binary,
        bench_data_dir=bench_data_dir,
        schema_path=schema_path,
    )

    output_path = (
        Path(args.output) if args.output else ensure_artifacts_dir(ROOT_DIR) / "compat_report.json"
    )
    write_json(output_path, report)

    print(f"compat_report.json written to {output_path}")
    for scenario in report["scenarios"]:
        print(f"{scenario['name']}: {scenario['status']}")
    print(f"routing metadata: {'PASS' if report['routing_metadata']['valid'] else 'FAIL'}")
    print(f"pytest gate: {'PASS' if report['pytest']['passed'] else 'FAIL'}")

    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
