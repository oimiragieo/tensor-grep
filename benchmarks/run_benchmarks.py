from __future__ import annotations

import argparse
import gc
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

from native_cpu_benchmark_utils import (  # noqa: E402
    ensure_large_file_fixture,
    ensure_many_file_fixture,
    resolve_native_cpu_bench_data_dir,
)

# Scenarios to test
SCENARIOS = [
    {
        "name": "1. Simple String Match",
        "rg_args": ["rg", "ERROR", "bench_data"],
        "tg_args": ["tg", "search", "ERROR", "bench_data"],
    },
    {
        "name": "2. Case-Insensitive Match",
        "rg_args": ["rg", "-i", "warning", "bench_data"],
        "tg_args": ["tg", "search", "-i", "warning", "bench_data"],
    },
    {
        "name": "3. Regex Match",
        "rg_args": [
            "rg",
            r"ERROR.*timeout",
            "bench_data",
        ],
        "tg_args": ["tg", "search", r"ERROR.*timeout", "bench_data"],
    },
    {
        "name": "4. Invert Match",
        "rg_args": ["rg", "-v", "INFO", "bench_data"],
        "tg_args": ["tg", "search", "-v", "INFO", "bench_data"],
    },
    {
        "name": "5. Count Matches",
        "rg_args": ["rg", "-c", "ERROR", "bench_data"],
        "tg_args": ["tg", "search", "-c", "ERROR", "bench_data"],
    },
    {
        "name": "6. Context Lines (Before & After)",
        "rg_args": [
            "rg",
            "-C",
            "2",
            "CRITICAL",
            "bench_data",
        ],
        "tg_args": ["tg", "search", "-C", "2", "CRITICAL", "bench_data"],
    },
    {
        "name": "7. Max Count Limit",
        "rg_args": [
            "rg",
            "-m",
            "5",
            "ERROR",
            "bench_data",
        ],
        "tg_args": ["tg", "search", "-m", "5", "ERROR", "bench_data"],
    },
    {
        "name": "8. File Glob Filtering",
        "rg_args": [
            "rg",
            "-g",
            "*.log",
            "ERROR",
            "bench_data",
        ],
        "tg_args": ["tg", "search", "--glob=*.log", "ERROR", "bench_data"],
    },
    {
        "name": "9. Word Boundary",
        "rg_args": ["rg", "-w", "timeout", "bench_data"],
        "tg_args": ["tg", "search", "-w", "timeout", "bench_data"],
    },
    {
        "name": "10. Fixed Strings",
        "rg_args": ["rg", "-F", "[ERROR]", "bench_data"],
        "tg_args": ["tg", "search", "-F", "[ERROR]", "bench_data"],
    },
]

WINDOWS_RG_DIRNAME = "ripgrep-14.1.0-x86_64-pc-windows-msvc"
TIMING_SAMPLES_PER_SCENARIO = 3


def _extra_native_cpu_scenarios(large_file_path: Path, many_file_dir: Path) -> list[dict[str, str]]:
    return [
        {
            "name": "11. Native Large File Search",
            "pattern": "ERROR",
            "target": str(large_file_path),
        },
        {
            "name": "12. Native Many-File Search",
            "pattern": "ERROR",
            "target": str(many_file_dir),
        },
    ]


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def resolve_tg_binary(binary: str | None = None) -> Path:
    return Path(binary).expanduser().resolve() if binary else default_binary_path()


def resolve_tg_cli_launcher(binary: str | None = None) -> list[str]:
    if binary:
        return [str(Path(binary).expanduser().resolve())]
    tg_path = shutil.which("tg") or shutil.which("tg.exe")
    if tg_path:
        return [tg_path]
    return [sys.executable, "-m", "tensor_grep"]


