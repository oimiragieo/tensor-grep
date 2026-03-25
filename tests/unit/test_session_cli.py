import json
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
    assert opened["file_count"] == 1
    assert opened["symbol_count"] == 1

    show_result = runner.invoke(app, ["session", "show", session_id, str(project), "--json"])
    assert show_result.exit_code == 0
    shown = json.loads(show_result.stdout)
    assert shown["session_id"] == session_id
    assert shown["repo_map"]["files"] == [str(module_path.resolve())]

    context_result = runner.invoke(
        app,
        ["session", "context", session_id, str(project), "--query", "invoice payment", "--json"],
    )
    assert context_result.exit_code == 0
    context = json.loads(context_result.stdout)
    assert context["session_id"] == session_id
    assert context["routing_reason"] == "session-context"
    assert context["coverage"]["language_scope"] == "python-js-ts-rust"
    assert context["coverage"]["symbol_navigation"] == "python-ast+parser-js-ts-rust"
    assert context["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert context["files"][0] == str(module_path.resolve())
    assert context["tests"][0] == str(test_path.resolve())


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
        "from src.payments import create_invoice\n\n"
        "assert create_invoice(1, 2) == 3\n",
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
        "\n".join(
            [
                json.dumps({"command": "repo_map"}),
                json.dumps({"command": "context", "query": "invoice payment"}),
                json.dumps({"command": "callers", "symbol": "create_invoice"}),
                json.dumps({"command": "blast_radius", "symbol": "create_invoice", "max_depth": 1}),
            ]
        )
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
    assert responses[0]["files"] == [str(module_path.resolve())]
    assert responses[1]["routing_reason"] == "session-context"
    assert responses[1]["tests"][0] == str(test_path.resolve())
    assert responses[2]["routing_reason"] == "session-callers"
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

    show_result = runner.invoke(app, ["session", "show", opened["session_id"], str(project), "--json"])
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





