"""Shared helpers for tests/unit/test_mcp_server_*.py siblings."""

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.core.result import MatchLine, SearchResult


def _canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return json.dumps(canonical, indent=2, sort_keys=True).encode("utf-8")


def _write_audit_manifest(
    path: Path,
    *,
    previous_manifest_sha256: str | None = None,
    project_root: Path | None = None,
    signing_key: bytes | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-03-23T12:00:00Z",
        "lang": "python",
        "path": str(project_root or path.parent),
        "plan_total_edits": 1,
        "applied_edit_ids": ["edit-1"],
        "checkpoint": None,
        "validation": None,
        "files": [
            {
                "path": "src/sample.py",
                "edit_ids": ["edit-1"],
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
            }
        ],
        "previous_manifest_sha256": previous_manifest_sha256,
    }
    payload["manifest_sha256"] = hashlib.sha256(_canonical_manifest_bytes(payload)).hexdigest()
    if signing_key is not None:
        payload["signature"] = {
            "kind": "hmac-sha256",
            "key_path": str(path.with_suffix(".key")),
            "value": hmac.new(
                signing_key,
                _canonical_manifest_bytes(payload),
                hashlib.sha256,
            ).hexdigest(),
        }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _write_scan_results(path: Path) -> dict[str, object]:
    payload = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "builtin-ruleset-scan",
        "sidecar_used": False,
        "ruleset": "auth-safe",
        "rule_count": 1,
        "matched_rules": 1,
        "total_matches": 1,
        "findings": [
            {
                "rule_id": "python-eval",
                "language": "python",
                "severity": "high",
                "matches": 1,
                "files": ["src/sample.py"],
                "evidence": [{"file": "src/sample.py", "match_count": 1}],
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _assert_audit_manifest_envelope(payload: dict[str, object], *, routing_reason: str) -> None:
    assert payload["version"] == 1
    assert payload["routing_backend"] == "AuditManifest"
    assert payload["routing_reason"] == routing_reason
    assert payload["sidecar_used"] is False


def _assert_enriched_edit_plan_seed(
    edit_plan_seed: dict[str, object],
    *,
    primary_file: Path | None = None,
    primary_symbol_name: str | None = None,
) -> None:
    if primary_file is not None:
        assert edit_plan_seed["primary_file"] == str(primary_file.resolve())
    else:
        assert isinstance(edit_plan_seed["primary_file"], str)
    if primary_symbol_name is not None:
        assert edit_plan_seed["primary_symbol"]["name"] == primary_symbol_name
    else:
        assert isinstance(edit_plan_seed["primary_symbol"]["name"], str)
    assert {"start_line", "end_line"} <= set(edit_plan_seed["primary_span"])
    assert edit_plan_seed["primary_span"]["start_line"] >= 1
    assert (
        edit_plan_seed["primary_span"]["end_line"] >= edit_plan_seed["primary_span"]["start_line"]
    )
    assert isinstance(edit_plan_seed["related_spans"], list)
    for related_span in edit_plan_seed["related_spans"]:
        assert {"file", "symbol", "start_line", "end_line", "depth", "score", "reasons"} <= set(
            related_span
        )
        assert related_span["end_line"] >= related_span["start_line"]
    assert isinstance(edit_plan_seed["dependent_files"], list)
    assert isinstance(edit_plan_seed["edit_ordering"], list)
    if primary_file is not None:
        assert edit_plan_seed["edit_ordering"][0] == str(primary_file.resolve())
    else:
        assert all(isinstance(path, str) for path in edit_plan_seed["edit_ordering"])
    # H6 audit: `rollback_risk` is always `round(min(1.0, max(0.0, risk)), 3)`
    # (repo_map.py:13489-13512) -- a `0.0 <= x <= 1.0` bound check can never fail; the
    # clamp is proven load-bearing by
    # test_edit_plan_seed.py::test_rollback_risk_clamp_is_load_bearing_{upper,lower}_bound.
    # This helper is shared across call sites with differing fixtures (8 in this file), so
    # assert the property it CAN check: the field is really a float.
    assert isinstance(edit_plan_seed["rollback_risk"], float)
    assert {
        "import_resolution_quality",
        "parser_backed_count",
        "heuristic_count",
    } <= set(edit_plan_seed["dependency_trust"])
    assert edit_plan_seed["dependency_trust"]["import_resolution_quality"] in {
        "strong",
        "moderate",
        "weak",
    }
    assert edit_plan_seed["dependency_trust"]["parser_backed_count"] >= 0
    assert edit_plan_seed["dependency_trust"]["heuristic_count"] >= 0
    assert isinstance(edit_plan_seed["plan_trust_summary"], str)
    assert edit_plan_seed["plan_trust_summary"]
    assert isinstance(edit_plan_seed["validation_plan"], list)
    for step in edit_plan_seed["validation_plan"]:
        assert {"command", "scope", "runner", "confidence", "detection"} <= set(step)
        assert step["scope"] in {"symbol", "file", "repo"}
        assert step["detection"] in {"detected", "heuristic", "generic"}
        # H6 audit: step confidence is always `round(min(1.0, max(0.0, confidence)), 3)`
        # (repo_map.py:12013-12030, same clamp shape proven load-bearing by
        # test_edit_plan_seed.py::test_confidence_from_score_clamp_is_load_bearing) -- a
        # `0.0 <= x <= 1.0` bound check can never fail. This helper is shared across
        # 8 differing fixtures, so assert the property it CAN check.
        assert isinstance(step["confidence"], float)


def _assert_navigation_pack(
    navigation_pack: dict[str, object],
    *,
    primary_file: Path | None = None,
    primary_symbol_name: str | None = None,
) -> None:
    assert {
        "primary_target",
        "follow_up_reads",
        "parallel_read_groups",
        "related_tests",
        "validation_commands",
        "edit_ordering",
        "rollback_risk",
    } <= set(navigation_pack)
    primary_target = navigation_pack["primary_target"]
    assert {"file", "symbol", "start_line", "end_line", "mention_ref", "reasons"} <= set(
        primary_target
    )
    if primary_file is not None:
        assert primary_target["file"] == str(primary_file.resolve())
    else:
        assert isinstance(primary_target["file"], str)
    if primary_symbol_name is not None:
        assert primary_target["symbol"] == primary_symbol_name
    else:
        assert isinstance(primary_target["symbol"], str)
    assert primary_target["mention_ref"].startswith(primary_target["file"])
    assert "#L" in primary_target["mention_ref"]
    assert isinstance(navigation_pack["follow_up_reads"], list)
    assert navigation_pack["follow_up_reads"]
    for item in navigation_pack["follow_up_reads"]:
        assert {
            "file",
            "symbol",
            "start_line",
            "end_line",
            "mention_ref",
            "role",
            "rationale",
        } <= set(item)
        assert item["mention_ref"].startswith(item["file"])
        assert "#L" in item["mention_ref"]
        assert item["role"] in {"primary", "related", "test"}
    assert isinstance(navigation_pack["related_tests"], list)
    assert isinstance(navigation_pack["validation_commands"], list)
    assert navigation_pack["validation_commands"]
    assert isinstance(navigation_pack["parallel_read_groups"], list)
    assert navigation_pack["parallel_read_groups"]
    expected_phase = 0
    for group in navigation_pack["parallel_read_groups"]:
        assert {"phase", "label", "can_parallelize", "mentions", "files", "roles"} <= set(group)
        assert group["phase"] == expected_phase
        expected_phase += 1
        assert group["label"] in {"primary", "related", "test"}
        assert isinstance(group["can_parallelize"], bool)
        assert isinstance(group["mentions"], list)
        assert group["mentions"]
        assert isinstance(group["files"], list)
        assert group["files"]
        assert isinstance(group["roles"], list)
        assert group["roles"]
    assert isinstance(navigation_pack["edit_ordering"], list)
    # H6 audit: same clamp as edit_plan_seed['rollback_risk'] above -- unlike that shared
    # helper, `_assert_navigation_pack` has exactly one call site in this file with one
    # deterministic fixture, so pin the exact value (verified 3x): 0.0.
    assert navigation_pack["rollback_risk"] == 0.0


def _without_profiling(payload: dict[str, object]) -> dict[str, object]:
    cleaned = dict(payload)
    cleaned.pop("_profiling", None)
    cleaned.pop("profile", None)
    cleaned.pop("session_timing", None)
    return cleaned


def _mcp_tool_names() -> set[str]:
    from tensor_grep.cli import mcp_server

    return {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}


def _call_mcp_tool_text(name: str, arguments: dict[str, object]) -> str:
    from tensor_grep.cli import mcp_server

    content, data = asyncio.run(mcp_server.mcp.call_tool(name, arguments))
    assert data["result"] == content[0].text
    return content[0].text


def _tg_search_rank_fixture():
    """Shared Pipeline/DirectoryScanner mock producing 2 matches for --rank/--semantic tests."""
    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        matched_file_paths=["a.log"],
        total_files=1,
        total_matches=1,
    )
    return fake_backend


# round-6 security (audit #7): tg_audit_manifest_verify, tg_audit_diff,
# tg_review_bundle_create, and tg_review_bundle_verify read caller-supplied JSON paths and
# echo their contents (or content-derived diffs/checksums/fields) back into the tool
# result. Unconfined, any of those 9 read-path params (manifest_path/scan_path/
# previous_manifest x2 across the 4 tools, current_manifest, bundle_path) is an
# arbitrary-file-read/exfil primitive reachable from any MCP client (e.g.
# manifest_path=~/.config/service-account.json). Each param must now resolve inside the
# project root (cwd) -- refusing an absolute path outside it, a "../" escape, AND a
# symlink planted inside the root that resolves to a target outside it -- and the refused
# response must never contain the target file's bytes. The "in-root path still works" case
# is covered by the (now cwd-anchored) tests above: test_tg_audit_manifest_verify_
# supports_signed_manifests, test_tg_audit_diff_matches_cli_json_schema,
# test_tg_review_bundle_create_matches_bundle_schema, and test_tg_review_bundle_verify_
# reports_invalid_integrity.

_AUDIT7_SECRET_MARKER = "SECRET_MARKER_AUDIT7_EXFIL_PROBE"


def _write_audit7_secret(path, *, field: str = "kind") -> None:
    path.write_text(json.dumps({field: _AUDIT7_SECRET_MARKER}), encoding="utf-8")


def _assert_audit7_refused_no_leak(out: str) -> None:
    parsed = json.loads(out)
    assert parsed["error"]["code"] == "invalid_input"
    assert _AUDIT7_SECRET_MARKER not in out


# --- round-7 coverage: enumerate every MCP read-path tool param and assert each rejects an
# out-of-root candidate (audit #81's "coverage test" recommendation). Covers both the
# round-6 (audit #7) params and the round-7 (audit #81) params added above -- if a future
# read-path param is added without confinement, add a case here too.


def _read_path_case_classify_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "secret.log"
    escape.write_text(f"ERROR {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")
    return mcp_server.tg_classify_logs(str(escape))


def _read_path_case_ruleset_scan_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    escape = tmp_path / "baseline.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), baseline_path=str(escape)
    )


