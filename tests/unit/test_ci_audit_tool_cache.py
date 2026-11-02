"""Check audit-tool cache scope and exercise the actual workflow shell without builds."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/audit.yml"


def _steps():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["audit"]["steps"]


def test_audit_cache_is_binary_only_and_toolchain_specific():
    caches = [step for step in _steps() if step.get("id") == "audit-tools-cache"]
    assert len(caches) == 1, "compiled audit-tool cache missing"
    cache = caches[0]
    assert cache["uses"] == "actions/cache@caa296126883cff596d87d8935842f9db880ef25"
    assert cache["with"] == {
        "path": "${{ runner.temp }}/tg-audit-tools/bin",
        "key": "audit-tools-${{ runner.os }}-${{ runner.arch }}-audit-0.22.2-deny-0.20.2-${{ steps.audit-toolchain.outputs.fingerprint }}-v1",
    }


def _write_script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + body, newline="\n")
    path.chmod(0o755)


def _run(script, env, cwd):
    env = {**os.environ, **env}
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)
    return subprocess.run(
        [shutil.which("bash"), "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _audit_commands():
    return {
        "Run cargo audit": '"$RUNNER_TEMP/tg-audit-tools/bin/cargo-audit" audit',
        "Audit recovery preflight lockfile": '"$RUNNER_TEMP/tg-audit-tools/bin/cargo-audit" audit --file recovery-preflight/Cargo.lock',
        "Run cargo deny": '"$RUNNER_TEMP/tg-audit-tools/bin/cargo-deny" deny check',
        "Check recovery preflight dependencies": '"$RUNNER_TEMP/tg-audit-tools/bin/cargo-deny" deny --locked --manifest-path recovery-preflight/Cargo.toml check',
    }


def test_all_audits_use_exact_verified_tools_and_existing_targets():
    for name, command in _audit_commands().items():
        matching = [step for step in _steps() if step.get("name") == name]
        assert len(matching) == 1
        step = matching[0]
        assert step["run"] == command
        assert step["working-directory"] == "rust_core"
        assert "if" not in step and not step.get("continue-on-error", False)


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="POSIX shell fixture"
)
@pytest.mark.parametrize(
    "hit,broken,version,probe_status,missing,success",
    [
        (False, None, None, 0, False, True),
        (False, "partial", None, 0, False, True),
        (True, None, None, 0, False, True),
        (True, "cargo-audit", "0.0.0", 0, False, False),
        (True, "cargo-deny", "0.0.0", 0, False, False),
        (True, "cargo-audit", "0.22.2", 1, False, False),
        (True, "cargo-deny", "0.20.2", 1, False, False),
        (True, "cargo-audit", None, 0, True, False),
        (True, "cargo-deny", None, 0, True, False),
    ],
)
def test_cold_warm_partial_and_bad_probes(
    tmp_path, hit, broken, version, probe_status, missing, success
):
    runner_temp = tmp_path / "runner temp"
    binaries = runner_temp / "tg-audit-tools/bin"
    stubs = tmp_path / "stubs"
    shadow = tmp_path / "cargo home/bin"
    for tool, expected in (("cargo-audit", "0.22.2"), ("cargo-deny", "0.20.2")):

        def body(reported, status, tool=tool):
            return (
                'if [ "${1:-}" = --version ]; then\n'
                f"  printf '%s\\n' '{tool} {reported}'\n  exit {status}\nfi\n"
                f"printf '%s %s\\n' '{tool}' \"$*\" >> \"$TOOL_CALLS\"\n"
            )

        _write_script(stubs / f"{tool}-template", body(expected, 0))
        _write_script(shadow / tool, "echo shadow-tool-executed >&2\nexit 97\n")
        if hit and not (missing and broken == tool):
            _write_script(
                binaries / tool,
                body(
                    version if broken == tool else expected, probe_status if broken == tool else 0
                ),
            )
        elif broken == "partial":
            _write_script(binaries / tool, "exit 97\n")
    _write_script(
        stubs / "cargo",
        r"""
test "$1" = install
name=$2
printf '%s\n' "$*" >> "$CARGO_CALLS"
shift 2
root=
while [ "$#" -gt 0 ]; do
  if [ "$1" = --root ]; then root=$2; shift 2; else shift; fi
done
test "$root" = "$RUNNER_TEMP/tg-audit-tools"
mkdir -p "$root/bin"
cp "$STUBS/$name-template" "$root/bin/$name"
chmod +x "$root/bin/$name"
""",
    )
    env = {
        "PATH": f"{stubs}{os.pathsep}{shadow}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": runner_temp.as_posix(),
        "CARGO_HOME": shadow.parent.as_posix(),
        "STUBS": stubs.as_posix(),
        "TOOL_CALLS": (tmp_path / "tool-calls").as_posix(),
        "CARGO_CALLS": (tmp_path / "cargo-calls").as_posix(),
    }
    install = next(
        step for step in _steps() if step.get("name") == "Install cargo-audit and cargo-deny"
    )
    script = install["run"].replace(
        "${{ steps.audit-tools-cache.outputs.cache-hit }}", str(hit).lower()
    )
    result = _run(script, env, tmp_path)
    assert (result.returncode == 0) is success, result.stderr
    calls = tmp_path / "cargo-calls"
    assert calls.exists() is (not hit)
    if not hit:
        assert calls.read_text().splitlines() == [
            f"install cargo-audit --version 0.22.2 --locked --force --root {runner_temp.as_posix()}/tg-audit-tools",
            f"install cargo-deny --version 0.20.2 --locked --force --root {runner_temp.as_posix()}/tg-audit-tools",
        ]
    if success:
        for name in _audit_commands():
            actual = next(step for step in _steps() if step.get("name") == name)
            result = _run(actual["run"], env, tmp_path)
            assert result.returncode == 0, result.stderr
        assert (tmp_path / "tool-calls").read_text().splitlines() == [
            "cargo-audit audit",
            "cargo-audit audit --file recovery-preflight/Cargo.lock",
            "cargo-deny deny check",
            "cargo-deny deny --locked --manifest-path recovery-preflight/Cargo.toml check",
        ]
    else:
        assert not (tmp_path / "tool-calls").exists()


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="POSIX shell fixture"
)
@pytest.mark.parametrize("failure", [None, "rustc", "cat", "getconf", "empty"])
def test_fingerprint_includes_compiler_os_and_abi_and_refuses_probe_errors(tmp_path, failure):
    stubs = tmp_path / "stubs"
    values = {
        "rustc": "rustc 1.96.0\nhost: fixture",
        "cat": "ID=ubuntu\nVERSION_ID=24.04",
        "getconf": "glibc 2.39",
    }
    for name, value in values.items():
        if failure == "empty" and name == "rustc":
            value = ""
        _write_script(
            stubs / name, f"printf '%s\\n' '{value}'\nexit {7 if failure == name else 0}\n"
        )
    output = tmp_path / "github-output"
    env = {"PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}", "GITHUB_OUTPUT": output.as_posix()}
    step = next(step for step in _steps() if step.get("id") == "audit-toolchain")
    result = _run(step["run"], env, tmp_path)
    assert (result.returncode == 0) is (failure is None), result.stderr
    if failure is None:
        data = "\n".join(values.values()) + "\n"
        assert output.read_text() == f"fingerprint={hashlib.sha256(data.encode()).hexdigest()}\n"
    else:
        assert not output.exists()
