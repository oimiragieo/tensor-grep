from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "smoke_test_pypi_artifacts.py"
    spec = importlib.util.spec_from_file_location("smoke_test_pypi_artifacts", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_should_run_smoke_install_from_local_dist(tmp_path: Path, monkeypatch):
    module = _load_module()
    calls: list[list[str]] = []

    def _fake_run(cmd, check, **kwargs):
        calls.append([str(item) for item in cmd])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    module.run_smoke_test(
        dist_dir=tmp_path,
        version="0.11.1",
        work_dir=tmp_path / "work",
    )

    assert len(calls) == 7
    expected_python = module._venv_python(tmp_path / "work" / ".pypi-smoke-venv")
    assert calls[0][:3] == [module.sys.executable, "-m", "venv"]
    assert calls[1][:4] == [str(expected_python), "-m", "pip", "install"]
    assert any(dep.startswith("typer") for dep in calls[1])
    assert calls[2][:8] == [
        str(expected_python),
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        str(tmp_path.resolve()),
        "--no-deps",
    ]
    assert calls[2][-1] == "tensor-grep==0.11.1"
    assert calls[3][1] == "-c"
    assert calls[4][-1] == "--version"
    assert calls[5][1] == "-c"
    assert calls[6][1] == "-c"
    assert "'run'" in calls[5][2]
    assert "'run'" in calls[6][2]
    assert "'--apply'" in calls[6][2]


def test_should_resolve_linux_tg_shim_path(tmp_path: Path, monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module.sys, "platform", "linux")

    venv_dir = tmp_path / ".venv"
    expected = venv_dir / "bin" / "tg"
    assert module._venv_tg(venv_dir) == expected


def test_should_prefer_existing_windows_tg_cmd_shim(tmp_path: Path, monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module.sys, "platform", "win32")

    venv_dir = tmp_path / ".venv"
    scripts_dir = venv_dir / "Scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "tg.cmd").write_text("@echo off\r\n", encoding="utf-8")

    assert module._venv_tg(venv_dir) == (scripts_dir / "tg.cmd")
