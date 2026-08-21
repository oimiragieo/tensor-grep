"""MCP path-confinement, root override, and security ratchet contracts."""

import asyncio
import json
import os
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from tests.unit.test_mcp_server_shared import (
    _ANCHOR_SPLIT_CASES,
    _AUDIT7_SECRET_MARKER,
    _CONFINEMENT_RATCHET_CASES,
    _PLURAL_CONFINEMENT_RATCHET_CASES,
    _PLURAL_RATCHET_BASE_KWARGS,
    _RATCHET_BASE_KWARGS,
    _READ_PATH_COVERAGE_CASES,
    CONFINEMENT_EXEMPT,
    PLURAL_CONFINEMENT_EXEMPT,
    _assert_audit7_refused_no_leak,
    _call_mcp_tool_text,
    _ratchet_positive_value,
    _tool_array_string_param_names,
    _write_audit7_secret,
    _write_audit_manifest,
)


def test_tg_audit_manifest_verify_refuses_manifest_path_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_audit_manifest_verify(str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_manifest_path_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_audit_manifest_verify("../secret.json")

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_manifest_path_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)
    link = proj / "link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_audit_manifest_verify(str(link))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_previous_manifest_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "rewrite-audit.json"
    _write_audit_manifest(manifest_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_audit_manifest_verify(str(manifest_path), previous_manifest=str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_previous_manifest_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "rewrite-audit.json"
    _write_audit_manifest(manifest_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_audit_manifest_verify(
        str(manifest_path), previous_manifest="../secret.json"
    )

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_previous_manifest_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "rewrite-audit.json"
    _write_audit_manifest(manifest_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)
    link = proj / "prev-link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_audit_manifest_verify(str(manifest_path), previous_manifest=str(link))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_previous_manifest_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    current_path = proj / "current.json"
    _write_audit_manifest(current_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")

    out = mcp_server.tg_audit_diff(str(secret), str(current_path))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_previous_manifest_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    current_path = proj / "current.json"
    _write_audit_manifest(current_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")

    out = mcp_server.tg_audit_diff("../secret.json", str(current_path))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_previous_manifest_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    current_path = proj / "current.json"
    _write_audit_manifest(current_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")
    link = proj / "prev-link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_audit_diff(str(link), str(current_path))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_current_manifest_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    previous_path = proj / "previous.json"
    _write_audit_manifest(previous_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")

    out = mcp_server.tg_audit_diff(str(previous_path), str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_current_manifest_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    previous_path = proj / "previous.json"
    _write_audit_manifest(previous_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")

    out = mcp_server.tg_audit_diff(str(previous_path), "../secret.json")

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_diff_refuses_current_manifest_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    previous_path = proj / "previous.json"
    _write_audit_manifest(previous_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")
    link = proj / "current-link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_audit_diff(str(previous_path), str(link))

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_create_refuses_manifest_path_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_review_bundle_create(manifest_path=str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_create_refuses_manifest_path_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)

    out = mcp_server.tg_review_bundle_create(manifest_path="../secret.json")

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_create_refuses_manifest_path_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret)
    link = proj / "link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_review_bundle_create(manifest_path=str(link))

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_create_refuses_scan_path_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "manifest.json"
    _write_audit_manifest(manifest_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="findings")

    out = mcp_server.tg_review_bundle_create(
        manifest_path=str(manifest_path), scan_path=str(secret)
    )

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_create_refuses_previous_manifest_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "manifest.json"
    _write_audit_manifest(manifest_path)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="reviewer")

    out = mcp_server.tg_review_bundle_create(
        manifest_path=str(manifest_path), previous_manifest=str(secret)
    )

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_verify_refuses_bundle_path_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="bundle_sha256")

    out = mcp_server.tg_review_bundle_verify(str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_verify_refuses_bundle_path_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="bundle_sha256")

    out = mcp_server.tg_review_bundle_verify("../secret.json")

    _assert_audit7_refused_no_leak(out)


def test_tg_review_bundle_verify_refuses_bundle_path_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.json"
    _write_audit7_secret(secret, field="bundle_sha256")
    link = proj / "link.json"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_review_bundle_verify(str(link))

    _assert_audit7_refused_no_leak(out)


# round-7 security (audit #81 #1/#2/#12): MCP read-path exfil cluster ---------------------
#
# tg_classify_logs (file_path) and tg_ruleset_scan (baseline_path/suppressions_path) forwarded
# LLM-supplied read paths straight to a reader/loader with ZERO confinement -- an
# arbitrary-file-read/exfil primitive reachable from any MCP client. tg_classify_logs also
# fully materialized the target file into memory (`list(reader.read_lines(file_path))`)
# BEFORE applying its DEFAULT_CLASSIFY_MAX_LINES budget -- an unbounded-memory DoS on a large
# (or attacker-influenceable) file. tg_audit_manifest_verify's signing_key (HMAC key material)
# was read unrestricted while its twin audit_signing_key on tg_rewrite_apply was already gated
# behind an explicit opt-in (round-5) -- this closes that inconsistency too.
# `_confine_read_path` is the new read-labeled chokepoint (a thin wrapper on
# `_confine_write_path`, which round-6/audit #7 already generalized to reads) so a new
# read-path param has an obvious place to route through instead of being forwarded raw.


def test_confine_read_path_refuses_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    anchor = tmp_path / "proj"
    anchor.mkdir()
    with pytest.raises(ValueError):
        mcp_server._confine_read_path("../evil.log", anchor, label="file_path")
    with pytest.raises(ValueError):
        mcp_server._confine_read_path(str(tmp_path / "evil.log"), anchor, label="file_path")
    ok = mcp_server._confine_read_path("app.log", anchor, label="file_path")
    assert ok == (anchor.resolve() / "app.log")


@pytest.mark.skipif(os.name != "nt", reason="UNC paths are absolute only on Windows")
def test_confine_read_path_refuses_unc_path(tmp_path):
    """A UNC path is absolute (outside any local anchor) and must be refused like any other
    out-of-root absolute path. Uses \\\\localhost\\... (loopback, resolves in milliseconds,
    no real network I/O) rather than an unreachable host, per anti-hang-test-protocol.

    Windows-only (skipif-guarded): a UNC path (``Path(r"\\\\localhost\\...").is_absolute()``)
    is absolute ONLY on Windows. On POSIX it is NOT absolute, so `_confine_write_path` joins it
    UNDER the anchor instead of refusing it -- the confinement CODE is correct on both
    platforms (a UNC string can't escape the anchor on POSIX either way), this test's
    ASSERTION (raises ValueError) is just Windows-specific, so it must not run on
    ubuntu-latest/macos-latest CI legs (audit #81 fix-council item #1)."""
    from tensor_grep.cli import mcp_server

    anchor = tmp_path / "proj"
    anchor.mkdir()
    unc = r"\\localhost\C$\Windows\System32\drivers\etc\hosts"
    with pytest.raises(ValueError):
        mcp_server._confine_read_path(unc, anchor, label="file_path")


def test_tg_classify_logs_refuses_file_path_outside_root(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.log"
    secret.write_text(f"ERROR {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")

    out = mcp_server.tg_classify_logs(str(secret))

    _assert_audit7_refused_no_leak(out)


def test_tg_classify_logs_refuses_file_path_dotdot_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.log"
    secret.write_text(f"ERROR {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")

    out = mcp_server.tg_classify_logs("../secret.log")

    _assert_audit7_refused_no_leak(out)


def test_tg_classify_logs_refuses_file_path_symlink_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    secret = tmp_path / "secret.log"
    secret.write_text(f"ERROR {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")
    link = proj / "app.log"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_classify_logs(str(link))

    _assert_audit7_refused_no_leak(out)


@pytest.mark.skipif(os.name != "nt", reason="UNC paths are absolute only on Windows")
def test_tg_classify_logs_refuses_file_path_unc_escape(tmp_path, monkeypatch):
    """Windows-only (skipif-guarded): passes accidentally on POSIX (a UNC string is not
    `.is_absolute()` there, so it never hits the refusal path the assertion checks for) --
    see test_confine_read_path_refuses_unc_path above for the full rationale. Skipped here too
    for honesty, not just to stop a failure (audit #81 fix-council item #1)."""
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    out = mcp_server.tg_classify_logs(r"\\localhost\C$\Windows\System32\drivers\etc\hosts")

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_input"


def test_tg_classify_logs_accepts_relative_in_root_path(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)
    (tmp_path / "app.log").write_text("INFO ok\nERROR boom\n", encoding="utf-8")

    out = mcp_server.tg_classify_logs("app.log")

    parsed = json.loads(out)
    assert parsed.get("error") is None
    assert parsed["provider"] == "heuristic"


def test_tg_classify_logs_bounds_read_before_materializing(tmp_path, monkeypatch):
    """FAILS pre-fix (`list(reader.read_lines(file_path))` drains the whole generator before
    the DEFAULT_CLASSIFY_MAX_LINES budget is applied -- unbounded-memory DoS on a large file);
    PASSES post-fix (only DEFAULT_CLASSIFY_MAX_LINES + 1 lines are ever pulled from the
    reader). The fake reader below yields a large-but-FINITE number of lines (not an
    unbounded/infinite generator), so a still-broken implementation fails the assertion below
    instead of hanging the test runner (anti-hang-test-protocol)."""
    from tensor_grep.cli import mcp_server
    from tensor_grep.io.reader_fallback import FallbackReader
    from tensor_grep.sidecar import DEFAULT_CLASSIFY_MAX_LINES

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    (proj / "huge.log").write_text("placeholder\n", encoding="utf-8")
    monkeypatch.delenv("TENSOR_GREP_CLASSIFY_PROVIDER", raising=False)

    consumed = {"count": 0}
    fake_total_lines = DEFAULT_CLASSIFY_MAX_LINES * 50  # large but finite

    def _fake_read_lines(self, file_path):
        for _ in range(fake_total_lines):
            consumed["count"] += 1
            yield "INFO line\n"

    monkeypatch.setattr(FallbackReader, "read_lines", _fake_read_lines)

    out = mcp_server.tg_classify_logs("huge.log")

    parsed = json.loads(out)
    assert parsed.get("error") is None
    # the reader must be capped one line past the budget, never drained anywhere near in full.
    assert consumed["count"] == DEFAULT_CLASSIFY_MAX_LINES + 1
    assert consumed["count"] < fake_total_lines
    assert parsed["sample_lines"] == DEFAULT_CLASSIFY_MAX_LINES
    assert parsed["total_lines"] == DEFAULT_CLASSIFY_MAX_LINES + 1


def test_ruleset_scan_refuses_baseline_path_outside_root(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    escape = tmp_path / "evil_baseline.json"
    _write_audit7_secret(escape)

    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), baseline_path=str(escape)
    )

    _assert_audit7_refused_no_leak(out)


def test_ruleset_scan_refuses_baseline_path_dotdot_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    escape = tmp_path / "evil_baseline.json"
    _write_audit7_secret(escape)

    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), baseline_path="../evil_baseline.json"
    )

    _assert_audit7_refused_no_leak(out)


def test_ruleset_scan_refuses_baseline_path_symlink_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    outside_target = tmp_path / "outside-baseline.json"
    _write_audit7_secret(outside_target)
    link_path = scan_root / "baseline.json"
    try:
        link_path.symlink_to(outside_target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), baseline_path="baseline.json"
    )

    _assert_audit7_refused_no_leak(out)


def test_ruleset_scan_refuses_suppressions_path_outside_root(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    escape = tmp_path / "evil_suppressions.json"
    _write_audit7_secret(escape)

    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), suppressions_path=str(escape)
    )

    _assert_audit7_refused_no_leak(out)


def test_ruleset_scan_refuses_suppressions_path_dotdot_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    escape = tmp_path / "evil_suppressions.json"
    _write_audit7_secret(escape)

    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic",
        path=str(scan_root),
        suppressions_path="../evil_suppressions.json",
    )

    _assert_audit7_refused_no_leak(out)


def test_tg_audit_manifest_verify_refuses_signing_key_without_opt_in(tmp_path, monkeypatch):
    """FAILS pre-fix (signing_key forwarded to verify_audit_manifest_json unconditionally, an
    arbitrary-file-read-as-HMAC-key primitive); PASSES post-fix (refused with
    code="unsupported_option" unless TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ=1, mirroring
    tg_rewrite_apply's audit_signing_key gate)."""
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ", raising=False)
    manifest_path = tmp_path / "rewrite-audit.json"
    signing_key_path = tmp_path / "audit.key"
    signing_key = b"top-secret"
    signing_key_path.write_bytes(signing_key)
    _write_audit_manifest(manifest_path, signing_key=signing_key)

    out = mcp_server.tg_audit_manifest_verify(
        str(manifest_path),
        signing_key=str(signing_key_path),
    )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "unsupported_option"


@pytest.mark.parametrize("case", _READ_PATH_COVERAGE_CASES)
def test_read_path_param_coverage_rejects_out_of_root(tmp_path, monkeypatch, case):
    out = case(tmp_path, monkeypatch)

    _assert_audit7_refused_no_leak(out)


# --- positive-path regression guards: confining the four params above must not break a
# legitimate in-root call (Opus adversarial gate on #81, fix-council item #2).


def test_tg_file_imports_accepts_in_root_path(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "util.js").write_text("export function foo() {}\n", encoding="utf-8")
    consumer = tmp_path / "consumer.js"
    consumer.write_text('import { foo } from "./util";\n', encoding="utf-8")

    out = mcp_server.tg_file_imports("consumer.js")

    parsed = json.loads(out)
    assert parsed.get("error") is None
    assert parsed["imports"][0]["module"] == "./util"
    assert parsed["imports"][0]["resolved"] == str((tmp_path / "util.js").resolve())


def test_tg_file_importers_accepts_in_root_path(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    project.mkdir()
    target = project / "util.js"
    target.write_text("export function foo() {}\n", encoding="utf-8")
    consumer = project / "consumer.js"
    consumer.write_text('import { foo } from "./util";\n', encoding="utf-8")
    monkeypatch.chdir(project)

    out = mcp_server.tg_file_importers("util.js", path=str(project))

    parsed = json.loads(out)
    assert parsed.get("error") is None
    assert parsed["importer_files"] == [str(consumer.resolve())]


def test_tg_session_file_importers_accepts_in_root_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    project.mkdir()
    target = project / "util.js"
    target.write_text("export function foo() {}\n", encoding="utf-8")
    consumer = project / "consumer.js"
    consumer.write_text('import { foo } from "./util";\n', encoding="utf-8")

    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]

    out = mcp_server.tg_session_file_importers(session_id, "util.js", str(project))

    parsed = json.loads(out)
    assert parsed.get("error") is None
    assert parsed["importer_files"] == [str(consumer.resolve())]


def test_tg_rewrite_apply_accepts_policy_within_scan_root(tmp_path, monkeypatch):
    """VERIFY confining `policy` (round-7 fix, Opus gate item #2) does not regress a
    legitimate in-root policy: a policy file inside the scan root must reach
    load_apply_policy's OWN schema validation (code="invalid_policy") rather than being
    refused by the new confinement check (which would instead surface code="invalid_input"
    with a "must stay within" message)."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    policy_path = proj / "apply-policy.json"
    policy_path.write_text(
        json.dumps({
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
            # deliberately omit on_failure: pins the failure to load_apply_policy's schema
            # validation, proving execution got PAST the new path-confinement check below.
        }),
        encoding="utf-8",
    )

    out = mcp_server.tg_rewrite_apply(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path=str(proj),
        policy=str(policy_path),
    )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_policy"
    assert any(detail["field"] == "on_failure" for detail in parsed["error"]["details"])


def test_tg_rewrite_apply_accepts_co_located_policy_for_single_file_target(tmp_path, monkeypatch):
    """audit #76 (Opus-gate nit on #464): when `path` is a single FILE (a targeted rewrite),
    a policy co-located in the file's directory must reach load_apply_policy's schema
    validation (code="invalid_policy"), NOT be fail-closed-refused by confinement
    (code="invalid_input"). Pre-fix the policy anchor was the file itself, which has no
    descendants, so any co-located policy was rejected."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    target = proj / "sample.py"
    target.write_text("def add(x, y): return x + y\n", encoding="utf-8")
    policy_path = proj / "apply-policy.json"
    policy_path.write_text(
        json.dumps({
            "version": 1,
            "lint_cmd": None,
            "test_cmd": None,
            "ruleset_scan": None,
            # omit on_failure: pins the failure to load_apply_policy's schema validation,
            # proving execution got PAST the path-confinement check with path=a single file.
        }),
        encoding="utf-8",
    )

    out = mcp_server.tg_rewrite_apply(
        pattern="def $F($$$ARGS): return $EXPR",
        replacement="lambda $$$ARGS: $EXPR",
        lang="python",
        path=str(target),  # a FILE, not a directory
        policy=str(policy_path),
    )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_policy"
    assert any(detail["field"] == "on_failure" for detail in parsed["error"]["details"])


def test_tg_rewrite_apply_still_rejects_policy_outside_single_file_target_dir(tmp_path):
    """audit #76: anchoring the policy to the target FILE's parent directory (so a co-located
    policy works) must NOT widen confinement -- a policy OUTSIDE the target's directory is still
    fail-closed refused (code="invalid_input"), preserving the #464 exfil guard."""
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    target = proj / "sample.py"
    target.write_text("def add(x, y): return x + y\n", encoding="utf-8")
    # sibling of proj/, i.e. OUTSIDE the target file's parent directory
    escape = tmp_path / "outside-policy.json"
    escape.write_text(json.dumps({"version": 1}), encoding="utf-8")

    out = mcp_server.tg_rewrite_apply(
        pattern="x",
        replacement="y",
        lang="python",
        path=str(target),
        policy=str(escape),
    )

    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_input"


def test_tg_orient_confines_path_to_mcp_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    try:
        out = mcp_server.tg_orient(str(outside))
        parsed = json.loads(out)
        assert parsed["error"]["code"] == "invalid_input"
        assert "must stay within" in parsed["error"]["message"]
    finally:
        outside.rmdir()


def test_tg_doctor_confines_path_to_mcp_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    try:
        out = mcp_server.tg_doctor(str(outside), with_lsp=False)
        parsed = json.loads(out)
        assert parsed["error"]["code"] == "invalid_input"
        assert "must stay within" in parsed["error"]["message"]
    finally:
        outside.rmdir()


def test_tg_doctor_confines_config_param(tmp_path, monkeypatch):
    """New hardening beyond the literal ask: `config` is a SECONDARY param that
    `_build_doctor_payload` uses to relocate its `root` (config's parent dir) for every
    downstream diagnostic probe -- unconfined, it is the exact 'secondary anchor derived from
    an unconfined param' bug class #95's gate flagged (see tg_session_file_importers). Confine
    it to the (already-confined) doctor root, mirroring tg_ruleset_scan's baseline_path."""
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    outside_config = tmp_path.parent / f"outside-cfg-{tmp_path.name}" / "sgconfig.yml"
    outside_config.parent.mkdir(exist_ok=True)
    outside_config.write_text("", encoding="utf-8")
    try:
        out = mcp_server.tg_doctor(str(tmp_path), config=str(outside_config), with_lsp=False)
        parsed = json.loads(out)
        assert parsed["error"]["code"] == "invalid_input"
        assert "must stay within" in parsed["error"]["message"]
    finally:
        outside_config.unlink()
        outside_config.parent.rmdir()


# --- round-4 security: MCP write-path confinement (arbitrary file write) -----------
#
# tg_ruleset_scan (write_baseline/write_suppressions) and tg_review_bundle_create
# (output_path) forwarded LLM-supplied paths straight to disk with no confinement —
# an arbitrary-file-write primitive reachable from any MCP client. Writes must stay
# within a per-tool anchor (scan root / cwd) and fail closed otherwise.


def test_confine_write_path_refuses_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    anchor = tmp_path / "proj"
    anchor.mkdir()
    with pytest.raises(ValueError):
        mcp_server._confine_write_path("../evil.json", anchor, label="write_baseline")
    with pytest.raises(ValueError):
        mcp_server._confine_write_path(str(tmp_path / "evil.json"), anchor, label="write_baseline")
    ok = mcp_server._confine_write_path("baseline.json", anchor, label="write_baseline")
    assert ok == (anchor.resolve() / "baseline.json")
    ok2 = mcp_server._confine_write_path("sub/dir/base.json", anchor, label="x")
    assert ok2 == (anchor.resolve() / "sub" / "dir" / "base.json")


def test_ruleset_scan_refuses_write_baseline_escape(tmp_path):
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    (scan_root / "a.py").write_text("x = 1\n", encoding="utf-8")
    escape = tmp_path / "evil_baseline.json"
    out = mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), write_baseline=str(escape)
    )
    parsed = json.loads(out)
    assert parsed.get("error", {}).get("code") == "invalid_input"
    assert not escape.exists()  # fail closed: nothing written outside the scan root


