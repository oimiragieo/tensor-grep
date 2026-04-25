import os
import subprocess
import tempfile
import time
from pathlib import Path


def run_cmd(cmd, cwd=None, env=None):
    print(f"Running: {' '.join(cmd)}")
    start = time.time()
    res = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    duration = time.time() - start
    print(f"Finished in {duration:.2f}s (Exit code: {res.returncode})")
    if res.returncode != 0 and res.returncode != 1:
        print(f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
    elif res.returncode == 1 and len(res.stdout) > 0:
        # ripgrep returns 1 if no match, log it if there's an issue
        print(f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
    return res


def create_massive_log(path: Path, size_mb: int = 100):
    print(f"Creating {size_mb}MB synthetic log at {path}...")
    chunk = b"2026-04-25 12:00:00 INFO [System] Normal operation continues without issue.\n" * 1000
    chunk += b"2026-04-25 12:01:00 ERROR [Payment] CRITICAL_FAILURE: transaction timeout.\n"
    target_size = size_mb * 1024 * 1024
    with open(path, "wb") as f:
        written = 0
        while written < target_size:
            f.write(chunk)
            written += len(chunk)


def create_deep_tree(root: Path, depth: int = 6, width: int = 3):
    print(f"Creating deep directory tree at {root} (depth={depth}, width={width})...")

    def _build(current: Path, current_depth: int):
        if current_depth == 0:
            return
        for i in range(width):
            d = current / f"dir_{i}"
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "file.py", "w", encoding="utf-8") as f:
                f.write(
                    f"def hello_{current_depth}_{i}():\n    return 'Hello from depth {current_depth}'\n"
                )
            _build(d, current_depth - 1)

    _build(root, depth)


def main():
    print("=== Tensor-Grep World-Class Stress Test ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)

        log_file = base_dir / "massive.log"
        create_massive_log(log_file, size_mb=50)

        env = os.environ.copy()

        print("\n--- Phase 1: Search Core ---")
        res = run_cmd(["uv", "run", "tg", "search", "CRITICAL_FAILURE", str(log_file)], env=env)
        assert res.returncode == 0, "Failed text search"

        deep_dir = base_dir / "deep_repo"
        deep_dir.mkdir()
        create_deep_tree(deep_dir, depth=6, width=4)  # ~5k files

        print("\n--- Phase 2: Structural / AST ---")
        res = run_cmd(
            ["uv", "run", "tg", "run", "--lang", "python", "def $F(): return $E", str(deep_dir)],
            env=env,
        )
        assert res.returncode == 0, "Failed AST search"

        print("\n--- Phase 3: Repository Planning ---")
        res = run_cmd(["uv", "run", "tg", "map", str(deep_dir)], env=env)
        assert res.returncode == 0, "Failed map"

        res = run_cmd(
            ["uv", "run", "tg", "context-render", str(deep_dir), "--query", "hello_3_1"], env=env
        )
        assert res.returncode == 0, "Failed context-render"

        res = run_cmd(
            ["uv", "run", "tg", "blast-radius-render", str(deep_dir), "--symbol", "hello_3_1"],
            env=env,
        )
        assert res.returncode == 0, "Failed blast-radius-render"

        print("\n--- Phase 4: Audit & Session ---")
        res = run_cmd(
            [
                "uv",
                "run",
                "tg",
                "run",
                "--lang",
                "python",
                "-r",
                "def rewritten_$F(): return 'replaced'",
                "def $F(): return $E",
                str(deep_dir),
            ],
            env=env,
        )
        assert res.returncode == 0, "Failed AST rewrite"

        print("\nAll stress tests passed successfully.")


if __name__ == "__main__":
    main()
