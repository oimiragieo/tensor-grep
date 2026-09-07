from __future__ import annotations

import json
import threading
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import session_store
from tensor_grep.cli.main import app
from tensor_grep.cli.session_resume_service import session_prepare
from tensor_grep.cli.session_store import refresh_session

runner = CliRunner()

_TIMEOUT_S = 10.0


def test_refresh_does_not_clobber_a_prepare_that_races_in_after_its_initial_read(
    tmp_path: Path,
) -> None:
    """AGT-02 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 02): refresh_session
    reads `existing = get_session(...)` (session_store.py) OUTSIDE its publication lock, then
    carries `existing["last_prepare"]` forward once it acquires the lock. If a concurrent
    `session_prepare` acquires the lock and publishes a NEWER decision in between refresh's
    initial read and refresh's own lock acquisition, refresh's stale `existing` snapshot
    overwrites that newer decision on write. Gate refresh immediately after its initial read
    with two threading.Events; the interleaved prepare must survive the refresh."""
    f1 = tmp_path / "calc.py"
    f1.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    open_res = runner.invoke(app, ["session", "open", str(tmp_path), "--json"])
    assert open_res.exit_code == 0
    session_id = json.loads(open_res.stdout)["session_id"]

    prep_res = runner.invoke(
        app, ["session", "prepare", session_id, "old query", str(tmp_path), "--json"]
    )
    assert prep_res.exit_code == 0

    f2 = tmp_path / "extra.py"
    f2.write_text("def noop():\n    pass\n", encoding="utf-8")

    refresh_read_done = threading.Event()
    prepare_published = threading.Event()
    real_get_session = session_store.get_session
    gate_fired = threading.Event()

    def gated_get_session(*args: object, **kwargs: object) -> dict[str, object]:
        result = real_get_session(*args, **kwargs)  # type: ignore[arg-type]
        if not gate_fired.is_set():
            gate_fired.set()
            refresh_read_done.set()
            assert prepare_published.wait(_TIMEOUT_S), "concurrent prepare never published"
        return result

    session_store.get_session = gated_get_session  # type: ignore[assignment]
    try:
        refresh_result: dict[str, Exception | None] = {"error": None}

        def run_refresh() -> None:
            try:
                refresh_session(session_id, str(tmp_path))
            except Exception as exc:  # pragma: no cover - surfaced via assertion below
                refresh_result["error"] = exc

        refresh_thread = threading.Thread(target=run_refresh)
        refresh_thread.start()

        assert refresh_read_done.wait(_TIMEOUT_S), "refresh_session never reached its initial read"
        session_prepare(session_id, "new query racing the refresh", str(tmp_path))
        prepare_published.set()

        refresh_thread.join(_TIMEOUT_S)
        assert not refresh_thread.is_alive(), "refresh_session did not finish within timeout"
        assert refresh_result["error"] is None, refresh_result["error"]
    finally:
        session_store.get_session = real_get_session  # type: ignore[assignment]

    resume_res = runner.invoke(app, ["session", "resume", session_id, str(tmp_path), "--json"])
    assert resume_res.exit_code == 0
    resume_data = json.loads(resume_res.stdout)
    last_prepare = resume_data.get("last_prepare")
    assert last_prepare is not None, "refresh must not discard the concurrently-published decision"
    assert last_prepare.get("query") == "new query racing the refresh", (
        "refresh clobbered the newer decision with the stale pre-lock snapshot it read before "
        f"acquiring its publication lock: got {last_prepare!r}"
    )
