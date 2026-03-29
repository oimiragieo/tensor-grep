from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import click


def test_unstyle_other_ansi() -> None:
    assert click.unstyle("\x1b[?25lx y\x1b[?25h") == "x y"
