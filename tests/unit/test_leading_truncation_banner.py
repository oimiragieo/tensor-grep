"""Task #329: the text-path incompleteness caveat must LEAD, not trail.

A truncated result whose only incompleteness signal is a trailing ``warning:`` line reads as a
complete document with a footnote -- the prefix is what a model treats as the answer. These
tests pin the POSITION, not merely the presence, of the marker:

* a truncation ``warning:`` appears ABOVE the first payload line (all THREE wired emitters --
  the symbol commands, the ``blast-radius`` counts block, and the ``--mermaid`` renderer);
* an advisory ``note:`` (the zero-callers caveat) stays BELOW it -- that result is complete and
  the note only warns against over-reading it, so trailing is correct there;
* a complete result emits neither (the control arm: with the fix reverted the ordering tests fail
  and this one must keep passing, so the assertions are discriminating rather than "some marker
  appeared somewhere in stdout").

Reproducing that control arm needs one setup note, because the obvious way to do it does NOT work:
reverting the fix wholesale deletes ``_completeness_caveat_lines``, so this module dies at import
and the whole file ERRORS rather than failing. A file that cannot be collected is not a red arm --
it proves nothing about any assertion in it. Back-fill the helper into the pre-fix tree (or revert
only the call sites) so the ASSERTIONS are what discriminate. Measured that way: 7 fail, 15 pass,
and every one of the 15 is labelled below as a control arm or a deliberate non-sweep guard.

Every ordering assertion is preceded by a premise assertion that the payload line was actually
emitted, so an inert ``emit_text`` cannot make ``index()`` comparisons pass vacuously.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer

from tensor_grep.cli import main as main_mod
from tensor_grep.cli.main import (
    _completeness_caveat_lines,
    _emit_symbol_command_result,
    _render_blast_radius_mermaid,
)

_PAYLOAD_SENTINEL = "callers=0 files=0"


def _emit_sentinel(_payload: dict[str, Any]) -> None:
    typer.echo(_PAYLOAD_SENTINEL)


def _truncated_scan_limit() -> dict[str, Any]:
    return {
        "max_repo_files": 512,
        "scanned_files": 512,
        "possibly_truncated": True,
        "truncation_cause": "project-files",
    }


# --------------------------------------------------------------- shared ordering policy (unit)


def test_completeness_caveat_lines_puts_truncation_first_and_advice_last() -> None:
    leading, trailing = _completeness_caveat_lines("INCOMPLETE RESULT: capped", is_truncation=True)
    assert leading == "warning: INCOMPLETE RESULT: capped"
    assert trailing is None

    leading, trailing = _completeness_caveat_lines(
        "0 callers is not dead code", is_truncation=False
    )
    assert leading is None
    assert trailing == "note: 0 callers is not dead code"

    # No caveat at all -> neither slot is filled (the complete-result control).
    assert _completeness_caveat_lines(None, is_truncation=False) == (None, None)
    assert _completeness_caveat_lines(None, is_truncation=True) == (None, None)


# ------------------------------------------------------- symbol commands (defs/refs/callers/...)


def test_truncation_warning_leads_the_symbol_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload: dict[str, Any] = {
        "callers": [],
        "files": [],
        "symbol": "x",
        "path": ".",
        "scan_limit": _truncated_scan_limit(),
    }
    with pytest.raises(typer.Exit):
        _emit_symbol_command_result(
            payload, result_key="callers", json_output=False, emit_text=_emit_sentinel
        )
    out = capsys.readouterr().out
    # Premise: the payload really was rendered, so the ordering check below is not vacuous.
    assert _PAYLOAD_SENTINEL in out
    assert "warning:" in out
    assert out.index("warning:") < out.index(_PAYLOAD_SENTINEL)
    # And it is the very first thing the reader sees.
    assert out.splitlines()[0].startswith("warning: INCOMPLETE RESULT:")


def test_zero_callers_advisory_note_still_trails_the_symbol_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A COMPLETE scan that found no callers: commentary, not incompleteness -> stays trailing.
    payload: dict[str, Any] = {"callers": [], "files": [], "symbol": "x", "path": "."}
    with pytest.raises(typer.Exit):
        _emit_symbol_command_result(
            payload, result_key="callers", json_output=False, emit_text=_emit_sentinel
        )
    out = capsys.readouterr().out
    assert _PAYLOAD_SENTINEL in out
    assert "note:" in out
    assert out.index("note:") > out.index(_PAYLOAD_SENTINEL)
    assert "warning:" not in out


def test_complete_symbol_result_emits_no_banner_at_all(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # CONTROL ARM: a complete result with callers must print the payload and nothing else --
    # otherwise "a marker appeared" would be true in every arm and prove nothing.
    payload: dict[str, Any] = {
        "callers": [{"path": "a.py", "line": 1}],
        "files": ["a.py"],
        "symbol": "x",
        "path": ".",
    }
    _emit_symbol_command_result(
        payload, result_key="callers", json_output=False, emit_text=_emit_sentinel
    )
    out = capsys.readouterr().out
    assert out.strip() == _PAYLOAD_SENTINEL


def test_json_output_is_unaffected_by_the_banner_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The reordering is a TEXT-path concern; --json must still emit exactly one JSON document
    # with the caveat as a field (no stray banner line would break json.loads on stdout).
    payload: dict[str, Any] = {
        "callers": [],
        "files": [],
        "symbol": "x",
        "path": ".",
        "scan_limit": _truncated_scan_limit(),
    }
    with pytest.raises(typer.Exit):
        _emit_symbol_command_result(
            payload, result_key="callers", json_output=True, emit_text=_emit_sentinel
        )
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["result_incomplete"] is True
    assert "INCOMPLETE" in emitted["caveat"]


# ------------------------------------------------------------------------- blast-radius (twin)


def _call_blast_radius(path: Path, *, json_output: bool = False) -> None:
    main_mod.blast_radius(
        path=str(path),
        symbol_arg="x",
        symbol=None,
        provider="native",
        max_depth=3,
        max_repo_files=512,
        max_callers=None,
        max_files=None,
        deadline=None,
        json_output=json_output,
        mermaid_output=False,
    )


def _stub_blast_radius_payload(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    from tensor_grep.cli import repo_map

    # The command imports these lazily from repo_map, so patching the module attributes is what
    # the call site actually resolves. The daemon fast path is forced OFF so both arms take the
    # same (cold) route -- an arm that silently took a different path would not be a control.
    monkeypatch.setattr(
        repo_map, "build_symbol_blast_radius", lambda *a, **k: dict(payload), raising=True
    )
    monkeypatch.setattr(
        main_mod, "_maybe_symbol_command_via_running_daemon", lambda **k: None, raising=True
    )


_BLAST_HEADER = "Blast radius for"


def _blast_payload(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": "x",
        "path": ".",
        "definitions": [],
        "callers": [],
        "files": [],
        "tests": [],
        "import_graph_consumers": [],
    }
    payload.update(extra)
    return payload


def test_truncation_warning_leads_the_blast_radius_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_blast_radius_payload(monkeypatch, _blast_payload(scan_limit=_truncated_scan_limit()))
    with pytest.raises(typer.Exit):
        _call_blast_radius(tmp_path)
    out = capsys.readouterr().out
    assert _BLAST_HEADER in out  # premise: the counts block really was rendered
    assert "warning:" in out
    assert out.index("warning:") < out.index(_BLAST_HEADER)
    assert out.splitlines()[0].startswith("warning: INCOMPLETE RESULT:")


def test_complete_blast_radius_emits_no_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # CONTROL ARM for the twin.
    _stub_blast_radius_payload(
        monkeypatch,
        _blast_payload(
            definitions=[{"path": "a.py", "line": 1}],
            callers=[{"path": "b.py", "line": 2}],
            files=["a.py", "b.py"],
        ),
    )
    _call_blast_radius(tmp_path)
    out = capsys.readouterr().out
    assert _BLAST_HEADER in out
    assert "warning:" not in out
    assert "note:" not in out


# --------------------------------------------------------------- blast-radius --mermaid (twin 2)
#
# The class fix has to cross to EVERY emitter of the same command or it recurs through the one
# nobody listed. `--mermaid` is the agent-facing renderer, so a trailing disclosure there is the
# more damaging half: an agent that has already walked the graph edges has formed its answer.


def _mermaid_payload(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": "Target",
        "path": "/repo",
        "callers": [{"file": "/repo/pkg/caller.py", "line": 12}],
    }
    payload.update(extra)
    return payload


def _truncated_mermaid_payload() -> dict[str, Any]:
    """A payload shaped the way the COMMAND hands one to the renderer.

    Faithfulness matters here. ``blast_radius`` calls ``_annotate_result_completeness`` before
    either ``--mermaid`` exit, and that stamps ``result_incomplete=True`` whenever a truncation
    exists -- so a production payload always carries BOTH the ``scan_limit`` and the flag. A
    fixture with only ``scan_limit`` would make the pre-fix arm emit no disclosure at all, and
    these tests would then be discriminating against a state the CLI cannot reach: a control that
    never crosses the real boundary is the same arm in a bigger coat.
    """
    return _mermaid_payload(scan_limit=_truncated_scan_limit(), result_incomplete=True)


def test_truncation_warning_leads_the_mermaid_graph() -> None:
    out = _render_blast_radius_mermaid(_truncated_mermaid_payload())
    lines = out.splitlines()
    # Premise: a real node was rendered, so the ordering comparison is not vacuous.
    assert "pkg/caller.py" in out
    # `graph TD` is the diagram-type declaration, not payload -- it stays line 1.
    assert lines[0] == "graph TD"
    assert lines[1].startswith("  %% warning: INCOMPLETE RESULT:")
    assert out.index("warning:") < out.index("pkg/caller.py")
    assert out.index("warning:") < out.index("-->")


def test_mermaid_truncation_uses_the_warning_prefix_not_the_advisory_note() -> None:
    # The old literal read `%% note: result truncated ...`. `note:` is this command's ADVISORY
    # prefix (a complete result you should not over-read); a truncation is a `warning:`. Emitting
    # the advisory prefix for an incompleteness inverts the very split the fix defines.
    out = _render_blast_radius_mermaid(_truncated_mermaid_payload())
    assert "%% warning:" in out
    assert "%% note:" not in out


def test_mermaid_truncation_names_the_knob_that_actually_lifts_the_cap() -> None:
    # WRONG-KNOB arm. The old literal advised "raise --max-callers/--max-files" for every cause.
    # Neither lifts a --max-repo-files scan cap, so the graph told the reader to turn a dial that
    # cannot change the answer -- the same defect #762 fixed on the MCP surface. Sharing
    # _scan_truncation_warning makes the advice cause-specific instead.
    out = _render_blast_radius_mermaid(_truncated_mermaid_payload())
    assert "--max-repo-files" in out
    assert "512-file cap" in out  # the cause is named, not just the fact of truncation


def test_complete_mermaid_graph_emits_no_banner() -> None:
    # CONTROL ARM: without it, "a warning appeared somewhere" would be true in every arm.
    out = _render_blast_radius_mermaid(_mermaid_payload())
    assert "-->" in out  # premise: the graph really rendered
    assert "warning:" not in out
    assert "INCOMPLETE" not in out


def test_mermaid_zero_callers_advisory_still_trails_the_graph() -> None:
    # The deliberate NON-sweep: this line is commentary on a COMPLETE scan that genuinely found
    # nothing, not a qualifier on an untrustworthy one, so trailing is correct and it must not be
    # dragged into the leading banner along with the truncation case.
    out = _render_blast_radius_mermaid(_mermaid_payload(callers=[]))
    lines = out.splitlines()
    assert "no callers found" in out
    assert lines[-1].strip().startswith("%% no callers found")
    assert "warning:" not in out


def test_mermaid_banner_survives_the_real_command_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Every other mermaid test calls the RENDERER. That leaves the wiring untested: the command
    # could stop reaching this branch, or annotate the payload differently, and the renderer-level
    # tests would all stay green. Drives `blast_radius` itself with `--mermaid`.
    _stub_blast_radius_payload(
        monkeypatch,
        _blast_payload(
            callers=[{"file": str(tmp_path / "c.py"), "line": 3}],
            scan_limit=_truncated_scan_limit(),
        ),
    )
    with pytest.raises(typer.Exit):
        main_mod.blast_radius(
            path=str(tmp_path),
            symbol_arg="x",
            symbol=None,
            provider="native",
            max_depth=3,
            max_repo_files=512,
            max_callers=None,
            max_files=None,
            deadline=None,
            json_output=False,
            mermaid_output=True,
        )
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "graph TD"
    assert lines[1].startswith("  %% warning: INCOMPLETE RESULT:")


def test_mermaid_banner_is_one_line_so_it_cannot_inject_graph_statements() -> None:
    # A `%%` comment ends at the newline. The deleted literal was a fixed string; the replacement
    # interpolates payload-derived values, so a newline reaching the banner would close the
    # comment and turn the rest into live graph statements. No reachable cold path types these as
    # anything but int -- this pins the flattening so that stays true regardless.
    hostile = _truncated_scan_limit()
    hostile["max_repo_files"] = 'BAD\n  evil["pwn"]\n  evil --> target'
    out = _render_blast_radius_mermaid(_mermaid_payload(scan_limit=hostile, result_incomplete=True))
    banner_lines = [ln for ln in out.splitlines() if "INCOMPLETE RESULT" in ln]
    assert len(banner_lines) == 1, f"banner spans multiple lines: {banner_lines}"
    assert 'evil["pwn"]' not in out.replace(banner_lines[0], "")
    # Premise: the hostile value really did reach the banner, or this proves nothing.
    assert "BAD" in banner_lines[0]


def test_mermaid_upstream_result_incomplete_still_gets_a_leading_disclosure() -> None:
    # An incompleteness stamped upstream carries no scan_limit/output_limit to describe. Sourcing
    # the text from _scan_truncation_warning must not silently DROP the disclosure in that case --
    # trading a mispositioned warning for an absent one is the worse half of the same class.
    out = _render_blast_radius_mermaid(_mermaid_payload(result_incomplete=True))
    lines = out.splitlines()
    assert lines[1].startswith("  %% warning: INCOMPLETE RESULT:")
    assert out.index("warning:") < out.index("pkg/caller.py")
