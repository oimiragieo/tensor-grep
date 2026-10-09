"""Authenticate persisted parser products before accepting checkout-controlled session state."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from tensor_grep.cli.symbols_cache_auth import load_machine_key
from tensor_grep.cli.symbols_cache_io import read_confined

_FIELD = "session_provenance"
_CORE_FIELDS = (
    "version",
    "session_id",
    "root",
    "repo_map",
    "snapshot",
    "scan_limit",
    "snapshot_unreadable_paths",
    "current_generation",
    "last_prepare",
)
_PAYLOAD_LIMIT = 64 * 1024 * 1024


class SessionPayloadError(ValueError):
    """Untrusted persisted state cannot be safely decoded within its read budget."""


def read_session_payload(root: Path, path: Path) -> dict[str, Any]:
    try:
        raw = read_confined(root, path, _PAYLOAD_LIMIT)
        payload = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise SessionPayloadError(
            "session payload is unreadable, linked, invalid, deeply nested, or exceeds 64 MiB; "
            "run tg session refresh to regenerate bounded state"
        ) from exc
    if not isinstance(payload, dict):
        raise SessionPayloadError("session payload must be a JSON object; run tg session refresh")
    return payload


def _tag(payload: dict[str, Any], secret: bytes) -> str:
    signer = hmac.new(secret, b"tg-session-provenance-v1\0", hashlib.sha256)
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False)
    for fragment in encoder.iterencode({key: payload.get(key) for key in _CORE_FIELDS}):
        signer.update(fragment.encode("utf-8"))
    return signer.hexdigest()


def seal_session(payload: dict[str, Any], root: Path) -> None:
    secret = load_machine_key(root)
    if secret is None:
        payload[_FIELD] = {"scheme": "unavailable", "trusted": False}
        return
    payload[_FIELD] = {"scheme": "machine-hmac-sha256-v1", "tag": _tag(payload, secret)}


def verified_session(payload: dict[str, Any], root: Path) -> bool:
    receipt = payload.get(_FIELD)
    if not isinstance(receipt, dict) or receipt.get("scheme") != "machine-hmac-sha256-v1":
        return False
    secret = load_machine_key(root)
    if secret is None:
        return False
    try:
        return isinstance(receipt.get("tag"), str) and hmac.compare_digest(
            _tag(payload, secret),
            receipt["tag"],
        )
    except (ValueError, TypeError, RecursionError):
        return False
