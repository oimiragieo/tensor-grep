"""H5 audit: the second native-delegation route (`_delegate_to_native_tg_search`,
cli/main.py:3970) had NO subprocess timeout, so a hung native search on the
`--cpu`/`--json` route hung forever (fail-open). Mirror the bootstrap twin's
`TimeoutExpired` -> exit 124 contract (bootstrap.py:1263-1269) and bound the call
with `configured_ripgrep_timeout_seconds()` (subprocess_policy.py:63)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tensor_grep.cli import main as tg_main
from tensor_grep.core.config import SearchConfig


class _TimeoutSubprocess:
    TimeoutExpired = subprocess.TimeoutExpired

    @staticmethod
    def run(*_args, **_kwargs) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd=["tg.exe"], timeout=60)


def test_native_delegation_timeout_returns_124(monkeypatch, capsys) -> None:
    monkeypatch.setattr(tg_main, "subprocess", _TimeoutSubprocess)
    rc = tg_main._delegate_to_native_tg_search(
        Path("tg.exe"),
        pattern="foo",
        paths=["."],
        config=SearchConfig(),
        ndjson=False,
    )
    assert rc == 124
    assert "timeout" in capsys.readouterr().err.lower()


def test_native_delegation_run_is_bounded(monkeypatch) -> None:
    recorded: dict[str, object] = {}

    class _RecordingSubprocess:
        TimeoutExpired = subprocess.TimeoutExpired
        CompletedProcess = subprocess.CompletedProcess

        @staticmethod
        def run(*_args, **_kwargs):
            recorded.update(_kwargs)
            return subprocess.CompletedProcess(args=_args[0], returncode=0)

    monkeypatch.setattr(tg_main, "subprocess", _RecordingSubprocess)
    rc = tg_main._delegate_to_native_tg_search(
        Path("tg.exe"),
        pattern="foo",
        paths=["."],
        config=SearchConfig(),
        ndjson=False,
    )
    assert rc == 0
    assert isinstance(recorded.get("timeout"), float) and recorded["timeout"] > 0


def test_native_delegation_relays_normal_return_code(monkeypatch) -> None:
    class _ResultSubprocess:
        TimeoutExpired = subprocess.TimeoutExpired
        CompletedProcess = subprocess.CompletedProcess

        @staticmethod
        def run(*_args, **_kwargs):
            return subprocess.CompletedProcess(args=_args[0], returncode=1)

    monkeypatch.setattr(tg_main, "subprocess", _ResultSubprocess)
    rc = tg_main._delegate_to_native_tg_search(
        Path("tg.exe"),
        pattern="foo",
        paths=["."],
        config=SearchConfig(),
        ndjson=False,
    )
    assert rc == 1


def test_native_delegation_uses_configured_timeout(monkeypatch) -> None:
    """Pin the KNOB that bounds the call: the timeout must be the value from
    `configured_ripgrep_timeout_seconds()`, not a hardcoded literal."""
    import tensor_grep.cli.subprocess_policy as policy

    monkeypatch.setattr(policy, "configured_ripgrep_timeout_seconds", lambda: 42.0)
    recorded: dict[str, object] = {}

    class _Recorder:
        TimeoutExpired = subprocess.TimeoutExpired
        CompletedProcess = subprocess.CompletedProcess

        @staticmethod
        def run(*_args, **_kwargs):
            recorded.update(_kwargs)
            return subprocess.CompletedProcess(args=_args[0], returncode=0)

    monkeypatch.setattr(tg_main, "subprocess", _Recorder)
    rc = tg_main._delegate_to_native_tg_search(
        Path("tg.exe"),
        pattern="foo",
        paths=["."],
        config=SearchConfig(),
        ndjson=False,
    )
    assert rc == 0
    assert recorded.get("timeout") == 42.0


def test_native_delegation_oserror_returns_2(monkeypatch, capsys) -> None:
    """Fail-closed: if the native binary cannot be SPAWNED (FileNotFoundError /
    PermissionError), do not present a traceback or a fake 0 -- return exit 2
    (untrustworthy) with a stderr note."""

    class _OserrorSubprocess:
        TimeoutExpired = subprocess.TimeoutExpired

        @staticmethod
        def run(*_args, **_kwargs) -> subprocess.CompletedProcess:
            raise FileNotFoundError(2, "No such file or directory", "tg.exe")

    monkeypatch.setattr(tg_main, "subprocess", _OserrorSubprocess)
    rc = tg_main._delegate_to_native_tg_search(
        Path("tg.exe"),
        pattern="foo",
        paths=["."],
        config=SearchConfig(),
        ndjson=False,
    )
    assert rc == 2
    assert "cannot be trusted" in capsys.readouterr().err.lower()
