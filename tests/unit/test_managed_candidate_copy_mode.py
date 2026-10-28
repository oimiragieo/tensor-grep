import os
import shutil
import subprocess
import sys
import zipfile

import pytest

from tensor_grep.cli import managed_runtime_candidate as candidate


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hard-link behavior")
def test_ManagedCandidate_UsesCopiesInsteadOfSharedCacheFiles(monkeypatch, tmp_path):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed")
    cache = tmp_path / "cache"
    monkeypatch.delenv("UV_NO_CACHE", raising=False)
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setenv("UV_LINK_MODE", "hardlink")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    wheel = tmp_path / "tensor_grep-0.0.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "x") as archive:
        archive.writestr("tensor_grep/__init__.py", b'PROBE = "before"\n')
        archive.writestr(
            "tensor_grep-0.0.1.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: tensor-grep\nVersion: 0.0.1\n\n",
        )
        archive.writestr(
            "tensor_grep-0.0.1.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
        )
        archive.writestr("tensor_grep-0.0.1.dist-info/RECORD", "")

    control = tmp_path / "control"
    selected = tmp_path / "selected"
    for path in (control, selected):
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(path)],
            check=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    control_python = control / "Scripts" / "python.exe"
    selected_python = selected / "Scripts" / "python.exe"
    subprocess.run(
        [uv, "pip", "install", "--python", str(control_python), "--no-deps", str(wheel)],
        check=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert (
        candidate._run_managed_candidate_install(
            selected_python, str(wheel), source_cuda=None, source_extras=()
        )
        == "uv"
    )

    control_file = control / "Lib" / "site-packages" / "tensor_grep" / "__init__.py"
    selected_file = selected / "Lib" / "site-packages" / "tensor_grep" / "__init__.py"
    assert control_file.read_bytes() == selected_file.read_bytes() == b'PROBE = "before"\n'
    aliases = [path for path in cache.rglob("__init__.py") if os.path.samefile(path, control_file)]
    assert len(aliases) == 1
    aliases[0].write_bytes(b'PROBE = "changed"\n')
    assert control_file.read_bytes() == b'PROBE = "changed"\n'
    assert selected_file.read_bytes() == b'PROBE = "before"\n'
