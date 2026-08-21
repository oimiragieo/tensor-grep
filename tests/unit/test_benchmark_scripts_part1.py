import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    "name,rel_path",
    [
        ("run_compat_checks_zipslip", "benchmarks/run_compat_checks.py"),
        ("run_benchmarks_zipslip", "benchmarks/run_benchmarks.py"),
    ],
)
def test_extract_windows_rg_bundle_rejects_zip_slip(name, rel_path, tmp_path):
    # A crafted rg.zip with a member that escapes the destination (zip-slip) must be REFUSED
    # before extraction, not written outside benchmarks_dir.
    module = _load_script_module(name, rel_path)
    bench_dir = tmp_path / name
    bench_dir.mkdir()
    archive = bench_dir / "rg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ripgrep/rg.exe", b"binary")
        zf.writestr("../escape.exe", b"evil")  # escapes benchmarks_dir
    with pytest.raises(RuntimeError, match="escapes destination"):
        module.extract_windows_rg_bundle(bench_dir)
    assert not (tmp_path / "escape.exe").exists(), "zip-slip member was written outside the dir"


def test_run_gpu_native_benchmarks_gpu_proof_summary_reports_unsupported_route():
    module = _load_script_module(
        "run_gpu_native_benchmarks_script_gpu_proof_summary",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    scale_summary = module.build_native_scale_gate_summary(
        [
            {
                "size_label": "1GB",
                "tg_gpu": {
                    "status": "UNSUPPORTED",
                    "routing_backend": "NativeCpuBackend",
                    "sidecar_used": False,
                },
            }
        ],
        correctness_checks=[],
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )

    summary = module.build_gpu_proof_summary(
        scale_gate_summary=scale_summary,
        public_managed_gpu_proof_gate=module.build_public_managed_gpu_proof_gate(
            tg_binary_metadata={},
            scale_gate_summary=scale_summary,
            requested=False,
        ),
    )

    assert summary["status"] == "unsupported"
    assert summary["local_native_gpu_proof"] is False
    assert summary["public_gpu_proof"] is False
    assert summary["native_gpu_unavailable"] is True
    assert "native_cuda_runtime_unsupported" in summary["blockers"]
    assert summary["next_action"] == "fix-native-cuda-routing-before-benchmarking-speed"


def test_run_gpu_native_benchmarks_public_summary_uses_many_pattern_public_proof():
    module = _load_script_module(
        "run_gpu_native_benchmarks_script_public_many_pattern_summary",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    scale_summary = _native_gpu_scale_summary_with_speed_failure(module)
    public_gate = module.build_public_managed_gpu_proof_gate(
        tg_binary_metadata={
            "kind": "managed-native",
            "native_frontdoor_flavor": "nvidia",
            "native_frontdoor_requested_flavor": "nvidia",
            "native_frontdoor_asset_name": "tg-windows-amd64-nvidia.exe",
            "native_frontdoor_metadata_status": "present",
            "native_frontdoor_metadata_version": "1.12.34",
            "expected_version": "1.12.34",
            "version_status": "matches",
        },
        scale_gate_summary=scale_summary,
        advanced_payload={"multi_pattern": _passing_many_pattern_payload(module)},
    )

    summary = module.build_gpu_proof_summary(
        scale_gate_summary=scale_summary,
        public_managed_gpu_proof_gate=public_gate,
    )

    assert scale_summary["promotion_ready"] is False
    assert public_gate["status"] == "PASS"
    assert summary["status"] == "public_promotion_ready"
    assert summary["gpu_evidence_status"] == "promotion_ready"
    assert summary["native_gpu_unavailable"] is False
    assert summary["not_gpu_proof_reason"] is None
    assert summary["scale_gate_promotion_ready"] is False
    assert summary["public_workload_class"] == module.NATIVE_MANY_PATTERN_WORKLOAD_CLASS


def test_run_pytest_stable_should_build_windows_friendly_default_command():
    module = _load_script_module("run_pytest_stable_script", "scripts/run_pytest_stable.py")

    command = module.build_pytest_command(
        timeout_s=180, extra_args=["tests/unit/test_cli_modes.py", "-x"]
    )

    assert command[:4] == ["uv", "run", "pytest", "-q"]
    assert "--capture=tee-sys" in command
    assert "console_output_style=classic" in command
    assert "faulthandler_timeout=180" in command
    assert "faulthandler_exit_on_timeout=true" in command
    assert command[-2:] == ["tests/unit/test_cli_modes.py", "-x"]


def test_run_pytest_stable_should_write_log_and_report(monkeypatch, tmp_path, capsys):
    module = _load_script_module("run_pytest_stable_report_script", "scripts/run_pytest_stable.py")
    command = ["uv", "run", "pytest", "-q"]
    log_path = tmp_path / "pytest_full.log"
    report_path = tmp_path / "pytest_full_report.json"

    class _FakeProcess:
        def __init__(self):
            self.stdout = iter(["..s\n", "10 passed in 1.23s\n"])

        def wait(self):
            return 0

    captured: dict[str, object] = {}

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(module.subprocess, "Popen", _fake_popen)

    exit_code = module.run_pytest_command(
        command,
        log_path=log_path,
        report_path=report_path,
        cwd=tmp_path,
    )

    assert exit_code == 0
    assert captured["cmd"] == command
    assert log_path.read_text(encoding="utf-8") == "..s\n10 passed in 1.23s\n"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["artifact"] == "pytest_full_report"
    assert report["exit_code"] == 0
    assert report["command"] == command
    assert report["log_path"] == str(log_path.resolve())
    assert report["line_count"] == 2
    assert report["tail"] == ["..s\n", "10 passed in 1.23s\n"]
    stdout = capsys.readouterr().out
    assert "..s" in stdout
    assert "10 passed in 1.23s" in stdout


def test_run_pytest_stable_should_fallback_when_console_encoding_cannot_encode(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_pytest_stable_encoding_script", "scripts/run_pytest_stable.py"
    )
    command = ["uv", "run", "pytest", "-q"]
    log_path = tmp_path / "pytest_full.log"
    report_path = tmp_path / "pytest_full_report.json"

    class _FakeProcess:
        def __init__(self):
            self.stdout = iter(["ok\n", "bad \ufffd char\n"])

        def wait(self):
            return 0

    class _FakeBuffer:
        def __init__(self):
            self.writes: list[bytes] = []

        def write(self, payload: bytes):
            self.writes.append(payload)

    class _FakeStdout:
        def __init__(self):
            self.buffer = _FakeBuffer()
            self.encoding = "cp1252"
            self.calls = 0

        def write(self, text: str):
            self.calls += 1
            if "\ufffd" in text:
                raise UnicodeEncodeError("charmap", text, 0, 1, "cannot encode")

        def flush(self):
            return None

    fake_stdout = _FakeStdout()

    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr(module.sys, "stdout", fake_stdout)

    exit_code = module.run_pytest_command(
        command,
        log_path=log_path,
        report_path=report_path,
        cwd=tmp_path,
    )

    assert exit_code == 0
    assert log_path.read_text(encoding="utf-8") == "ok\nbad \ufffd char\n"
    assert fake_stdout.buffer.writes


def test_run_benchmarks_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module("run_benchmarks_script", "benchmarks/run_benchmarks.py")
    monkeypatch.delenv("TENSOR_GREP_BENCH_DATA_DIR", raising=False)

    path = module.resolve_bench_data_dir()

    assert path.parts[-2:] == ("artifacts", "bench_data")


def test_build_attempt_ledger_should_normalize_payload_shape(tmp_path):
    module = _load_script_module(
        "build_attempt_ledger_script", "benchmarks/build_attempt_ledger.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    payload = module.build_attempt_ledger_payload(
        {
            "task_id": "tg-task-1",
            "root": str(repo_root),
            "attempts": [
                {
                    "attempt_id": "attempt-1",
                    "parent_attempt_id": None,
                    "kind": "rewrite_apply_verify",
                    "status": "validation_failed",
                    "retryable": True,
                    "retry_stage": "validation",
                    "retry_reason": "lint-failed",
                    "checkpoint_id": "chk-1",
                    "audit_manifest_path": "artifacts/audit/attempt-1.json",
                    "validation_success": False,
                    "score_artifact": None,
                    "inputs": ["artifacts/plans/plan-1.json"],
                    "outputs": ["artifacts/diffs/attempt-1.diff"],
                },
                {
                    "attempt_id": "attempt-2",
                    "parent_attempt_id": "attempt-1",
                    "kind": "rewrite_apply_verify",
                    "status": "accepted",
                    "retryable": False,
                    "retry_stage": "none",
                    "retry_reason": "accepted",
                    "checkpoint_id": "chk-2",
                    "audit_manifest_path": "artifacts/audit/attempt-2.json",
                    "validation_success": True,
                    "score_artifact": "artifacts/scores/attempt-2.json",
                    "inputs": ["artifacts/diffs/attempt-2.diff"],
                    "outputs": ["artifacts/scores/attempt-2.json"],
                },
            ],
            "final_outcome": {
                "status": "accepted",
                "accepted_attempt_id": "attempt-2",
                "score_artifact": "artifacts/scores/attempt-2.json",
                "summary": "accepted after one retry",
            },
            "replay": {
                "preserve_attempt_ids": True,
                "partial_retry_ledger": [
                    {
                        "attempt_id": "attempt-1",
                        "resumed_from": "validation",
                        "resumed_as": "attempt-2",
                        "reason": "lint-failed",
                    }
                ],
                "audit_chain": [
                    "artifacts/audit/attempt-1.json",
                    "artifacts/audit/attempt-2.json",
                ],
                "next_action": "score accepted attempt",
            },
        }
    )

    assert payload["artifact"] == "agent_attempt_ledger"
    assert payload["suite"] == "agent_loop"
    assert payload["task_id"] == "tg-task-1"
    assert payload["root"] == str(repo_root)
    assert len(payload["attempts"]) == 2
    assert payload["final_outcome"]["accepted_attempt_id"] == "attempt-2"
    assert payload["replay"]["preserve_attempt_ids"] is True
    assert payload["generated_at_epoch_s"] > 0


def test_run_compat_checks_should_ship_default_tg_output_schema():
    module = _load_script_module(
        "run_compat_checks_schema_script", "benchmarks/run_compat_checks.py"
    )
    schema_path = module.default_schema_path()

    assert schema_path.exists()

    module.validate_json_instance(
        {
            "version": 1,
            "routing_backend": "NativeCpuBackend",
            "routing_reason": "json_output",
            "sidecar_used": False,
            "requested_gpu_device_ids": [],
            "routing_gpu_device_ids": [],
            "query": "ERROR",
            "path": "bench_data",
            "total_matches": 1,
            "matches": [
                {
                    "file": "bench_data/app.log",
                    "line": 1,
                    "text": "ERROR timeout",
                }
            ],
        },
        schema_path,
    )
    module.validate_json_instance(
        {
            "version": 1,
            "routing_backend": "NativeCpuBackend",
            "routing_reason": "python-cli",
            "sidecar_used": False,
            "requested_gpu_device_ids": [],
            "routing_gpu_device_ids": [],
            "routing_gpu_chunk_plan_mb": [],
            "routing_distributed": False,
            "routing_worker_count": 1,
            "total_matches": 1,
            "total_files": 1,
            "matched_file_paths": ["bench_data/app.log"],
            "match_counts_by_file": {"bench_data/app.log": 1},
            "matches": [
                {
                    "file": "bench_data/app.log",
                    "line_number": 1,
                    "text": "ERROR timeout",
                }
            ],
        },
        schema_path,
    )


def test_run_compat_checks_routing_metadata_probe_should_disable_ignores(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_compat_checks_routing_script", "benchmarks/run_compat_checks.py"
    )
    bench_data_dir = tmp_path / "bench_data"
    bench_data_dir.mkdir()
    captured: dict[str, object] = {}

    def _fake_run_command(cmd, *, env=None, cwd=None):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["cwd"] = cwd
        return module.CommandResult(
            0,
            json.dumps(
                {
                    "version": 1,
                    "routing_backend": "NativeCpuBackend",
                    "routing_reason": "json_output",
                    "sidecar_used": False,
                    "requested_gpu_device_ids": [],
                    "routing_gpu_device_ids": [],
                    "query": "ERROR",
                    "path": str(bench_data_dir),
                    "total_matches": 1,
                    "matches": [
                        {
                            "file": str(bench_data_dir / "app.log"),
                            "line": 1,
                            "text": "ERROR timeout",
                        }
                    ],
                }
            ),
            "",
        )

    monkeypatch.setattr(module, "run_command", _fake_run_command)

    report = module.validate_routing_metadata(
        Path("tg"),
        bench_data_dir,
        module.default_schema_path(),
        Path("rg"),
    )

    assert report["valid"] is True
    assert "payload" not in report
    assert report["payload_elided"] is True
    assert report["payload_summary"]["total_matches"] == 1
    assert report["payload_summary"]["matches_preview"] == [
        {
            "file": str(bench_data_dir / "app.log"),
            "line": 1,
            "text": "ERROR timeout",
        }
    ]
    assert report["payload_summary"]["matches_omitted"] == 0
    assert captured["cmd"] == ["tg", "--json", "--no-ignore", "ERROR", str(bench_data_dir)]
    assert captured["env"]["TG_RG_PATH"] == "rg"
    assert captured["cwd"] == module.ROOT_DIR


def test_run_compat_checks_routing_metadata_report_caps_payload_previews():
    module = _load_script_module(
        "run_compat_checks_routing_cap_script", "benchmarks/run_compat_checks.py"
    )
    payload = {
        "version": 1,
        "routing_backend": "NativeCpuBackend",
        "routing_reason": "json_output",
        "sidecar_used": False,
        "query": "ERROR",
        "path": "bench_data",
        "total_matches": 5,
        "total_files": 3,
        "matched_file_paths": ["a.log", "b.log", "c.log"],
        "match_counts_by_file": {"a.log": 2, "b.log": 2, "c.log": 1},
        "matches": [
            {"file": f"file_{index}.log", "line": index + 1, "text": "ERROR"} for index in range(5)
        ],
    }

    summary = module.summarize_routing_payload(
        payload,
        match_preview_limit=2,
        path_preview_limit=1,
    )

    assert summary["matched_file_paths_preview"] == ["a.log"]
    assert summary["matched_file_paths_omitted"] == 2
    assert [match["file"] for match in summary["matches_preview"]] == [
        "file_0.log",
        "file_1.log",
    ]
    assert summary["matches_omitted"] == 3
    assert summary["match_counts_by_file_count"] == 3


def test_run_compat_checks_scenario_diff_caps_line_lists():
    module = _load_script_module(
        "run_compat_checks_diff_cap_script", "benchmarks/run_compat_checks.py"
    )
    scenario = {"name": "large-diff", "comparison": "lines"}
    rg_output = "\n".join(f"rg-line-{index:03d}" for index in range(80))
    tg_output = "\n".join(f"tg-line-{index:03d}" for index in range(80))

    report = module.compare_scenario(
        scenario,
        module.CommandResult(0, rg_output, ""),
        module.CommandResult(0, tg_output, ""),
    )

    assert report["status"] == "FAIL"
    assert report["reason"] == "sorted-line-diff"
    assert report["missing_lines_total"] == 80
    assert report["extra_lines_total"] == 80
    assert len(report["missing_lines"]) == module.LINE_DIFF_PREVIEW_LIMIT
    assert len(report["extra_lines"]) == module.LINE_DIFF_PREVIEW_LIMIT
    assert report["missing_lines_omitted"] == 80 - module.LINE_DIFF_PREVIEW_LIMIT
    assert report["extra_lines_omitted"] == 80 - module.LINE_DIFF_PREVIEW_LIMIT


def test_run_compat_checks_progress_always_uses_stderr_without_changing_report(
    monkeypatch, tmp_path, capsys
):
    module = _load_script_module(
        "run_compat_checks_progress_script", "benchmarks/run_compat_checks.py"
    )
    tg_binary = tmp_path / "tg"
    tg_binary.write_text("fake tg", encoding="utf-8")
    bench_data_dir = tmp_path / "bench_data"
    bench_data_dir.mkdir()
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    output_path = tmp_path / "compat_report.json"
    expected_report = {
        "suite": "run_compat_checks",
        "scenarios": [],
        "routing_metadata": {"valid": True},
        "pytest": {"passed": True},
        "all_passed": True,
    }
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: Path("rg"))
    monkeypatch.setattr(module, "run_compat_suite", lambda **_kwargs: dict(expected_report))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_compat_checks.py",
            "--binary",
            str(tg_binary),
            "--bench-data-dir",
            str(bench_data_dir),
            "--schema",
            str(schema_path),
            "--output",
            str(output_path),
            "--progress",
            "always",
        ],
    )

    exit_code = module.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == expected_report
    assert "[progress]" in captured.err
    assert "[progress]" not in captured.out


