"""Container entrypoint for git-history retrieval worker phases."""

import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"build", "prepare", "evaluate"}:
        raise SystemExit("expected build, prepare, or evaluate")
    python = sys.executable if sys.argv[1] == "build" else "/work/build/harness-venv/bin/python"
    harness = Path(__file__).with_name("harness.py")
    os.execv(python, [python, str(harness), "worker", *sys.argv[1:]])


if __name__ == "__main__":
    main()
