"""H.5 fork safety of the OS advisory sidecar lock (council wave-2b r12-r16).

A POSIX fork child inherits the open-file description of every held sidecar lock. If the holder
then dies WITHOUT releasing, the flock stays held for as long as the child lives; a child that
unwinds an inherited ``index_lock`` context must also release nothing. The scenarios run in
subprocesses (real ``os.fork``), every wait is bounded, and every RED arm is a monkeypatched
mutation inside the subprocess, never a production switch.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from tensor_grep.cli import _index_lock as il

_HAS_FORK_HOOKS = hasattr(os, "register_at_fork")
posix_only = pytest.mark.skipif(not _HAS_FORK_HOOKS, reason="POSIX fork semantics")
_SRC = str(Path(il.__file__).resolve().parents[2])


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (_SRC, env.get("PYTHONPATH", "")) if p)
    return env


def _spawn(script: str, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(script), *args],
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for(path: Path, proc: subprocess.Popen[str], timeout_s: float = 30.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return path.read_text()
        if proc.poll() is not None and not path.exists():
            raise AssertionError(f"child exited early rc={proc.returncode}: {proc.stdout.read()}")
        time.sleep(0.05)
    raise AssertionError(f"{path.name} never appeared")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_import_survives_a_platform_without_register_at_fork() -> None:
    # (c) Windows import safety: run on every platform, in a SUBPROCESS so pytest's own module
    # state is never reloaded.
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; os.__dict__.pop('register_at_fork', None); "
            "import tensor_grep.cli._index_lock",
        ],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


_HOLDER = """
    import os, sys, time
    from pathlib import Path
    from tensor_grep.cli import _index_lock as il

    sidecar, ready, stop, mode = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
    fd = il.try_os_file_lock(sidecar)
    assert fd is not None
    if mode == "clear":  # RED arm: the child has nothing to close in after_in_child
        il._HELD_LOCK_FDS.clear()
    pid = os.fork()
    if pid == 0:
        deadline = time.monotonic() + 30
        while not os.path.exists(stop) and time.monotonic() < deadline:
            time.sleep(0.05)
        os._exit(0)
    ready.write_text(str(pid))
    time.sleep(60)
"""


def _death_without_release(tmp_path: Path, mode: str) -> tuple[bool, bool]:
    """Returns (grandchild_alive_when_probed, lock_acquirable_within_2s)."""
    sidecar = il._os_lock_path_for(tmp_path / "index.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    ready, stop = tmp_path / "ready.txt", tmp_path / "stop.txt"
    holder = _spawn(_HOLDER, str(sidecar), str(ready), str(stop), mode)
    grandchild = 0
    try:
        grandchild = int(_wait_for(ready, holder))
        holder.kill()  # SIGKILL: no release runs
        holder.wait(timeout=10)
        alive = _alive(grandchild)
        fd = None
        deadline = time.monotonic() + 2.0
        while fd is None and time.monotonic() < deadline:
            fd = il.try_os_file_lock(sidecar)
            if fd is None:
                time.sleep(0.05)
        if fd is not None:
            il.release_os_file_lock(fd)
        return alive, fd is not None
    finally:
        stop.write_text("stop")
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
        if grandchild:
            try:
                os.kill(grandchild, signal.SIGKILL)
            except OSError:
                pass


@posix_only
def test_lock_is_released_when_the_holder_dies_without_release_despite_a_live_fork_child(
    tmp_path: Path,
) -> None:
    alive, acquired = _death_without_release(tmp_path, "normal")
    assert alive, "premise: the fork child must still be running when the lock is probed"
    assert acquired, "a live fork child kept the dead holder's sidecar lock held"


@posix_only
def test_death_without_release_red_arm_the_lock_stays_held_without_the_child_close(
    tmp_path: Path,
) -> None:
    # RED arm (no production switch): the holder clears the registry right before forking, so
    # after_in_child has nothing to close and the grandchild keeps the description alive.
    alive, acquired = _death_without_release(tmp_path, "clear")
    assert alive, "premise: the fork child must still be running when the lock is probed"
    assert not acquired, "the RED arm must keep the lock held; the mutation had no effect"


@posix_only
def test_no_fork_can_interleave_between_open_and_register(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar = il._os_lock_path_for(tmp_path / "index.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    entered, release, fork_returned = threading.Event(), threading.Event(), threading.Event()
    real_register = il._register_held_fd
    results: dict[str, int | None] = {}
    errors: list[BaseException] = []

    def _blocking_register(fd: int) -> None:
        entered.set()  # called while _REGISTRY_LOCK is held, after os.open + lock
        assert release.wait(10)
        real_register(fd)

    monkeypatch.setattr(il, "_register_held_fd", _blocking_register)

    def _acquire() -> None:
        try:
            results["fd"] = il.try_os_file_lock(sidecar)
        except BaseException as exc:
            errors.append(exc)

    def _fork() -> None:
        try:
            pid = os.fork()
            if pid == 0:
                os._exit(0)
            results["child"] = pid
            fork_returned.set()
        except BaseException as exc:
            errors.append(exc)
            fork_returned.set()

    t_acquire = threading.Thread(target=_acquire)
    t_fork = threading.Thread(target=_fork)
    t_acquire.start()
    try:
        assert entered.wait(10), "premise: the acquirer reached the register point"
        t_fork.start()
        assert not fork_returned.wait(0.5), "fork returned while an fd was open but unregistered"
    finally:
        release.set()
        t_acquire.join(10)
        if t_fork.ident is not None:
            t_fork.join(10)
    assert fork_returned.is_set() and errors == []
    child = results.get("child")
    if child:
        os.waitpid(child, 0)
    fd = results.get("fd")
    assert fd is not None
    il.release_os_file_lock(fd)


_INHERITED_CONTEXT = """
    import os, sys, time
    from pathlib import Path
    from tensor_grep.cli import _index_lock as il

    a, b = Path(sys.argv[1]), Path(sys.argv[2])
    parent_ready, child_ready, stop = Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5])
    mode = sys.argv[6]
    if mode == "no-outer":  # mutation: ONLY the outer index_lock pid guard removed
        il._outer_release_allowed = lambda owner_pid: True
    if mode == "no-legacy":  # mutation: ONLY the legacy-release pid guard removed
        il._legacy_release_allowed = lambda owner_pid: True

    cm = il.index_lock(a, timeout_s=10)
    cm.__enter__()
    held = sorted(il._HELD_LOCK_FDS)[0]  # the inherited sidecar fd number N
    pid = os.fork()
    if pid == 0:
        # after_in_child already closed N. Acquire B and make its descriptor number EXACTLY N
        # (asserted premise: the inherited context would release "its" fd N, which is now B's).
        fdb = il.try_os_file_lock(il._os_lock_path_for(b))
        assert fdb is not None
        if fdb != held:
            os.dup2(fdb, held)
            os.close(fdb)
            il._HELD_LOCK_FDS.discard(fdb)
            il._HELD_LOCK_FDS.add(held)
            fdb = held
        assert fdb == held, (fdb, held)
        cm.__exit__(None, None, None)  # unwind the INHERITED context in the child
        child_ready.write_text("child")
        deadline = time.monotonic() + 30
        while not os.path.exists(stop) and time.monotonic() < deadline:
            time.sleep(0.05)
        os._exit(0)
    parent_ready.write_text("parent")
    deadline = time.monotonic() + 30
    while not os.path.exists(stop) and time.monotonic() < deadline:
        time.sleep(0.05)
    cm.__exit__(None, None, None)
    os.waitpid(pid, 0)
