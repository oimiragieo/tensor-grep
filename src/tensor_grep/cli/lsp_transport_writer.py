"""Deadline-bounded LSP transport writes, independent of process death.

A blocking ``stdin.write``/``flush`` cannot be interrupted from another thread, and killing the
provider only unblocks it if EVERY reader of the pipe dies with it. A descendant that escaped
the provider's process group (``setsid``) keeps the read end open, so the write would never get
EPIPE. Every frame therefore goes through ONE dedicated writer thread: the caller hands the
frame off and waits at most its deadline for it to be written.

On timeout the transport is marked dead (later writes fail fast) and the stuck writer is
abandoned. It is a daemon thread; it ends when the pipe's last reader exits, and ``close()``
lets an idle one exit.
"""

from __future__ import annotations

import queue
import threading
from typing import Any

from tensor_grep.cli.lsp_probe_budget import remaining_seconds

_DEADLINE_MESSAGE = "LSP write did not complete before the deadline"


class DeadlineWriter:
    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.dead = False
        self._frames: queue.Queue[Any] = queue.Queue(maxsize=8)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            item = self._frames.get()
            if item is None:
                return
            data, done, failure = item
            try:
                try:
                    self.stream.write(data)
                except TypeError:  # text-mode stream
                    self.stream.write(data.decode("utf-8"))
                self.stream.flush()
            except (OSError, ValueError) as exc:
                failure.append(exc)
            finally:
                done.set()

    def write(self, data: bytes, timeout: float) -> None:
        if self.dead:
            raise TimeoutError("LSP transport is dead: " + _DEADLINE_MESSAGE)
        done = threading.Event()
        failure: list[BaseException] = []
        try:
            self._frames.put((data, done, failure), timeout=timeout)
        except queue.Full:
            self.dead = True
            raise TimeoutError(_DEADLINE_MESSAGE) from None
        if not done.wait(timeout):
            self.dead = True
            raise TimeoutError(_DEADLINE_MESSAGE)
        if failure:
            raise failure[0]

    def close(self) -> None:
        try:
            self._frames.put_nowait(None)
        except queue.Full:
            pass


class DeadlineStream:
    """File-like facade so ``_write_message`` keeps its ``(stream, payload)`` shape."""

    def __init__(self, writer: DeadlineWriter, timeout: float) -> None:
        self._writer = writer
        self._timeout = timeout

    def write(self, data: bytes) -> int:
        self._writer.write(data, self._timeout)
        return len(data)

    def flush(self) -> None:  # the writer thread already flushed
        return None


def bounded_stdin(client: Any) -> DeadlineStream:
    """The client's stdin as a deadline-bounded stream (writer rebuilt per spawned process)."""
    stdin = client.process.stdin
    writer = client._writer
    if writer is None or writer.stream is not stdin:
        writer = client._writer = DeadlineWriter(stdin)
    timeout = remaining_seconds(client, max(float(client.request_timeout_seconds), 0.05))
    return DeadlineStream(writer, timeout)
