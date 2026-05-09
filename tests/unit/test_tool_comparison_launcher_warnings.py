import importlib.util
from pathlib import Path


def _load_tool_comparison_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "benchmarks" / "run_tool_comparison_benchmarks.py"
    spec = importlib.util.spec_from_file_location(
        "run_tool_comparison_benchmarks_launcher_warnings",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_comparison_should_warn_for_cmd_shim_tg_binary(tmp_path):
    module = _load_tool_comparison_module()
    tg_cmd = tmp_path / "tg.cmd"
    tg_cmd.write_text("@echo off\n", encoding="utf-8")

    warnings = module.tg_launcher_warnings_for_binary(tg_cmd)

    assert warnings
    assert "cmd_shim" in warnings[0]
    assert "wrapper/interpreter overhead" in warnings[0]


def test_tool_comparison_should_not_warn_for_native_tg_binary(tmp_path):
    module = _load_tool_comparison_module()
    tg_exe = tmp_path / "tg.exe"
    tg_exe.write_text("binary\n", encoding="utf-8")

    assert module.tg_launcher_warnings_for_binary(tg_exe) == []
