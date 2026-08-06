"""Round-60 RED group 3: System32 identity + offline WinTrust + non-breakaway Job.

Task 2A / #89 Step 1. Platform-neutral policy tests run everywhere. Dedicated
Windows handle/Job/trust nodes are mandatory non-skipped manifest entries; Linux
explicitly skips those integration nodes. No fabricated HANDLE 0/1, no HANDLE-
number equality as identity, no string-only swaps, no fake security verdicts.

These tests MUST fail against unmodified / behaviorless seams. Do not weaken.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import threading
import time
from collections.abc import Iterator
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import pytest

from tensor_grep.cli import _win32_path_domain as win32

_WINDOWS = sys.platform == "win32"

FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
_FILE_SHARE_ALL = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


def _windows_required(fn):
    """Mark dedicated Windows manifest nodes (required_non_skip on Windows)."""
    fn._task2a_windows_required = True  # type: ignore[attr-defined]
    fn._task2a_owned = True  # type: ignore[attr-defined]
    return fn


def task2a_owned(fn):  # type: ignore[no-untyped-def]
    """Ownership marker for closed-world AST census (must match helper name)."""
    fn._task2a_owned = True  # type: ignore[attr-defined]
    return fn


def _require_windows() -> None:
    if not _WINDOWS:
        pytest.skip("Windows integration node; Linux must not fabricate handles")


def _kernel32() -> ctypes.WinDLL:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel32.GetSystemDirectoryW.restype = wintypes.UINT
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _invalid_handle_value() -> int:
    return int(wintypes.HANDLE(-1).value)


@contextlib.contextmanager
def _win32_handle(handle: int) -> Iterator[int]:
    """Context-managed CloseHandle for any test helper that opens handles."""
    try:
        yield handle
    finally:
        if handle not in (0, 1, _invalid_handle_value()):
            _kernel32().CloseHandle(wintypes.HANDLE(handle))


def _open_path_handle(path: str, *, directory: bool) -> int:
    kernel32 = _kernel32()
    flags = FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= FILE_FLAG_BACKUP_SEMANTICS
    handle = kernel32.CreateFileW(
        path,
        GENERIC_READ,
        _FILE_SHARE_ALL,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    invalid = _invalid_handle_value()
    if handle in (invalid, 0, 1):
        raise OSError(f"CreateFileW failed for {path!r}")
    return int(handle)


def _volume_identity(handle: int) -> win32.VolumeFileIdentity:
    kernel32 = _kernel32()
    info = _BY_HANDLE_FILE_INFORMATION()
    if not kernel32.GetFileInformationByHandle(wintypes.HANDLE(handle), ctypes.byref(info)):
        raise OSError("GetFileInformationByHandle failed")
    return win32.VolumeFileIdentity(
        volume_serial=int(info.dwVolumeSerialNumber),
        file_index_high=int(info.nFileIndexHigh),
        file_index_low=int(info.nFileIndexLow),
    )


def _system32_wsl_paths() -> tuple[str, str]:
    kernel32 = _kernel32()
    buf = ctypes.create_unicode_buffer(260)
    if not kernel32.GetSystemDirectoryW(buf, len(buf)):
        raise OSError("GetSystemDirectoryW failed")
    system32 = buf.value
    return system32, str(Path(system32) / "wsl.exe")


@contextlib.contextmanager
def _real_system32_handles() -> Iterator[
    tuple[int, int, win32.VolumeFileIdentity, win32.VolumeFileIdentity]
]:
    """Open real System32 directory + wsl.exe via typed ctypes (test fixture only)."""
    system32, wsl_path = _system32_wsl_paths()
    with _win32_handle(_open_path_handle(system32, directory=True)) as dir_h:
        with _win32_handle(_open_path_handle(wsl_path, directory=False)) as leaf_h:
            yield dir_h, leaf_h, _volume_identity(dir_h), _volume_identity(leaf_h)


@contextlib.contextmanager
def _open_wsl_exe_handle() -> Iterator[int]:
    """Real System32 wsl.exe held handle for catalog/embedded trust probes."""
    _system32, wsl_path = _system32_wsl_paths()
    with _win32_handle(_open_path_handle(wsl_path, directory=False)) as handle:
        yield handle


@contextlib.contextmanager
def _open_temp_unsigned_handle(tmp_path: Path) -> Iterator[int]:
    """Unsigned temp file handle — foreign-chain negative only (never positive trust)."""
    path = tmp_path / "foreign-chain-unsigned.bin"
    path.write_bytes(b"tensor-grep-round60-foreign-chain")
    with _win32_handle(_open_path_handle(str(path), directory=False)) as handle:
        yield handle


# ---------------------------------------------------------------------------
# Platform-neutral policy (no fabricated handles)
# ---------------------------------------------------------------------------


@task2a_owned
def test_offline_wintrust_flags_and_microsoft_root_policy() -> None:
    policy = win32.offline_wintrust_policy()
    expected_prov = (
        win32.WTD_CACHE_ONLY_URL_RETRIEVAL | win32.WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT
    )
    assert policy.dw_ui_choice == win32.WTD_UI_NONE
    assert policy.fdw_revocation_checks == win32.WTD_REVOKE_WHOLECHAIN
    assert policy.dw_prov_flags == expected_prov
    assert policy.require_microsoft_root_policy is True
    assert policy.accept_test_roots is False
    assert policy.offline_network_canary_required is True
    assert policy.production_root_thumbprints
    assert policy.production_root_thumbprints == win32.PRODUCTION_MICROSOFT_ROOT_SHA256_ALLOWLIST


@task2a_owned
def test_job_is_kill_on_close_and_non_breakaway() -> None:
    policy = win32.job_confinement_policy()
    assert policy.kill_on_job_close is True
    assert policy.create_suspended is True
    assert policy.breakaway_ok is False
    assert policy.silent_breakaway_ok is False
    assert policy.create_breakaway_from_job is False
    assert win32.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x00002000
    assert win32.CREATE_BREAKAWAY_FROM_JOB == 0x01000000
    assert win32.CREATE_SUSPENDED == 0x00000004


@task2a_owned
def test_linux_must_not_fabricate_system32_handles() -> None:
    if _WINDOWS:
        pytest.skip("Windows uses the dedicated System32 identity node")
    with pytest.raises(NotImplementedError, match="must not fabricate"):
        win32.resolve_system32_identity(system_root=None)


# ---------------------------------------------------------------------------
# Dedicated mandatory Windows nodes (manifest required_non_skip)
# ---------------------------------------------------------------------------


@_windows_required
def test_system32_identity_rejects_systemroot_poison() -> None:
    _require_windows()
    with win32.resolve_system32_identity(
        system_root=r"C:\Poisoned\SystemRoot",
    ) as poisoned:
        with win32.resolve_system32_identity(system_root=None) as clean:
            assert poisoned.system32_directory_handle not in (0, 1)
            assert poisoned.wsl_exe_handle not in (0, 1)
            assert clean.system32_directory_handle not in (0, 1)
            assert clean.wsl_exe_handle not in (0, 1)
            # Compare stable volume/file identities — not HANDLE number equality across opens.
            assert clean.directory_identity.as_key() == poisoned.directory_identity.as_key()
            assert clean.wsl_identity.as_key() == poisoned.wsl_identity.as_key()
            assert clean.directory_identity.as_key() != clean.wsl_identity.as_key()


@contextlib.contextmanager
def _open_microsoft_embedded_signed_handle() -> Iterator[tuple[int, str]]:
    """Genuine Microsoft embedded-signed PE (never unsigned held.bin).

    Requires System32\\WindowsPowerShell\\v1.0\\powershell.exe — no notepad fallback.
    """
    system32, _wsl = _system32_wsl_paths()
    powershell = str(Path(system32) / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    assert Path(powershell).is_file(), (
        "required embedded-signed fixture missing: "
        f"{powershell} (WindowsPowerShell v1.0 powershell.exe)"
    )
    with _win32_handle(_open_path_handle(powershell, directory=False)) as handle:
        yield handle, powershell


@_windows_required
def test_held_file_embedded_and_catalog_controls() -> None:
    _require_windows()
    # Catalog positive: real System32 wsl.exe — production derives catalog data
    # from the held handle (no fabricated catalog_context).
    with _open_wsl_exe_handle() as catalog_held:
        catalog = win32.HeldFileTrustProbe(
            held_file_handle=catalog_held,
            choice=win32.WTD_CHOICE_CATALOG,
        )
        catalog_verdict = win32.evaluate_embedded_or_catalog_trust(catalog)
        assert catalog_verdict.get("reason") != "organization_text"
        assert catalog_verdict.get("trusted") is True, (
            "catalog trust on System32 wsl.exe must succeed under Round-60 policy; "
            f"observed {catalog_verdict!r}"
        )
    # Embedded positive: genuine Microsoft embedded-signed PE (powershell.exe only).
    with _open_microsoft_embedded_signed_handle() as (embedded_held, fixture_path):
        assert Path(fixture_path).name.lower() == "powershell.exe"
        embedded = win32.HeldFileTrustProbe(
            held_file_handle=embedded_held,
            choice=win32.WTD_CHOICE_FILE,
            organization_text=None,
        )
        embedded_verdict = win32.evaluate_embedded_or_catalog_trust(embedded)
        assert embedded_verdict.get("reason") != "organization_text"
        assert embedded_verdict.get("trusted") is True, (
            "embedded trust on Microsoft-signed System32 PE must succeed; "
            f"observed {embedded_verdict!r}"
        )
        assert "signature_kind" in embedded_verdict, (
            f"fixture {fixture_path} verdict must carry signature_kind; "
            f"observed {embedded_verdict!r}"
        )
        assert embedded_verdict["signature_kind"] == "embedded", (
            f"fixture {fixture_path} must be asserted as embedded-signed with no default; "
            f"observed {embedded_verdict!r}"
        )


@_windows_required
def test_offline_network_canary_blocks_online_retrieval() -> None:
    _require_windows()
    policy = win32.offline_wintrust_policy()
    assert policy.offline_network_canary_required is True
    with _open_wsl_exe_handle() as held:
        probe = win32.HeldFileTrustProbe(
            held_file_handle=held,
            choice=win32.WTD_CHOICE_FILE,
            organization_text="Microsoft Corporation",
        )

        def _online_would_be_needed() -> bool:
            return True

        def _evaluator(_probe: win32.HeldFileTrustProbe) -> dict:
            return {"trusted": True, "reason": "should_not_reach"}

        verdict = win32.evaluate_embedded_or_catalog_trust(
            probe,
            evaluator=_evaluator,
            offline_network_canary=_online_would_be_needed,
        )
    assert verdict.get("trusted") is False
    assert verdict.get("reason") == "offline_network_canary_failed"


def _generate_foreign_same_org_signed_pe(tmp_path: Path) -> tuple[Path, str, str]:
    """Bounded offline Authenticode fixture via Windows built-ins (CurrentUser).

    Copies a real valid PE (``sys.executable``), replaces its signature with a
    CurrentUser self-signed code-signing cert whose subject Organization is
    ``Microsoft Corporation``, and returns
    ``(pe_path, signer_thumbprint_sha1, root_cert_sha256)``. Cleanup is the
    caller's responsibility.
    """
    import shutil
    import subprocess

    src = Path(sys.executable)
    assert src.is_file(), f"sys.executable missing for PE fixture: {src}"
    pe_path = tmp_path / "foreign-same-org-signed.exe"
    shutil.copy2(src, pe_path)
    # Confirm MZ header of a real PE before resigning.
    with pe_path.open("rb") as fh:
        assert fh.read(2) == b"MZ", f"copied PE lacks MZ header: {pe_path}"
    ps = f"""
