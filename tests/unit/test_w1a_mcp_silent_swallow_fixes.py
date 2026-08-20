"""W1-a RED-2: the two SILENT-SWALLOW handlers found on the MCP surface, and their fixes.

Both were found by reading every one of the 57 broad handlers in ``cli/mcp_server.py``,
``cli/mcp_symbol_tools.py``, ``cli/mcp_audit_tools.py`` and ``cli/mcp_rewrite_tools.py`` in its
enclosing function (docs/plans/2026-08-20-worldclass-closeout-plan.md, W1.2). Both share the
defining shape: the handler catches, does not disclose, and returns a value the CLIENT CANNOT
DISTINGUISH from the healthy one.

Both tests below were observed RED on the pre-fix bytes (``git stash`` is forbidden here, so the
red arm was taken with ``git checkout origin/main -- <file>``; the receipts are in the PR body):

  * ``test_audit_manifest_record_failure_is_disclosed`` -- pre-fix, stderr was EMPTY and the
    assertion failed naming it. The handler was literally ``except Exception: return``.
  * ``test_session_open_tracked_file_count_degradation_is_disclosed`` -- pre-fix, the payload
    had no ``tracked_file_count_error`` key and ``tracked_file_count`` silently equalled
    ``file_count``, indistinguishable from a correctly computed count.

NEITHER FIX CHANGES THE SUCCESS PATH, and each discloses on the channel its AUDIENCE reads:
#1 to stderr, because the client's rewrite genuinely succeeded and only the operator's audit
history is affected; #2 into the payload, because the degraded value is IN what the client
received. Both are failure-branch only, and both healthy paths are asserted unchanged below --
"I only touched the error path" is a claim about behaviour and gets a test like any other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import mcp_server


def _boom(*_args: object, **_kwargs: object) -> Any:
    raise RuntimeError("W1A_RED2_INJECTED")


# ---------------------------------------------------------------------------
# SILENT-SWALLOW #1 -- cli/mcp_server.py::_record_generated_audit_manifest, handler index 0.
#
# WHY IT MATTERS. This is the write side of the AUDIT TRAIL that ``tg_audit_history`` reads
# back. A swallowed failure here means an applied rewrite is missing from the audit history
# while the rewrite's own response says nothing at all -- an audit-integrity hole on a
# network-reachable tool, which is why it is SILENT-SWALLOW and not a benign best-effort.
# ---------------------------------------------------------------------------


def test_audit_manifest_record_failure_is_disclosed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Disclosure goes to STDERR, not the payload, and that channel choice is deliberate.

    The client's rewrite genuinely succeeded, so the JSON-RPC result must not gain an
    unversioned key on a network-facing contract; the party who needs to know is the OPERATOR,
    whose audit history is now incomplete. stdio MCP reserves stdout for JSON-RPC framing, so
    stderr is the only correct channel. (Contrast SILENT-SWALLOW #2 below, where the degraded
    value is IN the client's payload and therefore must be disclosed there.)

    A first draft of this fix stamped the payload instead and turned two previously-green tests
    in test_mcp_server.py red -- those tests mock the native binary, so the audit-history append
    was ALREADY failing under them and the bare `return` was hiding it. That is the swallow this
    record documents, observed live rather than argued.
    """

    monkeypatch.setattr("tensor_grep.cli.audit_manifest.record_audit_manifest", _boom)

    payload: dict[str, Any] = {"audit_manifest": {"path": "manifest.json"}}
    mcp_server._record_generated_audit_manifest(payload)

    err = capsys.readouterr().err
    assert "audit-history append failed" in err, (
        "audit-history append failed and NOTHING said so -- the operator cannot distinguish a "
        f"recorded rewrite from one the audit trail silently lost. stderr was {err!r}"
    )
    assert "W1A_RED2_INJECTED" in err, f"disclosure does not carry the reason: {err!r}"
    assert "manifest.json" in err, f"disclosure does not name the manifest: {err!r}"
    assert payload == {"audit_manifest": {"path": "manifest.json"}}, (
        f"the wire payload must be untouched on this path: {payload!r}"
    )


def test_audit_manifest_success_payload_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The success path must stay silent AND leave the payload untouched. Control for the fix
    above: if stderr spoke on the healthy path too, the disclosure would be noise rather than a
    signal, and the arm above would pass for the wrong reason."""

    calls: list[str] = []
    monkeypatch.setattr(
        "tensor_grep.cli.audit_manifest.record_audit_manifest",
        lambda p: calls.append(str(p)),
    )
    payload: dict[str, Any] = {"audit_manifest": {"path": "manifest.json"}}
    mcp_server._record_generated_audit_manifest(payload)

    assert calls == ["manifest.json"], "positive control: the recorder was never called"
    assert payload == {"audit_manifest": {"path": "manifest.json"}}, (
        f"success path grew a key: {payload!r}"
    )


# ---------------------------------------------------------------------------
# SILENT-SWALLOW #2 -- cli/mcp_server.py::tg_session_open, handler index 1.
#
# WHY IT MATTERS. `tracked_file_count` exists precisely BECAUSE it differs from `file_count`
# (source + test vs source only -- see the M13 comment at the site). Falling back to
# `file_count` therefore returns a number that is wrong in the exact dimension the field was
# added to express, in a payload otherwise identical to the healthy one.
# ---------------------------------------------------------------------------


def test_session_open_tracked_file_count_degradation_is_disclosed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr("tensor_grep.cli.session_store.get_session", _boom)
    payload = json.loads(mcp_server.tg_session_open(path=str(tmp_path)))

    assert "tracked_file_count_error" in payload, (
        "tracked_file_count fell back to file_count with no disclosure -- "
        f"payload keys={sorted(payload)}"
    )
    assert "W1A_RED2_INJECTED" in payload["tracked_file_count_error"], (
        f"degradation reason absent: {payload!r}"
    )
    # The fallback value itself is still returned (the session IS open) -- assert it, so a
    # future "fix" that drops the field entirely, or raises, is caught rather than welcomed.
    assert payload["tracked_file_count"] == payload["file_count"]


def test_session_open_healthy_payload_carries_no_degradation_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control arm for the fix above: with no injection the new key must be ABSENT, so its
    presence in the arm above is caused by the injected failure and not by the fix
    unconditionally stamping it."""

    (tmp_path / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    payload = json.loads(mcp_server.tg_session_open(path=str(tmp_path)))
    assert "error" not in payload, f"control arm failed for an unrelated reason: {payload}"
    assert "tracked_file_count_error" not in payload
    assert "tracked_file_count" in payload