def test_run_compat_checks_progress_is_on_by_default(monkeypatch, tmp_path, capsys):
    module = _load_script_module(
        "run_compat_checks_default_progress_script", "benchmarks/run_compat_checks.py"
    )
    tg_binary = tmp_path / "tg"
    tg_binary.write_text("fake tg", encoding="utf-8")
    bench_data_dir = tmp_path / "bench_data"
    bench_data_dir.mkdir()
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    output_path = tmp_path / "compat_report.json"
    expected_report = {
        "suite": "run_compat_checks",
        "scenarios": [],
        "routing_metadata": {"valid": True},
        "pytest": {"passed": True},
        "all_passed": True,
    }
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: Path("rg"))
    monkeypatch.setattr(module, "run_compat_suite", lambda **_kwargs: dict(expected_report))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_compat_checks.py",
            "--binary",
            str(tg_binary),
            "--bench-data-dir",
            str(bench_data_dir),
            "--schema",
            str(schema_path),
            "--output",
            str(output_path),
        ],
    )

    exit_code = module.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[progress] compat-suite start" in captured.err
    assert "[progress] compat-suite done" in captured.err
    assert "[progress]" not in captured.out


def test_build_attempt_ledger_cli_should_write_output_file(tmp_path):
    module = _load_script_module(
        "build_attempt_ledger_cli_script", "benchmarks/build_attempt_ledger.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    input_path = tmp_path / "attempt_ledger_input.json"
    output_path = tmp_path / "attempt_ledger_output.json"
    input_path.write_text(
        json.dumps(
            {
                "task_id": "tg-task-2",
                "root": str(repo_root),
                "attempts": [
                    {
                        "attempt_id": "attempt-a",
                        "parent_attempt_id": None,
                        "kind": "rewrite_apply_verify",
                        "status": "accepted",
                        "retryable": False,
                        "retry_stage": "none",
                        "retry_reason": "accepted",
                        "checkpoint_id": "chk-a",
                        "audit_manifest_path": "artifacts/audit/attempt-a.json",
                        "validation_success": True,
                        "score_artifact": "artifacts/scores/attempt-a.json",
                        "inputs": [],
                        "outputs": ["artifacts/scores/attempt-a.json"],
                    }
                ],
                "final_outcome": {
                    "status": "accepted",
                    "accepted_attempt_id": "attempt-a",
                    "score_artifact": "artifacts/scores/attempt-a.json",
                    "summary": "accepted",
                },
                "replay": {
                    "preserve_attempt_ids": True,
                    "partial_retry_ledger": [],
                    "audit_chain": ["artifacts/audit/attempt-a.json"],
                    "next_action": "none",
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = module.main(["--input", str(input_path), "--output", str(output_path)])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["artifact"] == "agent_attempt_ledger"
    assert payload["suite"] == "agent_loop"
    assert payload["task_id"] == "tg-task-2"


def test_run_benchmarks_should_honor_data_dir_override(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script", "benchmarks/run_benchmarks.py")
    override = tmp_path / "bench_override"
    monkeypatch.setenv("TENSOR_GREP_BENCH_DATA_DIR", str(override))

    path = module.resolve_bench_data_dir()

    assert path == override.resolve()


def test_run_benchmarks_should_target_native_tg_binary(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script_cmd", "benchmarks/run_benchmarks.py")
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd = module.build_tg_benchmark_cmd(["ERROR", "bench_data"])

    assert cmd[0] == str(tg_binary)
    assert cmd[1:] == ["search", "--no-ignore", "ERROR", "bench_data"]


def test_run_benchmarks_should_classify_explicit_binary_source(tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_binary_source_explicit", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")

    resolved, source = module.resolve_tg_binary_with_source(str(tg_binary))

    assert resolved == tg_binary.resolve()
    assert source == "explicit_arg"


def test_run_benchmarks_should_classify_default_binary_source(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_binary_source_default", "benchmarks/run_benchmarks.py"
    )
    default_binary = tmp_path / "rust_core" / "target" / "release" / "tg.exe"
    default_binary.parent.mkdir(parents=True, exist_ok=True)
    default_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "default_binary_path", lambda: default_binary)

    resolved, source = module.resolve_tg_binary_with_source()

    assert resolved == default_binary.resolve()
    assert source == "default_binary_path"


def test_run_benchmarks_should_fallback_to_cli_launcher_when_native_binary_is_missing(monkeypatch):
    module = _load_script_module(
        "run_benchmarks_script_launcher_cmd", "benchmarks/run_benchmarks.py"
    )
    missing_binary = Path("missing-tg.exe")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: missing_binary)
    monkeypatch.setattr(
        module,
        "resolve_tg_cli_launcher",
        lambda *_args, **_kwargs: ["python", "-m", "tensor_grep"],
    )

    cmd = module.build_tg_benchmark_cmd(["ERROR", "bench_data"])

    assert cmd == ["python", "-m", "tensor_grep", "search", "--no-ignore", "ERROR", "bench_data"]


def test_run_benchmarks_should_classify_python_module_launcher(monkeypatch):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_module", "benchmarks/run_benchmarks.py"
    )
    missing_binary = Path("missing-tg.exe")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: missing_binary)
    monkeypatch.setattr(
        module,
        "resolve_tg_cli_launcher",
        lambda *_args, **_kwargs: [sys.executable, "-m", "tensor_grep"],
    )

    cmd, mode = module.build_tg_benchmark_cmd(["ERROR", "bench_data"], return_mode=True)

    assert cmd == [
        sys.executable,
        "-m",
        "tensor_grep",
        "search",
        "--no-ignore",
        "ERROR",
        "bench_data",
    ]
    assert mode == "python_module_launcher"


def test_run_benchmarks_should_classify_explicit_binary_launcher(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_binary", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd, mode = module.build_tg_benchmark_cmd(["ERROR", "bench_data"], return_mode=True)

    assert cmd == [str(tg_binary), "search", "--no-ignore", "ERROR", "bench_data"]
    assert mode == "explicit_binary"


def test_run_benchmarks_should_force_explicit_fast_binary_launcher(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_fast_binary", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "tg-search-fast.exe"
    tg_binary.write_text("binary", encoding="utf-8")

    cmd, mode = module.build_tg_benchmark_cmd(
        ["ERROR", "bench_data"],
        binary=tg_binary,
        return_mode=True,
        launcher_mode="explicit_fast_binary",
    )

    assert cmd == [str(tg_binary), "--no-ignore", "ERROR", "bench_data"]
    assert mode == "explicit_fast_binary"


def test_run_benchmarks_should_force_python_module_launcher(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_forced_python", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)
    monkeypatch.setattr(
        module,
        "resolve_tg_cli_launcher",
        lambda *_args, **_kwargs: [sys.executable, "-m", "tensor_grep"],
    )

    cmd, mode = module.build_tg_benchmark_cmd(
        ["ERROR", "bench_data"],
        return_mode=True,
        launcher_mode="python_module_launcher",
    )

    assert cmd == [
        sys.executable,
        "-m",
        "tensor_grep",
        "search",
        "--no-ignore",
        "ERROR",
        "bench_data",
    ]
    assert mode == "python_module_launcher"


def test_run_benchmarks_should_force_python_module_rust_first_launcher(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_forced_rust_first", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd, mode, env = module.build_tg_benchmark_cmd_with_mode(
        ["ERROR", "bench_data"],
        return_mode=True,
        return_env=True,
        launcher_mode="python_module_rust_first",
    )

    assert cmd == [
        sys.executable,
        "-m",
        "tensor_grep",
        "search",
        "--no-ignore",
        "ERROR",
        "bench_data",
    ]
    assert mode == "python_module_rust_first"
    assert env == {"TG_RUST_FIRST_SEARCH": "1"}


def test_run_benchmarks_should_force_explicit_binary_early_rg_launcher(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_forced_early_rg", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd, mode, env = module.build_tg_benchmark_cmd_with_mode(
        ["ERROR", "bench_data"],
        return_mode=True,
        return_env=True,
        launcher_mode="explicit_binary_early_rg",
    )

    assert cmd == [str(tg_binary), "search", "--no-ignore", "ERROR", "bench_data"]
    assert mode == "explicit_binary_early_rg"
    assert env == {"TG_RUST_EARLY_RG": "1"}


def test_run_benchmarks_should_use_positional_launcher_for_supported_plain_search(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_forced_positional", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd, mode = module.build_tg_benchmark_cmd(
        ["ERROR", "bench_data"],
        return_mode=True,
        launcher_mode="explicit_binary_positional",
    )

    assert cmd == [str(tg_binary), "ERROR", "bench_data"]
    assert mode == "explicit_binary_positional"


def test_run_benchmarks_should_use_positional_early_rg_launcher_for_supported_plain_search(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_forced_positional_early_rg",
        "benchmarks/run_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd, mode, env = module.build_tg_benchmark_cmd_with_mode(
        ["ERROR", "bench_data"],
        return_mode=True,
        return_env=True,
        launcher_mode="explicit_binary_positional_early_rg",
    )

    assert cmd == [str(tg_binary), "ERROR", "bench_data"]
    assert mode == "explicit_binary_positional_early_rg"
    assert env == {"TG_RUST_EARLY_POSITIONAL_RG": "1"}


def test_run_benchmarks_should_use_positional_early_rg_launcher_for_max_count(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_forced_positional_early_rg_max_count",
        "benchmarks/run_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd, mode, env = module.build_tg_benchmark_cmd_with_mode(
        ["-m", "1", "ERROR", "bench_data"],
        return_mode=True,
        return_env=True,
        launcher_mode="explicit_binary_positional_early_rg",
    )

    assert cmd == [str(tg_binary), "-m", "1", "ERROR", "bench_data"]
    assert mode == "explicit_binary_positional_early_rg"
    assert env == {"TG_RUST_EARLY_POSITIONAL_RG": "1"}


def test_run_benchmarks_should_fallback_from_positional_launcher_for_unsupported_shapes(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_positional_fallback", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd, mode = module.build_tg_benchmark_cmd(
        ["-C", "2", "CRITICAL", "bench_data"],
        return_mode=True,
        launcher_mode="explicit_binary_positional",
    )

    assert cmd == [str(tg_binary), "search", "--no-ignore", "-C", "2", "CRITICAL", "bench_data"]
    assert mode == "explicit_binary_positional"


def test_run_benchmarks_should_fallback_from_positional_early_rg_launcher_for_unsupported_shapes(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_positional_early_rg_fallback",
        "benchmarks/run_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd, mode, env = module.build_tg_benchmark_cmd_with_mode(
        ["-C", "2", "CRITICAL", "bench_data"],
        return_mode=True,
        return_env=True,
        launcher_mode="explicit_binary_positional_early_rg",
    )

    assert cmd == [str(tg_binary), "search", "--no-ignore", "-C", "2", "CRITICAL", "bench_data"]
    assert mode == "explicit_binary_positional_early_rg"
    assert env == {"TG_RUST_EARLY_POSITIONAL_RG": "1"}


def test_run_benchmarks_should_refuse_stale_in_tree_native_binary_by_default(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_stale_in_tree_refusal",
        "benchmarks/run_benchmarks.py",
    )
    tg_binary = tmp_path / "repo" / "rust_core" / "target" / "release" / "tg.exe"
    tg_binary.parent.mkdir(parents=True, exist_ok=True)
    tg_binary.write_text("stale", encoding="utf-8")
    output_path = tmp_path / "bench.json"

    monkeypatch.setattr(
        "sys.argv",
        ["run_benchmarks.py", "--binary", str(tg_binary), "--output", str(output_path)],
    )
    monkeypatch.setattr(
        module,
        "benchmark_binary_warnings",
        lambda _binary: ["tensor-grep benchmark warning: stale in-tree native tg binary"],
    )
    monkeypatch.setattr(
        module,
        "generate_test_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale benchmark binary should fail before generating data")
        ),
    )

    exit_code = module.main()

    assert exit_code == 2
    assert not output_path.exists()


def test_run_native_cpu_benchmarks_should_refuse_stale_in_tree_native_binary_by_default(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_native_cpu_benchmarks_script_stale_in_tree_refusal",
        "benchmarks/run_native_cpu_benchmarks.py",
    )
    tg_binary = tmp_path / "repo" / "rust_core" / "target" / "release" / "tg.exe"
    tg_binary.parent.mkdir(parents=True, exist_ok=True)
    tg_binary.write_text("stale", encoding="utf-8")
    output_path = tmp_path / "native-cpu.json"

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_native_cpu_benchmarks.py",
            "--binary",
            str(tg_binary),
            "--output",
            str(output_path),
        ],
    )
    monkeypatch.setattr(
        module,
        "benchmark_binary_warnings",
        lambda _binary: ["tensor-grep benchmark warning: stale in-tree native tg binary"],
    )
    monkeypatch.setattr(
        module,
        "resolve_rg_binary",
        lambda: (_ for _ in ()).throw(
            AssertionError("stale benchmark binary should fail before resolving rg")
        ),
    )

    exit_code = module.main()

    assert exit_code == 2
    assert not output_path.exists()


def test_run_benchmarks_should_force_discovered_cli_binary_launcher(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_launcher_mode_forced_cli", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)
    monkeypatch.setattr(
        module,
        "resolve_tg_cli_launcher",
        lambda *_args, **_kwargs: ["tg.exe"],
    )

    cmd, mode = module.build_tg_benchmark_cmd(
        ["ERROR", "bench_data"],
        return_mode=True,
        launcher_mode="discovered_cli_binary",
    )

    assert cmd == ["tg.exe", "search", "--no-ignore", "ERROR", "bench_data"]
    assert mode == "discovered_cli_binary"


def test_run_benchmarks_should_force_cpu_when_native_flag_is_enabled(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script_native_cmd", "benchmarks/run_benchmarks.py")
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(
        module, "resolve_tg_cli_launcher", lambda *_args, **_kwargs: [str(tg_binary)]
    )

    cmd = module.build_tg_benchmark_cmd(["ERROR", "bench_data"], force_cpu=True)

    assert cmd[0] == str(tg_binary)
    assert cmd[1:] == ["search", "--cpu", "--no-ignore", "ERROR", "bench_data"]


def test_run_benchmarks_should_include_large_file_and_many_file_scenarios(tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_native_scenarios", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "native-tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")

    scenarios = module.build_benchmark_scenarios(
        bench_dir=Path("bench_data"),
        large_file_path=Path("large_fixture.log"),
        many_file_dir=Path("many_files"),
        force_cpu=True,
        binary=tg_binary,
        rg_binary="rg",
    )

    names = [scenario["name"] for scenario in scenarios]
    assert "11. Native Large File Search" in names
    assert "12. Native Many-File Search" in names

    large_scenario = next(s for s in scenarios if s["name"] == "11. Native Large File Search")
    many_scenario = next(s for s in scenarios if s["name"] == "12. Native Many-File Search")
    assert large_scenario["tg_cmd"][:3] == [str(tg_binary), "search", "--no-ignore"]
    assert large_scenario["tg_cmd"][-1] == "large_fixture.log"
    assert many_scenario["tg_cmd"][:3] == [str(tg_binary), "search", "--no-ignore"]
    assert many_scenario["tg_cmd"][-1] == "many_files"


def test_run_native_cpu_benchmarks_should_disable_rg_for_tg_cpu_measurements(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_native_cpu_benchmarks_env_script", "benchmarks/run_native_cpu_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    seen_timing_envs: list[dict[str, str] | None] = []
    seen_sample_envs: list[dict[str, str] | None] = []
    seen_count_envs: list[dict[str, str] | None] = []

    def fake_run_cmd_timing(_cmd, capture_stdout=False, *, env_overrides=None):
        seen_timing_envs.append(env_overrides)
        return 0.1

    def fake_collect_timing_samples(_cmd, sample_count=1, *, env_overrides=None):
        seen_sample_envs.append(env_overrides)
        return 0.1, [0.1] * sample_count

    def fake_run_match_count(_cmd, *, env_overrides=None):
        seen_count_envs.append(env_overrides)
        return {"seconds": 0.1, "total_matches": 2}

    monkeypatch.setattr(module, "run_cmd_timing", fake_run_cmd_timing)
    monkeypatch.setattr(module, "collect_timing_samples", fake_collect_timing_samples)
    monkeypatch.setattr(module, "run_match_count", fake_run_match_count)

    row = module.run_native_cpu_benchmark_case(
        name="count",
        pattern="ERROR",
        target=tmp_path,
        rg_binary="rg",
        tg_binary=tg_binary,
        sample_count=2,
        warmup_runs=1,
        max_ratio_vs_rg=2.0,
        benchmark_count_mode=True,
    )

    assert module.NATIVE_TG_ENV == {"TG_DISABLE_RG": "1"}
    assert seen_timing_envs == [None, module.NATIVE_TG_ENV]
    assert seen_sample_envs == [None, module.NATIVE_TG_ENV]
    assert seen_count_envs == [None, module.NATIVE_TG_ENV]
    assert row["tg_env"] == module.NATIVE_TG_ENV


def test_run_native_cpu_benchmarks_should_build_fair_fixed_multi_pattern_commands(tmp_path):
    module = _load_script_module(
        "run_native_cpu_benchmarks_multi_pattern_script",
        "benchmarks/run_native_cpu_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    target = tmp_path / "fixture.log"
    patterns = ["TODO", "FIXME", "BUG"]

    rg_cmd = module.build_rg_search_command("rg", patterns, target, fixed_strings=True)
    tg_cmd = module.build_tg_cpu_search_command(tg_binary, patterns, target, fixed_strings=True)
    rg_count_cmd = module.build_rg_count_command("rg", patterns, target, fixed_strings=True)
    tg_count_cmd = module.build_tg_cpu_count_command(
        tg_binary, patterns, target, fixed_strings=True
    )

    assert rg_cmd == [
        "rg",
        "--no-ignore",
        "-F",
        "-e",
        "TODO",
        "-e",
        "FIXME",
        "-e",
        "BUG",
        str(target),
    ]
    assert tg_cmd == [
        str(tg_binary),
        "search",
        "--cpu",
        "--no-ignore",
        "-F",
        "-e",
        "TODO",
        "-e",
        "FIXME",
        "-e",
        "BUG",
        str(target),
    ]
    assert rg_count_cmd[:3] == ["rg", "--no-ignore", "-c"]
    assert rg_count_cmd[3:] == rg_cmd[2:]
    assert tg_count_cmd[:5] == [str(tg_binary), "search", "--cpu", "--no-ignore", "-c"]
    assert tg_count_cmd[5:] == tg_cmd[4:]


def test_run_benchmarks_should_extract_windows_rg_zip_when_rg_missing(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script_rg_zip", "benchmarks/run_benchmarks.py")
    bench_dir = tmp_path / "benchmarks"
    archive = bench_dir / "rg.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "fake rg")

    monkeypatch.setattr(module, "__file__", str(bench_dir / "run_benchmarks.py"))
    monkeypatch.setattr(module.shutil, "which", lambda _binary: None)
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")

    resolved = Path(module.resolve_rg_binary())

    assert resolved == bench_dir / "ripgrep-14.1.0-x86_64-pc-windows-msvc" / "rg.exe"
    assert resolved.read_text(encoding="utf-8") == "fake rg"


def test_run_benchmarks_should_record_three_samples_and_median(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script_samples", "benchmarks/run_benchmarks.py")
    tg_binary = tmp_path / "tg"
    tg_binary.write_text("fake tg", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["run_benchmarks.py"])
    monkeypatch.setattr(
        module,
        "SCENARIOS",
        [
            {
                "name": "1. Simple String Match",
                "rg_args": ["rg", "ERROR", "bench_data"],
                "tg_args": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        module,
        "resolve_tg_binary_with_source",
        lambda *_args, **_kwargs: (tg_binary, "explicit_arg"),
    )
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)

    timing_samples = iter(
        [
            9.9,
            8.8,
            0.40,
            0.20,
            0.30,
            0.80,
            0.60,
            0.70,
        ]
    )
    timing_calls: list[list[str]] = []
    capture_calls: list[list[str]] = []

    def _fake_run_cmd_timing(cmd, capture_stdout=False):
        timing_calls.append(cmd)
        return next(timing_samples)

    def _fake_run_cmd_capture(cmd):
        capture_calls.append(cmd)
        if cmd[0] == "rg":
            return 0.0, "rg result"
        return 0.0, "tg result"

    monkeypatch.setattr(module, "run_cmd_timing", _fake_run_cmd_timing)
    monkeypatch.setattr(module, "run_cmd_capture", _fake_run_cmd_capture)

    captured: dict[str, object] = {}

    def _fake_write_json(path, payload):
        captured["path"] = path
        captured["payload"] = payload

    monkeypatch.setattr("tensor_grep.perf_guard.ensure_artifacts_dir", lambda _root: tmp_path)
    monkeypatch.setattr("tensor_grep.perf_guard.write_json", _fake_write_json)

    module.main()

    assert len(timing_calls) == 8
    assert len(capture_calls) == 2
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["artifact"] == "bench_run_benchmarks"
    assert payload["timing_samples_per_scenario"] == 3
    assert payload["environment"]["tg_launcher_mode"] == "explicit_binary"
    assert payload["environment"]["tg_binary_source"] == "explicit_arg"
    rows = payload["rows"]
    assert rows == [
        {
            "name": "1. Simple String Match",
            "rg_samples_s": [0.4, 0.2, 0.3],
            "rg_time_s": 0.3,
            "tg_samples_s": [0.8, 0.6, 0.7],
            "tg_time_s": 0.7,
            "parity": "PASS",
        }
    ]


def test_run_benchmarks_should_record_forced_launcher_mode_in_environment(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_forced_launcher_payload", "benchmarks/run_benchmarks.py"
    )
    monkeypatch.setattr(
        module,
        "SCENARIOS",
        [
            {
                "name": "1. Simple String Match",
                "rg_args": ["rg", "ERROR", "bench_data"],
                "tg_args": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        module, "collect_timing_samples", lambda *_args, **_kwargs: (0.1, [0.1, 0.1, 0.1])
    )
    monkeypatch.setattr(module, "run_cmd_capture", lambda cmd: (0.0, "ok"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_benchmarks.py",
            "--launcher-mode",
            "python_module_launcher",
            "--allow-claim-unsafe-launcher",
        ],
    )

    captured: dict[str, object] = {}
    monkeypatch.setattr("tensor_grep.perf_guard.ensure_artifacts_dir", lambda _root: tmp_path)
    monkeypatch.setattr(
        "tensor_grep.perf_guard.write_json",
        lambda path, payload: captured.update({"path": path, "payload": payload}),
    )

    module.main()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["environment"]["tg_launcher_mode"] == "python_module_launcher"
    assert payload["environment"]["tg_binary_source"] == "default_binary_path"


def test_run_benchmarks_should_record_rust_first_launcher_mode_in_environment(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_benchmarks_script_rust_first_payload", "benchmarks/run_benchmarks.py"
    )
    monkeypatch.setattr(
        module,
        "SCENARIOS",
        [
            {
                "name": "1. Simple String Match",
                "rg_args": ["rg", "ERROR", "bench_data"],
                "tg_args": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        module, "collect_timing_samples", lambda *_args, **_kwargs: (0.1, [0.1, 0.1, 0.1])
    )
    monkeypatch.setattr(module, "run_cmd_capture", lambda cmd, **_kwargs: (0.0, "ok"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_benchmarks.py",
            "--launcher-mode",
            "python_module_rust_first",
            "--allow-claim-unsafe-launcher",
        ],
    )

    captured: dict[str, object] = {}
    monkeypatch.setattr("tensor_grep.perf_guard.ensure_artifacts_dir", lambda _root: tmp_path)
    monkeypatch.setattr(
        "tensor_grep.perf_guard.write_json",
        lambda path, payload: captured.update({"path": path, "payload": payload}),
    )

    module.main()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["environment"]["tg_launcher_mode"] == "python_module_rust_first"


def test_run_benchmarks_should_record_early_rg_launcher_mode_in_environment(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_early_rg_payload", "benchmarks/run_benchmarks.py"
    )
    monkeypatch.setattr(
        module,
        "SCENARIOS",
        [
            {
                "name": "1. Simple String Match",
                "rg_args": ["rg", "ERROR", "bench_data"],
                "tg_args": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        module, "collect_timing_samples", lambda *_args, **_kwargs: (0.1, [0.1, 0.1, 0.1])
    )
    monkeypatch.setattr(module, "run_cmd_capture", lambda cmd, **_kwargs: (0.0, "ok"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_benchmarks.py",
            "--launcher-mode",
            "explicit_binary_early_rg",
            "--allow-claim-unsafe-launcher",
        ],
    )

    captured: dict[str, object] = {}
    monkeypatch.setattr("tensor_grep.perf_guard.ensure_artifacts_dir", lambda _root: tmp_path)
    monkeypatch.setattr(
        "tensor_grep.perf_guard.write_json",
        lambda path, payload: captured.update({"path": path, "payload": payload}),
    )

    module.main()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["environment"]["tg_launcher_mode"] == "explicit_binary_early_rg"


def test_run_benchmarks_should_record_fast_binary_launcher_mode_in_environment(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_benchmarks_script_fast_binary_payload", "benchmarks/run_benchmarks.py"
    )
    monkeypatch.setattr(
        module,
        "SCENARIOS",
        [
            {
                "name": "1. Simple String Match",
                "rg_args": ["rg", "ERROR", "bench_data"],
                "tg_args": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        module, "collect_timing_samples", lambda *_args, **_kwargs: (0.1, [0.1, 0.1, 0.1])
    )
    monkeypatch.setattr(module, "run_cmd_capture", lambda cmd, **_kwargs: (0.0, "ok"))
    monkeypatch.setattr(module, "default_fast_binary_path", lambda: tmp_path / "tg-search-fast.exe")
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_benchmarks.py", "--launcher-mode", "explicit_fast_binary"],
    )

    captured: dict[str, object] = {}
    monkeypatch.setattr("tensor_grep.perf_guard.ensure_artifacts_dir", lambda _root: tmp_path)
    monkeypatch.setattr(
        "tensor_grep.perf_guard.write_json",
        lambda path, payload: captured.update({"path": path, "payload": payload}),
    )

    module.main()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["environment"]["tg_launcher_mode"] == "explicit_fast_binary"
    assert payload["environment"]["tg_binary_source"] == "default_fast_binary_path"


def test_run_benchmarks_should_record_positional_launcher_mode_in_environment(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_benchmarks_script_positional_payload", "benchmarks/run_benchmarks.py"
    )
    monkeypatch.setattr(
        module,
        "SCENARIOS",
        [
            {
                "name": "1. Simple String Match",
                "rg_args": ["rg", "ERROR", "bench_data"],
                "tg_args": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        module, "collect_timing_samples", lambda *_args, **_kwargs: (0.1, [0.1, 0.1, 0.1])
    )
    monkeypatch.setattr(module, "run_cmd_capture", lambda cmd, **_kwargs: (0.0, "ok"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_benchmarks.py",
            "--launcher-mode",
            "explicit_binary_positional",
            "--allow-claim-unsafe-launcher",
        ],
    )

    captured: dict[str, object] = {}
    monkeypatch.setattr("tensor_grep.perf_guard.ensure_artifacts_dir", lambda _root: tmp_path)
    monkeypatch.setattr(
        "tensor_grep.perf_guard.write_json",
        lambda path, payload: captured.update({"path": path, "payload": payload}),
    )

    module.main()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["environment"]["tg_launcher_mode"] == "explicit_binary_positional"


def test_run_benchmarks_should_record_positional_early_rg_launcher_mode_in_environment(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_benchmarks_script_positional_early_rg_payload", "benchmarks/run_benchmarks.py"
    )
    monkeypatch.setattr(
        module,
        "SCENARIOS",
        [
            {
                "name": "1. Simple String Match",
                "rg_args": ["rg", "ERROR", "bench_data"],
                "tg_args": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        module, "collect_timing_samples", lambda *_args, **_kwargs: (0.1, [0.1, 0.1, 0.1])
    )
    monkeypatch.setattr(module, "run_cmd_capture", lambda cmd, **_kwargs: (0.0, "ok"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_benchmarks.py",
            "--launcher-mode",
            "explicit_binary_positional_early_rg",
            "--allow-claim-unsafe-launcher",
        ],
    )

    captured: dict[str, object] = {}
    monkeypatch.setattr("tensor_grep.perf_guard.ensure_artifacts_dir", lambda _root: tmp_path)
    monkeypatch.setattr(
        "tensor_grep.perf_guard.write_json",
        lambda path, payload: captured.update({"path": path, "payload": payload}),
    )

    module.main()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["environment"]["tg_launcher_mode"] == "explicit_binary_positional_early_rg"


def test_run_benchmarks_should_honor_output_and_milestone_args(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script_args", "benchmarks/run_benchmarks.py")
    monkeypatch.setattr(
        module,
        "SCENARIOS",
        [
            {
                "name": "1. Simple String Match",
                "rg_args": ["rg", "ERROR", "bench_data"],
                "tg_args": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "run_cmd_timing", lambda *_args, **_kwargs: 0.25)
    monkeypatch.setattr(module, "run_cmd_capture", lambda cmd: (0.0, "ok"))
    output_path = tmp_path / "bench_m2.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_benchmarks.py",
            "--output",
            str(output_path),
            "--milestone",
            "m2",
            "--allow-claim-unsafe-launcher",
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["milestone"] == "m2"


def test_run_benchmarks_progress_always_uses_stderr_without_changing_report(
    monkeypatch, tmp_path, capsys
):
    module = _load_script_module("run_benchmarks_progress_script", "benchmarks/run_benchmarks.py")
    tg_binary = tmp_path / "tg"
    tg_binary.write_text("fake tg", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "SCENARIOS",
        [
            {
                "name": "1. Simple String Match",
                "rg_args": ["rg", "ERROR", "bench_data"],
                "tg_args": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        module,
        "resolve_tg_binary_with_source",
        lambda *_args, **_kwargs: (tg_binary, "explicit_arg"),
    )
    monkeypatch.setattr(
        module,
        "inspect_native_tg_binary",
        lambda *_args, **_kwargs: {
            "kind": "release-native",
            "version": "9.9.9",
            "expected_version": "9.9.9",
            "version_status": "matches",
        },
    )
    monkeypatch.setattr(module, "benchmark_binary_warnings", lambda _binary: [])
    monkeypatch.setattr(module, "run_cmd_timing", lambda *_args, **_kwargs: 0.1)
    monkeypatch.setattr(
        module,
        "collect_timing_samples",
        lambda *_args, **_kwargs: (0.1, [0.1, 0.1, 0.1]),
    )
    monkeypatch.setattr(module, "run_cmd_capture", lambda *_args, **_kwargs: (0.0, "ok"))
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)
    output_path = tmp_path / "bench_run.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_benchmarks.py",
            "--output",
            str(output_path),
            "--progress",
            "always",
        ],
    )

    exit_code = module.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_run_benchmarks"
    assert payload["rows"] == [
        {
            "name": "1. Simple String Match",
            "rg_samples_s": [0.1, 0.1, 0.1],
            "rg_time_s": 0.1,
            "tg_samples_s": [0.1, 0.1, 0.1],
            "tg_time_s": 0.1,
            "parity": "PASS",
        }
    ]
    assert "progress" not in json.dumps(payload).lower()
    assert "[progress]" in captured.err
    assert "[progress]" not in captured.out


def test_run_hot_query_benchmarks_should_report_regression_status(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_hot_query_benchmarks_script_status", "benchmarks/run_hot_query_benchmarks.py"
    )
    monkeypatch.setattr(module, "resolve_hot_bench_data_dir", lambda: tmp_path / "hot")
    monkeypatch.setattr(module, "_prepare_corpus", lambda data_dir: data_dir / "hot_corpus.log")
    monkeypatch.setattr(module, "write_cpu_probe_script", lambda _path: None)
    monkeypatch.setattr(
        module,
        "_run_stringzilla_hot_query",
        lambda *_args, **_kwargs: {
            "name": "repeated_fixed_string",
            "first_s": 1.0,
            "second_s": 0.2,
            "first_reason": "index_build",
            "second_reason": "index_hit",
            "matches": 2000,
        },
    )
    monkeypatch.setattr(
        module,
        "_run_cpu_hot_query",
        lambda *_args, **_kwargs: {
            "name": "repeated_regex_prefilter",
            "first_s": 0.8,
            "second_s": 0.3,
            "first_reason": "regex_scan",
            "second_reason": "regex_prefilter_hit",
            "matches": 2000,
        },
    )
    output_path = tmp_path / "bench_hot.json"
    monkeypatch.setattr(
        "sys.argv",
        ["run_hot_query_benchmarks.py", "--output", str(output_path)],
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["suite"] == "run_hot_query_benchmarks"
    assert payload["generated_at_epoch_s"] > 0
    assert payload["no_regressions"] is True
    assert payload["rows"][0]["status"] == "PASS"
    assert payload["rows"][1]["status"] == "PASS"


def test_run_native_cpu_benchmarks_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module(
        "run_native_cpu_benchmarks_script", "benchmarks/run_native_cpu_benchmarks.py"
    )
    monkeypatch.delenv("TENSOR_GREP_NATIVE_CPU_BENCH_DATA_DIR", raising=False)

    path = module.resolve_native_cpu_bench_data_dir()

    assert path.parts[-2:] == ("artifacts", "native_cpu_bench_data")


def test_run_native_cpu_benchmarks_should_force_native_cpu_commands(tmp_path):
    module = _load_script_module(
        "run_native_cpu_benchmarks_script_cpu_commands",
        "benchmarks/run_native_cpu_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    target = tmp_path / "fixture.log"

    search_cmd = module.build_tg_cpu_search_command(tg_binary, "ERROR", target)
    count_cmd = module.build_tg_cpu_count_command(tg_binary, "ERROR", target)

    assert search_cmd[:4] == [str(tg_binary), "search", "--cpu", "--no-ignore"]
    assert count_cmd[:5] == [str(tg_binary), "search", "--cpu", "--no-ignore", "-c"]
