"""`tg scan`'s DEFAULT text output must disclose a scope it could not fully read.

`test_scan_unreadable_disclosure.py` (#299) pins the PAYLOAD: `_run_ast_scan_payload` sets
`partial` / `partial_reason` / `remediation` / `unreadable_paths` when a file in scope cannot be
opened, and #310's SARIF output carries that in-band. The default TEXT renderer read none of it,
so the surface most people actually run printed ``Scan completed. total_matches=N`` over files no
rule ever opened -- a payload-level fix that never reached the default output.

Measured on the SHIPPED v1.101.4 against an ACL-denied fixture (the denial asserted to bite
first, and a sibling file asserted still readable), one invocation rendered two ways:

    --json   partial=true, partial_reason="unreadable_path",
             unreadable_paths={"count": 2, "sample": [...]}
    text     "Scan completed. rules=6 matched_rules=2 total_matches=2"     exit 0

The blocked file held two secrets both rules would have matched. Nothing on stdout said so.

SCOPE: these pin the TEXT path only. The exit code stays 0 by an explicit, cross-referenced
decision (the SARIF block's exit-code note in `main.py`, and CHANGELOG v1.101.0) -- asserting
exit 2 here would pin a contract change this commit deliberately did not make.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import main as main_mod
from tensor_grep.cli.main import app

runner = CliRunner()

_SUMMARY = "Scan completed."
_BANNER = "warning: INCOMPLETE SCAN:"


def _base_payload() -> dict[str, Any]:
    return {
        "findings": [
            {
                "rule_id": "python-hardcoded-password",
                "language": "python",
                "matches": 1,
                "files": ["a.py"],
            }
        ],
        "rule_count": 6,
        "matched_rules": 1,
        "total_matches": 1,
        "backends": ["RegexRulesetBackend"],
    }


def _partial_payload() -> dict[str, Any]:
    """The shape `_run_ast_scan_payload` really builds on an unreadable file.

    Mirrored field-for-field from the writer (`scan_unreadable.hit` in `main.py`) rather than
    reduced to a bare `partial` flag: the remediation names the paths, and a fixture that dropped
    it would let a renderer satisfy these tests by printing a marker that tells a reader nothing.
    """
    payload = _base_payload()
    payload.update(
        partial=True,
        partial_reason="unreadable_path",
        unreadable_paths={
            "count": 2,
            "sample": ["/repo/blocked.py", "/repo/also_blocked.py"],
        },
        remediation=(
            "2 read attempt(s) in scope failed (e.g. /repo/blocked.py, "
            "/repo/also_blocked.py), so no rule ran against those files and this result does NOT "
            "prove they are clean. Make them readable, or scope the scan away from them."
        ),
    )
    return payload


def _run_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, Any],
    *extra_args: str,
):
    """Drive the REAL `scan` command with the producer stubbed to a known payload.

    The renderer under test is inline in the command body, so there is no helper to call
    directly -- an earlier draft of this file invented `_emit_scan_text_report`, which does not
    exist and would have failed on AttributeError as a false red. Stubbing the producer and
    invoking the command exercises the actual emit block.
    """
    monkeypatch.setattr(main_mod, "_run_ast_scan_payload", lambda *a, **k: dict(payload))
    return runner.invoke(app, ["scan", str(tmp_path), "--ruleset", "secrets", *extra_args])


def test_partial_scan_discloses_the_unreadable_scope_in_text_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _run_scan(monkeypatch, tmp_path, _partial_payload())
    out = result.stdout
    assert _SUMMARY in out  # premise: the renderer really ran
    assert "INCOMPLETE SCAN" in out
    assert "does NOT prove they are clean" in out
    # The paths are what make it actionable; "something was skipped" is not a disclosure.
    assert "/repo/blocked.py" in out


def test_the_disclosure_leads_the_findings_and_the_total(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Task #329's law. The sibling `codemap` prints its `PARTIAL:` line AFTER its counts; that
    # ordering is the defect, not the half of the precedent worth copying. A reader who has seen
    # `total_matches=1` has already formed the answer by the time a trailing line lands.
    out = _run_scan(monkeypatch, tmp_path, _partial_payload()).stdout
    assert _SUMMARY in out
    assert "[scan] rule=" in out
    assert out.index("INCOMPLETE SCAN") < out.index("[scan] rule=")
    assert out.index("INCOMPLETE SCAN") < out.index(_SUMMARY)


def test_a_complete_scan_emits_no_disclosure_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # CONTROL ARM. Without it, "a warning appeared somewhere" would hold in both arms.
    out = _run_scan(monkeypatch, tmp_path, _base_payload()).stdout
    assert _SUMMARY in out  # premise: the renderer really ran
    assert "INCOMPLETE" not in out
    assert "warning:" not in out


def test_partial_without_a_remediation_string_still_discloses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Fail-closed: `partial` is the signal and `remediation` is the courtesy. Keying the banner
    # off the optional field would silence an incomplete scan exactly when its writer was least
    # careful -- guidance about trust is an allow-list, never a deny-list.
    bare = _base_payload()
    bare["partial"] = True
    out = _run_scan(monkeypatch, tmp_path, bare).stdout
    assert _BANNER in out
    assert "does NOT prove those files are clean" in out


def test_json_mode_stays_a_single_parseable_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The banner is a TEXT-path concern. A stray line on the --json route would break
    # `json.loads` on stdout, which is the failure this whole campaign exists to prevent.
    out = _run_scan(monkeypatch, tmp_path, _partial_payload(), "--json").stdout
    emitted = json.loads(out)
    assert emitted["partial"] is True
    assert "remediation" in emitted
    assert _BANNER not in out
