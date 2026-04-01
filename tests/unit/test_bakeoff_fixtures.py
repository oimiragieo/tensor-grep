from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT_DIR / "benchmarks" / "bakeoff_scenarios"
FIXTURE_DIR = ROOT_DIR / "benchmarks" / "bakeoff_fixtures"

SCENARIO_SPECS = {
    "python_scenarios.json": {
        "minimum_count": 10,
        "language": "python",
        "categories": {
            "simple_function_one_caller",
            "multiple_callers_across_files",
            "class_method_inheritance",
            "module_level_import_change",
            "leaf_function_no_callers",
            "many_callers_high_blast_radius",
            "test_coverage_present",
            "test_coverage_absent",
            "cross_module_dependency_chain",
            "circular_import",
        },
    },
    "js_ts_scenarios.json": {
        "minimum_count": 10,
        "language": "js-ts",
        "categories": {
            "named_import_alias",
            "default_import",
            "re_export_chain",
            "jest_test_association",
            "type_only_import",
            "tsconfig_path_alias",
            "module_with_multiple_exports",
            "barrel_re_exports",
            "cross_directory_imports",
            "react_component_with_test",
        },
    },
    "rust_scenarios.json": {
        "minimum_count": 10,
        "language": "rust",
        "categories": {
            "simple_fn_with_caller",
            "pub_mod_tree_navigation",
            "use_alias_resolution",
            "workspace_cross_crate_import",
            "trait_implementation_caller",
            "test_function_association",
            "tokio_test_association",
            "scoped_identifier_call",
            "glob_use_resolution",
            "multiple_same_named_symbols",
        },
    },
}


def _load_script_module(module_name: str = "run_bakeoff_fixture_loader") -> ModuleType:
    script_path = ROOT_DIR / "benchmarks" / "run_bakeoff.py"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _absolute(repo_fixture: Path, relative_or_absolute: str | None) -> Path | None:
    if relative_or_absolute is None:
        return None
    path = Path(relative_or_absolute)
    return path if path.is_absolute() else repo_fixture / path


@pytest.mark.parametrize("filename", sorted(SCENARIO_SPECS))
def test_bakeoff_scenario_file_exists(filename: str) -> None:
    assert (SCENARIO_DIR / filename).is_file()


@pytest.mark.parametrize(
    ("filename", "expected"),
    [(name, spec) for name, spec in sorted(SCENARIO_SPECS.items())],
)
def test_bakeoff_scenarios_load_with_expected_coverage(
    filename: str,
    expected: dict[str, object],
) -> None:
    module = _load_script_module(f"run_bakeoff_fixture_loader_{filename.replace('.', '_')}")
    scenarios = module.load_scenarios(SCENARIO_DIR / filename)

    assert len(scenarios) >= expected["minimum_count"]
    assert {scenario["language"] for scenario in scenarios} == {expected["language"]}
    assert {scenario["category"] for scenario in scenarios} >= expected["categories"]
    assert len({scenario["id"] for scenario in scenarios}) == len(scenarios)


@pytest.mark.parametrize("filename", sorted(SCENARIO_SPECS))
def test_bakeoff_scenarios_reference_existing_fixture_files(filename: str) -> None:
    module = _load_script_module(f"run_bakeoff_fixture_paths_{filename.replace('.', '_')}")
    scenarios = module.load_scenarios(SCENARIO_DIR / filename)

    for scenario in scenarios:
        repo_fixture = Path(scenario["repo_fixture"])
        assert repo_fixture.is_dir()
        assert FIXTURE_DIR in repo_fixture.parents

        referenced_paths = [
            scenario.get("expected_primary_file"),
            *scenario.get("expected_dependent_files", []),
            *scenario.get("expected_suggested_edit_files", []),
            *scenario.get("expected_test_files", []),
        ]
        for path_value in referenced_paths:
            absolute = _absolute(repo_fixture, path_value)
            if absolute is None:
                continue
            assert absolute.exists(), f"missing path for scenario {scenario['id']}: {absolute}"