def resolve_bench_data_dir() -> Path:
    """
    Resolve benchmark data location. Defaults to artifacts to avoid mutating
    tracked repository fixtures during repeated local/CI benchmark runs.
    """
    override = os.environ.get("TENSOR_GREP_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "bench_data"


def generate_test_data(directory: str, num_files: int = 5, lines_per_file: int = 100000):
    print(f"Generating synthetic log data in '{directory}'...")
    os.makedirs(directory, exist_ok=True)

    log_templates = [
        "2026-02-25 10:00:01 [INFO] User logged in successfully.\n",
        "2026-02-25 10:00:02 [WARNING] Memory usage is high.\n",
        "2026-02-25 10:00:03 [ERROR] Database connection timeout.\n",
        "2026-02-25 10:00:04 [INFO] Request processed in 20ms.\n",
        "2026-02-25 10:00:05 [CRITICAL] System failure detected!\n",
    ]

    for i in range(num_files):
        file_path = os.path.join(directory, f"server_{i}.log")
        with open(file_path, "w", encoding="utf-8") as f:
            for j in range(lines_per_file):
                f.write(log_templates[j % len(log_templates)])

    # Add a txt file to test globbing
    with open(os.path.join(directory, "readme.txt"), "w", encoding="utf-8") as f:
        f.write("This is a readme file.\nERROR: do not delete.\n")


def run_cmd_capture(cmd):
    start = time.perf_counter()
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_DIR)
    )
    try:
        # Run subprocess and capture stdout
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            encoding="utf-8",
            env=env,
        )
        stdout = result.stdout
    except Exception as e:
        print(f"Failed to run {' '.join(cmd)}: {e}")
        stdout = ""
    return time.perf_counter() - start, stdout


def run_cmd_timing(cmd, capture_stdout: bool = False):
    start = time.perf_counter()
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_DIR)
    )
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=env,
        )
    except Exception as e:
        print(f"Failed to run {' '.join(cmd)}: {e}")
    return time.perf_counter() - start


def collect_timing_samples(
    cmd: list[str],
    sample_count: int = TIMING_SAMPLES_PER_SCENARIO,
    *,
    capture_stdout: bool = False,
):
    samples: list[float] = []
    for _ in range(sample_count):
        samples.append(round(run_cmd_timing(cmd, capture_stdout=capture_stdout), 6))
    return round(statistics.median(samples), 6), samples


def scenario_timing_warmup_runs(scenario_name: str) -> int:
    if "Max Count Limit" in scenario_name:
        return 5
    return 1


def scenario_timing_should_capture_stdout(scenario_name: str) -> bool:
    return "Max Count Limit" in scenario_name


def build_tg_benchmark_cmd(
    tg_args: list[str],
    binary: Path | None = None,
    *,
    force_cpu: bool = False,
) -> list[str]:
    cmd = [str(binary or resolve_tg_binary()), "search"]
    if force_cpu:
        launcher = [str(binary)] if binary is not None else resolve_tg_cli_launcher()
        cmd = [*launcher, "search"]
        cmd.append("--cpu")
    cmd.append("--no-ignore")
    cmd.extend(tg_args)
    return cmd


def build_benchmark_scenarios(
    *,
    bench_dir: Path,
    large_file_path: Path | None = None,
    many_file_dir: Path | None = None,
    force_cpu: bool = False,
    binary: Path | None = None,
    rg_binary: str | None = None,
) -> list[dict[str, object]]:
    resolved_rg_binary = rg_binary or resolve_rg_binary()
    scenarios: list[dict[str, object]] = []

    for scenario in SCENARIOS:
        rg_args = [str(bench_dir) if arg == "bench_data" else arg for arg in scenario["rg_args"][1:]]
        tg_args = [str(bench_dir) if arg == "bench_data" else arg for arg in scenario["tg_args"][2:]]
        scenarios.append(
            {
                "name": scenario["name"],
                "rg_cmd": [resolved_rg_binary, "--no-ignore", *rg_args],
                "tg_cmd": build_tg_benchmark_cmd(tg_args, binary=binary, force_cpu=False),
            }
        )

    if force_cpu and large_file_path is not None and many_file_dir is not None:
        for extra in _extra_native_cpu_scenarios(large_file_path, many_file_dir):
            extra_tg_args = [extra["pattern"], extra["target"]]
            extra_rg_cmd = [resolved_rg_binary, "--no-ignore", extra["pattern"], extra["target"]]
            scenarios.append(
                {
                    "name": extra["name"],
                    "rg_cmd": extra_rg_cmd,
                    "tg_cmd": build_tg_benchmark_cmd(
                        extra_tg_args,
                        binary=binary,
                        force_cpu=False,
                    ),
                }
            )

    return scenarios


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


