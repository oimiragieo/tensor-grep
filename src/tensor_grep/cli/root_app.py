"""Construct the root app before main registers its commands."""

import click
import typer

from tensor_grep.cli.help_catalog import (
    HELP_EXAMPLES,
    HELP_FOOTER,
    HELP_SUMMARY,
    TopLevelHelpGroup,
)


def _help_all_callback(ctx: typer.Context, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    ctx.meta["show_all_commands"] = True
    click.echo(ctx.get_help(), color=ctx.color)
    ctx.exit()


def create_root_app() -> typer.Typer:
    root = typer.Typer(
        cls=TopLevelHelpGroup,
        help=HELP_SUMMARY + "\n\n\b\nExamples:\n" + "\n".join(HELP_EXAMPLES),
        epilog=HELP_FOOTER,
        no_args_is_help=True,
        add_completion=True,
        rich_markup_mode="markdown",
    )

    @root.callback()
    def _root_options(
        help_all: bool = typer.Option(
            False,
            "--help-all",
            is_eager=True,
            callback=_help_all_callback,
            help="Show all available commands and exit.",
        ),
    ) -> None:
        return None

    return root
