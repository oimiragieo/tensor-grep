"""Behaviorless Round-60 seam for the Win32 path-domain bridge (Task 2A / #89).

Platform-neutral policy objects are injectable observables. Windows-only handle,
Job, and trust evaluation APIs are primitive OS-operation seams that fail closed
and never fabricate success handles or security verdicts. Green-phase replaces stubs.
"""

from __future__ import annotations

import os
import secrets
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

# mypy on the Linux CI leg has no `ctypes.WinDLL` / `ctypes.get_last_error` attributes
# (Windows-only stdlib surface), so every direct call site red the Formatting job
# (17x attr-defined; CI run 31635590757). These aliases are the single mypy-visible
# seam: on win32 they ARE the real ctypes attributes; off Windows they raise if ever
# reached (every caller sits behind a runtime win32 gate; the win32 arms are pinned
# by the round-60 suites on Windows CI).
_win_dll: Any
_win_last_error: Any
if sys.platform == "win32":
    import ctypes as _win_ctypes

    _win_dll = _win_ctypes.WinDLL
    _win_last_error = _win_ctypes.get_last_error
else:  # pragma: no cover - analysis stub; win32-only paths are unreachable off Windows

    def _win32_only(*_args: object, **_kwargs: object) -> Any:
        raise OSError("win32-only kernel32 API invoked off Windows")

    _win_dll = _win32_only
    _win_last_error = _win32_only

# WinTrust / provider flags required by Round-60 offline policy.
WTD_UI_NONE = 2
WTD_REVOKE_WHOLECHAIN = 1
WTD_CACHE_ONLY_URL_RETRIEVAL = 0x00000004
WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT = 0x40000000
WTD_CHOICE_FILE = 1
WTD_CHOICE_CATALOG = 2

CERT_CHAIN_POLICY_MICROSOFT_ROOT = 1
CODE_SIGNING_EKU_OID = "1.3.6.1.5.5.7.3.3"

# Job / process creation flags.
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
CREATE_SUSPENDED = 0x00000004
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Test-owned pipe heartbeat contract for SuspendedJobFixture Windows integration.
# GREEN's inherited descendant pipe worker must write the exact PID+nonce payload
# from ``descendant_job_pipe_heartbeat(own_pid, writer_nonce=...)`` before Job close.
# A parent-forgeable PID-only / fixed token is insufficient (A63 / HIGH#4).
# Clearing a threading.Event is never proof.
_DESCENDANT_JOB_PIPE_HEARTBEAT_PREFIX = b"TG60-JOB-DESCENDANT-HB pid="
_DESCENDANT_JOB_PIPE_HEARTBEAT_NONCE_MARK = b" nonce="
_DESCENDANT_JOB_PIPE_HEARTBEAT_SUFFIX = b"\n"
_MIN_WRITER_NONCE_LEN = 16


def mint_writer_nonce() -> bytes:
    """Factory-minted writer nonce (not caller-supplied-only).

    Returns at least ``_MIN_WRITER_NONCE_LEN`` cryptographically strong bytes.
    """
    return secrets.token_bytes(_MIN_WRITER_NONCE_LEN)


def descendant_job_pipe_heartbeat(descendant_pid: int, *, writer_nonce: bytes) -> bytes:
    """Format the exact canary-pipe heartbeat bound to PID + writer nonce.

    The nonce authenticates writer provenance: a parent that only knows the
    descendant PID cannot forge acceptance. Rejects non-positive PIDs and
    short/empty nonces.
    """
    if not isinstance(descendant_pid, int) or isinstance(descendant_pid, bool):
        raise TypeError("descendant_pid must be an int")
    if descendant_pid <= 0:
        raise ValueError("descendant_pid must be a positive int")
    if not isinstance(writer_nonce, (bytes, bytearray)):
        raise TypeError("writer_nonce must be bytes")
    nonce = bytes(writer_nonce)
    if len(nonce) < _MIN_WRITER_NONCE_LEN:
        raise ValueError(
            f"writer_nonce must be at least {_MIN_WRITER_NONCE_LEN} bytes "
            "(parent-forgeable short secrets refused)"
        )
    return (
        _DESCENDANT_JOB_PIPE_HEARTBEAT_PREFIX
        + str(descendant_pid).encode("ascii")
        + _DESCENDANT_JOB_PIPE_HEARTBEAT_NONCE_MARK
        + nonce.hex().encode("ascii")
        + _DESCENDANT_JOB_PIPE_HEARTBEAT_SUFFIX
    )


