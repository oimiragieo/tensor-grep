"""Regression tests for C3 and H3 launcher bugs.

C3: After a child process exits abnormally (non-zero, signal-killed), the launcher
    must NOT re-spawn — it must propagate the exit code and stop.  Also verifies that
    an atexit handler is registered that will terminate any still-running child if the
    parent Python process exits unexpectedly.

H3: On Windows, if the OS still holds the binary file-handle from a previous launch
    (sharing-violation / PermissionError), _popen_child must retry with back-off and
    succeed rather than returning a silent exit-1 / 127.

These tests import only the light CLI bootstrap module and do NOT require the compiled
Rust extension, so they run in a plain Python environment.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.cli import bootstrap
from tensor_grep.cli.bootstrap import (
    _ORIG_RUN_SUBPROCESS,
    _terminate_child,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_popen(returncode: int, *, pid: int = 12345) -> MagicMock:
    """Return a mock subprocess.Popen whose wait() immediately returns returncode."""
    proc = MagicMock(spec=subprocess.Popen)
    proc.pid = pid
    proc.returncode = returncode
    proc.poll.return_value = returncode  # already finished
    proc.wait.return_value = returncode
    proc.terminate.return_value = None
    proc.kill.return_value = None
    return proc


# ---------------------------------------------------------------------------
# C3: re-exec guard breaks the native<->python mutual delegation fork-bomb
# ---------------------------------------------------------------------------


def test_reexec_guard_prevents_native_search_delegation(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the native front door spawned us (TG_REEXEC_GUARD=1), the launcher must NOT
    delegate search back to the native binary. That mutual native<->python delegation
    fork-bombs on `tg --json --debug` / `--stats` (passthrough flags the render-flag guard
    does not cover)."""
    native_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: Path("fake-native-tg.exe"))
    monkeypatch.setattr(bootstrap, "resolve_ripgrep_binary", lambda: Path("fake-rg.exe"))
    monkeypatch.setattr(
        bootstrap, "_run_native_tg_search", lambda *a, **k: native_calls.append(a) or 0
    )
    monkeypatch.setattr(bootstrap, "_run_rg_passthrough", lambda *a, **k: 0)
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: None)
    monkeypatch.setattr(sys, "argv", ["tg", "search", "alpha", "f.txt", "--json", "--debug"])

    monkeypatch.setenv("TG_REEXEC_GUARD", "1")
    try:
        bootstrap.main_entry()
    except SystemExit:
        pass
    assert native_calls == [], "must not delegate search to native when spawned by native"


def test_native_search_delegation_happens_without_reexec_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the guard, `--json --debug` DOES reach native delegation (the loop path the
    guard intercepts) — confirms the guard is what breaks the cycle."""
    native_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: Path("fake-native-tg.exe"))
    monkeypatch.setattr(
        bootstrap, "_run_native_tg_search", lambda *a, **k: native_calls.append(a) or 0
    )
    monkeypatch.setattr(sys, "argv", ["tg", "search", "alpha", "f.txt", "--json", "--debug"])
    monkeypatch.delenv("TG_REEXEC_GUARD", raising=False)
    try:
        bootstrap.main_entry()
    except SystemExit:
        pass
    assert native_calls, "without the guard, --json --debug delegates to native (the loop)"


# ---------------------------------------------------------------------------
# C3: No re-spawn on abnormal exit
# ---------------------------------------------------------------------------


def test_streaming_passthrough_does_not_retry_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """C3: A child that exits non-zero must propagate the code and NOT re-spawn."""
    call_count = {"n": 0}

    def _one_shot_popen(argv: list[str]) -> MagicMock:
        call_count["n"] += 1
        if call_count["n"] > 1:
            pytest.fail("_popen_child called more than once — unexpected re-spawn")
        return _make_fake_popen(returncode=2)

    monkeypatch.setattr(bootstrap, "_popen_child", _one_shot_popen)
    monkeypatch.setattr(bootstrap, "run_subprocess", _ORIG_RUN_SUBPROCESS)

    rc = bootstrap._streaming_passthrough_returncode(["fake-binary", "--search", "foo"])
    assert rc == 2
    assert call_count["n"] == 1, "must have been called exactly once"


def test_streaming_passthrough_does_not_retry_signal_killed_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C3: A child whose wait() returns a negative exit code (Unix signal kill) must
    propagate the code and NOT re-spawn.  On Windows this is a large positive integer
    representing the NTSTATUS, but the key is: still called exactly once."""
    call_count = {"n": 0}
    # Simulate a signal kill: returncode -15 on Unix, or e.g. 0xC000013A on Windows.
    killed_rc = -15 if not sys.platform.startswith("win") else 0xC000013A

    def _one_shot_popen(argv: list[str]) -> MagicMock:
        call_count["n"] += 1
        return _make_fake_popen(returncode=killed_rc)

    monkeypatch.setattr(bootstrap, "_popen_child", _one_shot_popen)
    monkeypatch.setattr(bootstrap, "run_subprocess", _ORIG_RUN_SUBPROCESS)

    rc = bootstrap._streaming_passthrough_returncode(["fake-binary"])
    assert rc == killed_rc
    assert call_count["n"] == 1, "must not re-spawn after signal-kill"


