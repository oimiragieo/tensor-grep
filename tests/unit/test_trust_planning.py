import json
from pathlib import Path

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
        assert payload["graph_trust_summary"] == blast_radius_payload["graph_trust_summary"]
        assert payload["graph_trust_summary"]["confidence"] == "strong"
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

    assert _planning_trust_view(render_payload) == _planning_trust_view(expected_render)
    assert _planning_trust_view(edit_plan_payload) == _planning_trust_view(expected_edit_plan)