def parse_descendant_job_pipe_heartbeat_pid(
    payload: bytes,
    *,
    writer_nonce: bytes,
) -> int:
    """Extract the bound descendant PID from an exact heartbeat line in ``payload``.

    Requires the deterministic ``descendant_job_pipe_heartbeat`` line shape with
    the exact ``writer_nonce``. Rejects PID-only parent forgeries, truncated
    lines, and multiline / multi-match ambiguity (exactly one heartbeat line).
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")
    if not isinstance(writer_nonce, (bytes, bytearray)):
        raise TypeError("writer_nonce must be bytes")
    nonce = bytes(writer_nonce)
    if len(nonce) < _MIN_WRITER_NONCE_LEN:
        raise ValueError(
            f"writer_nonce must be at least {_MIN_WRITER_NONCE_LEN} bytes "
            "(parent-forgeable short secrets refused)"
        )
    raw = bytes(payload)
    prefix = _DESCENDANT_JOB_PIPE_HEARTBEAT_PREFIX
    mark = _DESCENDANT_JOB_PIPE_HEARTBEAT_NONCE_MARK
    suffix = _DESCENDANT_JOB_PIPE_HEARTBEAT_SUFFIX
    nonce_hex = nonce.hex().encode("ascii")

    # Exact-frame: heartbeat must begin at offset 0 (no pre-framing garbage).
    if not raw.startswith(prefix):
        if prefix not in raw:
            raise ValueError("descendant job pipe heartbeat prefix absent")
        raise ValueError(
            "descendant job pipe heartbeat framing garbage (pre-framing bytes before heartbeat)"
        )
    first = 0
    second = raw.find(prefix, len(prefix))
    if second >= 0:
        raise ValueError(
            "descendant job pipe heartbeat multiline ambiguity (multiple heartbeat prefixes)"
        )

    rest = raw[first + len(prefix) :]
    mark_at = rest.find(mark)
    if mark_at < 0:
        raise ValueError(
            "descendant job pipe heartbeat missing nonce mark "
            "(parent-forgeable PID-only form refused)"
        )
    digits = rest[:mark_at]
    after_mark = rest[mark_at + len(mark) :]
    end = after_mark.find(suffix)
    if end < 0:
        raise ValueError("descendant job pipe heartbeat suffix absent")
    got_nonce_hex = after_mark[:end]
    if not digits or not digits.isdigit():
        raise ValueError("descendant job pipe heartbeat PID must be decimal digits")
    if got_nonce_hex != nonce_hex:
        raise ValueError("descendant job pipe heartbeat nonce mismatch")
    pid = int(digits)
    if pid <= 0:
        raise ValueError("descendant job pipe heartbeat PID must be positive")
    line = prefix + digits + mark + nonce_hex + suffix
    if line != descendant_job_pipe_heartbeat(pid, writer_nonce=nonce):
        raise ValueError("descendant job pipe heartbeat failed round-trip")
    # Exact-frame only: refuse pre/post framing garbage around the heartbeat.
    if raw != line:
        raise ValueError(
            "descendant job pipe heartbeat framing garbage "
            "(payload must be exactly one heartbeat line)"
        )
    return pid


# Exact Microsoft-root rejection reason for foreign same-Organization chains.
MICROSOFT_ROOT_POLICY_REJECTED = "microsoft_root_policy_rejected"
UNTRUSTED_CATALOG_REASON = "untrusted_catalog"
CATALOG_MEMBER_HASH_MISMATCH_REASON = "catalog_member_hash_mismatch"

JobInjectFaultAfter = Literal[
    "job_assignment",
    "resume",
    "image_query",
    "pipe_worker_setup",
]

# Maintained production Microsoft-root SHA-256 thumbprint allowlist (empty until GREEN).
PRODUCTION_MICROSOFT_ROOT_SHA256_ALLOWLIST: frozenset[str] = frozenset()

IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True, slots=True)
class VolumeFileIdentity:
    """Stable BY_HANDLE_FILE_INFORMATION volume + file index (not a HANDLE number)."""

    volume_serial: int
    file_index_high: int
    file_index_low: int

    def as_key(self) -> str:
        return f"{self.volume_serial:08x}:{self.file_index_high:08x}:{self.file_index_low:08x}"


@dataclass(slots=True)
class _HandleCloseState:
    """Mutable close bookkeeping kept OUT of frozen identity equality/hash."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    closed: set[int] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class RetainedSystem32Identity:
    """Held no-reparse System32 directory identity + relative wsl.exe leaf.

    Callers own close: use as a context manager or call ``close()`` explicitly.
    ``close()`` is idempotent, thread-safe, and retry-safe if the closer raises
    (a handle is recorded closed only after a successful closer call).
    Close-state is deliberately excluded from equality/hash.
    """

    system32_directory_handle: int
    wsl_exe_handle: int
    directory_identity: VolumeFileIdentity
    wsl_identity: VolumeFileIdentity
    final_path_text: str | None = None
    _closer: Callable[[int], None] | None = None
    _close_state: _HandleCloseState = field(
        default_factory=_HandleCloseState, compare=False, hash=False
    )

    def close(self) -> None:
        closer = self._closer
        if closer is None:
            return
        with self._close_state.lock:
            for handle in (self.wsl_exe_handle, self.system32_directory_handle):
                if handle in (0, -1):
                    continue
                if handle in self._close_state.closed:
                    continue
                closer(handle)
                self._close_state.closed.add(handle)

    def __enter__(self) -> RetainedSystem32Identity:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class WinTrustPolicy:
    dw_ui_choice: int
    fdw_revocation_checks: int
    dw_prov_flags: int
    require_microsoft_root_policy: bool
    accept_test_roots: bool
    production_root_thumbprints: frozenset[str]
    offline_network_canary_required: bool = True


