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
        "passed": True,
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
