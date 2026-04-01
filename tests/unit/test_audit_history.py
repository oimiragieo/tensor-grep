import hashlib
import hmac
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
    project_root: Path,
    previous_manifest_sha256: str | None = None,
    created_at: str | None = "2026-03-23T12:00:00Z",
    signing_key: bytes | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
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
    if created_at is not None:
        payload["created_at"] = created_at
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    audit_dir = project / ".tensor-grep" / "audit"
    audit_dir.mkdir(parents=True)
    return project, audit_dir


def _index_path(project: Path) -> Path:
    return project / ".tensor-grep" / "audit" / "index.json"


def _read_index(project: Path) -> dict[str, object]:
    return json.loads(_index_path(project).read_text(encoding="utf-8"))


def test_list_audit_history_initializes_index_from_scan_and_orders_newest_first(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    first_payload = _write_audit_manifest(
        audit_dir / "2026-03-23-1.json",
        project_root=project,
        created_at="2026-03-23T12:00:00Z",
    )
    second_payload = _write_audit_manifest(
        audit_dir / "2026-03-24-2.json",
        project_root=project,
        previous_manifest_sha256=str(first_payload["manifest_sha256"]),
        created_at="2026-03-24T12:00:00Z",
    )

    history = audit_manifest.list_audit_history(project)

    assert [entry["manifest_sha256"] for entry in history] == [
        second_payload["manifest_sha256"],
        first_payload["manifest_sha256"],
    ]
    index_payload = _read_index(project)
    assert index_payload["version"] == 1
    assert len(index_payload["manifests"]) == 2
    assert index_payload["updated_at"].endswith("Z")


def test_list_audit_history_returns_empty_list_for_empty_directory(tmp_path: Path):
    project, _ = _make_project(tmp_path)

    history = audit_manifest.list_audit_history(project)

    assert history == []
    index_payload = _read_index(project)
    assert index_payload["version"] == 1
    assert index_payload["manifests"] == []


def test_list_audit_history_flags_missing_timestamp_for_legacy_manifest(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    payload = _write_audit_manifest(
        audit_dir / "legacy.json",
        project_root=project,
        created_at=None,
    )

    history = audit_manifest.list_audit_history(project)

    assert history == [
        {
            "manifest_sha256": payload["manifest_sha256"],
            "kind": "rewrite-audit-manifest",
            "created_at": None,
            "file_path": str((audit_dir / "legacy.json").resolve()),
            "previous_manifest_sha256": None,
            "missing_timestamp": True,
            "chain_gap": False,
            "signature_kind": None,
        }
    ]


def test_list_audit_history_flags_chain_gaps(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    missing_previous = "f" * 64
    payload = _write_audit_manifest(
        audit_dir / "gap.json",
        project_root=project,
        previous_manifest_sha256=missing_previous,
        created_at="2026-03-24T12:00:00Z",
    )

    history = audit_manifest.list_audit_history(project)

    assert history[0]["manifest_sha256"] == payload["manifest_sha256"]
    assert history[0]["chain_gap"] is True
    assert history[0]["previous_manifest_sha256"] == missing_previous


def test_list_audit_history_includes_signature_kind_for_signed_manifests(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    _write_audit_manifest(
        audit_dir / "unsigned.json",
        project_root=project,
        created_at="2026-03-23T12:00:00Z",
    )
    signed_payload = _write_audit_manifest(
        audit_dir / "signed.json",
        project_root=project,
        created_at="2026-03-24T12:00:00Z",
        signing_key=b"secret",
    )

    history = audit_manifest.list_audit_history(project)

    signed_entry = next(
        entry for entry in history if entry["manifest_sha256"] == signed_payload["manifest_sha256"]
    )
    assert signed_entry["signature_kind"] == "hmac-sha256"


def test_verify_audit_manifest_updates_history_index(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "rewrite-audit.json"
    payload = _write_audit_manifest(manifest_path, project_root=project)

    result = audit_manifest.verify_audit_manifest(manifest_path)

    assert result["valid"] is True
    index_payload = _read_index(project)
    assert index_payload["manifests"] == [
        {
            "manifest_sha256": payload["manifest_sha256"],
            "kind": "rewrite-audit-manifest",
            "created_at": "2026-03-23T12:00:00Z",
            "file_path": str(manifest_path.resolve()),
            "previous_manifest_sha256": None,
        }
    ]


def test_verify_audit_manifest_tolerates_manifests_without_created_at(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "legacy.json"
    _write_audit_manifest(manifest_path, project_root=project, created_at=None)

    result = audit_manifest.verify_audit_manifest(manifest_path)

    assert result["valid"] is True
    assert result["errors"] == []


def test_verify_audit_manifest_refreshes_existing_index_entry_without_duplicates(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    manifest_path = audit_dir / "rewrite-audit.json"
    _write_audit_manifest(manifest_path, project_root=project)

    audit_manifest.verify_audit_manifest(manifest_path)
    audit_manifest.verify_audit_manifest(manifest_path)

    index_payload = _read_index(project)
    assert len(index_payload["manifests"]) == 1


def test_list_audit_history_discovers_manifests_in_nested_audit_directories(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    payload = _write_audit_manifest(
        audit_dir / "nested" / "rewrite-audit.json",
        project_root=project,
    )

    history = audit_manifest.list_audit_history(project)

    assert history[0]["manifest_sha256"] == payload["manifest_sha256"]
    assert Path(history[0]["file_path"]).as_posix().endswith("nested/rewrite-audit.json")


def test_list_audit_history_json_matches_python_payload(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    _write_audit_manifest(audit_dir / "rewrite-audit.json", project_root=project)

    history = audit_manifest.list_audit_history(project)
    history_json = audit_manifest.list_audit_history_json(project)

    assert json.loads(history_json) == history


def test_list_audit_history_follows_chain_order_instead_of_file_name_order(tmp_path: Path):
    project, audit_dir = _make_project(tmp_path)
    first_payload = _write_audit_manifest(
        audit_dir / "z-last-name.json",
        project_root=project,
        created_at="2026-03-23T12:00:00Z",
    )
    second_payload = _write_audit_manifest(
        audit_dir / "a-first-name.json",
        project_root=project,
        previous_manifest_sha256=str(first_payload["manifest_sha256"]),
        created_at="2026-03-24T12:00:00Z",
    )

    history = audit_manifest.list_audit_history(project)

    assert [entry["manifest_sha256"] for entry in history] == [
        second_payload["manifest_sha256"],
        first_payload["manifest_sha256"],
    ]
