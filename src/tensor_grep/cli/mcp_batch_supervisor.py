"""Run one MCP batch worker inside the platform's process containment."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tensor_grep.cli.mcp_batch_json import JsonByteLimitError, iter_json_bytes
from tensor_grep.cli.mcp_batch_worker import MAX_WORKER_INPUT_BYTES, MAX_WORKER_OUTPUT_BYTES
from tensor_grep.cli.process_containment import close_streams_bounded, spawn_contained

_CLEANUP_GRACE_SECONDS = 2.0
_READ_SIZE = 64 * 1024
_MAX_WORKER_DIAGNOSTIC_BYTES = 64 * 1024
_WORKER_OPERATION_ERRORS = (
    OSError,
    subprocess.SubprocessError,
    RuntimeError,
    TypeError,
    ValueError,
    AssertionError,
    MemoryError,
)
_WORKER_PATH = Path(__file__).resolve().with_name("mcp_batch_worker.py")


@dataclass(frozen=True)
class WorkerRun:
    output: bytes | None
    finished_at: float
    exit_code: int | None
    timed_out: bool = False
    output_overflow: bool = False
    input_too_large: bool = False
    cleanup_failed: bool = False
    tree_reaped: bool = True
    diagnostics: bytes = b""
    diagnostics_truncated: bool = False
    cancelled: bool = False


def run_contained_worker(
    request: dict[str, object],
    deadline_end: float,
    cancel_event: threading.Event | None = None,
) -> WorkerRun:
    """Send one JSON request and supervise its Windows Job or POSIX process group.

    A POSIX ``setsid`` descendant that closes inherited pipes is outside process-group cleanup.
    """
    if cancel_event is not None and cancel_event.is_set():
        return WorkerRun(None, time.monotonic(), None, cancelled=True)
    try:
        worker_request = {
            **request,
            # spawn_contained uses a Windows Job here; POSIX only owns the worker process group.
            "whole_tree_containment": sys.platform == "win32",
        }
        input_bytes = b"".join(
            iter_json_bytes(worker_request, MAX_WORKER_INPUT_BYTES, deadline_end)
        )
    except JsonByteLimitError:
        return WorkerRun(None, time.monotonic(), None, input_too_large=True)
    except TimeoutError:
        return WorkerRun(None, time.monotonic(), None, timed_out=True)
    except _WORKER_OPERATION_ERRORS:
        return WorkerRun(None, time.monotonic(), None)
    if time.monotonic() >= deadline_end:
        return WorkerRun(None, time.monotonic(), None, timed_out=True)
    if cancel_event is not None and cancel_event.is_set():
        return WorkerRun(None, time.monotonic(), None, cancelled=True)

    process: subprocess.Popen[bytes] | None = None
    containment: Any = None
    output: bytearray = bytearray()
    diagnostics: bytearray = bytearray()
    output_overflow = threading.Event()
    diagnostics_overflow = threading.Event()
    reader_errors: list[str] = []
    stderr_errors: list[str] = []
    writer_errors: list[str] = []
    io_lock = threading.Lock()
    reader: threading.Thread | None = None
    stderr_reader: threading.Thread | None = None
    writer: threading.Thread | None = None
    cleanup_errors: list[str] = []
    timed_out = False
    cancelled = False
    exit_code: int | None = None
    finished_at = time.monotonic()

    def drain_stdout(stream: Any) -> None:
        try:
            while chunk := stream.read(_READ_SIZE):
                with io_lock:
                    available = MAX_WORKER_OUTPUT_BYTES - len(output)
                    if len(chunk) > available:
                        output.extend(chunk[: max(available, 0)])
                        output_overflow.set()
                    elif available > 0:
                        output.extend(chunk)
        except OSError as exc:
            reader_errors.append(str(exc))

    def drain_stderr(stream: Any) -> None:
        try:
            while chunk := stream.read(_READ_SIZE):
                with io_lock:
                    available = _MAX_WORKER_DIAGNOSTIC_BYTES - len(diagnostics)
                    if len(chunk) > available:
                        diagnostics.extend(chunk[: max(available, 0)])
                        diagnostics_overflow.set()
                    elif available > 0:
                        diagnostics.extend(chunk)
        except OSError as exc:
            stderr_errors.append(str(exc))

    def send_stdin(stream: Any) -> None:
        try:
            stream.write(input_bytes)
            stream.flush()
        except (BrokenPipeError, OSError) as exc:
            writer_errors.append(str(exc))
        finally:
            try:
                stream.close()
            except OSError as exc:
                writer_errors.append(str(exc))

    try:
        if time.monotonic() >= deadline_end:
            return WorkerRun(None, time.monotonic(), None, timed_out=True)
        process, containment = spawn_contained(
            [sys.executable, "-I", str(_WORKER_PATH)],
            cwd=os.getcwd(),
            env=os.environ.copy(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert (
            process.stdin is not None and process.stdout is not None and process.stderr is not None
        )
        reader = threading.Thread(target=drain_stdout, args=(process.stdout,), daemon=True)
        stderr_reader = threading.Thread(target=drain_stderr, args=(process.stderr,), daemon=True)
        writer = threading.Thread(target=send_stdin, args=(process.stdin,), daemon=True)
        reader.start()
        stderr_reader.start()
        writer.start()

        while process.poll() is None:
            now = time.monotonic()
            if output_overflow.is_set():
                break
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if now >= deadline_end:
                timed_out = True
                break
            try:
                wait_timeout = min(0.05, deadline_end - now)
                if cancel_event is None:
                    process.wait(timeout=wait_timeout)
                else:
                    cancel_event.wait(wait_timeout)
            except subprocess.TimeoutExpired:
                continue
        finished_at = time.monotonic()
        exit_code = process.poll()
        if exit_code is None:
            timed_out = timed_out or time.monotonic() >= deadline_end

        # Reap the tracked group/Job before releasing the global batch slot; POSIX descendants
        # that escape with setsid and close inherited pipes are outside this check.
        tree_errors: list[str] = []
        cleanup_end = time.monotonic() + _CLEANUP_GRACE_SECONDS
        if not cancelled:
            cleanup_end = min(cleanup_end, deadline_end + _CLEANUP_GRACE_SECONDS)
        if timed_out or cancelled or output_overflow.is_set():
            tree_errors.extend(containment.kill())
            if process.poll() is None:
                try:
                    process.kill()
                except OSError as exc:
                    tree_errors.append(f"direct worker kill failed: {exc}")
        else:
            initial_survivors = containment.survivors(time.monotonic())
            if initial_survivors:
                tree_errors.extend(containment.kill())
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError as exc:
                        tree_errors.append(f"direct worker kill failed: {exc}")

        if process.poll() is None:
            try:
                process.wait(timeout=max(min(cleanup_end - time.monotonic(), 0.75), 0.0))
            except subprocess.TimeoutExpired:
                tree_errors.append("direct worker did not exit during cleanup")
            except OSError as exc:
                tree_errors.append(f"direct worker wait failed: {exc}")
        try:
            process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            tree_errors.append("direct worker exit could not be confirmed")
        except OSError as exc:
            tree_errors.append(f"direct worker wait failed: {exc}")

        survivor_errors = containment.survivors(cleanup_end)
        tree_reaped = process.poll() is not None and not survivor_errors
        if not tree_reaped:
            cleanup_errors.extend(tree_errors)
            cleanup_errors.extend(survivor_errors)

        for thread, name in (
            (writer, "worker stdin"),
            (reader, "worker stdout"),
            (stderr_reader, "worker stderr"),
        ):
            if thread is not None:
                thread.join(timeout=max(cleanup_end - time.monotonic(), 0.0))
                if thread.is_alive():
                    cleanup_errors.append(f"{name} did not close during cleanup")
        if reader_errors:
            cleanup_errors.append("worker stdout could not be read")
        if stderr_errors:
            cleanup_errors.append("worker stderr could not be drained")
        if writer_errors and exit_code == 0:
            cleanup_errors.append("worker stdin could not be delivered")

        abandoned, close_failures = close_streams_bounded(
            [process.stdin, process.stdout, process.stderr],
            max(cleanup_end - time.monotonic(), 0.0),
        )
        cleanup_errors.extend(f"worker pipe close abandoned ({name})" for name in abandoned)
        cleanup_errors.extend(close_failures)
        tree_reaped = tree_reaped and not cleanup_errors
        if containment is not None:
            containment.release()
        return WorkerRun(
            bytes(output),
            finished_at,
            exit_code,
            timed_out=timed_out,
            cancelled=cancelled,
            output_overflow=output_overflow.is_set(),
            cleanup_failed=bool(cleanup_errors),
            tree_reaped=tree_reaped,
            diagnostics=bytes(diagnostics),
            diagnostics_truncated=diagnostics_overflow.is_set(),
        )
    except _WORKER_OPERATION_ERRORS:
        if process is None:
            return WorkerRun(None, time.monotonic(), None)
        cleanup_end = time.monotonic() + _CLEANUP_GRACE_SECONDS
        if cancel_event is None or not cancel_event.is_set():
            cleanup_end = min(cleanup_end, deadline_end + _CLEANUP_GRACE_SECONDS)
        if containment is not None:
            containment.kill()
        if process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=max(cleanup_end - time.monotonic(), 0.0))
            except (OSError, subprocess.TimeoutExpired):
                pass
        survivors = containment.survivors(cleanup_end) if containment is not None else []
        for cleanup_thread in (writer, reader, stderr_reader):
            if cleanup_thread is not None:
                cleanup_thread.join(timeout=max(cleanup_end - time.monotonic(), 0.0))
        abandoned, close_failures = close_streams_bounded(
            [process.stdin, process.stdout, process.stderr],
            max(cleanup_end - time.monotonic(), 0.0),
        )
        io_failed = any(
            cleanup_thread is not None and cleanup_thread.is_alive()
            for cleanup_thread in (writer, reader, stderr_reader)
        ) or bool(abandoned or close_failures)
        reaped = process.poll() is not None and not survivors and not io_failed
        if containment is not None:
            containment.release()
        return WorkerRun(
            None,
            time.monotonic(),
            process.poll(),
            cleanup_failed=not reaped,
            tree_reaped=reaped,
            diagnostics=bytes(diagnostics),
            diagnostics_truncated=diagnostics_overflow.is_set(),
        )
