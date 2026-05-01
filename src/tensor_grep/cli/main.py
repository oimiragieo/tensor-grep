import json
import os
import re
import subprocess
import sys
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

# Rich's legacy Windows renderer can raise EINVAL when long help is piped through
# PowerShell. Disable Typer/Rich help before Typer imports when stdout is not a TTY.
if sys.platform.startswith("win") and not sys.stdout.isatty():
    os.environ.setdefault("TYPER_USE_RICH", "0")

import typer

from tensor_grep.cli import ast_workflows
from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.cli.lsp_provider_setup import (
    install_managed_lsp_providers,
    supported_lsp_languages,
)
from tensor_grep.cli.runtime_paths import (
    env_flag_enabled,
    resolve_native_tg_binary,
    resolve_ripgrep_binary,
)
from tensor_grep.core.observability import nvtx_range
from tensor_grep.core.result import MatchLine

if TYPE_CHECKING:
    from tensor_grep.backends.base import ComputeBackend
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.io.directory_scanner import DirectoryScanner

app = typer.Typer(
    help="""tensor-grep (tg) - Fast text, AST, indexed, and GPU-aware search CLI

Search code and large datasets with ripgrep-compatible text search, native AST search/rewrite,
persisted repeated-query acceleration, and optional GPU routing.

**Common usage**
- `tg PATTERN [PATH ...]`
- `tg search [OPTIONS] PATTERN [PATH ...]`
- `tg run PATTERN [PATH]`
- `tg scan --config sgconfig.yml`
- `tg doctor --with-lsp`
- `tg mcp`

**AI workflows**
- `tg map PATH`
- `tg context-render PATH --query "invoice flow"`
- `tg edit-plan PATH --query "add retry with tests"`
- `tg blast-radius-render PATH --symbol create_invoice`
- `tg session open PATH`
- `tg session daemon start PATH`

**Notes**
- Bare patterns are treated as `tg search`.
- Use `tg search --help` for ripgrep-compatible flags.
- `tg run --help` for AST rewrite flags.
- Lexical repo-map retrieval bridges camelCase, snake_case, and source-term planning queries.
- Use `tg doctor --json` for system, GPU, cache, and daemon diagnostics.
- Use `tg session --help` for cached edit-loop and daemon commands.

**Environment overrides**
- `TG_SIDECAR_PYTHON`: Path to the Python executable used for sidecar-backed commands.
- `TG_RG_PATH`: Path to the ripgrep executable used for text-search passthrough.""",
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="markdown",
)
checkpoint_app = typer.Typer(
    help="Create, list, and undo edit checkpoints.",
    no_args_is_help=True,
)
session_app = typer.Typer(
    help="Open and reuse cached repository-map sessions.",
    no_args_is_help=True,
)
session_daemon_app = typer.Typer(
    help="Run and inspect the warm localhost session daemon.",
    no_args_is_help=True,
)
review_bundle_app = typer.Typer(
    help="Create and verify enterprise review bundles.",
    no_args_is_help=True,
)

session_app.add_typer(session_daemon_app, name="daemon")


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


@lru_cache(maxsize=1)
def _json_output_version() -> int:
    try:
        main_rs = Path(__file__).resolve().parents[3] / "rust_core" / "src" / "main.rs"
        match = re.search(
            r"const\s+JSON_OUTPUT_VERSION\s*:\s*u32\s*=\s*(\d+)\s*;",
            main_rs.read_text(encoding="utf-8"),
        )
    except OSError:
        match = None
    return int(match.group(1)) if match else 1


_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS = (
    "regexp",
    "file_patterns",
    "pre",
    "pre_glob",
    "search_zip",
    "crlf",
    "dfa_size_limit",
    "encoding",
    "engine",
    "line_regexp",
    "mmap",
    "multiline",
    "multiline_dotall",
    "no_unicode",
    "null_data",
    "pcre2",
    "regex_size_limit",
    "smart_case",
    "stop_on_nonmatch",
    "text",
    "threads",
    "binary",
    "follow",
    "glob_case_insensitive",
    "hidden",
    "iglob",
    "ignore_file",
    "ignore_file_case_insensitive",
    "max_depth",
    "max_filesize",
    "no_ignore_dot",
    "no_ignore_exclude",
    "no_ignore_files",
    "no_ignore_global",
    "no_ignore_parent",
    "no_ignore_vcs",
    "no_require_git",
    "one_file_system",
    "file_type",
    "type_not",
    "type_add",
    "type_clear",
    "unrestricted",
    "after_context",
    "before_context",
    "block_buffered",
    "byte_offset",
    "color",
    "colors",
    "column",
    "context_separator",
    "field_context_separator",
    "field_match_separator",
    "heading",
    "hostname_bin",
    "hyperlink_format",
    "include_zero",
    "line_buffered",
    "max_columns",
    "max_columns_preview",
    "null",
    "only_matching",
    "path_separator",
    "passthru",
    "pretty",
    "quiet",
    "replace_str",
    "sort_by",
    "sort_by_reverse",
    "trim",
    "vimgrep",
    "with_filename",
    "no_filename",
    "count_matches",
    "debug",
    "no_ignore_messages",
    "no_messages",
    "stats",
    "trace",
    "list_files",
    "generate",
    "no_config",
    "pcre2_version",
    "type_list",
    "format_type",
    "ast",
    "lang",
    "ltl",
)


def _doctor_installed_version() -> str:
    try:
        from importlib.metadata import version

        return version("tensor-grep")
    except Exception:
        return _read_project_version_fallback()


def _doctor_session_daemon_status(path: str) -> dict[str, Any]:
    from tensor_grep.cli.session_daemon import get_session_daemon_status

    return get_session_daemon_status(path)


def _doctor_lsp_languages() -> list[str]:
    return supported_lsp_languages()


def _doctor_lsp_provider_statuses(path: str) -> list[dict[str, Any]]:
    from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager

    manager = ExternalLSPProviderManager()
    workspace_root = Path(path).resolve()
    return [
        manager.provider_status(language=language, workspace_root=workspace_root)
        for language in _doctor_lsp_languages()
    ]


def _doctor_rust_binary_version(native_tg_binary: Path | None) -> str | None:
    if not native_tg_binary:
        return None
    try:
        import subprocess

        res = subprocess.run(
            [str(native_tg_binary), "--version"], capture_output=True, text=True, timeout=2
        )
        if res.returncode == 0:
            return res.stdout.strip()
        return None
    except Exception:
        return None


def _doctor_gpu_status() -> dict[str, Any]:
    status: dict[str, Any] = {"available": False, "devices": [], "error": None}
    try:
        from tensor_grep.core.hardware.device_detect import DeviceDetector

        detector = DeviceDetector()
        status["available"] = detector.has_gpu()
        status["device_count"] = detector.get_device_count()
        for device in detector.list_devices():
            status["devices"].append({
                "id": device.device_id,
                "vram_total_mb": device.vram_capacity_mb,
            })
    except ImportError:
        status["error"] = "PyTorch/cuDF not installed"
    except Exception as e:
        status["error"] = str(e)
    return status


def _doctor_ast_cache_status(root_path: str, config_path: str) -> dict[str, Any]:
    root = Path(root_path).resolve()
    cache_file = root / ".tg_cache" / "ast" / "project_data_v6.json"
    status: dict[str, Any] = {"exists": False}
    if cache_file.exists():
        stat = cache_file.stat()
        status["exists"] = True
        status["size_bytes"] = stat.st_size
        status["mtime"] = stat.st_mtime
        stale = False
        try:
            cache_mtime = stat.st_mtime
            sgconfig = Path(config_path).resolve()
            if sgconfig.exists() and sgconfig.stat().st_mtime > cache_mtime:
                stale = True
            if not stale:
                with cache_file.open("r", encoding="utf-8") as f:
                    import json

                    data = json.load(f)
                val_meta = data.get("validation_metadata", {})
                for field in ("rule_files", "test_files", "tree_dirs"):
                    for file_path_str, recorded_mtime_ns in val_meta.get(field, {}).items():
                        p = Path(file_path_str)
                        if not p.exists() or p.stat().st_mtime_ns > recorded_mtime_ns:
                            stale = True
                            break
                    if stale:
                        break
        except Exception:
            pass
        status["stale"] = stale
    return status


def _doctor_resident_worker_status(path: str) -> dict[str, Any]:
    import socket

    root = Path(path).resolve()
    port_file = root / ".tg_cache" / "ast" / "worker_port.txt"
    status: dict[str, Any] = {"port_file_exists": False, "port": None, "responding": False}
    if port_file.exists():
        status["port_file_exists"] = True
        try:
            port = int(port_file.read_text(encoding="utf-8").strip())
            status["port"] = port
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect(("127.0.0.1", port))
                status["responding"] = True
        except Exception:
            status["responding"] = False
    return status


def _build_doctor_payload(
    path: str, config: str | None = None, *, with_lsp: bool
) -> dict[str, Any]:
    root = Path(path).resolve()
    if config:
        config_p = Path(config)
        resolved_config = config_p if config_p.is_absolute() else (root / config_p).resolve()
        root = resolved_config.parent
    else:
        resolved_config = root / "sgconfig.yml"
    native_tg_binary = resolve_native_tg_binary()
    env_keys = [
        "TG_NATIVE_TG_BINARY",
        "TG_FORCE_CPU",
        "TG_RESIDENT_AST",
        "TG_RUST_FIRST_SEARCH",
        "TG_RUST_EARLY_RG",
        "TG_RUST_EARLY_POSITIONAL_RG",
        "TENSOR_GREP_LSP_REQUEST_TIMEOUT_SECONDS",
        "TENSOR_GREP_LSP_INITIALIZE_TIMEOUT_SECONDS",
    ]
    payload: dict[str, Any] = {
        "version": _doctor_installed_version(),
        "platform": sys.platform,
        "python_executable": sys.executable,
        "python_version": ".".join([str(x) for x in sys.version_info[:3]]),
        "invoked_as": sys.argv[0] if sys.argv else "tg",
        "root": str(root),
        "config": str(resolved_config),
        "native_tg_binary": str(native_tg_binary) if native_tg_binary is not None else None,
        "native_tg_binary_exists": native_tg_binary is not None,
        "rust_binary_version": _doctor_rust_binary_version(native_tg_binary),
        "gpu": _doctor_gpu_status(),
        "ast_cache": _doctor_ast_cache_status(str(root), str(resolved_config)),
        "resident_worker": _doctor_resident_worker_status(str(root)),
        "env": {key: os.environ[key] for key in env_keys if os.environ.get(key)},
        "session_daemon": _doctor_session_daemon_status(str(root)),
    }
    if with_lsp:
        payload["lsp"] = {
            "enabled": True,
            "providers": _doctor_lsp_provider_statuses(str(root)),
        }
    else:
        payload["lsp"] = {"enabled": False, "providers": []}
    return payload


def _render_doctor_payload(payload: dict[str, Any]) -> str:
    lines = [
        "tensor-grep doctor",
        f"version: {payload['version']}",
        f"platform: {payload['platform']}",
        f"python: {payload['python_executable']} ({payload.get('python_version', 'unknown')})",
        f"invoked_as: {payload['invoked_as']}",
        f"root: {payload['root']}",
    ]
    native_tg_binary = payload.get("native_tg_binary")
    lines.append(f"native_tg_binary: {native_tg_binary or 'missing'}")
    if rust_version := payload.get("rust_binary_version"):
        lines.append(f"rust_binary_version:\n  {rust_version.replace(chr(10), chr(10) + '  ')}")

    gpu_payload = cast(dict[str, Any], payload.get("gpu", {}))
    lines.append(f"gpu: available={gpu_payload.get('available', False)}")
    if gpu_payload.get("error"):
        lines.append(f"  error: {gpu_payload['error']}")
    for dev in gpu_payload.get("devices", []):
        lines.append(f"  device {dev.get('id')}: {dev.get('vram_total_mb')} MB VRAM")

    ast_payload = cast(dict[str, Any], payload.get("ast_cache", {}))
    lines.append(f"ast_cache: exists={ast_payload.get('exists', False)}")
    if ast_payload.get("exists"):
        lines.append(f"  size: {ast_payload.get('size_bytes')} bytes")
        lines.append(f"  mtime: {ast_payload.get('mtime')}")
        lines.append(f"  stale: {ast_payload.get('stale')}")

    worker_payload = cast(dict[str, Any], payload.get("resident_worker", {}))
    lines.append(
        f"resident_worker: port_file_exists={worker_payload.get('port_file_exists', False)} "
        f"port={worker_payload.get('port')} responding={worker_payload.get('responding', False)}"
    )

    env_payload = cast(dict[str, str], payload.get("env", {}))
    if env_payload:
        lines.append("env:")
        for key in sorted(env_payload):
            lines.append(f"  {key}={env_payload[key]}")

    session_payload = cast(dict[str, Any], payload["session_daemon"])
    if session_payload.get("running"):
        lines.append(
            "session_daemon: "
            f"running host={session_payload['host']} port={session_payload['port']} pid={session_payload['pid']}"
        )
    else:
        state = "stale-metadata" if session_payload.get("stale_metadata") else "stopped"
        lines.append(f"session_daemon: {state}")

    lsp_payload = cast(dict[str, Any], payload.get("lsp", {}))
    if lsp_payload.get("enabled"):
        lines.append("lsp_providers:")
        for current in cast(list[dict[str, Any]], lsp_payload.get("providers", [])):
            command = current.get("command") or []
            command_str = " ".join(str(part) for part in command) if command else "missing"
            status = "running" if current.get("running") else "idle"
            availability = "available" if current.get("available") else "unavailable"
            source = current.get("command_source", "path")
            managed_root = current.get("managed_provider_root")
            last_error = current.get("last_error")
            suffix = f" last_error={last_error}" if last_error else ""
            if managed_root:
                suffix = f" managed_root={managed_root}{suffix}"
            lines.append(
                f"  {current['language']}: {availability}/{status} source={source} command={command_str}{suffix}"
            )
    else:
        lines.append("lsp_providers: disabled")
    return "\n".join(lines)


def _can_delegate_to_native_tg_search(
    config: "SearchConfig",
    *,
    ndjson: bool,
    files_mode: bool,
    files_with_matches: bool,
    files_without_match: bool,
    format_type: str,
) -> bool:
    from tensor_grep.core.config import SearchConfig

    if files_mode or files_with_matches or files_without_match or format_type != "rg":
        return False

    defaults = SearchConfig()
    for field_name in _NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS:
        if getattr(config, field_name) != getattr(defaults, field_name):
            return False

    return config.force_cpu or config.json_mode or ndjson or bool(config.gpu_device_ids)


def _build_native_tg_search_command(
    native_binary: Path,
    *,
    pattern: str,
    paths: list[str],
    config: "SearchConfig",
    ndjson: bool,
) -> list[str]:
    command = [str(native_binary), "search"]

    if config.force_cpu:
        command.append("--cpu")
    elif config.gpu_device_ids:
        command.extend([
            "--gpu-device-ids",
            ",".join(str(device_id) for device_id in config.gpu_device_ids),
        ])

    if config.ignore_case:
        command.append("-i")
    if config.fixed_strings:
        command.append("-F")
    if config.invert_match:
        command.append("-v")
    if config.count:
        command.append("-c")
    if config.context is not None:
        command.extend(["-C", str(config.context)])
    if config.max_count is not None:
        command.extend(["-m", str(config.max_count)])
    if config.word_regexp:
        command.append("-w")
    for current_glob in config.glob or []:
        command.extend(["-g", current_glob])
    if config.no_ignore:
        command.append("--no-ignore")
    if config.json_mode:
        command.append("--json")
    if ndjson:
        command.append("--ndjson")

    command.extend([pattern, *paths])
    return command


def _delegate_to_native_tg_search(
    native_binary: Path,
    *,
    pattern: str,
    paths: list[str],
    config: "SearchConfig",
    ndjson: bool,
) -> int:
    command = _build_native_tg_search_command(
        native_binary,
        pattern=pattern,
        paths=paths,
        config=config,
        ndjson=ndjson,
    )
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


def _collect_candidate_files(
    scanner: "DirectoryScanner", paths: list[str]
) -> tuple[list[str], set[str]]:
    ordered = []
    seen = set()
    for p in paths:
        for current_file in scanner.walk(p):
            if current_file not in seen:
                seen.add(current_file)
                ordered.append(current_file)
    return ordered, seen


def _write_path_list(paths: list[str], *, use_nul: bool) -> None:
    if not paths:
        return
    if use_nul:
        payload = b"\x00".join(os.fsencode(path) for path in paths) + b"\x00"
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return
    sys.stdout.write("\n".join(paths))
    sys.stdout.write(os.linesep)


def _sum_total_bytes(paths: list[str]) -> int:
    total = 0
    for p in paths:
        try:
            total += Path(p).stat().st_size
        except OSError:
            continue
    return total


def _can_passthrough_rg(
    config: "SearchConfig",
    *,
    format_type: str,
    json_mode: bool,
    ndjson_mode: bool,
    files_mode: bool,
    files_with_matches: bool,
    files_without_match: bool,
    only_matching: bool,
    stats_mode: bool,
) -> bool:
    # Keep passthrough only for modes where rg semantics are fully compatible
    # with tensor-grep output and feature behavior.
    return bool(
        not config.ast
        and not config.ltl
        and not config.pcre2
        and not config.force_cpu
        and config.replace_str is None
        and format_type == "rg"
        and not json_mode
        and not ndjson_mode
        and not files_mode
        and not files_with_matches
        and not files_without_match
        and not only_matching
    )


def _selected_route_supports_rg_passthrough(
    *,
    selected_backend_name: str,
    selected_backend_reason: str,
    selected_gpu_device_ids: list[int],
    selected_gpu_chunk_plan_mb: list[tuple[int, int]],
) -> bool:
    if selected_backend_name != "RipgrepBackend":
        return False
    if selected_gpu_device_ids or selected_gpu_chunk_plan_mb:
        return False
    return not selected_backend_reason.startswith("gpu_")