$ErrorActionPreference = 'Stop'
$pe = '{pe_path}'
$cert = New-SelfSignedCertificate -Subject 'CN=TensorGrep Round60 Fixture, O=Microsoft Corporation' `
  -Type CodeSigningCert -CertStoreLocation 'Cert:\\CurrentUser\\My' `
  -KeyExportPolicy Exportable -KeySpec Signature -HashAlgorithm SHA256
$signerThumb = ($cert.Thumbprint -replace ' ','').ToUpperInvariant()
$sha = [System.Security.Cryptography.SHA256]::Create()
$rootSha256 = ([BitConverter]::ToString($sha.ComputeHash($cert.RawData)) -replace '-','').ToLowerInvariant()
$sig = Set-AuthenticodeSignature -FilePath $pe -Certificate $cert
if ($null -eq $sig.SignerCertificate) {{ throw 'Set-AuthenticodeSignature returned no SignerCertificate' }}
$got = ($sig.SignerCertificate.Thumbprint -replace ' ','').ToUpperInvariant()
if ($got -ne $signerThumb) {{
  throw "Set-AuthenticodeSignature signer mismatch: got=$got expected=$signerThumb status=$($sig.Status)"
}}
if ($sig.Status -ne 'Valid' -and $sig.Status -ne 'UnknownError') {{
  # UnknownError is acceptable only when the exact signer thumbprint matched above;
  # HashMismatch / NotSigned / etc. are hard failures.
  throw "Set-AuthenticodeSignature unexpected status: $($sig.Status)"
}}
Write-Output ($signerThumb + '|' + $rootSha256)
"""
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ps,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        raise OSError(
            "foreign same-Organization Authenticode fixture generator failed: "
            f"rc={completed.returncode} stderr={completed.stderr!r} stdout={completed.stdout!r}"
        )
    line = completed.stdout.strip().splitlines()[-1].strip()
    if "|" not in line:
        raise OSError(f"fixture generator returned unexpected output: {line!r}")
    signer_thumb, root_sha256 = line.split("|", 1)
    signer_thumb = signer_thumb.strip().upper()
    root_sha256 = root_sha256.strip().lower()
    if len(signer_thumb) != 40 or any(c not in "0123456789ABCDEF" for c in signer_thumb):
        raise OSError(f"fixture generator returned invalid signer thumbprint: {signer_thumb!r}")
    if len(root_sha256) != 64 or any(c not in "0123456789abcdef" for c in root_sha256):
        raise OSError(f"fixture generator returned invalid root SHA-256: {root_sha256!r}")
    return pe_path, signer_thumb, root_sha256


def _independently_verify_foreign_same_org_fixture(
    pe_path: Path,
    signer_thumbprint_sha1: str,
    test_root_thumbprint_sha256: str,
) -> None:
    """Independent setup proof: exact signer, Organization, non-allowlisted root, HashMismatch bind."""
    import shutil
    import subprocess

    assert pe_path.is_file()
    assert test_root_thumbprint_sha256 not in win32.PRODUCTION_MICROSOFT_ROOT_SHA256_ALLOWLIST
    tampered = pe_path.with_name(pe_path.stem + "-tampered.exe")
    shutil.copy2(pe_path, tampered)
    with tampered.open("ab") as fh:
        fh.write(b"\x00TG-ROUND60-TAMPER")
    ps = f"""
