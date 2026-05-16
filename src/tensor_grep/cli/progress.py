from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Literal, TextIO, cast

ProgressMode = Literal["auto", "always", "never"]
PROGRESS_MODES: tuple[ProgressMode, ...] = ("auto", "always", "never")


def normalize_progress_mode(value: str) -> ProgressMode:
    mode = value.strip().lower()
    if mode not in PROGRESS_MODES:
        choices = ", ".join(PROGRESS_MODES)
        raise ValueError(f"invalid progress mode {value!r}; expected one of: {choices}")
    return cast(ProgressMode, mode)


def positive_progress_interval_s(value: str) -> float:
    try:
        interval = float(value)
    except ValueError as exc:
        raise ValueError("progress interval must be a number") from exc
    if interval <= 0:
        raise ValueError("progress interval must be greater than 0")
    return interval


class ProgressReporter:
    def __init__(
        self,
        *,
        mode: str = "auto",
        interval_s: float = 30.0,
        json_output: bool = False,
        stream: TextIO | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.mode = normalize_progress_mode(mode)
        if interval_s <= 0:
            raise ValueError("progress interval must be greater than 0")
        self.interval_s = interval_s
        self.stream = stream if stream is not None else sys.stderr
        self.clock = clock
        self.enabled = self._should_emit(json_output=json_output)

    def _should_emit(self, *, json_output: bool) -> bool:
        if self.mode == "always":
            return True
        if self.mode == "never" or json_output:
            return False
        return bool(os.environ.get("CI")) or self.stream.isatty()

    def _emit(self, message: str) -> None:
        if not self.enabled:
            return
        print(f"[progress] {message}", file=self.stream, flush=True)

    def _elapsed_s(self, started_at: float) -> int:
        return max(0, int(self.clock() - started_at))

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        started_at = self.clock()
        stop_event = threading.Event()
        heartbeat_thread: threading.Thread | None = None
        self._emit(f"{name} start")

        if self.enabled:

            def _heartbeat() -> None:
                while not stop_event.wait(self.interval_s):
                    self._emit(f"{name} running {self._elapsed_s(started_at)}s")

            heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
            heartbeat_thread.start()

        try:
            yield
        except BaseException:
            stop_event.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=0.2)
            self._emit(f"{name} failed {self._elapsed_s(started_at)}s")
            raise
        else:
            stop_event.set()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=0.2)
            self._emit(f"{name} done {self._elapsed_s(started_at)}s")
