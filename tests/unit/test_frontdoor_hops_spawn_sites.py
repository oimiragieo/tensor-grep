"""Every Python spawn of the native ``tg`` carries ``TG_FRONTDOOR_HOPS`` (P0 loop backstop).

Companion to ``test_frontdoor_delegation_depth.py`` (the observed ``search -s --json`` loop):
this file pins the hop contract itself (``frontdoor_hops``), the remaining spawn sites that hand
a request to the native door (calibrate, worker, MCP rewrite/index, agent GPU evidence, doctor
GPU probe), and the bootstrap spawn-failure exit code (a failed spawn is exit 2, never a bare
traceback + exit 1, which a caller reads as "no match").

Nothing here spawns a real ``tg``: ``subprocess.run`` / ``run_subprocess`` are replaced by
recorders that keep ONLY the marker env keys (never the whole env -- it holds secrets).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import bootstrap
from tensor_grep.cli import main as cli_main
from tensor_grep.cli.frontdoor_hops import (
    FRONTDOOR_HOPS_ENV,
    MAX_FRONTDOOR_HOPS,
    FrontdoorHopLimitError,
    child_env_or_refusal,
    next_hop_env,
)

_MARKERS = (FRONTDOOR_HOPS_ENV, "TG_REEXEC_GUARD")


@pytest.fixture(autouse=True)
def _clean_hops(monkeypatch: pytest.MonkeyPatch) -> None:
    # setenv-then-delenv registers "absent" as the value to restore (a fix that stamps
    # os.environ cannot leak into later tests).
    monkeypatch.setenv(FRONTDOOR_HOPS_ENV, "")
    monkeypatch.delenv(FRONTDOOR_HOPS_ENV)
    monkeypatch.delenv("TG_REEXEC_GUARD", raising=False)


def _markers(env: Any) -> dict[str, str]:
    return {k: str(env[k]) for k in _MARKERS if env is not None and k in env}


# ---------------------------------------------------------------------------
# The hop contract
# ---------------------------------------------------------------------------


def test_next_hop_env_counts_from_absent_to_the_cap() -> None:
    assert next_hop_env({})[FRONTDOOR_HOPS_ENV] == "1"
    assert next_hop_env({FRONTDOOR_HOPS_ENV: "0"})[FRONTDOOR_HOPS_ENV] == "1"
    assert next_hop_env({FRONTDOOR_HOPS_ENV: "1"})[FRONTDOOR_HOPS_ENV] == "2"
    last_ok = next_hop_env({FRONTDOOR_HOPS_ENV: str(MAX_FRONTDOOR_HOPS - 1)})
    assert last_ok[FRONTDOOR_HOPS_ENV] == str(MAX_FRONTDOOR_HOPS)
    with pytest.raises(FrontdoorHopLimitError) as excinfo:
        next_hop_env({FRONTDOOR_HOPS_ENV: str(MAX_FRONTDOOR_HOPS)})
    assert FRONTDOOR_HOPS_ENV in str(excinfo.value)
    assert excinfo.value.exit_code == 2


#: The shared grammar (rust_core/src/frontdoor_hops.rs): 1-9 ASCII digits and nothing else.
_MALFORMED = [
    "abc",
    "-1",
    "+1",
    "1.5",
    "0x1",
    "0_0",
    " 1",
    "1 ",
    "",
    chr(0x660),  # ARABIC-INDIC DIGIT ZERO: int() would normalise it to 0
    chr(0x663),
    "abc" + chr(0x2603),  # snowman: printable non-ASCII
    "1" * 5000,  # past int()'s 4300-digit limit, which would raise ValueError
]


@pytest.mark.parametrize("bad", _MALFORMED)
def test_next_hop_env_fails_closed_on_a_malformed_value(bad: str) -> None:
    # A malformed marker read as 0 would let a corrupted chain restart its count and loop again.
    with pytest.raises(FrontdoorHopLimitError) as excinfo:
        next_hop_env({FRONTDOOR_HOPS_ENV: bad})
    message = str(excinfo.value)
    assert FRONTDOOR_HOPS_ENV in message
    assert message.isascii(), message  # no raw printable Unicode on stderr


@pytest.mark.parametrize("bad", [chr(0x2603) + "abc", "abc" + chr(0x2603)])
def test_child_env_or_refusal_stderr_is_ascii(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    monkeypatch.setenv(FRONTDOOR_HOPS_ENV, bad)
    env, code = child_env_or_refusal()
    assert env is None and code == 2
    assert capsys.readouterr().err.isascii()


def test_next_hop_env_preserves_the_rest_of_the_environment() -> None:
    child = next_hop_env({"PATH": "x", "TG_SIDECAR_PYTHON": "py"})
    assert child["PATH"] == "x"
    assert child["TG_SIDECAR_PYTHON"] == "py"


def test_child_env_or_refusal_writes_the_refusal_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(FRONTDOOR_HOPS_ENV, "99")
    env, code = child_env_or_refusal()
    assert env is None
    assert code == 2
    assert FRONTDOOR_HOPS_ENV in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Remaining spawn sites
# ---------------------------------------------------------------------------


class _RunRecorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, module: Any, attr: str) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []

        def fake(argv: Any, *args: Any, **kwargs: Any) -> Any:
            self.calls.append(([str(a) for a in argv], _markers(kwargs.get("env"))))
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(module, attr, fake)


@pytest.fixture
def fake_native(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "fake-native-tg"
    path.write_text("not a real binary", encoding="utf-8")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: path)
    return path


@pytest.mark.parametrize("command", ["calibrate", "worker"])
def test_calibrate_and_worker_stamp_the_next_hop(
    monkeypatch: pytest.MonkeyPatch, fake_native: Path, command: str
) -> None:
    recorder = _RunRecorder(monkeypatch, cli_main.subprocess, "run")
    result = CliRunner().invoke(cli_main.app, [command])
    assert result.exit_code == 0, result.output
    assert len(recorder.calls) == 1
    argv, markers = recorder.calls[0]
    assert argv[:2] == [str(fake_native), command]
    assert markers.get(FRONTDOOR_HOPS_ENV) == "1"


@pytest.mark.parametrize("command", ["calibrate", "worker"])
def test_calibrate_and_worker_refuse_at_the_hop_cap(
    monkeypatch: pytest.MonkeyPatch, fake_native: Path, command: str
) -> None:
    recorder = _RunRecorder(monkeypatch, cli_main.subprocess, "run")
    monkeypatch.setenv(FRONTDOOR_HOPS_ENV, "99")
    result = CliRunner().invoke(cli_main.app, [command])
    assert recorder.calls == []
    assert result.exit_code == 2, result.output


def test_mcp_rewrite_subprocess_stamps_and_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    importlib.import_module("tensor_grep.cli.mcp_server")  # mcp_rewrite_tools needs it loaded first
    mcp_rewrite_tools = importlib.import_module("tensor_grep.cli.mcp_rewrite_tools")
    subprocess_policy = importlib.import_module("tensor_grep.cli.subprocess_policy")

    recorder = _RunRecorder(monkeypatch, subprocess_policy, "run_subprocess")
    mcp_rewrite_tools._run_rewrite_subprocess(["tg-fake", "run"])
    assert recorder.calls[0][1].get(FRONTDOOR_HOPS_ENV) == "1"

    recorder.calls.clear()
    monkeypatch.setenv(FRONTDOOR_HOPS_ENV, "99")
    with pytest.raises(OSError, match=FRONTDOOR_HOPS_ENV):  # callers already map OSError
        mcp_rewrite_tools._run_rewrite_subprocess(["tg-fake", "run"])
    assert recorder.calls == []


def test_agent_gpu_evidence_command_stamps_and_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    from tensor_grep.cli import agent_capsule

    recorder = _RunRecorder(monkeypatch, agent_capsule.subprocess, "run")
    agent_capsule._run_agent_gpu_json_command(["tg-fake", "x"], timeout_s=1.0)
    assert recorder.calls[0][1].get(FRONTDOOR_HOPS_ENV) == "1"

    recorder.calls.clear()
    monkeypatch.setenv(FRONTDOOR_HOPS_ENV, "99")
    result = agent_capsule._run_agent_gpu_json_command(["tg-fake", "x"], timeout_s=1.0)
    assert recorder.calls == []
    assert result["status"] == "failed"
    assert FRONTDOOR_HOPS_ENV in result["reason"]


# ---------------------------------------------------------------------------
# Bootstrap native-spawn failure (separate bug): exit 2 + message, not a traceback + exit 1
# ---------------------------------------------------------------------------


def test_bootstrap_spawn_failure_is_exit_2_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(argv: list[str], env: dict[str, str] | None = None) -> Any:
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    monkeypatch.setattr(bootstrap, "_popen_child", _boom)
    rc = bootstrap._run_native_tg_search("definitely-not-a-binary", ["hello", "a.txt"])
    assert rc == 2  # exit 1 would read as "no match"
    err = capsys.readouterr().err
    assert "could not start" in err
    assert "output cannot be trusted" in err


# ---------------------------------------------------------------------------
# Info actions and version probes (the sites the first cut of #1208 missed)
# ---------------------------------------------------------------------------


def test_type_list_stamps_and_refuses_at_the_cap(
    monkeypatch: pytest.MonkeyPatch, fake_native: Path
) -> None:
    recorder = _RunRecorder(monkeypatch, cli_main.subprocess, "run")
    result = CliRunner().invoke(cli_main.app, ["search", "--type-list"])
    assert result.exit_code == 0, result.output
    assert recorder.calls and recorder.calls[0][1].get(FRONTDOOR_HOPS_ENV) == "1"

    recorder.calls.clear()
    monkeypatch.setenv(FRONTDOOR_HOPS_ENV, "4")
    result = CliRunner().invoke(cli_main.app, ["search", "--type-list"])
    assert recorder.calls == []
    assert result.exit_code == 2, result.output


def test_pcre2_version_refuses_at_the_cap(
    monkeypatch: pytest.MonkeyPatch, fake_native: Path
) -> None:
    import sys

    recorder = _RunRecorder(monkeypatch, cli_main.subprocess, "run")
    monkeypatch.setenv(FRONTDOOR_HOPS_ENV, "4")
    monkeypatch.setattr(sys, "argv", ["tg", "--pcre2-version"])
    with pytest.raises(SystemExit) as excinfo:
        cli_main.main_entry()
    assert excinfo.value.code == 2
    assert recorder.calls == []


def test_version_probes_stamp_and_do_not_spawn_at_the_cap(
    monkeypatch: pytest.MonkeyPatch, fake_native: Path
) -> None:
    from tensor_grep.cli import doctor_report, runtime_paths

    recorder = _RunRecorder(monkeypatch, subprocess, "run")
    runtime_paths._native_tg_version(fake_native)
    doctor_report._doctor_rust_binary_version(fake_native)
    doctor_report._doctor_tg_candidate_version(fake_native)
    assert len(recorder.calls) == 3
    assert all(markers.get(FRONTDOOR_HOPS_ENV) == "1" for _argv, markers in recorder.calls)

    recorder.calls.clear()
    monkeypatch.setenv(FRONTDOOR_HOPS_ENV, "4")
    assert runtime_paths._native_tg_version(fake_native) is None
    assert doctor_report._doctor_rust_binary_version(fake_native) is None
    assert doctor_report._doctor_tg_candidate_version(fake_native) is None
    assert recorder.calls == []


# ---------------------------------------------------------------------------
# Layer C must not change main's other exit codes: timeout stays 124, spawn failure is 2
# ---------------------------------------------------------------------------


def test_timeout_is_124_and_spawn_failure_is_2_and_the_stamp_does_not_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    from unittest.mock import MagicMock

    class _Proc:
        pid = 1
        returncode = None

        def poll(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("tg", timeout or 0)

    monkeypatch.setattr(bootstrap, "_popen_child", lambda argv, env=None: _Proc())
    monkeypatch.setattr(bootstrap, "_terminate_child", MagicMock())
    monkeypatch.setenv("TG_RG_TIMEOUT_SECONDS", "0.05")
    assert bootstrap._run_native_tg_search("tg", ["ERROR", "."]) == 124
    assert bootstrap._run_native_tg_command("tg", ["search", "ERROR"]) == 124

    def _boom(argv: list[str], env: dict[str, str] | None = None) -> Any:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(bootstrap, "_popen_child", _boom)
    assert bootstrap._run_native_tg_search("tg", ["ERROR", "."]) == 2
    assert bootstrap._run_native_tg_command("tg", ["search", "ERROR"]) == 2

    # Four calls above stamped four times; none of it may leak into this process.
    assert FRONTDOOR_HOPS_ENV not in os.environ


# ---------------------------------------------------------------------------
# Concurrency: the hop stamp is per-child, never a process-global write
# ---------------------------------------------------------------------------


def test_concurrent_bootstrap_spawns_each_see_exactly_parent_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import threading

    monkeypatch.setenv(FRONTDOOR_HOPS_ENV, "1")
    barrier = threading.Barrier(2, timeout=10)  # both spawns are in flight at once
    seen: list[str | None] = []
    seen_lock = threading.Lock()

    class _Proc:
        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_popen_child(argv: list[str], env: dict[str, str] | None = None) -> Any:
        barrier.wait()
        effective = os.environ if env is None else env
        with seen_lock:
            seen.append(effective.get(FRONTDOOR_HOPS_ENV))
        barrier.wait()
        return _Proc()

    monkeypatch.setattr(bootstrap, "_popen_child", fake_popen_child)
    results: list[int] = []

    def worker(fn: Any, *args: Any) -> None:
        results.append(fn(*args))

    threads = [
        threading.Thread(target=worker, args=(bootstrap._run_native_tg_search, "tg", ["a", "."])),
        threading.Thread(target=worker, args=(bootstrap._run_native_tg_command, "tg", ["run"])),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert results == [0, 0]
    assert seen == ["2", "2"], seen
    assert os.environ[FRONTDOOR_HOPS_ENV] == "1"  # the parent is untouched


# ---------------------------------------------------------------------------
# Spawn-failure message is ASCII even for a Unicode executable name / exception text
# ---------------------------------------------------------------------------


def test_bootstrap_spawn_failure_message_is_ascii(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snowman = chr(0x2603)

    def _boom(argv: list[str], env: dict[str, str] | None = None) -> Any:
        raise FileNotFoundError(2, "no such file " + snowman, argv[0])

    monkeypatch.setattr(bootstrap, "_popen_child", _boom)
    rc = bootstrap._run_native_tg_search("missing-" + snowman + ".exe", ["hello", "a.txt"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.isascii(), err
    assert "could not start" in err
