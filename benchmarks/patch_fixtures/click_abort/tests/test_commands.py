from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import click  # noqa: E402


def test_context_abort_raises_abort() -> None:
    with pytest.raises(click.Abort):
        click.Context().abort()
