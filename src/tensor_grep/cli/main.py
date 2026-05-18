import dataclasses
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import nullcontext
from dataclasses import dataclass, replace
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

from tensor_grep.backends.ast_backend import is_native_ast_language, normalize_ast_language
from tensor_grep.cli import ast_workflows
from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.cli.lsp_provider_setup import (
    install_managed_lsp_providers,
    supported_lsp_languages,
)
from tensor_grep.cli.runtime_paths import (
    _native_tg_version,
    _native_tg_version_matches,
    env_flag_enabled,
    iter_in_tree_native_tg_binaries,
    resolve_native_tg_binary,
    resolve_ripgrep_binary,
)
from tensor_grep.core.observability import nvtx_range
from tensor_grep.core.result import MatchLine
from tensor_grep.sidecar import DEFAULT_CLASSIFY_MAX_LINES

if TYPE_CHECKING:
    from tensor_grep.backends.base import ComputeBackend
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.io.directory_scanner import DirectoryScanner

_DEFAULT_AGENT_REPO_SCAN_LIMIT = 512
_DEFAULT_BLAST_RADIUS_JSON_MAX_CALLERS = 25
_DEFAULT_BLAST_RADIUS_JSON_MAX_FILES = 25
_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS = 1.0
_GUARDED_BROAD_SEARCH_ROOTS = {".claude", ".claude/context"}
_BROAD_GENERATED_SCAN_DIR_NAMES = {
    "__pycache__",
    ".claude",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
_GUARDED_BROAD_ROOT_RG_GLOBS = (
    "!context/**",
    "!**/context/**",
    "!node_modules/**",
    "!**/node_modules/**",
    "!__pycache__/**",
    "!**/__pycache__/**",
    "!dist/**",
    "!**/dist/**",
    "!build/**",
    "!**/build/**",
)
_BUILTIN_TYPE_LIST = (
    "asm: *.asm, *.s, *.S",
    "c: *.c, *.h",
    "cpp: *.cc, *.cpp, *.cxx, *.hpp, *.hh, *.hxx",
    "csharp: *.cs",
    "css: *.css",
    "go: *.go",
    "html: *.htm, *.html",
    "java: *.java",
    "javascript: *.js, *.jsx, *.mjs, *.cjs",
    "json: *.json, *.jsonl",
    "kotlin: *.kt, *.kts",
    "lua: *.lua",
    "markdown: *.md, *.markdown",
    "php: *.php",
    "python: *.py, *.pyi",
    "rust: *.rs",
    "swift: *.swift",
    "toml: *.toml",
    "typescript: *.ts, *.tsx",
    "yaml: *.yml, *.yaml",
)

app = typer.Typer(
    help="""tensor-grep (tg) - Fast text, AST, indexed, and GPU-aware search CLI

Search code and large datasets with ripgrep-compatible text search, native AST search/rewrite,
persisted repeated-query acceleration, and optional GPU routing.

**Common usage**
- `tg PATTERN [PATH ...]`
- `tg search [OPTIONS] PATTERN [PATH ...]`
- `tg run PATTERN [PATH]`
- `tg agent PATH --query "change invoice tax"`
- `tg scan --config sgconfig.yml`
- `tg doctor --with-lsp`
- `tg dogfood --output artifacts/agent_readiness.json`
- `tg repair-launcher --allow-foreign-rename`
- `tg mcp`

**AI workflows**
- `tg map PATH`
- `tg context-render PATH --query "invoice flow"`
- `tg edit-plan PATH --query "add retry with tests"`
- `tg agent PATH --query "change behavior" --json`
- `tg blast-radius-render PATH --symbol create_invoice`
- `tg session open PATH`
- `tg session daemon start PATH`

**Agent contracts**
- `tg agent` emits primary targets, alternative targets, snippets, validation_commands, rollback metadata, confidence, optional gpu_acceleration route evidence, and ask-before-editing guidance.
- `tg agent --gpu-device-ids 0,1 --json` runs an opt-in native GPU evidence scan; sidecar-routed GPU results are reported as unsupported.
- `context-render` and `edit-plan` also expose top-level validation_commands.
- Validation command templates can quote `$file` or `{file}` placeholders; applied rewrites run placeholder commands once per edited file.

**Search and safety**
- Use `--format rg --sort path` for deterministic ripgrep-shaped text output.
- The search surface is a validated common rg-compatible subset, not a full ripgrep replacement.
- Use `--format rg --json` for ripgrep JSON Lines events; plain `--json` is tensor-grep aggregate JSON.
- Broad generated-root scans are refused unless scoped with paths, `--glob`, `--type`, `--max-depth`, or explicit `--allow-broad-generated-scan`.
- `--smart-case`, `--hidden`, `--max-depth`, and `--text` are honored by structured CPU and sidecar search; native GPU falls back when a requested switch changes semantics it cannot safely execute yet.
- `--gpu-device-ids` pins selected GPUs for explicit search, benchmark, and agent evidence probes; GPU remains experimental until 1GB/5GB correctness and speed beat both `rg` and `tg_cpu`.
- `classify` is local by default; set `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` to opt into CyBERT/Triton.

**Notes**
- Bare patterns are treated as `tg search`.
- Use `tg search --help` for the current validated rg-compatible flag subset.
- `tg run --help` for AST rewrite flags.
- Lexical repo-map retrieval bridges camelCase, snake_case, and source-term planning queries.
- Use `tg doctor --json` for system, GPU, cache, daemon, and launcher diagnostics including path_tg_first_launcher_kind and fresh_shell_path_tg_first_launcher_kind.
- Use `tg repair-launcher --allow-foreign-rename` only when Windows Python subprocess resolution is blocked by a foreign `tg.exe` that you own and want tensor-grep to back up.
- Use `tg session --help` for cached edit-loop and daemon commands.

**Environment overrides**
- `TG_SIDECAR_PYTHON`: Path to the Python executable used for sidecar-backed commands.
- `TG_NATIVE_TG_BINARY`: Path to the native front door used by Python-backed commands.
- `TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR`: Set to `nvidia` to prefer NVIDIA release-native front-door assets, with CPU fallback.
- `TG_RG_PATH`: Path to the ripgrep executable used for text-search passthrough.
- `TG_FORCE_CPU`: Force CPU routing for search commands.
- `TG_SIDECAR_TIMEOUT_MS`: Timeout for sidecar-backed commands.
- `TENSOR_GREP_DEVICE_IDS`: Comma-separated GPU IDs available to tensor-grep.
- `TENSOR_GREP_CLASSIFY_PROVIDER`: Set to `cybert` to opt into CyBERT/Triton classification.
- `TENSOR_GREP_TRITON_TIMEOUT_SECONDS`: Timeout for Triton-backed NLP probes.
- `TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS`: Total per-command budget for optional external LSP provider requests before native fallback.""",
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


def _cli_package_version() -> str:
    try:
        from importlib.metadata import version

        return version("tensor-grep")
    except Exception:
        return _read_project_version_fallback()


_PYPI_JSON_URL = "https://pypi.org/pypi/tensor-grep/json"
_PYPI_SIMPLE_URL = "https://pypi.org/simple/tensor-grep/"
_PYPI_SIMPLE_VERSION_RE = re.compile(
    r"tensor[-_]?grep-([0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc|dev|post)[0-9]+)?)",
    re.IGNORECASE,
)
_PYPI_SIMPLE_ANCHOR_RE = re.compile(
    r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_NATIVE_FRONTDOOR_FLAVOR_ENV = "TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR"
_NATIVE_FRONTDOOR_REQUESTED_FLAVOR_ENV = "TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR"


@dataclass(frozen=True)
class _NativeFrontdoorAssetCandidate:
    flavor: str
    asset_name: str


@dataclass(frozen=True)
class _NativeFrontdoorInstallResult:
    url: str
    flavor: str


def _version_sort_key(version: str) -> tuple[tuple[int, int | str], ...]:
    parts = re.findall(r"\d+|[A-Za-z]+", version)
    key: list[tuple[int, int | str]] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return tuple(key)


def _is_version_newer(candidate: str, current: str) -> bool:
    return _version_sort_key(candidate) > _version_sort_key(current)


def _highest_tensor_grep_version(versions: list[str]) -> str | None:
    normalized = sorted({version.strip() for version in versions if version.strip()})
    if not normalized:
        return None
    stable_versions = [
        version for version in normalized if re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version)
    ]
    return max(stable_versions or normalized, key=_version_sort_key)


def _candidate_versions_from_pypi_json(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    candidates: list[str] = []
    releases = payload.get("releases")
    if isinstance(releases, dict):
        for version, release_files in releases.items():
            if not isinstance(version, str):
                continue
            if isinstance(release_files, list):
                if not release_files:
                    continue
                if all(
                    isinstance(file_payload, dict) and file_payload.get("yanked") is True
                    for file_payload in release_files
                ):
                    continue
            candidates.append(version)

    info = payload.get("info")
    info_version = info.get("version") if isinstance(info, dict) else None
    if isinstance(info_version, str) and info_version not in candidates:
        release_files = releases.get(info_version) if isinstance(releases, dict) else None
        if not isinstance(release_files, list) or any(
            not (isinstance(file_payload, dict) and file_payload.get("yanked") is True)
            for file_payload in release_files
        ):
            candidates.append(info_version)
    return candidates


def _candidate_versions_from_pypi_simple_index(simple_index: str) -> list[str]:
    candidates: list[str] = []
    for match in _PYPI_SIMPLE_ANCHOR_RE.finditer(simple_index):
        attrs = match.group("attrs")
        if re.search(r"(?:^|\s)data-yanked(?:\s|=|$)", attrs, re.IGNORECASE):
            continue
        body = re.sub(r"<[^>]+>", "", match.group("body"))
        candidates.extend(_PYPI_SIMPLE_VERSION_RE.findall(html.unescape(body)))
    return candidates


def _latest_pypi_tensor_grep_version(timeout_seconds: float = 15.0) -> str | None:
    """Best-effort latest-version probe that avoids trusting one stale PyPI cache surface."""
    import urllib.request

    candidates: list[str] = []
    headers = {
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": f"tensor-grep/{_cli_package_version()}",
    }

    try:
        request = urllib.request.Request(_PYPI_JSON_URL, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        candidates.extend(_candidate_versions_from_pypi_json(payload))
    except Exception:
        pass

    try:
        request = urllib.request.Request(_PYPI_SIMPLE_URL, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            simple_index = response.read().decode("utf-8", errors="replace")
        candidates.extend(_candidate_versions_from_pypi_simple_index(simple_index))
    except Exception:
        pass

    return _highest_tensor_grep_version(candidates)


def _verify_target_python_tensor_grep_version(python_executable: str) -> str:
    probe_code = (
        "import importlib.metadata as m; import tensor_grep; print(m.version('tensor-grep'))"
    )
    try:
        result = subprocess.run(
            [python_executable, "-c", probe_code],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"post-upgrade verification failed: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        combined = stderr or stdout or str(exc)
        raise RuntimeError(f"post-upgrade verification failed: {combined}") from exc

    version = (result.stdout or "").strip().splitlines()
    if not version:
        raise RuntimeError("post-upgrade verification failed: no tensor-grep version reported")
    return version[-1].strip()


def _normalize_native_frontdoor_flavor(raw: str | None) -> str | None:
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"nvidia", "cuda"}:
        return "nvidia"
    if normalized == "cpu":
        return "cpu"
    return None


def _requested_native_frontdoor_flavor() -> str:
    for env_name in (
        _NATIVE_FRONTDOOR_FLAVOR_ENV,
        _NATIVE_FRONTDOOR_REQUESTED_FLAVOR_ENV,
    ):
        flavor = _normalize_native_frontdoor_flavor(os.environ.get(env_name))
        if flavor is not None:
            return flavor
    return "cpu"


def _native_frontdoor_asset_candidates() -> list[_NativeFrontdoorAssetCandidate]:
    import platform

    machine = platform.machine().lower()
    if machine not in {"amd64", "x86_64"}:
        return []
    cpu_asset_name: str | None = None
    nvidia_asset_name: str | None = None
    if sys.platform.startswith("win"):
        cpu_asset_name = "tg-windows-amd64-cpu.exe"
        nvidia_asset_name = "tg-windows-amd64-nvidia.exe"
    elif sys.platform.startswith("linux"):
        cpu_asset_name = "tg-linux-amd64-cpu"
        nvidia_asset_name = "tg-linux-amd64-nvidia"
    elif sys.platform.startswith("darwin"):
        cpu_asset_name = "tg-macos-amd64-cpu"

    candidates: list[_NativeFrontdoorAssetCandidate] = []
    if _requested_native_frontdoor_flavor() == "nvidia" and nvidia_asset_name is not None:
        candidates.append(
            _NativeFrontdoorAssetCandidate(
                flavor="nvidia",
                asset_name=nvidia_asset_name,
            )
        )
    if cpu_asset_name is not None:
        candidates.append(_NativeFrontdoorAssetCandidate(flavor="cpu", asset_name=cpu_asset_name))
    return candidates


def _native_frontdoor_asset_name() -> str | None:
    candidates = _native_frontdoor_asset_candidates()
    return candidates[0].asset_name if candidates else None


def _native_frontdoor_download_candidates(
    version: str,
) -> list[tuple[_NativeFrontdoorAssetCandidate, str]]:
    return [
        (
            candidate,
            "https://github.com/oimiragieo/tensor-grep/releases/download/"
            f"v{version}/{candidate.asset_name}",
        )
        for candidate in _native_frontdoor_asset_candidates()
    ]


def _native_frontdoor_download_url(version: str) -> str | None:
    candidates = _native_frontdoor_download_candidates(version)
    if not candidates:
        return None
    return candidates[0][1]


def _managed_native_frontdoor_path_from_env() -> Path | None:
    native_env = os.environ.get("TG_NATIVE_TG_BINARY")
    sidecar_env = os.environ.get("TG_SIDECAR_PYTHON") or sys.executable
    if not sidecar_env:
        return None

    sidecar_python = Path(sidecar_env).expanduser()
    if sidecar_python.parent.name.lower() not in {"scripts", "bin"}:
        return None
    venv_root = sidecar_python.parent.parent
    if venv_root.name != ".venv":
        return None
    install_root = venv_root.parent
    if install_root.name != ".tensor-grep":
        return None
    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg-native"
    expected_native_path = install_root / "bin" / binary_name
    native_path = Path(native_env).expanduser() if native_env else expected_native_path
    try:
        native_parent = native_path.parent.resolve()
        expected_parent = expected_native_path.parent.resolve()
    except OSError:
        return expected_native_path
    if native_parent != expected_parent:
        return expected_native_path
    return native_path


def _download_native_frontdoor_asset(url: str, destination: Path) -> None:
    import urllib.request

    urllib.request.urlretrieve(url, destination)


def _install_release_native_frontdoor(
    version: str, destination: Path
) -> _NativeFrontdoorInstallResult:
    candidates = _native_frontdoor_download_candidates(version)
    if not candidates:
        raise RuntimeError("no release-native front-door asset is available for this platform")

    destination.parent.mkdir(parents=True, exist_ok=True)
    download_errors: list[str] = []
    for candidate, url in candidates:
        temp_path = destination.with_name(f"{destination.name}.{uuid4().hex}.tmp")
        try:
            try:
                _download_native_frontdoor_asset(url, temp_path)
            except Exception as exc:
                download_errors.append(f"{candidate.flavor} asset unavailable: {exc}")
                continue
            if not sys.platform.startswith("win"):
                temp_path.chmod(0o755)
            temp_version = _native_tg_version(temp_path)
            if not _native_tg_version_matches(version, temp_version):
                download_errors.append(
                    f"{candidate.flavor} asset failed smoke test: downloaded native tg "
                    f"front door reported {temp_version or 'no version'} instead of {version}"
                )
                continue
            previous_bytes = destination.read_bytes() if destination.exists() else None
            os.replace(temp_path, destination)
            installed_version = _native_tg_version(destination)
            if not _native_tg_version_matches(version, installed_version):
                if previous_bytes is not None:
                    destination.write_bytes(previous_bytes)
                else:
                    destination.unlink(missing_ok=True)
                download_errors.append(
                    f"{candidate.flavor} asset failed install verification: installed native "
                    f"tg front door reported {installed_version or 'no version'} instead of {version}"
                )
                continue
            return _NativeFrontdoorInstallResult(url=url, flavor=candidate.flavor)
        finally:
            temp_path.unlink(missing_ok=True)

    raise RuntimeError(
        "release-native front-door asset install failed: " + "; ".join(download_errors)
    )


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


_WINDOWS_EXE_BRIDGE_MARKER = "tg.exe.tensor-grep-bridge"
_WINDOWS_EXE_BRIDGE_MARKER_CONTENT = "tensor-grep managed tg.exe bridge\n"


def _windows_exe_bridge_marker_path(path: Path) -> Path:
    return path.with_name(_WINDOWS_EXE_BRIDGE_MARKER)


def _write_windows_exe_bridge_marker(path: Path) -> None:
    if path.name.lower() == "tg.exe":
        _windows_exe_bridge_marker_path(path).write_text(
            _WINDOWS_EXE_BRIDGE_MARKER_CONTENT,
            encoding="ascii",
        )


def _windows_managed_compat_shim_dirs() -> set[str]:
    if not sys.platform.startswith("win"):
        return set()
    homes: list[Path] = []
    for env_name in ("USERPROFILE", "HOME"):
        value = os.environ.get(env_name)
        if not value:
            continue
        home = Path(value)
        if home not in homes:
            homes.append(home)
    dirs: set[str] = set()
    for home in homes:
        dirs.add(_windows_path_part_key(str(home / "bin")))
        dirs.add(_windows_path_part_key(str(home / ".local" / "bin")))
    return dirs


def _windows_stale_tensor_grep_com_bridges(expected_version: str, native_path: Path) -> list[Path]:
    if not sys.platform.startswith("win"):
        return []

    def _add_path(path: Path) -> None:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        bridges.append(path)

    def _directory_has_tensor_grep_shim(directory: Path) -> bool:
        for shim_name in ("tg.cmd", "tg.ps1", "tg"):
            shim_path = directory / shim_name
            if not shim_path.is_file():
                continue
            version = _doctor_tg_candidate_version(shim_path)
            if _doctor_tg_version_looks_like_tensor_grep(version):
                return True
        return False

    path_values = [os.environ.get("PATH", "")]
    fresh_path = _doctor_fresh_shell_path_value()
    if fresh_path and fresh_path not in path_values:
        path_values.append(fresh_path)

    managed_compat_dirs = _windows_managed_compat_shim_dirs()
    bridges: list[Path] = []
    seen: set[str] = set()
    for path_value in path_values:
        for candidate in _doctor_path_tg_candidates(path_value):
            candidate_path = Path(str(candidate.get("path") or ""))
            candidate_name = candidate_path.name.lower()
            if candidate_name not in {"tg.com", "tg.exe"}:
                continue
            if _same_path(candidate_path, native_path):
                continue
            version = candidate.get("version")
            if not _doctor_tg_version_looks_like_tensor_grep(version):
                continue
            if candidate_name == "tg.exe" and not str(version).strip().lower().startswith("tg "):
                continue
            if _native_tg_version_matches(expected_version, version):
                continue
            _add_path(candidate_path)

        for entry in path_value.split(_doctor_path_list_separator(path_value)):
            if not entry:
                continue
            directory = Path(entry)
            if _windows_path_part_key(str(directory)) not in managed_compat_dirs:
                continue
            target = directory / "tg.exe"
            if _same_path(target, native_path) or target.exists():
                continue
            if _directory_has_tensor_grep_shim(directory):
                _add_path(target)
    return bridges


def _refresh_windows_tensor_grep_com_bridges(
    expected_version: str,
    native_path: Path,
    bridge_paths: list[Path] | None = None,
) -> list[Path]:
    if not sys.platform.startswith("win"):
        return []
    paths = bridge_paths
    if paths is None:
        paths = _windows_stale_tensor_grep_com_bridges(expected_version, native_path)

    refreshed: list[Path] = []
    for bridge_path in paths:
        shutil.copy2(native_path, bridge_path)
        _write_windows_exe_bridge_marker(bridge_path)
        installed_version = _native_tg_version(bridge_path)
        if not _native_tg_version_matches(expected_version, installed_version):
            raise RuntimeError(
                "refreshed PATH tg.com bridge reported "
                f"{installed_version or 'no version'} instead of {expected_version}: "
                f"{bridge_path}"
            )
        refreshed.append(bridge_path)
    return refreshed


def _refreshed_com_bridge_message(expected_version: str, paths: list[Path]) -> str | None:
    if not paths:
        return None
    names = {path.name.lower() for path in paths}
    if names == {"tg.com"}:
        subject = f"PATH tg.com {'bridge' if len(paths) == 1 else 'bridges'}"
    elif names == {"tg.exe"}:
        subject = f"PATH tg.exe front-door {'copy' if len(paths) == 1 else 'copies'}"
    else:
        subject = f"PATH tensor-grep front-door {'copy' if len(paths) == 1 else 'copies'}"
    rendered_paths = "\n".join(f"- {path}" for path in paths)
    return f"Refreshed {len(paths)} {subject} to {expected_version}.\n{rendered_paths}"


def _windows_path_parts(path_value: str | None) -> list[str]:
    if not path_value:
        return []
    return [part.strip() for part in path_value.split(";") if part.strip()]


def _windows_path_part_key(path_value: str) -> str:
    normalized = os.path.expandvars(path_value.strip())
    normalized = os.path.normpath(normalized)
    return os.path.normcase(normalized).rstrip("\\/")


def _windows_prepend_path_part(path_value: str | None, preferred_dir: Path) -> tuple[str, bool]:
    preferred_text = str(preferred_dir)
    preferred_key = _windows_path_part_key(preferred_text)
    parts = _windows_path_parts(path_value)
    reordered = [preferred_text]
    reordered.extend(part for part in parts if _windows_path_part_key(part) != preferred_key)
    rendered = ";".join(reordered)
    return rendered, rendered != (path_value or "")


def _windows_managed_native_bin_dir() -> Path | None:
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        return Path(user_profile).expanduser() / ".tensor-grep" / "bin"
    try:
        return Path.home() / ".tensor-grep" / "bin"
    except RuntimeError:
        return None


def _windows_user_path_value() -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _value_type = winreg.QueryValueEx(key, "Path")
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _set_windows_user_path_value(path_value: str) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError as exc:
        raise OSError("winreg is unavailable") from exc
    value_type = winreg.REG_EXPAND_SZ if "%" in path_value else winreg.REG_SZ
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, value_type, path_value)


def _windows_python_subprocess_resolution_blocker(
    *, managed_dir: Path, path_value: str | None
) -> str | None:
    if not sys.platform.startswith("win") or not path_value:
        return None

    candidate = _doctor_python_subprocess_path_tg_candidate(path_value)
    if not candidate:
        return None

    candidate_path_text = candidate.get("path")
    candidate_version = candidate.get("version")
    candidate_kind = _doctor_tg_launcher_kind(candidate_path_text, candidate_version)
    if candidate_kind == "managed-native":
        return None
    if candidate_kind != "foreign":
        return None

    foreign_path = Path(str(candidate_path_text))
    foreign_dir = foreign_path.parent
    return (
        "Windows PATH repair could not put managed native tg.exe ahead of the first "
        "Python subprocess tg.exe in fresh shells. Windows appends User PATH after "
        "Machine PATH, so a Machine PATH foreign tg.exe can still win "
        'subprocess.run(["tg", ...]) even when shell PATHEXT resolves tg.com.\n'
        f"- managed native dir: {managed_dir}\n"
        f"- first Python subprocess tg.exe: {foreign_path} "
        f"({candidate_version or 'no recognizable --version output'})\n"
        f"Remediation: move {managed_dir} earlier in Machine PATH than {foreign_dir}, "
        f"or run tg repair-launcher --allow-foreign-rename if you own {foreign_path} "
        "and want tensor-grep to back it up into a .bak file. Do not remove unrelated "
        "launchers automatically."
    )


def _repair_windows_python_subprocess_launcher(*, allow_foreign_rename: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "not_windows",
        "platform": sys.platform,
        "message": "Python subprocess launcher repair is only needed on Windows.",
        "managed_native": None,
        "foreign_path": None,
        "backup_path": None,
        "replaced_path": None,
        "pre_repair_version": None,
        "post_repair_version": None,
    }
    if not sys.platform.startswith("win"):
        return payload

    expected_version = _doctor_installed_version()
    native_tg_binary = resolve_native_tg_binary()
    payload["expected_version"] = expected_version
    payload["managed_native"] = str(native_tg_binary) if native_tg_binary else None
    if native_tg_binary is None or not native_tg_binary.is_file():
        payload.update({
            "status": "blocked_missing_managed_native",
            "message": (
                "No managed native tg.exe was found. Run tg upgrade or reinstall tensor-grep "
                "before repairing Python subprocess launcher resolution."
            ),
        })
        return payload

    native_version = _doctor_tg_candidate_version(native_tg_binary)
    payload["managed_native_version"] = native_version
    if not _native_tg_version_matches(expected_version, native_version):
        payload.update({
            "status": "blocked_managed_native_version_mismatch",
            "message": (
                "Managed native tg.exe is not verified for this tensor-grep version: "
                f"{native_tg_binary} reports {native_version or 'no version'}, "
                f"expected {expected_version}."
            ),
        })
        return payload

    candidate = _doctor_python_subprocess_path_tg_candidate()
    if not candidate:
        payload.update({
            "status": "blocked_no_python_subprocess_tg",
            "message": "No tg.exe candidate was found on PATH for Python subprocess resolution.",
        })
        return payload

    candidate_path = Path(str(candidate.get("path") or ""))
    candidate_version = candidate.get("version")
    candidate_kind = _doctor_tg_launcher_kind(str(candidate_path), candidate_version)
    payload.update({
        "foreign_path": str(candidate_path),
        "pre_repair_version": candidate_version,
        "pre_repair_launcher_kind": candidate_kind,
    })

    if _same_path(candidate_path, native_tg_binary) and _native_tg_version_matches(
        expected_version,
        candidate_version,
    ):
        payload.update({
            "status": "already_ok",
            "message": "Python subprocess resolution already finds the managed native tg.exe.",
            "post_repair_version": candidate_version,
        })
        return payload

    if candidate_kind != "foreign":
        payload.update({
            "status": "blocked_non_foreign_launcher",
            "message": (
                "Python subprocess resolution does not point at a foreign tg.exe, so "
                "foreign launcher repair is not applicable. Use tg doctor --json for details."
            ),
        })
        return payload

    if candidate_path.name.lower() != "tg.exe":
        payload.update({
            "status": "blocked_unsupported_launcher_name",
            "message": (
                "Python subprocess launcher repair only handles a foreign tg.exe selected "
                f"by Windows CreateProcess, not {candidate_path.name}."
            ),
        })
        return payload

    if not allow_foreign_rename:
        payload.update({
            "status": "blocked_requires_allow_foreign_rename",
            "message": (
                "Python subprocess resolution is blocked by a foreign tg.exe. Re-run with "
                "--allow-foreign-rename only if you own that command and accept that it will "
                "be moved aside to a .bak file before tensor-grep installs its managed "
                f"native front door at {candidate_path}."
            ),
        })
        return payload

    backup_path = candidate_path.with_name(
        f"{candidate_path.name}.foreign-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-"
        f"{uuid4().hex[:8]}.bak"
    )
    payload["backup_path"] = str(backup_path)
    try:
        os.replace(candidate_path, backup_path)
        try:
            shutil.copy2(native_tg_binary, candidate_path)
            post_version = _doctor_tg_candidate_version(candidate_path)
            payload["post_repair_version"] = post_version
            if not _native_tg_version_matches(expected_version, post_version):
                raise RuntimeError(
                    "repaired tg.exe reported "
                    f"{post_version or 'no version'} instead of {expected_version}"
                )
        except Exception:
            candidate_path.unlink(missing_ok=True)
            os.replace(backup_path, candidate_path)
            raise
    except Exception as exc:
        payload.update({
            "status": "failed",
            "message": f"Python subprocess launcher repair failed: {exc}",
        })
        return payload

    payload.update({
        "status": "repaired",
        "replaced_path": str(candidate_path),
        "message": (
            "Python subprocess launcher repaired. The foreign tg.exe was backed up and "
            "the verified managed native tensor-grep front door now occupies that PATH slot."
        ),
    })
    return payload


def _ensure_windows_managed_native_first_on_path(native_path: Path) -> str | None:
    if not sys.platform.startswith("win"):
        return None

    managed_dir = native_path.parent
    expected_managed_dir = _windows_managed_native_bin_dir()
    if expected_managed_dir is None or _windows_path_part_key(str(managed_dir)) != (
        _windows_path_part_key(str(expected_managed_dir))
    ):
        return None

    messages: list[str] = []
    try:
        user_path = _windows_user_path_value()
        reordered_user_path, user_changed = _windows_prepend_path_part(user_path, managed_dir)
        if user_changed:
            _set_windows_user_path_value(reordered_user_path)
            messages.append("persistent User PATH")
    except OSError as exc:
        messages.append(f"User PATH repair warning: {exc}")

    current_path = os.environ.get("PATH", "")
    reordered_current_path, current_changed = _windows_prepend_path_part(current_path, managed_dir)
    if current_changed:
        os.environ["PATH"] = reordered_current_path
        messages.append("current process PATH")

    fresh_shell_blocker = _windows_python_subprocess_resolution_blocker(
        managed_dir=managed_dir,
        path_value=_doctor_fresh_shell_path_value(),
    )

    if not messages and not fresh_shell_blocker:
        return None
    if fresh_shell_blocker:
        update_line = (
            f"Updated: {', '.join(messages)}." if messages else "Updated: no PATH entries."
        )
        return f"{fresh_shell_blocker}\n{update_line}"
    return (
        "Windows PATH now prefers managed native tg.exe for Python subprocesses.\n"
        f"- {managed_dir}\n"
        f"Updated: {', '.join(messages)}."
    )


def _looks_like_windows_file_lock_error(message: str) -> bool:
    lowered = message.lower()
    return (
        "winerror 32" in lowered
        or "os error 32" in lowered
        or "being used by another process" in lowered
        or "access is denied" in lowered
        or "permission denied" in lowered
    )


def _schedule_windows_native_frontdoor_refresh(
    native_path: Path, expected_version: str, bridge_paths: list[Path] | None = None
) -> Path:
    import textwrap

    asset_payload = json.dumps([
        {"url": url, "flavor": candidate.flavor}
        for candidate, url in _native_frontdoor_download_candidates(expected_version)
    ])
    if asset_payload == "[]":
        raise RuntimeError("no release-native front-door asset is available for this platform")
    bridge_payload = json.dumps([str(path) for path in bridge_paths or []])

    helper_code = textwrap.dedent(
        """
        import json
        import os
        import shutil
        import subprocess
        import sys
        import time
        import urllib.request
        from pathlib import Path
        from uuid import uuid4

        parent_pid = int(sys.argv[1])
        log_path = Path(sys.argv[2])
        native_path = Path(sys.argv[3])
        expected_version = sys.argv[4]
        asset_candidates = json.loads(sys.argv[5])
        bridge_paths = [Path(path) for path in json.loads(sys.argv[6])]
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

        def _version(path: Path) -> str:
            result = subprocess.run([str(path), "--version"], capture_output=True, text=True)
            if result.returncode != 0:
                return ""
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    return line
            return ""

        errors: list[str] = []
        for attempt in range(120):
            for asset_candidate in asset_candidates:
                url = asset_candidate.get("url", "")
                flavor = asset_candidate.get("flavor", "unknown")
                temp_path = native_path.with_name(native_path.name + ".download-" + uuid4().hex)
                try:
                    try:
                        urllib.request.urlretrieve(url, temp_path)
                    except Exception as exc:
                        errors.append(f"{flavor} asset unavailable: {exc}")
                        continue
                    temp_version = _version(temp_path)
                    if expected_version not in temp_version:
                        raise RuntimeError(
                            "downloaded native tg front door reported "
                            + (temp_version or "no version")
                        )
                    os.replace(temp_path, native_path)
                    installed_version = _version(native_path)
                    if expected_version not in installed_version:
                        raise RuntimeError(
                            "installed native tg front door reported "
                            + (installed_version or "no version")
                        )
                    refreshed_bridges: list[str] = []
                    for bridge_path in bridge_paths:
                        shutil.copy2(native_path, bridge_path)
                        bridge_version = _version(bridge_path)
                        if expected_version not in bridge_version:
                            raise RuntimeError(
                                "refreshed PATH tensor-grep front-door copy reported "
                                + (bridge_version or "no version")
                                + " for "
                                + str(bridge_path)
                            )
                        refreshed_bridges.append(str(bridge_path))
                    bridge_text = ""
                    if refreshed_bridges:
                        bridge_text = (
                            "\\nRefreshed PATH tensor-grep front-door copies:\\n"
                            + "\\n".join(refreshed_bridges)
                        )
                    log_path.write_text(
                        "Native tg front-door refresh completed.\\n"
                        + "Verified "
                        + installed_version
                        + ".\\nNative asset flavor: "
                        + flavor
                        + ".\\n"
                        + url
                        + bridge_text,
                        encoding="utf-8",
                    )
                    raise SystemExit(0)
                except Exception as exc:
                    errors.append(str(exc))
                finally:
                    try:
                        temp_path.unlink()
                    except FileNotFoundError:
                        pass
            time.sleep(0.5)

        log_path.write_text(
            "Native tg front-door refresh failed.\\n" + "\\n".join(errors[-10:]),
            encoding="utf-8",
        )
        raise SystemExit(1)
        """
    ).strip()

    log_path = Path.home() / ".tensor-grep" / "logs" / f"native-upgrade-{uuid4().hex}.log"
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
            str(native_path),
            expected_version,
            asset_payload,
            bridge_payload,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    return log_path


def _refresh_managed_native_frontdoor(expected_version: str) -> str | None:
    native_path = _managed_native_frontdoor_path_from_env()
    if native_path is None:
        return None

    messages: list[str] = []
    path_order_message = _ensure_windows_managed_native_first_on_path(native_path)
    if path_order_message:
        messages.append(path_order_message)
    stale_com_bridges = _windows_stale_tensor_grep_com_bridges(expected_version, native_path)
    current_version = _native_tg_version(native_path) if native_path.is_file() else None
    if not _native_tg_version_matches(expected_version, current_version):
        try:
            install_result = _install_release_native_frontdoor(expected_version, native_path)
        except OSError as exc:
            if sys.platform.startswith("win") and (
                getattr(exc, "winerror", None) == 32
                or _looks_like_windows_file_lock_error(str(exc))
            ):
                log_path = _schedule_windows_native_frontdoor_refresh(
                    native_path, expected_version, stale_com_bridges
                )
                scheduled_message = (
                    f"Native tg front door refresh scheduled for {expected_version}."
                    f"\nUpgrade log: {log_path}"
                )
                return "\n".join([scheduled_message, *messages])
            raise RuntimeError(f"native front-door refresh failed: {exc}") from exc
        except RuntimeError as exc:
            raise RuntimeError(f"native front-door refresh failed: {exc}") from exc
        messages.append(
            f"Native tg front door refreshed to {expected_version}. "
            f"Native asset flavor: {install_result.flavor}."
        )

    try:
        refreshed_bridges = _refresh_windows_tensor_grep_com_bridges(
            expected_version, native_path, stale_com_bridges
        )
    except OSError as exc:
        raise RuntimeError(f"PATH tensor-grep front-door copy refresh failed: {exc}") from exc
    bridge_message = _refreshed_com_bridge_message(expected_version, refreshed_bridges)
    if bridge_message:
        messages.append(bridge_message)

    return "\n".join(messages) if messages else None


def _version_detail_lines() -> tuple[str, ...]:
    return (
        "",
        "features:+gpu-cudf,+gpu-torch,+rust-core",
        "simd(compile):+SSE2,-SSSE3,-AVX2",
        "simd(runtime):+SSE2,+SSSE3,+AVX2",
        "",
        "Arrow Zero-Copy IPC is available",
    )


def _print_version(*, verbose: bool = False) -> None:
    print(f"tensor-grep {_cli_package_version()}")
    if verbose:
        for line in _version_detail_lines():
            print(line)


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
    "auto_hybrid_regex",
    "no_unicode",
    "unicode",
    "pcre2_unicode",
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
    "ignore",
    "no_ignore_dot",
    "no_ignore_exclude",
    "no_ignore_files",
    "no_ignore_global",
    "no_ignore_parent",
    "no_ignore_vcs",
    "no_require_git",
    "require_git",
    "no_hidden",
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
    "passthru",
    "pretty",
    "quiet",
    "replace_str",
    "sort_by",
    "sort_by_reverse",
    "trim",
    "with_filename",
    "no_filename",
    "count_matches",
    "debug",
    "no_ignore_messages",
    "no_messages",
    "messages",
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
    return _cli_package_version()


def _doctor_session_daemon_status(path: str) -> dict[str, Any]:
    from tensor_grep.cli.session_daemon import get_session_daemon_status

    return get_session_daemon_status(path)


def _doctor_lsp_languages() -> list[str]:
    return supported_lsp_languages()


def _doctor_lsp_provider_statuses(path: str) -> list[dict[str, Any]]:
    from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager

    manager = ExternalLSPProviderManager()
    workspace_root = Path(path).resolve()
    try:
        return [
            manager.provider_status(
                language=language,
                workspace_root=workspace_root,
                verify_health=True,
                probe_timeout_seconds=_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS,
            )
            for language in _doctor_lsp_languages()
        ]
    finally:
        manager.stop_all()


def _doctor_rust_core_extension_available() -> bool:
    try:
        from tensor_grep.backends.rust_backend import HAVE_RUST
    except Exception:
        return False
    return bool(HAVE_RUST)


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


def _doctor_rust_binary_version_matches(
    expected_version: str, rust_binary_version: str | None
) -> bool | None:
    if rust_binary_version is None:
        return None
    return _native_tg_version_matches(expected_version, rust_binary_version)


def _doctor_tg_version_looks_like_tensor_grep(version_text: str | None) -> bool:
    if not version_text:
        return False
    stripped = version_text.strip().lower()
    return stripped.startswith("tg ") or stripped.startswith("tensor-grep ")


def _doctor_native_tg_binary_kind(native_tg_binary: Path | None) -> str:
    if native_tg_binary is None:
        return "missing"

    repo_root = Path(__file__).resolve().parents[3]
    try:
        relative = native_tg_binary.resolve().relative_to(repo_root.resolve())
    except (OSError, ValueError):
        return "standalone-executable"

    parts = tuple(part.lower() for part in relative.parts)
    if len(parts) >= 4 and parts[:2] == ("rust_core", "target"):
        if parts[2] == "debug":
            return "in-tree-debug"
        if parts[2] == "release":
            return "in-tree-release"
        return "in-tree-target"
    return "standalone-executable"


def _doctor_rust_binary_version_status(
    *,
    native_tg_binary_kind: str,
    rust_binary_version: str | None,
    rust_binary_version_matches: bool | None,
) -> str:
    if rust_binary_version is None:
        return "missing"
    if rust_binary_version_matches is True:
        return "matches"
    if native_tg_binary_kind.startswith("in-tree-"):
        return "stale"
    return "mismatch"


def _doctor_skipped_native_tg_binaries(
    expected_version: str,
    selected_binary: Path | None,
) -> list[dict[str, str | None]]:
    skipped: list[dict[str, str | None]] = []
    try:
        selected_resolved = selected_binary.resolve() if selected_binary is not None else None
    except OSError:
        selected_resolved = selected_binary

    for candidate in iter_in_tree_native_tg_binaries():
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if selected_resolved is not None and resolved == selected_resolved:
            continue
        version = _native_tg_version(resolved)
        version_matches = _native_tg_version_matches(expected_version, version)
        if version_matches:
            continue
        skipped.append({
            "path": str(resolved),
            "kind": _doctor_native_tg_binary_kind(resolved),
            "version": version,
            "version_status": "stale" if version is not None else "unknown",
        })
    return skipped


def _doctor_rust_binary_remediation(
    *,
    rust_binary_version_status: str,
    native_tg_binary_kind: str,
) -> str | None:
    if (
        rust_binary_version_status == "stale" and native_tg_binary_kind.startswith("in-tree-")
    ) or rust_binary_version_status == "stale-skipped":
        return (
            "Rebuild the in-tree native tg binary, for example "
            "`C:/Users/oimir/.cargo/bin/cargo.exe build --manifest-path rust_core/Cargo.toml "
            "--release`, or set TG_NATIVE_TG_BINARY to opt in to a specific native binary."
        )
    if rust_binary_version_status == "mismatch":
        return "Set TG_NATIVE_TG_BINARY to the intended release binary or refresh the tg install."
    return None


def _doctor_rust_binary_warning(
    *,
    expected_version: str,
    rust_binary_version: str | None,
    rust_binary_version_status: str,
    skipped_native_tg_binaries: list[dict[str, str | None]] | None = None,
) -> str | None:
    if rust_binary_version_status == "stale-skipped":
        skipped = skipped_native_tg_binaries or []
        if skipped:
            first = skipped[0]
            return (
                "ignored stale in-tree native tg binary: "
                f"expected {expected_version}, found {first.get('version') or 'unknown'} "
                f"at {first.get('path')}"
            )
        return f"ignored stale in-tree native tg binary: expected {expected_version}"
    if rust_binary_version_status == "stale":
        return (
            "in-tree native tg binary is stale: "
            f"expected {expected_version}, found {rust_binary_version or 'unknown'}"
        )
    if rust_binary_version_status == "mismatch":
        return (
            "native tg binary version mismatch: "
            f"expected {expected_version}, found {rust_binary_version or 'unknown'}"
        )
    return None


def _doctor_tg_candidate_version(candidate: Path) -> str | None:
    env = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
    try:
        res = subprocess.run(
            [str(candidate), "--version"],
            capture_output=True,
            env=env,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if res.returncode != 0:
        return None
    for line in res.stdout.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


_DOCTOR_VERSION_NOT_PROVIDED = object()


def _doctor_tg_launcher_kind(
    path: str | None,
    version_text: str | None | object = _DOCTOR_VERSION_NOT_PROVIDED,
) -> str | None:
    if not path:
        return None
    if version_text is not _DOCTOR_VERSION_NOT_PROVIDED and not isinstance(version_text, str):
        return "foreign"
    if isinstance(version_text, str) and not _doctor_tg_version_looks_like_tensor_grep(
        version_text
    ):
        return "foreign"

    candidate = Path(path)
    suffix = candidate.suffix.lower()
    parts = tuple(part.lower() for part in candidate.parts)
    if suffix in {".cmd", ".bat"}:
        return "cmd-shim"
    if suffix == ".ps1":
        return "powershell-shim"
    if suffix in {".com", ".exe"}:
        if ".tensor-grep" in parts and "bin" in parts:
            return "managed-native"
        if suffix == ".exe" and isinstance(version_text, str) and version_text.startswith("tg "):
            return "native-exe"
        if "scripts" in parts and (
            suffix == ".exe"
            and (
                ".venv" in parts
                or "venv" in parts
                or any(part.startswith("python") for part in parts)
            )
        ):
            return "python-entrypoint"
        return "native-exe"
    if candidate.name.lower() == "tg":
        if ".tensor-grep" in parts and "bin" in parts:
            return "managed-native"
        if sys.platform.startswith("win"):
            return "bash-shim"
        return "native-exe"
    return "unknown"


def _doctor_windows_registry_path_value(root: Any, subkey: str) -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _value_type = winreg.QueryValueEx(key, "Path")
    except OSError:
        return None
    if not isinstance(value, str) or not value:
        return None
    return os.path.expandvars(value)


def _doctor_fresh_shell_path_value() -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    machine_path = _doctor_windows_registry_path_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    )
    user_path = _doctor_windows_registry_path_value(winreg.HKEY_CURRENT_USER, "Environment")
    parts: list[str] = []
    for value in (machine_path, user_path):
        if value:
            parts.extend(entry for entry in value.split(";") if entry)
    if not parts:
        return None
    return ";".join(parts)


def _doctor_path_list_separator(path_value: str) -> str:
    if not sys.platform.startswith("win"):
        return os.pathsep
    if ";" in path_value or re.search(r"(?:^|;)[A-Za-z]:[\\/]", path_value):
        return ";"
    return os.pathsep


def _doctor_path_tg_candidates(path_value: str | None = None) -> list[dict[str, str | None]]:
    if sys.platform.startswith("win"):
        raw_exts = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
        extensions = [ext.lower() for ext in raw_exts.split(";") if ext]
        if not extensions:
            extensions = [".com", ".exe", ".bat", ".cmd"]
        names = [f"tg{ext}" for ext in extensions]
        names.append("tg")
    else:
        names = ["tg"]

    candidates: list[dict[str, str | None]] = []
    seen: set[str] = set()
    path_to_scan = os.environ.get("PATH", "") if path_value is None else path_value
    for entry in path_to_scan.split(_doctor_path_list_separator(path_to_scan)):
        if not entry:
            continue
        directory = Path(entry)
        for name in names:
            candidate = directory / name
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            key = str(resolved).lower() if sys.platform.startswith("win") else str(resolved)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "path": str(resolved),
                "version": _doctor_tg_candidate_version(resolved),
            })
    return candidates


def _doctor_python_subprocess_path_tg_candidate(
    path_value: str | None = None,
) -> dict[str, str | None] | None:
    path_to_scan = os.environ.get("PATH", "") if path_value is None else path_value
    if sys.platform.startswith("win"):
        names = ["tg.exe"]
    else:
        names = ["tg"]

    seen: set[str] = set()
    for entry in path_to_scan.split(_doctor_path_list_separator(path_to_scan)):
        if not entry:
            continue
        directory = Path(entry)
        for name in names:
            candidate = directory / name
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            key = str(resolved).lower() if sys.platform.startswith("win") else str(resolved)
            if key in seen:
                continue
            seen.add(key)
            return {
                "path": str(resolved),
                "version": _doctor_tg_candidate_version(resolved),
            }
    return None


def _doctor_fresh_shell_path_tg_candidates() -> list[dict[str, str | None]]:
    fresh_path_value = _doctor_fresh_shell_path_value()
    if not fresh_path_value:
        return []
    return _doctor_path_tg_candidates(fresh_path_value)


def _doctor_path_tg_launcher_warning(
    *,
    current_kind: str | None,
    current_path: str | None,
    fresh_kind: str | None,
    fresh_path: str | None,
) -> str | None:
    compatibility_kinds = {"bash-shim", "cmd-shim", "powershell-shim", "python-entrypoint"}
    native_kinds = {"managed-native", "native-exe"}
    if current_kind in compatibility_kinds and fresh_kind in native_kinds:
        return (
            "current process PATH resolves a compatibility shim before the managed native "
            f"front door ({current_path}); fresh-shell PATH resolves {fresh_path}. "
            "restart the shell or refresh PATH before benchmarking subprocess-heavy workflows."
        )
    if current_kind in compatibility_kinds:
        return (
            "current process PATH resolves a compatibility shim "
            f"({current_path}); benchmark timing may include shim overhead."
        )
    return None


def _doctor_tg_foreign_warning(
    *,
    label: str,
    path: str | None,
    version: str | None,
    expected_version: str,
) -> str | None:
    if not path or _doctor_tg_version_looks_like_tensor_grep(version):
        return None
    return (
        f"first {label} tg is not tensor-grep: {path} reports "
        f"{version or 'no recognizable --version output'}; expected tg {expected_version}."
    )


def _doctor_tg_foreign_remediation(
    *,
    foreign_path: str | None,
    candidates: list[dict[str, str | None]],
) -> str | None:
    if not foreign_path:
        return None
    managed_candidate = next(
        (
            candidate
            for candidate in candidates
            if _doctor_tg_launcher_kind(candidate.get("path"), candidate.get("version"))
            == "managed-native"
        ),
        None,
    )
    managed_path = managed_candidate.get("path") if managed_candidate else None
    managed_dir = str(Path(managed_path).parent) if managed_path else "~/.tensor-grep/bin"
    foreign_dir = str(Path(foreign_path).parent)
    return (
        f"Move {managed_dir} earlier in PATH than {foreign_dir}, or rename the foreign tg "
        "command outside tensor-grep. If the foreign directory comes from Machine PATH, "
        "User PATH repair cannot outrank it. If you own the foreign command, run "
        "tg repair-launcher --allow-foreign-rename to back it up before installing the "
        "managed native tg.exe into that PATH slot. Do not remove unrelated launchers "
        "unless you own them."
    )


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


def _doctor_gpu_search_runtime_probe(native_tg_binary: Path | None) -> dict[str, Any]:
    requested_gpu_device_ids = [0]
    base: dict[str, Any] = {
        "status": "not_run",
        "requested_gpu_device_ids": requested_gpu_device_ids,
        "command": None,
        "exit_code": None,
        "routing_backend": None,
        "routing_reason": None,
        "sidecar_used": None,
        "routing_gpu_device_ids": [],
        "error": None,
    }
    if native_tg_binary is None:
        base["error"] = "native tg binary was not resolved"
        return base
    if not native_tg_binary.exists():
        base["error"] = f"native tg binary does not exist: {native_tg_binary}"
        return base

    sentinel = "tg doctor gpu runtime probe"
    with TemporaryDirectory(prefix="tg-doctor-gpu-probe-") as temp_dir:
        probe_file = Path(temp_dir) / "probe.log"
        probe_file.write_text(f"{sentinel}\n", encoding="utf-8")
        command = [
            str(native_tg_binary),
            "search",
            "--gpu-device-ids",
            ",".join(str(device_id) for device_id in requested_gpu_device_ids),
            "--json",
            "--no-ignore",
            "-F",
            sentinel,
            str(probe_file),
        ]
        base["command"] = " ".join([*command[:-1], "<doctor-gpu-probe-file>"])
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=2.0,
            )
        except subprocess.TimeoutExpired:
            base["status"] = "failed"
            base["error"] = "GPU runtime probe timed out after 2.0 seconds"
            return base
        except OSError as exc:
            base["status"] = "failed"
            base["error"] = str(exc)
            return base

    base["exit_code"] = result.returncode
    if result.returncode != 0:
        base["status"] = "failed"
        base["error"] = (result.stderr or "").strip() or "GPU runtime probe failed"
        return base

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        base["status"] = "failed"
        base["error"] = f"GPU runtime probe returned invalid JSON: {exc}"
        return base

    routing_backend = str(payload.get("routing_backend") or "")
    sidecar_used = bool(payload.get("sidecar_used", False))
    base.update({
        "routing_backend": routing_backend or None,
        "routing_reason": payload.get("routing_reason"),
        "sidecar_used": sidecar_used,
        "routing_gpu_device_ids": payload.get("routing_gpu_device_ids") or [],
    })
    if routing_backend == "NativeGpuBackend" and not sidecar_used:
        base["status"] = "supported"
        return base

    base["status"] = "unsupported"
    base["error"] = (
        "GPU route did not use NativeGpuBackend "
        f"(routing_backend={routing_backend or 'unknown'}, sidecar_used={sidecar_used})."
    )
    return base


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
        "TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS",
    ]
    installed_version = _doctor_installed_version()
    rust_binary_version = _doctor_rust_binary_version(native_tg_binary)
    native_tg_binary_kind = _doctor_native_tg_binary_kind(native_tg_binary)
    rust_binary_version_matches = _doctor_rust_binary_version_matches(
        installed_version,
        rust_binary_version,
    )
    rust_binary_version_status = _doctor_rust_binary_version_status(
        native_tg_binary_kind=native_tg_binary_kind,
        rust_binary_version=rust_binary_version,
        rust_binary_version_matches=rust_binary_version_matches,
    )
    skipped_native_tg_binaries = _doctor_skipped_native_tg_binaries(
        installed_version,
        native_tg_binary,
    )
    if native_tg_binary is None and any(
        candidate.get("version_status") == "stale" for candidate in skipped_native_tg_binaries
    ):
        rust_binary_version_status = "stale-skipped"
    rust_core_extension_available = _doctor_rust_core_extension_available()
    path_tg_candidates = _doctor_path_tg_candidates()
    path_tg_first_raw_version = path_tg_candidates[0].get("version") if path_tg_candidates else None
    path_tg_first_version = (
        str(path_tg_first_raw_version) if path_tg_first_raw_version is not None else None
    )
    path_tg_first_path = str(path_tg_candidates[0].get("path")) if path_tg_candidates else None
    path_tg_first_launcher_kind = _doctor_tg_launcher_kind(
        path_tg_first_path,
        path_tg_first_version,
    )
    fresh_shell_path_tg_candidates = _doctor_fresh_shell_path_tg_candidates()
    fresh_shell_path_tg_first_raw_version = (
        fresh_shell_path_tg_candidates[0].get("version") if fresh_shell_path_tg_candidates else None
    )
    fresh_shell_path_tg_first_version = (
        str(fresh_shell_path_tg_first_raw_version)
        if fresh_shell_path_tg_first_raw_version is not None
        else None
    )
    fresh_shell_path_tg_first_path = (
        str(fresh_shell_path_tg_candidates[0].get("path"))
        if fresh_shell_path_tg_candidates
        else None
    )
    fresh_shell_path_tg_first_launcher_kind = _doctor_tg_launcher_kind(
        fresh_shell_path_tg_first_path,
        fresh_shell_path_tg_first_version,
    )
    python_subprocess_path_tg_first = _doctor_python_subprocess_path_tg_candidate()
    python_subprocess_path_tg_first_raw_version = (
        python_subprocess_path_tg_first.get("version") if python_subprocess_path_tg_first else None
    )
    python_subprocess_path_tg_first_version = (
        str(python_subprocess_path_tg_first_raw_version)
        if python_subprocess_path_tg_first_raw_version is not None
        else None
    )
    python_subprocess_path_tg_first_path = (
        str(python_subprocess_path_tg_first.get("path"))
        if python_subprocess_path_tg_first
        else None
    )
    python_subprocess_path_tg_first_launcher_kind = _doctor_tg_launcher_kind(
        python_subprocess_path_tg_first_path,
        python_subprocess_path_tg_first_version,
    )
    path_tg_foreign_warning = _doctor_tg_foreign_warning(
        label="PATH",
        path=path_tg_first_path,
        version=path_tg_first_version,
        expected_version=installed_version,
    )
    fresh_shell_path_tg_foreign_warning = _doctor_tg_foreign_warning(
        label="fresh-shell PATH",
        path=fresh_shell_path_tg_first_path,
        version=fresh_shell_path_tg_first_version,
        expected_version=installed_version,
    )
    python_subprocess_path_tg_foreign_warning = _doctor_tg_foreign_warning(
        label="Python subprocess PATH",
        path=python_subprocess_path_tg_first_path,
        version=python_subprocess_path_tg_first_version,
        expected_version=installed_version,
    )
    python_subprocess_remediation_candidates: list[dict[str, str | None]] = []
    if python_subprocess_path_tg_first is not None:
        python_subprocess_remediation_candidates.append(python_subprocess_path_tg_first)
    python_subprocess_remediation_candidates.extend(path_tg_candidates)
    python_subprocess_remediation_candidates.extend(fresh_shell_path_tg_candidates)
    gpu_status = _doctor_gpu_status()
    gpu_status["search_runtime_probe"] = _doctor_gpu_search_runtime_probe(native_tg_binary)
    payload: dict[str, Any] = {
        "version": installed_version,
        "platform": sys.platform,
        "python_executable": sys.executable,
        "python_version": ".".join([str(x) for x in sys.version_info[:3]]),
        "invoked_as": sys.argv[0] if sys.argv else "tg",
        "root": str(root),
        "config": str(resolved_config),
        "native_tg_binary": str(native_tg_binary) if native_tg_binary is not None else None,
        "native_tg_binary_exists": native_tg_binary is not None,
        "native_tg_binary_kind": native_tg_binary_kind,
        "rust_core_extension_available": rust_core_extension_available,
        "search_acceleration_backend": (
            "standalone-native-tg"
            if native_tg_binary is not None
            else "rust-core-extension"
            if rust_core_extension_available
            else "python"
        ),
        "rust_binary_version": rust_binary_version,
        "rust_binary_expected_version": installed_version,
        "rust_binary_version_matches": rust_binary_version_matches,
        "rust_binary_version_status": rust_binary_version_status,
        "rust_binary_version_warning": _doctor_rust_binary_warning(
            expected_version=installed_version,
            rust_binary_version=rust_binary_version,
            rust_binary_version_status=rust_binary_version_status,
            skipped_native_tg_binaries=skipped_native_tg_binaries,
        ),
        "rust_binary_remediation": _doctor_rust_binary_remediation(
            rust_binary_version_status=rust_binary_version_status,
            native_tg_binary_kind=native_tg_binary_kind,
        ),
        "skipped_native_tg_binaries": skipped_native_tg_binaries,
        "path_tg_candidates": path_tg_candidates,
        "path_tg_first_version": path_tg_first_version,
        "path_tg_first_launcher_kind": path_tg_first_launcher_kind,
        "path_tg_first_version_matches": _doctor_rust_binary_version_matches(
            installed_version,
            path_tg_first_version,
        ),
        "path_tg_first_is_foreign": path_tg_first_launcher_kind == "foreign",
        "path_tg_foreign_warning": path_tg_foreign_warning,
        "path_tg_foreign_remediation": _doctor_tg_foreign_remediation(
            foreign_path=path_tg_first_path if path_tg_foreign_warning else None,
            candidates=path_tg_candidates,
        ),
        "fresh_shell_path_tg_candidates": fresh_shell_path_tg_candidates,
        "fresh_shell_path_tg_first_version": fresh_shell_path_tg_first_version,
        "fresh_shell_path_tg_first_launcher_kind": fresh_shell_path_tg_first_launcher_kind,
        "fresh_shell_path_tg_first_version_matches": _doctor_rust_binary_version_matches(
            installed_version,
            fresh_shell_path_tg_first_version,
        ),
        "fresh_shell_path_tg_first_is_foreign": (
            fresh_shell_path_tg_first_launcher_kind == "foreign"
        ),
        "fresh_shell_path_tg_foreign_warning": fresh_shell_path_tg_foreign_warning,
        "fresh_shell_path_tg_foreign_remediation": _doctor_tg_foreign_remediation(
            foreign_path=(
                fresh_shell_path_tg_first_path if fresh_shell_path_tg_foreign_warning else None
            ),
            candidates=fresh_shell_path_tg_candidates,
        ),
        "python_subprocess_path_tg_first": python_subprocess_path_tg_first,
        "python_subprocess_path_tg_first_version": python_subprocess_path_tg_first_version,
        "python_subprocess_path_tg_first_launcher_kind": (
            python_subprocess_path_tg_first_launcher_kind
        ),
        "python_subprocess_path_tg_first_version_matches": (
            _doctor_rust_binary_version_matches(
                installed_version,
                python_subprocess_path_tg_first_version,
            )
        ),
        "python_subprocess_path_tg_first_is_foreign": (
            python_subprocess_path_tg_first_launcher_kind == "foreign"
        ),
        "python_subprocess_path_tg_foreign_warning": (python_subprocess_path_tg_foreign_warning),
        "python_subprocess_path_tg_foreign_remediation": _doctor_tg_foreign_remediation(
            foreign_path=(
                python_subprocess_path_tg_first_path
                if python_subprocess_path_tg_foreign_warning
                else None
            ),
            candidates=python_subprocess_remediation_candidates,
        ),
        "path_tg_launcher_warning": _doctor_path_tg_launcher_warning(
            current_kind=path_tg_first_launcher_kind,
            current_path=path_tg_first_path,
            fresh_kind=fresh_shell_path_tg_first_launcher_kind,
            fresh_path=fresh_shell_path_tg_first_path,
        ),
        "gpu": gpu_status,
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
    lines.append(f"native_tg_binary_kind: {payload.get('native_tg_binary_kind', 'unknown')}")
    lines.append(
        f"search_acceleration_backend: {payload.get('search_acceleration_backend', 'unknown')}"
    )
    if rust_version := payload.get("rust_binary_version"):
        lines.append(f"rust_binary_version:\n  {rust_version.replace(chr(10), chr(10) + '  ')}")
    if rust_binary_warning := payload.get("rust_binary_version_warning"):
        lines.append(f"rust_binary_version_warning: {rust_binary_warning}")
    if rust_binary_remediation := payload.get("rust_binary_remediation"):
        lines.append(f"rust_binary_remediation: {rust_binary_remediation}")
    skipped_native_tg_binaries = cast(
        list[dict[str, str | None]],
        payload.get("skipped_native_tg_binaries", []),
    )
    if skipped_native_tg_binaries:
        lines.append("skipped_native_tg_binaries:")
        for candidate in skipped_native_tg_binaries:
            lines.append(
                "  "
                f"{candidate.get('path')} "
                f"kind={candidate.get('kind') or 'unknown'} "
                f"version={candidate.get('version') or 'unknown'} "
                f"status={candidate.get('version_status') or 'unknown'}"
            )
    path_tg_candidates = cast(list[dict[str, str | None]], payload.get("path_tg_candidates", []))
    if path_tg_candidates:
        lines.append("path_tg_candidates:")
        for candidate in path_tg_candidates:
            lines.append(
                f"  {candidate.get('path')} version={candidate.get('version') or 'unknown'}"
            )
        lines.append(
            "path_tg_first_launcher_kind: "
            f"{payload.get('path_tg_first_launcher_kind') or 'unknown'}"
        )
        if payload.get("path_tg_first_version_matches") is False:
            lines.append(
                f"path_tg_warning: first PATH tg reports {payload.get('path_tg_first_version')} "
                f"expected {payload.get('version')}"
            )
    fresh_shell_path_tg_candidates = cast(
        list[dict[str, str | None]],
        payload.get("fresh_shell_path_tg_candidates", []),
    )
    if fresh_shell_path_tg_candidates:
        first_fresh = fresh_shell_path_tg_candidates[0]
        lines.append(
            "fresh_shell_path_tg_first: "
            f"{first_fresh.get('path')} "
            f"kind={payload.get('fresh_shell_path_tg_first_launcher_kind') or 'unknown'} "
            f"version={first_fresh.get('version') or 'unknown'}"
        )
    python_subprocess_path_tg_first = cast(
        dict[str, str | None] | None,
        payload.get("python_subprocess_path_tg_first"),
    )
    if python_subprocess_path_tg_first:
        lines.append(
            "python_subprocess_path_tg_first: "
            f"{python_subprocess_path_tg_first.get('path')} "
            f"kind={payload.get('python_subprocess_path_tg_first_launcher_kind') or 'unknown'} "
            f"version={python_subprocess_path_tg_first.get('version') or 'unknown'}"
        )
    if launcher_warning := payload.get("path_tg_launcher_warning"):
        lines.append(f"path_tg_launcher_warning: {launcher_warning}")
    if python_subprocess_warning := payload.get("python_subprocess_path_tg_foreign_warning"):
        lines.append(f"python_subprocess_path_tg_foreign_warning: {python_subprocess_warning}")
    if python_subprocess_remediation := payload.get(
        "python_subprocess_path_tg_foreign_remediation"
    ):
        lines.append(
            f"python_subprocess_path_tg_foreign_remediation: {python_subprocess_remediation}"
        )

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
            health_status = current.get("health_status", "unknown")
            health_check = current.get("health_check", "unknown")
            lsp_proof = current.get("lsp_proof", False)
            not_lsp_proof_reason = current.get("not_lsp_proof_reason")
            suffix = f" last_error={last_error}" if last_error else ""
            if managed_root:
                suffix = f" managed_root={managed_root}{suffix}"
            if not_lsp_proof_reason:
                suffix = f"{suffix} not_lsp_proof_reason={not_lsp_proof_reason}"
            lines.append(
                f"  {current['language']}: {availability}/{status} "
                f"health={health_status} health_check={health_check} "
                f"lsp_proof={lsp_proof} source={source} command={command_str}{suffix}"
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
    if config.column:
        command.append("--column")
    if config.context is not None:
        command.extend(["-C", str(config.context)])
    if config.max_count is not None:
        command.extend(["-m", str(config.max_count)])
    if config.path_separator is not None:
        command.extend(["--path-separator", config.path_separator])
    if config.vimgrep:
        command.append("--vimgrep")
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
    _safe_stdout_line("\n".join(paths))


def _path_output_sort_key(path: str) -> str:
    return path.replace("\\", "/").lower()


def _ordered_path_output(paths: list[str], config: "SearchConfig") -> list[str]:
    if config.sort_by == "path":
        return sorted(paths, key=_path_output_sort_key)
    if config.sort_by_reverse == "path":
        return sorted(paths, key=_path_output_sort_key, reverse=True)
    return paths


def _looks_like_binary_path(path: str) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return b"\0" in handle.read(8192)
    except OSError:
        return False


def _path_has_hidden_component(path: str) -> bool:
    return any(part.startswith(".") and part not in {".", ".."} for part in Path(path).parts)


def _safe_stdout_line(text: str) -> None:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None and encoding and "utf" not in encoding and not text.isascii():
        buffer.write(f"{text}\n".encode("utf-8", errors="replace"))
        flush = getattr(buffer, "flush", None)
        if callable(flush):
            flush()
        return
    try:
        print(text)
    except UnicodeEncodeError:
        payload = f"{text}\n".encode("utf-8", errors="replace")
        if buffer is not None:
            buffer.write(payload)
            flush = getattr(buffer, "flush", None)
            if callable(flush):
                flush()
            return
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        escaped_text = f"{text}\n".encode(encoding, errors="backslashreplace").decode(
            encoding, errors="ignore"
        )
        sys.stdout.write(escaped_text)
        flush = getattr(sys.stdout, "flush", None)
        if callable(flush):
            flush()


def _is_invalid_regex_error(exc: Exception) -> bool:
    if isinstance(exc, re.error):
        return True
    message = str(exc).lower()
    if (
        "regex parse error" in message
        or "error parsing regex" in message
        or "invalid regex" in message
    ):
        return True
    return exc.__class__.__name__ == "InvalidRegexError"


def _search_error_payload(error: str, detail: str) -> dict[str, object]:
    from tensor_grep.cli.formatters.json_fmt import JSON_OUTPUT_VERSION

    return {
        "version": JSON_OUTPUT_VERSION,
        "ok": False,
        "error": error,
        "detail": detail,
    }


def _emit_search_error_json(error: str, detail: str) -> None:
    _safe_stdout_line(json.dumps(_search_error_payload(error, detail)))


def _exit_search_error(
    error: str,
    detail: str,
    *,
    json_mode: bool,
    stderr_detail: str | None = None,
    exit_code: int = 2,
) -> None:
    if json_mode:
        _emit_search_error_json(error, detail)
    else:
        typer.echo(f"Error: {stderr_detail or detail}", err=True)
    sys.exit(exit_code)


def _exit_invalid_regex(exc: Exception, *, json_mode: bool = False) -> None:
    message = str(exc)
    if "invalid regex" not in message.lower():
        message = f"invalid regex pattern: {message}"
    _exit_search_error(
        "invalid_regex",
        message,
        json_mode=json_mode,
        stderr_detail=f"{message}. Use --fixed-strings (-F) to search this pattern literally.",
    )


def _validate_search_regex(pattern: str, config: "SearchConfig") -> None:
    if config.fixed_strings or config.pcre2:
        return

    flags = 0
    if config.ignore_case or (config.smart_case and pattern.islower()):
        flags |= re.IGNORECASE

    candidate = pattern
    if config.line_regexp:
        candidate = f"^{pattern}$"
    elif config.word_regexp:
        candidate = rf"\b{pattern}\b"

    try:
        re.compile(candidate, flags)
    except re.error as exc:
        from tensor_grep.backends.cpu_backend import InvalidRegexError

        raise InvalidRegexError(f"error parsing regex: {exc}") from exc


def _search_paths_include_guarded_broad_root(paths: list[str]) -> bool:
    for path in paths:
        if not path or path == "-" or path.startswith("-"):
            continue
        normalized = path.replace("\\", "/").rstrip("/").lower()
        if normalized in _GUARDED_BROAD_SEARCH_ROOTS:
            return True
        if any(normalized.endswith(f"/{root}") for root in _GUARDED_BROAD_SEARCH_ROOTS):
            return True
    return False


def _config_with_guarded_broad_root_globs(config: "SearchConfig") -> "SearchConfig":
    existing_globs = list(config.glob or [])
    for glob in _GUARDED_BROAD_ROOT_RG_GLOBS:
        if glob not in existing_globs:
            existing_globs.append(glob)
    return replace(config, glob=existing_globs)


def _generated_scan_dir_names(paths: list[str]) -> list[str]:
    found: set[str] = set()
    generated_names = {name.lower() for name in _BROAD_GENERATED_SCAN_DIR_NAMES}
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
            for candidate_name in {path.name, resolved.name}:
                if candidate_name and candidate_name.lower() in generated_names:
                    found.add(candidate_name)
            for child in path.iterdir():
                if child.is_dir() and child.name.lower() in generated_names:
                    found.add(child.name)
        except OSError:
            continue
    return sorted(found, key=lambda item: item.lower())


def _has_generated_scan_bound(config: "SearchConfig") -> bool:
    return bool(
        config.max_depth is not None
        or config.glob
        or config.iglob
        or config.file_type
        or config.type_not
    )


def _should_refuse_unbounded_generated_scan(
    paths: list[str],
    config: "SearchConfig",
    *,
    allow_broad_generated_scan: bool,
    files_mode: bool,
) -> tuple[bool, list[str]]:
    if allow_broad_generated_scan or _has_generated_scan_bound(config):
        return False, []
    if not (
        (files_mode and config.hidden)
        or config.no_ignore
        or config.no_ignore_files
        or config.no_ignore_vcs
        or config.unrestricted > 0
    ):
        return False, []
    generated_dirs = _generated_scan_dir_names(paths)
    return bool(generated_dirs), generated_dirs


def _format_broad_generated_scan_error(generated_dirs: list[str]) -> str:
    visible_dirs = ", ".join(generated_dirs[:8])
    if len(generated_dirs) > 8:
        visible_dirs = f"{visible_dirs}, ..."
    return (
        "Error: broad generated-root scan refused: path contains generated, cache, "
        f"or dependency directories ({visible_dirs}). Scope the path, add --glob, --type, "
        "or --max-depth, or pass --allow-broad-generated-scan to opt in.\n"
        "For bounded output:\n"
        "tg search --files <path> --hidden --max-depth <N>\n"
        "For intentional broad scans:\n"
        "--allow-broad-generated-scan"
    )


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
    explicit_rg_format: bool,
    json_mode: bool,
    ndjson_mode: bool,
    files_mode: bool,
    files_with_matches: bool,
    files_without_match: bool,
    only_matching: bool,
    stats_mode: bool,
) -> bool:
    rg_json_passthrough = bool(json_mode and explicit_rg_format)
    # Keep passthrough only for modes where rg semantics are fully compatible
    # with tensor-grep output and feature behavior.
    return bool(
        not config.ast
        and not config.ltl
        and not config.force_cpu
        and format_type == "rg"
        and (not json_mode or rg_json_passthrough)
        and not ndjson_mode
        and not (files_mode and json_mode)
        and not only_matching
        and not (rg_json_passthrough and stats_mode)
        and not (rg_json_passthrough and (config.count or config.count_matches))
        and not (rg_json_passthrough and (files_with_matches or files_without_match))
        and not (rg_json_passthrough and config.replace_str is not None)
        and not (rg_json_passthrough and config.passthru)
        and not (files_with_matches and (config.count or config.count_matches))
    )


def _explicit_rg_format_requested(
    argv: list[str] | None = None,
    *,
    format_value: str | None = None,
) -> bool:
    del format_value
    tokens = list(sys.argv[1:] if argv is None else argv)
    if argv is None:
        if not tokens or tokens[0] != "search":
            return False
        tokens = tokens[1:]
    elif tokens and tokens[0] == "search":
        tokens = tokens[1:]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--format":
            index += 1
            return index < len(tokens) and tokens[index] == "rg"
        if token.startswith("--format="):
            return token.split("=", 1)[1] == "rg"
        index += 1
    return False


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


def _run_rg_compatible_info_action(flag: str, unavailable_message: str) -> None:
    candidates = [resolve_native_tg_binary(), resolve_ripgrep_binary()]
    last_completed: subprocess.CompletedProcess[str] | None = None
    for candidate in candidates:
        if not candidate or not candidate.exists():
            continue
        completed = subprocess.run([str(candidate), flag], capture_output=True, text=True)
        last_completed = completed
        if completed.returncode == 0:
            if completed.stdout:
                typer.echo(completed.stdout.rstrip("\n\r"))
            if completed.stderr:
                typer.echo(completed.stderr.rstrip("\n\r"), err=True)
            raise typer.Exit(0)
    if flag == "--type-list" and last_completed is None:
        typer.echo("\n".join(_BUILTIN_TYPE_LIST))
        raise typer.Exit(0)
    if last_completed is not None:
        output = last_completed.stderr.strip() or last_completed.stdout.strip()
        if output:
            typer.echo(output, err=True)
        raise typer.Exit(int(last_completed.returncode or 1))
    typer.echo(unavailable_message, err=True)
    raise typer.Exit(1)


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
        try:
            loaded = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            detail = str(exc).splitlines()[0] if str(exc).strip() else "parse error"
            raise ValueError(f"Invalid YAML in {path}: {detail}") from exc
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
        "utils_dir": str(raw.get("utilsDir") or "utils"),
        "language": normalize_ast_language(str(raw.get("language") or "python")),
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
                    "language": normalize_ast_language(
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
            "language": normalize_ast_language(str(payload.get("language") or default_language)),
        })

    return specs


