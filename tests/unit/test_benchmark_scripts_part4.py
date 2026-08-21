import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BENCHMARK_JSON_SCRIPTS = [
    "benchmarks/run_benchmarks.py",
    "benchmarks/run_native_cpu_benchmarks.py",
    "benchmarks/run_hot_query_benchmarks.py",
    "benchmarks/run_ast_benchmarks.py",
    "benchmarks/run_ast_multilang_benchmarks.py",
    "benchmarks/run_ast_rewrite_benchmarks.py",
    "benchmarks/run_ast_workflow_benchmarks.py",
    "benchmarks/build_attempt_ledger.py",
    "benchmarks/run_gpu_benchmarks.py",
    "benchmarks/run_gpu_native_benchmarks.py",
    "benchmarks/run_harness_loop_benchmark.py",
    "benchmarks/run_ast_parity_check.py",
    "benchmarks/run_compat_checks.py",
    "benchmarks/run_index_scaling_benchmark.py",
    "benchmarks/run_context_render_benchmarks.py",
    "benchmarks/run_blast_radius_benchmarks.py",
    "benchmarks/run_provider_navigation_bakeoff.py",
    "benchmarks/run_session_benchmarks.py",
    "benchmarks/run_external_eval.py",
    "benchmarks/analyze_external_profiling.py",
    "benchmarks/normalize_competitor_eval.py",
    "benchmarks/render_patch_scorecard.py",
    "benchmarks/render_provider_navigation_scorecard.py",
    "benchmarks/render_world_class_report.py",
    "benchmarks/run_patch_bakeoff.py",
    "benchmarks/build_attempt_ledger.py",
    "benchmarks/run_tensor_grep_patch_driver.py",
    "benchmarks/run_gemini_patch_predictions.py",
    "benchmarks/run_copilot_patch_predictions.py",
    "benchmarks/run_claude_patch_predictions.py",
    "benchmarks/run_claude_skill_ab.py",
    "benchmarks/run_claude_skill_ab_matrix.py",
    "benchmarks/run_claude_competitor_eval.py",
    "benchmarks/run_codex_competitor_eval.py",
    "benchmarks/run_copilot_competitor_eval.py",
    "benchmarks/run_gemini_competitor_eval.py",
]


def _load_script_module(name: str, rel_path: str):
    root = Path(__file__).resolve().parents[2]
    module_path = root / rel_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _passing_native_gpu_scale_summary(module):
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 1.2},
            "tg_cpu": {"status": "PASS", "median_s": 1.0},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 0.5,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 1.4},
            "tg_cpu": {"status": "PASS", "median_s": 1.1},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 0.6,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
    ]
    correctness_checks = [
        {
            "size_label": size_label,
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
            "rg_matches_equal": True,
            "rg_files_equal": True,
            "rg_match_identity_equal": True,
        }
        for size_label in ("1GB", "5GB")
    ]
    return module.build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )


def _native_gpu_scale_summary_with_speed_failure(module):
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 1.0},
            "tg_cpu": {"status": "PASS", "median_s": 1.1},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 2.0,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 1.2},
            "tg_cpu": {"status": "PASS", "median_s": 1.3},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 2.4,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
    ]
    correctness_checks = [
        {
            "size_label": size_label,
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
            "rg_matches_equal": True,
            "rg_files_equal": True,
            "rg_match_identity_equal": True,
        }
        for size_label in ("1GB", "5GB")
    ]
    return module.build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )


def _passing_many_pattern_payload(module):
    patterns = list(module.DEFAULT_CORRECTNESS_PATTERNS)
    correctness_check = {
        "status": "PASS",
        "matches_equal": True,
        "files_equal": True,
        "rg_matches_equal": True,
        "rg_files_equal": True,
        "rg_match_identity_equal": True,
    }
    payload = {
        "status": "PASS",
        "workload_class": module.NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
        "fair_rg_baseline": "single_invocation_rg_fixed_multi_pattern",
        "patterns": patterns,
        "speedup_vs_cpu": 2.0,
        "speedup_vs_rg_multi_pattern": 1.5,
        "gpu_stats": {
            "pipeline": {
                "pattern_count": len(patterns),
                "single_dispatch": True,
            }
        },
        "correctness_check": correctness_check,
    }
    payload["proof_gate"] = module.build_many_pattern_proof_gate(
        multi_pattern=payload,
        correctness_check=correctness_check,
    )
    return payload