@dataclass(frozen=True, slots=True)
class JobConfinementPolicy:
    kill_on_job_close: bool
    breakaway_ok: bool
    silent_breakaway_ok: bool
    create_breakaway_from_job: bool
    create_suspended: bool = True


@dataclass(frozen=True, slots=True)
class HeldFileTrustProbe:
    """Held-file embedded or catalog signature probe inputs (real HANDLE required).

    Catalog metadata must be derived from the held handle by production — never
    handed via a fabricated ``catalog_context`` dict.
    """

    held_file_handle: int
    choice: int  # WTD_CHOICE_FILE | WTD_CHOICE_CATALOG
    organization_text: str | None = None
    expected_member_hash: bytes | None = None
    member_hash_swap_event: threading.Event | None = None


@dataclass(frozen=True, slots=True)
class ProcessThreadHandles:
    """Real process + primary thread handles owned by the Job fixture."""

    process_handle: int
    thread_handle: int
    pid: int


@dataclass(frozen=True, slots=True)
class SuspendedJobFixture:
    """Real suspended Job + descendant breakaway attempt observables.

    Fixture exposes and owns real Job / parent process / primary thread /
    descendant process/thread handles. Tests own the Event/pipe canary and must
    independently wait/query exit via test-side Win32 helpers — never treat
    production booleans/dicts as authority.

    Callers own handle close via ``close()`` / context manager.
    ``close()`` is idempotent, thread-safe, and retry-safe if the closer raises.
    Close-state is excluded from equality/hash.
    """

    job_handle: int
    parent_process_handle: int
    parent_thread_handle: int
    descendant_process_handle: int
    descendant_thread_handle: int
    parent_pid: int
    descendant_pid: int
    create_flags: int
    notes: tuple[str, ...] = ()
    canary_event: threading.Event | None = None
    canary_pipe_write_fd: int | None = None
    writer_nonce: bytes | None = None
    _closer: Callable[[int], None] | None = None
    _close_state: _HandleCloseState = field(
        default_factory=_HandleCloseState, compare=False, hash=False
    )

    def owned_handles(self) -> tuple[int, ...]:
        return (
            self.job_handle,
            self.parent_process_handle,
            self.parent_thread_handle,
            self.descendant_process_handle,
            self.descendant_thread_handle,
        )

    def close_job_only(self) -> None:
        """Close only the Job handle (KILL_ON_JOB_CLOSE kills members).

        Process/thread handles stay open so tests can independently
        ``WaitForSingleObject`` / ``GetExitCodeProcess`` before ``close()``.
        Idempotent and retry-safe if the closer raises.
        """
        closer = self._closer
        if closer is None:
            return
        with self._close_state.lock:
            handle = self.job_handle
            if handle in (0, -1):
                return
            if handle in self._close_state.closed:
                return
            closer(handle)
            self._close_state.closed.add(handle)

    def close(self) -> None:
        closer = self._closer
        if closer is None:
            return
        with self._close_state.lock:
            for handle in self.owned_handles():
                if handle in (0, -1):
                    continue
                if handle in self._close_state.closed:
                    continue
                closer(handle)
                self._close_state.closed.add(handle)

    def __enter__(self) -> SuspendedJobFixture:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class IdentitySwapObservation:
    """Event-gated parent/leaf substitution observation."""

    retained_parent: VolumeFileIdentity
    retained_leaf: VolumeFileIdentity
    observed_parent: VolumeFileIdentity
    observed_leaf: VolumeFileIdentity
    parent_swap_event: threading.Event | None = None
    leaf_swap_event: threading.Event | None = None


class TrustEvaluator(Protocol):
    def __call__(self, probe: HeldFileTrustProbe) -> dict[str, Any]: ...


class JobFactoryPrimitives(Protocol):
    """Injectable Job/process factory primitives (tests supply; GREEN wires Win32)."""

    def create_job(self) -> int: ...

    def create_process_suspended(
        self,
        *,
        canary_event: threading.Event | None,
        canary_pipe_write_fd: int | None,
        writer_nonce: bytes | None = None,
    ) -> ProcessThreadHandles: ...

    def assign_process_to_job(self, job_handle: int, process_handle: int) -> None: ...

    def resume_thread(self, thread_handle: int) -> None: ...

    def query_process_image(self, process_handle: int) -> str: ...

    def setup_pipe_worker(
        self,
        *,
        parent: ProcessThreadHandles,
        canary_event: threading.Event | None,
        canary_pipe_write_fd: int | None,
        writer_nonce: bytes | None = None,
    ) -> ProcessThreadHandles: ...

    def terminate_process(self, process_handle: int) -> None: ...

    def close_handle(self, handle: int) -> None: ...