$ErrorActionPreference = 'Stop'
$pe = '{pe_path}'
$tampered = '{tampered}'
$expectedThumb = '{signer_thumbprint_sha1}'
$sig = Get-AuthenticodeSignature -FilePath $pe
if ($null -eq $sig.SignerCertificate) {{ throw "signature missing signer: $($sig.Status)" }}
$got = ($sig.SignerCertificate.Thumbprint -replace ' ','').ToUpperInvariant()
if ($got -ne $expectedThumb) {{
  throw "exact signer thumbprint mismatch: got=$got expected=$expectedThumb status=$($sig.Status)"
}}
# Do not accept generic UnknownError with only a signer object as sufficient —
# require either Valid status OR (UnknownError AND exact thumbprint already matched).
if ($sig.Status -eq 'Valid') {{
  # ok
}} elseif ($sig.Status -eq 'UnknownError') {{
  # Untrusted test root: exact thumbprint match above is required; Organization next.
}} else {{
  throw "unexpected Authenticode status for intact fixture: $($sig.Status)"
}}
$org = $sig.SignerCertificate.Subject
if ($org -notmatch 'O=Microsoft Corporation') {{ throw "unexpected subject: $org" }}
$bad = Get-AuthenticodeSignature -FilePath $tampered
if ($bad.Status -ne 'HashMismatch') {{
  throw "tampered copy must report HashMismatch (cryptographic binding); got $($bad.Status)"
}}
Write-Output 'ok'
"""
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, (
        "independent Authenticode setup verification failed: "
        f"stderr={completed.stderr!r} stdout={completed.stdout!r}"
    )
    tampered.unlink(missing_ok=True)


def _cleanup_foreign_same_org_certs() -> None:
    import subprocess

    ps = """
Get-ChildItem Cert:\\CurrentUser\\My |
  Where-Object { $_.Subject -like '*TensorGrep Round60 Fixture*' } |
  Remove-Item -Force -ErrorAction SilentlyContinue
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


@_windows_required
def test_same_organization_foreign_chain_is_not_trust(tmp_path: Path) -> None:
    """Genuine test-root-signed PE with O=Microsoft Corporation must be untrusted.

    No pytest.raises around production trust: a correct GREEN untrusted verdict
    (trusted=False, microsoft_root_policy_rejected) must pass. Behaviorless RED
    fails by raising; a wrong trusted=True must also fail.
    """
    _require_windows()
    pe_path: Path | None = None
    try:
        pe_path, signer_thumb, root_thumb = _generate_foreign_same_org_signed_pe(tmp_path)
        _independently_verify_foreign_same_org_fixture(pe_path, signer_thumb, root_thumb)
        with _win32_handle(_open_path_handle(str(pe_path), directory=False)) as held:
            probe = win32.HeldFileTrustProbe(
                held_file_handle=held,
                choice=win32.WTD_CHOICE_FILE,
                organization_text=None,
            )
            # Intentionally no pytest.raises: GREEN untrusted must pass here.
            verdict = win32.evaluate_embedded_or_catalog_trust(probe)
        assert verdict.get("trusted") is False
        assert verdict.get("reason") == win32.MICROSOFT_ROOT_POLICY_REJECTED
        assert verdict.get("reason") != "organization_text"
    finally:
        _cleanup_foreign_same_org_certs()
        if pe_path is not None and pe_path.exists():
            pe_path.unlink(missing_ok=True)
        for leftover in tmp_path.glob("foreign-same-org-signed*.exe"):
            leftover.unlink(missing_ok=True)


