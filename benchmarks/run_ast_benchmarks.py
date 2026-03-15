from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BENCHMARKS_DIR = Path(__file__).resolve().parent
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

from gen_corpus import generate_python_ast_bench_corpus  # noqa: E402

DEFAULT_PATTERN = "def $F($$$ARGS): return $EXPR"


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_ast_m3.json"


def resolve_ast_bench_data_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_AST_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "bench_ast_data"


def ensure_ast_bench_corpus(
    output_dir: Path,
    *,
    file_count: int,
    total_loc: int,
    seed: int,
) -> dict[str, object]:
    return generate_python_ast_bench_corpus(
        output_dir,
        file_count=file_count,
        total_loc=total_loc,
        seed=seed,
    )


def resolve_tg_binary(binary: str | None = None) -> Path:
    candidate = Path(binary).expanduser().resolve() if binary else default_binary_path()
    return candidate


def resolve_ast_grep_binary() -> Path | None:
    env_override = os.environ.get("AST_GREP_BINARY")
    if env_override:
        candidate = Path(env_override).expanduser().resolve()
        if candidate.exists():
            return candidate
        return None

    for candidate in ("sg", "sg.exe", "ast-grep", "ast-grep.exe"):
        if found := shutil.which(candidate):
            return Path(found)

    for local_name in ("sg.exe", "sg.cmd", "ast-grep.exe", "ast-grep.cmd"):
        local = BENCHMARKS_DIR / local_name
        if local.exists():
            return local

    return None


def resolve_hyperfine_binary() -> Path | None:
    if env_value := os.environ.get("HYPERFINE_BINARY"):
        candidate = Path(env_value).expanduser().resolve()
        if candidate.exists():
            return candidate

    for name in ("hyperfine", "hyperfine.exe"):
        if resolved := shutil.which(name):
            return Path(resolved)

    cargo_candidate = Path.home() / ".cargo" / "bin" / "hyperfine.exe"
    if cargo_candidate.exists():
        return cargo_candidate

    return None


def build_tg_ast_benchmark_cmd(args: list[str], binary: Path | None = None) -> list[str]:
    return [str(binary or resolve_tg_binary()), *args]


def build_sg_ast_benchmark_cmd(
    *,
    binary: Path,
    lang: str,
    pattern: str,
    corpus_dir: Path,
) -> list[str]:
    return [str(binary), "run", "--lang", lang, "-p", pattern, str(corpus_dir)]


def build_command_string(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return " ".join(shlex.quote(part) for part in args)


def run_hyperfine(
    hyperfine_path: Path,
    *,
    commands: list[str],
    runs: int,
    warmup: int,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        export_path = Path(tmp_dir) / "hyperfine.json"
        cmd = [
            str(hyperfine_path),
            "--runs",
            str(runs),
            "--warmup",
            str(warmup),
            "--export-json",
            str(export_path),
            *commands,
        ]
        completed = subprocess.run(cmd, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        return json.loads(export_path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Milestone 3 AST cold-start benchmark gate: tg.exe run vs native sg."
    )
    parser.add_argument("--binary", default=str(default_binary_path()), help="Path to tg.exe.")
    parser.add_argument(
        "--output",
        default=str(default_output_path()),
        help="Machine-readable output artifact path.",
    )
    parser.add_argument("--pattern", default=DEFAULT_PATTERN, help="AST pattern to benchmark.")
    parser.add_argument("--lang", default="python", help="Pattern language for tg/sg.")
    parser.add_argument("--runs", type=int, default=10, help="Number of hyperfine runs.")
    parser.add_argument("--warmup", type=int, default=0, help="Number of hyperfine warmup runs.")
    parser.add_argument("--files", type=int, default=1000, help="Synthetic corpus file count.")
    parser.add_argument("--loc", type=int, default=50000, help="Synthetic corpus total LOC.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic corpus seed.")
    parser.add_argument(
        "--max-ratio",
        type=float,
        default=3.0,
        help="Maximum allowed tg_median / sg_median ratio.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    corpus_info = ensure_ast_bench_corpus(
        resolve_ast_bench_data_dir(),
        file_count=args.files,
        total_loc=args.loc,
        seed=args.seed,
    )
    corpus_dir = Path(corpus_info["corpus_dir"])
    tg_binary = resolve_tg_binary(args.binary)
    sg_binary = resolve_ast_grep_binary()
    hyperfine_binary = resolve_hyperfine_binary()

    payload: dict[str, object] = {
        "artifact": "bench_ast_m3",
        "suite": "ast_search_benchmark_gate",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "file_count": corpus_info["file_count"],
        "total_loc": corpus_info["total_loc"],
        "seed": args.seed,
        "lang": args.lang,
        "pattern": args.pattern,
        "threshold": args.max_ratio,
        "corpus_dir": str(corpus_dir),
        "manifest_path": str(corpus_info["manifest_path"]),
    }

    errors: list[str] = []
    if not tg_binary.exists():
        errors.append(f"tg binary not found: {tg_binary}")
    if sg_binary is None:
        errors.append(
            "ast-grep binary not found. Install it via `cargo install ast-grep --version 0.41.1` or set AST_GREP_BINARY."
        )
    if hyperfine_binary is None:
        errors.append(
            "hyperfine was not found. Install it (for example `cargo install hyperfine --locked`) or set HYPERFINE_BINARY."
        )

    if errors:
        payload.update({"passed": False, "error": " ".join(errors)})
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        for error in errors:
            print(error, file=sys.stderr)
        return 2

    tg_command = build_tg_ast_benchmark_cmd(
        ["run", "--lang", args.lang, args.pattern, str(corpus_dir)],
        binary=tg_binary,
    )
    sg_command = build_sg_ast_benchmark_cmd(
        binary=sg_binary,
        lang=args.lang,
        pattern=args.pattern,
        corpus_dir=corpus_dir,
    )
    command_strings = [build_command_string(tg_command), build_command_string(sg_command)]
    hyperfine_data = run_hyperfine(
        hyperfine_binary,
        commands=command_strings,
        runs=args.runs,
        warmup=args.warmup,
    )
    results = hyperfine_data["results"]
    tg_median = float(results[0]["median"])
    sg_median = float(results[1]["median"])
    ratio = tg_median / sg_median if sg_median else float("inf")
    passed = ratio <= args.max_ratio

    payload.update(
        {
            "tg_binary": str(tg_binary),
            "sg_binary": str(sg_binary),
            "hyperfine_binary": str(hyperfine_binary),
            "runs": args.runs,
            "warmup": args.warmup,
            "tg_command": command_strings[0],
            "sg_command": command_strings[1],
            "tg_median_s": round(tg_median, 6),
            "sg_median_s": round(sg_median, 6),
            "ratio": round(ratio, 6),
            "passed": passed,
            "hyperfine": hyperfine_data,
        }
    )
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(
        f"AST benchmark gate: tg_median={tg_median:.3f}s sg_median={sg_median:.3f}s ratio={ratio:.3f} threshold<={args.max_ratio:.3f}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
