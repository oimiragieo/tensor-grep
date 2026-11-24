from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import predictive_test_selector, repo_map


def test_managed_runtime_candidate_ranks_first_ahead_of_unrelated_e2e(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    cli_dir = project / "src" / "tensor_grep" / "cli"
    cli_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='tg'\nversion='0.1.0'\n", encoding="utf-8"
    )
    primary = cli_dir / "managed_runtime_candidate.py"
    primary.write_text("def _run_managed_candidate_install():\n    return True\n", encoding="utf-8")

    e2e_dir = project / "tests" / "e2e"
    e2e_dir.mkdir(parents=True)
    unrelated_e2e = e2e_dir / "test_routing_parity.py"
    unrelated_e2e.write_text(
        "def test_run_java_rewrite_keeps_baseline_refusal():\n"
        "    # managed runtime staging timeout\n"
        "    assert True\n",
        encoding="utf-8",
    )

    unit_dir = project / "tests" / "unit"
    unit_dir.mkdir(parents=True)
    expected_unit = unit_dir / "test_managed_runtime_candidate.py"
    expected_unit.write_text(
        "from tensor_grep.cli import managed_runtime_candidate\n"
        "def test_install_staging_timeout():\n"
        "    assert managed_runtime_candidate._run_managed_candidate_install()\n",
        encoding="utf-8",
    )

    discovered = predictive_test_selector.discover_and_rank_tests_for_primary_file(
        cli_dir,
        str(primary),
        primary_symbol_name="_run_managed_candidate_install",
        query="fix managed runtime staging timeout",
        limit=5,
        repo_map_mod=repo_map,
    )

    assert discovered
    assert Path(discovered[0]).resolve() == expected_unit.resolve()


def test_unrelated_test_confidence_capped_below_0_55(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    cli_dir = project / "src" / "tensor_grep" / "cli"
    cli_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='tg'\nversion='0.1.0'\n", encoding="utf-8"
    )
    primary = cli_dir / "managed_runtime_candidate.py"
    primary.write_text("def _run_managed_candidate_install():\n    return True\n", encoding="utf-8")

    e2e_dir = project / "tests" / "e2e"
    e2e_dir.mkdir(parents=True)
    unrelated = e2e_dir / "test_routing_parity.py"
    unrelated.write_text(
        "def test_run_java_rewrite_keeps_baseline_refusal():\n    assert True\n",
        encoding="utf-8",
    )

    sym_conf = predictive_test_selector.calibrate_test_step_confidence(
        test_path=str(unrelated),
        scope="symbol",
        primary_file=str(primary),
        primary_symbol_name="_run_managed_candidate_install",
        query="fix managed runtime staging timeout",
        repo_map_mod=repo_map,
    )
    file_conf = predictive_test_selector.calibrate_test_step_confidence(
        test_path=str(unrelated),
        scope="file",
        primary_file=str(primary),
        primary_symbol_name="_run_managed_candidate_install",
        query="fix managed runtime staging timeout",
        repo_map_mod=repo_map,
    )
    assert sym_conf <= 0.55
    assert file_conf <= 0.55

    plan = repo_map._raw_validation_plan_for_tests(
        [str(unrelated)],
        repo_root=project,
        primary_test=str(unrelated),
        primary_symbol={"file": str(primary), "name": "_run_managed_candidate_install"},
        query="fix managed runtime staging timeout",
    )
    for step in plan:
        if step.get("target", "").endswith("test_routing_parity.py"):
            assert step["confidence"] <= 0.55


