import importlib.util
import json
import sys
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


def test_run_gemini_patch_predictions_should_prepare_isolated_home_without_mcp(tmp_path):
    module = _load_script_module(
        "run_gemini_patch_predictions_isolated_home_script",
        "benchmarks/run_gemini_patch_predictions.py",
    )
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    (source_home / "settings.json").write_text(
        json.dumps(
            {
                "mcpServers": {"Exa": {"command": "exa-mcp"}},
                "security": {"auth": {"selectedType": "oauth-personal"}},
                "general": {"preferredEditor": "vscode"},
            }
        ),
        encoding="utf-8",
    )
    (source_home / "oauth_creds.json").write_text("{}", encoding="utf-8")
    (source_home / "google_accounts.json").write_text("[]", encoding="utf-8")
    (source_home / "GEMINI.md").write_text("persona", encoding="utf-8")

    isolated_root = module._prepare_isolated_gemini_home(tmp_path / "run-root", source_home)
    isolated_settings = json.loads(
        (isolated_root / ".gemini" / "settings.json").read_text(encoding="utf-8")
    )

    assert "mcpServers" not in isolated_settings
    assert isolated_settings["security"]["auth"]["selectedType"] == "oauth-personal"
    assert (isolated_root / ".gemini" / "oauth_creds.json").exists()
    assert not (isolated_root / ".gemini" / "GEMINI.md").exists()


def test_run_gemini_patch_predictions_should_run_with_isolated_home_env(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gemini_patch_predictions_env_script", "benchmarks/run_gemini_patch_predictions.py"
    )
    seen_env: dict[str, str] = {}

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return "{}", ""

    def _fake_popen(*args, **kwargs):
        env = kwargs["env"]
        seen_env["HOME"] = env["HOME"]
        seen_env["USERPROFILE"] = env["USERPROFILE"]
        seen_env["APPDATA"] = env["APPDATA"]
        seen_env["LOCALAPPDATA"] = env["LOCALAPPDATA"]
        return FakeProc()

    monkeypatch.setattr(module, "resolve_gemini_binary", lambda: "gemini")
    monkeypatch.setattr(
        module,
        "_prepare_isolated_gemini_home",
        lambda repo_root, source_home=None: repo_root / ".gemini-home",
    )
    monkeypatch.setattr(module.subprocess, "Popen", _fake_popen)

    module._run_gemini_command(
        tmp_path, "prompt", model="gemini-3-flash-preview", timeout_seconds=5
    )

    assert seen_env["HOME"].endswith(".gemini-home")
    assert seen_env["USERPROFILE"] == seen_env["HOME"]
    assert seen_env["APPDATA"] == seen_env["HOME"]
    assert seen_env["LOCALAPPDATA"] == seen_env["HOME"]


def test_gemini_project_context_and_skill_should_exist():
    repo_root = Path(__file__).resolve().parents[2]
    project_context = repo_root / "GEMINI.md"
    skill_dir = repo_root / ".gemini" / "skills" / "tensor-grep"

    assert project_context.exists()
    assert skill_dir.joinpath("SKILL.md").exists()
    assert skill_dir.joinpath("REFERENCE.md").exists()

    context_text = project_context.read_text(encoding="utf-8")
    skill_text = skill_dir.joinpath("SKILL.md").read_text(encoding="utf-8")
    reference_text = skill_dir.joinpath("REFERENCE.md").read_text(encoding="utf-8")

    assert "Use the `tensor-grep` skill" in context_text
    assert "Do not ask what task to perform" in context_text
    assert "tg source REPO_PATH SYMBOL" in skill_text
    assert "tg source REPO_PATH SYMBOL" in reference_text
    assert "tg source --symbol SYMBOL REPO_PATH" not in skill_text
    assert "tg source --symbol SYMBOL REPO_PATH" not in reference_text
    assert "tg source SYMBOL REPO_PATH" not in skill_text
    assert "tg source SYMBOL REPO_PATH" not in reference_text


def test_run_gemini_skill_ab_should_install_project_skill(tmp_path):
    module = _load_script_module(
        "run_gemini_skill_ab_skill_script", "benchmarks/run_gemini_skill_ab.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    context_path = tmp_path / "GEMINI.md"
    context_path.write_text("# context\n", encoding="utf-8")

    module.install_skill_package(repo_root, skill_dir, context_path)

    assert (repo_root / "GEMINI.md").read_text(encoding="utf-8") == "# context\n"
    assert (repo_root / ".gemini" / "skills" / "tensor-grep" / "SKILL.md").exists()
    assert (repo_root / ".gemini" / "skills" / "tensor-grep" / "REFERENCE.md").exists()


def test_run_gemini_skill_ab_should_build_baseline_and_enhanced_records(monkeypatch, tmp_path):
    module = _load_script_module("run_gemini_skill_ab_script", "benchmarks/run_gemini_skill_ab.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "demo.py").write_text("old\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    context_path = tmp_path / "GEMINI.md"
    context_path.write_text("# context\n", encoding="utf-8")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-1",
                "repo_fixture": str(repo_root),
                "prompt": f"Fix {repo_root}",
                "actual_test_files": [],
                "actual_validation_commands": ["pytest -q"],
            }
        ]
    }

    def _fake_run(repo_root, prompt, **kwargs):
        del kwargs
        target = repo_root / "demo.py"
        if (repo_root / "GEMINI.md").exists():
            target.write_text("enhanced\n", encoding="utf-8")
        else:
            target.write_text("baseline\n", encoding="utf-8")
        return json.dumps({"response": "no diff emitted"})

    monkeypatch.setattr(module.gemini_runner, "_run_gemini_command", _fake_run)

    payload = module.build_payload(
        driver_payload,
        model="gemini-3-flash-preview",
        timeout_seconds=5,
        skill_dir=skill_dir,
        context_path=context_path,
        work_root=tmp_path / "work",
    )

    assert payload["artifact"] == "gemini_skill_ab"
    assert [record["system"] for record in payload["records"]] == [
        "gemini-baseline",
        "gemini-enhanced",
    ]
    assert all(
        "diff --git a/demo.py b/demo.py" in record["model_patch"] for record in payload["records"]
    )


