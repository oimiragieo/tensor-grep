"""`tg rulesets` must disclose whether the rules it lists can actually RUN here.

WHY. On a stock `pip install tensor-grep`, `tg rulesets` prints six security rulesets with rule
counts (`subprocess-safe … rules=33`) and no caveat, while every `tg scan --ruleset <name>` exits 1
because the ast-grep backend is absent. Measured on the PUBLISHED v1.111.1 wheel in a clean
container. A listing that advertises 33 runnable rules when zero of them can execute is worse than
an honest "unavailable here": it sends the user to a command that fails and gives them no reason.

This is the ADVERTISEMENT half of RULESET-UNREACHABLE-ON-STOCK-INSTALL (docs/BACKLOG.md). It does
not make the rulesets work; it stops the listing from overstating what this install can do.

Both arms are exercised, because a disclosure that is always printed is as useless as one that is
never printed: the field must be TRUE when the backend is missing and FALSE when it is present.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app


@pytest.fixture
def _force_backend(monkeypatch):
    """Force the ast-grep wrapper's availability, so neither arm depends on this machine.

    A85: force the optional-engine seam explicitly, never env-detect. The dev box that first
    reported this feature as "working" had an `ast-grep` binary on PATH; a fresh runner does not.
    A test that reads the ambient state measures the box, not the behaviour.
    """

    def _set(available: bool) -> None:
        from tensor_grep.cli import ast_scan

        monkeypatch.setattr(
            ast_scan, "_ruleset_backend_available", lambda: available, raising=False
        )

    return _set


def _rulesets_json(runner: CliRunner) -> dict:
    result = runner.invoke(app, ["rulesets", "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_json_discloses_unavailable_when_the_backend_is_missing(_force_backend):
    _force_backend(False)
    payload = _rulesets_json(CliRunner())

    # Control: the arm is meaningless unless rulesets were actually listed.
    assert payload["rulesets"], "no rulesets listed; this arm cannot discriminate"
    assert payload["rulesets_runnable"] is False, (
        "with the ast-grep backend absent, every listed ruleset fails at scan time; the listing "
        f"must say so. payload keys={sorted(payload)}"
    )
    assert "ast-grep-cli" in payload["rulesets_unavailable_reason"], payload


def test_json_reports_runnable_when_the_backend_is_present(_force_backend):
    """The OTHER arm. Without this, a hardcoded `False` would pass the test above."""
    _force_backend(True)
    payload = _rulesets_json(CliRunner())

    assert payload["rulesets"], "no rulesets listed; this arm cannot discriminate"
    assert payload["rulesets_runnable"] is True, payload
    assert "rulesets_unavailable_reason" not in payload, (
        "the reason field must be OMITTED when the rulesets are runnable, matching this repo's "
        "omit-when-complete envelope convention, so a healthy payload stays byte-identical"
    )


def test_human_output_warns_once_when_unavailable(_force_backend):
    _force_backend(False)
    result = CliRunner().invoke(app, ["rulesets"])

    assert result.exit_code == 0, result.output
    lowered = result.stdout.lower()
    assert "none of these rulesets can run" in lowered, result.stdout
    assert "ast-grep-cli" in result.stdout, (
        "the human surface must name the remediation, not just state the problem"
    )
    # One banner, not one line per ruleset -- six repetitions of the same warning is noise that
    # trains the reader to skip it.
    assert result.stdout.count("ast-grep-cli") == 1, result.stdout


def test_human_output_is_unchanged_when_available(_force_backend):
    _force_backend(True)
    result = CliRunner().invoke(app, ["rulesets"])

    assert result.exit_code == 0, result.output
    assert "ast-grep-cli" not in result.stdout, (
        "a healthy install must not be nagged about a dependency it already satisfies"
    )
    assert "subprocess-safe" in result.stdout, "control: the listing itself must still render"
