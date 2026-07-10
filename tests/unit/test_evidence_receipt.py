from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import evidence_receipt

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# Fixtures: a real temp git repo (for the GAP-1 revision-identity producer)
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_git_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-b", "main", "."], cwd=root)
    _run_git(["config", "user.email", "test@example.com"], cwd=root)
    _run_git(["config", "user.name", "Test User"], cwd=root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(["add", "README.md"], cwd=root)
    _run_git(["commit", "-m", "initial commit"], cwd=root)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    return repo


# ---------------------------------------------------------------------------
# Fixtures: sample capsule / manifest payloads matching the real producer
# shapes (agent_capsule.py:2227-2275 and rust_core/src/main.rs:6210 /
# tests/unit/test_review_bundles.py's manifest fixture).
# ---------------------------------------------------------------------------


def _sample_capsule(**overrides: Any) -> dict[str, Any]:
    capsule: dict[str, Any] = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "RepoMap",
        "routing_reason": "agent-context-capsule",
        "capsule_version": 1,
        "query": "charge_card",
        "path": "/repo",
        "ambiguity": {"status": "unambiguous"},
        "primary_target": {"file": "src/billing.py", "symbol": "charge_card", "confidence": 0.9},
        "alternative_targets": [
            {"file": "src/legacy_billing.py", "symbol": "charge_card_legacy", "confidence": 0.4}
        ],
        "snippets": [
            {"file": "src/billing.py", "source": "def charge_card(): ...", "truncated": False}
        ],
        "related_call_sites": [
            {"file": "src/api.py", "line": 42, "provenance": "parser-backed"},
        ],
        "call_site_evidence": {
            "status": "collected",
            "symbol": "charge_card",
            "routing_reason": "symbol-blast-radius",
            "max_callers": 8,
            "returned_call_sites": 1,
            "omitted_call_sites": 0,
            "provenance": ["parser-backed"],
            "graph_trust_summary": {"trust": "high"},
            "resolution_gaps": [],
        },
        "validation_plan": [{"kind": "test", "command": "pytest -q"}],
        "validation_commands": ["pytest -q"],
        "suggested_validation_commands": ["pytest -q -k billing"],
        "rollback": {
            "checkpoint_recommended": True,
            "reason": "source edit target selected",
            "command": "tg checkpoint undo ckpt-1 .",
            "argv": ["tg", "checkpoint", "undo", "ckpt-1", "."],
        },
        "omissions": {
            "token_budget": 1200,
            "omitted_section_count": 2,
            "omitted_sections": ["deep_call_graph"],
            "follow_up_reads": ["src/legacy_billing.py"],
        },
        "confidence": {"overall": 0.72, "downgrade_reasons": ["ambiguous secondary match"]},
        "ask_user_before_editing": {"required": False, "reasons": []},
        "result_incomplete": False,
        "partial": False,
        "scan_limit": {"max_repo_files": 4000, "possibly_truncated": False},
    }
    capsule.update(overrides)
    return capsule


def _canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return json.dumps(canonical, indent=2).encode("utf-8")


def _sample_manifest(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-07-10T12:00:00Z",
        "lang": "python",
        "path": "/repo",
        "plan_total_edits": 1,
        "applied_edit_ids": ["edit-1"],
        "checkpoint": {
            "checkpoint_id": "ckpt-abc123",
            "mode": "git-worktree-snapshot",
            "root": "/repo",
            "scope": "tree",
            "original_path": "/repo",
            "created_at": "2026-07-10T11:59:00Z",
            "file_count": 3,
        },
        "validation": {
            "success": True,
            "commands": [
                {
                    "kind": "test",
                    "command": "pytest -q",
                    "success": True,
                    "exit_code": 0,
                    "stdout": "",
                    "stderr": "",
                }
            ],
            "validation_targets_truncated": False,
            "validation_targets_total": 1,
        },
        "files": [
            {
                "path": "src/billing.py",
                "edit_ids": ["edit-1"],
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
            }
        ],
        "previous_manifest_sha256": None,
    }
    manifest.update(overrides)
    manifest["manifest_sha256"] = hashlib.sha256(_canonical_manifest_bytes(manifest)).hexdigest()
    return manifest


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Envelope / schema basics
# ---------------------------------------------------------------------------


