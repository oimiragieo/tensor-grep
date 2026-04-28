from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
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


def _project_dependencies() -> list[str]:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    metadata = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return list(metadata["project"].get("dependencies", []))


def run_smoke_test(*, dist_dir: Path, version: str, work_dir: Path) -> None:
    resolved_dist = dist_dir.resolve()
    venv_dir = work_dir / ".pypi-smoke-venv"
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    python_exe = _venv_python(venv_dir)
    dependencies = _project_dependencies()
    if dependencies:
        subprocess.run(
            [
                str(python_exe),
                "-m",
                "pip",
                "install",
                *dependencies,
            ],
            check=True,
        )
    subprocess.run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(resolved_dist),
            "--no-deps",
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
    rewrite_smoke_dir = work_dir / "rewrite-smoke"
    rewrite_smoke_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(python_exe),
            "-c",
            (
                "import subprocess, sys; "
                "from pathlib import Path; "
                "source = Path(sys.argv[2]); "
                "source.write_text('def add(x, y): return x + y\\n', encoding='utf-8'); "
                "result = subprocess.run([sys.argv[1], 'run', '--lang', 'python', "
                "'--rewrite', 'lambda $$$ARGS: $EXPR', "
                "'def $F($$$ARGS): return $EXPR', str(source)], "
                "capture_output=True, text=True, check=True); "
                "assert 'lambda x, y: x + y' in result.stdout"
            ),
            str(_venv_tg(venv_dir)),
            str(rewrite_smoke_dir / "plan.py"),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(python_exe),
            "-c",
            (
                "import subprocess, sys; "
                "from pathlib import Path; "
                "source = Path(sys.argv[2]); "
                "source.write_text('def add(x, y): return x + y\\n', encoding='utf-8'); "
                "subprocess.run([sys.argv[1], 'run', '--lang', 'python', "
                "'--rewrite', 'lambda $$$ARGS: $EXPR', '--apply', "
                "'def $F($$$ARGS): return $EXPR', str(source)], "
                "capture_output=True, text=True, check=True); "
                "assert source.read_text(encoding='utf-8') == 'lambda x, y: x + y\\n'"
            ),
            str(_venv_tg(venv_dir)),
            str(rewrite_smoke_dir / "apply.py"),
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
