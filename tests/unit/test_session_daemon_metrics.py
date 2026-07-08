"""Tests for the tg-ledger step-0 demand instrumentation on the session daemon.

This is DOCS + INSTRUMENTATION only (see ``docs/multi_agent_context_plane.md``) -- there is no
ledger, no claims/findings surface, and no new command/flag. The counters exist purely to answer
two build-decision questions with real traffic: (1) do multiple distinct agent processes hit the
same daemon concurrently, and (2) do they re-request the same expensive artifact within a short
window that a shared plane would de-duplicate.

Mirrors the ``_ThreadedSessionDaemon`` harness in ``test_session_daemon_security.py``: a real
loopback server, driven with real sockets via ``session_daemon._daemon_request``, with the
session-store/repo-map layer stubbed out so these tests never need the compiled rust_core
extension or a real repo.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import tensor_grep.cli.session_daemon as session_daemon
from tensor_grep.cli.main import _render_doctor_payload


def _serve(server: session_daemon._ThreadedSessionDaemon) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _stub_dispatch(monkeypatch: Any, root: Path) -> None:
    """Route every non-lifecycle command through a cheap fake (mirrors the routing-only stub
    in test_session_daemon_security.py::test_daemon_request_path_field_cannot_escape_root) so
    these tests never touch the real session store / repo-map builder."""

    def _fake_serve(
        session_id: str,
        request: dict[str, Any],
        path: str,
        *,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {"ok": True, "session_id": session_id, "command": request.get("command")}

    monkeypatch.setattr(session_daemon, "serve_session_request", _fake_serve)
    monkeypatch.setattr(
        session_daemon,
        "_implicit_session_id_for_request",
        lambda server, *, command, session_id, path, request: session_id or "session-x",
    )
    monkeypatch.setattr(
        session_daemon,
        "_load_payload_with_status_retry",
        lambda cache, session_id, path: ({"root": str(root), "repo_map": {}}, "miss"),
    )


def _minimal_doctor_payload(session_daemon_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "platform": "test",
        "python_executable": "/usr/bin/python",
        "invoked_as": "tg",
        "root": "/repo",
        "session_daemon": session_daemon_status,
    }


def _empty_day_bucket(**overrides: Any) -> dict[str, Any]:
    bucket = {
        "requests": 0,
        "expensive_requests": 0,
        "distinct_client_pids": 0,
        "max_concurrent_distinct_clients": 0,
        "overlap_events": 0,
        "dup_requests": 0,
        "dup_targets": {},
        "by_command": {},
    }
    bucket.update(overrides)
    return bucket


# ------------------------------------------------------------------ 1: concurrent distinct clients


def test_concurrent_distinct_clients_tracked(tmp_path: Path, monkeypatch: Any) -> None:
    root = (tmp_path / "project-distinct").resolve()
    root.mkdir()
    _stub_dispatch(monkeypatch, root)

    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    thread = _serve(server)
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        monkeypatch.setattr(session_daemon.os, "getpid", lambda: 111)
        session_daemon._daemon_request(
            host, port, {"command": "defs", "symbol": "foo"}, token="tok"
        )
        monkeypatch.setattr(session_daemon.os, "getpid", lambda: 222)
        session_daemon._daemon_request(
            host, port, {"command": "defs", "symbol": "bar"}, token="tok"
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    days = server.demand_metrics.snapshot()
    assert len(days) == 1
    bucket = next(iter(days.values()))
    assert bucket["max_concurrent_distinct_clients"] == 2
    assert bucket["overlap_events"] >= 1


def test_repeated_same_client_pid_stays_at_one_concurrent_with_no_overlap(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = (tmp_path / "project-same-pid").resolve()
    root.mkdir()
    _stub_dispatch(monkeypatch, root)

    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    thread = _serve(server)
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        monkeypatch.setattr(session_daemon.os, "getpid", lambda: 333)
        session_daemon._daemon_request(
            host, port, {"command": "defs", "symbol": "foo"}, token="tok"
        )
        session_daemon._daemon_request(
            host, port, {"command": "defs", "symbol": "foo2"}, token="tok"
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    days = server.demand_metrics.snapshot()
    bucket = next(iter(days.values()))
    assert bucket["max_concurrent_distinct_clients"] == 1
    assert bucket["overlap_events"] == 0


# ------------------------------------------------------------------------- 2: repeat-artifact dup


def test_repeat_expensive_artifact_detected_as_dup(tmp_path: Path, monkeypatch: Any) -> None:
    root = (tmp_path / "project-dup").resolve()
    root.mkdir()
    _stub_dispatch(monkeypatch, root)

    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    thread = _serve(server)
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        # same blast_radius symbol twice -> 1 dup
        session_daemon._daemon_request(
            host, port, {"command": "blast_radius", "symbol": "Foo"}, token="tok"
        )
        session_daemon._daemon_request(
            host, port, {"command": "blast_radius", "symbol": "Foo"}, token="tok"
        )
        # a different symbol -> 0 additional dups
        session_daemon._daemon_request(
            host, port, {"command": "blast_radius", "symbol": "Bar"}, token="tok"
        )
        # same context_render query twice -> 1 dup
        session_daemon._daemon_request(
            host,
            port,
            {"command": "context_render", "query": "invoice payment"},
            token="tok",
        )
        session_daemon._daemon_request(
            host,
            port,
            {"command": "context_render", "query": "invoice payment"},
            token="tok",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    days = server.demand_metrics.snapshot()
    bucket = next(iter(days.values()))
    assert bucket["dup_requests"] == 2
    assert bucket["expensive_requests"] == 5


# ------------------------------------------------------------------ 3: lifecycle commands excluded


def test_lifecycle_commands_never_counted(tmp_path: Path, monkeypatch: Any) -> None:
    root = (tmp_path / "project-lifecycle").resolve()
    root.mkdir()

    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    thread = _serve(server)
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        monkeypatch.setattr(session_daemon.os, "getpid", lambda: 555)
        session_daemon._daemon_request(host, port, {"command": "ping"}, token="tok")
        session_daemon._daemon_request(host, port, {"command": "stats"}, token="tok")
        session_daemon._daemon_request(
            host, port, {"command": "health", "session_id": "nope"}, token="tok"
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert server.demand_metrics.snapshot() == {}

    # "stop" is excluded too, but a live "stop" round trip would shut the server down mid-test
    # (it self-terminates cooperatively), so verify the exclusion directly against record().
    server.demand_metrics.record(command="stop", client_pid=555, request={"command": "stop"})
    assert server.demand_metrics.snapshot() == {}


# --------------------------------------------------------------- 4: unauthorized records nothing


def test_unauthorized_request_records_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    root = (tmp_path / "project-unauth").resolve()
    root.mkdir()
    _stub_dispatch(monkeypatch, root)

    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    thread = _serve(server)
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        # No token at all.
        session_daemon._daemon_request(host, port, {"command": "defs", "symbol": "foo"})
        # Wrong token.
        session_daemon._daemon_request(
            host, port, {"command": "defs", "symbol": "foo", "token": "nope"}
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert server.demand_metrics.snapshot() == {}


# ----------------------------------------------------------------------------- 5: flush integrity


def test_flush_writes_valid_pii_free_json_and_prunes_bounds(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = (tmp_path / "project-flush").resolve()
    root.mkdir()
    metrics = session_daemon._DemandMetrics()

    secret_symbol = "TopSecretInternalApiKeyRotationHandler"
    secret_query = "rotate the internal api key handler right now"

    base = datetime(2026, 1, 1, tzinfo=UTC)

    # 35 distinct days -> must prune to the newest 30 on flush.
    for day_index in range(35):
        moment = base + timedelta(days=day_index)
        monkeypatch.setattr(session_daemon.time, "time", lambda m=moment: m.timestamp())
        metrics.record(
            command="defs",
            client_pid=1000 + day_index,
            request={"command": "defs", "symbol": secret_symbol},
        )

    # On the newest day: 40 distinct symbols, each requested twice (a dup pair) -> dup_targets
    # must cap at 32 entries even though every symbol is a genuine, distinct dup target.
    last_moment = base + timedelta(days=34)
    monkeypatch.setattr(session_daemon.time, "time", lambda m=last_moment: m.timestamp())
    for n in range(40):
        symbol = f"sym-{n}"
        metrics.record(command="refs", client_pid=1, request={"command": "refs", "symbol": symbol})
        metrics.record(command="refs", client_pid=1, request={"command": "refs", "symbol": symbol})
    # A query-shaped secret must also never reach disk in the clear.
    metrics.record(
        command="context_render",
        client_pid=1,
        request={"command": "context_render", "query": secret_query},
    )

    metrics_path = session_daemon._metrics_file_path(root)
    session_daemon._write_demand_metrics(root, metrics)

    assert metrics_path.exists()
    leftover_tmp = list(metrics_path.parent.glob(f".{metrics_path.name}.*.tmp"))
    assert leftover_tmp == [], "atomic write must not leave a .tmp behind"

    raw_bytes = metrics_path.read_bytes()
    data = json.loads(raw_bytes)  # must be valid JSON
    assert isinstance(data["days"], dict)
    assert len(data["days"]) == 30, "35 day-buckets must prune down to the newest 30"

    newest_day = max(data["days"])
    dup_targets = data["days"][newest_day]["dup_targets"]
    assert len(dup_targets) <= 32

    # PII-free: the persisted bytes must never contain the literal symbol/query text.
    assert secret_symbol.encode("utf-8") not in raw_bytes
    assert secret_query.encode("utf-8") not in raw_bytes
    assert b"sym-0" not in raw_bytes


def test_metrics_target_hash_never_embeds_the_raw_target() -> None:
    target_hash = session_daemon._metrics_target_hash("defs", "super_secret_symbol")
    assert "super_secret_symbol" not in target_hash
    assert len(target_hash) == 16
    # deterministic for the same (command, target) pair
    assert target_hash == session_daemon._metrics_target_hash("defs", "super_secret_symbol")
    # different target -> different hash
    assert target_hash != session_daemon._metrics_target_hash("defs", "other_symbol")


# -------------------------------------------------------------- 6: load-merge survives a restart


def test_preseeded_metrics_survive_load_and_flush_no_clobber(tmp_path: Path) -> None:
    root = (tmp_path / "project-preseed").resolve()
    session_daemon._sessions_dir(root).mkdir(parents=True)

    preseeded = {
        "version": 1,
        "root": str(root),
        "days": {
            "2026-06-01": {
                "requests": 50,
                "expensive_requests": 40,
                "distinct_client_pids": 5,
                "max_concurrent_distinct_clients": 3,
                "overlap_events": 7,
                "dup_requests": 12,
                "dup_targets": {"abc123": 4},
                "by_command": {"defs": 30, "refs": 10},
            }
        },
    }
    metrics_path = session_daemon._metrics_file_path(root)
    metrics_path.write_text(json.dumps(preseeded), encoding="utf-8")

    # Simulate a fresh daemon process (new server, new in-memory metrics object) loading the
    # prior run's history before serving anything.
    metrics = session_daemon._DemandMetrics()
    metrics.load(session_daemon._read_demand_metrics_days(root))
    session_daemon._write_demand_metrics(root, metrics)

    data = json.loads(metrics_path.read_text(encoding="utf-8"))
    bucket = data["days"]["2026-06-01"]
    assert bucket["requests"] == 50
    assert bucket["distinct_client_pids"] == 5
    assert bucket["max_concurrent_distinct_clients"] == 3
    assert bucket["dup_targets"] == {"abc123": 4}
    assert bucket["by_command"] == {"defs": 30, "refs": 10}


# --------------------------------------------------------- 7: status read-back + doctor rendering


def test_status_readback_includes_demand_metrics_and_pre_gate_met(tmp_path: Path) -> None:
    root = (tmp_path / "project-status-met").resolve()
    session_daemon._sessions_dir(root).mkdir(parents=True)

    today = datetime.now(UTC).date()
    days = {
        (today - timedelta(days=i)).strftime("%Y-%m-%d"): _empty_day_bucket(
            requests=20,
            expensive_requests=15,
            distinct_client_pids=3,
            max_concurrent_distinct_clients=2,
            overlap_events=5,
            dup_requests=4,
        )
        for i in range(4)
    }
    session_daemon._metrics_file_path(root).write_text(
        json.dumps({"version": 1, "root": str(root), "days": days}), encoding="utf-8"
    )

    # No daemon.json here -> the status read-back must work with the daemon STOPPED.
    status = session_daemon.get_session_daemon_status(str(root))
    assert status["running"] is False
    assert "demand_metrics" in status
    demand = status["demand_metrics"]
    assert demand["days_covered"] == 4
    assert demand["days_with_2plus_concurrent"] == 4
    assert demand["dup_requests_14d"] == 16
    assert demand["pre_gate_met"] is True

    rendered = _render_doctor_payload(_minimal_doctor_payload(status))
    assert "session_daemon_demand(14d):" in rendered
    assert "pre_gate=MET" in rendered


def test_doctor_line_distinguishes_no_coverage_from_not_met(tmp_path: Path) -> None:
    # NO-COVERAGE: no daemon_metrics.json exists at all for this root.
    empty_root = (tmp_path / "project-empty").resolve()
    empty_root.mkdir()
    empty_status = session_daemon.get_session_daemon_status(str(empty_root))
    rendered_empty = _render_doctor_payload(_minimal_doctor_payload(empty_status))
    assert "pre_gate=NO-COVERAGE" in rendered_empty

    # NOT-MET: a covered day exists, but it does not clear the gate thresholds.
    sparse_root = (tmp_path / "project-sparse").resolve()
    session_daemon._sessions_dir(sparse_root).mkdir(parents=True)
    sparse_day = (datetime.now(UTC).date() - timedelta(days=1)).strftime("%Y-%m-%d")
    sparse_days = {
        sparse_day: _empty_day_bucket(
            requests=5,
            expensive_requests=3,
            distinct_client_pids=1,
            max_concurrent_distinct_clients=1,
            dup_requests=1,
        )
    }
    session_daemon._metrics_file_path(sparse_root).write_text(
        json.dumps({"version": 1, "root": str(sparse_root), "days": sparse_days}),
        encoding="utf-8",
    )
    sparse_status = session_daemon.get_session_daemon_status(str(sparse_root))
    rendered_sparse = _render_doctor_payload(_minimal_doctor_payload(sparse_status))
    assert "pre_gate=NOT-MET" in rendered_sparse
    assert sparse_status["demand_metrics"]["days_covered"] == 1
    assert sparse_status["demand_metrics"]["pre_gate_met"] is False


# --------------------------------------------------------------------------- 8: fail-open record


def test_record_exception_never_breaks_serving(tmp_path: Path, monkeypatch: Any) -> None:
    root = (tmp_path / "project-failopen").resolve()
    root.mkdir()
    _stub_dispatch(monkeypatch, root)

    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("demand_metrics exploded")

    monkeypatch.setattr(server.demand_metrics, "record", _boom)
    thread = _serve(server)
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        response = session_daemon._daemon_request(
            host, port, {"command": "defs", "symbol": "foo"}, token="tok"
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert response.get("ok") is True


# ----------------------------------------------------------- 9: client_pid does not split caching


def test_client_pid_does_not_fragment_response_cache(tmp_path: Path, monkeypatch: Any) -> None:
    root = (tmp_path / "project-cache").resolve()
    root.mkdir()

    call_count = {"n": 0}

    def _fake_serve(
        session_id: str,
        request: dict[str, Any],
        path: str,
        *,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        call_count["n"] += 1
        return {
            "ok": True,
            "session_id": session_id,
            "routing_reason": "session-context-render",
        }

    monkeypatch.setattr(session_daemon, "serve_session_request", _fake_serve)
    monkeypatch.setattr(
        session_daemon,
        "_implicit_session_id_for_request",
        lambda server, *, command, session_id, path, request: session_id or "session-x",
    )
    monkeypatch.setattr(
        session_daemon,
        "_load_payload_with_status_retry",
        lambda cache, session_id, path: ({"root": str(root), "repo_map": {}}, "miss"),
    )

    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    thread = _serve(server)
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        request = {
            "command": "context_render",
            "session_id": "session-x",
            "query": "invoice payment",
        }
        monkeypatch.setattr(session_daemon.os, "getpid", lambda: 111)
        first = session_daemon._daemon_request(host, port, dict(request), token="tok")
        monkeypatch.setattr(session_daemon.os, "getpid", lambda: 222)
        second = session_daemon._daemon_request(host, port, dict(request), token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert call_count["n"] == 1, "serve_session_request must only actually run once"
    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["daemon_response_cache"]["status"] == "hit"
    assert server.response_cache.hits >= 1


# -------------------------------------------------------------------------- bonus: kill switch


def test_kill_switch_disables_recording(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv(session_daemon._DAEMON_METRICS_ENABLED_ENV, "0")
    metrics = session_daemon._DemandMetrics()
    metrics.record(command="defs", client_pid=1, request={"command": "defs", "symbol": "foo"})
    assert metrics.snapshot() == {}


def test_metrics_enabled_by_default(monkeypatch: Any) -> None:
    monkeypatch.delenv(session_daemon._DAEMON_METRICS_ENABLED_ENV, raising=False)
    assert session_daemon._daemon_metrics_enabled() is True
    monkeypatch.setenv(session_daemon._DAEMON_METRICS_ENABLED_ENV, "0")
    assert session_daemon._daemon_metrics_enabled() is False
    monkeypatch.setenv(session_daemon._DAEMON_METRICS_ENABLED_ENV, "1")
    assert session_daemon._daemon_metrics_enabled() is True
