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


def test_run_ast_benchmarks_should_emit_m3_gate_artifact(monkeypatch, tmp_path):
    module = _load_script_module("run_ast_benchmarks_m3_gate", "benchmarks/run_ast_benchmarks.py")
    output_path = tmp_path / "bench_ast_m3.json"
    corpus_dir = tmp_path / "ast_bench"
    tg_binary = tmp_path / "tg.exe"
    sg_binary = tmp_path / "sg.cmd"
    hyperfine_binary = tmp_path / "hyperfine.exe"
    for path in (tg_binary, sg_binary, hyperfine_binary):
        path.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        ["run_ast_benchmarks.py", "--output", str(output_path), "--runs", "10"],
    )
    monkeypatch.setattr(
        module,
        "ensure_ast_bench_corpus",
        lambda *_args, **_kwargs: {
            "corpus_dir": corpus_dir,
            "manifest_path": tmp_path / "ast_bench.manifest.sha256",
            "file_count": 1000,
            "total_loc": 50000,
        },
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)
    monkeypatch.setattr(module, "resolve_ast_grep_binary", lambda: sg_binary)
    monkeypatch.setattr(module, "resolve_hyperfine_binary", lambda: hyperfine_binary)
    monkeypatch.setattr(
        module,
        "run_hyperfine",
        lambda *_args, **_kwargs: {
            "results": [
                {"command": str(tg_binary), "median": 0.9},
                {"command": str(sg_binary), "median": 0.4},
            ]
        },
    )

    exit_code = module.main()

    assert exit_code == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_ast_m3"
    assert payload["file_count"] == 1000
    assert payload["total_loc"] == 50000
    assert payload["tg_median_s"] == 0.9
    assert payload["sg_median_s"] == 0.4
    assert payload["ratio"] == 2.25
    assert payload["threshold"] == 1.1
    assert payload["passed"] is False


def test_run_ast_parity_check_should_fail_explicitly_when_sg_is_missing(monkeypatch, tmp_path):
    module = _load_script_module("run_ast_parity_missing_sg", "benchmarks/run_ast_parity_check.py")
    output_path = tmp_path / "ast_parity_report.json"
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["run_ast_parity_check.py", "--output", str(output_path)])
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)
    monkeypatch.setattr(module, "resolve_ast_grep_binary", lambda: None)
    monkeypatch.setattr(
        module,
        "ensure_ast_parity_corpus",
        lambda *_args, **_kwargs: {
            "corpus_dir": tmp_path / "ast_parity",
            "manifest_path": tmp_path / "ast_parity.manifest.sha256",
        },
    )

    exit_code = module.main()

    assert exit_code == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["total_cases"] == 40
    assert payload["passed_cases"] == 0
    assert payload["failed_cases"] == 40
    assert "ast-grep binary not found" in payload["error"]
    assert "cargo install ast-grep --version 0.41.1" in payload["error"]


def test_run_ast_parity_check_should_report_40_passing_cases(monkeypatch, tmp_path):
    module = _load_script_module("run_ast_parity_all_pass", "benchmarks/run_ast_parity_check.py")
    output_path = tmp_path / "ast_parity_report.json"
    tg_binary = tmp_path / "tg.exe"
    sg_binary = tmp_path / "sg.cmd"
    for path in (tg_binary, sg_binary):
        path.write_text("binary", encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["run_ast_parity_check.py", "--output", str(output_path)])
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)
    monkeypatch.setattr(module, "resolve_ast_grep_binary", lambda: sg_binary)
    monkeypatch.setattr(
        module,
        "ensure_ast_parity_corpus",
        lambda *_args, **_kwargs: {
            "corpus_dir": tmp_path / "ast_parity",
            "manifest_path": tmp_path / "ast_parity.manifest.sha256",
        },
    )
    monkeypatch.setattr(
        module, "run_parity_case", lambda *_args, **_kwargs: {"passed": True, "divergence": []}
    )

    exit_code = module.main()

    assert len(module.PARITY_CASES) == 40
    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["total_cases"] == 40
    assert payload["passed_cases"] == 40
    assert payload["failed_cases"] == 0
    assert payload["status"] == "PASS"
