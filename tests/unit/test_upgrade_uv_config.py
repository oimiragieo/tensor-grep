"""A repo-local interpreter directory still inherits ancestor uv configuration."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def uv_config_fixture(tmp_path, monkeypatch):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv executable unavailable")
    real_python = sys.executable
    repo = tmp_path / "repo"
    interpreter_dir = repo / ".venv" / "Scripts"
    interpreter_dir.mkdir(parents=True)
    (repo / "uv.toml").write_text("invalid-review-key = true\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    return str(Path(uv).resolve()), real_python, interpreter_dir, tmp_path


@pytest.mark.parametrize("tool_managed", [False, True])
def test_upgrade_uv_attempts_disable_config_and_leave_pip_unchanged(
    tmp_path, monkeypatch, tool_managed
):
    from tensor_grep.cli import main, runtime_paths

    uv = str(tmp_path / "trusted" / "uv.exe")
    python = (
        str(tmp_path / "uv" / "tools" / "tensor-grep" / "python.exe")
        if tool_managed
        else sys.executable
    )
    monkeypatch.setattr(sys, "executable", python)
    monkeypatch.setattr(runtime_paths, "resolve_executable_path", lambda _name: uv)
    attempts = main._upgrade_attempts("tensor-grep==1.2.3")
    assert [label for label, _ in attempts] == (
        ["uv-tool", "uv", "pip"] if tool_managed else ["uv", "pip"]
    )
    for label, argv in attempts:
        if label.startswith("uv"):
            assert argv[:3] == [uv, "--no-config", "tool" if label == "uv-tool" else "pip"]
        else:
            assert argv == [
                python,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--no-cache-dir",
                "tensor-grep==1.2.3",
            ]


@pytest.mark.parametrize(
    "explicit_no_config", [False, True], ids=["sibling-cwd", "nested-no-config"]
)
def test_real_uv_offline_dry_run_fixture_positive_controls(uv_config_fixture, explicit_no_config):
    uv, python, interpreter_dir, outside = uv_config_fixture
    argv = [
        uv,
        *(["--no-config"] if explicit_no_config else []),
        "pip",
        "install",
        "--dry-run",
        "--offline",
        "--python",
        python,
        "tensor-grep",
    ]
    result = subprocess.run(
        argv,
        cwd=interpreter_dir if explicit_no_config else outside,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_source_upgrade_uv_attempt_ignores_repo_config_in_local_venv(
    uv_config_fixture, monkeypatch
):
    from tensor_grep.cli import main, runtime_paths

    uv, python, interpreter_dir, _outside = uv_config_fixture
    monkeypatch.setattr(runtime_paths, "resolve_executable_path", lambda _name: uv)
    monkeypatch.setattr(sys, "executable", str(interpreter_dir / "python.exe"))
    argv = next(argv for label, argv in main._upgrade_attempts("tensor-grep") if label == "uv")
    # Keep the generated executable/global flags, replacing installation with an inert dry run.
    argv = [
        *argv[: argv.index("install") + 1],
        "--dry-run",
        "--offline",
        "--python",
        python,
        "tensor-grep",
    ]
    result, label = main._run_upgrade([("uv", argv)])
    assert result.returncode == 0 and label == "uv"
