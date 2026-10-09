"""Public dogfood CLI registration, shared by source and installed artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import typer


def dogfood(
    root: Path = typer.Option(Path("."), "--root", help="Repository root to validate."),
    output: Path | None = typer.Option(None, "--output", help="Optional JSON report path."),
    expected_version: str | None = typer.Option(
        None, "--expected-version", help="Expected tensor-grep version. Defaults to pyproject."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    progress: str = typer.Option(
        "auto",
        "--progress",
        help="Progress reporting mode: auto, always, or never. Emits to stderr only.",
    ),
    progress_interval_s: float = typer.Option(
        30.0,
        "--progress-interval-s",
        help="Seconds between progress heartbeats for the active phase.",
    ),
    timeout_s: float | None = typer.Option(
        None,
        "--timeout-s",
        help="Maximum seconds for the nested readiness process; defaults to a derived budget.",
    ),
    no_shell_probes: bool = typer.Option(
        False, "--no-shell-probes", help="Skip public shell version probes."
    ),
    no_wsl_probe: bool = typer.Option(False, "--no-wsl-probe", help="Skip the optional WSL probe."),
    features: bool = typer.Option(
        False, "--features", help="Run packaged feature checks with disposable fixtures."
    ),
    all_checks: bool = typer.Option(
        False, "--all", help="Run the deduplicated readiness and feature checks."
    ),
) -> None:
    """Run the agent-readiness dogfood gate; writes only explicit --output and a sibling readiness report."""
    from tensor_grep.cli.dogfood_unified import run_unified_dogfood
    from tensor_grep.cli.progress import normalize_progress_mode

    try:
        progress_mode = normalize_progress_mode(progress)
        if progress_interval_s <= 0:
            raise ValueError("progress interval must be greater than 0")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("dogfood timeout must be greater than 0")
        if features and all_checks:
            raise ValueError("--features and --all are mutually exclusive")
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    exit_code, report = run_unified_dogfood(
        features=features,
        all_checks=all_checks,
        root=root,
        output=output,
        expected_version=expected_version,
        include_shell_probes=not no_shell_probes,
        include_wsl_probe=not no_wsl_probe,
        progress_mode=progress_mode,
        progress_interval_s=progress_interval_s,
        json_output=json_output,
        timeout_s=timeout_s,
    )
    if json_output:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
    else:
        summary = cast(dict[str, object], report["agent_readiness"]).get("summary")
        if not isinstance(summary, dict):
            summary = {}
        verdict = cast(dict[str, object], report["verdict"])
        typer.echo(f"Dogfood verdict: {verdict['status']}")
        typer.echo(
            "agent-readiness: "
            f"passed={summary.get('passed', 0)} "
            f"failed={summary.get('failed', 0)} "
            f"skipped={summary.get('skipped', 0)}"
        )
        world_class_readiness = report.get("world_class_readiness")
        if isinstance(world_class_readiness, dict):
            typer.echo(f"world-class claim: {world_class_readiness.get('status', 'unknown')}")
        if output is not None:
            typer.echo(f"report: {output}")
        failed_checks = verdict.get("failed_checks")
        if isinstance(failed_checks, list) and failed_checks:
            typer.echo("failed checks: " + ", ".join(str(check) for check in failed_checks))
    if exit_code != 0:
        raise typer.Exit(code=exit_code)
