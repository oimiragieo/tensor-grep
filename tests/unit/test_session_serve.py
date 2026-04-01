import json
from io import BytesIO, StringIO
from pathlib import Path

import tensor_grep.cli.session_daemon as session_daemon_module
from tensor_grep.cli import session_store
from tensor_grep.cli.session_daemon import _SessionDaemonHandler, _ThreadedSessionDaemon


def _write_python_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_project(root: Path, module_name: str, symbol_name: str) -> Path:
    project = root
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    _write_python_file(
        src_dir / f"{module_name}.py",
        f"def {symbol_name}():\n    return '{symbol_name}'\n",
    )
    _write_python_file(
        tests_dir / f"test_{module_name}.py",
        f"from src.{module_name} import {symbol_name}\n",
    )
    return project


class _MutatingRequestStream:
    def __init__(self, lines: list[str], before_line_index: int, mutate) -> None:
        self._lines = lines
        self._before_line_index = before_line_index
        self._mutate = mutate
        self._index = 0

    def __iter__(self) -> "_MutatingRequestStream":
        return self

    def __next__(self) -> str:
        if self._index == self._before_line_index:
            self._mutate()
        if self._index >= len(self._lines):
            raise StopIteration
        line = self._lines[self._index]
        self._index += 1
        return line


def test_session_serve_uses_in_memory_cache_after_first_request(
    tmp_path: Path, monkeypatch
) -> None:
    project = _build_project(tmp_path / "project", "payments", "create_invoice")
    session_id = session_store.open_session(str(project)).session_id

    original_get_session = session_store.get_session
    calls: list[tuple[str, str]] = []

    def tracking_get_session(session_id: str, path: str = ".") -> dict[str, object]:
        calls.append((session_id, path))
        return original_get_session(session_id, path)

    monkeypatch.setattr(session_store, "get_session", tracking_get_session)

    stdout = StringIO()
    served = session_store.serve_session_stream(
        session_id,
        str(project),
        input_stream=StringIO(
            "\n".join([
                json.dumps({"command": "repo_map"}),
                json.dumps({"command": "repo_map"}),
            ])
            + "\n"
        ),
        output_stream=stdout,
    )

    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert served == 2
    assert len(calls) == 1
    assert responses[0]["files"] == responses[1]["files"]


def test_session_serve_stats_reports_cache_size_uptime_and_request_count(tmp_path: Path) -> None:
    project = _build_project(tmp_path / "project", "payments", "create_invoice")
    session_id = session_store.open_session(str(project)).session_id

    stdout = StringIO()
    session_store.serve_session_stream(
        session_id,
        str(project),
        input_stream=StringIO(
            "\n".join([
                json.dumps({"command": "repo_map"}),
                json.dumps({"command": "stats"}),
            ])
            + "\n"
        ),
        output_stream=stdout,
    )

    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    stats = responses[1]
    assert stats["ok"] is True
    assert stats["version"] == 1
    assert stats["session_count"] == 1
    assert stats["cache_size_bytes"] > 0
    assert stats["request_count"] == 2
    assert isinstance(stats["uptime_seconds"], float)
    assert stats["uptime_seconds"] >= 0


def test_session_serve_can_hold_sessions_from_multiple_roots(tmp_path: Path) -> None:
    project_a = _build_project(tmp_path / "project_a", "payments", "create_invoice")
    project_b = _build_project(tmp_path / "project_b", "billing", "settle_invoice")

    session_a = session_store.open_session(str(project_a)).session_id
    session_b = session_store.open_session(str(project_b)).session_id

    stdout = StringIO()
    served = session_store.serve_session_stream(
        session_a,
        str(project_a),
        input_stream=StringIO(
            "\n".join([
                json.dumps({"command": "defs", "symbol": "create_invoice"}),
                json.dumps({
                    "session_id": session_b,
                    "path": str(project_b),
                    "command": "defs",
                    "symbol": "settle_invoice",
                }),
                json.dumps({"command": "stats"}),
            ])
            + "\n"
        ),
        output_stream=stdout,
    )

    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert served == 3
    assert responses[0]["session_id"] == session_a
    assert responses[0]["definitions"][0]["name"] == "create_invoice"
    assert responses[1]["session_id"] == session_b
    assert responses[1]["definitions"][0]["name"] == "settle_invoice"
    assert responses[2]["session_count"] == 2


