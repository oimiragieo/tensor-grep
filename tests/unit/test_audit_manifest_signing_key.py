"""The audit-manifest verifier must not trust a key path embedded in the manifest (S2).

When no out-of-band signing key is supplied, an hmac-sha256 signed manifest must verify
as INVALID rather than reading the key from ``signature.key_path`` inside the manifest
being verified (which a tamperer controls). This guards the pure-Python verifier used by
the CLI ``audit-verify`` command and the MCP ``tg_audit_manifest_verify`` tool.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from tensor_grep.cli import audit_manifest


def _write_signed_manifest(path: Path, signing_key: bytes, embedded_key_path: str) -> None:
    payload: dict[str, object] = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-03-23T12:00:00Z",
        "lang": "python",
        "path": str(path.parent),
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
    canonical = audit_manifest._canonical_manifest_bytes(payload)
    payload["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    payload["signature"] = {
        "kind": "hmac-sha256",
        "key_path": embedded_key_path,
        "value": hmac.new(signing_key, canonical, hashlib.sha256).hexdigest(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_signed_manifest_with_correct_out_of_band_key_is_valid(tmp_path: Path) -> None:
    key = tmp_path / "audit.key"
    key.write_bytes(b"top-secret")
    manifest = tmp_path / "rewrite-audit.json"
    _write_signed_manifest(manifest, b"top-secret", embedded_key_path=str(key))

    result = audit_manifest.verify_audit_manifest(manifest, signing_key=key)
    assert result["checks"]["signature_valid"] is True
    assert result["valid"] is True


def test_signed_manifest_without_key_refuses_embedded_key_path(tmp_path: Path) -> None:
    # The attacker controls both the manifest AND the embedded key file it points at.
    attacker_key = tmp_path / "attacker.key"
    attacker_key.write_bytes(b"attacker-key")
    manifest = tmp_path / "rewrite-audit.json"
    _write_signed_manifest(manifest, b"attacker-key", embedded_key_path=str(attacker_key))

    # No out-of-band key supplied: verification must NOT trust the embedded key_path,
    # even though the HMAC would "match" that attacker-chosen key.
    result = audit_manifest.verify_audit_manifest(manifest)
    assert result["checks"]["signature_valid"] is False
    assert result["valid"] is False
    assert any("refusing to trust" in e for e in result["errors"])


def test_signed_manifest_with_wrong_key_is_invalid(tmp_path: Path) -> None:
    real_key = tmp_path / "audit.key"
    real_key.write_bytes(b"real-secret")
    wrong_key = tmp_path / "wrong.key"
    wrong_key.write_bytes(b"wrong-secret")
    manifest = tmp_path / "rewrite-audit.json"
    _write_signed_manifest(manifest, b"real-secret", embedded_key_path=str(real_key))

    result = audit_manifest.verify_audit_manifest(manifest, signing_key=wrong_key)
    assert result["checks"]["signature_valid"] is False
