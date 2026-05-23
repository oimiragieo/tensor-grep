import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import repo_map
from tensor_grep.cli.main import app


def _write_planning_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
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
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total, 2)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 4\n",
        encoding="utf-8",
    )
    return project, module_path, service_path, test_path


def _write_invoice_tax_fixture(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    payments_path = src_dir / "payments.py"
    payments_path.write_text(
        "TAX_RATE = 0.0825\n\n"
        "def create_invoice(subtotal: float) -> dict[str, float]:\n"
        "    tax = subtotal * TAX_RATE\n"
        "    total = subtotal + tax\n"
        '    return {"subtotal": subtotal, "tax": tax, "total": total}\n',
        encoding="utf-8",
    )
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_service(subtotal: float) -> dict[str, float]:\n"
        "    return create_invoice(subtotal)\n",
        encoding="utf-8",
    )
    app_path = src_dir / "app.ts"
    app_path.write_text(
        "export function createInvoice(subtotal: number) {\n  return { subtotal };\n}\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import TAX_RATE, create_invoice\n\n"
        "def test_create_invoice_applies_tax_rate():\n"
        "    invoice = create_invoice(100.0)\n"
        '    assert invoice["tax"] == 100.0 * TAX_RATE\n'
        '    assert invoice["total"] == invoice["subtotal"] + invoice["tax"]\n',
        encoding="utf-8",
    )
    return {
        "project": project,
        "payments": payments_path,
        "service": service_path,
        "app": app_path,
        "test": test_path,
    }


def _planning_trust_view(payload: dict[str, object]) -> dict[str, object]:
    edit_plan_seed = payload["edit_plan_seed"]
    candidate_edit_targets = payload["candidate_edit_targets"]
    return {
        "graph_trust_summary": payload["graph_trust_summary"],
        "dependency_trust": edit_plan_seed["dependency_trust"],
        "plan_trust_summary": edit_plan_seed["plan_trust_summary"],
        "candidate_ranking_quality": candidate_edit_targets["ranking_quality"],
        "candidate_coverage_summary": candidate_edit_targets["coverage_summary"],
    }


def test_build_planning_surfaces_include_graph_and_dependency_trust(tmp_path: Path) -> None:
    project, module_path, service_path, _ = _write_planning_fixture(tmp_path)

    blast_radius_payload = repo_map.build_symbol_blast_radius("create_invoice", project)
    render_payload = repo_map.build_context_render("create invoice", project)
    edit_plan_payload = repo_map.build_context_edit_plan("create invoice", project)

    expected_dependency_trust = {
        "import_resolution_quality": "strong",
        "parser_backed_count": 1,
        "heuristic_count": 0,
    }

    assert blast_radius_payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    for payload in (render_payload, edit_plan_payload):
        assert (
            payload["graph_trust_summary"]["edge_kind"]
            == blast_radius_payload["graph_trust_summary"]["edge_kind"]
        )
        assert payload["graph_trust_summary"]["confidence"] == "strong"
        assert payload["graph_trust_summary"]["evidence_counts"]["parser_backed"] >= 1
        assert payload["candidate_edit_targets"]["ranking_quality"] == payload["ranking_quality"]
        assert payload["candidate_edit_targets"]["coverage_summary"] == payload["coverage_summary"]
        assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
        assert str(service_path.resolve()) in payload["edit_plan_seed"]["dependent_files"]
        assert payload["edit_plan_seed"]["dependency_trust"] == expected_dependency_trust
        assert payload["edit_plan_seed"]["plan_trust_summary"]
        assert payload["ranking_quality"] in payload["edit_plan_seed"]["plan_trust_summary"]
        assert (
            payload["edit_plan_seed"]["dependency_trust"]["import_resolution_quality"]
            in payload["edit_plan_seed"]["plan_trust_summary"]
        )


def test_planning_trust_fields_are_deterministic(tmp_path: Path) -> None:
    project, _, _, _ = _write_planning_fixture(tmp_path)

    first_render = repo_map.build_context_render("create invoice", project)
    second_render = repo_map.build_context_render("create invoice", project)
    first_edit_plan = repo_map.build_context_edit_plan("create invoice", project)
    second_edit_plan = repo_map.build_context_edit_plan("create invoice", project)

    assert _planning_trust_view(first_render) == _planning_trust_view(second_render)
    assert _planning_trust_view(first_edit_plan) == _planning_trust_view(second_edit_plan)