def test_subtree_invocation_walks_up_to_find_tests_unit_even_under_scan_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "repo"
    cli_dir = project / "src" / "tensor_grep" / "cli"
    cli_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='tg'\nversion='0.1.0'\n", encoding="utf-8"
    )
    primary = cli_dir / "managed_runtime_candidate.py"
    primary.write_text("def _run_managed_candidate_install():\n    return True\n", encoding="utf-8")

    e2e_dir = project / "tests" / "e2e"
    e2e_dir.mkdir(parents=True)
    for idx in range(15):
        (e2e_dir / f"test_e2e_{idx:02d}.py").write_text(
            "def test_routing():\n    # timeout\n    assert True\n",
            encoding="utf-8",
        )

    unit_dir = project / "tests" / "unit"
    unit_dir.mkdir(parents=True)
    expected_unit = unit_dir / "test_managed_runtime_candidate.py"
    expected_unit.write_text("def test_smoke():\n    assert True\n", encoding="utf-8")

    # Force a tiny scan limit so root walk hits tests/e2e/ first and would starve tests/unit/.
    monkeypatch.setattr(repo_map, "_VALIDATION_RUNNER_SCAN_LIMIT", 5)

    discovered = repo_map._discover_validation_tests_for_primary_file(
        cli_dir,
        str(primary),
        primary_symbol_name="_run_managed_candidate_install",
        query="fix managed runtime staging timeout",
        limit=3,
    )

    assert discovered
    assert Path(discovered[0]).resolve() == expected_unit.resolve()


def test_ts_string_and_url_comment_do_not_trigger_tier1_confidence(tmp_path: Path) -> None:
    from tensor_grep.cli.predictive_test_selector import calibrate_test_step_confidence

    src = tmp_path / "src" / "widget.ts"
    src.parent.mkdir(parents=True)
    src.write_text("export function target() { return 1; }\n", encoding="utf-8")

    string_only = tmp_path / "tests" / "independent_a.test.ts"
    string_only.parent.mkdir(parents=True)
    string_only.write_text(
        """const log = 'import { target } from "./widget"';\n""", encoding="utf-8"
    )

    url_comment = tmp_path / "tests" / "independent_b.test.ts"
    url_comment.write_text(
        'const url = "https://example.com"; // "\nconst label = "target";\n',
        encoding="utf-8",
    )

    real_import = tmp_path / "tests" / "real.test.ts"
    real_import.write_text('import { target } from "../src/widget";\n', encoding="utf-8")

    regex_only = tmp_path / "tests" / "regex_only.test.ts"
    regex_only.write_text("const p = /target/;\n", encoding="utf-8")

    template_call = tmp_path / "tests" / "template_call.test.ts"
    template_call.write_text("const label = `${({}).a ?? target()}`;\n", encoding="utf-8")

    division_call = tmp_path / "tests" / "division_call.test.ts"
    division_call.write_text("const n = total / target() / 2;\n", encoding="utf-8")

    escaped_template = tmp_path / "tests" / "escaped_template.test.ts"
    escaped_template.write_text(
        r"const label = `result: \${target()}`;" + '\nconst s = `${"target"}`;\n',
        encoding="utf-8",
    )

    assert (
        calibrate_test_step_confidence(
            test_path=str(division_call),
            scope="symbol",
            primary_file=str(src),
            primary_symbol_name="target",
            repo_map_mod=repo_map,
        )
        >= 0.90
    )
    assert (
        calibrate_test_step_confidence(
            test_path=str(escaped_template),
            scope="symbol",
            primary_file=str(src),
            primary_symbol_name="target",
            repo_map_mod=repo_map,
        )
        <= 0.55
    )

    assert (
        calibrate_test_step_confidence(
            test_path=str(regex_only),
            scope="symbol",
            primary_file=str(src),
            primary_symbol_name="target",
            repo_map_mod=repo_map,
        )
        <= 0.55
    )
    assert (
        calibrate_test_step_confidence(
            test_path=str(template_call),
            scope="symbol",
            primary_file=str(src),
            primary_symbol_name="target",
            repo_map_mod=repo_map,
        )
        >= 0.90
    )

    assert (
        calibrate_test_step_confidence(
            test_path=str(string_only),
            scope="symbol",
            primary_file=str(src),
            primary_symbol_name="target",
            repo_map_mod=repo_map,
        )
        <= 0.55
    )
    assert (
        calibrate_test_step_confidence(
            test_path=str(url_comment),
            scope="symbol",
            primary_file=str(src),
            primary_symbol_name="target",
            repo_map_mod=repo_map,
        )
        <= 0.55
    )
    assert (
        calibrate_test_step_confidence(
            test_path=str(real_import),
            scope="symbol",
            primary_file=str(src),
            primary_symbol_name="target",
            repo_map_mod=repo_map,
        )
        >= 0.90
    )
