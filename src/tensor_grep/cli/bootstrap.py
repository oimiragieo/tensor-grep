from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from tensor_grep.cli.install_channel import format_display_version

_KNOWN_COMMANDS = {
    "search",
    "calibrate",
    "devices",
    "classify",
    "run",
    "scan",
    "test",
    "new",
    "lsp",
    "mcp",
    "upgrade",
    "update",
}

_TG_ONLY_SEARCH_FLAGS = {
    "--ast",
    "--cpu",
    "--debug",
    "--files",
    "--files-with-matches",
    "--files-without-match",
    "--format",
    "--gpu-device-ids",
    "--json",
    "--ndjson",
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


def _prefer_rust_first_search() -> bool:
    value = os.environ.get("TG_RUST_FIRST_SEARCH", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


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
    pkg_version = format_display_version(pkg_version)

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


def _resolve_native_tg_binary() -> str | None:
    override = os.environ.get("TG_NATIVE_TG_BINARY", "").strip()
    if override:
        override_path = Path(override).expanduser().resolve()
        if override_path.is_file():
            return str(override_path)
    repo_root = Path(__file__).resolve().parents[3]
    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    candidates = [
        repo_root / "rust_core" / "target" / "release" / binary_name,
        repo_root / "rust_core" / "target" / "debug" / binary_name,
    ]
    existing = [candidate for candidate in candidates if candidate.is_file()]
    if not existing:
        return None
    return str(max(existing, key=lambda candidate: candidate.stat().st_mtime_ns))


def _can_delegate_to_native_tg_search(search_args: list[str]) -> bool:
    if not search_args:
        return False

    supported_trigger = any(
        arg in {"--cpu", "--json", "--ndjson", "--gpu-device-ids"}
        or arg.startswith("--gpu-device-ids=")
        for arg in search_args
    )
    if not supported_trigger:
        return False

    unsupported_flags = {
        "--ast",
        "--files",
        "--files-with-matches",
        "--files-without-match",
        "--format",
        "--lang",
        "--ltl",
        "--only-matching",
        "--replace",
        "--stats",
        "-l",
        "-o",
        "-r",
    }
    unsupported_prefixes = ("--format=", "--lang=", "--replace=")
    return not any(
        arg in unsupported_flags or arg.startswith(unsupported_prefixes) for arg in search_args
    )


def _run_native_tg_search(binary_name: str, search_args: list[str]) -> int:
    result = subprocess.run([binary_name, "search", *search_args], check=False)
    return int(result.returncode)


def _run_rg_passthrough(binary_name: str, search_args: list[str]) -> int:
    result = subprocess.run([binary_name, *search_args], check=False)
    return int(result.returncode)


def _run_full_cli() -> None:
    from tensor_grep.cli.main import main_entry as full_main_entry

    full_main_entry()


def _run_ast_workflow_cli(argv: list[str]) -> None:
    from tensor_grep.cli.ast_workflows import main_entry as ast_main_entry

    ast_main_entry(argv)


def main_entry() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in {"--version", "-V"}:
        _print_version()
        raise SystemExit(0)

    if argv and argv[0] in {"run", "scan", "test"}:
        _run_ast_workflow_cli(argv)
        return

    search_args = _normalize_search_invocation(argv)
    if search_args is not None:
        native_binary = _resolve_native_tg_binary()
        if native_binary is not None and (
            _can_delegate_to_native_tg_search(search_args)
            or (_prefer_rust_first_search() and not _requires_full_cli(search_args))
        ):
            raise SystemExit(_run_native_tg_search(native_binary, search_args))

        if not _requires_full_cli(search_args):
            binary_name = _resolve_rg_binary()
            if binary_name is not None:
                raise SystemExit(_run_rg_passthrough(binary_name, search_args))

    _run_full_cli()


if __name__ == "__main__":
    main_entry()
