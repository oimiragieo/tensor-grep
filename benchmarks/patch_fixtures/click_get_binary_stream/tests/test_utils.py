from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import click


def test_get_binary_stream_returns_binary_stream() -> None:
    stream = click.get_binary_stream("stdout")
    assert isinstance(stream, io.BytesIO)
    assert stream.getvalue() == b"stdout"


def test_get_binary_stream_rejects_unknown_stream() -> None:
    with pytest.raises(TypeError):
        click.get_binary_stream("bogus")  # type: ignore[arg-type]
