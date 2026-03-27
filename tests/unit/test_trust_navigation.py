import copy
from pathlib import Path

from tensor_grep.cli import repo_map


def _write_navigation_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n"
        "    return total + tax\n",
        encoding="utf-8",
    )
    caller_path = src_dir / "billing.py"
    caller_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )
    return project, module_path, caller_path, test_path


def test_build_symbol_impact_includes_ranking_quality_and_coverage_summary(
    tmp_path: Path,
) -> None:
    project, module_path, caller_path, test_path = _write_navigation_fixture(tmp_path)

    payload = repo_map.build_symbol_impact("create_invoice", project)

    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["files"][0] == str(module_path.resolve())
    assert str(caller_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())
    assert payload["ranking_quality"] == repo_map._ranking_quality(
        payload["file_matches"],
        payload["test_matches"],
    )
    assert payload["coverage_summary"] == repo_map._coverage_summary(copy.deepcopy(payload))


def test_build_symbol_refs_include_trust_fields_and_per_reference_provenance(
    tmp_path: Path,
) -> None:
    project, _, caller_path, _ = _write_navigation_fixture(tmp_path)

    payload = repo_map.build_symbol_refs("create_invoice", project)
    context_payload = repo_map.build_context_pack("create_invoice", project)

    assert any(ref["file"] == str(caller_path.resolve()) for ref in payload["references"])
    assert payload["ranking_quality"] == repo_map._ranking_quality(
        context_payload["file_matches"],
        context_payload["test_matches"],
    )
    assert payload["coverage_summary"] == repo_map._coverage_summary(copy.deepcopy(payload))
    assert payload["coverage_summary"]["evidence_counts"]["parser_backed"] >= 1
    assert all("provenance" in ref for ref in payload["references"])


def test_build_symbol_callers_include_trust_fields_and_per_caller_provenance(
    tmp_path: Path,
) -> None:
    project, _, caller_path, test_path = _write_navigation_fixture(tmp_path)

    payload = repo_map.build_symbol_callers("create_invoice", project)
    context_payload = repo_map.build_context_pack("create_invoice", project)

    assert any(caller["file"] == str(caller_path.resolve()) for caller in payload["callers"])
    assert payload["tests"][0] == str(test_path.resolve())
    assert payload["ranking_quality"] == repo_map._ranking_quality(
        context_payload["file_matches"],
        context_payload["test_matches"],
    )
    assert payload["coverage_summary"] == repo_map._coverage_summary(copy.deepcopy(payload))
    assert payload["coverage_summary"]["evidence_counts"]["parser_backed"] >= 1
    assert all("provenance" in caller for caller in payload["callers"])
