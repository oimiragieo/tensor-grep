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

    assert len(calls) == 3
    assert calls[0][:3] == [module.sys.executable, "-m", "venv"]
    assert calls[1][2:6] == [
        "pip",
        "install",
        "--find-links",
        str(tmp_path.resolve()),
    ]
    assert calls[1][-1] == "tensor-grep==0.11.1"
    assert calls[2][1] == "-c"
