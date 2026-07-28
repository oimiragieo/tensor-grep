from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "smoke_test_pypi_artifacts.py"
    spec = importlib.util.spec_from_file_location("smoke_test_pypi_artifacts", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_should_run_smoke_install_from_local_dist(tmp_path: Path, monkeypatch):
    """The probes now invoke `tg` directly instead of through a nested `python -c` payload.

    The fake correspondingly has to ACT like tg -- emit the rewritten source on stdout, and write
    the file under `--apply`. That is not incidental test upkeep: under the old shape the probe's
    real work happened inside an opaque string this fake never executed, so the assertions could
    only inspect argv. Moving the logic into the parent process is what makes the outcome
    observable here at all.
    """
    module = _load_module()
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        argv = [str(item) for item in cmd]
        calls.append(argv)
        stdout = ""
        if "run" in argv and "--rewrite" in argv:
            stdout = '{"replacement_text": "lambda x, y: x + y"}'
            if "--apply" in argv:
                Path(argv[-1]).write_text("lambda x, y: x + y\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    module.run_smoke_test(
        dist_dir=tmp_path,
        version="0.11.1",
        work_dir=tmp_path / "work",
    )

    assert len(calls) == 7
    expected_python = module._venv_python(tmp_path / "work" / ".pypi-smoke-venv")
    expected_tg = str(module._venv_tg(tmp_path / "work" / ".pypi-smoke-venv"))
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
    assert calls[4] == [expected_tg, "--version"]

    # The two rewrite probes: real argv on the real binary, no interpreter in between.
    assert calls[5][:2] == [expected_tg, "run"]
    assert "--rewrite" in calls[5] and "--apply" not in calls[5]
    assert calls[6][:2] == [expected_tg, "run"]
    assert "--apply" in calls[6]
    for probe in (calls[5], calls[6]):
        assert "-c" not in probe, "a probe is still routing through a nested interpreter"


def test_should_fail_loudly_when_the_rewrite_plans_nothing(tmp_path: Path, monkeypatch, capsys):
    """A tg that exits 0 with useless output must fail the smoke AND say what it printed.

    This is the half a `CalledProcessError` can never express, and the half the v1.101.10 log
    needed: the command "succeeded", so the only signal was an AssertionError with no payload.
    """
    module = _load_module()

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="NOTHING-USEFUL", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(SystemExit) as excinfo:
        module.run_smoke_test(dist_dir=tmp_path, version="0.11.1", work_dir=tmp_path / "work")

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "NOTHING-USEFUL" in err, "the smoke failed without reporting what tg actually printed"


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
