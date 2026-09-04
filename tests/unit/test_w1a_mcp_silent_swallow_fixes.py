"""W1-a RED-2: the two SILENT-SWALLOW handlers found on the MCP surface, and their fixes.

Both were found by reading every one of the 57 broad handlers in ``cli/mcp_server.py``,
``cli/mcp_symbol_tools.py``, ``cli/mcp_audit_tools.py`` and ``cli/mcp_rewrite_tools.py`` in its
enclosing function (docs/plans/2026-08-20-worldclass-closeout-plan.md, W1.2). Both share the
defining shape: the handler catches, does not disclose, and returns a value the CLIENT CANNOT
DISTINGUISH from the healthy one.

Both tests below were observed RED on the pre-fix bytes (``git stash`` is forbidden here, so the
red arm was taken with ``git checkout origin/main -- <file>``; the receipts are in the PR body):

  * ``test_audit_manifest_record_failure_is_observable_by_the_mcp_CLIENT`` -- pre-fix, the
    rewrite response carried an ``audit_manifest`` with no ``recorded`` key at all, so the
    assertion failed on ``None is False``. The handler was literally ``except Exception: return``.
  * ``test_session_open_tracked_file_count_degradation_is_disclosed`` -- pre-fix, the payload
    had no ``tracked_file_count_error`` key and ``tracked_file_count`` silently equalled
    ``file_count``, indistinguishable from a correctly computed count.

NEITHER FIX CHANGES THE SUCCESS PATH. Both disclose to the CALLER on the wire (W1.3), on the
failure branch only, and both healthy paths are asserted unchanged below -- "I only touched the
error path" is a claim about behaviour and gets a test like any other. #1 additionally keeps the
RAW reason server-side and puts only the exception CLASS on the wire, matching what
``_sanitized_tool_error`` already does for every other error on this surface.

A3 ROUND 1 REJECTED an earlier #1 that wrote stderr only and asserted the wire payload
unchanged: that arrangement asserts the fail-open rather than the fix. Recorded here because the
rejected version looked more conservative than the correct one, which is what made it tempting.
"""

from __future__ import annotations

import json
import subprocess
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


def test_audit_manifest_record_failure_is_observable_by_the_mcp_CLIENT(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CALLER -- an MCP client on the wire -- must see it, not just the operator.

    A3 round 1 rejected an earlier version of this fix that wrote stderr only and asserted the
    wire payload UNCHANGED: that asserts the fail-open rather than the fix, because
    ``audit_manifest.path`` sitting in a success-shaped rewrite response ASSERTS an audit record
    that does not exist. W1.3 requires the caller observe the failure, so the disclosure is now
    on the wire (`recorded: false` + `record_error`), with the raw reason kept server-side.

    Driven through the PUBLIC tool ``tg_rewrite_apply``, not the private helper -- a test that
    calls `_record_generated_audit_manifest` directly cannot show what the client receives, and
    "the client cannot tell" is the entire finding.
    """

    (tmp_path / "sample.py").write_text("def f(a): return a\n", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    native_payload = {
        "applied": True,
        "audit_manifest": {"path": str(tmp_path / "audit.json"), "file_count": 1},
    }
    monkeypatch.setattr(
        "tensor_grep.cli.mcp_server.resolve_native_tg_binary", lambda *a, **k: Path("tg.exe")
    )
    monkeypatch.setattr(
        "tensor_grep.cli.mcp_rewrite_tools.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=["tg.exe"], returncode=0, stdout=json.dumps(native_payload), stderr=""
        ),
    )
    monkeypatch.setattr("tensor_grep.cli.audit_manifest.record_audit_manifest", _boom)

    parsed = json.loads(
        mcp_server.tg_rewrite_apply(
            pattern="def $F($A): return $A",
            replacement="def $F($A): return $A",
            lang="python",
            path=str(tmp_path),
            audit_manifest="audit.json",
        )
    )

    manifest = parsed.get("audit_manifest", {})
    assert manifest.get("recorded") is False, (
        "the rewrite response advertises an audit_manifest the audit history never recorded, "
        f"and says nothing about it -- this is the fail-open. payload={parsed!r}"
    )
    assert manifest.get("record_error"), f"no reason on the wire: {manifest!r}"
    # Sanitized on the wire (class name only), raw server-side -- same split the rest of this
    # module uses for tool errors. Both halves asserted so neither can quietly disappear.
    assert "W1A_RED2_INJECTED" not in json.dumps(parsed), (
        "raw exception text crossed the MCP wire; it must be sanitized"
    )
    assert "W1A_RED2_INJECTED" in capsys.readouterr().err, (
        "the raw reason is not in the server-side log either -- it was lost entirely"
    )


def test_audit_manifest_helper_marks_failure_on_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit-level companion to the tool-level arm above, pinning the exact keys."""

    monkeypatch.setattr("tensor_grep.cli.audit_manifest.record_audit_manifest", _boom)
    payload: dict[str, Any] = {"audit_manifest": {"path": "manifest.json"}}
    mcp_server._record_generated_audit_manifest(payload)

    assert payload["audit_manifest"]["recorded"] is False
    assert payload["audit_manifest"]["record_error"] == "RuntimeError"


def test_audit_manifest_success_payload_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control for the two arms above: on the healthy path NEITHER key appears, so their
    presence there is caused by the failure and not stamped unconditionally."""

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
    assert "RuntimeError" in payload["tracked_file_count_error"], (
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