def test_review_bundle_create_refuses_output_path_escape(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    manifest = proj / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(proj)  # cwd = the project anchor
    escape = tmp_path / "evil_bundle.json"
    out = mcp_server.tg_review_bundle_create(manifest_path=str(manifest), output_path=str(escape))
    parsed = json.loads(out)
    assert parsed.get("error", {}).get("code") == "invalid_input"
    assert not escape.exists()


# --- round-5 security: tg_rewrite_apply audit_manifest confinement + consume-resolved,
# audit_signing_key opt-in gate, and O_NOFOLLOW-guarded in-process writes (TOCTOU fix) -----
#
# tg_rewrite_apply's audit_manifest was entirely unconfined (an arbitrary MCP-reachable
# file-write primitive), and the round-4 write-path confinement that DID exist for
# write_baseline/write_suppressions/output_path validated a resolved Path then discarded
# it, forwarding the raw candidate string to the downstream consumer (TOCTOU: the
# validated location and the written location could diverge). This block covers: (1) the
# audit_manifest escape refusal, (2) the audit_signing_key opt-in gate, (3) a confined
# audit_manifest is still written for a rewrite target in a different directory, and (4)
# the O_NOFOLLOW symlink-swap refusal on the in-process ruleset-scan writers, guarding the
# O_TRUNC-not-O_EXCL re-run/overwrite semantics.


def test_rewrite_apply_refuses_audit_manifest_escape(tmp_path, monkeypatch):
    """FAILS pre-fix (audit_manifest unconfined at _build_rewrite_command call site);
    PASSES post-fix (Part A: confined to cwd, refused before any subprocess spawn)."""
    from tensor_grep.cli import mcp_server

    cwd = tmp_path / "proj"
    (cwd / "sub").mkdir(parents=True)
    (cwd / "sub" / "a.py").write_text("foo = 1\n", encoding="utf-8")
    monkeypatch.chdir(cwd)  # cwd is the anchor
    outside = tmp_path / "escape"
    outside.mkdir()
    escaped = outside / "pwned_manifest.json"  # absolute, outside cwd AND outside target
    payload_json, exit_code = mcp_server.execute_rewrite_apply_json(
        pattern="foo",
        replacement="bar",
        lang="python",
        path=str(cwd / "sub"),
        audit_manifest=str(escaped),
    )
    payload = json.loads(payload_json)
    assert exit_code == 1
    assert payload.get("error", {}).get("code") == "invalid_input"
    assert not escaped.exists()  # subprocess never spawned


def test_rewrite_apply_refuses_audit_signing_key_without_opt_in(tmp_path, monkeypatch):
    """FAILS pre-fix (audit_signing_key forwarded to the native binary unconditionally,
    an arbitrary-file-read-as-HMAC-key primitive); PASSES post-fix (Part A: refused with
    code=unsupported_option unless TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ=1)."""
    from tensor_grep.cli import mcp_server

    cwd = tmp_path / "proj"
    cwd.mkdir()
    (cwd / "a.py").write_text("foo = 1\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ", raising=False)
    secret = tmp_path / "outside-secret.key"
    secret.write_text("hmac-secret\n", encoding="utf-8")

    with patch("tensor_grep.cli.mcp_server.subprocess.run") as mock_run:
        payload_json, exit_code = mcp_server.execute_rewrite_apply_json(
            pattern="foo",
            replacement="bar",
            lang="python",
            path=str(cwd),
            audit_signing_key=str(secret),
        )
        mock_run.assert_not_called()  # refused before any subprocess spawn

    payload = json.loads(payload_json)
    assert exit_code == 1
    assert payload.get("error", {}).get("code") == "unsupported_option"


def test_rewrite_apply_writes_confined_audit_manifest(tmp_path, monkeypatch):
    """A confined audit_manifest under cwd must still be written for a rewrite target in
    a DIFFERENT directory (guards the anchor from over-restricting to the rewrite path)."""
    from tensor_grep.cli import mcp_server

    cwd = tmp_path / "proj"
    (cwd / "sub").mkdir(parents=True)
    (cwd / "sub" / "a.py").write_text("foo = 1\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    audit_dir = cwd / "tg_audit"
    audit_dir.mkdir()
    manifest = audit_dir / "manifest.json"  # UNDER the cwd anchor
    resolved_manifest = manifest.resolve()

    payload = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "ast-native",
        "sidecar_used": False,
        "audit_manifest": {
            "path": str(resolved_manifest),
            "file_count": 1,
            "applied_edit_count": 1,
            "signed": False,
            "signature_kind": None,
        },
        "plan": {"total_edits": 1},
        "verification": None,
        "validation": None,
    }
    with (
        patch("tensor_grep.cli.mcp_server.resolve_native_tg_binary", return_value=Path("tg.exe")),
        patch(
            "tensor_grep.cli.mcp_server.subprocess.run",
            return_value=CompletedProcess(
                args=["tg.exe"], returncode=0, stdout=json.dumps(payload), stderr=""
            ),
        ) as mock_run,
    ):
        _payload_json, exit_code = mcp_server.execute_rewrite_apply_json(
            pattern="foo",
            replacement="bar",
            lang="python",
            path=str(cwd / "sub"),  # rewrite target != cwd, legit
            audit_manifest=str(manifest),
        )

    assert exit_code == 0
    # the RESOLVED absolute path reached the native argv, not the raw candidate string.
    assert "--audit-manifest" in mock_run.call_args.args[0]
    idx = mock_run.call_args.args[0].index("--audit-manifest")
    assert mock_run.call_args.args[0][idx + 1] == str(resolved_manifest)


def test_write_json_refuse_symlink_refuses_swap(tmp_path):
    """Direct unit test of the Part-B in-process writer (main.py._write_json_refuse_symlink)
    shared by write_baseline and write_suppressions. FAILS pre-fix (plain
    write_path.write_text(...) blindly follows the symlink, silently overwriting whatever
    it points at); PASSES post-fix (refused via the is_symlink() pre-check -- authoritative
    on Windows, where os.O_NOFOLLOW is unavailable -- and via O_NOFOLLOW on POSIX; the
    outside target is left completely unchanged, not written through)."""
    from tensor_grep.cli import main as cli_main

    outside_target = tmp_path / "outside.json"
    outside_target.write_text("UNCHANGED\n", encoding="utf-8")
    link_path = tmp_path / "baseline.json"
    try:
        link_path.symlink_to(outside_target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    with pytest.raises(ValueError):
        cli_main._write_json_refuse_symlink(link_path, {"fingerprints": ["x"]})
    assert outside_target.read_text(encoding="utf-8") == "UNCHANGED\n"


def test_ruleset_scan_write_baseline_refuses_symlink_swap_end_to_end(tmp_path, monkeypatch):
    """End-to-end: a pre-planted symlink at a confined write_baseline target is refused
    fail-closed through the full tg_ruleset_scan path (confinement resolve() +
    Part-B is_symlink()/O_NOFOLLOW both refuse it), and the symlink's outside target is
    left unchanged (not written through)."""
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    scan_root = tmp_path / "proj"
    scan_root.mkdir()
    monkeypatch.chdir(scan_root)

    (scan_root / "a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")
    outside_target = tmp_path / "outside-baseline.json"  # sibling of scan_root, still in tmp_path
    outside_target.write_text("UNCHANGED\n", encoding="utf-8")
    link_path = scan_root / "baseline.json"
    try:
        link_path.symlink_to(outside_target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    out = mcp_server.tg_ruleset_scan(
        "crypto-safe",
        path=".",
        language="python",
        write_baseline="baseline.json",
    )
    parsed = json.loads(out)
    assert parsed.get("error", {}).get("code") == "invalid_input"
    assert outside_target.read_text(encoding="utf-8") == "UNCHANGED\n"


def test_ruleset_scan_write_baseline_overwrites_on_rerun(monkeypatch, tmp_path):
    """A repeated write to the SAME write_baseline path must succeed and overwrite
    (guards O_CREAT|O_TRUNC|O_NOFOLLOW, not O_EXCL, which would fail the second run)."""
    from tensor_grep.cli import mcp_server
    from tests.unit.test_cli_modes_shared import _FakeAstPipeline, _FakeAstScanner

    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", _FakeAstPipeline)
    monkeypatch.setattr("tensor_grep.io.directory_scanner.DirectoryScanner", _FakeAstScanner)
    monkeypatch.chdir(tmp_path)

    Path("a.py").write_text("hashlib.md5($$$ARGS)\n", encoding="utf-8")

    first = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe", path=".", language="python", write_baseline="baseline.json"
        )
    )
    assert first.get("error") is None
    second = json.loads(
        mcp_server.tg_ruleset_scan(
            "crypto-safe", path=".", language="python", write_baseline="baseline.json"
        )
    )
    assert second.get("error") is None
    baseline_file = Path("baseline.json")
    written = json.loads(baseline_file.read_text(encoding="utf-8"))
    assert written["fingerprints"] == [first["findings"][0]["fingerprint"]]
    # round-5: the write lands under the validated anchor (scan_root == cwd here, path=".").
    assert baseline_file.resolve().parent == tmp_path.resolve()


@pytest.mark.parametrize(
    "tool_name,param_name",
    _CONFINEMENT_RATCHET_CASES,
    ids=[f"{t}.{p}" for t, p in _CONFINEMENT_RATCHET_CASES],
)
def test_mcp_primary_path_confinement_ratchet(
    tool_name, param_name, tmp_path, tmp_path_factory, monkeypatch
):
    """Every non-exempt string param on every registered MCP tool must reject an
    out-of-root candidate AND accept an in-root one (audit #95 gate-corrected ratchet).

    Schema-driven (mcp.list_tools()), not a hand-maintained name-matched list: a NEW tool
    with an unclassified string param fails here (or errors loudly via the assertion
    below) until it is consciously confined or added to CONFINEMENT_EXEMPT with a reason.
    """
    # #102 fold-in (ratchet hermeticity): without this, a REAL TG_MCP_ROOT set in the
    # operator's/CI's own shell environment (not just a monkeypatch-scoped one from another
    # test -- pytest's monkeypatch fixture already auto-reverts those) would silently relocate
    # the confinement anchor away from tmp_path below, so the negative probe's "outside_dir"
    # might land INSIDE the real TG_MCP_ROOT and false-pass, or the positive probe's in-root
    # relative value might land OUTSIDE it and false-fail -- a "passes in CI, false result
    # locally" trap. Hermetic tests must not depend on ambient external environment state.
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert tool_name in _RATCHET_BASE_KWARGS, (
        f"{tool_name} has a non-exempt string param {param_name!r} this ratchet does not "
        "know how to reach. Either confine it (_confine_mcp_path for a primary path/root "
        "param, _confine_write_path/_confine_read_path for a secondary one) and add a "
        "_RATCHET_BASE_KWARGS entry, or add it to CONFINEMENT_EXEMPT with a reason if it "
        "is genuinely not a path."
    )
    base_kwargs = dict(_RATCHET_BASE_KWARGS[tool_name])

    outside_dir = tmp_path_factory.mktemp("ratchet_outside")

    # --- negative: an out-of-root candidate must be refused, fail-closed, structured.
    rejected = _call_mcp_tool_text(tool_name, {**base_kwargs, param_name: str(outside_dir)})
    assert "must stay within" in rejected, (
        f"{tool_name}.{param_name} accepted an out-of-root path without rejecting it "
        f"(response: {rejected[:500]!r}). Confine it via _confine_mcp_path (primary path/"
        "root param) or _confine_write_path/_confine_read_path (secondary param), or add "
        "it to CONFINEMENT_EXEMPT above if it is genuinely not a path."
    )
    try:
        rejected_payload = json.loads(rejected)
    except json.JSONDecodeError:
        rejected_payload = None
    if isinstance(rejected_payload, dict) and isinstance(rejected_payload.get("error"), dict):
        assert rejected_payload["error"].get("code") == "invalid_input"

    # --- positive: an in-root candidate must NOT trip the confinement check. Bidirectional
    # on purpose -- the negative case alone only proves *some* rejection fires; it would
    # stay green even if confinement were entirely absent, as long as some OTHER error
    # happened to fire for an out-of-root value. This half proves the "must stay within"
    # signal specifically tracks confinement, not noise.
    positive_value = _ratchet_positive_value(tool_name, param_name, tmp_path)
    try:
        accepted = _call_mcp_tool_text(tool_name, {**base_kwargs, param_name: positive_value})
    except Exception as exc:
        # A tool may fail for a NON-confinement reason on a given runner: e.g. the ast-grep /
        # tree-sitter deps are absent (Linux CI without ast-grep), so an ast-backed tool
        # (tg_ast_search, tg_ruleset_scan, ...) raises a wrapped ToolError BEFORE it would run.
        # That is NOT a confinement rejection -- confinement rejections RETURN structured text
        # (see the negative probe above), they never raise. So a raised error still satisfies
        # the positive half (the anchor did not reject the in-root path); assert only that it is
        # not specifically the confinement "must stay within" signal.
        accepted = str(exc)
    assert "must stay within" not in accepted, (
        f"{tool_name}.{param_name} rejected an in-root path as if it were out-of-root "
        f"(response: {accepted[:500]!r}); the confinement anchor is probably wrong."
    )


def test_confinement_exempt_allowlist_has_no_unused_entries():
    """Every CONFINEMENT_EXEMPT entry must correspond to a real param somewhere in the live
    schema -- otherwise a stale allowlist entry could mask a real future gap. (inline_rules
    shipped as a real tg_ruleset_scan param in audit #95 Part 2; no forward-looking
    reservation remains.)"""
    from tensor_grep.cli import mcp_server

    all_param_names: set[str] = set()
    for tool in asyncio.run(mcp_server.mcp.list_tools()):
        all_param_names.update(tool.inputSchema.get("properties", {}))

    stale = set(CONFINEMENT_EXEMPT) - all_param_names
    assert not stale, f"CONFINEMENT_EXEMPT has stale/unused entries: {sorted(stale)}"


def test_mcp_root_defaults_to_cwd(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert mcp_server._mcp_root() == tmp_path.resolve()


def test_mcp_root_empty_env_treated_as_unset(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", "")
    monkeypatch.chdir(tmp_path)

    assert mcp_server._mcp_root() == tmp_path.resolve()


def test_mcp_root_whitespace_env_treated_as_unset(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", "   ")
    monkeypatch.chdir(tmp_path)

    assert mcp_server._mcp_root() == tmp_path.resolve()


def test_mcp_root_honors_valid_override(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    override = tmp_path / "override-root"
    override.mkdir()
    monkeypatch.setenv("TG_MCP_ROOT", str(override))

    assert mcp_server._mcp_root() == override.resolve()


def test_mcp_root_falls_back_to_cwd_on_nonexistent_override(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("TG_MCP_ROOT", str(missing))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert mcp_server._mcp_root() == cwd.resolve()


def test_mcp_root_falls_back_to_cwd_when_override_is_a_file(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    a_file = tmp_path / "not-a-dir.txt"
    a_file.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TG_MCP_ROOT", str(a_file))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert mcp_server._mcp_root() == cwd.resolve()


def test_confine_mcp_path_uses_mcp_root_override(tmp_path, monkeypatch):
    """TG_MCP_ROOT relocates the primary-path confinement anchor for a real tool call --
    a path outside cwd but inside the configured override must now be ACCEPTED."""
    from tensor_grep.cli import mcp_server

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    override_root = tmp_path / "fleet-root"
    other_repo = override_root / "other-repo"
    other_repo.mkdir(parents=True)
    monkeypatch.setenv("TG_MCP_ROOT", str(override_root))
    monkeypatch.chdir(cwd)

    # other_repo is outside cwd but inside the TG_MCP_ROOT override -- must be accepted.
    out = mcp_server.tg_repo_map(str(other_repo))
    parsed = json.loads(out)
    assert parsed.get("error") is None

    # A path outside the override entirely must still be refused.
    outside_override = tmp_path / "outside-override"
    outside_override.mkdir()
    refused = mcp_server.tg_repo_map(str(outside_override))
    refused_parsed = json.loads(refused)
    assert refused_parsed["error"]["code"] == "invalid_input"
    assert "must stay within" in refused_parsed["error"]["message"]


@pytest.mark.parametrize(
    "tool_name,param_name,base_kwargs",
    _ANCHOR_SPLIT_CASES,
    ids=[f"{t}.{p}" for t, p, _ in _ANCHOR_SPLIT_CASES],
)
def test_round8_residual_cwd_params_move_with_tg_mcp_root(
    tool_name, param_name, base_kwargs, tmp_path, monkeypatch
):
    """The residual params still hardcoded to Path.cwd() as of the #95 gate report must be
    fixed to anchor at _mcp_root() -- an operator who relocates TG_MCP_ROOT to point an MCP
    server at a fleet repo other than its own cwd must get the SAME relocated confinement on
    these params as every primary path/root param already gets."""
    real_root = tmp_path / "real_root"
    real_root.mkdir()
    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir()

    monkeypatch.setenv("TG_MCP_ROOT", str(real_root))
    monkeypatch.chdir(other_cwd)  # cwd != TG_MCP_ROOT so a leftover cwd anchor is caught.

    # ABSOLUTE path, not relative: a bare relative filename resolves safely under ANY anchor
    # (anchor / "target.json" always stays "within" whatever the anchor happens to be), so it
    # cannot distinguish the old Path.cwd()-anchored behavior from the fixed _mcp_root()
    # behavior. An absolute path inside real_root but outside other_cwd can: it is only
    # accepted when the anchor is really real_root (mirrors test_confine_mcp_path_uses_
    # mcp_root_override's own absolute-path probe style above).
    in_root_target = real_root / "target.json"
    in_root_target.write_text("{}", encoding="utf-8")

    kwargs = {**base_kwargs, param_name: str(in_root_target)}
    result = _call_mcp_tool_text(tool_name, kwargs)

    assert "must stay within" not in result, (
        f"{tool_name}.{param_name} rejected a path inside TG_MCP_ROOT while cwd differed from "
        f"TG_MCP_ROOT -- still anchored to Path.cwd() instead of _mcp_root() "
        f"(response: {result[:400]!r})"
    )

    # And a path outside BOTH cwd and TG_MCP_ROOT must still be refused -- this proves the
    # positive case above is really exercising confinement, not an accidental no-op check.
    outside = tmp_path / "outside_both"
    outside.mkdir()
    (outside / "target.json").write_text("{}", encoding="utf-8")
    rejected = _call_mcp_tool_text(
        tool_name, {**base_kwargs, param_name: str(outside / "target.json")}
    )
    assert "must stay within" in rejected, (
        f"{tool_name}.{param_name} accepted a path outside TG_MCP_ROOT (response: "
        f"{rejected[:400]!r})"
    )


def test_plural_confinement_exempt_allowlist_has_no_unused_entries():
    """Mirrors test_confinement_exempt_allowlist_has_no_unused_entries for the plural
    allowlist -- every PLURAL_CONFINEMENT_EXEMPT entry must correspond to a real array<string>
    param somewhere in the live schema."""
    from tensor_grep.cli import mcp_server

    all_array_param_names: set[str] = set()
    for tool in asyncio.run(mcp_server.mcp.list_tools()):
        all_array_param_names.update(_tool_array_string_param_names(tool))

    stale = set(PLURAL_CONFINEMENT_EXEMPT) - all_array_param_names
    assert not stale, f"PLURAL_CONFINEMENT_EXEMPT has stale/unused entries: {sorted(stale)}"


def test_plural_confinement_ratchet_has_at_least_one_live_case():
    """Guard against the ratchet silently enumerating zero cases (a schema change that
    renamed/removed workspace_roots would otherwise make this whole ratchet a no-op)."""
    assert ("tg_query", "workspace_roots") in _PLURAL_CONFINEMENT_RATCHET_CASES


@pytest.mark.parametrize(
    "tool_name,param_name",
    _PLURAL_CONFINEMENT_RATCHET_CASES,
    ids=[f"{t}.{p}" for t, p in _PLURAL_CONFINEMENT_RATCHET_CASES],
)
def test_mcp_plural_path_confinement_ratchet(
    tool_name, param_name, tmp_path, tmp_path_factory, monkeypatch
):
    """Every non-exempt array<string> path param on every registered MCP tool must: (a)
    refuse the WHOLE call, fail-closed, if ANY element escapes the confinement root -- never
    silently drop the bad element and proceed with the rest; (b) accept a list of entirely
    in-root elements.

    Schema-driven (mcp.list_tools()), like ratchet A -- a NEW array<string> param on any tool
    fails here until it is consciously confined (per-element) and added to
    _PLURAL_RATCHET_BASE_KWARGS, or exempted in PLURAL_CONFINEMENT_EXEMPT with a reason.
    """
    monkeypatch.delenv("TG_MCP_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert tool_name in _PLURAL_RATCHET_BASE_KWARGS, (
        f"{tool_name} has a non-exempt array<string> param {param_name!r} this ratchet does "
        "not know how to reach. Either confine each element (via _confine_mcp_path) and add "
        "a _PLURAL_RATCHET_BASE_KWARGS entry, or add it to PLURAL_CONFINEMENT_EXEMPT with a "
        "reason if it is genuinely not a path list."
    )
    base_kwargs = dict(_PLURAL_RATCHET_BASE_KWARGS[tool_name])

    outside_dir = tmp_path_factory.mktemp("plural_ratchet_outside")
    in_root_dir = tmp_path / "plural_ratchet_inroot"
    in_root_dir.mkdir()

    # --- negative: ONE escaping element among otherwise-good elements must refuse the WHOLE
    # call, not silently drop the bad element and return partial/best-effort results for the
    # rest.
    rejected = _call_mcp_tool_text(
        tool_name, {**base_kwargs, param_name: [str(in_root_dir), str(outside_dir)]}
    )
    assert "must stay within" in rejected, (
        f"{tool_name}.{param_name} accepted a list containing an out-of-root element without "
        f"rejecting the WHOLE call (response: {rejected[:500]!r})."
    )
    try:
        rejected_payload = json.loads(rejected)
    except json.JSONDecodeError:
        rejected_payload = None
    if isinstance(rejected_payload, dict):
        assert "results_by_root" not in rejected_payload, (
            f"{tool_name}.{param_name} returned PARTIAL results_by_root alongside a "
            "confinement rejection -- the whole call must fail closed, never best-effort."
        )
        if isinstance(rejected_payload.get("error"), dict):
            assert rejected_payload["error"].get("code") == "invalid_input"

    # --- positive: an all-in-root list must not trip the confinement check.
    accepted = _call_mcp_tool_text(tool_name, {**base_kwargs, param_name: [str(in_root_dir)]})
    assert "must stay within" not in accepted, (
        f"{tool_name}.{param_name} rejected an all-in-root list as if an element were "
        f"out-of-root (response: {accepted[:500]!r}); the confinement anchor is probably wrong."
    )
