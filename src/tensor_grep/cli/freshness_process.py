"""Bounded capture and whole-process-tree cleanup for doctor freshness probes."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from tempfile import TemporaryFile

from tensor_grep.cli import process_containment

_LOGGER = logging.getLogger(__name__)
_MAX_OUTPUT_BYTES = 256 * 1024


def capture_probe(
    argv: list[str], timeout_seconds: float, *, env: dict[str, str] | None = None
) -> tuple[bytes, bytes]:
    deadline = time.monotonic() + timeout_seconds
    work_deadline = deadline - min(0.5, timeout_seconds / 5)
    with TemporaryFile() as stdout, TemporaryFile() as stderr:
        process, containment = process_containment.spawn_contained(
            argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=env
        )
        cleanup_errors = []
        try:
            while process.poll() is None:
                remaining = work_deadline - time.monotonic()
                if remaining <= 0:
                    break
                if any(
                    os.fstat(stream.fileno()).st_size > _MAX_OUTPUT_BYTES
                    for stream in (stdout, stderr)
                ):
                    break
                try:
                    process.wait(timeout=min(remaining, 0.025))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            cleanup_errors.extend(containment.kill())
            try:
                if process.poll() is None:
                    process.kill()
                try:
                    process.wait(timeout=max(deadline - time.monotonic(), 0))
                except subprocess.TimeoutExpired:
                    cleanup_errors.append("freshness child did not exit within its cleanup slice")
                # Query the live job/group before releasing the containment
                # handle. A released Windows handle cannot prove absence.
                cleanup_errors.extend(containment.survivors(deadline))
            finally:
                containment.release()
        if cleanup_errors:
            _LOGGER.warning("doctor freshness process cleanup failed: %s", cleanup_errors)
        output = []
        for stream in (stdout, stderr):
            stream.seek(0)
            data = stream.read(_MAX_OUTPUT_BYTES + 1)
            if len(data) > _MAX_OUTPUT_BYTES:
                _LOGGER.warning("doctor freshness child exceeded its output limit")
                return b"", b""
            output.append(data)
        return output[0], output[1]
