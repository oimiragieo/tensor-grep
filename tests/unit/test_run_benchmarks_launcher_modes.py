import importlib.util
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
