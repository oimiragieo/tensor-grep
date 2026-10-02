"""The bootstrap front door must not print a plausible-looking version when it has none.

`bootstrap._read_project_version_fallback` returned a bare "0.0.0" when package metadata AND the
source pyproject were both unreadable, while `cli/main.py` uses the explicit
`0.0.0-unavailable` sentinel for the same situation. The bare value reads as a real (very old)
version in the `--version` banner; the explicit one says the version is unknown.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

from tensor_grep.cli import bootstrap


def _break_both_version_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_metadata(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    def _unreadable(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("pyproject.toml is not readable")

    monkeypatch.setattr(importlib.metadata, "version", _no_metadata)
    monkeypatch.setattr(Path, "read_text", _unreadable)


def test_banner_says_unavailable_when_no_version_source_is_readable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _break_both_version_sources(monkeypatch)

    bootstrap._print_version()

    assert capsys.readouterr().out.startswith("tensor-grep 0.0.0-unavailable")


def test_the_two_front_doors_share_one_unavailable_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # bootstrap cannot import main (it must stay light), so the literal is duplicated; this pins
    # the duplicate to main's constant so the two cannot drift apart.
    from tensor_grep.cli import main

    _break_both_version_sources(monkeypatch)

    assert bootstrap._read_project_version_fallback() == main._VERSION_UNAVAILABLE_SENTINEL


def test_a_readable_project_version_is_still_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # CONTROL: the sentinel is ONLY for "no source readable"; a real version is untouched.
    def _no_metadata(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _no_metadata)

    bootstrap._print_version()

    out = capsys.readouterr().out
    assert out.startswith("tensor-grep ")
    assert "unavailable" not in out.splitlines()[0]