def _descendant_writer_python_executable() -> str:
    """Prefer the base interpreter so venv redirector stubs do not mismatch PIDs."""
    base = getattr(sys, "_base_executable", None)
    if isinstance(base, str) and base:
        return base
    return sys.executable


def _descendant_pipe_heartbeat_child_code(writer_nonce: bytes) -> str:
    """Python -c body: write OWN-pid heartbeat to stdout (inherited pipe), then sleep."""
    nonce_hex = bytes(writer_nonce).hex()
    # Inline format — child must not import tensor_grep (may lack PYTHONPATH).
    # stdout is the inherited canary write handle (STARTF_USESTDHANDLES / dup2).
    return (
        "import os,sys,time;"
        f"n=bytes.fromhex({nonce_hex!r});"
        "p=os.getpid();"
        "sys.stdout.buffer.write("
        "b'TG60-JOB-DESCENDANT-HB pid='+str(p).encode()+b' nonce='+n.hex().encode()+b'\\n'"
        ");"
        "sys.stdout.buffer.flush();"
        "time.sleep(3600)"
    )


def spawn_descendant_job_pipe_heartbeat_writer(
    *,
    canary_pipe_write_fd: int | None,
    writer_nonce: bytes,
) -> ProcessThreadHandles:
    """Spawn a descendant that inherits the canary write fd and emits OWN-pid heartbeat.

    The caller (parent) must not write the pipe or set a canary event — the
    descendant alone authenticates writer provenance (Sol R5 / HIGH#5).
    """
    if canary_pipe_write_fd is None or canary_pipe_write_fd < 0:
        raise ValueError("canary_pipe_write_fd required for descendant pipe heartbeat writer")
    if not isinstance(writer_nonce, (bytes, bytearray)):
        raise TypeError("writer_nonce must be bytes")
    nonce = bytes(writer_nonce)
    if len(nonce) < _MIN_WRITER_NONCE_LEN:
        raise ValueError(
            f"writer_nonce must be at least {_MIN_WRITER_NONCE_LEN} bytes "
            "(parent-forgeable short secrets refused)"
        )
    if sys.platform == "win32":
        return _spawn_descendant_pipe_heartbeat_writer_win32(
            canary_pipe_write_fd=canary_pipe_write_fd,
            writer_nonce=nonce,
        )
    return _spawn_descendant_pipe_heartbeat_writer_posix(
        canary_pipe_write_fd=canary_pipe_write_fd,
        writer_nonce=nonce,
    )


def _spawn_descendant_pipe_heartbeat_writer_posix(
    *,
    canary_pipe_write_fd: int,
    writer_nonce: bytes,
) -> ProcessThreadHandles:
    import subprocess

    child_code = _descendant_pipe_heartbeat_child_code(writer_nonce)
    # Replace stdout with the canary write fd so the child inherits it as fd 1.
    proc = subprocess.Popen(
        [_descendant_writer_python_executable(), "-c", child_code],
        stdin=subprocess.DEVNULL,
        stdout=canary_pipe_write_fd,
        stderr=subprocess.DEVNULL,
        close_fds=False,
        pass_fds=(canary_pipe_write_fd,),
    )
    # Synthesize handle slots from the real pid for platform-neutral tests.
    return ProcessThreadHandles(
        process_handle=int(proc.pid),
        thread_handle=int(proc.pid),
        pid=int(proc.pid),
    )


def _spawn_descendant_pipe_heartbeat_writer_win32(
    *,
    canary_pipe_write_fd: int,
    writer_nonce: bytes,
) -> ProcessThreadHandles:
    """Spawn via subprocess so the returned PID is the writer process (not a launcher)."""
    import msvcrt
    import subprocess
    from ctypes import wintypes

    HANDLE_FLAG_INHERIT = 0x00000001
    PROCESS_TERMINATE = 0x0001
    PROCESS_QUERY_INFORMATION = 0x0400
    SYNCHRONIZE = 0x00100000
    PROCESS_ACCESS = PROCESS_TERMINATE | PROCESS_QUERY_INFORMATION | SYNCHRONIZE

    kernel32 = _win_dll("kernel32", use_last_error=True)
    kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE

    write_handle = msvcrt.get_osfhandle(canary_pipe_write_fd)
    if not kernel32.SetHandleInformation(write_handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT):
        raise OSError(f"SetHandleInformation(inherit) failed: {_win_last_error()}")

    child_code = _descendant_pipe_heartbeat_child_code(writer_nonce)
    # stdout=fd makes the canary write end the child's stdout (inherited).
    # Use base executable so a venv redirector stub PID cannot diverge from getpid().
    proc = subprocess.Popen(
        [_descendant_writer_python_executable(), "-c", child_code],
        stdin=subprocess.DEVNULL,
        stdout=canary_pipe_write_fd,
        stderr=subprocess.DEVNULL,
        close_fds=False,
    )
    pid = int(proc.pid)
    h_process = kernel32.OpenProcess(PROCESS_ACCESS, False, pid)
    if not h_process:
        try:
            proc.kill()
        except Exception:
            pass
        raise OSError(f"OpenProcess({pid}) failed: {_win_last_error()}")
    # No separate thread handle from Popen; duplicate process handle for close accounting.
    h_thread = kernel32.OpenProcess(PROCESS_ACCESS, False, pid)
    if not h_thread:
        kernel32.CloseHandle(h_process)
        try:
            proc.kill()
        except Exception:
            pass
        raise OSError(f"OpenProcess(thread-slot {pid}) failed: {_win_last_error()}")
    return ProcessThreadHandles(
        process_handle=int(h_process),
        thread_handle=int(h_thread),
        pid=pid,
    )