def test_run_gemini_skill_ab_should_score_records_when_scenarios_are_provided(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gemini_skill_ab_scored_script", "benchmarks/run_gemini_skill_ab.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "demo.py").write_text("old\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    context_path = tmp_path / "GEMINI.md"
    context_path.write_text("# context\n", encoding="utf-8")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-1",
                "repo_fixture": str(repo_root),
                "prompt": f"Fix {repo_root}",
                "actual_test_files": [],
                "actual_validation_commands": ["pytest -q"],
            }
        ]
    }
    scenarios_path = tmp_path / "scenarios.json"
    scenarios_path.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "instance_id": "demo-1",
                        "repo_fixture": str(repo_root),
                        "expected_primary_file": "demo.py",
                        "expected_primary_span": {"start_line": 1, "end_line": 1},
                        "expected_changed_files": ["demo.py"],
                        "expected_test_files": [],
                        "validation_commands": [],
                        "expected_validation_commands_contain": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def _fake_run(repo_root, prompt, **kwargs):
        del kwargs, prompt
        target = repo_root / "demo.py"
        if (repo_root / "GEMINI.md").exists():
            target.write_text("enhanced\n", encoding="utf-8")
        else:
            target.write_text("baseline\n", encoding="utf-8")
        return json.dumps({"response": "no diff emitted"})

    monkeypatch.setattr(module.gemini_runner, "_run_gemini_command", _fake_run)

    payload = module.build_payload(
        driver_payload,
        model="gemini-3-flash-preview",
        timeout_seconds=5,
        skill_dir=skill_dir,
        context_path=context_path,
        work_root=tmp_path / "work",
        scenarios_path=scenarios_path,
    )

    assert payload["artifact"] == "gemini_skill_ab"
    assert payload["summary"]["scenario_count"] == 2
    assert len(payload["rows"]) == 2
    assert payload["system_score_summary"]["gemini-baseline"]["mean_patch_applied_rate"] == 1.0
    assert payload["system_score_summary"]["gemini-enhanced"]["mean_validation_pass_rate"] == 1.0


