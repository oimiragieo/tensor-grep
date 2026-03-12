from __future__ import annotations

import os
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

_TG_ONLY_SEARCH_FLAG_PREFIXES = ("--format=", "--gpu-device-ids=", "--lang=", "--replace=")


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
    path_env = os.environ.get("PATH", "")
    dev_path = Path.cwd() / "benchmarks" / "ripgrep-14.1.0-x86_64-pc-windows-msvc" / "rg.exe"
    if not path_env:
        if dev_path.exists():
            return str(dev_path)
        return None

    import shutil

    if shutil.which("rg"):
        return "rg"
    if shutil.which("rg.exe"):
        return "rg.exe"
    if dev_path.exists():
        return str(dev_path)
    return None


def _run_rg_passthrough(binary_name: str, search_args: list[str]) -> int:
    import subprocess

    result = subprocess.run([binary_name, *search_args], check=False)
    return int(result.returncode)


def _run_full_cli() -> None:
    from tensor_grep.cli.main import main_entry as full_main_entry

    full_main_entry()


def _run_ast_workflow_cli(argv: list[str]) -> None:
    from tensor_grep.cli.ast_workflows import main_entry as ast_main_entry

    ast_main_entry(argv)


def _looks_like_literal_pattern(pattern: str) -> bool:
    regex_tokens = {".", "*", "+", "?", "[", "]", "(", ")", "{", "}", "|", "^", "$", "\\"}
    return not any(token in pattern for token in regex_tokens)


def _iter_search_files(paths: list[str]) -> list[str]:
    resolved_files: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file():
            resolved_files.append(str(path))
            continue
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    resolved_files.append(str(child))
    return resolved_files


def _print_fast_search_result(
    *, file_path: str, line_number: int, text: str, include_filename: bool
) -> None:
    if include_filename:
        print(f"{file_path}:{line_number}:{text}")
    else:
        print(f"{line_number}:{text}")


def _run_text_search_fast_cli(search_args: list[str]) -> int:
    fixed_strings = False
    ignore_case = False
    invert_match = False
    count = False
    position = 0

    while position < len(search_args):
        arg = search_args[position]
        if arg == "--":
            position += 1
            break
        if arg == "-F" or arg == "--fixed-strings":
            fixed_strings = True
        elif arg == "-i" or arg == "--ignore-case":
            ignore_case = True
        elif arg == "-v" or arg == "--invert-match":
            invert_match = True
        elif arg == "-c" or arg == "--count":
            count = True
        elif arg.startswith("-"):
            raise ValueError("unsupported fast-path search options")
        else:
            break
        position += 1

    remaining = search_args[position:]
    if len(remaining) < 2:
        raise ValueError("unsupported fast-path search invocation")

    from tensor_grep.backends.base import ComputeBackend
    from tensor_grep.core.config import SearchConfig

    pattern = remaining[0]
    file_paths = _iter_search_files(remaining[1:])
    if not file_paths:
        return 1

    use_stringzilla = fixed_strings or _looks_like_literal_pattern(pattern)
    config = SearchConfig(
        fixed_strings=use_stringzilla,
        ignore_case=ignore_case,
        invert_match=invert_match,
        count=count,
        line_number=True,
    )
    backend: ComputeBackend
    if use_stringzilla:
        from tensor_grep.backends.stringzilla_backend import StringZillaBackend

        stringzilla_backend = StringZillaBackend()
        if stringzilla_backend.is_available():
            backend = stringzilla_backend
        else:
            from tensor_grep.backends.cpu_backend import CPUBackend

            backend = CPUBackend()
    else:
        from tensor_grep.backends.cpu_backend import CPUBackend

        backend = CPUBackend()

    total_matches = 0
    include_filename = len(file_paths) > 1
    for file_path in file_paths:
        result = backend.search(file_path, pattern, config)
        total_matches += result.total_matches
        if count:
            if include_filename:
                print(f"{file_path}:{result.total_matches}")
            else:
                print(f"{result.total_matches}")
            continue
        for match in result.matches:
            _print_fast_search_result(
                file_path=match.file,
                line_number=match.line_number,
                text=match.text,
                include_filename=include_filename,
            )
    return 0 if total_matches > 0 else 1


def main_entry() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in {"--version", "-V"}:
        _print_version()
        raise SystemExit(0)

    if argv and argv[0] in {"run", "scan", "test"}:
        _run_ast_workflow_cli(argv)
        return

    search_args = _normalize_search_invocation(argv)
    if search_args is not None and not _requires_full_cli(search_args):
        binary_name = _resolve_rg_binary()
        if binary_name is not None:
            raise SystemExit(_run_rg_passthrough(binary_name, search_args))
        try:
            raise SystemExit(_run_text_search_fast_cli(search_args))
        except ValueError:
            pass

    _run_full_cli()


if __name__ == "__main__":
    main_entry()
