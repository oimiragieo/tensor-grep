from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from tensor_grep.cli.commands import KNOWN_COMMANDS as _KNOWN_COMMANDS
from tensor_grep.cli.commands import PYTHON_FULL_HELP_COMMANDS as _PYTHON_FULL_HELP_COMMANDS
from tensor_grep.cli.runtime_paths import (
    env_flag_enabled,
    resolve_native_tg_binary,
    resolve_ripgrep_binary,
)
from tensor_grep.cli.subprocess_policy import run_subprocess

_TG_ONLY_SEARCH_FLAGS = {
    "--ast",
    "--cpu",
    "--debug",
    "--files",
    "--files-with-matches",
    "--files-without-match",
    "--force-cpu",
    "--format",
    "--generate",
    "--glob",
    "--gpu-device-ids",
    "--allow-broad-generated-scan",
    "--json",
    "--ndjson",
    "--pcre2-version",
    "--lang",
    "--ltl",
    "--replace",
    "--stats",
    "--type-list",
    "-l",
    "-g",
    "-r",
}

_TG_ONLY_SEARCH_FLAG_PREFIXES = (
    "--format=",
    "--gpu-device-ids=",
    "--lang=",
    "--replace=",
)

_SCAN_FULL_CLI_FLAGS = {
    "--help",
    "-h",
    "--baseline",
    "--allow-broad-generated-scan",
    "--glob",
    "--include-evidence-snippets",
    "--inline-rules",
    "--json",
    "--justification",
    "--language",
    "--max-depth",
    "--max-evidence-snippet-chars",
    "--max-evidence-snippets-per-file",
    "--path",
    "--rule",
    "--ruleset",
    "--suppressions",
    "--write-baseline",
    "--write-suppressions",
    "--type",
    "-g",
    "-r",
    "-t",
}

