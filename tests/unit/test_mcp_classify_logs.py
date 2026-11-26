from __future__ import annotations

import json
from pathlib import Path

import pytest

from tensor_grep.cli import mcp_server

_SAMPLE_LOGS = (
    "INFO startup complete\n"
    "CRITICAL database primary connection lost\n"
    "FATAL worker pool exhausted\n"
    "ALERT disk usage at 99 percent\n"
)


@pytest.mark.parametrize("mode", ["text", "file_path"])
def test_tg_classify_logs_retains_critical_fatal_alert_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)

    if mode == "text":
        out = mcp_server.tg_classify_logs(text=_SAMPLE_LOGS, structured_json=True)
    else:
        log_file = tmp_path / "anomalies.log"
        log_file.write_text(_SAMPLE_LOGS, encoding="utf-8")
        out = mcp_server.tg_classify_logs(file_path=str(log_file), structured_json=True)

    payload = json.loads(out)
    assert payload["anomaly_count"] == 3
    by_text = {entry["text"]: entry["label"] for entry in payload["anomalies"]}
    assert by_text == {
        "CRITICAL database primary connection lost": "critical",
        "FATAL worker pool exhausted": "error",
        "ALERT disk usage at 99 percent": "critical",
    }


@pytest.mark.parametrize("mode", ["text", "file_path"])
def test_tg_classify_logs_retains_critical_fatal_alert_plain_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)

    if mode == "text":
        out = mcp_server.tg_classify_logs(text=_SAMPLE_LOGS, structured_json=False)
    else:
        log_file = tmp_path / "anomalies.log"
        log_file.write_text(_SAMPLE_LOGS, encoding="utf-8")
        out = mcp_server.tg_classify_logs(file_path=str(log_file), structured_json=False)

    assert "Detected 3 High-Confidence Anomalies:" in out
    assert "[CRITICAL]" in out
    assert "[ERROR]" in out
    assert "CRITICAL database primary connection lost" in out
    assert "FATAL worker pool exhausted" in out
    assert "ALERT disk usage at 99 percent" in out
