from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tensor_grep.cli import _index_lock as il


def test_second_holder_times_out_while_first_holds(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    entered, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []

    def holder() -> None:
        try:
            with il.index_lock(index, timeout_s=5):
                entered.set()
                assert release.wait(5)
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert entered.wait(5)
        with pytest.raises(il.IndexLockTimeoutError):
            with il.index_lock(index, timeout_s=0.3, poll_interval_s=0.05):
                pytest.fail("second holder entered while first held")
    finally:
        release.set()
        t.join(10)
    assert not t.is_alive() and errors == []


def test_many_threads_never_overlap(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    state = {"n": 0, "max": 0, "done": 0}
    guard = threading.Lock()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(20):
                with il.index_lock(index, timeout_s=20, poll_interval_s=0.001):
                    with guard:
                        state["n"] += 1
                        state["max"] = max(state["max"], state["n"])
                    time.sleep(0.0005)
                    with guard:
                        state["n"] -= 1
                        state["done"] += 1
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads) and errors == []
    assert state["done"] == 120 and state["max"] == 1


def test_overlap_detector_positive_control() -> None:
    state = {"n": 0, "max": 0}
    for _ in range(2):  # two unlocked "holders"
        state["n"] += 1
        state["max"] = max(state["max"], state["n"])
    assert state["max"] == 2


def test_sidecar_lock_is_released_when_the_holder_process_dies(tmp_path: Path) -> None:
    sidecar = il._os_lock_path_for(tmp_path / "index.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    ready = tmp_path / "ready.txt"
    code = (
        "import time; from pathlib import Path; from tensor_grep.cli import _index_lock as il; "
        f"fd = il.try_os_file_lock(Path({str(sidecar)!r})); "
        f"Path({str(ready)!r}).write_text('held' if fd is not None else 'no'); time.sleep(60)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists() and ready.read_text() == "held", f"child not ready (rc={proc.poll()})"
        assert il.try_os_file_lock(sidecar) is None  # held by the child
        proc.kill()
        proc.wait(timeout=10)
        # Windows releases a dead process's byte-range lock a few ms AFTER wait() returns
        # (measured: first attempt None, second succeeds ~50 ms later); POSIX is immediate.
        # Bounded poll; the assertion (the OS frees the lock on death) is unchanged.
        fd = il.try_os_file_lock(sidecar)
        poll_deadline = time.monotonic() + 5.0
        while fd is None and time.monotonic() < poll_deadline:
            time.sleep(0.05)
            fd = il.try_os_file_lock(sidecar)
        assert fd is not None, "OS lock not released after the holder process died"
        il.release_os_file_lock(fd)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_identity_mismatch_rejects_and_closes_the_fd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = il._os_lock_path_for(tmp_path / "index.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    closed: list[int] = []
    real_close, real_stat = os.close, os.stat
    monkeypatch.setattr(il.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])

    def other_inode(path, *a, **k):
        st = real_stat(path, *a, **k)
        if str(path) == str(sidecar):
            return os.stat_result((st.st_mode, st.st_ino + 1, *tuple(st)[2:]))
        return st

    monkeypatch.setattr(il.os, "stat", other_inode)
    assert il.try_os_file_lock(sidecar) is None
    assert closed, "the opened descriptor was not closed"
    monkeypatch.setattr(il.os, "stat", real_stat)
    fd = il.try_os_file_lock(sidecar)  # unpatched control acquires
    assert fd is not None
    il.release_os_file_lock(fd)


def test_legacy_protocol_still_interoperates_with_an_old_version_holder(tmp_path: Path) -> None:
    # A live OLD-version holder = the legacy lock file with fresh content/mtime and no sidecar
    # lock. The new index_lock must wait on it exactly as main does (no regression).
    index = tmp_path / "index.json"
    legacy = il._lock_path_for(index)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(f"{os.getpid()}\nold-version-token\n", encoding="utf-8")
    with pytest.raises(il.IndexLockTimeoutError):
        with il.index_lock(index, timeout_s=0.3, poll_interval_s=0.05, stale_after_s=60):
            pytest.fail("entered while an old-version holder's fresh lock exists")
    assert legacy.read_text(encoding="utf-8").endswith("old-version-token\n")  # untouched


def test_daemon_start_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    from tensor_grep.cli import session_daemon

    root = tmp_path.resolve()
    results: list[bool] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def _try() -> None:
        try:
            barrier.wait(5)
            results.append(session_daemon._try_acquire_daemon_start_lock(root))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_try) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not any(t.is_alive() for t in threads) and errors == []
    assert sorted(results) == [False, False, False, True], results
    session_daemon._release_daemon_start_lock(root)
    assert session_daemon._try_acquire_daemon_start_lock(root) is True  # released -> reacquirable
    session_daemon._release_daemon_start_lock(root)
