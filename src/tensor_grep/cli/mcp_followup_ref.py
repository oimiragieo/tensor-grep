"""Bounded follow-up references for omitted MCP response content.

MCP-SURFACE extension (docs/plans/2026-09-07-agentic-quality-simplification.md Task 10,
checkbox "Prototype bounded follow-up references for omitted source"). When a response
profile omits source content to stay under a byte cap, a client may want to fetch it in a
follow-up call. That follow-up must be bound to the EXACT snapshot the omission came from --
a stale, tampered, or cross-root reference must fail closed rather than silently return
unrelated (or now-wrong) source, per AGT-02's session-refresh-race lesson and P9's
generation-safety half.

This module is a standalone, opt-in primitive: it registers no MCP tool and is not called
from the default mcp_server.py response path. Wiring it into an actual response profile is
out of scope for this slice (left open under the MCP-SURFACE Task 10 entry in BACKLOG.md).

KNOWN LIMITATION (documented, not silently accepted): ``resolve_followup_ref`` validates that
the file's content hash matches the minted snapshot AT VALIDATION TIME, then returns a range
descriptor -- it does not itself read or return bytes. A caller that reads the file AFTER
resolving has a TOCTOU window: the file could change between this function's hash check and
the caller's own read. A real byte-serving integration (out of scope here) must re-verify the
hash from the SAME read it serves, e.g. open the file once, hash the bytes it actually read,
and compare that hash to ``snapshot_id`` before returning those exact bytes -- never trust a
hash computed in a separate stat/read pass.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TOKEN_VERSION = "fr1"


class FollowupRefError(ValueError):
    """A follow-up reference failed to resolve. ``reason`` names why."""

    def __init__(self, reason: str, *, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class FollowupRef:
    """A minted, opaque reference to omitted content at a specific snapshot."""

    token: str


def _snapshot_id(path: Path) -> str:
    """The file's actual content hash right now -- a same-size/same-mtime replacement (a
    concurrent writer, or an adversary racing the mint) must NOT be able to pass as the
    original snapshot, so this is a real digest of the bytes, not mtime+size metadata.
    """
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"
    return digest


def _resolve_confined(root: Path, rel_path: str) -> Path | None:
    """Resolve ``rel_path`` under ``root`` and reject any traversal/symlink escape.

    Returns ``None`` (never raises) on escape, a missing path, or an absolute ``rel_path`` --
    callers must treat ``None`` as "cannot confirm containment" and fail closed.
    """
    if Path(rel_path).is_absolute():
        return None
    try:
        resolved_root = root.resolve(strict=True)
        resolved_target = (root / rel_path).resolve(strict=True)
    except OSError:
        return None
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved_target


def _canonical_payload(
    *,
    root: str,
    rel_path: str,
    params_hash: str,
    byte_start: int,
    byte_end: int,
    snapshot_id: str,
    issued_at: float,
    expires_at: float,
) -> dict[str, Any]:
    return {
        "v": _TOKEN_VERSION,
        "root": root,
        "path": rel_path,
        "params_hash": params_hash,
        "range": [byte_start, byte_end],
        "snapshot_id": snapshot_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }


def _sign(payload: dict[str, Any], secret: bytes) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def _is_finite_number(value: Any) -> bool:
    """True for a real, finite int/float -- excludes bool (an int subclass), NaN, and inf,
    all of which could otherwise smuggle a non-expiring or comparison-breaking reference
    through the expiry check."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _params_hash(params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def mint_followup_ref(
    *,
    root: Path,
    rel_path: str,
    params: dict[str, Any],
    byte_range: tuple[int, int],
    ttl_seconds: float,
    secret: bytes,
    now: float | None = None,
) -> FollowupRef:
    """Mint a bounded reference to omitted content, valid for ``ttl_seconds``.

    ``root`` and ``rel_path`` together locate the file; ``params`` is the exact request
    parameter set that produced the omission (so a follow-up under different parameters
    cannot silently reuse a stale range); ``byte_range`` is the omitted [start, end) span.
    """
    if isinstance(ttl_seconds, bool) or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be a finite, non-bool positive number")
    byte_start, byte_end = byte_range
    if (
        isinstance(byte_start, bool)
        or isinstance(byte_end, bool)
        or not isinstance(byte_start, int)
        or not isinstance(byte_end, int)
        or byte_start < 0
        or byte_end < byte_start
    ):
        raise ValueError("byte_range must be (int, int) satisfying 0 <= start <= end")
    if now is not None and (isinstance(now, bool) or not math.isfinite(now)):
        raise ValueError("now must be a finite, non-bool number")
    confined_path = _resolve_confined(root, rel_path)
    if confined_path is None:
        raise ValueError(f"rel_path escapes root or does not exist: {rel_path!r}")
    file_size = confined_path.stat().st_size
    if byte_end > file_size:
        raise ValueError(f"byte_range end {byte_end} exceeds file size {file_size}")
    issued_at = time.time() if now is None else now
    expires_at = issued_at + ttl_seconds
    snapshot_id = _snapshot_id(confined_path)
    payload = _canonical_payload(
        root=str(root),
        rel_path=rel_path,
        params_hash=_params_hash(params),
        byte_start=byte_start,
        byte_end=byte_end,
        snapshot_id=snapshot_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    signature = _sign(payload, secret)
    token = json.dumps({"payload": payload, "sig": signature}, separators=(",", ":"))
    return FollowupRef(token=token)


def resolve_followup_ref(
    ref: FollowupRef,
    *,
    current_root: Path,
    params: dict[str, Any],
    secret: bytes,
    now: float | None = None,
) -> dict[str, Any]:
    """Validate a follow-up reference and return its payload, or raise ``FollowupRefError``.

    Fails closed on: malformed token, signature mismatch (tampered), wrong root (cross-root),
    expiry, parameter mismatch, or a snapshot that no longer matches the file on disk (the
    file changed since the reference was minted).
    """
    try:
        envelope = json.loads(ref.token)
        if not isinstance(envelope, dict):
            raise FollowupRefError("malformed", message="reference envelope is not an object")
        payload = envelope["payload"]
        signature = envelope["sig"]
        if not isinstance(payload, dict) or not isinstance(signature, str):
            raise FollowupRefError(
                "malformed", message="reference payload/signature has wrong type"
            )
        required_fields = (
            "v",
            "root",
            "path",
            "params_hash",
            "range",
            "snapshot_id",
            "issued_at",
            "expires_at",
        )
        if any(field not in payload for field in required_fields):
            raise FollowupRefError("malformed", message="reference payload missing required field")
        range_ = payload["range"]
        is_valid_range = (
            isinstance(range_, list)
            and len(range_) == 2
            and all(
                isinstance(component, int) and not isinstance(component, bool)
                for component in range_
            )
        )
        if (
            not isinstance(payload["v"], str)
            or not isinstance(payload["root"], str)
            or not isinstance(payload["path"], str)
            or not isinstance(payload["params_hash"], str)
            or not isinstance(payload["snapshot_id"], str)
            or not is_valid_range
            or not _is_finite_number(payload["expires_at"])
            or not _is_finite_number(payload["issued_at"])
        ):
            raise FollowupRefError("malformed", message="reference payload field has wrong shape")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FollowupRefError(
            "malformed", message=f"malformed follow-up reference: {exc}"
        ) from exc

    if not hmac.compare_digest(_sign(payload, secret), signature):
        raise FollowupRefError("tampered", message="follow-up reference signature mismatch")

    if payload["v"] != _TOKEN_VERSION:
        raise FollowupRefError("unsupported_version", message="unsupported reference version")

    if payload["root"] != str(current_root):
        raise FollowupRefError("cross_root", message="reference minted under a different root")

    if _params_hash(params) != payload["params_hash"]:
        raise FollowupRefError("params_mismatch", message="reference parameters changed")

    check_time = time.time() if now is None else now
    if check_time >= payload["expires_at"]:
        raise FollowupRefError("expired", message="reference expired; refresh required")

    confined_path = _resolve_confined(current_root, payload["path"])
    if confined_path is None:
        raise FollowupRefError(
            "stale_snapshot",
            message="referenced path no longer resolves under root; refresh required",
        )
    live_snapshot = _snapshot_id(confined_path)
    if live_snapshot != payload["snapshot_id"]:
        raise FollowupRefError(
            "stale_snapshot", message="source changed since reference was minted; refresh required"
        )

    return payload
