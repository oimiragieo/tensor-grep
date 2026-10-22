"""Real-thread controls for daemon probe trace primitives."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from tests.unit.daemon_probe_trace_helpers import _ProbeServerTrace


def _recursive_worker(depth: int, entered: threading.Event, release: threading.Event) -> None:
    if depth:
        _recursive_worker(depth - 1, entered, release)
    else:
        entered.set()
        assert release.wait(2), "worker was not released"


def _snapshot(trace: _ProbeServerTrace, worker: Any) -> tuple[object, ...]:
    entered, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    thread = threading.Thread(
        target=_observe, args=(trace, (entered, release), errors, worker, "write"), daemon=True
    )
    trace.register_thread("handler", thread)
    thread.start()
    try:
        assert entered.wait(2), "worker missed its known pause"
        assert trace.wait_for("write", 2)
        saved = trace.freeze()
        assert not trace.join_registered("handler", 0)
    finally:
        release.set()
        thread.join(2)
        assert not thread.is_alive()
        assert trace.join_registered("handler", 2)
    assert not errors
    assert trace.freeze() is saved
    return saved


def _echo(value: Any, expected: Any, *, result: Any) -> Any:
    assert value is expected
    return result


def _raise_same(error: BaseException) -> None:
    raise error


def _pending_cap(trace: _ProbeServerTrace) -> tuple[object, ...]:
    for index in range(63):
        trace.observe("read", _echo, index, index, result=index)
    entered, release, errors = threading.Event(), threading.Event(), []
    thread = threading.Thread(target=_observe, args=(trace, (entered, release, "held"), errors))
    trace.register_thread("handler", thread)
    thread.start()
    try:
        assert entered.wait(2)
        assert trace.wait_for("read", 2)
        for index in range(16):
            assert trace.observe("read", _echo, index, index, result=index) == index
        snapshot = trace.freeze()
        assert snapshot[0][-1][5] is False
    finally:
        release.set()
        thread.join(2)
        assert not thread.is_alive()
        assert trace.join_registered("handler", 2)
    assert not errors
    return snapshot


def _gate(entered: threading.Event, release: threading.Event, value: Any) -> Any:
    entered.set()
    assert release.wait(2), "worker was not released"
    if isinstance(value, BaseException):
        raise value
    return value


def _observe(
    trace: _ProbeServerTrace,
    args: tuple[Any, ...],
    errors: list[Any],
    callback: Any = _gate,
    stage: str = "read",
) -> None:
    try:
        trace.observe(stage, callback, *args)
    except BaseException as caught:
        errors.append(caught)


def _event_outcomes(events: tuple[Any, ...]) -> list[tuple[bool, bool, bool]]:
    return [(event[3], event[4], event[5]) for event in events]


def _overlap(trace: _ProbeServerTrace) -> tuple[object, ...]:
    entered_a, entered_b = threading.Event(), threading.Event()
    release_a, release_b = threading.Event(), threading.Event()
    error, errors = RuntimeError("overlap canary"), []
    first = threading.Thread(target=_observe, args=(trace, (entered_a, release_a, error), errors))
    second = threading.Thread(
        target=_observe, args=(trace, (entered_b, release_b, "line\n"), errors)
    )
    trace.register_thread("handler", first)
    first.start()
    try:
        assert entered_a.wait(2)
        trace.register_thread("client", second)
        second.start()
        assert entered_b.wait(2)
        release_a.set()
        first.join(2)
        assert not first.is_alive()
        assert second.is_alive() and not release_b.is_set()
        release_b.set()
        second.join(2)
        assert not second.is_alive()
        assert errors == [error]
        events = trace.freeze()[0]
        assert _event_outcomes(events) == [
            (True, False, True),
            (False, True, True),
        ]
        return trace.freeze()
    finally:
        release_a.set()
        release_b.set()
        if first.ident is not None:
            first.join(2)
        if second.ident is not None:
            second.join(2)
        assert not first.is_alive() and not second.is_alive()


def _private(snapshot: tuple[object, ...]) -> bool:
    data = repr(snapshot)
    return all(
        value not in data
        for value in (
            "private exception text",
            "RuntimeError",
            "private-canary-alpha",
            "private-canary-beta",
        )
    )


def _frames_valid(snapshot: tuple[object, ...]) -> bool:
    sample = snapshot[1][0]
    frames = sample[2]
    aliases = all(
        (file, fn) == ("test_daemon_probe_trace_helpers.py", "_recursive_worker")
        for file, fn, _ in frames
    )
    known_line = any(line == 18 for _, _, line in frames)
    return sample[1] == "truncated" and len(frames) == 12 and aliases and known_line


def test_Observe_SensitiveCallbacks_PreservesIdentityAndPrivacy() -> None:
    trace = _ProbeServerTrace({})
    canary_a, canary_b = "private-canary-alpha", "private-canary-beta"
    assert trace.observe("read", _echo, canary_a, canary_a, result=canary_b) is canary_b
    error = RuntimeError("private exception text")
    with pytest.raises(RuntimeError) as caught:
        trace.observe("proof", _raise_same, error)
    assert caught.value is error
    assert _private(trace.freeze())
    writer = _ProbeServerTrace({})
    assert writer.observe("write", _echo, b"wire\n", b"wire\n", result=None) is None
    write_event = writer.freeze()[0][0]
    assert (write_event[1], write_event[4], write_event[5]) == (10, True, True)
    trailing = _ProbeServerTrace({})
    assert trailing.observe("read", _echo, b"request", b"request", result="reply\n") == "reply\n"
    assert trailing.freeze()[0][0][4] is True
    partial = _ProbeServerTrace({})
    partial.observe("read", _echo, b"head\npartial", b"head\npartial", result="tail")
    partial_event = partial.freeze()[0][0]
    assert (partial_event[1], partial_event[4]) == (24, False)


def test_Observe_CappedOverlappingCallbacks_PreservesEventOwnershipAndSnapshot() -> None:
    trace = _ProbeServerTrace({})
    frozen = _pending_cap(trace)
    assert len(frozen[0]) == 64
    assert frozen[2] == ("events_truncated", True, 16)
    after_freeze = object()
    assert (
        trace.observe("read", _echo, after_freeze, after_freeze, result=after_freeze)
        is after_freeze
    )
    assert trace.freeze() is frozen
    events = _overlap(_ProbeServerTrace({}))[0]
    assert _event_outcomes(events) == [
        (True, False, True),
        (False, True, True),
    ]


def test_Freeze_RegisteredDeepWorker_CapturesLiveLinesWithinLimit() -> None:
    trace = _ProbeServerTrace({
        _recursive_worker.__code__: ("test_daemon_probe_trace_helpers.py", "_recursive_worker"),
    })
    snapshot = _snapshot(trace, lambda entered, gate: _recursive_worker(20, entered, gate))
    assert _frames_valid(snapshot)


def _paused_worker(entered: threading.Event, release: threading.Event) -> None:
    entered.set()
    assert release.wait(2), "worker was not released"


def test_Freeze_UnknownOrExitedWorkers_ReportsDistinctStates() -> None:
    trace = _ProbeServerTrace({})
    snapshot = _snapshot(trace, _paused_worker)
    assert snapshot[1][0][1:] == ("unknown", ())
    assert trace.join_registered("handler", 2)
    absent = _ProbeServerTrace({})
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join(2)
    assert not dead.is_alive()
    absent.register_thread("handler", dead)
    assert absent.freeze()[1][0][1:] == ("absent", ())
    client = _ProbeServerTrace({})
    client.register_thread("client", dead)
    assert client.join_registered("client", 2)
    assert client.freeze()[1] == ()
    with pytest.raises(ValueError):
        _ProbeServerTrace({_paused_worker.__code__: ("untrusted.py", "_paused_worker")})
    with pytest.raises(ValueError):
        _ProbeServerTrace({_paused_worker.__code__: ("session_daemon.py", "_probe_daemon")})


def _raise_sampling_unavailable() -> None:
    raise RuntimeError("sampling unavailable")


def _snapshot_unavailable(
    monkeypatch: Any, trace: _ProbeServerTrace, worker: Any
) -> tuple[object, ...]:
    import sys

    monkeypatch.setattr(sys, "_current_frames", _raise_sampling_unavailable)
    return _snapshot(trace, worker)


def test_Freeze_UnavailableIntrospection_ReportsUnavailable(monkeypatch: Any) -> None:
    trace = _ProbeServerTrace({
        _paused_worker.__code__: ("test_daemon_probe_trace_helpers.py", "_paused_worker"),
    })
    snapshot = _snapshot_unavailable(monkeypatch, trace, _paused_worker)
    assert trace.join_registered("handler", 2)
    assert snapshot[1] == (("unavailable",),)