@_windows_required
def test_untrusted_catalog_reason_exact(tmp_path: Path) -> None:
    """Exact reason ``untrusted_catalog`` on a held unsigned/foreign file.

    Must not use trusted System32 wsl.exe (that positive lives in
    ``test_held_file_embedded_and_catalog_controls``).
    """
    _require_windows()
    with _open_temp_unsigned_handle(tmp_path) as held:
        probe = win32.HeldFileTrustProbe(
            held_file_handle=held,
            choice=win32.WTD_CHOICE_CATALOG,
        )
        verdict = win32.evaluate_untrusted_catalog(probe)
        assert verdict.get("trusted") is False
        assert verdict.get("reason") == win32.UNTRUSTED_CATALOG_REASON
        assert verdict.get("reason") != win32.CATALOG_MEMBER_HASH_MISMATCH_REASON


@_windows_required
def test_catalog_member_hash_mismatch_reason_exact() -> None:
    """Exact ``catalog_member_hash_mismatch`` via held catalog file + wrong hash.

    Binds real System32 wsl.exe (trusted-catalog positive sibling ensures an
    always-mismatch implementation fails that arm). Expected hash is derived
    from actual file bytes then deliberately corrupted so production must
    hash the held handle and compare.
    """
    _require_windows()
    import hashlib

    _system32, wsl_path = _system32_wsl_paths()
    actual = hashlib.sha256(Path(wsl_path).read_bytes()).digest()
    wrong = hashlib.sha256(actual + b"\x00-round60-wrong").digest()
    assert wrong != actual
    with _open_wsl_exe_handle() as held:
        probe = win32.HeldFileTrustProbe(
            held_file_handle=held,
            choice=win32.WTD_CHOICE_CATALOG,
            expected_member_hash=wrong,
        )
        verdict = win32.evaluate_catalog_member_hash_mismatch(probe)
        assert verdict.get("trusted") is False
        assert verdict.get("reason") == win32.CATALOG_MEMBER_HASH_MISMATCH_REASON
        assert verdict.get("reason") != win32.UNTRUSTED_CATALOG_REASON


@_windows_required
def test_parent_and_leaf_identity_swaps_fail_closed(tmp_path: Path) -> None:
    _require_windows()
    parent_dir = tmp_path / "parent"
    parent_dir.mkdir()
    leaf_path = parent_dir / "leaf.bin"
    leaf_path.write_bytes(b"v1")

    swap_permitted = threading.Event()
    swap_complete = threading.Event()
    worker_errors: list[BaseException] = []

    with _win32_handle(_open_path_handle(str(parent_dir), directory=True)) as dir_h:
        with _win32_handle(_open_path_handle(str(leaf_path), directory=False)) as leaf_h:
            retained_parent = _volume_identity(dir_h)
            retained_leaf = _volume_identity(leaf_h)

            def _leaf_swap_worker() -> None:
                try:
                    assert swap_permitted.wait(timeout=30), "leaf swap_permitted timed out"
                    leaf_path.unlink()
                    leaf_path.write_bytes(b"v2-swapped")
                    swap_complete.set()
                except BaseException as exc:
                    worker_errors.append(exc)
                    swap_complete.set()

            worker = threading.Thread(target=_leaf_swap_worker, daemon=True)
            worker.start()
            swap_permitted.set()
            assert swap_complete.wait(timeout=30), "leaf swap_complete timed out"
            worker.join(timeout=30)
            assert not worker.is_alive(), "leaf swap worker failed to join"
            if worker_errors:
                raise worker_errors[0]

            with _win32_handle(_open_path_handle(str(leaf_path), directory=False)) as new_leaf_h:
                observed_leaf = _volume_identity(new_leaf_h)

            assert observed_leaf.as_key() != retained_leaf.as_key(), (
                "Event-gated worker swap must change leaf VolumeFileIdentity"
            )

            assert (
                win32.parent_or_leaf_identity_unchanged_after_swap(
                    win32.IdentitySwapObservation(
                        retained_parent=retained_parent,
                        retained_leaf=retained_leaf,
                        observed_parent=retained_parent,
                        observed_leaf=observed_leaf,
                        leaf_swap_event=swap_complete,
                    )
                )
                is False
            )

    # Parent swap: replace directory at same path after retained open.
    parent2 = tmp_path / "parent2"
    parent2.mkdir()
    leaf2 = parent2 / "leaf.bin"
    leaf2.write_bytes(b"parent2-leaf")

    swap_permitted2 = threading.Event()
    swap_complete2 = threading.Event()
    worker_errors2: list[BaseException] = []

    with _win32_handle(_open_path_handle(str(parent2), directory=True)) as dir_h2:
        with _win32_handle(_open_path_handle(str(leaf2), directory=False)) as leaf_h2:
            retained_parent2 = _volume_identity(dir_h2)
            retained_leaf2 = _volume_identity(leaf_h2)

            def _parent_swap_worker() -> None:
                try:
                    assert swap_permitted2.wait(timeout=30), "parent swap_permitted timed out"
                    parent2.rename(tmp_path / "parent2_moved")
                    replacement = tmp_path / "parent2"
                    replacement.mkdir()
                    (replacement / "leaf.bin").write_bytes(b"replacement-leaf")
                    swap_complete2.set()
                except BaseException as exc:
                    worker_errors2.append(exc)
                    swap_complete2.set()

            worker2 = threading.Thread(target=_parent_swap_worker, daemon=True)
            worker2.start()
            swap_permitted2.set()
            assert swap_complete2.wait(timeout=30), "parent swap_complete timed out"
            worker2.join(timeout=30)
            assert not worker2.is_alive(), "parent swap worker failed to join"
            if worker_errors2:
                raise worker_errors2[0]

            with _win32_handle(
                _open_path_handle(str(tmp_path / "parent2"), directory=True)
            ) as new_dir_h:
                observed_parent = _volume_identity(new_dir_h)
            with _win32_handle(
                _open_path_handle(str(tmp_path / "parent2" / "leaf.bin"), directory=False)
            ) as new_leaf_h2:
                observed_leaf2 = _volume_identity(new_leaf_h2)

            assert observed_parent.as_key() != retained_parent2.as_key(), (
                "Event-gated worker swap must change parent VolumeFileIdentity"
            )

            assert (
                win32.parent_or_leaf_identity_unchanged_after_swap(
                    win32.IdentitySwapObservation(
                        retained_parent=retained_parent2,
                        retained_leaf=retained_leaf2,
                        observed_parent=observed_parent,
                        observed_leaf=observed_leaf2,
                        parent_swap_event=swap_complete2,
                    )
                )
                is False
            )

    # No-swap control: identities unchanged.
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    control_leaf = control_dir / "leaf.bin"
    control_leaf.write_bytes(b"stable")

    with _win32_handle(_open_path_handle(str(control_dir), directory=True)) as cdir_h:
        with _win32_handle(_open_path_handle(str(control_leaf), directory=False)) as cleaf_h:
            ctrl_parent = _volume_identity(cdir_h)
            ctrl_leaf = _volume_identity(cleaf_h)
            assert (
                win32.parent_or_leaf_identity_unchanged_after_swap(
                    win32.IdentitySwapObservation(
                        retained_parent=ctrl_parent,
                        retained_leaf=ctrl_leaf,
                        observed_parent=ctrl_parent,
                        observed_leaf=ctrl_leaf,
                    )
                )
                is True
            )