def test_session_serve_refresh_updates_in_memory_cache_entry(tmp_path: Path, monkeypatch) -> None:
    project = _build_project(tmp_path / "project", "payments", "create_invoice")
    module_path = project / "src" / "payments.py"
    session_id = session_store.open_session(str(project)).session_id

    original_get_session = session_store.get_session
    calls: list[tuple[str, str]] = []

    def tracking_get_session(session_id: str, path: str = ".") -> dict[str, object]:
        calls.append((session_id, path))
        return original_get_session(session_id, path)

    monkeypatch.setattr(session_store, "get_session", tracking_get_session)

    stdout = StringIO()
    served = session_store.serve_session_stream(
        session_id,
        str(project),
        refresh_on_stale=True,
        input_stream=_MutatingRequestStream(
            [
                json.dumps({"command": "defs", "symbol": "create_invoice"}) + "\n",
                json.dumps({"command": "defs", "symbol": "settle_invoice"}) + "\n",
                json.dumps({"command": "defs", "symbol": "settle_invoice"}) + "\n",
            ],
            before_line_index=1,
            mutate=lambda: _write_python_file(
                module_path,
                "def create_invoice():\n    return 'create_invoice'\n\n"
                "def settle_invoice():\n    return create_invoice()\n",
            ),
        ),
        output_stream=stdout,
    )

    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert served == 3
    assert responses[1]["definitions"][0]["name"] == "settle_invoice"
    assert responses[2]["definitions"][0]["name"] == "settle_invoice"
    assert len(calls) == 2


def test_session_daemon_returns_invalid_request_for_malformed_json(tmp_path: Path) -> None:
    project = _build_project(tmp_path / "project", "payments", "create_invoice")
    server = _ThreadedSessionDaemon(project, ("127.0.0.1", 0))
    try:
        handler = _SessionDaemonHandler.__new__(_SessionDaemonHandler)
        handler.server = server
        handler.rfile = BytesIO(b'{"command":"context"\n')
        handler.wfile = BytesIO()

        _SessionDaemonHandler.handle(handler)

        payload = json.loads(handler.wfile.getvalue().decode("utf-8").strip())
        assert payload["error"]["code"] == "invalid_request"
    finally:
        server.server_close()


def test_session_daemon_retries_initial_missing_session_payload(tmp_path: Path, monkeypatch) -> None:
    project = _build_project(tmp_path / "project", "payments", "create_invoice")
    server = _ThreadedSessionDaemon(project, ("127.0.0.1", 0))
    calls = {"count": 0}
    session_payload = {
        "repo_map": {
            "path": str(project),
            "files": [str((project / "src" / "payments.py").resolve())],
            "symbols": [],
        }
    }

    def _flaky_load_with_status(session_id: str, path: str) -> tuple[dict[str, object], str]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise FileNotFoundError(f"Session not found: {session_id}")
        return session_payload, "miss"

    monkeypatch.setattr(server.payload_cache, "load_with_status", _flaky_load_with_status)
    monkeypatch.setattr(
        session_daemon_module,
        "serve_session_request",
        lambda session_id, request, path, payload=None: {
            "version": 1,
            "session_id": session_id,
            "routing_reason": "session-context",
            "files": payload["repo_map"]["files"],
        },
    )
    monkeypatch.setattr(session_daemon_module.time, "sleep", lambda _seconds: None)

    try:
        opened = session_store.open_session(str(project))
        handler = _SessionDaemonHandler.__new__(_SessionDaemonHandler)
        handler.server = server
        handler.rfile = BytesIO(
            (
                json.dumps(
                    {
                        "command": "context",
                        "session_id": opened.session_id,
                        "path": str(project),
                        "query": "invoice",
                    }
                )
                + "\n"
            ).encode("utf-8")
        )
        handler.wfile = BytesIO()

        _SessionDaemonHandler.handle(handler)

        payload = json.loads(handler.wfile.getvalue().decode("utf-8").strip())
        assert payload["session_id"] == opened.session_id
        assert payload["routing_reason"] == "session-context"
        assert calls["count"] == 2
    finally:
        server.server_close()
