import json
from io import StringIO
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app


def test_session_refresh_updates_cached_repo_map_after_file_change(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    second_path = src_dir / "billing.py"
    second_path.write_text("def issue_invoice():\n    return 2\n", encoding="utf-8")

    refresh_result = runner.invoke(
        app,
        ["session", "refresh", opened["session_id"], str(project), "--json"],
    )

    assert refresh_result.exit_code == 0
    refreshed = json.loads(refresh_result.stdout)
    assert refreshed["session_id"] == opened["session_id"]
    assert refreshed["file_count"] == 2
    assert refreshed["symbol_count"] == 2

    show_result = runner.invoke(
        app, ["session", "show", opened["session_id"], str(project), "--json"]
    )
    shown = json.loads(show_result.stdout)
    assert str(second_path.resolve()) in shown["repo_map"]["files"]


def test_session_serve_can_auto_refresh_stale_session(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    module_path.write_text(
        "def create_invoice():\n    return 2\n\n"
        "def settle_invoice():\n    return create_invoice()\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["session", "serve", opened["session_id"], str(project), "--refresh-on-stale"],
        input='{"command":"defs","symbol":"settle_invoice"}\n',
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["session_id"] == opened["session_id"]
    assert payload["routing_reason"] == "session-defs"
    assert payload["definitions"][0]["name"] == "settle_invoice"


def test_session_context_can_auto_refresh_stale_session(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    module_path.write_text(
        "def create_invoice():\n    return 1\n\n"
        "def settle_invoice():\n    return create_invoice()\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "session",
            "context",
            opened["session_id"],
            str(project),
            "--query",
            "settle invoice",
            "--refresh-on-stale",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["session_id"] == opened["session_id"]
    assert payload["routing_reason"] == "session-context"
    assert any(symbol["name"] == "settle_invoice" for symbol in payload["symbols"])


def test_session_context_does_not_go_stale_from_non_context_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")
    (project / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (project / "debug.log").write_text("create invoice noise\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    result = runner.invoke(
        app,
        [
            "session",
            "context",
            opened["session_id"],
            str(project),
            "--query",
            "create invoice",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["files"][0] == str(module_path.resolve())


def test_session_refresh_on_stale_recovers_after_context_file_change(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")
    (project / ".gitignore").write_text("*.log\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    module_path.write_text(
        "def create_invoice():\n    return 1\n\ndef issue_refund():\n    return create_invoice()\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "session",
            "context",
            opened["session_id"],
            str(project),
            "--query",
            "issue refund",
            "--refresh-on-stale",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["session_id"] == opened["session_id"]
    assert any(symbol["name"] == "issue_refund" for symbol in payload["symbols"])


def test_session_context_render_omits_validation_commands_without_runner_evidence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "sample.py"
    module_path.write_text("def add(x):\n    return x + 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    result = runner.invoke(
        app,
        [
            "session",
            "context-render",
            opened["session_id"],
            str(project),
            "--query",
            "add",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["files"] == [str(module_path.resolve())]
    assert payload["edit_plan_seed"]["validation_commands"] == []
    assert payload["edit_plan_seed"]["validation_plan"] == []
    assert payload["navigation_pack"]["validation_commands"] == []


def test_session_context_render_json_defaults_to_llm_profile(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    result = runner.invoke(
        app,
        [
            "session",
            "context-render",
            opened["session_id"],
            str(project),
            "--query",
            "invoice",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["render_profile"] == "llm"
    assert payload["optimize_context"] is True
    assert payload["context_payload_profile"] == "llm-compact"


def test_session_context_render_applies_max_repo_files_to_cached_map(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(3):
        (src_dir / f"module_{index}.py").write_text(
            f"def create_invoice_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    session_id = session_store.open_session(str(project)).session_id
    seen: dict[str, int] = {}

    def fake_build_context_render_from_map(
        repo_map: dict[str, object],
        query: str,
        **kwargs: object,
    ) -> dict[str, object]:
        files = list(repo_map["files"])  # type: ignore[index]
        seen["file_count"] = len(files)
        return {
            "version": 1,
            "schema_version": 1,
            "routing_reason": "context-render-test",
            "query": query,
            "path": str(project.resolve()),
            "files": files,
            "tests": [],
            "rendered_context": "",
            "render_profile": kwargs["render_profile"],
            "optimize_context": kwargs["optimize_context"],
        }

    monkeypatch.setattr(
        session_store,
        "build_context_render_from_map",
        fake_build_context_render_from_map,
    )

    payload = session_store.session_context_render(
        session_id,
        "invoice",
        str(project),
        max_repo_files=1,
        render_profile="llm",
        optimize_context=True,
    )

    assert seen["file_count"] == 1
    assert len(payload["files"]) == 1


def test_session_serve_reports_cache_stats(tmp_path: Path) -> None:
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    opened = json.loads(CliRunner().invoke(app, ["session", "open", str(project), "--json"]).stdout)
    stdin = StringIO(
        "\n".join([
            json.dumps({"command": "repo_map"}),
            json.dumps({"command": "repo_map"}),
            json.dumps({"command": "stats"}),
        ])
        + "\n"
    )
    stdout = StringIO()

    served = session_store.serve_session_stream(
        opened["session_id"],
        str(project),
        input_stream=stdin,
        output_stream=stdout,
    )

    assert served == 3
    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    stats = responses[-1]
    assert stats["ok"] is True
    assert stats["request_count"] == 3
    assert stats["cache_hits"] >= 1
    assert stats["cache_misses"] >= 1


def test_session_serve_response_cache_obeys_byte_cap() -> None:
    from tensor_grep.cli.session_store import _SessionServeResponseCache

    cache = _SessionServeResponseCache(max_entries=8, max_size_bytes=60)
    cache.put(("one",), {"payload": "a" * 20})
    cache.put(("two",), {"payload": "b" * 20})

    assert cache.entry_count == 1
    assert cache.size_bytes <= cache.max_size_bytes
    assert cache.get(("one",)) is None
    assert cache.get(("two",)) == {"payload": "b" * 20}

    cache.put(("oversized",), {"payload": "c" * 80})
    assert cache.get(("oversized",)) is None
    assert cache.oversized_skips == 1

    cache.put(("stable",), {"payload": "ok"})
    cache.put(("stable",), {"payload": "d" * 80})
    assert cache.get(("stable",)) == {"payload": "ok"}
    assert cache.oversized_skips == 2


def test_session_response_cache_invalid_byte_env_uses_default(monkeypatch) -> None:
    from tensor_grep.cli.session_store import (
        _DEFAULT_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES,
        _SessionServeResponseCache,
    )

    monkeypatch.setenv("TENSOR_GREP_SESSION_RESPONSE_CACHE_MAX_BYTES", "not-an-int")

    cache = _SessionServeResponseCache()

    assert cache.max_size_bytes == _DEFAULT_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES


def test_session_daemon_response_cache_obeys_byte_cap() -> None:
    from tensor_grep.cli.session_daemon import _SessionResponseCache

    cache = _SessionResponseCache(max_entries=8, max_size_bytes=60)
    cache.put(("one",), {"payload": "a" * 20})
    cache.put(("two",), {"payload": "b" * 20})

    assert cache.entry_count == 1
    assert cache.size_bytes <= cache.max_size_bytes
    assert cache.get(("one",)) is None
    assert cache.get(("two",)) == {"payload": "b" * 20}

    cache.put(("oversized",), {"payload": "c" * 80})
    assert cache.get(("oversized",)) is None
    assert cache.oversized_skips == 1

    cache.put(("stable",), {"payload": "ok"})
    cache.put(("stable",), {"payload": "d" * 80})
    assert cache.get(("stable",)) == {"payload": "ok"}
    assert cache.oversized_skips == 2


def test_session_daemon_payload_fingerprint_uses_repo_map_counts() -> None:
    from tensor_grep.cli.session_daemon import _session_payload_fingerprint

    base_payload = {
        "root": "repo",
        "created_at": "created",
        "refreshed_at": "refreshed",
        "repo_map": {"files": ["a.py"], "symbols": ["create_invoice"]},
    }
    changed_payload = {
        **base_payload,
        "repo_map": {"files": ["a.py", "b.py"], "symbols": ["create_invoice"]},
    }

    assert _session_payload_fingerprint(base_payload) != _session_payload_fingerprint(
        changed_payload
    )


def test_session_daemon_payload_retry_handles_partial_json(monkeypatch) -> None:
    from tensor_grep.cli import session_daemon

    class FlakyCache:
        calls = 0

        def load_with_status(self, _session_id: str, _path: str):
            self.calls += 1
            if self.calls == 1:
                raise json.JSONDecodeError("partial", "{", 1)
            return {"ok": True}, "miss"

    monkeypatch.setattr(session_daemon, "_DAEMON_SESSION_LOOKUP_RETRY_SECONDS", 0.5)
    cache = FlakyCache()

    payload, status = session_daemon._load_payload_with_status_retry(cache, "s", ".")

    assert payload == {"ok": True}
    assert status == "miss"
    assert cache.calls == 2


def test_session_serve_context_render_reuses_identical_response_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice():\n    return 1\n",
        encoding="utf-8",
    )
    opened = json.loads(CliRunner().invoke(app, ["session", "open", str(project), "--json"]).stdout)
    build_calls = {"count": 0}

    def fake_build_context_render_from_map(
        repo_map: dict[str, object],
        query: str,
        **kwargs: object,
    ) -> dict[str, object]:
        build_calls["count"] += 1
        return {
            "version": 1,
            "schema_version": 1,
            "routing_reason": "context-render-test",
            "query": query,
            "path": str(project.resolve()),
            "files": list(repo_map.get("files", [])),
            "tests": [],
            "rendered_context": "",
            "render_profile": kwargs.get("render_profile", "llm"),
            "optimize_context": kwargs.get("optimize_context", True),
        }

    monkeypatch.setattr(
        session_store,
        "build_context_render_from_map",
        fake_build_context_render_from_map,
    )

    request = {
        "command": "context_render",
        "query": "invoice",
        "render_profile": "llm",
        "optimize_context": True,
    }
    stdin = StringIO(
        "\n".join([json.dumps(request), json.dumps(request), json.dumps({"command": "stats"})])
        + "\n"
    )
    stdout = StringIO()

    session_store.serve_session_stream(
        opened["session_id"],
        str(project),
        input_stream=stdin,
        output_stream=stdout,
    )

    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert build_calls["count"] == 1
    assert responses[0]["serve_response_cache"]["status"] == "miss"
    assert responses[1]["serve_response_cache"]["status"] == "hit"
    assert responses[2]["response_cache_hits"] == 1
    assert responses[2]["response_cache_puts"] == 1


def test_session_serve_reports_health_without_failing_on_stale_cache(tmp_path: Path) -> None:
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    opened = json.loads(CliRunner().invoke(app, ["session", "open", str(project), "--json"]).stdout)
    module_path.write_text("def create_invoice():\n    return 2\n", encoding="utf-8")

    stdin = StringIO(json.dumps({"command": "health"}) + "\n")
    stdout = StringIO()
    session_store.serve_session_stream(
        opened["session_id"],
        str(project),
        input_stream=stdin,
        output_stream=stdout,
    )

    payload = json.loads(stdout.getvalue().strip())
    assert payload["session_id"] == opened["session_id"]
    assert payload["ok"] is False
    assert payload["stale"] is True
    assert str(module_path.resolve()) in payload["changeset"]["modified"]


def test_session_serve_reports_multi_root_stats_and_cache_provenance(tmp_path: Path) -> None:
    from tensor_grep.cli import session_store

    first_project = tmp_path / "project_one"
    first_src = first_project / "src"
    first_src.mkdir(parents=True)
    (first_src / "payments.py").write_text(
        "def create_invoice():\n    return 1\n", encoding="utf-8"
    )

    second_project = tmp_path / "project_two"
    second_src = second_project / "src"
    second_src.mkdir(parents=True)
    (second_src / "billing.py").write_text("def issue_invoice():\n    return 2\n", encoding="utf-8")

    runner = CliRunner()
    first_opened = json.loads(
        runner.invoke(app, ["session", "open", str(first_project), "--json"]).stdout
    )
    second_opened = json.loads(
        runner.invoke(app, ["session", "open", str(second_project), "--json"]).stdout
    )

    stdin = StringIO(
        "\n".join([
            json.dumps({"command": "repo_map"}),
            json.dumps({"command": "repo_map"}),
            json.dumps({
                "command": "repo_map",
                "session_id": second_opened["session_id"],
                "path": str(second_project),
            }),
            json.dumps({"command": "stats"}),
        ])
        + "\n"
    )
    stdout = StringIO()

    session_store.serve_session_stream(
        first_opened["session_id"],
        str(first_project),
        input_stream=stdin,
        output_stream=stdout,
    )

    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    first_repo_map = responses[0]
    second_repo_map = responses[1]
    other_root_repo_map = responses[2]
    stats = responses[3]

    assert first_repo_map["serve_cache"]["status"] == "miss"
    assert second_repo_map["serve_cache"]["status"] == "hit"
    assert other_root_repo_map["serve_cache"]["status"] == "miss"
    assert stats["root_count"] == 2
    assert stats["session_count"] == 2
    assert len(stats["sessions"]) == 2


def test_session_daemon_lifecycle(tmp_path: Path) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()

    stopped = session_daemon.get_session_daemon_status(str(project))
    assert stopped["running"] is False

    started = session_daemon.start_session_daemon(str(project))
    assert started["running"] is True
    assert started["port"] > 0

    status = session_daemon.get_session_daemon_status(str(project))
    assert status["running"] is True
    assert status["port"] == started["port"]
    assert status["response_cache_hits"] == 0
    assert status["response_cache_misses"] == 0
    assert status["response_cache_entries"] == 0
    assert status["response_cache_size_bytes"] == 0
    assert status["response_cache_max_size_bytes"] > 0
    assert status["response_cache_oversized_skips"] == 0
    assert status["response_cache_scope"] == (
        "daemon-routed top-level/session context-render/edit-plan/defs/impact/refs/callers/"
        "blast-radius/orient/agent requests"
    )
    assert status["response_cache_stale_detection"] == "snapshot_content_sha256"
    assert status["response_cache_added_file_detection"] is False
    assert "refresh_on_stale" in status["response_cache_refresh_hint"]

    stopped_payload = session_daemon.stop_session_daemon(str(project))
    assert stopped_payload["stopped"] is True
    assert stopped_payload["running"] is False


def test_session_daemon_status_discovers_child_scope_from_parent_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    parent = tmp_path / "workspace"
    project = parent / "project"
    project.mkdir(parents=True)

    started = session_daemon.start_session_daemon(str(project))
    try:
        monkeypatch.chdir(parent)
        result = CliRunner().invoke(app, ["session", "daemon", "status", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["running"] is True
        assert payload["discovered"] is True
        assert payload["root"] == str(project.resolve())
        assert payload["port"] == started["port"]
        assert "response_cache_size_bytes" in payload
        assert "response_cache_max_size_bytes" in payload
    finally:
        session_daemon.stop_session_daemon(str(project))


def test_session_context_can_use_daemon(tmp_path: Path) -> None:
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
            "context",
            opened["session_id"],
            str(project),
            "--query",
            "invoice",
            "--daemon",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["session_id"] == opened["session_id"]
    assert payload["routing_reason"] == "session-context"
    assert payload["files"] == [str(module_path.resolve())]
    assert "session_timing" not in payload
    session_daemon.stop_session_daemon(str(project))


def test_top_level_context_render_uses_running_daemon_response_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text(
        "def create_invoice():\n    return 1\n",
        encoding="utf-8",
    )
    requests: list[dict[str, object]] = []

    def fake_status(path: str) -> dict[str, object]:
        return {"running": True, "root": str(Path(path).resolve())}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        requests.append(request)
        return {
            "routing_reason": "session-context-render",
            "render_profile": request["render_profile"],
            "rendered_context": "from daemon",
            "daemon_response_cache": {"status": "hit"},
        }

    monkeypatch.setattr(session_daemon, "get_session_daemon_status", fake_status)
    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = CliRunner().invoke(
        app,
        [
            "context-render",
            str(project),
            "create invoice",
            "--render-profile",
            "llm",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["rendered_context"] == "from daemon"
    assert payload["daemon_response_cache"]["status"] == "hit"
    assert requests == [
        {
            "command": "context_render",
            "path": str(project),
            "query": "create invoice",
            "refresh_on_stale": True,
            "max_files": 3,
            "max_sources": 5,
            "max_symbols_per_file": 6,
            "max_render_chars": None,
            "max_tokens": 16000,
            "model": None,
            "optimize_context": False,
            "render_profile": "llm",
            "profile": False,
            "max_repo_files": 2000,
        }
    ]


def test_top_level_edit_plan_uses_running_daemon_response_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    (project / "payments.py").write_text(
        "def create_invoice():\n    return 1\n",
        encoding="utf-8",
    )
    requests: list[dict[str, object]] = []

    def fake_status(path: str) -> dict[str, object]:
        return {"running": True, "root": str(Path(path).resolve())}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        requests.append(request)
        return {
            "routing_reason": "session-context-edit-plan",
            "query": request["query"],
            "files": [str(project / "payments.py")],
            "tests": [],
            "symbols": [],
            "daemon_response_cache": {"status": "hit"},
        }

    monkeypatch.setattr(session_daemon, "get_session_daemon_status", fake_status)
    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    result = CliRunner().invoke(
        app,
        [
            "edit-plan",
            str(project),
            "create invoice",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["routing_reason"] == "session-context-edit-plan"
    assert payload["daemon_response_cache"]["status"] == "hit"
    assert requests == [
        {
            "command": "context_edit_plan",
            "path": str(project),
            "query": "create invoice",
            "refresh_on_stale": True,
            "max_files": 3,
            "max_sources": None,
            "max_tokens": None,
            "max_symbols": 5,
            "profile": False,
            "max_repo_files": 2000,
        }
    ]


def test_top_level_context_render_does_not_daemon_route_file_targets(
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

    payload = cli_main._maybe_context_render_via_running_daemon(
        path=str(module_path),
        query="create invoice",
        max_files=3,
        max_repo_files=512,
        max_sources=5,
        max_symbols_per_file=6,
        max_render_chars=None,
        max_tokens=None,
        model=None,
        optimize_context=False,
        render_profile="llm",
        provider="native",
        profile=False,
    )

    assert payload is None


def test_top_level_context_render_daemon_request_uses_absolute_directory_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import main as cli_main
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        seen["path"] = path
        seen["request"] = request
        return {
            "routing_reason": "session-context-render",
            "render_profile": request["render_profile"],
            "rendered_context": "from daemon",
            "daemon_response_cache": {"status": "hit"},
        }

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    payload = cli_main._maybe_context_render_via_running_daemon(
        path="project",
        query="create invoice",
        max_files=3,
        max_repo_files=512,
        max_sources=5,
        max_symbols_per_file=6,
        max_render_chars=None,
        max_tokens=None,
        model=None,
        optimize_context=False,
        render_profile="llm",
        provider="native",
        profile=False,
    )

    expected_path = str(project.resolve())
    assert payload is not None
    assert seen["path"] == expected_path
    assert seen["request"]["path"] == expected_path


def test_top_level_context_render_routes_relative_directory_to_daemon_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice():\n    return 1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    project_root = str(project.resolve())
    start = runner.invoke(app, ["session", "daemon", "start", "project", "--json"])
    assert start.exit_code == 0, start.output

    try:
        first = runner.invoke(
            app,
            [
                "context-render",
                "project",
                "create invoice",
                "--render-profile",
                "llm",
                "--json",
            ],
        )
        second = runner.invoke(
            app,
            [
                "context-render",
                "project",
                "create invoice",
                "--render-profile",
                "llm",
                "--json",
            ],
        )
        status = runner.invoke(app, ["session", "daemon", "status", "project", "--json"])
    finally:
        session_daemon.stop_session_daemon(project_root)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    status_payload = json.loads(status.stdout)
    assert first_payload["routing_reason"] == "session-context-render"
    assert first_payload["daemon_response_cache"]["status"] == "miss"
    assert second_payload["daemon_response_cache"]["status"] == "hit"
    assert status_payload["response_cache_puts"] >= 1
    assert status_payload["response_cache_hits"] >= 1
    assert status_payload["response_cache_entries"] >= 1


def test_top_level_context_render_capped_implicit_session_populates_daemon_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice():\n    return 1\n",
        encoding="utf-8",
    )
    (src_dir / "shipping.py").write_text(
        "def ship_invoice():\n    return 2\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    project_root = str(project.resolve())
    start = runner.invoke(app, ["session", "daemon", "start", "project", "--json"])
    assert start.exit_code == 0, start.output

    try:
        first = runner.invoke(
            app,
            [
                "context-render",
                "project",
                "create invoice",
                "--render-profile",
                "llm",
                "--max-repo-files",
                "1",
                "--json",
            ],
        )
        second = runner.invoke(
            app,
            [
                "context-render",
                "project",
                "create invoice",
                "--render-profile",
                "llm",
                "--max-repo-files",
                "1",
                "--json",
            ],
        )
        status = runner.invoke(app, ["session", "daemon", "status", "project", "--json"])
    finally:
        session_daemon.stop_session_daemon(project_root)

    # --max-repo-files 1 scan-truncates -> exit 2 per the extended exit-code contract (Cluster B); render still populates the daemon cache first.
    assert first.exit_code == 2, first.output
    assert second.exit_code == 2, second.output
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    status_payload = json.loads(status.stdout)
    assert first_payload["routing_reason"] == "session-context-render"
    assert first_payload["daemon_response_cache"]["status"] == "miss"
    assert second_payload["daemon_response_cache"]["status"] == "hit"
    assert status_payload["response_cache_puts"] >= 1
    assert status_payload["response_cache_hits"] >= 1
    assert status_payload["response_cache_entries"] >= 1


def test_top_level_edit_plan_daemon_request_uses_absolute_directory_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import main as cli_main
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    seen: dict[str, object] = {}

    def fake_request(path: str, request: dict[str, object]) -> dict[str, object]:
        seen["path"] = path
        seen["request"] = request
        return {
            "routing_reason": "session-context-edit-plan",
            "query": request["query"],
            "files": [],
            "tests": [],
            "symbols": [],
            "daemon_response_cache": {"status": "hit"},
        }

    monkeypatch.setattr(session_daemon, "request_running_session_daemon", fake_request)

    payload = cli_main._maybe_edit_plan_via_running_daemon(
        path="project",
        query="create invoice",
        max_files=3,
        max_repo_files=512,
        max_sources=None,
        max_tokens=None,
        max_symbols=5,
        provider="native",
        profile=False,
    )

    expected_path = str(project.resolve())
    assert payload is not None
    assert seen["path"] == expected_path
    assert seen["request"]["path"] == expected_path


def test_top_level_edit_plan_routes_relative_directory_to_daemon_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice():\n    return 1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    project_root = str(project.resolve())
    start = runner.invoke(app, ["session", "daemon", "start", "project", "--json"])
    assert start.exit_code == 0, start.output

    try:
        first = runner.invoke(app, ["edit-plan", "project", "create invoice", "--json"])
        second = runner.invoke(app, ["edit-plan", "project", "create invoice", "--json"])
        status = runner.invoke(app, ["session", "daemon", "status", "project", "--json"])
    finally:
        session_daemon.stop_session_daemon(project_root)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    status_payload = json.loads(status.stdout)
    assert first_payload["routing_reason"] == "session-context-edit-plan"
    assert first_payload["daemon_response_cache"]["status"] == "miss"
    assert second_payload["daemon_response_cache"]["status"] == "hit"
    assert status_payload["response_cache_puts"] >= 1
    assert status_payload["response_cache_hits"] >= 1
    assert status_payload["response_cache_entries"] >= 1


def test_top_level_edit_plan_capped_implicit_session_populates_daemon_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_daemon

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice():\n    return 1\n",
        encoding="utf-8",
    )
    (src_dir / "shipping.py").write_text(
        "def ship_invoice():\n    return 2\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    project_root = str(project.resolve())
    start = runner.invoke(app, ["session", "daemon", "start", "project", "--json"])
    assert start.exit_code == 0, start.output

    try:
        first = runner.invoke(
            app,
            [
                "edit-plan",
                "project",
                "create invoice",
                "--max-repo-files",
                "1",
                "--json",
            ],
        )
        second = runner.invoke(
            app,
            [
                "edit-plan",
                "project",
                "create invoice",
                "--max-repo-files",
                "1",
                "--json",
            ],
        )
        status = runner.invoke(app, ["session", "daemon", "status", "project", "--json"])
    finally:
        session_daemon.stop_session_daemon(project_root)

    # --max-repo-files 1 scan-truncates -> exit 2 per the extended exit-code contract (Cluster B); render still populates the daemon cache first.
    assert first.exit_code == 2, first.output
    assert second.exit_code == 2, second.output
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    status_payload = json.loads(status.stdout)
    assert first_payload["routing_reason"] == "session-context-edit-plan"
    assert first_payload["daemon_response_cache"]["status"] == "miss"
    assert second_payload["daemon_response_cache"]["status"] == "hit"
    assert status_payload["response_cache_puts"] >= 1
    assert status_payload["response_cache_hits"] >= 1
    assert status_payload["response_cache_entries"] >= 1
