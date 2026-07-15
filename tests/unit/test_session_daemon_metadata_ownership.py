"""TDD for task #143a-a: the session daemon must remove only ITS OWN daemon.json.

Before this fix, ``_remove_daemon_metadata`` unlinked ``daemon.json`` unconditionally regardless
of caller. When ``_probe_daemon`` rejects a daemon over a ``package_version`` mismatch (task #94
PR-1, see test_session_daemon_version_skew.py), the client spawns a REPLACEMENT daemon while the
rejected one (still alive) keeps running until its own idle/max-uptime self-shutdown fires. That
old daemon's shutdown path -- and ``stop_session_daemon``'s PID-kill fallback -- called
``_remove_daemon_metadata`` unconditionally, deleting WHATEVER metadata currently existed,
including the healthy replacement's. Result: silent orphan-daemon pileup (the old daemon exits,
taking the replacement's only discoverable record with it) and a live daemon no client can find.

The fix: ``_remove_daemon_metadata`` accepts optional ``expected_pid``/``expected_port`` and only
unlinks when the CURRENT on-disk metadata still matches -- i.e. it still identifies the exact
instance the caller intends to remove. ``run_session_daemon_server``'s shutdown ``finally`` and
both ``stop_session_daemon`` cleanup paths now pass their own target identity; only
``_spawn_daemon_subprocess`` (protected by the daemon-start lock, and about to publish a brand new
instance's metadata) still uses the unconditional (force) mode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tensor_grep.cli import session_daemon


def _payload(*, pid: int, port: int, token: str = "tok") -> dict[str, Any]:
    return {
        "version": 1,
        "package_version": "irrelevant-for-these-tests",
        "root": "irrelevant",
        "host": "127.0.0.1",
        "port": port,
        "pid": pid,
        "started_at": "test",
        "token": token,
    }


# ------------------------------------------------------------------------- _daemon_identity


def test_daemon_identity_extracts_pid_and_port() -> None:
    assert session_daemon._daemon_identity(_payload(pid=111, port=2222)) == (111, 2222)


def test_daemon_identity_handles_missing_and_malformed() -> None:
    assert session_daemon._daemon_identity(None) == (None, None)
    assert session_daemon._daemon_identity({}) == (None, None)
    assert session_daemon._daemon_identity({"pid": "not-an-int", "port": 1}) == (None, 1)
    assert session_daemon._daemon_identity({"pid": 1, "port": "not-an-int"}) == (1, None)


# --------------------------------------------------------- _remove_daemon_metadata: force mode


def test_remove_daemon_metadata_force_mode_removes_unconditionally(tmp_path: Path) -> None:
    # Locks in _spawn_daemon_subprocess's contract: no expected_* kwargs -> unconditional
    # removal, regardless of whose metadata is currently on disk.
    root = tmp_path.resolve()
    session_daemon._write_daemon_metadata(root, _payload(pid=999, port=9999))
    metadata_path = session_daemon._daemon_metadata_path(root)
    assert metadata_path.exists()

    session_daemon._remove_daemon_metadata(root)

    assert not metadata_path.exists()


def test_remove_daemon_metadata_force_mode_is_noop_when_file_missing(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    session_daemon._remove_daemon_metadata(root)  # must not raise
    assert not session_daemon._daemon_metadata_path(root).exists()


# ------------------------------------------------------- _remove_daemon_metadata: guarded mode


def test_remove_daemon_metadata_guarded_removes_when_identity_matches(tmp_path: Path) -> None:
    # "A's own normal shutdown DOES remove A's metadata when A still owns it."
    root = tmp_path.resolve()
    session_daemon._write_daemon_metadata(root, _payload(pid=111, port=2222))

    session_daemon._remove_daemon_metadata(root, expected_pid=111, expected_port=2222)

    assert session_daemon._read_daemon_metadata(root) is None


def test_remove_daemon_metadata_guarded_preserves_replacement_on_pid_mismatch(
    tmp_path: Path,
) -> None:
    # The core #143a-a race: daemon-A writes metadata, a replacement daemon-B overwrites
    # daemon.json with B's pid/port, then daemon-A's shutdown path runs _remove_daemon_metadata
    # (exactly as run_session_daemon_server's finally does, passing A's own pid/port) -- assert
    # daemon.json STILL holds B's identity afterward (A did not delete B's).
    root = tmp_path.resolve()
    session_daemon._write_daemon_metadata(root, _payload(pid=111, port=2222))  # A starts
    session_daemon._write_daemon_metadata(root, _payload(pid=222, port=3333))  # B replaces A

    # A's shutdown path believes it owns pid=111/port=2222 -- it does not, anymore.
    session_daemon._remove_daemon_metadata(root, expected_pid=111, expected_port=2222)

    survivor = session_daemon._read_daemon_metadata(root)
    assert survivor is not None, "the replacement daemon's metadata must survive"
    assert survivor["pid"] == 222
    assert survivor["port"] == 3333


def test_remove_daemon_metadata_guarded_preserves_replacement_on_port_mismatch(
    tmp_path: Path,
) -> None:
    # Defense in depth: a port mismatch alone (pid coincidentally equal, e.g. pid reuse across
    # process churn) must also block the delete -- both fields are checked independently.
    root = tmp_path.resolve()
    session_daemon._write_daemon_metadata(root, _payload(pid=111, port=2222))
    session_daemon._write_daemon_metadata(root, _payload(pid=111, port=3333))  # same pid, new port

    session_daemon._remove_daemon_metadata(root, expected_pid=111, expected_port=2222)

    survivor = session_daemon._read_daemon_metadata(root)
    assert survivor is not None
    assert survivor["port"] == 3333


def test_remove_daemon_metadata_guarded_noop_when_file_missing(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    session_daemon._remove_daemon_metadata(root, expected_pid=111, expected_port=2222)
    assert session_daemon._read_daemon_metadata(root) is None


def test_remove_daemon_metadata_guarded_leaves_corrupt_file_untouched(tmp_path: Path) -> None:
    # Fail-safe choice: an unparseable file's identity cannot be verified, so it is left alone
    # rather than guessed-and-deleted. It self-heals via _spawn_daemon_subprocess's force mode
    # the next time a daemon is (re)started for this root.
    root = tmp_path.resolve()
    metadata_path = session_daemon._daemon_metadata_path(root)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text("{not valid json", encoding="utf-8")

    session_daemon._remove_daemon_metadata(root, expected_pid=111, expected_port=2222)

    assert metadata_path.exists()
    assert metadata_path.read_text(encoding="utf-8") == "{not valid json"


# ----------------------------------------------------- stop_session_daemon call-site coverage


def test_stop_session_daemon_stale_fallback_preserves_concurrent_replacement(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Reproduces the #143a-a race at the stop_session_daemon layer (the ~775 fallback branch):
    the cooperative probe fails (version mismatch / wedged socket), so stop_session_daemon falls
    back to reading + pid-killing the STALE metadata. If a replacement daemon B publishes its own
    daemon.json in the narrow window between that read and the cleanup unlink, the (formerly)
    unconditional unlink deleted B's metadata too. The guarded removal must leave B's file alone.
    """
    root = (tmp_path / "project").resolve()
    root.mkdir()
    session_daemon._write_daemon_metadata(root, _payload(pid=424242, port=11111, token="stale"))

    def _fake_terminate(metadata: dict[str, Any] | None) -> bool:
        # Simulate daemon B publishing its own metadata WHILE A is being torn down -- the exact
        # narrow window the guard must protect.
        session_daemon._write_daemon_metadata(
            root, _payload(pid=434343, port=22222, token="replacement")
        )
        return False  # irrelevant to this test whether the pid-kill itself "succeeded"

    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_terminate_daemon_by_pid", _fake_terminate)

    result = session_daemon.stop_session_daemon(str(root))

    assert result["running"] is False
    survivor = session_daemon._read_daemon_metadata(root)
    assert survivor is not None, "the replacement daemon's metadata must survive"
    assert survivor["pid"] == 434343
    assert survivor["port"] == 22222


