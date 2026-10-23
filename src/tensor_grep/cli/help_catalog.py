"""Plain, grouped root help while leaving each command's own help untouched."""

from __future__ import annotations

import textwrap

import click
from typer.core import TyperGroup

HELP_SUMMARY = "Search code, follow symbols, and prepare a bounded edit."
HELP_EXAMPLES = (
    "tg search -F TODO src",
    "tg callers src create_invoice --json",
    'tg prepare src "add invoice tax" --json',
    "tg mcp",
)
HELP_FOOTER = (
    "Search supports a validated rg-compatible subset. GPU execution is experimental. "
    "Use tg COMMAND --help for details."
)

# This metadata assigns sections only. Typer remains the registration source, and each
# registered command supplies its own short help. The outer tuple controls section order.
COMMAND_GROUPS = (
    (
        "Search and structure",
        frozenset("search find run scan test ast-info new classify sql".split()),
    ),
    (
        "Navigate",
        frozenset(
            "defs refs source impact callers imports importers blast-radius blast-radius-render blast-radius-plan file-api".split()
        ),
    ),
    (
        "Agent",
        frozenset(
            "agent prepare edit-plan diff-impact route-test map orient codemap context context-render inventory docs-coverage".split()
        ),
    ),
    (
        "State and evidence",
        frozenset(
            "session checkpoint freshness coverage rulesets audit audit-verify audit-history audit-diff review-bundle evidence ledger".split()
        ),
    ),
    (
        "Operations",
        frozenset(
            "calibrate upgrade update repair-launcher dogfood devices lsp lsp-setup install-dense install uninstall repair-env doctor mcp".split()
        ),
    ),
)


def _bounded_one_line_help(command: click.Command, width: int) -> str:
    """Render Click's registered help as bounded plain text."""
    raw_help = command.get_short_help_str(limit=width * 2).replace("`", "").replace("**", "")
    single_line = " ".join(raw_help.split())
    return textwrap.shorten(single_line, width=width, placeholder="…")


class TopLevelHelpGroup(TyperGroup):
    """Render only root commands in five plain, task-oriented sections."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        click.Group.format_help(self, ctx, formatter)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        grouped: dict[str, list[tuple[str, click.Command]]] = {
            title: [] for title, _ in COMMAND_GROUPS
        }
        for name in self.list_commands(ctx):
            command = self.get_command(ctx, name)
            if command is None or command.hidden:
                continue
            title = next((label for label, names in COMMAND_GROUPS if name in names), "Operations")
            grouped[title].append((name, command))

        for title, _ in COMMAND_GROUPS:
            names = grouped[title]
            if not names:
                continue
            max_name_width = min(max(len(name) for name, _ in names), 30)
            col_spacing = 2
            first_column_width = max_name_width + col_spacing
            row_indent = formatter.current_indent + formatter.indent_increment
            description_width = max(
                formatter.width - row_indent - first_column_width,
                10,
            )
            rows = [
                (name, _bounded_one_line_help(command, description_width))
                for name, command in names
            ]
            with formatter.section(title):
                formatter.write_dl(rows, col_max=30, col_spacing=col_spacing)
