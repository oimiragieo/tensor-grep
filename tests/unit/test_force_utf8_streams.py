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
    out.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace", newline="\n")
    err.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace", newline="\n")


def test_newline_only_reconfigured_when_already_utf8(monkeypatch):
    # task #262: this is NOT a full no-op anymore -- the stream is already UTF-8 so the
    # encoding/errors kwargs are skipped, but newline="\n" must still be applied so a
    # plain print() on this stream does not silently rewrite \n -> \r\n on Windows.
    out = _fake_stream("utf-8")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", _fake_stream("UTF-8"))
    _force_utf8_streams()
    out.reconfigure.assert_called_once_with(newline="\n")


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


def test_falls_back_to_encoding_only_when_stream_reconfigure_rejects_newline_kwarg(monkeypatch):
    """task #262 non-blocking gate finding: calling `reconfigure()` on an already-UTF-8
    stream is NEW behavior from this fix (the pre-fix early-`continue` skipped it entirely).
    A stream whose `reconfigure()` implements only the narrower pre-#262 signature
    (`encoding=`/`errors=`, no `newline=` parameter at all) now gets called for the first
    time and raises a genuine `TypeError` on the unexpected kwarg -- this must not crash
    startup, and the encoding fix should still land via a narrower retry.
    """

    class _NarrowReconfigureStream:
        encoding = "cp1252"

        def __init__(self):
            self.calls: list[dict[str, str]] = []

        def reconfigure(self, *, encoding=None, errors=None):
            self.calls.append({"encoding": encoding, "errors": errors})

    stream = _NarrowReconfigureStream()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", _fake_stream("utf-8"))

    _force_utf8_streams()  # must not raise TypeError and crash startup

    # The first (newline-included) call must have been attempted and rejected by the real
    # signature, then retried WITHOUT newline so the encoding fix still lands.
    assert stream.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_falls_back_gracefully_when_narrow_reconfigure_also_fails(monkeypatch):
    """The narrower retry itself can still fail (e.g. the same buffered-output ValueError as
    test_survives_reconfigure_error) -- must not escape and crash startup either."""

    class _NarrowReconfigureStreamThatAlsoFails:
        encoding = "cp1252"

        def reconfigure(self, *, encoding=None, errors=None):
            raise ValueError("stream has buffered output")

    monkeypatch.setattr(sys, "stdout", _NarrowReconfigureStreamThatAlsoFails())
    monkeypatch.setattr(sys, "stderr", _fake_stream("utf-8"))

    _force_utf8_streams()  # must not raise
