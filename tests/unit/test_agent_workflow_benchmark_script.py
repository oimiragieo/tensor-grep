import importlib.machinery
import importlib.util
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_MISSING = object()


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
        "expected_targets": [{"file_suffix": "src/app.ts", "symbol": ""}],
        "observed_primary_target": {
            "file": "src/app.ts",
            "symbol": "createInvoice",
            "confidence": 0.65,
        },
        "alternative_target_ranks": [
            {
                "rank": 2,
                "file": "src/payments.py",
                "symbol": "",
                "matches_expected_target": False,
            }
        ],
        "target_rank": 1,
        "hit_at_1": True,
        "hit_at_3": True,
        "mrr": 1.0,
        "mrr_at_3": 1.0,
        "coverage_at_budget": True,
        "false_primary": False,
        "ambiguous_requires_confirmation": False,
        "wrong_confident_miss": False,
        "wrong_confident_primary": False,
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
    assert metrics["mrr"] == 0.5
    assert metrics["mrr_at_3"] == 0.5
    assert metrics["coverage_at_budget"] is True
    assert metrics["wrong_confident_miss"] is False
    assert metrics["safe_ambiguity"] is True
    assert metrics["expected_targets"] == [
        {
            "file_suffix": "src/tensor_grep/cli/runtime_paths.py",
            "symbol": "resolve_ripgrep_binary",
        }
    ]
    assert metrics["observed_primary_target"] == {
        "file": "src/tensor_grep/cli/ripgrep_fmt.py",
        "symbol": "_binary_notice",
        "confidence": 0.82,
    }
    assert metrics["alternative_target_ranks"] == [
        {
            "rank": 2,
            "file": "src/tensor_grep/cli/runtime_paths.py",
            "symbol": "resolve_ripgrep_binary",
            "matches_expected_target": True,
        },
        {
            "rank": 3,
            "file": "src/tensor_grep/cli/bootstrap.py",
            "symbol": "resolve_native_binary",
            "matches_expected_target": False,
        },
    ]
    assert metrics["false_primary"] is True
    assert metrics["ambiguous_requires_confirmation"] is True


def test_run_agent_workflow_benchmarks_should_keep_mrr_at_3_bounded_for_late_hits():
    module = _load_script_module(
        "run_agent_workflow_benchmarks_late_target_selection",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )
    payload = {
        "confidence": {"overall": 0.7},
        "primary_target": {"file": "src/wrong0.py", "symbol": "wrong0"},
        "ask_before_editing": {"ask_required": False},
        "alternative_targets": [
            {"file": "src/wrong1.py", "symbol": "wrong1"},
            {"file": "src/wrong2.py", "symbol": "wrong2"},
            {"file": "src/right.py", "symbol": "target_symbol"},
        ],
        "snippets": [{"file": "src/right.py", "start_line": 1, "end_line": 4}],
    }

    metrics = module.extract_capsule_metrics(
        payload,
        {
            "name": "late_hit",
            "expected_targets": [{"file_suffix": "src/right.py", "symbol": "target_symbol"}],
        },
    )

    assert metrics["target_rank"] == 4
    assert metrics["hit_at_3"] is False
    assert metrics["mrr"] == 0.25
    assert metrics["mrr_at_3"] == 0.0
    assert metrics["coverage_at_budget"] is True


def test_run_agent_workflow_benchmarks_should_flag_wrong_confident_primary_as_autonomous_risk():
    """AGT-01: wrong-first/correct-second passes hit@3 recall but is an autonomous-risk
    miss -- an unattended agent acting on primary_target would edit the wrong file even
    though the correct target ranked second. Distinct from wrong_confident_miss, which
    only fires when the target is missed entirely within top-3."""
    module = _load_script_module(
        "run_agent_workflow_benchmarks_wrong_confident_primary",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )
    payload = {
        "confidence": {"overall": 0.94},
        "primary_target": {"file": "src/wrong_primary.py", "symbol": "wrong_primary"},
        "ask_before_editing": {"ask_required": False},
        "alternative_targets": [
            {"file": "src/right.py", "symbol": "target_symbol"},
        ],
        "snippets": [{"file": "src/wrong_primary.py", "start_line": 1, "end_line": 4}],
    }

    metrics = module.extract_capsule_metrics(
        payload,
        {
            "name": "wrong_first_correct_second",
            "expected_targets": [{"file_suffix": "src/right.py", "symbol": "target_symbol"}],
        },
    )

    assert metrics["hit_at_1"] is False
    assert metrics["hit_at_3"] is True
    assert metrics["wrong_confident_primary"] is True