_SCAN_FULL_CLI_FLAG_PREFIXES = (
    "--allow-broad-generated-scan=",
    "--baseline=",
    "--glob=",
    "--justification=",
    "--language=",
    "--max-depth=",
    "--max-evidence-snippet-chars=",
    "--max-evidence-snippets-per-file=",
    "--path=",
    "--rule=",
    "--ruleset=",
    "--suppressions=",
    "--type=",
    "--write-baseline=",
    "--write-suppressions=",
)
_GUARDED_BROAD_SEARCH_ROOTS = {".claude", ".claude/context"}
_BROAD_GENERATED_SCAN_DIR_NAMES = {
    "__pycache__",
    ".claude",
    ".cache",
    ".cargo",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".npm",
    ".nuget",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    "appdata",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
_BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD = 3
_BROAD_WORKSPACE_PROJECT_MARKERS = {
    ".git",
    "Cargo.toml",
    "build.gradle",
    "composer.json",
    "deno.json",
    "go.mod",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "settings.gradle",
}
_SEARCH_PATTERN_FLAGS = {"-e", "--regexp"}
_SEARCH_LITERAL_FLAGS = {"-F", "--fixed-strings"}
_SEARCH_PCRE2_FLAGS = {"-P", "--pcre2"}
_SEARCH_FLAGS_WITH_VALUES = {
    "-A",
    "-B",
    "-C",
    "-E",
    "-M",
    "-g",
    "-j",
    "-m",
    "--after-context",
    "--before-context",
    "--color",
    "--colors",
    "--context",
    "--context-separator",
    "--dfa-size-limit",
    "--encoding",
    "--engine",
    "--field-context-separator",
    "--field-match-separator",
    "--file",
    "-f",
    "--glob",
    "--gpu-device-ids",
    "--hostname-bin",
    "--hyperlink-format",
    "--iglob",
    "--ignore-file",
    "--max-columns",
    "--max-count",
    "--max-depth",
    "--maxdepth",
    "--max-filesize",
    "--path-separator",
    "--pre",
    "--pre-glob",
    "--regex-size-limit",
    "--replace",
    "--sort",
    "--sortr",
    "--threads",
    "--type",
    "--type-add",
    "--type-clear",
    "--type-not",
    "-d",
    "-r",
    "-t",
    "-T",
}
_SEARCH_ATTACHED_VALUE_SHORT_FLAGS = (
    "-A",
    "-B",
    "-C",
    "-E",
    "-M",
    "-d",
    "-f",
    "-g",
    "-j",
    "-m",
    "-r",
    "-t",
    "-T",
)
_SEARCH_GENERATED_SCAN_BOUND_FLAGS = {
    "-d",
    "-g",
    "-t",
    "-T",
    "--glob",
    "--iglob",
    "--max-depth",
    "--maxdepth",
    "--type",
    "--type-not",
}
_SEARCH_GENERATED_SCAN_BOUND_PREFIXES = (
    "--glob=",
    "--iglob=",
    "--max-depth=",
    "--maxdepth=",
    "--type=",
    "--type-not=",
)
_SEARCH_NO_IGNORE_FLAGS = {
    "--no-ignore",
    "--no-ignore-dot",
    "--no-ignore-exclude",
    "--no-ignore-files",
    "--no-ignore-global",
    "--no-ignore-parent",
    "--no-ignore-vcs",
}
_SEARCH_HIDDEN_FLAGS = {"-.", "--hidden"}


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

    print(f"tensor-grep {pkg_version}")
    if any(arg in {"--verbose", "-v"} for arg in sys.argv[2:]):
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


def _is_public_help_invocation(argv: list[str]) -> bool:
    if len(argv) == 1 and argv[0] in {"--help", "-h"}:
        return True
    if len(argv) == 2 and argv[0] in _PYTHON_FULL_HELP_COMMANDS and argv[1] in {"--help", "-h"}:
        return True
    return False


def _requires_full_cli(search_args: list[str]) -> bool:
    if not search_args:
        return True
    for arg in search_args:
        if arg in {"--help", "-h"}:
            return True
        if arg in {"--show-completion", "--install-completion"}:
            return True
        if arg in _TG_ONLY_SEARCH_FLAGS:
            return True
        if arg.startswith(_TG_ONLY_SEARCH_FLAG_PREFIXES):
            return True
    return False


def _strip_noop_rg_format(search_args: list[str]) -> list[str] | None:
    stripped: list[str] = []
    index = 0
    while index < len(search_args):
        arg = search_args[index]
        if arg == "--format":
            index += 1
            if index >= len(search_args) or search_args[index] != "rg":
                return None
        elif arg.startswith("--format="):
            if arg.split("=", 1)[1] != "rg":
                return None
        else:
            stripped.append(arg)
        index += 1
    return stripped


def _explicit_rg_format_requested(search_args: list[str]) -> bool:
    for index, arg in enumerate(search_args):
        if arg == "--format":
            return index + 1 < len(search_args) and search_args[index + 1] == "rg"
        if arg == "--format=rg":
            return True
    return False


def _explicit_json_requested(search_args: list[str]) -> bool:
    return "--json" in search_args


def _can_delegate_to_native_tg_search(search_args: list[str]) -> bool:
    if not search_args:
        return False

    supported_trigger = any(
        arg in {"--cpu", "--force-cpu", "--json", "--ndjson", "--gpu-device-ids"}
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
        "--replace",
        "--stats",
        "-l",
        "-r",
    }
    unsupported_prefixes = ("--format=", "--lang=", "--replace=")
    return not any(
        arg in unsupported_flags or arg.startswith(unsupported_prefixes) for arg in search_args
    )


def _search_args_include_guarded_broad_root(search_args: list[str]) -> bool:
    for arg in search_args:
        if not arg or arg == "-" or arg.startswith("-"):
            continue
        normalized = arg.replace("\\", "/").rstrip("/").lower()
        if normalized in _GUARDED_BROAD_SEARCH_ROOTS:
            return True
        if any(normalized.endswith(f"/{root}") for root in _GUARDED_BROAD_SEARCH_ROOTS):
            return True
    return False


def _is_short_flag_with_attached_value(arg: str) -> bool:
    if not arg.startswith("-") or arg.startswith("--"):
        return False
    return any(
        arg.startswith(flag) and len(arg) > len(flag) for flag in _SEARCH_ATTACHED_VALUE_SHORT_FLAGS
    )


def _search_args_include_generated_scan_bound(search_args: list[str]) -> bool:
    for arg in search_args:
        if arg in _SEARCH_GENERATED_SCAN_BOUND_FLAGS:
            return True
        if arg.startswith(_SEARCH_GENERATED_SCAN_BOUND_PREFIXES):
            return True
        if not arg.startswith("--"):
            if any(
                arg.startswith(flag) and len(arg) > len(flag) for flag in ("-d", "-g", "-t", "-T")
            ):
                return True
    return False


def _search_args_request_unrestricted_generated_scan(search_args: list[str]) -> bool:
    files_mode = "--files" in search_args
    if files_mode and any(arg in _SEARCH_HIDDEN_FLAGS for arg in search_args):
        return True
    if any(arg in _SEARCH_NO_IGNORE_FLAGS for arg in search_args):
        return True
    return any(
        arg == "--unrestricted" or (arg.startswith("-u") and not arg.startswith("--"))
        for arg in search_args
    )


def _search_path_args(search_args: list[str]) -> list[str]:
    paths: list[str] = []
    bare_pattern_seen = False
    regexp_pattern_seen = False
    skip_next = False
    parse_options = True
    for index, arg in enumerate(search_args):
        if skip_next:
            skip_next = False
            continue
        if parse_options and arg == "--":
            parse_options = False
            continue
        if parse_options:
            if arg in _SEARCH_PATTERN_FLAGS:
                regexp_pattern_seen = True
                skip_next = index + 1 < len(search_args)
                continue
            if any(arg.startswith(f"{flag}=") for flag in _SEARCH_PATTERN_FLAGS):
                regexp_pattern_seen = True
                continue
            if arg in _SEARCH_FLAGS_WITH_VALUES:
                skip_next = index + 1 < len(search_args)
                continue
            if any(arg.startswith(f"{flag}=") for flag in _SEARCH_FLAGS_WITH_VALUES):
                continue
            if _is_short_flag_with_attached_value(arg):
                continue
            if arg.startswith("-"):
                continue
        if not regexp_pattern_seen and not bare_pattern_seen:
            bare_pattern_seen = True
            continue
        paths.append(arg)
    return paths or ["."]


def _path_has_project_marker(path: Path) -> bool:
    for marker in _BROAD_WORKSPACE_PROJECT_MARKERS:
        try:
            if (path / marker).exists():
                return True
        except OSError:
            continue
    return False


def _search_paths_include_generated_root(paths: list[str]) -> bool:
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = Path(raw_path)
        try:
            if not path.is_dir():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if path.name.lower() in _BROAD_GENERATED_SCAN_DIR_NAMES:
                return True
            if resolved.name.lower() in _BROAD_GENERATED_SCAN_DIR_NAMES:
                return True
            for child in path.iterdir():
                if child.is_dir() and child.name.lower() in _BROAD_GENERATED_SCAN_DIR_NAMES:
                    return True
        except OSError:
            continue
    return False


def _search_paths_include_workspace_root(paths: list[str]) -> bool:
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = Path(raw_path)
        try:
            if not path.is_dir() or _path_has_project_marker(path):
                continue
            project_children = 0
            for child in path.iterdir():
                try:
                    if child.is_dir() and _path_has_project_marker(child):
                        project_children += 1
                except OSError:
                    continue
                if project_children >= _BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD:
                    return True
        except OSError:
            continue
    return False


def _search_args_include_unbounded_broad_scan(search_args: list[str]) -> bool:
    if "--allow-broad-generated-scan" in search_args:
        return False
    if _search_args_include_generated_scan_bound(search_args):
        return False
    paths = _search_path_args(search_args)
    if _search_paths_include_workspace_root(paths):
        return True
    return _search_args_request_unrestricted_generated_scan(
        search_args
    ) and _search_paths_include_generated_root(paths)


def _regex_patterns_from_search_args(search_args: list[str]) -> list[str]:
    skip_next = False
    bare_pattern: str | None = None
    regexp_patterns: list[str] = []
    for index, arg in enumerate(search_args):
        if skip_next:
            skip_next = False
            continue
        if arg in _SEARCH_PATTERN_FLAGS:
            if index + 1 < len(search_args):
                regexp_patterns.append(search_args[index + 1])
                skip_next = True
            continue
        if any(arg.startswith(f"{flag}=") for flag in _SEARCH_PATTERN_FLAGS):
            regexp_patterns.append(arg.split("=", 1)[1])
            continue
        if arg in _SEARCH_FLAGS_WITH_VALUES:
            skip_next = True
            continue
        if any(arg.startswith(f"{flag}=") for flag in _SEARCH_FLAGS_WITH_VALUES):
            continue
        if arg.startswith("-"):
            continue
        if bare_pattern is None:
            bare_pattern = arg
    if regexp_patterns:
        return regexp_patterns
    return [bare_pattern] if bare_pattern is not None else []


def _search_args_include_obviously_invalid_regex(search_args: list[str]) -> bool:
    if any(arg in _SEARCH_LITERAL_FLAGS for arg in search_args):
        return False
    if any(arg in _SEARCH_PCRE2_FLAGS for arg in search_args):
        return False
    for pattern in _regex_patterns_from_search_args(search_args):
        if not pattern:
            continue
        try:
            re.compile(pattern)
        except re.error:
            return True
    return False


def _effective_native_tg_search_args(search_args: list[str]) -> list[str]:
    if (
        not env_flag_enabled("TG_FORCE_CPU")
        or "--cpu" in search_args
        or "--force-cpu" in search_args
    ):
        return list(search_args)
    return [*search_args, "--cpu"]


def _streaming_passthrough_returncode(
    argv: list[str], *, timeout_env_var: str | None = None
) -> int:
    """Run an interactive streaming passthrough, returning its exit code and converting
    a subprocess timeout into a clean exit 124 instead of an uncaught TimeoutExpired
    traceback that also SIGKILLs the child mid-stream. ripgrep never self-terminates a
    search, so a timeout here is tg-imposed; surface it with the coreutils ``timeout``
    convention rather than crashing the CLI (audit B5/#10).
    """
    try:
        if timeout_env_var is not None:
            result = run_subprocess(argv, check=False, timeout_env_var=timeout_env_var)
        else:
            result = run_subprocess(argv, check=False)
        return int(result.returncode)
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            "tensor-grep: search exceeded the configured timeout and was stopped "
            "(adjust TG_RG_TIMEOUT_SECONDS / TG_SUBPROCESS_TIMEOUT_SECONDS).\n"
        )
        return 124