def test_edit_plan_prefers_source_backed_primary_file_for_camel_case_query(
    tmp_path: Path,
) -> None:
    project, module_path, _, _ = _write_planning_fixture(tmp_path)

    payload = repo_map.build_context_edit_plan("createInvoice subtotal tax", project)

    assert payload["edit_plan_seed"]["primary_file"] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["files"][0] == str(module_path.resolve())


def test_cli_json_planning_surfaces_include_trust_metadata(tmp_path: Path) -> None:
    runner = CliRunner()
    project, _, _, _ = _write_planning_fixture(tmp_path)

    expected_render = repo_map.build_context_render("create invoice", project)
    expected_edit_plan = repo_map.build_context_edit_plan("create invoice", project)

    render_result = runner.invoke(
        app,
        [
            "context-render",
            "--query",
            "create invoice",
            "--render-profile",
            "full",
            "--json",
            str(project),
        ],
    )
    edit_plan_result = runner.invoke(
        app,
        ["edit-plan", "--query", "create invoice", "--json", str(project)],
    )

    assert render_result.exit_code == 0, render_result.output
    assert edit_plan_result.exit_code == 0, edit_plan_result.output

    render_payload = json.loads(render_result.output)
    edit_plan_payload = json.loads(edit_plan_result.output)

    assert render_payload["schema_version"] == render_payload["version"]
    assert edit_plan_payload["schema_version"] == edit_plan_payload["version"]
    assert _planning_trust_view(render_payload) == _planning_trust_view(expected_render)
    assert _planning_trust_view(edit_plan_payload) == _planning_trust_view(expected_edit_plan)


def test_context_render_natural_invoice_tax_query_selects_payment_logic(
    tmp_path: Path,
) -> None:
    paths = _write_invoice_tax_fixture(tmp_path)

    payload = repo_map.build_context_render(
        "change invoice tax calculation",
        paths["project"],
        render_profile="llm",
    )

    assert payload["files"][0] == str(paths["payments"].resolve())
    assert payload["edit_plan_seed"]["primary_file"] == str(paths["payments"].resolve())
    assert payload["navigation_pack"]["primary_target"]["file"] == str(paths["payments"].resolve())
    assert payload["edit_plan_seed"]["primary_symbol"]["name"] == "create_invoice"
    assert payload["sources"][0]["file"] == str(paths["payments"].resolve())
    assert "src/app.ts" not in payload["edit_plan_seed"]["primary_file"].replace("\\", "/")


def test_context_render_bridges_natural_query_to_snake_case_symbol(
    tmp_path: Path,
) -> None:
    paths = _write_invoice_tax_fixture(tmp_path)

    payload = repo_map.build_context_render("create invoice tax", paths["project"])

    assert payload["edit_plan_seed"]["primary_symbol"]["name"] == "create_invoice"
    assert payload["edit_plan_seed"]["primary_file"] == str(paths["payments"].resolve())


def test_exact_camel_case_symbol_query_dominates_natural_language_scores(
    tmp_path: Path,
) -> None:
    paths = _write_invoice_tax_fixture(tmp_path)

    payload = repo_map.build_context_render("createInvoice", paths["project"])

    assert payload["edit_plan_seed"]["primary_symbol"]["name"] == "createInvoice"
    assert payload["edit_plan_seed"]["primary_file"] == str(paths["app"].resolve())


def test_exact_symbol_query_does_not_mark_snake_case_bridge_as_literal() -> None:
    assert repo_map._symbol_name_matches_query_exactly("createInvoice", "createInvoice")
    assert repo_map._symbol_name_matches_query_exactly("create_invoice", "create_invoice")
    assert not repo_map._symbol_name_matches_query_exactly("create_invoice", "createInvoice")


