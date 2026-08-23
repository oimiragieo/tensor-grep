"""Pin flag parity between `blast-radius` and `blast-radius-render`.

WHY THIS EXISTS
---------------
An external agent dogfood (2026-08-22, v1.111.7) reported: `tg blast-radius --deadline` works,
`tg blast-radius-render --deadline` returns "No such option". Verified against the published
binary -- `blast-radius --help` contains `--deadline`, `blast-radius-render --help` does not.

The reporter's framing is the important part, and it is about AGENTS not humans:

    "Agents copying flags across siblings break."

A human reads the help text for the command they are running. An agent generalises: it learns
`--deadline` bounds a scan on one blast-radius command and applies it to the other, because
nothing in the payload or the naming says the surfaces differ. So this test pins the CLASS
(the two siblings share their scan-bounding vocabulary) rather than the one flag, because
fixing only `--deadline` leaves the next divergence to be found by the next agent.

WHY THESE FLAGS
---------------
Both commands take `path`, `symbol`, `provider`, `max_depth` and `max_repo_files` -- they are
the same scan with different output. A flag that bounds THE SCAN must therefore exist on both.
Flags that bound THE RENDER (`max_files`, `max_sources`, `max_symbols_per_file`) legitimately
exist only on the renderer, and this test must NOT demand those on `blast-radius`.

WHAT THIS DOES NOT CLAIM
------------------------
It does not assert the two flags behave identically at runtime, only that the surface exists on
both. A behavioural equivalence test would need a corpus and a timing arm; that is a separate
concern and is not smuggled in here.
"""

from __future__ import annotations

from typer.testing import CliRunner

from tensor_grep.cli.main import app

#: Flags that bound THE SCAN. Both siblings run the same scan, so both must accept these.
#: Render-only bounds (max_files, max_sources, max_symbols_per_file) are deliberately absent.
SCAN_BOUNDING_FLAGS = ("--max-depth", "--max-repo-files", "--deadline")

SIBLINGS = ("blast-radius", "blast-radius-render")


def _help_text(command: str) -> str:
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, f"{command} --help exited {result.exit_code}\n{result.output}"
    return result.output


def test_positive_control_both_siblings_have_help() -> None:
    """Without this, a command that vanished would make every parity assertion vacuous."""
    for command in SIBLINGS:
        text = _help_text(command)
        assert len(text) > 200, f"{command} --help is implausibly short ({len(text)} chars)"
        assert "--max-depth" in text, (
            f"{command} --help lacks --max-depth, so it is not the command this test thinks it is"
        )


def test_detector_can_fail() -> None:
    """The matcher must be able to report absence, or the parity test proves nothing."""
    assert "--deadline" not in "usage: tg blast-radius-render [OPTIONS]", (
        "the substring matcher claims a flag is present in text that lacks it"
    )


def test_scan_bounding_flags_exist_on_both_blast_radius_siblings() -> None:
    missing: list[str] = []
    for command in SIBLINGS:
        text = _help_text(command)
        for flag in SCAN_BOUNDING_FLAGS:
            if flag not in text:
                missing.append(f"{command} is missing {flag}")

    assert not missing, (
        "blast-radius siblings disagree on scan-bounding flags, so an agent that learns a flag "
        "on one and applies it to the other gets 'No such option':\n  " + "\n  ".join(missing)
    )


def test_render_only_flags_are_not_demanded_of_the_plain_command() -> None:
    """Guard against over-correcting this parity rule into a false one.

    `blast-radius` renders nothing, so demanding the renderer's output-shaping flags on it would
    be a fabricated requirement. This test exists so a future 'make them fully identical' edit
    fails loudly instead of quietly adding dead options.
    """
    plain = _help_text("blast-radius")
    for render_only in ("--max-sources", "--max-symbols-per-file"):
        assert render_only not in plain, (
            f"blast-radius unexpectedly grew {render_only}; render-shaping flags belong to "
            "blast-radius-render only"
        )
