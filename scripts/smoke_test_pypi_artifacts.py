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


_SOURCE = "def add(x, y): return x + y\n"
_REWRITTEN = "lambda x, y: x + y"
_PATTERN = "def $F($$$ARGS): return $EXPR"
_REPLACEMENT = "lambda $$$ARGS: $EXPR"


def _run_checked(command: list[str], *, what: str) -> subprocess.CompletedProcess[str]:
    """Run a command; on failure print the output that explains the failure.

    `subprocess.run(..., capture_output=True, check=True)` raises a `CalledProcessError` whose
    string form carries the argv and the exit status and nothing else. The captured streams hang
    off the exception and are never printed, so a failure here reports THAT the artifact is bad
    while withholding WHY.

    Receipt: `validate-pypi-artifacts` failed on the v1.101.10 release run (30363114542). The log
    contains exactly `... 'run', '--lang', 'python', '--rewrite', ... returned non-zero exit
    status 1` -- `tg`'s own stderr, the one thing that would have named the cause, was captured and
    discarded. The failure was neither diagnosable from the log nor reproducible off it, and it
    blocked the PyPI publish for that version.

    A smoke test exists to explain a bad artifact. Swallowing the artifact's error message is the
    one thing it must not do.
    """
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"SMOKE FAILURE: {what}", file=sys.stderr)
        print(f"  command:     {command}", file=sys.stderr)
        print(f"  exit status: {result.returncode}", file=sys.stderr)
        print(f"  --- stdout ---\n{result.stdout or '(empty)'}", file=sys.stderr)
        print(f"  --- stderr ---\n{result.stderr or '(empty)'}", file=sys.stderr)
        raise SystemExit(1)
    return result


def _fail(what: str, *, detail: str) -> None:
    """A wrong-output failure, reported with the output rather than as a bare AssertionError."""
    print(f"SMOKE FAILURE: {what}", file=sys.stderr)
    print(detail, file=sys.stderr)
    raise SystemExit(1)


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
        _run_checked(
            [str(python_exe), "-m", "pip", "install", *dependencies],
            what="install project dependencies into the smoke venv",
        )
    _run_checked(
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
        what=f"install the built tensor-grep=={version} artifact",
    )
    _run_checked(
        [
            str(python_exe),
            "-c",
            (
                "import importlib.metadata as m; "
                f"assert m.version('tensor-grep') == '{version}'; "
                "import tensor_grep"
            ),
        ],
        what=f"import tensor_grep and confirm it reports version {version}",
    )
    tg_exe = str(_venv_tg(venv_dir))
    _run_checked([tg_exe, "--version"], what="tg --version")

    rewrite_smoke_dir = work_dir / "rewrite-smoke"
    rewrite_smoke_dir.mkdir(parents=True, exist_ok=True)

    # Run the probes from this process rather than through a nested `python -c`. The nesting bought
    # nothing -- the inner interpreter only wrote a file and shelled out to `tg` -- while putting a
    # second CalledProcessError between the real failure and the log.
    plan_source = rewrite_smoke_dir / "plan.py"
    plan_source.write_text(_SOURCE, encoding="utf-8")
    plan = _run_checked(
        [tg_exe, "run", "--lang", "python", "--rewrite", _REPLACEMENT, _PATTERN, str(plan_source)],
        what="tg run --rewrite (plan mode)",
    )
    if _REWRITTEN not in plan.stdout:
        _fail(
            "tg run --rewrite planned no usable edit",
            detail=(
                f"  expected {_REWRITTEN!r} in stdout\n"
                f"  --- stdout ---\n{plan.stdout or '(empty)'}\n"
                f"  --- stderr ---\n{plan.stderr or '(empty)'}"
            ),
        )

    apply_source = rewrite_smoke_dir / "apply.py"
    apply_source.write_text(_SOURCE, encoding="utf-8")
    applied = _run_checked(
        [
            tg_exe,
            "run",
            "--lang",
            "python",
            "--rewrite",
            _REPLACEMENT,
            "--apply",
            _PATTERN,
            str(apply_source),
        ],
        what="tg run --rewrite --apply",
    )
    final = apply_source.read_text(encoding="utf-8")
    expected = f"{_REWRITTEN}\n"
    if final != expected:
        _fail(
            "tg run --rewrite --apply did not rewrite the file on disk",
            detail=(
                f"  expected: {expected!r}\n"
                f"  actual:   {final!r}\n"
                f"  --- stdout ---\n{applied.stdout or '(empty)'}\n"
                f"  --- stderr ---\n{applied.stderr or '(empty)'}"
            ),
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
