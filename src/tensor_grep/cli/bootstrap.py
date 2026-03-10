from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_KNOWN_COMMANDS = {
    "search",
    "devices",
    "classify",
    "run",
    "scan",
    "test",
    "new",
    "lsp",
    "mcp",
    "upgrade",
}

_TG_ONLY_SEARCH_FLAGS = {
    "--ast",
    "--debug",
    "--files",
    "--files-with-matches",
    "--files-without-match",
    "--format",
    "--gpu-device-ids",
    "--json",
    "--lang",
    "--ltl",
    "--only-matching",
    "--replace",
    "--stats",
    "-l",
    "-o",
    "-r",
}

_TG_ONLY_SEARCH_FLAG_PREFIXES = (
    "--format=",
    "--gpu-device-ids=",
    "--lang=",
    "--replace=",
)


def _read_project_version_fallback() -> str:
    try:
        pyproject_path = Path(__file__).resolve().parents[3] / "pyproject.toml"
        for line in pyproject_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version = "):
                return stripped.split('"', 2)[1]
    except Exception:
        pass
    return "0.0.0"


def _print_version() -> None:
    try:
        from importlib.metadata import version

        pkg_version = version("tensor-grep")
    except Exception:
        pkg_version = _read_project_version_fallback()

    print(f"tensor-grep {pkg_version}")
    print()
    print("features:+gpu-cudf,+gpu-torch,+rust-core")
    print("simd(compile):+SSE2,-SSSE3,-AVX2")
    print("simd(runtime):+SSE2,+SSSE3,+AVX2")
    print()
    print("Arrow Zero-Copy IPC is available")


def _normalize_search_invocation(argv: list[str]) -> list[str] | None:
    if not argv:
        return None

    first_arg = argv[0]
    if first_arg == "search":
        return argv[1:]
    if first_arg in _KNOWN_COMMANDS or first_arg.startswith("--typer-"):
        return None
    return argv


def _requires_full_cli(search_args: list[str]) -> bool:
    if not search_args:
        return True
    for arg in search_args:
        if arg in {"--help", "-h"}:
            return True
        if arg in _TG_ONLY_SEARCH_FLAGS:
            return True
        if arg.startswith(_TG_ONLY_SEARCH_FLAG_PREFIXES):
            return True
    return False


def _resolve_rg_binary() -> str | None:
    if shutil.which("rg"):
        return "rg"
    if shutil.which("rg.exe"):
        return "rg.exe"

    dev_path = Path.cwd() / "benchmarks" / "ripgrep-14.1.0-x86_64-pc-windows-msvc" / "rg.exe"
    if dev_path.exists():
        return str(dev_path)
    return None


def _run_rg_passthrough(binary_name: str, search_args: list[str]) -> int:
    result = subprocess.run([binary_name, *search_args], check=False)
    return int(result.returncode)


def _run_full_cli() -> None:
    from tensor_grep.cli.main import main_entry as full_main_entry

    full_main_entry()


def main_entry() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in {"--version", "-V"}:
        _print_version()
        raise SystemExit(0)

    search_args = _normalize_search_invocation(argv)
    if search_args is not None and not _requires_full_cli(search_args):
        binary_name = _resolve_rg_binary()
        if binary_name is not None:
            raise SystemExit(_run_rg_passthrough(binary_name, search_args))

    _run_full_cli()


if __name__ == "__main__":
    main_entry()
