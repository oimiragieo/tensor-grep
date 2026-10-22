"""Bounded daemon-probe trace primitives used by tests only."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable, Mapping
from types import CodeType
from typing import Any


class _ProbeServerTrace:
    """Record allowlisted scalars and live lines without retaining request data."""

    _STAGES = frozenset({
        "accept",
        "setup",
        "handle",
        "read",
        "authorization",
        "proof",
        "secret",
        "version",
        "write",
        "flush",
        "connect",
        "send",
        "client_read",
    })
    _ROLES = frozenset({"listener", "handler", "client"})
    _SAMPLE_ROLES = frozenset({"listener", "handler"})
    _ALIASES = frozenset({
        ("session_daemon.py", "_probe_daemon"),
        ("session_daemon.py", "_read_bounded_request_line"),
        ("session_daemon.py", "_serve_daemon_response_with_cache"),
        ("session_daemon.py", "handle"),
        ("session_daemon_trust.py", "_load_or_create_user_secret"),
        ("session_daemon_trust.py", "_daemon_running_version"),
        ("session_daemon_trust.py", "_ping_proof_fields"),
        ("socketserver.py", "serve_forever"),
        ("socketserver.py", "process_request"),
        ("socketserver.py", "process_request_thread"),
        ("socketserver.py", "finish_request"),
        ("test_daemon_probe_trace_helpers.py", "_paused_worker"),
        ("test_daemon_probe_trace_helpers.py", "_recursive_worker"),
    })
    _EVENT_LIMIT = 64
    _FRAME_LIMIT = 12
    _WALK_LIMIT = 256
    _BYTE_LIMIT = 1_000_000

    def __init__(self, frame_aliases: Mapping[CodeType, tuple[str, str]]) -> None:
        if any(
            type(code) is not CodeType
            or alias not in self._ALIASES
            or (code.co_name, code.co_filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
            != (alias[1], alias[0])
            for code, alias in frame_aliases.items()
        ):
            raise ValueError("trace aliases must use known files and functions")
        self._aliases = dict(frame_aliases)
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._events: list[list[Any]] = []
        self._threads: dict[str, threading.Thread] = {}
        self._frozen: tuple[object, ...] | None = None
        self._dropped = 0

    def observe(self, stage: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if stage not in self._STAGES:
            raise ValueError("unregistered trace stage")
        inputs = (*args, *kwargs.values())
        byte_count = min(
            sum(len(value) for value in inputs if type(value) is bytes), self._BYTE_LIMIT
        )
        input_newline = any(type(value) is bytes and value.endswith(b"\n") for value in inputs)
        entry: list[Any] | None = None
        with self._lock:
            if self._frozen is None:
                if len(self._events) < self._EVENT_LIMIT:
                    entry = [stage, byte_count, 0, False, input_newline, False]
                    self._events.append(entry)
                else:
                    self._dropped = min(self._dropped + 1, self._EVENT_LIMIT + 1)
                self._changed.notify_all()
        started = time.perf_counter_ns()
        try:
            result = callback(*args, **kwargs)
        except BaseException:
            self._finish(entry, started, failed=True, newline=False)
            raise
        newline = (type(result) is str and result.endswith("\n")) or (
            type(result) is bytes and result.endswith(b"\n")
        )
        self._finish(entry, started, failed=False, newline=newline)
        return result

    def _finish(
        self, entry: list[Any] | None, started: int, *, failed: bool, newline: bool
    ) -> None:
        if entry is None:
            return
        duration = max(1, min((time.perf_counter_ns() - started) // 1000, 2_000_000))
        with self._lock:
            if any(candidate is entry for candidate in self._events) and entry[2] == 0:
                entry[2:] = [duration, failed, entry[4] or newline, True]
                self._changed.notify_all()

    def register_thread(self, role: str, thread: threading.Thread) -> None:
        if role not in self._ROLES:
            raise ValueError("unregistered trace role")
        with self._lock:
            if self._frozen is None:
                self._threads[role] = thread

    def wait_for(self, stage: str, timeout: float) -> bool:
        if stage not in self._STAGES:
            raise ValueError("unregistered trace stage")
        with self._changed:
            return self._changed.wait_for(
                lambda: any(event[0] == stage for event in self._events),
                min(timeout, 2.0),
            )

    def join_registered(self, role: str, timeout: float) -> bool:
        if role not in self._ROLES:
            raise ValueError("unregistered trace role")
        with self._lock:
            thread = self._threads.get(role)
        if thread is None:
            return False
        thread.join(max(0.0, min(timeout, 2.0)))
        return not thread.is_alive()

    def freeze(self) -> tuple[object, ...]:
        with self._lock:
            if self._frozen is not None:
                return self._frozen
            try:
                frames = sys._current_frames()
            except (AttributeError, RuntimeError):
                samples: tuple[object, ...] = (("unavailable",),)
            else:
                samples = self._sample_frames(frames)
                frames.clear()
            events = tuple(tuple(event) for event in self._events)
            dropped = ("events_truncated", self._dropped > 0, self._dropped)
            self._frozen = (events, samples, dropped)
            return self._frozen

    def _sample_frames(self, frames: dict[int, Any]) -> tuple[object, ...]:
        samples: list[object] = []
        for role, thread in self._threads.items():
            if role not in self._SAMPLE_ROLES:
                continue
            if not thread.is_alive():
                samples.append((role, "absent", ()))
                continue
            ident = thread.ident
            frame = frames.get(ident) if ident is not None else None
            if frame is None:
                samples.append((role, "absent", ()))
                continue
            found: list[tuple[str, str, int]] = []
            walked = 0
            while (
                frame is not None and walked < self._WALK_LIMIT and len(found) < self._FRAME_LIMIT
            ):
                alias = self._aliases.get(frame.f_code)
                if alias is not None:
                    found.append((*alias, frame.f_lineno))
                frame = frame.f_back
                walked += 1
            state = "captured" if found else "unknown"
            if frame is not None:
                state = "truncated"
            samples.append((role, state, tuple(found)))
        return tuple(samples)
