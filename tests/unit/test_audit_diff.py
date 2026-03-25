import hashlib
import json
from pathlib import Path

from tensor_grep.cli import audit_manifest


def _canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return json.dumps(canonical, indent=2).encode("utf-8")


def _write_audit_manifest(
    path: Path,
    *,
    kind: str = "rewrite-audit-manifest",
    files: list[dict[str, object]] | None = None,
    validation: dict[str, object] | None = None,
    extra_fields: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "kind": kind,
        "created_at": "2026-03-23T12:00:00Z",
        "lang": "python",
        "path": str(path.parent),
        "plan_total_edits": 1,
        "applied_edit_ids": ["edit-1"],
        "checkpoint": None,
        "validation": validation,
        "files": files
        or [
            {
                "path": "src/sample.py",
                "edit_ids": ["edit-1"],
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
            }
        ],
        "previous_manifest_sha256": None,
    }
    if extra_fields is not None:
        payload.update(extra_fields)
    payload["manifest_sha256"] = hashlib.sha256(_canonical_manifest_bytes(payload)).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def test_diff_manifest_objects_reports_added_removed_and_changed_fields():
    left = {
        "kind": "rewrite-audit-manifest",
        "manifest_sha256": "a" * 64,
        "signature": {"kind": "hmac-sha256", "value": "old"},
        "checkpoint": {"checkpoint_id": "ckpt-1", "enabled": True},
        "files": [{"path": "src/sample.py", "after_sha256": "b" * 64}],
    }
    right = {
        "kind": "rewrite-plan-manifest",
        "manifest_sha256": "b" * 64,
        "signature": {"kind": "hmac-sha256", "value": "new"},
        "checkpoint": {"status": "verified", "enabled": True},
        "files": [
            {"path": "src/sample.py", "after_sha256": "c" * 64},
            {"path": "src/new.py", "after_sha256": "d" * 64},
        ],
        "reviewer": "alice",
    }

    diff = audit_manifest.diff_manifest_objects(left, right)

    assert diff == {
        "added": {
            "reviewer": "alice",
            "files[1]": {"path": "src/new.py", "after_sha256": "d" * 64},
            "checkpoint.status": "verified",
        },
        "removed": {
            "checkpoint.checkpoint_id": "ckpt-1",
        },
        "changed": {
            "kind": {"old": "rewrite-audit-manifest", "new": "rewrite-plan-manifest"},
            "files[0].after_sha256": {"old": "b" * 64, "new": "c" * 64},
        },
    }


def test_diff_manifest_objects_ignores_manifest_digest_and_signature_changes():
    left = {
        "kind": "rewrite-audit-manifest",
        "manifest_sha256": "a" * 64,
        "signature": {"kind": "hmac-sha256", "value": "old"},
    }
    right = {
        "kind": "rewrite-audit-manifest",
        "manifest_sha256": "b" * 64,
        "signature": {"kind": "hmac-sha256", "value": "new"},
    }

    diff = audit_manifest.diff_manifest_objects(left, right)

    assert diff == {"added": {}, "removed": {}, "changed": {}}


def test_diff_manifest_objects_recurses_into_nested_lists_and_objects():
    left = {
        "validation": {
            "commands": ["uv run pytest tests/unit/test_old.py -q", "uv run ruff check ."],
            "status": {"passed": False},
        }
    }
    right = {
        "validation": {
            "commands": [
                "uv run pytest tests/unit/test_new.py -q",
                "uv run ruff check .",
                "uv run mypy src/tensor_grep",
            ],
            "status": {"passed": True},
        }
    }

    diff = audit_manifest.diff_manifest_objects(left, right)

    assert diff == {
        "added": {"validation.commands[2]": "uv run mypy src/tensor_grep"},
        "removed": {},
        "changed": {
            "validation.commands[0]": {
                "old": "uv run pytest tests/unit/test_old.py -q",
                "new": "uv run pytest tests/unit/test_new.py -q",
            },
            "validation.status.passed": {"old": False, "new": True},
        },
    }


def test_diff_manifest_objects_returns_empty_sections_for_identical_manifests():
    manifest = {
        "kind": "rewrite-audit-manifest",
        "validation": {"passed": True},
        "files": [{"path": "src/sample.py"}],
    }

    diff = audit_manifest.diff_manifest_objects(manifest, manifest)

    assert diff == {"added": {}, "removed": {}, "changed": {}}


def test_diff_audit_manifests_handles_different_manifest_kinds(tmp_path: Path):
    left_path = tmp_path / "rewrite-plan.json"
    right_path = tmp_path / "rewrite-audit.json"
    _write_audit_manifest(left_path, kind="rewrite-plan-manifest")
    _write_audit_manifest(right_path, kind="rewrite-audit-manifest")

    diff = audit_manifest.diff_audit_manifests(left_path, right_path)

    assert diff["changed"] == {
        "kind": {"old": "rewrite-plan-manifest", "new": "rewrite-audit-manifest"}
    }


def test_diff_audit_manifests_json_matches_python_payload(tmp_path: Path):
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path, extra_fields={"reviewer": "alice"})
    _write_audit_manifest(right_path, extra_fields={"reviewer": "bob"})

    diff = audit_manifest.diff_audit_manifests(left_path, right_path)
    diff_json = audit_manifest.diff_audit_manifests_json(left_path, right_path)

    assert json.loads(diff_json) == diff


def test_diff_audit_manifests_raises_file_not_found_for_missing_manifest(tmp_path: Path):
    existing_path = tmp_path / "existing.json"
    _write_audit_manifest(existing_path)

    missing_path = tmp_path / "missing.json"

    try:
        audit_manifest.diff_audit_manifests(existing_path, missing_path)
    except FileNotFoundError as exc:
        assert "Audit manifest not found" in str(exc)
    else:
        raise AssertionError("Expected FileNotFoundError")


def test_diff_audit_manifests_raises_json_decode_error_for_invalid_json(tmp_path: Path):
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    _write_audit_manifest(left_path)
    right_path.write_text("{not valid json", encoding="utf-8")

    try:
        audit_manifest.diff_audit_manifests(left_path, right_path)
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError("Expected JSONDecodeError")
