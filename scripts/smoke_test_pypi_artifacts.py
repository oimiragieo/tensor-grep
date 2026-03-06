from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_tg(venv_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        scripts = venv_dir / "Scripts"
        for candidate in ("tg.exe", "tg.cmd", "tg-script.py"):
            path = scripts / candidate
            if path.exists():
                return path
        return scripts / "tg.exe"
    return venv_dir / "bin" / "tg"


def run_smoke_test(*, dist_dir: Path, version: str, work_dir: Path) -> None:
    resolved_dist = dist_dir.resolve()
    venv_dir = work_dir / ".pypi-smoke-venv"
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    python_exe = _venv_python(venv_dir)
    subprocess.run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--find-links",
            str(resolved_dist),
            f"tensor-grep=={version}",
        ],
        check=True,
    )
    subprocess.run(
        [
            str(python_exe),
            "-c",
            (
                "import importlib.metadata as m; "
                f"assert m.version('tensor-grep') == '{version}'; "
                "import tensor_grep"
            ),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(_venv_tg(venv_dir)),
            "--version",
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test install of built PyPI artifacts from local dist directory."
    )
    parser.add_argument(
        "--dist-dir", type=Path, default=Path("dist"), help="Distribution directory"
    )
    parser.add_argument(
        "--version", required=True, help="Expected package version (without leading v)"
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(".tmp"),
        help="Working directory for temporary virtual environment",
    )
    args = parser.parse_args()

    run_smoke_test(
        dist_dir=args.dist_dir,
        version=args.version,
        work_dir=args.work_dir,
    )
    print(f"PyPI artifact smoke test passed for tensor-grep=={args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
