"""Task #329: the text-path incompleteness caveat must LEAD, not trail.

A truncated result whose only incompleteness signal is a trailing ``warning:`` line reads as a
complete document with a footnote -- the prefix is what a model treats as the answer. These
tests pin the POSITION, not merely the presence, of the marker:

* a truncation ``warning:`` appears ABOVE the first payload line (both text emitters);
* an advisory ``note:`` (the zero-callers caveat) stays BELOW it -- that result is complete and
  the note only warns against over-reading it, so trailing is correct there;
* a complete result emits neither (the control arm: with the fix reverted the first two tests
  fail on ordering, and this one must keep passing so the assertions are discriminating rather
  than "some marker appeared somewhere in stdout").

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
from tensor_grep.cli.main import _completeness_caveat_lines, _emit_symbol_command_result

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