def test_run_agent_workflow_benchmarks_should_not_flag_wrong_confident_primary_when_low_confidence():
    module = _load_script_module(
        "run_agent_workflow_benchmarks_wrong_confident_primary_low_conf",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )
    payload = {
        "confidence": {"overall": 0.5},
        "primary_target": {"file": "src/wrong_primary.py", "symbol": "wrong_primary"},
        "ask_before_editing": {"ask_required": False},
        "alternative_targets": [
            {"file": "src/right.py", "symbol": "target_symbol"},
        ],
        "snippets": [{"file": "src/wrong_primary.py", "start_line": 1, "end_line": 4}],
    }

    metrics = module.extract_capsule_metrics(
        payload,
        {
            "name": "wrong_first_correct_second_low_confidence",
            "expected_targets": [{"file_suffix": "src/right.py", "symbol": "target_symbol"}],
        },
    )

    assert metrics["hit_at_1"] is False
    assert metrics["hit_at_3"] is True
    assert metrics["wrong_confident_primary"] is False


def test_run_agent_workflow_benchmarks_should_not_flag_wrong_confident_primary_when_asking():
    module = _load_script_module(
        "run_agent_workflow_benchmarks_wrong_confident_primary_ask",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )
    payload = {
        "confidence": {"overall": 0.94},
        "primary_target": {"file": "src/wrong_primary.py", "symbol": "wrong_primary"},
        "ask_before_editing": {"ask_required": True},
        "alternative_targets": [
            {"file": "src/right.py", "symbol": "target_symbol"},
        ],
        "snippets": [{"file": "src/wrong_primary.py", "start_line": 1, "end_line": 4}],
    }

    metrics = module.extract_capsule_metrics(
        payload,
        {
            "name": "wrong_first_correct_second_ask_required",
            "expected_targets": [{"file_suffix": "src/right.py", "symbol": "target_symbol"}],
        },
    )

    assert metrics["wrong_confident_primary"] is False


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
            "false_primary": False,
            "ambiguous_requires_confirmation": False,
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
            "false_primary": True,
            "ambiguous_requires_confirmation": False,
        },
    ])

    assert summary["target_selection_summary"] == {
        "evaluated_cases": 2,
        "hit_at_1": 0.5,
        "hit_at_1_cases": 1,
        "hit_at_1_rate": 0.5,
        "hit_at_3": 0.5,
        "hit_at_3_cases": 1,
        "hit_at_3_rate": 0.5,
        "mrr": 0.5,
        "mrr_at_3": 0.5,
        "coverage_at_budget_cases": 1,
        "coverage_at_budget_rate": 0.5,
        "false_primary_cases": 1,
        "false_primary_rate": 0.5,
        "ambiguous_requires_confirmation_cases": 0,
        "ambiguous_requires_confirmation_rate": 0.0,
        "wrong_confident_miss_cases": 1,
        "wrong_confident_miss_rate": 0.5,
        "wrong_confident_primary_cases": 0,
        "wrong_confident_primary_rate": 0.0,
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


def _target_row(*, confidence_overall, hit_at_1, target_selection_evaluated=True, **overrides):
    row = {
        "scenario": "calibration",
        "elapsed_s": 0.1,
        "passed": True,
        "ask_required": False,
        "alternative_count": 0,
        "snippet_count": 1,
        "validation_command_count": 0,
        "validation_filtered_count": 0,
        "rollback_present": True,
        "omission_count": 0,
        "target_selection_evaluated": target_selection_evaluated,
        "confidence_overall": confidence_overall,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_1,
        "mrr_at_3": 1.0 if hit_at_1 else 0.0,
        "coverage_at_budget": hit_at_1,
        "wrong_confident_miss": False,
        "safe_ambiguity": False,
        "false_primary": not hit_at_1,
        "ambiguous_requires_confirmation": False,
    }
    row.update(overrides)
    return row


def test_build_agent_capsule_summary_should_produce_calibration_bins_with_counts_and_accuracy():
    module = _load_script_module(
        "run_agent_workflow_benchmarks_calibration_bins",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )

    rows = [
        _target_row(confidence_overall=0.05, hit_at_1=False),
        _target_row(confidence_overall=0.55, hit_at_1=True),
        _target_row(confidence_overall=0.55, hit_at_1=False),
        _target_row(confidence_overall=0.95, hit_at_1=True),
    ]

    summary = module.build_agent_capsule_summary(rows)
    calibration = summary["confidence_calibration"]

    assert calibration["confidence_kind"] == "heuristic"
    assert calibration["complete_tasks"] == 4
    assert calibration["incomplete_tasks"] == 0

    bins = {b["bin"]: b for b in calibration["calibration_bins"]}
    assert bins["[0.0,0.2)"]["count"] == 1
    assert bins["[0.0,0.2)"]["hit_at_1_correct"] == 0
    assert bins["[0.0,0.2)"]["hit_at_1_rate"] == 0.0
    assert bins["[0.4,0.6)"]["count"] == 2
    assert bins["[0.4,0.6)"]["hit_at_1_correct"] == 1
    assert bins["[0.4,0.6)"]["hit_at_1_rate"] == 0.5
    assert bins["[0.8,1.0]"]["count"] == 1
    assert bins["[0.8,1.0]"]["hit_at_1_correct"] == 1
    assert bins["[0.8,1.0]"]["hit_at_1_rate"] == 1.0

    # Selective accuracy vs. answer coverage: raising the confidence bar to only "answer"
    # (act without asking) at or above a threshold must never LOWER accuracy among answered
    # cases relative to a lower threshold -- that is the entire point of the metric.
    curve = {c["threshold"]: c for c in calibration["selective_accuracy_curve"]}
    assert curve[0.0]["answered_coverage"] == 1.0
    assert curve[0.0]["hit_at_1_rate_when_answered"] == 0.5
    assert curve[0.8]["answered_coverage"] == 0.25
    assert curve[0.8]["hit_at_1_rate_when_answered"] == 1.0


def test_build_agent_capsule_summary_calibration_reports_zero_tasks_as_insufficient_not_perfect():
    module = _load_script_module(
        "run_agent_workflow_benchmarks_calibration_empty",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )

    summary = module.build_agent_capsule_summary([])
    calibration = summary["confidence_calibration"]

    assert calibration["complete_tasks"] == 0
    assert calibration["calibration_bins"] == []
    assert calibration["selective_accuracy_curve"] == []
    # A caller must not read zero bins as 100% accuracy; the field says so explicitly.
    assert calibration["insufficient_evidence"] is True


def test_build_agent_capsule_summary_calibration_excludes_rows_missing_confidence():
    module = _load_script_module(
        "run_agent_workflow_benchmarks_calibration_missing_confidence",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )

    rows = [
        _target_row(confidence_overall=None, hit_at_1=True),
        _target_row(confidence_overall=0.9, hit_at_1=True),
    ]

    summary = module.build_agent_capsule_summary(rows)
    calibration = summary["confidence_calibration"]

    assert calibration["complete_tasks"] == 1
    assert calibration["incomplete_tasks"] == 1
    assert sum(b["count"] for b in calibration["calibration_bins"]) == 1


def test_build_agent_capsule_summary_calibration_treats_non_finite_confidence_as_incomplete():
    # codex_luna audit (AGT-05): NaN/inf/out-of-range confidence_overall must never crash
    # bucketing or be silently scored -- treat exactly like a missing value.
    module = _load_script_module(
        "run_agent_workflow_benchmarks_calibration_nonfinite",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )

    rows = [
        _target_row(confidence_overall=float("nan"), hit_at_1=True),
        _target_row(confidence_overall=float("inf"), hit_at_1=True),
        _target_row(confidence_overall=1.5, hit_at_1=True),
        _target_row(confidence_overall=-0.1, hit_at_1=True),
        _target_row(confidence_overall=0.5, hit_at_1=True),
    ]

    summary = module.build_agent_capsule_summary(rows)
    calibration = summary["confidence_calibration"]

    assert calibration["complete_tasks"] == 1
    assert calibration["incomplete_tasks"] == 4
    assert sum(b["count"] for b in calibration["calibration_bins"]) == 1


def test_build_agent_capsule_summary_calibration_bin_boundaries_are_exact():
    # codex_luna audit round 2 (AGT-05): float division put 0.6 in [0.4,0.6) instead of
    # [0.6,0.8) due to 0.6/0.2 == 2.9999999999999996.
    module = _load_script_module(
        "run_agent_workflow_benchmarks_calibration_boundaries",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )

    rows = [
        _target_row(confidence_overall=0.2, hit_at_1=True),
        _target_row(confidence_overall=0.4, hit_at_1=True),
        _target_row(confidence_overall=0.6, hit_at_1=True),
        _target_row(confidence_overall=0.8, hit_at_1=True),
    ]

    summary = module.build_agent_capsule_summary(rows)
    bins = {b["bin"]: b["count"] for b in summary["confidence_calibration"]["calibration_bins"]}

    assert bins.get("[0.2,0.4)") == 1
    assert bins.get("[0.4,0.6)") == 1
    assert bins.get("[0.6,0.8)") == 1
    assert bins.get("[0.8,1.0]") == 1


def test_scorecard_reports_verified_success_separately_from_command_fit() -> None:
    module = _load_script_module(
        "scorecard_outcome_fields",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    payload = module.build_scorecard_payload({
        "systems": [
            {
                "system": "fit-and-passed",
                "primary_file": "src/lib.rs",
                "follow_up_count": 3,
                "parallel_read_group_count": 1,
                "validation_commands": ["cargo test"],
                "outcome": {"execution_observed": True, "validation_passed": True},
            },
            {
                "system": "fit-but-failed",
                "primary_file": "src/lib.rs",
                "follow_up_count": 3,
                "parallel_read_group_count": 1,
                "validation_commands": ["cargo test"],
                "outcome": {"execution_observed": True, "validation_passed": False},
            },
            {
                "system": "fit-but-unobserved",
                "primary_file": "src/lib.rs",
                "follow_up_count": 3,
                "parallel_read_group_count": 1,
                "validation_commands": ["cargo test"],
            },
        ]
    })

    passed = payload["by_system"]["fit-and-passed"]
    failed = payload["by_system"]["fit-but-failed"]
    unobserved = payload["by_system"]["fit-but-unobserved"]
    assert passed["validation_fit"] == "strong"
    assert failed["validation_fit"] == "strong"
    assert failed["validation_fit_score"] == 1.0
    assert unobserved["validation_fit"] == "strong"
    assert passed["outcome_state"] == "passed"
    assert passed["verified_task_success"] is True
    assert failed["outcome_state"] == "failed"
    assert failed["verified_task_success"] is False
    assert unobserved["outcome_state"] == "unavailable"
    assert unobserved["verified_task_success"] is False
    assert payload["summary"]["complete_outcome_systems"] == 2
    assert payload["summary"]["incomplete_outcome_systems"] == 1
    assert payload["summary"]["verified_task_success_systems"] == 1
    assert payload["summary"]["verified_task_success_rate"] == 0.5
    assert passed["overall_score"] == failed["overall_score"]
    assert "mean_compactness_score" in payload["summary"]


def test_scorecard_verified_success_rate_is_null_when_nothing_was_executed() -> None:
    module = _load_script_module(
        "scorecard_outcome_no_evidence",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    payload = module.build_scorecard_payload({
        "systems": [
            {
                "system": "unobserved",
                "primary_file": "src/lib.rs",
                "follow_up_count": 1,
                "validation_commands": ["cargo test"],
            }
        ]
    })
    assert payload["summary"]["complete_outcome_systems"] == 0
    assert payload["summary"]["verified_task_success_rate"] is None


def test_scorecard_outcome_state_requires_strict_boolean_execution_flag() -> None:
    module = _load_script_module(
        "scorecard_outcome_strict_execution_flag",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )

    payload = module.build_scorecard_payload({
        "systems": [
            {
                "system": "truthy-but-not-boolean",
                "primary_file": "src/lib.rs",
                "validation_commands": ["cargo test"],
                "outcome": {"execution_observed": 1, "validation_passed": True},
            }
        ]
    })

    row = payload["by_system"]["truthy-but-not-boolean"]
    assert row["outcome_state"] == "unavailable"
    assert row["verified_task_success"] is False
    assert payload["summary"]["complete_outcome_systems"] == 0
    assert payload["summary"]["verified_task_success_rate"] is None


def test_scorecard_outcome_state_requires_strict_boolean_validation_flag() -> None:
    module = _load_script_module(
        "scorecard_outcome_strict_validation_flag",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )

    payload = module.build_scorecard_payload({
        "systems": [
            {
                "system": "string-false",
                "primary_file": "src/lib.rs",
                "validation_commands": ["cargo test"],
                "outcome": {"execution_observed": True, "validation_passed": "false"},
            }
        ]
    })

    row = payload["by_system"]["string-false"]
    assert row["outcome_state"] == "failed"
    assert row["verified_task_success"] is False
    assert payload["summary"]["complete_outcome_systems"] == 1
    assert payload["summary"]["verified_task_success_rate"] == 0.0


def test_scorecard_rejects_duplicate_system_names_before_scoring() -> None:
    module = _load_script_module(
        "scorecard_duplicate_system_names",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    comparison = {
        "systems": [
            {"system": "tensor-grep", "primary_file": "src/a.py"},
            {"system": "tensor-grep", "primary_file": "src/b.py"},
        ]
    }

    try:
        module.build_scorecard_payload(comparison)
    except ValueError as error:
        assert str(error) == "duplicate system name: 'tensor-grep'"
    else:
        raise AssertionError("duplicate system names must fail closed")


def test_scorecard_main_does_not_publish_duplicate_system_artifact(tmp_path) -> None:
    module = _load_script_module(
        "scorecard_duplicate_system_main",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    input_path = tmp_path / "comparison.json"
    output_path = tmp_path / "scorecard.json"
    input_path.write_text(
        json.dumps({
            "systems": [
                {"system": "tensor-grep", "primary_file": "src/a.py"},
                {"system": "tensor-grep", "primary_file": "src/b.py"},
            ]
        }),
        encoding="utf-8",
    )

    try:
        module.main(["--input", str(input_path), "--output", str(output_path)])
    except ValueError as error:
        assert str(error) == "duplicate system name: 'tensor-grep'"
    else:
        raise AssertionError("duplicate system names must fail closed")
    assert not output_path.exists()


def test_scorecard_payload_embeds_the_outcome_join_report() -> None:
    module = _load_script_module(
        "scorecard_outcome_join_embedded",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    identity = {
        "system_id": "tensor-grep",
        "instance_id": "clap-lex-parse",
        "repo_commit": "a" * 40,
        "tool_version": "1.113.3",
        "model_id": "gpt-5.6-sol",
        "budget_id": "tokens=16000",
    }
    payload = module.build_scorecard_payload({
        "systems": [],
        "outcome_join": {
            "predictions": [{**identity, "predicted_validation_commands": ["cargo test"]}],
            "outcomes": [{**identity, "execution_observed": True, "validation_passed": True}],
        },
    })
    join = payload["outcome_join"]
    assert join["artifact"] == "agent_outcome_join"
    assert join["identity_fields"][0] == "system_id"
    assert len(join["joined"]) == 1
    assert join["joined"][0]["verified_task_success"] is True
    assert join["summary"]["complete_cases"] == 1
    assert join["summary"]["verified_task_success_rate"] == 1.0


def test_scorecard_payload_reports_an_empty_join_when_no_records_are_supplied() -> None:
    module = _load_script_module(
        "scorecard_outcome_join_absent",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    payload = module.build_scorecard_payload({"systems": []})
    join = payload["outcome_join"]
    assert join["joined"] == []
    assert join["summary"]["verified_task_success_rate"] is None
    assert join["summary"]["complete_cases"] == 0


def test_scorecard_join_retains_non_dict_records_as_unidentified() -> None:
    module = _load_script_module(
        "scorecard_outcome_join_non_dict_records",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )

    payload = module.build_scorecard_payload({
        "systems": [],
        "outcome_join": {
            "predictions": ["not-a-prediction"],
            "outcomes": ["not-an-outcome"],
        },
    })

    join = payload["outcome_join"]
    expected_missing = [
        "system_id",
        "instance_id",
        "repo_commit",
        "tool_version",
        "model_id",
        "budget_id",
    ]
    assert join["unidentified_predictions"] == [
        {
            "record": "not-a-prediction",
            "missing_fields": expected_missing,
        }
    ]
    assert join["unidentified_outcomes"] == [
        {
            "record": "not-an-outcome",
            "missing_fields": expected_missing,
        }
    ]
    assert join["summary"]["unidentified_prediction_count"] == 1
    assert join["summary"]["unidentified_outcome_count"] == 1


def test_scorecard_join_rejects_null_record_lists_clearly() -> None:
    module = _load_script_module(
        "scorecard_outcome_join_null_records",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )

    try:
        module.build_scorecard_payload({
            "systems": [],
            "outcome_join": {"predictions": None},
        })
    except TypeError as error:
        assert str(error) == "outcome_join predictions must be a list"
    else:
        raise AssertionError("null outcome_join predictions must fail clearly")


@pytest.mark.parametrize("invalid_join", [None, "not-an-object", []])
def test_scorecard_rejects_present_non_object_outcome_join(invalid_join: object) -> None:
    module = _load_script_module(
        "scorecard_outcome_join_invalid_container",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )

    try:
        module.build_scorecard_payload({"systems": [], "outcome_join": invalid_join})
    except TypeError as error:
        assert str(error) == "outcome_join must be an object"
    else:
        raise AssertionError("present non-object outcome_join must fail clearly")


def test_scorecard_main_does_not_publish_present_null_outcome_join(tmp_path) -> None:
    module = _load_script_module(
        "scorecard_outcome_join_null_main",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    input_path = tmp_path / "comparison.json"
    output_path = tmp_path / "scorecard.json"
    input_path.write_text(
        json.dumps({"systems": [], "outcome_join": None}),
        encoding="utf-8",
    )

    try:
        module.main(["--input", str(input_path), "--output", str(output_path)])
    except TypeError as error:
        assert str(error) == "outcome_join must be an object"
    else:
        raise AssertionError("present null outcome_join must fail clearly")
    assert not output_path.exists()


@pytest.mark.parametrize("invalid_systems", [None, "not-a-list", {}, 3])
def test_scorecard_rejects_present_non_list_systems(invalid_systems: object) -> None:
    module = _load_script_module(
        "scorecard_invalid_systems_container",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )

    try:
        module.build_scorecard_payload({"systems": invalid_systems})
    except TypeError as error:
        assert str(error) == "systems must be a list"
    else:
        raise AssertionError("present non-list systems must fail clearly")


def test_scorecard_main_does_not_publish_present_null_systems(tmp_path) -> None:
    module = _load_script_module(
        "scorecard_null_systems_main",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    input_path = tmp_path / "comparison.json"
    output_path = tmp_path / "scorecard.json"
    input_path.write_text(json.dumps({"systems": None}), encoding="utf-8")

    try:
        module.main(["--input", str(input_path), "--output", str(output_path)])
    except TypeError as error:
        assert str(error) == "systems must be a list"
    else:
        raise AssertionError("present null systems must fail clearly")
    assert not output_path.exists()


def test_scorecard_main_invalidates_stale_output_before_validation(tmp_path) -> None:
    module = _load_script_module(
        "scorecard_stale_output_main",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    input_path = tmp_path / "comparison.json"
    output_path = tmp_path / "scorecard.json"
    input_path.write_text(json.dumps({"systems": None}), encoding="utf-8")
    output_path.write_text("old successful scorecard\n", encoding="utf-8")

    try:
        module.main(["--input", str(input_path), "--output", str(output_path)])
    except TypeError as error:
        assert str(error) == "systems must be a list"
    else:
        raise AssertionError("invalid input must fail clearly")
    assert not output_path.exists()


def test_scorecard_main_rejects_same_input_and_output_without_mutation(tmp_path) -> None:
    module = _load_script_module(
        "scorecard_same_input_output_main",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    input_output_path = tmp_path / "comparison.json"
    original = json.dumps({"systems": []}) + "\n"
    input_output_path.write_text(original, encoding="utf-8")

    try:
        module.main([
            "--input",
            str(input_output_path),
            "--output",
            str(input_output_path),
        ])
    except ValueError as error:
        assert str(error) == "input and output paths must differ"
    else:
        raise AssertionError("same input and output paths must fail clearly")
    assert input_output_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("invalid_system_entry", [None, "not-an-object", 3, []])
def test_scorecard_rejects_non_object_system_entries(invalid_system_entry: object) -> None:
    module = _load_script_module(
        "scorecard_invalid_system_entry",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )

    try:
        module.build_scorecard_payload({"systems": [invalid_system_entry]})
    except TypeError as error:
        assert str(error) == "systems entries must be objects"
    else:
        raise AssertionError("non-object systems entries must fail clearly")


def test_scorecard_main_does_not_publish_non_object_system_entry(tmp_path) -> None:
    module = _load_script_module(
        "scorecard_non_object_system_entry_main",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    input_path = tmp_path / "comparison.json"
    output_path = tmp_path / "scorecard.json"
    input_path.write_text(json.dumps({"systems": [None]}), encoding="utf-8")

    try:
        module.main(["--input", str(input_path), "--output", str(output_path)])
    except TypeError as error:
        assert str(error) == "systems entries must be objects"
    else:
        raise AssertionError("non-object systems entries must fail clearly")
    assert not output_path.exists()


def test_scorecard_main_writes_the_join_into_the_output_file(tmp_path) -> None:
    module = _load_script_module(
        "scorecard_outcome_join_written",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    identity = {
        "system_id": "tensor-grep",
        "instance_id": "clap-lex-parse",
        "repo_commit": "a" * 40,
        "tool_version": "1.113.3",
        "model_id": "gpt-5.6-sol",
        "budget_id": "tokens=16000",
    }
    input_path = tmp_path / "comparison.json"
    output_path = tmp_path / "scorecard.json"
    input_path.write_text(
        json.dumps({
            "artifact": "external_agent_patch_driver_comparison",
            "systems": [],
            "outcome_join": {
                "predictions": [{**identity, "predicted_validation_commands": ["cargo test"]}],
                "outcomes": [{**identity, "execution_observed": False, "validation_passed": True}],
            },
        }),
        encoding="utf-8",
    )
    exit_code = module.main(["--input", str(input_path), "--output", str(output_path)])
    assert exit_code == 0
    written = json.loads(output_path.read_text(encoding="utf-8"))
    join = written["outcome_join"]
    assert join["artifact"] == "agent_outcome_join"
    assert join["joined"][0]["outcome_state"] == "unavailable"
    assert join["joined"][0]["verified_task_success"] is False
    assert join["summary"]["incomplete_cases"] == 1
    assert join["summary"]["verified_task_success_rate"] is None
    assert not list(tmp_path.glob(f".{output_path.name}.*.tmp"))


def test_scorecard_join_loader_restores_a_preexisting_sys_modules_entry() -> None:
    module = _load_script_module(
        "scorecard_join_loader_restores",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    sentinel = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("agent_outcome_join", loader=None)
    )
    sys.modules["agent_outcome_join"] = sentinel
    try:
        payload = module.build_scorecard_payload({"systems": []})
        assert payload["outcome_join"]["artifact"] == "agent_outcome_join"
        assert sys.modules["agent_outcome_join"] is sentinel
    finally:
        del sys.modules["agent_outcome_join"]


def test_scorecard_join_loader_leaves_no_entry_when_none_existed() -> None:
    module = _load_script_module(
        "scorecard_join_loader_no_leak",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    assert "agent_outcome_join" not in sys.modules
    payload = module.build_scorecard_payload({"systems": []})
    assert payload["outcome_join"]["artifact"] == "agent_outcome_join"
    assert "agent_outcome_join" not in sys.modules


def test_scorecard_join_loader_restores_a_preexisting_none_entry() -> None:
    module = _load_script_module(
        "scorecard_join_loader_none_entry",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    sys.modules["agent_outcome_join"] = None
    try:
        payload = module.build_scorecard_payload({"systems": []})
        assert payload["outcome_join"]["artifact"] == "agent_outcome_join"
        assert "agent_outcome_join" in sys.modules
        assert sys.modules["agent_outcome_join"] is None
    finally:
        del sys.modules["agent_outcome_join"]


@pytest.mark.parametrize("prior_sentinel", [False, True])
def test_scorecard_join_loader_serializes_concurrent_sys_modules_access(
    monkeypatch: pytest.MonkeyPatch, prior_sentinel: bool
) -> None:
    module = _load_script_module(
        f"scorecard_join_loader_concurrent_{prior_sentinel}",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    module_name = "agent_outcome_join"
    previous = sys.modules.get(module_name, _MISSING)
    sentinel = object()
    if prior_sentinel:
        sys.modules[module_name] = sentinel
    else:
        sys.modules.pop(module_name, None)

    ready = threading.Barrier(3)
    first_entered = threading.Event()
    second_attempted = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    call_index_lock = threading.Lock()
    call_index = 0

    class BlockingLoader:
        def create_module(self, spec):
            return None

        def exec_module(self, loaded_module):
            nonlocal call_index
            with call_index_lock:
                index = call_index
                call_index += 1
            if index == 0:
                first_entered.set()
                if not release_first.wait(timeout=2):
                    raise AssertionError("first loader call was not released")
            else:
                second_entered.set()

    loader = BlockingLoader()

    def fake_spec_from_file_location(name, location):
        return importlib.machinery.ModuleSpec(name, loader)

    monkeypatch.setattr(
        module.importlib.util, "spec_from_file_location", fake_spec_from_file_location
    )

    def load(role):
        ready.wait(timeout=2)
        if role == "second":
            if not first_entered.wait(timeout=2):
                raise AssertionError("first loader call did not start")
            second_attempted.set()
        return module._load_outcome_join_module()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(load, "first"), executor.submit(load, "second")]
            try:
                ready.wait(timeout=2)
                assert first_entered.wait(timeout=2)
                assert second_attempted.wait(timeout=2)
                assert not second_entered.wait(timeout=1)
            finally:
                release_first.set()
            results = [future.result(timeout=2) for future in futures]
        assert len(results) == 2
        if prior_sentinel:
            assert sys.modules[module_name] is sentinel
        else:
            assert module_name not in sys.modules
    finally:
        if previous is _MISSING:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


def test_base_payload_declares_the_outcome_join_identity_contract() -> None:
    import argparse

    module = _load_script_module(
        "run_agent_workflow_benchmarks_identity_contract",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )
    args = argparse.Namespace(
        iterations=1,
        seed=42,
        max_files=3,
        max_sources=5,
        max_tokens=1200,
        max_repo_files=512,
        files=250,
        loc=12500,
        pattern="alpha",
        replacement="beta",
    )
    payload = module.build_base_payload(args)
    assert payload["outcome_join_identity_fields"] == [
        "system_id",
        "instance_id",
        "repo_commit",
        "tool_version",
        "model_id",
        "budget_id",
    ]
    assert payload["artifact"] == "bench_agent_workflow"
    assert payload["suite"] == "run_agent_workflow_benchmarks"
    assert payload["workflow_surfaces"] == ["agent_capsule", "edit_loop"]


def _load_registered_script_module(name: str, rel_path: str):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(name, root / rel_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name, _MISSING)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is _MISSING:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
    return module


def test_workflow_identity_fields_match_the_join_modules_definition() -> None:
    bench = _load_script_module(
        "run_agent_workflow_benchmarks_identity_drift",
        "benchmarks/run_agent_workflow_benchmarks.py",
    )
    join = _load_registered_script_module(
        "agent_outcome_join",
        "benchmarks/agent_outcome_join.py",
    )
    assert bench.OUTCOME_JOIN_IDENTITY_FIELDS == join.IDENTITY_FIELDS
    assert join.IDENTITY_FIELDS == join.ANNOTATED_IDENTITY_FIELDS