def _run_native_tg_search(binary_name: str, search_args: list[str]) -> int:
    return _streaming_passthrough_returncode([binary_name, "search", *search_args])


def _run_native_tg_command(binary_name: str, argv: list[str]) -> int:
    return _streaming_passthrough_returncode([binary_name, *argv])


def _run_rg_passthrough(binary_name: str, search_args: list[str]) -> int:
    return _streaming_passthrough_returncode(
        [binary_name, *search_args], timeout_env_var="TG_RG_TIMEOUT_SECONDS"
    )


def _run_full_cli() -> None:
    from tensor_grep.cli.main import main_entry as full_main_entry

    full_main_entry()


def _run_ast_workflow_cli(argv: list[str]) -> None:
    from tensor_grep.cli.ast_workflows import main_entry as ast_main_entry

    ast_main_entry(argv)


def _scan_requires_full_cli(scan_args: list[str]) -> bool:
    return any(
        arg in _SCAN_FULL_CLI_FLAGS or arg.startswith(_SCAN_FULL_CLI_FLAG_PREFIXES)
        for arg in scan_args
    )


# ast-grep semantic options that the native `run` handler cannot serve itself and
# bounces to the Python sidecar. These MUST be handled by the in-process Python AST
# workflow rather than re-delegated to the native binary: the native binary spawns
# `python -m tensor_grep run ...` for these options, and if bootstrap delegated that
# spawn straight back to native we would ping-pong native<->python forever (the
# `tg run --strictness/--selector/--stdin/--globs` hang). Keep this list in sync with
# `ast_run_requires_python_passthrough` in rust_core/src/main.rs.
_RUN_AST_WORKFLOW_FLAGS = ("--selector", "--strictness", "--stdin", "--globs")
_RUN_AST_WORKFLOW_FLAG_PREFIXES = ("--selector=", "--strictness=", "--globs=")


