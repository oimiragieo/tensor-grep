from __future__ import annotations

import runpy
from pathlib import Path


def test_build_binaries_should_target_bootstrap_entrypoint(monkeypatch):
    seen: dict[str, object] = {}
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "build_binaries.py"

    def _fake_run(cmd: list[str], *args, **kwargs):
        seen["cmd"] = list(cmd)

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setattr("os.path.exists", lambda _path: False)

    runpy.run_path(str(script_path), run_name="__main__")

    cmd = seen["cmd"]
    assert cmd[-1] == "src/tensor_grep/cli/bootstrap.py"
