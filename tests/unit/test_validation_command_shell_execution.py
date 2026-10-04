"""Validation commands are pasted into an UNKNOWN shell, so they are tested by EXECUTION.

A path made only of the conservative inert character set is emitted bare and must reach bash,
PowerShell and cmd byte-for-byte; any other path must never be interpolated into a command
string at all (fail closed), with the raw path disclosed in a separate field.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map

_PROBE = "import sys, json\nprint(json.dumps(sys.argv[1:]))\n"

SAFE_NAMES = [
    "test_plain.py",
    "test_a-b_c.py",
    "test_a+b@c=d,e:f.py",
]
# `$(...)` / backtick / `%VAR%` / `!` / `;` / `&` / `|` / quotes / space / glob / non-ASCII.
UNSAFE_NAMES = [
    "test_$(Write-Output PWN).py",
    "test_`whoami`.py",
    "test_%OS%.py",
    "test_bang!.py",
    "test_a;b.py",
    "test_a&b.py",
    "test_a|b.py",
    'test_a"b.py',
    "test_a'b.py",
    "test_a b.py",
    "test_[ab].py",
    "test_café.py",
    "test_$HOME.py",
]


def _commands_and_plan(project: Path, name: str) -> tuple[list[str], list[dict]]:
    test_path = project / "tests" / name
    plan = repo_map._validation_plan_for_tests(
        [str(test_path)],
        repo_root=project,
        primary_test=str(test_path),
        primary_symbol={"name": "x"},
    )
    return [str(step["command"]) for step in plan], plan


def _shell_runners(tmp_path: Path):
    script = tmp_path / "probe.py"
    script.write_text(_PROBE, encoding="utf-8")
    env = {**os.environ, "PROBE_PY": sys.executable, "PROBE_SCRIPT": str(script)}

    def bash(cmd: str):
        return subprocess.run(
            ["bash", "-c", cmd.replace("uv run pytest", '"$PROBE_PY" "$PROBE_SCRIPT"', 1)],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def pwsh(cmd: str):
        swapped = cmd.replace("uv run pytest", "& $env:PROBE_PY $env:PROBE_SCRIPT", 1)
        return subprocess.run(
            ["pwsh", "-NoProfile", "-Command", swapped],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def cmd_exe(cmd: str):
        swapped = cmd.replace("uv run pytest", '"%PROBE_PY%" "%PROBE_SCRIPT%"', 1)
        return subprocess.run(
            f'cmd /c "{swapped}"',
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    runners = []
    if shutil.which("bash"):
        runners.append(("bash", bash))
    if shutil.which("pwsh"):
        runners.append(("pwsh", pwsh))
    if os.name == "nt" and shutil.which("cmd"):
        runners.append(("cmd", cmd_exe))
    return runners


def _received_argv(run, command: str) -> list[str] | None:
    result = run(command)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


@pytest.mark.parametrize("name", SAFE_NAMES)
def test_inert_paths_reach_every_shell_exactly_as_written(tmp_path: Path, name: str) -> None:
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / name).write_text("def test_x():\n    pass\n", encoding="utf-8")
    commands, _plan = _commands_and_plan(project, name)
    file_cmd = [c for c in commands if c.endswith(f"tests/{name} -q")]
    assert file_cmd, commands  # positive control: the bare command IS emitted
    runners = _shell_runners(tmp_path)
    if not runners:
        pytest.skip("no shell binary available")
    checked = 0
    for shell, run in runners:
        control = _received_argv(run, "uv run pytest control.py -q")
        if control != ["control.py", "-q"]:
            continue  # this shell binary cannot run the probe; do not pretend it was checked
        checked += 1
        assert _received_argv(run, file_cmd[0]) == [f"tests/{name}", "-q"], shell
    if checked == 0:
        pytest.skip("no usable shell binary for the execution probe")


@pytest.mark.parametrize("name", UNSAFE_NAMES)
def test_unsafe_paths_never_appear_in_any_command_string(tmp_path: Path, name: str) -> None:
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    commands, plan = _commands_and_plan(project, name)
    for command in commands:
        assert name not in command, command
    # disclosure: the raw path is carried in a separate field, never in a command
    disclosed = [p for step in plan for p in step.get("omitted_unsafe_paths", [])]
    assert f"tests/{name}" in disclosed, plan
    assert any(step.get("omitted_note") == "path requires manual quoting" for step in plan), plan


def test_unsafe_neighbour_suggestion_has_no_command_but_argv_and_raw_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_mod.py").write_text("def test_f():\n    pass\n", encoding="utf-8")
    # positive control: an inert neighbour yields a bare command
    ok = repo_map._suggested_validation_command_for_primary_file(
        str(tmp_path / "src" / "mod.py"), tmp_path
    )
    assert ok is not None and ok["command"] == "pytest tests/test_mod.py"

    (tmp_path / "src" / "my mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_my mod.py").write_text(
        "def test_f():\n    pass\n", encoding="utf-8"
    )
    entry = repo_map._suggested_validation_command_for_primary_file(
        str(tmp_path / "src" / "my mod.py"), tmp_path
    )
    assert entry is not None
    assert "command" not in entry
    assert entry["target_test"] == "tests/test_my mod.py"
    assert entry["argv"] == ["pytest", "tests/test_my mod.py"]
    assert entry["command_omitted"] == "path requires manual quoting"


def test_unsafe_javascript_test_title_falls_back_to_file_level_command(tmp_path: Path) -> None:
    project = tmp_path / "project"
    test_path = project / "tests" / "widget.test.js"
    test_path.parent.mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps({"devDependencies": {"jest": "^29.0.0"}}), encoding="utf-8"
    )
    test_path.write_text(
        "test('$(Write-Output PWN) widget', () => expect(1).toBe(1));\n", encoding="utf-8"
    )
    commands = repo_map._validation_commands_for_tests(
        [str(test_path.resolve())],
        repo_root=project,
        primary_test=str(test_path.resolve()),
        query="widget",
    )
    assert "npx jest tests/widget.test.js" in commands  # file-level step survives
    assert not any("PWN" in c for c in commands), commands
