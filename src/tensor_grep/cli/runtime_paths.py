import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path


def _looks_like_current_python_launcher(candidate: Path) -> bool:
    """Reject console-entrypoint shims from the active Python environment.

    These PATH hits recurse back into `python -m tensor_grep...` and are not
    native `tg` binaries.
    """
    try:
        resolved = candidate.resolve()
        python_bin_dir = Path(sys.executable).resolve().parent
    except OSError:
        return False
    return resolved.parent == python_bin_dir


@lru_cache(maxsize=1)
def resolve_native_tg_binary() -> Path | None:
    repo_root = Path(__file__).resolve().parents[3]
    binary_name = "tg.exe" if sys.platform.startswith("win") else "tg"

    # Priority 1: Exact explicit override
    env_override = os.environ.get("TG_NATIVE_TG_BINARY") or os.environ.get("TG_MCP_TG_BINARY")
    if env_override:
        p = Path(env_override).expanduser()
        if p.is_file():
            return p.resolve()
        raise FileNotFoundError(f"Configured binary {p} not found.")

    candidates = []

    # Priority 2: In-tree build
    candidates.extend(
        [
            repo_root / "rust_core" / "target" / "release" / binary_name,
            repo_root / "rust_core" / "target" / "debug" / binary_name,
            repo_root / "benchmarks" / binary_name,
        ]
    )
    if sys.platform.startswith("win"):
        candidates.append(repo_root / "benchmarks" / "tg_rust.exe")

    existing = [candidate.resolve() for candidate in candidates if candidate.is_file()]
    if existing:
        return max(existing, key=lambda candidate: candidate.stat().st_mtime_ns)

    # Priority 3: PATH installations
    if which_tg := shutil.which(binary_name):
        resolved = Path(which_tg).resolve()
        if not _looks_like_current_python_launcher(resolved):
            return resolved
    if which_tensor_grep := shutil.which("tensor-grep" + (".exe" if sys.platform.startswith("win") else "")):
        resolved = Path(which_tensor_grep).resolve()
        if not _looks_like_current_python_launcher(resolved):
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
    repo_root = Path(__file__).resolve().parents[3]
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