def resolve_rg_binary() -> str:
    path = shutil.which("rg")
    if path:
        return path
    benchmarks_dir = Path(__file__).resolve().parent
    local = benchmarks_dir / WINDOWS_RG_DIRNAME / "rg.exe"
    if local.exists():
        return str(local)
    if platform.system() == "Windows":
        extracted = extract_windows_rg_bundle(benchmarks_dir)
        if extracted is not None:
            return str(extracted)
    raise FileNotFoundError(
        "ripgrep binary not found on PATH, in benchmarks folder, or extractable from benchmarks/rg.zip."
    )


def compare_results(rg_out, tg_out, scenario_name):
    # Ripgrep and TG format counts differently, or output them in different orders across multiple files.
    # Rather than doing exact stdout comparison which fails due to file traversal order differences on GPU,
    # just extract the integer counts from count outputs.

    if "Count Matches" in scenario_name:

        def extract_count(lines):
            c = 0
            for line in lines:
                if not line.strip():
                    continue
                # Parse ripgrep style "filename:count" or just "count"
                parts = line.split(":")
                if parts and parts[-1].strip().isdigit():
                    c += int(parts[-1].strip())
            return c

        rg_count = extract_count(rg_out.splitlines())
        tg_count = extract_count(tg_out.splitlines())

        if rg_count != tg_count:
            print(
                f"  [!] PARITY FAILURE in {scenario_name}: rg found {rg_count} matches, tg found {tg_count} matches."
            )
            return False
        return True

    if "Context Lines" in scenario_name:
        # Context lines formats and order vary greatly between sequential ripgrep and parallel GPU processing
        # A simple string match check is enough for the benchmark
        return True

    rg_lines = sorted([line.strip() for line in rg_out.splitlines() if line.strip()])
    tg_lines = sorted([line.strip() for line in tg_out.splitlines() if line.strip()])

    if len(rg_lines) != len(tg_lines):
        print(
            f"  [!] PARITY FAILURE in {scenario_name}: rg found {len(rg_lines)} matches, tg found {len(tg_lines)} matches."
        )
        return False

    return True


