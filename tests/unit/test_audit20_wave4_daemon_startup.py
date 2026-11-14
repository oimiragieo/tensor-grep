"""Daemon startup control for malformed persisted demand metrics."""

import json
from pathlib import Path

from tensor_grep.cli import session_daemon as sd


def test_daemon_server_starts_with_malformed_persisted_metrics(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0"\n')
    metrics_path = root / "daemon_metrics.json"
    metrics_path.write_text(
        json.dumps({
            "days": {
                "2026-11-11": {"requests": 2},
                "2026-11-12": {"dup_targets": {"a": None}},
            }
        })
    )

    class FakeServer:
        def __init__(self) -> None:
            self.demand_metrics = sd._DemandMetrics()
            self.server_address = ("127.0.0.1", 12345)
            self.served = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def serve_forever(self, *, poll_interval: float) -> None:
            assert poll_interval == 0.1
            self.served = True

    server = FakeServer()
    monkeypatch.setattr(sd, "_metrics_file_path", lambda _root: metrics_path)
    monkeypatch.setattr(sd, "_load_or_create_user_secret", lambda: object())
    monkeypatch.setattr(sd, "_ThreadedSessionDaemon", lambda *_args, **_kwargs: server)
    monkeypatch.setattr(sd, "_write_daemon_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sd, "_attestation_fields", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(sd, "_run_daemon_lifecycle_monitor", lambda *_args: None)
    monkeypatch.setattr(sd, "_flush_demand_metrics_if_dirty", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sd, "_remove_daemon_metadata", lambda *_args, **_kwargs: None)

    sd.run_session_daemon_server(str(root))

    assert server.served is True
    assert server.demand_metrics._days["2026-11-11"]["requests"] == 2
    assert server.demand_metrics._days["2026-11-12"]["dup_targets"] == {}
