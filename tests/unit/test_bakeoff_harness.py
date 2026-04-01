from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _load_script_module(module_name: str = "run_bakeoff_under_test") -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "benchmarks" / "run_bakeoff.py"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    payments = src_dir / "payments.py"
    service = src_dir / "service.py"
    report = src_dir / "report.py"
    test_path = tests_dir / "test_service.py"

    _write(
        payments,
        "def create_invoice(total):\n    return total + 1\n",
    )
    _write(
        service,
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
    )
    _write(
        report,
        "from src.service import build_invoice\n\n"
        "def render_report(total):\n"
        "    return build_invoice(total)\n",
    )
    _write(
        test_path,
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
    )
    return {
        "project": project,
        "payments": payments,
        "service": service,
        "report": report,
        "test": test_path,
    }


def _write_scenarios(path: Path, scenarios: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"scenarios": scenarios}, indent=2), encoding="utf-8")


def _scenario(project: Path, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "repo_fixture": str(project),
        "query_or_symbol": "create invoice",
        "mode": "context-render",
        "expected_primary_file": None,
        "expected_primary_span": None,
        "expected_dependent_files": [],
        "expected_suggested_edit_files": [],
        "expected_test_files": [],
        "expected_validation_commands_contain": [],
    }
    payload.update(overrides)
    return payload


def _actual(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "actual_primary_file": None,
        "actual_primary_span": None,
        "actual_dependent_files": [],
        "actual_suggested_edit_files": [],
        "actual_test_files": [],
        "actual_validation_commands": [],
        "context_token_count": 0,
    }
    payload.update(overrides)
    return payload


def test_load_scenarios_validates_missing_required_fields_with_structured_errors(
    tmp_path: Path,
) -> None:
    module = _load_script_module("run_bakeoff_missing_fields")
    scenarios_path = tmp_path / "scenarios.json"
    _write_scenarios(scenarios_path, [{"mode": "context-render"}])

    with pytest.raises(module.ScenarioValidationError) as exc_info:
        module.load_scenarios(scenarios_path)

    assert exc_info.value.errors == [
        {"scenario_index": 0, "field": "repo_fixture", "code": "missing_required_field"},
        {"scenario_index": 0, "field": "query_or_symbol", "code": "missing_required_field"},
        {"scenario_index": 0, "field": "expected_primary_file", "code": "missing_required_field"},
        {"scenario_index": 0, "field": "expected_primary_span", "code": "missing_required_field"},
        {
            "scenario_index": 0,
            "field": "expected_dependent_files",
            "code": "missing_required_field",
        },
        {
            "scenario_index": 0,
            "field": "expected_suggested_edit_files",
            "code": "missing_required_field",
        },
        {"scenario_index": 0, "field": "expected_test_files", "code": "missing_required_field"},
        {
            "scenario_index": 0,
            "field": "expected_validation_commands_contain",
            "code": "missing_required_field",
        },
    ]


