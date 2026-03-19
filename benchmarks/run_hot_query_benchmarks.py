import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def resolve_hot_bench_data_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_HOT_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.cwd() / "artifacts" / "hot_bench_data").resolve()


def write_cpu_probe_script(path: Path) -> None:
    src_dir = str(SRC_DIR).replace("\\", "\\\\")
    path.write_text(
        textwrap.dedent(
            f"""
            import json
            import sys
            import time
            import types
            from pathlib import Path

            SRC_DIR = Path(r"{src_dir}")
            if str(SRC_DIR) not in sys.path:
                sys.path.insert(0, str(SRC_DIR))

            from tensor_grep.backends.cpu_backend import CPUBackend
            from tensor_grep.core.config import SearchConfig

            rust_mod = types.ModuleType("tensor_grep.rust_core")

            class FailingRustBackend:
                def search(self, **kwargs):
                    raise RuntimeError("force python fallback")

            rust_mod.RustBackend = FailingRustBackend
            sys.modules["tensor_grep.rust_core"] = rust_mod

            target_path = sys.argv[1]
            pattern = sys.argv[2]
            t0 = time.perf_counter()
            result = CPUBackend().search(target_path, pattern, SearchConfig())
            t1 = time.perf_counter()
            print(json.dumps(
                {{
                    "matches": result.total_matches,
                    "routing_reason": result.routing_reason,
                    "seconds": t1 - t0,
                }}
            ))
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def _prepare_corpus(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = data_dir / "hot_corpus.log"
    line = "INFO hello keepalive marker\n"
    target = "ERROR alpha timeout marker critical path\n"
    corpus_path.write_text(line * 20000 + target * 2000 + line * 20000, encoding="utf-8")
    return corpus_path


def _run_stringzilla_hot_query(corpus_path: Path, cache_dir: Path) -> dict[str, object]:
    from tensor_grep.backends.stringzilla_backend import StringZillaBackend
    from tensor_grep.core.config import SearchConfig

    shutil.rmtree(cache_dir, ignore_errors=True)
    os.environ["TENSOR_GREP_STRING_INDEX"] = "1"
    os.environ["TENSOR_GREP_STRING_INDEX_DIR"] = str(cache_dir)
    StringZillaBackend._clear_shared_caches()
    cfg = SearchConfig(fixed_strings=True)

    t0 = time.perf_counter()
    first = StringZillaBackend().search(str(corpus_path), "ERROR timeout", config=cfg)
    t1 = time.perf_counter()
    second = StringZillaBackend().search(str(corpus_path), "critical path", config=cfg)
    t2 = time.perf_counter()

    return {
        "name": "repeated_fixed_string",
        "first_s": t1 - t0,
        "second_s": t2 - t1,
        "first_reason": first.routing_reason,
        "second_reason": second.routing_reason,
        "matches": second.total_matches,
    }


def _run_cpu_hot_query(corpus_path: Path, cache_dir: Path, probe_script: Path) -> dict[str, object]:
    shutil.rmtree(cache_dir, ignore_errors=True)
    env = os.environ.copy()
    env["TENSOR_GREP_CPU_REGEX_INDEX"] = "1"
    env["TENSOR_GREP_CPU_REGEX_INDEX_DIR"] = str(cache_dir)

    first = subprocess.check_output(
        [sys.executable, str(probe_script), str(corpus_path), r"ERROR.*timeout"],
        text=True,
        env=env,
    )
    second = subprocess.check_output(
        [sys.executable, str(probe_script), str(corpus_path), r"ERROR.*critical"],
        text=True,
        env=env,
    )
    first_payload = json.loads(first)
    second_payload = json.loads(second)
    return {
        "name": "repeated_regex_prefilter",
        "first_s": float(first_payload["seconds"]),
        "second_s": float(second_payload["seconds"]),
        "first_reason": first_payload["routing_reason"],
        "second_reason": second_payload["routing_reason"],
        "matches": int(second_payload["matches"]),
    }


def evaluate_hot_query_row(row: dict[str, object], max_regression_pct: float) -> dict[str, object]:
    first_s = row.get("first_s")
    second_s = row.get("second_s")
    if (
        not isinstance(first_s, (float, int))
        or not isinstance(second_s, (float, int))
        or first_s <= 0
    ):
        return {**row, "status": "UNKNOWN"}

    improvement_pct = ((float(first_s) - float(second_s)) / float(first_s)) * 100.0
    regression_limit = float(first_s) * (1.0 + (max_regression_pct / 100.0))
    status = "PASS" if float(second_s) <= regression_limit else "FAIL"
    return {
        **row,
        "improvement_pct": round(improvement_pct, 2),
        "status": status,
    }


def main() -> int:
    from tensor_grep.perf_guard import write_json

    parser = argparse.ArgumentParser(description="Benchmark hot repeated-query cache paths.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--max-regression-pct",
        type=float,
        default=5.0,
        help="Maximum allowed slowdown for the cached second query relative to the first query.",
    )
    args = parser.parse_args()

    data_dir = resolve_hot_bench_data_dir()
    corpus_path = _prepare_corpus(data_dir)
    probe_script = data_dir / "cpu_hot_probe.py"
    write_cpu_probe_script(probe_script)

    rows = [
        _run_stringzilla_hot_query(corpus_path, data_dir / "stringzilla-cache"),
        _run_cpu_hot_query(corpus_path, data_dir / "cpu-prefilter-cache", probe_script),
    ]
    evaluated_rows = [evaluate_hot_query_row(row, args.max_regression_pct) for row in rows]
    no_regressions = all(row.get("status") != "FAIL" for row in evaluated_rows)

    payload = {
        "artifact": "bench_hot_query_benchmarks",
        "suite": "run_hot_query_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "max_regression_pct": args.max_regression_pct,
        "no_regressions": no_regressions,
        "rows": evaluated_rows,
    }

    output_path = args.output or (Path.cwd() / "artifacts" / "bench_hot_query_benchmarks.json")
    write_json(output_path, payload)

    print("\nStarting Benchmarks: Hot repeated-query paths")
    print("-" * 75)
    print(f"{'Scenario':35} | {'First':>9} | {'Second':>9} | {'Status':>6} | Reason")
    print("-" * 75)
    for row in evaluated_rows:
        print(
            f"{row['name']:35} | {row['first_s']:>8.4f}s | {row['second_s']:>8.4f}s | "
            f"{row['status']:>6} | {row['second_reason']}"
        )
    return 0 if no_regressions else 1


if __name__ == "__main__":
    raise SystemExit(main())
