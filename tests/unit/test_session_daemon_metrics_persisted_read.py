"""Persisted metrics read/load controls without starting a daemon or opening sockets."""

import json

from tensor_grep.cli import session_daemon


def _load_persisted_metrics(tmp_path, monkeypatch, content):
    path = tmp_path / "daemon_metrics.json"
    path.write_bytes(content)
    monkeypatch.setattr(session_daemon, "_metrics_file_path", lambda root: path)
    metrics = session_daemon._DemandMetrics()
    metrics.load(session_daemon._read_demand_metrics_days(tmp_path))
    return metrics._days


def test_persisted_metrics_invalid_utf8_is_ignored_at_startup_load(tmp_path, monkeypatch):
    days = _load_persisted_metrics(
        tmp_path, monkeypatch, b'{"days":{"2026-11-12":{"requests":2}}}\xff'
    )
    assert days == {}


def test_persisted_metrics_valid_json_preserves_counters(tmp_path, monkeypatch):
    days = _load_persisted_metrics(
        tmp_path,
        monkeypatch,
        b'{"days":{"2026-11-12":{"requests":2,"by_command":{"defs":2}}}}',
    )
    assert days["2026-11-12"]["requests"] == 2
    assert days["2026-11-12"]["by_command"] == {"defs": 2}


def test_persisted_metrics_invalid_json_is_ignored_at_startup_load(tmp_path, monkeypatch):
    assert _load_persisted_metrics(tmp_path, monkeypatch, b'{"days":') == {}


def test_persisted_metrics_mixed_mapping_keeps_valid_entries(tmp_path, monkeypatch):
    content = json.dumps({
        "days": {
            "2026-11-12": {
                "requests": float("inf"),
                "dup_targets": {"valid": 2, "none": None, "infinite": float("inf")},
                "by_command": {"defs": 3, "none": None, "infinite": float("inf")},
            }
        }
    }).encode("utf-8")
    days = _load_persisted_metrics(tmp_path, monkeypatch, content)
    assert days["2026-11-12"]["requests"] == 0
    assert days["2026-11-12"]["dup_targets"] == {"valid": 2}
    assert days["2026-11-12"]["by_command"] == {"defs": 3}
