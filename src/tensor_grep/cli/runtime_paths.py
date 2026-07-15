import json
import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

NATIVE_FRONTDOOR_METADATA_FILENAME = "tg-native-metadata.json"


def env_flag_enabled(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def env_flag_disabled(name: str) -> bool:
    """Mirror of ``env_flag_enabled`` for a default-ON, opt-out env flag.

    ``env_flag_enabled`` cannot express "on unless explicitly turned off" -- it treats unset as
    falsy. This answers the opt-out question directly: true only when the variable is explicitly
    set to a recognized falsy token (``0``/``false``/``no``/``off``, case-insensitive, trimmed).
    An unset variable -- or any other value -- is NOT "disabled"; the caller applies its own
    default-on behavior via ``not env_flag_disabled(...)``.
    """
    value = os.environ.get(name, "").strip().lower()
    return value in {"0", "false", "no", "off"}


def _current_python_bin_dirs() -> set[Path]:
    executable = Path(sys.executable)
    bin_dirs = {executable.parent}
    try:
        bin_dirs.add(executable.resolve().parent)
    except OSError:
        pass
    return bin_dirs


def _looks_like_python_scripts_launcher(candidate: Path) -> bool:
    """Reject console-entrypoint shims from Python installation script dirs.

    The current environment check handles local venv shims. The Windows
    installation layout additionally exposes console launchers under
    ``...\\PythonXY\\Scripts\\``; those also recurse back into the Python CLI and
    are never native `tg` binaries.
    """
    candidate_bin_dirs = {candidate.parent}
    try:
        candidate_bin_dirs.add(candidate.resolve().parent)
    except OSError:
        pass
    if not _current_python_bin_dirs().isdisjoint(candidate_bin_dirs):
        return True

    if sys.platform.startswith("win") and candidate.parent.name.lower() == "scripts":
        scripts_dir = candidate.parent
        python_root = scripts_dir.parent
        return (
            (scripts_dir / "python.exe").is_file()
            or (scripts_dir / "pythonw.exe").is_file()
            or (python_root / "python.exe").is_file()
            or (python_root / "pythonw.exe").is_file()
            or (python_root / "pyvenv.cfg").is_file()
        )

    return False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_project_version_fallback() -> str:
    try:
        pyproject_path = _repo_root() / "pyproject.toml"
        for line in pyproject_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version = "):
                return stripped.split('"', 2)[1]
    except Exception:
        pass
    return "0.0.0"


def _expected_tg_version() -> str:
    source_version = _read_project_version_fallback()
    try:
        from importlib.metadata import version

        installed_version = version("tensor-grep")
    except Exception:
        return source_version
    if source_version != "0.0.0" and source_version != installed_version:
        return source_version
    return installed_version


def _native_tg_version(candidate: Path) -> str | None:
    try:
        result = subprocess.run(
            [str(candidate), "--version"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _native_tg_version_matches(expected_version: str, version_text: str | None) -> bool:
    if version_text is None:
        return False
    return bool(re.search(rf"\b{re.escape(expected_version)}\b", version_text))


def native_frontdoor_metadata_path(native_binary: Path) -> Path:
    return native_binary.with_name(NATIVE_FRONTDOOR_METADATA_FILENAME)


def _read_native_frontdoor_metadata(native_binary: Path) -> dict[str, str]:
    metadata_path = native_frontdoor_metadata_path(native_binary)
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {"native_frontdoor_metadata_status": "invalid"}
    if not isinstance(raw, dict):
        return {"native_frontdoor_metadata_status": "invalid"}
    metadata = {
        "native_frontdoor_metadata_status": "present",
    }
    field_map = {
        "asset_flavor": "native_frontdoor_flavor",
        "requested_asset_flavor": "native_frontdoor_requested_flavor",
        "asset_name": "native_frontdoor_asset_name",
        "version": "native_frontdoor_metadata_version",
    }
    for source_key, target_key in field_map.items():
        value = raw.get(source_key)
        if isinstance(value, str) and value:
            metadata[target_key] = value
    return metadata


def _in_tree_native_tg_candidates(*, repo_root: Path, binary_name: str) -> list[Path]:
    candidates = [
        repo_root / "rust_core" / "target" / "release" / binary_name,
        repo_root / "rust_core" / "target" / "debug" / binary_name,
    ]
    existing = [candidate.resolve() for candidate in candidates if candidate.is_file()]
    return sorted(existing, key=lambda candidate: candidate.stat().st_mtime_ns, reverse=True)


def iter_in_tree_native_tg_binaries() -> list[Path]:
    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    return _in_tree_native_tg_candidates(repo_root=_repo_root(), binary_name=binary_name)


def _native_candidate_matches_current_package(candidate: Path, *, expected_version: str) -> bool:
    return _native_tg_version_matches(expected_version, _native_tg_version(candidate))


def _path_binary_candidates(binary_name: str) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for raw_entry in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_entry:
            continue
        candidate = Path(raw_entry).expanduser() / binary_name
        if not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        if resolved in seen:
            continue
        seen.add(resolved)
        candidates.append(resolved)
    return candidates


def inspect_native_tg_binary(
    candidate: Path,
    *,
    repo_root: Path | None = None,
    expected_version: str | None = None,
    version_text: str | None = None,
) -> dict[str, str | None]:
    """Return non-destructive native tg version metadata for diagnostics.

    `version_text`, when provided, is trusted as-is and skips the internal `_native_tg_version`
    subprocess spawn -- GPU Phase-0 gate-nit #172 NIT-1: the doctor path already spawns its own
    `tg --version` via `_doctor_rust_binary_version` before calling this function, so spawning a
    SECOND `--version` subprocess for the identical binary here was pure duplication. Do not
    `@lru_cache` `_native_tg_version` itself to "fix" this a different way -- it is also called by
    installer verification, which re-reads the SAME path across a candidate loop after
    `os.replace`; a path-keyed cache would return a stale pre-replace version there.
    """
    root = repo_root or _repo_root()
    expected = expected_version or _expected_tg_version()
    try:
        resolved = candidate.expanduser().resolve()
    except OSError:
        resolved = candidate.expanduser().absolute()

    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    release_binary = (root / "rust_core" / "target" / "release" / binary_name).resolve()
    debug_binary = (root / "rust_core" / "target" / "debug" / binary_name).resolve()
    resolved_parts = {part.lower() for part in resolved.parts}
    if resolved == release_binary:
        kind = "in-tree-release"
    elif resolved == debug_binary:
        kind = "in-tree-debug"
    elif ".tensor-grep" in resolved_parts and "bin" in resolved_parts:
        kind = "managed-native"
    else:
        kind = "external"

    if version_text is None and resolved.is_file():
        version_text = _native_tg_version(resolved)
    if not resolved.is_file():
        version_status = "missing"
    elif _native_tg_version_matches(expected, version_text):
        version_status = "matches"
    elif version_text:
        version_status = "stale"
    else:
        version_status = "unknown"

    metadata = _read_native_frontdoor_metadata(resolved)
    payload = {
        "path": str(resolved),
        "kind": kind,
        "version": version_text,
        "expected_version": expected,
        "version_status": version_status,
    }
    payload.update(metadata)
    return payload


@lru_cache(maxsize=1)
def resolve_native_tg_binary() -> Path | None:
    repo_root = _repo_root()
    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"

    if env_flag_enabled("TG_DISABLE_NATIVE_TG"):
        return None

    # Priority 1: Exact explicit override
    env_override = os.environ.get("TG_NATIVE_TG_BINARY") or os.environ.get("TG_MCP_TG_BINARY")
    if env_override:
        p = Path(env_override).expanduser()
        if p.is_file():
            return p.resolve()
        raise FileNotFoundError(f"Configured binary {p} not found.")

    expected_version = _expected_tg_version()

    # Priority 2: compatible in-tree build. Stale in-tree binaries are ignored
    # unless pinned explicitly with TG_NATIVE_TG_BINARY/TG_MCP_TG_BINARY.
    for candidate in _in_tree_native_tg_candidates(repo_root=repo_root, binary_name=binary_name):
        if _native_candidate_matches_current_package(candidate, expected_version=expected_version):
            return candidate

    # Priority 3: PATH installations. Scan every candidate so a Python
    # console-entrypoint shim in an active repo venv cannot hide a later
    # managed native front door.
    for resolved in _path_binary_candidates(binary_name):
        if not _looks_like_python_scripts_launcher(
            resolved
        ) and _native_candidate_matches_current_package(
            resolved, expected_version=expected_version
        ):
            return resolved

    tensor_grep_binary_name = "tensor-grep" + (".exe" if sys.platform.startswith("win") else "")
    for resolved in _path_binary_candidates(tensor_grep_binary_name):
        if not _looks_like_python_scripts_launcher(
            resolved
        ) and _native_candidate_matches_current_package(
            resolved, expected_version=expected_version
        ):
            return resolved

    return None


# --- GPU-P0-1 (#171): WSL native-binary path-domain bridging ----------------------------------
#
# On WSL, resolve_native_tg_binary() can return a Windows-target binary: an explicit
# TG_NATIVE_TG_BINARY override pointing at a `.exe`, or an in-tree `tg.exe` produced by a
# Windows-side `cargo build` against a repo checkout shared over a `/mnt/<drive>/...` mount. In
# EVERY such case the binary carries the `.exe` suffix -- a Windows PE is not executable via the
# Win32 loader or the WSL binfmt interop handler without it. The GPU doctor/agent probes write a
# sentinel file under a Linux TemporaryDirectory (a `/tmp/...`-style path) and pass it as argv to
# that binary. A Windows PE cannot resolve a `/tmp/...` path -- a different filesystem namespace --
# so the probe fails with a structured `path_not_found` from the native binary, which reads as "no
# GPU support" even when the GPU route itself may be fine. These helpers detect that mismatch and
# bridge it via `wslpath`, so the doctor probe (cli/main.py) and the agent probe
# (cli/agent_capsule.py) share ONE implementation instead of two divergent copies.
#
# The detection keys on the `.exe` suffix ALONE, deliberately NOT on a `/mnt/<drive>/` location:
# a WSL user who checks the repo out on a Windows drive and runs the LINUX `maturin develop` /
# `cargo build` there gets a genuine Linux ELF at `/mnt/c/.../rust_core/target/release/tg` (no
# `.exe`), which the default resolver returns (it looks for `tg`, not `tg.exe`, on Linux). That
# ELF is same-domain -- it opens the `/tmp` sentinel fine -- so translating its path would BREAK a
# working config. The `.exe` suffix is both necessary and sufficient for a real Windows target.

#: Base GPU probe timeout (seconds) when host and resolved binary share a filesystem domain.
DEFAULT_GPU_PROBE_TIMEOUT_S = 2.0

#: Floor applied when the resolved native binary is cross-domain (WSL host, Windows binary) -- a
#: WSL -> Windows exec crosses an interop boundary that can legitimately exceed the same-domain
#: default.
CROSS_DOMAIN_GPU_PROBE_TIMEOUT_S = 6.0

_WSLPATH_TRANSLATE_TIMEOUT_S = 2.0


def is_wsl_host() -> bool:
    """True when this process is running inside a WSL (1 or 2) Linux environment.

    This is a NARROWER check than "any Linux box" -- it only adds the "genuinely WSL" signal on
    top of the `sys.platform.startswith("linux")` gate already applied by callers, so a
    `.exe`-suffixed path used by an unrelated fixture on a bare Linux CI runner (no WSL) is never
    misread as a real WSL path-domain mismatch. `WSL_DISTRO_NAME`/`WSL_INTEROP` are set by WSL
    itself in every real WSL session and are the standard, filesystem-read-free way to detect it;
    `/run/WSL` (also used by `core.hardware.device_detect`) is the fallback for a stripped
    subprocess environment that dropped those variables.
    """
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    return os.path.exists("/run/WSL")


def native_binary_targets_windows(binary: Path | str) -> bool:
    """True when `binary` is a Windows-target executable, keyed on the `.exe` suffix ALONE.

    The `.exe` suffix is both necessary and sufficient: a Windows PE cannot be exec'd via the
    Win32 loader or the WSL binfmt interop handler without it, and nothing native to a Linux/macOS
    filesystem carries it. A `/mnt/<drive>/` location is deliberately NOT treated as a Windows
    signal -- a Linux ELF built in-place on a Windows-drive checkout (the default resolver returns
    `/mnt/c/.../tg`, no `.exe`) lives there too and is same-domain, so flagging it would break a
    working WSL config (Opus MF-1).
    """
    return str(binary).lower().endswith(".exe")


def is_cross_domain_native_binary(binary: Path | str | None) -> bool:
    """True when the host is Linux/WSL but the resolved native `tg` binary targets Windows.

    Requires ALL THREE: a Linux host (`sys.platform`), a genuine WSL signal (`is_wsl_host()`),
    and a Windows-shaped binary path (`native_binary_targets_windows()`). Dropping the WSL-signal
    check would false-positive on any bare Linux CI runner whose test fixtures happen to use a
    `.exe`-suffixed name for unrelated reasons; dropping the platform check would be redundant but
    harmless. The downstream `wslpath` lookup also fails closed (returns None) when unavailable,
    so even a false positive here degrades to the honest `path_domain_mismatch` status rather than
    a silently wrong argv.
    """
    if binary is None:
        return False
    if not sys.platform.startswith("linux"):
        return False
    if not is_wsl_host():
        return False
    return native_binary_targets_windows(binary)


def translate_path_for_windows_binary(
    path: Path | str, *, timeout_s: float = _WSLPATH_TRANSLATE_TIMEOUT_S
) -> str | None:
    """Translate a Linux-side path to a form a Windows binary can open, via `wslpath -w`.

    Works both for a path under a drive mount (translates to the Windows drive-letter form) and a
    path purely inside the WSL VM filesystem such as `/tmp/...` (translates to a UNC path served
    over the WSL network redirector, which Windows can read while the distro is running). Returns
    None -- never raises -- when `wslpath` is not on PATH, times out, or exits non-zero, so the
    caller can report a distinct `path_domain_mismatch` status instead of silently handing the
    Windows binary a Linux path it cannot open.
    """
    wslpath_bin = shutil.which("wslpath")
    if wslpath_bin is None:
        return None
    try:
        result = subprocess.run(
            [wslpath_bin, "-w", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    translated = result.stdout.strip()
    return translated or None


def gpu_probe_timeout_s(
    *, cross_domain: bool = False, default_s: float = DEFAULT_GPU_PROBE_TIMEOUT_S
) -> float:
    """Resolve the GPU probe subprocess timeout (seconds).

    `TENSOR_GREP_GPU_PROBE_TIMEOUT_S` always wins when set to a valid positive float -- one
    operator-level knob shared by the doctor probe and the agent probe (no divergent copy).
    Absent an override, `default_s` applies unless `cross_domain` is set, in which case the floor
    is raised to at least `CROSS_DOMAIN_GPU_PROBE_TIMEOUT_S` (a WSL -> Windows exec can
    legitimately exceed the same-domain default; #171 GPU-P0-1). Passing the caller's own existing
    default as `default_s` (e.g. the agent capsule's `--gpu-timeout-s`) means an explicit,
    already-generous caller value is never lowered by the cross-domain floor -- only ever raised
    when it would otherwise be too tight.
    """
    raw = os.environ.get("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", "").strip()
    if raw:
        try:
            override = float(raw)
        except ValueError:
            override = 0.0
        if override > 0:
            return override
    if cross_domain:
        return max(default_s, CROSS_DOMAIN_GPU_PROBE_TIMEOUT_S)
    return default_s


@lru_cache(maxsize=1)
def resolve_ripgrep_binary() -> Path | None:
    binary_name = "rg.exe" if sys.platform.startswith("win") else "rg"

    # Priority 1: Explicit override
    if env_override := os.environ.get("TG_RG_PATH"):
        p = Path(env_override).expanduser()
        if p.is_file():
            return p.resolve()

    # Priority 2: PATH. For explicit rg-compatible passthrough, prefer the same rg that
    # caller gets from the shell so JSON event ordering and version-specific behavior
    # match the user's baseline. Bundled rg remains the fallback when PATH has none.
    if which_rg := shutil.which(binary_name):
        return Path(which_rg).resolve()

    # Priority 3: In-tree bundled binary
    repo_root = _repo_root()
    if sys.platform.startswith("win"):
        dev_path = repo_root / "benchmarks" / "ripgrep-14.1.0-x86_64-pc-windows-msvc" / "rg.exe"
    elif sys.platform.startswith("darwin"):
        dev_path = repo_root / "benchmarks" / "ripgrep-14.1.0-x86_64-apple-darwin" / "rg"
    else:
        dev_path = repo_root / "benchmarks" / "ripgrep-14.1.0-x86_64-unknown-linux-musl" / "rg"

    if dev_path.is_file():
        return dev_path.resolve()

    return None
