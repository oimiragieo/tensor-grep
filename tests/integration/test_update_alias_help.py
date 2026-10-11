from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tensor_grep.cli.runtime_paths import resolve_native_tg_binary


def _get_native_binary() -> str | None:
    try:
        resolve_native_tg_binary.cache_clear()
        native_binary = resolve_native_tg_binary()
    except FileNotFoundError:
        return None
    return str(native_binary) if native_binary is not None else None


def test_native_update_help_uses_update_usage() -> None:
    executable = _get_native_binary()
    if executable is None:
        pytest.skip("native tg binary unavailable")
    result = subprocess.run(
        [executable, "update", "--help"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage: tg update" in result.stdout


def test_native_upgrade_command_still_has_upgrade_usage() -> None:
    executable = _get_native_binary()
    if executable is None:
        pytest.skip("native tg binary unavailable")
    result = subprocess.run(
        [executable, "upgrade", "--help"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage: tg upgrade" in result.stdout


@pytest.mark.parametrize(
    ("command", "help_test"),
    [
        ("update", test_native_update_help_uses_update_usage),
        ("upgrade", test_native_upgrade_command_still_has_upgrade_usage),
    ],
)
def test_native_help_uses_configured_binary(
    command: str,
    help_test: Callable[[], None],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stale_executable = tmp_path / "stale" / "tg.exe"
    stale_executable.parent.mkdir()
    stale_executable.touch()
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(stale_executable))
    resolve_native_tg_binary.cache_clear()
    assert resolve_native_tg_binary() == stale_executable

    executable = tmp_path / "tg.exe"
    executable.touch()
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(executable))
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, f"Usage: tg {command}", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    help_test()

    assert calls == [[str(executable), command, "--help"]]
