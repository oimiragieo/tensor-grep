"""Regression tests for audit C4 (CWE-59 symlink-follow write + non-atomic write-integrity).

Two CLI writers used to emit a caller-specified output path via a bare
``resolved.write_text(json.dumps(...), encoding="utf-8")`` -- no symlink refusal, no atomic
temp+rename:

  * ``tg evidence emit --out <path>``            (tensor_grep.cli.main.evidence_emit)
  * ``tg review-bundle create --output <path>``  (tensor_grep.cli.audit_manifest.create_review_bundle)

A pre-existing symlink at the destination let ``write_text`` follow it and overwrite the
symlink's TARGET (CWE-59); a crash/kill mid-write had no atomic fallback and could publish a
truncated receipt/bundle. Both sites now route through the same hardened
``session_store._write_json_atomic`` helper already used for session/daemon state, extended
here with a symlink-refusal guard mirroring ``evidence_signing._write_private_key_atomic``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import audit_manifest, session_store
from tensor_grep.cli.main import app

# ---------------------------------------------------------------------------
# Shared fixtures (mirrors tests/unit/test_review_bundles.py's manifest shape)
# ---------------------------------------------------------------------------


def _canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return json.dumps(canonical, indent=2).encode("utf-8")


def _write_audit_manifest(path: Path, *, project_root: Path) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-03-23T12:00:00Z",
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
        "previous_manifest_sha256": None,
    }
    payload["manifest_sha256"] = hashlib.sha256(_canonical_manifest_bytes(payload)).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".tensor-grep" / "audit").mkdir(parents=True)
    (project / "src").mkdir(parents=True)
    (project / "src" / "sample.py").write_text("print('hello')\n", encoding="utf-8")
    return project


def _symlink_or_skip(link_path: Path, target: Path) -> None:
    try:
        link_path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported / not privileged on this platform")


# ---------------------------------------------------------------------------
# 1. Writing to a path that is a pre-existing SYMLINK is REFUSED, and the symlink's target is
#    never touched, for BOTH `evidence emit --out` and `audit bundle-create --output` (a.k.a.
#    `review-bundle create --output`, the actual registered command name -- see
#    tensor_grep.cli.main.review_bundle_create / app.add_typer(review_bundle_app,
#    name="review-bundle")).
# ---------------------------------------------------------------------------


def test_create_review_bundle_refuses_to_write_through_a_symlink(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    manifest_path = project / ".tensor-grep" / "audit" / "manifest.json"
    _write_audit_manifest(manifest_path, project_root=project)

    real_target = tmp_path / "real_target.txt"
    real_target.write_text("do-not-touch-me", encoding="utf-8")
    link_path = tmp_path / "bundle_output.json"
    _symlink_or_skip(link_path, real_target)

    with pytest.raises(OSError, match="symlink"):
        audit_manifest.create_review_bundle(manifest_path, output_path=link_path)

    # The refused attempt must leave the symlink -- and its target's content -- untouched.
    assert link_path.is_symlink()
    assert real_target.read_text(encoding="utf-8") == "do-not-touch-me"


def test_evidence_emit_cli_refuses_to_write_through_a_symlink(tmp_path: Path) -> None:
    real_target = tmp_path / "real_target.txt"
    real_target.write_text("do-not-touch-me", encoding="utf-8")
    link_path = tmp_path / "receipt.json"
    _symlink_or_skip(link_path, real_target)

    repo = tmp_path / "repo"
    repo.mkdir()

    result = CliRunner().invoke(app, ["evidence", "emit", str(repo), "--out", str(link_path)])

    assert result.exit_code != 0
    # A clean, fail-closed CLI error -- never an unhandled exception/raw traceback.
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert real_target.read_text(encoding="utf-8") == "do-not-touch-me"
    assert link_path.is_symlink()


def test_evidence_emit_cli_refuses_symlink_cleanly_in_json_mode(tmp_path: Path) -> None:
    """The --json error envelope must also fail closed (not just the text-mode path)."""
    real_target = tmp_path / "real_target.txt"
    real_target.write_text("do-not-touch-me", encoding="utf-8")
    link_path = tmp_path / "receipt.json"
    _symlink_or_skip(link_path, real_target)

    repo = tmp_path / "repo"
    repo.mkdir()

    result = CliRunner().invoke(
        app, ["evidence", "emit", str(repo), "--out", str(link_path), "--json"]
    )

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    error_payload = json.loads(result.output)
    assert error_payload["error"]["code"] == "write_error"
    assert "symlink" in error_payload["error"]["message"]
    assert real_target.read_text(encoding="utf-8") == "do-not-touch-me"


# ---------------------------------------------------------------------------
# 2. A normal write still produces a valid, complete JSON file.
# ---------------------------------------------------------------------------


def test_create_review_bundle_writes_a_valid_complete_json_file(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    manifest_path = project / ".tensor-grep" / "audit" / "manifest.json"
    _write_audit_manifest(manifest_path, project_root=project)
    output_path = tmp_path / "bundle.json"

    bundle = audit_manifest.create_review_bundle(manifest_path, output_path=output_path)

    on_disk = json.loads(output_path.read_text(encoding="utf-8"))
    assert on_disk == bundle
    assert on_disk["bundle_sha256"] == bundle["bundle_sha256"]


def test_evidence_emit_cli_writes_a_valid_complete_json_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    output_path = tmp_path / "receipt.json"

    result = CliRunner().invoke(app, ["evidence", "emit", str(repo), "--out", str(output_path)])

    assert result.exit_code == 0, result.output
    on_disk = json.loads(output_path.read_text(encoding="utf-8"))
    assert on_disk["kind"] == "evidence-receipt"
    assert "receipt_sha256" in on_disk


def test_create_review_bundle_write_still_creates_parent_dirs(tmp_path: Path) -> None:
    """Preserve the pre-fix behavior of creating missing parent directories."""
    project = _make_project(tmp_path)
    manifest_path = project / ".tensor-grep" / "audit" / "manifest.json"
    _write_audit_manifest(manifest_path, project_root=project)
    output_path = tmp_path / "nested" / "dir" / "bundle.json"

    audit_manifest.create_review_bundle(manifest_path, output_path=output_path)

    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8"))["bundle_sha256"]


# ---------------------------------------------------------------------------
# 3. The write is atomic: a simulated crash mid-write never leaves a partial/corrupted file at
#    the destination. Both C4 sites now route through the exact same shared helper
#    (session_store._write_json_atomic) for their disk I/O, so exercising it here through the
#    real `create_review_bundle` entry point covers both -- and proves the write genuinely goes
#    through the fsync'd temp-then-rename path (patching `os.fsync` has NO effect on the
#    pre-fix bare `write_text`, which never called fsync at all).
# ---------------------------------------------------------------------------


def test_create_review_bundle_write_is_atomic_on_simulated_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    manifest_path = project / ".tensor-grep" / "audit" / "manifest.json"
    _write_audit_manifest(manifest_path, project_root=project)
    output_path = tmp_path / "bundle.json"
    output_path.write_text('{"stale": "pre-existing-valid-json"}', encoding="utf-8")

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated crash before fsync completes")

    monkeypatch.setattr(session_store.os, "fsync", _boom)

    with pytest.raises(OSError):
        audit_manifest.create_review_bundle(manifest_path, output_path=output_path)

    # The destination must still hold the OLD, complete content -- never truncated/partial.
    assert output_path.read_text(encoding="utf-8") == '{"stale": "pre-existing-valid-json"}'
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "stale": "pre-existing-valid-json"
    }


# ---------------------------------------------------------------------------
# 4. Shared-helper unit coverage: `_write_json_atomic` itself refuses a symlinked destination
#    without disturbing its existing callers' contract (mode=None default write, mode=0o600
#    restrictive write used by session_daemon's IPC token file).
# ---------------------------------------------------------------------------


def test_write_json_atomic_refuses_to_write_through_a_symlink(tmp_path: Path) -> None:
    real_target = tmp_path / "real.json"
    real_target.write_text('{"untouched": true}', encoding="utf-8")
    link_path = tmp_path / "link.json"
    _symlink_or_skip(link_path, real_target)

    with pytest.raises(OSError, match="symlink"):
        session_store._write_json_atomic(link_path, {"attacker": "payload"})

    assert link_path.is_symlink()
    assert json.loads(real_target.read_text(encoding="utf-8")) == {"untouched": True}


def test_write_json_atomic_existing_callers_unaffected_default_mode(tmp_path: Path) -> None:
    dest = tmp_path / "payload.json"

    session_store._write_json_atomic(dest, {"a": 1, "b": [1, 2, 3]})

    assert json.loads(dest.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2, 3]}


def test_write_json_atomic_existing_callers_unaffected_restrictive_mode(tmp_path: Path) -> None:
    """Regression guard: extending `_write_json_atomic` with a symlink guard must not disturb
    the existing `mode=` callers (e.g. session_daemon's 0600 IPC token file)."""
    dest = tmp_path / "secret.json"

    session_store._write_json_atomic(dest, {"token": "abc"}, mode=0o600)

    assert json.loads(dest.read_text(encoding="utf-8")) == {"token": "abc"}


def test_write_json_atomic_overwrite_of_a_regular_file_still_succeeds(tmp_path: Path) -> None:
    """A regular (non-symlink) pre-existing file at the destination must still be overwritable
    -- the guard targets symlinks specifically, not "anything already there"."""
    dest = tmp_path / "existing.json"
    dest.write_text('{"old": true}', encoding="utf-8")

    session_store._write_json_atomic(dest, {"new": True})

    assert json.loads(dest.read_text(encoding="utf-8")) == {"new": True}


# ---------------------------------------------------------------------------
# 5. task #211 (the C4/#659 residual this file's name predates): audit_manifest.
#    _write_history_index was a bare `write_text` -- no symlink refusal, no atomic temp+rename,
#    no fsync at all -- even though it persists the tamper-evident audit-history index
#    (.tensor-grep/audit/index.json) that record_audit_manifest/list_audit_history build the
#    manifest chain from. Now routed through the same shared `_index_lock.atomic_write_json`
#    helper as every other writer covered by this file.
# ---------------------------------------------------------------------------


def test_write_history_index_refuses_to_write_through_a_symlink(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    real_target = tmp_path / "real.json"
    real_target.write_text('{"untouched": true}', encoding="utf-8")
    history_dir = root / ".tensor-grep" / "audit"
    history_dir.mkdir(parents=True)
    link_path = history_dir / "index.json"
    _symlink_or_skip(link_path, real_target)

    with pytest.raises(OSError, match="symlink"):
        audit_manifest._write_history_index(root, [])

    assert link_path.is_symlink()
    assert json.loads(real_target.read_text(encoding="utf-8")) == {"untouched": True}


def test_write_history_index_normal_write_still_succeeds(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    entry = {
        "manifest_sha256": "a" * 64,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-01-01T00:00:00Z",
        "file_path": str(root / "manifest.json"),
        "previous_manifest_sha256": None,
    }

    audit_manifest._write_history_index(root, [entry])

    index_path = root / ".tensor-grep" / "audit" / "index.json"
    on_disk = json.loads(index_path.read_text(encoding="utf-8"))
    assert on_disk["manifests"] == [entry]


def test_record_audit_manifest_refuses_when_history_index_is_a_pre_existing_symlink(
    tmp_path: Path,
) -> None:
    """End-to-end proof through the real ``record_audit_manifest`` entry point (reached from
    ``verify_audit_manifest`` on every successful verification, main.py's `tg run
    --audit-manifest`, and `tg audit record`): a pre-existing symlink at
    .tensor-grep/audit/index.json must fail the write closed instead of silently replacing it."""
    project = _make_project(tmp_path)
    manifest_path = project / ".tensor-grep" / "audit" / "manifest.json"
    manifest = _write_audit_manifest(manifest_path, project_root=project)

    real_target = tmp_path / "real.json"
    real_target.write_text('{"untouched": true}', encoding="utf-8")
    index_path = project / ".tensor-grep" / "audit" / "index.json"
    _symlink_or_skip(index_path, real_target)

    with pytest.raises(OSError, match="symlink"):
        audit_manifest.record_audit_manifest(manifest_path, manifest=manifest)

    assert index_path.is_symlink()
    assert json.loads(real_target.read_text(encoding="utf-8")) == {"untouched": True}