def test_load_scenarios_validates_mode_values(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_invalid_mode")
    project = _build_project(tmp_path)["project"]
    scenarios_path = tmp_path / "scenarios.json"
    _write_scenarios(scenarios_path, [_scenario(project, mode="edit-plan")])

    with pytest.raises(module.ScenarioValidationError) as exc_info:
        module.load_scenarios(scenarios_path)

    assert exc_info.value.errors == [
        {
            "scenario_index": 0,
            "field": "mode",
            "code": "invalid_choice",
            "expected": ["context-render", "blast-radius"],
        }
    ]


def test_load_scenarios_validates_primary_span_shape(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_invalid_span")
    project = _build_project(tmp_path)["project"]
    scenarios_path = tmp_path / "scenarios.json"
    _write_scenarios(
        scenarios_path,
        [
            _scenario(
                project,
                expected_primary_span={"start_line": 3},
            )
        ],
    )

    with pytest.raises(module.ScenarioValidationError) as exc_info:
        module.load_scenarios(scenarios_path)

    assert exc_info.value.errors == [
        {
            "scenario_index": 0,
            "field": "expected_primary_span",
            "code": "invalid_shape",
        }
    ]


def test_load_scenarios_validates_list_field_types(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_invalid_lists")
    project = _build_project(tmp_path)["project"]
    scenarios_path = tmp_path / "scenarios.json"
    _write_scenarios(
        scenarios_path,
        [_scenario(project, expected_test_files="tests/test_service.py")],
    )

    with pytest.raises(module.ScenarioValidationError) as exc_info:
        module.load_scenarios(scenarios_path)

    assert exc_info.value.errors == [
        {
            "scenario_index": 0,
            "field": "expected_test_files",
            "code": "invalid_type",
            "expected": "list[str]",
        }
    ]


def test_file_hit_rate_uses_expected_intersection(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_file_hit_rate")
    project_paths = _build_project(tmp_path)
    scenario = _scenario(
        project_paths["project"],
        expected_primary_file=str(project_paths["payments"].resolve()),
        expected_dependent_files=[
            str(project_paths["service"].resolve()),
            str(project_paths["report"].resolve()),
        ],
    )

    scored = module.score_scenario(
        scenario,
        _actual(
            actual_primary_file=str(project_paths["payments"].resolve()),
            actual_dependent_files=[str(project_paths["service"].resolve())],
        ),
    )

    assert scored["file_hit_rate"] == pytest.approx(2 / 3)


def test_file_hit_rate_is_zero_when_expected_files_empty(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_empty_expected_file_hit")
    project = _build_project(tmp_path)["project"]

    scored = module.score_scenario(
        _scenario(project),
        _actual(actual_dependent_files=["src/service.py"]),
    )

    assert scored["file_hit_rate"] == 0.0


def test_file_precision_uses_actual_intersection(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_file_precision")
    project_paths = _build_project(tmp_path)
    scenario = _scenario(
        project_paths["project"],
        expected_primary_file=str(project_paths["payments"].resolve()),
        expected_dependent_files=[str(project_paths["service"].resolve())],
    )

    scored = module.score_scenario(
        scenario,
        _actual(
            actual_primary_file=str(project_paths["payments"].resolve()),
            actual_dependent_files=[
                str(project_paths["service"].resolve()),
                str(project_paths["report"].resolve()),
                str(project_paths["test"].resolve()),
            ],
        ),
    )

    assert scored["file_precision"] == 0.5


def test_file_precision_is_one_when_actual_files_empty(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_empty_actual_precision")
    project_paths = _build_project(tmp_path)
    scenario = _scenario(
        project_paths["project"],
        expected_primary_file=str(project_paths["payments"].resolve()),
    )

    scored = module.score_scenario(scenario, _actual())

    assert scored["file_precision"] == 1.0


def test_span_hit_rate_requires_overlap(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_span_overlap")
    project = _build_project(tmp_path)["project"]
    scenario = _scenario(
        project,
        expected_primary_span={"start_line": 10, "end_line": 20},
    )

    overlapping = module.score_scenario(
        scenario,
        _actual(actual_primary_span={"start_line": 20, "end_line": 25}),
    )
    disjoint = module.score_scenario(
        scenario,
        _actual(actual_primary_span={"start_line": 21, "end_line": 30}),
    )

    assert overlapping["span_hit_rate"] == 1.0
    assert disjoint["span_hit_rate"] == 0.0


def test_test_hit_rate_uses_expected_tests(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_test_hit_rate")
    project_paths = _build_project(tmp_path)
    scenario = _scenario(
        project_paths["project"],
        expected_test_files=[
            str(project_paths["test"].resolve()),
            str((project_paths["project"] / "tests" / "test_other.py").resolve()),
        ],
    )

    scored = module.score_scenario(
        scenario,
        _actual(actual_test_files=[str(project_paths["test"].resolve())]),
    )

    assert scored["test_hit_rate"] == 0.5


def test_validation_cmd_hit_rate_uses_substring_matches(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_validation_hit_rate")
    project = _build_project(tmp_path)["project"]
    scenario = _scenario(
        project,
        expected_validation_commands_contain=["pytest", "jest"],
    )

    scored = module.score_scenario(
        scenario,
        _actual(actual_validation_commands=["uv run pytest -q", "python -m build"]),
    )

    assert scored["validation_cmd_hit_rate"] == 0.5


def test_false_positive_files_lists_unexpected_actual_files(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_false_positives")
    project_paths = _build_project(tmp_path)
    scenario = _scenario(
        project_paths["project"],
        expected_primary_file=str(project_paths["payments"].resolve()),
    )

    scored = module.score_scenario(
        scenario,
        _actual(
            actual_primary_file=str(project_paths["payments"].resolve()),
            actual_dependent_files=[str(project_paths["service"].resolve())],
        ),
    )

    assert scored["false_positive_files"] == [str(project_paths["service"].resolve())]


def test_score_scenario_preserves_provider_metadata(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_provider_metadata")
    project_paths = _build_project(tmp_path)

    scored = module.score_scenario(
        _scenario(project_paths["project"]),
        _actual(
            provider_agreement={"mode": "hybrid", "agreement_status": "fallback-native"},
            provider_status={"mode": "hybrid", "fallback_used": True},
        ),
    )

    assert scored["provider_agreement"] == {"mode": "hybrid", "agreement_status": "fallback-native"}
    assert scored["provider_status"] == {"mode": "hybrid", "fallback_used": True}


def test_run_scenario_calls_context_render_and_collects_actuals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_script_module("run_bakeoff_run_context")
    project_paths = _build_project(tmp_path)

    seen: dict[str, object] = {}

    def _fake_build_context_render(
        query: str, path: str | Path, *, profile: bool = False
    ) -> dict[str, object]:
        seen["query"] = query
        seen["path"] = str(path)
        seen["profile"] = profile
        return {
            "token_estimate": 123,
            "edit_plan_seed": {
                "primary_file": str(project_paths["payments"].resolve()),
                "primary_span": {"start_line": 1, "end_line": 2},
                "dependent_files": [str(project_paths["service"].resolve())],
                "suggested_edits": [{"file": str(project_paths["report"].resolve())}],
                "validation_tests": [str(project_paths["test"].resolve())],
                "validation_commands": ["uv run pytest -q"],
            },
        }

    monkeypatch.setattr(module.repo_map, "build_context_render", _fake_build_context_render)

    result = module.run_scenario(
        _scenario(
            project_paths["project"],
            expected_primary_file=str(project_paths["payments"].resolve()),
        )
    )

    assert seen == {
        "query": "create invoice",
        "path": str(project_paths["project"]),
        "profile": False,
    }
    assert result["actual_primary_file"] == str(project_paths["payments"].resolve())
    assert result["actual_primary_span"] == {"start_line": 1, "end_line": 2}
    assert result["actual_dependent_files"] == [str(project_paths["service"].resolve())]
    assert result["actual_suggested_edit_files"] == [str(project_paths["report"].resolve())]
    assert result["actual_test_files"] == [str(project_paths["test"].resolve())]
    assert result["actual_validation_commands"] == ["uv run pytest -q"]
    assert result["context_token_count"] == 123


def test_run_scenario_calls_blast_radius_and_forwards_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_script_module("run_bakeoff_run_blast_radius")
    project = _build_project(tmp_path)["project"]

    seen: dict[str, object] = {}

    def _fake_build_symbol_blast_radius_render(
        symbol: str,
        path: str | Path,
        *,
        profile: bool = False,
        semantic_provider: str = "native",
    ) -> dict[str, object]:
        seen["symbol"] = symbol
        seen["path"] = str(path)
        seen["profile"] = profile
        seen["semantic_provider"] = semantic_provider
        return {
            "token_estimate": 45,
            "_profiling": {"phases": [{"name": "caller_scan", "elapsed_s": 0.01, "calls": 1}]},
            "provider_agreement": {
                "mode": semantic_provider,
                "agreement_status": "fallback-native",
            },
            "provider_status": {"mode": semantic_provider, "fallback_used": True},
            "edit_plan_seed": {
                "primary_file": None,
                "primary_span": None,
                "dependent_files": [],
                "suggested_edits": [],
                "validation_tests": [],
                "validation_commands": [],
            },
        }

    monkeypatch.setattr(
        module.repo_map,
        "build_symbol_blast_radius_render",
        _fake_build_symbol_blast_radius_render,
    )

    result = module.run_scenario(
        _scenario(project, mode="blast-radius", query_or_symbol="create_invoice"),
        profile=True,
    )

    assert seen == {
        "symbol": "create_invoice",
        "path": str(project),
        "profile": True,
        "semantic_provider": "native",
    }
    assert result["_profiling"] == {
        "phases": [{"name": "caller_scan", "elapsed_s": 0.01, "calls": 1}]
    }
    assert result["provider_agreement"] == {"mode": "native", "agreement_status": "fallback-native"}
    assert result["provider_status"] == {"mode": "native", "fallback_used": True}


def test_evaluate_scenario_runs_edit_planning_against_fixture_repo(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_real_eval")
    project_paths = _build_project(tmp_path)
    scenario = _scenario(
        project_paths["project"],
        expected_primary_file=str(project_paths["payments"].resolve()),
        expected_primary_span={"start_line": 1, "end_line": 2},
        expected_dependent_files=[
            str(project_paths["service"].resolve()),
            str(project_paths["report"].resolve()),
        ],
        expected_test_files=[str(project_paths["test"].resolve())],
        expected_validation_commands_contain=["pytest"],
    )

    row = module.evaluate_scenario(scenario)

    assert row["actual_primary_file"] == str(project_paths["payments"].resolve())
    assert row["actual_primary_span"] == {"start_line": 1, "end_line": 2}
    assert str(project_paths["service"].resolve()) in row["actual_dependent_files"]
    assert row["context_token_count"] > 0
    assert row["validation_cmd_hit_rate"] == 1.0
    assert row["deterministic"] is True


def test_evaluate_scenario_rejects_nondeterministic_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_script_module("run_bakeoff_nondeterministic")
    project = _build_project(tmp_path)["project"]
    calls = {"count": 0}

    def _fake_run_scenario(
        scenario: dict[str, object],
        *,
        profile: bool = False,
        provider: str = "native",
    ) -> dict[str, object]:
        calls["count"] += 1
        return _actual(context_token_count=calls["count"])

    monkeypatch.setattr(module, "run_scenario", _fake_run_scenario)

    with pytest.raises(module.DeterminismError):
        module.evaluate_scenario(_scenario(project))

    assert calls["count"] == 2


def test_build_summary_reports_means(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_summary")
    rows = [
        module.score_scenario(
            _scenario(_build_project(tmp_path / "one")["project"]),
            _actual(context_token_count=10),
        )
        | {"name": "first"},
        module.score_scenario(
            _scenario(_build_project(tmp_path / "two")["project"]),
            _actual(context_token_count=30),
        )
        | {
            "name": "second",
            "file_hit_rate": 1.0,
            "file_precision": 0.5,
            "span_hit_rate": 1.0,
            "test_hit_rate": 0.5,
            "validation_cmd_hit_rate": 0.25,
            "context_token_count": 30,
            "false_positive_files": ["extra.py"],
        },
    ]

    summary = module.build_summary(rows)

    assert summary == {
        "scenario_count": 2,
        "mean_file_hit_rate": 0.5,
        "mean_file_precision": 0.75,
        "mean_span_hit_rate": 0.5,
        "mean_test_hit_rate": 0.25,
        "mean_validation_cmd_hit_rate": 0.125,
        "mean_context_token_count": 20.0,
        "mean_false_positive_file_count": 0.5,
    }


def test_main_writes_standard_benchmark_json_with_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_script_module("run_bakeoff_main")
    output_path = tmp_path / "bench_bakeoff.json"
    scenarios_path = tmp_path / "scenarios.json"
    _write_scenarios(scenarios_path, [])

    scenario_a = _scenario(tmp_path / "repo-a", query_or_symbol="alpha")
    scenario_b = _scenario(tmp_path / "repo-b", query_or_symbol="beta", mode="blast-radius")
    monkeypatch.setattr(module, "load_scenarios", lambda path: [scenario_a, scenario_b])
    monkeypatch.setattr(
        module,
        "evaluate_scenario",
        lambda scenario, *, profile=False, provider="native": {
            "name": f"{scenario['mode']}:{scenario['query_or_symbol']}",
            "mode": scenario["mode"],
            "query_or_symbol": scenario["query_or_symbol"],
            "file_hit_rate": 1.0 if scenario["query_or_symbol"] == "alpha" else 0.0,
            "file_precision": 0.5,
            "span_hit_rate": 1.0,
            "test_hit_rate": 0.5,
            "validation_cmd_hit_rate": 1.0,
            "context_token_count": 12,
            "false_positive_files": [],
            "deterministic": True,
        },
    )
    monkeypatch.setattr(
        "sys.argv",
        ["run_bakeoff.py", "--scenarios", str(scenarios_path), "--output", str(output_path)],
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_bakeoff"
    assert payload["suite"] == "run_bakeoff"
    assert payload["generated_at_epoch_s"] > 0
    assert payload["environment"]["platform"]
    assert [row["name"] for row in payload["rows"]] == [
        "context-render:alpha",
        "blast-radius:beta",
    ]
    assert payload["summary"]["scenario_count"] == 2
    assert payload["summary"]["mean_file_hit_rate"] == 0.5


def test_determinism_snapshot_ignores_provider_cooldown_drift(tmp_path: Path) -> None:
    module = _load_script_module("run_bakeoff_provider_determinism_snapshot")
    row = module.score_scenario(
        _scenario(_build_project(tmp_path)["project"]),
        _actual(
            provider_status={
                "mode": "hybrid",
                "fallback_used": True,
                "providers": [
                    {
                        "language": "python",
                        "last_error": "timeout waiting for LSP response: initialize",
                        "cooldown_remaining_s": 29.9,
                    }
                ],
            }
        ),
    )
    comparison = dict(row)
    comparison["provider_status"] = {
        "mode": "hybrid",
        "fallback_used": True,
        "providers": [
            {
                "language": "python",
                "last_error": "timeout waiting for LSP response: initialize",
                "cooldown_remaining_s": 11.2,
            }
        ],
    }

    assert module._determinism_snapshot(row) == module._determinism_snapshot(comparison)


def test_main_profile_keeps_per_scenario_profiling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_script_module("run_bakeoff_main_profile")
    output_path = tmp_path / "bench_bakeoff.json"
    scenarios_path = tmp_path / "scenarios.json"
    _write_scenarios(scenarios_path, [])
    scenario = _scenario(tmp_path / "repo", query_or_symbol="alpha")

    monkeypatch.setattr(module, "load_scenarios", lambda path: [scenario])
    monkeypatch.setattr(
        module,
        "evaluate_scenario",
        lambda current, *, profile=False, provider="native": {
            "name": "context-render:alpha",
            "mode": current["mode"],
            "query_or_symbol": current["query_or_symbol"],
            "file_hit_rate": 1.0,
            "file_precision": 1.0,
            "span_hit_rate": 1.0,
            "test_hit_rate": 1.0,
            "validation_cmd_hit_rate": 1.0,
            "context_token_count": 8,
            "false_positive_files": [],
            "deterministic": True,
            "_profiling": {"phases": [{"name": "repo_map_build", "elapsed_s": 0.01, "calls": 1}]},
            "profile_requested": profile,
        },
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_bakeoff.py",
            "--scenarios",
            str(scenarios_path),
            "--output",
            str(output_path),
            "--profile",
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["rows"][0]["_profiling"]["phases"][0]["name"] == "repo_map_build"
    assert payload["rows"][0]["profile_requested"] is True


def test_main_returns_nonzero_and_writes_structured_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module = _load_script_module("run_bakeoff_main_validation_error")
    output_path = tmp_path / "bench_bakeoff.json"
    scenarios_path = tmp_path / "scenarios.json"
    _write_scenarios(scenarios_path, [])

    monkeypatch.setattr(
        module,
        "load_scenarios",
        lambda path: (_ for _ in ()).throw(
            module.ScenarioValidationError([
                {
                    "scenario_index": 0,
                    "field": "mode",
                    "code": "invalid_choice",
                    "expected": ["context-render", "blast-radius"],
                }
            ])
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["run_bakeoff.py", "--scenarios", str(scenarios_path), "--output", str(output_path)],
    )

    exit_code = module.main()

    captured = capsys.readouterr()
    assert exit_code == 2
    assert json.loads(captured.err) == {
        "errors": [
            {
                "scenario_index": 0,
                "field": "mode",
                "code": "invalid_choice",
                "expected": ["context-render", "blast-radius"],
            }
        ]
    }