def test_cli_context_render_exact_symbol_query_prefers_literal_symbol(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    payments_path = src_dir / "aaa_payments.py"
    payments_path.write_text(
        "TAX_RATE = 0.0825\n\n"
        "def create_invoice(subtotal: float) -> dict[str, float]:\n"
        "    tax = subtotal * TAX_RATE\n"
        "    total = subtotal + tax\n"
        "    return {'subtotal': subtotal, 'tax': tax, 'total': total}\n\n"
        "def create_invoice_receipt(subtotal: float) -> str:\n"
        "    invoice = create_invoice(subtotal)\n"
        "    return f\"invoice total {invoice['total']}\"\n",
        encoding="utf-8",
    )
    app_path = src_dir / "zzz_app.ts"
    app_path.write_text(
        "export function createInvoice(subtotal: number) {\n  return { subtotal };\n}\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["context-render", "--query", "createInvoice", "--json", str(project)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["files"][0] == str(app_path.resolve())
    assert payload["edit_plan_seed"]["primary_symbol"]["name"] == "createInvoice"
    assert payload["edit_plan_seed"]["primary_file"] == str(app_path.resolve())
    assert str(payments_path.resolve()) != payload["edit_plan_seed"]["primary_file"]


def test_llm_render_keeps_executable_body_lines_for_selected_function(
    tmp_path: Path,
) -> None:
    paths = _write_invoice_tax_fixture(tmp_path)

    payload = repo_map.build_context_render(
        "invoice tax calculation",
        paths["project"],
        render_profile="llm",
    )
    source = next(item for item in payload["sources"] if item["name"] == "create_invoice")

    assert "def create_invoice" in source["rendered_source"]
    assert "tax = subtotal * TAX_RATE" in source["rendered_source"]
    assert "total = subtotal + tax" in source["rendered_source"]
    assert 'return {"subtotal": subtotal' in source["rendered_source"]
    assert "tax = subtotal * TAX_RATE" in payload["rendered_context"]


def test_context_render_consistency_metadata_links_seed_render_and_navigation(
    tmp_path: Path,
) -> None:
    paths = _write_invoice_tax_fixture(tmp_path)

    payload = repo_map.build_context_render(
        "invoice tax calculation",
        paths["project"],
        max_files=1,
        max_sources=1,
    )

    primary_file = payload["edit_plan_seed"]["primary_file"]
    source_files = {source["file"] for source in payload["sources"]}
    follow_up_files = {read["file"] for read in payload["navigation_pack"]["follow_up_reads"]}

    assert primary_file in set(payload["files"]) | source_files | follow_up_files
    assert primary_file == payload["navigation_pack"]["primary_target"]["file"]
    assert primary_file in payload["rendered_context"]
    assert payload["context_consistency"]["primary_file_included"] is True
    assert payload["context_consistency"]["render_matches_primary_target"] is True
    assert payload["context_consistency"]["confidence_downgraded"] is False


def test_edit_plan_seed_never_pairs_symbol_span_with_different_primary_file() -> None:
    repo = {
        "path": ".",
        "imports": [],
    }
    main_path = str(Path("src/tensor_grep/cli/main.py").resolve())
    runtime_path = str(Path("src/tensor_grep/cli/runtime_paths.py").resolve())
    payload = {
        "path": ".",
        "files": [main_path],
        "tests": [],
        "file_matches": [
            {
                "path": main_path,
                "score": 9,
                "reasons": ["source"],
            }
        ],
        "test_matches": [],
    }
    ranked_symbols = [
        {
            "name": "resolve_ripgrep_binary",
            "kind": "function",
            "file": runtime_path,
            "line": 227,
            "start_line": 227,
            "end_line": 252,
            "score": 9,
        }
    ]

    seed = repo_map._build_edit_plan_seed(
        repo,
        payload,
        ranked_symbols=ranked_symbols,
        query="ripgrep binary resolution",
        max_files=3,
    )
    navigation_target = repo_map._navigation_pack(
        repo,
        {"edit_plan_seed": seed, "candidate_edit_targets": {}},
        max_reads=3,
    )["primary_target"]

    assert seed["primary_file"] == runtime_path
    assert navigation_target["file"] == runtime_path
    assert navigation_target["mention_ref"] == f"{runtime_path}#L227-L252"


def test_validation_plan_does_not_suggest_npm_without_package_json(
    tmp_path: Path,
) -> None:
    paths = _write_invoice_tax_fixture(tmp_path)

    payload = repo_map.build_context_render("create invoice tax", paths["project"])
    validation_plan = payload["edit_plan_seed"]["validation_plan"]
    commands = [step["command"] for step in validation_plan]

    assert all("npm test" not in command for command in commands)
    assert all("npx " not in command for command in commands)
    assert any(command.startswith("uv run pytest") for command in commands)
    assert {step["detection"] for step in validation_plan} <= {
        "detected",
        "heuristic",
        "generic",
    }
    assert validation_plan[0]["detection"] == "detected"


def test_repo_map_excludes_generated_hidden_binary_and_log_noise_by_default(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    hidden_dir = project / ".hidden"
    git_dir = project / ".git"
    node_modules = project / "node_modules" / "pkg"
    cache_dir = project / ".pytest_cache"
    artifacts_dir = project / "artifacts" / "debug"
    src_dir.mkdir(parents=True)
    hidden_dir.mkdir()
    git_dir.mkdir()
    node_modules.mkdir(parents=True)
    cache_dir.mkdir()
    artifacts_dir.mkdir(parents=True)

    source_path = src_dir / "payments.py"
    source_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")
    (hidden_dir / "notes.txt").write_text("create invoice hidden note\n", encoding="utf-8")
    (git_dir / "index").write_bytes(b"binary\0index")
    (node_modules / "dep.js").write_text("export const dep = 1;\n", encoding="utf-8")
    (cache_dir / "lastfailed").write_text("{}\n", encoding="utf-8")
    artifact_probe = artifacts_dir / "agent_probe.py"
    artifact_probe.write_text(
        "def create_invoice_debug_probe():\n    return 'debug artifact'\n",
        encoding="utf-8",
    )
    (project / "run.log").write_text("create invoice noisy log\n", encoding="utf-8")
    (project / "blob.bin").write_bytes(b"create invoice\0binary")

    payload = repo_map.build_context_render("create invoice", project)
    files = {Path(path).resolve() for path in payload["files"]}

    assert source_path.resolve() in files
    assert (hidden_dir / "notes.txt").resolve() not in files
    assert (node_modules / "dep.js").resolve() not in files
    assert artifact_probe.resolve() not in files
    assert (project / "run.log").resolve() not in files
    assert (project / "blob.bin").resolve() not in files


def test_repo_map_does_not_open_every_context_file_for_binary_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    source_path = src_dir / "payments.py"
    source_path.write_text("def create_invoice():\n    return 1\n", encoding="utf-8")

    def fail_binary_probe(path: Path) -> bool:
        raise AssertionError(f"unexpected binary probe for {path}")

    monkeypatch.setattr(repo_map, "_looks_like_binary_file", fail_binary_probe)

    payload = repo_map.build_repo_map(project)

    assert str(source_path.resolve()) in payload["files"]


def test_edit_plan_excludes_temp_probe_context_and_caps_related_metadata(
    tmp_path: Path,
) -> None:
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
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )
    noisy_files = [
        project / "tmp_agent_probe" / "probe.py",
        project / ".tmp" / "probe.py",
        project / ".claude" / "context" / "debug.py",
        project / "artifacts" / "debug" / "agent_probe.py",
    ]
    for noisy_file in noisy_files:
        noisy_file.parent.mkdir(parents=True, exist_ok=True)
        noisy_file.write_text(
            "def create_invoice_probe():\n    return 'debug probe'\n",
            encoding="utf-8",
        )

    payload = repo_map.build_context_edit_plan(
        "create invoice",
        project,
        max_files=1,
        max_symbols=1,
        max_sources=1,
        max_tokens=64,
    )
    serialized = json.dumps(payload)

    assert payload["files"] == [str(module_path.resolve())]
    assert len(payload["imports"]) <= 1
    assert set(payload["related_paths"]) <= set(payload["files"] + payload["tests"])
    for noisy_file in noisy_files:
        assert str(noisy_file.resolve()) not in serialized
        assert noisy_file.name not in serialized


def test_repo_map_keeps_legitimate_temp_and_probe_named_source_dirs(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    temp_sensor = project / "temp_sensor" / "readings.py"
    system_probe = project / "system_probe" / "diagnostics.py"
    temp_sensor.parent.mkdir(parents=True)
    system_probe.parent.mkdir(parents=True)
    temp_sensor.write_text(
        "def read_temperature_sensor():\n    return 'temperature'\n",
        encoding="utf-8",
    )
    system_probe.write_text(
        "def system_probe_status():\n    return 'ok'\n",
        encoding="utf-8",
    )

    payload = repo_map.build_context_render(
        "temperature sensor probe status",
        project,
        max_files=4,
        max_sources=2,
    )
    files = {Path(path).resolve() for path in payload["files"]}

    assert temp_sensor.resolve() in files
    assert system_probe.resolve() in files
