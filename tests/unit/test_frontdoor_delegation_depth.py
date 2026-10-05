"""RED tests: the native <-> Python front-door mutual-delegation loop (P0, 2026-10-05).

Observed: ``tg.exe search -s --json -- hello a.txt`` -> ``python -m tensor_grep search ...`` ->
``tg.exe search ...`` -> ... 842 processes in ~60 s on a shared server.

Root cause (both halves must hold for the loop):
  * native: ``-s`` / ``-N`` are in ``SEARCH_PYTHON_PASSTHROUGH_FLAGS``
    (rust_core/src/search_flag_registry.rs), so ``search_format_python_passthrough_args``
    (rust_core/src/main.rs) re-execs ``python -m tensor_grep search ...`` and stamps
    ``TG_REEXEC_GUARD=1`` (python_sidecar.rs ``configure_python_child_environment``);
  * python: the bootstrap honours ``TG_REEXEC_GUARD`` but falls through to the full CLI, and
    ``search_command`` (cli/main.py) re-resolves the native binary and calls
    ``_delegate_to_native_tg_search`` WITHOUT checking the guard. ``_build_native_tg_search_command``
    forwards ``-s`` (case_sensitive) and ``-N`` (explicit line number) -- exactly the flags the
    native door routes back to Python.

No test here spawns a real ``tg``: the native binary is a fake path, and every spawn primitive
the delegation could use (``subprocess.run`` / ``subprocess.Popen``) is replaced by a recorder
that only intercepts argv whose ``argv[0]`` is the fake native binary.

Contract proposed by the fix (named here so the tests can pin it):
  * ``TG_FRONTDOOR_HOPS`` -- count of cross-door hops already taken (absent == 0). Every door
    passes ``hops + 1`` to the child it spawns for the OTHER door.
  * A door that would forward while ``hops >= cap`` (cap is small, <= 8) refuses with exit 2 and
    a stderr message naming ``TG_FRONTDOOR_HOPS``. The tests use 99, so the exact cap is free.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import bootstrap
from tensor_grep.cli import main as cli_main

HOPS_ENV = "TG_FRONTDOOR_HOPS"
_OBSERVED_ARGV = ["search", "-s", "--json", "--", "hello", "a.txt"]
_MARKER_KEYS = (HOPS_ENV, "TG_REEXEC_GUARD", "TG_NATIVE_TG_BINARY")


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A cwd with a.txt, a FAKE native binary, and no inherited front-door env."""
    for key in (
        "TG_REEXEC_GUARD",
        HOPS_ENV,
        "TG_NATIVE_TG_BINARY",
        "TG_MCP_TG_BINARY",
        "TG_SIDECAR_PYTHON",
        "TG_RUST_FIRST_SEARCH",
        "TG_FORCE_PYTHON",
    ):
        # setenv-then-delenv registers "absent" as the value to restore, so a fix that stamps
        # the hop count into THIS process's os.environ cannot leak it into later tests.
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("hello world\nHello again\n", encoding="utf-8")
    fake_native = tmp_path / (
        "fake-native-tg.exe" if sys.platform.startswith("win") else "fake-native-tg"
    )
    fake_native.write_text("not a real binary", encoding="utf-8")
    monkeypatch.setattr(cli_main, "resolve_native_tg_binary", lambda: fake_native)
    monkeypatch.setattr(bootstrap, "resolve_native_tg_binary", lambda: fake_native)
    return fake_native


