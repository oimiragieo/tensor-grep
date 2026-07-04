from __future__ import annotations

import gzip
import hashlib
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

# Pin + verify the managed Node runtime archive (fail-closed). SHA-256 from the official
# nodejs.org SHASUMS256.txt for v{_NODE_VERSION}, keyed by the exact archive filename
# _node_archive_name() requests (Linux .tar.xz, macOS .tar.gz, Windows .zip). Update this table
# together with _NODE_VERSION; a CI test asserts no entry is empty.
_NODE_SHA256: dict[str, str] = {
    f"node-v{_NODE_VERSION}-win-x64.zip": (
        "55b639295920b219bb2acbcfa00f90393a2789095b7323f79475c9f34795f217"
    ),
    f"node-v{_NODE_VERSION}-linux-x64.tar.xz": (
        "69b09dba5c8dcb05c4e4273a4340db1005abeafe3927efda2bc5b249e80437ec"
    ),
    f"node-v{_NODE_VERSION}-linux-arm64.tar.xz": (
        "08bfbf538bad0e8cbb0269f0173cca28d705874a67a22f60b57d99dc99e30050"
    ),
    f"node-v{_NODE_VERSION}-darwin-x64.tar.gz": (
        "6698587713ab565a94a360e091df9f6d91c8fadda6d00f0cf6526e9b40bed250"
    ),
    f"node-v{_NODE_VERSION}-darwin-arm64.tar.gz": (
        "e9404633bc02a5162c5c573b1e2490f5fb44648345d64a958b17e325729a5e42"
    ),
}

# Cap toolchain downloads so a malicious/oversized response can't exhaust memory or disk before
# the checksum is verified (mirrors the native front-door + npm install posture).
_MAX_TOOLCHAIN_DOWNLOAD_BYTES = 256 * 1024 * 1024

# audit S5: pin rust-analyzer to an exact release + verify each artifact's SHA-256 (fail-closed).
# Hashes are the sha256 of the downloaded release asset (the .gz on Unix, the .zip on Windows) for
# this tag, taken from https://github.com/rust-lang/rust-analyzer/releases/tag/2025-01-13. Update
# _RUST_ANALYZER_VERSION and these hashes together; a CI test asserts none is empty.
_RUST_ANALYZER_VERSION = "2025-01-13"
_RUST_ANALYZER_SHA256: dict[str, str] = {
    # platform_key -> sha256 of the downloaded artifact (.zip on Windows, .gz elsewhere)
    "windows/x86_64": "61188792c6d9aea497c0de071b5df28bb31a265b99796c2ca314ca4541605dab",
    "linux/x86_64": "c0583d4f57b14f001d74ff187d9c266e0ebe9b07a8d8ba3ac3dd4a658f780707",
    "linux/arm64": "e6e69ec26dc079df5e8431db851806fe0d5da9b9f17d115ad5d527004878e3d6",
    "darwin/x86_64": "490c66314989b37f795e41ada5f59f182e13aa762d0c6b527041e5e3b8f4cc1d",
    "darwin/arm64": "8092463bff864116b52b4c6c9153a24f7d41659dfc3c8485130430341f534d28",
}

# Pin gopls + csharp-ls to exact versions instead of "latest"/unversioned. Once the version is
# pinned, integrity is enforced fail-closed by each ecosystem's checksum DB: Go's GOSUMDB
# (sum.golang.org) for `go install gopls@vX`, and NuGet package signing for the dotnet tool.
_GOPLS_VERSION = "v0.22.0"
_CSHARP_LS_VERSION = "0.25.0"

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
    total = 0
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_TOOLCHAIN_DOWNLOAD_BYTES:
                raise RuntimeError(
                    f"Toolchain download exceeded {_MAX_TOOLCHAIN_DOWNLOAD_BYTES} bytes "
                    f"(possible oversized or malicious response): {url}"
                )
            output.write(chunk)