def _load_inline_rule_specs(
    inline_rules_text: str, *, default_language: str | None = None
) -> list[dict[str, str]]:
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    specs: list[dict[str, str]] = []

    try:
        documents = list(yaml.load_all(inline_rules_text, Loader=loader))
    except yaml.YAMLError as exc:
        detail = str(exc).splitlines()[0] if str(exc).strip() else "parse error"
        raise ValueError(f"Invalid inline rules YAML: {detail}") from exc

    for document_index, payload in enumerate(documents, start=1):
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
                spec = {
                    "id": str(item.get("id") or f"inline-rule-{document_index}-{rule_index}"),
                    "pattern": pattern,
                    "language": normalize_ast_language(
                        item.get("language")
                        or payload.get("language")
                        or default_language
                        or "python"
                    ),
                }
                for metadata_key in ("severity", "message"):
                    if item.get(metadata_key) is not None:
                        spec[metadata_key] = str(item[metadata_key])
                    elif payload.get(metadata_key) is not None:
                        spec[metadata_key] = str(payload[metadata_key])
                specs.append(spec)
            continue

        pattern = _extract_rule_pattern(payload)
        if not pattern:
            continue
        spec = {
            "id": str(payload.get("id") or f"inline-rule-{document_index}"),
            "pattern": pattern,
            "language": normalize_ast_language(
                str(payload.get("language") or default_language or "python")
            ),
        }
        for metadata_key in ("severity", "message"):
            if payload.get(metadata_key) is not None:
                spec[metadata_key] = str(payload[metadata_key])
        specs.append(spec)

    return specs