@dataclass
class _RecordingJobFactory:
    """Test-owned Job factory: real-shaped handles, injectable BaseException arms."""

    closed: list[int]
    terminated: list[int] = None  # type: ignore[assignment]
    acquired: list[int] = None  # type: ignore[assignment]
    fault_after: str | None = None
    _next_handle: int = 1000
    _next_pid: int = 5000

    def __post_init__(self) -> None:
        if self.terminated is None:
            self.terminated = []
        if self.acquired is None:
            self.acquired = []

    def _alloc(self) -> int:
        self._next_handle += 1
        handle = self._next_handle
        self.acquired.append(handle)
        return handle

    def create_job(self) -> int:
        return self._alloc()

    def create_process_suspended(
        self,
        *,
        canary_event: threading.Event | None,
        canary_pipe_write_fd: int | None,
    ) -> win32.ProcessThreadHandles:
        _ = canary_event, canary_pipe_write_fd
        self._next_pid += 1
        return win32.ProcessThreadHandles(
            process_handle=self._alloc(),
            thread_handle=self._alloc(),
            pid=self._next_pid,
        )

    def assign_process_to_job(self, job_handle: int, process_handle: int) -> None:
        _ = job_handle, process_handle

    def resume_thread(self, thread_handle: int) -> None:
        _ = thread_handle

    def query_process_image(self, process_handle: int) -> str:
        _ = process_handle
        return "C:\\Windows\\System32\\cmd.exe"

    def setup_pipe_worker(
        self,
        *,
        parent: win32.ProcessThreadHandles,
        canary_event: threading.Event | None,
        canary_pipe_write_fd: int | None,
        writer_nonce: bytes | None = None,
    ) -> win32.ProcessThreadHandles:
        _ = parent, canary_event, canary_pipe_write_fd, writer_nonce
        self._next_pid += 1
        return win32.ProcessThreadHandles(
            process_handle=self._alloc(),
            thread_handle=self._alloc(),
            pid=self._next_pid,
        )

    def terminate_process(self, process_handle: int) -> None:
        self.terminated.append(process_handle)

    def close_handle(self, handle: int) -> None:
        self.closed.append(handle)


