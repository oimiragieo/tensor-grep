from __future__ import annotations

import gzip
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

_MANAGED_PROVIDER_HOME_ENV_VAR = "TENSOR_GREP_LSP_PROVIDER_HOME"
_NODE_VERSION = "22.14.0"
_NODE_PACKAGES = ("pyright", "typescript", "typescript-language-server")


def managed_provider_root(root_override: Path | None = None) -> Path:
    if root_override is not None:
        return root_override.expanduser().resolve()
    configured = os.environ.get(_MANAGED_PROVIDER_HOME_ENV_VAR)
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".tensor-grep" / "providers").resolve()


def _node_runtime_dir(root: Path) -> Path:
    return root / "node-runtime"


def _node_packages_dir(root: Path) -> Path:
    return root / "node-packages"


def _managed_bin_dir(root: Path) -> Path:
    return root / "bin"


def _is_windows() -> bool:
    return sys_platform().startswith("win")


def sys_platform() -> str:
    return platform.system().lower()


def _normalized_machine() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine


def _node_archive_name() -> str:
    system = sys_platform()
    machine = _normalized_machine()
    if system == "windows" and machine == "x86_64":
        return f"node-v{_NODE_VERSION}-win-x64.zip"
    if system == "linux" and machine == "x86_64":
        return f"node-v{_NODE_VERSION}-linux-x64.tar.xz"
    if system == "linux" and machine == "arm64":
        return f"node-v{_NODE_VERSION}-linux-arm64.tar.xz"
    if system == "darwin" and machine == "x86_64":
        return f"node-v{_NODE_VERSION}-darwin-x64.tar.gz"
    if system == "darwin" and machine == "arm64":
        return f"node-v{_NODE_VERSION}-darwin-arm64.tar.gz"
    raise RuntimeError(f"Unsupported platform for managed Node runtime: {system}/{machine}")


