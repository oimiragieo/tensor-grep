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


def test_agent_success_harness_should_emit_end_to_end_contract(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_agent_success_harness_contract",
        "benchmarks/run_agent_success_harness.py",
    )
    output_path = tmp_path / "agent_success.json"
    corpus = tmp_path / "corpus"
    target = corpus / "src" / "payments.py"
    target.parent.mkdir(parents=True)
    original_text = "def create_invoice_tax(amount): return amount * 0.0825\n"
    target.write_text(original_text, encoding="utf-8")
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    checkpoint_id = "ckpt-test"

    def _fake_run_json_command(command):
        command_text = " ".join(str(part) for part in command)
        if " agent " in f" {command_text} ":
            return 0.11, {
                "primary_target": {"file": str(target), "symbol": "create_invoice_tax"},
                "alternative_targets": [{"file": str(corpus / "src" / "app.ts")}],
                "validation_commands": ["python -m py_compile src/payments.py"],
                "ask_user_before_editing": {"required": False},
            }
        if " context-render " in f" {command_text} ":
            return 0.12, {
                "edit_plan_seed": {"primary_file": str(target)},
                "navigation_pack": {"primary_target": {"file": str(target)}},
                "rendered_context": "def create_invoice_tax(amount): ...",
                "sources": [{"file": str(target)}],
                "validation_commands": ["python -m py_compile src/payments.py"],
            }
        if " edit-plan " in f" {command_text} ":
            return 0.13, {
                "edit_plan_seed": {
                    "primary_file": str(target),
                    "validation_plan": [{"command": "python -m py_compile src/payments.py"}],
                },
                "validation_commands": ["python -m py_compile src/payments.py"],
            }
        if " checkpoint create " in f" {command_text} ":
            return 0.04, {
                "checkpoint_id": checkpoint_id,
                "undo_argv": ["tg", "checkpoint", "undo", checkpoint_id, str(corpus)],
                "undo_command": f"tg checkpoint undo {checkpoint_id} {corpus}",
            }
        if " run " in f" {command_text} " and "--apply" in command:
            target.write_text(
                "create_invoice_tax = lambda amount: amount * 0.0825\n", encoding="utf-8"
            )
            return 0.21, {"plan": {"total_edits": 1}, "total_edits": 1}
        if " checkpoint undo " in f" {command_text} ":
            target.write_text(original_text, encoding="utf-8")
            return 0.05, {"checkpoint_id": checkpoint_id, "files_restored": [str(target)]}
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(module, "run_json_command", _fake_run_json_command)
    monkeypatch.setattr(
        module,
        "run_validation_commands",
        lambda *, corpus_dir, target_file: (
            0.03,
            [
                {
                    "command": ["python", "-m", "py_compile", str(target_file)],
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                    "passed": True,
                }
            ],
        ),
    )

    row = module.run_agent_success_scenario(
        tg_binary=tg_binary,
        corpus_dir=corpus,
        scenario=module.DEFAULT_SCENARIOS[0],
        max_files=3,
        max_sources=5,
        max_tokens=1200,
        max_repo_files=512,
        pattern=module.DEFAULT_PATTERN,
        replacement=module.DEFAULT_REPLACEMENT,
    )

    assert row["passed"] is True
    assert row["intent"]["primary_file"].replace("\\", "/").endswith("src/payments.py")
    assert row["context"]["primary_file"].replace("\\", "/").endswith("src/payments.py")
    assert row["edit_seed"]["primary_file"].replace("\\", "/").endswith("src/payments.py")
    assert row["apply"]["changed"] is True
    assert row["verify"]["changed_after_apply"] is True
    assert row["verify"]["validation_results"][0]["passed"] is True
    assert row["rollback"]["restored"] is True
    assert target.read_text(encoding="utf-8") == original_text

    payload = module.build_payload(
        output_path=output_path,
        tg_binary=tg_binary,
        corpus_manifest={"file_count": 1, "seed": 42},
        scenarios=[row],
        args=module.argparse.Namespace(
            seed=42,
            iterations=1,
            max_files=3,
            max_sources=5,
            max_tokens=1200,
            max_repo_files=512,
            pattern=module.DEFAULT_PATTERN,
            replacement=module.DEFAULT_REPLACEMENT,
        ),
    )

    assert payload["artifact"] == "bench_agent_success_harness"
    assert payload["suite"] == "run_agent_success_harness"
    assert payload["positioning"] == module.POSITIONING
    assert payload["workflow_surfaces"] == [
        "intent",
        "context",
        "edit_seed",
        "apply",
        "verify",
        "rollback",
    ]
    assert payload["summary"]["all_passed"] is True