class _SpawnRecorder:
    """Intercepts subprocess.run / subprocess.Popen ONLY for the fake native binary.

    Records the argv and the EFFECTIVE child environment (explicit ``env=`` or, when omitted,
    the parent's ``os.environ`` at spawn time -- so a fix that mutates os.environ is seen too).
    Any other spawn (rg, git, ...) goes to the real primitive untouched.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, native: Path) -> None:
        self.native = str(native)
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        real_run = subprocess.run
        real_popen = subprocess.Popen

        def _is_native(argv: Any) -> bool:
            return isinstance(argv, (list, tuple)) and bool(argv) and str(argv[0]) == self.native

        def _record(argv: Any, kwargs: dict[str, Any]) -> None:
            env = kwargs.get("env")
            effective = env if env is not None else os.environ
            # Keep ONLY the front-door markers: a full env copy would dump every secret on the
            # box (API keys, session tokens) into the assertion message and the CI log.
            markers = {k: str(effective[k]) for k in _MARKER_KEYS if k in effective}
            self.calls.append(([str(a) for a in argv], markers))

        def fake_run(argv: Any, *args: Any, **kwargs: Any) -> Any:
            if not _is_native(argv):
                return real_run(argv, *args, **kwargs)
            _record(argv, kwargs)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        def fake_popen(argv: Any, *args: Any, **kwargs: Any) -> Any:
            if not _is_native(argv):
                return real_popen(argv, *args, **kwargs)
            _record(argv, kwargs)
            proc = MagicMock(spec=real_popen)  # NOT subprocess.Popen: that name is patched
            proc.pid = 424242
            proc.returncode = 0
            proc.poll.return_value = 0
            proc.wait.return_value = 0
            return proc

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(subprocess, "Popen", fake_popen)


def _combined_output(result: Any) -> str:
    text = result.output or ""
    try:
        text += result.stderr or ""
    except ValueError:  # older click: stderr not captured separately (already in output)
        pass
    return text


# ---------------------------------------------------------------------------
# 1. The missing half of the C3 guard: the FULL CLI must honour TG_REEXEC_GUARD too.
# ---------------------------------------------------------------------------


def test_full_cli_search_does_not_delegate_back_to_native_under_reexec_guard(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    """RED on main: cli/main.py search_command delegates to native even though the native door
    spawned this process (TG_REEXEC_GUARD=1). That is the second edge of the loop; the bootstrap
    guard alone only diverts to this very path."""
    delegated: list[dict[str, Any]] = []

    def _fake_delegate(native_binary: Path, **kwargs: Any) -> int:
        delegated.append({"native_binary": native_binary, **kwargs})
        return 0

    monkeypatch.setattr(cli_main, "_delegate_to_native_tg_search", _fake_delegate)
    monkeypatch.setenv("TG_REEXEC_GUARD", "1")

    result = CliRunner().invoke(cli_main.app, list(_OBSERVED_ARGV))

    assert delegated == [], (
        "spawned by the native door (TG_REEXEC_GUARD=1) yet delegated search BACK to native: "
        "this is the native<->python ping-pong"
    )
    assert result.exit_code in (0, 1), result.output


def test_full_cli_search_delegates_to_native_without_the_guard(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    """Positive control (GREEN on main): without the guard the same argv DOES delegate, so the
    test above can only pass because the guard is honoured, not because delegation is dead."""
    delegated: list[Path] = []

    def _fake_delegate(native_binary: Path, **kwargs: Any) -> int:
        delegated.append(native_binary)
        return 0

    monkeypatch.setattr(cli_main, "_delegate_to_native_tg_search", _fake_delegate)
    result = CliRunner().invoke(cli_main.app, list(_OBSERVED_ARGV))
    assert delegated == [isolated], result.output


# ---------------------------------------------------------------------------
# 2. The generic backstop: a hop counter both doors stamp and both doors check.
# ---------------------------------------------------------------------------


def test_native_delegation_stamps_incremented_hop_count(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    """RED on main: `_delegate_to_native_tg_search` inherits the env unchanged, so the native
    child cannot tell how deep the cross-door chain already is."""
    from tensor_grep.core.config import SearchConfig

    recorder = _SpawnRecorder(monkeypatch, isolated)
    config = SearchConfig(case_sensitive=True, json_mode=True)

    for start, expected in ((None, "1"), ("2", "3")):
        recorder.calls.clear()
        if start is None:
            monkeypatch.delenv(HOPS_ENV, raising=False)
        else:
            monkeypatch.setenv(HOPS_ENV, start)
        rc = cli_main._delegate_to_native_tg_search(
            isolated, pattern="hello", paths=["a.txt"], config=config, ndjson=False
        )
        assert rc == 0
        assert len(recorder.calls) == 1
        argv, env = recorder.calls[0]
        assert argv[1:] == ["search", "-s", "--json", "--", "hello", "a.txt"]  # observed shape
        assert env.get(HOPS_ENV) == expected, (start, env.get(HOPS_ENV))


def test_full_cli_refuses_to_forward_at_the_hop_cap(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    """RED on main: at/over the cap the Python door still spawns native (exit 0 from the fake).
    It must instead refuse with exit 2 and a message naming the marker -- never loop."""
    recorder = _SpawnRecorder(monkeypatch, isolated)
    monkeypatch.setenv(HOPS_ENV, "99")

    result = CliRunner().invoke(cli_main.app, list(_OBSERVED_ARGV))

    assert recorder.calls == [], "forwarded to native at the hop cap"
    assert result.exit_code == 2, result.output
    assert HOPS_ENV in _combined_output(result), result.output


def test_bootstrap_refuses_to_forward_at_the_hop_cap(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    """RED on main: the bootstrap fast path (`_run_native_tg_search` -> `_popen_child`) forwards
    the observed argv to native regardless of depth. Whatever route the fix takes (refuse in the
    bootstrap, or divert to the full CLI which refuses), the observable must be: no native
    spawn, exit 2."""
    recorder = _SpawnRecorder(monkeypatch, isolated)
    monkeypatch.setenv(HOPS_ENV, "99")
    monkeypatch.setattr(sys, "argv", ["tg", *_OBSERVED_ARGV])

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()

    assert recorder.calls == [], f"bootstrap forwarded to native at the hop cap: {recorder.calls}"
    assert excinfo.value.code == 2


def test_bootstrap_stamps_incremented_hop_count(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    """RED on main: the bootstrap's native spawn passes no hop count."""
    recorder = _SpawnRecorder(monkeypatch, isolated)
    monkeypatch.setattr(sys, "argv", ["tg", *_OBSERVED_ARGV])

    with pytest.raises(SystemExit):
        bootstrap.main_entry()

    assert len(recorder.calls) == 1, recorder.calls  # control: this route DOES reach native
    _argv, env = recorder.calls[0]
    assert env.get(HOPS_ENV) == "1"
