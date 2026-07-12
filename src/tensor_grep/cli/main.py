import dataclasses
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
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

import click
import typer
from typer.core import TyperGroup

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.cli import ast_workflows
from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.cli.runtime_paths import (
    _native_tg_version,
    _native_tg_version_matches,
    env_flag_disabled,
    env_flag_enabled,
    iter_in_tree_native_tg_binaries,
    native_frontdoor_metadata_path,
    resolve_native_tg_binary,
    resolve_ripgrep_binary,
)
from tensor_grep.cli.scan_guardrails import BroadScanRefusedError, ensure_scan_not_broad
from tensor_grep.core.observability import nvtx_range
from tensor_grep.core.result import MatchLine
from tensor_grep.core.retrieval_chunker import MAX_CHUNKS
from tensor_grep.io.directory_scanner import UNBOUNDED_VENDORED_ROOT_DIR_NAMES
from tensor_grep.sidecar import DEFAULT_CLASSIFY_MAX_LINES

if TYPE_CHECKING:
    from tensor_grep.backends.base import ComputeBackend
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import SearchResult
    from tensor_grep.io.directory_scanner import DirectoryScanner

# backlog #1 (Fable+thinktank plan, 2026-07-06): kept numerically in sync with
# repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT (raised 512 -> 2000 for routing accuracy -- a file past
# the old cap never entered the map, so edit-plan/agent/context-render/defs misrouted on repos
# >512 files). This is a SEPARATE literal (not an import) because it is this module's CLI-option
# default, shared across both ROUTING commands (edit-plan/agent/context-render/defs/source) and
# CALLER-SCAN commands (callers/refs/blast-radius/impact/blast-radius-plan). Raising it to 2000
# is safe for the caller-scan commands ONLY because repo_map.CALLER_SCAN_FILE_CEILING bounds
# their actual per-file scan work at 512 regardless of how large this default is -- the
# chokepoint, not a per-command repoint, is what keeps them fast.
_DEFAULT_AGENT_REPO_SCAN_LIMIT = 2000
_DEFAULT_BLAST_RADIUS_JSON_MAX_CALLERS = 25
_DEFAULT_BLAST_RADIUS_JSON_MAX_FILES = 25
# audit #96 (answer-first payloads): defs/refs/callers/impact's own DEDICATED tests-cap, wired
# on-by-default like blast-radius's --max-callers/--max-files precedent above (not an opt-in-only
# flag -- the audit's "95% payload filler" bug needs a default that actually fixes it).
_DEFAULT_SYMBOL_MAX_TESTS = 25
_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_WINDOWS_PROBE_TIMEOUT_SECONDS = 15.0
_DOCTOR_LSP_PROBE_TIMEOUT_ENV = "TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS"
_DOCTOR_SCHEMA_VERSION = 2
_DOCTOR_LSP_SCHEMA_VERSION = 2
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
    "AppData",
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
- `tg agent PATH "change invoice tax"`
- `tg scan --config sgconfig.yml`
- `tg doctor --with-lsp`
- `tg dogfood --output artifacts/dogfood_readiness.json`
- `tg repair-launcher`
- `tg mcp`

**AI workflows**
- `tg map PATH`
- `tg context-render PATH "invoice flow"`
- `tg edit-plan PATH "add retry with tests"`
- `tg agent PATH "change behavior" --json`
- `tg blast-radius PATH create_invoice --json`  (caller graph; `blast-radius-render` = prose bundle)
- `tg session open PATH`
- `tg session daemon start PATH`

**Agent contracts**
- `tg agent` emits primary targets, alternative targets, snippets, validation_commands, rollback metadata, confidence, optional gpu_acceleration route evidence, and ask-before-editing guidance.
- `tg agent --gpu-device-ids 0,1 --json` runs an opt-in native GPU evidence scan; sidecar-routed GPU results are reported as unsupported.
- `context-render` and `edit-plan` also expose top-level validation_commands.
- Validation command templates can quote `$file` or `{file}` placeholders; the command is split into a program and arguments and spawned directly (no shell), so the file path is passed as a single argument and shell constructs (pipes, `&&`, redirects, `cmd`/`sh` builtins) are not interpreted. Applied rewrites run placeholder commands once per edited file.

**Search and safety**
- Use `--format rg --sort path` for deterministic ripgrep-shaped text output.
- The search surface is a validated common rg-compatible subset, not a full ripgrep replacement.
- Use `--format rg --json` for ripgrep JSON Lines events; plain `--json` is tensor-grep aggregate JSON.
- Direct generated-root, broad file-list, and multi-project workspace-root scans are refused unless scoped with paths, `--glob`, `--type`, `--max-depth`, or explicit `--allow-broad-generated-scan`; project-root `--no-ignore` content searches follow ripgrep.
- On Windows, PowerShell double quotes expand $NAME before `tg` receives literal patterns; use single quotes or escape `$`. In `cmd.exe`, quote or caret-escape metacharacters such as `|` and `&`.
- `--smart-case`, `--hidden`, `--max-depth`, and `--text` are honored by structured CPU and sidecar search; native GPU falls back when a requested switch changes semantics it cannot safely execute yet.
- `--gpu-device-ids` pins selected GPUs for explicit search, benchmark, and agent evidence probes; GPU remains experimental until 1GB/5GB correctness and speed beat both `rg` and `tg_cpu`.
- `classify` is local by default; set `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` to opt into CyBERT/Triton.

**Notes**
- Bare patterns and option-first common search flags are treated as `tg search`, including `tg -t js PATTERN PATH` and `tg --count-matches PATTERN PATH`.
- Use `tg search --help` for the current validated rg-compatible flag subset.
- `tg run --help` for AST rewrite flags.
- Lexical repo-map retrieval bridges camelCase, snake_case, and source-term planning queries.
- Use `tg doctor --json` for system, GPU, cache, daemon, and launcher diagnostics including path_tg_first_launcher_kind and fresh_shell_path_tg_first_launcher_kind.
- Use `tg repair-launcher` to remove verified or self-identifying tensor-grep Python Scripts launchers that shadow the managed native front door; add `--allow-foreign-rename` only for a foreign `tg.exe` that you own and want tensor-grep to back up.
- Use `tg session --help` for cached edit-loop and daemon commands; daemon-routed edit-plan/context requests keep a short connect probe, a longer work response timeout, and byte-bounded response-cache stats.