def test_streaming_passthrough_atexit_handler_registered_and_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C3: An atexit handler that kills the child must be registered during the wait
    and unregistered once the child exits normally."""
    import atexit

    proc_mock = _make_fake_popen(returncode=0)
    registered: list[Any] = []
    unregistered: list[Any] = []

    original_register = atexit.register
    original_unregister = atexit.unregister

    def _spy_register(fn: Any, *args: Any, **kwargs: Any) -> Any:
        registered.append(fn)
        return original_register(fn, *args, **kwargs)

    def _spy_unregister(fn: Any) -> None:
        unregistered.append(fn)
        original_unregister(fn)

    monkeypatch.setattr(bootstrap, "_popen_child", lambda _argv: proc_mock)
    monkeypatch.setattr(bootstrap, "run_subprocess", _ORIG_RUN_SUBPROCESS)
    monkeypatch.setattr("atexit.register", _spy_register)
    monkeypatch.setattr("atexit.unregister", _spy_unregister)

    rc = bootstrap._streaming_passthrough_returncode(["fake-binary"])
    assert rc == 0

    # At least one atexit function was registered and then unregistered.
    assert len(registered) >= 1
    assert len(unregistered) >= 1


def test_streaming_passthrough_atexit_handler_kills_running_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C3: If the atexit handler fires while the child is still running, it must
    terminate the child (not leave an orphan)."""
    terminate_called = {"n": 0}

    proc_mock = _make_fake_popen(returncode=0)
    proc_mock.poll.return_value = None  # still running when atexit fires

    def _fake_terminate_child(proc: subprocess.Popen[bytes]) -> None:
        terminate_called["n"] += 1

    monkeypatch.setattr(bootstrap, "_popen_child", lambda _argv: proc_mock)
    monkeypatch.setattr(bootstrap, "run_subprocess", _ORIG_RUN_SUBPROCESS)
    monkeypatch.setattr(bootstrap, "_terminate_child", _fake_terminate_child)

    # Make wait() raise KeyboardInterrupt to simulate parent being interrupted before
    # child completes — the atexit handler is what would fire in a SIGKILL scenario.
    proc_mock.wait.side_effect = KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        bootstrap._streaming_passthrough_returncode(["fake-binary"])

    # _terminate_child must have been called on KeyboardInterrupt path.
    assert terminate_called["n"] >= 1, "_terminate_child must be called on KeyboardInterrupt"


def test_keyboard_interrupt_forwards_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    """C3: KeyboardInterrupt must be forwarded to the child (via _terminate_child) and
    re-raised so the caller also exits — NOT silently swallowed."""
    proc_mock = _make_fake_popen(returncode=0)
    proc_mock.wait.side_effect = KeyboardInterrupt
    term_calls: list[Any] = []

    monkeypatch.setattr(bootstrap, "_popen_child", lambda _argv: proc_mock)
    monkeypatch.setattr(bootstrap, "run_subprocess", _ORIG_RUN_SUBPROCESS)
    monkeypatch.setattr(bootstrap, "_terminate_child", lambda p: term_calls.append(p))

    with pytest.raises(KeyboardInterrupt):
        bootstrap._streaming_passthrough_returncode(["fake-binary"])

    assert len(term_calls) == 1, "_terminate_child must be called exactly once"
    assert term_calls[0] is proc_mock


# ---------------------------------------------------------------------------
# H3: Retry on Windows sharing-violation
# ---------------------------------------------------------------------------


def test_popen_child_retries_on_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """H3: If subprocess.Popen raises PermissionError on the first attempt(s), _popen_child
    must retry and succeed on a later attempt."""
    attempt_counter = {"n": 0}
    expected_proc = _make_fake_popen(returncode=0)

    def _flaky_popen(argv: list[str], *args: Any, **kwargs: Any) -> MagicMock:
        attempt_counter["n"] += 1
        if attempt_counter["n"] < 2:
            raise PermissionError("binary handle still held by OS")
        return expected_proc

    # Patch the sleep so the test doesn't actually wait.
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _d: None)

    with patch("subprocess.Popen", side_effect=_flaky_popen):
        proc = bootstrap._popen_child(["some-binary", "--arg"])

    assert proc is expected_proc
    assert attempt_counter["n"] == 2, "must have tried exactly twice"


