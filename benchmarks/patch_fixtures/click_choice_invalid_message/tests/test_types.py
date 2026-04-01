from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import click  # noqa: E402


def test_choice_get_invalid_choice_message() -> None:
    choice = click.Choice(["a", "b", "c"])
    message = choice.get_invalid_choice_message("d", ctx=None)
    assert message == "'d' is not one of 'a', 'b', 'c'."
