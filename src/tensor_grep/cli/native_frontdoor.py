"""The managed native front door: PyPI version discovery, asset install, Windows launcher repair.

Split out of `cli/main.py` (see `src/tensor_grep/cli/_main_binding.py`). Everything
`tg upgrade` / `tg repair-launcher` need in order to put a verified native `tg` binary on PATH
and keep a stale Python-Scripts launcher from shadowing it: PyPI candidate-version discovery,
checksum-gated asset download, the managed metadata file, the Windows `tg.exe` COM-bridge and
Scripts-launcher scans, the user-PATH reordering, and the scheduled background refresh.

`_self` is `cli/main.py`'s module object, imported from `cli/_main_binding`. Every reference
here to a symbol that still lives in `main.py` goes through it -- `_self.subprocess` (patched
on `main` to stub external processes), `_self._MAX_NATIVE_ASSET_DOWNLOAD_BYTES` (patched to a
small value to exercise the download cap), and the `_doctor_*` launcher helpers that now live
in `cli/doctor_report.py` and are re-exported by `main`. Read `cli/_main_binding`'s docstring
before adding a bare cross-module reference.
"""

import json
import logging
import os
import re
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import typer

from tensor_grep.cli._index_lock import atomic_write_bytes_anchored
from tensor_grep.cli._main_binding import _self as _self
from tensor_grep.cli.freshness_process import capture_probe as _capture_freshness_probe
from tensor_grep.cli.managed_runtime_selection import ManagedRuntimeSelection, encode_selection
from tensor_grep.cli.runtime_paths import native_frontdoor_metadata_path
from tensor_grep.cli.subprocess_policy import decode_diagnostic_output, decode_protocol_output
from tensor_grep.cli.windows_parent_process import (
    _canonical_windows_path,
    _open_native_parent_handle,
)
from tensor_grep.io import windows_publication

_PYPI_JSON_URL = "https://pypi.org/pypi/tensor-grep/json"
_LOGGER = logging.getLogger(__name__)
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
    # P0-5 (GPU Phase-0 honesty): defaulted so the single construction site below and any
    # future caller stay valid without threading these through everywhere.
    requested_flavor: str = "cpu"
    downgrade_reason: str | None = None


@dataclass(frozen=True)
class ManagedWindowsRuntimeInstall:
    generation: str
    version: str
    install_method: str
    native_flavor: str
    activation_pending: bool = False
    activation_log: Path | None = None


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
    import html

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
    from tensor_grep.cli.subprocess_policy import decode_protocol_output

    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    try:
        stdout, stderr = _capture_freshness_probe(
            [
                sys.executable,
                "-I",
                "-X",
                "utf8",
                "-m",
                "pip",
                "index",
                "versions",
                "tensor-grep",
                "--no-cache-dir",
                "--index-url",
                "https://pypi.org/simple",
            ],
            timeout_seconds=timeout_seconds,
            env=env,
        )
        # Both streams are parsed for versions, so neither is merely diagnostic text.
        output = "\n".join(decode_protocol_output(part) for part in (stdout, stderr))
    except Exception:
        _LOGGER.debug("doctor freshness pip-index probe failed", exc_info=True)
        return []
    return _candidate_versions_from_pip_index_output(output)


def _candidate_versions_from_pypi_indices(
    timeout_seconds: float, headers: dict[str, str]
) -> list[str]:
    # urllib's socket timeout cannot bound trickling headers/body or DNS. Keep
    # those operations in one killable child, with incremental evidence so a
    # completed surface survives a later stall. No reader threads are needed.
    request = {
        "timeout_seconds": timeout_seconds,
        "urls": [_PYPI_JSON_URL, _PYPI_SIMPLE_URL],
        "headers": headers,
    }
    output: bytes | str | None = None
    try:
        output, _ = _capture_freshness_probe(
            [
                sys.executable,
                "-I",
                "-X",
                "utf8",
                "-c",
                "import runpy,sys; sys.path.insert(0, sys.argv.pop(1)); "
                "runpy.run_module('tensor_grep.cli.pypi_probe', run_name='__main__')",
                str(Path(__file__).resolve().parents[2]),
                json.dumps(request),
            ],
            timeout_seconds=timeout_seconds,
        )
    except OSError:
        _LOGGER.debug("doctor freshness HTTP worker failed", exc_info=True)
    candidates: list[str] = []
    # The child emits at most two short records. Reject unexpected protocol
    # volume, and ignore an incomplete final record left by deadline cleanup.
    if output is None or len(output) > 8192:
        return candidates
    for line in output.splitlines():
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                continue
            _LOGGER.debug("doctor freshness HTTP stage: %s", event)
            version = event.get("version")
            if isinstance(version, str) and len(version) <= 64:
                candidates.append(version)
        except (ValueError, UnicodeDecodeError):
            continue
    return candidates