**Environment overrides**
- `TG_SIDECAR_PYTHON`: Path to the Python executable used for sidecar-backed commands.
- `TG_NATIVE_TG_BINARY`: Path to the native front door used by Python-backed commands.
- `TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR`: Set to `nvidia` to prefer NVIDIA release-native front-door assets, with CPU fallback.
- `TG_RG_PATH`: Path to the ripgrep executable used for text-search passthrough.
- `TG_FORCE_CPU`: Force CPU routing for search commands.
- `TG_SIDECAR_TIMEOUT_MS`: Timeout for sidecar-backed commands.
- `TG_HELP_PROBE_TIMEOUT_MS`: Timeout for the native front door's `--help` passthrough probe to this rich Python help before it falls back to the condensed native help (default 3000ms).
- `TENSOR_GREP_DEVICE_IDS`: Comma-separated GPU IDs available to tensor-grep.
- `TENSOR_GREP_CLASSIFY_PROVIDER`: Set to `cybert` to opt into CyBERT/Triton classification.
- `TENSOR_GREP_TRITON_TIMEOUT_SECONDS`: Timeout for Triton-backed NLP probes.
- `TG_MCP_ALLOW_VALIDATION_COMMANDS`: Set to `1` to let the `tg mcp` server's `tg_rewrite_apply` tool accept and shell-execute `lint_cmd` / `test_cmd`; default off (such requests are rejected with `code="unsupported_option"`).
- `TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS`: Total per-command budget for optional external LSP provider requests before native fallback.
- `TENSOR_GREP_CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_STRING_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_AST_QUERY_CACHE_MAX_ENTRIES`, `TENSOR_GREP_AST_NODE_INDEX_CACHE_MAX_ENTRIES`, `TENSOR_GREP_REPO_CONTEXT_CACHE_MAX_ROOTS`: Bound long-lived in-process search and repo-context caches.
- `TENSOR_GREP_SESSION_RESPONSE_CACHE_MAX_BYTES`, `TENSOR_GREP_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES`, `TENSOR_GREP_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES`: Bound agent-loop response and LSP provider caches.
- `TG_SESSION_DAEMON_AUTOSTART`: Default-ON warm-daemon fast path for `defs`/`impact`/`refs`/`callers`/`blast-radius` (probes a running `tg session daemon`; auto-spawns one non-blocking on a miss, so only the first call per root pays the cold-start cost). Set to `0`/`false`/`no`/`off` to opt back out to the always-cold path; always forced off when `CI` or `GITHUB_ACTIONS` is set. Querying N distinct repo roots with this on can leave up to N resident daemons; each self-shuts-down after `TG_SESSION_DAEMON_IDLE_SECONDS` (900s default) of inactivity.""",
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


class _EvidenceGroup(TyperGroup):
    """Nudge `tg evidence <path>` toward the `emit` subcommand.

    Dogfood trap (v1.61.2): an agent reaches for `tg evidence <PATH> <query>` by
    analogy with `tg defs`/`tg orient` (which take a path directly), but `evidence`
    is a command GROUP whose only action is `emit`. Click's default
    "No such command 'src/...'" is correct (exit 2) but unhelpful -- when the unknown
    subcommand looks like a filesystem path, append the concrete fix so the caller
    does not have to re-read `--help`.
    """

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        try:
            return super().resolve_command(ctx, args)
        except click.exceptions.UsageError as exc:
            token = args[0] if args else ""
            if (
                token
                and not token.startswith("-")
                and ("/" in token or "\\" in token or Path(token).exists())
            ):
                # Re-raise a NEW UsageError with the hint appended -- `UsageError.message` is a
                # Final attribute (cannot be reassigned in place); a fresh error carrying the same
                # `ctx` renders identically (Usage + "Try --help" + Error:) and keeps exit code 2.
                raise click.exceptions.UsageError(
                    f"{exc.format_message()}\n"
                    "Hint: `tg evidence` is a command group; its receipt action is "
                    f"`emit`. Did you mean `tg evidence emit {token}`? "
                    "(run `tg evidence emit --help`)",
                    ctx=exc.ctx,
                ) from exc
            raise


evidence_app = typer.Typer(
    cls=_EvidenceGroup,
    help="Emit a versioned EvidenceReceipt aggregating tg's existing outputs.",
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
    asset_name: str


@dataclass(frozen=True)
class _WindowsStalePythonLauncher:
    path: Path
    python_executable: Path
    version: str | None
    package_version: str | None


@dataclass(frozen=True)
class _WindowsUnownedPythonLauncher:
    path: Path
    version: str | None


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


def _candidate_versions_from_pip_index_output(output: str) -> list[str]:
    candidates: list[str] = []
    version_pattern = r"[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc|dev|post)[0-9]+)?"
    for raw_line in output.splitlines():
        line = raw_line.strip()
        package_match = re.search(rf"\btensor-grep\s+\(({version_pattern})\)", line, re.IGNORECASE)
        if package_match:
            candidates.append(package_match.group(1))
        if re.match(r"(?i)^(?:available versions|latest)\s*:", line):
            candidates.extend(re.findall(version_pattern, line))
    return candidates


def _candidate_versions_from_pip_index(timeout_seconds: float) -> list[str]:
    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "index",
                "versions",
                "tensor-grep",
                "--no-cache-dir",
                "--index-url",
                "https://pypi.org/simple",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            env=env,
        )
    except Exception:
        return []
    return _candidate_versions_from_pip_index_output(
        "\n".join(part for part in (result.stdout, result.stderr) if part)
    )


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

    candidates.extend(_candidate_versions_from_pip_index(timeout_seconds))

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


def _managed_native_frontdoor_path() -> Path | None:
    native_path = _managed_native_frontdoor_path_from_env()
    if native_path is not None:
        return native_path
    if not sys.platform.startswith("win"):
        return None
    try:
        if not Path(sys.executable).expanduser().is_absolute():
            return None
    except RuntimeError:
        return None
    managed_bin_dir = _windows_managed_native_bin_dir()
    if managed_bin_dir is None:
        return None
    native_path = managed_bin_dir / "tg.exe"
    return native_path if native_path.is_file() else None


_MAX_NATIVE_ASSET_DOWNLOAD_BYTES = 512 * 1024 * 1024


def _download_native_frontdoor_asset(url: str, destination: Path) -> None:
    import socket
    import urllib.request

    # urlretrieve has NO timeout param -> bound it with a process socket timeout so a stalled CDN
    # read can't hang install/upgrade. A reporthook enforces a BYTE CAP on the ACTUAL bytes read
    # (block_number * read_size) so an oversized/malicious response can't exhaust disk before the
    # checksum is verified (audit #5) -- Content-Length/total_size is attacker-controlled, so we
    # count real blocks. Restore the prior default timeout afterward (don't leak a global timeout).
    def _enforce_byte_cap(block_number: int, read_size: int, total_size: int) -> None:
        if block_number * read_size > _MAX_NATIVE_ASSET_DOWNLOAD_BYTES:
            raise RuntimeError(
                f"Native asset download exceeded {_MAX_NATIVE_ASSET_DOWNLOAD_BYTES} bytes "
                f"(possible oversized or malicious response): {url}"
            )

    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(60)
    try:
        urllib.request.urlretrieve(url, destination, reporthook=_enforce_byte_cap)
    finally:
        socket.setdefaulttimeout(previous_timeout)


def _native_frontdoor_checksums_url(version: str) -> str:
    return f"https://github.com/oimiragieo/tensor-grep/releases/download/v{version}/CHECKSUMS.txt"


def _fetch_native_frontdoor_checksums(version: str) -> str | None:
    """Fetch the published CHECKSUMS.txt manifest for a release, or None if unavailable."""
    import urllib.request

    url = _native_frontdoor_checksums_url(version)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            raw: bytes = response.read()
            return raw.decode("utf-8")
    except Exception:
        return None


def _expected_asset_sha256(checksums_text: str, asset_name: str) -> str | None:
    """Look up the published sha256 for asset_name in a CHECKSUMS.txt manifest.

    Lines are ``<sha256>  <asset>`` (the format emitted by the release tooling and
    consumed by scripts/install.sh). Tolerates blank/comment lines and a leading
    ``*`` binary marker on the filename.
    """
    for raw_line in checksums_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[-1]
        if name.lstrip("*") == asset_name:
            return digest.lower()
    return None


def _native_frontdoor_checksum_error(
    asset_path: Path, asset_name: str, checksums_text: str
) -> str | None:
    """Return None when asset_path matches its published sha256, else an error string.

    Fail-closed: a missing manifest entry is an error (we refuse to trust an
    unlisted download), mirroring scripts/install.sh.
    """
    import hashlib

    expected = _expected_asset_sha256(checksums_text, asset_name)
    if not expected:
        return f"no published checksum for {asset_name}; refusing to trust the download"
    actual = hashlib.sha256(asset_path.read_bytes()).hexdigest().lower()
    if actual != expected:
        return f"checksum mismatch for {asset_name} (expected {expected}, got {actual})"
    return None


def _write_native_frontdoor_metadata(
    destination: Path,
    *,
    version: str,
    candidate: _NativeFrontdoorAssetCandidate,
) -> None:
    metadata = {
        "artifact": "tensor_grep_native_frontdoor_metadata",
        "asset_flavor": candidate.flavor,
        "asset_name": candidate.asset_name,
        "requested_asset_flavor": _requested_native_frontdoor_flavor(),
        "version": version,
    }
    native_frontdoor_metadata_path(destination).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _install_release_native_frontdoor(
    version: str, destination: Path
) -> _NativeFrontdoorInstallResult:
    candidates = _native_frontdoor_download_candidates(version)
    if not candidates:
        raise RuntimeError("no release-native front-door asset is available for this platform")

    # Audit HIGH (2026-06-24): verify every downloaded asset against the published
    # CHECKSUMS.txt BEFORE installing/executing it, matching the fail-closed posture
    # of the installers (scripts/install.sh, install.ps1, npm/install.js). Without
    # the manifest nothing can be verified, so refuse rather than trust the download.
    checksums_text = _fetch_native_frontdoor_checksums(version)
    if checksums_text is None:
        raise RuntimeError(
            "release-native front-door asset install refused: could not fetch "
            f"CHECKSUMS.txt for v{version}; refusing to install an unverified native binary"
        )

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
            checksum_error = _native_frontdoor_checksum_error(
                temp_path, candidate.asset_name, checksums_text
            )
            if checksum_error is not None:
                download_errors.append(f"{candidate.flavor} asset {checksum_error}")
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
            _write_native_frontdoor_metadata(
                destination,
                version=version,
                candidate=candidate,
            )
            return _NativeFrontdoorInstallResult(
                url=url,
                flavor=candidate.flavor,
                asset_name=candidate.asset_name,
            )
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


def _windows_python_install_scripts_executable(candidate: Path) -> Path | None:
    if not sys.platform.startswith("win"):
        return None
    if candidate.name.lower() != "tg.exe":
        return None
    if candidate.parent.name.lower() != "scripts":
        return None
    parts = tuple(part.lower() for part in candidate.parts)
    if ".tensor-grep" in parts or ".venv" in parts or "venv" in parts:
        return None
    python_executable = candidate.parent.parent / "python.exe"
    if not python_executable.is_file():
        return None
    return python_executable


def _windows_python_scripts_tensor_grep_package_version(
    python_executable: Path,
    launcher_path: Path,
) -> str | None:
    try:
        result = subprocess.run(
            [str(python_executable), "-m", "pip", "show", "-f", "tensor-grep"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    location: Path | None = None
    version: str | None = None
    owns_launcher = False
    try:
        resolved_launcher = launcher_path.resolve()
    except OSError:
        resolved_launcher = launcher_path
    for line in result.stdout.splitlines():
        if line.lower().startswith("location:"):
            raw_location = line.split(":", 1)[1].strip()
            if raw_location:
                location = Path(raw_location)
            continue
        if line.lower().startswith("version:"):
            version = line.split(":", 1)[1].strip() or "installed"
            continue
        if location is None:
            continue
        relative_file = line.strip()
        if not relative_file or relative_file.lower() == "files:":
            continue
        try:
            resolved_file = (location / relative_file).resolve()
        except OSError:
            resolved_file = location / relative_file
        if _same_path(resolved_file, resolved_launcher):
            owns_launcher = True
    if not owns_launcher:
        return None
    return version or "installed"


def _windows_stale_tensor_grep_python_launchers(
    expected_version: str,
    native_path: Path,
) -> list[_WindowsStalePythonLauncher]:
    stale_launchers, _unowned_launchers = _windows_tensor_grep_python_launcher_scan(
        expected_version,
        native_path,
    )
    return stale_launchers


def _windows_tensor_grep_python_launcher_scan(
    expected_version: str,
    native_path: Path,
) -> tuple[list[_WindowsStalePythonLauncher], list[_WindowsUnownedPythonLauncher]]:
    if not sys.platform.startswith("win"):
        return [], []

    path_values = [os.environ.get("PATH", "")]
    fresh_path = _doctor_fresh_shell_path_value()
    if fresh_path and fresh_path not in path_values:
        path_values.append(fresh_path)

    stale_launchers: list[_WindowsStalePythonLauncher] = []
    unowned_launchers: list[_WindowsUnownedPythonLauncher] = []
    seen: set[str] = set()
    for path_value in path_values:
        native_seen = False
        for candidate in _doctor_path_tg_candidates(path_value):
            candidate_path = Path(str(candidate.get("path") or ""))
            if _same_path(candidate_path, native_path):
                native_seen = True
                continue
            python_executable = _windows_python_install_scripts_executable(candidate_path)
            if python_executable is None:
                continue
            try:
                key = str(candidate_path.resolve()).lower()
            except OSError:
                key = str(candidate_path).lower()
            if key in seen:
                continue

            version = candidate.get("version")
            shadows_managed_native = not native_seen
            if _native_tg_version_matches(expected_version, version) and not shadows_managed_native:
                continue

            if not _doctor_tg_version_looks_like_tensor_grep(version):
                if version:
                    continue
            package_version = _windows_python_scripts_tensor_grep_package_version(
                python_executable,
                candidate_path,
            )
            if package_version is None:
                if not native_seen:
                    seen.add(key)
                    unowned_launchers.append(
                        _WindowsUnownedPythonLauncher(
                            path=candidate_path,
                            version=version,
                        )
                    )
                continue

            seen.add(key)
            stale_launchers.append(
                _WindowsStalePythonLauncher(
                    path=candidate_path,
                    python_executable=python_executable,
                    version=version,
                    package_version=package_version,
                )
            )
    return stale_launchers, unowned_launchers


def _remove_windows_stale_tensor_grep_python_launchers(
    expected_version: str,
    native_path: Path,
) -> str | None:
    stale_launchers, unowned_launchers = _windows_tensor_grep_python_launcher_scan(
        expected_version,
        native_path,
    )
    if not stale_launchers and not unowned_launchers:
        return None

    removed: list[str] = []
    backed_up_orphans: list[str] = []
    failed: list[str] = []
    for launcher in stale_launchers:
        reason = launcher.version or (
            f"tensor-grep package {launcher.package_version}"
            if launcher.package_version
            else "<unreadable --version>"
        )
        try:
            result = subprocess.run(
                [
                    str(launcher.python_executable),
                    "-m",
                    "pip",
                    "uninstall",
                    "-y",
                    "tensor-grep",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "pip uninstall tensor-grep failed"
                    + (f": {result.stderr.strip()}" if result.stderr else "")
                )
            launcher.path.unlink(missing_ok=True)
            if launcher.path.exists():
                raise OSError("launcher still exists after cleanup")
            removed.append(f"- {launcher.path} ({reason})")
        except Exception as exc:
            failed.append(f"- {launcher.path} ({reason}): {exc}")

    remaining_unowned_launchers: list[_WindowsUnownedPythonLauncher] = []
    for unowned_launcher in unowned_launchers:
        reason = unowned_launcher.version or "<unreadable --version>"
        if unowned_launcher.version is None or not _doctor_tg_version_looks_like_tensor_grep(
            unowned_launcher.version
        ):
            remaining_unowned_launchers.append(unowned_launcher)
            continue
        backup_path = unowned_launcher.path.with_name(
            f"{unowned_launcher.path.name}.orphaned-tensor-grep-"
            f"{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}.bak"
        )
        try:
            os.replace(unowned_launcher.path, backup_path)
            if unowned_launcher.path.exists():
                raise OSError("launcher still exists after backup")
            backed_up_orphans.append(f"- {unowned_launcher.path} -> {backup_path} ({reason})")
        except Exception as exc:
            failed.append(f"- {unowned_launcher.path} ({reason}): {exc}")

    sections: list[str] = []
    if removed:
        sections.append(
            "Removed stale tensor-grep Python package launchers from PATH:\n" + "\n".join(removed)
        )
    if backed_up_orphans:
        sections.append(
            "Backed up orphaned tensor-grep Python Scripts launchers from PATH:\n"
            + "\n".join(backed_up_orphans)
        )
    if failed:
        sections.append(
            "WARNING: stale tensor-grep Python package launchers remain ahead of managed "
            "native tg.exe:\n" + "\n".join(failed)
        )
    if remaining_unowned_launchers:
        sections.append(
            "WARNING: tensor-grep-looking Python Scripts tg.exe launchers remain ahead of "
            "managed native tg.exe, but package ownership could not be verified:\n"
            + "\n".join(
                f"- {launcher.path} ({launcher.version or '<unreadable --version>'})"
                for launcher in remaining_unowned_launchers
            )
        )
    return "\n".join(sections) if sections else None


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
        "cleanup_message": None,
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

    if candidate_kind == "python-entrypoint":
        cleanup_message = _remove_windows_stale_tensor_grep_python_launchers(
            expected_version,
            native_tg_binary,
        )
        payload["cleanup_message"] = cleanup_message
        post_candidate = _doctor_python_subprocess_path_tg_candidate()
        post_path = Path(str(post_candidate.get("path") or "")) if post_candidate else None
        post_version = post_candidate.get("version") if post_candidate else None
        payload["post_repair_version"] = post_version
        if (
            post_path is not None
            and _same_path(post_path, native_tg_binary)
            and _native_tg_version_matches(expected_version, post_version)
        ):
            payload.update({
                "status": "repaired",
                "replaced_path": str(candidate_path),
                "message": (
                    "Python subprocess launcher repaired. Removed or backed up the "
                    "tensor-grep Python Scripts entrypoint so the verified managed native "
                    "tg.exe is selected first."
                ),
            })
            return payload

        payload.update({
            "status": "blocked_python_entrypoint_cleanup",
            "message": (
                "Python subprocess resolution is still blocked by a Python Scripts "
                "tensor-grep entrypoint. "
                + (
                    cleanup_message
                    if cleanup_message
                    else "Package ownership could not be verified, so no launcher was removed."
                )
            ),
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

    # Audit HIGH (2026-06-28): fetch checksums on the parent side and embed the
    # expected sha256 into each payload entry so the detached helper can verify
    # each download WITHOUT importing main.py.  Fail-closed: skip any candidate
    # whose sha256 can't be resolved; refuse to schedule if none remain.
    checksums_text = _fetch_native_frontdoor_checksums(expected_version)
    if checksums_text is None:
        raise RuntimeError(
            "release-native front-door asset refresh refused: could not fetch "
            f"CHECKSUMS.txt for v{expected_version}; refusing to schedule an unverified native binary refresh"
        )
    verifiable_entries: list[dict[str, str]] = []
    for candidate, url in _native_frontdoor_download_candidates(expected_version):
        sha256 = _expected_asset_sha256(checksums_text, candidate.asset_name)
        if sha256 is None:
            continue
        verifiable_entries.append({
            "url": url,
            "flavor": candidate.flavor,
            "asset_name": candidate.asset_name,
            "requested_flavor": _requested_native_frontdoor_flavor(),
            "sha256": sha256,
        })
    if not verifiable_entries:
        raise RuntimeError("no release-native front-door asset is available for this platform")
    asset_payload = json.dumps(verifiable_entries)
    bridge_payload = json.dumps([str(path) for path in bridge_paths or []])

    helper_code = textwrap.dedent(
        """
        import hashlib
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
                asset_name = asset_candidate.get("asset_name", "")
                requested_flavor = asset_candidate.get("requested_flavor", "cpu")
                temp_path = native_path.with_name(native_path.name + ".download-" + uuid4().hex)
                try:
                    try:

                        def _cap(block_num, block_size, total_size):
                            if block_num * block_size > 512 * 1024 * 1024:
                                raise RuntimeError("native asset download exceeded 512MB")

                        urllib.request.urlretrieve(url, temp_path, reporthook=_cap)
                    except Exception as exc:
                        errors.append(f"{flavor} asset unavailable: {exc}")
                        continue
                    sha256 = asset_candidate.get("sha256", "")
                    if not sha256:
                        errors.append(
                            f"{flavor} asset has no published checksum; "
                            "refusing to install unverified binary"
                        )
                        continue
                    actual_sha256 = hashlib.sha256(temp_path.read_bytes()).hexdigest().lower()
                    if actual_sha256 != sha256.lower():
                        errors.append(
                            f"{flavor} asset checksum mismatch "
                            f"(expected {sha256}, got {actual_sha256})"
                        )
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
                    metadata_path = native_path.with_name("tg-native-metadata.json")
                    metadata_path.write_text(
                        json.dumps(
                            {
                                "artifact": "tensor_grep_native_frontdoor_metadata",
                                "asset_flavor": flavor,
                                "asset_name": asset_name,
                                "requested_asset_flavor": requested_flavor,
                                "version": expected_version,
                            },
                            indent=2,
                            sort_keys=True,
                        )
                        + "\\n",
                        encoding="utf-8",
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
    native_path = _managed_native_frontdoor_path()
    if native_path is None:
        return None

    messages: list[str] = []
    path_order_message = _ensure_windows_managed_native_first_on_path(native_path)
    if path_order_message:
        messages.append(path_order_message)
    stale_python_launcher_message = _remove_windows_stale_tensor_grep_python_launchers(
        expected_version,
        native_path,
    )
    if stale_python_launcher_message:
        messages.append(stale_python_launcher_message)
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


def _with_schema_version(payload: dict[str, Any], *, version: int | None = None) -> dict[str, Any]:
    stamped = dict(payload)
    resolved_version = stamped.get(
        "version", version if version is not None else _json_output_version()
    )
    stamped.setdefault("version", resolved_version)
    stamped.setdefault("schema_version", resolved_version)
    return stamped


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
    # Native tg cannot reproduce these output-ordering post-processes byte-for-byte, so a
    # non-default value must REFUSE delegation and fall through to the Python/backend path:
    # sort_files is applied in-backend (ripgrep_backend.py / rust_backend.py) and rank_bm25
    # drives the BM25 rerank at the end of the search flow (both bypassed by a delegated
    # sys.exit). See tests/unit/test_native_delegation_field_coverage.py (round-4 #25).
    "sort_files",
    "rank_bm25",
    # semantic_rank: same class as rank_bm25 above -- native tg has no dense/RRF hybrid leg, so
    # delegating a --semantic search would drop the hybrid rerank entirely.
    "semantic_rank",
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


def _upgrade_running_session_daemon_snapshot(path: str = ".") -> dict[str, Any] | None:
    try:
        status = _doctor_session_daemon_status(path)
    except Exception:
        return None
    if status.get("running") is not True:
        return None
    root = str(status.get("root") or "").strip()
    if not root:
        return None
    return {"root": root}


def _restart_session_daemon_after_upgrade(snapshot: dict[str, Any] | None) -> str | None:
    if not snapshot:
        return None
    root = str(snapshot.get("root") or "").strip()
    if not root:
        return None
    try:
        current = _doctor_session_daemon_status(root)
    except Exception as exc:
        current = {"running": False, "status_error": str(exc)}
    if current.get("running") is True:
        return None
    try:
        from tensor_grep.cli.session_daemon import start_session_daemon

        started = start_session_daemon(root)
    except Exception as exc:
        return f"WARNING: session daemon was running before upgrade but restart failed for {root}: {exc}"
    if started.get("running") is True:
        return f"Session daemon restarted after upgrade for {root}."
    return f"WARNING: session daemon was running before upgrade but did not restart for {root}."


def _doctor_lsp_languages() -> list[str]:
    from tensor_grep.cli.lsp_provider_setup import supported_lsp_languages

    return supported_lsp_languages()


def _doctor_lsp_probe_timeout_seconds() -> float:
    raw_timeout = os.environ.get(_DOCTOR_LSP_PROBE_TIMEOUT_ENV)
    if raw_timeout:
        try:
            parsed_timeout = float(raw_timeout)
        except ValueError:
            parsed_timeout = 0.0
        if parsed_timeout > 0:
            return parsed_timeout
    if sys.platform.startswith("win"):
        return _DOCTOR_LSP_WINDOWS_PROBE_TIMEOUT_SECONDS
    return _DOCTOR_LSP_PROBE_TIMEOUT_SECONDS


def _doctor_lsp_provider_statuses(path: str) -> list[dict[str, Any]]:
    from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager

    manager = ExternalLSPProviderManager()
    workspace_root = Path(path).resolve()
    probe_timeout_seconds = _doctor_lsp_probe_timeout_seconds()
    try:
        return [
            manager.provider_status(
                language=language,
                workspace_root=workspace_root,
                verify_health=True,
                probe_timeout_seconds=probe_timeout_seconds,
            )
            for language in _doctor_lsp_languages()
        ]
    finally:
        manager.stop_all()


_DOCTOR_LSP_WORKSPACE_ERROR_MARKERS = (
    "fetchworkspaceerror",
    "failed to fetch workspace",
    "workspace was not loaded",
    "no workspace folder",
    "could not load workspace",
    "rooturi",
)


def _doctor_lsp_workspace_error_lines(stderr_lines: list[str]) -> list[str]:
    """Return stderr lines that indicate a workspace/fetch failure."""
    matches: list[str] = []
    for raw in stderr_lines:
        line = str(raw)
        lowered = line.lower()
        if any(marker in lowered for marker in _DOCTOR_LSP_WORKSPACE_ERROR_MARKERS):
            matches.append(line)
    return matches


def _doctor_downgrade_lsp_workspace_proof(provider: dict[str, Any]) -> dict[str, Any]:
    """Demote a workspace-blind ``lsp_proof`` claim (audit M10).

    The managed health probe issues a single-file ``documentSymbol`` request, which a
    language server happily answers even when its workspace failed to load (e.g.
    rust-analyzer emitting ``FetchWorkspaceError``). The provider then reports
    ``lsp_proof:true`` while suppressing the very stderr tail that proves cross-file
    navigation is degraded. When the surfaced stderr names a workspace/fetch error, drop
    ``lsp_proof`` to ``false``, expose a ``workspace_warning``, and un-suppress the
    offending stderr lines so the JSON is honest instead of over-claiming.
    """
    if not provider.get("lsp_proof"):
        return provider
    surfaced = [str(item) for item in provider.get("provider_recent_stderr") or [] if str(item)]
    workspace_lines = _doctor_lsp_workspace_error_lines(surfaced)
    if not workspace_lines:
        return provider
    updated = dict(provider)
    updated["lsp_proof"] = False
    updated["lsp_workspace_ready"] = False
    updated["workspace_warning"] = (
        "Single-file documentSymbol probe succeeded, but the provider reported a "
        "workspace/fetch error, so cross-file navigation is not proven. Treat lsp_proof "
        "as degraded until the workspace loads cleanly."
    )
    updated.setdefault(
        "not_lsp_proof_reason",
        "Provider answered the single-file probe but its workspace failed to load "
        "(see provider_recent_stderr); cross-file navigation is unproven.",
    )
    # Stop hiding the evidence: restore the workspace-error lines to stderr_tail and clear
    # the suppression flag that previously masked them.
    existing_tail = [str(item) for item in updated.get("stderr_tail") or [] if str(item)]
    merged_tail = existing_tail + [line for line in workspace_lines if line not in existing_tail]
    updated["stderr_tail"] = merged_tail[-50:]
    updated["stderr_tail_suppressed"] = False
    return updated


def _doctor_apply_lsp_workspace_warnings(
    providers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [_doctor_downgrade_lsp_workspace_proof(provider) for provider in providers]


def _doctor_lsp_providers_by_language(
    providers: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for provider in providers:
        language = str(provider.get("language", "")).strip()
        if not language:
            continue
        entry = dict(provider)
        if "health" not in entry and "health_status" in entry:
            entry["health"] = entry["health_status"]
        keyed[language] = entry
    return keyed


def _doctor_ast_grep_status() -> dict[str, Any]:
    status: dict[str, Any] = {
        "schema_version": 1,
        "available": False,
        "binary": None,
        "wrapper_backend": "AstGrepWrapperBackend",
        "required_for": "tg run ast-grep semantic options",
        "semantic_run_options": ["--selector", "--strictness", "--stdin", "--globs"],
        "timeout_env": "TG_AST_GREP_TIMEOUT_SECONDS",
        "timeout_seconds": None,
    }
    try:
        from tensor_grep.backends.ast_wrapper_backend import (
            AstGrepWrapperBackend,
            _ast_grep_command_timeout_seconds,
        )

        backend = AstGrepWrapperBackend()
        binary = backend._get_binary_name()
        available = backend.is_available()
        status["available"] = available
        status["binary"] = binary if available else None
        status["timeout_seconds"] = _ast_grep_command_timeout_seconds()
        if not available:
            status["install_hint"] = (
                "Install ast-grep or put an ast-grep/sg binary on PATH to use "
                "tg run --selector, --strictness, --stdin, or --globs."
            )
    except Exception as exc:
        status["error"] = str(exc)
    return status


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
        # PowerShell can resolve script commands even when .PS1 is not in PATHEXT.
        # Include it as a non-primary candidate so doctor can flag MCP/stdio traps.
        if ".ps1" not in extensions:
            names.append("tg.ps1")
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


def _doctor_mcp_stdio_launcher_warning(
    *,
    native_tg_binary: Path | None,
    launchers: list[tuple[str, str | None, str | None]],
    path_tg_candidates: list[dict[str, str | None]] | None = None,
) -> str | None:
    native_stdio_path = native_tg_binary
    if native_stdio_path is None or native_stdio_path.suffix.lower() != ".exe":
        native_stdio_path = next(
            (
                Path(path)
                for _label, kind, path in launchers
                if path
                and Path(path).suffix.lower() == ".exe"
                and kind in {"managed-native", "native-exe"}
            ),
            None,
        )
    if native_stdio_path is None or native_stdio_path.suffix.lower() != ".exe":
        return None

    powershell_launchers = [
        (label, path)
        for label, kind, path in launchers
        if path and (kind == "powershell-shim" or Path(path).suffix.lower() == ".ps1")
    ]
    # Also flag .ps1 anywhere in PATH candidates: PowerShell's `Get-Command tg`
    # resolves .ps1 ahead of .exe regardless of enumeration order, so a .ps1
    # sibling next to a working .exe still traps MCP clients using Start-Process.
    if path_tg_candidates:
        seen_paths = {path for _, path in powershell_launchers if path}
        for candidate in path_tg_candidates:
            cpath = candidate.get("path")
            if cpath and cpath not in seen_paths and Path(cpath).suffix.lower() == ".ps1":
                powershell_launchers.append(("PATH .ps1 sibling", cpath))
                seen_paths.add(cpath)
    if not powershell_launchers:
        return None

    observed = "; ".join(f"{label} resolves {path}" for label, path in powershell_launchers)
    script_path = powershell_launchers[0][1]
    return (
        "MCP stdio launcher warning: "
        f"{observed}. Configure MCP clients for `tg mcp` to call the managed native "
        f"tg.exe directly: {native_stdio_path}. Windows MCP/stdio clients that launch "
        "`tg` via PowerShell Start-Process must target native tg.exe directly, not "
        "`tg.ps1`, because Start-Process can resolve the PowerShell shim instead of "
        "the native stdio-safe front door. If you intentionally use the PowerShell "
        "script shim, configure the client to launch it explicitly as "
        f"`pwsh -NoProfile -File {script_path} mcp`."
    )


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


def _doctor_gpu_tier_installed() -> bool:
    """Tier 1 — is the cuDF GPU library findable in the current environment?

    Uses ``importlib.util.find_spec`` so we can detect installation without actually
    importing cuDF (which may allocate GPU memory).  Returns False if cuDF is not
    installed or if the spec lookup itself raises.
    """
    try:
        import importlib.util

        return importlib.util.find_spec("cudf") is not None
    except Exception:
        return False


def _doctor_gpu_tier_usable() -> bool:
    """Tier 2 — does CuDFBackend.is_available() confirm live GPU allocation?

    Imports CuDFBackend *by name* (orthogonal to any cudf-device-bind slice that may
    also touch CuDFBackend) and calls is_available(), which physically allocates a GPU
    tensor.  Returns False on any exception so a missing CUDA driver or GPU is reported
    cleanly.
    """
    try:
        from tensor_grep.backends.cudf_backend import CuDFBackend

        return CuDFBackend().is_available()
    except Exception:
        return False


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
    # Observability tiers — installed and usable are computed here; promotion_proof is
    # filled in by _build_doctor_payload() after the search_runtime_probe runs.
    status["tier"] = {
        "installed": _doctor_gpu_tier_installed(),
        "usable": _doctor_gpu_tier_usable(),
        "promotion_proof": False,
    }
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
    if not status["exists"]:
        # Round-5 UX: a cold cache silently costs ~20-30s on the first query over a large tree.
        # Surface a self-service remediation (agents read this via `doctor --json`).
        status["remediation"] = (
            "run `tg map .` once to warm the AST cache "
            "(avoids ~20-30s first-query latency on large trees)"
        )
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


def _doctor_shell_escaping_guidance() -> dict[str, Any]:
    return {
        "platform": "windows",
        "status": "informational",
        "powershell": {
            "summary": ("PowerShell double quotes expand $NAME before tensor-grep receives argv."),
            "recommendation": (
                "Use single quotes for literal patterns containing $, or escape `$` "
                "inside double-quoted PowerShell strings."
            ),
            "literal_pattern_example": "tg search '$NAME' .",
        },
        "cmd": {
            "summary": "cmd.exe parses metacharacters before tensor-grep receives argv.",
            "metacharacters": ["|", "&", "<", ">", "^", "(", ")"],
            "recommendation": (
                "Quote arguments or caret-escape cmd.exe metacharacters such as ^| and ^&; "
                "prefer normal interactive PowerShell `tg` over direct `tg.cmd` from "
                "PowerShell. MCP/stdio clients using Start-Process should target native "
                "`tg.exe` directly, not `tg.ps1`."
            ),
            "literal_pattern_example": 'cmd /c tg search "foo^|bar" .',
        },
    }


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
        _DOCTOR_LSP_PROBE_TIMEOUT_ENV,
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
    mcp_stdio_launchers = [
        ("PATH", path_tg_first_launcher_kind, path_tg_first_path),
        (
            "fresh-shell PATH",
            fresh_shell_path_tg_first_launcher_kind,
            fresh_shell_path_tg_first_path,
        ),
        (
            "Python subprocess PATH",
            python_subprocess_path_tg_first_launcher_kind,
            python_subprocess_path_tg_first_path,
        ),
    ]
    for label, candidates in (
        ("PATH candidate", path_tg_candidates),
        ("fresh-shell PATH candidate", fresh_shell_path_tg_candidates),
    ):
        for index, candidate in enumerate(candidates, start=1):
            candidate_path = candidate.get("path")
            candidate_version = candidate.get("version")
            mcp_stdio_launchers.append((
                f"{label} {index}",
                _doctor_tg_launcher_kind(candidate_path, candidate_version),
                candidate_path,
            ))
    gpu_status = _doctor_gpu_status()
    gpu_status["search_runtime_probe"] = _doctor_gpu_search_runtime_probe(native_tg_binary)
    # audit M10: gpu.available reflects whether a CUDA device is *present*, not whether the
    # GPU search runtime actually routes through NativeGpuBackend. Surface an honest
    # search_ready boolean derived from the runtime probe so callers don't read
    # gpu.available=true as "GPU search works".
    gpu_status["search_ready"] = (
        cast(dict[str, Any], gpu_status["search_runtime_probe"]).get("status") == "supported"
    )
    if gpu_status.get("available") and not gpu_status["search_ready"]:
        # Round-5 UX: `available=True search_ready=False` reads as "GPU is broken". It is not —
        # GPU search is experimental/opt-in. State that so agents/users don't chase a non-bug.
        gpu_status["search_ready_note"] = (
            "GPU search is experimental/opt-in; search_ready=False is expected and not a "
            "failure -- text and AST search are unaffected"
        )
    # Complete the promotion_proof tier now that the runtime probe result is available.
    # This is the highest tier: GPU search actually routed through NativeGpuBackend.
    cast(dict[str, Any], gpu_status["tier"])["promotion_proof"] = gpu_status["search_ready"]
    payload: dict[str, Any] = {
        "schema_version": _DOCTOR_SCHEMA_VERSION,
        "doctor_schema_version": _DOCTOR_SCHEMA_VERSION,
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
        "mcp_stdio_launcher_warning": _doctor_mcp_stdio_launcher_warning(
            native_tg_binary=native_tg_binary,
            launchers=mcp_stdio_launchers,
            path_tg_candidates=path_tg_candidates,
        ),
        "shell_escaping_guidance": _doctor_shell_escaping_guidance(),
        "gpu": gpu_status,
        "ast_grep": _doctor_ast_grep_status(),
        "ast_cache": _doctor_ast_cache_status(str(root), str(resolved_config)),
        "resident_worker": _doctor_resident_worker_status(str(root)),
        "env": {key: os.environ[key] for key in env_keys if os.environ.get(key)},
        "session_daemon": _doctor_session_daemon_status(str(root)),
    }
    if with_lsp:
        lsp_providers = _doctor_apply_lsp_workspace_warnings(
            _doctor_lsp_provider_statuses(str(root))
        )
        lsp_providers_by_language = _doctor_lsp_providers_by_language(lsp_providers)
        payload["lsp"] = {
            "schema_version": _DOCTOR_LSP_SCHEMA_VERSION,
            "enabled": True,
            "probe_timeout_seconds": _doctor_lsp_probe_timeout_seconds(),
            "providers": lsp_providers,
            "providers_by_language": lsp_providers_by_language,
        }
    else:
        lsp_providers = []
        lsp_providers_by_language = {}
        payload["lsp"] = {
            "schema_version": _DOCTOR_LSP_SCHEMA_VERSION,
            "enabled": False,
            "probe_timeout_seconds": None,
            "providers": lsp_providers,
            "providers_by_language": lsp_providers_by_language,
        }
    payload["lsp_provider_items"] = lsp_providers
    payload["lsp_providers"] = lsp_providers_by_language
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
    if mcp_stdio_launcher_warning := payload.get("mcp_stdio_launcher_warning"):
        lines.append(f"mcp_stdio_launcher_warning: {mcp_stdio_launcher_warning}")
    if python_subprocess_warning := payload.get("python_subprocess_path_tg_foreign_warning"):
        lines.append(f"python_subprocess_path_tg_foreign_warning: {python_subprocess_warning}")
    if python_subprocess_remediation := payload.get(
        "python_subprocess_path_tg_foreign_remediation"
    ):
        lines.append(
            f"python_subprocess_path_tg_foreign_remediation: {python_subprocess_remediation}"
        )
    shell_escaping_guidance = cast(dict[str, Any], payload.get("shell_escaping_guidance", {}))
    if shell_escaping_guidance:
        powershell_guidance = cast(
            dict[str, Any],
            shell_escaping_guidance.get("powershell", {}),
        )
        cmd_guidance = cast(dict[str, Any], shell_escaping_guidance.get("cmd", {}))
        lines.append("shell_escaping_guidance:")
        lines.append(
            "  PowerShell: "
            f"{powershell_guidance.get('summary')} "
            f"{powershell_guidance.get('recommendation')} "
            f"example={powershell_guidance.get('literal_pattern_example')}"
        )
        metacharacters = ", ".join(str(item) for item in cmd_guidance.get("metacharacters", []))
        lines.append(
            "  cmd.exe metacharacters: "
            f"{metacharacters}. "
            f"{cmd_guidance.get('recommendation')} "
            f"example={cmd_guidance.get('literal_pattern_example')}"
        )

    gpu_payload = cast(dict[str, Any], payload.get("gpu", {}))
    gpu_tier = cast(dict[str, Any], gpu_payload.get("tier", {}))
    lines.append(
        f"gpu: available={gpu_payload.get('available', False)} "
        f"search_ready={gpu_payload.get('search_ready', False)}"
    )
    if gpu_payload.get("available") and not gpu_payload.get("search_ready"):
        lines.append(
            "  note: search_ready=False is expected -- GPU search is experimental/opt-in, "
            "not a failure; text and AST search are unaffected"
        )
    if gpu_tier:
        lines.append(
            f"  tier: installed={gpu_tier.get('installed', False)} "
            f"usable={gpu_tier.get('usable', False)} "
            f"promotion_proof={gpu_tier.get('promotion_proof', False)}"
        )
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
    else:
        lines.append(
            "  hint: cache cold -- run `tg map .` once to warm it "
            "(avoids ~20-30s first-query latency on large trees)"
        )

    ast_grep_payload = cast(dict[str, Any], payload.get("ast_grep", {}))
    ast_grep_options = "/".join(
        str(option) for option in ast_grep_payload.get("semantic_run_options", [])
    )
    lines.append(
        "ast_grep: "
        f"available={ast_grep_payload.get('available', False)} "
        f"binary={ast_grep_payload.get('binary') or 'missing'} "
        f"semantic_run_options={ast_grep_options or 'unknown'} "
        f"timeout_seconds={ast_grep_payload.get('timeout_seconds') or 'unknown'}"
    )
    if ast_grep_payload.get("install_hint"):
        lines.append(f"  install_hint: {ast_grep_payload['install_hint']}")
    if ast_grep_payload.get("error"):
        lines.append(f"  error: {ast_grep_payload['error']}")

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

    # tg-ledger step-0 (demand instrumentation, see docs/multi_agent_context_plane.md): a single
    # human summary line for the trailing-14-day demand receipt that the daemon persists to
    # daemon_metrics.json (read-back works even when the daemon is currently stopped).
    demand_metrics = cast(dict[str, Any], session_payload.get("demand_metrics") or {})
    demand_days_covered = int(demand_metrics.get("days_covered", 0) or 0)
    if "error" in demand_metrics or demand_days_covered == 0:
        demand_pre_gate = "NO-COVERAGE"
    else:
        demand_pre_gate = "MET" if demand_metrics.get("pre_gate_met") else "NOT-MET"
    lines.append(
        "session_daemon_demand(14d): "
        f"clients={int(demand_metrics.get('max_distinct_client_pids_14d', 0) or 0)} "
        f"concurrent_days={int(demand_metrics.get('days_with_2plus_concurrent', 0) or 0)} "
        f"dup_requests={int(demand_metrics.get('dup_requests_14d', 0) or 0)} "
        f"pre_gate={demand_pre_gate}"
    )

    lsp_payload = cast(dict[str, Any], payload.get("lsp", {}))
    if lsp_payload.get("enabled"):
        lines.append("lsp_providers:")
        if lsp_payload.get("probe_timeout_seconds") is not None:
            lines.append(f"lsp_probe_timeout_seconds: {lsp_payload['probe_timeout_seconds']}")
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
    if config.case_sensitive:
        command.append("-s")
    if config.fixed_strings:
        command.append("-F")
    if config.invert_match:
        command.append("-v")
    if config.count:
        command.append("-c")
    # Forward an EXPLICIT line-number choice only. The native subprocess inherits tg's stdout, so its
    # own tty heuristic already matches tg's auto decision; we only need to forward when the user
    # explicitly set --line-number/-n or --no-line-number/-N (otherwise that choice is dropped).
    if config.line_number_explicit:
        command.append("-n" if config.line_number else "-N")
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

    # The native binary's `search` positionals use clap allow_hyphen_values, so it
    # already accepts dash-leading patterns/paths without an -e/-- shim; the end-of-
    # options hardening (audit B4/#8) is applied to the external ripgrep builder, which
    # needs it. Keep the native delegation argv stable for the parity contract.
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


def _search_with_cpu_fallback(
    current_file: str,
    pattern: str,
    config: "SearchConfig",
    exc: Exception,
) -> "SearchResult":
    """Retry a failed native-backend search on the always-available CPU backend.

    A runtime backend failure (native panic, IO/encoding error, version skew, GPU/OOM
    fault) must never surface to the user as a clean no-match. The CPU backend is pure
    Python and always available, so it is the safe last-resort engine; the override is
    announced on stderr so it is observable rather than silent (audit B2/I1).
    """
    from tensor_grep.backends.cpu_backend import CPUBackend

    sys.stderr.write(
        f"tensor-grep: search backend failed on {current_file} ({exc}); "
        "retried on the CPU backend.\n"
    )
    return CPUBackend().search(current_file, pattern, config=config)


# F5 (Fable audit MED): retrieval_chunker.MAX_CHUNKS bounds a single chunk_file() call (per FILE).
# A matched-file set of many small files can still blow past a sane CORPUS-wide total even though
# no single file trips the per-file guard, so DenseIndex.__init__'s single-batch encode would face
# unbounded memory. Cap the CORPUS total here too, sharing the same threshold as the per-file guard
# (no separate magic number to keep in sync).
_SEMANTIC_CORPUS_CHUNK_CAP = MAX_CHUNKS


def _set_semantic_rank_fallback_reason(all_results: "SearchResult") -> None:
    """Probe dense-leg availability and set ``rank_fallback_reason`` (F16, Fable audit LOW).

    Used for the 0-match `--semantic` case: with no matches there is nothing to rerank, so the
    full :func:`_apply_semantic_rerank` path (chunking, model load) is skipped entirely -- but the
    availability probe must still run so the JSON envelope stays honest (a dense-unavailable
    search must report that even when it happens to find zero matches, not silently omit
    ``rank_fallback_reason``).
    """
    from tensor_grep.core.retrieval_dense import dense_available

    available, unavailable_reason = dense_available()
    if not available:
        all_results.rank_fallback_reason = unavailable_reason
        sys.stderr.write(f"tg: {unavailable_reason}\n")


def _note_late_rerank_degraded(all_results: "SearchResult", reason: str) -> None:
    """Append (or set) ``rank_fallback_reason`` for a RECOVERABLE late-rerank degrade (the
    ``rerank`` extra absent, or the model not fetched) and echo the same ``tg:``-prefixed stderr
    line the dense leg uses (T6, design doc "Fail-closed contract"). Appends rather than
    overwrites so a simultaneous dense-leg degrade is never clobbered -- both signals must survive
    on the returned envelope.
    """
    if all_results.rank_fallback_reason:
        all_results.rank_fallback_reason = f"{all_results.rank_fallback_reason}; {reason}"
    else:
        all_results.rank_fallback_reason = reason
    sys.stderr.write(f"tg: {reason}\n")


def _apply_semantic_rerank(all_results: "SearchResult", pattern: str) -> "SearchResult":
    """Apply the `--semantic` hybrid (BM25 + dense RRF [+ late MaxSim]) rerank, fail-closed to
    BM25-only.

    The dense leg is best-effort: when the `semantic` extra is absent, the model has not been
    fetched, the model produces a malformed/mismatched embedding, or a query-time dense fault
    occurs (F1: e.g. a dim mismatch raised from inside `rerank_hybrid`'s call to
    `DenseIndex.query`), this degrades VISIBLY to a BM25-only rerank (stderr warning +
    ``rank_fallback_reason`` set) -- it never silently returns unranked output and never mislabels
    BM25-only output as "semantic". A genuine backend fault (e.g. a corrupt model directory)
    raises ``BackendExecutionError`` instead of degrading, per the Backend Fail-Closed Contract --
    that is NOT caught here; the caller (the `search` command) must catch it and exit cleanly
    (F4).

    T5/T6 (design doc "The seam" + "Fail-closed contract"): when ``TG_LATE_RERANK=1`` is set, a
    late-interaction (MaxSim) reranker is built here (``late_available()`` probe, then
    ``load_late_reranker()``) and passed into the PRIMARY ``rerank_hybrid`` call only -- never
    into any of the BM25-only degrade retries below, since those already mean the whole hybrid
    stage bypassed the late stage too (each appends "; late rerank skipped" to its
    ``rank_fallback_reason`` when late rerank was requested). A RECOVERABLE late-leg failure
    (extra absent, model not fetched) degrades here exactly like the dense leg; an UNRECOVERABLE
    ``BackendExecutionError`` (e.g. a corrupt model directory) deliberately propagates, same as
    the dense leg's.
    """
    from tensor_grep.core.reranker import rerank_hybrid
    from tensor_grep.core.retrieval_bm25 import Bm25Index
    from tensor_grep.core.retrieval_chunker import Chunk, chunk_file
    from tensor_grep.core.retrieval_dense import (
        DenseIndex,
        DenseUnavailableError,
        default_model_dir,
        dense_available,
        load_dense_model,
    )

    late_rerank_requested = os.environ.get("TG_LATE_RERANK") == "1"

    def _maybe_append_late_skip(reason: str) -> str:
        """The 3 upstream degrade paths below all BYPASS the late stage entirely (it is only ever
        wired into the PRIMARY rerank_hybrid call further down) -- when late rerank was
        requested, say so explicitly rather than leaving the envelope silently ambiguous about
        why no late reorder happened (T6)."""
        return f"{reason}; late rerank skipped" if late_rerank_requested else reason

    dense_index = None
    available, unavailable_reason = dense_available()
    if not available:
        all_results.rank_fallback_reason = unavailable_reason
        sys.stderr.write(f"tg: {unavailable_reason}\n")

    # F3 (Fable audit MED): build the chunk corpus ONCE and share it between the BM25 and dense
    # legs. Previously the dense leg's corpus was built here while the BM25 leg rebuilt its own
    # corpus from scratch inside `rerank_hybrid` (bm25_index=None) -- a second full file-I/O pass,
    # and a silent RRF-misalignment risk if the two passes' chunk_size/overlap defaults ever
    # diverge.
    chunks: list[Chunk] = []
    for path in all_results.matched_file_paths:
        try:
            file_chunks = chunk_file(path)
        except RuntimeError as exc:
            # F5: retrieval_chunker's own MAX_CHUNKS guard is PER FILE; a single pathological file
            # can still trip it on its own even before the corpus-wide cap below is reached. Either
            # way, degrade to BM25-only (using whatever we already have -- discard the corpus, let
            # rerank_hybrid rebuild its own BM25-only chunks) rather than crash the whole search.
            all_results.rank_fallback_reason = _maybe_append_late_skip(str(exc))
            sys.stderr.write(f"tg: {exc}\n")
            return rerank_hybrid(
                all_results,
                pattern,
                all_results.matched_file_paths,
                # A2 (external audit 2026-07-11): reuse the chunks accumulated so far (already bounded
                # by the corpus cap / the file that raised) -- passing a prebuilt index stops
                # rerank_hybrid re-reading + re-chunking the FULL corpus UNCAPPED, which turned this
                # safety guard into the expensive op it exists to prevent. Mirrors the F1 retry below.
                bm25_index=Bm25Index(chunks),
                dense_index=None,
            )
        chunks.extend(file_chunks)
        if len(chunks) > _SEMANTIC_CORPUS_CHUNK_CAP:
            reason = (
                "semantic ranking unavailable: corpus chunk cap "
                f"({_SEMANTIC_CORPUS_CHUNK_CAP}) exceeded across the matched file set -- narrow "
                "the search to fewer files for a semantic rerank"
            )
            all_results.rank_fallback_reason = _maybe_append_late_skip(reason)
            sys.stderr.write(f"tg: {reason}\n")
            return rerank_hybrid(
                all_results,
                pattern,
                all_results.matched_file_paths,
                # A2 (external audit 2026-07-11): reuse the chunks accumulated so far (already bounded
                # by the corpus cap / the file that raised) -- passing a prebuilt index stops
                # rerank_hybrid re-reading + re-chunking the FULL corpus UNCAPPED, which turned this
                # safety guard into the expensive op it exists to prevent. Mirrors the F1 retry below.
                bm25_index=Bm25Index(chunks),
                dense_index=None,
            )

    bm25_index = Bm25Index(chunks)

    if available:
        try:
            model = load_dense_model(default_model_dir())
            dense_index = DenseIndex(chunks, model)
        except DenseUnavailableError as exc:
            all_results.rank_fallback_reason = str(exc)
            sys.stderr.write(f"tg: {exc}\n")
        # BackendExecutionError (e.g. a corrupt model directory) deliberately propagates: that is
        # an unrecoverable fault the CLI boundary must catch and exit on (F4), not degrade here.

    late_reranker = None
    if late_rerank_requested:
        from tensor_grep.core.retrieval_late import (
            LateRerankUnavailableError,
            late_available,
            load_late_reranker,
        )

        late_ok, late_reason = late_available()
        if not late_ok:
            _note_late_rerank_degraded(all_results, late_reason or "late rerank unavailable")
        else:
            try:
                late_reranker = load_late_reranker()
            except LateRerankUnavailableError as exc:
                _note_late_rerank_degraded(all_results, str(exc))
            # BackendExecutionError (e.g. a corrupt model directory) deliberately propagates: an
            # unrecoverable fault the CLI boundary must catch and exit on, not degrade here --
            # mirrors the dense leg immediately above.

    try:
        return rerank_hybrid(
            all_results,
            pattern,
            all_results.matched_file_paths,
            bm25_index=bm25_index,
            dense_index=dense_index,
            late_reranker=late_reranker,
        )
    except DenseUnavailableError as exc:
        # F1: a query-time dense fault (e.g. a dim mismatch) is raised from INSIDE rerank_hybrid's
        # call to `DenseIndex.query`, outside the try/except above (which only guards index
        # construction). Degrade to BM25-only here too -- reuse the SAME bm25_index (no second
        # chunk pass) rather than let it traceback. This also BYPASSES the late stage (it is only
        # ever wired into the primary call above, never into this retry).
        all_results.rank_fallback_reason = _maybe_append_late_skip(str(exc))
        sys.stderr.write(f"tg: {exc}\n")
        return rerank_hybrid(
            all_results,
            pattern,
            all_results.matched_file_paths,
            bm25_index=bm25_index,
            dense_index=None,
        )


def _search_error_payload(error: str, detail: str) -> dict[str, object]:
    from tensor_grep.cli.formatters.json_fmt import JSON_OUTPUT_VERSION

    return {
        "version": JSON_OUTPUT_VERSION,
        "schema_version": JSON_OUTPUT_VERSION,
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


def _is_inline_flag_regex_error(message: str) -> bool:
    """Return True when ``message`` is the "inline flag group not at the start of the
    pattern" rejection that PCRE2 (``-P``) accepts but the default Rust/``re`` engine does
    not (e.g. ``a(?s).*b``). Centralized so both the remediation hint (M14) and the
    transparent PCRE2 fallback (M14b) classify the error identically."""
    lowered = message.lower()
    return "global flags not at the start" in lowered or (
        "flag" in lowered and ("(?" in message or "inline" in lowered)
    )


def _invalid_regex_remediation(message: str) -> str:
    """Return a remediation hint that never converts a hard regex error into a silent
    wrong answer (audit M14).

    The default Rust regex engine rejects inline flag groups that are not at the start
    of the expression (e.g. ``a(?s).*b``). Suggesting ``-F`` there is actively harmful:
    ``-F`` searches the literal text ``a(?s).*b`` and returns a silent zero-match
    success, masking the real problem. For inline-flag / parse errors, point the user at
    ``-P`` (the PCRE2 engine, which accepts mid-expression inline flags) or at moving the
    flag to the front of the pattern instead.
    """
    if _is_inline_flag_regex_error(message):
        return (
            "Use -P (PCRE2) to allow inline flags mid-pattern, or move the inline flag "
            "group (for example (?s)) to the very start of the pattern."
        )
    return (
        "Use -P (PCRE2) for extended regex syntax, or --fixed-strings (-F) only if you "
        "intended to search this pattern as a literal string."
    )


def _exit_invalid_regex(exc: Exception, *, json_mode: bool = False) -> None:
    message = str(exc)
    if "invalid regex" not in message.lower():
        message = f"invalid regex pattern: {message}"
    _exit_search_error(
        "invalid_regex",
        message,
        json_mode=json_mode,
        stderr_detail=f"{message}. {_invalid_regex_remediation(message)}",
    )


def _engine_is_explicit_pcre2(config: "SearchConfig") -> bool:
    """True when the user explicitly selected PCRE2, via ``-P``/``--pcre2`` or
    ``--engine pcre2``. PCRE2 accepts mid-pattern inline flag groups, so the Python
    pre-flight validator must not reject patterns the chosen engine would accept."""
    return bool(config.pcre2) or str(getattr(config, "engine", "") or "").lower() == "pcre2"


def _pcre2_fallback_backend_available() -> bool:
    """True when the resolved ripgrep backend can actually run PCRE2. The rg shipped on some
    platforms (and most CI images) is built WITHOUT PCRE2, so blindly retrying under PCRE2
    would raise a confusing ConfigurationError instead of the helpful ``-P`` remediation."""
    try:
        from tensor_grep.backends.ripgrep_backend import RipgrepBackend

        return bool(RipgrepBackend().supports_pcre2())
    except Exception:
        return False


def _eligible_for_pcre2_inline_flag_fallback(config: "SearchConfig") -> bool:
    """True when an inline-flag regex rejection should transparently retry under PCRE2
    instead of erroring (audit M14b). Fires for the default/unset engine and for
    ``--engine auto``; ``-F`` is honored (literal intent) and an explicit PCRE2 engine
    already routes through PCRE2, so neither needs the fallback. The default engine value
    is the same whether the user typed ``--engine default`` or nothing, so both opt in --
    matching the bare ``tg search 'a(?s).*b'`` repro. (Whether a PCRE2-capable rg backend
    actually exists is a separate, environment-dependent check applied at the call site.)"""
    if config.fixed_strings or _engine_is_explicit_pcre2(config):
        return False
    return str(getattr(config, "engine", "") or "").lower() in {"default", "auto", ""}


def _validate_search_regex(pattern: str, config: "SearchConfig") -> None:
    if config.fixed_strings or _engine_is_explicit_pcre2(config):
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


_LEADING_INLINE_FLAG_RE = re.compile(r"^\(\?([aiLmsux]+)\)")


def _scope_leading_inline_flag(pattern: str) -> str:
    """Rewrite a GLOBAL leading inline flag group (``(?i)foo``) to the SCOPED form
    (``(?i:foo)``) so it stays legal -- and stays scoped to its own branch, never leaking
    case-insensitivity/etc. across the rest of the alternation -- once it is no longer the
    first thing in a combined multi-pattern regex (audit #69, re-do of #441)."""
    match = _LEADING_INLINE_FLAG_RE.match(pattern)
    if not match:
        return pattern
    flags = match.group(1)
    rest = pattern[match.end() :]
    return f"(?{flags}:{rest})"


def _combine_multi_patterns(patterns: list[str], *, fixed_strings: bool) -> str:
    """OR-combine multiple ``-e``/``-f`` patterns into one rg-parity alternation regex: a
    line matches if ANY pattern matches (rg's own multi-pattern semantics), reported once
    even when more than one pattern matches the same line -- never N independent passes.
    Each pattern becomes its own non-capturing-group branch (never a bare top-level ``|``
    join), and the whole alternation gets one more enclosing group, so downstream
    ``-w``/``-x``/``--line-regexp`` wrapping (which wraps the WHOLE pattern string, e.g.
    ``rf"\\b{pattern}\\b"``) applies to the entire alternation rather than mis-scoping to
    just the first/last branch via ``|``'s low precedence."""
    branches = []
    for raw_pattern in patterns:
        candidate = (
            re.escape(raw_pattern) if fixed_strings else _scope_leading_inline_flag(raw_pattern)
        )
        branches.append(f"(?:{candidate})")
    return "(?:" + "|".join(branches) + ")"


def _read_patterns_from_file_list(file_paths: list[str], *, json_mode: bool) -> list[str]:
    """Read ``-f``/``--file`` pattern files, one pattern per line (rg parity: a genuinely
    blank line is an EMPTY pattern that matches every line, so it is intentionally NOT
    filtered out here). A missing/unreadable file fails loud with exit 2 -- per the Backend
    Fail-Closed Contract -- instead of the pre-fix silent flood (an unread ``-f`` collapsed
    to an empty ``pattern`` that matched every line in every file)."""
    patterns: list[str] = []
    for file_path in file_paths:
        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _exit_search_error(
                "pattern_file_error",
                f"failed to read pattern file: {file_path} ({exc})",
                json_mode=json_mode,
                exit_code=2,
            )
            return []  # pragma: no cover -- _exit_search_error always calls sys.exit
        patterns.extend(content.splitlines())
    return patterns


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


def _generated_scan_dir_names(paths: list[str], *, include_child_dirs: bool = True) -> list[str]:
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
            if not include_child_dirs:
                continue
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


# Bug #88 (dogfood v1.54.0): `_has_generated_scan_bound` above answers "does this query have
# ANY scope-narrowing flag" -- correct for `_should_refuse_unbounded_generated_scan` (its own
# purpose: is a `--no-ignore` scan of a generated dir intentional), but WRONG when reused as
# the escape hatch for the workspace-root / vendored-root / large-root-ceiling guards below.
# `--glob`/`--iglob`/`--type`/`--type-not` only filter WHICH already-encountered files count
# as candidates -- they do not reduce how much of the tree must be walked to find them, unlike
# `--max-depth`, which genuinely bounds the walk itself. Treating a bare `--glob` as "already
# bounded" let a `tg search --glob X PATTERN` with NO explicit PATH auto-scope to an entire
# workspace/vendored/oversized root with all three refusal guards silently disabled -- exactly
# the shape reported in bug #88. The fix: `--glob`/`--iglob`/`--type`/`--type-not` remain a
# valid escape hatch ONLY when the caller also typed an explicit PATH (a deliberate, scoped
# root deliberately narrowed further by a file filter); when PATH was left to default, only
# `--max-depth` (or `--allow-broad-generated-scan`) may bypass these three guards.
def _has_walk_scope_bound(config: "SearchConfig", *, paths_defaulted: bool) -> bool:
    if config.max_depth is not None:
        return True
    if paths_defaulted:
        return False
    return bool(config.glob or config.iglob or config.file_type or config.type_not)


def _path_has_project_marker(path: Path) -> bool:
    for marker in _BROAD_WORKSPACE_PROJECT_MARKERS:
        try:
            if (path / marker).exists():
                return True
        except OSError:
            continue
    return False


def _workspace_project_child_names(paths: list[str]) -> list[str]:
    found: set[str] = set()
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = Path(raw_path)
        try:
            if not path.is_dir() or _path_has_project_marker(path):
                continue
            child_project_names: list[str] = []
            for child in path.iterdir():
                try:
                    if child.is_dir() and _path_has_project_marker(child):
                        child_project_names.append(child.name)
                except OSError:
                    continue
            if len(child_project_names) >= _BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD:
                found.update(child_project_names)
        except OSError:
            continue
    return sorted(found, key=lambda item: item.lower())


def _should_refuse_unbounded_workspace_root_scan(
    paths: list[str],
    config: "SearchConfig",
    *,
    allow_broad_generated_scan: bool,
    paths_defaulted: bool,
) -> tuple[bool, list[str]]:
    if allow_broad_generated_scan or _has_walk_scope_bound(config, paths_defaulted=paths_defaulted):
        return False, []
    project_dirs = _workspace_project_child_names(paths)
    return bool(project_dirs), project_dirs


# Critical unscoped-search-hang fix C: heavy vendored/index directories that can sit at the
# TOP LEVEL of a single project root -- a root `_workspace_project_child_names` never flags
# because the guard above SKIPS any root that is itself a project (has its own marker like
# pyproject.toml/.git) and only fires when it finds >= 3 sibling project dirs. A single huge
# vendored repo (its own project, one giant `node_modules`/`external_repos`/etc. at the top)
# always slips past that guard.
#
# Deliberately EXCLUDES tg's own index/reference dirs (`.tensor-grep`, `_tg_refs`,
# `.tg_semantic_index`): those are already (a) skipped by repo_map's walk (Fix A), (b)
# normally `.gitignore`d so DirectoryScanner's default walk never descends into them, and
# (c) bounded by Fix B's wall-clock deadline if they ever are walked. Including them here
# was verified (real dogfood run) to make this guard refuse EVERY unscoped default-path
# search from tensor-grep's own repo root -- a `.tensor-grep/` cache dir is a completely
# normal thing for any tg-managed repo to have, not a "genuinely pathological root".
#
# Review finding H1 (2026-07-05): also EXCLUDES any dir already walker-skipped by
# `DirectoryScanner`'s `_GENERATED_DIR_NAMES` (currently just `node_modules` of the four
# above) -- the native walker already hard-skips it, and `rg` respects `.gitignore` (where
# `node_modules` almost always lives) plus Fix B's per-file deadline, so that dir was
# ALREADY bounded and this refusal was a pure false positive that exit-2'd every ordinary
# Node/React repo's unscoped search. Imported (not hardcoded) from `io/directory_scanner.py`
# so this set and `cli/bootstrap.py`'s front-door mirror can never drift out of sync.
_UNBOUNDED_VENDORED_ROOT_DIR_NAMES = UNBOUNDED_VENDORED_ROOT_DIR_NAMES


def _root_top_level_vendored_dir_names(paths: list[str]) -> list[str]:
    """O(top-level-entries) probe: never walks -- only `Path.iterdir()` one level deep."""
    found: set[str] = set()
    vendored_names = {name.lower() for name in _UNBOUNDED_VENDORED_ROOT_DIR_NAMES}
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = Path(raw_path)
        try:
            if not path.is_dir():
                continue
            for child in path.iterdir():
                if child.is_dir() and child.name.lower() in vendored_names:
                    found.add(child.name)
        except OSError:
            continue
    return sorted(found, key=lambda item: item.lower())


def _should_refuse_unbounded_vendored_root_scan(
    paths: list[str],
    config: "SearchConfig",
    *,
    allow_broad_generated_scan: bool,
    paths_defaulted: bool,
) -> tuple[bool, list[str]]:
    if allow_broad_generated_scan or _has_walk_scope_bound(config, paths_defaulted=paths_defaulted):
        return False, []
    vendored_dirs = _root_top_level_vendored_dir_names(paths)
    return bool(vendored_dirs), vendored_dirs


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
    generated_dirs = _generated_scan_dir_names(paths, include_child_dirs=files_mode)
    return bool(generated_dirs), generated_dirs


def _format_broad_generated_scan_error(generated_dirs: list[str]) -> str:
    visible_dirs = ", ".join(generated_dirs[:8])
    if len(generated_dirs) > 8:
        visible_dirs = f"{visible_dirs}, ..."
    return (
        "Error: broad generated-root scan refused as a safety guard, not a zero-match result: "
        "path contains generated, cache, "
        f"or dependency directories ({visible_dirs}). Scope the path, add --glob, --type, "
        "or --max-depth, or pass --allow-broad-generated-scan to opt in.\n"
        "For bounded output:\n"
        "tg search --files <path> --hidden --max-depth <N>\n"
        "For intentional broad scans:\n"
        "--allow-broad-generated-scan"
    )


def _format_broad_workspace_scan_error(project_dirs: list[str]) -> str:
    visible_dirs = ", ".join(project_dirs[:8])
    if len(project_dirs) > 8:
        visible_dirs = f"{visible_dirs}, ..."
    return (
        "Error: broad workspace-root scan refused as a safety guard, not a zero-match result: "
        "path looks like a multi-project "
        f"workspace root ({visible_dirs}). Scope the path to one project, add --glob, "
        "--type, or --max-depth, or pass --allow-broad-generated-scan to opt in.\n"
        "For bounded output:\n"
        'tg search <pattern> <workspace> --glob "*.py"\n'
        "tg search <pattern> <workspace> --max-depth <N>\n"
        "For intentional broad scans:\n"
        "--allow-broad-generated-scan"
    )


def _format_unbounded_vendored_root_scan_error(vendored_dirs: list[str]) -> str:
    visible_dirs = ", ".join(vendored_dirs[:8])
    if len(vendored_dirs) > 8:
        visible_dirs = f"{visible_dirs}, ..."
    return (
        "Error: broad root scan refused as a safety guard, not a zero-match result: "
        "path contains a heavy vendored/index "
        f"directory at its top level ({visible_dirs}). Scope the path, add --glob, --type, "
        "or --max-depth, or pass --allow-broad-generated-scan to opt in.\n"
        "For bounded output:\n"
        'tg search <pattern> <root> --glob "*.py"\n'
        "tg search <pattern> <root> --max-depth <N>\n"
        "For intentional broad scans:\n"
        "--allow-broad-generated-scan"
    )


# F6: an unscoped `tg search` on a large SINGLE-project, non-vendored root matches NEITHER
# the workspace guard above (needs >=3 sibling project dirs) NOR the vendored-root guard
# (needs a top-level vendored dir name) -- it falls through both. When the Pipeline then
# selects anything other than `RipgrepBackend` (the one branch that hands ALL candidates to
# a single native call), the per-file Python loop a few lines below has no bound other than
# the wall-clock deadline (Fix B, `cli/main.py`'s native-walk-deadline check) -- so a big
# candidate set grinds through that full deadline instead of failing fast (dogfood v1.42.0).
#
# This guard is checked using the candidate count the real search ALREADY collected (never
# a second scan of its own -- that would just be the unbounded work this guard exists to
# avoid), and fires BEFORE the slow per-file loop starts.
#
# Bug #88 (dogfood v1.54.0): this ceiling is evaluated on the ACTUAL post-filter candidate
# count, so a `--glob`/`--type`/`--iglob` filter is already fully reflected in
# `candidate_file_count` -- it never needs its own bypass here (unlike the workspace/vendored
# guards, which are cheap top-level probes that never see the real count). Bypassing on
# `--glob` alone (the pre-fix `_has_generated_scan_bound` check) defeated this guard for
# exactly the bare-`--glob`-no-PATH shape it exists to catch; see `_has_walk_scope_bound`.
_LARGE_ROOT_SCAN_FILE_CEILING = 1500


def _should_refuse_unbounded_large_root_scan(
    candidate_file_count: int,
    config: "SearchConfig",
    *,
    allow_broad_generated_scan: bool,
    paths_defaulted: bool,
) -> bool:
    if allow_broad_generated_scan or _has_walk_scope_bound(config, paths_defaulted=paths_defaulted):
        return False
    return candidate_file_count > _LARGE_ROOT_SCAN_FILE_CEILING


def _format_unbounded_large_root_scan_error(file_count_floor: int) -> str:
    return (
        "Error: broad root scan refused as a safety guard, not a zero-match result: "
        f"path is a large single-project root (over {file_count_floor} files); --glob/--type/"
        "--iglob narrow WHICH files match but do not bound how much of the tree must be "
        "walked to find them, and no fast native/rg engine is available for this query -- an "
        "unscoped scan here would burn the search deadline instead of failing fast. Scope the "
        "path explicitly, add --max-depth, or pass --allow-broad-generated-scan to opt in.\n"
        "For bounded output:\n"
        'tg search <pattern> <root> --glob "*.py"\n'
        "tg search <pattern> <root> --max-depth <N>\n"
        "For intentional broad scans:\n"
        "--allow-broad-generated-scan"
    )


# Bug #88 (dogfood v1.54.1 re-harvest): the native-binary front door's implicit-`--glob`-no-PATH
# WALK guard (`implicit_search_walk_exceeds_ceiling`, rust_core/src/main.rs) needs a Python-CLI
# mirror, because the full CLI reaches this bug through a DIFFERENT door: `--glob` is a
# `_TG_ONLY_SEARCH_FLAG`, so `cli/bootstrap.py`'s launcher routes a bare `tg search --glob X
# PATTERN` to `_run_full_cli()`, which then hands the whole implicit-`.` walk to the rg
# passthrough (`RipgrepBackend.search_passthrough`) BEFORE `_should_refuse_unbounded_large_root_scan`
# (that guard only runs on the slow per-file Python loop, never on the rg-passthrough fast path).
# On a large single-project root whose top level carries a project marker (e.g. a workspace dir
# with a `package.json`), the workspace-root guard SKIPS it and the vendored-root guard finds no
# top-level vendored dir, so the search sailed straight into an unbounded rg walk (dogfood repro:
# `tg search "function" --glob "*"` on `C:/dev/projects` streamed 487k lines past 60s).
#
# Like the native probe this counts files the walker VISITS -- NOT post-glob matches: a file glob
# does not prune the walk, so a SELECTIVE glob (`*.rs` in a huge JS tree) would sail under a
# match-count ceiling yet still force the full unbounded walk. The glob/type filters are stripped
# from the probe config so `DirectoryScanner.walk` yields every walked file; `--max-depth` /
# ignore / hidden are kept because they genuinely bound how much of the tree is walked. The pull
# is bounded to `ceiling + 1` files (never a full-tree enumeration).
def _implicit_glob_search_walk_exceeds_ceiling(
    paths: list[str],
    config: "SearchConfig",
    ceiling: int,
) -> bool:
    from tensor_grep.io.directory_scanner import DirectoryScanner

    probe_config = dataclasses.replace(config, glob=None, iglob=None, file_type=None, type_not=None)
    count = 0
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        scanner = DirectoryScanner(probe_config)
        for _ in scanner.walk(raw_path):
            count += 1
            if count > ceiling:
                return True
    return False


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
        and not config.rank_bm25
        and not config.semantic_rank
        # An explicit --gpu-device-ids request must reach Pipeline, which raises loudly when GPU
        # can't be honored (the "never silently downgrade to CPU" contract). rg-passthrough would
        # run plain CPU rg with exit 0 and no fallback_reason — a silent downgrade. (round-5 Q9)
        and not config.gpu_device_ids
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


# Render-only ripgrep flags that shape *text* output. The tensor-grep aggregate
# `--json` object has no place to put them, so `_build_cmd` silently drops them when
# `json_mode` is set. Worse, the front-door launcher can respawn the search child in
# text-render mode while expecting JSON, spawning an rg child whose pipe is never
# drained -> deadlock (audit C3). Detect them up front and fail fast with a structured
# error instead of either silently ignoring the user's intent or risking the hang.
# Maps the user-facing flag spelling -> the SearchConfig attribute that records it.
_PLAIN_JSON_INCOMPATIBLE_RENDER_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--passthru", "--passthrough"),
    ("--heading", "--no-heading"),
    ("--trim", "--no-trim"),
    ("-b", "--byte-offset", "--no-byte-offset"),
    ("-M", "--max-columns"),
    ("--max-columns-preview", "--no-max-columns-preview"),
    ("--context-separator", "--no-context-separator"),
    ("--field-context-separator",),
    ("--field-match-separator",),
    ("-p", "--pretty"),
)


def _plain_json_incompatible_render_flags(argv: list[str] | None = None) -> list[str]:
    """Return the render-only flag spellings the user passed that the aggregate
    plain-``--json`` path cannot honor. Detection is argv-based because some flags
    (notably ``--heading``) share their default with the SearchConfig default and so
    cannot be recovered from the parsed config alone."""
    tokens = list(sys.argv[1:] if argv is None else argv)
    if tokens and tokens[0] == "search":
        tokens = tokens[1:]
    # Stop at an explicit end-of-options marker so a literal "--passthru" *pattern*
    # after "--" is never mistaken for the flag.
    seen: set[str] = set()
    flagged: list[str] = []
    for token in tokens:
        if token == "--":
            break
        base = token.split("=", 1)[0]
        for group in _PLAIN_JSON_INCOMPATIBLE_RENDER_FLAGS:
            if base in group and group[0] not in seen:
                seen.add(group[0])
                flagged.append(group[0])
    return flagged


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
    from tensor_grep.backends.ast_backend import normalize_ast_language

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
    from tensor_grep.backends.ast_backend import normalize_ast_language

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

    from tensor_grep.backends.ast_backend import normalize_ast_language

    class _NoAliasSafeLoader(yaml.SafeLoader):
        """SafeLoader that REJECTS YAML aliases. Inline ast-grep rules never legitimately
        need anchors/aliases, and an aliased node graph is a billion-laughs
        memory-exhaustion vector: the downstream ``str()`` coercions on ``id``/``severity``/
        ``message`` (below) deep-walk the SHARED alias graph and expand it ~9^depth. Audit
        #95 Part-2 Opus gate BLOCK proved a 469-byte aliased payload hangs >15s -- the
        ``_MAX_INLINE_RULES_CHARS`` length cap admits depth ~1000 while detonation is at
        depth ~9, so the length cap alone is insufficient; reject at the loader level. This
        shared helper guards BOTH the MCP ``tg_ruleset_scan(inline_rules=...)`` tool and the
        CLI ``--inline-rules`` twin (identical mechanism). Uses the pure-Python SafeLoader
        (not CSafeLoader) so ``compose_node`` is overridable -- inline payloads are small
        (length-capped) so the perf cost is negligible."""

        def compose_node(self, parent, index):  # type: ignore[override,no-untyped-def]
            if self.check_event(yaml.events.AliasEvent):  # type: ignore[no-untyped-call]
                event = self.get_event()  # type: ignore[no-untyped-call]
                raise yaml.composer.ComposerError(
                    None,
                    None,
                    "YAML aliases are not allowed in inline rules",
                    event.start_mark,
                )
            return super().compose_node(parent, index)

    specs: list[dict[str, str]] = []

    try:
        documents = list(yaml.load_all(inline_rules_text, Loader=_NoAliasSafeLoader))
    except (yaml.YAMLError, RecursionError) as exc:
        # RecursionError: a deeply-nested ALIAS-FREE payload (e.g. "["*20000) recurses the YAML
        # parser/composer past the interpreter limit. The _NoAliasSafeLoader cannot reject it (no
        # alias), but the pure-Python SafeLoader raises a *catchable* RecursionError where the C
        # loader would hard-crash the process (native stack overflow). Catch it here so this path
        # also fails closed as a structured invalid_input rather than escaping as a raw traceback
        # -- the tool's fail-closed contract (audit #95 Part-2 re-gate).
        detail = str(exc).splitlines()[0] if str(exc).strip() else "input nesting too deep"
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
        "schema_version": _json_output_version(),
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
    # Defense-in-depth: coerce to int so a direct (non-MCP) caller passing a fractional float
    # cannot crash the slice below (`normalized[:max_chars]` requires an int index). The MCP
    # surface already rejects non-int max_evidence_snippet_chars at the tool inputSchema + FastMCP
    # pydantic boundary, so this is not a reachable vuln -- it hardens the helper for any future
    # in-process caller. (audit #95 Part-2 round-6 gate: non-blocking hardening note.)
    max_chars = int(max_chars)
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


def _write_json_refuse_symlink(write_path: Path, data: object) -> None:
    """Write JSON to ``write_path`` refusing to follow a symlink at the final component.

    Round-5 security: closes the check->write symlink-swap race for the two in-process
    ruleset-scan writers (baseline/suppressions). O_TRUNC (not O_EXCL) preserves the
    documented create-or-overwrite semantics -- a re-run must still succeed and refresh
    the file.

    Two layers, because ``os.O_NOFOLLOW`` is unavailable on Windows (mirrors cpython's
    own tempfile module: ``getattr(os, "O_NOFOLLOW", 0)``, not a hard import-time
    dependency):
      1. An explicit ``is_symlink()`` pre-check -- works without elevated privileges
         (only *creating* a Windows symlink needs privilege; checking for one does not).
         On Windows this is a best-effort narrowing, NOT an atomic guard: ``O_NOFOLLOW``
         is a no-op there (see step 2), so a symlink swapped into ``write_path`` between
         this check and the ``os.open()`` call below would still be followed -- a narrow,
         same-process TOCTOU window. That residual window is consciously accepted rather
         than papered over with fragile ctypes/CreateFileW handle-reopen tricks: it is a
         single-digit-microsecond gap inside one process (not the cross-process window a
         planted symlink usually needs), the caller has already confined ``write_path``
         to a validated directory before this function runs, and creating a *new* Windows
         symlink at that exact instant still requires the attacker to hold
         symlink-creation privilege (Developer Mode or elevation). Audit #110 closed the
         cross-process analog of this race (a symlink planted between a separate
         confinement check and a *later* write, e.g. across a Rust subprocess boundary)
         in the Rust audit-manifest writer via ``O_NOFOLLOW`` / Windows
         ``FILE_FLAG_OPEN_REPARSE_POINT`` -- see ``write_bytes_refuse_symlink`` in
         ``rust_core/src/main.rs``.
      2. ``O_NOFOLLOW`` on the actual open -- the authoritative, atomic guard on POSIX,
         closing the narrow race between step 1's check and the open() call.
    """
    if write_path.is_symlink():
        raise ValueError(f"Refusing to write through symlink at {write_path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(write_path), flags, 0o600)
    except OSError as exc:
        raise ValueError(f"Refusing to write {write_path}: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2))
    except OSError as exc:
        raise ValueError(f"Refusing to write {write_path}: {exc}") from exc


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
            "schema_version": _json_output_version(),
            "kind": "ruleset-scan-baseline",
            "ruleset": payload.get("ruleset"),
            "language": payload.get("language"),
            "fingerprints": matched_fingerprints,
        }
        _write_json_refuse_symlink(write_path, baseline_payload)
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
            "schema_version": _json_output_version(),
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
        _write_json_refuse_symlink(write_path, suppressions_payload)
        payload["suppressions_written"] = {
            "path": str(write_path),
            "fingerprints": matched_fingerprints,
            "count": len(matched_fingerprints),
        }


def _regex_rule_targets_file(rule_language: str, file_path: str) -> bool:
    """Whether a regex-engine ruleset rule should scan ``file_path``.

    AST rules are already scoped to their language by the DirectoryScanner (via
    ``lang=rule["language"]``). The regex engine, by contrast, used to ``finditer``
    over *every* candidate file, so a ``--ruleset secrets-basic --language python``
    scan flagged ``.ts``/``.js``/``.rs`` files as python findings (audit H11). Mirror
    the AST scoping: if the file's language is detectable and differs from the rule's
    language, skip it. Files whose language is undetectable (extensionless, configs,
    data files, or a language tg cannot classify) are left to the rule so we never
    silently drop a finding for a language ``_target_language_for_path`` does not yet
    recognize.
    """
    from tensor_grep.backends.ast_backend import normalize_ast_language
    from tensor_grep.cli.repo_map import _target_language_for_path

    file_language = _target_language_for_path(file_path)
    if file_language is None:
        return True
    return file_language == normalize_ast_language(rule_language, default=file_language)


def _run_ast_scan_payload(
    project_cfg: dict[str, object],
    rules: list[dict[str, str]],
    *,
    routing_reason: str,
    scan_paths: list[str] | None = None,
    candidate_files: list[str] | None = None,
    project_scan_fast_path: bool = False,
    ruleset_name: str | None = None,
    scan_globs: list[str] | None = None,
    scan_types: list[str] | None = None,
    scan_max_depth: int | None = None,
    allow_broad_generated_scan: bool = False,
    baseline_path: str | None = None,
    write_baseline_path: str | None = None,
    suppressions_path: str | None = None,
    write_suppressions_path: str | None = None,
    suppression_justification: str | None = None,
    include_evidence_snippets: bool = False,
    max_evidence_snippets_per_file: int = 1,
    max_evidence_snippet_chars: int = 120,
) -> dict[str, object]:
    from tensor_grep.backends.ast_backend import normalize_ast_language
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
        glob=list(scan_globs or []) or None,
        file_type=list(scan_types or []) or None,
        max_depth=scan_max_depth,
    )
    root_dir = cast(Path, project_cfg["root_dir"])
    include_scan_paths_in_payload = bool(scan_paths)
    resolved_scan_paths = (
        [str(Path(scan_path).expanduser().resolve()) for scan_path in scan_paths]
        if scan_paths
        else [str(root_dir)]
    )
    ensure_scan_not_broad(
        resolved_scan_paths,
        globs=list(scan_globs or []),
        file_types=list(scan_types or []),
        max_depth=scan_max_depth,
        allow_broad_generated_scan=allow_broad_generated_scan,
    )
    scan_has_discovery_filter = bool(scan_globs or scan_types or scan_max_depth is not None)
    scanner: DirectoryScanner | None = None
    resolved_candidate_files = (
        None
        if scan_paths or scan_has_discovery_filter
        else list(candidate_files)
        if candidate_files is not None
        else None
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

    def _candidate_files_for_filtered_scan() -> list[str]:
        nonlocal scanner, resolved_candidate_files
        if scanner is None:
            scanner = DirectoryScanner(cfg)
        if resolved_candidate_files is None:
            resolved_candidate_files, _ = _collect_candidate_files(scanner, resolved_scan_paths)
        return resolved_candidate_files

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
            and not scan_has_discovery_filter
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
            backend_scan_paths = (
                _candidate_files_for_filtered_scan()
                if scan_has_discovery_filter
                else resolved_scan_paths
            )
            if backend_scan_paths:
                result = backend.search_many(backend_scan_paths, rule["pattern"], config=rule_cfg)
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
                    resolved_matched_files.update(
                        match.file for match in result.matches if match.file
                    )
            else:
                rule_matches = 0
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
        rule_language = rule["language"]
        for current_file in resolved_candidate_files:
            # H11: scope the regex scan to the rule's language so a python rule does
            # not flag .ts/.js/.rs files, matching how AST rules are scoped.
            if not _regex_rule_targets_file(rule_language, current_file):
                continue
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
        "schema_version": _json_output_version(),
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
    from tensor_grep.backends.ast_backend import is_native_ast_language
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
    help="""Search files for a regex pattern. GPU routing is experimental and opt-in via --gpu-device-ids; CPU/ripgrep is the default and the current speed baseline.
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
    no_pre: bool = typer.Option(False, "--no-pre", help="Disable any configured --pre command."),
    pre_glob: list[str] | None = typer.Option(
        None, "--pre-glob", help="Only run --pre command on files matching this glob."
    ),
    search_zip: bool = typer.Option(
        False, "-z", "--search-zip", help="Search in compressed files (gzip, bzip2, xz, lz4, etc)."
    ),
    no_search_zip: bool = typer.Option(
        False, "--no-search-zip", help="Do not search compressed files."
    ),
    # SEARCH OPTIONS
    case_sensitive: bool = typer.Option(
        False, "-s", "--case-sensitive", help="Execute the search case sensitively."
    ),
    crlf: bool = typer.Option(
        False, "--crlf", help="Treat CRLF as a line terminator instead of just LF."
    ),
    no_crlf: bool = typer.Option(
        False, "--no-crlf", help="Do not treat CRLF specially; useful for config overrides."
    ),
    dfa_size_limit: str | None = typer.Option(
        None, "--dfa-size-limit", help="The upper size limit of the regex DFA."
    ),
    encoding: str = typer.Option(
        "auto", "-E", "--encoding", help="Specify the text encoding (e.g., auto, none, utf-8)."
    ),
    no_encoding: bool = typer.Option(
        False, "--no-encoding", help="Disable configured explicit encoding."
    ),
    engine: str = typer.Option(
        "default", "--engine", help="Regex engine to use: 'default', 'pcre2', or 'auto'."
    ),
    fixed_strings: bool = typer.Option(
        False, "-F", "--fixed-strings", help="Treat all patterns as literals instead of regex."
    ),
    no_fixed_strings: bool = typer.Option(
        False, "--no-fixed-strings", help="Disable fixed-string mode."
    ),
    ignore_case: bool = typer.Option(
        False, "-i", "--ignore-case", help="Search case insensitively."
    ),
    invert_match: bool = typer.Option(
        False, "-v", "--invert-match", help="Invert matching (print lines that don't match)."
    ),
    no_invert_match: bool = typer.Option(
        False, "--no-invert-match", help="Disable inverted matching."
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
    no_mmap: bool = typer.Option(False, "--no-mmap", help="Do not use memory maps."),
    multiline: bool = typer.Option(
        False, "-U", "--multiline", help="Enable searching across multiple lines."
    ),
    no_multiline: bool = typer.Option(False, "--no-multiline", help="Disable multiline mode."),
    multiline_dotall: bool = typer.Option(
        False, "--multiline-dotall", help="Enable 'dot all' mode in multiline searches."
    ),
    no_multiline_dotall: bool = typer.Option(
        False, "--no-multiline-dotall", help="Disable multiline dot-all mode."
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
    no_pcre2: bool = typer.Option(False, "--no-pcre2", help="Disable PCRE2 regex mode."),
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
    rank: bool = typer.Option(
        False,
        "--rank",
        "--bm25",
        help=(
            "Re-rank results by BM25 lexical relevance to the query terms instead of grep order "
            "(pure-CPU ranking; no API key, no model download)."
        ),
    ),
    semantic: bool = typer.Option(
        False,
        "--semantic",
        help=(
            "Re-rank results by a hybrid of BM25 + local CPU dense-embedding relevance (RRF "
            "fusion), instead of grep order. No API key, no GPU. Requires the `semantic` extra "
            "and a fetched model; falls back to BM25-only (visibly, never silently) when "
            "either is missing."
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
            "Permit unbounded file-list/search scans through generated, cache, dependency, "
            "or multi-project workspace roots. Prefer scoped paths, --glob, --type, or "
            "--max-depth for agent runs."
        ),
    ),
) -> None:
    """
    Search files for a regex pattern. GPU routing is experimental and opt-in via --gpu-device-ids; CPU/ripgrep is the default and the current speed baseline.
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
    elif file:
        pattern = ""
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

    # `-f/--file` (patterns-from-file) and multiple `-e/--regexp` never build a real combined-pattern
    # regex -- `pattern` above is simply "" when the `elif file:` branch above actually ran (bool(file)
    # AND no regexp given, since `elif regexp_patterns:` takes priority over `elif file:` and would make
    # `-f` a dead flag), or regexp_patterns[0] (silently drops the rest) when multiple `-e` were given.
    # -o/-r/--rank/--semantic all operate on that single `pattern` string, so combining them previously
    # either silently returned zero matches (-o against pattern="") or reranked/replaced against the
    # wrong text. The multi-pattern combine feature was scoped OUT (#441 closed); reject the combo up
    # front instead, mirroring the plain-`--json` render-flag guard above (audit #5/#20). Excludes
    # `--files` mode (a distinct, unrelated file-listing path) and a single `-e` alongside an
    # otherwise-dead `-f` (regexp_patterns already wins there, so `pattern` is real).
    multi_pattern_source = not files and (
        (not regexp_patterns and bool(file)) or len(regexp_patterns) > 1
    )
    if multi_pattern_source:
        conflicting_flags = [
            spelling
            for present, spelling in (
                (only_matching, "-o/--only-matching"),
                (replace is not None, "-r/--replace"),
                (rank, "--rank/--bm25"),
                (semantic, "--semantic"),
            )
            if present
        ]
        if conflicting_flags:
            flag_list = " and ".join(conflicting_flags)
            source = "multiple -e/--regexp patterns" if len(regexp_patterns) > 1 else "-f/--file"
            _exit_search_error(
                "unsupported_flag",
                (
                    f"{flag_list} not supported with {source} (no single combined-pattern regex "
                    "is built from them); drop the flag(s), or provide a single -e/--regexp pattern."
                ),
                json_mode=json,
                exit_code=2,
            )

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

    # Capture whether the user explicitly chose a line-number mode BEFORE auto-resolving (so native
    # delegation can forward only an explicit -n/-N and leave the auto case to the native binary).
    line_number_explicit = bool(no_line_number) or line_number is True
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
        rank_bm25=rank,
        semantic_rank=semantic,
        regexp=regexp,
        file_patterns=file,
        pre=pre,
        no_pre=no_pre,
        pre_glob=pre_glob,
        search_zip=search_zip,
        no_search_zip=no_search_zip,
        case_sensitive=case_sensitive,
        crlf=crlf,
        no_crlf=no_crlf,
        dfa_size_limit=dfa_size_limit,
        encoding=encoding,
        no_encoding=no_encoding,
        engine=engine,
        fixed_strings=fixed_strings,
        no_fixed_strings=no_fixed_strings,
        ignore_case=ignore_case,
        invert_match=invert_match,
        no_invert_match=no_invert_match,
        line_regexp=line_regexp,
        max_count=max_count,
        mmap=mmap,
        no_mmap=no_mmap,
        multiline=multiline,
        no_multiline=no_multiline,
        multiline_dotall=multiline_dotall,
        no_multiline_dotall=no_multiline_dotall,
        auto_hybrid_regex=auto_hybrid_regex,
        no_auto_hybrid_regex=no_auto_hybrid_regex,
        no_unicode=no_unicode,
        unicode=unicode,
        pcre2_unicode=pcre2_unicode,
        no_pcre2_unicode=no_pcre2_unicode,
        null_data=null_data,
        pcre2=pcre2,
        no_pcre2=no_pcre2,
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
        line_number_explicit=line_number_explicit,
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
        # audit #69 (re-do of #441, this time with a Windows golden from the start):
        # `multi_pattern_source` already excludes -e-and-f-together (a single -e still makes
        # -f a dead flag, pinned by
        # test_search_single_regexp_with_unused_file_option_and_only_matching_still_works)
        # and excludes -o/-r/--rank/--semantic (rejected with exit 2 above), so this is
        # exactly the plain-search shape that used to silently drop every pattern but the
        # first (multiple -e) or never read the file at all (-f alone). The multiple-`-e`
        # sub-case combines EAGERLY here -- no I/O, and the rg-routed passthrough path is
        # untouched by it either way (see the combine step below). The `-f`-alone sub-case is
        # handled LATER, only once the search is confirmed to not be rg-passthrough
        # (deliberately deferred: an eager read here broke
        # test_python_search_treats_file_option_as_pattern_file_not_regex, where real `rg`
        # itself must read the `-f` file on the passthrough path, never tg).
        combined_multi_patterns: list[str] | None = (
            list(regexp_patterns) if multi_pattern_source and len(regexp_patterns) > 1 else None
        )
        try:
            patterns_to_validate = (
                combined_multi_patterns
                if combined_multi_patterns is not None
                else (regexp_patterns if regexp_patterns else [pattern])
            )
            for regex_pattern in patterns_to_validate:
                _validate_search_regex(regex_pattern, config)
        except Exception as exc:
            if _is_invalid_regex_error(exc):
                # M14b: a mid-pattern inline flag group (e.g. `start(?s).*end`) is rejected
                # by the default Rust/`re` engine but accepted by PCRE2. When the user did
                # not explicitly pick a non-PCRE2 engine, retry transparently under PCRE2
                # instead of erroring, and announce the switch on stderr so it is observable.
                if (
                    _is_inline_flag_regex_error(str(exc))
                    and _eligible_for_pcre2_inline_flag_fallback(config)
                    and _pcre2_fallback_backend_available()
                ):
                    config = dataclasses.replace(config, pcre2=True)
                    typer.echo(
                        "note: retried with PCRE2 (-P) for inline-flag pattern",
                        err=True,
                    )
                else:
                    _exit_invalid_regex(exc, json_mode=json)
            else:
                raise
        if combined_multi_patterns is not None:
            # Build one rg-parity OR-alternation and let 100% of the existing
            # single-pattern machinery (CPUBackend, the Rust FFI, native-binary delegation)
            # treat it exactly like a hand-typed `-e "foo|bar"` -- the rg-ROUTED passthrough
            # path is untouched by this (it reads `config.regexp`/`config.file_patterns`
            # directly and builds its own rg argv; see ripgrep_backend.py:788). `-F`
            # multi-literal is `re.escape`'d per branch, so `fixed_strings` must be cleared
            # here or the combined alternation string would be re-literal-matched whole.
            pattern = _combine_multi_patterns(
                combined_multi_patterns, fixed_strings=config.fixed_strings
            )
            config = dataclasses.replace(config, query_pattern=pattern, fixed_strings=False)
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
    refuse_workspace_scan, workspace_project_dirs = _should_refuse_unbounded_workspace_root_scan(
        paths_to_search,
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        paths_defaulted=paths_defaulted,
    )
    if refuse_workspace_scan:
        typer.echo(_format_broad_workspace_scan_error(workspace_project_dirs), err=True)
        raise typer.Exit(2)
    refuse_vendored_scan, vendored_root_dirs = _should_refuse_unbounded_vendored_root_scan(
        paths_to_search,
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        paths_defaulted=paths_defaulted,
    )
    if refuse_vendored_scan:
        typer.echo(_format_unbounded_vendored_root_scan_error(vendored_root_dirs), err=True)
        raise typer.Exit(2)

    # Bug #88 (dogfood v1.54.1 re-harvest): an implicit-path `--glob`/`--type` search that the
    # workspace/vendored guards above did not catch (a large single-project root whose top level
    # carries a project marker, e.g. a workspace dir with a package.json) would otherwise hand the
    # whole unbounded `.` walk to the rg passthrough / native delegation below. Mirror the native
    # binary's WALK-ceiling guard here so the full CLI refuses fast too. Gated on `paths_defaulted`
    # (an explicit, deliberately-scoped PATH still runs uninhibited -- Trap #3); `--max-depth` and
    # `--allow-broad-generated-scan` bypass it (a genuinely bounded walk / an opt-in override).
    #
    # P0-1 (dogfood + external audit 2026-07-11): fire for an unscoped search that carries NO
    # glob/type filter too, not just the glob/type combo. The plain fast-path search is already
    # bounded upstream -- the bootstrap front door delegates it to the native binary (whose own
    # walk-ceiling guard refuses) or to rg passthrough -- so a bare `tg search PATTERN` never
    # reaches this Python guard when a native/rg engine exists. The gap this closes is the
    # FULL-CLI path: a query that carries a TG-only flag (`--rank`/`--semantic`/`--cpu`, ...) is
    # forced to the full CLI where NO fast native/rg engine can serve it, and if no glob/type
    # rode along, the old gate let it fall through to the unbounded per-file Python loop and burn
    # the wall-clock deadline (dogfood-reproduced: `tg search PATTERN --rank` on a >1500-file
    # unscoped root did the full walk instead of refusing). The probe strips glob/type anyway, so
    # it counts every walked file (early-stopping at the ceiling) regardless of the filter flags.
    if (
        paths_defaulted
        and not allow_broad_generated_scan
        and config.max_depth is None
        and _implicit_glob_search_walk_exceeds_ceiling(
            paths_to_search, config, _LARGE_ROOT_SCAN_FILE_CEILING
        )
    ):
        typer.echo(
            _format_unbounded_large_root_scan_error(_LARGE_ROOT_SCAN_FILE_CEILING),
            err=True,
        )
        raise typer.Exit(2)

    explicit_rg_format = _explicit_rg_format_requested(format_value=format_type)
    # C3: plain `--json` emits one aggregate object and cannot render ripgrep's
    # text-shaping flags. Honoring them is impossible and silently dropping them is a
    # footgun that also lets the front-door launcher spawn an undrained text-render
    # child (-> deadlock). Fail fast and deterministically before any child is spawned.
    if json and not explicit_rg_format:
        # Detect from PARSED typer params (not sys.argv): reading sys.argv mis-fires when
        # the typer app is invoked in-process (e.g. CliRunner under pytest, whose argv
        # carries -p/--pretty-looking flags). The ambiguous-default flags (--heading and
        # the separators) are caught for the real CLI by the bootstrap launcher guard
        # (_json_aggregate_blocks_passthrough), so the secondary net here only needs the
        # unambiguously-set render flags (audit C3).
        incompatible_render_flags = [
            spelling
            for present, spelling in (
                (passthru, "--passthru"),
                (trim, "--trim"),
                (byte_offset, "-b"),
                (max_columns is not None, "-M"),
                (max_columns_preview, "--max-columns-preview"),
                (pretty, "-p"),
            )
            if present
        ]
        if incompatible_render_flags:
            flag_list = ", ".join(incompatible_render_flags)
            _exit_search_error(
                "unsupported_flag",
                (
                    f"flag(s) {flag_list} not supported with plain --json; "
                    "use --format rg --json for ripgrep JSON Lines that carry render "
                    "metadata, or drop the flag(s)."
                ),
                json_mode=True,
                exit_code=2,
            )
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

    if multi_pattern_source and not regexp_patterns:
        # The `-f`-alone sub-case (see the comment above the earlier `combined_multi_patterns`
        # assignment) is deferred until HERE, now that a real search is confirmed to not be
        # rg-passthrough (the `if can_passthrough_rg: if not stats: ... sys.exit(...)` block
        # just above already returned when it would have applied). Reading `-f` eagerly broke
        # `test_python_search_treats_file_option_as_pattern_file_not_regex`, where real `rg`
        # itself must read the pattern file on the passthrough path, never tg. This must land
        # before `Pipeline(...)` below, which reads `config.query_pattern` to route (audit #69,
        # re-do of #441).
        file_sourced_patterns = _read_patterns_from_file_list(file or [], json_mode=json)
        try:
            for regex_pattern in file_sourced_patterns:
                _validate_search_regex(regex_pattern, config)
        except Exception as exc:
            if _is_invalid_regex_error(exc):
                if (
                    _is_inline_flag_regex_error(str(exc))
                    and _eligible_for_pcre2_inline_flag_fallback(config)
                    and _pcre2_fallback_backend_available()
                ):
                    config = dataclasses.replace(config, pcre2=True)
                    typer.echo(
                        "note: retried with PCRE2 (-P) for inline-flag pattern",
                        err=True,
                    )
                else:
                    _exit_invalid_regex(exc, json_mode=json)
            else:
                raise
        pattern = _combine_multi_patterns(file_sourced_patterns, fixed_strings=config.fixed_strings)
        config = dataclasses.replace(config, query_pattern=pattern, fixed_strings=False)

    scanner = DirectoryScanner(config)
    candidate_files_ordered, candidate_files_set = _collect_candidate_files(
        scanner, paths_to_search
    )
    config.input_total_bytes = _sum_total_bytes(candidate_files_ordered)

    from tensor_grep.core.pipeline import Pipeline
    from tensor_grep.core.result import SearchResult, merge_runtime_routing

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

    # F6: at this point neither native delegation, the rg-passthrough fast path, nor the
    # stats-passthrough branch just above is handling this query for real -- the ONLY
    # remaining fast lane is Pipeline itself having routed to `RipgrepBackend` (the single
    # branch below that hands ALL candidates to one native call). Anything else means the
    # slow per-file Python loop is about to run with no bound but the wall-clock deadline
    # (trap: refusing a working native/rg-routed search would turn an instant search into
    # an error on every ordinary repo, so this checks the ACTUAL selected backend, not just
    # binary availability).
    if selected_backend_name != "RipgrepBackend" and _should_refuse_unbounded_large_root_scan(
        len(candidate_files_ordered),
        config,
        allow_broad_generated_scan=allow_broad_generated_scan,
        paths_defaulted=paths_defaulted,
    ):
        typer.echo(
            _format_unbounded_large_root_scan_error(_LARGE_ROOT_SCAN_FILE_CEILING),
            err=True,
        )
        raise typer.Exit(2)

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
    all_results.fallback_reason = getattr(pipeline, "fallback_reason", None)
    search_start = time.perf_counter()
    matched_file_paths: set[str] = set()
    matched_file_paths_ordered: list[str] = []

    def _record_matched_file(file_path: str | None) -> None:
        if not file_path or file_path in matched_file_paths:
            return
        matched_file_paths.add(file_path)
        matched_file_paths_ordered.append(file_path)

    def _merge_runtime_routing(result: SearchResult) -> None:
        merge_runtime_routing(all_results, result)
        if result.fallback_reason is not None:
            all_results.fallback_reason = result.fallback_reason

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
        # Critical unscoped-search-hang fix (B): the native (CPU/Torch) engine has no
        # internal per-file timeout -- unlike the RipgrepBackend branch above, which is
        # bounded by the rg subprocess's own `configured_ripgrep_timeout_seconds()` timeout.
        # A search that can't route through rg (native `--json` aggregate, `--rank`,
        # tensor-only flags, or rg absent from PATH) would otherwise walk
        # `candidate_files_ordered` with NO limit at all and could hang until manually
        # killed on a large/unscoped tree. Check the SAME wall-clock budget once per FILE
        # (never per match -- that would be too fine-grained to bound a pathological single
        # file) and, on expiry, stop and return whatever was found so far as an explicitly
        # incomplete (never silently empty, never a raw crash) result.
        from tensor_grep.backends.cpu_backend import (
            compute_native_walk_deadline,
            native_walk_deadline_exceeded,
        )
        from tensor_grep.cli.subprocess_policy import configured_ripgrep_timeout_seconds

        native_walk_deadline = compute_native_walk_deadline()
        for current_file in candidate_files_ordered:
            if native_walk_deadline_exceeded(native_walk_deadline):
                timeout_seconds = configured_ripgrep_timeout_seconds()
                all_results.result_incomplete = True
                all_results.incomplete_reason = (
                    f"native search exceeded the {timeout_seconds:g}s timeout and was "
                    "stopped; returning partial results. Scope the search to a smaller "
                    "path, or raise TG_RG_TIMEOUT_SECONDS."
                )
                sys.stderr.write(
                    "tg: native search exceeded the "
                    f"{timeout_seconds:g}s timeout, keeping partial results: "
                    f"{all_results.incomplete_reason}\n"
                )
                break
            span_ctx = (
                tracer.start_as_current_span("search.file") if tracer is not None else nullcontext()
            )
            with span_ctx as span, nvtx_range("search.file", color="cyan"):
                if span is not None:
                    span.set_attribute("backend", backend.__class__.__name__)
                    span.set_attribute("path", current_file)
                try:
                    result = backend.search(current_file, pattern, config=config)
                except BackendExecutionError as exc:
                    # A native backend failed at runtime; retry once on the always-
                    # available CPU backend so the search returns correct results instead
                    # of a false no-match or a crash (audit B2/I1).
                    result = _search_with_cpu_fallback(current_file, pattern, config, exc)
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
    if config.semantic_rank:
        if all_results.matches:
            try:
                all_results = _apply_semantic_rerank(all_results, pattern)
            except BackendExecutionError as exc:
                # F4 (Fable audit MED): a genuine dense-backend fault (e.g. a corrupt model
                # directory) must exit cleanly with a `tg:` message, never a raw traceback --
                # `_apply_semantic_rerank` deliberately does NOT catch this (see its docstring);
                # this is the CLI boundary the Backend Fail-Closed Contract requires.
                if json:
                    _emit_search_error_json("semantic_backend_error", str(exc))
                else:
                    typer.echo(f"tg: {exc}", err=True)
                sys.exit(2)
        else:
            # F16 (Fable audit LOW): probe dense-leg availability even on a 0-match search so
            # `rank_fallback_reason` is set whenever the leg is unavailable, regardless of match
            # count -- skipping the probe here silently made the JSON envelope dishonest.
            _set_semantic_rank_fallback_reason(all_results)
    elif config.rank_bm25 and all_results.matches:
        from tensor_grep.core.reranker import rerank_by_bm25

        all_results = rerank_by_bm25(all_results, pattern, all_results.matched_file_paths)
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
            sys.exit(2 if all_results.result_incomplete else 0)
        _emit_stats()
        sys.exit(2 if all_results.result_incomplete else 1)

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
            sys.exit(2 if all_results.result_incomplete else 0)
        _emit_stats()
        sys.exit(2 if all_results.result_incomplete else 1)

    if all_results.is_empty:
        _emit_stats()
        if json or format_type == "json":
            from tensor_grep.cli.formatters.json_fmt import JsonFormatter

            _safe_stdout_line(JsonFormatter().format(all_results))
        sys.exit(2 if all_results.result_incomplete else 1)

    if quiet:
        _emit_stats()
        sys.exit(2 if all_results.result_incomplete else 0)

    formatter: OutputFormatter

    if ndjson:
        from tensor_grep.cli.formatters.json_fmt import NdjsonFormatter

        formatter = NdjsonFormatter()
    elif json or format_type == "json":
        from tensor_grep.cli.formatters.json_fmt import JsonFormatter

        # Pass the search config so aggregate --json match objects can carry the 1-based
        # `column` for text-search matches (which have no ast-grep range) — audit L5.
        formatter = JsonFormatter(config=config)
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
    if all_results.result_incomplete:
        # rg-parity: partial results (rg exit 2, soft per-file error) exit 2 after a formatted
        # success, not 0 — so a caller/agent sees the same incompleteness rg would signal.
        sys.exit(2)


@app.command()
def calibrate() -> None:
    """Measure CPU vs GPU crossover thresholds using the native Rust binary."""
    native_tg_binary = resolve_native_tg_binary()
    if native_tg_binary is None:
        # audit L10: calibrate is unsupported without the native binary (and on CPU-only
        # boxes the native binary itself exits non-zero when CUDA is unavailable). tg's
        # convention is exit 1 for runtime/unsupported errors, not exit 2 (usage errors).
        typer.echo("Error: native tg binary not found for calibrate command.", err=True)
        raise typer.Exit(1)

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
        print(json.dumps(_with_schema_version(payload)))
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
        512,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning. Defaults to the agent-safe 512-file cap.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a deterministic repository map for AI editing workflows."""
    from tensor_grep.cli.repo_map import (
        DEFAULT_AGENT_REPO_MAP_LIMIT,
        apply_repo_map_output_limits,
        build_repo_map,
    )

    try:
        effective_max_repo_files = max_repo_files or DEFAULT_AGENT_REPO_MAP_LIMIT
        payload = build_repo_map(path, max_repo_files=effective_max_repo_files)
        payload = apply_repo_map_output_limits(payload, max_files=max_files)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Cold path (Cluster B, 2026-07-06): dump the SAME payload/limit order the old build_repo_map_json
    # helper used (build_repo_map then apply_repo_map_output_limits, json.dumps(indent=2)) so JSON
    # stays byte-identical, and gate on it so both json and text branches share the scan-truncation
    # contract -- output the full payload FIRST, then exit 2 if the scan itself was capped (an
    # output-only cap from --max-files stays exit 0).
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"Repository map for {payload['path']}")
        typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")
        typer.echo(f"symbols={len(payload['symbols'])} imports={len(payload['imports'])}")

    if _scan_incomplete(payload):
        raise typer.Exit(2)


@app.command()
def inventory(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    max_repo_files: int = typer.Option(
        # Literal mirrors inventory.DEFAULT_MAX_INVENTORY_FILES (kept literal so the heavy
        # repo_map import stays lazy, matching `map`'s 512 pattern); a guard test pins them.
        50_000,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before truncating (walk-only; defaults to 50000).",
    ),
    deadline: float | None = typer.Option(
        None,
        "--deadline",
        min=0.1,
        help=(
            "Stop the inventory scan after N seconds and return a partial manifest labeled "
            "scan_limit.truncation_cause='deadline' (counts are a floor), instead of running unbounded "
            "on a huge tree."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Emit a single-pass repository inventory (files, bytes, languages, categories)."""
    import json as _json

    from tensor_grep.cli.inventory import build_inventory, render_inventory_text

    try:
        payload = build_inventory(path, max_files=max_repo_files, deadline_seconds=deadline)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(_json.dumps(payload))
    else:
        typer.echo(render_inventory_text(payload))

    # #130(a) optional bundle: mirror `map`'s exit-2-on-scan-truncation contract (:7418-7419)
    # -- a truncated scan (scan_limit.possibly_truncated, e.g. a fired --deadline) previously
    # always exited 0, indistinguishable from a genuinely complete inventory. _scan_incomplete
    # already checks exactly this payload's scan_limit.possibly_truncated shape.
    if _scan_incomplete(payload):
        raise typer.Exit(2)


@app.command(name="docs-coverage")
def docs_coverage(
    path: str = typer.Argument(".", help="File or directory to check for governing-doc coverage"),
    max_repo_files: int = typer.Option(
        50_000,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before truncating (walk-only; defaults to 50000).",
    ),
    ignore: list[str] = typer.Option(
        [],
        "--ignore",
        help="Glob(s) of source files to exclude entirely (repeatable). Matched against the "
        "repo-relative path and basename, e.g. --ignore 'commands/*/index.js' --ignore '*.stub.py'. "
        "An intentional stub group stops being re-flagged and no longer drags coverage_pct.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Emit a paste-ready Markdown table of undocumented files (path/size/first line).",
    ),
    stale: bool = typer.Option(
        False,
        "--stale",
        help="Inverse mode: report governing-doc references to files that no longer exist.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Exit non-zero when any file is uncovered (or, with --stale, any reference is stale) "
        "-- turns docs-coverage into a CI doc-drift gate. Respects --ignore.",
    ),
) -> None:
    """List source files not referenced by any governing doc (CLAUDE.md/README/AGENTS.md)."""
    import json as _json

    from tensor_grep.cli.docs_coverage import (
        build_docs_coverage,
        build_docs_stale_references,
        render_docs_coverage_fix_markdown,
        render_docs_coverage_text,
        render_docs_stale_text,
    )

    # --fix renders a Markdown table of UNCOVERED source files (build_docs_coverage's
    # uncovered_details shape); --stale reports a disjoint shape (doc -> dangling reference) with no
    # analogous fix-table renderer. Silently ignoring --fix here previously looked like a no-op with
    # no signal (audit #23); reject up front rather than emit a report the flag never affected.
    if stale and fix:
        typer.echo(
            "Error: --fix is not supported with --stale (no fix table for stale references).",
            err=True,
        )
        raise typer.Exit(1)

    try:
        if stale:
            stale_payload = build_docs_stale_references(
                path, max_files=max_repo_files, ignore=tuple(ignore)
            )
            if json_output:
                typer.echo(_json.dumps(stale_payload))
            else:
                _safe_stdout_line(render_docs_stale_text(stale_payload))
            # --check exits AFTER emitting the report, so CI shows what failed AND fails the job.
            if check and stale_payload["totals"]["stale"] > 0:
                raise typer.Exit(1)
            return
        payload = build_docs_coverage(
            path, max_files=max_repo_files, include_details=fix, ignore=tuple(ignore)
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(_json.dumps(payload))
    # Text output can embed a resolved filesystem path (non-English username -> non-ASCII); route
    # through the cp1252-safe writer, never bare typer.echo (the #346 crash class).
    elif fix:
        _safe_stdout_line(render_docs_coverage_fix_markdown(payload))
    else:
        _safe_stdout_line(render_docs_coverage_text(payload))
    if check and payload["totals"]["uncovered"] > 0:
        raise typer.Exit(1)


@app.command()
def orient(
    path: str = typer.Argument(".", help="File or directory to orient on"),
    max_tokens: int = typer.Option(3000, "--max-tokens", help="Snippet token budget", min=1),
    max_central_files: int = typer.Option(
        10, "--max-central-files", help="Number of top central files to surface", min=1
    ),
    ignore: list[str] = typer.Option(
        [],
        "--ignore",
        help=(
            "Glob(s) to exclude from the centrality ranking (basename or repo-relative path), e.g. "
            "--ignore 'seo/**' --ignore 'core/skills/**'. Excludes vendor/skill CODE trees that "
            "otherwise rank as 'central' on a harness repo. Repeatable."
        ),
    ),
    no_auto_deweight: bool = typer.Option(
        False,
        "--no-auto-deweight",
        help=(
            "Disable auto de-weighting of detected vendor/skill/generated CODE subtrees (nested "
            "package manifest + import-island or name prior). De-weighting is ON by default and "
            "only LOWERS a subtree's centrality score -- it never excludes files; use --ignore for "
            "a hard exclude."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the capsule as JSON"),
) -> None:
    """Emit a one-call codebase orientation capsule (central files, entry points, AST snippets)."""
    from tensor_grep.cli.orient_capsule import build_orient_capsule, build_orient_capsule_json

    try:
        if json_output:
            typer.echo(
                build_orient_capsule_json(
                    path,
                    max_tokens=max_tokens,
                    max_central_files=max_central_files,
                    ignore=tuple(ignore),
                    auto_deweight=not no_auto_deweight,
                )
            )
            return
        payload = build_orient_capsule(
            path,
            max_tokens=max_tokens,
            max_central_files=max_central_files,
            ignore=tuple(ignore),
            auto_deweight=not no_auto_deweight,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"# Codebase orientation: {payload['path']}")
    typer.echo(f"central files ({len(payload['central_files'])}):")
    for cf in payload["central_files"]:
        typer.echo(f"  {cf['file']}  (in-degree={cf['graph_score']})")
    if payload["deweighted_trees"]:
        typer.echo(f"deweighted_trees ({len(payload['deweighted_trees'])}):")
        for tree in payload["deweighted_trees"]:
            typer.echo(f"  {tree['path']}  ({', '.join(tree['reasons'])})")
    typer.echo(
        f"entry_points={len(payload['entry_points'])} "
        f"snippets={len(payload['snippets'])} ~{payload['token_estimate']} tokens"
    )


@app.command()
def codemap(
    path: str = typer.Argument(".", help="Directory to render a browsable code map for"),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Output directory for pages + _coverage.json. Defaults to <path>/docs/code-map.",
    ),
    index_file: str = typer.Option(
        "index.md",
        "--index",
        help="Index filename, resolved inside --out unless given as an absolute path.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Read-only freshness check of an existing code map (no re-parse); exits 1 when stale.",
    ),
    max_repo_files: int = typer.Option(
        # Literal mirrors codemap.DEFAULT_MAX_REPO_FILES (kept literal so the heavy repo_map
        # import stays lazy, matching map's/inventory's pattern); a guard test pins them.
        50_000,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before truncating (walk-only; defaults to 50000).",
    ),
    max_symbols_per_file: int = typer.Option(
        50,
        "--max-symbols-per-file",
        min=1,
        help="Per-file symbol cap before an overflow pointer line (defaults to 50).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Render a persisted, browsable folder->file->symbol code map (lean index + per-folder pages)."""
    from tensor_grep.cli.codemap import build_codemap, check_codemap_freshness

    if check:
        try:
            result = check_codemap_freshness(
                path, out=out, index=index_file, max_repo_files=max_repo_files
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

        if json_output:
            typer.echo(json.dumps(result, indent=2, sort_keys=True))
        else:
            status = "fresh" if result["fresh"] else "stale"
            _safe_stdout_line(f"codemap --check: {status} -- {result['reason']}")

        if not result["fresh"]:
            raise typer.Exit(1)
        return

    try:
        payload = build_codemap(
            path,
            out=out,
            index=index_file,
            max_repo_files=max_repo_files,
            max_symbols_per_file=max_symbols_per_file,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _safe_stdout_line(f"Code map for {payload['path']}")
        _safe_stdout_line(f"out={payload['out']} index={payload['index']}")
        _safe_stdout_line(
            f"folders={payload['folders_total']} files={payload['files_total']} "
            f"symbols={payload['symbols_total']}"
        )
        if payload.get("partial"):
            _safe_stdout_line(f"PARTIAL: {payload.get('remediation', '')}")

    if _scan_incomplete(payload):
        raise typer.Exit(2)


@app.command()
def context(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query_arg: str | None = typer.Argument(
        None, help="Query text used to rank relevant repo context."
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
    max_files: int | None = typer.Option(
        None, "--max-files", min=1, help="Maximum ranked source files to include."
    ),
    max_repo_files: int | None = typer.Option(
        None, "--max-repo-files", min=1, help="Maximum repo files to scan before ranking."
    ),
    max_tokens: int = typer.Option(
        # Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS (literal keeps the heavy repo_map import lazy,
        # matching inventory's 50_000 pattern; a guard test pins them). The pack is for prompt
        # injection, so bound it by default -- an unbounded pack ballooned to >1MB (dogfood v1.19.9).
        16000,
        "--max-tokens",
        min=0,
        help="Bound the context pack to ~N tokens for prompt injection (0 = unbounded).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a ranked repository context pack for edit planning."""
    from tensor_grep.cli.repo_map import build_context_pack

    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="context",
        )
        payload = build_context_pack(
            resolved_query,
            resolved_path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_tokens=max_tokens,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Build the payload ONCE and gate both branches on it (mirrors `map`'s cold-path contract,
    # Cluster B 2026-07-06): the old json branch called build_context_pack_json + returned early,
    # which meant a >max_repo_files scan (default cap) silently truncated and always exited 0 --
    # `context` was the only command in this family that never gated on `_scan_incomplete` (audit #9).
    if json_output:
        typer.echo(json.dumps(payload))
    else:
        typer.echo(f"Context pack for {payload['path']}")
        typer.echo(f"query={payload['query']}")
        typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")
        typer.echo(f"symbols={len(payload['symbols'])} imports={len(payload['imports'])}")

    if _scan_incomplete(payload):
        raise typer.Exit(2)


def _daemon_directory_path(path: str) -> str | None:
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except OSError:
        return None
    if resolved.is_file():
        return None
    return str(resolved)


def _session_daemon_autostart_enabled() -> bool:
    """TG_SESSION_DAEMON_AUTOSTART opt-out for the default Tier-1 warm-daemon fast path.

    Task #94 PR-1 (the conscious default flip flagged by the original Part A comment; cleared
    after #498 landed the daemon response-cache correctness fix docs/BACKLOG.md's #94 entry
    gated the flip on). DEFAULT ON: unset -- or any value other than an explicit falsy token
    (``0``/``false``/``no``/``off``, see ``env_flag_disabled`` in runtime_paths.py) -- routes
    defs/impact/refs/callers/blast-radius through a running ``tg session daemon``, non-blocking
    auto-spawning one on a miss. This is the ~20x warm-vs-cold latency win: the cold path pays a
    6-33s repo-map build on every call. Set the flag to an explicit falsy token to opt back out
    to the always-cold path, byte-for-byte unchanged from before this PR.

    Auto-forced OFF whenever CI or GITHUB_ACTIONS is set, regardless of the flag's own value,
    so a CI job can never leave a background session-daemon process (idle-lived up to
    TG_SESSION_DAEMON_IDLE_SECONDS, 900s default) running past the job that spawned it.
    """
    if env_flag_enabled("CI") or env_flag_enabled("GITHUB_ACTIONS"):
        return False
    return not env_flag_disabled("TG_SESSION_DAEMON_AUTOSTART")


def _maybe_symbol_command_via_running_daemon(
    *,
    command: str,
    path: str,
    symbol: str,
    provider: str,
    max_repo_files: int,
    max_tests: int | None = None,
    max_depth: int | None = None,
) -> dict[str, Any] | None:
    """Fail-open Tier-1 default fast path (task #94 Part A) for defs/impact/refs/callers/
    blast_radius.

    Mirrors the existing ``_maybe_context_render_via_running_daemon`` /
    ``_maybe_edit_plan_via_running_daemon`` fail-open shape (probe-only, ``except Exception:
    return None``) with one addition: on a probe MISS (no daemon reachable yet) it fires a
    non-blocking spawn so a LATER call is warm, while THIS call still returns None and the
    caller runs the existing cold path unchanged (must-fix 3 -- cold call #1 must never block
    on daemon warmup).

    Returns None (forcing the caller's cold path) whenever: the flag is off, a non-native
    provider was requested (the daemon session is native-only, same rule as context-render/
    edit-plan), the path does not resolve to a directory, no daemon could be reached, or the
    daemon responded with an error. A `refresh_on_stale=True` request (mirroring
    ``_maybe_context_render_via_running_daemon``) means a session whose files changed on disk
    is refreshed once before being served, so warm output matches cold output on a changed
    tree (must-fix 5).
    """
    if not _session_daemon_autostart_enabled():
        return None
    if provider != "native":
        return None
    daemon_path = _daemon_directory_path(path)
    if daemon_path is None:
        return None
    try:
        from tensor_grep.cli.session_daemon import (
            maybe_autostart_session_daemon_nonblocking,
            request_running_session_daemon,
        )

        request: dict[str, Any] = {
            "command": command,
            "path": daemon_path,
            "symbol": symbol,
            "provider": provider,
            "refresh_on_stale": True,
            "max_repo_files": max_repo_files,
        }
        if max_tests is not None:
            request["max_tests"] = max_tests
        if max_depth is not None:
            request["max_depth"] = max_depth
        payload = request_running_session_daemon(daemon_path, request)
        if payload is None:
            # No daemon reachable yet. Fire-and-forget spawn so a LATER call is warm; THIS
            # call must not block on daemon startup -- run the cold path below (must-fix 3).
            maybe_autostart_session_daemon_nonblocking(daemon_path)
            return None
        if "error" in payload:
            return None
        return payload
    except Exception:
        return None


def _maybe_context_render_via_running_daemon(
    *,
    path: str,
    query: str,
    max_files: int,
    max_repo_files: int,
    max_sources: int,
    max_symbols_per_file: int,
    max_render_chars: int | None,
    max_tokens: int | None,
    model: str | None,
    optimize_context: bool,
    render_profile: str,
    provider: str,
    profile: bool,
) -> dict[str, Any] | None:
    if provider != "native":
        return None
    daemon_path = _daemon_directory_path(path)
    if daemon_path is None:
        return None
    try:
        from tensor_grep.cli.session_daemon import request_running_session_daemon

        payload = request_running_session_daemon(
            daemon_path,
            {
                "command": "context_render",
                "path": daemon_path,
                "query": query,
                "refresh_on_stale": True,
                "max_files": max_files,
                "max_sources": max_sources,
                "max_symbols_per_file": max_symbols_per_file,
                "max_render_chars": max_render_chars,
                "max_tokens": max_tokens,
                "model": model,
                "optimize_context": optimize_context,
                "render_profile": render_profile,
                "profile": profile,
                "max_repo_files": max_repo_files,
            },
        )
        if payload is None or "error" in payload:
            return None
        return payload
    except Exception:
        return None


def _maybe_edit_plan_via_running_daemon(
    *,
    path: str,
    query: str,
    max_files: int,
    max_repo_files: int,
    max_sources: int | None,
    max_tokens: int | None,
    max_symbols: int,
    provider: str,
    profile: bool,
) -> dict[str, Any] | None:
    if provider != "native":
        return None
    daemon_path = _daemon_directory_path(path)
    if daemon_path is None:
        return None
    try:
        from tensor_grep.cli.session_daemon import request_running_session_daemon

        payload = request_running_session_daemon(
            daemon_path,
            {
                "command": "context_edit_plan",
                "path": daemon_path,
                "query": query,
                "refresh_on_stale": True,
                "max_files": max_files,
                "max_sources": max_sources,
                "max_tokens": max_tokens,
                "max_symbols": max_symbols,
                "profile": profile,
                "max_repo_files": max_repo_files,
            },
        )
        if payload is None or "error" in payload:
            return None
        return payload
    except Exception:
        return None


@app.command(name="context-render")
def context_render(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query_arg: str | None = typer.Argument(
        None, help="Query text used to rank and render repo context."
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
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
    max_tokens: int = typer.Option(
        # Bound a prompt-ready render bundle by default, mirroring the `context` command (dogfood
        # 1.23.0: context-render defaulted to ~800KB, too big for prompt injection). 0 = unbounded;
        # downstream normalizes <=0 -> None (repo_map.py _normalize / _apply_context_token_budget).
        16000,
        "--max-tokens",
        min=0,
        help="Bound the rendered_context to ~N tokens for prompt injection (0 = unbounded).",
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
    provider: str = typer.Option(
        "native",
        "--provider",
        help="Semantic provider for primary target proof: native, lsp, or hybrid.",
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-phase profiling in JSON output."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a prompt-ready repository context bundle for edit planning."""
    from tensor_grep.cli.repo_map import build_context_render

    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="context-render",
        )
        resolved_render_profile = render_profile or ("llm" if json_output else "full")
        resolved_optimize_context = optimize_context or (json_output and render_profile is None)
        daemon_payload = _maybe_context_render_via_running_daemon(
            path=resolved_path,
            query=resolved_query,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            max_tokens=max_tokens,
            model=model,
            optimize_context=resolved_optimize_context,
            render_profile=resolved_render_profile,
            provider=provider,
            profile=profile,
        )
        if daemon_payload is not None:
            # Output-before-exit (Cluster B, 2026-07-06): the warm-daemon path must honor the same
            # exit-2-on-scan-truncation contract as the cold path below -- a truncated daemon payload
            # still prints in full, then exits 2, instead of a silent exit 0 that reads as complete.
            if json_output:
                if daemon_payload.get("render_profile") == "llm":
                    typer.echo(json.dumps(daemon_payload, separators=(",", ":")))
                else:
                    typer.echo(json.dumps(daemon_payload, indent=2))
            else:
                typer.echo(str(daemon_payload.get("rendered_context", "")))
            if _scan_incomplete(daemon_payload):
                raise typer.Exit(2)
            return

        payload = build_context_render(
            resolved_query,
            resolved_path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            max_tokens=max_tokens,
            model=model,
            optimize_context=resolved_optimize_context,
            render_profile=resolved_render_profile,
            semantic_provider=provider,
            profile=profile,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Cold path (Cluster B, 2026-07-06): build the payload once and dump it here (byte-identical to
    # the old build_context_render_json helper: separators=(",", ":") for an "llm" render profile,
    # else indent=2) so both json and text branches share the same scan-truncation gate below --
    # output the full payload FIRST, then exit 2 if the scan itself (not just the output) was capped.
    if json_output:
        if payload.get("render_profile") == "llm":
            typer.echo(json.dumps(payload, separators=(",", ":")))
        else:
            typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(payload["rendered_context"])

    if _scan_incomplete(payload):
        raise typer.Exit(2)


@app.command(name="agent")
def agent(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query_arg: str | None = typer.Argument(None, help="Natural-language task or symbol query."),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
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
    provider: str = typer.Option(
        "native",
        "--provider",
        help="Semantic provider for primary target proof: native, lsp, or hybrid.",
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
    ignore: list[str] = typer.Option(
        [],
        "--ignore",
        help=(
            "Glob(s) to exclude from the capsule ranking (basename or repo-relative path), e.g. "
            "--ignore 'seo/**' --ignore 'core/skills/**'. Excludes vendor/skill CODE trees that "
            "otherwise rank as the primary target on a harness repo. Repeatable."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return an actionable context capsule for agents before editing."""
    from tensor_grep.cli.agent_capsule import build_agent_capsule

    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="agent",
        )
        parsed_gpu_device_ids = _parse_gpu_device_ids_cli(gpu_device_ids)
        payload = build_agent_capsule(
            resolved_query,
            resolved_path,
            max_files=max_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_repo_files=max_repo_files,
            model=model,
            semantic_provider=provider,
            gpu_device_ids=parsed_gpu_device_ids,
            gpu_timeout_s=gpu_timeout_s,
            ignore=tuple(ignore),
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Cold path (PR-1 1D, mirrors the context-render cold path :7486-7499): build the payload once
    # and dump it here for both the json and text branches -- BYTE-IDENTICAL to the old
    # `build_agent_capsule_json` serialization (`ensure_ascii=False, indent=2`) -- so they share
    # ONE scan-truncation gate below. Output the full payload FIRST, then exit 2 if the SCAN itself
    # (not just the capsule's own render/token output budget) was capped -- `tg agent` was
    # previously the only command in this family that never gated on `_scan_incomplete`.
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        primary = payload.get("primary_target", {})
        primary_file = primary.get("file") or "<none>"
        primary_line = primary.get("line") or 1
        primary_symbol = primary.get("symbol") or "<unknown>"
        validation_commands = payload.get("validation_commands", [])
        confidence = payload.get("confidence", {}).get("overall", 0)
        gpu_acceleration = payload.get("gpu_acceleration", {})
        ambiguity = payload.get("ambiguity", {})
        ask_user_before_editing = payload.get("ask_user_before_editing", {})
        context_consistency = payload.get("context_consistency", {})
        alternatives = payload.get("alternative_targets", [])
        typer.echo(f"Agent capsule for {payload['path']}")
        typer.echo(f"query={payload['query']}")
        typer.echo(f"primary={primary_file}#L{primary_line} {primary_symbol}")
        typer.echo(f"validation={len(validation_commands)} commands")
        typer.echo(f"confidence={confidence}")
        typer.echo(f"ask_required={bool(ask_user_before_editing.get('required'))}")
        typer.echo(f"ambiguity={ambiguity.get('status', 'unknown')}")
        typer.echo(
            "alternatives="
            f"{len(alternatives)}"
            f" omitted={context_consistency.get('alternative_targets_omitted_count', 0)}"
        )
        if gpu_device_ids:
            typer.echo(f"gpu_acceleration={gpu_acceleration.get('status', 'unknown')}")

    if _scan_incomplete(payload):
        raise typer.Exit(2)


@app.command(name="edit-plan")
def edit_plan(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query_arg: str | None = typer.Argument(None, help="Query text used to rank edit targets."),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
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
    provider: str = typer.Option(
        "native",
        "--provider",
        help="Semantic provider for primary target proof: native, lsp, or hybrid.",
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-phase profiling in JSON output."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a machine-readable edit-planning bundle without rendered source text."""
    from tensor_grep.cli.repo_map import build_context_edit_plan

    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="edit-plan",
        )
        daemon_payload = _maybe_edit_plan_via_running_daemon(
            path=resolved_path,
            query=resolved_query,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_symbols=max_symbols,
            provider=provider,
            profile=profile,
        )
        if daemon_payload is not None:
            # Output-before-exit (Cluster B, 2026-07-06): same exit-2-on-scan-truncation contract as
            # the cold path below -- print the full daemon payload, then exit 2 if it was truncated.
            if json_output:
                typer.echo(json.dumps(daemon_payload, indent=2))
            else:
                payload = daemon_payload
                typer.echo(f"Edit plan for {payload['path']}")
                typer.echo(f"query={payload['query']}")
                typer.echo(
                    f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
                )
            if _scan_incomplete(daemon_payload):
                raise typer.Exit(2)
            return

        payload = build_context_edit_plan(
            resolved_query,
            resolved_path,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_sources=max_sources,
            max_tokens=max_tokens,
            max_symbols=max_symbols,
            semantic_provider=provider,
            profile=profile,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Cold path (Cluster B, 2026-07-06): build the payload once and dump it here (byte-identical to
    # the old build_context_edit_plan_json helper: json.dumps(payload, indent=2)) so both json and
    # text branches share the same scan-truncation gate below -- output the full payload FIRST, then
    # exit 2 if the scan itself (not just the output) was capped.
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"Edit plan for {payload['path']}")
        typer.echo(f"query={payload['query']}")
        typer.echo(
            f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
        )

    if _scan_incomplete(payload):
        raise typer.Exit(2)


_ROUTE_TEST_CONFIDENCE_WARNING_THRESHOLD = 0.75
# When both routes AGREE on the primary target, a sub-threshold confidence reflects ranking-score
# calibration, not routing doubt -- it is demoted to an additive `note`, not a `warning`. But
# agreement is not correctness (context-render + edit-plan share the upstream ranker, so they can
# agree on the same WRONG file); if BOTH confidences fall below this floor, keep the warning as the
# correlated-error tell.
_ROUTE_TEST_CONFIDENCE_FLOOR = 0.4


def _route_test_int(value: object) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _route_test_confidence_score(confidence: object) -> float | None:
    if isinstance(confidence, dict):
        for key in ("overall", "primary", "target"):
            try:
                return float(cast(Any, confidence[key]))
            except (KeyError, TypeError, ValueError):
                continue
        primary_scores: list[float] = []
        for key in ("file", "symbol"):
            try:
                primary_scores.append(float(cast(Any, confidence[key])))
            except (KeyError, TypeError, ValueError):
                continue
        if primary_scores:
            return min(primary_scores)
        return None
    try:
        return float(cast(Any, confidence))
    except (TypeError, ValueError):
        return None


def _route_test_primary_target(payload: dict[str, Any]) -> dict[str, Any]:
    raw_primary_target = payload.get("primary_target")
    if not isinstance(raw_primary_target, dict):
        navigation_pack = payload.get("navigation_pack")
        if isinstance(navigation_pack, dict):
            raw_primary_target = navigation_pack.get("primary_target")
    primary_target = dict(raw_primary_target) if isinstance(raw_primary_target, dict) else {}

    edit_plan_seed = payload.get("edit_plan_seed")
    seed = dict(edit_plan_seed) if isinstance(edit_plan_seed, dict) else {}
    primary_symbol = seed.get("primary_symbol")
    primary_symbol_payload = dict(primary_symbol) if isinstance(primary_symbol, dict) else {}
    primary_span = seed.get("primary_span")
    primary_span_payload = dict(primary_span) if isinstance(primary_span, dict) else {}

    file_path = str(
        primary_target.get("file")
        or seed.get("primary_file")
        or primary_symbol_payload.get("file")
        or ""
    )
    symbol = str(
        primary_target.get("symbol")
        or primary_symbol_payload.get("name")
        or primary_symbol_payload.get("symbol")
        or ""
    )
    line = (
        _route_test_int(primary_target.get("line"))
        or _route_test_int(primary_target.get("start_line"))
        or _route_test_int(primary_span_payload.get("start_line"))
        or _route_test_int(primary_symbol_payload.get("line"))
        or _route_test_int(primary_symbol_payload.get("start_line"))
    )
    end_line = (
        _route_test_int(primary_target.get("end_line"))
        or _route_test_int(primary_span_payload.get("end_line"))
        or _route_test_int(primary_symbol_payload.get("end_line"))
        or line
    )
    confidence = primary_target.get("confidence")
    if confidence is None:
        confidence = seed.get("confidence")
    confidence_score = _route_test_confidence_score(confidence)

    return {
        "file": file_path or None,
        "symbol": symbol or None,
        "line": line,
        "end_line": end_line,
        "confidence": confidence if confidence is not None else None,
        "confidence_score": confidence_score,
    }


def _route_test_normalized_file(value: object) -> str:
    if not value:
        return ""
    try:
        return os.path.normcase(str(Path(str(value)).expanduser().resolve(strict=False)))
    except OSError:
        return os.path.normcase(str(value))


def _route_test_validation_command_count(payload: dict[str, Any]) -> int | None:
    validation_commands = payload.get("validation_commands")
    if isinstance(validation_commands, list):
        return len(validation_commands)
    navigation_pack = payload.get("navigation_pack")
    if isinstance(navigation_pack, dict) and isinstance(
        navigation_pack.get("validation_commands"), list
    ):
        return len(navigation_pack["validation_commands"])
    edit_plan_seed = payload.get("edit_plan_seed")
    if isinstance(edit_plan_seed, dict) and isinstance(
        edit_plan_seed.get("validation_commands"), list
    ):
        return len(edit_plan_seed["validation_commands"])
    return None


def _build_route_test_payload(
    *,
    path: str,
    query: str,
    max_files: int,
    max_repo_files: int,
    max_sources: int,
    max_symbols_per_file: int,
    max_symbols: int,
    provider: str,
    profile: bool,
) -> dict[str, Any]:
    from tensor_grep.cli.repo_map import build_context_edit_plan, build_context_render

    context_payload = build_context_render(
        query,
        path,
        max_files=max_files,
        max_repo_files=max_repo_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        render_profile="llm",
        optimize_context=True,
        semantic_provider=provider,
        profile=profile,
    )
    edit_payload = build_context_edit_plan(
        query,
        path,
        max_files=max_files,
        max_repo_files=max_repo_files,
        max_sources=max_sources,
        max_symbols=max_symbols,
        semantic_provider=provider,
        profile=profile,
    )

    context_target = _route_test_primary_target(context_payload)
    edit_target = _route_test_primary_target(edit_payload)
    file_agrees = _route_test_normalized_file(
        context_target["file"]
    ) == _route_test_normalized_file(edit_target["file"])
    symbol_agrees = context_target["symbol"] == edit_target["symbol"]
    line_agrees = context_target["line"] == edit_target["line"]
    agreement = bool(
        context_target["file"]
        and edit_target["file"]
        and file_agrees
        and symbol_agrees
        and line_agrees
    )

    warnings: list[str] = []
    notes: list[str] = []
    if not agreement:
        warnings.append("primary targets disagree between context-render and edit-plan")
    low_confidence_lines: list[str] = []
    scored_confidences: list[float] = []
    for label, target in (
        ("context-render", context_target),
        ("edit-plan", edit_target),
    ):
        confidence_score = target.get("confidence_score")
        if isinstance(confidence_score, int | float):
            scored_confidences.append(float(confidence_score))
            if confidence_score < _ROUTE_TEST_CONFIDENCE_WARNING_THRESHOLD:
                low_confidence_lines.append(
                    f"{label} primary target confidence {confidence_score:.3f} is below "
                    f"{_ROUTE_TEST_CONFIDENCE_WARNING_THRESHOLD:.2f}"
                )
    if low_confidence_lines:
        both_very_low = len(scored_confidences) >= 2 and all(
            c < _ROUTE_TEST_CONFIDENCE_FLOOR for c in scored_confidences
        )
        if agreement and not both_very_low:
            # Routes agree -> low confidence is calibration, not routing doubt: demote to a note.
            notes.append(
                "context-render and edit-plan agree on the primary target; the sub-threshold "
                "confidence reflects ranking-score calibration, not routing disagreement"
            )
            notes.extend(low_confidence_lines)
        else:
            warnings.extend(low_confidence_lines)

    context_validation_count = _route_test_validation_command_count(context_payload)
    edit_validation_count = _route_test_validation_command_count(edit_payload)
    return {
        "version": 1,
        "routing_reason": "route-test",
        "path": str(Path(path).expanduser().resolve(strict=False)),
        "query": query,
        "agreement": agreement,
        "agreement_details": {
            "file": file_agrees,
            "symbol": symbol_agrees,
            "line": line_agrees,
        },
        "warnings": warnings,
        "notes": notes,
        "context_render": {
            "routing_reason": context_payload.get("routing_reason"),
            "primary_target": context_target,
            "validation_command_count": context_validation_count,
        },
        "edit_plan": {
            "routing_reason": edit_payload.get("routing_reason"),
            "primary_target": edit_target,
            "validation_command_count": edit_validation_count,
        },
        "validation_command_counts": {
            "context_render": context_validation_count,
            "edit_plan": edit_validation_count,
        },
    }


# Hidden/experimental: `route-test` (compare context-render vs edit-plan routing) works but is not yet in
# the public --help surface -- promoting it needs native-binary registration + PUBLIC_TOP_LEVEL_COMMANDS
# parity (the 4-registration-sites contract). Ships hidden here; a follow-up PR promotes it to visible.
@app.command(name="route-test", hidden=True)
def route_test(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    query_arg: str | None = typer.Argument(
        None, help="Query text to compare through context-render and edit-plan."
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in each route."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repository files to scan before ranking targets.",
    ),
    max_sources: int = typer.Option(
        5, "--max-sources", min=1, help="Maximum source/span records to retain per route."
    ),
    max_symbols_per_file: int = typer.Option(
        6,
        "--max-symbols-per-file",
        min=1,
        help="Maximum context-render summary symbols to include per file.",
    ),
    max_symbols: int = typer.Option(
        5, "--max-symbols", min=1, help="Maximum edit-plan ranked symbols to retain."
    ),
    provider: str = typer.Option(
        "native",
        "--provider",
        help="Semantic provider for primary target proof: native, lsp, or hybrid.",
    ),
    profile: bool = typer.Option(
        False, "--profile", help="Include per-route profiling in the compared builders."
    ),
    json_output: bool = typer.Option(
        True, "--json/--text", help="Emit machine-readable JSON output (default)."
    ),
) -> None:
    """Compare context-render and edit-plan target routing for the same query."""
    try:
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="route-test",
        )
        payload = _build_route_test_payload(
            path=resolved_path,
            query=resolved_query,
            max_files=max_files,
            max_repo_files=max_repo_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_symbols=max_symbols,
            provider=provider,
            profile=profile,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    context_target = payload["context_render"]["primary_target"]
    edit_target = payload["edit_plan"]["primary_target"]
    typer.echo(f"Route test for {payload['path']}")
    typer.echo(f"query={payload['query']}")
    typer.echo(
        "context-render="
        f"{context_target.get('file')}#L{context_target.get('line')} "
        f"{context_target.get('symbol')}"
    )
    typer.echo(
        "edit-plan="
        f"{edit_target.get('file')}#L{edit_target.get('line')} "
        f"{edit_target.get('symbol')}"
    )
    typer.echo(f"agreement={payload['agreement']}")
    for warning in payload["warnings"]:
        typer.echo(f"warning={warning}")


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _format_symbol_location_row(row: dict[str, Any]) -> str:
    file_name = str(row.get("file", "")).strip()
    if not file_name:
        return ""

    line = _positive_int(row.get("line", row.get("start_line")))
    location = file_name if line is None else f"{file_name}:{line}"
    column = _positive_int(row.get("column", row.get("col", row.get("start_column"))))
    if line is not None and column is not None:
        location = f"{location}:{column}"

    details: list[str] = []
    kind = str(row.get("kind", "")).strip()
    name = str(row.get("name", "")).strip()
    text = " ".join(str(row.get("text", "")).strip().split())
    if kind:
        details.append(kind)
    if name:
        details.append(name)
    if text:
        details.append(f"| {text}")
    if not details:
        return location
    return f"{location} {' '.join(details)}"


def _echo_symbol_location_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        rendered = _format_symbol_location_row(row)
        if rendered:
            typer.echo(rendered)


def _apply_defs_class_filter(payload: dict[str, Any], class_filter: str) -> None:
    """Filter ``payload['definitions']`` in place to those whose enclosing class matches
    ``class_filter`` (case-insensitive exact match), disambiguating common method names
    such as ``search`` (audit L3-cli).

    Each definition carries a ``class`` field (enclosing class name, or ``None`` for
    module-level/free functions) populated by ``build_symbol_defs`` in repo_map.py. The
    filter and the requested value are recorded as additive top-level fields so JSON
    consumers can see that a narrowing was applied; the existing keys are left intact.
    """
    target = class_filter.strip().casefold()
    definitions = payload.get("definitions") or []
    filtered = [
        definition
        for definition in definitions
        if str(definition.get("class") or "").casefold() == target
    ]
    payload["definitions"] = filtered
    payload["class_filter"] = class_filter
    payload["class_filter_matched"] = len(filtered)


def _symbol_payload_has_no_results(payload: dict[str, Any], result_key: str) -> bool:
    """Whether a symbol-command payload found nothing for the requested symbol.

    A payload is empty either when the resolver flagged ``no_match`` or when its
    primary result collection is empty. Used to honor rg's no-match exit convention
    for the symbol commands (audit L1).
    """
    if payload.get("no_match"):
        return True
    return not payload.get(result_key)


_ZERO_CALLERS_CAVEAT = (
    "0 callers in the static call graph does not mean this symbol is dead code. Dynamic "
    "dispatch (getattr / decorators / string-keyed registries), test files, re-exports, and "
    "cross-repo callers can be invisible to the graph. Cross-check with `tg refs` or grep "
    "before treating it as unused."
)


_TRUNCATION_REMEDY = (
    "A zero or small count here is NOT trustworthy. Remedy: scope to a subdirectory, raise "
    "--max-repo-files / --max-callers / --max-files, or warm the index with "
    "`tg session daemon start`."
)


def _truncation_message(what: str) -> str:
    # ASCII-only (no em-dash): the warning prints to Windows consoles where cp1252 mojibakes it.
    return f"INCOMPLETE RESULT: {what}, so callers/definitions may be missing. {_TRUNCATION_REMEDY}"


def _scan_truncation_warning(payload: dict[str, Any]) -> str | None:
    """Human warning when a result was truncated before covering the project (P0).

    A truncated result that drops project files can return a confident-looking zero (or small
    count) that renders identically to a real one — the single most dangerous output for a
    refactor-safety tool, since it greenlights deleting live code. The payload already knows;
    this projects it into the default output so an incomplete result can never look complete.
    Handles all four shapes production emits: the repo-scan cap
    (``scan_limit.possibly_truncated`` — callers/refs/impact), the caller-scan ceiling
    (``caller_scan_limit.possibly_truncated`` — F1: a COMPLETE repo-map whose own internal
    CALLER_SCAN_FILE_CEILING still bounded how many of its files were walked for callers/refs),
    the repo-map output cap (``output_limit.possibly_truncated`` — map/context), and the
    blast-radius output cap (``output_limit.callers_truncated`` / ``files_truncated``). Returns
    None when complete.
    """
    for key in ("scan_limit", "caller_scan_limit", "output_limit"):
        limit = payload.get(key)
        if not (isinstance(limit, dict) and limit.get("possibly_truncated")):
            continue
        if key == "caller_scan_limit":
            ceiling = limit.get("ceiling", "?")
            files_total = limit.get("files_total", "?")
            return _truncation_message(
                f"caller-scan bounded to the first {ceiling} of {files_total} mapped files; "
                "narrow the PATH or raise --max-repo-files for full coverage"
            )
        scanned = limit.get("scanned_files", limit.get("emitted_files", "?"))
        cap = limit.get("max_repo_files", limit.get("max_files", "?"))
        return _truncation_message(
            f"the scan stopped at a {cap}-file cap (scanned {scanned}) and dropped project files"
        )
    output_limit = payload.get("output_limit")
    if isinstance(output_limit, dict) and (
        output_limit.get("callers_truncated") or output_limit.get("files_truncated")
    ):
        dropped: list[str] = []
        if output_limit.get("callers_truncated"):
            omitted = output_limit.get(
                "omitted_callers",
                max(
                    0,
                    int(output_limit.get("total_callers", 0))
                    - int(output_limit.get("returned_callers", 0)),
                ),
            )
            dropped.append(f"{omitted} caller(s)")
        if output_limit.get("files_truncated"):
            omitted_files = max(
                0,
                int(output_limit.get("total_files", 0))
                - int(output_limit.get("returned_files", 0)),
            )
            dropped.append(f"{omitted_files} file(s)")
        return _truncation_message(f"output was capped, omitting {' and '.join(dropped)}")
    return None


def _scan_incomplete(payload: dict[str, Any]) -> bool:
    """Whether a payload's SCAN (not output) was truncated.

    The shared exit-2 gate for the daemon/render fast-paths (``map``, ``context-render``,
    ``edit-plan``, ``blast-radius-render``, incl. their warm-daemon routes; Cluster B, 2026-07-06)
    and the ``blast-radius`` command. An OUTPUT cap (``output_limit.*`` -- ``--max-callers``,
    ``--max-files``) is a COMPLETE analysis capped only for display and must stay exit 0, so this
    checks ONLY ``scan_limit`` / ``caller_scan_limit`` ``possibly_truncated``, ``partial`` (a
    ``--deadline`` cutoff), and ``caller_scan_truncated`` (the ``CALLER_SCAN_FILE_CEILING``) --
    NEVER ``result_incomplete``, which ``_annotate_result_completeness`` also sets on an output cap
    (that would silently flip an output-cap-only invocation to exit 2 and break the
    output-cap-stays-0 pins).
    """
    for key in ("scan_limit", "caller_scan_limit"):
        limit = payload.get(key)
        if isinstance(limit, dict) and limit.get("possibly_truncated"):
            return True
    return bool(payload.get("partial") or payload.get("caller_scan_truncated"))


def _annotate_result_completeness(
    payload: dict[str, Any], *, result_key: str | None = None
) -> tuple[str | None, bool]:
    """Set additive ``result_incomplete`` + ``caveat`` on a symbol payload.

    Returns ``(caveat_text_or_None, is_truncation)``. Truncation (P0) supersedes the
    "zero callers != dead code" caveat (P7), which applies only to a resolved ``callers`` result.
    Shared by the symbol-command emitter and the blast-radius command (which has its own output).
    """
    truncation = _scan_truncation_warning(payload)
    payload["result_incomplete"] = bool(payload.get("result_incomplete")) or (
        truncation is not None
    )
    caveat = truncation
    if (
        caveat is None
        and result_key == "callers"
        and not payload.get("no_match")
        and not payload.get("callers")
    ):
        caveat = _ZERO_CALLERS_CAVEAT
    if caveat is not None:
        payload["caveat"] = caveat
    return caveat, truncation is not None


def _attach_symbol_omissions(
    payload: dict[str, Any],
    *,
    command_name: str,
    path: str,
    symbol: str,
    max_tests: int | None,
    max_tokens: int | None,
    primary_field: str,
) -> None:
    """Stamp an additive, agent-facing ``omissions`` envelope (design #96 item 3).

    Mirrors ``agent_capsule.py``'s ``omissions:{token_budget, omitted_section_count,
    omitted_sections[], follow_up_reads[]}`` shape (``agent_capsule.py:2262-2267``) as a sibling
    key on defs/refs/callers/impact, summarizing what the tests-cap (``output_limit``, set by
    ``repo_map._apply_symbol_field_output_limit``) and the token budget (``token_budget``, set by
    ``repo_map._apply_symbol_token_budget``) trimmed. Unlike the capsule's follow-up reads (which
    point at a DIFFERENT command to read more source), the follow-up pointer here is
    SELF-referential: re-run this SAME command with a bigger ``--max-tests``/``--max-tokens``,
    since there is nothing else to point at. ALWAYS present (even with nothing omitted, in which
    case ``omitted_sections``/``follow_up_reads`` are simply empty) so the shape is stable at v1.

    Purely additive/descriptive: never reads or writes ``result_incomplete``/``partial``/
    ``caller_scan_limit``, so it cannot affect the scan-truncation exit-2 contract.
    """
    omitted_sections: list[dict[str, Any]] = []
    retry_argv: list[str] = ["tg", command_name, path, symbol, "--json"]
    retry_needed = False

    output_limit = payload.get("output_limit")
    if isinstance(output_limit, dict) and output_limit.get("tests_truncated"):
        omitted_sections.append({
            "section": "tests",
            "omitted_count": int(output_limit.get("omitted_tests", 0)),
            "reason": "max-tests cap",
        })
        retry_argv.extend(["--max-tests", str(output_limit.get("total_tests", 0))])
        retry_needed = True

    token_budget = payload.get("token_budget")
    if isinstance(token_budget, dict) and token_budget.get("primary_truncated"):
        omitted_sections.append({
            "section": primary_field,
            "omitted_count": int(token_budget.get("primary_omitted", 0)),
            "reason": "max-tokens budget",
        })
        retry_argv.extend(["--max-tokens", "0"])
        retry_needed = True

    follow_up_reads: list[dict[str, Any]] = []
    if retry_needed:
        follow_up_reads.append({
            "file": None,
            "symbol": symbol,
            "role": "retry-bigger-budget",
            "command": subprocess.list2cmdline(retry_argv),
            "argv": retry_argv,
        })

    payload["omissions"] = {
        "token_budget": max_tokens,
        "max_tests": max_tests,
        "omitted_section_count": len(omitted_sections),
        "omitted_sections": omitted_sections,
        "follow_up_reads": follow_up_reads,
    }


def _emit_symbol_command_result(
    payload: dict[str, Any],
    *,
    result_key: str,
    json_output: bool,
    emit_text: Callable[[dict[str, Any]], None],
) -> None:
    """Emit a symbol-command payload and honor the no-match exit convention (L1).

    When the symbol resolved to zero results we annotate the payload with
    ``not_found: true`` (additive JSON field) and exit 1, mirroring how ``rg`` exits 1
    on no match, while still emitting a valid JSON object for ``--json`` consumers.

    Two additive completeness signals are surfaced in BOTH the JSON and the default text
    output so an incomplete answer can never look complete (validated on real repos):

    * ``result_incomplete`` (+ a loud ``caveat``) when the scan was truncated before covering
      the project — the dangerous "confident false zero" (P0).
    * for ``callers``, the "zero callers != dead code" caveat (P7) when a symbol resolved but
      has no callers on a complete scan — dynamic dispatch / tests / re-exports stay invisible.

    The truncation warning supersedes the generic caveat (incompleteness is the real story).
    """
    not_found = _symbol_payload_has_no_results(payload, result_key)
    payload["not_found"] = not_found
    caveat, is_truncation = _annotate_result_completeness(payload, result_key=result_key)
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        emit_text(payload)
        if caveat is not None:
            typer.echo(f"{'warning' if is_truncation else 'note'}: {caveat}")
    # Exit-code contract (council-verified B, 2026-07-05): a deadline/scan-truncated result is INCOMPLETE
    # and must NOT read as complete (0) nor as a genuine not-found (1). Exit 2 -- REGARDLESS of whether
    # results were found -- mirrors `tg search`'s result_incomplete convention (see the search command) so
    # an agent sees ONE contract across every command, never trusts a truncated caller-set as exhaustive
    # (a wrong blast-radius/refactor decision), and can distinguish "ran out of budget/cap, retry with
    # more" from "genuinely absent". A found-but-truncated result exiting 0 was tried (#399) and overturned
    # by a UNANIMOUS design council: truncation trumps found. The "every big-repo query exits 2" friction
    # is a DEFAULT-CAP miscalibration (512, entangled with the slow TS caller re-parse), to fix separately
    # -- NOT a reason to fork the contract in two. `--deadline` sets `partial`; a --max-repo-files cap sets
    # `result_incomplete`; either -> exit 2.
    if payload.get("partial") or payload.get("result_incomplete"):
        raise typer.Exit(2)
    if not_found:
        raise typer.Exit(1)


def _maybe_swap_reversed_positionals(
    *,
    path: str,
    value: str,
    command_name: str,
    value_label: str,
) -> tuple[str, str]:
    """Auto-correct a reversed ``<VALUE> <PATH>`` invocation.

    Agents (and grep muscle memory, and older docs) frequently call these
    commands as ``tg <command> <SYMBOL> <PATH>`` instead of the canonical
    path-first ``tg <command> <PATH> <SYMBOL>``. When that happens the first
    positional is not an existing path but the second one is, which previously
    produced an opaque ``Path not found: <SYMBOL>`` error. Detect that exact
    case and transparently swap, emitting a hint so the caller can learn the
    canonical order. The swap only fires when the first arg is definitively not
    a path AND the second arg definitively is, so a legitimate ``<PATH>
    <VALUE>`` call (where the value happens to share a name with a real path)
    is never disturbed.
    """
    if Path(path).expanduser().exists():
        return path, value
    if not Path(value).expanduser().exists():
        return path, value
    typer.echo(
        f"Warning: '{path}' is not an existing path but '{value}' is; "
        f"interpreting as `tg {command_name} <PATH> <{value_label}>` "
        f"(path={value!r}, {value_label.lower()}={path!r}). "
        f"Pass <PATH> before <{value_label}> to silence this hint.",
        err=True,
    )
    return value, path


def _maybe_swap_reversed_session_path(
    *,
    session_id: str,
    path: str,
    command_name: str,
) -> tuple[str, str]:
    """Auto-correct ``tg session <command> <PATH> <SESSION_ID> ...``.

    Session commands are the one user-facing surface where the stable session
    identifier must lead the path. Agents commonly transpose this after using
    the path-first top-level commands. Only swap when the first positional is
    an existing path and the second positional resolves to an existing session
    under that path, so ordinary session-first calls remain untouched.
    """
    if not Path(session_id).expanduser().exists():
        return session_id, path
    if Path(path).expanduser().exists():
        return session_id, path
    try:
        from tensor_grep.cli.session_store import get_session

        get_session(path, session_id)
    except Exception:
        return session_id, path
    typer.echo(
        f"Warning: '{session_id}' is an existing path and '{path}' is an existing "
        f"session for it; interpreting as `tg session {command_name} <SESSION_ID> "
        f"<PATH> <QUERY>`. Pass <SESSION_ID> before <PATH> to silence this hint.",
        err=True,
    )
    return path, session_id


def _resolve_path_and_symbol(
    *,
    path: str,
    symbol_arg: str | None,
    symbol_option: str | None,
    command_name: str,
) -> tuple[str, str]:
    if symbol_arg is not None and symbol_option is not None:
        raise ValueError("Use either positional SYMBOL or --symbol, not both.")
    if symbol_option is not None:
        typer.echo(
            "Warning: --symbol is deprecated for "
            f"tg {command_name}; pass SYMBOL as a positional instead "
            f"(shorthand `tg {command_name} <SYMBOL>` with PATH defaulting to '.', or "
            f"`tg {command_name} <PATH> <SYMBOL>` to scope a large repo). "
            "The --symbol form remains accepted for backward compatibility.",
            err=True,
        )
        return path, symbol_option
    if symbol_arg is not None:
        return _maybe_swap_reversed_positionals(
            path=path,
            value=symbol_arg,
            command_name=command_name,
            value_label="SYMBOL",
        )
    if path != "." and not Path(path).expanduser().exists():
        return ".", path
    raise ValueError("Missing symbol. Use positional SYMBOL or --symbol SYMBOL.")


def _resolve_path_and_query(
    *,
    path: str,
    query_arg: str | None,
    query_option: str | None,
    command_name: str,
) -> tuple[str, str]:
    if query_arg is not None and query_option is not None:
        raise ValueError("Use either positional QUERY or --query, not both.")
    if query_option is not None:
        typer.echo(
            "Warning: --query is deprecated for "
            f"tg {command_name}; use a positional QUERY form instead. "
            "The --query form remains accepted during the 1.13.x deprecation cycle "
            "and will not be removed before 1.14.0.",
            err=True,
        )
        return path, query_option
    if query_arg is not None:
        return _maybe_swap_reversed_positionals(
            path=path,
            value=query_arg,
            command_name=command_name,
            value_label="QUERY",
        )
    if path != "." and not Path(path).expanduser().exists():
        return ".", path
    raise ValueError("Missing query. Use positional QUERY or --query QUERY.")


@app.command()
def defs(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    class_filter: str | None = typer.Option(
        None,
        "--class",
        help=(
            "Only return definitions whose enclosing class matches TEXT "
            "(case-insensitive). Disambiguates common method names like 'search'."
        ),
    ),
    max_tests: int | None = typer.Option(
        _DEFAULT_SYMBOL_MAX_TESTS,
        "--max-tests",
        min=1,
        help="Maximum relevant test files to include in output; raise for full coverage.",
    ),
    max_tokens: int = typer.Option(
        # Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS (literal keeps the heavy repo_map import
        # lazy). Answer-first: secondary fields (tests/related_paths) are trimmed before
        # `definitions` itself. 0 = unbounded opt-out.
        16000,
        "--max-tokens",
        min=0,
        help="Approximate maximum payload size in tokens (0 = unbounded).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return exact definition locations for a symbol."""
    from tensor_grep.cli.repo_map import _apply_symbol_token_budget, build_symbol_defs

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="defs",
        )
        # task #94 Part A Tier-1: default-OFF warm-daemon fast path. Fails open to the cold
        # build_symbol_defs(...) call below on any miss/error -- see
        # _maybe_symbol_command_via_running_daemon's docstring for the full contract.
        payload = _maybe_symbol_command_via_running_daemon(
            command="defs",
            path=resolved_path,
            symbol=resolved_symbol,
            provider=provider,
            max_repo_files=max_repo_files,
            max_tests=max_tests,
        )
        if payload is None:
            payload = build_symbol_defs(
                resolved_symbol,
                resolved_path,
                semantic_provider=provider,
                max_repo_files=max_repo_files,
                max_tests=max_tests,
            )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if class_filter is not None:
        _apply_defs_class_filter(payload, class_filter)

    payload = _apply_symbol_token_budget(payload, max_tokens, primary_field="definitions")
    _attach_symbol_omissions(
        payload,
        command_name="defs",
        path=resolved_path,
        symbol=resolved_symbol,
        max_tests=max_tests,
        max_tokens=max_tokens,
        primary_field="definitions",
    )

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Definitions for {current['symbol']} in {current['path']}")
        typer.echo(f"definitions={len(current['definitions'])}")
        _echo_symbol_location_rows(current["definitions"])

    _emit_symbol_command_result(
        payload,
        result_key="definitions",
        json_output=json_output,
        emit_text=_emit_text,
    )


@app.command()
def source(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
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
    from tensor_grep.cli.repo_map import build_symbol_source

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="source",
        )
        payload = build_symbol_source(
            resolved_symbol,
            resolved_path,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Source for {current['symbol']} in {current['path']}")
        typer.echo(f"sources={len(current['sources'])} files={len(current['files'])}")

    _emit_symbol_command_result(
        payload,
        result_key="sources",
        json_output=json_output,
        emit_text=_emit_text,
    )


@app.command()
def impact(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to evaluate."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    deadline: float | None = typer.Option(
        None,
        "--deadline",
        min=0.1,
        help=(
            "Stop the underlying repo scan after N seconds and return partial:true JSON with "
            "whatever was found so far, instead of running unbounded."
        ),
    ),
    max_tests: int | None = typer.Option(
        _DEFAULT_SYMBOL_MAX_TESTS,
        "--max-tests",
        min=1,
        help="Maximum relevant test files to include in output; raise for full coverage.",
    ),
    max_tokens: int = typer.Option(
        # Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS (literal keeps the heavy repo_map import
        # lazy). Answer-first: secondary fields (tests/related_paths) are trimmed before `files`
        # itself. 0 = unbounded opt-out.
        16000,
        "--max-tokens",
        min=0,
        help="Approximate maximum payload size in tokens (0 = unbounded).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return likely impacted files and tests for a symbol change."""
    from tensor_grep.cli.repo_map import (
        _apply_symbol_token_budget,
        _copy_partial_signal,
        _deadline_monotonic_from_seconds,
        build_repo_map,
        build_symbol_callers_from_map,
        build_symbol_impact_from_map,
    )

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="impact",
        )
        # task #103: build the repo_map and convert --deadline to an absolute monotonic
        # timestamp ONCE, then share both across the impact + callers passes below -- mirrors
        # build_symbol_blast_radius's own shared-map pattern (repo_map.py's build_repo_map(...)
        # once + two `_from_map` calls against it) and the daemon/MCP server, which already
        # share one repo_map across multiple `_from_map` calls in a session. Previously each of
        # the two independent wrapper calls (build_symbol_impact + build_symbol_callers) built
        # its OWN repo_map from scratch -- parsing the whole repo twice -- AND independently
        # re-derived deadline_monotonic from a fresh time.monotonic() at its own start, so
        # --deadline silently allowed up to ~2x the requested budget for `tg impact`.
        deadline_monotonic = _deadline_monotonic_from_seconds(deadline)

        def _merge_impact_and_callers(
            impact_payload: dict[str, Any], callers_payload: dict[str, Any]
        ) -> None:
            # H5 merge (task #103): impact previously surfaced only definition/import-derived
            # `files` and so under-reported call sites relative to `tg callers`. Shared by BOTH
            # the cold and warm-daemon (task #94 Part A) arms below so they cannot silently
            # diverge into two different merge behaviors.
            impact_payload["callers"] = list(callers_payload.get("callers", []))
            # Propagate the caller-scan's --deadline partial signal (cursor review 1.40.0): impact's
            # second pass can be deadline-truncated even when the first pass wasn't, so carry partial +
            # deadline_limit onto the impact payload or _emit_symbol_command_result would exit 0 while
            # `tg callers` with the same flags exits 2.
            if callers_payload.get("partial"):
                impact_payload["partial"] = True
                caller_deadline_limit = callers_payload.get("deadline_limit")
                # Don't clobber a deadline_limit the first (impact) pass already set (cursor review LOW).
                if (
                    isinstance(caller_deadline_limit, dict)
                    and "deadline_limit" not in impact_payload
                ):
                    impact_payload["deadline_limit"] = dict(caller_deadline_limit)
            for caller in impact_payload["callers"]:
                caller_file = str(caller.get("file", ""))
                if caller_file and caller_file not in impact_payload["files"]:
                    impact_payload["files"].append(caller_file)

        # task #94 Part A Tier-1: default-OFF warm-daemon fast path, skipped entirely when a
        # --deadline was requested (mirrors defs/refs/callers/blast-radius above). impact's cold
        # path is a TWO-PASS shared-repo_map call (task #103): impact + a callers-merge. The
        # daemon session caches ONE repo_map per (path, max_repo_files) key, so issuing TWO
        # daemon requests (impact, then callers) against that same implicit session reuses the
        # same cached map -- equivalent sharing to the cold path's single build_repo_map call,
        # just over two IPC round-trips instead of two in-process calls. Both requests must
        # succeed (or the symbol must be a confirmed no_match, which never needs a callers pass)
        # or this falls through to the cold path entirely, so the merged result is never a
        # warm/cold hybrid.
        daemon_callers_payload: dict[str, Any] | None = None
        daemon_impact_payload = (
            _maybe_symbol_command_via_running_daemon(
                command="impact",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
                max_tests=max_tests,
            )
            if deadline is None
            else None
        )
        if daemon_impact_payload is not None and not daemon_impact_payload.get("no_match"):
            daemon_callers_payload = _maybe_symbol_command_via_running_daemon(
                command="callers",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
            )
            if daemon_callers_payload is None:
                daemon_impact_payload = None  # both-or-nothing -- fall through to cold below

        if daemon_impact_payload is not None:
            payload = daemon_impact_payload
            if not payload.get("no_match"):
                # Invariant: reaching here with daemon_impact_payload set and no_match falsy
                # means the "both-or-nothing" check above already confirmed the callers request
                # succeeded (any callers miss reset daemon_impact_payload to None instead).
                assert daemon_callers_payload is not None
                _merge_impact_and_callers(payload, daemon_callers_payload)
            else:
                payload.setdefault("callers", [])
        else:
            repo_map = build_repo_map(
                resolved_path,
                max_repo_files=max_repo_files,
                deadline_monotonic=deadline_monotonic,
            )
            payload = build_symbol_impact_from_map(
                repo_map,
                resolved_symbol,
                semantic_provider=provider,
                deadline_monotonic=deadline_monotonic,
                max_tests=max_tests,
            )
            _copy_partial_signal(payload, repo_map)
            # H5: impact previously surfaced only definition/import-derived `files` and so
            # under-reported call sites relative to `tg callers` (which finds the CLI
            # handler, RPC handler, and tests). Populate a top-level `callers` key from the
            # same caller pass so impact is a superset, not a subset, of callers.
            if not payload.get("no_match"):
                callers_payload = build_symbol_callers_from_map(
                    repo_map,
                    resolved_symbol,
                    semantic_provider=provider,
                    deadline_monotonic=deadline_monotonic,
                )
                _copy_partial_signal(callers_payload, repo_map)
                _merge_impact_and_callers(payload, callers_payload)
            else:
                payload.setdefault("callers", [])
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    payload = _apply_symbol_token_budget(
        payload, max_tokens, primary_field="files", companion_fields=("file_matches",)
    )
    _attach_symbol_omissions(
        payload,
        command_name="impact",
        path=resolved_path,
        symbol=resolved_symbol,
        max_tests=max_tests,
        max_tokens=max_tokens,
        primary_field="files",
    )

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Impact for {current['symbol']} in {current['path']}")
        typer.echo(
            f"files={len(current['files'])} tests={len(current['tests'])} "
            f"callers={len(current['callers'])}"
        )
        typer.echo("preferred=blast-radius for direct symbol impact")

    _emit_symbol_command_result(
        payload,
        result_key="files",
        json_output=json_output,
        emit_text=_emit_text,
    )


@app.command()
def refs(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    deadline: float | None = typer.Option(
        None,
        "--deadline",
        min=0.1,
        help=(
            "Stop the underlying repo scan after N seconds and return partial:true JSON with "
            "whatever was found so far, instead of running unbounded."
        ),
    ),
    max_tests: int | None = typer.Option(
        _DEFAULT_SYMBOL_MAX_TESTS,
        "--max-tests",
        min=1,
        help="Maximum relevant test files to include in output; raise for full coverage.",
    ),
    max_tokens: int = typer.Option(
        # Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS (literal keeps the heavy repo_map import
        # lazy). Answer-first: secondary fields (tests/related_paths) are trimmed before
        # `references` itself. 0 = unbounded opt-out.
        16000,
        "--max-tokens",
        min=0,
        help="Approximate maximum payload size in tokens (0 = unbounded).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return Python-first symbol references across the inventory root."""
    from tensor_grep.cli.repo_map import _apply_symbol_token_budget, build_symbol_refs

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="refs",
        )
        # task #94 Part A Tier-1: default-OFF warm-daemon fast path, skipped entirely when a
        # --deadline was requested (a warm session's cached repo_map cannot honor a fresh
        # per-request scan deadline) so that flag combination always takes the cold path.
        payload = (
            _maybe_symbol_command_via_running_daemon(
                command="refs",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
                max_tests=max_tests,
            )
            if deadline is None
            else None
        )
        if payload is None:
            payload = build_symbol_refs(
                resolved_symbol,
                resolved_path,
                semantic_provider=provider,
                max_repo_files=max_repo_files,
                deadline_seconds=deadline,
                max_tests=max_tests,
            )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    payload = _apply_symbol_token_budget(payload, max_tokens, primary_field="references")
    _attach_symbol_omissions(
        payload,
        command_name="refs",
        path=resolved_path,
        symbol=resolved_symbol,
        max_tests=max_tests,
        max_tokens=max_tokens,
        primary_field="references",
    )

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"References for {current['symbol']} in {current['path']}")
        typer.echo(f"references={len(current['references'])} files={len(current['files'])}")
        _echo_symbol_location_rows(current["references"])

    _emit_symbol_command_result(
        payload,
        result_key="references",
        json_output=json_output,
        emit_text=_emit_text,
    )


@app.command()
def callers(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
    provider: str = typer.Option(
        "native", "--provider", help="Semantic provider: native, lsp, or hybrid."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    deadline: float | None = typer.Option(
        None,
        "--deadline",
        min=0.1,
        help=(
            "Stop the underlying repo scan after N seconds and return partial:true JSON with "
            "whatever was found so far, instead of running unbounded."
        ),
    ),
    max_tests: int | None = typer.Option(
        _DEFAULT_SYMBOL_MAX_TESTS,
        "--max-tests",
        min=1,
        help="Maximum relevant test files to include in output; raise for full coverage.",
    ),
    max_tokens: int = typer.Option(
        # Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS (literal keeps the heavy repo_map import
        # lazy). Answer-first: secondary fields (tests/related_paths) are trimmed before
        # `callers` itself. 0 = unbounded opt-out.
        16000,
        "--max-tokens",
        min=0,
        help="Approximate maximum payload size in tokens (0 = unbounded).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return Python-first call sites and likely impacted tests for a symbol."""
    from tensor_grep.cli.repo_map import _apply_symbol_token_budget, build_symbol_callers

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="callers",
        )
        # task #94 Part A Tier-1: default-OFF warm-daemon fast path, skipped entirely when a
        # --deadline was requested (a warm session's cached repo_map cannot honor a fresh
        # per-request scan deadline) so that flag combination always takes the cold path.
        payload = (
            _maybe_symbol_command_via_running_daemon(
                command="callers",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
                max_tests=max_tests,
            )
            if deadline is None
            else None
        )
        if payload is None:
            payload = build_symbol_callers(
                resolved_symbol,
                resolved_path,
                semantic_provider=provider,
                max_repo_files=max_repo_files,
                deadline_seconds=deadline,
                max_tests=max_tests,
            )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    payload = _apply_symbol_token_budget(payload, max_tokens, primary_field="callers")
    _attach_symbol_omissions(
        payload,
        command_name="callers",
        path=resolved_path,
        symbol=resolved_symbol,
        max_tests=max_tests,
        max_tokens=max_tokens,
        primary_field="callers",
    )

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Callers for {current['symbol']} in {current['path']}")
        typer.echo(
            f"callers={len(current['callers'])} files={len(current['files'])} "
            f"import_consumers={len(current.get('import_graph_consumers', []))}"
        )
        _echo_symbol_location_rows(current["callers"])

    _emit_symbol_command_result(
        payload,
        result_key="callers",
        json_output=json_output,
        emit_text=_emit_text,
    )


@app.command()
def imports(
    file: str = typer.Argument(..., help="File to inspect for its own imports."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return what a single FILE imports, resolved to target files where possible.

    The scoped forward file-dependency primitive (#74): O(1) -- parses exactly one file, no
    repo scan, no --deadline. Use `tg importers FILE` for the reverse question (who imports
    this file). Both are far cheaper than `tg map` for a single file's dependency edges.
    """
    from tensor_grep.cli.repo_map import build_file_imports

    try:
        payload = build_file_imports(file)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Imports for {current['file']}")
        typer.echo(
            f"imports={len(current['imports'])} resolved={len(current['resolved_files'])} "
            f"external={len(current['external_modules'])} unresolved={len(current['unresolved'])}"
        )
        for entry in current["imports"]:
            if entry.get("resolved"):
                target = str(entry["resolved"])
            elif entry.get("external"):
                target = "external"
            else:
                target = "unresolved"
            # #93 SUB-1: a dynamic call (`importlib.import_module(...)` / `import(...)`) with a
            # non-literal argument has no module name to print -- label it instead of an empty
            # string, and flag every dynamic entry so a human reader can tell it apart from a
            # static import statement.
            module_label = entry["module"] or "<dynamic>"
            suffix = " [dynamic]" if entry.get("dynamic") else ""
            typer.echo(f"  {entry['line']}: {module_label} -> {target}{suffix}")

    _emit_symbol_command_result(
        payload,
        result_key="imports",
        json_output=json_output,
        emit_text=_emit_text,
    )


@app.command()
def importers(
    file: str = typer.Argument(
        ...,
        help=(
            "File to find importers of. Resolved against the current directory (like any "
            "normal path argument) whether relative or absolute -- NOT joined onto ROOT."
        ),
    ),
    root: str = typer.Argument(".", help="Root to scan for importers (the scan boundary only)."),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum repo files to scan before returning a bounded result.",
    ),
    deadline: float | None = typer.Option(
        None,
        "--deadline",
        min=0.1,
        help=(
            "Stop the underlying repo scan after N seconds and return partial:true JSON with "
            "whatever was found so far, instead of running unbounded."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return the files that import a single FILE (the reverse #74 file-dependency primitive).

    Bounded reverse lookup: prefilters candidate importers via the repo's import-alias graph,
    then re-parses and CONFIRMS each candidate against FILE before reporting it as an edge (the
    alias prefilter alone over-counts -- see `tg callers`' import-consumer precision notes).

    FILE is always resolved independently against the current directory (same rule as `tg
    imports FILE`), never joined onto ROOT -- e.g. from a parent directory,
    `tg importers myrepo/src/util.py myrepo` resolves FILE to `<cwd>/myrepo/src/util.py`, not
    `<cwd>/myrepo/myrepo/src/util.py` (dogfood #104).
    """
    from tensor_grep.cli.repo_map import build_file_importers

    try:
        payload = build_file_importers(
            file,
            root,
            max_repo_files=max_repo_files,
            deadline_seconds=deadline,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    def _emit_text(current: dict[str, Any]) -> None:
        typer.echo(f"Importers of {current['file']}")
        typer.echo(f"importers={current['importer_count']} files={len(current['importer_files'])}")
        _echo_symbol_location_rows(current["importers"])

    _emit_symbol_command_result(
        payload,
        result_key="importers",
        json_output=json_output,
        emit_text=_emit_text,
    )


def _mermaid_label(text: str) -> str:
    """Neutralize characters that would break a quoted Mermaid node/edge label."""
    return text.replace("\\", "/").replace('"', "'")


def _mermaid_relpath(file_path: str, root: str) -> str:
    """A short forward-slashed path for a Mermaid node (relative to root when it stays inside)."""
    forward = file_path.replace("\\", "/")
    try:
        rel = os.path.relpath(file_path, root).replace("\\", "/")
        if rel and not rel.startswith(".."):
            return rel
    except (ValueError, OSError):
        pass
    return os.path.basename(forward) or forward


def _render_blast_radius_mermaid(payload: dict[str, Any]) -> str:
    """Render a blast-radius payload's exact call sites (``callers[]``) as a Mermaid ``graph TD``.

    Only DIRECT callers are drawn (each unique caller file --> the symbol), because they carry
    exact file+line evidence. The depth-layered ``caller_tree`` has no exact file-to-file edges,
    so inventing them would lie to the reader (the agent-native contract). Output is deterministic
    (sorted nodes) so it is diff-friendly for doc generators.
    """
    symbol = str(payload.get("symbol", "symbol"))
    root = str(payload.get("path") or ".")
    callers = cast(list[dict[str, Any]], payload.get("callers") or [])
    grouped: dict[str, list[int]] = {}
    for caller in callers:
        raw = caller.get("file")
        if not raw:
            continue
        entry = grouped.setdefault(_mermaid_relpath(str(raw), root), [])
        line_no = caller.get("line")
        if isinstance(line_no, int):
            entry.append(line_no)
    lines = ["graph TD", f'  target["{_mermaid_label(symbol)}"]']
    for idx, rel in enumerate(sorted(grouped)):
        node = f"n{idx}"
        lines.append(f'  {node}["{_mermaid_label(rel)}"]')
        call_lines = sorted(grouped[rel])
        if len(call_lines) == 1:
            lines.append(f"  {node} -->|L{call_lines[0]}| target")
        elif call_lines:
            lines.append(f"  {node} -->|{len(call_lines)} calls| target")
        else:
            lines.append(f"  {node} --> target")
    if not grouped:
        lines.append(f"  %% no callers found for {symbol}")
    if payload.get("result_incomplete"):
        lines.append(
            "  %% note: result truncated -- raise --max-callers/--max-files for the full graph"
        )
    return "\n".join(lines)


def _daemon_blast_radius_no_match_is_unreliable(payload: dict[str, Any]) -> bool:
    """audit #107 (#94 flip blocker): True iff a warm/daemon blast_radius payload is a no_match on
    a possibly_truncated map -- the one case where the daemon-served
    build_symbol_blast_radius_from_map (repo_map.py, no literal-seed rescue) can disagree with
    what the cold build_symbol_blast_radius (repo_map.py, which DOES retry via
    _literal_symbol_seed_files) would find. The symbol may simply sit outside the daemon
    session's scan window, so a no_match here is unreliable and the caller should fall through to
    cold instead of trusting it. Mirrors the truncated-no_match condition in
    repo_map.build_symbol_blast_radius verbatim (repo_map.py:~15373-15378) so the two arms agree
    on exactly when a no_match is trustworthy.

    Deliberately narrow: only fires on no_match AND possibly_truncated together. A warm no_match
    on a COMPLETE map is a real miss -- falling back to cold there would defeat the daemon
    speedup for every genuine no-match, not just the truncated-and-wrong ones.
    """
    if not payload.get("no_match"):
        return False
    scan_limit = payload.get("scan_limit")
    return isinstance(scan_limit, dict) and bool(scan_limit.get("possibly_truncated"))


@app.command(name="blast-radius")
def blast_radius(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
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
    deadline: float | None = typer.Option(
        None,
        "--deadline",
        min=0.1,
        help=(
            "Stop the underlying repo scan after N seconds and return partial:true JSON with "
            "whatever was found so far, instead of running unbounded."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    mermaid_output: bool = typer.Option(
        False,
        "--mermaid",
        help="Render the direct-caller graph as a Mermaid `graph TD` (doc/agent-friendly).",
    ),
) -> None:
    """Return exact callers plus a transitive file/test blast radius for a symbol.

    The machine-readable caller GRAPH. Pass --json for callers, caller_tree,
    affected_files, blast_radius_score, imports, tests, and graph_trust_summary
    (~3s on a mid-size repo). Use this, not blast-radius-render, when you want the
    impact graph rather than a prose paste-in.
    """
    from tensor_grep.cli.repo_map import (
        _apply_blast_radius_output_limits,
        build_symbol_blast_radius,
    )

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="blast-radius",
        )
        # task #94 Part A Tier-1: default-OFF warm-daemon fast path, skipped entirely when a
        # --deadline was requested (a warm session's cached repo_map cannot honor a fresh
        # per-request scan deadline) so that flag combination always takes the cold path. The
        # daemon-served build_symbol_blast_radius_from_map does not itself apply the
        # --max-callers/--max-files OUTPUT caps (unlike the cold build_symbol_blast_radius
        # wrapper, which calls _apply_blast_radius_output_limits internally) -- apply the same
        # helper here so warm output matches cold output byte-for-byte.
        payload = (
            _maybe_symbol_command_via_running_daemon(
                command="blast_radius",
                path=resolved_path,
                symbol=resolved_symbol,
                provider=provider,
                max_repo_files=max_repo_files,
                max_depth=max_depth,
            )
            if deadline is None
            else None
        )
        if payload is not None and _daemon_blast_radius_no_match_is_unreliable(payload):
            # audit #107: discard the unreliable warm no_match and fall through to the cold
            # path below, which has the literal-seed rescue the daemon route lacks.
            payload = None
        if payload is not None:
            payload = _apply_blast_radius_output_limits(
                payload, max_callers=max_callers, max_files=max_files
            )
        else:
            payload = build_symbol_blast_radius(
                resolved_symbol,
                resolved_path,
                max_depth=max_depth,
                semantic_provider=provider,
                max_repo_files=max_repo_files,
                max_callers=max_callers,
                max_files=max_files,
                deadline_seconds=deadline,
            )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Honor rg's no-match exit convention (audit #12): a typo'd/nonexistent symbol previously exited
    # 0 with an empty callers list -- on a refactor-safety command that reads as "resolved, zero
    # impact" instead of "never found". Compute + stamp BEFORE any output path (mirrors
    # _emit_symbol_command_result) so json/text/mermaid all see the same additive `not_found` field.
    not_found = _symbol_payload_has_no_results(payload, "callers")
    payload["not_found"] = not_found
    # Annotate completeness BEFORE any output path so mermaid/json/text all see result_incomplete and
    # honor the shared exit contract (cursor review 1.40.0): a --deadline partial or output-cap
    # truncation must exit 2, never a silent exit 0 that reads as complete. (The mermaid renderer also
    # reads payload.result_incomplete for its `%% truncated` comment.)
    caveat, is_truncation = _annotate_result_completeness(payload, result_key="callers")
    # Exit 2 ONLY for SCAN incompleteness (--deadline partial, or a --max-repo-files scan cap) -- the
    # analysis didn't finish. An OUTPUT cap (--max-callers/--max-files) is a COMPLETE analysis with a
    # capped display (callers_truncated/files_truncated) and stays exit 0: the agent raises the cap for
    # more. So gate on scan-truncation, NOT result_incomplete (which _annotate also sets on output cap).
    # A SCAN-truncated blast radius is INCOMPLETE regardless of whether callers were found -> exit 2
    # (council-verified B, 2026-07-05; found-but-truncated->0 was tried in #399 and overturned). A
    # truncated caller-set silently trusted as exhaustive is exactly the wrong-refactor risk this gate
    # exists to prevent. `caller_scan_truncated` = the backlog-#1 caller-scan ceiling
    # (CALLER_SCAN_FILE_CEILING) dropped files the 2000-map covers -> a SCAN truncation (exit 2),
    # distinct from an output cap. Without this the ceiling would silently exit 0 with a caller-set
    # truncated at 512 (Fable final review of #405). `_scan_incomplete` is the shared gate reused by
    # every daemon/render fast-path (map, context-render, edit-plan, blast-radius-render; Cluster B,
    # 2026-07-06) so the scan-vs-output-cap contract is defined exactly once.
    incomplete = _scan_incomplete(payload)

    if mermaid_output:
        typer.echo(_render_blast_radius_mermaid(payload))
    elif json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"Blast radius for {payload['symbol']} in {payload['path']}")
        typer.echo(
            f"definitions={len(payload['definitions'])} callers={len(payload['callers'])} "
            f"files={len(payload['files'])} tests={len(payload['tests'])} "
            f"import_consumers={len(payload.get('import_graph_consumers', []))}"
        )
        if caveat is not None:
            typer.echo(f"{'warning' if is_truncation else 'note'}: {caveat}")

    # Exit-order: a SCAN truncation (2) always wins over a genuine no-match (1) -- a truncated scan
    # never had the chance to find the symbol, so "not found" is not yet a trustworthy answer.
    if incomplete:
        raise typer.Exit(2)
    if not_found:
        raise typer.Exit(1)


@app.command(name="blast-radius-render")
def blast_radius_render(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
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
    """Return a prompt-ready blast-radius bundle for a symbol.

    Emits PROSE for pasting into a prompt. For the machine-readable caller graph
    (callers/caller_tree/affected_files/blast_radius_score), use
    `tg blast-radius SYMBOL --json` instead -- it is faster and agent-consumable.
    """
    from tensor_grep.cli.repo_map import build_symbol_blast_radius_render

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="blast-radius-render",
        )
        resolved_render_profile = render_profile or ("llm" if json_output else "full")
        resolved_optimize_context = optimize_context or (json_output and render_profile is None)

        payload = build_symbol_blast_radius_render(
            resolved_symbol,
            resolved_path,
            max_depth=max_depth,
            max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            max_render_chars=max_render_chars,
            optimize_context=resolved_optimize_context,
            render_profile=resolved_render_profile,
            profile=profile,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # Cold path (Cluster B, 2026-07-06): build the payload once and dump it here (byte-identical to
    # the old build_symbol_blast_radius_render_json helper: json.dumps(payload, indent=2)) so both
    # json and text branches share the same scan-truncation gate below -- output the full payload
    # FIRST, then exit 2 if the scan itself (not just the output) was capped.
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(payload["rendered_context"])

    if _scan_incomplete(payload):
        raise typer.Exit(2)


@app.command(name="blast-radius-plan")
def blast_radius_plan(
    path: str = typer.Argument(".", help="File or directory to inventory"),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
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
    """Return a machine-readable blast-radius planning bundle without rendered source text.

    Like `blast-radius --json` but shaped as an edit/action plan (no source snippets).
    """
    from tensor_grep.cli.repo_map import build_symbol_blast_radius_plan

    try:
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="blast-radius-plan",
        )
        payload = build_symbol_blast_radius_plan(
            resolved_symbol,
            resolved_path,
            max_depth=max_depth,
            max_files=max_files,
            max_symbols=max_symbols,
            semantic_provider=provider,
            max_repo_files=max_repo_files,
        )
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    # F14 (Fable audit MED): output the payload FIRST, then gate on the shared _scan_incomplete
    # contract -- mirrors blast-radius/map/context-render/edit-plan/blast-radius-render (Cluster B,
    # 2026-07-06). This payload is built from build_symbol_blast_radius_from_map and carries the
    # exact scan_limit/caller_scan_truncated markers the gate checks; without this, a scan-truncated
    # plan exited 0 while the sibling `blast-radius` command exits 2 on identical truncation.
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"Blast radius plan for {payload['symbol']} in {payload['path']}")
        typer.echo(
            f"files={len(payload['files'])} tests={len(payload['tests'])} symbols={len(payload['symbols'])}"
        )

    if _scan_incomplete(payload):
        raise typer.Exit(2)


@session_app.command("open")
def session_open(
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    max_repo_files: int | None = typer.Option(
        512,
        "--max-repo-files",
        min=1,
        help=(
            "Maximum files scanned into the initial session repo map. "
            "Defaults to the agent-safe 512-file cap."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Create a cached repo-map session for repeated edit loops."""
    from tensor_grep.cli.session_store import open_session

    try:
        payload = open_session(path, max_repo_files=max_repo_files)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload.__dict__, version=1), indent=2))
        return

    typer.echo(
        f"Opened session {payload.session_id} "
        f"(files={payload.file_count}, symbols={payload.symbol_count})"
    )
    if isinstance(payload.scan_limit, dict) and payload.scan_limit.get("possibly_truncated"):
        typer.echo(
            "Session repo map is capped; reopen with a larger --max-repo-files for full coverage."
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
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(
        f"Session daemon running on {payload['host']}:{payload['port']} pid={payload['pid']}"
    )
    if payload.get("response_cache_scope"):
        typer.echo(f"response_cache_scope={payload['response_cache_scope']}")


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
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    if payload.get("running"):
        typer.echo(
            f"Session daemon running on {payload['host']}:{payload['port']} pid={payload['pid']}"
        )
        if payload.get("response_cache_scope"):
            typer.echo(f"response_cache_scope={payload['response_cache_scope']}")
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
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo("Session daemon stopped" if payload.get("stopped") else "Session daemon not running")


@session_app.command("list")
def session_list(
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """List cached sessions for the current root, with nearby-scope discovery."""
    from tensor_grep.cli.session_store import list_sessions_with_discovery

    try:
        session_records, scope_root, discovered = list_sessions_with_discovery(path)
        records = [record.__dict__ for record in session_records]
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "version": 1,
                    "schema_version": 1,
                    "root": scope_root,
                    "discovered": discovered,
                    "sessions": records,
                },
                indent=2,
            )
        )
        return

    if not records:
        typer.echo("No sessions found.")
        return

    if discovered:
        typer.echo(f"Discovered sessions outside current scope under {scope_root}.")

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
        session_id, path = _maybe_swap_reversed_session_path(
            session_id=session_id,
            path=path,
            command_name="show",
        )
        payload = get_session(session_id, path)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    repo_map = cast(dict[str, Any], payload.get("repo_map") or {})
    file_count = len(cast(list[Any], repo_map.get("files", [])))
    symbol_count = len(cast(list[Any], repo_map.get("symbols", [])))

    if json_output:
        # Additive parity with `session open --json` / `session list --json`, which both
        # surface top-level file_count/symbol_count (audit M8). Only fill them when absent
        # so a payload that already carries them is left untouched.
        json_payload = dict(payload)
        json_payload.setdefault("file_count", file_count)
        json_payload.setdefault("symbol_count", symbol_count)
        typer.echo(json.dumps(_with_schema_version(json_payload, version=1), indent=2))
        return

    typer.echo(f"Session {payload['session_id']} for {payload['root']}")
    typer.echo(f"files={file_count} symbols={symbol_count}")


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
        typer.echo(json.dumps(_with_schema_version(payload.__dict__, version=1), indent=2))
        return

    typer.echo(
        f"Refreshed session {payload.session_id} "
        f"(files={payload.file_count}, symbols={payload.symbol_count})"
    )


@session_app.command("context")
def session_context_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    query_arg: str | None = typer.Argument(
        None, help="Query text used to rank relevant repo context."
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
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
    max_tokens: int = typer.Option(
        # Bound the session context pack for prompt injection, matching the standalone `context`
        # command (dogfood 1.27.0: `session context --daemon` was UNBOUNDED at ~557KB / 384 files
        # while standalone capped to ~84KB — a 6x payload bump on the daemon surface agents use for
        # speed). 0 = unbounded opt-out. Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS.
        16000,
        "--max-tokens",
        min=0,
        help="Bound the context pack to ~N tokens for prompt injection (0 = unbounded).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Return a context pack derived from a cached session."""
    from tensor_grep.cli.repo_map import _apply_context_token_budget
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_context

    try:
        session_id, path = _maybe_swap_reversed_session_path(
            session_id=session_id,
            path=path,
            command_name="context",
        )
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="session context",
        )
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "context",
                    "session_id": session_id,
                    "path": resolved_path,
                    "query": resolved_query,
                    "refresh_on_stale": refresh_on_stale,
                    "max_tokens": max_tokens,
                },
            )
        else:
            payload = session_context(
                session_id,
                resolved_query,
                resolved_path,
                refresh_on_stale=refresh_on_stale,
            )
        # Bound the pack for prompt injection on BOTH the direct and daemon paths (the daemon still
        # returns the full pack today; this guarantees the agent-facing payload is capped). 0 =
        # unbounded. The budget records token_budget honestly and never orphans a symbol.
        payload = _apply_context_token_budget(payload, max_tokens)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(f"Session context for {payload['session_id']}")
    typer.echo(f"query={payload['query']}")
    typer.echo(f"files={len(payload['files'])} tests={len(payload['tests'])}")


@session_app.command("context-render")
def session_context_render_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    query_arg: str | None = typer.Argument(
        None, help="Query text used to rank and render repo context."
    ),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
    max_files: int = typer.Option(
        3, "--max-files", min=1, help="Maximum files to include in the render bundle."
    ),
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum cached repo files to score before rendering warm session context.",
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
    max_tokens: int = typer.Option(
        # Bound a prompt-ready render bundle by default, mirroring the `context` command (dogfood
        # 1.23.0: context-render defaulted to ~800KB, too big for prompt injection). 0 = unbounded;
        # downstream normalizes <=0 -> None (repo_map.py _normalize / _apply_context_token_budget).
        16000,
        "--max-tokens",
        min=0,
        help="Bound the rendered_context to ~N tokens for prompt injection (0 = unbounded).",
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
        session_id, path = _maybe_swap_reversed_session_path(
            session_id=session_id,
            path=path,
            command_name="context-render",
        )
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="session context-render",
        )
        resolved_render_profile = render_profile or ("llm" if json_output else "full")
        resolved_optimize_context = optimize_context or (json_output and render_profile is None)
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "context_render",
                    "session_id": session_id,
                    "path": resolved_path,
                    "query": resolved_query,
                    "max_files": max_files,
                    "max_repo_files": max_repo_files,
                    "max_sources": max_sources,
                    "max_symbols_per_file": max_symbols_per_file,
                    "max_render_chars": max_render_chars,
                    "max_tokens": max_tokens,
                    "model": model,
                    "optimize_context": resolved_optimize_context,
                    "render_profile": resolved_render_profile,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_context_render(
                session_id,
                resolved_query,
                resolved_path,
                max_files=max_files,
                max_repo_files=max_repo_files,
                max_sources=max_sources,
                max_symbols_per_file=max_symbols_per_file,
                max_render_chars=max_render_chars,
                max_tokens=max_tokens,
                model=model,
                optimize_context=resolved_optimize_context,
                render_profile=resolved_render_profile,
                refresh_on_stale=refresh_on_stale,
            )
    except SessionStaleError as exc:
        error_payload = {
            "version": 1,
            "schema_version": 1,
            "session_id": session_id,
            "error": {"code": "invalid_input", "message": str(exc)},
        }
        typer.echo(json.dumps(error_payload, indent=2))
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(payload["rendered_context"])


@session_app.command("edit-plan")
def session_edit_plan_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    query_arg: str | None = typer.Argument(None, help="Query text used to rank edit targets."),
    query: str | None = typer.Option(
        None,
        "--query",
        help="Deprecated: use positional QUERY.",
        hidden=True,
    ),
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum cached repo files to score before ranking warm edit-plan targets.",
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
        session_id, path = _maybe_swap_reversed_session_path(
            session_id=session_id,
            path=path,
            command_name="edit-plan",
        )
        resolved_path, resolved_query = _resolve_path_and_query(
            path=path,
            query_arg=query_arg,
            query_option=query,
            command_name="session edit-plan",
        )
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "context_edit_plan",
                    "session_id": session_id,
                    "path": resolved_path,
                    "query": resolved_query,
                    "max_files": max_files,
                    "max_sources": max_sources,
                    "max_tokens": max_tokens,
                    "max_symbols": max_symbols,
                    "max_repo_files": max_repo_files,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_context_edit_plan(
                session_id,
                resolved_query,
                resolved_path,
                max_files=max_files,
                max_sources=max_sources,
                max_tokens=max_tokens,
                max_symbols=max_symbols,
                max_repo_files=max_repo_files,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
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
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
    ),
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
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="session blast-radius",
        )
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "blast_radius",
                    "session_id": session_id,
                    "path": resolved_path,
                    "symbol": resolved_symbol,
                    "max_depth": max_depth,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_blast_radius(
                session_id,
                resolved_symbol,
                resolved_path,
                max_depth=max_depth,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(payload["rendered_caller_tree"])


@session_app.command("importers")
def session_importers_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    file: str = typer.Argument(..., help="File to find importers of."),
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
    """Return a cached-session (zero-reparse) list of the files that import FILE."""
    from tensor_grep.cli.session_daemon import request_session_daemon
    from tensor_grep.cli.session_store import session_file_importers

    try:
        if daemon:
            payload = request_session_daemon(
                ".",
                {
                    "command": "file_importers",
                    "session_id": session_id,
                    "path": ".",
                    "file": file,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_file_importers(
                session_id,
                file,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(f"Importers of {payload['file']}")
    typer.echo(f"importers={payload['importer_count']} files={len(payload['importer_files'])}")
    _echo_symbol_location_rows(payload["importers"])


@session_app.command("blast-radius-render")
def session_blast_radius_render_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
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
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="session blast-radius-render",
        )
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "blast_radius_render",
                    "session_id": session_id,
                    "path": resolved_path,
                    "symbol": resolved_symbol,
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
                resolved_symbol,
                resolved_path,
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
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
        return

    typer.echo(payload["rendered_context"])


@session_app.command("blast-radius-plan")
def session_blast_radius_plan_cmd(
    session_id: str = typer.Argument(..., help="Session ID to query."),
    path: str = typer.Argument(".", help="File or directory rooted at the session scope."),
    symbol_arg: str | None = typer.Argument(None, help="Exact symbol name to resolve."),
    symbol: str | None = typer.Option(
        None,
        "--symbol",
        help="Deprecated: use positional SYMBOL.",
        hidden=True,
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
    max_repo_files: int = typer.Option(
        _DEFAULT_AGENT_REPO_SCAN_LIMIT,
        "--max-repo-files",
        min=1,
        help="Maximum cached repo files to score before building the warm blast-radius plan.",
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
        resolved_path, resolved_symbol = _resolve_path_and_symbol(
            path=path,
            symbol_arg=symbol_arg,
            symbol_option=symbol,
            command_name="session blast-radius-plan",
        )
        if daemon:
            payload = request_session_daemon(
                resolved_path,
                {
                    "command": "blast_radius_plan",
                    "session_id": session_id,
                    "path": resolved_path,
                    "symbol": resolved_symbol,
                    "max_depth": max_depth,
                    "max_files": max_files,
                    "max_symbols": max_symbols,
                    "max_repo_files": max_repo_files,
                    "refresh_on_stale": refresh_on_stale,
                },
            )
        else:
            payload = session_blast_radius_plan(
                session_id,
                resolved_symbol,
                resolved_path,
                max_depth=max_depth,
                max_files=max_files,
                max_symbols=max_symbols,
                max_repo_files=max_repo_files,
                refresh_on_stale=refresh_on_stale,
            )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
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
        typer.echo(json.dumps(_with_schema_version(payload.__dict__, version=1), indent=2))
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
        help=(
            "Discover bounded child checkpoint scopes under PATH instead of listing one detected "
            "scope. Generated/cache roots are skipped except artifacts checkpoint scopes."
        ),
    ),
    discover_full: bool = typer.Option(
        False,
        "--discover-full",
        help=(
            "Exhaustively discover checkpoint scopes under PATH, including generated/cache roots. "
            "May be slow on broad workspaces."
        ),
    ),
) -> None:
    """List available checkpoints."""
    from tensor_grep.cli.checkpoint_store import (
        describe_checkpoint_scope,
        discover_checkpoint_scopes_result,
        discover_nearby_checkpoint_scopes,
    )

    def _scope_payloads(scopes: list[Any]) -> tuple[list[dict[str, Any]], int]:
        scope_payloads = [
            {
                "root": scope.root,
                "mode": scope.mode,
                "checkpoint_count": scope.checkpoint_count,
                "checkpoints": [record.__dict__ for record in scope.checkpoints],
            }
            for scope in scopes
        ]
        checkpoint_count = sum(
            int(cast(int, scope_payload["checkpoint_count"])) for scope_payload in scope_payloads
        )
        return scope_payloads, checkpoint_count

    def _discovered_payloads(*, full: bool = False) -> tuple[list[dict[str, Any]], int, bool]:
        result = discover_checkpoint_scopes_result(path, full=full)
        scope_payloads, checkpoint_count = _scope_payloads(result.scopes)
        return scope_payloads, checkpoint_count, result.truncated

    def _nearby_payloads() -> tuple[list[dict[str, Any]], int]:
        return _scope_payloads(discover_nearby_checkpoint_scopes(path))

    def _emit_discovered(
        scope_payloads: list[dict[str, Any]],
        checkpoint_count: int,
        *,
        auto_discovered: bool,
        truncated: bool = False,
    ) -> None:
        if json_output:
            payload = {
                "version": 1,
                "schema_version": 1,
                "path": str(Path(path).expanduser().resolve()),
                "checkpoint_count": checkpoint_count,
                "discovered_scopes": scope_payloads,
            }
            if auto_discovered:
                payload["auto_discovered"] = True
            if truncated:
                payload["truncated"] = True
                payload["warning"] = "walk truncated; use --discover-full to override"
            typer.echo(json.dumps(payload, indent=2))
            return

        if truncated:
            typer.echo("walk truncated; use --discover-full to override", err=True)
        if not scope_payloads:
            typer.echo(f"No checkpoint scopes found under {Path(path).expanduser().resolve()}.")
            return

        prefix = "Auto-discovered" if auto_discovered else "Discovered"
        typer.echo(
            f"{prefix} {checkpoint_count} checkpoint(s) across {len(scope_payloads)} scope(s)."
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

    try:
        if discover and discover_full:
            typer.echo("Use either --discover or --discover-full, not both.", err=True)
            raise typer.Exit(1)
        if discover or discover_full:
            scope_payloads, checkpoint_count, truncated = _discovered_payloads(full=discover_full)
            _emit_discovered(
                scope_payloads,
                checkpoint_count,
                auto_discovered=False,
                truncated=truncated,
            )
            return

        scope_result = describe_checkpoint_scope(path)
        records = [record.__dict__ for record in scope_result.checkpoints]
        if not records:
            scope_payloads, checkpoint_count = _nearby_payloads()
            if scope_payloads:
                _emit_discovered(scope_payloads, checkpoint_count, auto_discovered=True)
                return
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "version": 1,
                    "schema_version": 1,
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
    checkpoint_id: str | None = typer.Argument(
        None,
        help="Checkpoint ID to restore, or omit when using --last.",
    ),
    path: str = typer.Argument(".", help="File or directory rooted at the checkpoint scope."),
    last: bool = typer.Option(False, "--last", help="Restore the newest checkpoint in scope."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
) -> None:
    """Restore a checkpoint."""
    from tensor_grep.cli.checkpoint_store import resolve_latest_checkpoint, undo_checkpoint

    if path == "--json":
        json_output = True
        path = "."

    try:
        if last:
            if checkpoint_id is not None and path != ".":
                typer.echo("Use either a checkpoint id or --last, not both.", err=True)
                raise typer.Exit(1)
            latest_path = path
            if checkpoint_id is not None:
                candidate = Path(checkpoint_id).expanduser()
                if not candidate.exists() and checkpoint_id.startswith("ckpt-"):
                    typer.echo("Use either a checkpoint id or --last, not both.", err=True)
                    raise typer.Exit(1)
                latest_path = checkpoint_id
            latest = resolve_latest_checkpoint(latest_path)
            payload = undo_checkpoint(latest.checkpoint_id, latest.root)
        else:
            if checkpoint_id is None:
                typer.echo("Checkpoint id is required unless --last is provided.", err=True)
                raise typer.Exit(1)
            payload = undo_checkpoint(checkpoint_id, path)
    except Exception as exc:
        message = str(exc)
        if not last and checkpoint_id is not None:
            candidate = Path(checkpoint_id).expanduser()
            if candidate.exists():
                message = (
                    f"{message}. The first positional argument is parsed as CHECKPOINT_ID; "
                    f"to restore the newest checkpoint for this path, use "
                    f"`tg checkpoint undo --last {checkpoint_id}`."
                )
        if json_output:
            typer.echo(
                json.dumps(
                    _with_schema_version(
                        {
                            "ok": False,
                            "error": "checkpoint_not_found",
                            "detail": message,
                            "checkpoint_id": checkpoint_id,
                            "path": path,
                        },
                        version=1,
                    ),
                    indent=2,
                )
            )
            raise typer.Exit(1) from exc
        typer.echo(message, err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload.__dict__, version=1), indent=2))
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

    classify_path = Path(file_path).expanduser()
    if not classify_path.exists():
        typer.echo(
            "Error: classify expects a file path; --text/stdin literal classification "
            f"is not supported yet. Received: {file_path}",
            err=True,
        )
        raise typer.Exit(1)

    reader = FallbackReader()
    lines = list(reader.read_lines(file_path))
    if not lines:
        sys.exit(1)

    budgeted_lines, line_budget = _apply_classify_line_budget(lines, max_lines)
    results, classification_backend = _classify_lines_with_metadata(budgeted_lines)

    if format_type == "json":
        data = {
            "version": _json_output_version(),
            "schema_version": _json_output_version(),
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


@app.command(name="audit")
def audit_help() -> None:
    """Audit command entry points: audit-verify, audit-history, audit-diff, review-bundle."""
    typer.echo("Audit commands:")
    typer.echo("  tg audit-verify MANIFEST [--json]")
    typer.echo("  tg audit-history [PATH] [--json]")
    typer.echo("  tg audit-diff PREVIOUS CURRENT [--json]")
    typer.echo("  tg review-bundle create --manifest MANIFEST [--json]")
    typer.echo("  tg review-bundle verify BUNDLE [--json]")


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
    glob: list[str] | None = typer.Option(
        None,
        "--glob",
        "-g",
        help="Include/exclude files matching a glob before executing scan rules.",
    ),
    type_filter: list[str] | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Scan only files with this extension/type name. May be repeated.",
    ),
    max_depth: int | None = typer.Option(
        None,
        "--max-depth",
        min=0,
        help="Limit directory traversal depth for broad scan roots.",
    ),
    allow_broad_generated_scan: bool = typer.Option(
        False,
        "--allow-broad-generated-scan",
        help=(
            "Permit broad AST scans through temp, cache, dependency, system, or "
            "multi-project workspace roots. Prefer scoped --path or --max-depth."
        ),
    ),
) -> None:
    """Scan code with tensor-grep's bounded AST rule/config surface."""
    from tensor_grep.backends.ast_backend import normalize_ast_language
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
        try:
            # --filter was previously honored only for the sgconfig project-scan path (below) and
            # explicitly rejected for --rule -- silently no-op'd here, so a --ruleset run always
            # scanned every rule in the pack regardless of --filter (audit #22).
            rules = _filter_ast_rule_specs(rules, filter_regex)
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
        try:
            # Same uniform --filter application as --ruleset above (audit #22): previously silently
            # ignored here, so a --inline-rules run always scanned every parsed rule regardless of
            # --filter.
            rules = _filter_ast_rule_specs(rules, filter_regex)
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
            scan_globs=glob,
            scan_types=type_filter,
            scan_max_depth=max_depth,
            allow_broad_generated_scan=allow_broad_generated_scan,
            baseline_path=baseline,
            write_baseline_path=write_baseline,
            suppressions_path=suppressions,
            write_suppressions_path=write_suppressions,
            suppression_justification=justification,
            include_evidence_snippets=include_evidence_snippets,
            max_evidence_snippets_per_file=max_evidence_snippets_per_file,
            max_evidence_snippet_chars=max_evidence_snippet_chars,
        )
    except BroadScanRefusedError as exc:
        typer.echo(str(exc), err=True)
        sys.exit(2)
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
        raise ValueError(f"Invalid item name {name!r}; use a bare scaffold identifier.")


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
    name: str | None = typer.Argument(None, help="Name for project/rule/test/util scaffolds."),
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
            project_dir = base_dir
            if name is not None:
                _validate_ast_new_name(name)
                project_dir = base_dir / name
            config_path = _write_ast_project_scaffold(project_dir, lang)
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
    timeout_s: float = typer.Option(
        170.0,
        "--timeout-s",
        help="Maximum seconds for the nested agent-readiness process before partial failure output.",
    ),
    no_shell_probes: bool = typer.Option(
        False, "--no-shell-probes", help="Skip public shell version probes."
    ),
    no_wsl_probe: bool = typer.Option(False, "--no-wsl-probe", help="Skip the optional WSL probe."),
) -> None:
    """Run the agent-readiness dogfood gate; writes only explicit --output and a sibling readiness report."""
    from tensor_grep.cli.dogfood import run_dogfood_readiness
    from tensor_grep.cli.progress import normalize_progress_mode

    try:
        progress_mode = normalize_progress_mode(progress)
        if progress_interval_s <= 0:
            raise ValueError("progress interval must be greater than 0")
        if timeout_s <= 0:
            raise ValueError("dogfood timeout must be greater than 0")
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
        timeout_s=timeout_s,
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
            "lsp=external provider only, hybrid=merge both. Invalid modes "
            "fail before the server starts."
        ),
    ),
    debug_trace_language: str | None = typer.Option(
        None,
        "--debug-trace",
        help=(
            "Run a one-shot external-provider health probe for LANGUAGE and emit "
            "JSON-RPC trace diagnostics instead of starting the tg LSP server."
        ),
    ),
    path: Path = typer.Option(
        Path("."),
        "--path",
        help="Workspace root for --debug-trace probes.",
    ),
    probe_timeout_seconds: float | None = typer.Option(
        None,
        "--probe-timeout-seconds",
        help="Override the external-provider request timeout for --debug-trace.",
    ),
) -> None:
    """Start the structural search language server.

    Examples:
      tg lsp
      tg lsp --provider native
      tg lsp --provider lsp
      tg lsp --provider hybrid
      tg lsp --debug-trace python --path .

    External LSP providers are experimental semantic evidence. Provider
    availability means the binary was found, not that initialization or
    navigation requests have succeeded.

    The provider mode is also exposed to editor clients through the
    `TG_LSP_PROVIDER` environment variable.
    """
    import os

    from tensor_grep.cli.lsp_server import run_lsp

    normalized_provider = provider.strip().lower()
    if normalized_provider not in {"native", "lsp", "hybrid"}:
        typer.echo(
            "Unsupported LSP provider mode; expected one of: native, lsp, hybrid",
            err=True,
        )
        raise typer.Exit(code=2)
    if debug_trace_language is not None:
        from tensor_grep.cli.lsp_external_provider import ExternalLSPProviderManager

        payload = ExternalLSPProviderManager().provider_debug_trace(
            language=debug_trace_language,
            workspace_root=path,
            probe_timeout_seconds=probe_timeout_seconds,
        )
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        status = cast(dict[str, Any], payload.get("status", {}))
        if status.get("health_status") != "ready":
            raise typer.Exit(code=1)
        return
    os.environ["TG_LSP_PROVIDER"] = normalized_provider
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
    from tensor_grep.cli.lsp_provider_setup import (
        install_managed_lsp_providers,
        supported_lsp_languages,
    )

    payload = install_managed_lsp_providers(
        python_executable=sys.executable,
        managed_root=None,
        include_toolchain_providers=include_toolchain_providers,
    )
    has_install_errors = bool(payload.get("install_errors"))
    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
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
app.add_typer(evidence_app, name="evidence")


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
    """Repair Windows Python subprocess tg resolution.

    Removes verified or self-identifying tensor-grep Python Scripts entrypoints
    that shadow the managed native front door. Use --allow-foreign-rename only
    for a foreign tg.exe that you own and want tensor-grep to back up.
    """
    payload = _repair_windows_python_subprocess_launcher(allow_foreign_rename=allow_foreign_rename)
    if json_output:
        typer.echo(json.dumps(_with_schema_version(payload, version=1), indent=2))
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
    """Print system, GPU, cache, AST, daemon, shell-escaping, and provider-proof diagnostics.

    Reports Windows shell guidance for PowerShell literal patterns and cmd.exe metacharacters.
    """
    payload = _build_doctor_payload(path, config=config, with_lsp=with_lsp)
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(_render_doctor_payload(payload))


def _is_uv_tool_managed_python(executable: str) -> bool:
    """True when `executable` belongs to a `uv tool install`-managed tool venv (path under
    `.../uv/tools/`). Such launchers live in an isolated venv that `uv pip`/`pip install` into that
    same interpreter cannot upgrade correctly; the source-aware path is `uv tool install --force`
    (audit #2 — matches the WSL uv-tool pin that stranded tg at a stale version)."""
    return "/uv/tools/" in executable.replace("\\", "/").lower()


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
        attempts: list[tuple[str, list[str]]] = [
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
        # A uv-tool-managed launcher must be upgraded via the uv-tool front door, not `uv pip`/`pip`
        # into its isolated interpreter — try it first when detected (audit #2).
        if _is_uv_tool_managed_python(sys.executable):
            attempts.insert(0, ("uv-tool", ["uv", "tool", "install", "--force", package_spec]))
        return attempts

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
        daemon_root: str | None = None,
    ) -> Path:
        import textwrap

        native_asset_payload = json.dumps(native_assets or [])
        bridge_payload = json.dumps([str(path) for path in bridge_paths or []])
        helper_code = textwrap.dedent(
            """
            import hashlib
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
            daemon_root = sys.argv[8] if len(sys.argv) > 8 else ""
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

            def _same_path(left: Path, right: Path) -> bool:
                try:
                    return left.resolve() == right.resolve()
                except OSError:
                    return left == right

            def _python_scripts_launcher_python(candidate: Path) -> Path | None:
                if candidate.name.lower() != "tg.exe":
                    return None
                if candidate.parent.name.lower() != "scripts":
                    return None
                parts = tuple(part.lower() for part in candidate.parts)
                if ".tensor-grep" in parts or ".venv" in parts or "venv" in parts:
                    return None
                python_executable = candidate.parent.parent / "python.exe"
                if not python_executable.is_file():
                    return None
                return python_executable

            def _package_owns_launcher(
                python_executable: Path,
                launcher_path: Path,
            ) -> str:
                try:
                    result = subprocess.run(
                        [
                            str(python_executable),
                            "-m",
                            "pip",
                            "show",
                            "-f",
                            "tensor-grep",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                except Exception:
                    return ""
                if result.returncode != 0:
                    return ""
                location: Path | None = None
                version = ""
                files_started = False
                files: list[str] = []
                for raw_line in result.stdout.splitlines():
                    line = raw_line.rstrip()
                    if line.startswith("Location:"):
                        value = line.split(":", 1)[1].strip()
                        if value:
                            location = Path(value)
                    elif line.startswith("Version:"):
                        version = line.split(":", 1)[1].strip()
                    elif line.strip() == "Files:":
                        files_started = True
                    elif files_started:
                        value = line.strip()
                        if value:
                            files.append(value)
                if location is None:
                    return ""
                try:
                    resolved_launcher = launcher_path.resolve()
                except OSError:
                    resolved_launcher = launcher_path
                for relative_file in files:
                    try:
                        resolved_file = (location / relative_file).resolve()
                    except OSError:
                        resolved_file = location / relative_file
                    if _same_path(resolved_file, resolved_launcher):
                        return version or "installed"
                return ""

            def _cleanup_stale_python_launchers() -> str:
                if not expected_version or native_path is None:
                    return ""
                removed: list[str] = []
                failed: list[str] = []
                seen: set[str] = set()
                native_seen = False
                for entry in os.environ.get("PATH", "").split(os.pathsep):
                    if not entry:
                        continue
                    candidate = Path(entry.strip('"')) / "tg.exe"
                    if _same_path(candidate, native_path):
                        native_seen = True
                        continue
                    python_executable = _python_scripts_launcher_python(candidate)
                    if python_executable is None:
                        continue
                    try:
                        key = str(candidate.resolve()).lower()
                    except OSError:
                        key = str(candidate).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    version = _version(candidate)
                    if _version_matches(version) and native_seen:
                        continue
                    if version and not version.strip().lower().startswith("tensor-grep "):
                        continue
                    package_version = _package_owns_launcher(python_executable, candidate)
                    if not package_version:
                        continue
                    reason = version or "tensor-grep package " + package_version
                    try:
                        result = subprocess.run(
                            [
                                str(python_executable),
                                "-m",
                                "pip",
                                "uninstall",
                                "-y",
                                "tensor-grep",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=120,
                        )
                        if result.returncode != 0:
                            error = (result.stderr or result.stdout or "").strip()
                            raise RuntimeError(
                                "pip uninstall tensor-grep failed"
                                + (": " + error if error else "")
                            )
                        candidate.unlink(missing_ok=True)
                        if candidate.exists():
                            raise OSError("launcher still exists after cleanup")
                        removed.append("- " + str(candidate) + " (" + reason + ")")
                    except Exception as exc:
                        failed.append("- " + str(candidate) + " (" + reason + "): " + str(exc))
                sections: list[str] = []
                if removed:
                    sections.append(
                        "Removed stale tensor-grep Python package launchers from PATH:\\n"
                        + "\\n".join(removed)
                    )
                if failed:
                    sections.append(
                        "WARNING: stale tensor-grep Python package launchers remain "
                        "ahead of managed native tg.exe:\\n"
                        + "\\n".join(failed)
                    )
                return "\\n".join(sections)

            def _refresh_native_frontdoor_and_bridges() -> str:
                # refresh native front door, stale PATH copies, and stale Python launchers after locked self-upgrade
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

                                    def _cap(block_num, block_size, total_size):
                                        if block_num * block_size > 512 * 1024 * 1024:
                                            raise RuntimeError("native asset download exceeded 512MB")

                                    urllib.request.urlretrieve(url, temp_path, reporthook=_cap)
                                except Exception as exc:
                                    errors.append(f"{flavor} asset unavailable: {exc}")
                                    continue
                                sha256 = asset.get("sha256", "")
                                if not sha256:
                                    errors.append(
                                        f"{flavor} asset has no published checksum; "
                                        "refusing to install unverified binary"
                                    )
                                    continue
                                actual_sha256 = hashlib.sha256(temp_path.read_bytes()).hexdigest().lower()
                                if actual_sha256 != sha256.lower():
                                    errors.append(
                                        f"{flavor} asset checksum mismatch "
                                        f"(expected {sha256}, got {actual_sha256})"
                                    )
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
                cleanup_payload = _cleanup_stale_python_launchers()
                if cleanup_payload:
                    messages.append(cleanup_payload)
                return "\\n".join(messages)

            def _restart_session_daemon_after_upgrade() -> str:
                if not daemon_root:
                    return ""
                status_command = [
                    sys.executable,
                    "-m",
                    "tensor_grep.cli.main",
                    "session",
                    "daemon",
                    "status",
                    daemon_root,
                    "--json",
                ]
                start_command = [
                    sys.executable,
                    "-m",
                    "tensor_grep.cli.main",
                    "session",
                    "daemon",
                    "start",
                    daemon_root,
                    "--json",
                ]
                try:
                    status = subprocess.run(
                        status_command,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if status.returncode == 0:
                        try:
                            if json.loads(status.stdout).get("running") is True:
                                return ""
                        except json.JSONDecodeError:
                            pass
                    started = subprocess.run(
                        start_command,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if started.returncode == 0:
                        return "Session daemon restarted after scheduled upgrade for " + daemon_root + "."
                    error = (started.stderr or started.stdout or "").strip()
                    return (
                        "WARNING: session daemon was running before scheduled upgrade but "
                        "restart failed for "
                        + daemon_root
                        + (": " + error if error else ".")
                    )
                except Exception as exc:
                    return (
                        "WARNING: session daemon was running before scheduled upgrade but "
                        "restart failed for "
                        + daemon_root
                        + ": "
                        + str(exc)
                    )

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
                daemon_payload = _restart_session_daemon_after_upgrade()
                if daemon_payload:
                    text += "\\n" + daemon_payload
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
                daemon_root or "",
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
        daemon_snapshot = _upgrade_running_session_daemon_snapshot()
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
        daemon_restart_message = _restart_session_daemon_after_upgrade(daemon_snapshot)
        if daemon_restart_message:
            typer.echo(daemon_restart_message)

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
            native_path = _managed_native_frontdoor_path()
            path_order_message = (
                _ensure_windows_managed_native_first_on_path(native_path)
                if native_path is not None
                else None
            )
            if expected_version:
                # Audit HIGH (2026-06-28): embed the expected sha256 into each payload
                # entry on the parent side so the detached helper can verify each
                # download WITHOUT importing main.py.  Fail-closed: skip any candidate
                # whose sha256 can't be resolved; refuse to schedule if none remain.
                _native_checksums = _fetch_native_frontdoor_checksums(expected_version)
                if _native_checksums is None:
                    raise RuntimeError(
                        "release-native front-door asset refresh refused: could not fetch "
                        f"CHECKSUMS.txt for v{expected_version}; refusing to schedule "
                        "an unverified native binary refresh"
                    ) from None
                native_assets = []
                for _cand, _url in _native_frontdoor_download_candidates(expected_version):
                    _sha256 = _expected_asset_sha256(_native_checksums, _cand.asset_name)
                    if _sha256 is None:
                        continue
                    native_assets.append({
                        "url": _url,
                        "flavor": _cand.flavor,
                        "asset_name": _cand.asset_name,
                        "sha256": _sha256,
                    })
                if not native_assets:
                    raise RuntimeError(
                        "no release-native front-door asset is available for this platform"
                    ) from None
            else:
                native_assets = []
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
                daemon_root=(
                    str(daemon_snapshot.get("root"))
                    if isinstance(daemon_snapshot, dict) and daemon_snapshot.get("root")
                    else None
                ),
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
        "schema_version": _json_output_version(),
        "routing_backend": "AuditManifest",
        "routing_reason": "audit-manifest-diff",
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


def _audit_history_error_payload(message: str, *, code: str) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
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
        "schema_version": _json_output_version(),
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
            json_text = verify_audit_manifest_json(
                manifest_path,
                signing_key=signing_key,
                previous_manifest=previous_manifest,
            )
            typer.echo(json_text)
            # Mirror the text path: a tampered/invalid manifest must exit 1 even in
            # --json mode (audit H1), so callers can gate on the process status.
            if not json.loads(json_text).get("valid", False):
                raise typer.Exit(code=1)
            return

        payload = verify_audit_manifest(
            manifest_path,
            signing_key=signing_key,
            previous_manifest=previous_manifest,
        )
    except typer.Exit:
        raise
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
    signing_key: str | None = typer.Option(
        None,
        "--signing-key",
        help="Optional HMAC signing key path to verify the embedded manifest's signature.",
    ),
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
            json_text = verify_review_bundle_json(bundle_path, signing_key=signing_key)
            typer.echo(json_text)
            # Mirror the text path: a tampered/invalid bundle must exit 1 even in
            # --json mode (audit H1) so callers can gate on the process status.
            if not json.loads(json_text).get("valid", False):
                raise typer.Exit(code=1)
            return
        payload = verify_review_bundle(bundle_path, signing_key=signing_key)
    except typer.Exit:
        raise
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


def _evidence_error_payload(message: str, *, code: str, routing_reason: str) -> dict[str, object]:
    return {
        "version": _json_output_version(),
        "schema_version": _json_output_version(),
        "routing_backend": "EvidenceReceipt",
        "routing_reason": routing_reason,
        "sidecar_used": False,
        "error": {"code": code, "message": message},
    }


@evidence_app.command("emit")
def evidence_emit(
    path: str = typer.Argument(
        ".", help="Repository path to bind the receipt's revision identity to."
    ),
    query: str | None = typer.Option(None, "--query", help="Symbol/query this receipt is about."),
    manifest_path: str | None = typer.Option(
        None,
        "--manifest",
        help="Path to a prior rewrite-audit-manifest JSON (changes/validation-outcomes/rollback).",
    ),
    capsule_path: str | None = typer.Option(
        None,
        "--capsule",
        help="Path to a prior `tg agent --json` capsule (blast-radius/ambiguity/confidence).",
    ),
    checkpoint_id: str | None = typer.Option(
        None,
        "--checkpoint-id",
        help="Optional checkpoint ID for rollback info when --manifest has no checkpoint block.",
    ),
    agent_id: str | None = typer.Option(
        None,
        "--agent-id",
        help="Caller-supplied agent identifier, recorded verbatim (never inferred). "
        "Falls back to TG_EVIDENCE_AGENT_ID.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Caller-supplied model identifier, recorded verbatim (never inferred). "
        "Falls back to TG_EVIDENCE_MODEL.",
    ),
    cost_json: str | None = typer.Option(
        None,
        "--cost-json",
        help="Path to caller-supplied cost JSON, recorded verbatim (never inferred). "
        "Falls back to TG_EVIDENCE_COST_JSON.",
    ),
    recompute: bool = typer.Option(
        False,
        "--recompute",
        help="OPT-IN: recompute blast-radius for --query instead of aggregating only. "
        "OFF by default (performance contract: no re-scan unless explicitly requested).",
    ),
    output_path: str | None = typer.Option(
        None, "--out", help="Optional file path where the receipt JSON should be written."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the receipt as structured JSON to stdout."
    ),
) -> None:
    """Emit a versioned EvidenceReceipt aggregating tg's existing outputs (no re-scan)."""
    from tensor_grep.cli.evidence_receipt import build_evidence_receipt

    try:
        receipt = build_evidence_receipt(
            path,
            query=query,
            manifest_path=manifest_path,
            capsule_path=capsule_path,
            checkpoint_id=checkpoint_id,
            agent_id=agent_id,
            model=model,
            cost_json_path=cost_json,
            recompute=recompute,
        )
    except Exception as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    _evidence_error_payload(
                        str(exc), code="internal_error", routing_reason="evidence-receipt-emit"
                    ),
                    indent=2,
                )
            )
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if output_path is not None:
        resolved_output = Path(output_path).expanduser().resolve()
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        resolved_output.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    if json_output:
        typer.echo(json.dumps(receipt, indent=2))
        return

    target = output_path or "<stdout only>"
    revision = cast(dict[str, object], receipt.get("revision", {}))
    typer.echo(f"Evidence receipt ({target}):")
    typer.echo(f"  commit_sha={revision.get('commit_sha', '<unavailable>')}")
    typer.echo(f"  dirty={revision.get('dirty', '<unavailable>')}")
    for block_name in ("scope", "blast_radius", "confidence", "validation", "changes", "caller"):
        block = receipt.get(block_name)
        status = block.get("status", "unknown") if isinstance(block, dict) else "unknown"
        typer.echo(f"  {block_name}.status={status}")


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
    help=(
        "Run a validated AST slice for structural search and guarded rewrites. "
        "PowerShell users should single-quote AST patterns containing $ captures, "
        "for example 'def $NAME($$$ARGS): $$$BODY'."
    ),
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
        help="ast-grep matcher selector for read-only structural search.",
    ),
    strictness: str | None = typer.Option(
        None,
        "--strictness",
        help="ast-grep strictness control for read-only structural search.",
    ),
    stdin: bool = typer.Option(
        False,
        "--stdin",
        help="Read source code from stdin for read-only structural search.",
    ),
    globs: list[str] | None = typer.Option(
        None,
        "--globs",
        help="ast-grep include/exclude glob. May be repeated; prefix with ! to exclude.",
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
        if (
            (selector is not None or strictness is not None or stdin or globs)
            and len(positional_args) == 1
            and Path(positional_args[0]).exists()
        ):
            typer.echo(
                "Error: tg run ast-grep semantic options require --pattern <PATTERN> "
                "before PATH; positional arguments without --pattern are treated as PATTERN.",
                err=True,
            )
            raise typer.Exit(code=2)
        # L9: a lone positional that resolves to an existing file/dir is almost certainly
        # a PATH supplied without a PATTERN. Previously it was swallowed as the AST
        # pattern, yielding a silent zero-match exit 1. Fail loudly with a clear message
        # instead so the missing pattern is obvious.
        if len(positional_args) == 1 and Path(positional_args[0]).exists():
            typer.echo(
                "Error: tg run requires a PATTERN. Received only a PATH "
                f"({positional_args[0]!r}); pass the AST pattern before the path "
                "(tg run <PATTERN> <PATH>) or use --pattern <PATTERN>.",
                err=True,
            )
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
        selector=selector,
        strictness=strictness,
        stdin=stdin,
        globs=globs,
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
