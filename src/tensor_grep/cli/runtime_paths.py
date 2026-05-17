import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


def env_flag_enabled(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


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
        python_root = candidate.parent.parent
        return (python_root / "python.exe").is_file() or (python_root / "pythonw.exe").is_file()

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
    try:
        from importlib.metadata import version

        return version("tensor-grep")
    except Exception:
        return _read_project_version_fallback()


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
) -> dict[str, str | None]:
    """Return non-destructive native tg version metadata for diagnostics."""
    root = repo_root or _repo_root()
    expected = expected_version or _expected_tg_version()
    try:
        resolved = candidate.expanduser().resolve()
    except OSError:
        resolved = candidate.expanduser().absolute()

    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"
    release_binary = (root / "rust_core" / "target" / "release" / binary_name).resolve()
    debug_binary = (root / "rust_core" / "target" / "debug" / binary_name).resolve()
    if resolved == release_binary:
        kind = "in-tree-release"
    elif resolved == debug_binary:
        kind = "in-tree-debug"
    else:
        kind = "external"

    version_text = _native_tg_version(resolved) if resolved.is_file() else None
    if not resolved.is_file():
        version_status = "missing"
    elif _native_tg_version_matches(expected, version_text):
        version_status = "matches"
    elif version_text:
        version_status = "stale"
    else:
        version_status = "unknown"

    return {
        "path": str(resolved),
        "kind": kind,
        "version": version_text,
        "expected_version": expected,
        "version_status": version_status,
    }


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


@lru_cache(maxsize=1)
def resolve_ripgrep_binary() -> Path | None:
    binary_name = "rg.exe" if sys.platform.startswith("win") else "rg"

    # Priority 1: Explicit override
    if env_override := os.environ.get("TG_RG_PATH"):
        p = Path(env_override).expanduser()
        if p.is_file():
            return p.resolve()

    # Priority 2: In-tree bundled binary
    repo_root = _repo_root()
    if sys.platform.startswith("win"):
        dev_path = repo_root / "benchmarks" / "ripgrep-14.1.0-x86_64-pc-windows-msvc" / "rg.exe"
    elif sys.platform.startswith("darwin"):
        dev_path = repo_root / "benchmarks" / "ripgrep-14.1.0-x86_64-apple-darwin" / "rg"
    else:
        dev_path = repo_root / "benchmarks" / "ripgrep-14.1.0-x86_64-unknown-linux-musl" / "rg"

    if dev_path.is_file():
        return dev_path.resolve()

    # Priority 3: PATH
    if which_rg := shutil.which(binary_name):
        return Path(which_rg).resolve()

    return None