def _latest_pypi_tensor_grep_version(timeout_seconds: float = 15.0) -> str | None:
    """Best-effort latest-version probe that avoids trusting one stale PyPI cache surface.

    `TG_DOCTOR_OFFLINE=1` short-circuits to None (a documented escape hatch: forces doctor's
    freshness signal to 'unknown_pypi' instead of making a network call — used by tests and
    genuinely-offline runs; it is NOT a silent 'clean', it disables the probe)."""
    if os.environ.get("TG_DOCTOR_OFFLINE") == "1":
        return None
    # All freshness surfaces share one monotonic budget, rather than each
    # starting its own 15-second wait. Preserve the best version observed even
    # when a subsequent surface uses the rest of the budget.
    deadline = time.monotonic() + timeout_seconds
    candidates: list[str] = []
    headers = {
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": f"tensor-grep/{_self._cli_package_version()}",
    }

    remaining = deadline - time.monotonic()
    if remaining > 0:
        candidates.extend(_candidate_versions_from_pypi_indices(remaining, headers))

    started = time.monotonic()
    remaining = deadline - started
    if remaining > 0:
        candidates.extend(_candidate_versions_from_pip_index(remaining))
        _LOGGER.debug("doctor freshness pip-index took %.3fs", time.monotonic() - started)
    else:
        _LOGGER.debug("doctor freshness shared budget exhausted")

    return _highest_tensor_grep_version(candidates)


def _verify_target_python_tensor_grep_version(python_executable: str) -> str:
    probe_code = (
        "import importlib.metadata as m; import tensor_grep; print(m.version('tensor-grep'))"
    )
    try:
        result = _self.subprocess.run(
            [python_executable, "-c", probe_code],
            capture_output=True,
            text=False,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"post-upgrade verification failed: {exc}") from exc
    except _self.subprocess.CalledProcessError as exc:
        stderr = decode_diagnostic_output(exc.stderr).strip()
        stdout = decode_diagnostic_output(exc.stdout).strip()
        combined = stderr or stdout or str(exc)
        raise RuntimeError(f"post-upgrade verification failed: {combined}") from exc

    try:
        version = decode_protocol_output(result.stdout).strip().splitlines()
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            "post-upgrade verification returned invalid UTF-8 version output"
        ) from exc
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


def _download_native_frontdoor_asset(url: str, destination: Path) -> None:
    import urllib.request

    # Claim the temp name ATOMICALLY as a regular file, then stream the WHOLE transfer through
    # that SAME held fd -- mirroring lsp_provider_setup._download, which documents why: `urlretrieve`
    # (the prior implementation here) opens its target with a plain 'wb' AFTER the O_EXCL claim's fd
    # is closed, reopening the path BY NAME. The O_EXCL claim only guarantees a fresh regular file at
    # claim time; between that close and urlretrieve's later by-name reopen, the destination can be
    # replaced with a symlink, and urlretrieve then writes THROUGH it. The payload here is a native
    # EXECUTABLE the front door later runs, so that close-then-reopen gap is a real hole, not
    # defence-in-depth. Streaming through the held fd (os.fdopen, never destination.open()/a second
    # os.open()) removes the reopen entirely -- there is no name lookup after the O_EXCL claim, so no
    # window for a symlink swap to matter.
    #
    # O_EXCL fails if ANYTHING already exists at the path (symlink, hard link, or regular file)
    # instead of writing through it. Callers pass a `{name}.{uuid4().hex}` temp path, so an attacker
    # cannot pre-plant a symlink at a name they can predict; O_NOFOLLOW additionally refuses to open
    # through a symlink even in the (unexpected) case of a name collision. Mode is passed to os.open
    # rather than chmod-ed afterwards, so there is no window where the file exists with wider
    # permissions.
    #
    # A collision on a uuid4 name is not expected; refusing (rather than truncating) is the honest
    # response, and it keeps the guard from degrading into "symlinks only", which a hard link would
    # sidestep. Found by the #859 atomic-writer ratchet; TOCTOU close-then-reopen gap closed as one
    # of that ratchet's two H2 deferrals.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, 0o600)
    # Wrap the fd in a file object IMMEDIATELY, before anything that can raise (urlopen included)
    # -- `os.fdopen` itself essentially cannot fail here (a fresh, valid fd), so this keeps `fd`
    # from ever being a bare, unclosed integer if a later step (network open, cap check) raises.
    # An earlier draft evaluated `urlopen(...)` as part of the SAME `with A() as a, B() as b:`
    # statement as `os.fdopen(fd, ...)`; when `urlopen` raised during that expression's own
    # evaluation, `os.fdopen(fd, ...)` was never reached, leaking `fd` -- and the except handler's
    # `destination.unlink()` then hit a real second bug on Windows, which opens files without
    # `FILE_SHARE_DELETE` by default: unlinking a path with a live, unclosed fd against it raises
    # `PermissionError: [WinError 32] ... used by another process`, masking the original error
    # instead of cleaning up. Opening `output` first and nesting `with output:` around the urlopen
    # call means ANY failure inside -- including urlopen's own call -- closes `output` (and so
    # `fd`) via normal `with`-block unwinding before the outer `except` ever runs, so the unlink
    # below always sees a released fd.
    output = os.fdopen(fd, "wb")

    # urlopen's timeout param replaces the previous process-global socket.setdefaulttimeout(60):
    # equivalent 60s bound on the request, and this function is the only caller that touched the
    # global default, so there is nothing left relying on it being set.
    total = 0
    try:
        with output, urllib.request.urlopen(url, timeout=60) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                # Byte cap enforced on ACTUAL bytes read, same as the prior reporthook-based check
                # (audit #5) -- Content-Length is attacker-controlled, so we count real bytes.
                if total > _self._MAX_NATIVE_ASSET_DOWNLOAD_BYTES:
                    raise RuntimeError(
                        f"Native asset download exceeded {_self._MAX_NATIVE_ASSET_DOWNLOAD_BYTES} bytes "
                        f"(possible oversized or malicious response): {url}"
                    )
                output.write(chunk)
    except BaseException:
        destination.unlink(missing_ok=True)  # don't leave a partial/unsafe temp behind
        raise


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

    expected = _self._expected_asset_sha256(checksums_text, asset_name)
    if not expected:
        return f"no published checksum for {asset_name}; refusing to trust the download"
    actual = hashlib.sha256(asset_path.read_bytes()).hexdigest().lower()
    if actual != expected:
        return f"checksum mismatch for {asset_name} (expected {expected}, got {actual})"
    return None