def _filter_ast_rule_specs(
    rules: list[dict[str, str]], filter_regex: str | None
) -> list[dict[str, str]]:
    if filter_regex is None:
        return rules
    try:
        compiled = re.compile(filter_regex)
    except re.error as exc:
        raise ValueError(f"Invalid --filter regex: {exc}") from exc
    return [rule for rule in rules if compiled.search(str(rule.get("id", "")))]


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
    scan_paths: list[str] | None = None,
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

    project_language = normalize_ast_language(project_cfg.get("language"))
    normalized_rules: list[dict[str, str]] = []
    for rule in rules:
        normalized_rule = dict(rule)
        normalized_rule["language"] = normalize_ast_language(rule.get("language"))
        normalized_rules.append(normalized_rule)
    rules = normalized_rules

    cfg = SearchConfig(
        ast=True,
        ast_prefer_native=True,
        lang=project_language,
    )
    root_dir = cast(Path, project_cfg["root_dir"])
    include_scan_paths_in_payload = bool(scan_paths)
    resolved_scan_paths = (
        [str(Path(scan_path).expanduser().resolve()) for scan_path in scan_paths]
        if scan_paths
        else [str(root_dir)]
    )
    scanner: DirectoryScanner | None = None
    resolved_candidate_files = (
        None if scan_paths else list(candidate_files) if candidate_files is not None else None
    )
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
    regex_rules: list[dict[str, str]] = []
    other_resolved: list[tuple[dict[str, str], SearchConfig, ComputeBackend]] = []
    wrapper_backend: object | None = None
    for rule in rules:
        if rule.get("engine") == "regex":
            regex_rules.append(rule)
            continue
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
            result = backend.search_many(resolved_scan_paths, rule["pattern"], config=rule_cfg)
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
                resolved_candidate_files, _ = _collect_candidate_files(scanner, resolved_scan_paths)
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

    for rule in regex_rules:
        backend_names_used.add("RegexRulesetBackend")
        if scanner is None:
            scanner = DirectoryScanner(cfg)
        if resolved_candidate_files is None:
            resolved_candidate_files, _ = _collect_candidate_files(scanner, resolved_scan_paths)

        pattern = re.compile(rule["pattern"])
        regex_matched_files: set[str] = set()
        regex_match_counts_by_file: dict[str, int] = {}
        regex_snippets_by_file: dict[str, list[dict[str, object]]] = {}
        regex_rule_occurrences: list[dict[str, object]] = []
        rule_matches = 0
        for current_file in resolved_candidate_files:
            try:
                lines = (
                    Path(current_file).read_text(encoding="utf-8", errors="replace").splitlines()
                )
            except OSError:
                continue
            for line_number, line_text in enumerate(lines, start=1):
                line_matches = list(pattern.finditer(line_text))
                if not line_matches:
                    continue
                match_count = len(line_matches)
                rule_matches += match_count
                regex_matched_files.add(current_file)
                regex_match_counts_by_file[current_file] = (
                    regex_match_counts_by_file.get(current_file, 0) + match_count
                )
                regex_rule_occurrences.append({
                    "file": current_file,
                    "line": line_number,
                })
                if include_evidence_snippets:
                    file_snippets = regex_snippets_by_file.setdefault(current_file, [])
                    for regex_match in line_matches:
                        if len(file_snippets) >= max_evidence_snippets_per_file:
                            break
                        file_snippets.append(
                            _truncate_evidence_snippet(
                                regex_match.group(0), max_evidence_snippet_chars
                            )
                        )

        _append_finding(
            rule=rule,
            rule_matches=rule_matches,
            matched_files=regex_matched_files,
            match_counts_by_file=regex_match_counts_by_file,
            snippets_by_file=regex_snippets_by_file,
            rule_occurrences=regex_rule_occurrences,
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
    if include_scan_paths_in_payload:
        payload["scan_paths"] = resolved_scan_paths
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
        "native"
        if (
            base_config.ast_prefer_native
            and supports_native_pattern
            and is_native_ast_language(base_config.lang)
        )
        else "wrapper"
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
The stable text-search contract is the validated common rg-compatible subset documented in docs/CONTRACTS.md.
Use --format rg --json when a tool needs ripgrep JSON Lines events; plain --json is tensor-grep aggregate JSON.

**Other Available Subcommands:**
- `tg calibrate`: Measure CPU vs GPU crossover thresholds
- `tg devices`: Print routable GPU device IDs and VRAM inventory
- `tg mcp`: Start the AI-assistant Model Context Protocol (MCP) server
- `tg classify`: Run log classification with local heuristics by default, or CyBERT when explicitly enabled
- `tg run`: Run a validated AST slice for structural search and guarded rewrites
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
    auto_hybrid_regex: bool = typer.Option(
        False,
        "--auto-hybrid-regex",
        help="Use ripgrep's hybrid regex engine selection when rg passthrough is selected.",
    ),
    no_auto_hybrid_regex: bool = typer.Option(
        False,
        "--no-auto-hybrid-regex",
        help="Disable ripgrep's hybrid regex engine selection; useful for config overrides.",
    ),
    unicode: bool = typer.Option(
        False, "--unicode", help="Enable Unicode mode for regex. This is the default."
    ),
    pcre2_unicode: bool = typer.Option(
        False,
        "--pcre2-unicode",
        help="Enable PCRE2 Unicode mode. Alias of --unicode in ripgrep.",
    ),
    no_pcre2_unicode: bool = typer.Option(
        False, "--no-pcre2-unicode", help="Disable PCRE2 Unicode mode."
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
    no_text: bool = typer.Option(False, "--no-text", help="Do not search binary files as text."),
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
    no_binary: bool = typer.Option(
        False, "--no-binary", help="Do not search binary files unless --text is set."
    ),
    follow: bool = typer.Option(False, "-L", "--follow", help="Follow symbolic links."),
    no_follow: bool = typer.Option(
        False, "--no-follow", help="Do not follow symbolic links; useful for config overrides."
    ),
    glob: list[str] | None = typer.Option(
        None, "-g", "--glob", help="Include/exclude files matching glob."
    ),
    glob_case_insensitive: bool = typer.Option(
        False, "--glob-case-insensitive", help="Process glob patterns case insensitively."
    ),
    no_glob_case_insensitive: bool = typer.Option(
        False,
        "--no-glob-case-insensitive",
        help="Process glob patterns case sensitively; useful for config overrides.",
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
    no_ignore_file_case_insensitive: bool = typer.Option(
        False,
        "--no-ignore-file-case-insensitive",
        help="Process ignore files case sensitively; useful for config overrides.",
    ),
    max_depth: int | None = typer.Option(
        None, "-d", "--max-depth", "--maxdepth", help="Limit depth of directory traversal."
    ),
    max_filesize: str | None = typer.Option(
        None, "--max-filesize", help="Ignore files larger than this size."
    ),
    no_ignore: bool = typer.Option(
        False, "--no-ignore", help="Don't respect ignore files (.gitignore, .rgignore, etc)."
    ),
    ignore: bool = typer.Option(
        False, "--ignore", help="Respect ignore files; useful for overriding ripgrep config."
    ),
    no_ignore_dot: bool = typer.Option(
        False, "--no-ignore-dot", help="Don't respect .ignore or .rgignore files."
    ),
    ignore_dot: bool = typer.Option(
        False, "--ignore-dot", help="Respect .ignore and .rgignore files."
    ),
    no_ignore_exclude: bool = typer.Option(
        False, "--no-ignore-exclude", help="Don't respect .git/info/exclude."
    ),
    ignore_exclude: bool = typer.Option(
        False, "--ignore-exclude", help="Respect .git/info/exclude."
    ),
    no_ignore_files: bool = typer.Option(
        False, "--no-ignore-files", help="Ignore any --ignore-file flags."
    ),
    ignore_files: bool = typer.Option(False, "--ignore-files", help="Respect --ignore-file flags."),
    no_ignore_global: bool = typer.Option(
        False, "--no-ignore-global", help="Don't respect global gitignore."
    ),
    ignore_global: bool = typer.Option(
        False, "--ignore-global", help="Respect global gitignore files."
    ),
    ignore_messages: bool = typer.Option(
        False, "--ignore-messages", help="Show ignore file parsing errors."
    ),
    no_ignore_parent: bool = typer.Option(
        False, "--no-ignore-parent", help="Don't respect ignore files in parent directories."
    ),
    ignore_parent: bool = typer.Option(
        False, "--ignore-parent", help="Respect ignore files in parent directories."
    ),
    ignore_vcs: bool = typer.Option(
        False, "--ignore-vcs", help="Respect source control ignore files."
    ),
    no_ignore_vcs: bool = typer.Option(
        False, "--no-ignore-vcs", help="Don't respect source control ignore files (.gitignore)."
    ),
    no_require_git: bool = typer.Option(
        False, "--no-require-git", help="Respect .gitignore even outside of git repos."
    ),
    require_git: bool = typer.Option(
        False,
        "--require-git",
        help="Require a git repo before respecting git ignore rules.",
    ),
    no_hidden: bool = typer.Option(
        False, "--no-hidden", help="Do not search hidden files and directories."
    ),
    one_file_system: bool = typer.Option(
        False, "--one-file-system", help="Don't cross file system boundaries."
    ),
    no_one_file_system: bool = typer.Option(
        False, "--no-one-file-system", help="Allow crossing file system boundaries."
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
    no_block_buffered: bool = typer.Option(
        False, "--no-block-buffered", help="Disable forced block buffering."
    ),
    byte_offset: bool = typer.Option(
        False, "-b", "--byte-offset", help="Print 0-based byte offset before each output line."
    ),
    no_byte_offset: bool = typer.Option(
        False, "--no-byte-offset", help="Do not print byte offsets."
    ),
    color: str = typer.Option(
        "auto", "--color", help="When to use colors: never, auto, always, ansi."
    ),
    colors: list[str] | None = typer.Option(
        None, "--colors", help="Color settings for output (e.g. 'match:fg:magenta')."
    ),
    column: bool = typer.Option(False, "--column", help="Show column numbers (1-based)."),
    no_column: bool = typer.Option(False, "--no-column", help="Do not show column numbers."),
    context: int | None = typer.Option(
        None, "-C", "--context", help="Show NUM lines before and after each match."
    ),
    context_separator: str = typer.Option(
        "--", "--context-separator", help="String used to separate non-contiguous context lines."
    ),
    no_context_separator: bool = typer.Option(
        False, "--no-context-separator", help="Disable explicit context separators."
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
    no_include_zero: bool = typer.Option(
        False, "--no-include-zero", help="Do not print zero match counts with -c."
    ),
    line_buffered: bool = typer.Option(False, "--line-buffered", help="Force line buffering."),
    no_line_buffered: bool = typer.Option(
        False, "--no-line-buffered", help="Disable forced line buffering."
    ),
    line_number: bool | None = typer.Option(
        None, "-n", "--line-number", help="Show line numbers (1-based)."
    ),
    no_line_number: bool = typer.Option(
        False, "-N", "--no-line-number", help="Suppress line numbers."
    ),
    max_columns: int | None = typer.Option(
        None, "-M", "--max-columns", help="Omit lines longer than this limit."
    ),
    max_columns_preview: bool = typer.Option(
        False, "--max-columns-preview", help="Preview lines exceeding max column limit."
    ),
    no_max_columns_preview: bool = typer.Option(
        False, "--no-max-columns-preview", help="Do not preview lines exceeding max column limit."
    ),
    null: bool = typer.Option(False, "-0", "--null", help="Follow file paths with a NUL byte."),
    only_matching: bool = typer.Option(
        False, "-o", "--only-matching", help="Print only the matched parts of a line."
    ),
    path_separator: str | None = typer.Option(
        None, "--path-separator", help="Path separator to use."
    ),
    passthru: bool = typer.Option(
        False,
        "--passthru",
        "--passthrough",
        help="Print both matching and non-matching lines.",
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
    sort_files: bool = typer.Option(
        False,
        "--sort-files",
        help="Deprecated ripgrep alias for --sort path; disables parallel traversal.",
    ),
    trim: bool = typer.Option(False, "--trim", help="Remove leading ASCII whitespace from output."),
    no_trim: bool = typer.Option(
        False, "--no-trim", help="Do not remove leading ASCII whitespace from output."
    ),
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
    json: bool = typer.Option(
        False,
        "--json",
        help=(
            "Print results as one tensor-grep aggregate JSON object, not rg JSON Lines. "
            "Use --format rg --json for ripgrep JSON Lines or --ndjson for tensor-grep streaming output."
        ),
    ),
    no_json: bool = typer.Option(
        False, "--no-json", help="Disable ripgrep JSON Lines when overriding rg config."
    ),
    ndjson: bool = typer.Option(
        False,
        "--ndjson",
        help="Print tensor-grep newline-delimited JSON rows, not the rg event schema.",
    ),
    # LOGGING OPTIONS
    debug: bool = typer.Option(False, "--debug", help="Show debug messages."),
    no_ignore_messages: bool = typer.Option(
        False, "--no-ignore-messages", help="Suppress ignore file parsing errors."
    ),
    no_messages: bool = typer.Option(
        False, "--no-messages", help="Suppress some error messages (like failed file opens)."
    ),
    messages: bool = typer.Option(
        False, "--messages", help="Show normal diagnostic messages; overrides ripgrep config."
    ),
    stats: bool = typer.Option(False, "--stats", help="Print aggregate statistics."),
    no_stats: bool = typer.Option(False, "--no-stats", help="Do not print aggregate statistics."),
    trace: bool = typer.Option(False, "--trace", help="Show exhaustive trace messages."),
    # OTHER BEHAVIORS
    files: bool = typer.Option(
        False, "--files", help="Print files that would be searched and exit."
    ),
    generate: str | None = typer.Option(
        None,
        "--generate",
        help=(
            "Generate shell completion output "
            "(complete-bash, complete-zsh, complete-fish, complete-powershell)."
        ),
    ),
    no_config: bool = typer.Option(False, "--no-config", help="Never read configuration files."),
    pcre2_version: bool = typer.Option(
        False, "--pcre2-version", help="Print PCRE2 version and exit."
    ),
    type_list: bool = typer.Option(
        False, "--type-list", help="Show all supported file types and exit."
    ),
    version: bool = typer.Option(False, "-V", "--version", help="Show tensor-grep version."),
    # TENSOR-GREP SPECIFIC
    cpu: bool = typer.Option(
        False,
        "--cpu",
        "--force-cpu",
        help="Force CPU fallback (tensor-grep specific).",
    ),
    format_type: str = typer.Option(
        "rg",
        "--format",
        help="Output format: rg, json, table, or csv. Use rg for exact ripgrep-style text output.",
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
    allow_broad_generated_scan: bool = typer.Option(
        False,
        "--allow-broad-generated-scan",
        help=(
            "Permit unbounded file-list/search scans through generated, cache, or dependency "
            "directories. Prefer scoped paths, --glob, --type, or --max-depth for agent runs."
        ),
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
    regexp_patterns = regexp or []
    if generate is not None:
        typer.echo(_generate_shell_completion_script(generator=generate))
        raise typer.Exit(0)
    if version:
        typer.echo(f"tensor-grep {_cli_package_version()}")
        raise typer.Exit(0)
    if pcre2_version:
        _run_rg_compatible_info_action(
            "--pcre2-version",
            "PCRE2 version unavailable: no native tg or ripgrep binary found.",
        )
    if type_list:
        _run_rg_compatible_info_action(
            "--type-list",
            "Type list unavailable: no native tg or ripgrep binary found.",
        )
    if files:
        paths_to_search = args or ["."]
        paths_defaulted = not args
    elif regexp_patterns:
        pattern = regexp_patterns[0]
        if pattern == "":
            _exit_search_error(
                "empty_pattern",
                "PATTERN must not be empty.",
                json_mode=json,
            )
        paths_to_search = args or ["."]
        paths_defaulted = not args
    else:
        if not args:
            typer.echo("Error: Please provide a PATTERN to search.", err=True)
            sys.exit(1)
        pattern = args[0]
        if pattern == "":
            _exit_search_error(
                "empty_pattern",
                "PATTERN must not be empty.",
                json_mode=json,
            )
        paths_to_search = args[1:] or ["."]
        paths_defaulted = not args[1:]

    if not files:
        missing_paths = [
            path for path in paths_to_search if path != "-" and not Path(path).exists()
        ]
        if missing_paths:
            if json:
                detail = "search path does not exist: " + ", ".join(missing_paths)
                _exit_search_error("path_not_found", detail, json_mode=True)
            else:
                for missing_path in missing_paths:
                    typer.echo(
                        f"Error: search path does not exist: {missing_path}",
                        err=True,
                    )
                sys.exit(2)

    if no_line_number:
        line_number = False
    elif line_number is None:
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
        auto_hybrid_regex=auto_hybrid_regex,
        no_auto_hybrid_regex=no_auto_hybrid_regex,
        no_unicode=no_unicode,
        unicode=unicode,
        pcre2_unicode=pcre2_unicode,
        no_pcre2_unicode=no_pcre2_unicode,
        null_data=null_data,
        pcre2=pcre2,
        regex_size_limit=regex_size_limit,
        smart_case=smart_case,
        stop_on_nonmatch=stop_on_nonmatch,
        text=text,
        no_text=no_text,
        threads=threads,
        word_regexp=word_regexp,
        binary=binary,
        no_binary=no_binary,
        follow=follow,
        no_follow=no_follow,
        glob=glob,
        glob_case_insensitive=glob_case_insensitive,
        no_glob_case_insensitive=no_glob_case_insensitive,
        hidden=hidden,
        iglob=iglob,
        ignore_file=ignore_file,
        ignore_file_case_insensitive=ignore_file_case_insensitive,
        no_ignore_file_case_insensitive=no_ignore_file_case_insensitive,
        max_depth=max_depth,
        max_filesize=max_filesize,
        ignore=ignore,
        no_ignore=no_ignore,
        ignore_dot=ignore_dot,
        no_ignore_dot=no_ignore_dot,
        ignore_exclude=ignore_exclude,
        no_ignore_exclude=no_ignore_exclude,
        ignore_files=ignore_files,
        no_ignore_files=no_ignore_files,
        ignore_global=ignore_global,
        no_ignore_global=no_ignore_global,
        ignore_parent=ignore_parent,
        no_ignore_parent=no_ignore_parent,
        ignore_vcs=ignore_vcs,
        no_ignore_vcs=no_ignore_vcs,
        no_require_git=no_require_git,
        require_git=require_git,
        no_hidden=no_hidden,
        one_file_system=one_file_system,
        no_one_file_system=no_one_file_system,
        file_type=type,
        type_not=type_not,
        type_add=type_add,
        type_clear=type_clear,
        unrestricted=unrestricted,
        after_context=after_context,
        before_context=before_context,
        block_buffered=block_buffered,
        no_block_buffered=no_block_buffered,
        byte_offset=byte_offset,
        no_byte_offset=no_byte_offset,
        color=color,
        colors=colors,
        column=column,
        no_column=no_column,
        context=context,
        context_separator=context_separator,
        no_context_separator=no_context_separator,
        field_context_separator=field_context_separator,
        field_match_separator=field_match_separator,
        heading=heading,
        hostname_bin=hostname_bin,
        hyperlink_format=hyperlink_format,
        include_zero=include_zero,
        no_include_zero=no_include_zero,
        line_buffered=line_buffered,
        no_line_buffered=no_line_buffered,
        line_number=line_number,
        max_columns=max_columns,
        max_columns_preview=max_columns_preview,
        no_max_columns_preview=no_max_columns_preview,
        null=null,
        only_matching=only_matching,
        path_separator=path_separator,
        passthru=passthru,
        pretty=pretty,
        quiet=quiet,
        replace_str=replace,
        sort_by=sort,
        sort_by_reverse=sortr,
        sort_files=sort_files,
        trim=trim,
        no_trim=no_trim,
        vimgrep=vimgrep,
        with_filename=with_filename or implicit_with_filename,
        no_filename=no_filename,
        count=count,
        count_matches=count_matches,
        files_with_matches=files_with_matches,
        files_without_match=files_without_match,
        json_mode=json,
        no_json=no_json,
        debug=debug,
        ignore_messages=ignore_messages,
        no_ignore_messages=no_ignore_messages,
        no_messages=no_messages,
        messages=messages,
        stats=stats,
        no_stats=no_stats,
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
    if not files:
        try:
            patterns_to_validate = regexp_patterns if regexp_patterns else [pattern]
            for regex_pattern in patterns_to_validate:
                _validate_search_regex(regex_pattern, config)
        except Exception as exc:
            if _is_invalid_regex_error(exc):
                _exit_invalid_regex(exc, json_mode=json)
            raise
    guarded_broad_root = _search_paths_include_guarded_broad_root(paths_to_search)
    explicit_hidden_search_root = not config.hidden and any(
        _path_has_hidden_component(path) for path in paths_to_search
    )
    refuse_generated_scan, generated_scan_dirs = _should_refuse_unbounded_generated_scan(
        paths_to_search,
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        files_mode=files,
    )
    if refuse_generated_scan:
        typer.echo(_format_broad_generated_scan_error(generated_scan_dirs), err=True)
        raise typer.Exit(2)

    explicit_rg_format = _explicit_rg_format_requested(format_value=format_type)
    native_tg_binary = resolve_native_tg_binary()
    if (
        native_tg_binary is not None
        and not guarded_broad_root
        and not explicit_hidden_search_root
        and not (json and explicit_rg_format)
        and _can_delegate_to_native_tg_search(
            config,
            ndjson=ndjson,
            files_mode=files,
            files_with_matches=files_with_matches,
            files_without_match=files_without_match,
            format_type=format_type,
        )
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
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.io.directory_scanner import DirectoryScanner

    rg_backend = RipgrepBackend()
    can_passthrough_rg = (
        not guarded_broad_root
        and not explicit_hidden_search_root
        and rg_backend.is_available()
        and _can_passthrough_rg(
            config,
            format_type=format_type,
            explicit_rg_format=explicit_rg_format,
            json_mode=json,
            ndjson_mode=ndjson,
            files_mode=files,
            files_with_matches=files_with_matches,
            files_without_match=files_without_match,
            only_matching=only_matching,
            stats_mode=stats,
        )
    )
    if can_passthrough_rg:
        if not stats:
            passthrough_paths = [] if paths_defaulted else paths_to_search
            with nvtx_range("search.passthrough_rg", color="green"):
                exit_code = rg_backend.search_passthrough(passthrough_paths, pattern, config=config)
            sys.exit(exit_code)

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
        passthrough_paths = [] if paths_defaulted else paths_to_search
        with nvtx_range("search.passthrough_rg", color="green"):
            exit_code = rg_backend.search_passthrough(passthrough_paths, pattern, config=config)
        sys.exit(exit_code)
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
    all_results.requested_gpu_device_ids = list(parsed_gpu_device_ids or [])
    all_results.routing_gpu_device_ids = selected_gpu_device_ids
    all_results.routing_gpu_chunk_plan_mb = selected_gpu_chunk_plan_mb
    search_start = time.perf_counter()
    matched_file_paths: set[str] = set()
    matched_file_paths_ordered: list[str] = []

    def _record_matched_file(file_path: str | None) -> None:
        if not file_path or file_path in matched_file_paths:
            return
        matched_file_paths.add(file_path)
        matched_file_paths_ordered.append(file_path)

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
        if guarded_broad_root:
            rg_search_config = _config_with_guarded_broad_root_globs(config)
        else:
            rg_search_config = config
        if explicit_hidden_search_root:
            rg_search_config = dataclasses.replace(rg_search_config, hidden=True)
        if files_without_match:
            rg_search_config = dataclasses.replace(
                rg_search_config,
                files_without_match=False,
            )
        search_targets = (
            paths_to_search
            if (guarded_broad_root or files_with_matches)
            else candidate_files_ordered
            if files_without_match
            else paths_to_search
        )
        span_ctx = (
            tracer.start_as_current_span("search.file") if tracer is not None else nullcontext()
        )
        with span_ctx as span, nvtx_range("search.file", color="cyan"):
            if span is not None:
                span.set_attribute("backend", backend.__class__.__name__)
                span.set_attribute("path_count", len(search_targets))
            try:
                result = rg_backend.search(search_targets, pattern, config=rg_search_config)
            except Exception as exc:
                if _is_invalid_regex_error(exc):
                    _exit_invalid_regex(exc, json_mode=json)
                raise
            if span is not None:
                span.set_attribute("matches", result.total_matches)
            all_results.matches.extend(result.matches)
            for matched_path in result.matched_file_paths:
                _record_matched_file(matched_path)
            _merge_count_metadata(result)
            all_results.total_matches += result.total_matches
            all_results.total_files += result.total_files
            for match in result.matches:
                _record_matched_file(match.file)
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
                try:
                    result = backend.search(current_file, pattern, config=config)
                except Exception as exc:
                    if _is_invalid_regex_error(exc):
                        _exit_invalid_regex(exc, json_mode=json)
                    raise
                if span is not None:
                    span.set_attribute("matches", result.total_matches)
            all_results.matches.extend(result.matches)
            for matched_path in result.matched_file_paths:
                _record_matched_file(matched_path)
            _merge_count_metadata(result)
            all_results.total_matches += result.total_matches
            if result.total_files > 0 or result.total_matches > 0:
                all_results.total_files += 1
                _record_matched_file(current_file)
            for match in result.matches:
                _record_matched_file(match.file)
            _merge_runtime_routing(result)

    if config.replace_str is not None:
        all_results.matches = _replace_lines(all_results.matches, pattern, config)

    if only_matching:
        all_results.matches = _only_matching_lines(all_results.matches, pattern, config)
        all_results.total_matches = len(all_results.matches)
        all_results.total_files = len({m.file for m in all_results.matches})
        matched_file_paths = {m.file for m in all_results.matches}
        matched_file_paths_ordered = []
        for match in all_results.matches:
            if match.file not in matched_file_paths_ordered:
                matched_file_paths_ordered.append(match.file)

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
            output_paths = _ordered_path_output(
                matched_file_paths_ordered or sorted(matched_files),
                config,
            )
            _write_path_list(output_paths, use_nul=null)
            sys.exit(0)
        _emit_stats()
        sys.exit(1)

    if files_without_match:
        unmatched_candidates = candidate_files_set - matched_files
        if not (config.text or config.binary):
            unmatched_candidates = {
                path for path in unmatched_candidates if not _looks_like_binary_path(path)
            }
        unmatched = _ordered_path_output(sorted(unmatched_candidates), config)
        if unmatched:
            _emit_stats()
            _write_path_list(unmatched, use_nul=null)
            sys.exit(0)
        _emit_stats()
        sys.exit(1)

    if all_results.is_empty:
        _emit_stats()
        if json or format_type == "json":
            from tensor_grep.cli.formatters.json_fmt import JsonFormatter

            _safe_stdout_line(JsonFormatter().format(all_results))
        sys.exit(1)

    if quiet:
        _emit_stats()
        sys.exit(0)

    formatter: OutputFormatter

    if ndjson:
        from tensor_grep.cli.formatters.json_fmt import NdjsonFormatter

        formatter = NdjsonFormatter()
    elif json or format_type == "json":
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

    _safe_stdout_line(formatter.format(all_results))
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
    max_files: int | None = typer.Option(
        None, "--max-files", min=1, help="Maximum source files to include in output."
    ),
    max_repo_files: int | None = typer.Option(
        None, "--max-repo-files", min=1, help="Maximum repo files to scan before returning."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a deterministic repository map for AI editing workflows."""
    from tensor_grep.cli.repo_map import (
        apply_repo_map_output_limits,
        build_repo_map,
        build_repo_map_json,
    )

    try:
        if json_output:
            typer.echo(
                build_repo_map_json(
                    path,
                    max_files=max_files,
                    max_repo_files=max_repo_files,
                )
            )
            return

        payload = build_repo_map(path, max_repo_files=max_repo_files)
        payload = apply_repo_map_output_limits(payload, max_files=max_files)
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
    max_files: int | None = typer.Option(
        None, "--max-files", min=1, help="Maximum ranked source files to include."
    ),
    max_repo_files: int | None = typer.Option(
        None, "--max-repo-files", min=1, help="Maximum repo files to scan before ranking."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a ranked repository context pack for edit planning."""
    from tensor_grep.cli.repo_map import build_context_pack, build_context_pack_json

    try:
        if json_output:
            typer.echo(
                build_context_pack_json(
                    query,
                    path,
                    max_files=max_files,
                    max_repo_files=max_repo_files,
                )
            )
            return

        payload = build_context_pack(
            query,
            path,
            max_files=max_files,
            max_repo_files=max_repo_files,
        )
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
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
    render_profile: str | None = typer.Option(
        None,
        "--render-profile",
        help="Render profile: full, compact, or llm. Defaults to llm for JSON and full for text.",
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-phase profiling in JSON output."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready repository context bundle for edit planning."""
    from tensor_grep.cli.repo_map import build_context_render, build_context_render_json

    try:
        resolved_render_profile = render_profile or ("llm" if json_output else "full")
        resolved_optimize_context = optimize_context or (json_output and render_profile is None)
        if json_output:
            typer.echo(
                build_context_render_json(
                    query,
                    path,
                    max_files=max_files,
                    max_repo_files=max_repo_files,
                    max_sources=max_sources,
                    max_symbols_per_file=max_symbols_per_file,
                    max_render_chars=max_render_chars,
                    max_tokens=max_tokens,
                    model=model,
                    optimize_context=resolved_optimize_context,
                    render_profile=resolved_render_profile,
                    profile=profile,
                )
            )
            return

        payload = build_context_render(
            query,
            path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            max_tokens=max_tokens,
            model=model,
            optimize_context=resolved_optimize_context,
            render_profile=resolved_render_profile,
            profile=profile,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(payload["rendered_context"])


@app.command(name="agent")
def agent(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query: str = typer.Option(..., "--query", help="Natural-language task or symbol query."),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the capsule."
    ),
    max_sources: int = typer.Option(
        5, "--max-sources", min=1, help="Maximum exact source blocks to include."
    ),
    max_tokens: int | None = typer.Option(
        1200, "--max-tokens", min=1, help="Approximate maximum capsule snippet tokens."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    model: str | None = typer.Option(
        None, "--model", help="Future tokenizer model selector; currently accepted but ignored."
    ),
    gpu_device_ids: str | None = typer.Option(
        None,
        "--gpu-device-ids",
        help=(
            "Comma-separated GPU IDs for an opt-in native evidence scan. "
            "Sidecar routes are reported as unsupported."
        ),
    ),
    gpu_timeout_s: float = typer.Option(
        5.0,
        "--gpu-timeout-s",
        min=0.1,
        help="Maximum seconds for each opt-in agent GPU evidence command.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return an actionable context capsule for agents before editing."""
    from tensor_grep.cli.agent_capsule import build_agent_capsule, build_agent_capsule_json

    try:
        parsed_gpu_device_ids = _parse_gpu_device_ids_cli(gpu_device_ids)
        if json_output:
            typer.echo(
                build_agent_capsule_json(
                    query,
                    path,
                    max_files=max_files,
                    max_sources=max_sources,
                    max_tokens=max_tokens,
                    max_repo_files=max_repo_files,
                    model=model,
                    gpu_device_ids=parsed_gpu_device_ids,
                    gpu_timeout_s=gpu_timeout_s,
                )
            )
            return

        payload = build_agent_capsule(
            query,
            path,
            max_files=max_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_repo_files=max_repo_files,
            model=model,
            gpu_device_ids=parsed_gpu_device_ids,
            gpu_timeout_s=gpu_timeout_s,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    primary = payload.get("primary_target", {})
    primary_file = primary.get("file") or "<none>"
    primary_line = primary.get("line") or 1
    primary_symbol = primary.get("symbol") or "<unknown>"
    validation_commands = payload.get("validation_commands", [])
    confidence = payload.get("confidence", {}).get("overall", 0)
    gpu_acceleration = payload.get("gpu_acceleration", {})
    typer.echo(f"Agent capsule for {payload['path']}")
    typer.echo(f"query={payload['query']}")
    typer.echo(f"primary={primary_file}#L{primary_line} {primary_symbol}")
    typer.echo(f"validation={len(validation_commands)} commands")
    typer.echo(f"confidence={confidence}")
    if gpu_device_ids:
        typer.echo(f"gpu_acceleration={gpu_acceleration.get('status', 'unknown')}")


@app.command(name="edit-plan")
def edit_plan(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query: str = typer.Option(..., "--query", help="Query text used to rank edit targets."),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the plan."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repository files to scan before ranking edit targets.",
    ),
    max_sources: int | None = typer.Option(
        None,
        "--max-sources",
        min=1,
        help="Maximum related source/span records to retain in the plan.",
    ),
    max_tokens: int | None = typer.Option(
        None,
        "--max-tokens",
        min=1,
        help="Accepted for agent command-surface parity; edit-plan emits no rendered source text.",
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
                    max_repo_files=max_repo_files,
                    max_sources=max_sources,
                    max_tokens=max_tokens,
                    max_symbols=max_symbols,
                    profile=profile,
                )
            )
            return

        payload = build_context_edit_plan(
            query,
            path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return exact definition locations for a symbol."""
    from tensor_grep.cli.repo_map import build_symbol_defs, build_symbol_defs_json

    try:
        if json_output:
            typer.echo(
                build_symbol_defs_json(
                    symbol,
                    path,
                    semantic_provider=provider,
                    max_repo_files=max_repo_files,
                )
            )
            return

        payload = build_symbol_defs(
            symbol,
            path,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
        )
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return exact source blocks for a symbol definition."""
    from tensor_grep.cli.repo_map import build_symbol_source, build_symbol_source_json

    try:
        if json_output:
            typer.echo(
                build_symbol_source_json(
                    symbol,
                    path,
                    semantic_provider=provider,
                    max_repo_files=max_repo_files,
                )
            )
            return

        payload = build_symbol_source(
            symbol,
            path,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
        )
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return likely impacted files and tests for a symbol change."""
    from tensor_grep.cli.repo_map import build_symbol_impact, build_symbol_impact_json

    try:
        if json_output:
            typer.echo(
                build_symbol_impact_json(
                    symbol,
                    path,
                    semantic_provider=provider,
                    max_repo_files=max_repo_files,
                )
            )
            return

        payload = build_symbol_impact(
            symbol,
            path,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Impact for {payload['symbol']} in {payload['path']}")
    typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")
    typer.echo("preferred=blast-radius for direct symbol impact")


@app.command()
def refs(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol: str = typer.Option(..., "--symbol", help="Exact symbol name to resolve."),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return Python-first symbol references across the inventory root."""
    from tensor_grep.cli.repo_map import build_symbol_refs, build_symbol_refs_json

    try:
        if json_output:
            typer.echo(
                build_symbol_refs_json(
                    symbol,
                    path,
                    semantic_provider=provider,
                    max_repo_files=max_repo_files,
                )
            )
            return

        payload = build_symbol_refs(
            symbol,
            path,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
        )
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return Python-first call sites and likely impacted tests for a symbol."""
    from tensor_grep.cli.repo_map import build_symbol_callers, build_symbol_callers_json

    try:
        if json_output:
            typer.echo(
                build_symbol_callers_json(
                    symbol,
                    path,
                    semantic_provider=provider,
                    max_repo_files=max_repo_files,
                )
            )
            return

        payload = build_symbol_callers(
            symbol,
            path,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
        )
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    max_callers: int | None = typer.Option(
        _DEFAULT_BLAST_RADIUS_JSON_MAX_CALLERS,
        "--max-callers",
        min=1,
        help="Maximum caller records to include in output; raise for fuller broad impact analysis.",
    ),
    max_files: int | None = typer.Option(
        _DEFAULT_BLAST_RADIUS_JSON_MAX_FILES,
        "--max-files",
        min=1,
        help="Maximum impacted files to include in output; raise for fuller broad impact analysis.",
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
                    symbol,
                    path,
                    max_depth=max_depth,
                    semantic_provider=provider,
                    max_repo_files=max_repo_files,
                    max_callers=max_callers,
                    max_files=max_files,
                )
            )
            return

        payload = build_symbol_blast_radius(
            symbol,
            path,
            max_depth=max_depth,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
            max_callers=max_callers,
            max_files=max_files,
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
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
                    max_repo_files=max_repo_files,
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
            max_repo_files=max_repo_files,
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
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
                    max_repo_files=max_repo_files,
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
            max_repo_files=max_repo_files,
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
    max_sources: int | None = typer.Option(
        None,
        "--max-sources",
        min=1,
        help="Maximum related source/span records to retain in the plan.",
    ),
    max_tokens: int | None = typer.Option(
        None,
        "--max-tokens",
        min=1,
        help="Accepted for agent command-surface parity; edit-plan emits no rendered source text.",
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
                    "max_sources": max_sources,
                    "max_tokens": max_tokens,
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
                max_sources=max_sources,
                max_tokens=max_tokens,
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
    typer.echo(f"Undo command: {payload.undo_command}")


@checkpoint_app.command("list")
def checkpoint_list(
    path: str = typer.Argument(".", help="File or directory rooted at the checkpoint scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    discover: bool = typer.Option(
        False,
        "--discover",
        help="Recursively discover checkpoint scopes under PATH instead of listing one detected scope.",
    ),
) -> None:
    """List available checkpoints."""
    from tensor_grep.cli.checkpoint_store import (
        describe_checkpoint_scope,
        discover_checkpoint_scopes,
    )

    try:
        if discover:
            scope_payloads: list[dict[str, Any]] = [
                {
                    "root": scope.root,
                    "mode": scope.mode,
                    "checkpoint_count": scope.checkpoint_count,
                    "checkpoints": [record.__dict__ for record in scope.checkpoints],
                }
                for scope in discover_checkpoint_scopes(path)
            ]
            checkpoint_count = sum(
                int(cast(int, scope_payload["checkpoint_count"]))
                for scope_payload in scope_payloads
            )
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "version": 1,
                            "path": str(Path(path).expanduser().resolve()),
                            "checkpoint_count": checkpoint_count,
                            "discovered_scopes": scope_payloads,
                        },
                        indent=2,
                    )
                )
                return

            if not scope_payloads:
                typer.echo(f"No checkpoint scopes found under {Path(path).expanduser().resolve()}.")
                return

            typer.echo(
                f"Discovered {checkpoint_count} checkpoint(s) across {len(scope_payloads)} scope(s)."
            )
            for scope_payload in scope_payloads:
                typer.echo(
                    f"Checkpoint root: {scope_payload['root']} "
                    f"({scope_payload['mode']}, count={scope_payload['checkpoint_count']})"
                )
                checkpoint_records = cast(list[dict[str, object]], scope_payload["checkpoints"])
                for record in checkpoint_records:
                    typer.echo(
                        f"  {record['checkpoint_id']}  {record['mode']}  "
                        f"{record['created_at']}  files={record['file_count']}"
                    )
            return

        scope_result = describe_checkpoint_scope(path)
        records = [record.__dict__ for record in scope_result.checkpoints]
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "version": 1,
                    "root": scope_result.root,
                    "mode": scope_result.mode,
                    "checkpoint_count": scope_result.checkpoint_count,
                    "checkpoints": records,
                },
                indent=2,
            )
        )
        return

    if not records:
        typer.echo(f"Checkpoint root: {scope_result.root} ({scope_result.mode})")
        typer.echo("No checkpoints found under this scope.")
        typer.echo("Use `tg checkpoint list PATH --discover` to search child scopes explicitly.")
        return

    typer.echo(
        f"Checkpoint root: {scope_result.root} "
        f"({scope_result.mode}, count={scope_result.checkpoint_count})"
    )
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
    file_path: str,
    format_type: str = typer.Option("json", "--format", help="Output format"),
    max_lines: int = typer.Option(
        DEFAULT_CLASSIFY_MAX_LINES,
        "--max-lines",
        help="Maximum input lines to emit in JSON output (0 disables the cap).",
    ),
) -> None:
    """Run log classification with local heuristics or an explicit cyBERT provider."""
    import json

    from tensor_grep.io.reader_fallback import FallbackReader
    from tensor_grep.sidecar import (
        _apply_classify_line_budget,
        _classify_lines_with_metadata,
        _enrich_classifications,
    )

    reader = FallbackReader()
    lines = list(reader.read_lines(file_path))
    if not lines:
        sys.exit(1)

    budgeted_lines, line_budget = _apply_classify_line_budget(lines, max_lines)
    results, classification_backend = _classify_lines_with_metadata(budgeted_lines)

    if format_type == "json":
        data = {
            "classification_backend": classification_backend,
            "line_budget": line_budget,
            "classifications": _enrich_classifications(
                results,
                budgeted_lines,
                source_path=file_path,
            ),
        }
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
    paths: list[str] | None = typer.Argument(
        None,
        help="Optional scan paths for tensor-grep's bounded AST scan slice.",
    ),
    config: str | None = typer.Option(
        "sgconfig.yml", "--config", "-c", help="Path to ast-grep root config"
    ),
    rule_file: str | None = typer.Option(
        None,
        "--rule",
        "-r",
        help="Scan with a single ast-grep rule file without requiring sgconfig.",
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
    filter_regex: str | None = typer.Option(
        None,
        "--filter",
        "-f",
        help="Filter loaded rule IDs with a regex before scanning.",
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
    """Scan code with tensor-grep's bounded AST rule/config surface."""
    from tensor_grep.cli.rule_packs import resolve_rule_pack

    inline_source_count = sum(item is not None for item in (ruleset, inline_rules, rule_file))
    if inline_source_count > 1:
        typer.echo("Error: --rule, --inline-rules, and --ruleset are mutually exclusive.", err=True)
        sys.exit(1)
    if rule_file is not None and filter_regex is not None:
        typer.echo("Error: --filter is incompatible with --rule.", err=True)
        sys.exit(1)
    scan_paths = list(paths or [])
    if scan_paths and path != ".":
        typer.echo("Error: positional PATHS are incompatible with --path.", err=True)
        sys.exit(1)
    effective_scan_paths = scan_paths or [path]

    candidate_files: list[str] | None = None
    project_scan_fast_path = False
    if ruleset:
        ruleset_language = normalize_ast_language(language) if language is not None else None
        try:
            ruleset_meta, rules = resolve_rule_pack(ruleset, ruleset_language)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        project_cfg: dict[str, object] = {
            "config_path": f"builtin:{ruleset_meta['name']}",
            "root_dir": Path(effective_scan_paths[0]).resolve(),
            "rule_dirs": [],
            "test_dirs": [],
            "language": ruleset_meta["language"],
        }
        scan_banner = (
            "Scanning project using built-in ruleset "
            f"{ruleset_meta['name']} ({ruleset_meta['language']})"
        )
        routing_reason = "builtin-ruleset-scan"
    elif rule_file is not None:
        rule_path = Path(rule_file).expanduser().resolve()
        try:
            rules = _load_inline_rule_specs(
                rule_path.read_text(encoding="utf-8"),
                default_language=language,
            )
        except OSError as exc:
            typer.echo(f"Error: failed to read rule file {rule_path}: {exc}", err=True)
            sys.exit(1)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        if not rules:
            typer.echo(f"Error: No valid rule was found in {rule_path}.", err=True)
            sys.exit(1)
        inferred_language = (
            normalize_ast_language(language) if language else str(rules[0]["language"])
        )
        project_cfg = {
            "config_path": rule_path,
            "root_dir": Path(effective_scan_paths[0]).resolve(),
            "rule_dirs": [],
            "test_dirs": [],
            "language": inferred_language,
        }
        scan_banner = f"Scanning project using rule file {rule_path}"
        routing_reason = "ast-single-rule-scan"
    elif inline_rules is not None:
        try:
            rules = _load_inline_rule_specs(inline_rules, default_language=language)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)
        if not rules:
            typer.echo("Error: No valid inline rules were found.", err=True)
            sys.exit(1)
        inferred_language = (
            normalize_ast_language(language) if language else str(rules[0]["language"])
        )
        project_cfg = {
            "config_path": "inline-rules",
            "root_dir": Path(effective_scan_paths[0]).resolve(),
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
        try:
            rules = _filter_ast_rule_specs(rules, filter_regex)
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            sys.exit(1)

        if not rules:
            typer.echo(
                "Error: No valid rules found after applying configuration and filters.",
                err=True,
            )
            sys.exit(1)
        scan_banner = "Scanning project using adaptive AST routing"
        routing_reason = "ast-project-scan"
        project_scan_fast_path = True
        if scan_paths:
            project_scan_fast_path = False

    if not json_output:
        typer.echo(f"{scan_banner} based on {project_cfg['config_path']}...")
    try:
        payload = _run_ast_scan_payload(
            project_cfg,
            rules,
            routing_reason=routing_reason,
            scan_paths=scan_paths or None,
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
    except (ValueError, RuntimeError) as exc:
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
    """Test structural rules in tensor-grep's bounded AST workflow slice."""
    exit_code = ast_workflows.test_command(config)
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


def _validate_ast_new_name(name: str) -> None:
    if not name.strip() or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"Invalid item name {name!r}; use a bare rule/test/util identifier.")


def _write_ast_project_scaffold(base_dir: Path, lang: str) -> Path:
    import yaml

    config_path = base_dir / "sgconfig.yml"
    if config_path.exists():
        raise FileExistsError(f"Project already initialized ({config_path} exists).")

    config_data = {
        "ruleDirs": ["rules"],
        "testDirs": ["tests"],
        "utilsDir": "utils",
        "language": lang,
    }

    base_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.dump(config_data), encoding="utf-8")

    rules_dir = base_dir / "rules"
    tests_dir = base_dir / "tests"
    rules_dir.mkdir(exist_ok=True)
    tests_dir.mkdir(exist_ok=True)
    (rules_dir / "sample-rule.yml").write_text(
        f"id: sample-rule\nlanguage: {lang}\nrule:\n  pattern: 'print($$$ARGS)'\n",
        encoding="utf-8",
    )
    (tests_dir / "sample-test.yml").write_text(
        'id: sample-test\nruleId: sample-rule\nvalid:\n  - "pass"\ninvalid:\n'
        '  - "print(\\"hello\\")"\n',
        encoding="utf-8",
    )
    return config_path


@app.command()
def new(
    command: str | None = typer.Argument(
        None,
        help="Scaffold kind for tensor-grep's bounded AST workflow: project, rule, test, or util.",
    ),
    name: str | None = typer.Argument(None, help="Name for rule/test/util scaffolds."),
    lang: str = typer.Option("python", "--lang", "-l", help="Language for generated items."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Accept default scaffold choices without prompting."
    ),
    base_dir: Path = typer.Option(
        Path("."), "--base-dir", "-b", help="Directory where scaffold files are created."
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to sgconfig.yml for selecting configured rule/test/util directories.",
    ),
) -> None:
    """Create bounded AST workflow project, rule, test, or util scaffolds."""
    _ = yes
    scaffold_kind = command or "project"
    try:
        if scaffold_kind == "project":
            if name is not None:
                raise ValueError("tg new project does not accept a name; use --base-dir DIR.")
            config_path = _write_ast_project_scaffold(base_dir, lang)
            typer.echo(f"Initialized new tensor-grep structural search project in {config_path}.")
            return

        if scaffold_kind not in {"rule", "test", "util"}:
            raise ValueError(
                "Unsupported scaffold kind "
                f"{scaffold_kind!r}; expected project, rule, test, or util."
            )
        if name is None:
            raise ValueError(f"tg new {scaffold_kind} requires a name.")
        _validate_ast_new_name(name)

        project_cfg: dict[str, object] | None = None
        if config is not None:
            project_cfg = _load_sg_project_config(config)

        if scaffold_kind == "rule":
            target_dir = (
                cast(Path, project_cfg["root_dir"]) / cast(list[str], project_cfg["rule_dirs"])[0]
                if project_cfg is not None
                else base_dir / "rules"
            )
            target_path = target_dir / f"{name}.yml"
            contents = f"id: {name}\nlanguage: {lang}\nrule:\n  pattern: ''\n"
        elif scaffold_kind == "test":
            target_dir = (
                cast(Path, project_cfg["root_dir"]) / cast(list[str], project_cfg["test_dirs"])[0]
                if project_cfg is not None
                else base_dir / "tests"
            )
            target_path = target_dir / f"{name}.yml"
            contents = f"id: {name}\nruleId: {name}\nvalid:\n  - ''\ninvalid: []\n"
        else:
            target_dir = (
                cast(Path, project_cfg["root_dir"]) / cast(str, project_cfg["utils_dir"])
                if project_cfg is not None
                else base_dir / "utils"
            )
            target_path = target_dir / f"{name}.yml"
            contents = f"id: {name}\npattern: ''\n"

        if target_path.exists():
            raise FileExistsError(f"Scaffold target already exists: {target_path}")
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path.write_text(contents, encoding="utf-8")
    except (FileExistsError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Created {scaffold_kind} scaffold in {target_path}.")


@app.command(name="dogfood")
def dogfood(
    root: Path = typer.Option(Path("."), "--root", help="Repository root to validate."),
    output: Path | None = typer.Option(None, "--output", help="Optional JSON report path."),
    expected_version: str | None = typer.Option(
        None, "--expected-version", help="Expected tensor-grep version. Defaults to pyproject."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    progress: str = typer.Option(
        "auto",
        "--progress",
        help="Progress reporting mode: auto, always, or never. Emits to stderr only.",
    ),
    progress_interval_s: float = typer.Option(
        30.0,
        "--progress-interval-s",
        help="Seconds between progress heartbeats for the active phase.",
    ),
    no_shell_probes: bool = typer.Option(
        False, "--no-shell-probes", help="Skip public shell version probes."
    ),
    no_wsl_probe: bool = typer.Option(False, "--no-wsl-probe", help="Skip the optional WSL probe."),
) -> None:
    """Run the agent-readiness dogfood gate and emit a release-readiness verdict."""
    from tensor_grep.cli.dogfood import run_dogfood_readiness
    from tensor_grep.cli.progress import normalize_progress_mode

    try:
        progress_mode = normalize_progress_mode(progress)
        if progress_interval_s <= 0:
            raise ValueError("progress interval must be greater than 0")
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    exit_code, report = run_dogfood_readiness(
        root=root,
        output=output,
        expected_version=expected_version,
        include_shell_probes=not no_shell_probes,
        include_wsl_probe=not no_wsl_probe,
        progress_mode=progress_mode,
        progress_interval_s=progress_interval_s,
        json_output=json_output,
    )
    if json_output:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = cast(dict[str, object], report["agent_readiness"]).get("summary")
        if not isinstance(summary, dict):
            summary = {}
        verdict = cast(dict[str, object], report["verdict"])
        typer.echo(f"Dogfood verdict: {verdict['status']}")
        typer.echo(
            "agent-readiness: "
            f"passed={summary.get('passed', 0)} "
            f"failed={summary.get('failed', 0)} "
            f"skipped={summary.get('skipped', 0)}"
        )
        world_class_readiness = report.get("world_class_readiness")
        if isinstance(world_class_readiness, dict):
            typer.echo(f"world-class claim: {world_class_readiness.get('status', 'unknown')}")
        if output is not None:
            typer.echo(f"report: {output}")
        failed_checks = verdict.get("failed_checks")
        if isinstance(failed_checks, list) and failed_checks:
            typer.echo("failed checks: " + ", ".join(str(check) for check in failed_checks))
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@app.command()
def lsp(
    provider: str = typer.Option(
        "native",
        "--provider",
        help=(
            "Experimental semantic provider mode. native=repo-map only, "
            "lsp=external provider only, hybrid=merge both."
        ),
    ),
) -> None:
    """Start the structural search language server.

    Examples:
      tg lsp
      tg lsp --provider native
      tg lsp --provider lsp
      tg lsp --provider hybrid

    External LSP providers are experimental semantic evidence. Provider
    availability means the binary was found, not that initialization or
    navigation requests have succeeded.

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
    """Install managed external LSP providers.

    Setup availability does not prove semantic navigation. Use
    `tg doctor --with-lsp --json` and inspect health_status / health_check plus
    navigation lsp_proof fields before treating LSP evidence as dependable.
    """
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


@app.command(name="repair-launcher")
def repair_launcher(
    allow_foreign_rename: bool = typer.Option(
        False,
        "--allow-foreign-rename",
        help=(
            "Move aside the first foreign Windows tg.exe selected by Python subprocess "
            "resolution and replace it with the managed tensor-grep native front door. "
            "Use only when you own that foreign command."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Repair Windows Python subprocess tg resolution when explicitly allowed."""
    payload = _repair_windows_python_subprocess_launcher(allow_foreign_rename=allow_foreign_rename)
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(payload["message"])
        if payload.get("backup_path"):
            typer.echo(f"backup_path: {payload['backup_path']}")
        if payload.get("replaced_path"):
            typer.echo(f"replaced_path: {payload['replaced_path']}")
        if payload.get("post_repair_version"):
            typer.echo(f"post_repair_version: {payload['post_repair_version']}")

    if str(payload.get("status") or "").startswith(("blocked", "failed")):
        raise typer.Exit(code=1)


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
        help=(
            "Include external LSP provider diagnostics. Provider availability is "
            "not navigation proof; inspect health_status and health_check."
        ),
    ),
) -> None:
    """Print system, GPU, cache, daemon, and provider-proof diagnostics."""
    payload = _build_doctor_payload(path, config=config, with_lsp=with_lsp)
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(_render_doctor_payload(payload))


@app.command()
def upgrade() -> None:
    """Upgrade tensor-grep to the latest version published on PyPI."""
    import importlib.metadata

    def _upgrade_attempts(package_spec: str) -> list[tuple[str, list[str]]]:
        pip_cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--no-cache-dir",
            package_spec,
        ]
        return [
            (
                "uv",
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    sys.executable,
                    "--upgrade",
                    "--refresh-package",
                    "tensor-grep",
                    package_spec,
                ],
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
                        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
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

    def _schedule_windows_self_upgrade(
        attempts: list[tuple[str, list[str]]],
        expected_version: str,
        *,
        native_path: Path | None = None,
        native_assets: list[dict[str, str]] | None = None,
        bridge_paths: list[Path] | None = None,
    ) -> Path:
        import textwrap

        native_asset_payload = json.dumps(native_assets or [])
        bridge_payload = json.dumps([str(path) for path in bridge_paths or []])
        helper_code = textwrap.dedent(
            """
            import json
            import os
            import shutil
            import subprocess
            import sys
            import time
            import urllib.request
            from pathlib import Path
            from uuid import uuid4

            parent_pid = int(sys.argv[1])
            log_path = Path(sys.argv[2])
            attempts = json.loads(sys.argv[3])
            expected_version = sys.argv[4]
            native_path_arg = sys.argv[5]
            native_assets = json.loads(sys.argv[6])
            bridge_paths = [Path(path) for path in json.loads(sys.argv[7])]
            native_path = Path(native_path_arg) if native_path_arg else None
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

            def _verify_installed_version(expected_version: str) -> tuple[bool, str]:
                probe_code = (
                    "import importlib.metadata as m; "
                    "import tensor_grep; "
                    "print(m.version('tensor-grep'))"
                )
                try:
                    result = subprocess.run(
                        [sys.executable, "-c", probe_code],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                except FileNotFoundError as exc:
                    return False, f"post-upgrade verification failed: {exc}"
                except subprocess.CalledProcessError as exc:
                    stderr = (exc.stderr or "").strip()
                    stdout = (exc.stdout or "").strip()
                    combined = stderr or stdout or str(exc)
                    return False, f"post-upgrade verification failed: {combined}"
                version = (result.stdout or "").strip().splitlines()
                if not version:
                    return False, "post-upgrade verification failed: no tensor-grep version reported"
                installed_version = version[-1].strip()
                if expected_version and installed_version != expected_version:
                    return (
                        False,
                        "post-upgrade verification failed: expected tensor-grep "
                        + expected_version
                        + " but target Python reports "
                        + installed_version,
                    )
                return True, installed_version

            def _version(path: Path) -> str:
                result = subprocess.run([str(path), "--version"], capture_output=True, text=True)
                if result.returncode != 0:
                    return ""
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line:
                        return line
                return ""

            def _version_matches(version_text: str) -> bool:
                return bool(expected_version and expected_version in version_text)

            def _refresh_native_frontdoor_and_bridges() -> str:
                # refresh native front door and stale PATH tensor-grep front-door copies after locked self-upgrade
                if not expected_version or native_path is None:
                    return ""

                messages: list[str] = []
                native_path.parent.mkdir(parents=True, exist_ok=True)
                current_native_version = _version(native_path) if native_path.is_file() else ""
                if not _version_matches(current_native_version):
                    if not native_assets:
                        raise RuntimeError(
                            "no release-native front-door asset is available for this platform"
                        )
                    errors: list[str] = []
                    for _ in range(120):
                        refreshed = False
                        for asset in native_assets:
                            url = asset.get("url", "")
                            flavor = asset.get("flavor", "unknown")
                            temp_path = native_path.with_name(
                                native_path.name + ".download-" + uuid4().hex
                            )
                            try:
                                try:
                                    urllib.request.urlretrieve(url, temp_path)
                                except Exception as exc:
                                    errors.append(f"{flavor} asset unavailable: {exc}")
                                    continue
                                temp_version = _version(temp_path)
                                if not _version_matches(temp_version):
                                    raise RuntimeError(
                                        "downloaded native tg front door reported "
                                        + (temp_version or "no version")
                                    )
                                os.replace(temp_path, native_path)
                                installed_native_version = _version(native_path)
                                if not _version_matches(installed_native_version):
                                    raise RuntimeError(
                                        "installed native tg front door reported "
                                        + (installed_native_version or "no version")
                                    )
                                messages.append(
                                    "Native tg front-door refresh completed.\\n"
                                    + "Verified "
                                    + installed_native_version
                                    + ".\\nNative asset flavor: "
                                    + flavor
                                    + "."
                                )
                                refreshed = True
                                break
                            except Exception as exc:
                                errors.append(str(exc))
                            finally:
                                try:
                                    temp_path.unlink()
                                except FileNotFoundError:
                                    pass
                        if refreshed:
                            break
                        time.sleep(0.5)
                    else:
                        raise RuntimeError(
                            "native tg front-door refresh failed: " + "; ".join(errors[-10:])
                        )

                refreshed_bridges: list[str] = []
                for bridge_path in bridge_paths:
                    shutil.copy2(native_path, bridge_path)
                    bridge_version = _version(bridge_path)
                    if not _version_matches(bridge_version):
                        raise RuntimeError(
                            "refreshed PATH tensor-grep front-door copy reported "
                            + (bridge_version or "no version")
                            + " for "
                            + str(bridge_path)
                        )
                    refreshed_bridges.append(str(bridge_path))
                if refreshed_bridges:
                    messages.append(
                        "Refreshed PATH tensor-grep front-door copies:\\n"
                        + "\\n".join(refreshed_bridges)
                    )
                return "\\n".join(messages)

            ok, method, payload = _run_attempts()
            if ok:
                verified, version = _verify_installed_version(expected_version)
                if not verified:
                    log_path.write_text(
                        "Scheduled tensor-grep upgrade failed.\\n" + version,
                        encoding="utf-8",
                    )
                    raise SystemExit(1)
                try:
                    native_payload = _refresh_native_frontdoor_and_bridges()
                except Exception as exc:
                    log_path.write_text(
                        "Scheduled tensor-grep upgrade failed.\\n"
                        + "post-upgrade native front-door refresh failed: "
                        + str(exc),
                        encoding="utf-8",
                    )
                    raise SystemExit(1)
                text = "Scheduled tensor-grep upgrade completed via " + method + "."
                text += "\\nVerified tensor-grep " + version + "."
                if native_payload:
                    text += "\\n" + native_payload
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
                expected_version,
                str(native_path) if native_path is not None else "",
                native_asset_payload,
                bridge_payload,
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
        previous_version = _installed_version()
        latest_version = _latest_pypi_tensor_grep_version()
        exact_latest_requested = False
        if latest_version is not None and (
            previous_version is None
            or latest_version == previous_version
            or _is_version_newer(latest_version, previous_version)
        ):
            package_spec = f"tensor-grep=={latest_version}"
            exact_latest_requested = True
        else:
            package_spec = "tensor-grep"
        attempts = _upgrade_attempts(package_spec)
        result, method = _run_upgrade(attempts)
        current_version = _verify_target_python_tensor_grep_version(sys.executable)
        if (
            exact_latest_requested
            and latest_version is not None
            and current_version != latest_version
        ):
            raise RuntimeError(
                "post-upgrade verification failed: expected tensor-grep "
                f"{latest_version} from PyPI but target Python reports {current_version}"
            )
        native_refresh_message = _refresh_managed_native_frontdoor(current_version)
        output = "\n".join(
            part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part
        )
        if (
            latest_version is not None
            and current_version == previous_version
            and current_version == latest_version
        ):
            typer.echo(f"tensor-grep is already at the latest PyPI version ({current_version}).")
        elif current_version == previous_version:
            if latest_version is None:
                typer.echo(
                    "tensor-grep install completed, but the latest PyPI version could not be "
                    f"verified; installed version is {current_version}."
                )
            elif _is_version_newer(current_version, latest_version):
                typer.echo(
                    f"tensor-grep {current_version} is installed; PyPI metadata reported "
                    f"{latest_version}, so no downgrade was attempted."
                )
            elif "Requirement already satisfied" in output:
                typer.echo(f"tensor-grep is already installed ({current_version}).")
            else:
                typer.echo(f"tensor-grep remains installed at {current_version}.")
        else:
            typer.echo(f"Successfully upgraded tensor-grep via {method}!")
            if output:
                typer.echo(output)
        if native_refresh_message:
            typer.echo(native_refresh_message)

    except RuntimeError as e:
        if _looks_like_windows_self_update_lock(str(e)):
            previous_version = _installed_version()
            latest_version = _latest_pypi_tensor_grep_version()
            expected_version = ""
            if latest_version is not None and (
                previous_version is None
                or latest_version == previous_version
                or _is_version_newer(latest_version, previous_version)
            ):
                package_spec = f"tensor-grep=={latest_version}"
                expected_version = latest_version
            else:
                package_spec = "tensor-grep"
            native_path = _managed_native_frontdoor_path_from_env()
            path_order_message = (
                _ensure_windows_managed_native_first_on_path(native_path)
                if native_path is not None
                else None
            )
            native_assets = [
                {"url": url, "flavor": candidate.flavor}
                for candidate, url in (
                    _native_frontdoor_download_candidates(expected_version)
                    if expected_version
                    else []
                )
            ]
            bridge_paths = (
                _windows_stale_tensor_grep_com_bridges(expected_version, native_path)
                if expected_version and native_path is not None
                else []
            )
            log_path = _schedule_windows_self_upgrade(
                _upgrade_attempts(package_spec),
                expected_version,
                native_path=native_path,
                native_assets=native_assets,
                bridge_paths=bridge_paths,
            )
            typer.echo(
                "Windows is still using tg.exe, so the upgrade was scheduled in the background."
            )
            typer.echo("Wait a few seconds, then run `tg --version` again.")
            typer.echo(f"Upgrade log: {log_path}")
            if path_order_message:
                typer.echo(path_order_message)
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
def ast_info(
    json_output: bool = typer.Option(
        False, "--json", help="Output supported AST languages as JSON."
    ),
) -> None:
    """List supported AST language identifiers."""
    from tensor_grep.backends.ast_backend import get_supported_languages

    languages = get_supported_languages()
    if json_output:
        typer.echo(json.dumps({"languages": languages}))
        return

    typer.echo("Supported AST Languages:")
    for lang in languages:
        typer.echo(f"- {lang}")


@app.command(
    name="run",
    help="Run a validated AST slice for structural search and guarded rewrites.",
)
def run(
    arguments: list[str] | None = typer.Argument(
        None,
        help="The positional AST pattern and optional path, or just path when --pattern is used.",
    ),
    pattern_option: str | None = typer.Option(
        None,
        "--pattern",
        "-p",
        help="The AST pattern to search for, matching ast-grep's option form.",
    ),
    rewrite: str | None = typer.Option(None, "--rewrite", "-r", help="Replacement pattern."),
    lang: str | None = typer.Option(None, "--lang", "-l", help="Language for AST parsing."),
    apply: bool = typer.Option(False, "--apply", help="Apply the rewrite to files."),
    verify: bool = typer.Option(False, "--verify", help="Verify the rewrite with tests."),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format."),
    checkpoint: bool = typer.Option(False, "--checkpoint", help="Enable edit checkpoints."),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Start interactive edit session"
    ),
    update_all: bool = typer.Option(
        False,
        "--update-all",
        "-U",
        help="ast-grep-compatible alias for applying all rewrite edits.",
    ),
    selector: str | None = typer.Option(
        None,
        "--selector",
        help="Unsupported ast-grep semantic matcher selector; fails explicitly.",
    ),
    strictness: str | None = typer.Option(
        None,
        "--strictness",
        help="Unsupported ast-grep strictness control; fails explicitly.",
    ),
    stdin: bool = typer.Option(
        False,
        "--stdin",
        help="Unsupported ast-grep stdin mode; fails explicitly.",
    ),
    filter_regex: str | None = typer.Option(
        None, "--filter", help="Filter matched AST nodes by text regex"
    ),
    files_with_matches: bool = typer.Option(
        False,
        "--files-with-matches",
        help="Print only paths with at least one AST match.",
    ),
) -> None:
    from tensor_grep.cli.ast_workflows import run_command as execute_run

    unsupported_semantic_flags = [
        flag
        for flag, value in (
            ("--selector", selector),
            ("--strictness", strictness),
            ("--stdin", stdin),
        )
        if value
    ]
    if unsupported_semantic_flags:
        typer.echo(
            "Error: "
            + ", ".join(unsupported_semantic_flags)
            + " is not supported by tg run yet. Use ast-grep directly for this semantic matcher.",
            err=True,
        )
        raise typer.Exit(code=2)
    if update_all and rewrite is None:
        typer.echo("Error: tg run --update-all requires --rewrite.", err=True)
        raise typer.Exit(code=2)

    positional_args = list(arguments or [])
    if pattern_option:
        if len(positional_args) > 1:
            typer.echo(
                "Error: tg run --pattern accepts at most one positional PATH argument.",
                err=True,
            )
            raise typer.Exit(code=2)
        resolved_pattern = pattern_option
        resolved_path = positional_args[0] if positional_args else None
    else:
        if not positional_args:
            typer.echo(
                "Error: tg run requires --pattern <PATTERN> or positional PATTERN.",
                err=True,
            )
            raise typer.Exit(code=2)
        if len(positional_args) > 2:
            typer.echo("Error: tg run accepts at most PATTERN and PATH positionals.", err=True)
            raise typer.Exit(code=2)
        resolved_pattern = positional_args[0]
        resolved_path = positional_args[1] if len(positional_args) > 1 else None

    exit_code = execute_run(
        pattern=resolved_pattern,
        path=resolved_path,
        rewrite=rewrite,
        lang=lang,
        apply=apply or update_all,
        verify=verify,
        json_mode=json_output,
        checkpoint=checkpoint,
        interactive=interactive,
        filter_regex=filter_regex,
        files_with_matches=files_with_matches,
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

        _print_version(verbose=any(arg in {"--verbose", "-v"} for arg in sys.argv[2:]))
        sys.exit(0)

    from tensor_grep.cli.commands import KNOWN_COMMANDS as _KNOWN_COMMANDS

    known_commands = _KNOWN_COMMANDS

    if len(sys.argv) == 1:
        app(args=["--help"], prog_name="tg", windows_expand_args=False)
        return

    if len(sys.argv) > 1:
        first_arg = sys.argv[1]
        if (
            first_arg not in ("--help", "-h")
            and first_arg not in known_commands
            and not first_arg.startswith("--typer-")
        ):
            sys.argv.insert(1, "search")

    app(prog_name="tg", windows_expand_args=False)


if __name__ == "__main__":
    main_entry()
