"""`tg doctor` must never hang on external LSP providers (fail closed as ``unresponsive``).

Receipt: `tests/unit/test_mcp_contract_stamp_ratchet.py` appeared to hang on a dev box with
real language servers installed -- `tg_doctor` probed every provider serially, each with its
own per-phase timeout and NO total deadline, so wall time was ``languages x phases x timeout``.

Hermetic: the "provider" is a fake child that never answers any request (including
``shutdown``) and never reads its stdin. Every arm runs the subject on a worker thread with
a hard join deadline so a regression is a test FAIL, not a CI hang.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import doctor_report, lsp_external_provider

_HARD_DEADLINE_SECONDS = 40.0

_FAKE_SERVER = "import time\ntime.sleep(600)\n"


def _fake_command(tmp_path: Path) -> list[str]:
    script = tmp_path / "silent_lsp.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8", newline="\n")
    return [sys.executable, str(script)]


def _run_bounded(fn: Any) -> tuple[Any, float]:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # surfaced below
            box["error"] = exc

    started = time.monotonic()
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=_HARD_DEADLINE_SECONDS)
    elapsed = time.monotonic() - started
    assert not thread.is_alive(), f"HUNG: still running after {_HARD_DEADLINE_SECONDS}s"
    if "error" in box:
        raise box["error"]
    return box["result"], elapsed


def test_doctor_lsp_sweep_has_total_deadline_and_reports_unresponsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _fake_command(tmp_path)
    languages = ["python", "go", "rust", "java", "typescript", "javascript"]
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    monkeypatch.setattr(doctor_report, "_doctor_lsp_languages", lambda: list(languages))
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", "2")

    statuses, elapsed = _run_bounded(
        lambda: doctor_report._doctor_lsp_provider_statuses(str(tmp_path))
    )

    # Pre-fix: ~1s initialize + ~1s bounded stop per provider x 6 providers (> 10s).
    assert elapsed < 9.0, f"doctor LSP sweep took {elapsed:.1f}s; total deadline not enforced"
    assert [s["language"] for s in statuses] == languages
    assert all(s["lsp_proof"] is False for s in statuses)
    assert all(s["health_status"] in {"unhealthy", "unresponsive"} for s in statuses)
    assert statuses[-1]["health_status"] == "unresponsive"
    assert statuses[-1]["health_check"] == "deadline_exceeded"
    assert "deadline" in str(statuses[-1]["last_error"])


def test_client_stop_is_bounded_for_child_that_ignores_shutdown_and_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _fake_command(tmp_path)
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    client = lsp_external_provider.ExternalLSPClient(
        language="python",
        workspace_root=tmp_path,
        request_timeout_seconds=1.0,
        initialize_timeout_seconds=1.0,
    )
    process = None
    try:
        with pytest.raises(lsp_external_provider.LSPTransportError):
            _run_bounded(client.start)
        process = client.process
        client.stop()
        _, elapsed = _run_bounded(client.stop)
        assert elapsed < 10.0
    finally:
        if process is not None and process.poll() is None:
            process.kill()


def test_doctor_lsp_total_timeout_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", raising=False)
    assert doctor_report._doctor_lsp_total_timeout_seconds() == 90.0
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", "garbage")
    assert doctor_report._doctor_lsp_total_timeout_seconds() == 90.0
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", "-3")
    assert doctor_report._doctor_lsp_total_timeout_seconds() == 90.0
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", "7")
    assert doctor_report._doctor_lsp_total_timeout_seconds() == 7.0
