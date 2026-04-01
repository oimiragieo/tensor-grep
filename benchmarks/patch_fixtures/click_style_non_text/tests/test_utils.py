from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import click  # noqa: E402


def test_style_non_text_is_coerced_to_string() -> None:
    assert click.style(123, fg="red") == "\x1b[31m123\x1b[0m"