def _generate_shell_completion_script(*, generator: str, prog_name: str = "tg") -> str:
    shell_by_generator = {
        "complete-bash": "bash",
        "complete-zsh": "zsh",
        "complete-fish": "fish",
        "complete-powershell": "powershell",
    }
    shell = shell_by_generator.get(generator)
    if shell is None:
        supported_values = ", ".join(shell_by_generator)
        raise typer.BadParameter(
            f"Unsupported --generate value '{generator}'. Supported values: {supported_values}"
        )

    complete_var = f"_{prog_name.replace('-', '_').upper()}_COMPLETE"
    from typer._completion_shared import get_completion_script

    return str(get_completion_script(prog_name=prog_name, complete_var=complete_var, shell=shell))


def _replace_lines(
    matches: list[MatchLine], pattern: str, config: "SearchConfig"
) -> list[MatchLine]:
    if config.replace_str is None:
        return matches

    flags = 0
    if config.ignore_case or (config.smart_case and pattern.islower()):
        flags |= re.IGNORECASE

    if config.fixed_strings:
        regex = re.compile(re.escape(pattern), flags)
    elif config.line_regexp:
        regex = re.compile(f"^{pattern}$", flags)
    elif config.word_regexp:
        regex = re.compile(rf"\b{pattern}\b", flags)
    else:
        regex = re.compile(pattern, flags)

    extracted: list[MatchLine] = []
    for match in matches:
        replacement = config.replace_str
        if config.fixed_strings and "$" not in replacement:
            flags_val = flags
            if flags_val & re.IGNORECASE:
                new_text = re.sub(
                    re.escape(pattern),
                    replacement.replace("\\", r"\\"),
                    match.text,
                    flags=re.IGNORECASE,
                )
            else:
                new_text = match.text.replace(pattern, replacement)
            extracted.append(replace(match, text=new_text))
            continue
        if regex is not None:

            def _expand_match(current: re.Match[str], replacement: str = replacement) -> str:
                return _expand_ripgrep_replacement(replacement, current)

            new_text = regex.sub(
                _expand_match,
                match.text,
            )
        else:
            new_text = match.text
        extracted.append(replace(match, text=new_text))
    return extracted


def _expand_ripgrep_replacement(template: str, match: re.Match[str]) -> str:
    def _is_ascii_digit(char: str) -> bool:
        return "0" <= char <= "9"

    def _is_ascii_ref_char(char: str) -> bool:
        return char == "_" or ("0" <= char <= "9") or ("A" <= char <= "Z") or ("a" <= char <= "z")

    def _resolve_token(token: str) -> str:
        if not token:
            return ""
        try:
            if all(_is_ascii_digit(char) for char in token):
                group_value = match.group(int(token))
            else:
                group_value = match.group(token)
        except Exception:
            return ""
        return "" if group_value is None else str(group_value)

    result: list[str] = []
    index = 0
    while index < len(template):
        char = template[index]
        if char != "$" or index + 1 >= len(template):
            result.append(char)
            index += 1
            continue

        next_char = template[index + 1]
        if next_char == "$":
            result.append("$")
            index += 2
            continue

        if next_char == "{":
            end_index = template.find("}", index + 2)
            if end_index != -1:
                result.append(_resolve_token(template[index + 2 : end_index]))
                index = end_index + 1
                continue

        if _is_ascii_ref_char(next_char):
            end_index = index + 2
            while end_index < len(template) and _is_ascii_ref_char(template[end_index]):
                end_index += 1
            result.append(_resolve_token(template[index + 1 : end_index]))
            index = end_index
            continue

        result.append("$")
        index += 1

    return "".join(result)


def _only_matching_lines(
    matches: list[MatchLine], pattern: str, config: "SearchConfig"
) -> list[MatchLine]:
    flags = 0
    if config.ignore_case or (config.smart_case and pattern.islower()):
        flags |= re.IGNORECASE

    if config.fixed_strings:
        regex = re.compile(re.escape(pattern), flags)
    elif config.line_regexp:
        regex = re.compile(f"^{pattern}$", flags)
    elif config.word_regexp:
        regex = re.compile(rf"\b{pattern}\b", flags)
    else:
        regex = re.compile(pattern, flags)

    extracted: list[MatchLine] = []
    for match in matches:
        for token in regex.findall(match.text):
            if isinstance(token, tuple):
                token = "".join(token)
            token_text = str(token)
            if token_text:
                extracted.append(replace(match, text=token_text))
    return extracted


def _normalize_string_list(value: object, fallback: list[str]) -> list[str]:
    if value is None:
        return fallback
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return fallback


