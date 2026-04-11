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


def setup_bench_project(root: Path, file_count=100):
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "sgconfig.yml").write_text(
        "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n", encoding="utf-8"
    )

    for idx in range(file_count):
        (root / "src" / f"module_{idx:03d}.py").write_text(
            f"def sym_{idx:03d}():\n    pass\ndef caller_{idx:03d}():\n    sym_000()\n",
            encoding="utf-8",
        )


def main():
    if not BIN_PATH.exists():
        print(f"Error: {BIN_PATH} not found. Build it with 'cargo build --release' in rust_core.")
        return 1

    bench_dir = ROOT_DIR / "artifacts" / "bench_editor_plane"
    if bench_dir.exists():
        import shutil

        shutil.rmtree(bench_dir)
    setup_bench_project(bench_dir)

    results = []

    scenarios = [
        ("defs", ["defs", "src", "--symbol", "sym_000"]),
        ("refs", ["refs", "src", "--symbol", "sym_000"]),
        ("context", ["context", "src", "--query", "sym_000"]),
    ]

    def avg(lst):
        return sum(lst[1:]) / (len(lst) - 1) if len(lst) > 1 else (lst[0] if lst else 0)

    # 1. Cold path
    for name, args in scenarios:
        times = []
        for _ in range(10):
            t, code = run_cmd([str(BIN_PATH), *args, "--json"], cwd=bench_dir)
            if code == 0:
                times.append(t)

        results.append({"name": f"editor_{name}_cold", "tg_time_s": avg(times), "backend": "native"})

    # 2. Resident path
    subprocess.Popen([str(BIN_PATH), "worker", "--port", "12352"], cwd=str(bench_dir))
    port_file = bench_dir / ".tg_cache" / "ast" / "worker_port.txt"
    for _ in range(50):
        if port_file.exists():
            break
        time.sleep(0.1)

    env = os.environ.copy()
    env["TG_RESIDENT_AST"] = "1"

    for name, args in scenarios:
        times = []
        for _ in range(10):
            t, code = run_cmd([str(BIN_PATH), *args, "--json"], cwd=bench_dir, env=env)
            if code == 0:
                times.append(t)

        results.append(
            {"name": f"editor_{name}_resident", "tg_time_s": avg(times), "backend": "native_resident"}
        )

    # Stop worker
    subprocess.run([str(BIN_PATH), "worker", "--stop"], capture_output=True, cwd=str(bench_dir))

    # Output results
    output = {
        "artifact": "bench_editor_plane",
        "environment": {
            "machine": platform.machine(),
            "platform": platform.system().lower(),
            "python_version": platform.python_version(),
        },
        "rows": results,
    }

    print(json.dumps(output, indent=2))

    output_path = ROOT_DIR / "artifacts" / "bench_editor_plane_canonical.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