def _allow_unverified_toolchain() -> bool:
    """Explicit opt-out for air-gapped/offline installs: TG_ALLOW_UNVERIFIED_TOOLCHAIN=1 skips
    integrity verification (fail-OPEN by consent). The default posture is fail-CLOSED."""
    return os.environ.get("TG_ALLOW_UNVERIFIED_TOOLCHAIN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_node_archive(archive_path: Path, archive_name: str) -> None:
    """Fail-closed: verify the downloaded Node archive against its pinned SHA-256 BEFORE extraction.

    Refuses (raises) when no pinned hash exists or the hash mismatches, unless the explicit
    TG_ALLOW_UNVERIFIED_TOOLCHAIN opt-out is set.
    """
    if _allow_unverified_toolchain():
        import warnings

        warnings.warn(
            f"TG_ALLOW_UNVERIFIED_TOOLCHAIN set; skipping checksum verification of {archive_name}",
            stacklevel=2,
        )
        return
    expected = _NODE_SHA256.get(archive_name, "")
    if not expected:
        raise RuntimeError(
            f"No pinned SHA-256 for Node archive {archive_name}; refusing to install an unverified "
            "runtime (set TG_ALLOW_UNVERIFIED_TOOLCHAIN=1 to override)."
        )
    actual = _sha256_file(archive_path)
    if actual.lower() != expected.lower():
        raise RuntimeError(
            f"Node archive {archive_name} failed checksum verification (expected {expected}, "
            f"got {actual}); refusing to install a tampered runtime."
        )


def _safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    # audit S6: validate member names AND symlink/hardlink targets; reject members
    # that would resolve outside the destination tree (CVE-2007-4559 class).
    destination_root = destination.resolve()
    for member in archive.getmembers():
        # Check the entry path itself.
        target = (destination / member.name).resolve()
        if target != destination_root and destination_root not in target.parents:
            raise RuntimeError(f"Archive member escapes destination: {member.name}")
        # Check symlink targets (issym) and hardlink targets (islnk).
        if member.issym() or member.islnk():
            link_target = member.linkname
            if link_target:
                # Resolve relative to the member's containing directory so that
                # both absolute and relative link targets are handled correctly.
                member_parent = (destination / member.name).parent
                resolved_link = (member_parent / link_target).resolve()
                if (
                    resolved_link != destination_root
                    and destination_root not in resolved_link.parents
                ):
                    raise RuntimeError(
                        f"Archive member symlink/hardlink escapes destination: "
                        f"{member.name} -> {link_target}"
                    )
    # Use filter='data' on Python 3.12+ to apply additional hardening; fall back
    # to our pre-validated extractall on older releases.
    try:
        archive.extractall(destination, filter="data")  # type: ignore[call-arg]
    except TypeError:
        # Python < 3.12 does not support the filter= parameter; our manual
        # validation above already guards against path-traversal attacks.
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
    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    # Stage the new runtime NEXT TO runtime_dir (same filesystem -> atomic rename), and BACK UP the
    # existing runtime instead of deleting it up front. A download/extract/move failure must never
    # brick a previously-working provider runtime (audit): on any failure the backup is restored.
    staged_dir = runtime_dir.with_name(f".{runtime_dir.name}.staging-{os.getpid()}")
    backup_dir = runtime_dir.with_name(f".{runtime_dir.name}.backup-{os.getpid()}")
    for stale in (staged_dir, backup_dir):
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
    with tempfile.TemporaryDirectory(prefix="tg-node-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        archive_path = temp_dir / archive_name
        _download(url, archive_path)
        _verify_node_archive(archive_path, archive_name)
        extracted_dir = _extract_archive(archive_path, temp_dir / "extract")
        shutil.move(
            str(extracted_dir), str(staged_dir)
        )  # into place (cross-fs copy) before any swap
    had_previous = runtime_dir.exists()
    if had_previous:
        os.replace(str(runtime_dir), str(backup_dir))  # atomic move-aside, not a destructive delete
    try:
        os.replace(str(staged_dir), str(runtime_dir))  # atomic swap-in (same filesystem)
        if not node_executable.is_file():
            raise RuntimeError(f"Managed Node runtime install failed: missing {node_executable}")
    except Exception:
        # Restore the previous working runtime on any failure of the swap/verify.
        if runtime_dir.exists():
            shutil.rmtree(runtime_dir, ignore_errors=True)
        if had_previous:
            os.replace(str(backup_dir), str(runtime_dir))
        raise
    if had_previous:
        shutil.rmtree(backup_dir, ignore_errors=True)
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


def wrap_windows_batch_command(command: list[str]) -> list[str]:
    """Route a ``.cmd``/``.bat`` shim through ``cmd.exe`` on Windows.

    Windows ``CreateProcess`` cannot launch a batch script directly — ``subprocess`` raises
    ``WinError 193`` ("%1 is not a valid Win32 application"). The managed Node toolchain ships
    every entry point as a ``.cmd`` shim (``npm.cmd``, ``pyright-langserver.cmd``,
    ``intelephense.cmd``, ``typescript-language-server.cmd``), so every managed-LSP spawn hits
    this. No-op on non-Windows and for real executables. The test suite mocks ``subprocess``,
    so CI never exercised the real launch — this is the fix for that blind spot.
    """
    if command and is_windows() and Path(command[0]).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/C", *command]
    return command


# Managed Node LSP shim name -> npm package name. NOT 1:1: pyright-langserver ships in
# the `pyright` package. Used to resolve the trusted JS entrypoint for the CWE-427 bypass.
_MANAGED_NODE_BIN_TO_PACKAGE = {
    "pyright-langserver": "pyright",
    "typescript-language-server": "typescript-language-server",
    "intelephense": "intelephense",
}


def managed_provider_js_entrypoint(binary_name: str, root: Path) -> Path:
    """Resolve a managed Node LSP shim to its trusted absolute JS entrypoint.

    Reads the package's ``package.json['bin']`` (the stable contract) rather than
    text-parsing the generated ``.cmd`` cmd-shim (npm's cmd-shim template drifts across
    versions with no stability guarantee). Fails CLOSED — raises on any resolution gap —
    so the caller can never silently fall back to the CWD-searchable ``cmd.exe``/``.cmd``
    launch path (CWE-427).
    """
    package = _MANAGED_NODE_BIN_TO_PACKAGE.get(binary_name)
    if package is None:
        raise ValueError(f"no managed npm package known for LSP shim {binary_name!r}")
    package_dir = _node_packages_dir(root) / "node_modules" / package
    data = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    bin_field = data.get("bin")
    relative: str | None
    if isinstance(bin_field, str):
        relative = bin_field
    elif isinstance(bin_field, dict):
        relative = bin_field.get(binary_name)
    else:
        relative = None
    if not relative:
        raise ValueError(f"package.json for {package!r} has no bin entry for {binary_name!r}")
    entry = (package_dir / relative).resolve()
    if not entry.is_file():
        raise FileNotFoundError(f"managed LSP entrypoint missing: {entry}")
    return entry


def direct_managed_node_command(command: list[str], *, root: Path) -> list[str] | None:
    """Rewrite a MANAGED Node ``.cmd`` shim spawn into a direct, trusted-absolute
    ``[node.exe, entry.js, *args]`` argv, bypassing the CWE-427-vulnerable cmd-shim.

    The npm ``.cmd`` shim resolves a BARE ``node`` token, which ``cmd.exe`` searches
    CWD-first — and for an LSP spawn CWD is the attacker-controlled analyzed workspace, so
    a planted ``workspace_root\\node.exe`` hijacks the language server. Rewriting to the
    trusted absolute node runtime + JS entrypoint removes every CWD-searchable name.

    Returns ``None`` (caller keeps its existing ``wrap_windows_batch_command`` path) for
    every case this bypass must NOT touch: non-Windows, non-managed/external providers, and
    managed native ``.exe`` binaries (rust-analyzer/gopls/csharp-ls). Once the gate passes
    (a managed Windows ``.cmd``/``.bat`` shim), any failure to resolve the trusted node
    runtime or JS entrypoint RAISES (fail closed) — it never returns ``None``, so it can
    never silently drop back to the vulnerable shim.
    """
    if not command or not is_windows():
        return None
    if _command_source(command, root) != "managed":
        return None
    if Path(command[0]).suffix.lower() not in {".cmd", ".bat"}:
        return None
    node_exe = _node_executable(root)
    if not node_exe.is_file():
        raise FileNotFoundError(f"managed node runtime missing: {node_exe}")
    js_entry = managed_provider_js_entrypoint(Path(command[0]).stem, root)
    return [str(node_exe), str(js_entry), *command[1:]]


def _run_checked(command: list[str], *, cwd: Path | None = None) -> None:
    command = wrap_windows_batch_command(command)
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
            # --ignore-scripts blocks dependency lifecycle scripts (pre/postinstall) AND node-gyp
            # binding.gyp execution — the #1 npm code-exec vector (the 2026 node-gyp worm). The
            # managed providers (pyright / typescript-language-server / intelephense) are pure JS
            # with no native build step, so disabling scripts is safe and needs no selective rebuild.
            "--ignore-scripts",
            *list(_NODE_PACKAGE_SPECS),
        ],
        cwd=_node_packages_dir(root),
    )
    if not all(binary.is_file() for binary in required_binaries):
        raise RuntimeError("Managed Node package install completed without expected LSP binaries")


