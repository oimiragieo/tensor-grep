"""Windows resolves executable names before applying a subprocess working directory."""

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows executable lookup")


def test_upgrade_does_not_spawn_cwd_executable_when_path_has_no_candidate(tmp_path, monkeypatch):
    from tensor_grep.cli import main

    repo = tmp_path / "repo"
    trusted = tmp_path / "trusted"
    repo.mkdir()
    trusted.mkdir()
    shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe", repo / "uv.exe")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", str(trusted))
    with pytest.raises(RuntimeError):
        main._run_upgrade([("fixture", ["uv.exe", "/c", "echo", "FIXTURE-UV"])])


def test_upgrade_accepts_explicit_absolute_interpreter(tmp_path, monkeypatch):
    from tensor_grep.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    result, label = main._run_upgrade([
        ("fixture", [sys.executable, "-c", "print('TRUSTED-PYTHON')"])
    ])
    assert label == "fixture"
    assert result.stdout.strip() == "TRUSTED-PYTHON"


def test_detached_upgrader_serializes_absolute_trusted_attempts(tmp_path, monkeypatch):
    import importlib.metadata

    from tensor_grep.cli import main

    trusted = tmp_path / "trusted"
    trusted.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    uv = trusted / "uv.exe"
    shutil.copy2(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe", uv)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", str(trusted))
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "1.0.0")
    monkeypatch.setattr(main, "_latest_pypi_tensor_grep_version", lambda: None)
    monkeypatch.setattr(main, "_upgrade_running_session_daemon_snapshot", lambda: None)
    monkeypatch.setattr(main, "_managed_native_frontdoor_path", lambda: None)
    monkeypatch.setattr(
        main._managed_runtime_upgrade, "handle_managed_windows_upgrade", lambda *_a: False
    )
    monkeypatch.setattr(main, "_run_upgrade", Mock(side_effect=RuntimeError("WinError 32")))
    popen = Mock()
    monkeypatch.setattr(main.subprocess, "Popen", popen)
    main.upgrade()
    popen.assert_called_once()
    attempts = json.loads(popen.call_args.args[0][5])
    assert attempts
    assert all(Path(argv[0]).is_absolute() for _, argv in attempts), attempts
    assert next(argv[0] for label, argv in attempts if label == "uv") == str(uv)