def test_receipt_has_stable_envelope_and_schema_fields(git_repo: Path) -> None:
    receipt = evidence_receipt.build_evidence_receipt(git_repo)

    assert receipt["routing_backend"] == "EvidenceReceipt"
    assert receipt["routing_reason"] == "evidence-receipt-emit"
    assert receipt["sidecar_used"] is False
    assert receipt["kind"] == "evidence-receipt"
    assert receipt["receipt_schema_version"] == 1
    assert receipt["created_at"].endswith("Z")
    assert receipt["tool"]["name"] == "tensor-grep"
    assert isinstance(receipt["tool"]["version"], str) and receipt["tool"]["version"]
    assert isinstance(receipt["tool"]["json_output_version"], int)


def test_receipt_top_level_blocks_are_all_present_even_when_every_source_is_absent(
    git_repo: Path,
) -> None:
    receipt = evidence_receipt.build_evidence_receipt(git_repo)

    for block_name in (
        "revision",
        "scope",
        "blast_radius",
        "confidence",
        "validation",
        "changes",
        "caller",
        "sources",
    ):
        assert block_name in receipt, f"missing stable block: {block_name}"


def test_build_evidence_receipt_json_matches_python_payload(git_repo: Path) -> None:
    receipt = evidence_receipt.build_evidence_receipt(git_repo)
    receipt_json = evidence_receipt.build_evidence_receipt_json(git_repo)

    assert json.loads(receipt_json) == receipt


# ---------------------------------------------------------------------------
# GAP 1: _repo_revision_identity
# ---------------------------------------------------------------------------


def test_repo_revision_identity_on_clean_repo(git_repo: Path) -> None:
    identity = evidence_receipt._repo_revision_identity(git_repo)

    expected_sha = _run_git(["rev-parse", "HEAD"], cwd=git_repo).stdout.strip()
    assert identity["status"] == "present"
    assert identity["commit_sha"] == expected_sha
    assert identity["branch"] == "main"
    assert identity["dirty"] is False
    assert identity["dirty_file_count"] == 0
    assert identity["dirty_tree_sha256"] == _EMPTY_SHA256


def test_repo_revision_identity_on_dirty_repo(git_repo: Path) -> None:
    (git_repo / "scratch.tmp").write_text("uncommitted\n", encoding="utf-8")

    identity = evidence_receipt._repo_revision_identity(git_repo)

    assert identity["status"] == "present"
    assert identity["dirty"] is True
    assert identity["dirty_file_count"] == 1
    assert identity["dirty_tree_sha256"] != _EMPTY_SHA256


def test_repo_revision_identity_dirty_tree_sha256_is_deterministic_for_same_state(
    git_repo: Path,
) -> None:
    (git_repo / "scratch.tmp").write_text("uncommitted\n", encoding="utf-8")

    first = evidence_receipt._repo_revision_identity(git_repo)
    second = evidence_receipt._repo_revision_identity(git_repo)

    assert first["dirty_tree_sha256"] == second["dirty_tree_sha256"]
    assert first["commit_sha"] == second["commit_sha"]


def test_repo_revision_identity_changes_hash_when_dirty_state_changes(git_repo: Path) -> None:
    baseline = evidence_receipt._repo_revision_identity(git_repo)

    (git_repo / "scratch.tmp").write_text("uncommitted\n", encoding="utf-8")
    after_one_file = evidence_receipt._repo_revision_identity(git_repo)

    (git_repo / "scratch2.tmp").write_text("more\n", encoding="utf-8")
    after_two_files = evidence_receipt._repo_revision_identity(git_repo)

    assert baseline["dirty_tree_sha256"] != after_one_file["dirty_tree_sha256"]
    assert after_one_file["dirty_tree_sha256"] != after_two_files["dirty_tree_sha256"]
    # binding key stays anchored to the same commit throughout
    assert baseline["commit_sha"] == after_one_file["commit_sha"] == after_two_files["commit_sha"]


def test_repo_revision_identity_fails_closed_outside_a_git_repo(tmp_path: Path) -> None:
    non_git_dir = tmp_path / "not_a_repo"
    non_git_dir.mkdir()

    identity = evidence_receipt._repo_revision_identity(non_git_dir)

    assert identity["status"] == "unavailable"
    assert identity.get("reason")
    assert "commit_sha" not in identity