def _mark_executable(path: Path) -> None:
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _rust_analyzer_artifact_name() -> tuple[str, str]:
    """Return (artifact_filename, platform_key) for the current OS/arch."""
    system = sys_platform()
    machine = _normalized_machine()
    if system == "windows" and machine == "x86_64":
        # Windows ships a .zip (containing rust-analyzer.exe), not a .gz like the Unix targets.
        return "rust-analyzer-x86_64-pc-windows-msvc.zip", "windows/x86_64"
    if system == "linux" and machine == "x86_64":
        return "rust-analyzer-x86_64-unknown-linux-gnu.gz", "linux/x86_64"
    if system == "linux" and machine == "arm64":
        return "rust-analyzer-aarch64-unknown-linux-gnu.gz", "linux/arm64"
    if system == "darwin" and machine == "x86_64":
        return "rust-analyzer-x86_64-apple-darwin.gz", "darwin/x86_64"
    if system == "darwin" and machine == "arm64":
        return "rust-analyzer-aarch64-apple-darwin.gz", "darwin/arm64"
    raise RuntimeError(f"Unsupported platform for rust-analyzer: {system}/{machine}")


def _rust_analyzer_download_url() -> str:
    # audit S5: use a pinned tag instead of "latest" to prevent MITM/supply-chain
    # attacks that redirect the installer to a different binary via tag mutation.
    artifact, _ = _rust_analyzer_artifact_name()
    return (
        f"https://github.com/rust-lang/rust-analyzer/releases/download/"
        f"{_RUST_ANALYZER_VERSION}/{artifact}"
    )


