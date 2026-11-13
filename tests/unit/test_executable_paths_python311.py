"""Python 3.11 which() returns early for explicit paths before expanding PATHEXT."""

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PATHEXT lookup")


@pytest.fixture
def python311_which(monkeypatch):
    def which(command, mode=os.F_OK | os.X_OK, path=None):
        # Python 3.11's direct-path branch does not append Windows executable extensions.
        assert Path(command).is_absolute()
        return command if Path(command).is_file() else None

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")


@pytest.mark.parametrize(
    "name,filename", [("uv", "uv.exe"), ("ast-grep", "ast-grep.cmd"), ("rg.exe", "rg.exe")]
)
def test_python311_suffix_expansion_keeps_trusted_path_positive(
    tmp_path,
    monkeypatch,
    python311_which,
    name,
    filename,
):
    from tensor_grep.io.executable_paths import resolve_executable_path

    repo = tmp_path / "repo"
    trusted = tmp_path / "trusted"
    repo.mkdir()
    trusted.mkdir()
    (repo / filename).write_bytes(b"untrusted")
    executable = trusted / filename
    executable.write_bytes(b"trusted")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", str(trusted))
    assert resolve_executable_path(name) == str(executable)


@pytest.mark.parametrize("name,filename", [("uv", "uv.exe"), ("ast-grep", "ast-grep.cmd")])
def test_python311_suffix_expansion_never_selects_cwd(
    tmp_path,
    monkeypatch,
    python311_which,
    name,
    filename,
):
    from tensor_grep.io.executable_paths import resolve_executable_path

    repo = tmp_path / "repo"
    trusted = tmp_path / "trusted"
    repo.mkdir()
    trusted.mkdir()
    (repo / filename).write_bytes(b"untrusted")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", os.pathsep.join((str(repo), ".", str(trusted))))
    assert resolve_executable_path(name) is None


def test_ast_public_search_refuses_bare_fallback_without_spawning_cwd(tmp_path, monkeypatch):
    from tensor_grep.backends import ast_wrapper_backend
    from tensor_grep.backends.base import BackendExecutionError

    repo = tmp_path / "repo"
    trusted = tmp_path / "trusted"
    repo.mkdir()
    trusted.mkdir()
    (repo / "ast-grep.cmd").write_text("@echo off\necho UNTRUSTED\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", str(trusted))
    spawn = Mock()
    monkeypatch.setattr(ast_wrapper_backend.subprocess, "run", spawn)
    backend = ast_wrapper_backend.AstGrepWrapperBackend()
    assert backend._get_binary_name() == "ast-grep"
    assert backend.is_available() is False
    with pytest.raises(BackendExecutionError, match="requires the 'ast-grep' binary"):
        backend.search(str(repo / "sample.py"), "foo")
    spawn.assert_not_called()