def _read_path_case_ruleset_scan_suppressions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    scan_root = tmp_path / "scan"
    scan_root.mkdir()
    escape = tmp_path / "suppressions.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_ruleset_scan(
        ruleset="secrets-basic", path=str(scan_root), suppressions_path=str(escape)
    )


def _read_path_case_audit_manifest_verify_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "manifest.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_audit_manifest_verify(str(escape))


def _read_path_case_audit_manifest_verify_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "manifest.json"
    _write_audit_manifest(manifest_path)
    escape = tmp_path / "previous.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_audit_manifest_verify(str(manifest_path), previous_manifest=str(escape))


def _read_path_case_audit_diff_previous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    current_path = proj / "current.json"
    _write_audit_manifest(current_path)
    escape = tmp_path / "previous.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_audit_diff(str(escape), str(current_path))


def _read_path_case_audit_diff_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    previous_path = proj / "previous.json"
    _write_audit_manifest(previous_path)
    escape = tmp_path / "current.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_audit_diff(str(previous_path), str(escape))


def _read_path_case_review_bundle_create_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "manifest.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_review_bundle_create(manifest_path=str(escape))


def _read_path_case_review_bundle_create_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "manifest.json"
    _write_audit_manifest(manifest_path)
    escape = tmp_path / "scan.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_review_bundle_create(
        manifest_path=str(manifest_path), scan_path=str(escape)
    )