def _download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _extract_archive(archive_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(destination)
    else:
        with tarfile.open(archive_path) as archive:
            archive.extractall(destination)
    extracted_children = [child for child in destination.iterdir() if child.is_dir()]
    if len(extracted_children) != 1:
        raise RuntimeError(f"Expected one extracted directory from {archive_path.name}")
    return extracted_children[0]


def _node_executable(root: Path) -> Path:
    runtime_dir = _node_runtime_dir(root)
    if _is_windows():
        return runtime_dir / "node.exe"
    return runtime_dir / "bin" / "node"


def _npm_executable(root: Path) -> Path:
    runtime_dir = _node_runtime_dir(root)
    if _is_windows():
        return runtime_dir / "npm.cmd"
    return runtime_dir / "bin" / "npm"


def _ensure_node_runtime(root: Path) -> Path:
    runtime_dir = _node_runtime_dir(root)
    node_executable = _node_executable(root)
    if node_executable.is_file():
        return runtime_dir

    archive_name = _node_archive_name()
    url = f"https://nodejs.org/dist/v{_NODE_VERSION}/{archive_name}"
    with tempfile.TemporaryDirectory(prefix="tg-node-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        archive_path = temp_dir / archive_name
        _download(url, archive_path)
        extracted_dir = _extract_archive(archive_path, temp_dir / "extract")
        if runtime_dir.exists():
            shutil.rmtree(runtime_dir)
        shutil.move(str(extracted_dir), str(runtime_dir))
    if not node_executable.is_file():
        raise RuntimeError(f"Managed Node runtime install failed: missing {node_executable}")
    return runtime_dir


def _managed_node_binary(root: Path, binary_name: str) -> Path:
    suffix = ".cmd" if _is_windows() else ""
    return _node_packages_dir(root) / "node_modules" / ".bin" / f"{binary_name}{suffix}"


def _managed_rust_analyzer_binary(root: Path) -> Path:
    suffix = ".exe" if _is_windows() else ""
    return _managed_bin_dir(root) / f"rust-analyzer{suffix}"


def managed_provider_command(
    language: str, *, managed_root: Path | None = None
) -> list[str] | None:
    root = managed_provider_root(managed_root)
    normalized = language.lower()
    if normalized == "python":
        binary = _managed_node_binary(root, "pyright-langserver")
        if binary.is_file():
            return [str(binary), "--stdio"]
        return None
    if normalized in {"javascript", "typescript"}:
        binary = _managed_node_binary(root, "typescript-language-server")
        if binary.is_file():
            return [str(binary), "--stdio"]
        return None
    if normalized == "rust":
        binary = _managed_rust_analyzer_binary(root)
        if binary.is_file():
            return [str(binary)]
        return None
    return None


def _run_checked(command: list[str], *, cwd: Path | None = None) -> None:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return
    raise RuntimeError(
        f"Command failed ({completed.returncode}): {' '.join(command)}\n"
        f"stdout: {completed.stdout}\n"
        f"stderr: {completed.stderr}"
    )


def _write_package_json(root: Path) -> None:
    package_dir = _node_packages_dir(root)
    package_dir.mkdir(parents=True, exist_ok=True)
    package_json = package_dir / "package.json"
    if package_json.exists():
        return
    package_json.write_text(
        json.dumps({"name": "tensor-grep-lsp-providers", "private": True}, indent=2),
        encoding="utf-8",
    )


def _ensure_node_packages(root: Path) -> None:
    pyright_binary = _managed_node_binary(root, "pyright-langserver")
    ts_binary = _managed_node_binary(root, "typescript-language-server")
    if pyright_binary.is_file() and ts_binary.is_file():
        return
    _ensure_node_runtime(root)
    _write_package_json(root)
    _run_checked(
        [
            str(_npm_executable(root)),
            "install",
            "--no-fund",
            "--no-audit",
            *list(_NODE_PACKAGES),
        ],
        cwd=_node_packages_dir(root),
    )
    if not pyright_binary.is_file() or not ts_binary.is_file():
        raise RuntimeError("Managed Node package install completed without expected LSP binaries")


def _rust_analyzer_download_url() -> str:
    system = sys_platform()
    machine = _normalized_machine()
    if system == "windows" and machine == "x86_64":
        artifact = "rust-analyzer-x86_64-pc-windows-msvc.gz"
    elif system == "linux" and machine == "x86_64":
        artifact = "rust-analyzer-x86_64-unknown-linux-gnu.gz"
    elif system == "linux" and machine == "arm64":
        artifact = "rust-analyzer-aarch64-unknown-linux-gnu.gz"
    elif system == "darwin" and machine == "x86_64":
        artifact = "rust-analyzer-x86_64-apple-darwin.gz"
    elif system == "darwin" and machine == "arm64":
        artifact = "rust-analyzer-aarch64-apple-darwin.gz"
    else:
        raise RuntimeError(f"Unsupported platform for rust-analyzer: {system}/{machine}")
    return f"https://github.com/rust-lang/rust-analyzer/releases/latest/download/{artifact}"


def _mark_executable(path: Path) -> None:
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_rust_analyzer_from_rustup(destination: Path) -> bool:
    rustup = shutil.which("rustup")
    if not rustup:
        return False
    _run_checked([rustup, "component", "add", "rust-analyzer", "rust-src"])
    binary = shutil.which("rust-analyzer")
    if binary is None:
        cargo_bin = Path.home() / ".cargo" / "bin" / destination.name
        if cargo_bin.is_file():
            binary = str(cargo_bin)
    if binary is None:
        return False
    shutil.copy2(binary, destination)
    if not _is_windows():
        _mark_executable(destination)
    return True


def _download_rust_analyzer(destination: Path) -> None:
    url = _rust_analyzer_download_url()
    with tempfile.TemporaryDirectory(prefix="tg-rust-analyzer-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        archive_path = temp_dir / "rust-analyzer.gz"
        _download(url, archive_path)
        with gzip.open(archive_path, "rb") as compressed, destination.open("wb") as output:
            shutil.copyfileobj(compressed, output)
    if not _is_windows():
        _mark_executable(destination)


def _ensure_rust_analyzer(root: Path) -> Path:
    destination = _managed_rust_analyzer_binary(root)
    if destination.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not _copy_rust_analyzer_from_rustup(destination):
        _download_rust_analyzer(destination)
    if not destination.is_file():
        raise RuntimeError(f"Managed rust-analyzer install failed: missing {destination}")
    return destination


def install_managed_lsp_providers(
    *,
    python_executable: str,
    managed_root: Path | None = None,
) -> dict[str, Any]:
    root = managed_provider_root(managed_root)
    root.mkdir(parents=True, exist_ok=True)
    _ensure_node_packages(root)
    rust_binary = _ensure_rust_analyzer(root)
    payload: dict[str, Any] = {
        "python_executable": python_executable,
        "managed_provider_root": str(root),
        "node": {
            "runtime": str(_node_executable(root)),
            "packages_dir": str(_node_packages_dir(root)),
            "installed": True,
        },
        "providers": {
            "python": {"command": managed_provider_command("python", managed_root=root)},
            "javascript": {"command": managed_provider_command("javascript", managed_root=root)},
            "typescript": {"command": managed_provider_command("typescript", managed_root=root)},
            "rust": {"command": [str(rust_binary)]},
        },
    }
    return payload