def test_stop_session_daemon_cooperative_path_preserves_concurrent_replacement(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Mirrors the fallback-branch test above but for the main cooperative-stop tail (the ~805
    branch): the probe succeeds once (stop_session_daemon captures a live `metadata`), the stop
    command is dispatched, and by the time the post-stop poll observes the daemon gone, a
    replacement has already published its own metadata. The cleanup unlink must target only the
    ORIGINAL daemon's identity, not whatever is currently on disk."""
    root = (tmp_path / "project").resolve()
    root.mkdir()
    target_metadata = _payload(pid=111, port=2222, token="a")

    probe_calls = {"count": 0}

    def _fake_probe(_root: Path) -> dict[str, Any] | None:
        probe_calls["count"] += 1
        if probe_calls["count"] == 1:
            return target_metadata
        return None  # every subsequent probe: the daemon has (apparently) stopped

    def _fake_daemon_request(
        host: str,
        port: int,
        request: dict[str, Any],
        *,
        response_timeout: float | None = None,
        token: str = "",
    ) -> dict[str, Any]:
        if request.get("command") == "stop":
            # A replacement daemon B publishes its own metadata in the window while A processes
            # the stop request.
            session_daemon._write_daemon_metadata(root, _payload(pid=222, port=3333, token="b"))
            return {"version": 1, "ok": True, "stopping": True}
        return {"version": 1, "ok": True}

    monkeypatch.setattr(session_daemon, "_probe_daemon", _fake_probe)
    monkeypatch.setattr(session_daemon, "_daemon_request", _fake_daemon_request)

    result = session_daemon.stop_session_daemon(str(root))

    assert result["running"] is False
    assert result["stopped"] is True
    survivor = session_daemon._read_daemon_metadata(root)
    assert survivor is not None, "the replacement daemon's metadata must survive"
    assert survivor["pid"] == 222
    assert survivor["port"] == 3333


def test_stop_session_daemon_removes_own_metadata_when_uncontested(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The happy path must behave exactly as before the fix: when nothing races in, stopping the
    # daemon removes its own (uncontested) metadata.
    root = (tmp_path / "project").resolve()
    root.mkdir()
    target_metadata = _payload(pid=111, port=2222, token="a")
    session_daemon._write_daemon_metadata(root, target_metadata)

    probe_calls = {"count": 0}

    def _fake_probe(_root: Path) -> dict[str, Any] | None:
        probe_calls["count"] += 1
        if probe_calls["count"] == 1:
            return target_metadata
        return None

    def _fake_daemon_request(
        host: str,
        port: int,
        request: dict[str, Any],
        *,
        response_timeout: float | None = None,
        token: str = "",
    ) -> dict[str, Any]:
        return {"version": 1, "ok": True, "stopping": True}

    monkeypatch.setattr(session_daemon, "_probe_daemon", _fake_probe)
    monkeypatch.setattr(session_daemon, "_daemon_request", _fake_daemon_request)

    result = session_daemon.stop_session_daemon(str(root))

    assert result["running"] is False
    assert result["stopped"] is True
    assert session_daemon._read_daemon_metadata(root) is None