"""


def _inherited_context_scenario(tmp_path: Path, mode: str) -> dict[str, bool]:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for index in (a, b):
        il._os_lock_path_for(index).parent.mkdir(parents=True, exist_ok=True)
    parent_ready, child_ready = tmp_path / "parent_ready", tmp_path / "child_ready"
    stop = tmp_path / "stop"
    proc = _spawn(
        _INHERITED_CONTEXT,
        str(a),
        str(b),
        str(parent_ready),
        str(child_ready),
        str(stop),
        mode,
    )
    try:
        _wait_for(parent_ready, proc)
        _wait_for(child_ready, proc)
        legacy_a = il._lock_path_for(a)
        token = il._token_for_lock(legacy_a)
        outcome: dict[str, bool] = {"legacy_a_intact": token is not None}
        fd_a = il.try_os_file_lock(il._os_lock_path_for(a))
        outcome["a_acquirable"] = fd_a is not None
        if fd_a is not None:
            il.release_os_file_lock(fd_a)
        fd_b = il.try_os_file_lock(il._os_lock_path_for(b))
        outcome["b_acquirable"] = fd_b is not None
        if fd_b is not None:
            il.release_os_file_lock(fd_b)
        return outcome
    finally:
        stop.write_text("stop")
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@posix_only
def test_a_child_unwinding_an_inherited_index_lock_context_releases_nothing(
    tmp_path: Path,
) -> None:
    outcome = _inherited_context_scenario(tmp_path, "green")
    assert outcome == {"legacy_a_intact": True, "a_acquirable": False, "b_acquirable": False}


@posix_only
def test_mutation_without_the_outer_pid_guard_frees_the_childs_reused_fd(tmp_path: Path) -> None:
    outcome = _inherited_context_scenario(tmp_path, "no-outer")
    assert outcome["b_acquirable"] is True, "the outer guard is what protects the reused fd"


@posix_only
def test_mutation_without_the_legacy_pid_guard_deletes_the_parents_lock_file(
    tmp_path: Path,
) -> None:
    outcome = _inherited_context_scenario(tmp_path, "no-legacy")
    assert outcome["legacy_a_intact"] is False, "the legacy guard is what protects the token file"
