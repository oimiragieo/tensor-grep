"""A signed evidence receipt must not carry a plausible-looking version it does not have.

`evidence_receipt._read_project_version_fallback` is documented as mirroring
`cli/main.py::_read_project_version_fallback` "exactly", but main.py was hardened to return the
explicit `0.0.0-unavailable` sentinel (A3 / W1-c) while this copy kept a bare `0.0.0`. That string
lands in a SIGNED receipt's `tool.version` (and in codemap's `tool_version`), where a bare `0.0.0`
reads as a real, very old version.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

from tensor_grep.cli import evidence_receipt


def _break_both_version_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_metadata(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    def _unreadable(path: Path, *args: object, **kwargs: object) -> str:
        raise OSError("pyproject.toml is not readable")

    monkeypatch.setattr(importlib.metadata, "version", _no_metadata)
    monkeypatch.setattr(Path, "read_text", _unreadable)


def test_receipt_version_is_the_shared_unavailable_sentinel_when_no_source_is_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # evidence_receipt cannot import main (layering), so the literal is duplicated; this pins the
    # duplicate to main's constant so the two cannot drift apart again.
    from tensor_grep.cli import main

    _break_both_version_sources(monkeypatch)

    assert evidence_receipt._cli_package_version() == main._VERSION_UNAVAILABLE_SENTINEL
    assert evidence_receipt._read_project_version_fallback() == main._VERSION_UNAVAILABLE_SENTINEL


def test_a_readable_package_version_is_still_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    # CONTROL: the sentinel is only for "no source readable".
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9")

    assert evidence_receipt._cli_package_version() == "9.9.9"
