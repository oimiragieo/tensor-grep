"""
Tests for audit B10: _upsert_history_entry must use OR semantics on the upsert key
(file_path only), so that re-generating the same path with a new digest replaces the
stale entry rather than duplicating it.

These tests import only audit_manifest (no rust_core) and can run standalone:
    PYTHONUTF8=1 PYTHONPATH=src python -m pytest tests/unit/test_audit_history_upsert.py -v
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tensor_grep.cli.audit_manifest import _upsert_history_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    file_path: str,
    digest: str,
    *,
    kind: str | None = "rewrite-audit-manifest",
    created_at: str | None = "2026-01-01T00:00:00Z",
    previous_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "manifest_sha256": digest,
        "file_path": file_path,
        "kind": kind,
        "created_at": created_at,
        "previous_manifest_sha256": previous_manifest_sha256,
    }


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Unit tests for _upsert_history_entry (B10)
# ---------------------------------------------------------------------------


class TestUpsertHistoryEntryOrSemantics:
    """audit B10: file_path is the upsert key; changing the digest for the same path
    must replace (not duplicate) the existing entry."""

    def test_same_path_new_digest_replaces_existing_entry(self) -> None:
        """Core B10 scenario: regenerating the same manifest path with a new digest
        must leave exactly one entry for that path."""
        path = "/project/.tensor-grep/audit/rewrite.json"
        original = _make_entry(path, _digest("v1"))
        entries = [original]

        updated = _make_entry(path, _digest("v2"), created_at="2026-06-01T00:00:00Z")
        result = _upsert_history_entry(entries, updated)

        assert len(result) == 1, (
            f"Expected 1 entry for the path; got {len(result)}.  "
            "Stale entry was not removed (B10 regression)."
        )
        assert result[0]["manifest_sha256"] == _digest("v2")
        assert result[0]["file_path"] == path

    def test_different_path_adds_new_entry(self) -> None:
        """A new file_path must add a second entry rather than replacing the existing one."""
        path_a = "/project/.tensor-grep/audit/a.json"
        path_b = "/project/.tensor-grep/audit/b.json"
        entry_a = _make_entry(path_a, _digest("a"))
        entries = [entry_a]

        entry_b = _make_entry(path_b, _digest("b"))
        result = _upsert_history_entry(entries, entry_b)

        assert len(result) == 2
        paths_in_result = {e["file_path"] for e in result}
        assert paths_in_result == {path_a, path_b}

    def test_same_path_same_digest_is_idempotent(self) -> None:
        """Upserting an identical entry must leave exactly one copy."""
        path = "/project/.tensor-grep/audit/stable.json"
        entry = _make_entry(path, _digest("v1"))
        entries = [entry]

        result = _upsert_history_entry(entries, _make_entry(path, _digest("v1")))

        assert len(result) == 1
        assert result[0]["manifest_sha256"] == _digest("v1")

    def test_upsert_into_empty_list_appends_entry(self) -> None:
        """Upserting into an empty list must produce a single-element list."""
        entry = _make_entry("/project/.tensor-grep/audit/first.json", _digest("first"))
        result = _upsert_history_entry([], entry)

        assert len(result) == 1
        assert result[0]["file_path"] == entry["file_path"]

    def test_multiple_preexisting_paths_only_matching_path_is_replaced(self) -> None:
        """When there are N paths and the upsert targets path[1], only that entry is removed."""
        paths = [f"/project/.tensor-grep/audit/{i}.json" for i in range(4)]
        entries = [_make_entry(p, _digest(p)) for p in paths]

        target_path = paths[2]
        new_entry = _make_entry(target_path, _digest("updated"), created_at="2026-06-09T00:00:00Z")
        result = _upsert_history_entry(entries, new_entry)

        assert len(result) == len(paths)  # same count: replaced, not added
        result_by_path = {e["file_path"]: e for e in result}
        assert result_by_path[target_path]["manifest_sha256"] == _digest("updated")
        # All other paths are untouched.
        for p in paths:
            if p != target_path:
                assert result_by_path[p]["manifest_sha256"] == _digest(p)


# ---------------------------------------------------------------------------
# Integration-level test via record_audit_manifest (audit B10 end-to-end)
# ---------------------------------------------------------------------------


def _write_manifest(path: Path, *, project_root: Path, digest_seed: str) -> dict[str, Any]:
    """Write a minimal but structurally valid audit manifest and return its dict."""
    payload: dict[str, Any] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "lang": "python",
        "path": str(project_root),
        "plan_total_edits": 1,
        "applied_edit_ids": ["edit-1"],
        "checkpoint": None,
        "validation": None,
        "files": [],
        "previous_manifest_sha256": None,
        "created_at": "2026-06-09T12:00:00Z",
    }
    # Compute digest without the manifest_sha256 field itself.
    canonical = dict(payload)
    raw_bytes = json.dumps(canonical, indent=2).encode("utf-8")
    payload["manifest_sha256"] = hashlib.sha256(raw_bytes + digest_seed.encode()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def test_record_audit_manifest_replaces_stale_entry_on_digest_change(tmp_path: Path) -> None:
    """End-to-end: calling record_audit_manifest twice for the same file path with different
    digests must leave exactly one index entry for that path (B10 integration check)."""
    from tensor_grep.cli.audit_manifest import record_audit_manifest

    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    manifest_path = audit_dir / "rewrite.json"

    # First recording.
    _write_manifest(manifest_path, project_root=project, digest_seed="v1")
    record_audit_manifest(manifest_path)

    # Overwrite the file with a different digest (simulating a regenerated manifest).
    _write_manifest(manifest_path, project_root=project, digest_seed="v2")
    record_audit_manifest(manifest_path)

    index_path = project / ".tensor-grep" / "audit" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    manifests = index["manifests"]
    path_occurrences = [m for m in manifests if m["file_path"] == str(manifest_path.resolve())]
    assert len(path_occurrences) == 1, (
        f"Expected exactly 1 index entry for the manifest path; "
        f"got {len(path_occurrences)} (B10 regression)."
    )