def _native_frontdoor_download_error_for_flavor(
    download_errors: list[str], flavor: str
) -> str | None:
    """First download_errors entry attributable to `flavor` (each entry is formatted as
    "<flavor> asset ...", see _install_release_native_frontdoor), or None if no candidate of
    that flavor was ever attempted. SF-1: a platform with no NVIDIA asset at all never
    appends an nvidia candidate to the download loop, so download_errors has no
    nvidia-flavored entry -- callers must not index into it blindly in that case.
    """
    prefix = f"{flavor} asset"
    for error in download_errors:
        if error.startswith(prefix):
            return error
    return None


def _native_frontdoor_downgrade_reason(
    *, requested_flavor: str, installed_flavor: str, download_errors: list[str]
) -> str | None:
    """Honest reason the installed native-frontdoor flavor differs from what was requested
    (TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR), or None when they already agree -- including the
    default cpu-request path, which must stay silent (requested cpu always installs cpu).
    """
    if installed_flavor == requested_flavor:
        return None
    reason = _native_frontdoor_download_error_for_flavor(download_errors, requested_flavor)
    if reason is not None:
        return reason
    # SF-1: no candidate of the requested flavor was ever attempted for this platform (e.g.
    # darwin/non-amd64, where _native_frontdoor_asset_candidates never appends an nvidia
    # entry) -- state that honestly rather than fabricating a reason nothing recorded.
    return f"no {requested_flavor.upper()} asset is published for this platform"


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
    # H2 (#859 class ratchet): route through the shared anchored helper rather than a raw
    # write_text -- the metadata sidecar path is derived from `destination`, a fixed/predictable
    # install location (unlike the uuid4-suffixed temp paths elsewhere in this module), so a
    # pre-planted symlink there is a real target. atomic_write_bytes_anchored refuses to write
    # through a symlinked destination and publishes via a same-directory O_EXCL temp + os.replace.
    atomic_write_bytes_anchored(
        native_frontdoor_metadata_path(destination),
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _install_release_native_frontdoor(
    version: str, destination: Path, *, verify_destination: bool = True
) -> _NativeFrontdoorInstallResult:
    candidates = _self._native_frontdoor_download_candidates(version)
    if not candidates:
        raise RuntimeError("no release-native front-door asset is available for this platform")

    # Audit HIGH (2026-06-24): verify every downloaded asset against the published
    # CHECKSUMS.txt BEFORE installing/executing it, matching the fail-closed posture
    # of the installers (scripts/install.sh, install.ps1, npm/install.js). Without
    # the manifest nothing can be verified, so refuse rather than trust the download.
    try:
        checksums_text = _self._fetch_native_frontdoor_checksums(version)
    except Exception as exc:
        # W1-b MEDIUM hardening (2026-08-20): the real `_fetch_native_frontdoor_checksums`
        # never raises (it catches internally and returns None), but a caller-supplied
        # override (test injection, a future refactor) might. Refuse and disclose the
        # underlying cause rather than letting it escape unwrapped or fall through silently.
        raise RuntimeError(
            "release-native front-door asset install refused: fetching CHECKSUMS.txt for "
            f"v{version} raised {exc}; refusing to install an unverified native binary"
        ) from exc
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
                _self._download_native_frontdoor_asset(url, temp_path)
            except Exception as exc:
                download_errors.append(f"{candidate.flavor} asset unavailable: {exc}")
                continue
            checksum_error = _self._native_frontdoor_checksum_error(
                temp_path, candidate.asset_name, checksums_text
            )
            if checksum_error is not None:
                download_errors.append(f"{candidate.flavor} asset {checksum_error}")
                continue
            if not sys.platform.startswith("win"):
                temp_path.chmod(0o755)
            temp_version = _self._native_tg_version(temp_path)
            if not _self._native_tg_version_matches(version, temp_version):
                download_errors.append(
                    f"{candidate.flavor} asset failed smoke test: downloaded native tg "
                    f"front door reported {temp_version or 'no version'} instead of {version}"
                )
                continue
            previous_bytes = destination.read_bytes() if destination.exists() else None
            # H2 (#859 class ratchet): the raw os.replace/write_bytes pair here was a hand-rolled
            # duplicate of atomic_write_bytes_anchored's own create-temp/replace flow, operating
            # on a SINGLE FILE (unlike lsp_provider_setup._ensure_node_runtime's directory-tree
            # swap, which has no shared-helper equivalent to route through). temp_path itself was
            # already claimed via the O_EXCL guard in _download_native_frontdoor_asset, so reading
            # its bytes here and publishing through the shared helper closes the destination-
            # symlink gap the raw os.replace left open (os.replace alone is non-dereferencing, but
            # routing through the helper keeps this call site consistent with the reviewed pattern
            # and gives the destination its own explicit symlink-refusal check).
            install_mode = None if sys.platform.startswith("win") else 0o755
            atomic_write_bytes_anchored(destination, temp_path.read_bytes(), mode=install_mode)
            if verify_destination:
                installed_version = _self._native_tg_version(destination)
                if not _self._native_tg_version_matches(version, installed_version):
                    if previous_bytes is not None:
                        atomic_write_bytes_anchored(destination, previous_bytes, mode=install_mode)
                    else:
                        destination.unlink(missing_ok=True)
                    download_errors.append(
                        f"{candidate.flavor} asset failed install verification: installed native "
                        f"tg front door reported {installed_version or 'no version'} instead of "
                        f"{version}"
                    )
                    continue
            else:
                checksum_error = _self._native_frontdoor_checksum_error(
                    destination, candidate.asset_name, checksums_text
                )
                if checksum_error is not None:
                    if previous_bytes is not None:
                        atomic_write_bytes_anchored(destination, previous_bytes, mode=install_mode)
                    else:
                        destination.unlink(missing_ok=True)
                    download_errors.append(
                        f"{candidate.flavor} asset failed install checksum verification: "
                        f"{checksum_error}"
                    )
                    continue
            _self._write_native_frontdoor_metadata(
                destination,
                version=version,
                candidate=candidate,
            )
            # P0-5 (GPU Phase-0 honesty): a requested nvidia flavor that silently
            # lands cpu (nvidia unavailable, or no nvidia asset exists for this platform at
            # all) used to be indistinguishable from an ordinary cpu install. Warn loudly on
            # stderr so a caller sees the downgrade instead of only discovering it later via
            # `tg doctor`. Silent on the default cpu-request path (requested cpu always
            # installs cpu, so this never fires there) and never pollutes any --json stream
            # (stderr only; `tg upgrade` has no --json output).
            requested_flavor = _requested_native_frontdoor_flavor()
            downgrade_reason = _native_frontdoor_downgrade_reason(
                requested_flavor=requested_flavor,
                installed_flavor=candidate.flavor,
                download_errors=download_errors,
            )
            if downgrade_reason is not None:
                # Gate-nit C (#172): the house idiom is typer.echo(..., err=True), not a raw
                # print(..., file=sys.stderr) -- functionally equivalent, ASCII-safe.
                typer.echo(
                    f"WARNING: requested native tg front-door flavor '{requested_flavor}' "
                    f"was not installed ({downgrade_reason}); installed '{candidate.flavor}' "
                    "instead. Run 'tg doctor' to check the requested-vs-installed native "
                    "flavor.",
                    err=True,
                )
            return _NativeFrontdoorInstallResult(
                url=url,
                flavor=candidate.flavor,
                asset_name=candidate.asset_name,
                requested_flavor=requested_flavor,
                downgrade_reason=downgrade_reason,
            )
        finally:
            temp_path.unlink(missing_ok=True)

    raise RuntimeError(
        "release-native front-door asset install failed: " + "; ".join(download_errors)
    )


def managed_windows_install_root(executable: str) -> Path | None:
    """Return the managed root after validating its profile, ancestry, and opened handles."""
    if not sys.platform.startswith("win"):
        return None
    executable_path = Path(executable).expanduser().absolute()
    scripts = executable_path.parent
    venv = scripts.parent
    if scripts.name.casefold() != "scripts" or venv.name.casefold() != ".venv":
        return None
    if executable_path.name.casefold() != "python.exe":
        return None
    parent = venv.parent
    if parent.name.casefold() == ".tensor-grep":
        root = parent
    elif (
        re.fullmatch(r"[0-9a-f]{32}", parent.name) is not None
        and parent.parent.name.casefold() == "runtimes"
        and parent.parent.parent.name.casefold() == ".tensor-grep"
    ):
        root = parent.parent.parent
    else:
        return None
    from tensor_grep.cli import session_daemon_trust as trust
    from tensor_grep.cli.managed_runtime_candidate import _windows_profile_directory

    authority = (_windows_profile_directory() / ".tensor-grep").absolute()

    def path_key(value: Path) -> str:
        return os.path.normcase(os.path.normpath(os.fspath(value)))

    if path_key(root) != path_key(authority):
        raise RuntimeError("managed Windows runtime is outside the OS-verified user profile")
    if not trust._ancestors_trusted(authority):
        raise RuntimeError("managed Windows runtime root or ancestry is not trusted")
    with ExitStack() as held:
        _pin_managed_directory(authority, held)
    return authority


def selected_managed_windows_version(install_root: Path) -> str | None:
    try:
        from tensor_grep.cli.managed_runtime_reader import read_managed_runtime

        managed = read_managed_runtime(trusted_root=install_root)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"managed runtime selector cannot be trusted: {exc}") from exc
    if managed is None:
        return None
    generation_root = install_root / "runtimes" / managed.selection.generation
    try:
        with ExitStack() as held:
            _pin_managed_directory(install_root, held)
            _pin_managed_directory(install_root / "runtimes", held)
            _pin_managed_directory(generation_root, held)
            _pin_managed_generation_executables(generation_root, held)
    except (OSError, RuntimeError):
        raise RuntimeError(
            "managed runtime selector points to an incomplete or untrusted generation"
        ) from None
    return managed.selection.version


