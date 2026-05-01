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
_NODE_PACKAGE_SPECS = (
    "pyright@1.1.409",
    "typescript@6.0.3",
    "typescript-language-server@5.1.3",
    "intelephense@1.18.0",
)

_LANGUAGE_ALIASES = {
    "python": "python",
    "javascript": "javascript",
    "js": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "go": "go",
    "golang": "go",
    "rust": "rust",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "csharp": "csharp",
    "c#": "csharp",
    "cs": "csharp",
    "php": "php",
    "kotlin": "kotlin",
    "swift": "swift",
    "lua": "lua",
}

_LANGUAGE_ORDER = [
    "python",
    "javascript",
    "typescript",
    "go",
    "rust",
    "java",
    "c",
    "cpp",
    "csharp",
    "php",
    "kotlin",
    "swift",
    "lua",
]


def managed_provider_root(root_override: Path | None = None) -> Path:
    if root_override is not None:
        return root_override.expanduser().resolve()
    configured = os.environ.get(_MANAGED_PROVIDER_HOME_ENV_VAR)
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".tensor-grep" / "providers").resolve()


def supported_lsp_languages() -> list[str]:
    return list(_LANGUAGE_ORDER)


def canonical_language(language: str) -> str:
    normalized = language.lower().strip()
    return _LANGUAGE_ALIASES.get(normalized, normalized)


def is_windows() -> bool:
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


def _node_runtime_dir(root: Path) -> Path:
    return root / "node-runtime"


def _node_packages_dir(root: Path) -> Path:
    return root / "node-packages"


def _managed_bin_dir(root: Path) -> Path:
    return root / "bin"


def _node_executable(root: Path) -> Path:
    runtime_dir = _node_runtime_dir(root)
    if is_windows():
        return runtime_dir / "node.exe"
    return runtime_dir / "bin" / "node"


def _node_runtime_path_entry(root: Path) -> Path:
    if is_windows():
        return _node_runtime_dir(root)
    return _node_runtime_dir(root) / "bin"


def _npm_executable(root: Path) -> Path:
    runtime_dir = _node_runtime_dir(root)
    if is_windows():
        return runtime_dir / "npm.cmd"
    return runtime_dir / "bin" / "npm"


def _managed_node_binary(root: Path, binary_name: str) -> Path:
    suffix = ".cmd" if is_windows() else ""
    return _node_packages_dir(root) / "node_modules" / ".bin" / f"{binary_name}{suffix}"


def _managed_bin_binary(root: Path, binary_name: str) -> Path:
    suffix = ".exe" if is_windows() else ""
    return _managed_bin_dir(root) / f"{binary_name}{suffix}"


def _provider_args(binary: str, language: str) -> list[str]:
    normalized = canonical_language(language)
    if normalized in {"python", "javascript", "typescript", "php"}:
        return [binary, "--stdio"]
    return [binary]


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
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    destination_root = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if target != destination_root and destination_root not in target.parents:
            raise RuntimeError(f"Archive member escapes destination: {member.name}")
    archive.extractall(destination)


def _safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    destination_root = destination.resolve()
    for member_name in archive.namelist():
        target = (destination / member_name).resolve()
        if target != destination_root and destination_root not in target.parents:
            raise RuntimeError(f"Archive member escapes destination: {member_name}")
    archive.extractall(destination)


def _extract_archive(archive_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract_zip(archive, destination)
    else:
        with tarfile.open(archive_path) as archive:
            _safe_extract_tar(archive, destination)
    extracted_children = [child for child in destination.iterdir() if child.is_dir()]
    if len(extracted_children) != 1:
        raise RuntimeError(f"Expected one extracted directory from {archive_path.name}")
    return extracted_children[0]


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
        runtime_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted_dir), str(runtime_dir))
    if not node_executable.is_file():
        raise RuntimeError(f"Managed Node runtime install failed: missing {node_executable}")
    return runtime_dir


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
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )


def _ensure_node_packages(root: Path) -> None:
    required_binaries = [
        _managed_node_binary(root, "pyright-langserver"),
        _managed_node_binary(root, "typescript-language-server"),
        _managed_node_binary(root, "intelephense"),
    ]
    if all(binary.is_file() for binary in required_binaries):
        return
    _ensure_node_runtime(root)
    _write_package_json(root)
    _run_checked(
        [
            str(_npm_executable(root)),
            "install",
            "--no-fund",
            "--no-audit",
            *list(_NODE_PACKAGE_SPECS),
        ],
        cwd=_node_packages_dir(root),
    )
    if not all(binary.is_file() for binary in required_binaries):
        raise RuntimeError("Managed Node package install completed without expected LSP binaries")