def terminate_spawned_descendant(pid: int) -> None:
    """Best-effort terminate a spawn_descendant_* child by PID (test cleanup)."""
    if pid <= 0:
        return
    if sys.platform == "win32":
        kernel32 = _win_dll("kernel32", use_last_error=True)
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
        return
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def offline_wintrust_policy() -> WinTrustPolicy:
    """Exact offline WinTrust + Microsoft-root policy (platform-neutral).

    Behaviorless: returns a deliberately wrong policy so semantic REDs fail
    until production pins the Round-60 flags and offline network canary.
    """
    return WinTrustPolicy(
        dw_ui_choice=WTD_UI_NONE,
        fdw_revocation_checks=0,  # wrong: must be WTD_REVOKE_WHOLECHAIN
        dw_prov_flags=0,  # wrong: must include cache-only + exclude-root
        require_microsoft_root_policy=False,
        accept_test_roots=True,
        production_root_thumbprints=frozenset(),
        offline_network_canary_required=False,
    )


def job_confinement_policy() -> JobConfinementPolicy:
    """Kill-on-close non-breakaway Job policy (platform-neutral).

    Behaviorless: enables breakaway so REDs fail until production clears both
    Job breakaway limit flags and CREATE_BREAKAWAY_FROM_JOB.
    """
    return JobConfinementPolicy(
        kill_on_job_close=True,
        breakaway_ok=True,
        silent_breakaway_ok=True,
        create_breakaway_from_job=True,
        create_suspended=False,
    )


def close_handle(handle: int) -> None:
    """Behaviorless CloseHandle primitive. GREEN wires real Windows CloseHandle."""
    _ = handle
    raise NotImplementedError("close_handle requires Windows CloseHandle")


def volume_file_identity_from_handle(handle: int) -> VolumeFileIdentity:
    """Primitive: BY_HANDLE_FILE_INFORMATION for a retained HANDLE."""
    _ = handle
    raise NotImplementedError("volume_file_identity_from_handle requires Windows BY_HANDLE API")


def open_system32_directory_nofollow(*, system_root: str | None = None) -> int:
    """Primitive: GetSystemDirectoryW + no-reparse directory open. Ignores SystemRoot poison."""
    _ = system_root
    raise NotImplementedError(
        "open_system32_directory_nofollow requires Windows GetSystemDirectoryW"
    )


def open_wsl_exe_nofollow(directory_handle: int) -> int:
    """Primitive: open wsl.exe relative to retained System32 directory handle."""
    _ = directory_handle
    raise NotImplementedError("open_wsl_exe_nofollow requires Windows handle-relative open")


def resolve_system32_identity(
    *,
    system_root: str | None = None,
) -> RetainedSystem32Identity:
    """Resolve System32 via GetSystemDirectoryW and retain no-reparse handles.

    Never fabricates handles on non-Windows hosts. Composes from production
    primitives only; production must ignore SystemRoot poison and compare
    volume/file identities (not HANDLE number equality).

    Ownership: wires ``close_handle`` as the retained closer. On partial failure
    (leaf open or identity query) already-opened handles are closed before raise.
    """
    if not IS_WINDOWS:
        raise NotImplementedError(
            "retained System32 identity requires a Windows GetSystemDirectoryW "
            "resolver; Linux must not fabricate handles"
        )
    dir_handle = open_system32_directory_nofollow(system_root=system_root)
    try:
        leaf_handle = open_wsl_exe_nofollow(dir_handle)
    except BaseException as original:
        try:
            close_handle(dir_handle)
        except BaseException as cleanup_err:
            try:
                original.add_note(f"cleanup also failed: {cleanup_err!r}")
            except Exception:
                pass
        raise original
    try:
        directory_identity = volume_file_identity_from_handle(dir_handle)
        wsl_identity = volume_file_identity_from_handle(leaf_handle)
    except BaseException as original:
        for handle in (leaf_handle, dir_handle):
            try:
                close_handle(handle)
            except BaseException as cleanup_err:
                try:
                    original.add_note(f"cleanup also failed: {cleanup_err!r}")
                except Exception:
                    pass
        raise original
    return RetainedSystem32Identity(
        system32_directory_handle=dir_handle,
        wsl_exe_handle=leaf_handle,
        directory_identity=directory_identity,
        wsl_identity=wsl_identity,
        _closer=close_handle,
    )


