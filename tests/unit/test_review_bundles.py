import hashlib
import json
from pathlib import Path

from tensor_grep.cli import audit_manifest
from tensor_grep.cli.checkpoint_store import create_checkpoint


def _canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return json.dumps(canonical, indent=2).encode("utf-8")


def _write_audit_manifest(
    path: Path,
    *,
    project_root: Path,
    previous_manifest_sha256: str | None = None,
    created_at: str = "2026-03-23T12:00:00Z",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": created_at,
        "lang": "python",
        "path": str(project_root),
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _write_scan_results(path: Path) -> dict[str, object]:
    payload = {
        "version": 1,
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


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "src" / "sample.py").write_text("print('hello')\n", encoding="utf-8")
    return project, audit_dir


def test_create_review_bundle_packages_all_components_and_checksums(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    previous_path = audit_dir / "previous.json"
    previous_payload = _write_audit_manifest(previous_path, project_root=project)
    current_path = audit_dir / "current.json"
    current_payload = _write_audit_manifest(
        current_path,
        project_root=project,
        previous_manifest_sha256=str(previous_payload["manifest_sha256"]),
        created_at="2026-03-24T12:00:00Z",
    )
    scan_path = project / "scan.json"
    scan_payload = _write_scan_results(scan_path)
    checkpoint = create_checkpoint(str(project))

    bundle = audit_manifest.create_review_bundle(
        current_path,
        scan_path=scan_path,
        checkpoint_id=checkpoint.checkpoint_id,
        previous_manifest=current_path.parent / previous_path.name,
    )

    assert bundle["version"] == 1
    assert bundle["routing_backend"] == "AuditManifest"
    assert bundle["routing_reason"] == "review-bundle-create"
    assert bundle["sidecar_used"] is False
    assert bundle["created_at"].endswith("Z")
    assert bundle["audit_manifest"] == current_payload
    assert bundle["scan_results"] == scan_payload
    assert bundle["checkpoint_metadata"]["checkpoint_id"] == checkpoint.checkpoint_id
    assert bundle["diff"] == audit_manifest.diff_audit_manifests(previous_path, current_path)
    assert set(bundle["checksums"]) == {
        "audit_manifest",
        "scan_results",
        "checkpoint_metadata",
        "diff",
    }
    assert len(bundle["bundle_sha256"]) == 64


def test_create_review_bundle_sets_missing_optional_components_to_null_and_omits_checksums(
    tmp_path: Path,
):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "current.json"
    manifest_payload = _write_audit_manifest(manifest_path, project_root=project)

    bundle = audit_manifest.create_review_bundle(manifest_path)

    assert bundle["audit_manifest"] == manifest_payload
    assert bundle["scan_results"] is None
    assert bundle["checkpoint_metadata"] is None
    assert bundle["diff"] is None
    assert bundle["checksums"] == {"audit_manifest": bundle["checksums"]["audit_manifest"]}


def test_create_review_bundle_writes_output_file_when_requested(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    bundle_path = tmp_path / "review-bundle.json"

    bundle = audit_manifest.create_review_bundle(manifest_path, output_path=bundle_path)

    assert bundle_path.exists()
    assert json.loads(bundle_path.read_text(encoding="utf-8")) == bundle


def test_create_review_bundle_json_matches_python_payload(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)

    bundle = audit_manifest.create_review_bundle(manifest_path)
    bundle_json = audit_manifest.create_review_bundle_json(manifest_path)

    assert json.loads(bundle_json) == bundle


def test_create_review_bundle_is_deterministic_for_same_inputs(monkeypatch, tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    previous_path = audit_dir / "previous.json"
    previous_payload = _write_audit_manifest(previous_path, project_root=project)
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(
        manifest_path,
        project_root=project,
        previous_manifest_sha256=str(previous_payload["manifest_sha256"]),
        created_at="2026-03-24T12:00:00Z",
    )
    checkpoint = create_checkpoint(str(project))

    created_at_values = iter(("2026-03-29T10:00:00Z", "2026-03-29T10:00:01Z"))
    monkeypatch.setattr(audit_manifest, "_utc_now_iso", lambda: next(created_at_values))

    first = audit_manifest.create_review_bundle(
        manifest_path,
        checkpoint_id=checkpoint.checkpoint_id,
        previous_manifest=previous_path,
    )
    second = audit_manifest.create_review_bundle(
        manifest_path,
        checkpoint_id=checkpoint.checkpoint_id,
        previous_manifest=previous_path,
    )

    assert second == first


def test_create_review_bundle_raises_for_missing_checkpoint(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)

    try:
        audit_manifest.create_review_bundle(manifest_path, checkpoint_id="ckpt-missing")
    except FileNotFoundError as exc:
        assert "Checkpoint not found" in str(exc)
    else:
        raise AssertionError("Expected FileNotFoundError")


def test_verify_review_bundle_accepts_pristine_bundle(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest.create_review_bundle(manifest_path, output_path=bundle_path)

    payload = audit_manifest.verify_review_bundle(bundle_path)

    assert payload["routing_backend"] == "AuditManifest"
    assert payload["routing_reason"] == "review-bundle-verify"
    assert payload["bundle_path"] == str(bundle_path.resolve())
    assert payload["valid"] is True
    assert payload["checks"]["audit_manifest"]["valid"] is True
    assert payload["bundle_integrity"]["valid"] is True


def test_verify_review_bundle_treats_missing_optional_components_as_valid(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest.create_review_bundle(manifest_path, output_path=bundle_path)

    payload = audit_manifest.verify_review_bundle(bundle_path)

    assert payload["checks"]["scan_results"] == {
        "expected": None,
        "actual": None,
        "valid": True,
    }
    assert payload["checks"]["checkpoint_metadata"] == {
        "expected": None,
        "actual": None,
        "valid": True,
    }
    assert payload["checks"]["diff"] == {
        "expected": None,
        "actual": None,
        "valid": True,
    }


def test_verify_review_bundle_detects_tampered_component_content(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest.create_review_bundle(manifest_path, output_path=bundle_path)

    tampered = json.loads(bundle_path.read_text(encoding="utf-8"))
    tampered["audit_manifest"]["kind"] = "tampered-manifest"
    bundle_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")

    payload = audit_manifest.verify_review_bundle(bundle_path)

    assert payload["valid"] is False
    assert payload["checks"]["audit_manifest"]["valid"] is False
    assert payload["bundle_integrity"]["valid"] is False


def test_verify_review_bundle_detects_tampered_bundle_hash_only(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest.create_review_bundle(manifest_path, output_path=bundle_path)

    tampered = json.loads(bundle_path.read_text(encoding="utf-8"))
    tampered["bundle_sha256"] = "0" * 64
    bundle_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")

    payload = audit_manifest.verify_review_bundle(bundle_path)

    assert payload["checks"]["audit_manifest"]["valid"] is True
    assert payload["bundle_integrity"]["valid"] is False
    assert payload["valid"] is False


def test_verify_review_bundle_detects_missing_checksum_for_present_component(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    scan_path = project / "scan.json"
    _write_scan_results(scan_path)
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest.create_review_bundle(manifest_path, scan_path=scan_path, output_path=bundle_path)

    tampered = json.loads(bundle_path.read_text(encoding="utf-8"))
    del tampered["checksums"]["scan_results"]
    bundle_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")

    payload = audit_manifest.verify_review_bundle(bundle_path)

    assert payload["checks"]["scan_results"]["expected"] is None
    assert payload["checks"]["scan_results"]["actual"] is not None
    assert payload["checks"]["scan_results"]["valid"] is False
    assert payload["valid"] is False


def test_verify_review_bundle_json_matches_python_payload(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "current.json"
    _write_audit_manifest(manifest_path, project_root=project)
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest.create_review_bundle(manifest_path, output_path=bundle_path)

    payload = audit_manifest.verify_review_bundle(bundle_path)
    payload_json = audit_manifest.verify_review_bundle_json(bundle_path)

    assert json.loads(payload_json) == payload
