import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from tensor_grep.backends.stringzilla_backend import StringZillaBackend
from tensor_grep.core.config import SearchConfig


def resolve_hot_bench_data_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_HOT_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.cwd() / "artifacts" / "hot_bench_data").resolve()


def write_cpu_probe_script(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            import time
            import types

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
                {
                    "matches": result.total_matches,
                    "routing_reason": result.routing_reason,
                    "seconds": t1 - t0,
                }
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark hot repeated-query cache paths.")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    data_dir = resolve_hot_bench_data_dir()
    corpus_path = _prepare_corpus(data_dir)
    probe_script = data_dir / "cpu_hot_probe.py"
    write_cpu_probe_script(probe_script)

    rows = [
        _run_stringzilla_hot_query(corpus_path, data_dir / "stringzilla-cache"),
        _run_cpu_hot_query(corpus_path, data_dir / "cpu-prefilter-cache", probe_script),
    ]

    payload = {
        "suite": "run_hot_query_benchmarks",
        "environment": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
        },
        "rows": rows,
    }

    output_path = args.output or (Path.cwd() / "artifacts" / "bench_hot_query_benchmarks.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nStarting Benchmarks: Hot repeated-query paths")
    print("-" * 75)
    print(f"{'Scenario':35} | {'First':>9} | {'Second':>9} | Reason")
    print("-" * 75)
    for row in rows:
        print(
            f"{row['name']:35} | {row['first_s']:>8.4f}s | {row['second_s']:>8.4f}s | "
            f"{row['second_reason']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