def _read_path_case_review_bundle_create_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    manifest_path = proj / "manifest.json"
    _write_audit_manifest(manifest_path)
    escape = tmp_path / "previous.json"
    _write_audit7_secret(escape)
    return mcp_server.tg_review_bundle_create(
        manifest_path=str(manifest_path), previous_manifest=str(escape)
    )


def _read_path_case_review_bundle_verify_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "bundle.json"
    _write_audit7_secret(escape, field="bundle_sha256")
    return mcp_server.tg_review_bundle_verify(str(escape))


# --- round-7 coverage gap (Opus adversarial gate on #81, fix-council item #2): the #74 file-
# dependency primitives (tg_file_imports/tg_file_importers/tg_session_file_importers) and
# tg_rewrite_apply's `policy` param were missed by the original round-7 sweep above -- same
# class (a caller-named read path forwarded unconfined, echoing file existence / import
# strings / policy-schema details back to the caller). Closed the same way: confine-then-
# forward through `_confine_read_path`, structured invalid_input on reject.


def _read_path_case_file_imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "secret.py"
    escape.write_text(f"# {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")
    return mcp_server.tg_file_imports(str(escape))


def _read_path_case_file_importers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    escape = tmp_path / "secret.py"
    escape.write_text(f"# {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")
    return mcp_server.tg_file_importers(str(escape), path=str(proj))