def _parse_gpu_device_ids_cli(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    parsed: list[int] = []
    seen: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise typer.BadParameter(
                f"Invalid GPU device id '{token}'. Use comma-separated integers, e.g. 0,1."
            ) from exc
        if value < 0:
            raise typer.BadParameter(
                f"Invalid GPU device id '{token}'. Device IDs must be non-negative."
            )
        if value in seen:
            continue
        seen.add(value)
        parsed.append(value)
    if not parsed:
        raise typer.BadParameter(
            "No valid GPU device IDs provided. Use comma-separated integers, e.g. 0,1."
        )
    return parsed


def _selected_gpu_execution_defaults(
    gpu_device_ids: list[int], gpu_chunk_plan_mb: list[tuple[int, int]]
) -> tuple[bool, int]:
    if gpu_device_ids:
        worker_count = len(dict.fromkeys(gpu_device_ids))
    else:
        worker_count = len(dict.fromkeys(device_id for device_id, _ in gpu_chunk_plan_mb))
    if worker_count <= 0:
        return False, 0
    return worker_count > 1, worker_count


def _load_yaml_dict(path: Path) -> dict[str, object]:
    import yaml

    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML in {path} must be a mapping.")
    return loaded


def _load_sg_project_config(config_path: str | None) -> dict[str, object]:
    resolved = Path(config_path or "sgconfig.yml").resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Config file {resolved} not found. Use `tg new` to create one.")

    raw = _load_yaml_dict(resolved)
    return {
        "config_path": resolved,
        "root_dir": resolved.parent,
        "rule_dirs": _normalize_string_list(raw.get("ruleDirs"), ["rules"]),
        "test_dirs": _normalize_string_list(raw.get("testDirs"), ["tests"]),
        "language": str(raw.get("language") or "python"),
    }


def _iter_yaml_files(base_dir: Path, rel_dirs: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for rel_dir in rel_dirs:
        target = (base_dir / rel_dir).resolve()
        if target.is_file() and target.suffix.lower() in {".yml", ".yaml"}:
            candidates.append(target)
            continue
        if not target.is_dir():
            continue
        candidates.extend(sorted(target.rglob("*.yml")))
        candidates.extend(sorted(target.rglob("*.yaml")))
    return sorted(set(candidates))


def _extract_rule_pattern(rule_data: dict[str, object]) -> str | None:
    direct = rule_data.get("pattern")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    rule_node = rule_data.get("rule")
    if isinstance(rule_node, dict):
        nested = rule_node.get("pattern")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()

    return None


def _load_rule_specs(project_cfg: dict[str, object]) -> list[dict[str, str]]:
    root_dir = cast(Path, project_cfg["root_dir"])
    rule_dirs = cast(list[str], project_cfg["rule_dirs"])
    default_language = cast(str, project_cfg["language"])

    specs: list[dict[str, str]] = []
    for rule_file in _iter_yaml_files(root_dir, rule_dirs):
        payload = _load_yaml_dict(rule_file)

        raw_rules = payload.get("rules")
        if isinstance(raw_rules, list):
            for idx, item in enumerate(raw_rules):
                if not isinstance(item, dict):
                    continue
                pattern = _extract_rule_pattern(item)
                if not pattern:
                    continue
                specs.append({
                    "id": str(item.get("id") or f"{rule_file.stem}-{idx + 1}"),
                    "pattern": pattern,
                    "language": str(
                        item.get("language") or payload.get("language") or default_language
                    ),
                })
            continue

        pattern = _extract_rule_pattern(payload)
        if not pattern:
            continue
        specs.append({
            "id": str(payload.get("id") or rule_file.stem),
            "pattern": pattern,
            "language": str(payload.get("language") or default_language),
        })

    return specs


def _load_inline_rule_specs(
    inline_rules_text: str, *, default_language: str | None = None
) -> list[dict[str, str]]:
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    specs: list[dict[str, str]] = []

    for document_index, payload in enumerate(
        yaml.load_all(inline_rules_text, Loader=loader),
        start=1,
    ):
        if payload is None:
            continue
        if not isinstance(payload, dict):
            raise ValueError("Inline rules YAML must contain mapping documents.")

        raw_rules = payload.get("rules")
        if isinstance(raw_rules, list):
            for rule_index, item in enumerate(raw_rules, start=1):
                if not isinstance(item, dict):
                    continue
                pattern = _extract_rule_pattern(item)
                if not pattern:
                    continue
                specs.append({
                    "id": str(item.get("id") or f"inline-rule-{document_index}-{rule_index}"),
                    "pattern": pattern,
                    "language": str(
                        item.get("language")
                        or payload.get("language")
                        or default_language
                        or "python"
                    ),
                })
            continue

        pattern = _extract_rule_pattern(payload)
        if not pattern:
            continue
        specs.append({
            "id": str(payload.get("id") or f"inline-rule-{document_index}"),
            "pattern": pattern,
            "language": str(payload.get("language") or default_language or "python"),
        })

    return specs


def _suffix_for_language(language: str) -> str:
    normalized = language.lower()
    if normalized in {"js", "javascript"}:
        return ".js"
    if normalized in {"ts", "typescript"}:
        return ".ts"
    return ".py"


def _build_rulesets_payload() -> dict[str, object]:
    from tensor_grep.cli.rule_packs import list_rule_packs

    return {
        "version": _json_output_version(),
        "routing_backend": "AstBackend",
        "routing_reason": "builtin-rulesets",
        "sidecar_used": False,
        "rulesets": list_rule_packs(),
    }


def _ruleset_finding_fingerprint(
    *,
    rule_id: str,
    language: str,
    matched_files: list[str],
) -> str:
    import hashlib

    fingerprint_input = json.dumps(
        {
            "rule_id": rule_id,
            "language": language,
            "files": matched_files,
        },
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(fingerprint_input).hexdigest()


def _truncate_evidence_snippet(text: str, max_chars: int) -> dict[str, object]:
    normalized = " ".join(text.split())
    if max_chars <= 0:
        return {"text": "", "truncated": bool(normalized)}
    if len(normalized) <= max_chars:
        return {"text": normalized, "truncated": False}
    return {"text": normalized[:max_chars], "truncated": True}


def _load_ruleset_baseline(path: str) -> dict[str, object]:
    baseline_path = Path(path).expanduser().resolve()
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Ruleset baseline must be a JSON object.")
    fingerprints = payload.get("fingerprints")
    if not isinstance(fingerprints, list) or not all(
        isinstance(item, str) and item.strip() for item in fingerprints
    ):
        raise ValueError("Ruleset baseline must include a non-empty 'fingerprints' string list.")
    return {
        "path": str(baseline_path),
        "fingerprints": sorted(dict.fromkeys(fingerprints)),
    }


def _load_ruleset_suppressions(path: str) -> dict[str, object]:
    suppressions_path = Path(path).expanduser().resolve()
    payload = json.loads(suppressions_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Ruleset suppressions must be a JSON object.")
    entries_payload = payload.get("entries")
    if entries_payload is not None:
        if not isinstance(entries_payload, list):
            raise ValueError("Ruleset suppressions 'entries' must be a list.")
        entries: list[dict[str, object]] = []
        for raw_entry in entries_payload:
            if not isinstance(raw_entry, dict):
                raise ValueError("Ruleset suppressions entries must be JSON objects.")
            fingerprint = raw_entry.get("fingerprint")
            if not isinstance(fingerprint, str) or not fingerprint.strip():
                raise ValueError(
                    "Ruleset suppressions entries must include a non-empty 'fingerprint' string."
                )
            justification = raw_entry.get("justification")
            if not isinstance(justification, str) or not justification.strip():
                raise ValueError(
                    "Ruleset suppressions entries must include a non-empty 'justification' string."
                )
            created_at = raw_entry.get("created_at")
            if not isinstance(created_at, str) or not created_at.strip():
                raise ValueError(
                    "Ruleset suppressions entries must include a non-empty 'created_at' timestamp."
                )
            try:
                datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(
                    "Ruleset suppressions entries must include ISO-8601 'created_at' timestamps."
                ) from exc
            entry: dict[str, object] = {
                "fingerprint": fingerprint.strip(),
                "justification": justification.strip(),
                "created_at": created_at,
            }
            file_path = raw_entry.get("file")
            if file_path is not None:
                if not isinstance(file_path, str) or not file_path.strip():
                    raise ValueError(
                        "Ruleset suppressions entries must use non-empty strings for optional 'file'."
                    )
                entry["file"] = file_path
            line = raw_entry.get("line")
            if line is not None:
                if isinstance(line, bool) or not isinstance(line, int) or line <= 0:
                    raise ValueError(
                        "Ruleset suppressions entries must use positive integers for optional 'line'."
                    )
                entry["line"] = line
            rule_id = raw_entry.get("rule_id")
            if rule_id is not None:
                if not isinstance(rule_id, str) or not rule_id.strip():
                    raise ValueError(
                        "Ruleset suppressions entries must use non-empty strings for optional 'rule_id'."
                    )
                entry["rule_id"] = rule_id
            entries.append(entry)
        return {
            "path": str(suppressions_path),
            "entries": entries,
            "warnings": [],
        }
    fingerprints = payload.get("fingerprints")
    if not isinstance(fingerprints, list) or not all(
        isinstance(item, str) and item.strip() for item in fingerprints
    ):
        raise ValueError(
            "Ruleset suppressions must include a non-empty 'fingerprints' string list."
        )
    return {
        "path": str(suppressions_path),
        "entries": [{"fingerprint": item} for item in sorted(dict.fromkeys(fingerprints))],
        "warnings": [
            "Legacy suppression format using 'fingerprints' is deprecated; use 'entries' instead."
        ],
    }


def _ruleset_suppression_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_ruleset_source_path(file_path: str, root_dir: Path) -> Path:
    candidate = Path(file_path)
    if candidate.is_absolute():
        return candidate
    return (root_dir / candidate).resolve()


def _ruleset_files_match(entry_file: str, occurrence_file: str, root_dir: Path) -> bool:
    if entry_file == occurrence_file:
        return True
    return _resolve_ruleset_source_path(entry_file, root_dir) == _resolve_ruleset_source_path(
        occurrence_file, root_dir
    )


def _inline_suppression_targets(line_text: str, language: str) -> set[str]:
    comment_prefix = (
        "#"
        if language == "python"
        else "//"
        if language
        in {
            "javascript",
            "typescript",
            "rust",
        }
        else None
    )
    if comment_prefix is None:
        return set()
    match = re.search(
        rf"{re.escape(comment_prefix)}\s*tg-ignore\s*:\s*([^\r\n]+)",
        line_text,
    )
    if not match:
        return set()
    return {token.strip() for token in match.group(1).split(",") if token.strip()}


def _occurrence_has_inline_suppression(
    *,
    occurrence_file: str,
    occurrence_line: int,
    rule_id: str,
    language: str,
    root_dir: Path,
    source_cache: dict[str, list[str]],
) -> bool:
    try:
        source_path = _resolve_ruleset_source_path(occurrence_file, root_dir)
        cache_key = str(source_path)
        if cache_key not in source_cache:
            source_cache[cache_key] = source_path.read_text(encoding="utf-8").splitlines()
        source_lines = source_cache[cache_key]
    except OSError:
        return False
    targets: set[str] = set()
    for candidate_line in (occurrence_line - 1, occurrence_line):
        if 1 <= candidate_line <= len(source_lines):
            targets.update(_inline_suppression_targets(source_lines[candidate_line - 1], language))
    return "*" in targets or rule_id in targets


def _suppression_entry_matches(
    *,
    entry: dict[str, object],
    fingerprint: str,
    rule_id: str,
    occurrence_file: str | None,
    occurrence_line: int | None,
    root_dir: Path,
) -> bool:
    if cast(str, entry["fingerprint"]) != fingerprint:
        return False
    entry_rule_id = entry.get("rule_id")
    if entry_rule_id is not None and cast(str, entry_rule_id) != rule_id:
        return False
    entry_file = entry.get("file")
    if entry_file is not None:
        if occurrence_file is None or not _ruleset_files_match(
            cast(str, entry_file), occurrence_file, root_dir
        ):
            return False
    entry_line = entry.get("line")
    if entry_line is not None and occurrence_line != cast(int, entry_line):
        return False
    return True


def _apply_ruleset_baseline(
    payload: dict[str, object],
    *,
    baseline_path: str | None = None,
    write_baseline_path: str | None = None,
    suppressions_path: str | None = None,
    write_suppressions_path: str | None = None,
    suppression_justification: str | None = None,
) -> None:
    findings = cast(list[dict[str, object]], payload["findings"])
    matched_fingerprints = sorted({
        cast(str, finding["fingerprint"])
        for finding in findings
        if cast(int, finding["matches"]) > 0
    })
    if baseline_path is not None:
        baseline = _load_ruleset_baseline(baseline_path)
        baseline_fingerprints = set(cast(list[str], baseline["fingerprints"]))
        current_fingerprints = set(matched_fingerprints)
        for finding in findings:
            if cast(int, finding["matches"]) <= 0:
                finding["status"] = "clear"
                continue
            finding["status"] = (
                "existing" if cast(str, finding["fingerprint"]) in baseline_fingerprints else "new"
            )
        payload["baseline"] = {
            "path": baseline["path"],
            "new_findings": sum(1 for finding in findings if finding.get("status") == "new"),
            "existing_findings": sum(
                1 for finding in findings if finding.get("status") == "existing"
            ),
            "resolved_findings": len(baseline_fingerprints - current_fingerprints),
            "resolved_fingerprints": sorted(baseline_fingerprints - current_fingerprints),
        }
    else:
        for finding in findings:
            if cast(int, finding["matches"]) <= 0:
                finding["status"] = "clear"
            else:
                finding["status"] = "new"
    if write_baseline_path is not None:
        write_path = Path(write_baseline_path).expanduser().resolve()
        baseline_payload = {
            "version": _json_output_version(),
            "kind": "ruleset-scan-baseline",
            "ruleset": payload.get("ruleset"),
            "language": payload.get("language"),
            "fingerprints": matched_fingerprints,
        }
        write_path.write_text(json.dumps(baseline_payload, indent=2), encoding="utf-8")
        payload["baseline_written"] = {
            "path": str(write_path),
            "fingerprints": matched_fingerprints,
            "count": len(matched_fingerprints),
        }
    suppressions_summary: dict[str, object] | None = None
    suppression_entries: list[dict[str, object]] = []
    suppression_warnings: list[str] = []
    if suppressions_path is not None:
        suppressions = _load_ruleset_suppressions(suppressions_path)
        suppressions_summary = {"path": suppressions["path"]}
        suppression_entries = cast(list[dict[str, object]], suppressions["entries"])
        suppression_warnings = cast(list[str], suppressions["warnings"])
        if suppression_warnings:
            suppressions_summary["warnings"] = suppression_warnings
    root_dir = Path(str(payload["path"]))
    source_cache: dict[str, list[str]] = {}
    suppressed_occurrences = 0
    inline_suppressed_occurrences = 0
    for finding in findings:
        raw_occurrences = cast(
            list[dict[str, object]],
            finding.pop("_raw_occurrences", []),
        )
        if cast(int, finding["matches"]) <= 0:
            continue
        base_status = cast(str, finding["status"])
        occurrence_rows: list[dict[str, object]] = []
        finding_suppressed_occurrences = 0
        finding_inline_occurrences = 0
        active_occurrences = 0
        for occurrence in raw_occurrences:
            occurrence_file = cast(str, occurrence["file"])
            occurrence_line = cast(int, occurrence["line"])
            occurrence_status = base_status
            if any(
                _suppression_entry_matches(
                    entry=entry,
                    fingerprint=cast(str, finding["fingerprint"]),
                    rule_id=cast(str, finding["rule_id"]),
                    occurrence_file=occurrence_file,
                    occurrence_line=occurrence_line,
                    root_dir=root_dir,
                )
                for entry in suppression_entries
            ):
                occurrence_status = "suppressed"
                finding_suppressed_occurrences += 1
            elif _occurrence_has_inline_suppression(
                occurrence_file=occurrence_file,
                occurrence_line=occurrence_line,
                rule_id=cast(str, finding["rule_id"]),
                language=cast(str, finding["language"]),
                root_dir=root_dir,
                source_cache=source_cache,
            ):
                occurrence_status = "inline-suppressed"
                finding_inline_occurrences += 1
            else:
                active_occurrences += 1
            occurrence_rows.append({
                "file": occurrence_file,
                "line": occurrence_line,
                "status": occurrence_status,
            })
        if not raw_occurrences and any(
            _suppression_entry_matches(
                entry=entry,
                fingerprint=cast(str, finding["fingerprint"]),
                rule_id=cast(str, finding["rule_id"]),
                occurrence_file=None,
                occurrence_line=None,
                root_dir=root_dir,
            )
            for entry in suppression_entries
        ):
            finding["status"] = "suppressed"
            finding_suppressed_occurrences += 1
        elif occurrence_rows:
            if active_occurrences == 0:
                finding["status"] = (
                    "inline-suppressed"
                    if finding_inline_occurrences > 0
                    else "suppressed"
                    if finding_suppressed_occurrences > 0
                    else base_status
                )
            else:
                finding["status"] = base_status
        if occurrence_rows and (
            suppressions_path is not None
            or finding_suppressed_occurrences > 0
            or finding_inline_occurrences > 0
        ):
            finding["occurrences"] = sorted(
                occurrence_rows,
                key=lambda row: (str(row["file"]), cast(int, row["line"])),
            )
        suppressed_occurrences += finding_suppressed_occurrences
        inline_suppressed_occurrences += finding_inline_occurrences
    if suppressions_summary is not None or inline_suppressed_occurrences > 0:
        if suppressions_summary is None:
            suppressions_summary = {}
        suppressions_summary["suppressed_findings"] = sum(
            1 for finding in findings if finding.get("status") == "suppressed"
        )
        if suppressed_occurrences > 0:
            suppressions_summary["suppressed_occurrences"] = suppressed_occurrences
        if inline_suppressed_occurrences > 0:
            suppressions_summary["inline_suppressed_findings"] = sum(
                1 for finding in findings if finding.get("status") == "inline-suppressed"
            )
            suppressions_summary["inline_suppressed_occurrences"] = inline_suppressed_occurrences
        payload["suppressions"] = suppressions_summary
    if write_suppressions_path is not None:
        if not isinstance(suppression_justification, str) or not suppression_justification.strip():
            raise ValueError("--write-suppressions requires a non-empty --justification value.")
        write_path = Path(write_suppressions_path).expanduser().resolve()
        suppressions_payload = {
            "version": _json_output_version(),
            "kind": "ruleset-scan-suppressions",
            "ruleset": payload.get("ruleset"),
            "language": payload.get("language"),
            "entries": [
                {
                    "fingerprint": fingerprint,
                    "justification": suppression_justification.strip(),
                    "created_at": _ruleset_suppression_timestamp(),
                }
                for fingerprint in matched_fingerprints
            ],
        }
        write_path.write_text(json.dumps(suppressions_payload, indent=2), encoding="utf-8")
        payload["suppressions_written"] = {
            "path": str(write_path),
            "fingerprints": matched_fingerprints,
            "count": len(matched_fingerprints),
        }


def _run_ast_scan_payload(
    project_cfg: dict[str, object],
    rules: list[dict[str, str]],
    *,
    routing_reason: str,
    candidate_files: list[str] | None = None,
    project_scan_fast_path: bool = False,
    ruleset_name: str | None = None,
    baseline_path: str | None = None,
    write_baseline_path: str | None = None,
    suppressions_path: str | None = None,
    write_suppressions_path: str | None = None,
    suppression_justification: str | None = None,
    include_evidence_snippets: bool = False,
    max_evidence_snippets_per_file: int = 1,
    max_evidence_snippet_chars: int = 120,
) -> dict[str, object]:
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import SearchResult
    from tensor_grep.io.directory_scanner import DirectoryScanner

    cfg = SearchConfig(
        ast=True,
        ast_prefer_native=True,
        lang=cast(str, project_cfg["language"]),
    )
    root_dir = cast(Path, project_cfg["root_dir"])
    scanner: DirectoryScanner | None = None
    resolved_candidate_files = list(candidate_files) if candidate_files is not None else None
    backend_cache: dict[tuple[str | None, str, bool], ComputeBackend] = {}
    backend_names_used: set[str] = set()

    total_matches = 0
    matched_rules = 0
    findings: list[dict[str, object]] = []

    def _append_finding(
        *,
        rule: dict[str, str],
        rule_matches: int,
        matched_files: set[str],
        match_counts_by_file: dict[str, int],
        snippets_by_file: dict[str, list[dict[str, object]]],
        rule_occurrences: list[dict[str, object]],
    ) -> None:
        nonlocal total_matches, matched_rules

        total_matches += rule_matches
        if rule_matches > 0:
            matched_rules += 1
        sorted_files = sorted(matched_files)
        findings.append({
            "rule_id": rule["id"],
            "language": rule["language"],
            "severity": rule.get("severity"),
            "message": rule.get("message"),
            "fingerprint": _ruleset_finding_fingerprint(
                rule_id=rule["id"],
                language=rule["language"],
                matched_files=sorted_files,
            ),
            "matches": rule_matches,
            "files": sorted_files,
            "evidence": [
                {
                    "file": file_path,
                    "match_count": match_counts_by_file.get(file_path, 0),
                    **(
                        {"snippets": snippets_by_file.get(file_path, [])}
                        if include_evidence_snippets
                        else {}
                    ),
                }
                for file_path in sorted_files
            ],
            "_raw_occurrences": sorted({
                (cast(str, occurrence["file"]), cast(int, occurrence["line"]))
                for occurrence in rule_occurrences
            }),
        })
        if findings[-1]["_raw_occurrences"]:
            findings[-1]["_raw_occurrences"] = [
                {"file": file_path, "line": line_number}
                for file_path, line_number in cast(
                    list[tuple[str, int]], findings[-1]["_raw_occurrences"]
                )
            ]

    wrapper_rules: list[tuple[dict[str, str], SearchConfig]] = []
    other_resolved: list[tuple[dict[str, str], SearchConfig, ComputeBackend]] = []
    wrapper_backend: object | None = None
    for rule in rules:
        rule_cfg = replace(cfg, lang=rule["language"])
        backend = _select_ast_backend_for_pattern(rule_cfg, rule["pattern"], backend_cache)
        if (
            project_scan_fast_path
            and type(backend).__name__ == "AstGrepWrapperBackend"
            and hasattr(backend, "search_project")
        ):
            wrapper_rules.append((rule, rule_cfg))
            if wrapper_backend is None:
                wrapper_backend = backend
            continue
        other_resolved.append((rule, rule_cfg, backend))

    wrapper_project_results: dict[str, SearchResult] | None = None
    if wrapper_rules and wrapper_backend is not None:
        backend_names_used.add(type(wrapper_backend).__name__)
        try:
            wrapper_project_results = cast(Any, wrapper_backend).search_project(
                str(root_dir), str(project_cfg["config_path"])
            )
        except Exception:
            for rule, rule_cfg in wrapper_rules:
                other_resolved.append((rule, rule_cfg, cast(ComputeBackend, wrapper_backend)))
            wrapper_rules = []

    for rule, _rule_cfg in wrapper_rules:
        result = (
            wrapper_project_results.get(
                rule["id"],
                SearchResult(matches=[], total_files=0, total_matches=0),
            )
            if wrapper_project_results is not None
            else SearchResult(matches=[], total_files=0, total_matches=0)
        )
        matched_files = set(result.matched_file_paths)
        match_counts_by_file = dict(result.match_counts_by_file)
        snippets_by_file: dict[str, list[dict[str, object]]] = {}
        rule_occurrences: list[dict[str, object]] = []
        for match in result.matches:
            if match.file:
                match_counts_by_file[match.file] = match_counts_by_file.get(match.file, 0) + 1
                rule_occurrences.append({"file": match.file, "line": match.line_number})
                if (
                    include_evidence_snippets
                    and len(snippets_by_file.get(match.file, [])) < max_evidence_snippets_per_file
                ):
                    snippets_by_file.setdefault(match.file, []).append(
                        _truncate_evidence_snippet(match.text, max_evidence_snippet_chars)
                    )
        if not matched_files and result.total_files > 0:
            matched_files.update(match.file for match in result.matches if match.file)
        _append_finding(
            rule=rule,
            rule_matches=result.total_matches,
            matched_files=matched_files,
            match_counts_by_file=match_counts_by_file,
            snippets_by_file=snippets_by_file,
            rule_occurrences=rule_occurrences,
        )

    for rule, rule_cfg, backend in other_resolved:
        backend_names_used.add(type(backend).__name__)
        resolved_matched_files: set[str] = set()
        resolved_match_counts_by_file: dict[str, int] = {}
        resolved_snippets_by_file: dict[str, list[dict[str, object]]] = {}
        resolved_rule_occurrences: list[dict[str, object]] = []
        if type(backend).__name__ == "AstGrepWrapperBackend" and hasattr(backend, "search_many"):
            result = backend.search_many([str(root_dir)], rule["pattern"], config=rule_cfg)
            rule_matches = result.total_matches
            resolved_matched_files.update(result.matched_file_paths)
            for file_path, count in result.match_counts_by_file.items():
                resolved_match_counts_by_file[file_path] = (
                    resolved_match_counts_by_file.get(file_path, 0) + count
                )
            for match in result.matches:
                if match.file:
                    resolved_match_counts_by_file[match.file] = (
                        resolved_match_counts_by_file.get(match.file, 0) + 1
                    )
                    resolved_rule_occurrences.append({
                        "file": match.file,
                        "line": match.line_number,
                    })
                    if (
                        include_evidence_snippets
                        and len(resolved_snippets_by_file.get(match.file, []))
                        < max_evidence_snippets_per_file
                    ):
                        resolved_snippets_by_file.setdefault(match.file, []).append(
                            _truncate_evidence_snippet(match.text, max_evidence_snippet_chars)
                        )
            if not resolved_matched_files and result.total_files > 0:
                resolved_matched_files.update(match.file for match in result.matches if match.file)
        else:
            if scanner is None:
                scanner = DirectoryScanner(cfg)
            if resolved_candidate_files is None:
                resolved_candidate_files, _ = _collect_candidate_files(scanner, [str(root_dir)])
            rule_matches = 0
            for current_file in resolved_candidate_files:
                result = backend.search(current_file, rule["pattern"], config=rule_cfg)
                rule_matches += result.total_matches
                if result.total_files > 0 or result.total_matches > 0:
                    resolved_matched_files.add(current_file)
                    resolved_match_counts_by_file[current_file] = (
                        resolved_match_counts_by_file.get(current_file, 0) + result.total_matches
                    )
                    for match in result.matches:
                        resolved_rule_occurrences.append({
                            "file": match.file or current_file,
                            "line": match.line_number,
                        })
                    if include_evidence_snippets:
                        file_snippets = resolved_snippets_by_file.setdefault(current_file, [])
                        for match in result.matches:
                            if len(file_snippets) >= max_evidence_snippets_per_file:
                                break
                            file_snippets.append(
                                _truncate_evidence_snippet(match.text, max_evidence_snippet_chars)
                            )
        _append_finding(
            rule=rule,
            rule_matches=rule_matches,
            matched_files=resolved_matched_files,
            match_counts_by_file=resolved_match_counts_by_file,
            snippets_by_file=resolved_snippets_by_file,
            rule_occurrences=resolved_rule_occurrences,
        )

    payload = {
        "version": _json_output_version(),
        "routing_backend": "AstBackend",
        "routing_reason": routing_reason,
        "sidecar_used": False,
        "config_path": str(project_cfg["config_path"]),
        "path": str(root_dir),
        "ruleset": ruleset_name,
        "language": str(project_cfg["language"]),
        "rule_count": len(rules),
        "matched_rules": matched_rules,
        "total_matches": total_matches,
        "backends": sorted(backend_names_used),
        "findings": findings,
    }
    _apply_ruleset_baseline(
        payload,
        baseline_path=baseline_path,
        write_baseline_path=write_baseline_path,
        suppressions_path=suppressions_path,
        write_suppressions_path=write_suppressions_path,
        suppression_justification=suppression_justification,
    )
    return payload


def _search_ast_test_snippets_with_wrapper(
    backend: object,
    *,
    root_dir: Path,
    case_cfg: "SearchConfig",
    pattern: str,
    language: str,
    snippets: list[str],
) -> list[bool]:
    if not snippets:
        return []

    suffix = _suffix_for_language(language)
    with TemporaryDirectory(prefix=".tg_rule_test_batch_", dir=root_dir) as temp_dir:
        temp_root = Path(temp_dir)
        snippet_paths: list[Path] = []
        for index, snippet in enumerate(snippets):
            snippet_path = temp_root / f"case_{index}{suffix}"
            snippet_path.write_text(snippet, encoding="utf-8")
            snippet_paths.append(snippet_path)

        result = cast(Any, backend).search_many(
            [str(temp_root)],
            pattern,
            config=case_cfg,
        )

        def _resolve_match_path(raw_path: str) -> Path:
            candidate = Path(raw_path)
            if candidate.is_absolute():
                return candidate.resolve()
            return (temp_root / candidate).resolve()

        matched_paths = {_resolve_match_path(path) for path in result.matched_file_paths}
        matched_paths.update(
            _resolve_match_path(match.file) for match in result.matches if match.file
        )
        return [snippet_path.resolve() in matched_paths for snippet_path in snippet_paths]


def _evaluate_ast_test_case_with_wrapper(
    backend: object,
    *,
    root_dir: Path,
    case_cfg: "SearchConfig",
    pattern: str,
    language: str,
    valid_snippets: list[str],
    invalid_snippets: list[str],
) -> list[tuple[str, bool, bool]]:
    snippets = [*valid_snippets, *invalid_snippets]
    if not snippets:
        return []

    match_results = _search_ast_test_snippets_with_wrapper(
        backend,
        root_dir=root_dir,
        case_cfg=case_cfg,
        pattern=pattern,
        language=language,
        snippets=snippets,
    )
    expected_matches = [False] * len(valid_snippets) + [True] * len(invalid_snippets)
    return list(zip(snippets, expected_matches, match_results, strict=True))


def _evaluate_grouped_ast_test_cases_with_wrapper(
    *,
    failures: list[str],
    grouped_cases: dict[
        tuple[int, str, str],
        dict[str, object],
    ],
) -> None:
    for batch in grouped_cases.values():
        backend = batch["backend"]
        root_dir = cast(Path, batch["root_dir"])
        case_cfg = cast("SearchConfig", batch["case_cfg"])
        pattern = cast(str, batch["pattern"])
        language = cast(str, batch["language"])
        items = cast(list[tuple[str, str, bool]], batch["items"])
        snippets = [snippet for _, snippet, _ in items]
        try:
            match_results = _search_ast_test_snippets_with_wrapper(
                backend,
                root_dir=root_dir,
                case_cfg=case_cfg,
                pattern=pattern,
                language=language,
                snippets=snippets,
            )
        except Exception as exc:
            for case_key, _, _ in items:
                failures.append(f"{case_key}: backend error: {exc}")
            continue

        for (case_key, snippet, expected_match), has_match in zip(
            items, match_results, strict=True
        ):
            if has_match != expected_match:
                expectation = "match" if expected_match else "no match"
                failures.append(
                    f"{case_key}: expected {expectation}, got "
                    f"{'match' if has_match else 'no match'} for snippet {snippet!r}"
                )


def _describe_ast_backend_mode(backend_name: str) -> str:
    if backend_name == "AstBackend":
        return "native AST matching"
    if backend_name == "AstGrepWrapperBackend":
        return "ast-grep structural matching"
    return backend_name


def _describe_ast_backend_modes(backend_names: set[str]) -> str:
    if not backend_names:
        return "adaptive AST routing"
    if len(backend_names) == 1:
        return _describe_ast_backend_mode(next(iter(backend_names)))
    return "adaptive AST routing"


def _select_ast_backend_for_pattern(
    base_config: "SearchConfig",
    pattern: str,
    backend_cache: dict[tuple[str | None, str, bool], "ComputeBackend"] | None = None,
) -> "ComputeBackend":
    from tensor_grep.core.pipeline import ConfigurationError, Pipeline

    stripped_pattern = pattern.strip()
    supports_native_pattern = bool(
        stripped_pattern
        and (
            stripped_pattern.startswith("(")
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped_pattern)
        )
    )
    pattern_kind = (
        "native" if base_config.ast_prefer_native and supports_native_pattern else "wrapper"
    )
    cache_key = (base_config.lang, pattern_kind, base_config.ast_prefer_native)
    if backend_cache is not None and cache_key in backend_cache:
        return backend_cache[cache_key]

    backend: ComputeBackend
    if Pipeline.__module__ == "tensor_grep.core.pipeline":
        try:
            from tensor_grep.backends.ast_backend import AstBackend
            from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend

            ast_backend = AstBackend()
            ast_wrapper = AstGrepWrapperBackend()
            if pattern_kind == "native":
                if ast_backend.is_available():
                    backend = ast_backend
                elif ast_wrapper.is_available():
                    backend = ast_wrapper
                else:
                    backend = Pipeline(
                        config=replace(base_config, query_pattern=pattern)
                    ).get_backend()
            else:
                if ast_wrapper.is_available():
                    backend = ast_wrapper
                else:
                    raise ConfigurationError(
                        "Explicit AST search requires AST dependencies: ast-grep wrapper backend "
                        "is required for this pattern but is not available"
                    )
        except ImportError:
            backend = Pipeline(config=replace(base_config, query_pattern=pattern)).get_backend()
    else:
        backend = Pipeline(config=replace(base_config, query_pattern=pattern)).get_backend()

    if backend_cache is not None:
        backend_cache[cache_key] = backend
    return backend


@app.command(
    name="search",
    help="""Search files for a regex pattern, with GPU acceleration when applicable.
The stable text-search contract is the validated rg-compatible surface documented in docs/CONTRACTS.md.

**Other Available Subcommands:**
- `tg calibrate`: Measure CPU vs GPU crossover thresholds
- `tg devices`: Print routable GPU device IDs and VRAM inventory
- `tg mcp`: Start the AI-assistant Model Context Protocol (MCP) server
- `tg classify`: Run semantic NLP threat classification on logs via cyBERT
- `tg run`: Run AST structural search and optional rewrites (ast-grep parity)
- `tg scan` / `tg test` / `tg lsp`: Auxiliary AST workflows
- `tg upgrade` / `tg update`: Upgrade tensor-grep in place
""",
)
def search_command(
    # POSITIONAL ARGUMENTS
    positionals: list[str] | None = typer.Argument(
        None,
        help="PATTERN followed by file paths, or just file paths when --files is set.",
    ),
    # INPUT OPTIONS
    regexp: list[str] | None = typer.Option(
        None, "-e", "--regexp", help="A pattern to search for. Can be provided multiple times."
    ),
    file: list[str] | None = typer.Option(
        None,
        "-f",
        "--file",
        help="Search for patterns from the given file, with one pattern per line.",
    ),
    pre: str | None = typer.Option(
        None, "--pre", help="For each input PATH, search standard output of COMMAND PATH."
    ),
    pre_glob: list[str] | None = typer.Option(
        None, "--pre-glob", help="Only run --pre command on files matching this glob."
    ),
    search_zip: bool = typer.Option(
        False, "-z", "--search-zip", help="Search in compressed files (gzip, bzip2, xz, lz4, etc)."
    ),
    # SEARCH OPTIONS
    case_sensitive: bool = typer.Option(
        False, "-s", "--case-sensitive", help="Execute the search case sensitively."
    ),
    crlf: bool = typer.Option(
        False, "--crlf", help="Treat CRLF as a line terminator instead of just LF."
    ),
    dfa_size_limit: str | None = typer.Option(
        None, "--dfa-size-limit", help="The upper size limit of the regex DFA."
    ),
    encoding: str = typer.Option(
        "auto", "-E", "--encoding", help="Specify the text encoding (e.g., auto, none, utf-8)."
    ),
    engine: str = typer.Option(
        "default", "--engine", help="Regex engine to use: 'default', 'pcre2', or 'auto'."
    ),
    fixed_strings: bool = typer.Option(
        False, "-F", "--fixed-strings", help="Treat all patterns as literals instead of regex."
    ),
    ignore_case: bool = typer.Option(
        False, "-i", "--ignore-case", help="Search case insensitively."
    ),
    invert_match: bool = typer.Option(
        False, "-v", "--invert-match", help="Invert matching (print lines that don't match)."
    ),
    line_regexp: bool = typer.Option(
        False, "-x", "--line-regexp", help="Only show matches surrounded by line boundaries."
    ),
    max_count: int | None = typer.Option(
        None, "-m", "--max-count", help="Limit the number of matching lines per file."
    ),
    mmap: bool = typer.Option(
        True, "--mmap", help="Search using memory maps when possible (enabled by default)."
    ),
    multiline: bool = typer.Option(
        False, "-U", "--multiline", help="Enable searching across multiple lines."
    ),
    multiline_dotall: bool = typer.Option(
        False, "--multiline-dotall", help="Enable 'dot all' mode in multiline searches."
    ),
    no_unicode: bool = typer.Option(False, "--no-unicode", help="Disable Unicode mode for regex."),
    null_data: bool = typer.Option(
        False, "--null-data", help="Use NUL as a line terminator instead of \\n."
    ),
    pcre2: bool = typer.Option(False, "-P", "--pcre2", help="Use the PCRE2 regex engine."),
    regex_size_limit: str | None = typer.Option(
        None, "--regex-size-limit", help="Size limit of the compiled regex."
    ),
    smart_case: bool = typer.Option(
        False, "-S", "--smart-case", help="Search case insensitively if pattern is all lowercase."
    ),
    stop_on_nonmatch: bool = typer.Option(
        False,
        "--stop-on-nonmatch",
        help="Stop reading file once a non-matching line is encountered after a match.",
    ),
    text: bool = typer.Option(
        False, "-a", "--text", help="Search binary files as if they were text."
    ),
    threads: int = typer.Option(
        0, "-j", "--threads", help="Approximate number of threads to use (0 = auto)."
    ),
    word_regexp: bool = typer.Option(
        False, "-w", "--word-regexp", help="Only show matches surrounded by word boundaries."
    ),
    # FILTER OPTIONS
    binary: bool = typer.Option(
        False, "--binary", help="Search binary files (don't stop on NUL byte)."
    ),
    follow: bool = typer.Option(False, "-L", "--follow", help="Follow symbolic links."),
    glob: list[str] | None = typer.Option(
        None, "-g", "--glob", help="Include/exclude files matching glob."
    ),
    glob_case_insensitive: bool = typer.Option(
        False, "--glob-case-insensitive", help="Process glob patterns case insensitively."
    ),
    hidden: bool = typer.Option(
        False, "-.", "--hidden", help="Search hidden files and directories."
    ),
    iglob: list[str] | None = typer.Option(
        None, "--iglob", help="Include/exclude files matching glob (case-insensitive)."
    ),
    ignore_file: list[str] | None = typer.Option(
        None, "--ignore-file", help="Path to gitignore formatted rules file."
    ),
    ignore_file_case_insensitive: bool = typer.Option(
        False, "--ignore-file-case-insensitive", help="Process ignore files case insensitively."
    ),
    max_depth: int | None = typer.Option(
        None, "-d", "--max-depth", help="Limit depth of directory traversal."
    ),
    max_filesize: str | None = typer.Option(
        None, "--max-filesize", help="Ignore files larger than this size."
    ),
    no_ignore: bool = typer.Option(
        False, "--no-ignore", help="Don't respect ignore files (.gitignore, .rgignore, etc)."
    ),
    no_ignore_dot: bool = typer.Option(
        False, "--no-ignore-dot", help="Don't respect .ignore or .rgignore files."
    ),
    no_ignore_exclude: bool = typer.Option(
        False, "--no-ignore-exclude", help="Don't respect .git/info/exclude."
    ),
    no_ignore_files: bool = typer.Option(
        False, "--no-ignore-files", help="Ignore any --ignore-file flags."
    ),
    no_ignore_global: bool = typer.Option(
        False, "--no-ignore-global", help="Don't respect global gitignore."
    ),
    no_ignore_parent: bool = typer.Option(
        False, "--no-ignore-parent", help="Don't respect ignore files in parent directories."
    ),
    no_ignore_vcs: bool = typer.Option(
        False, "--no-ignore-vcs", help="Don't respect source control ignore files (.gitignore)."
    ),
    no_require_git: bool = typer.Option(
        False, "--no-require-git", help="Respect .gitignore even outside of git repos."
    ),
    one_file_system: bool = typer.Option(
        False, "--one-file-system", help="Don't cross file system boundaries."
    ),
    type: list[str] | None = typer.Option(
        None, "-t", "--type", help="Only search files matching TYPE."
    ),
    type_not: list[str] | None = typer.Option(
        None, "-T", "--type-not", help="Do not search files matching TYPE."
    ),
    type_add: list[str] | None = typer.Option(
        None, "--type-add", help="Add a new glob for a file type."
    ),
    type_clear: str | None = typer.Option(None, "--type-clear", help="Clear globs for TYPE."),
    unrestricted: int = typer.Option(
        0, "-u", "--unrestricted", count=True, help="Reduce smart filtering (repeat up to 3 times)."
    ),
    # OUTPUT OPTIONS
    after_context: int | None = typer.Option(
        None, "-A", "--after-context", help="Show NUM lines after each match."
    ),
    before_context: int | None = typer.Option(
        None, "-B", "--before-context", help="Show NUM lines before each match."
    ),
    block_buffered: bool = typer.Option(False, "--block-buffered", help="Force block buffering."),
    byte_offset: bool = typer.Option(
        False, "-b", "--byte-offset", help="Print 0-based byte offset before each output line."
    ),
    color: str = typer.Option(
        "auto", "--color", help="When to use colors: never, auto, always, ansi."
    ),
    colors: list[str] | None = typer.Option(
        None, "--colors", help="Color settings for output (e.g. 'match:fg:magenta')."
    ),
    column: bool = typer.Option(False, "--column", help="Show column numbers (1-based)."),
    context: int | None = typer.Option(
        None, "-C", "--context", help="Show NUM lines before and after each match."
    ),
    context_separator: str = typer.Option(
        "--", "--context-separator", help="String used to separate non-contiguous context lines."
    ),
    field_context_separator: str = typer.Option(
        "-", "--field-context-separator", help="Set the field context separator."
    ),
    field_match_separator: str = typer.Option(
        ":", "--field-match-separator", help="Set the field match separator."
    ),
    heading: bool = typer.Option(
        True, "--heading", help="Print file path above clusters of matches."
    ),
    hostname_bin: str | None = typer.Option(
        None, "--hostname-bin", help="Executable to determine system hostname."
    ),
    hyperlink_format: str | None = typer.Option(
        None, "--hyperlink-format", help="Format of hyperlinks to use."
    ),
    include_zero: bool = typer.Option(
        False, "--include-zero", help="Print zero match counts with -c."
    ),
    line_buffered: bool = typer.Option(False, "--line-buffered", help="Force line buffering."),
    line_number: bool | None = typer.Option(
        None, "-n", "--line-number", help="Show line numbers (1-based)."
    ),
    max_columns: int | None = typer.Option(
        None, "-M", "--max-columns", help="Omit lines longer than this limit."
    ),
    max_columns_preview: bool = typer.Option(
        False, "--max-columns-preview", help="Preview lines exceeding max column limit."
    ),
    null: bool = typer.Option(False, "-0", "--null", help="Follow file paths with a NUL byte."),
    only_matching: bool = typer.Option(
        False, "-o", "--only-matching", help="Print only the matched parts of a line."
    ),
    path_separator: str | None = typer.Option(
        None, "--path-separator", help="Path separator to use."
    ),
    passthru: bool = typer.Option(
        False, "--passthru", help="Print both matching and non-matching lines."
    ),
    pretty: bool = typer.Option(
        False, "-p", "--pretty", help="Alias for --color=always --heading --line-number."
    ),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Do not print anything to stdout."),
    replace: str | None = typer.Option(
        None,
        "-r",
        "--replace",
        help="Replace every match with the given text. Supports capture groups (e.g., $1).",
    ),
    sort: str = typer.Option(
        "none", "--sort", help="Sort results (none, path, modified, accessed, created)."
    ),
    sortr: str = typer.Option("none", "--sortr", help="Sort results in reverse order."),
    trim: bool = typer.Option(False, "--trim", help="Remove leading ASCII whitespace from output."),
    vimgrep: bool = typer.Option(
        False,
        "--vimgrep",
        help="Print results with every match on its own line (line/column numbers).",
    ),
    with_filename: bool = typer.Option(
        False, "-H", "--with-filename", help="Print file path for each matching line."
    ),
    no_filename: bool = typer.Option(
        False, "-I", "--no-filename", help="Never print the file path."
    ),
    # OUTPUT MODES
    count: bool = typer.Option(
        False, "-c", "--count", help="Show only the number of matching lines per file."
    ),
    count_matches: bool = typer.Option(
        False, "--count-matches", help="Show only the total number of matches per file."
    ),
    files_with_matches: bool = typer.Option(
        False, "-l", "--files-with-matches", help="Print only paths with at least one match."
    ),
    files_without_match: bool = typer.Option(
        False, "--files-without-match", help="Print paths containing zero matches."
    ),
    json: bool = typer.Option(False, "--json", help="Print results in JSON Lines format."),
    ndjson: bool = typer.Option(False, "--ndjson", help="Print results in newline-delimited JSON."),
    # LOGGING OPTIONS
    debug: bool = typer.Option(False, "--debug", help="Show debug messages."),
    no_ignore_messages: bool = typer.Option(
        False, "--no-ignore-messages", help="Suppress ignore file parsing errors."
    ),
    no_messages: bool = typer.Option(
        False, "--no-messages", help="Suppress some error messages (like failed file opens)."
    ),
    stats: bool = typer.Option(False, "--stats", help="Print aggregate statistics."),
    trace: bool = typer.Option(False, "--trace", help="Show exhaustive trace messages."),
    # OTHER BEHAVIORS
    files: bool = typer.Option(
        False, "--files", help="Print files that would be searched and exit."
    ),
    generate: str | None = typer.Option(
        None, "--generate", help="Generate special output (e.g. man, complete-bash)."
    ),
    no_config: bool = typer.Option(False, "--no-config", help="Never read configuration files."),
    pcre2_version: bool = typer.Option(
        False, "--pcre2-version", help="Print PCRE2 version and exit."
    ),
    type_list: bool = typer.Option(
        False, "--type-list", help="Show all supported file types and exit."
    ),
    # TENSOR-GREP SPECIFIC
    cpu: bool = typer.Option(
        False,
        "--cpu",
        "--force-cpu",
        help="Force CPU fallback (tensor-grep specific).",
    ),
    format_type: str = typer.Option(
        "rg", "--format", help="Internal formatter: json, table, csv, rg"
    ),
    ast: bool = typer.Option(
        False,
        "--ast",
        help="Parse files into ASTs and search structurally using PyTorch Geometric.",
    ),
    lang: str | None = typer.Option(
        None,
        "--lang",
        help="Explicitly define language grammar for --ast (e.g. python, javascript).",
    ),
    ltl: bool = typer.Option(
        False,
        "--ltl",
        help="Interpret PATTERN as a temporal query (supports: 'A -> eventually B').",
    ),
    gpu_device_ids: str | None = typer.Option(
        None,
        "--gpu-device-ids",
        help="Comma-separated GPU IDs to pin this search request to (e.g. 0,1).",
    ),
) -> None:
    """
    Search files for a regex pattern, with GPU acceleration when applicable.
    The stable text-search contract is the validated rg-compatible surface documented in docs/CONTRACTS.md.
    """
    # Just forward to CPU backend for now as a stub.
    # Note: Full flag wiring will require mapping these dozens of parameters into the Pipeline/Core components.
    args = positionals or []
    pattern = ""
    if generate is not None:
        typer.echo(_generate_shell_completion_script(generator=generate))
        raise typer.Exit(0)
    if files:
        if not args:
            typer.echo("Error: Please provide at least one PATH to search.", err=True)
            sys.exit(1)
        paths_to_search = args
    else:
        if not args:
            typer.echo("Error: Please provide a PATTERN to search.", err=True)
            sys.exit(1)
        pattern = args[0]
        if pattern == "":
            typer.echo("Error: PATTERN must not be empty.", err=True)
            sys.exit(2)
        paths_to_search = args[1:]
        if not paths_to_search:
            typer.echo("Error: Please provide at least one PATH to search.", err=True)
            sys.exit(1)

    if not files:
        missing_paths = [
            path for path in paths_to_search if path != "-" and not Path(path).exists()
        ]
        if missing_paths:
            for missing_path in missing_paths:
                typer.echo(f"Error: search path does not exist: {missing_path}", err=True)
            sys.exit(2)

    if line_number is None:
        line_number = sys.stdout.isatty()

    from tensor_grep.core.config import SearchConfig

    parsed_gpu_device_ids = _parse_gpu_device_ids_cli(gpu_device_ids)

    effective_force_cpu = cpu or env_flag_enabled("TG_FORCE_CPU")
    implicit_with_filename = (
        not no_filename
        and not effective_force_cpu
        and not json
        and not ndjson
        and not only_matching
        and not parsed_gpu_device_ids
        and replace is None
        and (
            len(paths_to_search) > 1
            or any(path != "-" and Path(path).is_dir() for path in paths_to_search)
        )
    )

    config = SearchConfig(
        regexp=regexp,
        file_patterns=file,
        pre=pre,
        pre_glob=pre_glob,
        search_zip=search_zip,
        case_sensitive=case_sensitive,
        crlf=crlf,
        dfa_size_limit=dfa_size_limit,
        encoding=encoding,
        engine=engine,
        fixed_strings=fixed_strings,
        ignore_case=ignore_case,
        invert_match=invert_match,
        line_regexp=line_regexp,
        max_count=max_count,
        mmap=mmap,
        multiline=multiline,
        multiline_dotall=multiline_dotall,
        no_unicode=no_unicode,
        null_data=null_data,
        pcre2=pcre2,
        regex_size_limit=regex_size_limit,
        smart_case=smart_case,
        stop_on_nonmatch=stop_on_nonmatch,
        text=text,
        threads=threads,
        word_regexp=word_regexp,
        binary=binary,
        follow=follow,
        glob=glob,
        glob_case_insensitive=glob_case_insensitive,
        hidden=hidden,
        iglob=iglob,
        ignore_file=ignore_file,
        ignore_file_case_insensitive=ignore_file_case_insensitive,
        max_depth=max_depth,
        max_filesize=max_filesize,
        no_ignore=no_ignore,
        no_ignore_dot=no_ignore_dot,
        no_ignore_exclude=no_ignore_exclude,
        no_ignore_files=no_ignore_files,
        no_ignore_global=no_ignore_global,
        no_ignore_parent=no_ignore_parent,
        no_ignore_vcs=no_ignore_vcs,
        no_require_git=no_require_git,
        one_file_system=one_file_system,
        file_type=type,
        type_not=type_not,
        type_add=type_add,
        type_clear=type_clear,
        unrestricted=unrestricted,
        after_context=after_context,
        before_context=before_context,
        block_buffered=block_buffered,
        byte_offset=byte_offset,
        color=color,
        colors=colors,
        column=column,
        context=context,
        context_separator=context_separator,
        field_context_separator=field_context_separator,
        field_match_separator=field_match_separator,
        heading=heading,
        hostname_bin=hostname_bin,
        hyperlink_format=hyperlink_format,
        include_zero=include_zero,
        line_buffered=line_buffered,
        line_number=line_number,
        max_columns=max_columns,
        max_columns_preview=max_columns_preview,
        null=null,
        only_matching=only_matching,
        path_separator=path_separator,
        passthru=passthru,
        pretty=pretty,
        quiet=quiet,
        replace_str=replace,
        sort_by=sort,
        sort_by_reverse=sortr,
        trim=trim,
        vimgrep=vimgrep,
        with_filename=with_filename or implicit_with_filename,
        no_filename=no_filename,
        count=count,
        count_matches=count_matches,
        files_with_matches=files_with_matches,
        files_without_match=files_without_match,
        json_mode=json,
        debug=debug,
        no_ignore_messages=no_ignore_messages,
        no_messages=no_messages,
        stats=stats,
        trace=trace,
        list_files=files,
        generate=generate,
        no_config=no_config,
        pcre2_version=pcre2_version,
        type_list=type_list,
        force_cpu=effective_force_cpu,
        format_type=format_type,
        ast=ast,
        lang=lang,
        ltl=ltl,
        query_pattern=pattern,
        gpu_device_ids=parsed_gpu_device_ids,
    )

    native_tg_binary = resolve_native_tg_binary()
    if native_tg_binary is not None and _can_delegate_to_native_tg_search(
        config,
        ndjson=ndjson,
        files_mode=files,
        files_with_matches=files_with_matches,
        files_without_match=files_without_match,
        format_type=format_type,
    ):
        sys.exit(
            _delegate_to_native_tg_search(
                native_tg_binary,
                pattern=pattern,
                paths=paths_to_search,
                config=config,
                ndjson=ndjson,
            )
        )
    if ndjson:
        typer.echo(
            "Error: --ndjson requires the native tg binary with a compatible native-search flag set.",
            err=True,
        )
        sys.exit(2)

    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.io.directory_scanner import DirectoryScanner

    rg_backend = RipgrepBackend()
    can_passthrough_rg = rg_backend.is_available() and _can_passthrough_rg(
        config,
        format_type=format_type,
        json_mode=json,
        ndjson_mode=ndjson,
        files_mode=files,
        files_with_matches=files_with_matches,
        files_without_match=files_without_match,
        only_matching=only_matching,
        stats_mode=stats,
    )
    if can_passthrough_rg:
        if not stats:
            with nvtx_range("search.passthrough_rg", color="green"):
                exit_code = rg_backend.search_passthrough(paths_to_search, pattern, config=config)
            sys.exit(0 if exit_code == 0 else 1)

    scanner = DirectoryScanner(config)
    candidate_files_ordered, candidate_files_set = _collect_candidate_files(
        scanner, paths_to_search
    )
    config.input_total_bytes = _sum_total_bytes(candidate_files_ordered)

    from tensor_grep.core.pipeline import Pipeline
    from tensor_grep.core.result import SearchResult

    pipeline = Pipeline(force_cpu=effective_force_cpu, config=config)
    backend = pipeline.get_backend()
    selected_backend_name = getattr(pipeline, "selected_backend_name", backend.__class__.__name__)
    selected_backend_reason = getattr(pipeline, "selected_backend_reason", "unknown")
    selected_gpu_device_ids = list(getattr(pipeline, "selected_gpu_device_ids", []) or [])
    selected_gpu_chunk_plan_mb = list(getattr(pipeline, "selected_gpu_chunk_plan_mb", []) or [])
    if (
        can_passthrough_rg
        and stats
        and _selected_route_supports_rg_passthrough(
            selected_backend_name=selected_backend_name,
            selected_backend_reason=selected_backend_reason,
            selected_gpu_device_ids=selected_gpu_device_ids,
            selected_gpu_chunk_plan_mb=selected_gpu_chunk_plan_mb,
        )
    ):
        with nvtx_range("search.passthrough_rg", color="green"):
            exit_code = rg_backend.search_passthrough(paths_to_search, pattern, config=config)
        sys.exit(0 if exit_code == 0 else 1)
    if debug:
        typer.echo(
            f"[debug] routing.backend={selected_backend_name} reason={selected_backend_reason}"
        )
        if selected_gpu_device_ids or selected_gpu_chunk_plan_mb:
            typer.echo(
                f"[debug] routing.gpu_device_ids={selected_gpu_device_ids} "
                f"routing.gpu_chunk_plan_mb={selected_gpu_chunk_plan_mb}"
            )

    if files:
        if candidate_files_ordered:
            _write_path_list(candidate_files_ordered, use_nul=null)
            sys.exit(0)
        sys.exit(1)

    tracer = None
    try:
        from opentelemetry import trace as otel_trace

        tracer = otel_trace.get_tracer(__name__)
    except ImportError:
        tracer = None

    all_results = SearchResult(matches=[], total_files=0, total_matches=0)
    all_results.routing_backend = selected_backend_name
    all_results.routing_reason = selected_backend_reason
    all_results.routing_gpu_device_ids = selected_gpu_device_ids
    all_results.routing_gpu_chunk_plan_mb = selected_gpu_chunk_plan_mb
    search_start = time.perf_counter()
    matched_file_paths: set[str] = set()

    def _merge_runtime_routing(result: SearchResult) -> None:
        # Runtime routing metadata is authoritative when a backend internally
        # falls back (for example Torch -> CPU for unsupported regex paths).
        if result.routing_backend:
            all_results.routing_backend = result.routing_backend
            all_results.routing_gpu_device_ids = list(result.routing_gpu_device_ids)
            all_results.routing_gpu_chunk_plan_mb = list(result.routing_gpu_chunk_plan_mb)
        elif result.routing_gpu_device_ids or result.routing_gpu_chunk_plan_mb:
            all_results.routing_gpu_device_ids = list(result.routing_gpu_device_ids)
            all_results.routing_gpu_chunk_plan_mb = list(result.routing_gpu_chunk_plan_mb)
        if result.routing_reason:
            all_results.routing_reason = result.routing_reason
        all_results.routing_distributed = (
            all_results.routing_distributed or result.routing_distributed
        )
        all_results.routing_worker_count = max(
            all_results.routing_worker_count, result.routing_worker_count
        )

    def _merge_count_metadata(result: SearchResult) -> None:
        for file_path, count in result.match_counts_by_file.items():
            all_results.match_counts_by_file[file_path] = (
                all_results.match_counts_by_file.get(file_path, 0) + count
            )

    # RipgrepBackend optimization: passing all paths natively
    if backend.__class__.__name__ == "RipgrepBackend":
        rg_backend = cast(RipgrepBackend, backend)
        search_targets = (
            candidate_files_ordered
            if (files_with_matches or files_without_match)
            else paths_to_search
        )
        span_ctx = (
            tracer.start_as_current_span("search.file") if tracer is not None else nullcontext()
        )
        with span_ctx as span, nvtx_range("search.file", color="cyan"):
            if span is not None:
                span.set_attribute("backend", backend.__class__.__name__)
                span.set_attribute("path_count", len(search_targets))
            result = rg_backend.search(search_targets, pattern, config=config)
            if span is not None:
                span.set_attribute("matches", result.total_matches)
            all_results.matches.extend(result.matches)
            matched_file_paths.update(result.matched_file_paths)
            _merge_count_metadata(result)
            all_results.total_matches += result.total_matches
            all_results.total_files += result.total_files
            matched_file_paths.update(m.file for m in result.matches)
            _merge_runtime_routing(result)
    else:
        for current_file in candidate_files_ordered:
            span_ctx = (
                tracer.start_as_current_span("search.file") if tracer is not None else nullcontext()
            )
            with span_ctx as span, nvtx_range("search.file", color="cyan"):
                if span is not None:
                    span.set_attribute("backend", backend.__class__.__name__)
                    span.set_attribute("path", current_file)
                result = backend.search(current_file, pattern, config=config)
                if span is not None:
                    span.set_attribute("matches", result.total_matches)
            all_results.matches.extend(result.matches)
            matched_file_paths.update(result.matched_file_paths)
            _merge_count_metadata(result)
            all_results.total_matches += result.total_matches
            if result.total_files > 0 or result.total_matches > 0:
                all_results.total_files += 1
                matched_file_paths.add(current_file)
            matched_file_paths.update(m.file for m in result.matches)
            _merge_runtime_routing(result)

    if config.replace_str is not None:
        all_results.matches = _replace_lines(all_results.matches, pattern, config)

    if only_matching:
        all_results.matches = _only_matching_lines(all_results.matches, pattern, config)
        all_results.total_matches = len(all_results.matches)
        all_results.total_files = len({m.file for m in all_results.matches})
        matched_file_paths = {m.file for m in all_results.matches}

    matched_files = set(matched_file_paths)
    all_results.matched_file_paths = sorted(matched_files)
    if not all_results.match_counts_by_file and all_results.matches:
        for match in all_results.matches:
            all_results.match_counts_by_file[match.file] = (
                all_results.match_counts_by_file.get(match.file, 0) + 1
            )
    matched_file_count = len(matched_files) or all_results.total_files
    elapsed_ms = (time.perf_counter() - search_start) * 1000.0
    runtime_override_active = (
        all_results.routing_backend is not None
        and all_results.routing_backend != selected_backend_name
    ) or (
        all_results.routing_reason is not None
        and all_results.routing_reason != selected_backend_reason
    )
    if (
        not runtime_override_active
        and all_results.routing_worker_count == 0
        and (all_results.routing_gpu_device_ids or all_results.routing_gpu_chunk_plan_mb)
    ):
        (
            all_results.routing_distributed,
            all_results.routing_worker_count,
        ) = _selected_gpu_execution_defaults(
            list(all_results.routing_gpu_device_ids),
            list(all_results.routing_gpu_chunk_plan_mb),
        )

    def _emit_runtime_debug() -> None:
        if not debug:
            return
        runtime_backend = all_results.routing_backend or selected_backend_name
        runtime_reason = all_results.routing_reason or selected_backend_reason
        runtime_gpu_device_ids = all_results.routing_gpu_device_ids or selected_gpu_device_ids
        runtime_gpu_chunk_plan_mb = (
            all_results.routing_gpu_chunk_plan_mb or selected_gpu_chunk_plan_mb
        )

        runtime_differs = (
            runtime_backend != selected_backend_name
            or runtime_reason != selected_backend_reason
            or runtime_gpu_device_ids != selected_gpu_device_ids
            or runtime_gpu_chunk_plan_mb != selected_gpu_chunk_plan_mb
        )
        if not runtime_differs:
            return

        typer.echo(
            f"[debug] routing.runtime backend={runtime_backend} reason={runtime_reason}",
            err=True,
        )
        if runtime_gpu_device_ids or runtime_gpu_chunk_plan_mb:
            typer.echo(
                (
                    f"[debug] routing.runtime.gpu_device_ids={runtime_gpu_device_ids} "
                    f"routing.runtime.gpu_chunk_plan_mb={runtime_gpu_chunk_plan_mb} "
                    f"distributed={all_results.routing_distributed} "
                    f"workers={all_results.routing_worker_count}"
                ),
                err=True,
            )

    def _emit_stats() -> None:
        if not stats:
            return
        typer.echo(
            (
                f"[stats] scanned_files={len(candidate_files_ordered)} "
                f"matched_files={matched_file_count} "
                f"total_matches={all_results.total_matches} "
                f"elapsed_ms={elapsed_ms:.2f}"
            ),
            err=True,
        )
        typer.echo(
            (
                f"[stats] backend={all_results.routing_backend or selected_backend_name} "
                f"reason={all_results.routing_reason or selected_backend_reason}"
            ),
            err=True,
        )
        if runtime_override_active:
            stats_gpu_device_ids = list(all_results.routing_gpu_device_ids)
            stats_gpu_chunk_plan_mb = list(all_results.routing_gpu_chunk_plan_mb)
        else:
            stats_gpu_device_ids = all_results.routing_gpu_device_ids or selected_gpu_device_ids
            stats_gpu_chunk_plan_mb = (
                all_results.routing_gpu_chunk_plan_mb or selected_gpu_chunk_plan_mb
            )
        if stats_gpu_device_ids or stats_gpu_chunk_plan_mb:
            typer.echo(
                (
                    f"[stats] gpu_device_ids={stats_gpu_device_ids} "
                    f"gpu_chunk_plan_mb={stats_gpu_chunk_plan_mb} "
                    f"distributed={all_results.routing_distributed} "
                    f"workers={all_results.routing_worker_count}"
                ),
                err=True,
            )

    _emit_runtime_debug()

    if files_with_matches:
        if matched_files:
            _emit_stats()
            _write_path_list(sorted(matched_files), use_nul=null)
            sys.exit(0)
        _emit_stats()
        sys.exit(1)

    if files_without_match:
        unmatched = sorted(candidate_files_set - matched_files)
        if unmatched:
            _emit_stats()
            _write_path_list(unmatched, use_nul=null)
            sys.exit(0)
        _emit_stats()
        sys.exit(1)

    if all_results.is_empty:
        _emit_stats()
        sys.exit(1)

    if quiet:
        _emit_stats()
        sys.exit(0)

    formatter: OutputFormatter

    if json or format_type == "json":
        from tensor_grep.cli.formatters.json_fmt import JsonFormatter

        formatter = JsonFormatter()
    elif format_type == "table":
        from tensor_grep.cli.formatters.table_fmt import TableFormatter

        formatter = TableFormatter()
    elif format_type == "csv":
        from tensor_grep.cli.formatters.csv_fmt import CsvFormatter

        formatter = CsvFormatter()
    else:
        from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter

        formatter = RipgrepFormatter(config=config)

    print(formatter.format(all_results))
    _emit_stats()


@app.command()
def calibrate() -> None:
    """Measure CPU vs GPU crossover thresholds using the native Rust binary."""
    native_tg_binary = resolve_native_tg_binary()
    if native_tg_binary is None:
        typer.echo("Error: native tg binary not found for calibrate command.", err=True)
        raise typer.Exit(2)

    completed = subprocess.run([str(native_tg_binary), "calibrate"], check=False)
    raise typer.Exit(int(completed.returncode))


@app.command()
def devices(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit device inventory as JSON for automation.",
    ),
    format_type: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """Print routable GPU device IDs and VRAM inventory."""
    import json

    from tensor_grep.core.hardware.device_inventory import collect_device_inventory

    normalized_format = format_type.lower().strip()
    if json_output:
        normalized_format = "json"
    if normalized_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be one of: text, json")

    inventory = collect_device_inventory()
    payload = inventory.to_dict()

    if normalized_format == "json":
        print(json.dumps(payload))
        return

    if not inventory.devices:
        typer.echo("No routable GPUs detected.")
        return

    typer.echo(f"Detected {inventory.device_count} routable GPU(s):")
    for device in inventory.devices:
        typer.echo(f"- gpu:{device.device_id} vram_mb={device.vram_capacity_mb}")


@app.command()
def map(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a deterministic repository map for AI editing workflows."""
    from tensor_grep.cli.repo_map import build_repo_map, build_repo_map_json

    try:
        if json_output:
            typer.echo(build_repo_map_json(path))
            return

        payload = build_repo_map(path)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Repository map for {payload['path']}")
    typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")
    typer.echo(f"symbols={len(payload['symbols'])} imports={len(payload['imports'])}")


@app.command()
def context(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query: str = typer.Option(
        ..., "--query", help="Query text used to rank relevant repo context."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a ranked repository context pack for edit planning."""
    from tensor_grep.cli.repo_map import build_context_pack, build_context_pack_json

    try:
        if json_output:
            typer.echo(build_context_pack_json(query, path))
            return

        payload = build_context_pack(query, path)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Context pack for {payload['path']}")
    typer.echo(f"query={payload['query']}")
    typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")
    typer.echo(f"symbols={len(payload['symbols'])} imports={len(payload['imports'])}")


@app.command(name="context-render")
def context_render(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query: str = typer.Option(
        ..., "--query", help="Query text used to rank and render repo context."
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the render bundle."
    ),
    max_sources: int = typer.Option(
        5, "--max-sources", min=1, help="Maximum exact source blocks to include."
    ),
    max_symbols_per_file: int = typer.Option(
        6, "--max-symbols-per-file", min=1, help="Maximum summary symbols to include per file."
    ),
    max_render_chars: int | None = typer.Option(
        None, "--max-render-chars", min=1, help="Maximum characters to emit in rendered_context."
    ),
    max_tokens: int | None = typer.Option(
        None, "--max-tokens", min=1, help="Approximate maximum tokens to emit in rendered_context."
    ),
    model: str | None = typer.Option(
        None, "--model", help="Future tokenizer model selector; currently accepted but ignored."
    ),
    optimize_context: bool = typer.Option(
        False,
        "--optimize-context",
        help="Strip blank lines and comment-only lines from rendered source blocks.",
    ),
    render_profile: str = typer.Option(
        "full",
        "--render-profile",
        help="Render profile: full, compact, or llm.",
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-phase profiling in JSON output."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready repository context bundle for edit planning."""
    from tensor_grep.cli.repo_map import build_context_render, build_context_render_json

    try:
        if json_output:
            typer.echo(
                build_context_render_json(
                    query,
                    path,
                    max_files=max_files,
                    max_sources=max_sources,
                    max_symbols_per_file=max_symbols_per_file,
                    max_render_chars=max_render_chars,
                    max_tokens=max_tokens,
                    model=model,
                    optimize_context=optimize_context,
                    render_profile=render_profile,
                    profile=profile,
                )
            )
            return

        payload = build_context_render(
            query,
            path,
            max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            max_tokens=max_tokens,
            model=model,
            optimize_context=optimize_context,
            render_profile=render_profile,
            profile=profile,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(payload["rendered_context"])


@app.command(name="edit-plan")
def edit_plan(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query: str = typer.Option(..., "--query", help="Query text used to rank edit targets."),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the plan."
    ),
    max_symbols: int = typer.Option(
        5, "--max-symbols", min=1, help="Maximum ranked symbols to retain in the plan payload."
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-phase profiling in JSON output."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a machine-readable edit-planning bundle without rendered source text."""
    from tensor_grep.cli.repo_map import build_context_edit_plan, build_context_edit_plan_json

    try:
        if json_output:
            typer.echo(
                build_context_edit_plan_json(
                    query,
                    path,
                    max_files=max_files,
                    max_symbols=max_symbols,
                    profile=profile,
                )
            )
            return

        payload = build_context_edit_plan(
            query,
            path,
            max_files=max_files,
            max_symbols=max_symbols,
            profile=profile,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Edit plan for {payload['path']}")
    typer.echo(f"query={payload['query']}")
    typer.echo(
        f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
    )


@app.command()
def defs(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return exact definition locations for a symbol."""
    from tensor_grep.cli.repo_map import build_symbol_defs, build_symbol_defs_json

    try:
        if json_output:
            typer.echo(build_symbol_defs_json(symbol, path, semantic_provider=provider))
            return

        payload = build_symbol_defs(symbol, path, semantic_provider=provider)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Definitions for {payload['symbol']} in {payload['path']}")
    typer.echo(f"definitions={len(payload['definitions'])}")


@app.command()
def source(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return exact source blocks for a symbol definition."""
    from tensor_grep.cli.repo_map import build_symbol_source, build_symbol_source_json

    try:
        if json_output:
            typer.echo(build_symbol_source_json(symbol, path, semantic_provider=provider))
            return

        payload = build_symbol_source(symbol, path, semantic_provider=provider)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Source for {payload['symbol']} in {payload['path']}")
    typer.echo(f"sources={len(payload['sources'])} files={len(payload['files'])}")


@app.command()
def impact(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to evaluate."),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return likely impacted files and tests for a symbol change."""
    from tensor_grep.cli.repo_map import build_symbol_impact, build_symbol_impact_json

    try:
        if json_output:
            typer.echo(build_symbol_impact_json(symbol, path, semantic_provider=provider))
            return

        payload = build_symbol_impact(symbol, path, semantic_provider=provider)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Impact for {payload['symbol']} in {payload['path']}")
    typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")


@app.command()
def refs(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return Python-first symbol references across the inventory root."""
    from tensor_grep.cli.repo_map import build_symbol_refs, build_symbol_refs_json

    try:
        if json_output:
            typer.echo(build_symbol_refs_json(symbol, path, semantic_provider=provider))
            return

        payload = build_symbol_refs(symbol, path, semantic_provider=provider)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"References for {payload['symbol']} in {payload['path']}")
    typer.echo(f"references={len(payload['references'])} files={len(payload['files'])}")


@app.command()
def callers(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return Python-first call sites and likely impacted tests for a symbol."""
    from tensor_grep.cli.repo_map import build_symbol_callers, build_symbol_callers_json

    try:
        if json_output:
            typer.echo(build_symbol_callers_json(symbol, path, semantic_provider=provider))
            return

        payload = build_symbol_callers(symbol, path, semantic_provider=provider)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Callers for {payload['symbol']} in {payload['path']}")
    typer.echo(f"callers={len(payload['callers'])} files={len(payload['files'])}")


@app.command(name="blast-radius")
def blast_radius(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return exact callers plus a transitive file/test blast radius for a symbol."""
    from tensor_grep.cli.repo_map import (
        build_symbol_blast_radius,
        build_symbol_blast_radius_json,
    )

    try:
        if json_output:
            typer.echo(
                build_symbol_blast_radius_json(
                    symbol, path, max_depth=max_depth, semantic_provider=provider
                )
            )
            return

        payload = build_symbol_blast_radius(
            symbol, path, max_depth=max_depth, semantic_provider=provider
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Blast radius for {payload['symbol']} in {payload['path']}")
    typer.echo(
        f"definitions={len(payload['definitions'])} callers={len(payload['callers'])} "
        f"files={len(payload['files'])} tests={len(payload['tests'])}"
    )


@app.command(name="blast-radius-render")
def blast_radius_render(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the render bundle."
    ),
    max_sources: int = typer.Option(
        5, "--max-sources", min=1, help="Maximum exact source blocks to include."
    ),
    max_symbols_per_file: int = typer.Option(
        6, "--max-symbols-per-file", min=1, help="Maximum summary symbols to include per file."
    ),
    max_render_chars: int | None = typer.Option(
        None, "--max-render-chars", min=1, help="Maximum characters to emit in rendered_context."
    ),
    optimize_context: bool = typer.Option(
        False,
        "--optimize-context",
        help="Strip blank lines and comment-only lines from rendered source blocks.",
    ),
    render_profile: str = typer.Option(
        "full",
        "--render-profile",
        help="Render profile: full, compact, or llm.",
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-phase profiling in JSON output."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready blast-radius bundle for a symbol."""
    from tensor_grep.cli.repo_map import (
        build_symbol_blast_radius_render,
        build_symbol_blast_radius_render_json,
    )

    try:
        if json_output:
            typer.echo(
                build_symbol_blast_radius_render_json(
                    symbol,
                    path,
                    max_depth=max_depth,
                    max_files=max_files,
                    max_sources=max_sources,
                    max_symbols_per_file=max_symbols_per_file,
                    max_render_chars=max_render_chars,
                    optimize_context=optimize_context,
                    render_profile=render_profile,
                    profile=profile,
                    semantic_provider=provider,
                )
            )
            return

        payload = build_symbol_blast_radius_render(
            symbol,
            path,
            max_depth=max_depth,
            max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            optimize_context=optimize_context,
            render_profile=render_profile,
            profile=profile,
            semantic_provider=provider,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(payload["rendered_context"])


@app.command(name="blast-radius-plan")
def blast_radius_plan(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the plan."
    ),
    max_symbols: int = typer.Option(
        5, "--max-symbols", min=1, help="Maximum ranked symbols to retain in the plan payload."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a machine-readable blast-radius planning bundle without rendered source text."""
    from tensor_grep.cli.repo_map import (
        build_symbol_blast_radius_plan,
        build_symbol_blast_radius_plan_json,
    )

    try:
        if json_output:
            typer.echo(
                build_symbol_blast_radius_plan_json(
                    symbol,
                    path,
                    max_depth=max_depth,
                    max_files=max_files,
                    max_symbols=max_symbols,
                    semantic_provider=provider,
                )
            )
            return

        payload = build_symbol_blast_radius_plan(
            symbol,
            path,
            max_depth=max_depth,
            max_files=max_files,
            max_symbols=max_symbols,
            semantic_provider=provider,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Blast radius plan for {payload['symbol']} in {payload['path']}")
    typer.echo(
        f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
    )


@session_app.command("open")
def session_open(
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Create a cached repo-map session for repeated edit loops."""
    from tensor_grep.cli.session_store import open_session

    try:
        payload = open_session(path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload.__dict__, indent=2))
        return

    typer.echo(
        f"Opened session {payload.session_id} "
        f"(files={payload.file_count}, symbols={payload.symbol_count})"
    )


@session_daemon_app.command("start")
def session_daemon_start(
    path: str = typer.Argument(".", help="File or directory rooted at the daemon scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Start or reuse a warm localhost session daemon for the current root."""
    from tensor_grep.cli.session_daemon import start_session_daemon

    try:
        payload = start_session_daemon(path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(
        f"Session daemon running on {payload['host']}:{payload['port']} pid={payload['pid']}"
    )


@session_daemon_app.command("status")
def session_daemon_status(
    path: str = typer.Argument(".", help="File or directory rooted at the daemon scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Show daemon status for the current root."""
    from tensor_grep.cli.session_daemon import get_session_daemon_status

    try:
        payload = get_session_daemon_status(path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    if payload.get("running"):
        typer.echo(
            f"Session daemon running on {payload['host']}:{payload['port']} pid={payload['pid']}"
        )
    else:
        typer.echo("Session daemon not running")


@session_daemon_app.command("stop")
def session_daemon_stop(
    path: str = typer.Argument(".", help="File or directory rooted at the daemon scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Stop the warm localhost session daemon for the current root."""
    from tensor_grep.cli.session_daemon import stop_session_daemon

    try:
        payload = stop_session_daemon(path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo("Session daemon stopped" if payload.get("stopped") else "Session daemon not running")


@session_app.command("list")
def session_list(
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """List cached sessions for the current root."""
    from tensor_grep.cli.session_store import list_sessions

    try:
        records = [record.__dict__ for record in list_sessions(path)]
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps({"version": 1, "sessions": records}, indent=2))
        return

    if not records:
        typer.echo("No sessions found.")
        return

    for record in records:
        typer.echo(
            f"{record['session_id']}  {record['created_at']}  "
            f"files={record['file_count']} symbols={record['symbol_count']}"
        )


@session_app.command("show")
def session_show(
    session_id: str = typer.Argument(..., help="Session ID to inspect."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Show the cached repo-map payload for a session."""
    from tensor_grep.cli.session_store import get_session

    try:
        payload = get_session(session_id, path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Session {payload['session_id']} for {payload['root']}")
    typer.echo(
        f"files={len(payload['repo_map']['files'])} symbols={len(payload['repo_map']['symbols'])}"
    )


@session_app.command("refresh")
def session_refresh(
    session_id: str = typer.Argument(..., help="Session ID to refresh."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Refresh a cached session after file changes."""
    from tensor_grep.cli.session_store import refresh_session

    try:
        payload = refresh_session(session_id, path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload.__dict__, indent=2))
        return

    typer.echo(
        f"Refreshed session {payload.session_id} "
        f"(files={payload.file_count}, symbols={payload.symbol_count})"
    )


@session_app.command("context")
def session_context_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    query: str = typer.Option(
        ..., "--query", help="Query text used to rank relevant repo context."
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a context pack derived from a cached session."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_context

    try:
        if daemon:
            payload = request_session_daemon(
                path,
                {
                    "command": "context",
                    "session_id": session_id,
                    "path": path,
                    "query": query,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_context(session_id, query, path, refresh_on_stale=refresh_on_stale)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Session context for {payload['session_id']}")
    typer.echo(f"query={payload['query']}")
    typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")


@session_app.command("context-render")
def session_context_render_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    query: str = typer.Option(
        ..., "--query", help="Query text used to rank and render repo context."
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the render bundle."
    ),
    max_sources: int = typer.Option(
        5, "--max-sources", min=1, help="Maximum exact source blocks to include."
    ),
    max_symbols_per_file: int = typer.Option(
        6, "--max-symbols-per-file", min=1, help="Maximum summary symbols to include per file."
    ),
    max_render_chars: int | None = typer.Option(
        None, "--max-render-chars", min=1, help="Maximum characters to emit in rendered_context."
    ),
    max_tokens: int | None = typer.Option(
        None, "--max-tokens", min=1, help="Approximate maximum tokens to emit in rendered_context."
    ),
    model: str | None = typer.Option(
        None, "--model", help="Future tokenizer model selector; currently accepted but ignored."
    ),
    optimize_context: bool = typer.Option(
        False,
        "--optimize-context",
        help="Strip blank lines and comment-only lines from rendered source blocks.",
    ),
    render_profile: str = typer.Option(
        "full",
        "--render-profile",
        help="Render profile: full, compact, or llm.",
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready render bundle derived from a cached session."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import SessionStaleError, session_context_render

    try:
        if daemon:
            payload = request_session_daemon(
                path,
                {
                    "command": "context_render",
                    "session_id": session_id,
                    "path": path,
                    "query": query,
                    "max_files": max_files,
                    "max_sources": max_sources,
                    "max_symbols_per_file": max_symbols_per_file,
                    "max_render_chars": max_render_chars,
                    "max_tokens": max_tokens,
                    "model": model,
                    "optimize_context": optimize_context,
                    "render_profile": render_profile,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_context_render(
                session_id,
                query,
                path,
                max_files=max_files,
                max_sources=max_sources,
                max_symbols_per_file=max_symbols_per_file,
                max_render_chars=max_render_chars,
                max_tokens=max_tokens,
                model=model,
                optimize_context=optimize_context,
                render_profile=render_profile,
                refresh_on_stale=refresh_on_stale,
            )
    except SessionStaleError as exc:
        error_payload = {
            "version": 1,
            "session_id": session_id,
            "error": {"code": "invalid_input", "message": str(exc)},
        }
        typer.echo(json.dumps(error_payload, indent=2))
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(payload["rendered_context"])


@session_app.command("edit-plan")
def session_edit_plan_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    query: str = typer.Option(..., "--query", help="Query text used to rank edit targets."),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the plan."
    ),
    max_symbols: int = typer.Option(
        5, "--max-symbols", min=1, help="Maximum ranked symbols to retain in the plan payload."
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a cached-session edit-planning bundle without rendered source text."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_context_edit_plan

    try:
        if daemon:
            payload = request_session_daemon(
                path,
                {
                    "command": "context_edit_plan",
                    "session_id": session_id,
                    "path": path,
                    "query": query,
                    "max_files": max_files,
                    "max_symbols": max_symbols,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_context_edit_plan(
                session_id,
                query,
                path,
                max_files=max_files,
                max_symbols=max_symbols,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Session edit plan for {payload['session_id']}")
    typer.echo(f"query={payload['query']}")
    typer.echo(
        f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
    )


@session_app.command("blast-radius")
def session_blast_radius_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a cached-session blast radius for a symbol."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_blast_radius

    try:
        if daemon:
            payload = request_session_daemon(
                path,
                {
                    "command": "blast_radius",
                    "session_id": session_id,
                    "path": path,
                    "symbol": symbol,
                    "max_depth": max_depth,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_blast_radius(
                session_id,
                symbol,
                path,
                max_depth=max_depth,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(payload["rendered_caller_tree"])


@session_app.command("blast-radius-render")
def session_blast_radius_render_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the render bundle."
    ),
    max_sources: int = typer.Option(
        5, "--max-sources", min=1, help="Maximum exact source blocks to include."
    ),
    max_symbols_per_file: int = typer.Option(
        6, "--max-symbols-per-file", min=1, help="Maximum summary symbols to include per file."
    ),
    max_render_chars: int | None = typer.Option(
        None, "--max-render-chars", min=1, help="Maximum characters to emit in rendered_context."
    ),
    optimize_context: bool = typer.Option(
        False,
        "--optimize-context",
        help="Strip blank lines and comment-only lines from rendered source blocks.",
    ),
    render_profile: str = typer.Option(
        "full",
        "--render-profile",
        help="Render profile: full, compact, or llm.",
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready cached-session blast radius bundle."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_blast_radius_render

    try:
        if daemon:
            payload = request_session_daemon(
                path,
                {
                    "command": "blast_radius_render",
                    "session_id": session_id,
                    "path": path,
                    "symbol": symbol,
                    "max_depth": max_depth,
                    "max_files": max_files,
                    "max_sources": max_sources,
                    "max_symbols_per_file": max_symbols_per_file,
                    "max_render_chars": max_render_chars,
                    "optimize_context": optimize_context,
                    "render_profile": render_profile,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_blast_radius_render(
                session_id,
                symbol,
                path,
                max_depth=max_depth,
                max_files=max_files,
                max_sources=max_sources,
                max_symbols_per_file=max_symbols_per_file,
                max_render_chars=max_render_chars,
                optimize_context=optimize_context,
                render_profile=render_profile,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(payload["rendered_context"])


@session_app.command("blast-radius-plan")
def session_blast_radius_plan_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    max_depth: int = typer.Option(
        3,
        "--max-depth",
        min=0,
        help="Maximum reverse-import depth to include in the blast radius.",
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the plan."
    ),
    max_symbols: int = typer.Option(
        5, "--max-symbols", min=1, help="Maximum ranked symbols to retain in the plan payload."
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Route this request through the warm localhost session daemon.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a cached-session blast-radius planning bundle without rendered source text."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_blast_radius_plan

    try:
        if daemon:
            payload = request_session_daemon(
                path,
                {
                    "command": "blast_radius_plan",
                    "session_id": session_id,
                    "path": path,
                    "symbol": symbol,
                    "max_depth": max_depth,
                    "max_files": max_files,
                    "max_symbols": max_symbols,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_blast_radius_plan(
                session_id,
                symbol,
                path,
                max_depth=max_depth,
                max_files=max_files,
                max_symbols=max_symbols,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Session blast radius plan for {payload['session_id']}")
    typer.echo(f"symbol={payload['symbol']}")
    typer.echo(
        f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
    )


@session_app.command("serve")
def session_serve(
    session_id: str = typer.Argument(..., help="Session ID to serve from cache."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    jsonl: bool = typer.Option(
        True,
        "--jsonl/--no-jsonl",
        help="Read newline-delimited JSON requests from stdin and emit JSON responses.",
    ),
    refresh_on_stale: bool = typer.Option(
        False,
        "--refresh-on-stale",
        help="Refresh the cached session once when file changes are detected, then retry the request.",
    ),
) -> None:
    """Serve repeated repo-map and symbol requests from a cached session."""
    from tensor_grep.cli.session_store import serve_session_stream

    if not jsonl:
        typer.echo("session serve currently requires --jsonl mode", err=True)
        raise typer.Exit(2)

    try:
        serve_session_stream(session_id, path, refresh_on_stale=refresh_on_stale)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@checkpoint_app.command("create")
def checkpoint_create(
    path: str = typer.Argument(".", help="File or directory rooted at the checkpoint scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Create a checkpoint for the current editable tree."""
    from tensor_grep.cli.checkpoint_store import create_checkpoint

    try:
        payload = create_checkpoint(path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload.__dict__, indent=2))
        return

    typer.echo(
        f"Created checkpoint {payload.checkpoint_id} ({payload.mode}, files={payload.file_count})"
    )


@checkpoint_app.command("list")
def checkpoint_list(
    path: str = typer.Argument(".", help="File or directory rooted at the checkpoint scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """List available checkpoints."""
    from tensor_grep.cli.checkpoint_store import list_checkpoints

    try:
        records = [record.__dict__ for record in list_checkpoints(path)]
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps({"version": 1, "checkpoints": records}, indent=2))
        return

    if not records:
        typer.echo("No checkpoints found.")
        return

    for record in records:
        typer.echo(
            f"{record['checkpoint_id']}  {record['mode']}  "
            f"{record['created_at']}  files={record['file_count']}"
        )


@checkpoint_app.command("undo")
def checkpoint_undo(
    checkpoint_id: str = typer.Argument(..., help="Checkpoint ID to restore."),
    path: str = typer.Argument(".", help="File or directory rooted at the checkpoint scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Restore a checkpoint."""
    from tensor_grep.cli.checkpoint_store import undo_checkpoint

    try:
        payload = undo_checkpoint(checkpoint_id, path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload.__dict__, indent=2))
        return

    typer.echo(
        f"Restored checkpoint {payload.checkpoint_id} "
        f"({payload.mode}, restored_files={payload.restored_files}, removed_paths={payload.removed_paths})"
    )


@app.command()
def classify(
    file_path: str, format_type: str = typer.Option("json", "--format", help="Output format")
) -> None:
    """Run semantic log classification via cyBERT."""
    import json
    import re

    from tensor_grep.backends.cybert_backend import CybertBackend
    from tensor_grep.io.reader_fallback import FallbackReader

    reader = FallbackReader()
    lines = list(reader.read_lines(file_path))
    if not lines:
        sys.exit(1)

    backend = CybertBackend()
    try:
        results = backend.classify(lines)
    except Exception:
        # Keep CLI usable when Triton/PyTorch is unavailable in CI or local environments.
        results = []
        for line in lines:
            if re.search(r"\berror\b|\bfail(?:ed)?\b|\bexception\b", line, re.IGNORECASE):
                results.append({"label": "error", "confidence": 0.9})
            elif re.search(r"\bwarn(?:ing)?\b", line, re.IGNORECASE):
                results.append({"label": "warn", "confidence": 0.8})
            else:
                results.append({"label": "info", "confidence": 0.7})

    if format_type == "json":
        data = {"classifications": results}
        print(json.dumps(data))
    else:
        for r in results:
            print(f"{r['label']} ({r['confidence']:.2f})")


@app.command()
def rulesets(
    json_output: bool = typer.Option(False, "--json", help="Emit structured ruleset metadata."),
) -> None:
    """List built-in security and compliance rule packs."""
    payload = _build_rulesets_payload()
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    if not payload["rulesets"]:
        typer.echo("No built-in rulesets are currently registered.")
        return

    for ruleset in cast(list[dict[str, object]], payload["rulesets"]):
        typer.echo(
            f"{ruleset['name']}: {ruleset['description']} "
            f"[category={ruleset['category']} status={ruleset['status']} "
            f"languages={','.join(cast(list[str], ruleset['languages']))} "
            f"rules={ruleset['rule_count']}]"
        )


@app.command()
def scan(
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config"
    ),
    ruleset: str | None = typer.Option(
        None,
        "--ruleset",
        help="Built-in security/compliance ruleset to scan without sgconfig.",
    ),
    inline_rules: str | None = typer.Option(
        None,
        "--inline-rules",
        help="Scan using inline ast-grep rule YAML without requiring sgconfig.",
    ),
    path: str = typer.Option(
        ".",
        "--path",
        help="Scan root when using a built-in ruleset.",
    ),
    language: str | None = typer.Option(
        None,
        "--language",
        help="Language override when using a built-in ruleset.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured scan findings.",
    ),
    baseline: str | None = typer.Option(
        None,
        "--baseline",
        help="Compare matched findings against a saved baseline fingerprint file.",
    ),
    write_baseline: str | None = typer.Option(
        None,
        "--write-baseline",
        help="Write the current matched finding fingerprints to a baseline file.",
    ),
    suppressions: str | None = typer.Option(
        None,
        "--suppressions",
        help="Mark matched findings present in a suppression fingerprint file as suppressed.",
    ),
    write_suppressions: str | None = typer.Option(
        None,
        "--write-suppressions",
        help="Write the current matched finding fingerprints to a suppression file.",
    ),
    justification: str | None = typer.Option(
        None,
        "--justification",
        help="Required justification text when writing suppressions.",
    ),
    include_evidence_snippets: bool = typer.Option(
        False,
        "--include-evidence-snippets",
        help="Attach bounded raw match snippets to structured ruleset scan evidence rows.",
    ),
    max_evidence_snippets_per_file: int = typer.Option(
        1,
        "--max-evidence-snippets-per-file",
        min=1,
        help="Maximum number of snippets to keep per matched file when snippet evidence is enabled.",
    ),
    max_evidence_snippet_chars: int = typer.Option(
        120,
        "--max-evidence-snippet-chars",
        min=1,
        help="Maximum characters to keep per evidence snippet when snippet evidence is enabled.",
    ),
) -> None:
    """Scan and rewrite code by configuration."""
    from tensor_grep.cli.rule_packs import resolve_rule_pack

    if ruleset and inline_rules:
        typer.echo("Error: --inline-rules is incompatible with --ruleset.", err=True)
        sys.exit(1)

    candidate_files: list[str] | None = None
    project_scan_fast_path = False
    if ruleset:
        try:
            ruleset_meta, rules = resolve_rule_pack(ruleset, language)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        project_cfg: dict[str, object] = {
            "config_path": f"builtin:{ruleset_meta['name']}",
            "root_dir": Path(path).resolve(),
            "rule_dirs": [],
            "test_dirs": [],
            "language": ruleset_meta["language"],
        }
        scan_banner = (
            "Scanning project using built-in ruleset "
            f"{ruleset_meta['name']} ({ruleset_meta['language']})"
        )
        routing_reason = "builtin-ruleset-scan"
    elif inline_rules is not None:
        try:
            rules = _load_inline_rule_specs(inline_rules, default_language=language)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        if not rules:
            typer.echo("Error: No valid inline rules were found.", err=True)
            sys.exit(1)
        inferred_language = language or str(rules[0]["language"])
        project_cfg = {
            "config_path": "inline-rules",
            "root_dir": Path(path).resolve(),
            "rule_dirs": [],
            "test_dirs": [],
            "language": inferred_language,
        }
        scan_banner = "Scanning project using inline AST rules"
        routing_reason = "ast-inline-rules-scan"
    else:
        from tensor_grep.cli.ast_workflows import _load_ast_project_data

        try:
            project_cfg, rules, candidate_files, _test_data, _hints = _load_ast_project_data(config)
        except (FileNotFoundError, ValueError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)

        if not rules:
            typer.echo("Error: No valid rules found in configured rule directories.", err=True)
            sys.exit(1)
        scan_banner = "Scanning project using adaptive AST routing"
        routing_reason = "ast-project-scan"
        project_scan_fast_path = True

    if not json_output:
        typer.echo(f"{scan_banner} based on {project_cfg['config_path']}...")
    try:
        payload = _run_ast_scan_payload(
            project_cfg,
            rules,
            routing_reason=routing_reason,
            candidate_files=candidate_files,
            project_scan_fast_path=project_scan_fast_path,
            ruleset_name=ruleset_meta["name"] if ruleset else None,
            baseline_path=baseline,
            write_baseline_path=write_baseline,
            suppressions_path=suppressions,
            write_suppressions_path=write_suppressions,
            suppression_justification=justification,
            include_evidence_snippets=include_evidence_snippets,
            max_evidence_snippets_per_file=max_evidence_snippets_per_file,
            max_evidence_snippet_chars=max_evidence_snippet_chars,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    for finding in cast(list[dict[str, object]], payload["findings"]):
        typer.echo(
            f"[scan] rule={finding['rule_id']} lang={finding['language']} "
            f"matches={finding['matches']} files={len(cast(list[str], finding['files']))}"
        )

    typer.echo(
        "Scan completed. "
        f"rules={payload['rule_count']} matched_rules={payload['matched_rules']} "
        f"total_matches={payload['total_matches']} "
        f"backends={','.join(cast(list[str], payload['backends'])) or 'none'}"
    )
    if payload.get("baseline"):
        baseline_summary = cast(dict[str, object], payload["baseline"])
        typer.echo(
            "Baseline compared. "
            f"new={baseline_summary['new_findings']} "
            f"existing={baseline_summary['existing_findings']} "
            f"resolved={baseline_summary['resolved_findings']}"
        )
    if payload.get("baseline_written"):
        baseline_written = cast(dict[str, object], payload["baseline_written"])
        typer.echo(
            f"Baseline written to {baseline_written['path']} (count={baseline_written['count']})."
        )
    if payload.get("suppressions"):
        suppressions_summary = cast(dict[str, object], payload["suppressions"])
        if suppressions_summary.get("path"):
            typer.echo(
                f"Suppressions applied from {suppressions_summary['path']} "
                f"(suppressed={suppressions_summary['suppressed_findings']})."
            )
        if suppressions_summary.get("inline_suppressed_findings"):
            typer.echo(
                "Inline suppressions applied "
                f"(suppressed={suppressions_summary['inline_suppressed_findings']})."
            )
        for warning in cast(list[str], suppressions_summary.get("warnings", [])):
            typer.echo(f"Warning: {warning}", err=True)
    if payload.get("suppressions_written"):
        suppressions_written = cast(dict[str, object], payload["suppressions_written"])
        typer.echo(
            f"Suppressions written to {suppressions_written['path']} "
            f"(count={suppressions_written['count']})."
        )


@app.command()
def test(
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config"
    ),
) -> None:
    """Test structural rules by configuration."""
    exit_code = ast_workflows.test_command(config)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@app.command()
def new() -> None:
    """Create a new structural search project or rules/tests scaffold."""
    import os

    import yaml

    if os.path.exists("sgconfig.yml"):
        typer.echo("Project already initialized (sgconfig.yml exists).", err=True)
        sys.exit(1)

    config_data = {
        "ruleDirs": ["rules"],
        "testDirs": ["tests"],
        "utilsDir": "utils",
        "language": "python",
    }

    with open("sgconfig.yml", "w") as f:
        yaml.dump(config_data, f)

    os.makedirs("rules", exist_ok=True)
    os.makedirs("tests", exist_ok=True)

    typer.echo("Initialized new tensor-grep structural search project.")


@app.command()
def lsp(
    provider: str = typer.Option(
        "native",
        "--provider",
        help="Semantic provider mode. native=repo-map only, lsp=external provider only, hybrid=merge both.",
    ),
) -> None:
    """Start the structural search language server.

    Examples:
      tg lsp
      tg lsp --provider native
      tg lsp --provider lsp
      tg lsp --provider hybrid

    The provider mode is also exposed to editor clients through the
    `TG_LSP_PROVIDER` environment variable.
    """
    import os

    from tensor_grep.cli.lsp_server import run_lsp

    os.environ["TG_LSP_PROVIDER"] = provider
    run_lsp()


@app.command(name="lsp-setup")
def lsp_setup(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    include_toolchain_providers: bool = typer.Option(
        False,
        "--include-toolchain-providers",
        help=(
            "Also install/copy rust-analyzer, gopls, and csharp-ls using local "
            "toolchains. Off by default to avoid mutating external toolchains during "
            "normal installs."
        ),
    ),
) -> None:
    """Install managed external LSP providers under the tensor-grep install root."""
    payload = install_managed_lsp_providers(
        python_executable=sys.executable,
        managed_root=None,
        include_toolchain_providers=include_toolchain_providers,
    )
    has_install_errors = bool(payload.get("install_errors"))
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        if has_install_errors:
            raise typer.Exit(code=1)
        return
    if has_install_errors:
        typer.echo(
            f"Managed external LSP provider setup completed with errors under {payload['managed_provider_root']}"
        )
    else:
        typer.echo(
            f"Managed external LSP provider setup complete under {payload['managed_provider_root']}"
        )
    providers = cast(dict[str, dict[str, Any]], payload["providers"])
    for language in supported_lsp_languages():
        provider = providers.get(language, {})
        command = provider.get("command") or []
        source = provider.get("command_source", "missing")
        availability = "available" if provider.get("available") else "missing"
        command_text = " ".join(str(part) for part in command) if command else "missing"
        install_error = provider.get("install_error")
        suffix = f", error={install_error}" if install_error else ""
        typer.echo(f"  {language}: {command_text} [{source}, {availability}{suffix}]")
    if has_install_errors:
        raise typer.Exit(code=1)


app.add_typer(checkpoint_app, name="checkpoint")
app.add_typer(session_app, name="session")
app.add_typer(review_bundle_app, name="review-bundle")


@app.command(name="mcp")
def mcp_server() -> None:
    """Start the Model Context Protocol (MCP) server for AI assistants"""
    from tensor_grep.cli.mcp_server import run_mcp_server

    run_mcp_server()


@app.command()
def doctor(
    path: str = typer.Argument(".", help="Workspace root to inspect."),
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    with_lsp: bool = typer.Option(
        True,
        "--with-lsp/--no-lsp",
        help="Include external LSP provider diagnostics.",
    ),
) -> None:
    """Print system, GPU, cache, and optional daemon diagnostics for AI troubleshooting."""
    payload = _build_doctor_payload(path, config=config, with_lsp=with_lsp)
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(_render_doctor_payload(payload))


@app.command()
def upgrade() -> None:
    """Upgrade tensor-grep to the latest version published on PyPI."""
    import importlib.metadata
    import subprocess
    import sys

    pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "tensor-grep"]

    def _upgrade_attempts() -> list[tuple[str, list[str]]]:
        return [
            (
                "uv",
                ["uv", "pip", "install", "--python", sys.executable, "--upgrade", "tensor-grep"],
            ),
            ("pip", pip_cmd),
        ]

    def _run_upgrade(
        attempts: list[tuple[str, list[str]]],
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        errors: list[str] = []
        for label, cmd in attempts:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return result, label
            except FileNotFoundError as e:
                errors.append(f"{label}: {e}")
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or "").strip()
                stdout = (e.stdout or "").strip()
                combined = stderr or stdout or str(e)
                errors.append(f"{label}: {combined}")
                if label == "pip" and "No module named pip" in combined:
                    try:
                        subprocess.run(
                            [sys.executable, "-m", "ensurepip", "--upgrade"],
                            capture_output=True,
                            text=True,
                            check=True,
                        )
                        result = subprocess.run(pip_cmd, capture_output=True, text=True, check=True)
                        return result, "pip+ensurepip"
                    except FileNotFoundError as ee:
                        errors.append(f"ensurepip: {ee}")
                    except subprocess.CalledProcessError as ee:
                        ee_stderr = (ee.stderr or "").strip()
                        ee_stdout = (ee.stdout or "").strip()
                        errors.append(f"ensurepip: {ee_stderr or ee_stdout or str(ee)}")
        raise RuntimeError("; ".join(errors))

    def _looks_like_windows_self_update_lock(message: str) -> bool:
        lowered = message.lower()
        return (
            "winerror 32" in lowered
            or "os error 32" in lowered
            or "being used by another process" in lowered
        )

    def _schedule_windows_self_upgrade(attempts: list[tuple[str, list[str]]]) -> Path:
        import textwrap

        helper_code = textwrap.dedent(
            """
            import json
            import subprocess
            import sys
            import time
            from pathlib import Path

            parent_pid = int(sys.argv[1])
            log_path = Path(sys.argv[2])
            attempts = json.loads(sys.argv[3])
            log_path.parent.mkdir(parents=True, exist_ok=True)

            for _ in range(300):
                try:
                    subprocess.run(
                        [
                            "powershell",
                            "-NoProfile",
                            "-Command",
                            f"Get-Process -Id {parent_pid} -ErrorAction Stop | Out-Null",
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                except subprocess.CalledProcessError:
                    break
                time.sleep(0.1)

            def _run_attempts() -> tuple[bool, str, str]:
                errors: list[str] = []
                for label, cmd in attempts:
                    try:
                        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                        output = "\\n".join(
                            part
                            for part in (
                                (result.stdout or "").strip(),
                                (result.stderr or "").strip(),
                            )
                            if part
                        )
                        return True, label, output
                    except FileNotFoundError as exc:
                        errors.append(f"{label}: {exc}")
                    except subprocess.CalledProcessError as exc:
                        stderr = (exc.stderr or "").strip()
                        stdout = (exc.stdout or "").strip()
                        combined = stderr or stdout or str(exc)
                        errors.append(f"{label}: {combined}")
                        if label == "pip" and "No module named pip" in combined:
                            try:
                                subprocess.run(
                                    [sys.executable, "-m", "ensurepip", "--upgrade"],
                                    capture_output=True,
                                    text=True,
                                    check=True,
                                )
                                result = subprocess.run(
                                    cmd,
                                    capture_output=True,
                                    text=True,
                                    check=True,
                                )
                                output = "\\n".join(
                                    part
                                    for part in (
                                        (result.stdout or "").strip(),
                                        (result.stderr or "").strip(),
                                    )
                                    if part
                                )
                                return True, "pip+ensurepip", output
                            except FileNotFoundError as ensurepip_exc:
                                errors.append(f"ensurepip: {ensurepip_exc}")
                            except subprocess.CalledProcessError as ensurepip_exc:
                                ensure_stderr = (ensurepip_exc.stderr or "").strip()
                                ensure_stdout = (ensurepip_exc.stdout or "").strip()
                                errors.append(
                                    f"ensurepip: {ensure_stderr or ensure_stdout or str(ensurepip_exc)}"
                                )
                return False, "", "; ".join(errors)

            ok, method, payload = _run_attempts()
            if ok:
                text = "Scheduled tensor-grep upgrade completed via " + method + "."
                if payload:
                    text += "\\n" + payload
                log_path.write_text(text, encoding="utf-8")
                raise SystemExit(0)

            log_path.write_text(
                "Scheduled tensor-grep upgrade failed.\\n" + payload,
                encoding="utf-8",
            )
            raise SystemExit(1)
            """
        ).strip()

        log_path = Path.home() / ".tensor-grep" / "logs" / f"upgrade-{uuid4().hex}.log"
        creationflags = 0
        for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
            creationflags |= int(getattr(subprocess, flag_name, 0))
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                helper_code,
                str(os.getpid()),
                str(log_path),
                json.dumps(attempts),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        return log_path

    def _installed_version() -> str | None:
        try:
            return importlib.metadata.version("tensor-grep")
        except importlib.metadata.PackageNotFoundError:
            return None

    typer.echo("Upgrading tensor-grep to the latest version...")

    try:
        attempts = _upgrade_attempts()
        previous_version = _installed_version()
        result, method = _run_upgrade(attempts)
        current_version = _installed_version()
        output = "\n".join(
            part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part
        )
        if current_version is not None and current_version == previous_version:
            typer.echo(f"tensor-grep is already at the latest PyPI version ({current_version}).")
        elif "Requirement already satisfied" in output:
            typer.echo("tensor-grep is already up to date!")
        else:
            typer.echo(f"Successfully upgraded tensor-grep via {method}!")
            if output:
                typer.echo(output)

    except RuntimeError as e:
        if _looks_like_windows_self_update_lock(str(e)):
            log_path = _schedule_windows_self_upgrade(_upgrade_attempts())
            typer.echo(
                "Windows is still using tg.exe, so the upgrade was scheduled in the background."
            )
            typer.echo("Wait a few seconds, then run `tg --version` again.")
            typer.echo(f"Upgrade log: {log_path}")
            return
        typer.echo("Error occurred while upgrading tensor-grep.", err=True)
        typer.echo(str(e), err=True)
        sys.exit(1)


def _audit_diff_error_payload(message: str, *, code: str) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "routing_backend": "AuditManifest",
        "routing_reason": "audit-manifest-diff",
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


def _audit_history_error_payload(message: str, *, code: str) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "routing_backend": "AuditManifest",
        "routing_reason": "audit-manifest-history",
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


def _review_bundle_error_payload(
    message: str, *, code: str, routing_reason: str
) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "routing_backend": "AuditManifest",
        "routing_reason": routing_reason,
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


@app.command(name="audit-verify")
def audit_verify(
    manifest_path: str = typer.Argument(..., help="Path to the rewrite audit manifest JSON file."),
    signing_key: str | None = typer.Option(
        None,
        "--signing-key",
        help="Optional HMAC signing key path for signed manifests.",
    ),
    previous_manifest: str | None = typer.Option(
        None,
        "--previous-manifest",
        help="Optional previous manifest path for validating manifest chaining.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON verification output.",
    ),
) -> None:
    """Verify a rewrite audit manifest digest, chain, and optional signature."""
    from tensor_grep.cli.audit_manifest import (
        verify_audit_manifest,
        verify_audit_manifest_json,
    )

    try:
        if json_output:
            typer.echo(
                verify_audit_manifest_json(
                    manifest_path,
                    signing_key=signing_key,
                    previous_manifest=previous_manifest,
                )
            )
            return

        payload = verify_audit_manifest(
            manifest_path,
            signing_key=signing_key,
            previous_manifest=previous_manifest,
        )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Manifest: {payload['manifest_path']}")
    typer.echo(f"valid={payload['valid']}")
    checks = payload["checks"]
    typer.echo(
        "checks="
        f"digest:{checks['digest_valid']} "
        f"chain:{checks['chain_valid']} "
        f"signature:{checks['signature_valid']}"
    )
    for error in payload["errors"]:
        typer.echo(f"- {error}")
    if not payload["valid"]:
        raise typer.Exit(code=1)


@app.command(name="audit-history")
def audit_history(
    path: str = typer.Argument(".", help="Project root to inspect for audit manifests."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON history output.",
    ),
) -> None:
    """List known audit manifests in newest-first chain order."""
    from tensor_grep.cli.audit_manifest import list_audit_history, list_audit_history_payload

    try:
        if json_output:
            typer.echo(json.dumps(list_audit_history_payload(path), indent=2))
            return
        payload = list_audit_history(path)
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_history_error_payload(str(exc), code="not_found"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_history_error_payload(str(exc), code="invalid_input"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_history_error_payload(str(exc), code="internal_error"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    for entry in payload:
        annotations: list[str] = []
        if entry["missing_timestamp"]:
            annotations.append("missing_timestamp")
        if entry["chain_gap"]:
            annotations.append("chain_gap")
        if entry["signature_kind"] is not None:
            annotations.append(f"signature={entry['signature_kind']}")
        created_at = entry["created_at"] or "<missing>"
        suffix = f" [{' '.join(annotations)}]" if annotations else ""
        typer.echo(f"{created_at}  {entry['manifest_sha256']}  {entry['file_path']}{suffix}")


@app.command(name="audit-diff")
def audit_diff(
    previous_manifest: str = typer.Argument(
        ..., help="Path to the previous audit manifest JSON file."
    ),
    current_manifest: str = typer.Argument(
        ..., help="Path to the current audit manifest JSON file."
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured JSON diff output.",
    ),
) -> None:
    """Compute a semantic diff between two audit manifests."""
    from tensor_grep.cli.audit_manifest import diff_audit_manifests, diff_audit_manifests_payload

    try:
        if json_output:
            typer.echo(
                json.dumps(
                    diff_audit_manifests_payload(previous_manifest, current_manifest), indent=2
                )
            )
            return
        payload = diff_audit_manifests(previous_manifest, current_manifest)
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(json.dumps(_audit_diff_error_payload(str(exc), code="not_found"), indent=2))
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(_audit_diff_error_payload(str(exc), code="invalid_json"), indent=2)
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _audit_diff_error_payload(str(exc), code="internal_error"),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Audit diff: {previous_manifest} -> {current_manifest}")
    for section_name in ("added", "removed", "changed"):
        typer.echo(f"{section_name.capitalize()}:")
        section = payload[section_name]
        if not section:
            typer.echo("  (none)")
            continue
        for key, value in section.items():
            if section_name == "changed":
                typer.echo(f"  {key}:")
                typer.echo(f"    old: {json.dumps(value['old'], sort_keys=True)}")
                typer.echo(f"    new: {json.dumps(value['new'], sort_keys=True)}")
                continue
            typer.echo(f"  {key}: {json.dumps(value, sort_keys=True)}")


@review_bundle_app.command("create")
def review_bundle_create(
    manifest_path: str = typer.Option(
        ...,
        "--manifest",
        help="Path to the rewrite audit manifest JSON file.",
    ),
    scan_path: str | None = typer.Option(
        None,
        "--scan",
        help="Optional path to the ruleset scan JSON file.",
    ),
    checkpoint_id: str | None = typer.Option(
        None,
        "--checkpoint-id",
        help="Optional checkpoint ID to include in the bundle.",
    ),
    previous_manifest: str | None = typer.Option(
        None,
        "--previous-manifest",
        help="Optional previous audit manifest JSON for diff generation.",
    ),
    output_path: str | None = typer.Option(
        None,
        "--output",
        help="Optional file path where the review bundle JSON should be written.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the review bundle as structured JSON.",
    ),
) -> None:
    """Create a review bundle for enterprise change review."""
    from tensor_grep.cli.audit_manifest import create_review_bundle, create_review_bundle_json

    try:
        if json_output:
            typer.echo(
                create_review_bundle_json(
                    manifest_path,
                    scan_path=scan_path,
                    checkpoint_id=checkpoint_id,
                    previous_manifest=previous_manifest,
                    output_path=output_path,
                )
            )
            return
        payload = create_review_bundle(
            manifest_path,
            scan_path=scan_path,
            checkpoint_id=checkpoint_id,
            previous_manifest=previous_manifest,
            output_path=output_path,
        )
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="not_found",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="invalid_json",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="internal_error",
                        routing_reason="review-bundle-create",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    included_components = [
        component
        for component in (
            "audit_manifest",
            "scan_results",
            "checkpoint_metadata",
            "diff",
        )
        if payload[component] is not None
    ]
    target = output_path or "<not written>"
    typer.echo(
        f"Created review bundle {target} "
        f"(components={','.join(included_components)}, bundle_sha256={payload['bundle_sha256']})"
    )


@review_bundle_app.command("verify")
def review_bundle_verify(
    bundle_path: str = typer.Argument(..., help="Path to the review bundle JSON file."),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit structured verification JSON.",
    ),
) -> None:
    """Verify review bundle integrity and component checksums."""
    from tensor_grep.cli.audit_manifest import verify_review_bundle, verify_review_bundle_json

    try:
        if json_output:
            typer.echo(verify_review_bundle_json(bundle_path))
            return
        payload = verify_review_bundle(bundle_path)
    except FileNotFoundError as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="not_found",
                        routing_reason="review-bundle-verify",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="invalid_json",
                        routing_reason="review-bundle-verify",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _review_bundle_error_payload(
                        str(exc),
                        code="internal_error",
                        routing_reason="review-bundle-verify",
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Review bundle: {payload['bundle_path']}")
    typer.echo(f"valid={payload['valid']}")
    for component, check in cast(dict[str, dict[str, object]], payload["checks"]).items():
        typer.echo(
            f"{component}: valid={check['valid']} "
            f"expected={check['expected']} actual={check['actual']}"
        )
    bundle_integrity = cast(dict[str, object], payload["bundle_integrity"])
    typer.echo(
        "bundle_integrity="
        f"{bundle_integrity['valid']} "
        f"expected={bundle_integrity['expected']} actual={bundle_integrity['actual']}"
    )
    if not payload["valid"]:
        raise typer.Exit(code=1)


@app.command("update")
def update() -> None:
    """Alias for upgrade."""
    upgrade()


@app.command(name="ast-info")
def ast_info() -> None:
    """List supported AST languages and grammars."""
    from tensor_grep.backends.ast_backend import get_supported_languages

    typer.echo("Supported AST Languages:")
    for lang in get_supported_languages():
        typer.echo(f"- {lang}")


@app.command(
    name="run",
    help="Run AST structural search and optional rewrites.",
)
def run(
    pattern: str = typer.Argument(..., help="The AST pattern to search for."),
    path: str | None = typer.Argument(None, help="The path to search in."),
    rewrite: str | None = typer.Option(None, "--rewrite", "-r", help="Replacement pattern."),
    lang: str | None = typer.Option(None, "--lang", "-l", help="Language for AST parsing."),
    apply: bool = typer.Option(False, "--apply", help="Apply the rewrite to files."),
    verify: bool = typer.Option(False, "--verify", help="Verify the rewrite with tests."),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
    checkpoint: bool = typer.Option(False, "--checkpoint", help="Enable edit checkpoints."),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Start interactive edit session"
    ),
    filter_regex: str | None = typer.Option(
        None, "--filter", help="Filter matched AST nodes by text regex"
    ),
) -> None:
    from tensor_grep.cli.ast_workflows import run_command as execute_run

    exit_code = execute_run(
        pattern=pattern,
        path=path,
        rewrite=rewrite,
        lang=lang,
        apply=apply,
        verify=verify,
        json_mode=json_output,
        checkpoint=checkpoint,
        interactive=interactive,
        filter_regex=filter_regex,
    )
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@app.command(hidden=True)
def worker(
    port: int | None = typer.Option(None, "--port", help="Port to bind the TCP worker."),
    stop: bool = typer.Option(False, "--stop", help="Stop the active resident worker."),
) -> None:
    """Internal command to manage the experimental Resident AST Worker."""
    native_tg_binary = resolve_native_tg_binary()
    if native_tg_binary is None:
        typer.echo("Error: native tg binary not found for worker command.", err=True)
        raise typer.Exit(2)

    cmd = [str(native_tg_binary), "worker"]
    if port is not None:
        cmd.extend(["--port", str(port)])
    if stop:
        cmd.append("--stop")

    completed = subprocess.run(cmd, check=False)
    raise typer.Exit(int(completed.returncode))


def main_entry() -> None:
    import sys

    # Emulate ripgrep's top-level help behavior and transparent drop-in compatibility.
    # Typer requires an explicit subcommand (like `tg search pattern`).
    # To act exactly like ripgrep (`rg pattern`), we dynamically inject the `search`
    # subcommand into sys.argv if the user didn't provide any recognized subcommand.

    # Check for version flag first
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V", "--pcre2-version"):
        first_arg = sys.argv[1]

        try:
            from importlib.metadata import version

            pkg_version = version("tensor-grep")
        except Exception:
            pkg_version = _read_project_version_fallback()

        if first_arg == "--pcre2-version":
            candidates = [resolve_native_tg_binary(), resolve_ripgrep_binary()]
            last_completed: subprocess.CompletedProcess[str] | None = None
            for candidate in candidates:
                if not candidate or not candidate.exists():
                    continue
                completed = subprocess.run(
                    [str(candidate), "--pcre2-version"], capture_output=True, text=True
                )
                last_completed = completed
                if completed.returncode == 0:
                    print(completed.stdout.strip())
                    sys.exit(0)
            if last_completed is not None:
                output = last_completed.stderr.strip() or last_completed.stdout.strip()
                if output:
                    print(output, file=sys.stderr)
                sys.exit(last_completed.returncode or 1)
            print(
                "PCRE2 version unavailable: no native tg or ripgrep binary found.",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"tensor-grep {pkg_version}")
        print()
        print("features:+gpu-cudf,+gpu-torch,+rust-core")
        print("simd(compile):+SSE2,-SSSE3,-AVX2")
        print("simd(runtime):+SSE2,+SSSE3,+AVX2")
        print()
        print("Arrow Zero-Copy IPC is available")
        sys.exit(0)

    from tensor_grep.cli.commands import KNOWN_COMMANDS as _KNOWN_COMMANDS

    known_commands = _KNOWN_COMMANDS

    if len(sys.argv) == 1:
        app(args=["--help"])
        return

    if len(sys.argv) > 1:
        first_arg = sys.argv[1]
        if (
            first_arg not in ("--help", "-h")
            and first_arg not in known_commands
            and not first_arg.startswith("--typer-")
        ):
            sys.argv.insert(1, "search")

    app()


if __name__ == "__main__":
    main_entry()
