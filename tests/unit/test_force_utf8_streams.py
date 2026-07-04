"""Q7 root fix: main_entry forces UTF-8 stdout/stderr so non-ASCII CLI output never crashes on a
cp1252 Windows console (the #346/#42 typer.echo crash class). One reconfigure covers every command."""

import sys
from unittest.mock import MagicMock

from tensor_grep.cli.bootstrap import _force_utf8_streams


def _fake_stream(encoding: str, *, raises: bool = False) -> MagicMock:
    stream = MagicMock()
    stream.encoding = encoding
    if raises:
        stream.reconfigure.side_effect = ValueError("stream has buffered output")
    return stream


def test_reconfigures_a_cp1252_stream(monkeypatch):
    out, err = _fake_stream("cp1252"), _fake_stream("cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    _force_utf8_streams()
    out.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
    err.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


def test_noop_when_already_utf8(monkeypatch):
    out = _fake_stream("utf-8")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", _fake_stream("UTF-8"))
    _force_utf8_streams()
    out.reconfigure.assert_not_called()


def test_survives_reconfigure_error(monkeypatch):
    monkeypatch.setattr(sys, "stdout", _fake_stream("cp1252", raises=True))
    monkeypatch.setattr(sys, "stderr", _fake_stream("cp1252", raises=True))
    _force_utf8_streams()  # must not propagate — startup never crashes on this


def test_survives_stream_without_reconfigure(monkeypatch):
    class _Bare:
        encoding = "cp1252"  # no reconfigure attribute at all

    monkeypatch.setattr(sys, "stdout", _Bare())
    monkeypatch.setattr(sys, "stderr", _Bare())
    _force_utf8_streams()  # must not raise