def test_run_gemini_skill_ab_should_support_partial_resume(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gemini_skill_ab_resume_script", "benchmarks/run_gemini_skill_ab.py"
    )
    output_path = tmp_path / "gemini_ab.json"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    context_path = tmp_path / "GEMINI.md"
    context_path.write_text("# context\n", encoding="utf-8")
    driver_payload = {
        "records": [
            {"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "one"},
            {"instance_id": "demo-2", "repo_fixture": str(repo_root), "prompt": "two"},
        ]
    }
    seen: list[str] = []

    def _fake_run(record, **kwargs):
        del kwargs
        seen.append(str(record["instance_id"]))
        return [
            {
                "instance_id": str(record["instance_id"]),
                "system": "gemini-baseline",
                "model_patch": "",
                "wall_clock_seconds": 1.0,
                "notes": "",
                "use_skill": False,
            },
            {
                "instance_id": str(record["instance_id"]),
                "system": "gemini-enhanced",
                "model_patch": "",
                "wall_clock_seconds": 2.0,
                "notes": "",
                "use_skill": True,
            },
        ]

    monkeypatch.setattr(module, "run_ab_record", _fake_run)
    partial = module.build_partial_payload(
        [
            {
                "instance_id": "demo-1",
                "system": "gemini-baseline",
                "model_patch": "",
                "wall_clock_seconds": 1.0,
                "notes": "",
                "use_skill": False,
            },
            {
                "instance_id": "demo-1",
                "system": "gemini-enhanced",
                "model_patch": "",
                "wall_clock_seconds": 2.0,
                "notes": "",
                "use_skill": True,
            },
        ]
    )
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_payload(
        driver_payload,
        model="gemini-3-flash-preview",
        timeout_seconds=5,
        skill_dir=skill_dir,
        context_path=context_path,
        work_root=tmp_path / "work",
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-2"]
    assert len(payload["records"]) == 4


def test_run_gemini_skill_ab_should_resume_incomplete_instance_ids(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gemini_skill_ab_incomplete_resume_script", "benchmarks/run_gemini_skill_ab.py"
    )
    output_path = tmp_path / "gemini_ab.json"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    context_path = tmp_path / "GEMINI.md"
    context_path.write_text("# context\n", encoding="utf-8")
    driver_payload = {
        "records": [
            {"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "one"},
            {"instance_id": "demo-2", "repo_fixture": str(repo_root), "prompt": "two"},
        ]
    }
    seen: list[str] = []

    def _fake_run(record, **kwargs):
        del kwargs
        seen.append(str(record["instance_id"]))
        return [
            {
                "instance_id": str(record["instance_id"]),
                "system": "gemini-baseline",
                "model_patch": "",
                "wall_clock_seconds": 1.0,
                "notes": "",
                "use_skill": False,
            },
            {
                "instance_id": str(record["instance_id"]),
                "system": "gemini-enhanced",
                "model_patch": "",
                "wall_clock_seconds": 2.0,
                "notes": "",
                "use_skill": True,
            },
        ]

    monkeypatch.setattr(module, "run_ab_record", _fake_run)
    partial = module.build_partial_payload(
        [
            {
                "instance_id": "demo-1",
                "system": "gemini-enhanced",
                "model_patch": "",
                "wall_clock_seconds": 2.0,
                "notes": "",
                "use_skill": True,
            },
        ]
    )
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_payload(
        driver_payload,
        model="gemini-3-flash-preview",
        timeout_seconds=5,
        skill_dir=skill_dir,
        context_path=context_path,
        work_root=tmp_path / "work",
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-1", "demo-2"]
    assert len(payload["records"]) == 4


def test_run_gemini_skill_ab_should_build_attempt_ledger_payloads_by_instance(tmp_path):
    module = _load_script_module(
        "run_gemini_skill_ab_ledger_script", "benchmarks/run_gemini_skill_ab.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    driver_payload = {
        "records": [
            {"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "Fix one."},
            {"instance_id": "demo-2", "repo_fixture": str(repo_root), "prompt": "Fix two."},
        ]
    }
    prediction_records = [
        {
            "instance_id": "demo-1",
            "system": "gemini-baseline",
            "model_patch": "",
            "notes": "timeout after 60s",
        },
        {
            "instance_id": "demo-1",
            "system": "gemini-enhanced",
            "model_patch": "diff --git a/x b/x",
            "notes": "",
        },
        {"instance_id": "demo-2", "system": "gemini-baseline", "model_patch": "", "notes": ""},
        {"instance_id": "demo-2", "system": "gemini-enhanced", "model_patch": "", "notes": ""},
    ]

    ledgers = module.build_attempt_ledger_payloads(driver_payload, prediction_records)

    assert set(ledgers) == {"demo-1", "demo-2"}
    accepted = ledgers["demo-1"]
    assert accepted["artifact"] == "agent_attempt_ledger"
    assert accepted["task_id"] == "demo-1"
    assert accepted["root"] == str(repo_root)
    assert accepted["final_outcome"]["status"] == "completed"
    assert accepted["replay"]["next_action"] == "score patch bakeoff"
    assert accepted["attempts"][0]["status"] == "needs_retry"
    assert accepted["attempts"][0]["retry_reason"] == "timeout after 60s"
    assert accepted["attempts"][1]["status"] == "completed"
    retry = ledgers["demo-2"]
    assert retry["final_outcome"]["status"] == "needs_retry"
    assert retry["attempts"][0]["status"] == "needs_retry"
    assert retry["attempts"][1]["status"] == "needs_retry"


def test_run_gemini_skill_ab_should_write_attempt_ledgers_when_requested(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gemini_skill_ab_ledger_cli_script", "benchmarks/run_gemini_skill_ab.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    context_path = tmp_path / "GEMINI.md"
    context_path.write_text("# context\n", encoding="utf-8")
    output_path = tmp_path / "gemini_ab.json"
    ledger_dir = tmp_path / "attempt_ledgers"
    captured: list[tuple[Path, dict[str, object]]] = []

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_gemini_skill_ab.py",
            "--input",
            str(tmp_path / "driver.json"),
            "--output",
            str(output_path),
            "--attempt-ledger-dir",
            str(ledger_dir),
            "--skill-dir",
            str(skill_dir),
            "--context-path",
            str(context_path),
            "--work-root",
            str(tmp_path / "work"),
        ],
    )
    monkeypatch.setattr(
        module.gemini_runner,
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
            "artifact": "gemini_skill_ab",
            "suite": "run_gemini_skill_ab",
            "generated_at_epoch_s": 1.0,
            "environment": {"platform": "windows"},
            "records": [
                {
                    "instance_id": "demo-1",
                    "system": "gemini-baseline",
                    "model_patch": "",
                    "notes": "timeout after 60s",
                },
                {
                    "instance_id": "demo-1",
                    "system": "gemini-enhanced",
                    "model_patch": "diff --git a/x b/x",
                    "notes": "",
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
    assert captured[0][1]["artifact"] == "gemini_skill_ab"
    assert captured[1][0] == (ledger_dir / "demo-1.json").resolve()
    assert captured[1][1]["artifact"] == "agent_attempt_ledger"


def test_run_gemini_patch_predictions_should_support_partial_resume(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gemini_patch_predictions_resume_script", "benchmarks/run_gemini_patch_predictions.py"
    )
    output_path = tmp_path / "gemini_predictions.json"
    driver_payload = {
        "records": [
            {"instance_id": "demo-1", "repo_fixture": str(tmp_path), "prompt": "one"},
            {"instance_id": "demo-2", "repo_fixture": str(tmp_path), "prompt": "two"},
        ]
    }
    seen: list[str] = []

    def _fake_run(record, **kwargs):
        del kwargs
        seen.append(str(record["instance_id"]))
        return {
            "instance_id": str(record["instance_id"]),
            "system": "gemini-cli",
            "model_patch": f"diff --git a/{record['instance_id']} b/{record['instance_id']}",
            "actual_test_files": [],
            "actual_validation_commands": [],
            "wall_clock_seconds": 1.0,
            "notes": "",
        }

    monkeypatch.setattr(module, "run_gemini_patch_record", _fake_run)
    partial = module.build_partial_payload(
        [
            {
                "instance_id": "demo-1",
                "system": "gemini-cli",
                "model_patch": "diff --git a/demo-1 b/demo-1",
                "actual_test_files": [],
                "actual_validation_commands": [],
                "wall_clock_seconds": 1.0,
                "notes": "",
            }
        ]
    )
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_payload(
        driver_payload,
        model="gemini-2.5-flash",
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-2"]
    assert [record["instance_id"] for record in payload["records"]] == ["demo-1", "demo-2"]


def test_run_gemini_patch_predictions_should_checkpoint_per_record(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gemini_patch_predictions_checkpoint_script",
        "benchmarks/run_gemini_patch_predictions.py",
    )
    output_path = tmp_path / "gemini_predictions.json"
    driver_payload = {
        "records": [
            {"instance_id": "demo-1", "repo_fixture": str(tmp_path), "prompt": "one"},
            {"instance_id": "demo-2", "repo_fixture": str(tmp_path), "prompt": "two"},
        ]
    }
    writes: list[int] = []

    monkeypatch.setattr(
        module,
        "run_gemini_patch_record",
        lambda record, **kwargs: {
            "instance_id": str(record["instance_id"]),
            "system": "gemini-cli",
            "model_patch": f"diff --git a/{record['instance_id']} b/{record['instance_id']}",
            "actual_test_files": [],
            "actual_validation_commands": [],
            "wall_clock_seconds": 1.0,
            "notes": "",
        },
    )
    monkeypatch.setattr(
        module, "write_checkpoint", lambda path, records: writes.append(len(records))
    )

    payload = module.build_payload(
        driver_payload,
        model="gemini-2.5-flash",
        output_path=output_path,
        resume=False,
    )

    assert len(payload["records"]) == 2
    assert writes == [1, 2]


def test_run_copilot_patch_predictions_should_build_patch_records(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_copilot_patch_predictions_script", "benchmarks/run_copilot_patch_predictions.py"
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
        "_run_copilot_command",
        lambda *args, **kwargs: (
            "```diff\n"
            "diff --git a/demo.py b/demo.py\n"
            "--- a/demo.py\n"
            "+++ b/demo.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "```"
        ),
    )

    payload = module.build_payload(driver_payload, model="gpt-5.2")

    assert payload["suite"] == "run_copilot_patch_predictions"
    assert payload["records"][0]["system"] == "copilot"
    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert payload["records"][0]["actual_validation_commands"] == ["pytest -q"]


def test_run_copilot_patch_predictions_should_strip_invalid_index_lines(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_copilot_patch_predictions_normalize_script",
        "benchmarks/run_copilot_patch_predictions.py",
    )
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-1",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": [],
                "actual_validation_commands": [],
            }
        ]
    }
    monkeypatch.setattr(
        module,
        "_run_copilot_command",
        lambda *args, **kwargs: (
            "diff --git a/demo.py b/demo.py\n"
            "index XXXXXXX..XXXXXXX 100644\n"
            "--- a/demo.py\n"
            "+++ b/demo.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
    )

    payload = module.build_payload(driver_payload, model="gpt-5.2")

    assert "index XXXXXXX..XXXXXXX 100644" not in payload["records"][0]["model_patch"]
    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]


def test_run_copilot_patch_predictions_should_capture_timeout_as_empty_patch(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_copilot_patch_predictions_timeout_script",
        "benchmarks/run_copilot_patch_predictions.py",
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
        raise module.subprocess.TimeoutExpired(cmd="copilot", timeout=5)

    monkeypatch.setattr(module, "_run_copilot_command", _raise_timeout)

    payload = module.build_payload(driver_payload, model="gpt-5.2", timeout_seconds=5)

    assert payload["records"][0]["model_patch"] == ""
    assert payload["records"][0]["notes"] == "timeout after 5s"


def test_run_copilot_patch_predictions_should_fallback_to_repo_diff(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_copilot_patch_predictions_diff_script", "benchmarks/run_copilot_patch_predictions.py"
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
        return "no diff emitted"

    monkeypatch.setattr(module, "_run_copilot_command", _edit_repo)

    payload = module.build_payload(driver_payload, model="gpt-5.2")

    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "old\n"


def test_run_copilot_patch_predictions_should_build_attempt_ledger_payloads_by_instance(tmp_path):
    module = _load_script_module(
        "run_copilot_patch_predictions_ledger_script",
        "benchmarks/run_copilot_patch_predictions.py",
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
                "system": "copilot",
                "model_patch": "",
                "notes": "timeout after 60s",
            }
        ],
    )

    ledger = ledgers["demo-1"]
    assert ledger["artifact"] == "agent_attempt_ledger"
    assert ledger["task_id"] == "demo-1"
    assert ledger["final_outcome"]["status"] == "needs_retry"
    assert ledger["attempts"][0]["retry_reason"] == "timeout after 60s"


def test_run_copilot_patch_predictions_should_write_attempt_ledgers_when_requested(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_copilot_patch_predictions_ledger_cli_script",
        "benchmarks/run_copilot_patch_predictions.py",
    )
    output_path = tmp_path / "copilot_predictions.json"
    ledger_dir = tmp_path / "attempt_ledgers"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    captured: list[tuple[Path, dict[str, object]]] = []

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_copilot_patch_predictions.py",
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
            "artifact": "copilot_patch_predictions",
            "suite": "run_copilot_patch_predictions",
            "generated_at_epoch_s": 1.0,
            "environment": {"platform": "windows"},
            "records": [
                {
                    "instance_id": "demo-1",
                    "system": "copilot",
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
    assert captured[0][1]["artifact"] == "copilot_patch_predictions"
    assert captured[1][0] == (ledger_dir / "demo-1.json").resolve()
    assert captured[1][1]["artifact"] == "agent_attempt_ledger"


def test_run_copilot_patch_predictions_should_support_partial_resume(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_copilot_patch_predictions_resume_script", "benchmarks/run_copilot_patch_predictions.py"
    )
    output_path = tmp_path / "copilot_predictions.json"
    driver_payload = {
        "records": [
            {"instance_id": "demo-1", "repo_fixture": str(tmp_path), "prompt": "one"},
            {"instance_id": "demo-2", "repo_fixture": str(tmp_path), "prompt": "two"},
        ]
    }
    seen: list[str] = []

    def _fake_run(record, **kwargs):
        del kwargs
        seen.append(str(record["instance_id"]))
        return {
            "instance_id": str(record["instance_id"]),
            "system": "copilot",
            "model_patch": f"diff --git a/{record['instance_id']} b/{record['instance_id']}",
            "actual_test_files": [],
            "actual_validation_commands": [],
            "wall_clock_seconds": 1.0,
            "notes": "",
        }

    monkeypatch.setattr(module, "run_copilot_patch_record", _fake_run)
    partial = module.build_partial_payload(
        [
            {
                "instance_id": "demo-1",
                "system": "copilot",
                "model_patch": "diff --git a/demo-1 b/demo-1",
                "actual_test_files": [],
                "actual_validation_commands": [],
                "wall_clock_seconds": 1.0,
                "notes": "",
            }
        ]
    )
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_payload(
        driver_payload,
        model="gpt-5.2",
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-2"]
    assert [record["instance_id"] for record in payload["records"]] == ["demo-1", "demo-2"]


def test_run_copilot_patch_predictions_should_checkpoint_per_record(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_copilot_patch_predictions_checkpoint_script",
        "benchmarks/run_copilot_patch_predictions.py",
    )
    output_path = tmp_path / "copilot_predictions.json"
    driver_payload = {
        "records": [
            {"instance_id": "demo-1", "repo_fixture": str(tmp_path), "prompt": "one"},
            {"instance_id": "demo-2", "repo_fixture": str(tmp_path), "prompt": "two"},
        ]
    }
    writes: list[int] = []

    monkeypatch.setattr(
        module,
        "run_copilot_patch_record",
        lambda record, **kwargs: {
            "instance_id": str(record["instance_id"]),
            "system": "copilot",
            "model_patch": f"diff --git a/{record['instance_id']} b/{record['instance_id']}",
            "actual_test_files": [],
            "actual_validation_commands": [],
            "wall_clock_seconds": 1.0,
            "notes": "",
        },
    )
    monkeypatch.setattr(
        module, "write_checkpoint", lambda path, records: writes.append(len(records))
    )

    payload = module.build_payload(
        driver_payload,
        model="gpt-5.2",
        output_path=output_path,
        resume=False,
    )

    assert len(payload["records"]) == 2
    assert writes == [1, 2]


def test_run_claude_patch_predictions_should_build_patch_records(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_patch_predictions_script", "benchmarks/run_claude_patch_predictions.py"
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
        "_run_claude_command",
        lambda *args, **kwargs: (
            "```diff\n"
            "diff --git a/demo.py b/demo.py\n"
            "--- a/demo.py\n"
            "+++ b/demo.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "```"
        ),
    )

    payload = module.build_payload(
        driver_payload, model="sonnet", permission_mode="bypassPermissions"
    )

    assert payload["suite"] == "run_claude_patch_predictions"
    assert payload["records"][0]["system"] == "claude-code"
    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert payload["records"][0]["actual_validation_commands"] == ["pytest -q"]


def test_run_claude_patch_predictions_should_prefix_direct_edit_instruction():
    module = _load_script_module(
        "run_claude_patch_predictions_prompt_script",
        "benchmarks/run_claude_patch_predictions.py",
    )

    prompt = module._build_claude_prompt("Return only a diff patch.")

    assert "edit the repository files directly" in prompt
    assert "do not print a summary" in prompt
    assert prompt.endswith("Return only a diff patch.")


def test_run_claude_patch_predictions_should_capture_timeout_as_empty_patch(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_patch_predictions_timeout_script", "benchmarks/run_claude_patch_predictions.py"
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
        raise module.subprocess.TimeoutExpired(cmd="claude", timeout=5)

    monkeypatch.setattr(module, "_run_claude_command", _raise_timeout)

    payload = module.build_payload(
        driver_payload,
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
    )

    assert payload["records"][0]["model_patch"] == ""
    assert payload["records"][0]["notes"] == "timeout after 5s"


def test_run_claude_patch_predictions_should_fallback_to_repo_diff(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_patch_predictions_diff_script", "benchmarks/run_claude_patch_predictions.py"
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
        return "no diff emitted"

    monkeypatch.setattr(module, "_run_claude_command", _edit_repo)

    payload = module.build_payload(
        driver_payload, model="sonnet", permission_mode="bypassPermissions"
    )

    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "old\n"


def test_run_claude_patch_predictions_should_build_attempt_ledger_payloads_by_instance(tmp_path):
    module = _load_script_module(
        "run_claude_patch_predictions_ledger_script",
        "benchmarks/run_claude_patch_predictions.py",
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
                "system": "claude-code",
                "model_patch": "",
                "notes": "timeout after 60s",
            }
        ],
    )

    ledger = ledgers["demo-1"]
    assert ledger["artifact"] == "agent_attempt_ledger"
    assert ledger["task_id"] == "demo-1"
    assert ledger["final_outcome"]["status"] == "needs_retry"
    assert ledger["attempts"][0]["retry_reason"] == "timeout after 60s"


def test_run_claude_patch_predictions_should_write_attempt_ledgers_when_requested(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_claude_patch_predictions_ledger_cli_script",
        "benchmarks/run_claude_patch_predictions.py",
    )
    output_path = tmp_path / "claude_predictions.json"
    ledger_dir = tmp_path / "attempt_ledgers"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    captured: list[tuple[Path, dict[str, object]]] = []

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_claude_patch_predictions.py",
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
            "artifact": "claude_patch_predictions",
            "suite": "run_claude_patch_predictions",
            "generated_at_epoch_s": 1.0,
            "environment": {"platform": "windows"},
            "records": [
                {
                    "instance_id": "demo-1",
                    "system": "claude-code",
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
    assert captured[0][1]["artifact"] == "claude_patch_predictions"
    assert captured[1][0] == (ledger_dir / "demo-1.json").resolve()
    assert captured[1][1]["artifact"] == "agent_attempt_ledger"


def test_run_claude_patch_predictions_should_separate_prompt_from_add_dir(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_patch_predictions_command_script",
        "benchmarks/run_claude_patch_predictions.py",
    )
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return ("diff --git a/demo.py b/demo.py\n", "")

    monkeypatch.setattr(module, "resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append(list(command)) or FakeProc(),
    )

    output = module._run_claude_command(
        tmp_path,
        "Return only a diff patch.",
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
    )

    assert output.startswith("diff --git")
    assert "--" in calls[0]
    assert calls[0][-2:] == ["--", "Return only a diff patch."]


def test_run_claude_skill_ab_should_install_project_skill(tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_skill_script", "benchmarks/run_claude_skill_ab.py"
    )
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8"
    )
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    installed = module.install_skill_package(repo_root, skill_dir)

    assert installed == repo_root / ".claude" / "skills" / "tensor-grep"
    assert (repo_root / ".claude" / "skills" / "tensor-grep" / "SKILL.md").exists()
    assert (repo_root / ".claude" / "skills" / "tensor-grep" / "REFERENCE.md").exists()


def test_run_claude_skill_ab_should_prepare_unique_repo_copy_when_stale_run_root_exists(tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_repo_copy_script", "benchmarks/run_claude_skill_ab.py"
    )
    source_repo = tmp_path / "source"
    source_repo.mkdir()
    (source_repo / "demo.py").write_text("print('ok')\n", encoding="utf-8")

    stale_run_root = tmp_path / "work" / "demo-1" / "claude-enhanced"
    (stale_run_root / "b").mkdir(parents=True)
    (stale_run_root / "b" / "stale.txt").write_text("stale\n", encoding="utf-8")

    before_root, repo_root = module.prepare_persistent_repo_copy(
        source_repo,
        tmp_path / "work",
        "demo-1",
        "claude-enhanced",
    )

    assert before_root.exists()
    assert repo_root.exists()
    assert before_root.parent != stale_run_root
    assert repo_root.parent != stale_run_root
    assert (repo_root / "demo.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_run_claude_skill_ab_should_write_claude_md(tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_claude_md_script", "benchmarks/run_claude_skill_ab.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    guidance_path = module.write_claude_md(repo_root)

    assert guidance_path == repo_root / "CLAUDE.md"
    text = guidance_path.read_text(encoding="utf-8")
    assert "Use the tensor-grep project skill" in text
    assert "Do not ask what task to perform" in text
    assert "make the change directly" in text


def test_run_claude_skill_ab_should_install_tg_trace_wrapper(tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_tg_wrapper_script", "benchmarks/run_claude_skill_ab.py"
    )
    run_root = tmp_path / "run"
    run_root.mkdir()

    wrapper_dir, log_path = module.install_tg_trace_wrapper(run_root)

    assert wrapper_dir == run_root / ".claude-bin"
    assert log_path == run_root / "tg_trace.jsonl"
    assert (wrapper_dir / "tg.cmd").exists()
    assert (wrapper_dir / "tg.ps1").exists()
    assert "TENSOR_GREP_TRACE_LOG" in (wrapper_dir / "tg.ps1").read_text(encoding="utf-8")


def test_run_claude_skill_ab_should_classify_response_shape():
    module = _load_script_module(
        "run_claude_skill_ab_response_shape_script", "benchmarks/run_claude_skill_ab.py"
    )

    assert module.classify_response_shape("What would you like me to do?", "") == "meta_question"
    assert (
        module.classify_response_shape("What would you like me to help you with?", "")
        == "meta_question"
    )
    assert (
        module.classify_response_shape("What task would you like me to work on?", "")
        == "meta_question"
    )
    assert module.classify_response_shape("", "diff --git a/x b/x") == "direct_patch"
    assert (
        module.classify_response_shape("Fixed the bug.", "diff --git a/x b/x")
        == "analysis_then_patch"
    )
    assert module.classify_response_shape("I inspected the repo.", "") == "analysis_only"
    assert module.classify_response_shape("", "") == "empty"


def test_run_claude_skill_ab_should_compute_first_tg_seconds():
    module = _load_script_module(
        "run_claude_skill_ab_first_tg_script", "benchmarks/run_claude_skill_ab.py"
    )

    assert module.first_tg_seconds(100.0, []) is None
    assert module.first_tg_seconds(100.0, [{"timestamp_epoch_s": 100.75}]) == 0.75


def test_run_claude_skill_ab_should_compute_post_edit_deliberation_seconds():
    module = _load_script_module(
        "run_claude_skill_ab_post_edit_script", "benchmarks/run_claude_skill_ab.py"
    )

    assert module.post_edit_deliberation_seconds(None, 10.0) is None
    assert module.post_edit_deliberation_seconds(0.5, None) is None
    assert module.post_edit_deliberation_seconds(0.5, 10.0) == 9.5


def test_run_claude_skill_ab_should_clear_transient_file_change_when_no_final_diff(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_claude_skill_ab_transient_change_script", "benchmarks/run_claude_skill_ab.py"
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

    def _fake_run(repo_dir, prompt, **kwargs):
        path = Path(repo_dir) / "demo.py"
        path.write_text("temp\n", encoding="utf-8")
        path.write_text("old\n", encoding="utf-8")
        return "What would you like me to do?"

    monkeypatch.setattr(module, "_run_claude_command", _fake_run)

    payload = module.build_payload(
        {
            "records": [
                {
                    "instance_id": "demo-1",
                    "repo_fixture": str(repo_root),
                    "prompt": "Fix the bug.",
                    "actual_validation_commands": ["pytest -q"],
                }
            ]
        },
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        skill_dir=skill_dir,
        work_root=tmp_path / "work",
    )

    baseline_trace = payload["trace_records"][0]
    assert baseline_trace["changed_file_count"] == 0
    assert baseline_trace["first_file_change_seconds"] is None


def test_run_claude_skill_ab_prompt_should_require_non_interactive_action(tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_prompt_script", "benchmarks/run_claude_skill_ab.py"
    )

    prompt = module._build_claude_prompt("Fix the bug.")
    terse_prompt = module._build_claude_prompt("Fix the bug.", terse_output=True)
    done_prompt = module._build_claude_prompt("Fix the bug.", done_output=True)

    assert "edit the repository files directly" in prompt
    assert "do not print a summary" in prompt
    assert prompt.endswith("Fix the bug.")
    assert "stop immediately" in terse_prompt
    assert "Do not print any explanation" in terse_prompt
    assert "respond with exactly DONE" in done_prompt


def test_run_claude_skill_ab_should_prepend_explicit_skill_instruction():
    module = _load_script_module(
        "run_claude_skill_ab_enhanced_prompt_script", "benchmarks/run_claude_skill_ab.py"
    )

    prompt = module.build_system_prompt("Fix the bug.", use_skill=True)
    terse_prompt = module.build_system_prompt(
        "Fix the bug.", use_skill=True, enhanced_output_contract="terse"
    )
    done_prompt = module.build_system_prompt(
        "Fix the bug.", use_skill=True, enhanced_output_contract="done"
    )
    engage_prompt = module.build_system_prompt(
        "Fix the bug.", use_skill=True, enhanced_task_contract="engage"
    )
    act_prompt = module.build_system_prompt(
        "Fix the bug.", use_skill=True, enhanced_task_contract="act"
    )

    assert "Use the tensor-grep project skill" in prompt
    assert prompt.endswith("Fix the bug.")
    assert "stop immediately" in terse_prompt
    assert "respond with exactly DONE" in done_prompt
    assert "Start working on it immediately" in engage_prompt
    assert "<system>" in act_prompt
    assert "<task>" in act_prompt
    assert "Do not ask clarifying questions" in act_prompt
    assert act_prompt.endswith("</task>")


def test_run_claude_skill_ab_should_resolve_contract_profiles(monkeypatch):
    module = _load_script_module(
        "run_claude_skill_ab_contract_profile_script", "benchmarks/run_claude_skill_ab.py"
    )

    assert module.resolve_contract_profile(
        "current",
        enhanced_output_contract="done",
        enhanced_task_contract="engage",
        enhanced_effort="low",
    ) == ("done", "engage", "low")
    assert module.resolve_contract_profile(
        "probe-standard-engage",
        enhanced_output_contract="done",
        enhanced_task_contract="standard",
        enhanced_effort="low",
    ) == ("standard", "engage", "")
    assert module.resolve_contract_profile(
        "probe-standard-act",
        enhanced_output_contract="terse",
        enhanced_task_contract="standard",
        enhanced_effort="low",
    ) == ("standard", "act", "")
    assert module.resolve_contract_profile(
        "probe-standard-act-low",
        enhanced_output_contract="terse",
        enhanced_task_contract="standard",
        enhanced_effort="",
    ) == ("standard", "act", "low")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_claude_skill_ab.py",
            "--input",
            "driver.json",
            "--enhanced-output-contract",
            "done",
            "--enhanced-task-contract",
            "standard",
            "--enhanced-contract-profile",
            "probe-standard-act-low",
        ],
    )

    args = module.parse_args()

    assert args.enhanced_output_contract == "standard"
    assert args.enhanced_task_contract == "act"
    assert args.enhanced_effort == "low"


def test_run_claude_skill_ab_should_rewrite_prompt_repo_paths(tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_rewrite_script", "benchmarks/run_claude_skill_ab.py"
    )
    source_repo = tmp_path / "source"
    copied_repo = tmp_path / "copy"
    source_repo.mkdir()
    copied_repo.mkdir()
    original = (
        f"File: {source_repo}\\src\\demo.py\nContext path: {source_repo / 'tests' / 'test_demo.py'}"
    )

    rewritten = module.rewrite_prompt_repo_paths(original, source_repo, copied_repo)

    assert str(source_repo) not in rewritten
    assert str(copied_repo) in rewritten


def test_run_claude_skill_ab_default_work_root_should_live_outside_repo():
    module = _load_script_module(
        "run_claude_skill_ab_work_root_script", "benchmarks/run_claude_skill_ab.py"
    )

    assert Path(module.DEFAULT_WORK_ROOT) != Path(module.ROOT_DIR)
    assert Path(module.DEFAULT_WORK_ROOT).is_absolute()
    assert module.ROOT_DIR not in Path(module.DEFAULT_WORK_ROOT).parents


def test_run_claude_skill_ab_should_build_baseline_and_enhanced_records(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_script", "benchmarks/run_claude_skill_ab.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "demo.py").write_text("old\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8"
    )
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")

    calls: list[tuple[str, str, str, str]] = []

    def _fake_run(repo_dir, prompt, **kwargs):
        has_skill = (Path(repo_dir) / ".claude" / "skills" / "tensor-grep" / "SKILL.md").exists()
        calls.append((str(repo_dir), str(repo_dir), prompt, str(has_skill)))
        if has_skill:
            assert (Path(repo_dir) / "CLAUDE.md").exists()
            (Path(repo_dir) / "demo.py").write_text("new\n", encoding="utf-8")
            Path(kwargs["extra_env"]["TENSOR_GREP_TRACE_LOG"]).write_text(
                '{"argv":["tg","defs","Demo"],"exit_code":0,"duration_seconds":0.125,"timestamp_epoch_s":100.125}\n',
                encoding="utf-8",
            )
        return "ok"

    monkeypatch.setattr(module, "_run_claude_command", _fake_run)

    payload = module.build_payload(
        {
            "records": [
                {
                    "instance_id": "demo-1",
                    "repo_fixture": str(repo_root),
                    "prompt": "Fix the bug.",
                    "actual_validation_commands": ["pytest -q"],
                }
            ]
        },
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        skill_dir=skill_dir,
        work_root=tmp_path / "work",
        enhanced_output_contract="done",
        enhanced_task_contract="engage",
        enhanced_effort="low",
    )

    assert payload["artifact"] == "claude_skill_ab"
    assert payload["enhanced_output_contract"] == "done"
    assert payload["enhanced_task_contract"] == "engage"
    assert payload["enhanced_effort"] == "low"
    assert payload["trace_artifact"] == "claude_skill_ab_trace"
    assert len(payload["trace_records"]) == 2
    assert [record["system"] for record in payload["records"]] == [
        "claude-baseline",
        "claude-enhanced",
    ]
    assert payload["records"][0]["model_patch"] == ""
    assert "diff --git a/demo.py b/demo.py" in payload["records"][1]["model_patch"]
    assert calls[0][3] == "False"
    assert calls[1][3] == "True"
    assert "edit the repository files directly" in calls[0][2]
    assert "Use the tensor-grep project skill" not in calls[0][2]
    assert "Use the tensor-grep project skill" in calls[1][2]
    assert payload["trace_records"][0]["use_skill"] is False
    assert payload["trace_records"][1]["use_skill"] is True
    assert payload["trace_records"][1]["enhanced_output_contract"] == "done"
    assert payload["trace_records"][1]["enhanced_task_contract"] == "engage"
    assert payload["trace_records"][0]["effort"] == "default"
    assert payload["trace_records"][1]["effort"] == "low"
    assert payload["trace_records"][0]["response_shape"] == "analysis_only"
    assert payload["trace_records"][1]["response_shape"] == "analysis_then_patch"
    assert payload["trace_records"][1]["asked_meta_question"] is False
    assert payload["trace_records"][0]["first_patch_seconds"] is None
    assert payload["trace_records"][1]["first_patch_seconds"] is not None
    assert payload["trace_records"][1]["first_file_change_seconds"] is not None
    assert payload["trace_records"][1]["post_edit_deliberation_seconds"] is not None
    assert payload["trace_records"][1]["changed_file_count"] == 1
    assert payload["trace_records"][1]["tg_invocation_count"] == 1
    assert payload["trace_records"][1]["tg_seconds_total"] == 0.125
    assert payload["trace_records"][1]["first_tg_seconds"] is not None
    assert payload["trace_records"][1]["tg_trace_records"][0]["argv"] == ["tg", "defs", "Demo"]
    assert "claude_seconds" in payload["trace_records"][0]["timing"]


def test_run_claude_skill_ab_should_build_attempt_ledger_payloads_by_instance(tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_ledger_script", "benchmarks/run_claude_skill_ab.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    driver_payload = {
        "records": [
            {"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "Fix one."},
            {"instance_id": "demo-2", "repo_fixture": str(repo_root), "prompt": "Fix two."},
        ]
    }
    prediction_records = [
        {
            "instance_id": "demo-1",
            "system": "claude-baseline",
            "model_patch": "",
            "notes": "timeout after 60s",
        },
        {
            "instance_id": "demo-1",
            "system": "claude-enhanced",
            "model_patch": "diff --git a/x b/x",
            "notes": "",
        },
        {"instance_id": "demo-2", "system": "claude-baseline", "model_patch": "", "notes": ""},
        {"instance_id": "demo-2", "system": "claude-enhanced", "model_patch": "", "notes": ""},
    ]
    trace_records = [
        {"instance_id": "demo-1", "system": "claude-baseline", "response_shape": "analysis_only"},
        {
            "instance_id": "demo-1",
            "system": "claude-enhanced",
            "response_shape": "analysis_then_patch",
        },
        {"instance_id": "demo-2", "system": "claude-baseline", "response_shape": "meta_question"},
        {"instance_id": "demo-2", "system": "claude-enhanced", "response_shape": "analysis_only"},
    ]

    ledgers = module.build_attempt_ledger_payloads(
        driver_payload, prediction_records, trace_records
    )

    assert set(ledgers) == {"demo-1", "demo-2"}
    accepted = ledgers["demo-1"]
    assert accepted["artifact"] == "agent_attempt_ledger"
    assert accepted["task_id"] == "demo-1"
    assert accepted["root"] == str(repo_root)
    assert accepted["final_outcome"]["status"] == "completed"
    assert accepted["final_outcome"]["accepted_attempt_id"] is None
    assert accepted["replay"]["next_action"] == "score patch bakeoff"
    assert accepted["attempts"][0]["status"] == "needs_retry"
    assert accepted["attempts"][0]["retry_reason"] == "timeout after 60s"
    assert accepted["attempts"][1]["status"] == "completed"
    assert accepted["attempts"][1]["outputs"] == ["analysis_then_patch"]
    retry = ledgers["demo-2"]
    assert retry["final_outcome"]["status"] == "needs_retry"
    assert retry["attempts"][0]["status"] == "needs_retry"
    assert retry["attempts"][1]["status"] == "needs_retry"


def test_run_claude_skill_ab_should_write_attempt_ledgers_when_requested(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_skill_ab_ledger_cli_script", "benchmarks/run_claude_skill_ab.py"
    )
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8"
    )
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    output_path = tmp_path / "ab.json"
    trace_path = tmp_path / "ab_trace.json"
    ledger_dir = tmp_path / "attempt_ledgers"
    captured: list[tuple[Path, dict[str, object]]] = []

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_claude_skill_ab.py",
            "--input",
            str(tmp_path / "driver.json"),
            "--output",
            str(output_path),
            "--trace-output",
            str(trace_path),
            "--attempt-ledger-dir",
            str(ledger_dir),
            "--skill-dir",
            str(skill_dir),
            "--work-root",
            str(tmp_path / "work"),
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
            "artifact": "claude_skill_ab",
            "trace_artifact": "claude_skill_ab_trace",
            "suite": "run_claude_skill_ab",
            "generated_at_epoch_s": 1.0,
            "environment": {"platform": "windows"},
            "records": [
                {
                    "instance_id": "demo-1",
                    "system": "claude-baseline",
                    "model_patch": "",
                    "notes": "timeout after 60s",
                },
                {
                    "instance_id": "demo-1",
                    "system": "claude-enhanced",
                    "model_patch": "diff --git a/x b/x",
                    "notes": "",
                },
            ],
            "trace_records": [
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
        },
    )

    def _fake_write_json(path: Path, payload: dict[str, object]) -> None:
        captured.append((Path(path), payload))

    monkeypatch.setattr(module, "write_json", _fake_write_json)

    exit_code = module.main()

    assert exit_code == 0
    assert captured[0][0] == output_path.resolve()
    assert captured[0][1]["artifact"] == "claude_skill_ab"
    assert captured[1][0] == trace_path.resolve()
    assert captured[1][1]["artifact"] == "claude_skill_ab_trace"
    assert captured[2][0] == (ledger_dir / "demo-1.json").resolve()
    assert captured[2][1]["artifact"] == "agent_attempt_ledger"
