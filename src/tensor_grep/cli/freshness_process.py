"""Bounded capture and whole-process-tree cleanup for doctor freshness probes."""

from __future__ import annotations

import logging
import os
import select
import subprocess
import sys
import time

from tensor_grep.cli import process_containment

_LOGGER = logging.getLogger(__name__)
_MAX_OUTPUT_BYTES = 256 * 1024
_READ_CHUNK = 4096


def _available(stream: object) -> int:
    """Return readable pipe bytes without starting a reader thread or blocking."""
    fd = stream.fileno()  # type: ignore[attr-defined]
    if sys.platform != "win32":
        return _READ_CHUNK if select.select([fd], [], [], 0)[0] else 0
    import ctypes
    import msvcrt
    from ctypes import wintypes

    available = wintypes.DWORD()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.PeekNamedPipe.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.PeekNamedPipe.restype = wintypes.BOOL
    if not kernel32.PeekNamedPipe(
        msvcrt.get_osfhandle(fd), None, 0, None, ctypes.byref(available), None
    ):
        return 0  # closed pipe; no read may block
    return available.value


def _drain(stream: object, output: bytearray) -> bool:
    """Drain only bytes already in the pipe; keep at most cap+1 bytes."""
    while (available := _available(stream)) > 0:
        size = min(available, _READ_CHUNK, _MAX_OUTPUT_BYTES + 1 - len(output))
        if size <= 0:
            return True
        chunk = os.read(stream.fileno(), size)  # type: ignore[attr-defined]
        if not chunk:
            break
        output.extend(chunk)
        if len(output) > _MAX_OUTPUT_BYTES:
            return True
    return False


def capture_probe(
    argv: list[str], timeout_seconds: float, *, env: dict[str, str] | None = None
) -> tuple[bytes, bytes]:
    deadline = time.monotonic() + timeout_seconds
    work_deadline = deadline - min(0.5, timeout_seconds / 5)
    process, containment = process_containment.spawn_contained(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    overflow = False
    cleanup_errors: list[str] = []
    try:
        while True:
            overflow = _drain(process.stdout, stdout) or _drain(process.stderr, stderr)
            if overflow or process.poll() is not None or time.monotonic() >= work_deadline:
                break
            time.sleep(min(0.01, max(work_deadline - time.monotonic(), 0)))
    finally:
        cleanup_errors.extend(containment.kill())
        try:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=max(deadline - time.monotonic(), 0))
            except subprocess.TimeoutExpired:
                cleanup_errors.append("freshness child did not exit within its cleanup slice")
            cleanup_errors.extend(containment.survivors(deadline))
            if not overflow:
                overflow = _drain(process.stdout, stdout) or _drain(process.stderr, stderr)
        finally:
            containment.release()
            process.stdout.close()
            process.stderr.close()
    if cleanup_errors:
        _LOGGER.warning("doctor freshness process cleanup failed: %s", cleanup_errors)
    if overflow:
        _LOGGER.warning("doctor freshness child exceeded its output limit")
        return b"", b""
    return bytes(stdout), bytes(stderr)