def _pin_managed_directory(path: Path, held: ExitStack) -> str:
    """Retain each directory handle while checking its final path and ACL."""
    from tensor_grep.cli import managed_runtime_reader as reader
    from tensor_grep.cli import session_daemon_trust as trust
    from tensor_grep.cli import session_daemon_winsec as winsec

    if (
        not path.is_absolute()
        or len(path.drive) != 2
        or path.drive[1] != ":"
        or len(path.parts) < 2
    ):
        raise RuntimeError("managed runtime directory must be below a local drive root")
    current = Path(path.anchor)
    drive_handle = winsec.open_no_follow(str(current), directory=True, share=3)
    if drive_handle is None:
        raise RuntimeError("managed runtime drive root is missing or unsafe")
    held.callback(winsec.close_handle, drive_handle)
    parent_final = winsec.final_path(drive_handle)
    if reader._final_key(parent_final) != reader._final_key(str(current)):
        raise RuntimeError("managed runtime drive root identity changed")
    for component in path.parts[1:]:
        current = current / component
        handle = winsec.open_no_follow(str(current), directory=True, share=3)
        if handle is None:
            raise RuntimeError(f"managed runtime directory is missing or unsafe: {current}")
        held.callback(winsec.close_handle, handle)
        final = winsec.final_path(handle)
        if parent_final is not None and not reader._is_direct_child(final, parent_final, component):
            raise RuntimeError("managed runtime directory identity changed during validation")
        if current == path:
            if not trust._parent_handle_ok(handle):
                raise RuntimeError("managed runtime directory ACL is not trusted")
        else:
            details = winsec.owner_and_dacl_entries(handle)
            sid = trust._win_current_user_sid()
            if details is None or sid is None or not trust._windows_owner_ok(details[0], sid):
                raise RuntimeError("managed runtime ancestor owner is not trusted")
            if not trust._windows_ancestor_dacl_ok(details[1], sid):
                raise RuntimeError("managed runtime ancestor ACL is not trusted")
        parent_final = final
    if parent_final is None:
        raise RuntimeError("managed runtime directory final path is unavailable")
    return parent_final


