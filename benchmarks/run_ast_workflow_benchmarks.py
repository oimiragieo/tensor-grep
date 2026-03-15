from __future__ import annotations

import os
import platform
import subprocess
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def resolve_tg_binary(binary: str | None = None) -> Path:
    return Path(binary).expanduser().resolve() if binary else default_binary_path()


def resolve_ast_workflow_bench_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_AST_WORKFLOW_BENCH_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "bench_ast_workflow"


def build_tg_ast_workflow_cmd(args: list[str], binary: Path | None = None) -> list[str]:
    return [str(binary or resolve_tg_binary()), *args]


def _write_rules(rules_dir: Path, rule_count: int) -> None:
    rules_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(rule_count):
        if idx % 2 == 0:
            pattern = '"def $FUNC():\\n    $$$BODY"'
        else:
            pattern = '"class $NAME:\\n    $$$BODY"'
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
            "\n".join(
                [
                    "class SampleClass:",
                    "    def __init__(self):",
                    "        pass",
                    "",
                    "def sample_function():",
                    "    return 1",
                ]
            )
            + "\n",
            encoding="utf-8",
        )


def generate_ast_workflow_project(root: Path, *, rule_count: int = 4, file_count: int = 4) -> None:
    root.mkdir(parents=True, exist_ok=True)

    scan_project = root / "scan_project"
    scan_project.mkdir(exist_ok=True)
    (scan_project / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\ntestDirs:\n  - tests\nlanguage: python\n",
        encoding="utf-8",
    )
    _write_source_files(scan_project, file_count)
    _write_rules(scan_project / "rules", rule_count)
    _write_tests(scan_project / "tests", rule_count)


def run_cmd_capture(cmd: list[str], cwd: Path) -> tuple[float, int]:
    start = time.perf_counter()
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_DIR)
    )
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return time.perf_counter() - start, result.returncode


def parse_args() -> tuple:
    import argparse

    parser = argparse.ArgumentParser(
        description="Benchmark AST workflow startup for tg run/scan/test."
    )
    parser.add_argument(
        "--binary",
        default=str(default_binary_path()),
        help="Path to tg binary. Defaults to rust_core/target/release/tg.exe.",
    )
    return parser.parse_args()


def main() -> int:
    from tensor_grep.perf_guard import ensure_artifacts_dir, write_json

    args = parse_args()
    tg_binary = resolve_tg_binary(args.binary)

    bench_root = resolve_ast_workflow_bench_dir()
    bench_dir = bench_root / f"run_{int(time.time() * 1000)}"
    generate_ast_workflow_project(bench_dir)

    run_cmd = build_tg_ast_workflow_cmd(
        ["run", "def $FUNC():\n    $$$BODY", "."], binary=tg_binary
    )
    scan_cmd = build_tg_ast_workflow_cmd(
        ["scan", "--config", "sgconfig.yml"], binary=tg_binary
    )
    test_cmd = build_tg_ast_workflow_cmd(
        ["test", "--config", "sgconfig.yml"], binary=tg_binary
    )

    scan_project = bench_dir / "scan_project"

    run_cmd_capture(run_cmd, scan_project)
    run_cmd_capture(scan_cmd, scan_project)
    run_cmd_capture(test_cmd, scan_project)

    run_time_s, run_exit = run_cmd_capture(run_cmd, scan_project)
    scan_time_s, scan_exit = run_cmd_capture(scan_cmd, scan_project)
    test_time_s, test_exit = run_cmd_capture(test_cmd, scan_project)

    rows = [
        {
            "name": "ast_run_workflow",
            "tg_time_s": round(run_time_s, 6),
            "exit_code": run_exit,
        },
        {
            "name": "ast_scan_workflow",
            "tg_time_s": round(scan_time_s, 6),
            "exit_code": scan_exit,
        },
        {
            "name": "ast_test_workflow",
            "tg_time_s": round(test_time_s, 6),
            "exit_code": test_exit,
        },
    ]

    artifacts_dir = ensure_artifacts_dir(ROOT_DIR)
    write_json(
        artifacts_dir / "bench_run_ast_workflow_benchmarks.json",
        {
            "suite": "run_ast_workflow_benchmarks",
            "generated_at_epoch_s": time.time(),
            "environment": {
                "platform": platform.system().lower(),
                "machine": platform.machine().lower(),
                "python_version": platform.python_version(),
            },
            "rows": rows,
        },
    )

    return 0 if run_exit == 0 and scan_exit == 0 and test_exit == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
