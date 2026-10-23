from __future__ import annotations

import re
from collections import Counter

import pytest
from click import Command
from click.testing import CliRunner
from typer.main import get_command

from tensor_grep.cli.help_catalog import HELP_FOOTER, HELP_SUMMARY, TopLevelHelpGroup
from tensor_grep.cli.main import app, main_entry

EXPECTED_SECTIONS = (
    "Search and structure:",
    "Navigate:",
    "Agent:",
    "State and evidence:",
    "Operations:",
)


def _command_rows(output: str) -> tuple[str, ...]:
    return tuple(re.findall(r"(?m)^  ([a-z][a-z0-9-]*) {2,}", output))


@pytest.mark.parametrize("width", [80, 120])
def test_RenderRootHelp_SupportedWidth_ShowsCommandsOnceInOrder(width):
    command_group = get_command(app)
    with command_group.make_context("tg", [], resilient_parsing=True) as ctx:
        expected_commands = tuple(
            filter(
                lambda name: not command_group.get_command(ctx, name).hidden,
                command_group.list_commands(ctx),
            )
        )

    result = CliRunner().invoke(command_group, ["--help"], terminal_width=width)
    output = result.output
    rendered_commands = _command_rows(output)

    assert result.exit_code == 0
    assert Counter(rendered_commands) == Counter(expected_commands)
    assert (
        tuple(
            re.findall(
                r"(?m)^(?:Search and structure|Navigate|Agent|State and evidence|Operations):$",
                output,
            )
        )
        == EXPECTED_SECTIONS
    )
    assert "**" not in output and "`" not in output and "\b" not in output
    assert "TG_SIDECAR_PYTHON" not in output
    assert "  tg search -F TODO src" in output.splitlines()
    assert "  tg callers src create_invoice --json" in output.splitlines()
    assert '  tg prepare src "add invoice tax" --json' in output.splitlines()
    assert "  tg mcp" in output.splitlines()
    assert output.index("Examples:") < output.index("Options:")
    assert output.index("Options:") < output.index("Search and structure:")
    assert output.index("Operations:") < output.index(
        "Search supports a validated rg-compatible subset."
    )
    assert max(map(len, output.splitlines())) <= width


def test_RenderRootHelp_NoArguments_ShowsCompleteSurface(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["tg"])

    with pytest.raises(SystemExit) as exited:
        main_entry()
    captured = capsys.readouterr()

    assert exited.value.code == 0
    assert "Usage: tg" in captured.out and captured.err == ""
    assert Counter(_command_rows(captured.out)) == Counter(
        _command_rows(CliRunner().invoke(get_command(app), ["--help"]).output)
    )


def test_RenderRootHelp_RegisteredSearch_ShowsCommandDescription():
    command = get_command(app)

    output = CliRunner().invoke(command, ["--help"], terminal_width=80).output

    assert re.search(r"(?m)^  search\s+Search files for a regex pattern\.$", output)


@pytest.mark.parametrize("width", [80, 120])
def test_RenderRootHelp_LongDescription_StaysOnOneLine(width):
    group = TopLevelHelpGroup(name="tg", help=HELP_SUMMARY, epilog=HELP_FOOTER)
    group.add_command(
        Command("long-description-command", short_help="A registered short help sentence " * 8)
    )

    output = CliRunner().invoke(group, ["--help"], terminal_width=width).output
    lines = output.splitlines()
    rows = tuple(line for line in lines if line.startswith("  long-description-command"))

    assert len(rows) == 1
    assert len(rows[0]) <= width
    assert "…" in rows[0]
    assert lines[lines.index(rows[0]) + 1] == ""


def test_RenderRootHelp_HiddenAndFutureCommands_PreservesVisibility():
    group = TopLevelHelpGroup(name="tg", help=HELP_SUMMARY, epilog=HELP_FOOTER)
    group.add_command(Command("search", help="Search files."))
    group.add_command(Command("hidden-command", help="Hidden command.", hidden=True))
    group.add_command(Command("future-visible", help="Future command."))

    output = CliRunner().invoke(group, ["--help"], terminal_width=80).output

    assert Counter(_command_rows(output)) == Counter(("search", "future-visible"))
    assert output.index("Operations:") < output.index("future-visible")