def _validate_managed_leaf_handle(handle: int, parent_final: str, name: str) -> None:
    from tensor_grep.cli import managed_runtime_reader as reader
    from tensor_grep.cli import session_daemon_trust as trust
    from tensor_grep.cli import session_daemon_winsec as winsec

    if not trust._windows_handle_trusted(handle, check_dacl=True):
        raise RuntimeError(f"managed runtime executable ACL is not trusted: {name}")
    if winsec.link_count(handle) != 1:
        raise RuntimeError(f"managed runtime executable must have one hard link: {name}")
    if not reader._is_direct_child(winsec.final_path(handle), parent_final, name):
        raise RuntimeError(f"managed runtime executable is outside its pinned directory: {name}")


def _pin_managed_executable(path: Path, held: ExitStack) -> None:
    from tensor_grep.cli import session_daemon_winsec as winsec

    parent_final = _pin_managed_directory(path.parent, held)
    handle = winsec.open_no_follow(str(path), share=1)
    if handle is None:
        raise RuntimeError(f"managed runtime executable is missing or unsafe: {path.name}")
    try:
        _validate_managed_leaf_handle(handle, parent_final, path.name)
    except BaseException:
        winsec.close_handle(handle)
        raise
    held.callback(winsec.close_handle, handle)


def _pin_managed_generation_executables(generation_root: Path, held: ExitStack) -> None:
    _pin_managed_directory(generation_root / ".venv", held)
    _pin_managed_executable(generation_root / "bin" / "tg.exe", held)
    _pin_managed_executable(generation_root / ".venv" / "Scripts" / "python.exe", held)


def _validate_managed_publication_descriptor(descriptor: int, parent_final: str, name: str) -> None:
    import msvcrt

    descriptor_handle = msvcrt.get_osfhandle(descriptor)  # type: ignore[attr-defined]
    _validate_managed_leaf_handle(descriptor_handle, parent_final, name)