def _verify_rust_analyzer_checksum(archive_path: Path) -> None:
    """Fail-closed: verify SHA-256 of the downloaded rust-analyzer archive BEFORE decompressing
    (audit S5). Raises on a missing pin or a checksum mismatch, unless the explicit
    TG_ALLOW_UNVERIFIED_TOOLCHAIN opt-out is set.
    """
    if _allow_unverified_toolchain():
        import warnings

        warnings.warn(
            "TG_ALLOW_UNVERIFIED_TOOLCHAIN set; skipping rust-analyzer checksum verification",
            stacklevel=2,
        )
        return
    _, platform_key = _rust_analyzer_artifact_name()
    expected = _RUST_ANALYZER_SHA256.get(platform_key, "")
    if not expected:
        raise RuntimeError(
            f"No pinned SHA-256 for rust-analyzer {platform_key}; refusing to install an "
            "unverified binary (set TG_ALLOW_UNVERIFIED_TOOLCHAIN=1 to override)."
        )
    digest = _sha256_file(archive_path)
    if digest.lower() != expected.lower():
        raise RuntimeError(
            f"rust-analyzer archive checksum mismatch for {platform_key}: "
            f"expected {expected}, got {digest}"
        )


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


def _extract_rust_analyzer_exe_from_zip(archive_path: Path, destination: Path) -> None:
    # Windows rust-analyzer ships as a .zip with rust-analyzer.exe (+ a .pdb we ignore). Extract
    # ONLY the single top-level .exe member, matched by basename (never a path with separators),
    # so a malicious zip cannot traverse outside the destination.
    with zipfile.ZipFile(archive_path) as bundle:
        exe_members = [
            name
            for name in bundle.namelist()
            if name.lower().endswith(".exe") and "/" not in name and "\\" not in name
        ]
        if not exe_members:
            raise RuntimeError(f"rust-analyzer archive {archive_path.name} has no .exe member")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with bundle.open(exe_members[0]) as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)


