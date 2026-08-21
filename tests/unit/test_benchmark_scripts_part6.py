import importlib.util
import json
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


def test_run_claude_skill_ab_should_support_partial_resume(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_resume_script", "benchmarks/run_claude_skill_ab.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "demo.py").write_text("old\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8"
    )
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    output_path = tmp_path / "ab.json"

    seen: list[str] = []

    def _fake_run_ab_record(record, **kwargs):
        seen.append(str(record["instance_id"]))
        return (
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-baseline",
                    "model_patch": "",
                    "wall_clock_seconds": 1.0,
                },
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "model_patch": "diff --git a/x b/x",
                    "wall_clock_seconds": 2.0,
                },
            ],
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-baseline",
                    "response_shape": "analysis_only",
                },
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "response_shape": "analysis_then_patch",
                },
            ],
        )

    monkeypatch.setattr(module, "run_ab_record", _fake_run_ab_record)

    partial = module.build_partial_payload(
        [
            {
                "instance_id": "demo-1",
                "system": "claude-baseline",
                "model_patch": "",
                "wall_clock_seconds": 1.0,
            },
            {
                "instance_id": "demo-1",
                "system": "claude-enhanced",
                "model_patch": "diff --git a/x b/x",
                "wall_clock_seconds": 2.0,
            },
        ],
        [
            {
                "instance_id": "demo-1",
                "system": "claude-baseline",
                "response_shape": "analysis_only",
            },
            {
                "instance_id": "demo-1",
                "system": "claude-enhanced",
                "response_shape": "analysis_then_patch",
            },
        ],
        enhanced_output_contract="standard",
        enhanced_task_contract="engage",
    )
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_payload(
        {
            "records": [
                {"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "Fix one."},
                {"instance_id": "demo-2", "repo_fixture": str(repo_root), "prompt": "Fix two."},
            ]
        },
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        skill_dir=skill_dir,
        work_root=tmp_path / "work",
        enhanced_output_contract="standard",
        enhanced_task_contract="engage",
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-2"]
    assert len(payload["records"]) == 4
    assert len(payload["trace_records"]) == 4


def test_run_claude_skill_ab_should_resume_incomplete_instance_ids(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_incomplete_resume_script", "benchmarks/run_claude_skill_ab.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "demo.py").write_text("old\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8"
    )
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    output_path = tmp_path / "ab.json"

    seen: list[str] = []

    def _fake_run_ab_record(record, **kwargs):
        seen.append(str(record["instance_id"]))
        return (
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-baseline",
                    "model_patch": "",
                    "wall_clock_seconds": 1.0,
                },
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "model_patch": "diff --git a/x b/x",
                    "wall_clock_seconds": 2.0,
                },
            ],
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-baseline",
                    "response_shape": "analysis_only",
                },
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "response_shape": "analysis_then_patch",
                },
            ],
        )

    monkeypatch.setattr(module, "run_ab_record", _fake_run_ab_record)

    partial = module.build_partial_payload(
        [
            {
                "instance_id": "demo-1",
                "system": "claude-baseline",
                "model_patch": "",
                "wall_clock_seconds": 1.0,
            },
            {
                "instance_id": "demo-1",
                "system": "claude-enhanced",
                "model_patch": "diff --git a/x b/x",
                "wall_clock_seconds": 2.0,
            },
        ],
        [
            {
                "instance_id": "demo-1",
                "system": "claude-baseline",
                "response_shape": "analysis_only",
            },
        ],
        enhanced_output_contract="standard",
        enhanced_task_contract="engage",
    )
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_payload(
        {
            "records": [
                {"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "Fix one."},
                {"instance_id": "demo-2", "repo_fixture": str(repo_root), "prompt": "Fix two."},
            ]
        },
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        skill_dir=skill_dir,
        work_root=tmp_path / "work",
        enhanced_output_contract="standard",
        enhanced_task_contract="engage",
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-1", "demo-2"]
    assert len(payload["records"]) == 4
    assert len(payload["trace_records"]) == 4


def test_run_claude_skill_ab_should_checkpoint_per_record(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_checkpoint_script", "benchmarks/run_claude_skill_ab.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "demo.py").write_text("old\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8"
    )
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    output_path = tmp_path / "ab.json"

    monkeypatch.setattr(
        module,
        "run_ab_record",
        lambda record, **kwargs: (
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-baseline",
                    "model_patch": "",
                    "wall_clock_seconds": 1.0,
                },
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "model_patch": "diff --git a/x b/x",
                    "wall_clock_seconds": 2.0,
                },
            ],
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-baseline",
                    "response_shape": "analysis_only",
                },
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "response_shape": "analysis_then_patch",
                },
            ],
        ),
    )

    writes: list[int] = []

    def _fake_write_json(path, payload):
        if Path(path) == output_path:
            writes.append(len(payload["records"]))

    monkeypatch.setattr(module, "write_json", _fake_write_json)

    payload = module.build_payload(
        {
            "records": [
                {"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "Fix one."},
                {"instance_id": "demo-2", "repo_fixture": str(repo_root), "prompt": "Fix two."},
            ]
        },
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        skill_dir=skill_dir,
        work_root=tmp_path / "work",
        enhanced_output_contract="standard",
        enhanced_task_contract="engage",
        output_path=output_path,
        resume=False,
    )

    assert len(payload["records"]) == 4
    assert writes == [2, 4]


def test_run_claude_skill_ab_should_pass_prompt_as_positional_argument(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_command_script", "benchmarks/run_claude_skill_ab.py"
    )
    calls: list[list[str]] = []
    kwargs_calls: list[dict[str, object]] = []

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return ("ok", "")

    monkeypatch.setattr(module, "resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda: "C:/tools/tg.exe")
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda command, **kwargs: (
            calls.append(list(command)) or kwargs_calls.append(kwargs) or FakeProc()
        ),
    )

    output = module._run_claude_command(
        tmp_path,
        "Say hi in one word.",
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        extra_env={
            "PATH": "C:/tmp/bin",
            "TENSOR_GREP_REAL": "C:/tools/tg.exe",
            "TENSOR_GREP_TRACE_LOG": "C:/tmp/tg.jsonl",
        },
    )

    assert output == "ok"
    assert "--dangerously-skip-permissions" in calls[0]


def test_run_claude_skill_ab_matrix_should_build_experiment_configs():
    module = _load_script_module(
        "run_claude_skill_ab_matrix_configs_script", "benchmarks/run_claude_skill_ab_matrix.py"
    )

    experiments = module.build_experiment_configs(["standard", "done"], ["standard"], [""])

    assert experiments == [
        {
            "name": "output-standard__task-standard__effort-default",
            "enhanced_output_contract": "standard",
            "enhanced_task_contract": "standard",
            "enhanced_effort": "",
        },
        {
            "name": "output-done__task-standard__effort-default",
            "enhanced_output_contract": "done",
            "enhanced_task_contract": "standard",
            "enhanced_effort": "",
        },
    ]


def test_run_claude_skill_ab_matrix_should_summarize_trace_rows():
    module = _load_script_module(
        "run_claude_skill_ab_matrix_summary_script", "benchmarks/run_claude_skill_ab_matrix.py"
    )

    summary = module.summarize_trace_rows([
        {
            "system": "claude-baseline",
            "asked_meta_question": False,
            "response_shape": "analysis_then_patch",
            "first_tg_seconds": None,
            "first_patch_seconds": 10.0,
            "first_file_change_seconds": 0.2,
            "post_edit_deliberation_seconds": 9.8,
            "tg_invocation_count": 0,
            "tg_seconds_total": 0.0,
            "changed_file_count": 1,
        },
        {
            "system": "claude-enhanced",
            "asked_meta_question": True,
            "response_shape": "meta_question",
            "first_tg_seconds": 1.5,
            "first_patch_seconds": None,
            "first_file_change_seconds": None,
            "post_edit_deliberation_seconds": None,
            "tg_invocation_count": 2,
            "tg_seconds_total": 0.75,
            "changed_file_count": 0,
        },
        {
            "system": "claude-enhanced",
            "asked_meta_question": False,
            "response_shape": "analysis_then_patch",
            "first_tg_seconds": 1.0,
            "first_patch_seconds": 20.0,
            "first_file_change_seconds": 0.1,
            "post_edit_deliberation_seconds": 19.9,
            "tg_invocation_count": 1,
            "tg_seconds_total": 0.25,
            "changed_file_count": 1,
        },
    ])

    assert summary["claude-baseline"]["record_count"] == 1
    assert summary["claude-baseline"]["response_shape_counts"] == {"analysis_then_patch": 1}
    assert summary["claude-enhanced"]["record_count"] == 2
    assert summary["claude-enhanced"]["meta_question_rate"] == 0.5
    assert summary["claude-enhanced"]["mean_first_tg_seconds"] == 1.25
    assert summary["claude-enhanced"]["mean_tg_invocation_count"] == 1.5
    assert summary["claude-enhanced"]["mean_post_edit_deliberation_seconds"] == 19.9


def test_run_claude_skill_ab_matrix_should_build_payload(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_matrix_payload_script", "benchmarks/run_claude_skill_ab_matrix.py"
    )
    driver_path = tmp_path / "driver.json"
    scenarios_path = tmp_path / "scenarios.json"
    driver_path.write_text(json.dumps({"records": [{"instance_id": "demo-1"}]}), encoding="utf-8")
    scenarios_path.write_text(
        json.dumps({"scenarios": [{"instance_id": "demo-1"}]}), encoding="utf-8"
    )

    monkeypatch.setattr(
        module.ab_runner,
        "load_driver_payload",
        lambda path: {"records": [{"instance_id": "demo-1", "prompt": "Fix it."}]},
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "load_patch_scenarios",
        lambda path: [{"instance_id": "demo-1", "repo_fixture": "x"}],
    )

    monkeypatch.setattr(
        module.ab_runner,
        "run_ab_record",
        lambda record, **kwargs: (
            [
                {
                    "instance_id": "demo-1",
                    "system": "claude-baseline",
                    "model_patch": "",
                    "wall_clock_seconds": 10.0,
                },
                {
                    "instance_id": "demo-1",
                    "system": "claude-enhanced",
                    "model_patch": "diff --git a/x b/x",
                    "wall_clock_seconds": 20.0,
                },
            ],
            [
                {
                    "instance_id": "demo-1",
                    "system": "claude-baseline",
                    "response_shape": "analysis_only",
                    "asked_meta_question": False,
                    "tg_invocation_count": 0,
                    "tg_seconds_total": 0.0,
                    "changed_file_count": 0,
                    "first_tg_seconds": None,
                    "first_patch_seconds": None,
                    "first_file_change_seconds": None,
                    "post_edit_deliberation_seconds": None,
                },
                {
                    "instance_id": "demo-1",
                    "system": "claude-enhanced",
                    "response_shape": "analysis_then_patch",
                    "asked_meta_question": False,
                    "tg_invocation_count": 1,
                    "tg_seconds_total": 0.1,
                    "changed_file_count": 1,
                    "first_tg_seconds": 0.5,
                    "first_patch_seconds": 5.0,
                    "first_file_change_seconds": 0.1,
                    "post_edit_deliberation_seconds": 4.9,
                },
            ],
        ),
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "evaluate_prediction",
        lambda scenario, prediction: {
            "instance_id": "demo-1",
            "system": str(prediction["system"]),
            "patch_applied": prediction["system"] == "claude-enhanced",
            "validation_passed": prediction["system"] == "claude-enhanced",
            "primary_file_hit": float(prediction["system"] == "claude-enhanced"),
            "primary_span_hit": float(prediction["system"] == "claude-enhanced"),
        },
    )

    payload = module.build_matrix_payload(
        input_path=driver_path,
        scenarios_path=scenarios_path,
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=30,
        skill_dir=tmp_path / "skill",
        work_root=tmp_path / "work",
        limit=1,
        output_contracts=["standard"],
        task_contracts=["engage"],
        enhanced_efforts=["low"],
    )

    assert payload["artifact"] == "claude_skill_ab_matrix"
    assert payload["experiment_count"] == 1
    experiment = payload["experiments"][0]
    assert experiment["name"] == "output-standard__task-engage__effort-low"
    assert experiment["enhanced_effort"] == "low"
    assert experiment["trace_summary"]["claude-enhanced"]["mean_first_tg_seconds"] == 0.5
    assert experiment["bakeoff_summary"]["scenario_count"] == 2
    assert experiment["system_score_summary"]["claude-enhanced"]["mean_patch_applied_rate"] == 1.0


def test_run_claude_skill_ab_matrix_should_support_partial_and_resume(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_matrix_resume_script", "benchmarks/run_claude_skill_ab_matrix.py"
    )
    driver_path = tmp_path / "driver.json"
    scenarios_path = tmp_path / "scenarios.json"
    output_path = tmp_path / "matrix.json"
    driver_path.write_text(
        json.dumps({"records": [{"instance_id": "demo-1"}, {"instance_id": "demo-2"}]}),
        encoding="utf-8",
    )
    scenarios_path.write_text(
        json.dumps({"scenarios": [{"instance_id": "demo-1"}, {"instance_id": "demo-2"}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        module.ab_runner,
        "load_driver_payload",
        lambda path: {
            "records": [
                {"instance_id": "demo-1", "prompt": "Fix it."},
                {"instance_id": "demo-2", "prompt": "Fix it."},
            ]
        },
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "load_patch_scenarios",
        lambda path: [
            {"instance_id": "demo-1", "repo_fixture": "x"},
            {"instance_id": "demo-2", "repo_fixture": "x"},
        ],
    )

    seen: list[tuple[str, str]] = []

    def _fake_build_payload(*_args, **kwargs):
        raise AssertionError("_fake_build_payload should not be used")

    monkeypatch.setattr(
        module.ab_runner,
        "run_ab_record",
        lambda record, **kwargs: (
            seen.append(str(record["instance_id"]))
            or [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "model_patch": "diff --git a/x b/x",
                    "wall_clock_seconds": 20.0,
                }
            ],
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "response_shape": "analysis_then_patch",
                    "asked_meta_question": False,
                    "tg_invocation_count": 0,
                    "tg_seconds_total": 0.0,
                    "changed_file_count": 1,
                    "first_tg_seconds": None,
                    "first_patch_seconds": 5.0,
                    "first_file_change_seconds": 0.1,
                    "post_edit_deliberation_seconds": 4.9,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "evaluate_prediction",
        lambda scenario, prediction: {
            "instance_id": str(prediction["instance_id"]),
            "system": str(prediction["system"]),
            "patch_applied": True,
            "validation_passed": True,
            "primary_file_hit": 1.0,
            "primary_span_hit": 1.0,
        },
    )

    partial = module.build_partial_payload([])
    partial["experiments"].append({
        "name": "output-standard__task-standard__effort-default",
        "enhanced_output_contract": "standard",
        "enhanced_task_contract": "standard",
        "enhanced_effort": "",
        "prediction_records": [
            {
                "instance_id": "demo-1",
                "system": "claude-enhanced",
                "model_patch": "diff --git a/x b/x",
            }
        ],
        "trace_records": [
            {
                "instance_id": "demo-1",
                "system": "claude-enhanced",
                "response_shape": "analysis_then_patch",
            }
        ],
        "bakeoff_rows": [
            {
                "instance_id": "demo-1",
                "system": "claude-enhanced",
                "patch_applied": True,
                "validation_passed": True,
            }
        ],
        "prediction_record_count": 1,
        "trace_record_count": 1,
        "trace_summary": {"claude-enhanced": {"meta_question_rate": 1.0}},
        "bakeoff_summary": {"scenario_count": 1},
        "system_score_summary": {"claude-enhanced": {"mean_patch_applied_rate": 1.0}},
    })
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_matrix_payload(
        input_path=driver_path,
        scenarios_path=scenarios_path,
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=30,
        skill_dir=tmp_path / "skill",
        work_root=tmp_path / "work",
        limit=2,
        output_contracts=["standard"],
        task_contracts=["standard"],
        enhanced_efforts=[""],
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-1", "demo-2"]
    assert payload["experiment_count"] == 1
    assert [experiment["name"] for experiment in payload["experiments"]] == [
        "output-standard__task-standard__effort-default",
    ]


def test_run_claude_skill_ab_matrix_should_resume_incomplete_experiment_instance_ids(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_claude_skill_ab_matrix_incomplete_resume_script",
        "benchmarks/run_claude_skill_ab_matrix.py",
    )
    driver_path = tmp_path / "driver.json"
    scenarios_path = tmp_path / "scenarios.json"
    output_path = tmp_path / "matrix.json"
    driver_path.write_text(
        json.dumps({"records": [{"instance_id": "demo-1"}, {"instance_id": "demo-2"}]}),
        encoding="utf-8",
    )
    scenarios_path.write_text(
        json.dumps({"scenarios": [{"instance_id": "demo-1"}, {"instance_id": "demo-2"}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        module.ab_runner,
        "load_driver_payload",
        lambda path: {
            "records": [
                {"instance_id": "demo-1", "prompt": "Fix it."},
                {"instance_id": "demo-2", "prompt": "Fix it."},
            ]
        },
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "load_patch_scenarios",
        lambda path: [
            {"instance_id": "demo-1", "repo_fixture": "x"},
            {"instance_id": "demo-2", "repo_fixture": "x"},
        ],
    )

    seen: list[str] = []

    monkeypatch.setattr(
        module.ab_runner,
        "run_ab_record",
        lambda record, **kwargs: (
            seen.append(str(record["instance_id"]))
            or [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-baseline",
                    "model_patch": "",
                    "wall_clock_seconds": 10.0,
                },
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "model_patch": "diff --git a/x b/x",
                    "wall_clock_seconds": 20.0,
                },
            ],
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-baseline",
                    "response_shape": "analysis_only",
                    "asked_meta_question": False,
                    "tg_invocation_count": 0,
                    "tg_seconds_total": 0.0,
                    "changed_file_count": 0,
                    "first_tg_seconds": None,
                    "first_patch_seconds": None,
                    "first_file_change_seconds": None,
                    "post_edit_deliberation_seconds": None,
                },
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "response_shape": "analysis_then_patch",
                    "asked_meta_question": False,
                    "tg_invocation_count": 1,
                    "tg_seconds_total": 0.1,
                    "changed_file_count": 1,
                    "first_tg_seconds": 0.5,
                    "first_patch_seconds": 5.0,
                    "first_file_change_seconds": 0.1,
                    "post_edit_deliberation_seconds": 4.9,
                },
            ],
        ),
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "evaluate_prediction",
        lambda scenario, prediction: {
            "instance_id": str(prediction["instance_id"]),
            "system": str(prediction["system"]),
            "patch_applied": True,
            "validation_passed": True,
            "primary_file_hit": 1.0,
            "primary_span_hit": 1.0,
        },
    )

    partial = module.build_partial_payload([])
    partial["experiments"].append({
        "name": "output-standard__task-standard__effort-default",
        "enhanced_output_contract": "standard",
        "enhanced_task_contract": "standard",
        "enhanced_effort": "",
        "prediction_records": [
            {
                "instance_id": "demo-1",
                "system": "claude-baseline",
                "model_patch": "",
                "wall_clock_seconds": 10.0,
            },
            {
                "instance_id": "demo-1",
                "system": "claude-enhanced",
                "model_patch": "diff --git a/x b/x",
                "wall_clock_seconds": 20.0,
            },
        ],
        "trace_records": [
            {
                "instance_id": "demo-1",
                "system": "claude-baseline",
                "response_shape": "analysis_only",
            }
        ],
        "bakeoff_rows": [
            {
                "instance_id": "demo-1",
                "system": "claude-baseline",
                "patch_applied": False,
                "validation_passed": False,
            }
        ],
        "prediction_record_count": 2,
        "trace_record_count": 1,
        "trace_summary": {"claude-baseline": {"record_count": 1}},
        "bakeoff_summary": {"scenario_count": 1},
        "system_score_summary": {"claude-baseline": {"record_count": 1}},
    })
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_matrix_payload(
        input_path=driver_path,
        scenarios_path=scenarios_path,
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=30,
        skill_dir=tmp_path / "skill",
        work_root=tmp_path / "work",
        limit=2,
        output_contracts=["standard"],
        task_contracts=["standard"],
        enhanced_efforts=[""],
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-1", "demo-2"]
    assert payload["experiment_count"] == 1


def test_run_claude_skill_ab_matrix_should_write_checkpoint_per_experiment(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_matrix_checkpoint_script", "benchmarks/run_claude_skill_ab_matrix.py"
    )
    driver_path = tmp_path / "driver.json"
    scenarios_path = tmp_path / "scenarios.json"
    output_path = tmp_path / "matrix.json"
    driver_path.write_text(json.dumps({"records": [{"instance_id": "demo-1"}]}), encoding="utf-8")
    scenarios_path.write_text(
        json.dumps({"scenarios": [{"instance_id": "demo-1"}]}), encoding="utf-8"
    )

    monkeypatch.setattr(
        module.ab_runner,
        "load_driver_payload",
        lambda path: {"records": [{"instance_id": "demo-1"}]},
    )
    monkeypatch.setattr(
        module.patch_bakeoff, "load_patch_scenarios", lambda path: [{"instance_id": "demo-1"}]
    )
    monkeypatch.setattr(
        module.ab_runner,
        "run_ab_record",
        lambda *_args, **_kwargs: ([], []),
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "evaluate_prediction",
        lambda scenario, prediction: {
            "instance_id": "demo-1",
            "system": "claude-enhanced",
            "patch_applied": True,
            "validation_passed": True,
        },
    )

    writes: list[int] = []

    def _fake_write_json(path, payload):
        if Path(path) == output_path:
            writes.append(int(payload["experiment_count"]))

    monkeypatch.setattr(module, "write_json", _fake_write_json)

    payload = module.build_matrix_payload(
        input_path=driver_path,
        scenarios_path=scenarios_path,
        model="",
        permission_mode="bypassPermissions",
        timeout_seconds=30,
        skill_dir=tmp_path / "skill",
        work_root=tmp_path / "work",
        limit=1,
        output_contracts=["standard", "terse"],
        task_contracts=["standard"],
        enhanced_efforts=[""],
        output_path=output_path,
        resume=False,
    )

    assert payload["experiment_count"] == 2
    assert writes == [1, 2]


def test_run_claude_skill_ab_matrix_should_checkpoint_per_record_and_resume(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_matrix_record_resume_script",
        "benchmarks/run_claude_skill_ab_matrix.py",
    )
    driver_path = tmp_path / "driver.json"
    scenarios_path = tmp_path / "scenarios.json"
    output_path = tmp_path / "matrix.json"
    driver_path.write_text(
        json.dumps({
            "records": [
                {"instance_id": "demo-1"},
                {"instance_id": "demo-2"},
            ]
        }),
        encoding="utf-8",
    )
    scenarios_path.write_text(
        json.dumps({
            "scenarios": [
                {"instance_id": "demo-1"},
                {"instance_id": "demo-2"},
            ]
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        module.ab_runner,
        "load_driver_payload",
        lambda path: {"records": [{"instance_id": "demo-1"}, {"instance_id": "demo-2"}]},
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "load_patch_scenarios",
        lambda path: [{"instance_id": "demo-1"}, {"instance_id": "demo-2"}],
    )

    seen: list[str] = []

    def _fake_run_ab_record(record, **_kwargs):
        seen.append(str(record["instance_id"]))
        return (
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "model_patch": "diff --git a/x b/x",
                }
            ],
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "response_shape": "analysis_then_patch",
                }
            ],
        )

    monkeypatch.setattr(module.ab_runner, "run_ab_record", _fake_run_ab_record)

    def _fake_evaluate(scenario, prediction):
        return {
            "instance_id": str(scenario["instance_id"]),
            "system": str(prediction["system"]),
            "patch_applied": True,
            "validation_passed": True,
            "primary_file_hit": 1.0,
            "primary_span_hit": 1.0,
        }

    monkeypatch.setattr(module.patch_bakeoff, "evaluate_prediction", _fake_evaluate)

    writes: list[tuple[int, int]] = []

    def _fake_write_json(path, payload):
        if Path(path) == output_path:
            writes.append((
                int(payload["experiment_count"]),
                len(payload["experiments"][0]["prediction_records"]),
            ))

    monkeypatch.setattr(module, "write_json", _fake_write_json)

    partial = module.build_partial_payload([
        {
            "name": "output-standard__task-standard",
            "enhanced_output_contract": "standard",
            "enhanced_task_contract": "standard",
            "prediction_records": [
                {
                    "instance_id": "demo-1",
                    "system": "claude-enhanced",
                    "model_patch": "diff --git a/x b/x",
                }
            ],
            "trace_records": [
                {
                    "instance_id": "demo-1",
                    "system": "claude-enhanced",
                    "response_shape": "analysis_then_patch",
                }
            ],
            "bakeoff_rows": [
                {
                    "instance_id": "demo-1",
                    "system": "claude-enhanced",
                    "patch_applied": True,
                    "validation_passed": True,
                }
            ],
            "prediction_record_count": 1,
            "trace_record_count": 1,
            "trace_summary": {"claude-enhanced": {"record_count": 1}},
            "bakeoff_summary": {"scenario_count": 1},
            "system_score_summary": {"claude-enhanced": {"record_count": 1}},
        }
    ])
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_matrix_payload(
        input_path=driver_path,
        scenarios_path=scenarios_path,
        model="",
        permission_mode="bypassPermissions",
        timeout_seconds=30,
        skill_dir=tmp_path / "skill",
        work_root=tmp_path / "work",
        limit=2,
        output_contracts=["standard"],
        task_contracts=["standard"],
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-1", "demo-2"]
    experiment = payload["experiments"][0]
    assert [row["instance_id"] for row in experiment["prediction_records"]] == ["demo-1", "demo-2"]
    assert experiment["prediction_record_count"] == 2
    assert writes == [(1, 1), (1, 2)]


def test_render_claude_skill_ab_matrix_should_render_markdown(tmp_path):
    module = _load_script_module(
        "render_claude_skill_ab_matrix_script", "benchmarks/render_claude_skill_ab_matrix.py"
    )
    payload_path = tmp_path / "matrix.json"
    payload_path.write_text(
        json.dumps({
            "artifact": "claude_skill_ab_matrix",
            "experiments": [
                {
                    "name": "output-standard__task-standard",
                    "enhanced_output_contract": "standard",
                    "enhanced_task_contract": "standard",
                    "system_score_summary": {
                        "claude-enhanced": {
                            "mean_patch_applied_rate": 0.0,
                            "mean_validation_pass_rate": 0.0,
                        }
                    },
                    "trace_summary": {
                        "claude-enhanced": {
                            "meta_question_rate": 1.0,
                            "mean_post_edit_deliberation_seconds": None,
                            "mean_first_tg_seconds": None,
                        }
                    },
                },
                {
                    "name": "output-terse__task-standard",
                    "enhanced_output_contract": "terse",
                    "enhanced_task_contract": "standard",
                    "system_score_summary": {
                        "claude-enhanced": {
                            "mean_patch_applied_rate": 1.0,
                            "mean_validation_pass_rate": 1.0,
                        }
                    },
                    "trace_summary": {
                        "claude-enhanced": {
                            "meta_question_rate": 0.0,
                            "mean_post_edit_deliberation_seconds": 41.545078,
                            "mean_first_tg_seconds": None,
                        }
                    },
                },
            ],
        }),
        encoding="utf-8",
    )

    markdown = module.render_markdown([payload_path])

    assert "# Claude Skill A/B Matrix" in markdown
    assert "output-terse__task-standard" in markdown
    assert "Recommended Next Default Probe" in markdown
    assert "meta_question_rate=`0.0`" in markdown


def test_render_provider_navigation_scorecard_should_render_ranked_markdown(tmp_path):
    module = _load_script_module(
        "render_provider_navigation_scorecard_script",
        "benchmarks/render_provider_navigation_scorecard.py",
    )
    payload_path = tmp_path / "provider.json"
    payload_path.write_text(
        json.dumps({
            "artifact": "bench_provider_navigation",
            "providers": ["native", "hybrid"],
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
            },
        }),
        encoding="utf-8",
    )

    markdown = module.render_markdown(payload_path)

    assert markdown.startswith("# Provider Navigation Scorecard")
    assert "`hybrid`" in markdown
    assert "caller_hit_rate=`1.0`" in markdown
    assert "Recommended Provider" in markdown


def test_run_claude_skill_ab_should_load_tg_trace_records(tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_trace_log_script", "benchmarks/run_claude_skill_ab.py"
    )
    log_path = tmp_path / "tg_trace.jsonl"
    log_path.write_text(
        "\n".join([
            '{"argv":["tg","defs","Demo"],"exit_code":0,"duration_seconds":0.5}',
            '{"argv":["tg","refs","Demo"],"exit_code":0,"duration_seconds":1.25}',
        ])
        + "\n",
        encoding="utf-8",
    )

    records = module.load_tg_trace_records(log_path)

    assert len(records) == 2
    assert records[0]["argv"] == ["tg", "defs", "Demo"]
    assert records[1]["duration_seconds"] == 1.25


def test_run_claude_skill_ab_should_omit_model_flag_when_model_is_empty(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_model_script", "benchmarks/run_claude_skill_ab.py"
    )
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return ("ok", "")

    monkeypatch.setattr(module, "resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append(list(command)) or FakeProc(),
    )

    module._run_claude_command(
        tmp_path,
        "Say hi in one word.",
        model="",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        effort="",
    )

    assert "--model" not in calls[0]


def test_run_claude_skill_ab_should_include_effort_flag_when_requested(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_effort_script", "benchmarks/run_claude_skill_ab.py"
    )
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return ("ok", "")

    monkeypatch.setattr(module, "resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append(list(command)) or FakeProc(),
    )

    module._run_claude_command(
        tmp_path,
        "Say hi in one word.",
        model="",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        effort="low",
    )

    assert "--effort" in calls[0]
    assert calls[0][calls[0].index("--effort") + 1] == "low"


def test_run_claude_skill_ab_default_trace_output_path():
    module = _load_script_module(
        "run_claude_skill_ab_trace_path_script", "benchmarks/run_claude_skill_ab.py"
    )

    trace_path = module.default_trace_output_path(Path("C:/tmp/result.json"))

    assert trace_path == Path("C:/tmp/result_trace.json")


def test_tensor_grep_claude_skill_should_require_non_interactive_action():
    skill_text = Path(".claude/skills/tensor-grep/SKILL.md").read_text(encoding="utf-8")

    assert "do not ask for confirmation" in skill_text
    assert "make the change directly" in skill_text
    assert "want me to apply this?" in skill_text


def test_run_editor_profiling_should_pass_provider_to_blast_radius(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_editor_profiling_provider_script", "benchmarks/run_editor_profiling.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    captured: dict[str, object] = {}

    def fake_build_symbol_blast_radius_render(
        symbol,
        path,
        max_depth=3,
        max_files=6,
        max_sources=6,
        profile=True,
        semantic_provider="native",
    ):
        captured.update({"provider": semantic_provider})
        return {
            "_profiling": {"total_elapsed_s": 0.2, "breakdown_pct": {}, "phases": []},
            "files": [],
            "tests": [],
            "token_estimate": 0,
            "truncated": False,
        }

    monkeypatch.setattr(
        module.repo_map,
        "build_symbol_blast_radius_render",
        fake_build_symbol_blast_radius_render,
    )

    row = module.benchmark_blast_radius_fixture(
        {
            "root": str(repo_root),
            "name": "demo",
            "target_symbol": "create_invoice",
            "file_count": 1,
        },
        repeats=1,
        provider="hybrid",
    )

    assert captured["provider"] == "hybrid"
    assert row["semantic_provider"] == "hybrid"


def test_run_codex_competitor_eval_should_retry_without_schema_when_first_result_is_empty(
    tmp_path, monkeypatch
):
    module = _load_script_module(
        "run_codex_competitor_eval_retry_script", "benchmarks/run_codex_competitor_eval.py"
    )
    scenario = {
        "id": "demo",
        "language": "python",
        "repo_fixture": str(tmp_path),
        "query_or_symbol": "symbol",
        "mode": "blast-radius",
    }
    monkeypatch.setattr(module, "resolve_codex_binary", lambda: "codex")
    calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        command = list(args[0])
        calls.append(command)
        if "--output-schema" in command:
            stdout = "\n".join([
                json.dumps({"type": "thread.started", "thread_id": "demo"}),
                json.dumps({
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps({
                            "actual_primary_file": None,
                            "actual_primary_span": None,
                            "actual_dependent_files": [],
                            "actual_suggested_edit_files": [],
                            "actual_test_files": [],
                            "actual_validation_commands": [],
                            "context_token_count": 0,
                            "notes": "Awaiting code-edit task to plan against.",
                        }),
                    },
                }),
            ])
        else:
            stdout = "\n".join([
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
        return type("Proc", (), {"stdout": stdout})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    record = module.run_codex_scenario(scenario, model="gpt-5-codex", timeout_seconds=30)

    assert record["actual_primary_file"] == "a.py"
    assert len(calls) == 2
    assert any("--output-schema" in command for command in calls)


def test_run_codex_competitor_eval_should_normalize_string_primary_span():
    module = _load_script_module(
        "run_codex_competitor_eval_span_script", "benchmarks/run_codex_competitor_eval.py"
    )

    record = module._normalize_primary_span({
        "actual_primary_file": None,
        "actual_primary_span": "src/pkg/mod.py:10-14",
    })

    assert record["actual_primary_file"] == "src/pkg/mod.py"
    assert record["actual_primary_span"] == {"start_line": 10, "end_line": 14}


def test_run_copilot_competitor_eval_should_build_records_from_scenarios(tmp_path, monkeypatch):
    module = _load_script_module(
        "run_copilot_competitor_eval_script", "benchmarks/run_copilot_competitor_eval.py"
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

    monkeypatch.setattr(module, "resolve_copilot_binary", lambda: "copilot")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Proc",
            (),
            {
                "stdout": "● "
                + json.dumps({
                    "actual_primary_file": "a.py",
                    "actual_primary_span": {"start_line": 1, "end_line": 2},
                    "actual_dependent_files": [],
                    "actual_suggested_edit_files": [],
                    "actual_test_files": [],
                    "actual_validation_commands": ["pytest -q"],
                    "context_token_count": 123,
                    "notes": "ok",
                })
            },
        )(),
    )

    payload = module.build_payload(scenario_pack, model="gpt-5.2")

    assert payload["artifact"] == "copilot_competitor_eval"
    assert payload["suite"] == "run_copilot_competitor_eval"
    assert payload["records"][0]["system"] == "copilot"
    assert payload["records"][0]["actual_primary_file"] == "a.py"


def test_run_copilot_competitor_eval_should_cleanup_ephemeral_agents_file(tmp_path):
    module = _load_script_module(
        "run_copilot_competitor_eval_cleanup_script", "benchmarks/run_copilot_competitor_eval.py"
    )
    agents_path = tmp_path / "AGENTS.md"

    with module._ephemeral_repo_instructions(tmp_path):
        assert agents_path.exists()

    assert not agents_path.exists()


def test_run_copilot_competitor_eval_should_parse_wrapped_final_json():
    module = _load_script_module(
        "run_copilot_competitor_eval_wrapped_script", "benchmarks/run_copilot_competitor_eval.py"
    )
    stdout = "\n".join([
        "● Planning the answer first.",
        "",
        '● {"actual_primary_file":"a.py","actual_primary_span":{"start_li',
        '  ne":1,"end_line":2},"actual_dependent_files":[],"actual_suggested_',
        '  edit_files":[],"actual_test_files":[],"actual_validation_commands":[',
        '  "pytest -q"],"context_token_count":123,"notes":"ok"}',
        "",
    ])

    extracted = module._extract_text_from_copilot_output(stdout)

    assert json.loads(extracted)["actual_primary_file"] == "a.py"


def test_run_copilot_competitor_eval_should_parse_fenced_json_from_mixed_output():
    module = _load_script_module(
        "run_copilot_competitor_eval_fenced_script", "benchmarks/run_copilot_competitor_eval.py"
    )
    stdout = "\n".join([
        "Analyzing repository...",
        "I found the likely target below.",
        "```json",
        '{"actual_primary_file":"b.py","actual_primary_span":{"start_line":10,"end_line":12},"actual_dependent_files":[],"actual_suggested_edit_files":[],"actual_test_files":[],"actual_validation_commands":["pytest -q"],"context_token_count":321,"notes":"ok"}',
        "```",
    ])

    extracted = module._extract_text_from_copilot_output(stdout)

    assert json.loads(extracted)["actual_primary_file"] == "b.py"


def test_run_gemini_competitor_eval_should_build_records_from_scenarios(tmp_path, monkeypatch):
    module = _load_script_module(
        "run_gemini_competitor_eval_script", "benchmarks/run_gemini_competitor_eval.py"
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

    monkeypatch.setattr(module, "resolve_gemini_binary", lambda: "gemini")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Proc",
            (),
            {
                "stdout": json.dumps({
                    "session_id": "demo",
                    "response": json.dumps({
                        "actual_primary_file": "a.py",
                        "actual_primary_span": {"start_line": 1, "end_line": 2},
                        "actual_dependent_files": [],
                        "actual_suggested_edit_files": [],
                        "actual_test_files": [],
                        "actual_validation_commands": ["pytest -q"],
                        "context_token_count": 123,
                        "notes": "ok",
                    }),
                    "stats": {},
                })
            },
        )(),
    )

    payload = module.build_payload(scenario_pack, model="gemini-2.5-flash")

    assert payload["artifact"] == "gemini_competitor_eval"
    assert payload["suite"] == "run_gemini_competitor_eval"
    assert payload["records"][0]["system"] == "gemini-cli"
    assert payload["records"][0]["actual_primary_file"] == "a.py"


def test_build_external_agent_patch_driver_comparison_should_build_payload(tmp_path):
    module = _load_script_module(
        "build_external_agent_patch_driver_comparison_script",
        "benchmarks/build_external_agent_patch_driver_comparison.py",
    )
    gemini_path = tmp_path / "gemini.json"
    claude_path = tmp_path / "claude.json"
    codex_path = tmp_path / "codex.json"
    gemini_output_path = tmp_path / "gemini_output.json"
    claude_output_path = tmp_path / "claude_output.json"
    codex_output_path = tmp_path / "codex_output.json"
    gemini_path.write_text(
        json.dumps({
            "artifact": "gemini_patch_driver_validation_summary",
            "instance_id": "gemini-1",
            "output_file": str(gemini_output_path),
            "actual_primary_file": "glob.ts",
            "follow_up_reads": ["glob.ts#L1-L10", "grep.ts#L1-L20"],
            "validation_commands": ["uv run pytest -q"],
            "ledger_next_action": "run patch system",
        }),
        encoding="utf-8",
    )
    claude_path.write_text(
        json.dumps({
            "artifact": "claude_patch_driver_validation_summary",
            "instance_id": "claude-1",
            "output_file": str(claude_output_path),
            "actual_primary_file": "FileWriteToolDiff.tsx",
            "follow_up_reads": ["FileWriteToolDiff.tsx#L1-L10"],
            "validation_commands": ["uv run pytest -q"],
            "ledger_next_action": "run patch system",
        }),
        encoding="utf-8",
    )
    codex_path.write_text(
        json.dumps({
            "artifact": "codex_patch_driver_validation_summary",
            "instance_id": "codex-1",
            "output_file": str(codex_output_path),
            "actual_primary_file": "fuzzy_file_search.rs",
            "follow_up_reads": ["fuzzy_file_search.rs#L1-L10"],
            "validation_commands": ["cargo test"],
            "ledger_next_action": "run patch system",
        }),
        encoding="utf-8",
    )
    gemini_output_path.write_text(
        json.dumps({
            "records": [
                {
                    "instance_id": "gemini-1",
                    "navigation_pack": {
                        "parallel_read_groups": [
                            {
                                "phase": 0,
                                "label": "primary",
                                "can_parallelize": False,
                                "mentions": ["glob.ts#L1-L10"],
                                "files": ["glob.ts"],
                                "roles": ["primary"],
                            },
                            {
                                "phase": 1,
                                "label": "related",
                                "can_parallelize": True,
                                "mentions": ["grep.ts#L1-L20"],
                                "files": ["grep.ts"],
                                "roles": ["related"],
                            },
                        ]
                    },
                }
            ]
        }),
        encoding="utf-8",
    )
    claude_output_path.write_text(
        json.dumps({
            "records": [
                {
                    "instance_id": "claude-1",
                    "navigation_pack": {
                        "parallel_read_groups": [
                            {
                                "phase": 0,
                                "label": "primary",
                                "can_parallelize": False,
                                "mentions": ["FileWriteToolDiff.tsx#L1-L10"],
                                "files": ["FileWriteToolDiff.tsx"],
                                "roles": ["primary"],
                            }
                        ]
                    },
                }
            ]
        }),
        encoding="utf-8",
    )
    codex_output_path.write_text(
        json.dumps({
            "records": [
                {
                    "instance_id": "codex-1",
                    "navigation_pack": {
                        "parallel_read_groups": [
                            {
                                "phase": 0,
                                "label": "primary",
                                "can_parallelize": False,
                                "mentions": ["fuzzy_file_search.rs#L1-L10"],
                                "files": ["fuzzy_file_search.rs"],
                                "roles": ["primary"],
                            }
                        ]
                    },
                }
            ]
        }),
        encoding="utf-8",
    )

    payload = module.build_payload([
        ("gemini", gemini_path),
        ("claude", claude_path),
        ("codex", codex_path),
    ])

    assert payload["artifact"] == "external_agent_patch_driver_comparison"
    assert payload["common_contract"]["ledger_artifact"] == "agent_attempt_ledger"
    assert payload["common_contract"]["next_action"] == "run patch system"
    assert payload["systems"][0]["system"] == "gemini"
    assert payload["systems"][0]["follow_up_count"] == 2
    assert payload["systems"][0]["parallel_read_group_count"] == 2
    assert payload["systems"][0]["estimated_saved_read_steps"] == 0
    assert payload["systems"][2]["validation_commands"] == ["cargo test"]


def test_build_external_agent_patch_driver_comparison_cli_should_write_output(tmp_path):
    module = _load_script_module(
        "build_external_agent_patch_driver_comparison_cli_script",
        "benchmarks/build_external_agent_patch_driver_comparison.py",
    )
    gemini_path = tmp_path / "gemini.json"
    claude_path = tmp_path / "claude.json"
    output_path = tmp_path / "comparison.json"
    gemini_output_path = tmp_path / "gemini_output.json"
    claude_output_path = tmp_path / "claude_output.json"
    gemini_path.write_text(
        json.dumps({
            "artifact": "gemini_patch_driver_validation_summary",
            "instance_id": "gemini-1",
            "output_file": str(gemini_output_path),
            "actual_primary_file": "glob.ts",
            "follow_up_reads": ["glob.ts#L1-L10"],
            "validation_commands": ["uv run pytest -q"],
            "ledger_next_action": "run patch system",
        }),
        encoding="utf-8",
    )
    claude_path.write_text(
        json.dumps({
            "artifact": "claude_patch_driver_validation_summary",
            "instance_id": "claude-1",
            "output_file": str(claude_output_path),
            "actual_primary_file": "FileWriteToolDiff.tsx",
            "follow_up_reads": ["FileWriteToolDiff.tsx#L1-L10"],
            "validation_commands": ["uv run pytest -q"],
            "ledger_next_action": "run patch system",
        }),
        encoding="utf-8",
    )
    gemini_output_path.write_text(
        json.dumps({
            "records": [
                {"instance_id": "gemini-1", "navigation_pack": {"parallel_read_groups": []}}
            ]
        }),
        encoding="utf-8",
    )
    claude_output_path.write_text(
        json.dumps({
            "records": [
                {"instance_id": "claude-1", "navigation_pack": {"parallel_read_groups": []}}
            ]
        }),
        encoding="utf-8",
    )

    exit_code = module.main([
        "--summary",
        f"gemini={gemini_path}",
        "--summary",
        f"claude={claude_path}",
        "--output",
        str(output_path),
    ])

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert written["systems"][1]["system"] == "claude"