def test_real_patch_fixture_scenarios_should_load_and_score_oracle_predictions(tmp_path):
    driver_module = _load_script_module(
        "run_tensor_grep_patch_driver_real_fixture_script",
        "benchmarks/run_tensor_grep_patch_driver.py",
    )
    bakeoff_module = _load_script_module(
        "run_patch_bakeoff_real_fixture_script",
        "benchmarks/run_patch_bakeoff.py",
    )

    driver_scenarios = driver_module.load_driver_scenarios(
        Path("benchmarks/patch_eval/real_patch_driver_scenarios.json")
    )
    bakeoff_scenarios = bakeoff_module.load_patch_scenarios(
        Path("benchmarks/patch_eval/real_patch_bakeoff_scenarios.json")
    )

    assert len(driver_scenarios) == 12
    assert len(bakeoff_scenarios) == 12

    scenario_by_id = {scenario["instance_id"]: scenario for scenario in bakeoff_scenarios}

    def _pin_pytest_validation(instance_id: str, test_path: str) -> None:
        scenario_by_id[instance_id]["validation_commands"] = [
            f'"{sys.executable}" -m pytest {test_path} -q'
        ]

    def _build_git_patch(repo_root: Path, relative_path: str, updated_text: str) -> str:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            a_root = temp_root / "a"
            b_root = temp_root / "b"
            shutil.copytree(repo_root, a_root)
            shutil.copytree(repo_root, b_root)
            (b_root / relative_path).write_text(updated_text, encoding="utf-8")
            completed = subprocess.run(
                ["git", "diff", "--no-index", "--", f"a/{relative_path}", f"b/{relative_path}"],
                cwd=temp_root,
                capture_output=True,
                text=True,
                check=False,
            )
            patch = completed.stdout
            patch = patch.replace(
                f"diff --git a/a/{relative_path} b/b/{relative_path}",
                f"diff --git a/{relative_path} b/{relative_path}",
            )
            patch = patch.replace(f"--- a/a/{relative_path}", f"--- a/{relative_path}")
            patch = patch.replace(f"+++ b/b/{relative_path}", f"+++ b/{relative_path}")
            return patch

    def _materialize_broken_fixture_copy(
        repo_root: Path,
        relative_path: str,
        broken_text: str,
        instance_id: str,
    ) -> Path:
        broken_root = tmp_path / f"{instance_id}-broken"
        shutil.copytree(repo_root, broken_root)
        (broken_root / relative_path).write_text(broken_text, encoding="utf-8")
        scenario_by_id[instance_id]["repo_fixture"] = str(broken_root)
        return broken_root

    def _prepare_fixture_patch(
        repo_root: Path,
        relative_path: str,
        *,
        broken_snippet: str,
        fixed_snippet: str,
        instance_id: str,
    ) -> tuple[Path, str]:
        current_text = (repo_root / relative_path).read_text(encoding="utf-8")
        if broken_snippet in current_text:
            fixed_text = current_text.replace(broken_snippet, fixed_snippet, 1)
            return repo_root, fixed_text
        if fixed_snippet in current_text:
            broken_text = current_text.replace(fixed_snippet, broken_snippet, 1)
            broken_root = _materialize_broken_fixture_copy(
                repo_root, relative_path, broken_text, instance_id
            )
            return broken_root, current_text
        raise AssertionError(f"Fixture text did not contain expected snippet for {instance_id}")

    click_repo = Path("benchmarks/patch_fixtures/click_format_filename")
    click_patch_repo, click_fixed = _prepare_fixture_patch(
        click_repo,
        "src/click/utils.py",
        broken_snippet="        filename = os.fspath(filename)\n",
        fixed_snippet="        filename = os.path.basename(filename)\n",
        instance_id="click-format-filename-shorten",
    )
    click_patch = _build_git_patch(click_patch_repo, "src/click/utils.py", click_fixed)
    click_prediction = {
        "instance_id": "click-format-filename-shorten",
        "system": "oracle",
        "model_patch": click_patch,
        "actual_test_files": ["tests/test_utils.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    _pin_pytest_validation("click-format-filename-shorten", "tests/test_utils.py")
    commander_repo = Path("benchmarks/patch_fixtures/commander_human_readable_arg_name")
    commander_source = commander_repo / "lib/argument.js"
    commander_fixed = commander_source.read_text(encoding="utf-8")
    commander_broken = commander_fixed.replace(
        "  return arg.required ? '<' + nameOutput + '>' : '[' + nameOutput + ']';\n",
        "  return arg.required ? '[' + nameOutput + ']' : '<' + nameOutput + '>';\n",
        1,
    )
    commander_broken_repo = _materialize_broken_fixture_copy(
        commander_repo,
        "lib/argument.js",
        commander_broken,
        "commander-human-readable-arg-name",
    )
    commander_patch = _build_git_patch(commander_broken_repo, "lib/argument.js", commander_fixed)
    commander_prediction = {
        "instance_id": "commander-human-readable-arg-name",
        "system": "oracle",
        "model_patch": commander_patch,
        "actual_test_files": ["tests/argument.test.js"],
        "actual_validation_commands": ["node --test tests/argument.test.js"],
    }
    click_unstyle_repo = Path("benchmarks/patch_fixtures/click_unstyle_ansi")
    click_unstyle_patch_repo, click_unstyle_fixed = _prepare_fixture_patch(
        click_unstyle_repo,
        "src/click/_compat.py",
        broken_snippet=r're.compile(r"\x1b\[[0-9;]*m")',
        fixed_snippet=r're.compile(r"\x1b\[[0-9;?]*[A-Za-z]")',
        instance_id="click-unstyle-other-ansi",
    )
    click_unstyle_patch = _build_git_patch(
        click_unstyle_patch_repo, "src/click/_compat.py", click_unstyle_fixed
    )
    click_unstyle_prediction = {
        "instance_id": "click-unstyle-other-ansi",
        "system": "oracle",
        "model_patch": click_unstyle_patch,
        "actual_test_files": ["tests/test_termui.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    _pin_pytest_validation("click-unstyle-other-ansi", "tests/test_termui.py")
    commander_error_repo = Path("benchmarks/patch_fixtures/commander_invalid_argument_error")
    commander_error_source = commander_error_repo / "lib/error.js"
    commander_error_fixed = commander_error_source.read_text(encoding="utf-8")
    commander_error_broken = commander_error_fixed.replace(
        "    super(1, 'commander.invalidArgument', message);\n",
        "    super(1, 'commander.invalidOptionArgument', message);\n",
        1,
    )
    commander_error_broken_repo = _materialize_broken_fixture_copy(
        commander_error_repo,
        "lib/error.js",
        commander_error_broken,
        "commander-invalid-argument-error-code",
    )
    commander_error_patch = _build_git_patch(
        commander_error_broken_repo, "lib/error.js", commander_error_fixed
    )
    commander_error_prediction = {
        "instance_id": "commander-invalid-argument-error-code",
        "system": "oracle",
        "model_patch": commander_error_patch,
        "actual_test_files": ["tests/error.test.js"],
        "actual_validation_commands": ["node --test tests/error.test.js"],
    }
    click_secho_repo = Path("benchmarks/patch_fixtures/click_secho_non_text")
    click_secho_patch_repo, click_secho_fixed = _prepare_fixture_patch(
        click_secho_repo,
        "src/click/termui.py",
        broken_snippet="    if message is not None:\n",
        fixed_snippet="    if message is not None and not isinstance(message, (bytes, bytearray)):\n",
        instance_id="click-secho-bytes-pass-through",
    )
    click_secho_patch = _build_git_patch(
        click_secho_patch_repo, "src/click/termui.py", click_secho_fixed
    )
    click_secho_prediction = {
        "instance_id": "click-secho-bytes-pass-through",
        "system": "oracle",
        "model_patch": click_secho_patch,
        "actual_test_files": ["tests/test_termui.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    _pin_pytest_validation("click-secho-bytes-pass-through", "tests/test_termui.py")
    click_style_repo = Path("benchmarks/patch_fixtures/click_style_non_text")
    click_style_patch_repo, click_style_fixed = _prepare_fixture_patch(
        click_style_repo,
        "src/click/termui.py",
        broken_snippet="    bits: list[str] = []\n",
        fixed_snippet="    if not isinstance(text, str):\n        text = str(text)\n\n    bits: list[str] = []\n",
        instance_id="click-style-non-text-coercion",
    )
    click_style_patch = _build_git_patch(
        click_style_patch_repo, "src/click/termui.py", click_style_fixed
    )
    click_style_prediction = {
        "instance_id": "click-style-non-text-coercion",
        "system": "oracle",
        "model_patch": click_style_patch,
        "actual_test_files": ["tests/test_utils.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    _pin_pytest_validation("click-style-non-text-coercion", "tests/test_utils.py")
    click_abort_repo = Path("benchmarks/patch_fixtures/click_abort")
    click_abort_source = click_abort_repo / "src/click/core.py"
    click_abort_fixed = click_abort_source.read_text(encoding="utf-8")
    click_abort_broken = click_abort_fixed.replace(
        "        raise Abort()\n",
        '        raise RuntimeError("aborted")\n',
        1,
    )
    click_abort_broken_repo = _materialize_broken_fixture_copy(
        click_abort_repo,
        "src/click/core.py",
        click_abort_broken,
        "click-abort-raises-abort",
    )
    click_abort_patch = _build_git_patch(
        click_abort_broken_repo, "src/click/core.py", click_abort_fixed
    )
    click_abort_prediction = {
        "instance_id": "click-abort-raises-abort",
        "system": "oracle",
        "model_patch": click_abort_patch,
        "actual_test_files": ["tests/test_commands.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    _pin_pytest_validation("click-abort-raises-abort", "tests/test_commands.py")
    click_binary_repo = Path("benchmarks/patch_fixtures/click_get_binary_stream")
    click_binary_patch_repo, click_binary_fixed = _prepare_fixture_patch(
        click_binary_repo,
        "src/click/utils.py",
        broken_snippet="    opener = text_streams.get(name)\n",
        fixed_snippet="    opener = binary_streams.get(name)\n",
        instance_id="click-get-binary-stream-uses-binary-map",
    )
    click_binary_patch = _build_git_patch(
        click_binary_patch_repo, "src/click/utils.py", click_binary_fixed
    )
    click_binary_prediction = {
        "instance_id": "click-get-binary-stream-uses-binary-map",
        "system": "oracle",
        "model_patch": click_binary_patch,
        "actual_test_files": ["tests/test_utils.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    _pin_pytest_validation("click-get-binary-stream-uses-binary-map", "tests/test_utils.py")
    commander_strip_repo = Path("benchmarks/patch_fixtures/commander_strip_color")
    commander_strip_source = commander_strip_repo / "lib/help.js"
    commander_strip_fixed = commander_strip_source.read_text(encoding="utf-8")
    commander_strip_broken = commander_strip_fixed.replace(
        r"  const sgrPattern = /\x1b\[[0-9;]*m/g;",
        r"  const sgrPattern = /\x1b\[\d+(;\d+)*m/g;",
        1,
    )
    commander_strip_broken_repo = _materialize_broken_fixture_copy(
        commander_strip_repo,
        "lib/help.js",
        commander_strip_broken,
        "commander-strip-color-implicit-reset",
    )
    commander_strip_patch = _build_git_patch(
        commander_strip_broken_repo, "lib/help.js", commander_strip_fixed
    )
    commander_strip_prediction = {
        "instance_id": "commander-strip-color-implicit-reset",
        "system": "oracle",
        "model_patch": commander_strip_patch,
        "actual_test_files": ["tests/help.test.js"],
        "actual_validation_commands": ["node --test tests/help.test.js"],
    }
    commander_dual_repo = Path("benchmarks/patch_fixtures/commander_dual_options")
    commander_dual_source = commander_dual_repo / "lib/option.js"
    commander_dual_fixed = commander_dual_source.read_text(encoding="utf-8")
    commander_dual_broken = commander_dual_fixed.replace(
        "      if (this.positiveOptions.has(key)) {\n",
        "      if (!this.positiveOptions.has(key)) {\n",
        1,
    )
    commander_dual_broken_repo = _materialize_broken_fixture_copy(
        commander_dual_repo,
        "lib/option.js",
        commander_dual_broken,
        "commander-dual-options-unrelated-flags",
    )
    commander_dual_patch = _build_git_patch(
        commander_dual_broken_repo, "lib/option.js", commander_dual_fixed
    )
    commander_dual_prediction = {
        "instance_id": "commander-dual-options-unrelated-flags",
        "system": "oracle",
        "model_patch": commander_dual_patch,
        "actual_test_files": ["tests/options.dual-options.test.js"],
        "actual_validation_commands": ["node --test tests/options.dual-options.test.js"],
    }
    click_choice_repo = Path("benchmarks/patch_fixtures/click_choice_invalid_message")
    click_choice_patch_repo, click_choice_fixed = _prepare_fixture_patch(
        click_choice_repo,
        "src/click/types.py",
        broken_snippet='        choices_str = ", ".join(map(repr, self.choices))\n'
        '        raise ValueError(f"{value!r} is not one of {choices_str}.")\n',
        fixed_snippet="        raise ValueError(self.get_invalid_choice_message(value, ctx=ctx))\n\n"
        "    def get_invalid_choice_message(self, value: t.Any, ctx: t.Any) -> str:\n"
        '        choices_str = ", ".join(map(repr, self.choices))\n'
        '        return f"{value!r} is not one of {choices_str}."\n',
        instance_id="click-choice-invalid-message",
    )
    click_choice_patch = _build_git_patch(
        click_choice_patch_repo, "src/click/types.py", click_choice_fixed
    )
    click_choice_prediction = {
        "instance_id": "click-choice-invalid-message",
        "system": "oracle",
        "model_patch": click_choice_patch,
        "actual_test_files": ["tests/test_types.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    _pin_pytest_validation("click-choice-invalid-message", "tests/test_types.py")
    commander_color_repo = Path("benchmarks/patch_fixtures/commander_use_color")
    commander_color_source = commander_color_repo / "lib/command.js"
    commander_color_fixed = commander_color_source.read_text(encoding="utf-8")
    commander_color_broken = commander_color_fixed.replace(
        "function useColor() {\n"
        "  const noColor = process.env.NO_COLOR;\n"
        "  if (noColor !== undefined && noColor !== '') return false;\n"
        "\n"
        "  const forceColor = process.env.FORCE_COLOR;\n"
        "  const cliColorForce = process.env.CLICOLOR_FORCE;\n"
        "\n"
        "  if (forceColor !== undefined) {\n"
        "    if (forceColor === '0') return false;\n"
        "    return true;\n"
        "  }\n"
        "\n"
        "  if (cliColorForce !== undefined) {\n"
        "    if (cliColorForce === '0') return false;\n"
        "    return true;\n"
        "  }\n"
        "\n"
        "  return undefined;\n"
        "}\n",
        "function useColor() {\n"
        "  if (process.env.NO_COLOR !== undefined) return false;\n"
        "  if (process.env.FORCE_COLOR || process.env.CLICOLOR_FORCE) return true;\n"
        "  return undefined;\n"
        "}\n",
        1,
    )
    commander_color_broken_repo = _materialize_broken_fixture_copy(
        commander_color_repo,
        "lib/command.js",
        commander_color_broken,
        "commander-use-color-env-conventions",
    )
    commander_color_patch = _build_git_patch(
        commander_color_broken_repo, "lib/command.js", commander_color_fixed
    )
    commander_color_prediction = {
        "instance_id": "commander-use-color-env-conventions",
        "system": "oracle",
        "model_patch": commander_color_patch,
        "actual_test_files": ["tests/useColor.test.js"],
        "actual_validation_commands": ["node --test tests/useColor.test.js"],
    }

    payload = bakeoff_module.build_patch_bakeoff_payload(
        bakeoff_scenarios,
        [
            click_prediction,
            commander_prediction,
            click_unstyle_prediction,
            commander_error_prediction,
            click_secho_prediction,
            click_style_prediction,
            click_abort_prediction,
            click_binary_prediction,
            commander_strip_prediction,
            commander_dual_prediction,
            click_choice_prediction,
            commander_color_prediction,
        ],
    )

    assert payload["summary"]["scenario_count"] == 12
    assert payload["summary"]["mean_patch_applied_rate"] == 1.0
    assert payload["summary"]["mean_validation_pass_rate"] == 1.0
    assert payload["summary"]["mean_primary_file_hit_rate"] == 1.0


def test_render_world_class_report_should_include_baseline_competitor_and_provider_sections():
    module = _load_script_module(
        "render_world_class_report_script", "benchmarks/render_world_class_report.py"
    )
    external_eval = {
        "summary": {
            "scenario_count": 29,
            "mean_file_hit_rate": 1.0,
            "mean_span_hit_rate": 1.0,
            "mean_file_precision": 0.9,
            "mean_test_hit_rate": 0.7,
            "mean_validation_cmd_hit_rate": 1.0,
            "mean_false_positive_file_count": 1.2,
            "mean_context_token_count": 700.0,
        },
        "by_language": {
            "python": {"scenario_count": 10, "mean_file_precision": 0.72},
        },
    }
    profiling = {
        "dominant_phases": [
            {
                "name": "caller_scan",
                "elapsed_s": 5.0,
                "avg_elapsed_s": 0.2,
                "percent_total_elapsed": 25.0,
            }
        ]
    }
    provider_navigation = {
        "by_provider": {
            "native": {
                "scenario_count": 2,
                "mean_caller_hit_rate": 0.0,
                "mean_caller_precision": 0.0,
                "mean_test_hit_rate": 1.0,
            },
            "hybrid": {
                "scenario_count": 2,
                "mean_caller_hit_rate": 1.0,
                "mean_caller_precision": 1.0,
                "mean_test_hit_rate": 1.0,
            },
        }
    }
    competitor = {
        "by_system": {
            "tensor-grep": {
                "mean_overall_score": 0.9,
                "mean_primary_file_hit": 1.0,
                "mean_primary_span_hit": 1.0,
                "mean_wall_clock_seconds": 2.0,
            }
        }
    }

    report = module.render_world_class_report(
        external_eval=external_eval,
        profiling=profiling,
        provider_navigation=provider_navigation,
        competitor=competitor,
    )

    assert report.startswith("# World-Class Evaluation Report")
    assert "## External Baseline" in report
    assert "## Dominant Profiling Phases" in report
    assert "## Provider Hard Cases" in report
    assert "`hybrid`: caller_hit_rate=`1.0`" in report
    assert "## Competitor Summary" in report


def test_run_claude_competitor_eval_should_build_records_from_scenarios(tmp_path, monkeypatch):
    module = _load_script_module(
        "run_claude_competitor_eval_script", "benchmarks/run_claude_competitor_eval.py"
    )
    scenario_pack = tmp_path / "scenarios.json"
    scenario_pack.write_text(
        json.dumps({
            "scenarios": [
                {
                    "id": "demo",
                    "language": "python",
                    "category": "demo",
                    "description": "demo",
                    "repo_fixture": str(tmp_path),
                    "query_or_symbol": "symbol",
                    "mode": "blast-radius",
                    "expected_primary_file": "a.py",
                    "expected_primary_span": {"start_line": 1, "end_line": 2},
                    "expected_dependent_files": [],
                    "expected_suggested_edit_files": [],
                    "expected_test_files": [],
                    "expected_validation_commands_contain": [],
                }
            ]
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Proc",
            (),
            {
                "stdout": json.dumps({
                    "result": json.dumps({
                        "actual_primary_file": "a.py",
                        "actual_primary_span": {"start_line": 1, "end_line": 2},
                        "actual_dependent_files": [],
                        "actual_suggested_edit_files": [],
                        "actual_test_files": [],
                        "actual_validation_commands": ["pytest -q"],
                        "context_token_count": 123,
                        "notes": "ok",
                    })
                })
            },
        )(),
    )

    payload = module.build_payload(
        scenario_pack, model="sonnet", permission_mode="bypassPermissions"
    )

    assert payload["artifact"] == "claude_competitor_eval"
    assert payload["suite"] == "run_claude_competitor_eval"
    assert payload["records"][0]["system"] == "claude-code"
    assert payload["records"][0]["actual_primary_file"] == "a.py"


def test_run_codex_competitor_eval_should_build_records_from_scenarios(tmp_path, monkeypatch):
    module = _load_script_module(
        "run_codex_competitor_eval_script", "benchmarks/run_codex_competitor_eval.py"
    )
    scenario_pack = tmp_path / "scenarios.json"
    scenario_pack.write_text(
        json.dumps({
            "scenarios": [
                {
                    "id": "demo",
                    "language": "python",
                    "category": "demo",
                    "description": "demo",
                    "repo_fixture": str(tmp_path),
                    "query_or_symbol": "symbol",
                    "mode": "blast-radius",
                    "expected_primary_file": "a.py",
                    "expected_primary_span": {"start_line": 1, "end_line": 2},
                    "expected_dependent_files": [],
                    "expected_suggested_edit_files": [],
                    "expected_test_files": [],
                    "expected_validation_commands_contain": [],
                }
            ]
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "resolve_codex_binary", lambda: "codex")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Proc",
            (),
            {
                "stdout": "\n".join([
                    json.dumps({"type": "thread.started", "thread_id": "demo"}),
                    json.dumps({
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps({
                                "actual_primary_file": "a.py",
                                "actual_primary_span": {"start_line": 1, "end_line": 2},
                                "actual_dependent_files": [],
                                "actual_suggested_edit_files": [],
                                "actual_test_files": [],
                                "actual_validation_commands": ["pytest -q"],
                                "context_token_count": 123,
                                "notes": "ok",
                            }),
                        },
                    }),
                ])
            },
        )(),
    )

    payload = module.build_payload(scenario_pack, model="gpt-5-codex")

    assert payload["artifact"] == "codex_competitor_eval"
    assert payload["suite"] == "run_codex_competitor_eval"
    assert payload["records"][0]["system"] == "codex"
    assert payload["records"][0]["actual_primary_file"] == "a.py"


def test_run_codex_competitor_eval_should_cleanup_ephemeral_agents_file(tmp_path):
    module = _load_script_module(
        "run_codex_competitor_eval_cleanup_script", "benchmarks/run_codex_competitor_eval.py"
    )
    agents_path = tmp_path / "AGENTS.md"

    with module._ephemeral_repo_instructions(tmp_path):
        assert agents_path.exists()

    assert not agents_path.exists()


def test_run_bakeoff_should_pass_provider_to_blast_radius(monkeypatch, tmp_path):
    module = _load_script_module("run_bakeoff_provider_script", "benchmarks/run_bakeoff.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        module.repo_map,
        "build_symbol_blast_radius_render",
        lambda symbol, path, profile=False, semantic_provider="native": (
            captured.update({"symbol": symbol, "path": str(path), "provider": semantic_provider})
            or {
                "edit_plan_seed": {
                    "primary_file": "a.py",
                    "primary_span": {"start_line": 1, "end_line": 2},
                    "dependent_files": [],
                    "suggested_edits": [],
                    "validation_tests": [],
                    "validation_commands": [],
                },
                "tests": [],
                "token_estimate": 12,
                "semantic_provider": semantic_provider,
            }
        ),
    )

    result = module.run_scenario(
        {
            "repo_fixture": str(repo_root),
            "query_or_symbol": "create_invoice",
            "mode": "blast-radius",
            "expected_primary_file": "a.py",
            "expected_primary_span": {"start_line": 1, "end_line": 2},
            "expected_dependent_files": [],
            "expected_suggested_edit_files": [],
            "expected_test_files": [],
            "expected_validation_commands_contain": [],
        },
        provider="hybrid",
    )

    assert captured["provider"] == "hybrid"
    assert result["semantic_provider"] == "hybrid"


def test_run_provider_navigation_bakeoff_should_score_callers_and_tests() -> None:
    module = _load_script_module(
        "run_provider_navigation_bakeoff_score_script",
        "benchmarks/run_provider_navigation_bakeoff.py",
    )

    row = module.score_scenario(
        {
            "repo_fixture": "C:/repo",
            "query_or_symbol": "getchar",
            "expected_caller_files": ["termui.py"],
            "expected_test_files": ["tests/test_termui.py"],
        },
        {
            "actual_caller_files": ["C:/repo/termui.py"],
            "actual_test_files": ["C:/repo/tests/test_termui.py"],
            "semantic_provider": "hybrid",
        },
    )

    assert row["caller_hit_rate"] == 1.0
    assert row["caller_precision"] == 1.0
    assert row["test_hit_rate"] == 1.0
    assert row["semantic_provider"] == "hybrid"


def test_run_provider_navigation_bakeoff_should_normalize_windows_paths_on_non_windows_hosts() -> (
    None
):
    module = _load_script_module(
        "run_provider_navigation_bakeoff_windows_paths_script",
        "benchmarks/run_provider_navigation_bakeoff.py",
    )

    assert module._normalize_path("C:/repo/termui.py", Path("C:/repo")) == "termui.py"
    assert (
        module._normalize_path("C:/repo/tests/test_termui.py", Path("C:/repo"))
        == "tests/test_termui.py"
    )


def test_run_provider_navigation_bakeoff_should_build_payload_for_multiple_providers(
    tmp_path,
) -> None:
    module = _load_script_module(
        "run_provider_navigation_bakeoff_payload_script",
        "benchmarks/run_provider_navigation_bakeoff.py",
    )

    payload = module.build_payload(
        {"native": [{"caller_hit_rate": 0.0, "caller_precision": 0.0, "test_hit_rate": 0.0}]},
        providers=["native", "hybrid"],
        scenarios_path=tmp_path / "provider_hardcases.json",
    )

    assert payload["artifact"] == "bench_provider_navigation"
    assert payload["providers"] == ["native", "hybrid"]
    assert payload["by_provider"]["native"]["mean_caller_hit_rate"] == 0.0
    assert payload["by_provider"]["hybrid"]["scenario_count"] == 0


def test_run_external_eval_should_include_provider_in_payload(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_external_eval_provider_script", "benchmarks/run_external_eval.py"
    )
    manifest = {
        "manifest_path": "manifest.json",
        "packs": [{"name": "demo", "language": "python", "scenario_pack": "demo.json"}],
    }
    monkeypatch.setattr(
        module,
        "run_pack",
        lambda entry, profile=False, provider="native": {
            "name": entry["name"],
            "language": entry["language"],
            "scenario_pack": entry["scenario_pack"],
            "scenario_count": 1,
            "summary": {
                "scenario_count": 1,
                "mean_file_hit_rate": 1.0,
                "mean_file_precision": 1.0,
                "mean_span_hit_rate": 1.0,
                "mean_test_hit_rate": 1.0,
                "mean_validation_cmd_hit_rate": 1.0,
                "mean_context_token_count": 1.0,
                "mean_false_positive_file_count": 0.0,
            },
            "analysis": {
                "bucket_counts": {},
                "mean_file_precision": 1.0,
                "scenarios_with_false_positives": 0,
            },
            "rows": [
                {
                    "language": entry["language"],
                    "file_hit_rate": 1.0,
                    "file_precision": 1.0,
                    "span_hit_rate": 1.0,
                    "test_hit_rate": 1.0,
                    "validation_cmd_hit_rate": 1.0,
                    "context_token_count": 1,
                    "false_positive_files": [],
                }
            ],
            "payload": {},
        },
    )

    payload = module.build_external_eval_payload(manifest, provider="lsp")

    assert payload["semantic_provider"] == "lsp"


def test_run_patch_bakeoff_should_score_applied_patch_and_validation(tmp_path):
    module = _load_script_module("run_patch_bakeoff_script", "benchmarks/run_patch_bakeoff.py")
    repo_root = tmp_path / "repo"
    src_dir = repo_root / "src"
    tests_dir = repo_root / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(2) == 4\n",
        encoding="utf-8",
    )
    patch_text = "\n".join([
        "diff --git a/src/payments.py b/src/payments.py",
        "--- a/src/payments.py",
        "+++ b/src/payments.py",
        "@@ -1,2 +1,2 @@",
        " def create_invoice(total):",
        "-    return total + 1",
        "+    return total + 2",
        "",
    ])
    scenario = {
        "instance_id": "demo-1",
        "repo_fixture": str(repo_root),
        "expected_primary_file": "src/payments.py",
        "expected_primary_span": {"start_line": 1, "end_line": 2},
        "expected_changed_files": ["src/payments.py"],
        "expected_test_files": ["tests/test_payments.py"],
        "validation_commands": [
            "python -c \"import sys; sys.path.insert(0, 'src'); import payments; sys.exit(0 if payments.create_invoice(2) == 4 else 1)\""
        ],
        "expected_validation_commands_contain": ["python -c"],
    }
    prediction = {
        "instance_id": "demo-1",
        "system": "demo",
        "model_patch": patch_text,
        "actual_test_files": ["tests/test_payments.py"],
        "actual_validation_commands": ['python -c "..."'],
    }

    row = module.evaluate_prediction(scenario, prediction)

    assert row["patch_applied"] is True
    assert row["validation_passed"] is True
    assert row["primary_file_hit"] == 1.0
    assert row["primary_span_hit"] == 1.0
    assert row["changed_file_recall"] == 1.0
    assert row["changed_file_precision"] == 1.0
    assert row["predicted_test_hit_rate"] == 1.0
    assert row["predicted_validation_cmd_hit_rate"] == 1.0
    assert row["reason"] == "ok"


def test_run_patch_bakeoff_should_normalize_truncated_patch_before_apply(tmp_path):
    module = _load_script_module(
        "run_patch_bakeoff_truncated_script", "benchmarks/run_patch_bakeoff.py"
    )
    repo_root = tmp_path / "repo"
    src_dir = repo_root / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "demo.py").write_text(
        "def value():\n    return 'old'\n",
        encoding="utf-8",
    )
    scenario = {
        "instance_id": "demo-truncated",
        "repo_fixture": str(repo_root),
        "expected_primary_file": "src/demo.py",
        "expected_primary_span": {"start_line": 1, "end_line": 2},
        "expected_changed_files": ["src/demo.py"],
        "expected_test_files": [],
        "validation_commands": [],
        "expected_validation_commands_contain": [],
    }
    prediction = {
        "instance_id": "demo-truncated",
        "system": "demo",
        "model_patch": "\n".join([
            "diff --git a/src/demo.py b/src/demo.py",
            "--- a/src/demo.py",
            "+++ b/src/demo.py",
            "@@ -1,2 +1,2 @@",
            " def value():",
            "-    return 'old'",
            "+    return 'new'",
        ]),
        "actual_test_files": [],
        "actual_validation_commands": [],
    }

    row = module.evaluate_prediction(scenario, prediction)

    assert row["patch_applied"] is True
    assert row["primary_file_hit"] == 1.0
    assert row["primary_span_hit"] == 1.0
    assert row["reason"] == "ok"


def test_run_patch_bakeoff_should_classify_no_patch_and_timeout_reasons(tmp_path):
    module = _load_script_module(
        "run_patch_bakeoff_reason_script", "benchmarks/run_patch_bakeoff.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scenario = {
        "instance_id": "demo-timeout",
        "repo_fixture": str(repo_root),
        "expected_primary_file": "a.py",
        "expected_primary_span": {"start_line": 1, "end_line": 1},
        "expected_changed_files": ["a.py"],
        "expected_test_files": [],
        "validation_commands": [],
        "expected_validation_commands_contain": [],
    }

    no_patch_row = module.evaluate_prediction(
        scenario,
        {
            "instance_id": "demo-timeout",
            "system": "demo",
            "model_patch": "",
            "notes": "",
            "actual_validation_commands": [],
        },
    )
    timeout_row = module.evaluate_prediction(
        scenario,
        {
            "instance_id": "demo-timeout",
            "system": "demo",
            "model_patch": "",
            "notes": "timeout after 60s",
            "actual_validation_commands": [],
        },
    )

    assert no_patch_row["patch_applied"] is False
    assert no_patch_row["reason"] == "no patch emitted"
    assert timeout_row["patch_applied"] is False
    assert timeout_row["reason"] == "timeout after 60s"


def test_run_patch_bakeoff_should_build_summary_payload(tmp_path):
    module = _load_script_module(
        "run_patch_bakeoff_payload_script", "benchmarks/run_patch_bakeoff.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scenarios = [
        {
            "instance_id": "demo-1",
            "repo_fixture": str(repo_root),
            "expected_primary_file": "a.py",
            "expected_primary_span": {"start_line": 1, "end_line": 1},
            "expected_changed_files": ["a.py"],
            "expected_test_files": [],
            "validation_commands": [],
            "expected_validation_commands_contain": [],
        }
    ]
    predictions = [
        {
            "instance_id": "demo-1",
            "system": "demo",
            "model_patch": "",
            "notes": "",
            "actual_validation_commands": [],
        }
    ]

    payload = module.build_patch_bakeoff_payload(scenarios, predictions)

    assert payload["suite"] == "run_patch_bakeoff"
    assert payload["summary"]["scenario_count"] == 1
    assert payload["rows"][0]["system"] == "demo"
    assert payload["rows"][0]["reason"] == "no patch emitted"


def test_run_patch_bakeoff_should_build_attempt_ledger_payloads_by_instance(tmp_path):
    module = _load_script_module(
        "run_patch_bakeoff_ledger_script", "benchmarks/run_patch_bakeoff.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    payload = {
        "generated_at_epoch_s": 123.0,
        "rows": [
            {
                "instance_id": "demo-1",
                "system": "claude-baseline",
                "patch_applied": False,
                "validation_passed": False,
                "reason": "patch apply failed",
                "apply_error": "bad patch",
            },
            {
                "instance_id": "demo-1",
                "system": "claude-enhanced",
                "patch_applied": True,
                "validation_passed": True,
                "reason": "ok",
                "apply_error": "",
            },
            {
                "instance_id": "demo-2",
                "system": "gemini-enhanced",
                "patch_applied": False,
                "validation_passed": False,
                "reason": "timeout after 60s",
                "apply_error": "",
            },
        ],
    }
    scenarios = [
        {
            "instance_id": "demo-1",
            "repo_fixture": str(repo_root),
            "expected_primary_file": "a.py",
            "expected_primary_span": {"start_line": 1, "end_line": 1},
            "expected_changed_files": ["a.py"],
            "expected_test_files": [],
            "validation_commands": [],
            "expected_validation_commands_contain": [],
        },
        {
            "instance_id": "demo-2",
            "repo_fixture": str(repo_root),
            "expected_primary_file": "b.py",
            "expected_primary_span": {"start_line": 1, "end_line": 1},
            "expected_changed_files": ["b.py"],
            "expected_test_files": [],
            "validation_commands": [],
            "expected_validation_commands_contain": [],
        },
    ]

    ledgers = module.build_attempt_ledger_payloads(payload, scenarios)

    assert set(ledgers) == {"demo-1", "demo-2"}
    accepted = ledgers["demo-1"]
    assert accepted["artifact"] == "agent_attempt_ledger"
    assert accepted["task_id"] == "demo-1"
    assert accepted["root"] == str(repo_root)
    assert accepted["final_outcome"]["status"] == "accepted"
    assert accepted["final_outcome"]["accepted_attempt_id"] == "demo-1:claude-enhanced"
    assert accepted["attempts"][0]["status"] == "rejected"
    assert accepted["attempts"][1]["status"] == "accepted"
    assert accepted["attempts"][1]["validation_success"] is True
    rejected = ledgers["demo-2"]
    assert rejected["final_outcome"]["status"] == "rejected"
    assert rejected["final_outcome"]["accepted_attempt_id"] is None
    assert rejected["attempts"][0]["retry_reason"] == "timeout after 60s"


def test_run_patch_bakeoff_should_write_attempt_ledgers_when_requested(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_patch_bakeoff_ledger_cli_script", "benchmarks/run_patch_bakeoff.py"
    )
    output_path = tmp_path / "patch_bakeoff.json"
    ledger_dir = tmp_path / "attempt_ledgers"
    captured: list[tuple[Path, dict[str, object]]] = []
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    scenarios = [
        {
            "instance_id": "demo-1",
            "repo_fixture": str(repo_root),
            "expected_primary_file": "a.py",
            "expected_primary_span": {"start_line": 1, "end_line": 1},
            "expected_changed_files": ["a.py"],
            "expected_test_files": [],
            "validation_commands": [],
            "expected_validation_commands_contain": [],
        }
    ]
    predictions = [
        {
            "instance_id": "demo-1",
            "system": "claude-enhanced",
            "model_patch": "",
            "notes": "",
            "actual_validation_commands": [],
        }
    ]

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_patch_bakeoff.py",
            "--scenarios",
            str(tmp_path / "scenarios.json"),
            "--predictions",
            str(tmp_path / "predictions.json"),
            "--output",
            str(output_path),
            "--attempt-ledger-dir",
            str(ledger_dir),
        ],
    )
    monkeypatch.setattr(module, "load_patch_scenarios", lambda path: scenarios)
    monkeypatch.setattr(module, "load_patch_predictions", lambda path: predictions)

    def _fake_write_json(path: Path, payload: dict[str, object]) -> None:
        captured.append((Path(path), payload))

    monkeypatch.setattr(module, "write_json", _fake_write_json)

    exit_code = module.main()

    assert exit_code == 0
    assert captured[0][0] == output_path.resolve()
    assert captured[0][1]["artifact"] == "bench_patch_bakeoff"
    assert captured[1][0] == (ledger_dir / "demo-1.json").resolve()
    assert captured[1][1]["artifact"] == "agent_attempt_ledger"
    assert captured[1][1]["task_id"] == "demo-1"


def test_build_attempt_ledger_should_infer_final_outcome_and_retry_chain(tmp_path):
    module = _load_script_module(
        "build_attempt_ledger_script", "benchmarks/build_attempt_ledger.py"
    )
    payload = module.build_attempt_ledger_payload({
        "task_id": "tg-task-1",
        "root": str(tmp_path),
        "attempts": [
            {
                "attempt_id": "attempt-1",
                "status": "validation_failed",
                "retry_stage": "validation",
                "retry_reason": "lint-failed",
                "audit_manifest_path": "artifacts/audit/attempt-1.json",
            },
            {
                "attempt_id": "attempt-2",
                "parent_attempt_id": "attempt-1",
                "status": "accepted",
                "validation_success": True,
                "score_artifact": "artifacts/scores/attempt-2.json",
                "audit_manifest_path": "artifacts/audit/attempt-2.json",
            },
        ],
    })

    assert payload["artifact"] == "agent_attempt_ledger"
    assert payload["suite"] == "agent_loop"
    assert payload["final_outcome"]["status"] == "accepted"
    assert payload["final_outcome"]["accepted_attempt_id"] == "attempt-2"
    assert payload["replay"]["preserve_attempt_ids"] is True
    assert payload["replay"]["partial_retry_ledger"] == [
        {
            "attempt_id": "attempt-1",
            "resumed_from": "validation",
            "resumed_as": "attempt-2",
            "reason": "lint-failed",
        }
    ]
    assert payload["replay"]["audit_chain"] == [
        "artifacts/audit/attempt-1.json",
        "artifacts/audit/attempt-2.json",
    ]


def test_build_attempt_ledger_should_infer_multi_session_and_multi_task_replay(tmp_path):
    module = _load_script_module(
        "build_attempt_ledger_multitask_script", "benchmarks/build_attempt_ledger.py"
    )
    payload = module.build_attempt_ledger_payload({
        "task_id": "tg-task-1",
        "root": str(tmp_path),
        "tasks": [
            {"task_id": "tg-task-1", "status": "accepted", "accepted_attempt_id": "attempt-2"},
            {"task_id": "tg-task-2", "status": "accepted", "accepted_attempt_id": "attempt-3"},
        ],
        "attempts": [
            {
                "attempt_id": "attempt-1",
                "status": "validation_failed",
                "session_id": "session-a",
            },
            {
                "attempt_id": "attempt-2",
                "parent_attempt_id": "attempt-1",
                "status": "accepted",
                "session_id": "session-a",
            },
            {
                "attempt_id": "attempt-3",
                "parent_attempt_id": "attempt-2",
                "status": "accepted",
                "session_id": "session-b",
            },
        ],
    })

    assert payload["replay"]["multi_session"] is True
    assert payload["replay"]["handoff"]["from_session_id"] == "session-a"
    assert payload["replay"]["handoff"]["to_session_id"] == "session-b"
    assert payload["replay"]["multi_task"] is True
    assert payload["replay"]["task_chain"] == ["tg-task-1", "tg-task-2"]


def test_run_tensor_grep_patch_driver_should_build_patch_ready_records(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_tensor_grep_patch_driver_script", "benchmarks/run_tensor_grep_patch_driver.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    def fake_build_symbol_blast_radius_render(
        symbol,
        path,
        max_files=6,
        max_sources=6,
        max_symbols_per_file=6,
        semantic_provider="native",
    ):
        return {
            "semantic_provider": semantic_provider,
            "rendered_context": "def create_invoice(total):\n    return total + 1\n",
            "token_estimate": 42,
            "tests": ["tests/test_payments.py"],
            "edit_plan_seed": {
                "primary_file": "src/payments.py",
                "primary_span": {"start_line": 1, "end_line": 2},
                "dependent_files": ["src/service.py"],
                "suggested_edits": [{"file": "src/service.py"}],
                "validation_tests": ["tests/test_payments.py"],
                "validation_commands": ["pytest -q"],
            },
            "navigation_pack": {
                "primary_target": {
                    "file": "src/payments.py",
                    "mention_ref": "src/payments.py#L1-L2",
                    "role": "primary",
                },
                "follow_up_reads": [
                    {
                        "file": "src/service.py",
                        "mention_ref": "src/service.py#L1-L5",
                        "role": "related",
                    }
                ],
                "related_tests": ["tests/test_payments.py"],
                "validation_commands": ["pytest -q"],
                "edit_ordering": ["src/payments.py", "src/service.py"],
                "rollback_risk": "medium",
            },
        }

    monkeypatch.setattr(
        module.repo_map,
        "build_symbol_blast_radius_render",
        fake_build_symbol_blast_radius_render,
    )
    scenarios = [
        {
            "instance_id": "demo-1",
            "repo_fixture": str(repo_root),
            "query_or_symbol": "create_invoice",
            "mode": "blast-radius",
            "problem_statement": "Change create_invoice to add 2 instead of 1.",
        }
    ]

    payload = module.build_payload(scenarios, provider="hybrid")

    assert payload["suite"] == "run_tensor_grep_patch_driver"
    assert payload["semantic_provider"] == "hybrid"
    assert payload["records"][0]["actual_primary_file"] == "src/payments.py"
    assert payload["records"][0]["semantic_provider"] == "hybrid"
    assert payload["records"][0]["navigation_pack"]["primary_target"]["file"] == "src/payments.py"
    assert (
        payload["records"][0]["navigation_pack"]["follow_up_reads"][0]["file"] == "src/service.py"
    )
    assert payload["records"][0]["navigation_pack"]["related_tests"] == ["tests/test_payments.py"]
    prompt = payload["records"][0]["prompt"]
    assert "Prefer editing the repository files directly." in prompt
    assert "include diff --git headers" in prompt
    assert "Do not emit fragile one-line hunks." in prompt


def test_run_tensor_grep_patch_driver_should_fall_back_to_navigation_pack_when_edit_plan_seed_is_empty(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_tensor_grep_patch_driver_navigation_fallback_script",
        "benchmarks/run_tensor_grep_patch_driver.py",
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(
        module.repo_map,
        "build_context_render",
        lambda query, path, **kwargs: {
            "semantic_provider": "native",
            "rendered_context": "function HybridSearch() {}",
            "token_estimate": 21,
            "tests": [],
            "edit_plan_seed": {},
            "navigation_pack": {
                "primary_target": {
                    "file": "src/hybrid-search.cjs",
                    "start_line": 33,
                    "end_line": 171,
                    "mention_ref": "src/hybrid-search.cjs#L33-L171",
                },
                "follow_up_reads": [
                    {
                        "file": "src/vector-store.cjs",
                        "mention_ref": "src/vector-store.cjs#L21-L88",
                        "role": "related",
                    }
                ],
                "parallel_read_groups": [
                    {
                        "phase": 0,
                        "label": "primary",
                        "can_parallelize": False,
                        "mentions": ["src/hybrid-search.cjs#L33-L171"],
                        "files": ["src/hybrid-search.cjs"],
                        "roles": ["primary"],
                    }
                ],
                "related_tests": [],
                "validation_commands": [],
                "edit_ordering": ["src/hybrid-search.cjs"],
                "rollback_risk": 0.0,
            },
        },
    )
    scenarios = [
        {
            "instance_id": "demo-nav-1",
            "repo_fixture": str(repo_root),
            "query_or_symbol": "hybrid search",
            "mode": "context-render",
            "problem_statement": "Fix the hybrid search CLI.",
            "max_repo_files": 25,
        }
    ]

    payload = module.build_payload(scenarios, provider="native")

    assert payload["records"][0]["actual_primary_file"] == "src/hybrid-search.cjs"
    assert payload["records"][0]["actual_primary_span"] == {"start_line": 33, "end_line": 171}
    assert payload["records"][0]["actual_validation_commands"] == []
    prompt = payload["records"][0]["prompt"]
    assert "Do not run the test suite or create caches like .pytest_cache." in prompt


def test_run_tensor_grep_patch_driver_should_forward_max_repo_files_for_context_render(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_tensor_grep_patch_driver_max_repo_files_script",
        "benchmarks/run_tensor_grep_patch_driver.py",
    )
    seen: dict[str, object] = {}

    def _fake_build_context_render(query, path, **kwargs):
        seen["query"] = query
        seen["path"] = str(path)
        seen["max_repo_files"] = kwargs.get("max_repo_files")
        seen["include_edit_plan_seed"] = kwargs.get("include_edit_plan_seed")
        return {
            "semantic_provider": "hybrid",
            "rendered_context": "function demo() {}",
            "token_estimate": 12,
            "tests": [],
            "edit_plan_seed": {
                "primary_file": "src/demo.cjs",
                "primary_span": {"start_line": 1, "end_line": 1},
                "dependent_files": [],
                "suggested_edits": [{"file": "src/demo.cjs"}],
                "validation_tests": [],
                "validation_commands": ["npm test"],
            },
            "navigation_pack": {
                "primary_target": {"file": "src/demo.cjs", "mention_ref": "src/demo.cjs#L1-L1"},
                "follow_up_reads": [],
                "parallel_read_groups": [],
                "related_tests": [],
                "validation_commands": ["npm test"],
                "edit_ordering": ["src/demo.cjs"],
                "rollback_risk": 0.0,
            },
        }

    monkeypatch.setattr(module.repo_map, "build_context_render", _fake_build_context_render)
    scenarios = [
        {
            "instance_id": "demo-ctx-1",
            "repo_fixture": str(tmp_path),
            "query_or_symbol": "hybrid search daemon",
            "mode": "context-render",
            "problem_statement": "Fix the CLI.",
            "max_repo_files": 25,
        }
    ]

    payload = module.build_payload(scenarios, provider="hybrid")

    assert seen["query"] == "hybrid search daemon"
    assert seen["path"] == str(tmp_path)
    assert seen["max_repo_files"] == 25
    assert seen["include_edit_plan_seed"] is False
    assert payload["records"][0]["actual_validation_commands"] == ["npm test"]


def test_run_tensor_grep_patch_driver_should_build_attempt_ledger_from_records(tmp_path):
    module = _load_script_module(
        "run_tensor_grep_patch_driver_ledger_script", "benchmarks/run_tensor_grep_patch_driver.py"
    )
    payload = {
        "records": [
            {
                "instance_id": "demo-1",
                "repo_fixture": str(tmp_path),
                "prompt": "Fix it.",
            },
            {
                "instance_id": "demo-2",
                "repo_fixture": str(tmp_path),
                "prompt": "Fix it again.",
            },
        ]
    }

    ledger = module.build_attempt_ledger_for_payload(payload)

    assert ledger["artifact"] == "agent_attempt_ledger"
    assert ledger["suite"] == "agent_loop"
    assert ledger["final_outcome"]["status"] == "accepted"
    assert ledger["replay"]["multi_task"] is True
    assert ledger["replay"]["task_chain"] == ["demo-1", "demo-2"]
    assert ledger["attempts"][0]["attempt_id"] == "demo-1:tensor-grep"
    assert ledger["attempts"][1]["attempt_id"] == "demo-2:tensor-grep"


def test_run_tensor_grep_patch_driver_should_load_utf8_bom_scenarios(tmp_path):
    module = _load_script_module(
        "run_tensor_grep_patch_driver_bom_script", "benchmarks/run_tensor_grep_patch_driver.py"
    )
    scenarios_path = tmp_path / "driver_scenarios.json"
    payload = {
        "scenarios": [
            {
                "instance_id": "demo-1",
                "repo_fixture": str(tmp_path),
                "query_or_symbol": "create_invoice",
                "mode": "context-render",
                "problem_statement": "Fix create_invoice.",
            }
        ]
    }
    scenarios_path.write_text(json.dumps(payload), encoding="utf-8-sig")

    scenarios = module.load_driver_scenarios(scenarios_path)

    assert len(scenarios) == 1
    assert scenarios[0]["instance_id"] == "demo-1"


def test_patch_runner_common_should_ignore_ephemeral_files_when_diffing(tmp_path):
    module = _load_script_module("patch_runner_common_script", "benchmarks/patch_runner_common.py")
    before_root = tmp_path / "a"
    work_root = tmp_path / "b"
    (before_root / "src").mkdir(parents=True)
    (work_root / "src").mkdir(parents=True)
    (before_root / ".pytest_cache").mkdir()
    (work_root / ".pytest_cache").mkdir()
    (before_root / "src" / "demo.py").write_text("old\n", encoding="utf-8")
    (work_root / "src" / "demo.py").write_text("new\n", encoding="utf-8")
    (work_root / ".pytest_cache" / ".gitignore").write_text("*\n", encoding="utf-8")
    (work_root / "AGENTS.md").write_text("temp\n", encoding="utf-8")

    patch_text = module.derive_patch_from_repo_changes(before_root, work_root)

    assert "diff --git a/src/demo.py b/src/demo.py" in patch_text
    assert ".pytest_cache" not in patch_text
    assert "AGENTS.md" not in patch_text


def test_patch_runner_common_should_normalize_truncated_model_patch():
    module = _load_script_module(
        "patch_runner_common_normalize_script", "benchmarks/patch_runner_common.py"
    )
    patch_text = "\n".join([
        "diff --git a/src/demo.py b/src/demo.py",
        "index 1111111..2222222 100644",
        "--- a/src/demo.py",
        "+++ b/src/demo.py",
        "@@ -1,3 +1,3 @@",
        " line1",
        "-old",
        "+new",
        " line3",
    ])

    normalized = module.normalize_model_patch_text(patch_text)

    assert normalized.endswith("\n \n")


def test_run_gemini_patch_predictions_should_build_patch_records(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gemini_patch_predictions_script", "benchmarks/run_gemini_patch_predictions.py"
    )
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-1",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": ["tests/test_demo.py"],
                "actual_validation_commands": ["pytest -q"],
            }
        ]
    }
    monkeypatch.setattr(
        module,
        "_run_gemini_command",
        lambda *args, **kwargs: json.dumps({
            "response": "```diff\n"
            "diff --git a/demo.py b/demo.py\n"
            "--- a/demo.py\n"
            "+++ b/demo.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "```"
        }),
    )

    payload = module.build_payload(driver_payload, model="gemini-2.5-flash")

    assert payload["suite"] == "run_gemini_patch_predictions"
    assert payload["records"][0]["system"] == "gemini-cli"
    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert payload["records"][0]["actual_validation_commands"] == ["pytest -q"]


def test_run_gemini_patch_predictions_should_capture_timeout_as_empty_patch(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gemini_patch_predictions_timeout_script", "benchmarks/run_gemini_patch_predictions.py"
    )
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-timeout",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": [],
                "actual_validation_commands": [],
            }
        ]
    }

    def _raise_timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(cmd="gemini", timeout=5)

    monkeypatch.setattr(module, "_run_gemini_command", _raise_timeout)

    payload = module.build_payload(driver_payload, model="gemini-2.5-flash", timeout_seconds=5)

    assert payload["records"][0]["model_patch"] == ""
    assert payload["records"][0]["notes"] == "timeout after 5s"


def test_run_gemini_patch_predictions_should_fallback_to_repo_diff(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gemini_patch_predictions_diff_script", "benchmarks/run_gemini_patch_predictions.py"
    )
    (tmp_path / "demo.py").write_text("old\n", encoding="utf-8")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-diff",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": [],
                "actual_validation_commands": [],
            }
        ]
    }

    def _edit_repo(repo_root, prompt, **kwargs):
        del prompt, kwargs
        (repo_root / "demo.py").write_text("new\n", encoding="utf-8")
        return json.dumps({"response": "no diff emitted"})

    monkeypatch.setattr(module, "_run_gemini_command", _edit_repo)

    payload = module.build_payload(driver_payload, model="gemini-2.5-flash")

    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "old\n"


def test_run_gemini_patch_predictions_should_build_attempt_ledger_payloads_by_instance(tmp_path):
    module = _load_script_module(
        "run_gemini_patch_predictions_ledger_script",
        "benchmarks/run_gemini_patch_predictions.py",
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    driver_payload = {
        "records": [{"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "Fix one."}]
    }

    ledgers = module.build_attempt_ledger_payloads(
        driver_payload,
        [
            {
                "instance_id": "demo-1",
                "system": "gemini-cli",
                "model_patch": "",
                "notes": "timeout after 60s",
            }
        ],
    )

    ledger = ledgers["demo-1"]
    assert ledger["artifact"] == "agent_attempt_ledger"
    assert ledger["task_id"] == "demo-1"
    assert ledger["final_outcome"]["status"] == "needs_retry"
    assert ledger["replay"]["next_action"] == "score patch bakeoff"
    assert ledger["attempts"][0]["retry_reason"] == "timeout after 60s"


def test_run_gemini_patch_predictions_should_write_attempt_ledgers_when_requested(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gemini_patch_predictions_ledger_cli_script",
        "benchmarks/run_gemini_patch_predictions.py",
    )
    output_path = tmp_path / "gemini_predictions.json"
    ledger_dir = tmp_path / "attempt_ledgers"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    captured: list[tuple[Path, dict[str, object]]] = []

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_gemini_patch_predictions.py",
            "--input",
            str(tmp_path / "driver.json"),
            "--output",
            str(output_path),
            "--attempt-ledger-dir",
            str(ledger_dir),
        ],
    )
    monkeypatch.setattr(
        module,
        "load_driver_payload",
        lambda path: {
            "records": [
                {"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "Fix one."}
            ]
        },
    )
    monkeypatch.setattr(
        module,
        "build_payload",
        lambda *args, **kwargs: {
            "artifact": "gemini_patch_predictions",
            "suite": "run_gemini_patch_predictions",
            "generated_at_epoch_s": 1.0,
            "environment": {"platform": "windows"},
            "records": [
                {
                    "instance_id": "demo-1",
                    "system": "gemini-cli",
                    "model_patch": "",
                    "notes": "timeout after 60s",
                },
            ],
        },
    )

    def _fake_write_json(path: Path, payload: dict[str, object]) -> None:
        captured.append((Path(path), payload))

    monkeypatch.setattr(module, "write_json", _fake_write_json)

    exit_code = module.main()

    assert exit_code == 0
    assert captured[0][0] == output_path.resolve()
    assert captured[0][1]["artifact"] == "gemini_patch_predictions"
    assert captured[1][0] == (ledger_dir / "demo-1.json").resolve()
    assert captured[1][1]["artifact"] == "agent_attempt_ledger"


def test_run_gemini_patch_predictions_should_terminate_process_tree_on_timeout(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gemini_patch_predictions_kill_script", "benchmarks/run_gemini_patch_predictions.py"
    )
    calls: list[tuple[str, object]] = []

    class FakeProc:
        pid = 4242
        returncode = None

        def communicate(self, timeout=None):
            calls.append(("communicate", timeout))
            raise module.subprocess.TimeoutExpired(cmd="gemini", timeout=timeout)

        def kill(self):
            calls.append(("kill", None))

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            return 0

    monkeypatch.setattr(module, "resolve_gemini_binary", lambda: "gemini")
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (
            calls.append(("taskkill", (list(args[0]), kwargs.get("timeout"))))
            or type("Proc", (), {"returncode": 0})()
        ),
    )

    try:
        module._run_gemini_command(tmp_path, "prompt", model="gemini-2.5-flash", timeout_seconds=7)
    except module.subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("expected timeout")

    assert ("communicate", 7) in calls
    assert any(call[0] == "taskkill" and "/PID" in call[1][0] and call[1][1] == 5 for call in calls)


def test_run_gemini_patch_predictions_should_fallback_to_kill_when_taskkill_hangs(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gemini_patch_predictions_kill_fallback_script",
        "benchmarks/run_gemini_patch_predictions.py",
    )
    calls: list[tuple[str, object]] = []

    class FakeProc:
        pid = 4242
        returncode = None

        def kill(self):
            calls.append(("kill", None))

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            return 0

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            module.subprocess.TimeoutExpired(cmd="taskkill", timeout=5)
        ),
    )

    module._terminate_process_tree(FakeProc())

    assert ("kill", None) in calls
    assert ("wait", 5) in calls
