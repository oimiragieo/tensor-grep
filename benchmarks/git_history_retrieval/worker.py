"""Container entrypoint for git-history retrieval worker phases."""

import os
import sys
from pathlib import Path

HARNESS_VENV_PYTHON = Path("/work/build/harness-venv/bin/python")


def evaluator_python() -> str:
    if HARNESS_VENV_PYTHON.is_file():
        return str(HARNESS_VENV_PYTHON)
    return sys.executable


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"build", "prepare", "evaluate"}:
        raise SystemExit("expected build, prepare, or evaluate")
    python = evaluator_python()
    harness = Path(__file__).with_name("harness.py")
    os.execv(python, [python, "-I", str(harness), "worker", *sys.argv[1:]])


if __name__ == "__main__":
    main()
