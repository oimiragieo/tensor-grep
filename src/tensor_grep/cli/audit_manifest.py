from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any


def _json_output_version() -> int:
    main_rs = Path(__file__).resolve().parents[3] / "rust_core" / "src" / "main.rs"
    try:
        match = re.search(
            r"const\s+JSON_OUTPUT_VERSION\s*:\s*u32\s*=\s*(\d+)\s*;",
            main_rs.read_text(encoding="utf-8"),
        )
    except OSError:
        match = None
    return int(match.group(1)) if match else 1


def _envelope() -> dict[str, Any]:
    return {
        "version": _json_output_version(),
        "routing_backend": "AuditManifest",
        "routing_reason": "audit-manifest-verify",
        "sidecar_used": False,
    }


def _canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return json.dumps(canonical, indent=2).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _previous_manifest_digest(path: Path) -> str:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return _sha256_hex(raw)
    if isinstance(payload, dict):
        digest = payload.get("manifest_sha256")
        if isinstance(digest, str) and digest:
            return digest
    return _sha256_hex(raw)


def verify_audit_manifest(
    manifest_path: str | Path,
    *,
    signing_key: str | Path | None = None,
    previous_manifest: str | Path | None = None,
) -> dict[str, Any]:
    resolved_manifest = Path(manifest_path).expanduser().resolve()
    if not resolved_manifest.exists():
        raise FileNotFoundError(f"Audit manifest not found: {resolved_manifest}")

    manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Audit manifest must be a JSON object.")

    canonical_bytes = _canonical_manifest_bytes(manifest)
    expected_digest = _sha256_hex(canonical_bytes)
    stored_digest = manifest.get("manifest_sha256")
    digest_valid = isinstance(stored_digest, str) and stored_digest == expected_digest

    previous_manifest_sha256 = manifest.get("previous_manifest_sha256")
    previous_manifest_path = (
        str(Path(previous_manifest).expanduser().resolve()) if previous_manifest is not None else None
    )
    chain_valid = True
    chain_error: str | None = None
    if previous_manifest_sha256 is not None:
        if not isinstance(previous_manifest_sha256, str) or not previous_manifest_sha256:
            chain_valid = False
            chain_error = "Manifest previous_manifest_sha256 must be a non-empty string when present."
        elif previous_manifest is None:
            chain_valid = False
            chain_error = "Manifest chain digest is present but no previous manifest was provided."
        else:
            previous_path = Path(previous_manifest).expanduser().resolve()
            if not previous_path.exists():
                chain_valid = False
                chain_error = f"Previous manifest not found: {previous_path}"
            else:
                chain_valid = previous_manifest_sha256 == _previous_manifest_digest(previous_path)
                if not chain_valid:
                    chain_error = "Previous manifest digest does not match previous_manifest_sha256."

    signature = manifest.get("signature")
    signature_valid = True
    signature_kind: str | None = None
    signature_key_path: str | None = None
    signature_error: str | None = None
    if signature is not None:
        if not isinstance(signature, dict):
            signature_valid = False
            signature_error = "Manifest signature must be an object."
        else:
            signature_kind = str(signature.get("kind") or "")
            signature_value = str(signature.get("value") or "")
            signature_key_path = (
                str(Path(signing_key).expanduser().resolve())
                if signing_key is not None
                else str(signature.get("key_path") or "")
            )
            if signature_kind != "hmac-sha256":
                signature_valid = False
                signature_error = f"Unsupported signature kind: {signature_kind or '<empty>'}"
            elif not signature_key_path:
                signature_valid = False
                signature_error = "Signed manifest requires a signing key path."
            else:
                key_path = Path(signature_key_path).expanduser().resolve()
                if not key_path.exists():
                    signature_valid = False
                    signature_error = f"Signing key not found: {key_path}"
                else:
                    actual_signature = hmac.new(
                        key_path.read_bytes(),
                        canonical_bytes,
                        hashlib.sha256,
                    ).hexdigest()
                    signature_valid = hmac.compare_digest(signature_value, actual_signature)
                    if not signature_valid:
                        signature_error = "Manifest signature does not match the supplied signing key."
    elif signing_key is not None:
        signature_valid = False
        signature_error = "Signing key was provided but the manifest is unsigned."

    errors = [message for message in [chain_error, signature_error] if message]
    if not digest_valid:
        errors.insert(0, "Manifest digest does not match manifest_sha256.")

    payload = _envelope()
    payload["manifest_path"] = str(resolved_manifest)
    payload["signing_key_path"] = signature_key_path
    payload["previous_manifest_path"] = previous_manifest_path
    payload["kind"] = manifest.get("kind")
    payload["manifest_sha256"] = stored_digest
    payload["previous_manifest_sha256"] = previous_manifest_sha256
    payload["checks"] = {
        "digest_valid": digest_valid,
        "chain_valid": chain_valid,
        "signature_valid": signature_valid,
    }
    payload["signature_kind"] = signature_kind
    payload["valid"] = digest_valid and chain_valid and signature_valid
    payload["errors"] = errors
    return payload


def verify_audit_manifest_json(
    manifest_path: str | Path,
    *,
    signing_key: str | Path | None = None,
    previous_manifest: str | Path | None = None,
) -> str:
    return json.dumps(
        verify_audit_manifest(
            manifest_path,
            signing_key=signing_key,
            previous_manifest=previous_manifest,
        ),
        indent=2,
    )
