from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import typer

from tensor_grep.cli import _index_lock as _index_lock
from tensor_grep.cli import doctor_report as _doctor_report
from tensor_grep.cli import managed_runtime_upgrade as _managed_runtime_upgrade
from tensor_grep.cli import native_frontdoor as _native_frontdoor
from tensor_grep.cli._main_binding import _self
from tensor_grep.cli.investigation_options import (
    _note_late_rerank_degraded as _note_late_rerank_degraded,
)
from tensor_grep.cli.investigation_options import (
    _set_semantic_rank_fallback_reason as _set_semantic_rank_fallback_reason,
)
from tensor_grep.cli.prepare_service import _build_prepare_payload as _build_prepare_payload
from tensor_grep.cli.runtime_paths import _native_tg_version as _native_tg_version
from tensor_grep.cli.runtime_paths import _native_tg_version_matches as _native_tg_version_matches
from tensor_grep.cli.runtime_paths import inspect_native_tg_binary as inspect_native_tg_binary
from tensor_grep.cli.runtime_paths import (
    is_cross_domain_native_binary as is_cross_domain_native_binary,
)

# Re-exported, not used here: a front-door checksum test reads `main.native_frontdoor_metadata_path`.
from tensor_grep.cli.runtime_paths import (
    native_frontdoor_metadata_path as native_frontdoor_metadata_path,
)
from tensor_grep.cli.runtime_paths import resolve_native_tg_binary as resolve_native_tg_binary
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary as resolve_ripgrep_binary
from tensor_grep.cli.runtime_paths import (
    translate_path_for_windows_binary as translate_path_for_windows_binary,
)
from tensor_grep.io.scan_limits import (
    BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD,
    BROAD_WORKSPACE_PROJECT_MARKERS,
)

# perf (+10% campaign #6 / F2.4): import the 5 broad-scan-guard constants from the
# zero-dependency `tensor_grep.io.scan_limits` module directly, NOT from
# `tensor_grep.io.directory_scanner` (which does `from tensor_grep.core.config import
# SearchConfig` at its own module level -- SearchConfig transitively pulls in the stdlib
# `dataclasses`/`inspect` chain). This module-level import runs for every full-CLI command
# (--help, scan, test, ast-info, ...); these 5 names are plain frozensets/ints consumed only by
# the module-level broad-scan literals below, never SearchConfig itself, so there is no reason
# to pay for the heavier module merely to read 5 constants from it. `DirectoryScanner` itself is
# still imported lazily, function-local, at each call site that actually walks a tree (unchanged
# by this PR). See `tensor_grep.io.scan_limits`'s module docstring for the full rationale.

# Read this module object through late-bound
# attribute reads. A BARE call to a monkeypatched name resolves through THIS module's
# globals, welding the caller to this file -- move it and the test still passes while
# production runs the unpatched original. `_self.NAME(...)` resolves at CALL time.
# The two branches are load-bearing. At runtime only `sys.modules[__name__]` works (the
# module is mid-import, so importing itself by name would be circular), but that is typed
# `ModuleType`, whose `__getattr__` returns `Any` -- under a single-branch form every
# converted call returns Any and mypy raises `no-any-return` at each concrete-returning
# caller. The TYPE_CHECKING branch never executes; it exists so the checker resolves
# `_self` to this module and keeps every real signature.
# scripts/bare_call_ratchet.py pins the remaining bare-call count and fails if it grows.
# Re-exports from the modules split out of this file on 2026-08-20 (see
# cli/_main_binding.py). EVERY moved name is
# rebound here, not only the ones a scan can prove are read. The test suite patches many of
# them on `main` and the moved code reads them back through `_self`, so the patch target must
# not move with the code -- and tests reach them through local aliases, so 'nothing references
# it' would be a claim about the scan, not about the code. Rebinding all of them keeps
# `main.<name>` exactly the surface it was before the split. Plain assignment rather than
# `from ... import x as x` only because isort expands the aliased form to three lines each.

# --- from cli/ast_scan.py ----------------------------------------------------

# --- from cli/doctor_report.py -----------------------------------------------
_restart_session_daemon_after_upgrade = _doctor_report._restart_session_daemon_after_upgrade

# --- from cli/doctor_payload.py ----------------------------------------------