def test_repo_revision_identity_makes_at_most_two_git_subprocess_calls(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_count = 0
    real_run_subprocess = evidence_receipt.run_subprocess

    def _counting_run_subprocess(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return real_run_subprocess(*args, **kwargs)

    monkeypatch.setattr(evidence_receipt, "run_subprocess", _counting_run_subprocess)

    evidence_receipt._repo_revision_identity(git_repo)

    assert call_count <= 2


def test_receipt_revision_block_reflects_dirty_worktree_end_to_end(git_repo: Path) -> None:
    (git_repo / "scratch.tmp").write_text("uncommitted\n", encoding="utf-8")

    receipt = evidence_receipt.build_evidence_receipt(git_repo)

    assert receipt["revision"]["status"] == "present"
    assert receipt["revision"]["dirty"] is True


# ---------------------------------------------------------------------------
# Fail-closed contract: missing/invalid sources -> status unavailable + reason,
# never a silently empty/guessed value.
# ---------------------------------------------------------------------------


def test_scope_and_blast_radius_and_confidence_and_validation_unavailable_without_capsule(
    git_repo: Path,
) -> None:
    receipt = evidence_receipt.build_evidence_receipt(git_repo)

    assert receipt["scope"]["status"] == "unavailable"
    assert receipt["scope"]["reason"]
    assert receipt["scope"]["files_selected"] is None

    assert receipt["blast_radius"]["status"] == "unavailable"
    assert receipt["blast_radius"]["reason"]

    assert receipt["confidence"]["status"] == "unavailable"
    assert receipt["confidence"]["reason"]

    assert receipt["validation"]["status"] == "unavailable"
    assert receipt["validation"]["reason"]


def test_changes_unavailable_without_manifest_or_checkpoint(git_repo: Path) -> None:
    receipt = evidence_receipt.build_evidence_receipt(git_repo)

    assert receipt["changes"]["status"] == "unavailable"
    assert receipt["changes"]["reason"]


def test_invalid_capsule_json_degrades_to_unavailable_not_a_crash(
    git_repo: Path, tmp_path: Path
) -> None:
    bad_capsule = tmp_path / "capsule.json"
    bad_capsule.write_text("{not valid json", encoding="utf-8")

    receipt = evidence_receipt.build_evidence_receipt(git_repo, capsule_path=bad_capsule)

    assert receipt["scope"]["status"] == "unavailable"
    assert "not valid JSON" in receipt["scope"]["reason"]
    assert receipt["blast_radius"]["status"] == "unavailable"


def test_missing_manifest_path_degrades_to_unavailable_with_reason(
    git_repo: Path, tmp_path: Path
) -> None:
    missing = tmp_path / "does_not_exist.json"

    receipt = evidence_receipt.build_evidence_receipt(git_repo, manifest_path=missing)

    assert receipt["changes"]["status"] == "unavailable"
    assert "not found" in receipt["changes"]["reason"]


def test_capsule_missing_call_site_evidence_key_is_unavailable_not_fabricated(
    git_repo: Path, tmp_path: Path
) -> None:
    capsule = _sample_capsule()
    del capsule["call_site_evidence"]
    capsule_path = _write_json(tmp_path / "capsule.json", capsule)

    receipt = evidence_receipt.build_evidence_receipt(git_repo, capsule_path=capsule_path)

    assert receipt["blast_radius"]["status"] == "unavailable"
    assert receipt["blast_radius"]["reason"]
    assert "callers" not in receipt["blast_radius"]


# ---------------------------------------------------------------------------
# Capsule-sourced blocks: scope / blast_radius / confidence / validation-plan
# ---------------------------------------------------------------------------


def test_scope_block_sourced_from_capsule(git_repo: Path, tmp_path: Path) -> None:
    capsule_path = _write_json(tmp_path / "capsule.json", _sample_capsule())

    receipt = evidence_receipt.build_evidence_receipt(
        git_repo, query="charge_card", capsule_path=capsule_path
    )

    scope = receipt["scope"]
    assert scope["status"] == "present"
    assert scope["query"] == "charge_card"
    assert "src/billing.py" in scope["files_selected"]
    assert scope["files_omitted_count"] == 2
    assert scope["completeness"]["status"] == "present"
    assert scope["completeness"]["result_incomplete"] is False


def test_blast_radius_block_sourced_from_capsule_call_site_evidence(
    git_repo: Path, tmp_path: Path
) -> None:
    capsule_path = _write_json(tmp_path / "capsule.json", _sample_capsule())

    receipt = evidence_receipt.build_evidence_receipt(git_repo, capsule_path=capsule_path)

    blast_radius = receipt["blast_radius"]
    assert blast_radius["status"] == "collected"
    assert blast_radius["source"] == "capsule"
    assert blast_radius["symbol"] == "charge_card"
    assert blast_radius["callers"] == _sample_capsule()["related_call_sites"]
    assert blast_radius["omitted_callers"] == 0
    assert blast_radius["graph_trust_summary"] == {"trust": "high"}
    assert blast_radius["resolution_gaps"] == []


def test_confidence_block_sourced_from_capsule(git_repo: Path, tmp_path: Path) -> None:
    capsule_path = _write_json(tmp_path / "capsule.json", _sample_capsule())

    receipt = evidence_receipt.build_evidence_receipt(git_repo, capsule_path=capsule_path)

    confidence = receipt["confidence"]
    assert confidence["status"] == "present"
    assert confidence["overall"] == 0.72
    assert confidence["downgrade_reasons"] == ["ambiguous secondary match"]
    assert confidence["ambiguity"] == {"status": "unambiguous"}
    assert confidence["alternative_targets"] == _sample_capsule()["alternative_targets"]
    assert confidence["ask_user_before_editing"] == {"required": False, "reasons": []}


def test_validation_planned_commands_sourced_from_capsule_without_manifest(
    git_repo: Path, tmp_path: Path
) -> None:
    capsule_path = _write_json(tmp_path / "capsule.json", _sample_capsule())

    receipt = evidence_receipt.build_evidence_receipt(git_repo, capsule_path=capsule_path)

    validation = receipt["validation"]
    assert validation["status"] == "present"
    assert validation["planned_commands"] == ["pytest -q"]
    assert validation["suggested_validation_commands"] == ["pytest -q -k billing"]
    # no manifest -> actual outcome is unknown, not fabricated as success
    assert validation["success"] is None
    assert validation["commands"] is None


# ---------------------------------------------------------------------------
# Manifest-sourced blocks: validation outcomes / changes / rollback
# ---------------------------------------------------------------------------


def test_validation_actual_outcome_sourced_from_manifest(git_repo: Path, tmp_path: Path) -> None:
    manifest_path = _write_json(tmp_path / "manifest.json", _sample_manifest())

    receipt = evidence_receipt.build_evidence_receipt(git_repo, manifest_path=manifest_path)

    validation = receipt["validation"]
    assert validation["status"] == "present"
    assert validation["success"] is True
    assert validation["targets_truncated"] is False
    assert validation["targets_total"] == 1
    # no capsule -> planned commands are unknown, not fabricated
    assert validation["planned_commands"] is None


def test_changes_block_sourced_from_manifest(git_repo: Path, tmp_path: Path) -> None:
    manifest_path = _write_json(tmp_path / "manifest.json", _sample_manifest())

    receipt = evidence_receipt.build_evidence_receipt(git_repo, manifest_path=manifest_path)

    changes = receipt["changes"]
    assert changes["status"] == "present"
    assert changes["files"][0]["path"] == "src/billing.py"
    assert changes["applied_edit_ids"] == ["edit-1"]
    assert changes["plan_total_edits"] == 1


def test_changes_rollback_undo_command_is_reconstructed_from_manifest_checkpoint(
    git_repo: Path, tmp_path: Path
) -> None:
    manifest_path = _write_json(tmp_path / "manifest.json", _sample_manifest())

    receipt = evidence_receipt.build_evidence_receipt(git_repo, manifest_path=manifest_path)

    rollback = receipt["changes"]["rollback"]
    assert rollback["status"] == "present"
    assert rollback["checkpoint_id"] == "ckpt-abc123"
    assert rollback["undo_argv"] == ["tg", "checkpoint", "undo", "ckpt-abc123", "/repo"]
    assert "ckpt-abc123" in rollback["undo_command"]


def test_changes_triggered_rollback_outcome_is_always_unavailable_not_persisted(
    git_repo: Path, tmp_path: Path
) -> None:
    """The rewrite-audit-manifest on disk does not persist ValidationRollbackSummary
    (rust_core/src/main.rs:6097 ApplyVerifyJson.rollback vs :6210 RewriteAuditManifest,
    which has no rollback field) -- this must fail closed, never guess success/failure."""
    manifest_path = _write_json(tmp_path / "manifest.json", _sample_manifest())

    receipt = evidence_receipt.build_evidence_receipt(git_repo, manifest_path=manifest_path)

    triggered = receipt["changes"]["triggered_rollback"]
    assert triggered["status"] == "unavailable"
    assert triggered["reason"]


def test_changes_rollback_unavailable_when_manifest_has_no_checkpoint(
    git_repo: Path, tmp_path: Path
) -> None:
    manifest_path = _write_json(tmp_path / "manifest.json", _sample_manifest(checkpoint=None))

    receipt = evidence_receipt.build_evidence_receipt(git_repo, manifest_path=manifest_path)

    assert receipt["changes"]["rollback"]["status"] == "unavailable"


def test_standalone_checkpoint_id_without_manifest_still_answers_rollback(
    git_repo: Path,
) -> None:
    """--checkpoint-id (no --manifest) can answer rollback even though the changed-files list
    and validation outcome stay unknown -- mirrors review-bundle's own --checkpoint-id support
    (checkpoint_store.load_checkpoint_metadata)."""
    from tensor_grep.cli.checkpoint_store import create_checkpoint

    checkpoint = create_checkpoint(str(git_repo))

    receipt = evidence_receipt.build_evidence_receipt(
        git_repo, checkpoint_id=checkpoint.checkpoint_id
    )

    changes = receipt["changes"]
    assert changes["status"] == "unavailable"
    assert "files" not in changes
    rollback = changes["rollback"]
    assert rollback["status"] == "present"
    assert rollback["checkpoint_id"] == checkpoint.checkpoint_id
    assert rollback["undo_argv"][:3] == ["tg", "checkpoint", "undo"]
    assert checkpoint.checkpoint_id in rollback["undo_command"]


def test_standalone_checkpoint_id_unknown_id_fails_closed(git_repo: Path) -> None:
    receipt = evidence_receipt.build_evidence_receipt(git_repo, checkpoint_id="ckpt-does-not-exist")

    rollback = receipt["changes"]["rollback"]
    assert rollback["status"] == "unavailable"
    assert rollback["reason"]


# ---------------------------------------------------------------------------
# GAP 2: caller-supplied agent/model/cost metadata -- never invented.
# ---------------------------------------------------------------------------


def test_caller_metadata_is_null_when_absent_never_invented(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TG_EVIDENCE_AGENT_ID", raising=False)
    monkeypatch.delenv("TG_EVIDENCE_MODEL", raising=False)
    monkeypatch.delenv("TG_EVIDENCE_COST_JSON", raising=False)

    receipt = evidence_receipt.build_evidence_receipt(git_repo)

    caller = receipt["caller"]
    assert caller["status"] == "caller-supplied"
    assert caller["provenance"] == "caller-supplied"
    assert caller["caller_metadata_present"] is False
    assert caller["agent_id"] is None
    assert caller["model"] is None
    assert caller["cost"] is None


def test_caller_metadata_from_explicit_flags(git_repo: Path) -> None:
    receipt = evidence_receipt.build_evidence_receipt(
        git_repo, agent_id="claude-code", model="opus-4.8"
    )

    caller = receipt["caller"]
    assert caller["caller_metadata_present"] is True
    assert caller["agent_id"] == "claude-code"
    assert caller["model"] == "opus-4.8"
    assert caller["provenance"] == "caller-supplied"


def test_caller_metadata_falls_back_to_env_vars(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TG_EVIDENCE_AGENT_ID", "env-agent")
    monkeypatch.setenv("TG_EVIDENCE_MODEL", "env-model")

    receipt = evidence_receipt.build_evidence_receipt(git_repo)

    assert receipt["caller"]["agent_id"] == "env-agent"
    assert receipt["caller"]["model"] == "env-model"


def test_caller_metadata_explicit_flag_overrides_env_var(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TG_EVIDENCE_AGENT_ID", "env-agent")

    receipt = evidence_receipt.build_evidence_receipt(git_repo, agent_id="flag-agent")

    assert receipt["caller"]["agent_id"] == "flag-agent"


def test_caller_cost_json_read_and_recorded_verbatim(git_repo: Path, tmp_path: Path) -> None:
    cost_path = _write_json(
        tmp_path / "cost.json", {"input_tokens": 1234, "output_tokens": 567, "usd": 0.12}
    )

    receipt = evidence_receipt.build_evidence_receipt(git_repo, cost_json_path=cost_path)

    assert receipt["caller"]["cost"] == {"input_tokens": 1234, "output_tokens": 567, "usd": 0.12}
    assert receipt["caller"]["caller_metadata_present"] is True


def test_caller_cost_json_missing_path_is_visible_not_silently_dropped(
    git_repo: Path, tmp_path: Path
) -> None:
    missing_cost = tmp_path / "no_such_cost.json"

    receipt = evidence_receipt.build_evidence_receipt(git_repo, cost_json_path=missing_cost)

    caller = receipt["caller"]
    assert caller["cost"] is None
    assert caller.get("cost_source_error")
    assert "not found" in caller["cost_source_error"]


def test_caller_cost_source_error_absent_when_no_cost_json_requested_at_all(
    git_repo: Path,
) -> None:
    receipt = evidence_receipt.build_evidence_receipt(git_repo)

    assert "cost_source_error" not in receipt["caller"]


# ---------------------------------------------------------------------------
# Performance contract: default run never invokes an expensive recompute;
# --recompute is opt-in.
# ---------------------------------------------------------------------------


def test_default_run_never_calls_repo_map_blast_radius_recompute(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import repo_map

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("build_symbol_blast_radius must not run by default (perf contract)")

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius", _explode)
    capsule_path = _write_json(tmp_path / "capsule.json", _sample_capsule())

    # No --recompute: must aggregate the capsule only, never touch repo_map.
    evidence_receipt.build_evidence_receipt(
        git_repo, query="charge_card", capsule_path=capsule_path, recompute=False
    )


def test_recompute_flag_is_opt_in_and_recomputes_blast_radius(tmp_path: Path) -> None:
    project = tmp_path / "recompute_project"
    project.mkdir()
    (project / "module.py").write_text(
        "def target_fn():\n    return 1\n\n\ndef caller_fn():\n    return target_fn()\n",
        encoding="utf-8",
    )

    receipt = evidence_receipt.build_evidence_receipt(project, query="target_fn", recompute=True)

    blast_radius = receipt["blast_radius"]
    assert blast_radius["source"] == "recomputed"
    assert blast_radius["status"] == "present"
    assert receipt["sources"]["recomputed"] is True


def test_recompute_without_query_is_unavailable_not_a_silent_no_op(git_repo: Path) -> None:
    receipt = evidence_receipt.build_evidence_receipt(git_repo, recompute=True)

    assert receipt["blast_radius"]["status"] == "unavailable"
    assert receipt["sources"]["recomputed"] is False


# ---------------------------------------------------------------------------
# sources block
# ---------------------------------------------------------------------------


def test_sources_block_reports_manifest_and_capsule_provenance(
    git_repo: Path, tmp_path: Path
) -> None:
    manifest = _sample_manifest()
    manifest_path = _write_json(tmp_path / "manifest.json", manifest)
    capsule_path = _write_json(tmp_path / "capsule.json", _sample_capsule())

    receipt = evidence_receipt.build_evidence_receipt(
        git_repo, manifest_path=manifest_path, capsule_path=capsule_path
    )

    sources = receipt["sources"]
    assert sources["manifest_path"] == str(manifest_path.resolve())
    assert sources["manifest_sha256"] == manifest["manifest_sha256"]
    assert sources["capsule_path"] == str(capsule_path.resolve())
    assert sources["capsule_source"].startswith("file:")
    assert sources["session_id"] is None
    assert sources["recomputed"] is False


def test_sources_block_when_nothing_supplied(git_repo: Path) -> None:
    receipt = evidence_receipt.build_evidence_receipt(git_repo)

    sources = receipt["sources"]
    assert sources["manifest_path"] is None
    assert sources["manifest_sha256"] is None
    assert sources["capsule_path"] is None
    assert sources["capsule_source"] == "none"


# ---------------------------------------------------------------------------
# No P2 (signing) leakage: receipt_sha256 / signature / previous_receipt_sha256
# must NOT appear in the Phase-1 schema.
# ---------------------------------------------------------------------------


def test_phase1_receipt_has_no_signing_fields(git_repo: Path) -> None:
    receipt = evidence_receipt.build_evidence_receipt(git_repo)

    assert "receipt_sha256" not in receipt
    assert "signature" not in receipt
    assert "previous_receipt_sha256" not in receipt
