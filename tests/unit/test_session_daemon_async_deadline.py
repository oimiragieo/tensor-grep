"""Wall-clock changes must neither shorten nor extend daemon wait budgets."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_daemon_deadlines as dd


class _Clock:
    ticks = 0
    jump = 0

    def monotonic(self):
        return self.ticks

    def time(self):
        return 1_700_000_000 + self.ticks + (self.jump if self.ticks else 0)

    def sleep(self, seconds):
        assert seconds == 0.05
        self.ticks += 1
        assert self.ticks <= 4, "wall-clock rollback extended the monotonic wait budget"


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    # Replace only this module's reference; pytest and other modules keep their real clocks.
    monkeypatch.setattr(sd, "time", SimpleNamespace(time=clock.time, sleep=clock.sleep))
    monkeypatch.setattr(sd, "monotonic", clock.monotonic)
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 3)
    monkeypatch.setattr(sd, "_DAEMON_SESSION_LOOKUP_RETRY_SECONDS", 3)
    monkeypatch.setattr(sd, "_resolve_root", lambda path: path)
    # The public re-export now lives in a separate helper with an explicit retry budget.
    monkeypatch.setattr(dd, "time", SimpleNamespace(sleep=clock.sleep))
    monkeypatch.setattr(dd, "monotonic", clock.monotonic)
    retry = sd._load_payload_with_status_retry
    monkeypatch.setattr(
        sd,
        "_load_payload_with_status_retry",
        lambda cache, session_id, path: retry(cache, session_id, path, retry_seconds=3),
    )
    return clock


@pytest.fixture
def metadata():
    return {"host": "127.0.0.1", "port": 12345, "pid": 987, "started_at": "1700000000"}


@pytest.mark.parametrize("jump", [-1000, 0, 1000], ids=["rollback", "steady", "forward"])
@pytest.mark.parametrize("acquired", [False, True], ids=["contended", "spawned"])
@pytest.mark.parametrize("ready", [False, True], ids=["timeout", "ready"])
def test_start_wait_budget_survives_wall_jump(monkeypatch, clock, metadata, jump, acquired, ready):
    clock.jump = jump
    events = []
    root = Path("deadline-root")
    capture = root / "capture"
    monkeypatch.setattr(
        sd, "_probe_daemon", lambda root: metadata if ready and clock.ticks >= 2 else None
    )
    monkeypatch.setattr(sd, "_try_acquire_daemon_start_lock", lambda root: acquired)

    def spawn(root):
        events.append("spawn")
        return 42, capture

    monkeypatch.setattr(sd, "_spawn_daemon_subprocess", spawn)
    monkeypatch.setattr(sd, "diagnose_user_secret", lambda: None)
    monkeypatch.setattr(sd, "read_daemon_start_capture", lambda fd: b"")
    monkeypatch.setattr(
        sd,
        "daemon_start_failure",
        lambda root, captured: sd.DaemonStartError(root, "timed out", "daemon_start_failed"),
    )
    monkeypatch.setattr(
        sd,
        "cleanup_daemon_start_capture",
        lambda fd, path: events.append(("cleanup", fd, path)),
    )
    monkeypatch.setattr(sd, "_release_daemon_start_lock", lambda root: events.append("release"))

    if ready:
        result = sd.start_session_daemon(str(root))
        assert result["running"] is True
        assert result["auto_started"] is acquired
        assert result["started_at"] == metadata["started_at"]
        assert clock.ticks == 2
    else:
        with pytest.raises(sd.DaemonStartError, match="timed out"):
            sd.start_session_daemon(str(root))
        assert clock.ticks == 3, "wall-clock jump shortened the startup wait budget"
    assert events == (["spawn", ("cleanup", 42, capture), "release"] if acquired else [])


@pytest.mark.parametrize("jump", [-1000, 0, 1000], ids=["rollback", "steady", "forward"])
@pytest.mark.parametrize("ready", [False, True], ids=["timeout", "ready"])
def test_lookup_retry_budget_survives_wall_jump(monkeypatch, clock, jump, ready):
    clock.jump = jump
    attempts = []
    payload = {"created_at": "1700000000"}

    def load(session_id, path):
        assert (session_id, path) == ("session-id", "deadline-root")
        attempts.append(clock.ticks)
        if ready and clock.ticks >= 2:
            return payload, "warm"
        raise FileNotFoundError("session not published")

    cache = SimpleNamespace(load_with_status=load)
    if ready:
        assert sd._load_payload_with_status_retry(cache, "session-id", "deadline-root") == (
            payload,
            "warm",
        )
        assert attempts == [0, 1, 2]
    else:
        with pytest.raises(FileNotFoundError, match="session not published"):
            sd._load_payload_with_status_retry(cache, "session-id", "deadline-root")
        assert attempts == [0, 1, 2, 3], "wall-clock jump shortened the lookup retry budget"


@pytest.mark.parametrize("jump", [-1000, 0, 1000], ids=["rollback", "steady", "forward"])
@pytest.mark.parametrize("ready", [False, True], ids=["escalation", "cooperative"])
def test_stop_wait_budget_survives_wall_jump(monkeypatch, clock, metadata, jump, ready):
    clock.jump = jump
    terminated_at = []
    removed = []
    monkeypatch.setattr(
        sd, "_probe_daemon", lambda root: None if ready and clock.ticks >= 2 else metadata
    )
    monkeypatch.setattr(sd, "_daemon_request", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(sd, "_await_endpoint_refused", lambda *args: True)

    def terminate(metadata, *, root):
        terminated_at.append(clock.ticks)
        return True

    monkeypatch.setattr(sd, "_terminate_daemon_by_pid", terminate)
    monkeypatch.setattr(
        sd, "_remove_daemon_metadata", lambda *args, **kwargs: removed.append(kwargs)
    )
    result = sd.stop_session_daemon("deadline-root")
    assert result["stopped"] is True
    assert result["stop_method"] == ("cooperative" if ready else "pid")
    assert clock.ticks == (2 if ready else 3)
    assert terminated_at == ([] if ready else [3])
    assert removed == [{"expected_pid": metadata["pid"], "expected_port": metadata["port"]}]
