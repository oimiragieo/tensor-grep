import json
from io import StringIO
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import repo_map
from tensor_grep.cli.main import app


def test_session_open_show_and_context_reuse_repo_map(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text("from src.payments import create_invoice\n", encoding="utf-8")

    runner = CliRunner()

    open_result = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert open_result.exit_code == 0
    opened = json.loads(open_result.stdout)
    session_id = opened["session_id"]
    assert opened["schema_version"] == opened["version"]
    assert opened["file_count"] == 1
    assert opened["symbol_count"] == 1

    show_result = runner.invoke(app, ["session", "show", session_id, str(project), "--json"])
    assert show_result.exit_code == 0
    shown = json.loads(show_result.stdout)
    assert shown["schema_version"] == shown["version"]
    assert shown["session_id"] == session_id
    assert shown["repo_map"]["files"] == [str(module_path.resolve())]

    context_result = runner.invoke(
        app,
        ["session", "context", session_id, str(project), "--query", "invoice payment", "--json"],
    )
    assert context_result.exit_code == 0
    context = json.loads(context_result.stdout)
    assert context["schema_version"] == context["version"]
    assert context["session_id"] == session_id
    assert context["routing_reason"] == "session-context"
    assert (
        context["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
    assert context["coverage"]["symbol_navigation"] == repo_map._symbol_navigation_descriptor()
    assert context["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert context["files"][0] == str(module_path.resolve())
    assert context["tests"][0] == str(test_path.resolve())


def test_session_context_bounds_pack_by_max_tokens(tmp_path: Path) -> None:
    # dogfood 1.27.0: `session context` was UNBOUNDED (~557KB) while standalone `context` capped.
    # It now bounds the pack by default; a tiny --max-tokens truncates, 0 opts out.
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    for index in range(40):
        (src / f"m{index}.py").write_text(
            f"def f{index}():\n    return {index}\n", encoding="utf-8"
        )
    runner = CliRunner()
    session_id = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)[
        "session_id"
    ]

    capped = json.loads(
        runner.invoke(
            app,
            ["session", "context", session_id, str(project), "f1", "--max-tokens", "200", "--json"],
        ).stdout
    )
    assert capped["token_budget"]["truncated"] is True  # 40 files trimmed to ~200 tokens

    unbounded = json.loads(
        runner.invoke(
            app,
            ["session", "context", session_id, str(project), "f1", "--max-tokens", "0", "--json"],
        ).stdout
    )
    assert unbounded.get("token_budget", {}).get("truncated", False) is False  # 0 = opt-out


def test_session_open_can_cap_initial_repo_map(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(5):
        (src_dir / f"module_{index}.py").write_text(
            f"def function_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    runner = CliRunner()
    open_result = runner.invoke(
        app,
        ["session", "open", str(project), "--max-repo-files", "2", "--json"],
    )

    assert open_result.exit_code == 0, open_result.output
    opened = json.loads(open_result.stdout)
    assert opened["file_count"] == 2
    assert opened["symbol_count"] == 2
    assert opened["scan_limit"] == {
        "max_repo_files": 2,
        "scanned_files": 2,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
        "budget_remediable": True,
    }
    assert opened["build_seconds"] >= 0

    show_result = runner.invoke(
        app,
        ["session", "show", opened["session_id"], str(project), "--json"],
    )

    assert show_result.exit_code == 0, show_result.output
    shown = json.loads(show_result.stdout)
    assert len(shown["repo_map"]["files"]) == 2
    assert shown["scan_limit"] == opened["scan_limit"]

    refresh_result = runner.invoke(
        app,
        ["session", "refresh", opened["session_id"], str(project), "--json"],
    )

    assert refresh_result.exit_code == 0, refresh_result.output
    refreshed = json.loads(refresh_result.stdout)
    assert refreshed["file_count"] == 2
    assert refreshed["scan_limit"]["max_repo_files"] == 2


def test_session_open_defaults_to_agent_safe_repo_map_cap(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(520):
        (src_dir / f"module_{index:03}.py").write_text(
            f"def function_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    runner = CliRunner()
    open_result = runner.invoke(app, ["session", "open", str(project), "--json"])

    assert open_result.exit_code == 0, open_result.output
    opened = json.loads(open_result.stdout)
    assert opened["file_count"] == 512
    assert opened["scan_limit"] == {
        "max_repo_files": 512,
        "scanned_files": 512,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
        "budget_remediable": True,
    }

    show_result = runner.invoke(
        app,
        ["session", "show", opened["session_id"], str(project), "--json"],
    )

    assert show_result.exit_code == 0, show_result.output
    shown = json.loads(show_result.stdout)
    assert len(shown["repo_map"]["files"]) == 512
    assert shown["scan_limit"] == opened["scan_limit"]


def test_session_edit_plan_and_blast_radius_plan_reuse_cached_repo_map(tmp_path: Path) -> None:
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

    edit_plan = runner.invoke(
        app,
        [
            "session",
            "edit-plan",
            opened["session_id"],
            str(project),
            "--query",
            "create invoice",
            "--json",
        ],
    )
    assert edit_plan.exit_code == 0
    edit_payload = json.loads(edit_plan.stdout)
    assert edit_payload["routing_reason"] == "session-context-edit-plan"
    assert edit_payload["session_id"] == opened["session_id"]
    assert edit_payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert "rendered_context" not in edit_payload

    radius_plan = runner.invoke(
        app,
        [
            "session",
            "blast-radius-plan",
            opened["session_id"],
            str(project),
            "--symbol",
            "create_invoice",
            "--max-depth",
            "1",
            "--json",
        ],
    )
    assert radius_plan.exit_code == 0
    radius_payload = json.loads(radius_plan.stdout)
    assert radius_payload["routing_reason"] == "session-blast-radius-plan"
    assert radius_payload["session_id"] == opened["session_id"]
    assert radius_payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    assert "rendered_context" not in radius_payload


def test_session_commands_accept_positional_query_and_symbol_aliases(tmp_path: Path) -> None:
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
    (tests_dir / "test_service.py").write_text(
        "from src.service import build_invoice\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    expected_module_file = str(module_path.resolve())
    expected_service_file = str(service_path.resolve())

    def _has_string(value, expected: str) -> bool:
        if isinstance(value, str):
            return expected in value
        if isinstance(value, dict):
            return any(_has_string(item, expected) for item in value.values())
        if isinstance(value, list):
            return any(_has_string(item, expected) for item in value)
        return False

    query_commands = {
        "context": "session-context",
        "context-render": "session-context-render",
        "edit-plan": "session-context-edit-plan",
    }
    for command, routing_reason in query_commands.items():
        result = runner.invoke(
            app,
            [
                "session",
                command,
                opened["session_id"],
                str(project),
                "create invoice",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["routing_reason"] == routing_reason
        assert payload["query"] == "create invoice"
        assert _has_string(payload, expected_module_file)

    symbol_commands = {
        "blast-radius": "session-blast-radius",
        "blast-radius-render": "session-blast-radius-render",
        "blast-radius-plan": "session-blast-radius-plan",
    }
    for command, routing_reason in symbol_commands.items():
        result = runner.invoke(
            app,
            [
                "session",
                command,
                opened["session_id"],
                str(project),
                "create_invoice",
                "--max-depth",
                "1",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["routing_reason"] == routing_reason
        assert payload["symbol"] == "create_invoice"
        assert _has_string(payload, expected_service_file)


def test_session_edit_plan_auto_corrects_reversed_path_session_order(tmp_path: Path) -> None:
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
            "edit-plan",
            str(project),
            opened["session_id"],
            "create invoice",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "interpreting as `tg session edit-plan <SESSION_ID> <PATH> <QUERY>`" in result.stderr
    payload = json.loads(result.stdout)
    assert payload["session_id"] == opened["session_id"]
    assert payload["routing_reason"] == "session-context-edit-plan"
    assert payload["files"] == [str(module_path.resolve())]


def test_session_commands_warn_for_legacy_query_and_symbol_options(tmp_path: Path) -> None:
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
    (tests_dir / "test_service.py").write_text(
        "from src.service import build_invoice\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    expected_module_file = str(module_path.resolve())
    expected_service_file = str(service_path.resolve())

    for command in ("context", "context-render", "edit-plan"):
        result = runner.invoke(
            app,
            [
                "session",
                command,
                opened["session_id"],
                str(project),
                "--query",
                "create invoice",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"Warning: --query is deprecated for tg session {command}" in result.stderr
        assert expected_module_file in result.stdout.replace("\\\\", "\\")

    for command in ("blast-radius", "blast-radius-render", "blast-radius-plan"):
        result = runner.invoke(
            app,
            [
                "session",
                command,
                opened["session_id"],
                str(project),
                "--symbol",
                "create_invoice",
                "--max-depth",
                "1",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"Warning: --symbol is deprecated for tg session {command}" in result.stderr
        assert expected_service_file in result.stdout.replace("\\\\", "\\")


def test_session_edit_plan_rejects_positional_and_flag_query(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    result = runner.invoke(
        app,
        [
            "session",
            "edit-plan",
            opened["session_id"],
            str(project),
            "create invoice",
            "--query",
            "other",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert "Use either positional QUERY or --query" in result.output


def test_session_edit_plan_does_not_walk_repo_for_default_stale_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    monkeypatch.setattr(
        session_store,
        "_iter_repo_files",
        lambda _root: (_ for _ in ()).throw(
            AssertionError("warm session request should not rescan the repo")
        ),
    )

    edit_plan = runner.invoke(
        app,
        [
            "session",
            "edit-plan",
            opened["session_id"],
            str(project),
            "--query",
            "create invoice",
            "--json",
        ],
    )

    assert edit_plan.exit_code == 0, edit_plan.output
    payload = json.loads(edit_plan.stdout)
    assert payload["routing_reason"] == "session-context-edit-plan"
    assert payload["files"] == [str(module_path.resolve())]


def test_session_edit_plan_does_not_walk_repo_for_validation_discovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    monkeypatch.setattr(
        repo_map,
        "_iter_repo_files",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("warm session edit-plan should not rescan validation files")
        ),
    )

    edit_plan = runner.invoke(
        app,
        [
            "session",
            "edit-plan",
            opened["session_id"],
            str(project),
            "--query",
            "create invoice",
            "--json",
        ],
    )

    assert edit_plan.exit_code == 0, edit_plan.output
    payload = json.loads(edit_plan.stdout)
    assert payload["routing_reason"] == "session-context-edit-plan"
    assert payload["files"] == [str(module_path.resolve())]
    assert payload["validation_commands"] == []
    timing = payload["session_timing"]
    assert timing["cache_status"] == "disk-load"
    assert timing["load_session_seconds"] >= 0
    assert timing["build_edit_plan_seconds"] >= 0
    assert timing["total_seconds"] >= 0


def test_session_edit_plan_applies_repo_map_cap_before_building_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(6):
        (src_dir / f"module_{index}.py").write_text(
            f"def target_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    assert opened["file_count"] == 6

    captured_repo_maps: list[dict] = []

    def fake_build_context_edit_plan_from_map(
        repo_map: dict,
        query: str,
        **_kwargs,
    ) -> dict:
        captured_repo_maps.append(repo_map)
        return {
            "query": query,
            "files": list(repo_map.get("files", [])),
            "tests": list(repo_map.get("tests", [])),
            "symbols": list(repo_map.get("symbols", [])),
            "output_limit": dict(repo_map.get("output_limit", {})),
        }

    monkeypatch.setattr(
        session_store,
        "build_context_edit_plan_from_map",
        fake_build_context_edit_plan_from_map,
    )

    edit_plan = runner.invoke(
        app,
        [
            "session",
            "edit-plan",
            opened["session_id"],
            str(project),
            "--query",
            "target",
            "--max-repo-files",
            "2",
            "--json",
        ],
    )

    assert edit_plan.exit_code == 0, edit_plan.output
    payload = json.loads(edit_plan.stdout)
    assert payload["routing_reason"] == "session-context-edit-plan"
    assert len(payload["files"]) == 2
    assert payload["output_limit"]["max_files"] == 2
    assert payload["output_limit"]["original_files"] == 6
    assert payload["output_limit"]["possibly_truncated"] is True
    assert len(captured_repo_maps) == 1
    assert len(captured_repo_maps[0]["files"]) == 2

    show_result = runner.invoke(
        app, ["session", "show", opened["session_id"], str(project), "--json"]
    )
    assert show_result.exit_code == 0, show_result.output
    shown = json.loads(show_result.stdout)
    assert len(shown["repo_map"]["files"]) == 6


def test_session_serve_edit_plan_applies_repo_map_cap_before_building_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(5):
        (src_dir / f"module_{index}.py").write_text(
            f"def target_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    session_payload = json.loads(
        runner.invoke(app, ["session", "show", opened["session_id"], str(project), "--json"]).stdout
    )

    captured_repo_maps: list[dict] = []

    def fake_build_context_edit_plan_from_map(
        repo_map: dict,
        query: str,
        **_kwargs,
    ) -> dict:
        captured_repo_maps.append(repo_map)
        return {
            "query": query,
            "files": list(repo_map.get("files", [])),
            "tests": list(repo_map.get("tests", [])),
            "symbols": list(repo_map.get("symbols", [])),
            "output_limit": dict(repo_map.get("output_limit", {})),
        }

    monkeypatch.setattr(
        session_store,
        "build_context_edit_plan_from_map",
        fake_build_context_edit_plan_from_map,
    )

    payload = session_store.serve_session_request(
        opened["session_id"],
        {
            "command": "context_edit_plan",
            "query": "target",
            "max_repo_files": 2,
        },
        str(project),
        payload=session_payload,
    )

    assert payload["routing_reason"] == "session-context-edit-plan"
    assert len(payload["files"]) == 2
    assert payload["output_limit"]["original_files"] == 5
    assert len(captured_repo_maps) == 1
    assert len(captured_repo_maps[0]["files"]) == 2


def test_session_blast_radius_plan_accepts_max_repo_files_without_changing_max_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(5):
        (src_dir / f"module_{index}.py").write_text(
            f"def target_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    assert opened["file_count"] == 5

    captured_repo_maps: list[dict] = []
    captured_kwargs: list[dict] = []

    def fake_build_symbol_blast_radius_plan_from_map(
        repo_map: dict,
        symbol: str,
        **kwargs,
    ) -> dict:
        captured_repo_maps.append(repo_map)
        captured_kwargs.append(kwargs)
        max_files = int(kwargs["max_files"])
        return {
            "symbol": symbol,
            "files": list(repo_map.get("files", []))[:max_files],
            "tests": list(repo_map.get("tests", []))[:max_files],
            "symbols": list(repo_map.get("symbols", [])),
            "output_limit": dict(repo_map.get("output_limit", {})),
            "max_files": max_files,
            "max_symbols": kwargs["max_symbols"],
        }

    monkeypatch.setattr(
        session_store,
        "build_symbol_blast_radius_plan_from_map",
        fake_build_symbol_blast_radius_plan_from_map,
    )

    radius_plan = runner.invoke(
        app,
        [
            "session",
            "blast-radius-plan",
            opened["session_id"],
            str(project),
            "target_0",
            "--max-repo-files",
            "4",
            "--max-files",
            "1",
            "--json",
        ],
    )

    assert radius_plan.exit_code == 0, radius_plan.output
    payload = json.loads(radius_plan.stdout)
    assert payload["routing_reason"] == "session-blast-radius-plan"
    assert payload["max_files"] == 1
    assert len(payload["files"]) == 1
    assert payload["output_limit"]["max_files"] == 4
    assert payload["output_limit"]["original_files"] == 5
    assert payload["output_limit"]["possibly_truncated"] is True
    assert len(captured_repo_maps) == 1
    assert len(captured_repo_maps[0]["files"]) == 4
    assert captured_kwargs[0]["max_files"] == 1

    show_result = runner.invoke(
        app, ["session", "show", opened["session_id"], str(project), "--json"]
    )
    assert show_result.exit_code == 0, show_result.output
    shown = json.loads(show_result.stdout)
    assert len(shown["repo_map"]["files"]) == 5


def test_session_serve_blast_radius_plan_applies_repo_map_cap_before_building_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    for index in range(5):
        (src_dir / f"module_{index}.py").write_text(
            f"def target_{index}():\n    return {index}\n",
            encoding="utf-8",
        )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    session_payload = json.loads(
        runner.invoke(app, ["session", "show", opened["session_id"], str(project), "--json"]).stdout
    )

    captured_repo_maps: list[dict] = []

    def fake_build_symbol_blast_radius_plan_from_map(
        repo_map: dict,
        symbol: str,
        **kwargs,
    ) -> dict:
        captured_repo_maps.append(repo_map)
        return {
            "symbol": symbol,
            "files": list(repo_map.get("files", [])),
            "tests": list(repo_map.get("tests", [])),
            "symbols": list(repo_map.get("symbols", [])),
            "output_limit": dict(repo_map.get("output_limit", {})),
            "max_files": kwargs["max_files"],
            "max_symbols": kwargs["max_symbols"],
        }

    monkeypatch.setattr(
        session_store,
        "build_symbol_blast_radius_plan_from_map",
        fake_build_symbol_blast_radius_plan_from_map,
    )

    payload = session_store.serve_session_request(
        opened["session_id"],
        {
            "command": "blast_radius_plan",
            "symbol": "target_0",
            "max_repo_files": 2,
        },
        str(project),
        payload=session_payload,
    )

    assert payload["routing_reason"] == "session-blast-radius-plan"
    assert len(payload["files"]) == 2
    assert payload["output_limit"]["original_files"] == 5
    assert len(captured_repo_maps) == 1
    assert len(captured_repo_maps[0]["files"]) == 2


def test_session_edit_plan_uses_cached_validation_evidence_after_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(2) == 3\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    monkeypatch.setattr(
        repo_map,
        "_iter_repo_files",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("warm session edit-plan should use cached validation paths")
        ),
    )

    edit_plan = runner.invoke(
        app,
        [
            "session",
            "edit-plan",
            opened["session_id"],
            str(project),
            "--query",
            "create invoice",
            "--json",
        ],
    )

    assert edit_plan.exit_code == 0, edit_plan.output
    payload = json.loads(edit_plan.stdout)
    assert payload["routing_reason"] == "session-context-edit-plan"
    assert payload["files"] == [str(module_path.resolve())]
    assert payload["validation_commands"] == [
        "pytest tests/test_payments.py -k test_create_invoice -q",
        "pytest tests/test_payments.py -q",
        "pytest -q",
    ]


def test_session_refresh_on_stale_detects_added_files_when_requested(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    added_path = src_dir / "refunds.py"
    added_path.write_text("def issue_refund():\n    return 2\n", encoding="utf-8")

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


def test_stale_changeset_bounds_the_added_file_probe_to_the_session_scan_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """M3 : `_stale_changeset(detect_added_files=True)` used to
    call `_iter_repo_files(root)` with no `max_files`, defaulting to a full unbounded
    recursive enumeration. Reachable from MCP via `refresh_session` / `_load_session_payload`
    on any `tg_session_*` call with `refresh_on_stale=True`. It must now bound the probe to
    the session's own recorded `scan_limit.max_repo_files` (or the shared default)."""
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("value = 1\n", encoding="utf-8")

    captured: dict[str, object] = {}
    real_iter_repo_files = session_store._iter_repo_files

    def spy_iter_repo_files(root, **kwargs):
        captured["max_files"] = kwargs.get("max_files")
        return real_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(session_store, "_iter_repo_files", spy_iter_repo_files)

    # A non-empty snapshot is no longer required for the added-file probe (an empty one is probed
    # too when detect_added_files=True); this payload keeps one entry as a realistic fixture.
    payload = {
        "root": str(project),
        "snapshot": [{"path": str(project / "sample.py"), "size": 0, "mtime_ns": 0}],
        "scan_limit": {"max_repo_files": 7},
    }
    session_store._stale_changeset(payload, detect_added_files=True)

    assert captured["max_files"] == 7


def test_stale_changeset_falls_back_to_shared_default_without_a_recorded_scan_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tensor_grep.cli import repo_map, session_store

    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("value = 1\n", encoding="utf-8")

    captured: dict[str, object] = {}
    real_iter_repo_files = session_store._iter_repo_files

    def spy_iter_repo_files(root, **kwargs):
        captured["max_files"] = kwargs.get("max_files")
        return real_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(session_store, "_iter_repo_files", spy_iter_repo_files)

    payload = {
        "root": str(project),
        "snapshot": [{"path": str(project / "sample.py"), "size": 0, "mtime_ns": 0}],
    }
    session_store._stale_changeset(payload, detect_added_files=True)

    # No recorded scan_limit on the session payload -- bounded to the shared default
    # (never an unbounded max_files=None full recursive enumeration).
    assert captured["max_files"] == repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT


def test_session_list_returns_newest_first(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("value = 1\n", encoding="utf-8")

    runner = CliRunner()
    first = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    second = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    listing = runner.invoke(app, ["session", "list", str(project), "--json"])
    assert listing.exit_code == 0
    payload = json.loads(listing.stdout)
    assert payload["sessions"][0]["session_id"] == second["session_id"]
    assert payload["sessions"][1]["session_id"] == first["session_id"]


def test_session_list_discovers_child_scope_from_parent_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    parent = tmp_path / "workspace"
    project = parent / "project"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("value = 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    monkeypatch.chdir(parent)
    listing = runner.invoke(app, ["session", "list", "--json"])

    assert listing.exit_code == 0, listing.output
    payload = json.loads(listing.stdout)
    assert payload["discovered"] is True
    assert payload["sessions"][0]["session_id"] == opened["session_id"]
    assert payload["sessions"][0]["root"] == str(project.resolve())


def test_session_open_generates_unique_ids_within_same_second(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("value = 1\n", encoding="utf-8")

    runner = CliRunner()
    first = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)
    second = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    assert first["session_id"] != second["session_id"]

    listing = json.loads(runner.invoke(app, ["session", "list", str(project), "--json"]).stdout)
    listed_ids = [entry["session_id"] for entry in listing["sessions"]]
    assert listed_ids.count(first["session_id"]) == 1
    assert listed_ids.count(second["session_id"]) == 1


def test_session_serve_streams_jsonl_requests_from_cached_session(
    tmp_path: Path, monkeypatch
) -> None:
    from tensor_grep.cli import session_store

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "invoice_flow.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\nassert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    monkeypatch.setattr(
        session_store,
        "build_repo_map",
        lambda path=".": (_ for _ in ()).throw(AssertionError("serve should use cached repo_map")),
    )

    stdin = StringIO(
        "\n".join([
            json.dumps({"command": "repo_map"}),
            json.dumps({"command": "context", "query": "invoice payment"}),
            json.dumps({"command": "callers", "symbol": "create_invoice"}),
            json.dumps({"command": "blast_radius", "symbol": "create_invoice", "max_depth": 1}),
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

    assert served == 4
    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert responses[0]["session_id"] == opened["session_id"]
    assert responses[0]["routing_reason"] == "session-repo-map"
    assert responses[0]["serve_cache"]["status"] == "miss"
    assert responses[0]["files"] == [str(module_path.resolve())]
    assert responses[1]["routing_reason"] == "session-context"
    assert responses[1]["serve_cache"]["status"] == "hit"
    assert responses[1]["tests"][0] == str(test_path.resolve())
    assert responses[2]["routing_reason"] == "session-callers"
    assert responses[2]["serve_cache"]["status"] == "hit"
    assert responses[2]["callers"][0]["file"] == str(test_path.resolve())
    assert responses[3]["routing_reason"] == "session-blast-radius"
    assert responses[3]["max_depth"] == 1
    assert responses[3]["tests"][0] == str(test_path.resolve())


def test_session_serve_cli_reports_invalid_request_as_jsonl(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    result = runner.invoke(
        app,
        ["session", "serve", opened["session_id"], str(project)],
        input='{"command":"context"}\n',
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["session_id"] == opened["session_id"]
    assert payload["error"]["code"] == "invalid_request"
    assert "non-empty query" in payload["error"]["message"]


def test_session_serve_reports_stale_session_after_file_change(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = json.loads(runner.invoke(app, ["session", "open", str(project), "--json"]).stdout)

    module_path.write_text("def create_invoice():\n    return 2\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["session", "serve", opened["session_id"], str(project)],
        input='{"command":"context","query":"invoice"}\n',
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["session_id"] == opened["session_id"]
    assert payload["error"]["code"] == "stale_session"
    assert "changed on disk" in payload["error"]["message"]
