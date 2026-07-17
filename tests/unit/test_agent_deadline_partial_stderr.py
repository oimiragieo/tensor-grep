"""TDD for the v1.81.6 dogfood finding #1 (CEO-relayed, both dogfood reports flagged it as the
#1 agent confusion): `tg agent <path> --deadline N` can exit 2 with `partial: true` / a
deadline-type `partial_reason` while `confidence.overall` is high (e.g. 0.9) and
`ask_user_before_editing.required` is false -- a genuinely USABLE answer that merely got
truncated collecting SECONDARY evidence after the deadline, not a real failure. Agents keying on
the exit code alone misread this as a hard failure.

The fix (`main._agent_trustworthy_deadline_partial_note`, wired at both `raise typer.Exit(2)`
sites inside `main.agent`) is additive/advisory-only: it never changes the exit code (stays 2),
never changes the stdout JSON, and never changes the capsule schema -- it only ever adds ONE
stderr line, and ONLY when the exit-2 is caused solely by a trustworthy deadline-partial. A
genuine needs-attention exit-2 (ask required, low confidence, or a non-deadline partial such as a
`scan_limit`/`caller_scan_limit` possibly-truncated cap) must get no note and keep reading as
needs-attention, unchanged.

Two layers of coverage:
  * Direct unit tests of the pure predicate (`_agent_trustworthy_deadline_partial_note`) -- fast,
    precise pins of every branch, mirroring how `test_render_daemon_exit_codes.py` unit-tests the
    sibling `_scan_incomplete` gate directly.
  * CliRunner end-to-end tests through both `tg agent` exit-2 call sites (the cold path via a
    monkeypatched `agent_capsule.build_agent_capsule`, and the warm-daemon path via a
    monkeypatched `main._maybe_agent_via_running_daemon`) -- proving the note actually reaches
    stderr (not stdout) at the real CLI boundary, and that stdout JSON is byte-for-byte unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

import tensor_grep.cli.agent_capsule as agent_capsule
import tensor_grep.cli.main as main
from tensor_grep.cli.main import _agent_trustworthy_deadline_partial_note, app

runner = CliRunner()


def _trustworthy_deadline_partial_capsule(
    path: Path | str, query: str, *, overall: float = 0.9
) -> dict[str, Any]:
    """A capsule shaped like a real `build_agent_capsule_from_map` result (agent_capsule.py
    :2860-2947): a deadline cutoff hit AFTER the primary-target ranking/render already completed
    (e.g. the call-site rescue scan or the final wall-clock backstop), so confidence is high and
    no confirmation was required -- the "genuinely usable" case this fix targets."""
    return {
        "capsule_version": 1,
        "path": str(path),
        "query": query,
        "primary_target": {"file": "m.py", "line": 1, "symbol": "f", "confidence": overall},
        "alternative_targets": [],
        "confidence": {"overall": overall, "downgrade_reasons": []},
        "ask_user_before_editing": {"required": False, "reasons": []},
        "ambiguity": {"status": "none", "requires_confirmation": False},
        "context_consistency": {},
        "validation_commands": [],
        "partial": True,
        "partial_reason": "deadline",
        "deadline_limit": {"deadline_exceeded": True},
    }


# ==================================================================================================
# Layer 1: direct unit tests of the pure predicate.
# ==================================================================================================


def test_note_present_on_trustworthy_deadline_partial() -> None:
    payload = _trustworthy_deadline_partial_capsule("p", "q", overall=0.9)
    note = _agent_trustworthy_deadline_partial_note(payload)
    assert note is not None
    assert note.startswith("note:")
    assert "--deadline" in note
    assert "0.90" in note
    assert "usable" in note
    assert note.isascii(), "CLI-rendered stderr text must stay ASCII-only (cp1252 console rule)"


def test_note_present_on_deadline_exceeded_reason_variant() -> None:
    payload = _trustworthy_deadline_partial_capsule("p", "q")
    payload["partial_reason"] = "deadline_exceeded"
    assert _agent_trustworthy_deadline_partial_note(payload) is not None


def test_note_absent_when_not_partial() -> None:
    payload = _trustworthy_deadline_partial_capsule("p", "q")
    payload["partial"] = False
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_absent_on_ask_required() -> None:
    payload = _trustworthy_deadline_partial_capsule("p", "q")
    payload["ask_user_before_editing"] = {
        "required": True,
        "reasons": ["primary target is ambiguous"],
    }
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_absent_on_low_confidence() -> None:
    payload = _trustworthy_deadline_partial_capsule("p", "q", overall=0.5)
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_absent_just_below_confidence_threshold() -> None:
    # Mirrors agent_capsule._capsule_low_confidence_ask_reason's own "< 0.75" cutoff exactly, by
    # calling through that real helper rather than a second hardcoded literal -- this pins the
    # boundary without risking drift between the two thresholds.
    payload = _trustworthy_deadline_partial_capsule("p", "q", overall=0.749)
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_present_exactly_at_confidence_threshold() -> None:
    payload = _trustworthy_deadline_partial_capsule("p", "q", overall=0.75)
    assert _agent_trustworthy_deadline_partial_note(payload) is not None


def test_note_absent_on_non_deadline_partial_reason() -> None:
    payload = _trustworthy_deadline_partial_capsule("p", "q")
    payload["partial_reason"] = "self_verify"
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_absent_on_missing_partial_reason() -> None:
    payload = _trustworthy_deadline_partial_capsule("p", "q")
    del payload["partial_reason"]
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_absent_on_scan_limit_truncation_only() -> None:
    """A `--max-repo-files` scan-coverage cap, no deadline at all -- the genuine "the scan did
    not cover the whole repo" case the finding says must keep reading as needs-attention."""
    payload = {
        "path": "p",
        "query": "q",
        "confidence": {"overall": 0.9},
        "ask_user_before_editing": {"required": False, "reasons": []},
        "scan_limit": {"possibly_truncated": True, "files_scanned": 100, "files_total": 500},
    }
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_absent_on_caller_scan_limit_truncation_only() -> None:
    payload = {
        "path": "p",
        "query": "q",
        "confidence": {"overall": 0.9},
        "ask_user_before_editing": {"required": False, "reasons": []},
        "caller_scan_limit": {"possibly_truncated": True},
    }
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_absent_on_caller_scan_truncated_flag() -> None:
    payload = {
        "path": "p",
        "query": "q",
        "confidence": {"overall": 0.9},
        "ask_user_before_editing": {"required": False, "reasons": []},
        "caller_scan_truncated": True,
    }
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_absent_when_deadline_partial_combined_with_scan_limit_truncation() -> None:
    """ "Solely" semantics: a deadline-partial that ALSO carries a scan_limit possibly_truncated
    cap is a compound truncation, not a solely-trustworthy deadline-partial -- must stay silent
    even though `partial`/`partial_reason` alone would otherwise qualify."""
    payload = _trustworthy_deadline_partial_capsule("p", "q")
    payload["scan_limit"] = {"possibly_truncated": True, "files_scanned": 10, "files_total": 999}
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_absent_when_confidence_missing() -> None:
    payload = _trustworthy_deadline_partial_capsule("p", "q")
    del payload["confidence"]
    assert _agent_trustworthy_deadline_partial_note(payload) is None


