import json
import threading
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app


def test_top_level_edit_plan_does_not_daemon_route_file_targets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import main as cli_main
    from tensor_grep.cli import session_daemon

    module_path = tmp_path / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(
        session_daemon,
        "request_running_session_daemon",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("file target should not daemon-route")
        ),
    )

    payload = cli_main._maybe_edit_plan_via_running_daemon(
        path=str(module_path),
        query="create invoice",
        max_files=3,
        max_repo_files=512,
        max_sources=None,
        max_tokens=None,
        max_symbols=5,
        provider="native",
        profile=False,
    )

    assert payload is None


def test_session_edit_plan_can_use_daemon(tmp_path: Path) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    result = runner.invoke(
        app,
        [
            "session",
            "edit-plan",
            opened["session_id"],
            str(project),
            "--query",
            "create invoice",
            "--daemon",
            "--json",
        ],
    )

    try:
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["session_id"] == opened["session_id"]
        assert payload["routing_reason"] == "session-context-edit-plan"
        assert payload["files"] == [str(module_path.resolve())]
        assert payload["serve_cache"]["status"] in {"hit", "miss"}
        assert payload["session_timing"]["cache_status"] == payload["serve_cache"]["status"]
        assert payload["session_timing"]["load_session_seconds"] >= 0
        assert payload["session_timing"]["build_edit_plan_seconds"] >= 0
        assert payload["session_timing"]["total_seconds"] >= 0
    finally:
        session_daemon.stop_session_daemon(str(project))


def test_session_edit_plan_daemon_accepts_relative_path_from_project_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    monkeypatch.chdir(project)

    result = runner.invoke(
        app,
        [
            "session",
            "edit-plan",
            opened["session_id"],
            ".",
            "--query",
            "create invoice",
            "--daemon",
            "--json",
        ],
    )

    try:
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["session_id"] == opened["session_id"]
        assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    finally:
        session_daemon.stop_session_daemon(".")


