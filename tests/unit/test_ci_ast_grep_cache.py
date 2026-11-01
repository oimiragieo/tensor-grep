"""Exercise test-tool cache hits/misses without downloading or compiling a tool."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"


def _steps():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["test-python"]["steps"]


def test_cache_is_scoped_to_versioned_test_tool():
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    caches = [
        (name, step)
        for name, job in jobs.items()
        for step in job.get("steps", [])
        if step.get("id") == "ast-grep-cache"
    ]
    assert len(caches) == 1 and caches[0][0] == "test-python"
    cache = caches[0][1]
    assert cache["uses"] == "actions/cache@caa296126883cff596d87d8935842f9db880ef25"
    assert cache["with"] == {
        "path": "${{ runner.temp }}/tg-ast-grep",
        "key": "ast-grep-${{ runner.os }}-${{ runner.arch }}-0.41.1-rust-1.96.0-v1",
    }


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="POSIX shell fixture"
)
@pytest.mark.parametrize(
    "hit,version,exit_code,success",
    [
        (False, "0.41.1", 0, True),
        (True, "0.41.1", 0, True),
        (True, "0.0.0", 0, False),
        (True, "0.41.1", 1, False),
    ],
)
def test_install_step_cold_warm_and_wrong_version(tmp_path, hit, version, exit_code, success):
    runner_temp = tmp_path / "runner temp"
    tool = runner_temp / "tg-ast-grep/bin/ast-grep"
    tool.parent.mkdir(parents=True)
    fake_binary = f"#!/bin/sh\nprintf 'ast-grep {version}\\n'\nexit {exit_code}\n"
    if hit:
        tool.write_text(fake_binary, newline="\n")
        tool.chmod(0o755)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    cargo = stubs / "cargo"
    cargo.write_text(
        "#!/bin/sh\nset -eu\n"
        'printf "%s\\n" "$*" > "$RUNNER_TEMP/cargo-call"\n'
        'printf "#!/bin/sh\\nprintf \'ast-grep 0.41.1\\\\n\'\\n" > "$RUNNER_TEMP/tg-ast-grep/bin/ast-grep"\n'
        'chmod +x "$RUNNER_TEMP/tg-ast-grep/bin/ast-grep"\n',
        newline="\n",
    )
    cargo.chmod(0o755)
    path_file = tmp_path / "github-path"
    step = next(
        step
        for step in _steps()
        if step.get("name") == "Install ast-grep CLI (AST wrapper backend)"
    )
    script = step["run"].replace("${{ steps.ast-grep-cache.outputs.cache-hit }}", str(hit).lower())
    result = subprocess.run(
        [shutil.which("bash"), "-c", script],
        env={
            **os.environ,
            "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": runner_temp.as_posix(),
            "RUNNER_OS": "Linux",
            "GITHUB_PATH": path_file.as_posix(),
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert (result.returncode == 0) is success, result.stderr
    assert (runner_temp / "cargo-call").exists() is (not hit)
    if success:
        assert Path(path_file.read_text().strip()) == tool.parent
    else:
        assert not path_file.exists()
