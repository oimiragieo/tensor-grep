"""Construct the root app before main registers its commands."""

import typer

from tensor_grep.cli.help_catalog import (
    HELP_EXAMPLES,
    HELP_FOOTER,
    HELP_SUMMARY,
    TopLevelHelpGroup,
)


def create_root_app() -> typer.Typer:
    return typer.Typer(
        cls=TopLevelHelpGroup,
        help=HELP_SUMMARY + "\n\n\b\nExamples:\n" + "\n".join(HELP_EXAMPLES),
        epilog=HELP_FOOTER,
        no_args_is_help=True,
        add_completion=True,
        rich_markup_mode="markdown",
    )