def _test_side_wait_process_exited(process_handle: int, *, timeout_ms: int = 5000) -> bool:
    """Test-owned Win32 wait — never call production query_process_alive/reap."""
    if not _WINDOWS:
        raise RuntimeError("test-side wait requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    # WAIT_OBJECT_0 == 0; WAIT_TIMEOUT == 0x102
    return int(kernel32.WaitForSingleObject(wintypes.HANDLE(process_handle), timeout_ms)) == 0


def _test_side_get_exit_code(process_handle: int) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(wintypes.HANDLE(process_handle), ctypes.byref(code)):
        raise OSError("GetExitCodeProcess failed")
    return int(code.value)


# STILL_ACTIVE
_STILL_ACTIVE = 259


def _bounded_pipe_read_until_contains(
    fd: int,
    needle: bytes,
    *,
    timeout_s: float,
    max_bytes: int = 65_536,
) -> bytes:
    """Bounded non-blocking read until ``needle`` appears; absence fails.

    Authority is pipe bytes, never a threading.Event. Empty/EOF before the
    identifiable heartbeat is a hard failure (no vacuous pass).
    """
    if not needle:
        raise ValueError("heartbeat needle must be non-empty")
    deadline = time.monotonic() + timeout_s
    buf = bytearray()
    was_blocking = os.get_blocking(fd)
    os.set_blocking(fd, False)
    try:
        while time.monotonic() < deadline:
            if needle in buf:
                return bytes(buf)
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                time.sleep(0.01)
                continue
            if chunk == b"":
                break
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise AssertionError(
                    f"canary pipe exceeded {max_bytes} bytes before heartbeat {needle!r}; "
                    f"prefix={bytes(buf[:64])!r}"
                )
        raise AssertionError(
            f"pre-close descendant heartbeat {needle!r} not observed within "
            f"{timeout_s:.1f}s; buffered={bytes(buf)!r}"
        )
    finally:
        os.set_blocking(fd, was_blocking)


def _bounded_pipe_drain_to_eof(
    fd: int,
    *,
    timeout_s: float,
    max_bytes: int = 65_536,
) -> bytes:
    """Drain all remaining buffered pipe data, then require EOF."""
    deadline = time.monotonic() + timeout_s
    buf = bytearray()
    was_blocking = os.get_blocking(fd)
    os.set_blocking(fd, False)
    try:
        while time.monotonic() < deadline:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                time.sleep(0.01)
                continue
            if chunk == b"":
                return bytes(buf)
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise AssertionError(
                    f"canary pipe drain exceeded {max_bytes} bytes without EOF; "
                    f"prefix={bytes(buf[:64])!r}"
                )
        raise AssertionError(
            f"canary pipe EOF not observed within {timeout_s:.1f}s after drain; "
            f"buffered={bytes(buf)!r}"
        )
    finally:
        os.set_blocking(fd, was_blocking)


@task2a_owned
def test_suspended_job_descendant_breakaway_orchestration() -> None:
    """Platform-neutral Job orchestration over injectable factory (not Windows proof).

    Does not accept production booleans/dicts as authority. Behaviorless default
    factory path fails closed (no fake success). When a factory is supplied,
    asserts exact handle ownership without consulting production reap dicts.
    Clearing a threading.Event is never treated as proof. Kept separate from the
    mandatory Windows default-factory heartbeat/Job-close integration.
    """
    canary_event = threading.Event()
    canary_event.set()
    r_fd, w_fd = os.pipe()
    try:
        with pytest.raises(
            NotImplementedError,
            match=r"Job factory|process/Job|windows_job|CreateJobObject|DefaultJobFactory",
        ):
            win32.create_suspended_job_with_descendant_breakaway(
                canary_event=canary_event,
                canary_pipe_write_fd=w_fd,
            )
        closed: list[int] = []
        factory = _RecordingJobFactory(closed=closed)
        with win32.create_suspended_job_with_descendant_breakaway(
            canary_event=canary_event,
            canary_pipe_write_fd=w_fd,
            factory=factory,
        ) as fixture:
            assert fixture.job_handle not in (0, 1)
            assert fixture.parent_process_handle not in (0, 1)
            assert fixture.parent_thread_handle not in (0, 1)
            assert fixture.descendant_process_handle not in (0, 1)
            assert fixture.descendant_thread_handle not in (0, 1)
            assert fixture.parent_pid > 0
            assert fixture.descendant_pid > 0
            assert fixture.parent_pid != fixture.descendant_pid
            assert fixture.create_flags & win32.CREATE_SUSPENDED
            assert not (fixture.create_flags & win32.CREATE_BREAKAWAY_FROM_JOB)
            # PID+nonce heartbeat: descendant payload ≠ parent; round-trip;
            # PID-only parent forgery refused.
            nonce = b"\x11" * 16
            desc_hb = win32.descendant_job_pipe_heartbeat(
                fixture.descendant_pid, writer_nonce=nonce
            )
            parent_hb = win32.descendant_job_pipe_heartbeat(
                fixture.parent_pid, writer_nonce=nonce
            )
            assert desc_hb != parent_hb
            assert win32.parse_descendant_job_pipe_heartbeat_pid(
                desc_hb, writer_nonce=nonce
            ) == (fixture.descendant_pid)
            assert fixture.canary_event is canary_event
            assert fixture.canary_pipe_write_fd == w_fd
            owned = fixture.owned_handles()
            assert len(owned) == 5
            assert len(set(owned)) == 5
            assert list(factory.acquired) == list(owned)
            assert canary_event.is_set()
            fixture.close()
            assert closed == list(owned)
            # Event remains set — clearing it is not Job-kill proof.
            assert canary_event.is_set() is True
    finally:
        for fd in (r_fd, w_fd):
            try:
                os.close(fd)
            except OSError:
                pass


@_windows_required
def test_suspended_job_descendant_breakaway_windows_integration() -> None:
    """Mandatory Windows Job integration: default factory + PID+nonce heartbeat.

    Calls production with no injected factory. Requires a test-owned exact
    descendant-bound pipe heartbeat from ``descendant_job_pipe_heartbeat``
    (containing the actual descendant PID + writer nonce, written by the
    descendant worker) before Job close — absence fails (no vacuous empty-pipe
    / parent-forged PID-only pass). Before ``close_job_only``, independently
    proves BOTH retained process handles are ``STILL_ACTIVE`` via test-side
    ``GetExitCodeProcess`` and zero-time ``WaitForSingleObject``. Then closes
    Job only, proves both transition to exited / non-``STILL_ACTIVE``,
    boundedly drains buffered heartbeat data, and requires EOF. Never uses
    ``threading.Event`` as authority.
    """
    _require_windows()
    r_fd, w_fd = os.pipe()
    nonce = os.urandom(16)
    try:
        # Inheritability: pass the writer fd into production; GREEN must duplicate
        # into the child and have the descendant write the PID+nonce heartbeat.
        # On behaviorless RED this raises at the default factory.
        fixture = win32.create_suspended_job_with_descendant_breakaway(
            canary_pipe_write_fd=w_fd,
            writer_nonce=nonce,
        )
        try:
            assert fixture.job_handle not in (0, 1, -1)
            assert fixture.parent_process_handle not in (0, 1, -1)
            assert fixture.parent_thread_handle not in (0, 1, -1)
            assert fixture.descendant_process_handle not in (0, 1, -1)
            assert fixture.descendant_thread_handle not in (0, 1, -1)
            assert fixture.parent_pid > 0
            assert fixture.descendant_pid > 0
            assert fixture.parent_pid != fixture.descendant_pid
            parent_proc = fixture.parent_process_handle
            descendant_proc = fixture.descendant_process_handle
            # Exact heartbeat is a pure function of the real descendant PID +
            # test-owned nonce — a parent-writable PID-only token cannot satisfy.
            heartbeat = win32.descendant_job_pipe_heartbeat(
                fixture.descendant_pid, writer_nonce=nonce
            )
            assert heartbeat, "contract heartbeat must be non-empty"
            assert heartbeat != win32.descendant_job_pipe_heartbeat(
                fixture.parent_pid, writer_nonce=nonce
            )
            # Pre-close: descendant must have inherited the writer and emitted
            # the exact PID+nonce heartbeat. Empty pipe / missing writer fails.
            pre_close = _bounded_pipe_read_until_contains(
                r_fd,
                heartbeat,
                timeout_s=10.0,
            )
            assert heartbeat in pre_close
            assert (
                win32.parse_descendant_job_pipe_heartbeat_pid(
                    pre_close, writer_nonce=nonce
                )
                == fixture.descendant_pid
            )
            # Before Job close: BOTH members must still be alive. If either
            # already exited, Job-kill proof is vacuous.
            assert _test_side_get_exit_code(parent_proc) == _STILL_ACTIVE
            assert _test_side_get_exit_code(descendant_proc) == _STILL_ACTIVE
            assert not _test_side_wait_process_exited(parent_proc, timeout_ms=0)
            assert not _test_side_wait_process_exited(descendant_proc, timeout_ms=0)
            # Close ONLY the Job first (KILL_ON_JOB_CLOSE).
            fixture.close_job_only()
            assert _test_side_wait_process_exited(parent_proc, timeout_ms=10_000)
            assert _test_side_wait_process_exited(descendant_proc, timeout_ms=10_000)
            parent_code = _test_side_get_exit_code(parent_proc)
            descendant_code = _test_side_get_exit_code(descendant_proc)
            assert parent_code != _STILL_ACTIVE
            assert descendant_code != _STILL_ACTIVE
            # Drop the test-side writer so EOF is reachable once members died.
            os.close(w_fd)
            w_fd = -1
            # Drain any remaining buffered heartbeat bytes, then require EOF.
            # Do not assert empty on a single-byte read (false-fails on buffer).
            drained = _bounded_pipe_drain_to_eof(r_fd, timeout_s=5.0)
            assert isinstance(drained, bytes)
        finally:
            fixture.close()
    finally:
        for fd in (r_fd, w_fd):
            if fd < 0:
                continue
            try:
                os.close(fd)
            except OSError:
                pass


@task2a_owned
def test_job_heartbeat_rejects_parent_forgeable_and_multiline_ambiguity() -> None:
    """HIGH#4 / A63: nonce-bound heartbeat; PID-only forge + multiline reject."""
    nonce = b"\xab" * 16
    other = b"\xcd" * 16
    pid = 4242
    good = win32.descendant_job_pipe_heartbeat(pid, writer_nonce=nonce)
    assert win32.parse_descendant_job_pipe_heartbeat_pid(good, writer_nonce=nonce) == pid

    # Parent-forgeable PID-only legacy form (no nonce mark) must refuse.
    pid_only = b"TG60-JOB-DESCENDANT-HB pid=4242\n"
    with pytest.raises(ValueError, match=r"nonce mark|parent-forgeable|PID-only"):
        win32.parse_descendant_job_pipe_heartbeat_pid(pid_only, writer_nonce=nonce)

    # Wrong nonce (parent guessing PID but not the writer secret) must refuse.
    with pytest.raises(ValueError, match=r"nonce mismatch"):
        win32.parse_descendant_job_pipe_heartbeat_pid(good, writer_nonce=other)

    # Multiline / multi-prefix ambiguity must refuse (not first-match win).
    ambiguous = good + b"noise" + win32.descendant_job_pipe_heartbeat(9999, writer_nonce=nonce)
    with pytest.raises(ValueError, match=r"multiline ambiguity"):
        win32.parse_descendant_job_pipe_heartbeat_pid(ambiguous, writer_nonce=nonce)

    # Short nonce is itself parent-forgeable — refused at format time.
    with pytest.raises(ValueError, match=r"writer_nonce|forgeable"):
        win32.descendant_job_pipe_heartbeat(pid, writer_nonce=b"short")


@task2a_owned
def test_default_job_cleanup_independently_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH#5 / A63: default-factory cleanup via production ``close_handle``.

    Uses the real ``DefaultJobFactoryPrimitives`` class (method patches only) —
    does not replace ``windows_job_factory_primitives`` with a wholly separate
    recording factory while also mocking the closer (Sol R2).
    """
    closed_via_production: list[int] = []

    def _prod_close(handle: int) -> None:
        closed_via_production.append(handle)

    monkeypatch.setattr(win32, "close_handle", _prod_close)
    monkeypatch.setattr(win32, "IS_WINDOWS", True)

    factory = win32.windows_job_factory_primitives()
    assert type(factory).__name__ == "DefaultJobFactoryPrimitives"
    acquired: list[int] = []
    nxt = {"n": 100}

    def _alloc() -> int:
        nxt["n"] += 1
        acquired.append(nxt["n"])
        return nxt["n"]

    monkeypatch.setattr(type(factory), "create_job", lambda self: _alloc())
    monkeypatch.setattr(
        type(factory),
        "create_process_suspended",
        lambda self, **kwargs: win32.ProcessThreadHandles(
            process_handle=_alloc(), thread_handle=_alloc(), pid=1000
        ),
    )
    monkeypatch.setattr(type(factory), "assign_process_to_job", lambda self, j, p: None)
    monkeypatch.setattr(type(factory), "resume_thread", lambda self, t: None)
    monkeypatch.setattr(type(factory), "query_process_image", lambda self, p: "img")
    monkeypatch.setattr(
        type(factory),
        "setup_pipe_worker",
        lambda self, **kwargs: win32.ProcessThreadHandles(
            process_handle=_alloc(), thread_handle=_alloc(), pid=2000
        ),
    )
    monkeypatch.setattr(type(factory), "terminate_process", lambda self, p: None)

    canary = threading.Event()
    canary.set()
    with pytest.raises(BaseException, match="injected fault"):
        win32.create_suspended_job_with_descendant_breakaway(
            canary_event=canary,
            inject_fault_after="pipe_worker_setup",
            factory=None,  # default path
        )
    assert acquired, "premise: default factory acquired handles"
    assert closed_via_production == list(reversed(acquired))
    assert canary.is_set() is True


# Exact fault-boundary expectations for the injectable recording factory.
# acquired order: job, parent_proc, parent_thread [, desc_proc, desc_thread]
# terminate order: descendant (if any) then parent
# close order: reversed(acquired)
_FAULT_ARM_EXPECTATIONS: dict[str, tuple[int, int]] = {
    # fault_after -> (acquired_count, terminated_count)
    "job_assignment": (3, 1),
    "resume": (3, 1),
    "image_query": (3, 1),
    "pipe_worker_setup": (5, 2),
}


def _assert_job_fault_arm(fault_after: win32.JobInjectFaultAfter) -> None:
    closed: list[int] = []
    terminated: list[int] = []
    factory = _RecordingJobFactory(closed=closed, terminated=terminated)
    canary = threading.Event()
    canary.set()
    with pytest.raises(BaseException, match="injected fault") as exc_info:
        win32.create_suspended_job_with_descendant_breakaway(
            canary_event=canary,
            inject_fault_after=fault_after,
            factory=factory,
        )
    expected_acq, expected_term = _FAULT_ARM_EXPECTATIONS[fault_after]
    acquired = list(factory.acquired)
    assert len(acquired) == expected_acq, (
        f"{fault_after}: acquired={acquired!r} expected count {expected_acq}"
    )
    assert len(set(acquired)) == expected_acq
    # Process handles are at odd indices after job: indices 1, (3 if descendant).
    parent_proc = acquired[1]
    expected_terminated = [parent_proc]
    if expected_term == 2:
        expected_terminated = [acquired[3], parent_proc]
    assert terminated == expected_terminated, (
        f"{fault_after}: terminated={terminated!r} expected {expected_terminated!r}"
    )
    assert closed == list(reversed(acquired)), (
        f"{fault_after}: close order {closed!r} != reversed acquired {list(reversed(acquired))!r}"
    )
    assert canary.is_set()  # test still owns canary; production must not fake-clear
    # Original BaseException preserved (not wrapped away).
    assert "injected fault" in str(exc_info.value)


@task2a_owned
def test_suspended_job_fault_after_job_assignment() -> None:
    _assert_job_fault_arm("job_assignment")


@task2a_owned
def test_suspended_job_fault_after_resume() -> None:
    _assert_job_fault_arm("resume")


@task2a_owned
def test_suspended_job_fault_after_image_query() -> None:
    _assert_job_fault_arm("image_query")


@task2a_owned
def test_suspended_job_fault_after_pipe_worker_setup() -> None:
    _assert_job_fault_arm("pipe_worker_setup")


@_windows_required
@pytest.mark.parametrize(
    "fault_after",
    ["job_assignment", "resume", "image_query", "pipe_worker_setup"],
    ids=["job_assignment", "resume", "image_query", "pipe_worker_setup"],
)
def test_suspended_job_fault_after_default_factory(
    fault_after: win32.JobInjectFaultAfter,
) -> None:
    """Mandatory Windows default-factory fault arms (no injected recording factory).

    A correct GREEN must raise the injected fault (not a factory stub error).
    Behaviorless RED fails here — do not accept NotImplementedError as a pass.
    """
    _require_windows()
    canary = threading.Event()
    canary.set()
    with pytest.raises(BaseException, match="injected fault") as exc_info:
        win32.create_suspended_job_with_descendant_breakaway(
            canary_event=canary,
            inject_fault_after=fault_after,
        )
    assert "injected fault" in str(exc_info.value)
    notes = getattr(exc_info.value, "__notes__", [])
    _ = notes  # cleanup notes optional; original fault must remain


@task2a_owned
def test_resolve_system32_identity_closes_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Close-on-success RED: retained closer must run for both handles, idempotently."""
    closed: list[int] = []
    monkeypatch.setattr(win32, "IS_WINDOWS", True)
    monkeypatch.setattr(win32, "open_system32_directory_nofollow", lambda **_kw: 101)
    monkeypatch.setattr(win32, "open_wsl_exe_nofollow", lambda _h: 102)
    monkeypatch.setattr(
        win32,
        "volume_file_identity_from_handle",
        lambda h: win32.VolumeFileIdentity(1, 2, h),
    )
    monkeypatch.setattr(win32, "close_handle", lambda h: closed.append(h))
    with win32.resolve_system32_identity() as retained:
        assert retained.system32_directory_handle == 101
        assert retained.wsl_exe_handle == 102
        assert closed == []
    assert closed == [102, 101]
    retained.close()
    assert closed == [102, 101], "close must be idempotent (exact-once per handle)"


@task2a_owned
def test_resolve_system32_identity_closes_on_partial_leaf_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close-on-partial-failure RED: dir handle must close when leaf open fails."""
    closed: list[int] = []
    monkeypatch.setattr(win32, "IS_WINDOWS", True)
    monkeypatch.setattr(win32, "open_system32_directory_nofollow", lambda **_kw: 201)

    def _boom(_h: int) -> int:
        raise OSError("leaf open failed")

    monkeypatch.setattr(win32, "open_wsl_exe_nofollow", _boom)
    monkeypatch.setattr(win32, "close_handle", lambda h: closed.append(h))
    with pytest.raises(OSError, match="leaf open"):
        win32.resolve_system32_identity()
    assert closed == [201]


@task2a_owned
def test_resolve_system32_identity_closes_on_partial_identity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close-on-partial-failure RED: both handles close when identity query fails."""
    closed: list[int] = []
    monkeypatch.setattr(win32, "IS_WINDOWS", True)
    monkeypatch.setattr(win32, "open_system32_directory_nofollow", lambda **_kw: 301)
    monkeypatch.setattr(win32, "open_wsl_exe_nofollow", lambda _h: 302)

    def _identity(h: int) -> win32.VolumeFileIdentity:
        if h == 302:
            raise OSError("identity query failed")
        return win32.VolumeFileIdentity(1, 2, h)

    monkeypatch.setattr(win32, "volume_file_identity_from_handle", _identity)
    monkeypatch.setattr(win32, "close_handle", lambda h: closed.append(h))
    with pytest.raises(OSError, match="identity query"):
        win32.resolve_system32_identity()
    assert closed == [302, 301]


@task2a_owned
def test_suspended_job_fixture_close_ownership_idempotent() -> None:
    """Same ownership contract for SuspendedJobFixture: all owned handles, idempotent."""
    closed: list[int] = []
    fixture = win32.SuspendedJobFixture(
        job_handle=55,
        parent_process_handle=56,
        parent_thread_handle=57,
        descendant_process_handle=58,
        descendant_thread_handle=59,
        parent_pid=1,
        descendant_pid=2,
        create_flags=win32.CREATE_SUSPENDED,
        _closer=closed.append,
    )
    with fixture:
        assert closed == []
    assert closed == [55, 56, 57, 58, 59]
    fixture.close()
    assert closed == [55, 56, 57, 58, 59]


@task2a_owned
def test_retained_close_state_excluded_from_equality_and_hash() -> None:
    """Close-state must not participate in equality/hash (finding 18)."""

    def _noop(_h: int) -> None:
        return None

    a = win32.RetainedSystem32Identity(
        system32_directory_handle=1,
        wsl_exe_handle=2,
        directory_identity=win32.VolumeFileIdentity(1, 0, 1),
        wsl_identity=win32.VolumeFileIdentity(1, 0, 2),
        _closer=_noop,
    )
    b = win32.RetainedSystem32Identity(
        system32_directory_handle=1,
        wsl_exe_handle=2,
        directory_identity=win32.VolumeFileIdentity(1, 0, 1),
        wsl_identity=win32.VolumeFileIdentity(1, 0, 2),
        _closer=_noop,
    )
    a.close()
    assert a == b
    assert hash(a) == hash(b)
