from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_AUDIT_INDEX_VERSION = 1
_TG_DIRNAME = ".tensor-grep"
_AUDIT_SUBDIR = "audit"
_AUDIT_INDEX_FILE = "index.json"
_AUDIT_DIFF_IGNORED_KEYS = frozenset({"manifest_sha256", "signature"})
_REVIEW_BUNDLE_COMPONENTS = (
    "audit_manifest",
    "scan_results",
    "checkpoint_metadata",
    "diff",
)
_REVIEW_BUNDLE_REQUIRED_COMPONENTS = frozenset({"audit_manifest"})


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


def _envelope(*, routing_reason: str = "audit-manifest-verify") -> dict[str, Any]:
    return {
        "version": _json_output_version(),
        "routing_backend": "AuditManifest",
        "routing_reason": routing_reason,
        "sidecar_used": False,
    }


def _canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    canonical.pop("signature", None)
    return json.dumps(canonical, indent=2).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _resolve_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    return resolved if resolved.is_dir() else resolved.parent


def _audit_dir(root: Path) -> Path:
    return root / _TG_DIRNAME / _AUDIT_SUBDIR


def _history_index_path(root: Path) -> Path:
    return _audit_dir(root) / _AUDIT_INDEX_FILE


def _read_manifest_object(manifest_path: Path) -> dict[str, Any]:
    resolved = manifest_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Audit manifest not found: {resolved}")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Audit manifest must be a JSON object.")
    return payload


def _read_json_value(path: str | Path, *, description: str) -> Any:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"{description} not found: {resolved}")
    return json.loads(resolved.read_text(encoding="utf-8"))


def _read_review_bundle_object(bundle_path: str | Path) -> dict[str, Any]:
    resolved = Path(bundle_path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Review bundle not found: {resolved}")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Review bundle must be a JSON object.")
    return payload


def _canonical_review_bundle_bytes(bundle: dict[str, Any]) -> bytes:
    canonical = dict(bundle)
    canonical.pop("bundle_sha256", None)
    return _canonical_json_bytes(canonical)


def _component_checksum(value: Any) -> str:
    return _sha256_hex(_canonical_json_bytes(value))


