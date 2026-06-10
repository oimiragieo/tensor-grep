"""Regression tests for the audit-manifest canonicalization unification (audit C1/C2/M5).

The native Rust writer hashes the manifest via ``serde_json::to_value`` (a key-sorted map)
plus ``to_vec_pretty``, which is byte-for-byte equivalent to
``json.dumps(canonical, indent=2, sort_keys=True)``. Before the fix the Python verifier
canonicalized WITHOUT ``sort_keys``, so a manifest written by ``tg run --audit-manifest``
failed its own ``tg audit-verify`` (digest_valid=false) and signed manifests failed signature
verification. These tests reproduce the writer scheme purely in Python (no compiled extension)
and assert the verifier now accepts it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from tensor_grep.cli.audit_manifest import (
    _canonical_manifest_bytes,
    _parse_timestamp,
    verify_audit_manifest,
)


def _base_manifest() -> dict[str, Any]:
    # Field order intentionally NOT alphabetical, mirroring the Rust struct declaration order
    # that lands on disk via `to_vec_pretty(&manifest)`. The digest must be independent of it.
    return {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-06-10T14:39:06Z",
        "lang": "python",
        "path": "a.py",
        "plan_total_edits": 1,
        "applied_edit_ids": ["e0000:a.py:15-29"],
        "previous_manifest_sha256": None,
        "checkpoint": None,
        "validation": None,
        "files": [
            {
                "path": "a.py",
                "edit_ids": ["e0000:a.py:15-29"],
                "before_sha256": "7a2362d8" + "0" * 56,
                "after_sha256": "0bc39355" + "0" * 56,
            }
        ],
    }


def _writer_digest(manifest: dict[str, Any]) -> str:
    # Emulates the Rust writer: sorted-key, indent=2 canonical bytes.
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return hashlib.sha256(
        json.dumps(canonical, indent=2, sort_keys=True).encode("utf-8")
    ).hexdigest()


def test_canonical_bytes_use_sorted_keys() -> None:
    manifest = _base_manifest()
    canonical = _canonical_manifest_bytes(manifest)
    # sort_keys=True means applied_edit_ids precedes version regardless of insertion order.
    text = canonical.decode("utf-8")
    assert text.index('"applied_edit_ids"') < text.index('"version"')
    # The verifier's canonicalization must equal the writer's byte-for-byte.
    assert canonical == json.dumps(
        {k: v for k, v in manifest.items() if k not in {"manifest_sha256", "signature"}},
        indent=2,
        sort_keys=True,
    ).encode("utf-8")


def test_unsigned_roundtrip_is_valid(tmp_path: Path) -> None:
    manifest = _base_manifest()
    manifest["manifest_sha256"] = _writer_digest(manifest)

    manifest_path = tmp_path / "m.json"
    # Write in field order (as the Rust writer does) to prove the digest does not depend on
    # the on-disk key ordering.
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = verify_audit_manifest(manifest_path)
    assert result["checks"]["digest_valid"] is True
    assert result["valid"] is True
    assert result["errors"] == []


def test_signed_roundtrip_signature_valid(tmp_path: Path) -> None:
    key_path = tmp_path / "key.bin"
    key_bytes = b"supersecretkeymaterial0123456789"
    key_path.write_bytes(key_bytes)

    manifest = _base_manifest()
    canonical = _canonical_manifest_bytes(manifest)
    manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    manifest["signature"] = {
        "kind": "hmac-sha256",
        "key_path": str(key_path),
        # HMAC over EXACTLY the canonical bytes the verifier reconstructs (audit C2).
        "value": hmac.new(key_bytes, canonical, hashlib.sha256).hexdigest(),
    }

    manifest_path = tmp_path / "signed.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = verify_audit_manifest(manifest_path, signing_key=key_path)
    assert result["checks"]["digest_valid"] is True
    assert result["checks"]["signature_valid"] is True
    assert result["signature_kind"] == "hmac-sha256"
    assert result["valid"] is True


def test_bare_epoch_created_at_breaks_time_ordering_iso_does_not() -> None:
    # M5: a bare epoch string parses to None (ties in audit-history), while the ISO-8601 form
    # the fixed Rust writer emits parses to a real instant.
    assert _parse_timestamp("1781102346") is None
    parsed = _parse_timestamp("2026-06-10T14:39:06Z")
    assert parsed is not None
    assert parsed.year == 2026 and parsed.month == 6 and parsed.day == 10


def test_tampered_manifest_fails_digest(tmp_path: Path) -> None:
    manifest = _base_manifest()
    manifest["manifest_sha256"] = _writer_digest(manifest)
    # Tamper after the digest is computed.
    manifest["lang"] = "rust"

    manifest_path = tmp_path / "tampered.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = verify_audit_manifest(manifest_path)
    assert result["checks"]["digest_valid"] is False
    assert result["valid"] is False