def _read_path_case_session_file_importers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    # round-8 (audit #95): tg_session_open's `path` is now confined to the MCP root (cwd);
    # chdir to tmp_path so `project` (a subdirectory) is in-root while `escape` (a SIBLING of
    # project, still under tmp_path/cwd) stays correctly outside the session_root=project
    # anchor this case is actually testing.
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("x = 1\n", encoding="utf-8")
    opened = json.loads(mcp_server.tg_session_open(str(project)))
    session_id = opened["session_id"]
    escape = tmp_path / "secret.py"
    escape.write_text(f"# {_AUDIT7_SECRET_MARKER}\n", encoding="utf-8")
    return mcp_server.tg_session_file_importers(session_id, str(escape), str(project))


def _read_path_case_rewrite_apply_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    from tensor_grep.cli import mcp_server

    proj = tmp_path / "proj"
    proj.mkdir()
    escape = tmp_path / "policy.json"
    _write_audit7_secret(escape, field="version")
    return mcp_server.tg_rewrite_apply(
        pattern="x", replacement="y", lang="python", path=str(proj), policy=str(escape)
    )


_READ_PATH_COVERAGE_CASES = [
    pytest.param(_read_path_case_classify_logs, id="tg_classify_logs.file_path"),
    pytest.param(_read_path_case_ruleset_scan_baseline, id="tg_ruleset_scan.baseline_path"),
    pytest.param(_read_path_case_ruleset_scan_suppressions, id="tg_ruleset_scan.suppressions_path"),
    pytest.param(
        _read_path_case_audit_manifest_verify_manifest,
        id="tg_audit_manifest_verify.manifest_path",
    ),
    pytest.param(
        _read_path_case_audit_manifest_verify_previous,
        id="tg_audit_manifest_verify.previous_manifest",
    ),
    pytest.param(_read_path_case_audit_diff_previous, id="tg_audit_diff.previous_manifest"),
    pytest.param(_read_path_case_audit_diff_current, id="tg_audit_diff.current_manifest"),
    pytest.param(
        _read_path_case_review_bundle_create_manifest,
        id="tg_review_bundle_create.manifest_path",
    ),
    pytest.param(_read_path_case_review_bundle_create_scan, id="tg_review_bundle_create.scan_path"),
    pytest.param(
        _read_path_case_review_bundle_create_previous,
        id="tg_review_bundle_create.previous_manifest",
    ),
    pytest.param(
        _read_path_case_review_bundle_verify_bundle, id="tg_review_bundle_verify.bundle_path"
    ),
    pytest.param(_read_path_case_file_imports, id="tg_file_imports.file"),
    pytest.param(_read_path_case_file_importers, id="tg_file_importers.file"),
    pytest.param(_read_path_case_session_file_importers, id="tg_session_file_importers.file"),
    pytest.param(_read_path_case_rewrite_apply_policy, id="tg_rewrite_apply.policy"),
]


# ============================================================================================
# round-8 ratchet (audit #95 gate-corrected version): every MCP tool's PRIMARY path/root
# param was UNCONFINED (only secondary params like manifest_path/baseline_path/policy were
# confined by the round-6/7 work above) -- an arbitrary-directory READ (and, on the rewrite/
# checkpoint family, WRITE) primitive reachable from any MCP client. `_mcp_root()`/
# `_confine_mcp_path()` (mcp_server.py) close this. This ratchet enumerates the LIVE
# registered schema (mcp.list_tools()), NOT a hand-maintained name-matched list like
# _READ_PATH_COVERAGE_CASES above -- a name-matched list only catches an escape on a param
# someone remembered to add a case for. A future tool with an unclassified string param
# (e.g. named "directory"/"target") FAILS this test until it is consciously confined (a real
# _confine_mcp_path/_confine_write_path/_confine_read_path call, plus a _RATCHET_BASE_KWARGS
# entry below) or allowlisted with a documented, genuinely-non-path reason.
# ============================================================================================

