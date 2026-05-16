import importlib.util
import json
import sys
from pathlib import Path


def _load_run_benchmarks_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "benchmarks" / "run_benchmarks.py"
    spec = importlib.util.spec_from_file_location(
        "run_benchmarks_script_word_boundary_launcher",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_positional_early_rg_launcher_should_cover_word_boundary(monkeypatch, tmp_path):
    module = _load_run_benchmarks_module()
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd, mode, env = module.build_tg_benchmark_cmd_with_mode(
        ["-w", "timeout", "bench_data"],
        return_mode=True,
        return_env=True,
        launcher_mode="explicit_binary_positional_early_rg",
    )

    assert cmd == [str(tg_binary), "-w", "timeout", "bench_data"]
    assert mode == "explicit_binary_positional_early_rg"
    assert env == {"TG_RUST_EARLY_POSITIONAL_RG": "1"}


def test_launcher_command_kind_should_identify_timed_entrypoint(tmp_path):
    module = _load_run_benchmarks_module()

    assert module.classify_tg_launcher_command([str(tmp_path / "tg.exe"), "search"]) == "native_exe"
    assert module.classify_tg_launcher_command([str(tmp_path / "tg.cmd"), "search"]) == "cmd_shim"
    assert module.classify_tg_launcher_command(["uv", "run", "tg", "search"]) == "uv"
    assert module.classify_tg_launcher_command([sys.executable, "-m", "tensor_grep"]) == (
        "python_module"
    )


def test_benchmark_launcher_warnings_should_flag_non_native_timed_entrypoints():
    module = _load_run_benchmarks_module()

    native_warnings = module.benchmark_launcher_warnings(
        command_kind="native_exe",
        launcher_mode="explicit_binary",
    )
    shim_warnings = module.benchmark_launcher_warnings(
        command_kind="cmd_shim",
        launcher_mode="discovered_cli_binary",
    )

    assert native_warnings == []
    assert shim_warnings
    assert "cmd_shim" in shim_warnings[0]
    assert "wrapper/interpreter overhead" in shim_warnings[0]
    assert "native executable" in shim_warnings[0]


def test_benchmark_binary_warnings_should_flag_stale_in_tree_native_binary(monkeypatch, tmp_path):
    module = _load_run_benchmarks_module()
    tg_binary = tmp_path / "repo" / "rust_core" / "target" / "release" / "tg.exe"
    tg_binary.parent.mkdir(parents=True, exist_ok=True)
    tg_binary.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "inspect_native_tg_binary",
        lambda *_args, **_kwargs: {
            "path": str(tg_binary),
            "kind": "in-tree-release",
            "version": "tg 1.12.0",
            "expected_version": "1.12.4",
            "version_status": "stale",
        },
    )

    warnings = module.benchmark_binary_warnings(tg_binary)

    assert warnings
    assert "stale in-tree native tg binary" in warnings[0]
    assert "tg 1.12.0" in warnings[0]
    assert "1.12.4" in warnings[0]


def test_run_benchmarks_should_record_tg_binary_version_metadata(monkeypatch, tmp_path):
    module = _load_run_benchmarks_module()
    tg_binary = tmp_path / "repo" / "rust_core" / "target" / "release" / "tg.exe"
    tg_binary.parent.mkdir(parents=True, exist_ok=True)
    tg_binary.write_text("native\n", encoding="utf-8")
    output_path = tmp_path / "bench.json"
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
        lambda *_args, **_kwargs: (tg_binary, "default_binary_path"),
    )
    monkeypatch.setattr(
        module,
        "inspect_native_tg_binary",
        lambda *_args, **_kwargs: {
            "path": str(tg_binary),
            "kind": "in-tree-release",
            "version": "tg 1.12.0",
            "expected_version": "1.12.4",
            "version_status": "stale",
        },
    )
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "run_cmd_timing", lambda *_args, **_kwargs: 0.1)
    monkeypatch.setattr(module, "run_cmd_capture", lambda *_args, **_kwargs: (0.0, "ok"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_benchmarks.py",
            "--output",
            str(output_path),
            "--allow-claim-unsafe-launcher",
        ],
    )

    exit_code = module.main()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["environment"]["tg_binary_kind"] == "in-tree-release"
    assert payload["environment"]["tg_binary_version_status"] == "stale"
    assert payload["environment"]["tg_binary_expected_version"] == "1.12.4"
    assert payload["environment"]["tg_binary_version"] == "tg 1.12.0"
    assert any("stale in-tree native tg binary" in warning for warning in payload["warnings"])


def test_run_benchmarks_should_record_launcher_command_kind_in_environment(monkeypatch, tmp_path):
    module = _load_run_benchmarks_module()
    tg_binary = tmp_path / "tg.cmd"
    tg_binary.write_text("@echo off\n", encoding="utf-8")
    output_path = tmp_path / "bench.json"
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
    monkeypatch.setattr(module, "run_cmd_timing", lambda *_args, **_kwargs: 0.1)
    monkeypatch.setattr(module, "run_cmd_capture", lambda *_args, **_kwargs: (0.0, "ok"))
    monkeypatch.setattr(sys, "argv", ["run_benchmarks.py", "--output", str(output_path)])

    exit_code = module.main()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["environment"]["tg_launcher_mode"] == "explicit_binary"
    assert payload["environment"]["tg_launcher_command_kind"] == "cmd_shim"
    assert payload["warnings"]
    assert "cmd_shim" in payload["warnings"][0]