def test_session_daemon_edit_plan_reuses_identical_response_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    calls = 0

    def fake_serve_session_request(
        session_id: str,
        request: dict[str, object],
        path: str,
        *,
        payload: dict[str, object],
        deadline_monotonic: float | None = None,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "session_id": session_id,
            "routing_reason": "session-context-edit-plan",
            "files": [str(module_path.resolve())],
            "call_count": calls,
        }

    monkeypatch.setattr(session_daemon, "serve_session_request", fake_serve_session_request)

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        request = {
            "command": "context_edit_plan",
            "session_id": opened["session_id"],
            "path": str(project),
            "query": "create invoice",
            "max_repo_files": 2,
        }

        first = session_daemon._daemon_request(host, port, request, token="tok")
        second = session_daemon._daemon_request(host, port, request, token="tok")
        stats = session_daemon._daemon_request(host, port, {"command": "stats"}, token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert calls == 1
    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["daemon_response_cache"]["status"] == "hit"
    assert second["call_count"] == 1
    assert second["session_timing"]["response_cache_status"] == "hit"
    assert stats["response_cache_hits"] == 1
    assert stats["response_cache_misses"] == 1
    assert stats["response_cache_entries"] == 1


def test_session_daemon_refresh_on_stale_response_is_cached(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    module_path.write_text("def create_invoice():\n    return 2\n", encoding="utf-8")
    calls = 0

    def fake_serve_session_request(
        session_id: str,
        request: dict[str, object],
        path: str,
        *,
        payload: dict[str, object],
        deadline_monotonic: float | None = None,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "session_id": session_id,
            "routing_reason": "session-context-edit-plan",
            "files": [str(module_path.resolve())],
            "call_count": calls,
        }

    monkeypatch.setattr(session_daemon, "serve_session_request", fake_serve_session_request)

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        request = {
            "command": "context_edit_plan",
            "session_id": opened["session_id"],
            "path": str(project),
            "query": "create invoice",
            "refresh_on_stale": True,
            "max_repo_files": 2,
        }

        first = session_daemon._daemon_request(host, port, request, token="tok")
        second = session_daemon._daemon_request(host, port, request, token="tok")
        stats = session_daemon._daemon_request(host, port, {"command": "stats"}, token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert calls == 1
    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["daemon_response_cache"]["status"] == "hit"
    assert second["call_count"] == 1
    assert stats["response_cache_hits"] == 1
    assert stats["response_cache_entries"] == 1


def test_session_daemon_refresh_on_added_file_response_is_cached(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice():\n    return 1\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    opened = json.loads(
        runner.invoke(
            app,
            ["session", "open", str(project), "--max-repo-files", "10", "--json"],
        ).stdout
    )
    (src_dir / "refunds.py").write_text(
        "def issue_refund():\n    return 2\n",
        encoding="utf-8",
    )

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        request = {
            "command": "context_render",
            "session_id": opened["session_id"],
            "path": str(project),
            "query": "issue refund",
            "refresh_on_stale": True,
            "max_repo_files": 10,
            "render_profile": "llm",
            "optimize_context": True,
        }

        first = session_daemon._daemon_request(host, port, request, token="tok")
        second = session_daemon._daemon_request(host, port, request, token="tok")
        stats = session_daemon._daemon_request(host, port, {"command": "stats"}, token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    serialized_first = json.dumps(first)
    assert first["routing_reason"] == "session-context-render"
    assert first["daemon_response_cache"]["status"] == "miss"
    assert "refunds.py" in serialized_first
    assert "issue_refund" in serialized_first
    assert second["daemon_response_cache"]["status"] == "hit"
    assert stats["response_cache_puts"] >= 1
    assert stats["response_cache_hits"] >= 1
    assert stats["response_cache_entries"] >= 1


def test_session_daemon_edit_plan_repeated_core_payload_is_stable(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        request = {
            "command": "context_edit_plan",
            "session_id": opened["session_id"],
            "path": str(project),
            "query": "create invoice",
            "max_repo_files": 2,
        }

        first = session_daemon._daemon_request(host, port, request, token="tok")
        second = session_daemon._daemon_request(host, port, request, token="tok")
        third = session_daemon._daemon_request(host, port, request, token="tok")
        stats = session_daemon._daemon_request(host, port, {"command": "stats"}, token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    def core(payload: dict[str, object]) -> dict[str, object]:
        return {
            key: value
            for key, value in payload.items()
            if key not in {"serve_cache", "daemon_response_cache", "session_timing"}
        }

    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["daemon_response_cache"]["status"] == "hit"
    assert third["daemon_response_cache"]["status"] == "hit"
    assert core(second) == core(third)
    assert "daemon_response_cache" not in core(second)
    assert stats["response_cache_entries"] == 1


def test_session_daemon_context_render_reuses_identical_response_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    calls = 0

    def fake_serve_session_request(
        session_id: str,
        request: dict[str, object],
        path: str,
        *,
        payload: dict[str, object],
        deadline_monotonic: float | None = None,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "session_id": session_id,
            "routing_reason": "session-context-render",
            "rendered_context": "rendered",
            "files": [str(module_path.resolve())],
            "call_count": calls,
        }

    monkeypatch.setattr(session_daemon, "serve_session_request", fake_serve_session_request)

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        request = {
            "command": "context_render",
            "session_id": opened["session_id"],
            "path": str(project),
            "query": "create invoice",
            "render_profile": "llm",
            "optimize_context": True,
            "max_repo_files": 2,
        }

        first = session_daemon._daemon_request(host, port, request, token="tok")
        second = session_daemon._daemon_request(host, port, request, token="tok")
        stats = session_daemon._daemon_request(host, port, {"command": "stats"}, token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert calls == 1
    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["daemon_response_cache"]["status"] == "hit"
    assert second["call_count"] == 1
    assert second["session_timing"]["response_cache_status"] == "hit"
    assert stats["response_cache_hits"] == 1
    assert stats["response_cache_misses"] == 1


def test_session_daemon_context_render_without_session_uses_implicit_cached_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    calls = 0

    def fake_serve_session_request(
        session_id: str,
        request: dict[str, object],
        path: str,
        *,
        payload: dict[str, object],
        deadline_monotonic: float | None = None,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "session_id": session_id,
            "routing_reason": "session-context-render",
            "rendered_context": "rendered",
            "files": [str(module_path.resolve())],
            "call_count": calls,
            "serve_response_cache": {"status": "miss"},
        }

    monkeypatch.setattr(session_daemon, "serve_session_request", fake_serve_session_request)

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        request = {
            "command": "context_render",
            "path": str(project),
            "query": "create invoice",
            "render_profile": "llm",
            "optimize_context": True,
            "max_repo_files": 2,
        }

        first = session_daemon._daemon_request(host, port, request, token="tok")
        second = session_daemon._daemon_request(host, port, request, token="tok")
        stats = session_daemon._daemon_request(host, port, {"command": "stats"}, token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert calls == 1
    assert first["session_id"]
    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["daemon_response_cache"]["status"] == "hit"
    assert second["session_id"] == first["session_id"]
    assert second["call_count"] == 1
    assert "serve_response_cache" not in second
    assert stats["response_cache_hits"] == 1
    assert stats["response_cache_misses"] == 1
    assert stats["response_cache_puts"] == 1
    assert stats["response_cache_entries"] == 1


def test_session_daemon_implicit_sessions_are_keyed_by_repo_scan_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(4):
        (src_dir / f"mod_{index}.py").write_text(
            f"def create_invoice_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    def fake_serve_session_request(
        session_id: str,
        request: dict[str, object],
        path: str,
        *,
        payload: dict[str, object],
        deadline_monotonic: float | None = None,
    ) -> dict[str, object]:
        repo_map = payload.get("repo_map") or {}
        files = repo_map.get("files") if isinstance(repo_map, dict) else []
        return {
            "session_id": session_id,
            "routing_reason": "session-context-render",
            "rendered_context": "rendered",
            "file_count": len(files) if isinstance(files, list) else 0,
            "scan_limit": payload.get("scan_limit"),
        }

    monkeypatch.setattr(session_daemon, "serve_session_request", fake_serve_session_request)

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        first = session_daemon._daemon_request(
            host,
            port,
            {
                "command": "context_render",
                "path": str(project),
                "query": "create invoice",
                "max_repo_files": 1,
            },
            token="tok",
        )
        second = session_daemon._daemon_request(
            host,
            port,
            {
                "command": "context_render",
                "path": str(project),
                "query": "create invoice",
                "max_repo_files": 10,
            },
            token="tok",
        )
        stats = session_daemon._daemon_request(host, port, {"command": "stats"}, token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert first["session_id"] != second["session_id"]
    assert first["scan_limit"]["max_repo_files"] == 1
    assert second["scan_limit"]["max_repo_files"] == 10
    assert second["file_count"] > first["file_count"]
    assert stats["response_cache_misses"] == 2
    assert stats["response_cache_entries"] == 2


def test_session_daemon_implicit_session_lru_eviction_cleans_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(session_daemon, "_DAEMON_IMPLICIT_SESSION_MAX_ENTRIES", 1)

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        first = session_daemon._daemon_request(
            host,
            port,
            {
                "command": "context_render",
                "path": str(project),
                "query": "create invoice",
                "max_repo_files": 1,
            },
            token="tok",
        )
        second = session_daemon._daemon_request(
            host,
            port,
            {
                "command": "context_render",
                "path": str(project),
                "query": "create invoice",
                "max_repo_files": 2,
            },
            token="tok",
        )
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert first["session_id"] != second["session_id"]
    assert list(server.implicit_session_ids.values()) == [second["session_id"]]
    first_payload_path = project / ".tensor-grep" / "sessions" / f"{first['session_id']}.json"
    assert not first_payload_path.exists()


def test_session_daemon_edit_plan_without_session_uses_implicit_cached_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    calls = 0

    def fake_serve_session_request(
        session_id: str,
        request: dict[str, object],
        path: str,
        *,
        payload: dict[str, object],
        deadline_monotonic: float | None = None,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "session_id": session_id,
            "routing_reason": "session-context-edit-plan",
            "files": [str(module_path.resolve())],
            "tests": [],
            "symbols": [],
            "call_count": calls,
        }

    monkeypatch.setattr(session_daemon, "serve_session_request", fake_serve_session_request)

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        request = {
            "command": "context_edit_plan",
            "path": str(project),
            "query": "create invoice",
            "max_repo_files": 2,
        }

        first = session_daemon._daemon_request(host, port, request, token="tok")
        second = session_daemon._daemon_request(host, port, request, token="tok")
        stats = session_daemon._daemon_request(host, port, {"command": "stats"}, token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert calls == 1
    assert first["session_id"]
    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["daemon_response_cache"]["status"] == "hit"
    assert second["session_id"] == first["session_id"]
    assert second["call_count"] == 1
    assert stats["response_cache_hits"] == 1
    assert stats["response_cache_misses"] == 1
    assert stats["response_cache_puts"] == 1
    assert stats["response_cache_entries"] == 1


def test_session_daemon_edit_plan_cache_checks_stale_files_before_hit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    calls = 0

    def fake_serve_session_request(
        session_id: str,
        request: dict[str, object],
        path: str,
        *,
        payload: dict[str, object],
        deadline_monotonic: float | None = None,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "session_id": session_id,
            "routing_reason": "session-context-edit-plan",
            "files": [str(module_path.resolve())],
            "call_count": calls,
        }

    monkeypatch.setattr(session_daemon, "serve_session_request", fake_serve_session_request)

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        request = {
            "command": "context_edit_plan",
            "session_id": opened["session_id"],
            "path": str(project),
            "query": "create invoice",
            "max_repo_files": 2,
        }

        first = session_daemon._daemon_request(host, port, request, token="tok")
        module_path.write_text("def create_invoice():\n    return 100\n", encoding="utf-8")
        second = session_daemon._daemon_request(host, port, request, token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert calls == 1
    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["error"]["code"] == "invalid_request"
    assert "cached session files changed" in second["error"]["message"]


def test_session_daemon_capped_refresh_checks_modified_files_before_hit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")
    (src_dir / "shipping.py").write_text("def ship_invoice():\n    return 2\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(
        runner.invoke(
            app,
            ["session", "open", str(project), "--max-repo-files", "1", "--json"],
        ).stdout
    )
    assert opened["scan_limit"]["possibly_truncated"] is True

    calls = 0

    def fake_serve_session_request(
        session_id: str,
        request: dict[str, object],
        path: str,
        *,
        payload: dict[str, object],
        deadline_monotonic: float | None = None,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "session_id": session_id,
            "routing_reason": "session-context-render",
            "files": [str(module_path.resolve())],
            "call_count": calls,
            "scan_limit": payload.get("scan_limit"),
        }

    monkeypatch.setattr(session_daemon, "serve_session_request", fake_serve_session_request)

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        request = {
            "command": "context_render",
            "session_id": opened["session_id"],
            "path": str(project),
            "query": "create invoice",
            "refresh_on_stale": True,
            "max_repo_files": 1,
        }

        first = session_daemon._daemon_request(host, port, request, token="tok")
        module_path.write_text("def create_invoice():\n    return 100\n", encoding="utf-8")
        second = session_daemon._daemon_request(host, port, request, token="tok")
        stats = session_daemon._daemon_request(host, port, {"command": "stats"}, token="tok")
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()

    assert first["daemon_response_cache"]["status"] == "miss"
    assert second["daemon_response_cache"]["status"] == "miss"
    assert second["call_count"] == 2
    assert second["scan_limit"]["possibly_truncated"] is True
    assert stats["response_cache_hits"] == 0
    assert stats["response_cache_puts"] == 2
    assert stats["response_cache_entries"] == 2


def test_session_blast_radius_reuses_cached_repo_map(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    result = runner.invoke(
        app,
        [
            "session",
            "blast-radius",
            opened["session_id"],
            str(project),
            "--symbol",
            "create_invoice",
            "--max-depth",
            "1",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["session_id"] == opened["session_id"]
    assert payload["routing_reason"] == "session-blast-radius"
    assert payload["max_depth"] == 1
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert any(caller["file"] == str(service_path.resolve()) for caller in payload["callers"])
    assert payload["tests"][0] == str(test_path.resolve())
    assert "Depth 0:" in payload["rendered_caller_tree"]


def test_session_blast_radius_render_reuses_cached_repo_map(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    result = runner.invoke(
        app,
        [
            "session",
            "blast-radius-render",
            opened["session_id"],
            str(project),
            "--symbol",
            "create_invoice",
            "--max-depth",
            "1",
            "--max-render-chars",
            "400",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["session_id"] == opened["session_id"]
    assert payload["routing_reason"] == "session-blast-radius-render"
    assert payload["symbol"] == "create_invoice"
    assert payload["sources"][0]["name"] == "create_invoice"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    assert "create_invoice" in payload["rendered_context"]


def test_session_open_fsyncs_payload_before_atomic_replace(tmp_path: Path, monkeypatch) -> None:
    """M6: session_store's atomic writer must fsync the payload data before the atomic
    rename, mirroring checkpoint_store's fix (audit I5) -- otherwise a power-loss between
    the write and the page-cache flush can publish a truncated index.json/session payload.

    task #211: the write/fsync/rename mechanics now live in the shared
    ``_index_lock.atomic_write_bytes`` helper (``session_store._write_json_atomic`` is a thin
    delegator as of the uniform-onofollow-211 hardening pass) -- patch the fsync/replace call
    sites there, where the real work now happens, instead of on ``session_store`` directly.
    """
    from tensor_grep.cli import _index_lock, session_store

    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("value = 1\n", encoding="utf-8")

    call_order: list[str] = []
    real_fsync = _index_lock.os.fsync
    real_replace = _index_lock.replace_with_retry

    def spy_fsync(fd):
        call_order.append("fsync")
        return real_fsync(fd)

    def spy_replace(src, dst, **kwargs):
        call_order.append("replace")
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(_index_lock.os, "fsync", spy_fsync)
    monkeypatch.setattr(_index_lock, "replace_with_retry", spy_replace)

    session_store.open_session(str(project))

    assert "fsync" in call_order
    assert "replace" in call_order
    # The data must be durably flushed BEFORE the rename publishes it under the real name.
    assert call_order.index("fsync") < call_order.index("replace")


def test_prune_session_records_prunes_by_created_at_not_insert_position(
    tmp_path: Path,
) -> None:
    """M8: created_at is stamped BEFORE the caller acquires index_lock, so concurrent
    writers can insert out of created_at order. Retention pruning must sort by created_at
    (newest first) before slicing, not trust list position -- otherwise it can drop a
    genuinely NEWER session and keep an older one."""
    from tensor_grep.cli import session_store

    root = tmp_path / "project"
    root.mkdir()

    # Deliberately out-of-order: position 0 is the OLDEST record (as if its writer won the
    # lock-acquisition race despite being stamped first), positions 1/2 are progressively
    # newer -- mirroring a concurrent-insert race where lock-arrival order != created_at
    # order.
    records = [
        session_store.SessionRecord(
            version=1,
            session_id="oldest",
            root=str(root),
            created_at="2026-01-01T00:00:00+00:00",
            file_count=1,
            symbol_count=1,
        ),
        session_store.SessionRecord(
            version=1,
            session_id="newest",
            root=str(root),
            created_at="2026-01-03T00:00:00+00:00",
            file_count=1,
            symbol_count=1,
        ),
        session_store.SessionRecord(
            version=1,
            session_id="middle",
            root=str(root),
            created_at="2026-01-02T00:00:00+00:00",
            file_count=1,
            symbol_count=1,
        ),
    ]

    retained = session_store._prune_session_records(root, records, max_records=2)

    retained_ids = {record.session_id for record in retained}
    # Position-based (buggy) pruning would keep {"oldest", "newest"} (records[:2]).
    # created_at-based (fixed) pruning must keep the two NEWEST: {"newest", "middle"}.
    assert retained_ids == {"newest", "middle"}
    assert "oldest" not in retained_ids