def _publish_managed_generation(
    install_root: Path,
    generation: str,
    version: str,
    held: ExitStack,
    publication_attempted: list[bool] | None = None,
) -> None:
    """Publish one verified generation; the caller holds the selector lock and generation pins."""
    selector_path = install_root / "runtime-selection.json"
    selector_bytes = encode_selection(ManagedRuntimeSelection(generation, version))
    selected_version = selected_managed_windows_version(install_root)
    if selected_version is not None and _self._is_version_newer(selected_version, version):
        raise RuntimeError(
            f"managed runtime selector advanced to {selected_version}; "
            f"refusing to activate older candidate {version}"
        )

    parent_final = _pin_managed_directory(install_root, held)
    temporary_path = selector_path.with_name(f"{selector_path.name}.{uuid4().hex}.tmp")
    descriptor = None
    replacement_attempted = False
    try:
        descriptor = windows_publication.open_temporary(temporary_path)
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(selector_bytes)
            output.flush()
            os.fsync(output.fileno())
        _validate_managed_publication_descriptor(descriptor, parent_final, temporary_path.name)
        replacement_attempted = True
        if publication_attempted is not None:
            publication_attempted[0] = True
        windows_publication.replace_open(descriptor, selector_path)
        os.close(descriptor)
        descriptor = None
        if selected_managed_windows_version(install_root) != version:
            raise RuntimeError("managed selector read-back failed")
        from tensor_grep.cli.managed_runtime_reader import read_managed_runtime

        resolved = read_managed_runtime(trusted_root=install_root)
        if resolved is None or resolved.selection.generation != generation:
            raise RuntimeError("stable dispatcher did not resolve the activated generation")
    finally:
        if descriptor is not None:
            discard_temporary = not replacement_attempted
            if replacement_attempted:
                try:
                    import msvcrt

                    from tensor_grep.cli import managed_runtime_reader as reader
                    from tensor_grep.cli import session_daemon_winsec as winsec

                    discard_temporary = reader._is_direct_child(
                        winsec.final_path(
                            msvcrt.get_osfhandle(descriptor)  # type: ignore[attr-defined]
                        ),
                        parent_final,
                        temporary_path.name,
                    )
                except OSError:
                    pass
            if discard_temporary:
                try:
                    windows_publication.discard_open(descriptor)
                except OSError:
                    pass
            os.close(descriptor)