def _run_requires_ast_workflow(run_args: list[str]) -> bool:
    return any(
        arg in _RUN_AST_WORKFLOW_FLAGS or arg.startswith(_RUN_AST_WORKFLOW_FLAG_PREFIXES)
        for arg in run_args
    )


def main_entry() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in {"--version", "-V"}:
        _print_version()
        raise SystemExit(0)
    if _is_public_help_invocation(argv):
        _run_full_cli()
        return

    if argv and argv[0] in {"run", "scan", "test", "ast-info"}:
        if argv[0] == "run":
            # ast-grep semantic options (--selector/--strictness/--stdin/--globs) are
            # served by the Python AST workflow. Routing them to the native binary would
            # bounce right back here (native spawns `python -m tensor_grep run ...`) and
            # ping-pong forever, so handle them directly in Python.
            if _run_requires_ast_workflow(argv[1:]):
                _run_ast_workflow_cli(argv)
                return
            native_binary_path = resolve_native_tg_binary()
            native_binary = str(native_binary_path) if native_binary_path else None
            if native_binary is not None:
                raise SystemExit(_run_native_tg_command(native_binary, argv))
            _run_full_cli()
            return
        if (argv[0] in {"test", "ast-info"}) or (
            argv[0] == "scan" and _scan_requires_full_cli(argv[1:])
        ):
            _run_full_cli()
            return
        _run_ast_workflow_cli(argv)
        return

    search_args = _normalize_search_invocation(argv)
    if search_args is not None:
        passthrough_search_args = _strip_noop_rg_format(search_args)
        if passthrough_search_args is None:
            _run_full_cli()
            return
        explicit_rg_json = _explicit_rg_format_requested(search_args) and _explicit_json_requested(
            search_args
        )

        effective_search_args = _effective_native_tg_search_args(passthrough_search_args)
        native_binary_path = resolve_native_tg_binary()
        native_binary = str(native_binary_path) if native_binary_path else None
        guarded_broad_root = _search_args_include_guarded_broad_root(
            passthrough_search_args
        ) or _search_args_include_unbounded_broad_scan(passthrough_search_args)
        invalid_regex = _search_args_include_obviously_invalid_regex(passthrough_search_args)

        if (
            not explicit_rg_json
            and native_binary is not None
            and not guarded_broad_root
            and not invalid_regex
            and (
                _can_delegate_to_native_tg_search(effective_search_args)
                or (_prefer_rust_first_search() and not _requires_full_cli(passthrough_search_args))
            )
        ):
            command_args = (
                effective_search_args
                if _can_delegate_to_native_tg_search(effective_search_args)
                else search_args
            )
            raise SystemExit(_run_native_tg_search(native_binary, command_args))

        if (
            not guarded_broad_root
            and not invalid_regex
            and (explicit_rg_json or not _requires_full_cli(passthrough_search_args))
        ):
            rg_binary_path = resolve_ripgrep_binary()
            binary_name = str(rg_binary_path) if rg_binary_path else None
            if binary_name is not None:
                raise SystemExit(_run_rg_passthrough(binary_name, passthrough_search_args))

    _run_full_cli()


if __name__ == "__main__":
    main_entry()
