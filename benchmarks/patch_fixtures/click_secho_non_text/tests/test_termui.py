from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import click  # noqa: E402


def test_secho_bytes_are_not_styled() -> None:
    out = io.BytesIO()
    click.secho(b"test", nl=False, color=True, bg="magenta", file=out)
    assert out.getvalue() == b"test"


def test_secho_non_text_values_are_styled() -> None:
    out = io.BytesIO()
    click.secho(123, nl=False, color=True, bg="magenta", file=out)
    assert out.getvalue() == b"\x1b[45m123\x1b[0m"