# Every string/string|None param NAME that is not a filesystem path, keyed by parameter name
# (not tool) since the same name means the same thing everywhere it appears in this file. Two
# exemption REASONS show up: (1) genuinely not a path (an identifier, pattern, or enum-like
# mode name); (2) deliberately gated by a DIFFERENT mechanism than path confinement (an
# operator opt-in env var) where confining it would be wrong, not a gap.
CONFINEMENT_EXEMPT: dict[str, str] = {
    "pattern": "a regex/literal search pattern, not a path",
    "query": "a free-text ranking query, not a path",
    "symbol": "an exact symbol name to resolve, not a path",
    "session_id": "an opaque session identifier, not a path",
    "lang": "a tree-sitter language name, not a path",
    "ruleset": "a built-in ruleset NAME resolved via resolve_rule_pack, not a path",
    "replacement": "a rewrite template string, not a path",
    "glob": "a glob pattern fragment (tg_search), not a path",
    "type_filter": "a file-type filter token e.g. 'py' (tg_search), not a path",
    "file_type": (
        "a file-type filter token e.g. 'py' (tg_ruleset_scan's sibling of type_filter), not a path"
    ),
    "language": "a ruleset language override name, not a path",
    "justification": "free-text audit-suppression rationale, not a path",
    "model": "a model name used for local token estimation, not a path",
    "provider": "a semantic-provider mode name (native/lsp/hybrid), not a path",
    "render_profile": "an enum-like render mode name (full/compact/llm), not a path",
    "inline_rules": (
        "a string of inline ast-grep rule YAML (tg_ruleset_scan, mirrors CLI --inline-rules), "
        "not a path -- parsed via _load_inline_rule_specs with zero file I/O; length-bounded "
        "by _MAX_INLINE_RULES_CHARS to blunt a YAML anchor/alias expansion-bomb before it ever "
        "reaches the parser"
    ),
    "signing_key": (
        "a READ of secret HMAC key material, deliberately gated by the "
        "TG_MCP_ALLOW_AUDIT_SIGNING_KEY_READ opt-in env var instead of path confinement -- "
        "operators legitimately keep HMAC keys outside the repo (e.g. ~/.config)"
    ),
    "audit_signing_key": (
        "tg_rewrite_apply's sibling of signing_key above; same opt-in-env-var gate, not "
        "path confinement"
    ),
    "checkpoint_id": "an opaque checkpoint identifier, not a path",
    "expected_plan_digest": "a hex content digest from a prior tg_rewrite_plan call, not a path",
    "lint_cmd": (
        "a shell command string, refused outright unless TG_MCP_ALLOW_VALIDATION_COMMANDS=1 "
        "(a stronger, independent gate) -- not a path, and not confinable as one"
    ),
    "test_cmd": "tg_rewrite_apply's sibling of lint_cmd above; same validation-commands gate",
    # #98 (MCP consolidation Phase-1): the 10 meta-tools' shared dispatch selector -- an
    # enum-like action name (e.g. "defs"/"scan"/"apply"), never a path.
    "action": "the meta-tool's dispatch action selector (e.g. 'defs'/'scan'/'apply'), not a path",
}