def _mark_executable(path: Path) -> None:
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


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


def _copy_binary_to_managed(binary: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, destination)
    if not is_windows():
        _mark_executable(destination)
    return destination


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
    _copy_binary_to_managed(binary, destination)
    return True


def _download_rust_analyzer(destination: Path) -> None:
    url = _rust_analyzer_download_url()
    with tempfile.TemporaryDirectory(prefix="tg-rust-analyzer-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        archive_path = temp_dir / "rust-analyzer.gz"
        _download(url, archive_path)
        with gzip.open(archive_path, "rb") as compressed, destination.open("wb") as output:
            shutil.copyfileobj(compressed, output)
    if not is_windows():
        _mark_executable(destination)


def _ensure_rust_analyzer(root: Path) -> Path:
    destination = _managed_bin_binary(root, "rust-analyzer")
    if destination.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not _copy_rust_analyzer_from_rustup(destination):
        _download_rust_analyzer(destination)
    if not destination.is_file():
        raise RuntimeError(f"Managed rust-analyzer install failed: missing {destination}")
    return destination


def _find_go_binary_name(root: Path, binary_name: str) -> Path | None:
    go = shutil.which("go")
    if not go:
        return None
    for env_name in ("GOBIN", "GOPATH"):
        completed = subprocess.run(
            [go, "env", env_name],
            check=False,
            capture_output=True,
            text=True,
        )
        value = completed.stdout.strip()
        if not value:
            continue
        parent = Path(value) if env_name == "GOBIN" else Path(value) / "bin"
        candidate = parent / _managed_bin_binary(root, binary_name).name
        if candidate.is_file():
            return candidate
    return None


def _ensure_gopls(root: Path) -> Path:
    destination = _managed_bin_binary(root, "gopls")
    if destination.is_file():
        return destination
    existing = shutil.which("gopls")
    if existing:
        return _copy_binary_to_managed(existing, destination)
    go = shutil.which("go")
    if not go:
        raise RuntimeError("Go toolchain not found; unable to install gopls")
    _run_checked([go, "install", "golang.org/x/tools/gopls@latest"])
    built = _find_go_binary_name(root, "gopls")
    if built is None:
        raise RuntimeError("Go install completed without a discoverable gopls binary")
    return _copy_binary_to_managed(str(built), destination)


def _ensure_csharp_ls(root: Path) -> Path:
    destination = _managed_bin_binary(root, "csharp-ls")
    if destination.is_file():
        return destination
    existing = shutil.which("csharp-ls")
    if existing:
        return _copy_binary_to_managed(existing, destination)
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise RuntimeError("dotnet not found; unable to install csharp-ls")
    tool_dir = _managed_bin_dir(root)
    tool_dir.mkdir(parents=True, exist_ok=True)
    install_cmd = [dotnet, "tool", "install", "--tool-path", str(tool_dir), "csharp-ls"]
    completed = subprocess.run(install_cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0 and "already installed" not in completed.stderr.lower():
        update_cmd = [dotnet, "tool", "update", "--tool-path", str(tool_dir), "csharp-ls"]
        _run_checked(update_cmd)
    if not destination.is_file():
        raise RuntimeError(f"dotnet tool install completed without {destination.name}")
    return destination


def _find_on_path(candidates: list[str]) -> str | None:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _swift_command() -> list[str] | None:
    resolved = _find_on_path(["sourcekit-lsp"])
    if resolved:
        return [resolved]
    if sys_platform() != "darwin":
        return None
    xcrun = shutil.which("xcrun")
    if not xcrun:
        return None
    completed = subprocess.run(
        [xcrun, "--find", "sourcekit-lsp"],
        check=False,
        capture_output=True,
        text=True,
    )
    candidate = completed.stdout.strip()
    if completed.returncode == 0 and candidate:
        return [candidate]
    return None


def managed_provider_command(
    language: str, *, managed_root: Path | None = None
) -> list[str] | None:
    root = managed_provider_root(managed_root)
    normalized = canonical_language(language)
    if normalized == "python":
        binary = _managed_node_binary(root, "pyright-langserver")
    elif normalized in {"javascript", "typescript"}:
        binary = _managed_node_binary(root, "typescript-language-server")
    elif normalized == "php":
        binary = _managed_node_binary(root, "intelephense")
    elif normalized == "rust":
        binary = _managed_bin_binary(root, "rust-analyzer")
    elif normalized == "go":
        binary = _managed_bin_binary(root, "gopls")
    elif normalized == "csharp":
        binary = _managed_bin_binary(root, "csharp-ls")
    else:
        return None
    if not binary.is_file():
        return None
    return _provider_args(str(binary.resolve()), normalized)


def path_provider_command(language: str) -> list[str] | None:
    normalized = canonical_language(language)
    if normalized == "python":
        resolved = _find_on_path(["pyright-langserver"])
    elif normalized in {"javascript", "typescript"}:
        resolved = _find_on_path(["typescript-language-server"])
    elif normalized == "go":
        resolved = _find_on_path(["gopls"])
    elif normalized == "rust":
        resolved = _find_on_path(["rust-analyzer"])
        if resolved is None:
            cargo_bin = (
                Path.home()
                / ".cargo"
                / "bin"
                / _managed_bin_binary(Path("."), "rust-analyzer").name
            )
            if cargo_bin.is_file():
                resolved = str(cargo_bin)
    elif normalized == "java":
        resolved = _find_on_path(["jdtls"])
    elif normalized in {"c", "cpp"}:
        resolved = _find_on_path(["clangd"])
    elif normalized == "csharp":
        resolved = _find_on_path(["csharp-ls"])
    elif normalized == "php":
        resolved = _find_on_path(["intelephense"])
    elif normalized == "kotlin":
        resolved = _find_on_path(["kotlin-lsp"])
    elif normalized == "swift":
        return _swift_command()
    elif normalized == "lua":
        resolved = _find_on_path(["lua-language-server", "lua-language-server.exe"])
    else:
        return None
    if not resolved:
        return None
    return _provider_args(resolved, normalized)


def resolved_provider_command(
    language: str, *, managed_root: Path | None = None
) -> list[str] | None:
    managed = managed_provider_command(language, managed_root=managed_root)
    if managed is not None:
        return managed
    return path_provider_command(language)


def managed_provider_env(
    command: list[str],
    *,
    base_env: dict[str, str] | None = None,
    managed_root: Path | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    root = managed_provider_root(managed_root)
    if _command_source(command, root) != "managed":
        return env
    path_entries = [str(_node_runtime_path_entry(root)), str(_managed_bin_dir(root).resolve())]
    existing_path = env.get("PATH")
    if existing_path:
        path_entries.append(existing_path)
    env["PATH"] = os.pathsep.join(path_entries)
    return env


def _command_source(command: list[str] | None, root: Path) -> str:
    if not command:
        return "missing"
    try:
        command_path = Path(command[0]).resolve()
        command_path.relative_to(root)
    except ValueError:
        return "path"
    except OSError:
        return "path"
    return "managed"


def install_managed_lsp_providers(
    *,
    python_executable: str,
    managed_root: Path | None = None,
    include_toolchain_providers: bool = False,
) -> dict[str, Any]:
    root = managed_provider_root(managed_root)
    root.mkdir(parents=True, exist_ok=True)

    errors: dict[str, str] = {}
    try:
        _ensure_node_packages(root)
    except Exception as exc:
        errors["node"] = str(exc)
    if include_toolchain_providers:
        try:
            _ensure_rust_analyzer(root)
        except Exception as exc:
            errors["rust"] = str(exc)
    toolchain_installers = (("go", _ensure_gopls), ("csharp", _ensure_csharp_ls))
    for language, installer in toolchain_installers if include_toolchain_providers else ():
        try:
            installer(root)
        except Exception as exc:
            errors[language] = str(exc)

    providers: dict[str, dict[str, Any]] = {}
    for language in supported_lsp_languages():
        command = resolved_provider_command(language, managed_root=root)
        install_error = errors.get(language)
        if language in {"python", "javascript", "typescript", "php"}:
            install_error = install_error or errors.get("node")
        providers[language] = {
            "command": command,
            "available": command is not None,
            "command_source": _command_source(command, root),
            "install_error": install_error,
        }

    payload: dict[str, Any] = {
        "python_executable": python_executable,
        "managed_provider_root": str(root),
        "include_toolchain_providers": include_toolchain_providers,
        "node": {
            "runtime": str(_node_executable(root)),
            "packages_dir": str(_node_packages_dir(root)),
            "package_specs": list(_NODE_PACKAGE_SPECS),
            "installed": not bool(errors.get("node")),
        },
        "providers": providers,
    }
    if errors:
        payload["install_errors"] = errors
    return payload