# --- from cli/native_frontdoor.py --------------------------------------------
_is_version_newer = _native_frontdoor._is_version_newer
_verify_target_python_tensor_grep_version = (
    _native_frontdoor._verify_target_python_tensor_grep_version
)

# --- from cli/windows_launcher.py --------------------------------------------

# backlog #1 : kept numerically in sync with
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
_DOCTOR_SCHEMA_VERSION = 3
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
# Single source of truth: `io/directory_scanner.py` (item #154) -- keeps this file and
# `cli/bootstrap.py`'s front-door mirror from drifting out of sync.
_BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD = BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD
_BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD = BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD
_BROAD_WORKSPACE_PROJECT_MARKERS = BROAD_WORKSPACE_PROJECT_MARKERS
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


def _is_uv_tool_managed_python(executable: str) -> bool:
    return "/uv/tools/" in executable.replace("\\", "/").lower()


def _upgrade_attempts(package_spec: str) -> list[tuple[str, list[str]]]:
    from tensor_grep.cli import runtime_paths

    uv = runtime_paths.resolve_executable_path("uv")
    uv_prefix = [uv, "--no-config"] if uv else []
    pip_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-cache-dir",
        package_spec,
    ]
    attempts: list[tuple[str, list[str]]] = [("pip", pip_cmd)]
    if uv:
        attempts.insert(
            0,
            (
                "uv",
                [
                    *uv_prefix,
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
        )
    if uv and _is_uv_tool_managed_python(sys.executable):
        attempts.insert(0, ("uv-tool", [*uv_prefix, "tool", "install", "--force", package_spec]))
    return attempts


def _run_upgrade(
    attempts: list[tuple[str, list[str]]],
) -> tuple[subprocess.CompletedProcess[str], str]:
    return _managed_runtime_upgrade.run_upgrade(attempts)


def upgrade() -> None:
    """Upgrade tensor-grep to the latest version published on PyPI."""
    import importlib.metadata

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

            def _decode_utf8(raw, errors="strict"): return raw.decode("utf-8", errors=errors) if isinstance(raw, bytes) else raw or ""

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
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=True,
                    )
                except subprocess.CalledProcessError:
                    break
                time.sleep(0.1)

            def _run_attempts() -> tuple[bool, str, str]:
                errors: list[str] = []
                for label, cmd in attempts:
                    try:
                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
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
                                    encoding="utf-8",
                                    errors="replace",
                                    check=True,
                                )
                                result = subprocess.run(
                                    cmd,
                                    capture_output=True,
                                    text=True,
                                    encoding="utf-8",
                                    errors="replace",
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
                        text=False,
                        check=True,
                    )
                except FileNotFoundError as exc:
                    return False, f"post-upgrade verification failed: {exc}"
                except subprocess.CalledProcessError as exc:
                    stderr = _decode_utf8(exc.stderr, "replace").strip()
                    stdout = _decode_utf8(exc.stdout, "replace").strip()
                    combined = stderr or stdout or str(exc)
                    return False, f"post-upgrade verification failed: {combined}"
                try:
                    stdout = _decode_utf8(result.stdout)
                except UnicodeDecodeError:
                    return False, "post-upgrade verification returned invalid UTF-8 version output"
                version = stdout.strip().splitlines()
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
                result = subprocess.run(
                    [str(path), "--version"],
                    capture_output=True,
                    text=False,
                )
                if result.returncode != 0:
                    return ""
                try:
                    stdout = _decode_utf8(result.stdout)
                except UnicodeDecodeError:
                    return ""
                for line in stdout.splitlines():
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
                    text=False,
                    timeout=10,
                    )
                except Exception:
                    return ""
                if result.returncode != 0:
                    return ""
                try:
                    output = _decode_utf8(result.stdout)
                except UnicodeDecodeError:
                    return ""
                location: Path | None = None
                version = ""
                files_started = False
                files: list[str] = []
                for raw_line in output.splitlines():
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
                            encoding="utf-8",
                            errors="replace",
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

                                    # O_EXCL claims the temp name as a regular file first; urlretrieve's 'wb'
                                    # FOLLOWS a symlink and the payload is an executable. Full rationale in
                                    # _download_native_frontdoor_asset (found by the #859 ratchet).
                                    _fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                                    os.close(_fd)
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
                # End-of-options sentinel (CWE-88), INLINE and duplicated, deliberately.
                #
                # This block is inside `helper_code = textwrap.dedent(...)` -- a standalone script
                # written to disk and run by the Windows scheduler. It cannot import tg, so a
                # shared `_session_daemon_argv` helper would NameError at runtime. A first cut
                # extracted one and was caught by
                # `test_upgrade_scheduled_windows_helper_restarts_preexisting_session_daemon`,
                # which pins the generated script's TEXT. Refactoring code that lives inside a
                # generated string is not the same as refactoring code.
                #
                # `daemon_root` is caller-influenced: it is the PATH the user gave
                # `tg session daemon start <PATH>`, persisted in daemon state and round-tripped
                # back here. The callee is a real Typer/Click command whose `path` argument
                # DEFAULTS TO "." -- so a dash-leading root consumed as an option does NOT error;
                # the daemon is started or queried at the CWD instead. Silent wrong scope.
                #
                # `--json` sits BEFORE the sentinel: everything after `--` is a positional, so
                # leaving the flag there would hand Click a second positional instead.
                status_command = [
                    sys.executable,
                    "-m",
                    "tensor_grep.cli.main",
                    "session",
                    "daemon",
                    "status",
                    "--json",
                    "--",
                    daemon_root,
                ]
                start_command = [
                    sys.executable,
                    "-m",
                    "tensor_grep.cli.main",
                    "session",
                    "daemon",
                    "start",
                    "--json",
                    "--",
                    daemon_root,
                ]
                try:
                    status = subprocess.run(
                        status_command,
                        capture_output=True,
                        text=False,
                        timeout=10,
                    )
                    if status.returncode == 0:
                        try:
                            if json.loads(_decode_utf8(status.stdout)).get("running") is True:
                                return ""
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                    started = subprocess.run(
                        start_command,
                        capture_output=True,
                        text=False,
                        timeout=30,
                    )
                    if started.returncode == 0:
                        return "Session daemon restarted after scheduled upgrade for " + daemon_root + "."
                    error = _decode_utf8(started.stderr or started.stdout, "replace").strip()
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
            cwd=str(Path(sys.executable).parent),
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
        daemon_snapshot = _self._upgrade_running_session_daemon_snapshot()
        previous_version = _installed_version()
        latest_version = _self._latest_pypi_tensor_grep_version()
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
        if _managed_runtime_upgrade.handle_managed_windows_upgrade(
            sys.executable, previous_version, latest_version, package_spec, daemon_snapshot
        ):
            return
        result, method = _self._run_upgrade(attempts)
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
        native_refresh_message = _self._refresh_managed_native_frontdoor(current_version)
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
        if isinstance(e, _managed_runtime_upgrade.ManagedWindowsUpgradeError):
            _managed_runtime_upgrade.exit_managed_upgrade_error(e)
        if _looks_like_windows_self_update_lock(str(e)):
            previous_version = _installed_version()
            latest_version = _self._latest_pypi_tensor_grep_version()
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
            native_path = _self._managed_native_frontdoor_path()
            path_order_message = (
                _self._ensure_windows_managed_native_first_on_path(native_path)
                if native_path is not None
                else None
            )
            if expected_version:
                # Audit HIGH (2026-06-28): embed the expected sha256 into each payload
                # entry on the parent side so the detached helper can verify each
                # download WITHOUT importing main.py.  Fail-closed: skip any candidate
                # whose sha256 can't be resolved; refuse to schedule if none remain.
                _native_checksums = _self._fetch_native_frontdoor_checksums(expected_version)
                if _native_checksums is None:
                    raise RuntimeError(
                        "release-native front-door asset refresh refused: could not fetch "
                        f"CHECKSUMS.txt for v{expected_version}; refusing to schedule "
                        "an unverified native binary refresh"
                    ) from None
                native_assets = []
                for _cand, _url in _self._native_frontdoor_download_candidates(expected_version):
                    _sha256 = _self._expected_asset_sha256(_native_checksums, _cand.asset_name)
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
                _self._windows_stale_tensor_grep_com_bridges(expected_version, native_path)
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


def register(app: typer.Typer) -> None:
    app.command()(upgrade)
