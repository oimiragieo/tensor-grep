import importlib.util
import json
from pathlib import Path


def _load_script_module(name: str, rel_path: str):
    root = Path(__file__).resolve().parents[2]
    module_path = root / rel_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_agent_workflow_benchmarks_should_declare_suite_and_timestamp() -> None:
    source = Path("benchmarks/run_agent_workflow_benchmarks.py").read_text(encoding="utf-8")

    assert '"suite"' in source
    assert '"generated_at_epoch_s"' in source


def test_run_agent_workflow_benchmarks_should_extract_capsule_contract_metrics():
    module = _load_script_module(
        "run_agent_workflow_benchmarks_metrics",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )
    payload = {
        "confidence": {"overall": 0.65},
        "primary_target": {
            "file": "src/app.ts",
            "symbol": "createInvoice",
            "confidence": 0.65,
        },
        "ask_before_editing": {"ask_required": True},
        "alternative_targets": [{"file": "src/payments.py"}],
        "snippets": [{"file": "src/app.ts", "start_line": 1, "end_line": 4}],
        "validation_commands": ["npm test"],
        "context_consistency": {
            "validation_alignment": "filtered-mismatch",
            "validation_filtered_count": 1,
        },
        "edit_order": [{"file": "src/app.ts"}],
        "rollback": {"checkpoint_id": "cp-1"},
        "omission_counts": {"sources": 2, "call_sites": 1},
    }

    metrics = module.extract_capsule_metrics(
        payload,
        {
            "name": "ambiguous_invoice",
            "expected_ask_required": True,
            "expected_primary_file_suffix": "src/app.ts",
        },
    )

    assert metrics == {
        "scenario": "ambiguous_invoice",
        "primary_file": "src/app.ts",
        "primary_symbol": "createInvoice",
        "confidence_overall": 0.65,
        "primary_confidence": 0.65,
        "ask_required": True,
        "alternative_count": 1,
        "snippet_count": 1,
        "validation_command_count": 1,
        "validation_alignment": "filtered-mismatch",
        "validation_filtered_count": 1,
        "edit_order_count": 1,
        "rollback_present": True,
        "omission_count": 3,
        "target_selection_evaluated": True,
        "expected_target_file_suffix": "src/app.ts",
        "expected_target_symbol": "",
        "target_rank": 1,
        "hit_at_1": True,
        "hit_at_3": True,
        "mrr_at_3": 1.0,
        "coverage_at_budget": True,
        "wrong_confident_miss": False,
        "safe_ambiguity": False,
        "passed": True,
    }


def test_run_agent_workflow_benchmarks_should_extract_target_selection_metrics():
    module = _load_script_module(
        "run_agent_workflow_benchmarks_target_selection",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )
    payload = {
        "confidence": {"overall": 0.82},
        "primary_target": {
            "file": "src/tensor_grep/cli/ripgrep_fmt.py",
            "symbol": "_binary_notice",
            "confidence": 0.82,
        },
        "ask_user_before_editing": {"required": True},
        "alternative_targets": [
            {
                "file": "src/tensor_grep/cli/runtime_paths.py",
                "symbol": "resolve_ripgrep_binary",
            },
            {"file": "src/tensor_grep/cli/bootstrap.py", "symbol": "resolve_native_binary"},
        ],
        "snippets": [
            {"file": "src/tensor_grep/cli/ripgrep_fmt.py", "start_line": 1, "end_line": 4}
        ],
        "omissions": {
            "follow_up_reads": [
                {
                    "file": "src/tensor_grep/cli/runtime_paths.py",
                    "reason": "alternative target",
                }
            ]
        },
    }

    metrics = module.extract_capsule_metrics(
        payload,
        {
            "name": "ripgrep_binary_resolution",
            "expected_target_file_suffix": "src/tensor_grep/cli/runtime_paths.py",
            "expected_target_symbol": "resolve_ripgrep_binary",
        },
    )

    assert metrics["target_selection_evaluated"] is True
    assert metrics["expected_target_file_suffix"] == "src/tensor_grep/cli/runtime_paths.py"
    assert metrics["expected_target_symbol"] == "resolve_ripgrep_binary"
    assert metrics["target_rank"] == 2
    assert metrics["hit_at_1"] is False
    assert metrics["hit_at_3"] is True
    assert metrics["mrr_at_3"] == 0.5
    assert metrics["coverage_at_budget"] is True
    assert metrics["wrong_confident_miss"] is False
    assert metrics["safe_ambiguity"] is True


