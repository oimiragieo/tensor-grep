from __future__ import annotations

import ast
from pathlib import Path

from tensor_grep.cli.bootstrap import _KNOWN_COMMANDS
from tensor_grep.cli.commands import KNOWN_COMMANDS
from tensor_grep.cli.main import app

def test_bootstrap_commands_match_source_of_truth() -> None:
    assert _KNOWN_COMMANDS == set(KNOWN_COMMANDS), "Bootstrap commands must exactly match KNOWN_COMMANDS"

def test_typer_app_commands_match_source_of_truth() -> None:
    typer_commands = set()
    for cmd in app.registered_commands:
        typer_commands.add(cmd.name or cmd.callback.__name__)  # type: ignore
    for group in app.registered_groups:
        typer_commands.add(group.name)  # type: ignore
        
    expected_typer_cmds = {cmd for cmd in KNOWN_COMMANDS if not cmd.startswith("__")}
    assert typer_commands == expected_typer_cmds, "Typer commands must exactly match public KNOWN_COMMANDS"

def test_rust_core_uses_source_of_truth() -> None:
    rust_main = Path(__file__).resolve().parents[2] / "rust_core" / "src" / "main.rs"
    content = rust_main.read_text(encoding="utf-8")
    assert 'include_str!("../../src/tensor_grep/cli/commands.py")' in content, "Rust core must include commands.py as source of truth"