def _download_rust_analyzer(destination: Path) -> None:
    artifact, _ = _rust_analyzer_artifact_name()
    url = _rust_analyzer_download_url()
    with tempfile.TemporaryDirectory(prefix="tg-rust-analyzer-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        archive_path = temp_dir / artifact
        _download(url, archive_path)
        # audit S5: verify checksum before decompressing/executing the binary.
        _verify_rust_analyzer_checksum(archive_path)
        if artifact.endswith(".zip"):
            _extract_rust_analyzer_exe_from_zip(archive_path, destination)
        else:
            with gzip.open(archive_path, "rb") as compressed, destination.open("wb") as output:
                shutil.copyfileobj(compressed, output)
    if not is_windows():
        _mark_executable(destination)


def _ensure_rust_analyzer(root: Path) -> Path:
    destination = _managed_bin_binary(root, "rust-analyzer")
    if destination.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Default to tg's own pinned, checksum-verified download. Only trust the user's rustup/PATH
    # rust-analyzer under the explicit unsafe opt-in: `_copy_rust_analyzer_from_rustup` resolves the
    # binary via `shutil.which`, which a shadowed/stale rust-analyzer on PATH could hijack into
    # becoming the "managed" provider, bypassing the pin.
    if not (_allow_unverified_toolchain() and _copy_rust_analyzer_from_rustup(destination)):
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
    # Only accept a pre-existing PATH binary under the explicit unsafe opt-in: otherwise a stale or
    # shadowed `gopls` on PATH would silently become the "managed" provider, bypassing the version
    # pin. By default, install the pinned (GOSUMDB-verified) version below.
    existing = shutil.which("gopls")
    if existing and _allow_unverified_toolchain():
        return _copy_binary_to_managed(existing, destination)
    go = shutil.which("go")
    if not go:
        raise RuntimeError("Go toolchain not found; unable to install gopls")
    _run_checked([go, "install", f"golang.org/x/tools/gopls@{_GOPLS_VERSION}"])
    built = _find_go_binary_name(root, "gopls")
    if built is None:
        raise RuntimeError("Go install completed without a discoverable gopls binary")
    return _copy_binary_to_managed(str(built), destination)


def _ensure_csharp_ls(root: Path) -> Path:
    destination = _managed_bin_binary(root, "csharp-ls")
    if destination.is_file():
        return destination
    # Only accept a pre-existing PATH binary under the explicit unsafe opt-in (see _ensure_gopls):
    # by default install the pinned, NuGet-verified version below.
    existing = shutil.which("csharp-ls")
    if existing and _allow_unverified_toolchain():
        return _copy_binary_to_managed(existing, destination)
    dotnet = shutil.which("dotnet")
    if not dotnet:
        raise RuntimeError("dotnet not found; unable to install csharp-ls")
    tool_dir = _managed_bin_dir(root)
    tool_dir.mkdir(parents=True, exist_ok=True)
    install_cmd = [
        dotnet,
        "tool",
        "install",
        "--tool-path",
        str(tool_dir),
        "--version",
        _CSHARP_LS_VERSION,
        "csharp-ls",
    ]
    completed = subprocess.run(install_cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        if "already installed" in completed.stderr.lower():
            # A different version may already be on the tool-path; converge it to the pinned
            # version rather than silently accepting whatever happens to be installed.
            _run_checked([
                dotnet,
                "tool",
                "update",
                "--tool-path",
                str(tool_dir),
                "--version",
                _CSHARP_LS_VERSION,
                "csharp-ls",
            ])
        else:
            raise RuntimeError(f"dotnet tool install csharp-ls failed: {completed.stderr.strip()}")
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
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
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