# Minimal valid kwargs per tool so a targeted param's confinement check is actually REACHED
# during the test call instead of short-circuiting on an earlier missing-required-arg or
# unrelated validation error. None of these values need to exist on disk -- confinement is
# pure path resolution/ancestry, never an existence check -- except where a tool's OWN
# downstream loader reads the file directly with no FileNotFoundError guard (see
# _ratchet_positive_value below for the two params that need a real file).
_RATCHET_BASE_KWARGS: dict[str, dict[str, object]] = {
    "tg_ruleset_scan": {"ruleset": "secrets-basic", "path": "."},
    "tg_repo_map": {"path": "."},
    "tg_orient": {"path": "."},
    "tg_doctor": {"path": "."},
    "tg_context_pack": {"query": "x", "path": "."},
    "tg_edit_plan": {"query": "x", "path": "."},
    "tg_context_render": {"query": "x", "path": "."},
    "tg_agent_capsule": {"query": "x", "path": "."},
    "tg_session_edit_plan": {"session_id": "nonexistent-session", "query": "x", "path": "."},
    "tg_session_context_render": {
        "session_id": "nonexistent-session",
        "query": "x",
        "path": ".",
    },
    "tg_session_blast_radius": {
        "session_id": "nonexistent-session",
        "symbol": "Foo",
        "path": ".",
    },
    "tg_session_file_importers": {
        "session_id": "nonexistent-session",
        "file": "dummy.py",
        "path": ".",
    },
    "tg_symbol_blast_radius_plan": {"symbol": "Foo", "path": "."},
    "tg_session_blast_radius_render": {
        "session_id": "nonexistent-session",
        "symbol": "Foo",
        "path": ".",
    },
    "tg_session_blast_radius_plan": {
        "session_id": "nonexistent-session",
        "symbol": "Foo",
        "path": ".",
    },
    "tg_symbol_defs": {"symbol": "Foo", "path": "."},
    "tg_symbol_source": {"symbol": "Foo", "path": "."},
    "tg_symbol_impact": {"symbol": "Foo", "path": "."},
    "tg_symbol_refs": {"symbol": "Foo", "path": "."},
    "tg_symbol_callers": {"symbol": "Foo", "path": "."},
    "tg_file_imports": {"file": "dummy.py"},
    "tg_file_importers": {"file": "dummy.py", "path": "."},
    "tg_symbol_blast_radius": {"symbol": "Foo", "path": "."},
    "tg_symbol_blast_radius_render": {"symbol": "Foo", "path": "."},
    "tg_search": {"pattern": "x", "path": "."},
    "tg_ast_search": {"pattern": "x", "lang": "python", "path": "."},
    "tg_find": {"query": "x", "path": "."},
    "tg_classify_logs": {"file_path": "dummy.log"},
    "tg_index_search": {"pattern": "x", "path": "."},
    "tg_rewrite_plan": {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
    "tg_rewrite_apply": {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
    "tg_audit_manifest_verify": {"manifest_path": "manifest.json"},
    "tg_audit_history": {"path": "."},
    "tg_audit_diff": {
        "previous_manifest": "previous.json",
        "current_manifest": "current.json",
    },
    "tg_review_bundle_create": {"manifest_path": "manifest.json"},
    "tg_review_bundle_verify": {"bundle_path": "bundle.json"},
    "tg_checkpoint_create": {"path": "."},
    "tg_checkpoint_list": {"path": "."},
    "tg_checkpoint_undo": {"checkpoint_id": "cp-1", "path": "."},
    "tg_session_open": {"path": "."},
    "tg_session_list": {"path": "."},
    "tg_session_show": {"session_id": "nonexistent-session", "path": "."},
    "tg_session_refresh": {"session_id": "nonexistent-session", "path": "."},
    "tg_session_context": {"session_id": "nonexistent-session", "query": "x", "path": "."},
    "tg_rewrite_diff": {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
    # #98 (MCP consolidation Phase-1): the 10 meta-tools. Every meta tool confines its PRIMARY
    # path/root param -- and most other declared path-shaped params -- UNCONDITIONALLY at the
    # top (before the action branch), so -- unlike the legacy tools above, where the chosen
    # action sometimes matters for reachability -- a single fixed `action` here reaches
    # confinement for almost every non-exempt string param on that tool's schema regardless of
    # which action it belongs to. TWO EXCEPTIONS: tg_scan's baseline_path/write_baseline/
    # suppressions_path/write_suppressions and tg_rewrite's audit_manifest/policy are confined
    # by the DELEGATED legacy function (tg_ruleset_scan / execute_rewrite_apply_json, the latter
    # reached via tg_rewrite_apply) before any filesystem op, not by this meta layer -- load-
    # bearing, not redundant, which is why the fixed action below is deliberately "scan"/"apply"
    # (the one action each actually dispatches through) so this ratchet still reaches them.
    "tg_navigate": {"action": "imports", "file": "dummy.py", "path": "."},
    "tg_impact": {"action": "impact", "symbol": "Foo", "path": "."},
    "tg_query": {"action": "text", "pattern": "x", "path": "."},
    "tg_context": {"action": "pack", "query": "x", "path": "."},
    "tg_explore": {"action": "orient", "path": "."},
    "tg_session": {
        "action": "file_importers",
        "session_id": "nonexistent-session",
        "file": "dummy.py",
        "path": ".",
    },
    "tg_scan": {"action": "scan", "ruleset": "secrets-basic", "path": "."},
    "tg_audit": {"action": "manifest_verify", "manifest_path": "manifest.json", "path": "."},
    "tg_checkpoint": {"action": "list", "path": "."},
    "tg_rewrite": {
        "action": "apply",
        "pattern": "x",
        "replacement": "y",
        "lang": "python",
        "path": ".",
    },
}


def _tool_string_param_names(tool) -> list[str]:
    """Every param name in `tool`'s live input schema typed `str` or `str | None`."""
    properties = tool.inputSchema.get("properties", {})
    names = []
    for param_name, schema in properties.items():
        types_seen = set()
        if "type" in schema:
            types_seen.add(schema["type"])
        for sub in schema.get("anyOf", ()):
            if "type" in sub:
                types_seen.add(sub["type"])
        if "string" in types_seen:
            names.append(param_name)
    return names


def _enumerate_confinement_ratchet_cases() -> list[tuple[str, str]]:
    """(tool_name, param_name) for every non-exempt string param on every registered tool."""
    from tensor_grep.cli import mcp_server

    cases: list[tuple[str, str]] = []
    for tool in asyncio.run(mcp_server.mcp.list_tools()):
        for param_name in _tool_string_param_names(tool):
            if param_name in CONFINEMENT_EXEMPT:
                continue
            cases.append((tool.name, param_name))
    return sorted(cases)


_CONFINEMENT_RATCHET_CASES = _enumerate_confinement_ratchet_cases()


def _ratchet_positive_value(tool_name: str, param_name: str, root: Path) -> str:
    """The 'valid in-root' value for a (tool, param) ratchet case.

    Defaults to a nonexistent in-root relative name -- confinement never requires
    existence, and every tool's OWN not-found handling for that param is already covered
    by its existing tests. Two params are special-cased: tg_ruleset_scan's baseline_path/
    suppressions_path loaders (`_load_ruleset_baseline`/`_load_ruleset_suppressions` in
    cli/main.py) call `.read_text()` directly with no FileNotFoundError guard in
    tg_ruleset_scan's own except clauses (only ValueError/BroadScanRefusedError are caught
    there), so a missing file would raise past this test instead of exercising the
    confinement layer -- pre-create a minimal valid file for those two. `tg_scan` (#98)
    dispatches action="scan" straight to `tg_ruleset_scan`, so it inherits the identical
    need whenever its OWN baseline_path/suppressions_path ratchet case is exercised.
    """
    if param_name == "path":
        return "."
    if tool_name in {"tg_ruleset_scan", "tg_scan"} and param_name == "baseline_path":
        (root / "ratchet_baseline.json").write_text(
            json.dumps({"fingerprints": []}), encoding="utf-8"
        )
        return "ratchet_baseline.json"
    if tool_name in {"tg_ruleset_scan", "tg_scan"} and param_name == "suppressions_path":
        (root / "ratchet_suppressions.json").write_text(json.dumps({}), encoding="utf-8")
        return "ratchet_suppressions.json"
    return "ratchet_ok_target"


# #102 fold-in: the 13 round-6/7 params `_confine_mcp_path`'s docstring names as a residual
# cwd-hardcoded set (tg_file_imports/importers `file`, tg_classify_logs `file_path`, the
# tg_audit_*/tg_review_bundle_* manifest/bundle params, tg_rewrite_apply `audit_manifest`) now
# route through `_mcp_root()` too, so TG_MCP_ROOT relocates them exactly like every primary
# path/root param. (tool_name, param_name, base_kwargs) -- base_kwargs supplies every OTHER
# required param with a value that either doesn't need to exist on disk (confinement is pure
# path resolution) or is pre-created in the test body when the tool's own loader reads it
# directly (mirrors _RATCHET_BASE_KWARGS / _ratchet_positive_value above).
_ANCHOR_SPLIT_CASES: list[tuple[str, str, dict[str, object]]] = [
    ("tg_file_imports", "file", {}),
    ("tg_file_importers", "file", {"path": "."}),
    ("tg_classify_logs", "file_path", {}),
    ("tg_audit_manifest_verify", "manifest_path", {}),
    ("tg_audit_manifest_verify", "previous_manifest", {"manifest_path": "manifest.json"}),
    ("tg_audit_diff", "previous_manifest", {"current_manifest": "current.json"}),
    ("tg_audit_diff", "current_manifest", {"previous_manifest": "previous.json"}),
    ("tg_review_bundle_create", "manifest_path", {}),
    ("tg_review_bundle_create", "scan_path", {"manifest_path": "manifest.json"}),
    ("tg_review_bundle_create", "previous_manifest", {"manifest_path": "manifest.json"}),
    ("tg_review_bundle_create", "output_path", {"manifest_path": "manifest.json"}),
    ("tg_review_bundle_verify", "bundle_path", {}),
    (
        "tg_rewrite_apply",
        "audit_manifest",
        {"pattern": "x", "replacement": "y", "lang": "python", "path": "."},
    ),
]


# ================================================================================================
# #98 (MCP consolidation Phase-1): the 10 additive task-shaped meta-tools.
#   - Ratchet B (plural path ratchet): schema-driven, mirrors the string ratchet A above but
#     for array<string> params -- `_tool_string_param_names` only matches `type=="string"`, so
#     an array-of-strings path param (today, only tg_query's `workspace_roots`) is invisible to
#     ratchet A and needs its own coverage (must-fix 3).
#   - The flag-OFF invariant, proven via SUBPROCESS isolation, not `importlib.reload` (must-fix 2).
#   - Per-meta dispatch tests (monkeypatch-spy the legacy fn, assert forwarded args).
#   - Fail-closed-class preservation (native-unavailable, validation-command gating).
# ================================================================================================

# Plural (array<string>) path params, keyed by param name -- the array counterpart of
# CONFINEMENT_EXEMPT above. `ignore` (tg_orient / tg_explore) is a glob-pattern list used to
# EXCLUDE files from centrality ranking, not a location to read/write -- confining it would
# incorrectly demand it be an in-root path.
PLURAL_CONFINEMENT_EXEMPT: dict[str, str] = {
    "ignore": "a glob-pattern list (tg_orient/tg_explore), excludes files, not a path to confine",
}

# Minimal valid kwargs per meta tool so a targeted plural param's confinement check is
# actually reached (mirrors _RATCHET_BASE_KWARGS above, scoped to the meta tools that declare
# an array<string> param at all).
_PLURAL_RATCHET_BASE_KWARGS: dict[str, dict[str, object]] = {
    "tg_query": {"action": "text", "pattern": "x", "path": "."},
}


def _tool_array_string_param_names(tool) -> list[str]:
    """Every param name in `tool`'s live input schema typed as an array of strings
    (`list[str]` or `list[str] | None`)."""
    properties = tool.inputSchema.get("properties", {})
    names = []
    for param_name, schema in properties.items():
        candidates = [schema, *schema.get("anyOf", ())]
        for candidate in candidates:
            if (
                candidate.get("type") == "array"
                and candidate.get("items", {}).get("type") == "string"
            ):
                names.append(param_name)
                break
    return names


def _enumerate_plural_confinement_ratchet_cases() -> list[tuple[str, str]]:
    """(tool_name, param_name) for every non-exempt array<string> param on every registered
    tool."""
    from tensor_grep.cli import mcp_server

    cases: list[tuple[str, str]] = []
    for tool in asyncio.run(mcp_server.mcp.list_tools()):
        for param_name in _tool_array_string_param_names(tool):
            if param_name in PLURAL_CONFINEMENT_EXEMPT:
                continue
            cases.append((tool.name, param_name))
    return sorted(cases)


_PLURAL_CONFINEMENT_RATCHET_CASES = _enumerate_plural_confinement_ratchet_cases()


# ------------------------------------------------------------------------------------------
# Flag-OFF invariant, via SUBPROCESS isolation (#98 must-fix 2).
#
# Registration (`_register_legacy_tool`) and `_MCP_TOOL_CAPABILITIES`
# (`_build_mcp_tool_capabilities`) are BOTH bound to `_legacy_tools_enabled()` at IMPORT time
# (module load). `importlib.reload(mcp_server)` in the SAME test process would re-run that
# import-time binding under the reloaded flag state, but the reload also REPLACES the module
# object every other already-imported reference points at -- leaking the flag-OFF registry
# into sibling call-time schema gates (the ratchet tests above, test_harness_api_docs.py) that
# run later in the same pytest session against what they still think is the flag-ON module.
# There is no reload precedent to reuse in this file; a subprocess is a clean process boundary
# instead: nothing the child process imports or mutates can leak back into this test process.
# ------------------------------------------------------------------------------------------

_MCP_FLAG_PROBE_SCRIPT = """
import asyncio
import json

from tensor_grep.cli import mcp_server

tool_names = sorted(t.name for t in asyncio.run(mcp_server.mcp.list_tools()))
capability_names = sorted(mcp_server._MCP_TOOL_CAPABILITIES)
print(json.dumps({
    "tool_names": tool_names,
    "capability_names": capability_names,
    "legacy_enabled": mcp_server._legacy_tools_enabled(),
}))
"""

_EXPECTED_META_TOOL_NAMES = {
    "tg_navigate",
    "tg_impact",
    "tg_query",
    "tg_context",
    "tg_explore",
    "tg_session",
    "tg_scan",
    "tg_audit",
    "tg_checkpoint",
    "tg_rewrite",
}
_EXPECTED_SINGLETON_TOOL_NAMES = {"tg_mcp_capabilities", "tg_classify_logs"}


def _run_mcp_flag_probe_subprocess(env_overrides: dict[str, str]) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[2]
    src_dir = repo_root / "src"
    env = os.environ.copy()
    env.update(env_overrides)
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{src_dir}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(src_dir)
    )
    completed = subprocess.run(
        [sys.executable, "-c", _MCP_FLAG_PROBE_SCRIPT],
        env=env,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert completed.returncode == 0, (
        f"probe subprocess failed (exit {completed.returncode}):\n"
        f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
    )
    # The probe script prints exactly one JSON line; be defensive about any stray warning
    # lines a dependency might emit on stdout ahead of it.
    return json.loads(completed.stdout.strip().splitlines()[-1])


class _StubScanner:
    """Explicit scanner double for the #283 scan_limit tests.

    Deliberately NOT a `MagicMock`: a bare MagicMock auto-vivifies a TRUTHY `.scan_truncated`
    (and a truthy `.scan_truncation_cause`), which is precisely why the sibling OR at the AST
    tool was tried and reverted. A stub with real values makes each arm mean what it says.
    """

    def __init__(self, *, truncated: bool, cause: str | None, unreadable_count: int = 0) -> None:
        self.scan_truncated = truncated
        self.scan_truncation_cause = cause
        self.unreadable_path_count = unreadable_count
        self.unreadable_path_sample: list[str] = []
        self.max_scan_entries = 200_000

    def walk(self, *args, **kwargs):
        return ["a.log"]


def _tg_search_scan_limit_payload(stub: "_StubScanner") -> dict:
    from tensor_grep.cli import mcp_server

    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(
        matches=[MatchLine(line_number=1, text="ERROR here", file="a.log")],
        total_files=1,
        total_matches=1,
    )
    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner", return_value=stub),
    ):
        pipeline = mock_pipeline.return_value
        pipeline.get_backend.return_value = fake_backend
        pipeline.selected_backend_name = "CPUBackend"
        pipeline.selected_backend_reason = "cpu_default"
        out = mcp_server.tg_search("ERROR", ".")
    return json.loads(out)["scan_limit"]
