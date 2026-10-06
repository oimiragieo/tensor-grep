"""Exercise the native CI prerequisite without compiling or downloading ripgrep."""

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _script():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    step = workflow.split("      - name: Ensure ripgrep is available\n", 1)[1].split(
        "\n      - name:", 1
    )[0]
    assert "timeout-minutes: 6" in step
    return textwrap.dedent(step.split("        run: |\n", 1)[1]).strip()


def _run(tmp_path, script, *, install_provides_rg=True):
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
    assert bash, "Bash is required to verify the CI script"
    preamble = r"""
installed=0
command() {
  if [ "$1" = "-v" ] && [ "$2" = "rg" ]; then
    [ "$installed" = 1 ]
  else
    builtin command "$@"
  fi
}
cargo() {
  printf 'CARGO:%s\n' "$*"
  installed="$INSTALL_PROVIDES_RG"
}
cygpath() {
  case "$1" in
    -u) printf '/d/Runner Temp/cargo-rg\n' ;;
    -m) printf 'D:/Runner Temp/cargo-rg/bin\n' ;;
    *) return 2 ;;
  esac
}
rg() { printf 'RG_AVAILABLE\n'; }
"""
    env = {
        **os.environ,
        "RUNNER_OS": "Windows",
        "RUNNER_TEMP": "D:/Runner Temp",
        "GITHUB_PATH": str(tmp_path / "github-path"),
        "INSTALL_PROVIDES_RG": str(int(install_provides_rg)),
    }
    return subprocess.run(
        [bash, "--noprofile", "--norc"],
        input=preamble + script,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )


def _assert_installs(result, tmp_path):
    assert result.returncode == 0, result.stderr
    assert (
        "CARGO:install ripgrep --version 14.1.1 --locked --root /d/Runner Temp/cargo-rg"
        in result.stdout
    )
    assert "RG_AVAILABLE" in result.stdout
    assert (tmp_path / "github-path").read_text(
        encoding="utf-8"
    ).strip() == "D:/Runner Temp/cargo-rg/bin"


def test_windows_native_ci_installs_pinned_ripgrep_with_portable_paths(tmp_path):
    _assert_installs(_run(tmp_path, _script()), tmp_path)


def test_windows_native_ci_fails_if_installer_does_not_provide_executable(tmp_path):
    result = _run(tmp_path, _script(), install_provides_rg=False)
    assert result.returncode == 1
    assert "ripgrep unavailable after installation" in result.stderr


def test_windows_install_bypass_mutation_is_detected(tmp_path):
    mutant = _script().replace(
        "if ! command -v rg", 'if [ "$RUNNER_OS" != "Windows" ] && ! command -v rg'
    )
    with pytest.raises(AssertionError):
        _assert_installs(_run(tmp_path, mutant), tmp_path)


def test_windows_postcondition_bypass_mutation_is_detected(tmp_path):
    script = _script()
    position = script.rindex("if ! command -v rg")
    mutant = script[:position] + script[position:].replace(
        "if ! command -v rg", 'if [ "$RUNNER_OS" != "Windows" ] && ! command -v rg', 1
    )
    result = _run(tmp_path, mutant, install_provides_rg=False)
    assert result.returncode == 0
    assert "ripgrep unavailable after installation" not in result.stderr
    # The non-mutated control above requires exit 1 for exactly this condition.


def test_ripgrep_setup_precedes_both_native_smoke_steps():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    prerequisite = workflow.index("- name: Ensure ripgrep is available")
    assert prerequisite < workflow.index("- name: Smoke-test native release binary (Unix)")
    assert prerequisite < workflow.index("- name: Smoke-test native release binary (Windows)")
