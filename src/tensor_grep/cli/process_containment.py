"""Contain a spawned provider's WHOLE process tree so cleanup can kill descendants.

Killing only the direct child leaves grandchildren holding the inherited stdout/stderr
handles: reader threads stay blocked and ``stream.close()`` waits on the BufferedReader
lock forever (the `tg doctor` LSP-probe hang). Platform primitives, by name:

* Windows: a Job Object (``CreateJobObjectW`` + ``SetInformationJobObject(
  JobObjectExtendedLimitInformation)`` with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and
  NEITHER ``BREAKAWAY_OK`` flag). The child is spawned ``CREATE_SUSPENDED``, assigned with
  ``AssignProcessToJobObject`` and only then resumed (``NtResumeProcess``), so it cannot
  fork before it is contained. Teardown is ``TerminateJobObject`` (called once); survivors
  are read back with ``QueryInformationJobObject(JobObjectBasicAccountingInformation)``;
  closing the job handle also kills every member.
* POSIX: ``start_new_session=True`` + ``os.killpg(pgid, SIGTERM)`` then ``SIGKILL``;
  survivors are probed with ``os.killpg(pgid, 0)``. The level is honestly ``process_group``,
  NOT whole-tree: a descendant that calls ``setsid`` leaves the group and ``killpg`` misses it.
  That escape is DETECTED (the provider's pipes stay held after the group is dead) and
  reported as ``ESCAPE_MESSAGE``; it cannot be prevented without cgroups (needs delegation).

FAIL CLOSED: if reliable containment is unavailable (Job Object cannot be created or
assigned, or ``os.killpg`` is missing) the provider is NOT launched and
``ContainmentUnavailableError`` is raised. Recursive PID enumeration (psutil / ``taskkill /T``)
cannot find the descendants of an already-dead root, so it is deliberately not a fallback.
The one exception is a Popen *test double* with no OS handle: it is returned with a
``direct_child_only`` containment (``degraded=True``) so callers still terminate/kill it.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

LEVEL_JOB_OBJECT = "job_object"
LEVEL_PROCESS_GROUP = "process_group"
LEVEL_DIRECT_ONLY = "direct_child_only"

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS = 1


ESCAPE_MESSAGE = "a descendant outside the provider's process group still holds its pipes"


class ContainmentUnavailableError(OSError):
    """Reliable whole-tree containment could not be established; nothing was launched."""


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

    class _Accounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
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
    k32.QueryInformationJobObject.restype = wintypes.BOOL
    k32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
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
    return k32, nt, _Extended, _Accounting


def _create_kill_on_close_job() -> tuple[Any, Any, Any]:
    k32, nt, extended, _accounting = _win_api()
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


def _job_active_processes(k32: Any, job: Any) -> int | None:
    accounting = _win_api()[3]()
    ok = k32.QueryInformationJobObject(
        job,
        _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
        ctypes.byref(accounting),
        ctypes.sizeof(accounting),
        None,
    )
    return int(accounting.ActiveProcesses) if ok else None


class Containment:
    """Handle that can kill a spawned process and every descendant.

    ``terminate``/``kill``/``survivors`` return a list of failure strings (empty = clean).
    A Job Object is terminated at most once, however many times these are called.
    """

    def __init__(self, level: str, *, degraded: bool, pid: int, job: Any = None, k32: Any = None):
        self.level = level
        self.degraded = degraded
        self.pid = pid
        self._job = job
        self._k32 = k32
        self._lock = threading.Lock()
        self._job_terminated = False

    def terminate(self) -> list[str]:
        """Begin teardown of the whole tree (Windows has no polite tree signal: forceful)."""
        if self.level == LEVEL_PROCESS_GROUP:
            return self._killpg(signal.SIGTERM, "tree terminate")
        return self.kill()

    def kill(self) -> list[str]:
        """Force-kill the whole tree. Never raises; the Job is terminated only once."""
        if self.level == LEVEL_PROCESS_GROUP:
            return self._killpg(getattr(signal, "SIGKILL", signal.SIGTERM), "tree kill")
        with self._lock:
            if self._job is None or self._job_terminated:
                return []
            self._job_terminated = True
            if not self._k32.TerminateJobObject(self._job, 1):
                return [f"TerminateJobObject failed (winerror {ctypes.get_last_error()})"]  # type: ignore[attr-defined]
        return []

    def survivors(self, deadline: float) -> list[str]:
        """Poll (until the absolute monotonic ``deadline``) for members that outlived the kill."""
        while True:
            alive = self._alive_count()
            if alive is None:  # a failed query is NOT "zero survivors"
                return ["could not verify the provider tree exited (job query failed)"]
            if not alive:
                return []
            if time.monotonic() >= deadline:
                return [
                    f"{alive} process(es) of the provider tree still alive (pgid/job {self.pid})"
                ]
            time.sleep(0.01)

    def release(self) -> None:
        """Close the job handle (KILL_ON_JOB_CLOSE kills any remaining member)."""
        with self._lock:
            if self._job is not None:
                self._k32.CloseHandle(self._job)
                self._job = None

    def _alive_count(self) -> int | None:
        if self.level == LEVEL_PROCESS_GROUP:
            if self.pid <= 0:
                return 0
            try:
                os.killpg(self.pid, 0)  # type: ignore[attr-defined,unused-ignore]
            except ProcessLookupError:
                return 0
            except OSError:
                return 1
            return 1
        with self._lock:
            if self._job is None:
                return 0
            active = _job_active_processes(self._k32, self._job)
        return active

    def _killpg(self, sig: int, what: str) -> list[str]:
        if self.pid <= 0:  # killpg(0) would signal OUR OWN group
            return []
        try:
            os.killpg(self.pid, sig)  # type: ignore[attr-defined,unused-ignore]
        except ProcessLookupError:
            return []
        except OSError as exc:
            return [f"{what} failed: {exc}"]
        return []


def spawn_contained(
    argv: list[str], **popen_kwargs: Any
) -> tuple[subprocess.Popen[Any], Containment]:
    """``subprocess.Popen`` whose whole descendant tree is killable via the returned handle.

    Raises ``ContainmentUnavailableError`` (provider NOT running) if that cannot be guaranteed.
    """
    if sys.platform != "win32":
        if not hasattr(os, "killpg"):
            raise ContainmentUnavailableError("os.killpg is unavailable: no process-group kill")
        process = subprocess.Popen(argv, start_new_session=True, **popen_kwargs)
        return process, Containment(LEVEL_PROCESS_GROUP, degraded=False, pid=_pid_of(process))

    try:
        job, k32, nt = _create_kill_on_close_job()
    except (OSError, AttributeError, ImportError) as exc:
        raise ContainmentUnavailableError(f"Job Object could not be created: {exc}") from exc
    flags = int(popen_kwargs.pop("creationflags", 0)) | _CREATE_SUSPENDED
    owned = True  # until ownership of the Job handle moves into the Containment
    process: subprocess.Popen[Any] | None = None
    try:
        process = subprocess.Popen(argv, creationflags=flags, **popen_kwargs)
        raw_handle = getattr(process, "_handle", None)
        if raw_handle is None:  # a Popen test double: no OS handle to contain
            return process, Containment(LEVEL_DIRECT_ONLY, degraded=True, pid=_pid_of(process))
        handle = int(raw_handle)
        if not k32.AssignProcessToJobObject(job, handle):
            raise ContainmentUnavailableError("AssignProcessToJobObject refused the provider")
        if nt.NtResumeProcess(handle) < 0:
            raise ContainmentUnavailableError("NtResumeProcess failed for a suspended provider")
        contained = Containment(LEVEL_JOB_OBJECT, degraded=False, pid=process.pid, job=job, k32=k32)
        owned = False
        return process, contained
    except BaseException:
        if process is not None:  # never leave a suspended/orphaned child behind
            _reap_bounded(process)
        raise
    finally:
        if owned:
            k32.CloseHandle(job)  # KILL_ON_JOB_CLOSE also reaps an assigned suspended child


def _reap_bounded(process: Any) -> None:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _pid_of(process: Any) -> int:
    pid = getattr(process, "pid", None)
    return pid if isinstance(pid, int) else 0


def _call(fn: Callable[[], Any], what: str, errors: list[str]) -> None:
    try:
        fn()
    except OSError as exc:
        errors.append(f"{what} failed: {exc}")


def close_streams_bounded(streams: list[Any], timeout_seconds: float) -> list[str]:
    """Close each stream on a daemon thread; abandon (and report) any close still blocked.

    Kill the process tree FIRST: once every holder of the pipe is dead, close() returns.
    """
    started: list[tuple[threading.Thread, str]] = []
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
        started.append((thread, f"stream[{index}]"))
    end = time.monotonic() + max(timeout_seconds, 0.0)
    abandoned: list[str] = []
    for thread, name in started:
        thread.join(timeout=max(end - time.monotonic(), 0.0))
        if thread.is_alive():
            abandoned.append(name)
    return abandoned


def teardown_provider(
    process: Any,
    containment: Containment | None,
    *,
    deadline: float,
    graceful: Callable[[float], None] | None = None,
) -> list[str]:
    """Stop a provider and its whole tree inside ONE absolute monotonic ``deadline``.

    Order: bounded graceful shutdown (own thread) -> terminate tree, then the direct child
    (each at most once) -> wait -> escalate to kill (once) -> final tree kill, survivor check,
    release -> close stdin/stdout/stderr TOGETHER, bounded. Returns failure strings.
    """

    def left() -> float:
        return max(deadline - time.monotonic(), 0.0)

    errors: list[str] = []
    if graceful is not None:
        budget = left() / 2.0
        worker = threading.Thread(target=graceful, args=(budget,), daemon=True)
        worker.start()
        worker.join(timeout=budget)  # an abandoned graceful thread dies when the pipes do
    if containment is not None:
        errors += containment.terminate()
    _call(process.terminate, "terminate", errors)
    try:
        process.wait(timeout=left())
    except subprocess.TimeoutExpired:
        if containment is not None:
            errors += containment.kill()
        _call(process.kill, "kill", errors)
        try:
            process.wait(timeout=left())
        except subprocess.TimeoutExpired:
            errors.append("direct child did not exit after kill")
        except OSError as exc:
            errors.append(f"wait after kill failed: {exc}")
    except OSError as exc:
        errors.append(f"wait failed: {exc}")
    if containment is not None:
        errors += containment.kill()  # stragglers that outlived the leader (Job: no-op)
        errors += containment.survivors(deadline)
        containment.release()
    streams = [process.stdin, process.stdout, process.stderr]
    group_only = containment is not None and containment.level == LEVEL_PROCESS_GROUP
    for name in close_streams_bounded(streams, left()):
        if group_only and name in ("stream[1]", "stream[2]"):
            # The group is dead yet its output pipe is still held: a descendant left the
            # process group (setsid). POSIX has no general primitive to stop that; detect it.
            errors.append(ESCAPE_MESSAGE)
        else:
            errors.append(f"pipe close abandoned ({name})")
    return list(dict.fromkeys(errors))


def cleanup_budget_seconds(
    request_timeout: float, default_stop: float, grace: float | None
) -> float:
    """Total teardown budget: the explicit ``grace`` slice, else twice the default stop bound."""
    if grace is not None:
        return max(float(grace), 0.05)
    return 2.0 * max(min(max(float(request_timeout), 0.0), default_stop), 0.05)