def evaluate_embedded_or_catalog_trust(
    probe: HeldFileTrustProbe,
    *,
    evaluator: TrustEvaluator | None = None,
    offline_network_canary: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Handle-bound embedded/catalog trust evaluation.

    Behaviorless: no fabricated security verdict. Raises until GREEN implements
    WinVerifyTrust on a real held handle under offline Microsoft-root policy.
    Organization text alone is never trust. ``evaluator`` is accepted only for
    the offline-network-canary negative control in tests.
    """
    if probe.held_file_handle in (0, 1, -1):
        raise ValueError("fake/null HANDLE refused; supply a real retained file handle")
    if evaluator is not None:
        if offline_network_canary is not None and offline_network_canary():
            return {"trusted": False, "reason": "offline_network_canary_failed"}
        return evaluator(probe)
    raise NotImplementedError(
        "evaluate_embedded_or_catalog_trust requires WinVerifyTrust on a held handle; "
        "no organization-text or boolean security verdict is shipped"
    )


def parent_or_leaf_identity_unchanged_after_swap(
    observation: IdentitySwapObservation,
) -> bool:
    """Event-gated parent/leaf identity revalidation via volume/file identities.

    Behaviorless: always True (misses swaps). Production must wait on swap events
    when provided and compare VolumeFileIdentity keys, never HANDLE numbers.
    """
    _ = observation
    return True


def _close_job_handles(
    handles: list[int],
    closer: Callable[[int], None],
    *,
    original: BaseException | None = None,
) -> None:
    for handle in handles:
        if handle in (0, -1):
            continue
        try:
            closer(handle)
        except BaseException as cleanup_err:
            if original is not None:
                try:
                    original.add_note(f"cleanup also failed: {cleanup_err!r}")
                except Exception:
                    pass


class DefaultJobFactoryPrimitives:
    """Default Job/process factory (production front door when factory=None).

    Minimal real Win32 CreateJobObjectW / CreateProcessW path. ``close_handle``
    delegates to the module production closer so default-path cleanup is
    independently observable (A63 / HIGH#5).
    """

    def create_job(self) -> int:
        if not IS_WINDOWS:
            raise OSError("CreateJobObjectW requires Windows")
        import ctypes

        kernel32 = _win_dll("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(f"CreateJobObjectW failed: {_win_last_error()}")
        return int(handle)

    def create_process_suspended(
        self,
        *,
        canary_event: threading.Event | None,
        canary_pipe_write_fd: int | None,
        writer_nonce: bytes | None = None,
    ) -> ProcessThreadHandles:
        _ = canary_event
        # Sol R5: pipe-worker path — real descendant inherits write fd and emits
        # OWN-pid+nonce heartbeat. Parent must not forge the payload.
        if writer_nonce is not None:
            return spawn_descendant_job_pipe_heartbeat_writer(
                canary_pipe_write_fd=canary_pipe_write_fd,
                writer_nonce=writer_nonce,
            )
        if not IS_WINDOWS:
            raise OSError("CreateProcessW requires Windows")
        import ctypes
        from ctypes import wintypes

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD),
                ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD),
                ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD),
                ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE),
                ("hStdError", wintypes.HANDLE),
            ]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", wintypes.HANDLE),
                ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD),
            ]

        kernel32 = _win_dll("kernel32", use_last_error=True)
        kernel32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        kernel32.CreateProcessW.restype = wintypes.BOOL
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        pi = PROCESS_INFORMATION()
        # Parent placeholder: suspended, no pipe inheritance (descendant writer is separate).
        _ = canary_pipe_write_fd
        cmdline = ctypes.create_unicode_buffer("cmd.exe /c exit 0")
        ok = kernel32.CreateProcessW(
            None,
            cmdline,
            None,
            None,
            False,
            CREATE_SUSPENDED,
            None,
            None,
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if not ok:
            raise OSError(f"CreateProcessW failed: {_win_last_error()}")
        return ProcessThreadHandles(
            process_handle=int(pi.hProcess),
            thread_handle=int(pi.hThread),
            pid=int(pi.dwProcessId),
        )

    def assign_process_to_job(self, job_handle: int, process_handle: int) -> None:
        if not IS_WINDOWS:
            raise OSError("AssignProcessToJobObject requires Windows")
        import ctypes

        kernel32 = _win_dll("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_bool
        if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise OSError(f"AssignProcessToJobObject failed: {_win_last_error()}")

    def resume_thread(self, thread_handle: int) -> None:
        if not IS_WINDOWS:
            raise OSError("ResumeThread requires Windows")
        import ctypes

        kernel32 = _win_dll("kernel32", use_last_error=True)
        kernel32.ResumeThread.argtypes = [ctypes.c_void_p]
        kernel32.ResumeThread.restype = ctypes.c_uint32
        result = kernel32.ResumeThread(thread_handle)
        if result == 0xFFFFFFFF:
            raise OSError(f"ResumeThread failed: {_win_last_error()}")

    def query_process_image(self, process_handle: int) -> str:
        if not IS_WINDOWS:
            raise OSError("QueryFullProcessImageNameW requires Windows")
        import ctypes
        from ctypes import wintypes

        kernel32 = _win_dll("kernel32", use_last_error=True)
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        if not kernel32.QueryFullProcessImageNameW(process_handle, 0, buf, ctypes.byref(size)):
            raise OSError(f"QueryFullProcessImageNameW failed: {_win_last_error()}")
        return buf.value

    def setup_pipe_worker(
        self,
        *,
        parent: ProcessThreadHandles,
        canary_event: threading.Event | None,
        canary_pipe_write_fd: int | None,
        writer_nonce: bytes | None = None,
    ) -> ProcessThreadHandles:
        # Sol R5 HIGH#5: descendant inherits pipe and writes OWN pid+nonce.
        # Parent must NOT write the canary pipe or signal the canary event.
        if not isinstance(parent, ProcessThreadHandles):
            raise TypeError("parent must be ProcessThreadHandles")
        if parent.pid <= 0:
            raise ValueError("parent pid must be positive")
        if parent.process_handle in (0, -1) or parent.thread_handle in (0, -1):
            raise OSError("parent handles invalid")
        if writer_nonce is None:
            raise ValueError("writer_nonce required for pipe worker setup")
        nonce = bytes(writer_nonce)
        if len(nonce) < _MIN_WRITER_NONCE_LEN:
            raise ValueError(
                f"writer_nonce must be at least {_MIN_WRITER_NONCE_LEN} bytes "
                "(parent-forgeable short secrets refused)"
            )
        if canary_pipe_write_fd is None or canary_pipe_write_fd < 0:
            raise ValueError("canary_pipe_write_fd required for pipe worker setup")
        # canary_event is caller-owned; parent must not signal it (Sol R5).
        _ = canary_event
        worker = self.create_process_suspended(
            canary_event=canary_event,
            canary_pipe_write_fd=canary_pipe_write_fd,
            writer_nonce=nonce,
        )
        if worker.pid == parent.pid or worker.process_handle == parent.process_handle:
            raise OSError("pipe worker must be a distinct process from parent")
        return worker

    def terminate_process(self, process_handle: int) -> None:
        if not IS_WINDOWS:
            raise OSError("TerminateProcess requires Windows")
        import ctypes

        kernel32 = _win_dll("kernel32", use_last_error=True)
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.TerminateProcess.restype = ctypes.c_bool
        if not kernel32.TerminateProcess(process_handle, 1):
            raise OSError(f"TerminateProcess failed: {_win_last_error()}")

    def close_handle(self, handle: int) -> None:
        close_handle(handle)


def windows_job_factory_primitives() -> JobFactoryPrimitives:
    """Default Windows Job/process factory seam.

    Returns ``DefaultJobFactoryPrimitives`` — the real default factory path.
    Individual create primitives fail closed until GREEN wires Win32.
    """
    return DefaultJobFactoryPrimitives()


def create_suspended_job_with_descendant_breakaway(
    *,
    canary_event: threading.Event | None = None,
    canary_pipe_write_fd: int | None = None,
    writer_nonce: bytes | None = None,
    inject_fault_after: JobInjectFaultAfter | None = None,
    factory: JobFactoryPrimitives | None = None,
) -> SuspendedJobFixture:
    """Create a real suspended Job and attempt a descendant breakaway.

    Orchestration over injectable primitives. When ``factory`` is omitted on
    Windows, uses ``windows_job_factory_primitives()`` (behaviorless until
    GREEN). Non-Windows hosts without an injected factory fail closed (no fake
    PID / boolean success). On injected BaseException after CreateProcessW
    steps (job assignment / resume / image query / pipe-worker setup),
    terminates opened processes, closes owned handles, and re-raises the
    original error (cleanup notes attached via ``add_note``).

    Default-factory path always binds module ``close_handle`` as the fixture
    closer so cleanup is independently observable (A63 / HIGH#5) — not only via
    a test-owned factory private list, and not via heartbeat/EOF alone.

    GREEN must wire a real Windows factory. Tests own the Event/pipe canary and
    independently wait/query exit. The default-factory Windows integration
    requires the descendant worker to write
    ``descendant_job_pipe_heartbeat(descendant_pid, writer_nonce=...)``
    (PID + nonce; never parent-forgeable PID-only text) before Job close;
    Event clear is never proof.
    """
    using_default_factory = factory is None
    if factory is None:
        if not IS_WINDOWS:
            _ = canary_event, canary_pipe_write_fd, inject_fault_after, writer_nonce
            raise NotImplementedError(
                "suspended Job + descendant breakaway fixture requires a real "
                "Windows process/Job factory; fake PIDs are insufficient"
            )
        factory = windows_job_factory_primitives()

    # HIGH#5: default path cleanup goes through the production close_handle seam.
    closer = close_handle if using_default_factory else factory.close_handle
    job_handle = factory.create_job()
    opened: list[int] = [job_handle]
    parent: ProcessThreadHandles | None = None
    descendant: ProcessThreadHandles | None = None
    try:
        parent = factory.create_process_suspended(
            canary_event=canary_event,
            canary_pipe_write_fd=canary_pipe_write_fd,
        )
        opened.extend([parent.process_handle, parent.thread_handle])
        factory.assign_process_to_job(job_handle, parent.process_handle)
        if inject_fault_after == "job_assignment":
            raise BaseException("injected fault after job assignment")
        factory.resume_thread(parent.thread_handle)
        if inject_fault_after == "resume":
            raise BaseException("injected fault after resume")
        _ = factory.query_process_image(parent.process_handle)
        if inject_fault_after == "image_query":
            raise BaseException("injected fault after image query")
        # HIGH#4: factory path mints nonce when caller omits it (not caller-only).
        effective_nonce = writer_nonce if writer_nonce is not None else mint_writer_nonce()
        descendant = factory.setup_pipe_worker(
            parent=parent,
            canary_event=canary_event,
            canary_pipe_write_fd=canary_pipe_write_fd,
            writer_nonce=effective_nonce,
        )
        opened.extend([descendant.process_handle, descendant.thread_handle])
        if inject_fault_after == "pipe_worker_setup":
            raise BaseException("injected fault after pipe-worker setup")
        return SuspendedJobFixture(
            job_handle=job_handle,
            parent_process_handle=parent.process_handle,
            parent_thread_handle=parent.thread_handle,
            descendant_process_handle=descendant.process_handle,
            descendant_thread_handle=descendant.thread_handle,
            parent_pid=parent.pid,
            descendant_pid=descendant.pid,
            create_flags=CREATE_SUSPENDED,
            canary_event=canary_event,
            canary_pipe_write_fd=canary_pipe_write_fd,
            writer_nonce=effective_nonce,
            _closer=closer,
        )
    except BaseException as original:
        for proc in (descendant, parent):
            if proc is not None:
                try:
                    factory.terminate_process(proc.process_handle)
                except BaseException as cleanup_err:
                    try:
                        original.add_note(f"terminate also failed: {cleanup_err!r}")
                    except Exception:
                        pass
        _close_job_handles(list(reversed(opened)), closer, original=original)
        raise original


def load_test_root_signed_foreign_same_org_chain_fixture(path: str | Path) -> HeldFileTrustProbe:
    """Load a real test-root-signed PE fixture for same-Organization foreign chain RED.

    Organization text alone is never trust. Behaviorless until a real signed PE
    fixture path is supplied and verified under Microsoft-root policy.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"foreign same-Organization chain fixture missing: {p}")
    raise NotImplementedError(
        "load_test_root_signed_foreign_same_org_chain_fixture requires a real "
        "test-root-signed PE open + HeldFileTrustProbe (not organization text)"
    )


def evaluate_untrusted_catalog(
    probe: HeldFileTrustProbe,
) -> dict[str, Any]:
    """Exact untrusted-catalog arm — must return reason ``untrusted_catalog``.

    Behaviorless: never fabricates a trusted verdict or a reason union.
    """
    if probe.held_file_handle in (0, 1, -1):
        raise ValueError("fake/null HANDLE refused")
    if probe.choice != WTD_CHOICE_CATALOG:
        raise ValueError("untrusted catalog arm requires WTD_CHOICE_CATALOG")
    raise NotImplementedError(
        "evaluate_untrusted_catalog requires WinVerifyTrust catalog rejection "
        f"with exact reason {UNTRUSTED_CATALOG_REASON!r}"
    )


def evaluate_catalog_member_hash_mismatch(
    probe: HeldFileTrustProbe,
) -> dict[str, Any]:
    """Exact catalog-member-hash mismatch arm — distinct from untrusted catalog.

    Uses ``expected_member_hash`` and optional Event-gated substitution so an
    implementation that always returns one reason fails. Behaviorless until GREEN.
    """
    if probe.held_file_handle in (0, 1, -1):
        raise ValueError("fake/null HANDLE refused")
    if probe.choice != WTD_CHOICE_CATALOG:
        raise ValueError("catalog member-hash mismatch arm requires WTD_CHOICE_CATALOG")
    if probe.expected_member_hash is None:
        raise ValueError("catalog member-hash mismatch requires expected_member_hash")
    if probe.member_hash_swap_event is not None:
        # GREEN must wait on the swap event before hashing; behaviorless ignores.
        _ = probe.member_hash_swap_event
    raise NotImplementedError(
        "evaluate_catalog_member_hash_mismatch requires held-handle member hash "
        f"compare with exact reason {CATALOG_MEMBER_HASH_MISMATCH_REASON!r}"
    )