def test_popen_child_retries_on_windows_sharing_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    """H3: OSError with winerror=32 (ERROR_SHARING_VIOLATION) triggers retry with back-off."""
    if not sys.platform.startswith("win"):
        pytest.skip("Windows-specific sharing violation (winerror 32)")

    attempt_counter = {"n": 0}
    expected_proc = _make_fake_popen(returncode=0)

    def _flaky_popen(argv: list[str], *args: Any, **kwargs: Any) -> MagicMock:
        attempt_counter["n"] += 1
        if attempt_counter["n"] < 2:
            err = OSError("sharing violation")
            err.winerror = 32  # type: ignore[attr-defined]
            raise err
        return expected_proc

    monkeypatch.setattr(bootstrap.time, "sleep", lambda _d: None)

    with patch("subprocess.Popen", side_effect=_flaky_popen):
        proc = bootstrap._popen_child(["some-binary"])

    assert proc is expected_proc
    assert attempt_counter["n"] == 2


def test_popen_child_raises_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """H3: If every attempt raises PermissionError, _popen_child must re-raise after the
    maximum number of retries rather than silently returning an error code."""
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _d: None)

    with patch("subprocess.Popen", side_effect=PermissionError("handle locked")):
        with pytest.raises(PermissionError):
            bootstrap._popen_child(["some-binary"])


def test_popen_child_does_not_retry_unrelated_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    """H3: An OSError that is NOT a sharing-violation must be raised immediately, without
    sleeping or retrying."""
    sleep_called = {"n": 0}
    monkeypatch.setattr(
        bootstrap.time, "sleep", lambda _d: sleep_called.__setitem__("n", sleep_called["n"] + 1)
    )

    unrelated_error = OSError("no such file or directory")
    # winerror 2 == ERROR_FILE_NOT_FOUND, not a sharing violation
    if sys.platform.startswith("win"):
        unrelated_error.winerror = 2  # type: ignore[attr-defined]

    with patch("subprocess.Popen", side_effect=unrelated_error):
        with pytest.raises(OSError):
            bootstrap._popen_child(["missing-binary"])

    assert sleep_called["n"] == 0, "must not sleep/retry on non-sharing OSError"


# ---------------------------------------------------------------------------
# C3: Terminate-child helper
# ---------------------------------------------------------------------------


def test_terminate_child_calls_terminate_then_wait() -> None:
    """_terminate_child must first terminate(), then wait(), and kill() if wait times out."""
    proc_mock = MagicMock(spec=subprocess.Popen)
    proc_mock.terminate.return_value = None
    proc_mock.wait.return_value = 0

    _terminate_child(proc_mock)

    proc_mock.terminate.assert_called_once()
    proc_mock.wait.assert_called_once()


def test_terminate_child_kills_if_wait_times_out() -> None:
    """If the child is still running after terminate(), _terminate_child must call kill()."""
    proc_mock = MagicMock(spec=subprocess.Popen)
    proc_mock.terminate.return_value = None
    proc_mock.wait.side_effect = subprocess.TimeoutExpired(cmd=["x"], timeout=5)
    proc_mock.kill.return_value = None

    _terminate_child(proc_mock)

    proc_mock.kill.assert_called_once()


def test_terminate_child_swallows_oserror_on_terminate() -> None:
    """_terminate_child must not raise even if terminate() or kill() raises OSError."""
    proc_mock = MagicMock(spec=subprocess.Popen)
    proc_mock.terminate.side_effect = OSError("process already gone")
    proc_mock.wait.side_effect = OSError("wait failed")
    proc_mock.kill.side_effect = OSError("kill failed")

    # Must not raise.
    _terminate_child(proc_mock)


# ---------------------------------------------------------------------------
# Rapid-loop smoke test (H3 integration — real process, no mocking)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle-release race is Windows-only")
def test_rapid_version_invocations_never_produce_empty_output() -> None:
    """H3 smoke: 30 back-to-back --version calls must all succeed (exit 0, non-empty
    stdout).  This exercises the real retry-with-backoff path for Windows handle races.
    """
    tg_exe = Path(__file__).resolve().parents[2] / ".venv" / "Scripts" / "tg.exe"
    if not tg_exe.is_file():
        pytest.skip(f"tg.exe not found at {tg_exe}")

    failures = []
    for i in range(30):
        result = subprocess.run(
            [str(tg_exe), "--version"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            failures.append((i, result.returncode, repr(result.stdout), repr(result.stderr)))

    assert not failures, f"Some rapid invocations failed: {failures}"