def create_review_bundle(
    manifest_path: str | Path,
    *,
    scan_path: str | Path | None = None,
    checkpoint_id: str | None = None,
    previous_manifest: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    from tensor_grep.cli.checkpoint_store import load_checkpoint_metadata

    resolved_manifest = Path(manifest_path).expanduser().resolve()
    manifest = _read_manifest_object(resolved_manifest)
    root = _resolve_manifest_root(resolved_manifest, manifest)

    scan_results = (
        _read_json_value(scan_path, description="Scan results")
        if scan_path is not None
        else None
    )
    checkpoint_metadata = (
        load_checkpoint_metadata(checkpoint_id, str(root))
        if checkpoint_id is not None
        else None
    )
    diff_payload = (
        diff_audit_manifests(previous_manifest, resolved_manifest)
        if previous_manifest is not None
        else None
    )

    payload = _envelope(routing_reason="review-bundle-create")
    payload["created_at"] = _utc_now_iso()
    payload["audit_manifest"] = manifest
    payload["scan_results"] = scan_results
    payload["checkpoint_metadata"] = checkpoint_metadata
    payload["diff"] = diff_payload

    checksums = {
        component: _component_checksum(payload[component])
        for component in _REVIEW_BUNDLE_COMPONENTS
        if payload[component] is not None
    }
    payload["checksums"] = checksums
    payload["bundle_sha256"] = _sha256_hex(_canonical_review_bundle_bytes(payload))

    if output_path is not None:
        resolved_output = Path(output_path).expanduser().resolve()
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        resolved_output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return payload


def create_review_bundle_json(
    manifest_path: str | Path,
    *,
    scan_path: str | Path | None = None,
    checkpoint_id: str | None = None,
    previous_manifest: str | Path | None = None,
    output_path: str | Path | None = None,
) -> str:
    return json.dumps(
        create_review_bundle(
            manifest_path,
            scan_path=scan_path,
            checkpoint_id=checkpoint_id,
            previous_manifest=previous_manifest,
            output_path=output_path,
        ),
        indent=2,
    )


def verify_review_bundle(bundle_path: str | Path) -> dict[str, Any]:
    resolved_bundle = Path(bundle_path).expanduser().resolve()
    bundle = _read_review_bundle_object(resolved_bundle)
    raw_checksums = bundle.get("checksums")
    checksums = raw_checksums if isinstance(raw_checksums, dict) else {}

    checks: dict[str, dict[str, str | bool | None]] = {}
    for component in _REVIEW_BUNDLE_COMPONENTS:
        expected = _normalize_optional_str(checksums.get(component))
        value = bundle.get(component)
        if value is None:
            actual = None
            valid = (
                component not in _REVIEW_BUNDLE_REQUIRED_COMPONENTS and expected is None
            )
        else:
            actual = _component_checksum(value)
            valid = expected is not None and expected == actual
        checks[component] = {
            "expected": expected,
            "actual": actual,
            "valid": valid,
        }

    expected_bundle_sha256 = _normalize_optional_str(bundle.get("bundle_sha256"))
    actual_bundle_sha256 = _sha256_hex(_canonical_review_bundle_bytes(bundle))
    bundle_integrity = {
        "expected": expected_bundle_sha256,
        "actual": actual_bundle_sha256,
        "valid": expected_bundle_sha256 is not None
        and expected_bundle_sha256 == actual_bundle_sha256,
    }

    payload = _envelope(routing_reason="review-bundle-verify")
    payload["bundle_path"] = str(resolved_bundle)
    payload["valid"] = bundle_integrity["valid"] and all(
        bool(component_check["valid"]) for component_check in checks.values()
    )
    payload["checks"] = checks
    payload["bundle_integrity"] = bundle_integrity
    return payload


def verify_review_bundle_json(bundle_path: str | Path) -> str:
    return json.dumps(verify_review_bundle(bundle_path), indent=2)


def _resolve_history_root(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Path not found: {resolved}")
    if resolved.is_file():
        try:
            return _resolve_manifest_root(resolved, _read_manifest_object(resolved))
        except (OSError, ValueError, json.JSONDecodeError):
            return resolved.parent
    if resolved.name == _AUDIT_SUBDIR and resolved.parent.name == _TG_DIRNAME:
        return resolved.parent.parent
    if resolved.name == _TG_DIRNAME:
        return resolved.parent
    return resolved


def _resolve_manifest_root(manifest_path: Path, manifest: dict[str, Any]) -> Path:
    manifest_root = _normalize_optional_str(manifest.get("path"))
    if manifest_root is not None:
        candidate = Path(manifest_root).expanduser().resolve()
        if candidate.exists():
            return _resolve_root(candidate)

    for ancestor in manifest_path.expanduser().resolve().parents:
        if ancestor.name == _AUDIT_SUBDIR and ancestor.parent.name == _TG_DIRNAME:
            return ancestor.parent.parent
    return manifest_path.expanduser().resolve().parent


def _audit_diff_field_path(parent: str, field: str) -> str:
    return f"{parent}.{field}" if parent else field


def _audit_diff_index_path(parent: str, index: int) -> str:
    return f"{parent}[{index}]" if parent else f"[{index}]"


def _diff_manifest_values(
    previous: Any,
    current: Any,
    *,
    path: str,
    added: dict[str, Any],
    removed: dict[str, Any],
    changed: dict[str, dict[str, Any]],
) -> None:
    if isinstance(previous, dict) and isinstance(current, dict):
        for key in sorted(set(previous) | set(current)):
            if key in _AUDIT_DIFF_IGNORED_KEYS:
                continue
            key_path = _audit_diff_field_path(path, key)
            if key not in previous:
                added[key_path] = current[key]
                continue
            if key not in current:
                removed[key_path] = previous[key]
                continue
            _diff_manifest_values(
                previous[key],
                current[key],
                path=key_path,
                added=added,
                removed=removed,
                changed=changed,
            )
        return

    if isinstance(previous, list) and isinstance(current, list):
        shared_length = min(len(previous), len(current))
        for index in range(shared_length):
            _diff_manifest_values(
                previous[index],
                current[index],
                path=_audit_diff_index_path(path, index),
                added=added,
                removed=removed,
                changed=changed,
            )
        for index in range(shared_length, len(current)):
            added[_audit_diff_index_path(path, index)] = current[index]
        for index in range(shared_length, len(previous)):
            removed[_audit_diff_index_path(path, index)] = previous[index]
        return

    if previous != current:
        changed[path or "$"] = {"old": previous, "new": current}


def diff_manifest_objects(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    added: dict[str, Any] = {}
    removed: dict[str, Any] = {}
    changed: dict[str, dict[str, Any]] = {}
    _diff_manifest_values(
        previous,
        current,
        path="",
        added=added,
        removed=removed,
        changed=changed,
    )
    return {"added": added, "removed": removed, "changed": changed}


def diff_audit_manifests(
    previous_manifest_path: str | Path,
    current_manifest_path: str | Path,
) -> dict[str, dict[str, Any]]:
    previous_manifest = _read_manifest_object(Path(previous_manifest_path))
    current_manifest = _read_manifest_object(Path(current_manifest_path))
    return diff_manifest_objects(previous_manifest, current_manifest)


def diff_audit_manifests_json(
    previous_manifest_path: str | Path,
    current_manifest_path: str | Path,
) -> str:
    return json.dumps(
        diff_audit_manifests(previous_manifest_path, current_manifest_path),
        indent=2,
    )


def _parse_timestamp(value: Any) -> datetime | None:
    raw = _normalize_optional_str(value)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _manifest_entry(manifest_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest_sha256 = _normalize_optional_str(manifest.get("manifest_sha256"))
    if manifest_sha256 is None:
        manifest_sha256 = _sha256_hex(_canonical_manifest_bytes(manifest))
    return {
        "manifest_sha256": manifest_sha256,
        "kind": _normalize_optional_str(manifest.get("kind")),
        "created_at": _normalize_optional_str(manifest.get("created_at")),
        "file_path": str(manifest_path.expanduser().resolve()),
        "previous_manifest_sha256": _normalize_optional_str(
            manifest.get("previous_manifest_sha256")
        ),
    }


def _scan_audit_manifest_entries(root: Path) -> list[dict[str, Any]]:
    audit_dir = _audit_dir(root)
    if not audit_dir.exists():
        return []

    entries: list[dict[str, Any]] = []
    for manifest_path in sorted(audit_dir.rglob("*.json")):
        if manifest_path.name == _AUDIT_INDEX_FILE:
            continue
        try:
            manifest = _read_manifest_object(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        entries.append(_manifest_entry(manifest_path, manifest))
    return entries


def _history_sort_key(entry: dict[str, Any]) -> tuple[datetime, str, str]:
    return (
        _parse_timestamp(entry.get("created_at")) or datetime.min.replace(tzinfo=UTC),
        str(entry.get("manifest_sha256") or ""),
        str(entry.get("file_path") or ""),
    )


def _write_history_index(root: Path, entries: list[dict[str, Any]]) -> None:
    index_path = _history_index_path(root)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _AUDIT_INDEX_VERSION,
        "manifests": sorted(entries, key=_history_sort_key, reverse=True),
        "updated_at": _utc_now_iso(),
    }
    index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_history_index(root: Path) -> list[dict[str, Any]] | None:
    index_path = _history_index_path(root)
    if not index_path.exists():
        return None

    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    manifests = payload.get("manifests")
    if not isinstance(manifests, list):
        return None

    entries: list[dict[str, Any]] = []
    for raw in manifests:
        if not isinstance(raw, dict):
            continue
        manifest_sha256 = _normalize_optional_str(raw.get("manifest_sha256"))
        file_path = _normalize_optional_str(raw.get("file_path"))
        if manifest_sha256 is None or file_path is None:
            continue
        entries.append(
            {
                "manifest_sha256": manifest_sha256,
                "kind": _normalize_optional_str(raw.get("kind")),
                "created_at": _normalize_optional_str(raw.get("created_at")),
                "file_path": file_path,
                "previous_manifest_sha256": _normalize_optional_str(
                    raw.get("previous_manifest_sha256")
                ),
            }
        )
    return entries


def _ensure_history_index(root: Path) -> list[dict[str, Any]]:
    entries = _load_history_index(root)
    if entries is not None:
        return entries

    scanned_entries = _scan_audit_manifest_entries(root)
    _write_history_index(root, scanned_entries)
    return scanned_entries


def _history_entry_identity(entry: dict[str, Any]) -> tuple[str, str, str | None, str | None, str | None]:
    return (
        str(entry["manifest_sha256"]),
        str(entry["file_path"]),
        _normalize_optional_str(entry.get("kind")),
        _normalize_optional_str(entry.get("created_at")),
        _normalize_optional_str(entry.get("previous_manifest_sha256")),
    )


def _upsert_history_entry(
    entries: list[dict[str, Any]], entry: dict[str, Any]
) -> list[dict[str, Any]]:
    manifest_sha256 = str(entry["manifest_sha256"])
    file_path = str(entry["file_path"])
    filtered = [
        existing
        for existing in entries
        if str(existing.get("manifest_sha256")) != manifest_sha256
        and str(existing.get("file_path")) != file_path
    ]
    filtered.append(entry)
    return filtered


def _sync_history_index(root: Path) -> list[dict[str, Any]]:
    entries = _ensure_history_index(root)
    merged = entries
    for scanned_entry in _scan_audit_manifest_entries(root):
        merged = _upsert_history_entry(merged, scanned_entry)

    if sorted(merged, key=_history_entry_identity) != sorted(entries, key=_history_entry_identity):
        _write_history_index(root, merged)
    return merged


def record_audit_manifest(
    manifest_path: str | Path,
    *,
    manifest: dict[str, Any] | None = None,
) -> None:
    resolved_manifest = Path(manifest_path).expanduser().resolve()
    manifest_payload = manifest if manifest is not None else _read_manifest_object(resolved_manifest)
    root = _resolve_manifest_root(resolved_manifest, manifest_payload)
    entries = _sync_history_index(root)
    _write_history_index(
        root,
        _upsert_history_entry(entries, _manifest_entry(resolved_manifest, manifest_payload)),
    )


def _load_signature_kind(file_path: str) -> str | None:
    try:
        manifest = _read_manifest_object(Path(file_path))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    signature = manifest.get("signature")
    if not isinstance(signature, dict):
        return None
    return _normalize_optional_str(signature.get("kind"))


def _order_history_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_sha = {
        str(entry["manifest_sha256"]): entry
        for entry in entries
        if _normalize_optional_str(entry.get("manifest_sha256")) is not None
    }
    referenced = {
        previous_sha
        for entry in entries
        for previous_sha in [_normalize_optional_str(entry.get("previous_manifest_sha256"))]
        if previous_sha is not None and previous_sha in by_sha
    }
    heads = [
        entry
        for entry in entries
        if str(entry.get("manifest_sha256")) not in referenced
    ]

    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_chain(start: dict[str, Any]) -> None:
        current: dict[str, Any] | None = start
        while current is not None:
            current_sha = _normalize_optional_str(current.get("manifest_sha256"))
            if current_sha is None or current_sha in seen:
                return
            seen.add(current_sha)
            ordered.append(current)
            previous_sha = _normalize_optional_str(current.get("previous_manifest_sha256"))
            current = by_sha.get(previous_sha) if previous_sha is not None else None

    for head in sorted(heads, key=_history_sort_key, reverse=True):
        append_chain(head)

    for entry in sorted(entries, key=_history_sort_key, reverse=True):
        append_chain(entry)

    return ordered


def list_audit_history(path: str | Path = ".") -> list[dict[str, Any]]:
    root = _resolve_history_root(path)
    entries = _sync_history_index(root)
    known_manifests = {
        str(entry["manifest_sha256"])
        for entry in entries
        if _normalize_optional_str(entry.get("manifest_sha256")) is not None
    }

    history: list[dict[str, Any]] = []
    for entry in _order_history_entries(entries):
        previous_manifest_sha256 = _normalize_optional_str(entry.get("previous_manifest_sha256"))
        created_at = _normalize_optional_str(entry.get("created_at"))
        file_path = str(entry["file_path"])
        history.append(
            {
                "manifest_sha256": str(entry["manifest_sha256"]),
                "kind": _normalize_optional_str(entry.get("kind")),
                "created_at": created_at,
                "file_path": file_path,
                "previous_manifest_sha256": previous_manifest_sha256,
                "missing_timestamp": created_at is None,
                "chain_gap": previous_manifest_sha256 is not None
                and previous_manifest_sha256 not in known_manifests,
                "signature_kind": _load_signature_kind(file_path),
            }
        )
    return history


def list_audit_history_json(path: str | Path = ".") -> str:
    return json.dumps(list_audit_history(path), indent=2)


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
    try:
        record_audit_manifest(resolved_manifest, manifest=manifest)
    except OSError:
        pass
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
