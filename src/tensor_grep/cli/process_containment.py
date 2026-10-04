"""Contain a spawned provider's WHOLE process tree so cleanup can kill descendants.

Killing only the direct child leaves grandchildren holding the inherited stdout/stderr
handles: reader threads stay blocked and ``stream.close()`` waits on the BufferedReader
lock forever (the `tg doctor` LSP-probe hang). Platform primitives, by name:

* Windows: a Job Object (``CreateJobObjectW`` + ``SetInformationJobObject(
  JobObjectExtendedLimitInformation)`` with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and
  NEITHER ``BREAKAWAY_OK`` flag). The child is spawned ``CREATE_SUSPENDED``, assigned with
  ``AssignProcessToJobObject`` and only then resumed (``NtResumeProcess``), so it cannot
  fork before it is contained. Teardown is ``TerminateJobObject``; closing the job handle
  also kills every member.
* POSIX: ``start_new_session=True`` + ``os.killpg(pgid, SIGTERM)`` then ``SIGKILL``.
* Degraded (Job Object unavailable or assignment refused): psutil recursive kill, then
  ``taskkill /T /F``. ``Containment.level`` / ``degraded`` record which level is in force --
  never a silent fail-open.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
from typing import Any

LEVEL_JOB_OBJECT = "job_object"
LEVEL_PROCESS_GROUP = "process_group"
LEVEL_PSUTIL = "psutil_tree_kill"
LEVEL_TASKKILL = "taskkill_tree"

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


def _win_api() -> Any:
    """kernel32/ntdll bindings with explicit 64-bit-safe prototypes (Windows only)."""
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("a", "b", "c", "d", "e", "f")]

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("Basic", _Basic),
            ("Io", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    nt = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
    nt.NtResumeProcess.restype = ctypes.c_long
    nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
    return k32, nt, _Extended


def _create_kill_on_close_job() -> tuple[Any, Any, Any]:
    k32, nt, extended = _win_api()
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")  # type: ignore[attr-defined]
    info = extended()
    info.Basic.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE  # no BREAKAWAY_OK flags
    ok = k32.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        err = ctypes.get_last_error()  # type: ignore[attr-defined]
        k32.CloseHandle(job)
        raise OSError(err, "SetInformationJobObject failed")
    return job, k32, nt


class Containment:
    """Handle that can kill a spawned process and every descendant."""

    def __init__(self, level: str, *, degraded: bool, pid: int, job: Any = None, k32: Any = None):
        self.level = level
        self.degraded = degraded
        self.pid = pid
        self._job = job
        self._k32 = k32
        self._lock = threading.Lock()

    def terminate(self) -> None:
        """Begin teardown of the whole tree (Windows has no polite tree signal: forceful)."""
        if self.level == LEVEL_PROCESS_GROUP:
            self._killpg(signal.SIGTERM)
        else:
            self.kill()

    def kill(self) -> None:
        """Force-kill the whole tree. Idempotent; never raises."""
        if self.level == LEVEL_PROCESS_GROUP:
            self._killpg(getattr(signal, "SIGKILL", signal.SIGTERM))
            return
        with self._lock:
            if self._job is not None:
                self._k32.TerminateJobObject(self._job, 1)
                return
        self._fallback_tree_kill()

    def release(self) -> None:
        """Close the job handle (KILL_ON_JOB_CLOSE kills any remaining member)."""
        with self._lock:
            if self._job is not None:
                self._k32.CloseHandle(self._job)
                self._job = None

    def _killpg(self, sig: int) -> None:
        if self.pid <= 0:  # killpg(0) would signal OUR OWN group
            return
        try:
            os.killpg(self.pid, sig)  # type: ignore[attr-defined,unused-ignore]
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _fallback_tree_kill(self) -> None:
        if self.pid <= 0:  # unknown pid (test double): never target pid 0 / System Idle
            return
        psutil = _import_psutil() if self.level == LEVEL_PSUTIL else None
        if psutil is not None:
            try:
                root = psutil.Process(self.pid)
                victims = [*root.children(recursive=True), root]
                for victim in victims:
                    try:
                        victim.kill()
                    except psutil.Error:
                        pass
                psutil.wait_procs(victims, timeout=2)
                return
            except (OSError, psutil.Error):
                pass
        root_dir = os.environ.get("SystemRoot", r"C:\Windows")
        try:
            subprocess.run(
                [
                    os.path.join(root_dir, "System32", "taskkill.exe"),
                    "/PID",
                    str(self.pid),
                    "/T",
                    "/F",
                ],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def spawn_contained(
    argv: list[str], **popen_kwargs: Any
) -> tuple[subprocess.Popen[Any], Containment]:
    """``subprocess.Popen`` whose whole descendant tree is killable via the returned handle."""
    if sys.platform != "win32":
        process = subprocess.Popen(argv, start_new_session=True, **popen_kwargs)
        return process, Containment(LEVEL_PROCESS_GROUP, degraded=False, pid=_pid_of(process))

    try:
        job, k32, nt = _create_kill_on_close_job()
    except (OSError, AttributeError, ImportError):
        process = subprocess.Popen(argv, **popen_kwargs)
        return process, _degraded(_pid_of(process))
    flags = int(popen_kwargs.pop("creationflags", 0)) | _CREATE_SUSPENDED
    try:
        process = subprocess.Popen(argv, creationflags=flags, **popen_kwargs)
    except BaseException:
        k32.CloseHandle(job)
        raise
    raw_handle = getattr(process, "_handle", None)
    if raw_handle is None:  # not a real Windows Popen (test double): cannot assign; report it
        k32.CloseHandle(job)
        return process, _degraded(_pid_of(process))
    handle = int(raw_handle)
    assigned = bool(k32.AssignProcessToJobObject(job, handle))
    resumed = nt.NtResumeProcess(handle) >= 0
    if not resumed:
        process.kill()
        k32.CloseHandle(job)
        raise OSError("NtResumeProcess failed for a CREATE_SUSPENDED provider")
    if not assigned:
        k32.CloseHandle(job)
        return process, _degraded(_pid_of(process))
    return process, Containment(LEVEL_JOB_OBJECT, degraded=False, pid=process.pid, job=job, k32=k32)


def _import_psutil() -> Any:
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def _pid_of(process: Any) -> int:
    pid = getattr(process, "pid", None)
    return pid if isinstance(pid, int) else 0


def _degraded(pid: int) -> Containment:
    try:
        import psutil  # noqa: F401

        return Containment(LEVEL_PSUTIL, degraded=True, pid=pid)
    except ImportError:
        return Containment(LEVEL_TASKKILL, degraded=True, pid=pid)


def close_streams_bounded(streams: list[Any], timeout_seconds: float) -> list[str]:
    """Close each stream on a daemon thread; abandon (and report) any close still blocked.

    Kill the process tree FIRST: once every holder of the pipe is dead, close() returns.
    """
    abandoned: list[str] = []
    deadline_threads: list[tuple[threading.Thread, str]] = []
    for index, stream in enumerate(streams):
        if stream is None:
            continue

        def _close(target: Any = stream) -> None:
            try:
                target.close()
            except (OSError, ValueError):
                pass

        thread = threading.Thread(target=_close, daemon=True)
        thread.start()
        deadline_threads.append((thread, f"stream[{index}]"))
    import time

    end = time.monotonic() + max(timeout_seconds, 0.0)
    for thread, name in deadline_threads:
        thread.join(timeout=max(end - time.monotonic(), 0.0))
        if thread.is_alive():
            abandoned.append(name)
    return abandoned
