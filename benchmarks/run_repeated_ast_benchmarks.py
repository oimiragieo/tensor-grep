from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BIN_PATH = ROOT_DIR / "rust_core" / "target" / "release" / "tg.exe"


def run_cmd(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[float, int]:
    start = time.perf_counter()
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    end = time.perf_counter()
    return end - start, result.returncode


def _write_rules(rules_dir: Path, rule_count: int) -> None:
    rules_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(rule_count):
        pattern = '"def $FUNC():\\n    $$$BODY"' if idx % 2 == 0 else '"class $NAME:\\n    $$$BODY"'
        (rules_dir / f"rule_{idx:03d}.yml").write_text(
            f"id: rule-{idx}\nlanguage: python\nrule:\n  pattern: {pattern}\n",
            encoding="utf-8",
        )


def _write_tests(tests_dir: Path, rule_count: int) -> None:
    tests_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(rule_count):
        if idx % 2 == 0:
            invalid_case = "  - |\n      def sample_function():\n          return 1\n"
        else:
            invalid_case = (
                "  - |\n"
                "      class SampleClass:\n"
                "          def __init__(self):\n"
                "              pass\n"
            )
        (tests_dir / f"test_{idx:03d}.yml").write_text(
            f"id: test-{idx}\nruleId: rule-{idx}\nvalid:\n  - ok\ninvalid:\n{invalid_case}",
            encoding="utf-8",
        )


def _write_source_files(root: Path, file_count: int) -> None:
    for idx in range(file_count):
        (root / f"module_{idx:03d}.py").write_text(
            "class SampleClass:\n    def __init__(self):\n        pass\ndef sample_function():\n    return 1\n",
            encoding="utf-8",
        )


def setup_bench_project(root: Path, rule_count=100, file_count=10):
    root.mkdir(parents=True, exist_ok=True)
    (root / "sgconfig.yml").write_text(
        "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n", encoding="utf-8"
    )
    _write_source_files(root, file_count)
    _write_rules(root / "rules", rule_count)
    _write_tests(root / "tests", rule_count)


def main():
    if not BIN_PATH.exists():
        print(f"Error: {BIN_PATH} not found. Build it with 'cargo build --release' in rust_core.")
        return 1

    # Scenario 1: Moderate project
    bench_dir = ROOT_DIR / "artifacts" / "bench_repeated_ast"
    if bench_dir.exists():
        import shutil

        shutil.rmtree(bench_dir)
    setup_bench_project(bench_dir, rule_count=100, file_count=10)

    # Scenario 2: Large project (100 rules, 1000 files)
    large_dir = ROOT_DIR / "artifacts" / "bench_repeated_ast_large"
    if large_dir.exists():
        import shutil

        shutil.rmtree(large_dir)
    setup_bench_project(large_dir, rule_count=100, file_count=1000)

    # Scenario 3: Micro project (to show startup win)
    micro_dir = ROOT_DIR / "artifacts" / "bench_repeated_ast_micro"
    if micro_dir.exists():
        import shutil

        shutil.rmtree(micro_dir)
    setup_bench_project(micro_dir, rule_count=1, file_count=1)

    results = []

    for name, b_dir in [("moderate", bench_dir), ("large", large_dir), ("micro", micro_dir)]:
        # Ensure clean state
        subprocess.run([str(BIN_PATH), "worker", "--stop"], capture_output=True, cwd=str(b_dir))

        # 1. Cold path
        cold_scan_times = []
        cold_test_times = []
        for _ in range(5):  # Reduced iterations for large project
            t, code = run_cmd([str(BIN_PATH), "scan"], cwd=b_dir)
            if code == 0:
                cold_scan_times.append(t)
            t, code = run_cmd([str(BIN_PATH), "test"], cwd=b_dir)
            if code == 0:
                cold_test_times.append(t)

        def avg(lst):
            return sum(lst[1:]) / (len(lst) - 1) if len(lst) > 1 else (lst[0] if lst else 0)

        results.append({
            "name": f"ast_scan_{name}_cold",
            "tg_time_s": avg(cold_scan_times),
            "backend": "native",
        })
        results.append({
            "name": f"ast_test_{name}_cold",
            "tg_time_s": avg(cold_test_times),
            "backend": "native",
        })

        # 2. Resident path
        subprocess.Popen([str(BIN_PATH), "worker", "--port", "12349"], cwd=str(b_dir))
        port_file = b_dir / ".tg_cache" / "ast" / "worker_port.txt"
        for _ in range(50):
            if port_file.exists():
                break
            time.sleep(0.1)

        resident_scan_times = []
        resident_test_times = []
        env = os.environ.copy()
        env["TG_RESIDENT_AST"] = "1"

        for _ in range(5):  # Reduced iterations for large project
            t, code = run_cmd([str(BIN_PATH), "scan"], cwd=b_dir, env=env)
            if code == 0:
                resident_scan_times.append(t)
            t, code = run_cmd([str(BIN_PATH), "test"], cwd=b_dir, env=env)
            if code == 0:
                resident_test_times.append(t)

        results.append({
            "name": f"ast_scan_{name}_resident",
            "tg_time_s": avg(resident_scan_times),
            "backend": "native_resident",
        })
        results.append({
            "name": f"ast_test_{name}_resident",
            "tg_time_s": avg(resident_test_times),
            "backend": "native_resident",
        })

        subprocess.run([str(BIN_PATH), "worker", "--stop"], capture_output=True, cwd=str(b_dir))

    # Output results
    output = {
        "artifact": "bench_repeated_ast_workflow",
        "environment": {
            "machine": platform.machine(),
            "platform": platform.system().lower(),
            "python_version": platform.python_version(),
        },
        "rows": results,
    }

    print(json.dumps(output, indent=2))
    output_path = ROOT_DIR / "artifacts" / "bench_ast_workflow_resident.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
