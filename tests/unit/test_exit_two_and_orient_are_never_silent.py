"""An incomplete result must SAY so on the text path -- including where the exit code cannot.

Two confident-false-zero bugs, both measured on the real CLI before the fix, and both invisible to
the existing never-silent ratchet because that keys on `_scan_incomplete`, which deliberately does
not read `result_incomplete`.

A0 -- `TENSOR_GREP_MAX_PARSE_BYTES=10 tg imports <file>`::

    exit 2 · stdout "imports=0 resolved=0 external=0 unresolved=0" · stderr 0 bytes
    --json  ->  result_incomplete: true, incomplete_reason: "file exceeds the 10-byte parse cap..."

  Cause: `_emit_symbol_command_result` exits on `partial or result_incomplete`, but the caveat it
  prints comes from `_annotate_result_completeness`, computed from a DIFFERENT set of signals. A
  payload arriving with `result_incomplete` already set by the command itself got no caveat.

A1 -- `tg orient <dir> --deadline 0.1`::

    exit 0 · stdout "central files (0):" · stderr 0 bytes
    --json  ->  partial: true

  `tg orient` has NO exit-2 contract by design, which makes the text disclosure the ONLY signal a
  caller gets. It was absent, so the deliberate exit 0 made this worse rather than better.

Both fixes derive the message from the SAME predicate the exit uses, so the two cannot disagree by
construction rather than by two lists being kept in sync.
"""

from __future__ import annotations

from typing import Any

import pytest

from tensor_grep.cli import main as cli_main


def _capture(payload: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> str:
    """Run the shared symbol-command emitter in text mode and return stdout."""
    try:
        cli_main._emit_symbol_command_result(
            payload,
            result_key="imports",
            json_output=False,
            emit_text=lambda p: print(f"imports={len(p.get('imports') or [])}"),
        )
    except SystemExit:
        pass
    except Exception as exc:  # typer.Exit is not a SystemExit subclass in every version
        if type(exc).__name__ != "Exit":
            raise
    return capsys.readouterr().out


def test_a_command_set_result_incomplete_is_disclosed(capsys: pytest.CaptureFixture[str]) -> None:
    """THE A0 DEFECT: `result_incomplete` set by the command produced no caveat at all."""
    out = _capture(
        {
            "imports": [],
            "result_incomplete": True,
            "incomplete_reason": "file exceeds the 10-byte parse cap (size=46)",
        },
        capsys,
    )

    assert "INCOMPLETE" in out.upper(), (
        "an incomplete result printed a bare zero with no disclosure -- a confident false zero"
    )
    assert "parse cap" in out, "the disclosure dropped the reason the payload already carried"


def test_the_disclosure_leads_the_payload(capsys: pytest.CaptureFixture[str]) -> None:
    """Position is contract: a truncation banner LEADS, it does not trail.

    An agent that streams or head-truncates output must see the caveat before the numbers it
    would otherwise trust.
    """
    out = _capture(
        {"imports": [], "result_incomplete": True, "incomplete_reason": "cap hit"},
        capsys,
    )
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines, "no output at all"
    assert "INCOMPLETE" in lines[0].upper(), (
        f"the banner does not lead; first line was {lines[0]!r}"
    )


def test_a_complete_result_stays_silent(capsys: pytest.CaptureFixture[str]) -> None:
    """CONTROL ARM: without it, printing the banner unconditionally would pass every test above
    while making every complete run cry wolf -- which is how a disclosure gets ignored."""
    out = _capture({"imports": [{"module": "os"}]}, capsys)

    assert "INCOMPLETE" not in out.upper(), "a complete result must not claim to be incomplete"


def test_a_partial_result_is_disclosed_too(capsys: pytest.CaptureFixture[str]) -> None:
    """The other half of the exit predicate. `--deadline` sets `partial`, a cap sets
    `result_incomplete`; the message must follow BOTH or it re-splits from the exit."""
    out = _capture({"imports": [], "partial": True}, capsys)

    assert "INCOMPLETE" in out.upper(), "a deadline-truncated result was not disclosed"


def test_orient_discloses_a_partial_scan() -> None:
    """THE A1 DEFECT: orient exits 0 by design, so text was the only signal -- and it was absent.

    Pinned by source, not behaviour, because orient's text path is inline in the command body and
    the payload comes from a real repo scan; a skipped test here would prove nothing, which is
    worse than no test. Measured before the fix on
    `tg orient src/tensor_grep/cli --deadline 0.1`: exit 0, `central files (0)`, 0 bytes of stderr,
    `--json` carrying `partial: true`.
    """
    import inspect

    source = inspect.getsource(cli_main.orient)
    text_path = source.split('typer.echo(f"# Codebase orientation')[0]

    # PREMISE: we really isolated the code that runs BEFORE the payload is printed. If the header
    # line is ever reworded, this split silently yields the whole function and the assertion below
    # would pass for the wrong reason.
    assert text_path != source, "could not locate orient's text header; update this guard"
    assert "_truncation_message(" in text_path, (
        "orient's text path emits no incompleteness banner before the payload. It has no exit-2 "
        "contract, so this line is the ONLY signal a caller gets that the scan was cut short."
    )
    assert 'payload.get("partial")' in text_path, (
        "orient's disclosure is not keyed on `partial`, the field its own --json arm sets"
    )


def test_the_orient_banner_never_interpolates_a_raw_dict() -> None:
    """`deadline_limit` is a dict of counters, not prose.

    The first cut interpolated it verbatim and produced a banner containing
    `{'deadline_exceeded': True, 'files_scanned': 0, ...}`. Pinned by source because the value is
    read inline in the command body: the emitter must not format the mapping itself.
    """
    import inspect

    source = inspect.getsource(cli_main.orient)
    assert "deadline_limit" in source, "orient no longer reads deadline_limit; update this guard"
    assert 'f"the scan stopped at the --deadline ({reason})"' not in source, (
        "orient interpolates the deadline_limit mapping straight into the banner; read the "
        "actionable field and say it in words instead"
    )
