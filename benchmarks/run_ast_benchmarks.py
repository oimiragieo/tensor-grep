import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCENARIOS = [
    {
        "name": "1. Simple Function Def",
        "ast_args": ["ast-grep.exe", "run", "-p", "def $FUNC():", "bench_ast_data"],
        "tg_args": ["tg", "run", "--ast", "--lang", "python", "def $FUNC():", "bench_ast_data"],
    },
    {
        "name": "2. Try/Except Block",
        "ast_args": ["ast-grep.exe", "run", "-p", "try: $$$ catch: $$$", "bench_ast_data"],
        "tg_args": [
            "tg",
            "run",
            "--ast",
            "--lang",
            "python",
            "try: $$$ catch: $$$",
            "bench_ast_data",
        ],
    },
    {
        "name": "3. Class Declaration",
        "ast_args": ["ast-grep.exe", "run", "-p", "class $NAME:", "bench_ast_data"],
        "tg_args": ["tg", "run", "--ast", "--lang", "python", "class $NAME:", "bench_ast_data"],
    },
]


def generate_ast_data(directory: str, num_files: int = 10, funcs_per_file: int = 500):
    print(f"Generating synthetic Python code data in '{directory}'...")
    os.makedirs(directory, exist_ok=True)

    template = """
class DataProcessor_{idx}:
    def __init__(self):
        self.data = []

    def process_{func_idx}(self):
        try:
            x = {func_idx} * 2
            if x > 100:
                return x
        except Exception as e:
            print(f"Error: {{e}}")

    def validate_{func_idx}(self):
        return True
"""

    for i in range(num_files):
        file_path = os.path.join(directory, f"module_{i}.py")
        with open(file_path, "w", encoding="utf-8") as f:
            for j in range(funcs_per_file):
                f.write(template.format(idx=j, func_idx=j))


def run_cmd_capture(cmd):
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            encoding="utf-8",
        )
        stdout = result.stdout
    except Exception as e:
        print(f"Failed to run {' '.join(cmd)}: {e}")
        stdout = ""
    return time.time() - start, stdout


def resolve_ast_grep_binary() -> str | None:
    for candidate in ("sg", "ast-grep", "ast-grep.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def compare_results(ast_out, tg_out, scenario_name):
    # Both ast-grep and tg will print matches, but formatting differs heavily (ast-grep has color highlighting by default, tg outputs rg style)
    # Just checking if both found matches
    ast_lines = len([line for line in ast_out.splitlines() if line.strip()])
    tg_lines = len([line for line in tg_out.splitlines() if line.strip()])

    if (ast_lines > 0 and tg_lines == 0) or (ast_lines == 0 and tg_lines > 0):
        print(
            f"  [!] PARITY FAILURE in {scenario_name}: ast-grep found {ast_lines} output lines, tg found {tg_lines}."
        )
        return False
    return True


def main():
    bench_dir = Path(__file__).resolve().parent / "bench_ast_data"
    # Generates 10 files, each with 500 classes and 1000 functions
    generate_ast_data(str(bench_dir), num_files=10, funcs_per_file=500)

    print("\nStarting Benchmarks: ast-grep vs tensor-grep (--ast)")
    print("-" * 75)
    print(f"{'Scenario':<35} | {'ast-grep':<10} | {'tensor-grep':<10} | {'Parity'}")
    print("-" * 75)

    ast_bin = resolve_ast_grep_binary()
    if not ast_bin:
        print("ast-grep binary not found on PATH. Skipping ast-grep parity baseline.")
        print("Run `cargo install ast-grep` or install ast-grep binary to enable this benchmark.")
        return

    tg_cmd = [sys.executable, "-m", "tensor_grep.cli.main", "run"]

    for scenario in SCENARIOS:
        ast_cmd = [
            ast_bin,
            *[
                str(bench_dir) if arg == "bench_ast_data" else arg
                for arg in scenario["ast_args"][2:]
            ],
        ]
        actual_tg_cmd = [
            *tg_cmd,
            *[
                str(bench_dir) if arg == "bench_ast_data" else arg
                for arg in scenario["tg_args"][2:]
            ],
        ]

        # Warmup caches
        run_cmd_capture(ast_cmd)
        run_cmd_capture(actual_tg_cmd)

        # Actual benchmark
        ast_time, ast_out = run_cmd_capture(ast_cmd)
        tg_time, tg_out = run_cmd_capture(actual_tg_cmd)

        parity_ok = compare_results(ast_out, tg_out, scenario["name"])
        parity_str = "PASS" if parity_ok else "FAIL"

        print(f"{scenario['name']:<35} | {ast_time:>8.3f}s | {tg_time:>8.3f}s | {parity_str}")


if __name__ == "__main__":
    main()
