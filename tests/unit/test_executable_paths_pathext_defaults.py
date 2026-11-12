"""Empty Windows executable-extension settings retain safe default lookup."""

import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PATHEXT lookup")


@pytest.mark.parametrize(
    "pathext",
    ["", None, ";.EXE;.CMD;", ".COM;.EXE;.BAT;.CMD"],
    ids=["empty", "unset", "empty-entries", "normal"],
)
@pytest.mark.parametrize("name,filename", [("uv", "uv.exe"), ("ast-grep", "ast-grep.cmd")])
def test_pathext_defaults_and_empty_entries_keep_trusted_candidate(
    tmp_path,
    monkeypatch,
    pathext,
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
    if pathext is None:
        monkeypatch.delenv("PATHEXT", raising=False)
    else:
        monkeypatch.setenv("PATHEXT", pathext)

    def which(command):
        # Model the real 3.11 direct-path branch; it does not add PATHEXT suffixes.
        assert Path(command).is_absolute()
        return command if Path(command).is_file() else None

    monkeypatch.setattr(shutil, "which", which)
    assert resolve_executable_path(name) == str(executable)