def test_agent_success_harness_main_writes_payload(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_agent_success_harness_main",
        "benchmarks/run_agent_success_harness.py",
    )
    output_path = tmp_path / "agent_success.json"
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    corpus = tmp_path / "corpus"

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_agent_success_harness.py",
            "--output",
            str(output_path),
            "--iterations",
            "1",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(module, "resolve_agent_success_bench_dir", lambda: tmp_path / "bench")
    monkeypatch.setattr(
        module,
        "ensure_agent_success_corpus",
        lambda output_dir, *, seed: {
            "corpus_dir": corpus,
            "manifest_path": output_dir / "manifest.json",
            "file_count": 3,
            "seed": seed,
        },
    )
    monkeypatch.setattr(
        module,
        "run_agent_success_harness",
        lambda **_kwargs: [
            {
                "scenario": "python_invoice_success",
                "passed": True,
                "phase_timings_s": {},
            }
        ],
    )

    assert module.main() == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_agent_success_harness"
    assert payload["summary"]["scenario_count"] == 1
    assert payload["summary"]["all_passed"] is True


def test_agent_success_harness_should_refuse_stale_in_tree_native_binary_by_default(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_agent_success_harness_stale_refusal",
        "benchmarks/run_agent_success_harness.py",
    )
    output_path = tmp_path / "agent_success.json"
    tg_binary = tmp_path / "repo" / "rust_core" / "target" / "release" / "tg.exe"
    tg_binary.parent.mkdir(parents=True, exist_ok=True)
    tg_binary.write_text("stale", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_agent_success_harness.py",
            "--binary",
            str(tg_binary),
            "--output",
            str(output_path),
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(
        module,
        "benchmark_binary_warnings",
        lambda _binary: ["tensor-grep benchmark warning: stale in-tree native tg binary"],
    )
    monkeypatch.setattr(
        module,
        "ensure_agent_success_corpus",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale benchmark binary should fail before corpus setup")
        ),
    )

    assert module.main() == 2
    assert not output_path.exists()


def test_agent_success_harness_should_allow_exploratory_stale_in_tree_binary(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_agent_success_harness_stale_exploratory",
        "benchmarks/run_agent_success_harness.py",
    )
    output_path = tmp_path / "agent_success.json"
    tg_binary = tmp_path / "repo" / "rust_core" / "target" / "release" / "tg.exe"
    tg_binary.parent.mkdir(parents=True, exist_ok=True)
    tg_binary.write_text("stale", encoding="utf-8")
    corpus = tmp_path / "corpus"

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_agent_success_harness.py",
            "--binary",
            str(tg_binary),
            "--output",
            str(output_path),
            "--allow-claim-unsafe-launcher",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(
        module,
        "benchmark_binary_warnings",
        lambda _binary: ["tensor-grep benchmark warning: stale in-tree native tg binary"],
    )
    monkeypatch.setattr(module, "resolve_agent_success_bench_dir", lambda: tmp_path / "bench")
    monkeypatch.setattr(
        module,
        "ensure_agent_success_corpus",
        lambda output_dir, *, seed: {
            "corpus_dir": corpus,
            "manifest_path": output_dir / "manifest.json",
            "file_count": 3,
            "seed": seed,
        },
    )
    monkeypatch.setattr(
        module,
        "run_agent_success_harness",
        lambda **_kwargs: [
            {
                "scenario": "python_invoice_success",
                "passed": True,
                "phase_timings_s": {},
            }
        ],
    )

    assert module.main() == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["warnings"] == ["tensor-grep benchmark warning: stale in-tree native tg binary"]
    assert payload["environment"]["tg_binary_version_status"] == "unsafe-launcher-allowed"
