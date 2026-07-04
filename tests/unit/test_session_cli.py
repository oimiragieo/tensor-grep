import json
import threading
from io import StringIO
from pathlib import Path

from typer.testing import CliRunner

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
    assert context["coverage"]["language_scope"] == "python-js-ts-rust"
    assert context["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
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
        "uv run pytest tests/test_payments.py -k test_create_invoice -q",
        "uv run pytest tests/test_payments.py -q",
        "uv run pytest -q",
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
    assert (
        status["response_cache_scope"]
        == "daemon-routed top-level/session context-render/edit-plan requests"
    )
    assert status["response_cache_stale_detection"] == "snapshot_mtime_only"
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
            "max_repo_files": 512,
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
            "max_repo_files": 512,
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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

        first = session_daemon._daemon_request(host, port, request)
        second = session_daemon._daemon_request(host, port, request)
        stats = session_daemon._daemon_request(host, port, {"command": "stats"})
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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

        first = session_daemon._daemon_request(host, port, request)
        second = session_daemon._daemon_request(host, port, request)
        stats = session_daemon._daemon_request(host, port, {"command": "stats"})
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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

        first = session_daemon._daemon_request(host, port, request)
        second = session_daemon._daemon_request(host, port, request)
        stats = session_daemon._daemon_request(host, port, {"command": "stats"})
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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

        first = session_daemon._daemon_request(host, port, request)
        second = session_daemon._daemon_request(host, port, request)
        third = session_daemon._daemon_request(host, port, request)
        stats = session_daemon._daemon_request(host, port, {"command": "stats"})
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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

        first = session_daemon._daemon_request(host, port, request)
        second = session_daemon._daemon_request(host, port, request)
        stats = session_daemon._daemon_request(host, port, {"command": "stats"})
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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

        first = session_daemon._daemon_request(host, port, request)
        second = session_daemon._daemon_request(host, port, request)
        stats = session_daemon._daemon_request(host, port, {"command": "stats"})
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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
        )
        stats = session_daemon._daemon_request(host, port, {"command": "stats"})
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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

        first = session_daemon._daemon_request(host, port, request)
        second = session_daemon._daemon_request(host, port, request)
        stats = session_daemon._daemon_request(host, port, {"command": "stats"})
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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

        first = session_daemon._daemon_request(host, port, request)
        module_path.write_text("def create_invoice():\n    return 100\n", encoding="utf-8")
        second = session_daemon._daemon_request(host, port, request)
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

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0))
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

        first = session_daemon._daemon_request(host, port, request)
        module_path.write_text("def create_invoice():\n    return 100\n", encoding="utf-8")
        second = session_daemon._daemon_request(host, port, request)
        stats = session_daemon._daemon_request(host, port, {"command": "stats"})
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