def test_run_agent_workflow_benchmarks_should_summarize_target_selection_metrics():
    module = _load_script_module(
        "run_agent_workflow_benchmarks_target_summary",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )

    summary = module.build_agent_capsule_summary([
        {
            "scenario": "resolver",
            "elapsed_s": 0.1,
            "passed": True,
            "ask_required": False,
            "alternative_count": 0,
            "snippet_count": 1,
            "validation_command_count": 1,
            "validation_filtered_count": 0,
            "rollback_present": True,
            "omission_count": 0,
            "target_selection_evaluated": True,
            "hit_at_1": True,
            "hit_at_3": True,
            "mrr_at_3": 1.0,
            "coverage_at_budget": True,
            "wrong_confident_miss": False,
            "safe_ambiguity": False,
        },
        {
            "scenario": "bridge",
            "elapsed_s": 0.2,
            "passed": True,
            "ask_required": False,
            "alternative_count": 0,
            "snippet_count": 1,
            "validation_command_count": 0,
            "validation_filtered_count": 0,
            "rollback_present": True,
            "omission_count": 1,
            "target_selection_evaluated": True,
            "hit_at_1": False,
            "hit_at_3": False,
            "mrr_at_3": 0.0,
            "coverage_at_budget": False,
            "wrong_confident_miss": True,
            "safe_ambiguity": False,
        },
    ])

    assert summary["target_selection_summary"] == {
        "evaluated_cases": 2,
        "hit_at_1_cases": 1,
        "hit_at_1_rate": 0.5,
        "hit_at_3_cases": 1,
        "hit_at_3_rate": 0.5,
        "mrr_at_3": 0.5,
        "coverage_at_budget_cases": 1,
        "coverage_at_budget_rate": 0.5,
        "wrong_confident_miss_cases": 1,
        "wrong_confident_miss_rate": 0.5,
        "safe_ambiguity_cases": 0,
        "safe_ambiguity_rate": 0.0,
        "wrong_confident_miss_threshold": 0.75,
    }


def test_run_agent_workflow_benchmarks_should_emit_capsule_and_edit_loop_sections(
    monkeypatch,
    tmp_path,
):
    module = _load_script_module(
        "run_agent_workflow_benchmarks_rows",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )
    output_path = tmp_path / "bench_agent_workflow.json"
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_agent_workflow_benchmarks.py",
            "--output",
            str(output_path),
            "--iterations",
            "3",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(
        module, "resolve_agent_workflow_bench_dir", lambda: tmp_path / "bench_agent_workflow"
    )
    monkeypatch.setattr(
        module,
        "ensure_agent_workflow_corpus",
        lambda output_dir, *, seed: {
            "corpus_dir": output_dir / "agent",
            "manifest_path": output_dir / "agent.manifest.json",
            "file_count": 4,
            "seed": seed,
        },
    )
    monkeypatch.setattr(
        module,
        "ensure_harness_loop_bench_corpus",
        lambda output_dir, *, file_count, total_loc, seed: {
            "corpus_dir": output_dir / "edit_loop",
            "manifest_path": output_dir / "edit.manifest.sha256",
            "file_count": file_count,
            "total_loc": total_loc,
            "seed": seed,
        },
    )
    monkeypatch.setattr(
        module,
        "run_agent_workflow_benchmark",
        lambda **_kwargs: {
            "iterations": 3,
            "agent_capsule": {
                "all_passed": True,
                "scenario_medians_s": {
                    "ambiguous_invoice": 0.21,
                    "python_invoice": 0.18,
                },
                "contract_summary": {
                    "ask_required_cases": 1,
                    "aligned_validation_cases": 1,
                    "filtered_validation_cases": 1,
                },
                "rows": [
                    {
                        "iteration": 1,
                        "scenario": "ambiguous_invoice",
                        "elapsed_s": 0.21,
                        "confidence_overall": 0.65,
                        "ask_required": True,
                        "passed": True,
                    }
                ],
            },
            "edit_loop": {
                "all_passed": True,
                "phase_medians_s": {
                    "search_s": 0.11,
                    "plan_s": 0.12,
                    "apply_s": 0.2,
                    "verify_s": 0.04,
                },
                "phase_totals_s": {
                    "search_s": 0.33,
                    "plan_s": 0.36,
                    "apply_s": 0.6,
                    "verify_s": 0.12,
                },
                "rows": [
                    {
                        "iteration": 1,
                        "search_s": 0.11,
                        "plan_s": 0.12,
                        "apply_s": 0.2,
                        "verify_s": 0.04,
                        "remaining_matches": 0,
                        "passed": True,
                    }
                ],
            },
            "all_passed": True,
        },
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_agent_workflow"
    assert payload["suite"] == "run_agent_workflow_benchmarks"
    assert payload["workflow_surfaces"] == ["agent_capsule", "edit_loop"]
    assert (
        payload["positioning"]
        == "agent-native workflow benchmark; not a cold exact-text speed claim"
    )
    assert payload["agent_capsule"]["contract_summary"]["ask_required_cases"] == 1
    assert payload["edit_loop"]["phase_medians_s"]["apply_s"] == 0.2
    assert payload["passed"] is True