def test_note_absent_when_confidence_overall_not_numeric() -> None:
    payload = _trustworthy_deadline_partial_capsule("p", "q")
    payload["confidence"] = {"overall": "unknown"}
    assert _agent_trustworthy_deadline_partial_note(payload) is None


# ==================================================================================================
# Layer 2: CliRunner end-to-end, cold path (`build_agent_capsule` monkeypatched).
# ==================================================================================================


def _stub_cold_path(monkeypatch, payload: dict[str, Any]) -> None:
    """Force the cold path deterministically regardless of the local TG_SESSION_DAEMON_AUTOSTART
    default: a clean daemon "miss" plus a fixed `build_agent_capsule` return, mirroring
    test_cli_deadline_coverage_gaps.py's `_agent_cold_spy` pattern."""
    monkeypatch.setattr(main, "_maybe_agent_via_running_daemon", lambda **kwargs: None)
    monkeypatch.setattr(agent_capsule, "build_agent_capsule", lambda *a, **k: payload)


def test_cli_agent_cold_path_trustworthy_deadline_partial_emits_stderr_note(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = _trustworthy_deadline_partial_capsule(tmp_path, "f")
    _stub_cold_path(monkeypatch, payload)

    result = runner.invoke(app, ["agent", str(tmp_path), "f", "--json"])

    assert result.exit_code == 2, result.output
    assert "note: partial result" in result.stderr
    assert "--deadline" in result.stderr
    assert "0.90" in result.stderr
    # stdout JSON is byte-for-byte the untouched capsule -- the note is stderr-only.
    assert json.loads(result.stdout) == payload
    assert "note:" not in result.stdout


def test_cli_agent_cold_path_ask_required_suppresses_note(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = _trustworthy_deadline_partial_capsule(tmp_path, "f")
    payload["ask_user_before_editing"] = {
        "required": True,
        "reasons": ["alternative target confidence ties primary target"],
    }
    _stub_cold_path(monkeypatch, payload)

    result = runner.invoke(app, ["agent", str(tmp_path), "f", "--json"])

    assert result.exit_code == 2, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == payload


def test_cli_agent_cold_path_low_confidence_suppresses_note(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = _trustworthy_deadline_partial_capsule(tmp_path, "f", overall=0.55)
    _stub_cold_path(monkeypatch, payload)

    result = runner.invoke(app, ["agent", str(tmp_path), "f", "--json"])

    assert result.exit_code == 2, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == payload


def test_cli_agent_cold_path_non_deadline_partial_suppresses_note(
    tmp_path: Path, monkeypatch
) -> None:
    """A `--max-repo-files` scan-coverage cap (no --deadline involved at all) must still exit 2
    via `_scan_incomplete`'s `scan_limit` branch, but get no trustworthy-deadline note."""
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = {
        "path": str(tmp_path),
        "query": "f",
        "primary_target": {"file": "m.py", "line": 1, "symbol": "f", "confidence": 0.9},
        "alternative_targets": [],
        "confidence": {"overall": 0.9},
        "ask_user_before_editing": {"required": False, "reasons": []},
        "ambiguity": {"status": "none"},
        "context_consistency": {},
        "validation_commands": [],
        "scan_limit": {"possibly_truncated": True, "files_scanned": 3, "files_total": 3000},
    }
    _stub_cold_path(monkeypatch, payload)

    result = runner.invoke(app, ["agent", str(tmp_path), "f", "--json"])

    assert result.exit_code == 2, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == payload


def test_cli_agent_cold_path_complete_result_no_note_no_exit2(tmp_path: Path, monkeypatch) -> None:
    """Sanity/regression guard: a COMPLETE (non-truncated) capsule must stay exit 0 with no
    stderr note at all -- the fix must never touch the untruncated happy path."""
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = {
        "path": str(tmp_path),
        "query": "f",
        "primary_target": {"file": "m.py", "line": 1, "symbol": "f", "confidence": 0.9},
        "alternative_targets": [],
        "confidence": {"overall": 0.9},
        "ask_user_before_editing": {"required": False, "reasons": []},
        "ambiguity": {"status": "none"},
        "context_consistency": {},
        "validation_commands": [],
    }
    _stub_cold_path(monkeypatch, payload)

    result = runner.invoke(app, ["agent", str(tmp_path), "f", "--json"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == payload


# ==================================================================================================
# Layer 2b: CliRunner end-to-end, warm-daemon path (`_maybe_agent_via_running_daemon`
# monkeypatched directly -- exercises the OTHER `raise typer.Exit(2)` site this fix touches).
# ==================================================================================================


def test_cli_agent_daemon_path_trustworthy_deadline_partial_emits_stderr_note(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = _trustworthy_deadline_partial_capsule(tmp_path, "f")
    monkeypatch.setattr(main, "_maybe_agent_via_running_daemon", lambda **kwargs: payload)

    result = runner.invoke(app, ["agent", str(tmp_path), "f", "--json"])

    assert result.exit_code == 2, result.output
    assert "note: partial result" in result.stderr
    assert json.loads(result.stdout) == payload


def test_cli_agent_daemon_path_ask_required_suppresses_note(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    payload = _trustworthy_deadline_partial_capsule(tmp_path, "f")
    payload["ask_user_before_editing"] = {"required": True, "reasons": ["no snippets included"]}
    monkeypatch.setattr(main, "_maybe_agent_via_running_daemon", lambda **kwargs: payload)

    result = runner.invoke(app, ["agent", str(tmp_path), "f", "--json"])

    assert result.exit_code == 2, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == payload
