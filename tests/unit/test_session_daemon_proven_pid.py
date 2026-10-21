"""The pid used to terminate a daemon must be the one PROVEN by the HMAC ping reply (PR #1197).

``_probe_daemon`` used to verify the signed reply (nonce|pid|root|port) and then return the
ORIGINAL metadata, whose ``pid`` was never verified. Root A's daemon.json could carry A's valid
endpoint and token but name root B's daemon pid; A's stop would then escalate to
``_terminate_daemon_by_pid(metadata)`` and kill B.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli.runtime_paths import _expected_tg_version


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 0.3)  # keep escalation tests fast


@contextmanager
def _daemon(root: Path, *, metadata_pid: int, package_version: str | None = None) -> Iterator[Any]:
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    real_shutdown = server.shutdown

    def _serve_then_close() -> None:
        # A real daemon is a PROCESS: when serve_forever returns the process exits and its listening
        # socket disappears (connections are REFUSED). In this in-process stand-in the socket would
        # otherwise stay bound, and on Linux a listening-but-not-accepting socket still completes
        # connects via the kernel backlog (probed on Linux), so a cooperative stop could never be
        # confirmed there. Close the listener when the loop ends, as process exit does.
        try:
            server.serve_forever()
        finally:
            server.server_close()

    thread = threading.Thread(target=_serve_then_close, daemon=True)
    thread.start()
    server._test_real_shutdown = real_shutdown  # type: ignore[attr-defined]
    sd._write_daemon_metadata(
        root,
        {
            "version": 1,
            "package_version": package_version or _expected_tg_version(),
            "root": str(root),
            "host": "127.0.0.1",
            "port": int(server.server_address[1]),
            "pid": metadata_pid,
            "started_at": "x",
            "token": "tok",
        },
    )
    try:
        yield server
    finally:
        real_shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_probe_returns_the_proven_pid_not_the_planted_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    with _daemon(root, metadata_pid=999_999):
        trace = _ProbeTrace()
        original_metadata = sd._read_daemon_metadata
        original_request = sd._daemon_request
        original_verify = sd._verify_ping_reply

        def _read_metadata(*args: Any, **kwargs: Any) -> Any:
            return trace.observe("metadata", original_metadata, *args, **kwargs)

        def _request(*args: Any, **kwargs: Any) -> Any:
            return trace.observe("request", original_request, *args, **kwargs)

        def _verify(*args: Any, **kwargs: Any) -> Any:
            return trace.observe("verify", original_verify, *args, **kwargs)

        monkeypatch.setattr(sd, "_read_daemon_metadata", _read_metadata)
        monkeypatch.setattr(sd, "_daemon_request", _request)
        monkeypatch.setattr(sd, "_verify_ping_reply", _verify)
        proven = sd._probe_daemon(root)
    assert proven is not None, f"sanitized probe stages: {trace.safe_summary()!r}"
    assert proven["pid"] == os.getpid()  # the in-process daemon's own pid, signed in the reply
    assert trace.safe_summary()["metadata"]["called"] is True
    assert trace.safe_summary()["request"]["called"] is True
    assert trace.safe_summary()["verify"]["called"] is True


def test_stop_never_signals_a_planted_pid_and_reports_an_unconfirmed_stop_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with _daemon(root, metadata_pid=victim.pid) as server:
            # The daemon acks "stop" but keeps serving (a wedged / lying shutdown).
            monkeypatch.setattr(server, "shutdown", lambda: None)
            calls: list[Any] = []

            def _record(metadata: Any, **_k: Any) -> bool:
                calls.append(dict(metadata) if metadata else metadata)
                return True  # "a terminate signal was delivered" -- but it never took effect

            monkeypatch.setattr(sd, "_terminate_daemon_by_pid", _record)
            result = sd.stop_session_daemon(str(root))
            assert calls, "the escalation was never attempted: the test is vacuous"
            assert all(c["pid"] != victim.pid for c in calls), calls
            assert all(c["pid"] == os.getpid() for c in calls), calls
            assert victim.poll() is None  # the unrelated process is untouched
            assert result["running"] is True
            assert result["stopped"] is False
            assert result["stop_method"] == "none"
            assert sd._read_daemon_metadata(root) is not None  # daemon.json is KEPT
    finally:
        victim.kill()
        victim.wait(timeout=5)


def _close_listener(server: Any) -> None:
    server._test_real_shutdown()
    server.server_close()


def test_stale_branch_terminate_true_while_the_listener_still_serves_is_not_a_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A package_version mismatch makes the probe return None (stale branch) although the
    # endpoint is alive. A DELIVERED signal is not a stopped daemon: only a refused connection is.
    root = tmp_path.resolve()
    with _daemon(root, metadata_pid=os.getpid(), package_version="0.0.0-stale"):
        assert sd._probe_daemon(root) is None
        calls: list[Any] = []
        monkeypatch.setattr(sd, "_terminate_daemon_by_pid", lambda m, **_k: calls.append(m) or True)
        result = sd.stop_session_daemon(str(root))
        assert calls, "no pid escalation happened: the test is vacuous"
        assert result["running"] is True
        assert result["stopped"] is False
        assert result["stop_method"] == "none"
        assert sd._read_daemon_metadata(root) is not None  # daemon.json kept


def test_stale_branch_control_terminate_true_then_listener_closed_is_a_pid_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    with _daemon(root, metadata_pid=os.getpid(), package_version="0.0.0-stale") as server:

        def _terminate_and_die(_m: Any, **_k: Any) -> bool:
            _close_listener(server)
            return True

        monkeypatch.setattr(sd, "_terminate_daemon_by_pid", _terminate_and_die)
        result = sd.stop_session_daemon(str(root))
        assert result["running"] is False
        assert result["stopped"] is True
        assert result["stop_method"] == "pid"
        assert sd._read_daemon_metadata(root) is None  # identity-guarded removal


def test_probe_branch_control_terminate_true_then_listener_closed_is_a_pid_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    with _daemon(root, metadata_pid=os.getpid()) as server:
        monkeypatch.setattr(server, "shutdown", lambda: None)  # ack "stop", keep serving

        def _terminate_and_die(_m: Any, **_k: Any) -> bool:
            _close_listener(server)
            return True

        monkeypatch.setattr(sd, "_terminate_daemon_by_pid", _terminate_and_die)
        result = sd.stop_session_daemon(str(root))
        assert result["running"] is False
        assert result["stopped"] is True
        assert result["stop_method"] == "pid"
        assert sd._read_daemon_metadata(root) is None


def test_cooperative_stop_control_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # POSITIVE CONTROL: a normal stop (daemon really shuts down) is confirmed, not escalated.
    root = tmp_path.resolve()
    calls: list[Any] = []
    monkeypatch.setattr(sd, "_terminate_daemon_by_pid", lambda m, **_k: calls.append(m) or False)
    with _daemon(root, metadata_pid=os.getpid()):
        result = sd.stop_session_daemon(str(root))
        assert result["running"] is False
        assert result["stopped"] is True
        assert result["stop_method"] == "cooperative"
        assert calls == []
        assert sd._read_daemon_metadata(root) is None


@dataclass
class _ProbeTrace:
    _steps: dict[str, dict[str, object]] = field(
        default_factory=lambda: {
            "metadata": {"called": False},
            "request": {"called": False},
            "verify": {"called": False},
        }
    )

    def observe(self, stage: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        entry = self._steps[stage]
        entry["called"] = True
        started = time.perf_counter()
        try:
            result = callback(*args, **kwargs)
        except BaseException as exc:
            entry["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            entry["exception_category"] = self._safe_exception_category(exc)
            raise
        entry["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        self._record_safe_result(stage, entry, result)
        return result

    @staticmethod
    def _safe_type_category(value: object) -> str:
        kind = type(value)
        if kind is type(None):
            return "none"
        if kind is bool:
            return "bool"
        if kind is int:
            return "int"
        if kind is str:
            return "str"
        if kind is dict:
            return "dict"
        if kind is list:
            return "list"
        if kind is float:
            return "float"
        return "other"

    @staticmethod
    def _safe_exception_category(exc: BaseException) -> str:
        kind = type(exc)
        if kind is TimeoutError or kind is ConnectionError or kind is ConnectionRefusedError:
            return "connection_or_timeout"
        if kind is OSError:
            return "os_error"
        if kind is RuntimeError or kind is ValueError or kind is TypeError or kind is KeyError:
            return "runtime_or_value_error"
        return "other"

    @staticmethod
    def _record_safe_result(stage: str, entry: dict[str, object], result: object) -> None:
        if stage == "metadata":
            entry["metadata_is_dict"] = type(result) is dict
            if type(result) is dict:
                host = result.get("host")
                port = result.get("port")
                version = result.get("package_version")
                entry["loopback_host"] = type(host) is str and host in {
                    "127.0.0.1",
                    "::1",
                    "localhost",
                }
                entry["valid_port"] = type(port) is int and 1 <= port <= 65535
                entry["package_version_matches"] = (
                    type(version) is str and version == _expected_tg_version()
                )
        elif stage == "request":
            entry["reply_is_dict"] = type(result) is dict
            if type(result) is dict:
                entry["reply_ok"] = result.get("ok") is True
                entry["pid_type"] = _ProbeTrace._safe_type_category(result.get("pid"))
                entry["root_type"] = _ProbeTrace._safe_type_category(result.get("root"))
                entry["port_type"] = _ProbeTrace._safe_type_category(result.get("port"))
                entry["proof_type"] = _ProbeTrace._safe_type_category(result.get("proof"))
                entry["package_version_type"] = _ProbeTrace._safe_type_category(
                    result.get("package_version")
                )
                entry["version_proof_type"] = _ProbeTrace._safe_type_category(
                    result.get("version_proof")
                )
        elif stage == "verify":
            entry["result_type"] = _ProbeTrace._safe_type_category(result)
            entry["verified"] = result is True

    def safe_summary(self) -> dict[str, dict[str, object]]:
        return {stage: dict(values) for stage, values in self._steps.items()}


def test_probe_trace_passes_arguments_and_result_through_without_retaining_values() -> None:
    trace = _ProbeTrace()
    marker = {"nonce": "synthetic-nonce", "token": "synthetic-token"}
    result = {
        "ok": True,
        "pid": 55,
        "root": "synthetic-root",
        "port": 1234,
        "proof": "synthetic-proof",
        "package_version": "1.2",
        "version_proof": "synthetic-version-proof",
    }
    received: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _callback(*args: Any, **kwargs: Any) -> Any:
        received.append((args, kwargs))
        return result

    observed = trace.observe("request", _callback, marker, token="synthetic-token")

    assert len(received) == 1
    assert received[0][0][0] is marker
    assert received[0][1] == {"token": "synthetic-token"}
    assert observed is result
    assert "synthetic-nonce" not in repr(trace.safe_summary())
    assert "synthetic-token" not in repr(trace.safe_summary())
    assert "synthetic-proof" not in repr(trace.safe_summary())
    assert trace.safe_summary()["request"]["reply_ok"] is True
    assert trace.safe_summary()["request"]["pid_type"] == "int"


def test_probe_trace_reraises_the_same_exception_and_records_only_its_class() -> None:
    trace = _ProbeTrace()
    failure = RuntimeError("synthetic private exception text")

    def _callback() -> None:
        raise failure

    with pytest.raises(RuntimeError) as caught:
        trace.observe("metadata", _callback)

    assert caught.value is failure
    assert trace.safe_summary()["metadata"]["exception_category"] == "runtime_or_value_error"
    assert "synthetic private exception text" not in repr(trace.safe_summary())


def test_ProbeTrace_UntrustedTypeNamesAndEndpointValues_AreRedacted() -> None:
    trace = _ProbeTrace()
    untrusted_name = "raw-root-987654321-private-class"
    untrusted_type = type(untrusted_name, (), {})
    result = {
        "ok": True,
        "pid": 987654321,
        "root": "C:/private/root-canary",
        "port": 45678,
        "proof": "private-proof-canary",
        "package_version": untrusted_type(),
        "version_proof": "private-version-proof-canary",
    }

    observed = trace.observe("request", lambda: result)
    summary = repr(trace.safe_summary())

    assert observed is result
    assert trace.safe_summary()["request"]["package_version_type"] == "other"
    assert trace.safe_summary()["request"]["pid_type"] == "int"
    assert trace.safe_summary()["request"]["root_type"] == "str"
    assert untrusted_name not in summary
    assert "987654321" not in summary
    assert "C:/private/root-canary" not in summary
    assert "private-proof-canary" not in summary
    assert "private-version-proof-canary" not in summary


def test_ProbeTrace_UntrustedExceptionNameAndMessage_AreRedacted() -> None:
    trace = _ProbeTrace()
    untrusted_name = "raw-root-987654321-private-exception"
    untrusted_error = type(untrusted_name, (Exception,), {})
    failure = untrusted_error("private exception message canary")

    with pytest.raises(Exception) as caught:
        trace.observe("verify", lambda: (_ for _ in ()).throw(failure))

    summary = repr(trace.safe_summary())
    assert caught.value is failure
    assert trace.safe_summary()["verify"]["exception_category"] == "other"
    assert untrusted_name not in summary
    assert "987654321" not in summary
    assert "private exception message canary" not in summary
