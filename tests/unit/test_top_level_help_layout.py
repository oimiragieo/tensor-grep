from __future__ import annotations

import re

import pytest
from click import Command
from click.testing import CliRunner
from typer.main import get_command

from tensor_grep.cli.help_catalog import (
    CORE_WORKFLOW_COMMANDS,
    HELP_FOOTER,
    HELP_SUMMARY,
    TopLevelHelpGroup,
)
from tensor_grep.cli.main import app, main_entry

EXPECTED_PUBLIC_COMMANDS = frozenset(
    """
    agent ast-info audit audit-diff audit-history audit-verify blast-radius blast-radius-plan
    blast-radius-render calibrate callers checkpoint classify codemap context context-render
    coverage defs devices diff-impact docs-coverage doctor dogfood edit-plan evidence file-api
    find freshness impact importers imports install install-dense inventory ledger lsp lsp-setup
    map mcp new orient prepare refs repair-env repair-launcher review-bundle route-test rulesets
    run scan search session source sql test uninstall update upgrade
""".split()
)

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
def test_RenderRootHelp_SupportedWidth_ShowsCoreWorkflowConcise(width):
    command_group = get_command(app)
    result = CliRunner().invoke(command_group, ["--help"], terminal_width=width)
    output = result.output
    rendered_commands = _command_rows(output)

    assert result.exit_code == 0
    assert len(output.splitlines()) <= 45
    assert rendered_commands == CORE_WORKFLOW_COMMANDS
    assert "--help-all" in output
    assert "Core workflow:" in output
    assert "**" not in output and "`" not in output and "\b" not in output
    assert "TG_SIDECAR_PYTHON" not in output
    assert "  tg search -F TODO src" in output.splitlines()
    assert "  tg callers src create_invoice --json" in output.splitlines()
    assert '  tg prepare src "add invoice tax" --json' in output.splitlines()
    assert "  tg mcp" in output.splitlines()
    assert max(map(len, output.splitlines())) <= width


@pytest.mark.parametrize("width", [80, 120])
def test_RenderRootHelpAll_SupportedWidth_ShowsCommandsOnceInOrder(width):
    command_group = get_command(app)
    result = CliRunner().invoke(command_group, ["--help-all"], terminal_width=width)
    output = result.output
    rendered_commands = _command_rows(output)

    assert result.exit_code == 0
    assert set(rendered_commands) == EXPECTED_PUBLIC_COMMANDS
    assert len(rendered_commands) == 58
    assert rendered_commands[0] == "search"
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
    assert output.index("Examples:") < output.index("Options:")
    assert output.index("Options:") < output.index("Search and structure:")
    assert output.index("Operations:") < output.index("Use tg --help-all for all commands")
    assert max(map(len, output.splitlines())) <= width


def test_RenderRootHelp_NoArguments_ShowsCoreWorkflow(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["tg"])

    with pytest.raises(SystemExit) as exited:
        main_entry()
    captured = capsys.readouterr()

    assert exited.value.code == 0
    assert "Usage: tg" in captured.out and captured.err == ""
    rendered_commands = _command_rows(captured.out)
    assert rendered_commands == CORE_WORKFLOW_COMMANDS
    assert len(captured.out.splitlines()) <= 45


def test_RenderRootHelpAll_ArgvRouting_ShowsCompleteSurface(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["tg", "--help-all"])

    with pytest.raises(SystemExit) as exited:
        main_entry()
    captured = capsys.readouterr()

    assert exited.value.code == 0
    rendered_commands = _command_rows(captured.out)
    assert set(rendered_commands) == EXPECTED_PUBLIC_COMMANDS
    assert len(rendered_commands) == 58


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

    output = (
        CliRunner()
        .invoke(group, ["--help"], terminal_width=width, obj={"show_all_commands": True})
        .output
    )
    lines = output.splitlines()
    rows = tuple(re.findall(r"(?m)^  long-description-command[^\n]*", output))

    assert len(rows) == 1
    assert len(rows[0]) <= width
    assert "…" in rows[0]
    assert lines[lines.index(rows[0]) + 1] == ""


def test_RenderRootHelp_HiddenAndFutureCommands_PreservesVisibility():
    group = TopLevelHelpGroup(name="tg", help=HELP_SUMMARY, epilog=HELP_FOOTER)
    group.add_command(Command("search", help="Search files."))
    group.add_command(Command("hidden-command", help="Hidden command.", hidden=True))
    group.add_command(Command("future-visible", help="Future command."))

    output = (
        CliRunner()
        .invoke(group, ["--help"], terminal_width=80, obj={"show_all_commands": True})
        .output
    )

    assert _command_rows(output) == ("search", "future-visible")
    assert output.index("Operations:") < output.index("future-visible")