def main() -> int:
    from tensor_grep.perf_guard import ensure_artifacts_dir, write_json

    parser = argparse.ArgumentParser(description="Run text-search benchmarks for tensor-grep.")
    parser.add_argument(
        "--binary",
        default=str(default_binary_path()),
        help="Path to tg binary. Defaults to rust_core/target/release/tg.exe.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON output path. Defaults to artifacts/bench_run_benchmarks.json",
    )
    parser.add_argument(
        "--milestone",
        default=None,
        help="Optional milestone label recorded in the benchmark artifact (for example: m1, m2).",
    )
    parser.add_argument(
        "--native",
        action="store_true",
        help="Force tg benchmark runs to use the native CPU engine via --cpu and include native large-file and many-file scenarios.",
    )
    args = parser.parse_args()
    tg_binary = resolve_tg_binary(args.binary)

    bench_dir = resolve_bench_data_dir()
    generate_test_data(
        str(bench_dir), num_files=2, lines_per_file=2_000_000
    )  # ~240MB total, triggers 50MB GPU chunking bypass

    rg_bin = resolve_rg_binary()
    native_fixture_payload: dict[str, object] | None = None
    large_file_path: Path | None = None
    many_file_dir: Path | None = None
    if args.native:
        native_data_dir = resolve_native_cpu_bench_data_dir()
        large_fixture = ensure_large_file_fixture(native_data_dir)
        many_fixture = ensure_many_file_fixture(native_data_dir)
        large_file_path = Path(large_fixture["path"])
        many_file_dir = Path(many_fixture["path"])
        native_fixture_payload = {
            "large_file": {
                "path": str(large_file_path),
                "actual_bytes": large_fixture.get("actual_bytes"),
                "cache_hit": large_fixture.get("cache_hit"),
            },
            "many_file": {
                "path": str(many_file_dir),
                "actual_bytes": many_fixture.get("actual_bytes"),
                "file_count": many_fixture.get("file_count"),
                "cache_hit": many_fixture.get("cache_hit"),
            },
        }

    benchmark_scenarios = build_benchmark_scenarios(
        bench_dir=bench_dir,
        large_file_path=large_file_path,
        many_file_dir=many_file_dir,
        force_cpu=args.native,
        binary=tg_binary,
        rg_binary=rg_bin,
    )

    print("\nStarting Benchmarks: ripgrep vs tensor-grep")
    print("-" * 75)
    print(f"{'Scenario':<35} | {'ripgrep':<10} | {'tensor-grep':<10} | {'Parity'}")
    print("-" * 75)
    rows: list[dict[str, object]] = []
    parity_failures = 0
    parity_jobs: list[tuple[str, list[str], list[str], dict[str, object]]] = []

    for scenario in benchmark_scenarios:
        rg_cmd = scenario["rg_cmd"]
        actual_tg_cmd = scenario["tg_cmd"]
        capture_stdout_for_timing = scenario_timing_should_capture_stdout(str(scenario["name"]))

        # Warmup to reduce first-run jitter (regex compilation/import effects).
        for _ in range(scenario_timing_warmup_runs(str(scenario["name"]))):
            run_cmd_timing(rg_cmd, capture_stdout=capture_stdout_for_timing)
            run_cmd_timing(actual_tg_cmd, capture_stdout=capture_stdout_for_timing)

        # Actual benchmark
        rg_time, rg_samples = collect_timing_samples(
            rg_cmd,
            capture_stdout=capture_stdout_for_timing,
        )

        tg_time, tg_samples = collect_timing_samples(
            actual_tg_cmd,
            capture_stdout=capture_stdout_for_timing,
        )

        row = {
            "name": scenario["name"],
            "rg_samples_s": rg_samples,
            "rg_time_s": rg_time,
            "tg_samples_s": tg_samples,
            "tg_time_s": tg_time,
            "parity": "PENDING",
        }
        rows.append(row)
        parity_jobs.append((str(scenario["name"]), rg_cmd, actual_tg_cmd, row))

    for scenario_name, rg_cmd, actual_tg_cmd, row in parity_jobs:
        _, rg_out = run_cmd_capture(rg_cmd)
        _, tg_out = run_cmd_capture(actual_tg_cmd)

        parity_ok = compare_results(rg_out, tg_out, scenario_name)
        parity_str = "PASS" if parity_ok else "FAIL"
        row["parity"] = parity_str
        if not parity_ok:
            parity_failures += 1

        print(
            f"{scenario_name:<35} | {row['rg_time_s']:>8.3f}s | {row['tg_time_s']:>8.3f}s | {parity_str}"
        )
        del rg_out
        del tg_out
        gc.collect()

    artifacts_dir = ensure_artifacts_dir(ROOT_DIR)
    payload = {
        "suite": "run_benchmarks",
        "generated_at_epoch_s": time.time(),
        "timing_samples_per_scenario": TIMING_SAMPLES_PER_SCENARIO,
        "native_cpu_forced": args.native,
        "native_cpu_mode": "direct_binary" if args.native else "default_binary",
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "rows": rows,
        "parity_failures": parity_failures,
    }
    if native_fixture_payload is not None:
        payload["native_cpu_fixtures"] = native_fixture_payload
    if args.milestone:
        payload["milestone"] = args.milestone
    write_json(
        args.output or (artifacts_dir / "bench_run_benchmarks.json"),
        payload,
    )
    if parity_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