def _create_activation_ready_event() -> int:
    import ctypes
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel.CreateEventW.argtypes = [
        ctypes.POINTER(SecurityAttributes),
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel.CreateEventW.restype = wintypes.HANDLE
    handle = kernel.CreateEventW(ctypes.byref(attributes), True, False, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    return int(handle)


def _signal_activation_ready(ready_handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel.SetEvent.argtypes = [wintypes.HANDLE]
    kernel.SetEvent.restype = wintypes.BOOL
    if not kernel.SetEvent(ready_handle):
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]


class _ManagedActivationUnsettledError(RuntimeError):
    pass


_MANAGED_ACTIVATION_WORKER_MAY_LIVE = "_managed_activation_worker_may_live"
_ACTIVATION_WORKER_PROGRAM = (
    "import sys; from pathlib import Path; "
    "from tensor_grep.cli.native_frontdoor import _activate_managed_generation_after_parent; "
    "raise SystemExit(_activate_managed_generation_after_parent("
    "int(sys.argv[1]),int(sys.argv[2]),Path(sys.argv[3]),sys.argv[4],sys.argv[5]))"
)


def _schedule_managed_activation(
    candidate_python: Path,
    install_root: Path,
    generation: str,
    expected_version: str,
    parent_handle: int,
    held: ExitStack,
) -> Path:
    """Start an isolated helper and return only after its pinned preflight is ready."""
    import ctypes
    import subprocess
    from ctypes import wintypes

    from tensor_grep.cli import session_daemon_winsec as winsec

    ready_handle: int | None = None
    process_handle = None
    child = None
    kernel = None
    status_path = install_root / "logs" / f"activation-{generation}.log"
    try:
        encode_selection(ManagedRuntimeSelection(generation, expected_version))
        generation_root = install_root / "runtimes" / generation
        expected_python = generation_root / ".venv" / "Scripts" / "python.exe"
        if _canonical_windows_path(candidate_python) != _canonical_windows_path(expected_python):
            raise RuntimeError("activation Python does not match the selected generation")
        _pin_managed_directory(install_root, held)
        _pin_managed_directory(install_root / "runtimes", held)
        _pin_managed_generation_executables(generation_root, held)

        ready_handle = _create_activation_ready_event()
        startup = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
        startup.lpAttributeList = {"handle_list": [parent_handle, ready_handle]}
        flags = 0
        for flag in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
            flags |= int(getattr(subprocess, flag, 0))
        child = _self.subprocess.Popen(
            [
                str(expected_python),
                "-I",
                "-c",
                _ACTIVATION_WORKER_PROGRAM,
                str(parent_handle),
                str(ready_handle),
                str(install_root),
                generation,
                expected_version,
            ],
            stdin=_self.subprocess.DEVNULL,
            stdout=_self.subprocess.DEVNULL,
            stderr=_self.subprocess.DEVNULL,
            close_fds=True,
            startupinfo=startup,
            creationflags=flags,
        )
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        kernel.SetHandleInformation.restype = wintypes.BOOL
        for inherited in (parent_handle, ready_handle):
            if not kernel.SetHandleInformation(inherited, 1, 0):
                raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        process_handle = winsec.open_process(child.pid)
        if process_handle is None:
            raise RuntimeError("cannot open activation helper process for readiness wait")
        handles = (wintypes.HANDLE * 2)(ready_handle, process_handle)
        kernel.WaitForMultipleObjects.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel.WaitForMultipleObjects.restype = wintypes.DWORD
        waited = int(kernel.WaitForMultipleObjects(2, handles, False, 30_000))
        if waited == 1:
            raise RuntimeError("activation helper exited before candidate preflight was ready")
        if waited == 258:
            raise TimeoutError("activation helper did not become ready within 30 seconds")
        if waited != 0:
            raise RuntimeError("activation helper readiness wait failed")
        return status_path
    except BaseException as exc:
        settled = child is None
        cleanup_interruption: BaseException | None = None
        try:
            if child is not None and process_handle is not None:
                winsec.terminate_process(process_handle)
                settled = winsec.wait_process(process_handle, 5_000)
            elif child is not None:
                child.terminate()
                child.wait(timeout=5)
                settled = True
        except BaseException as cleanup_exc:
            cleanup_interruption = cleanup_exc
            try:
                if process_handle is not None:
                    settled = winsec.wait_process(process_handle, 5_000)
                elif child is not None:
                    child.wait(timeout=5)
                    settled = True
            except BaseException as retry_exc:
                if not isinstance(retry_exc, Exception):
                    setattr(retry_exc, _MANAGED_ACTIVATION_WORKER_MAY_LIVE, True)
                    raise retry_exc from exc
                settled = child is not None and child.poll() is not None
        if not settled:
            if cleanup_interruption is not None and not isinstance(cleanup_interruption, Exception):
                setattr(cleanup_interruption, _MANAGED_ACTIVATION_WORKER_MAY_LIVE, True)
                raise cleanup_interruption from exc
            setattr(exc, _MANAGED_ACTIVATION_WORKER_MAY_LIVE, True)
            if isinstance(exc, Exception):
                raise _ManagedActivationUnsettledError(
                    "activation helper may still be running; staged generation retained"
                ) from exc
        elif cleanup_interruption is not None and not isinstance(cleanup_interruption, Exception):
            raise cleanup_interruption from exc
        raise
    finally:
        winsec.close_handle(parent_handle)
        if ready_handle is not None:
            winsec.close_handle(ready_handle)
        if process_handle is not None:
            winsec.close_handle(process_handle)


def _stable_dispatcher_matches_release(version: str, stable_path: Path) -> bool:
    """Keep a launcher only when its bytes match a checksum-listed release asset."""
    with ExitStack() as held:
        try:
            _pin_managed_executable(stable_path, held)
        except (OSError, RuntimeError):
            return False
        checksums = _self._fetch_native_frontdoor_checksums(version)
        if checksums is None:
            return False
        return any(
            _self._native_frontdoor_checksum_error(stable_path, candidate.asset_name, checksums)
            is None
            for candidate, _url in _self._native_frontdoor_download_candidates(version)
        )


def _activate_managed_generation_after_parent(
    parent_handle: int,
    ready_handle: int,
    install_root: Path,
    generation: str,
    version: str,
) -> int:
    """Wait for the old launcher, publish selection, then repair its stable dispatcher."""
    import ctypes
    from ctypes import wintypes

    from tensor_grep.cli import session_daemon_winsec as winsec
    from tensor_grep.cli.managed_runtime_candidate import (
        _verify_managed_candidate_python,
        _windows_profile_directory,
    )

    with ExitStack() as resources:
        resources.callback(winsec.close_handle, parent_handle)
        resources.callback(winsec.close_handle, ready_handle)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        status_path: Path | None = None
        selector_attempted = [False]
        with ExitStack() as held:
            try:
                expected_root = (_windows_profile_directory() / ".tensor-grep").absolute()
                if _canonical_windows_path(install_root) != _canonical_windows_path(expected_root):
                    raise RuntimeError(
                        "activation root does not match the OS-verified user profile"
                    )
                if re.fullmatch(r"[0-9a-f]{32}", generation) is None:
                    raise RuntimeError("activation generation is not a valid UUID")
                if (
                    re.fullmatch(
                        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",
                        version,
                    )
                    is None
                ):
                    raise RuntimeError("activation version is not a stable x.y.z version")

                install_root = expected_root
                _pin_managed_directory(install_root, held)
                logs_path = install_root / "logs"
                logs_path.mkdir(exist_ok=True)
                _pin_managed_directory(logs_path, held)
                status_path = logs_path / f"activation-{generation}.log"

                runtimes = install_root / "runtimes"
                _pin_managed_directory(runtimes, held)
                generation_root = runtimes / generation
                _pin_managed_directory(generation_root, held)
                _pin_managed_generation_executables(generation_root, held)
                candidate_python = generation_root / ".venv" / "Scripts" / "python.exe"
                _verify_managed_candidate_python(candidate_python, version)
                _signal_activation_ready(ready_handle)
                if kernel.WaitForSingleObject(parent_handle, 900_000) != 0:
                    raise RuntimeError(
                        "native launcher did not exit before the activation deadline"
                    )

                stable_path = _pin_stable_launcher_directory(install_root, held)
                from tensor_grep.cli._index_lock import index_lock

                with index_lock(install_root / "runtime-selection.json"):
                    _publish_managed_generation(
                        install_root, generation, version, held, selector_attempted
                    )
                    if not _stable_dispatcher_matches_release(version, stable_path):
                        _install_release_native_frontdoor(version, stable_path)
                atomic_write_bytes_anchored(
                    status_path, f"activated {version} generation {generation}\n".encode()
                )
                return 0
            except Exception as exc:
                if status_path is not None:
                    state = (
                        "pending after selector publication attempt"
                        if selector_attempted[0]
                        else "failed"
                    )
                    try:
                        atomic_write_bytes_anchored(
                            status_path, f"activation {state}: {type(exc).__name__}\n".encode()
                        )
                    except OSError:
                        pass
                return 1


def stage_managed_windows_runtime(
    executable: str, package_spec: str, expected_version: str
) -> ManagedWindowsRuntimeInstall:
    """Install a verified generation beside the active runtime, then select it."""
    from tensor_grep.cli import managed_runtime_candidate as candidate
    from tensor_grep.cli._index_lock import index_lock

    install_root = managed_windows_install_root(executable)
    if install_root is None:
        raise RuntimeError("executable is not a Windows managed tensor-grep Python runtime")
    generation = uuid4().hex
    encode_selection(ManagedRuntimeSelection(generation, expected_version))
    generation_root = install_root / "runtimes" / generation
    candidate_python = generation_root / ".venv" / "Scripts" / "python.exe"
    native_path = generation_root / "bin" / "tg.exe"
    selector_path = install_root / "runtime-selection.json"
    held = ExitStack()
    selector_attempted = [False]
    try:
        _pin_managed_directory(install_root, held)
        runtimes_root = install_root / "runtimes"
        runtimes_root.mkdir(exist_ok=True)
        _pin_managed_directory(runtimes_root, held)
        # Failed generations stay unselected; recursive path cleanup races Windows swaps.
        generation_root.mkdir(exist_ok=False)
        _pin_managed_directory(generation_root, held)

        venv_path = candidate_python.parent.parent
        try:
            _self.subprocess.run(
                ["uv", "venv", "--python", str(Path(executable).absolute()), str(venv_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
        except (FileNotFoundError, _self.subprocess.CalledProcessError):
            _self.subprocess.run(
                [str(Path(executable).absolute()), "-m", "venv", str(venv_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )

        normalized_spec = candidate._managed_candidate_package_spec(package_spec)
        expected_cuda = "12.8" if "gpu-win" in normalized_spec else None
        install_method = candidate._run_managed_candidate_install(candidate_python, normalized_spec)
        candidate._verify_managed_candidate_python(
            candidate_python, expected_version, expected_cuda=expected_cuda
        )
        native_result = _install_release_native_frontdoor(
            expected_version, native_path, verify_destination=False
        )
        _pin_managed_generation_executables(generation_root, held)
        stable_path = _pin_stable_launcher_directory(install_root, held)
        parent_handle = _open_native_parent_handle(stable_path)
        if parent_handle is not None:
            status_path = _schedule_managed_activation(
                candidate_python, install_root, generation, expected_version, parent_handle, held
            )
            return ManagedWindowsRuntimeInstall(
                generation,
                expected_version,
                install_method,
                native_result.flavor,
                activation_pending=True,
                activation_log=status_path,
            )

        with index_lock(selector_path):
            _publish_managed_generation(
                install_root, generation, expected_version, held, selector_attempted
            )
            if not _stable_dispatcher_matches_release(expected_version, stable_path):
                _install_release_native_frontdoor(expected_version, stable_path)
        return ManagedWindowsRuntimeInstall(
            generation, expected_version, install_method, native_result.flavor
        )
    except BaseException as exc:
        if isinstance(exc, RuntimeError) or not isinstance(exc, Exception):
            raise
        raise RuntimeError(f"managed runtime staging failed: {exc}") from exc
    finally:
        try:
            held.close()
        except Exception:
            pass


def _pin_stable_launcher_directory(install_root: Path, held: ExitStack) -> Path:
    _pin_managed_directory(install_root, held)
    stable_bin = install_root / "bin"
    stable_bin.mkdir(exist_ok=True)
    _pin_managed_directory(stable_bin, held)
    return stable_bin / "tg.exe"


def _windows_managed_native_bin_dir() -> Path | None:
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        return Path(user_profile).expanduser() / ".tensor-grep" / "bin"
    try:
        return Path.home() / ".tensor-grep" / "bin"
    except RuntimeError:
        return None
