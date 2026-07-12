"""EvidenceReceipt v1 -- Phase 2: Ed25519 signing layer.

Isolates ALL cryptography used by `tg evidence emit --sign` / `tg evidence verify` / `tg evidence
keygen` / `tg evidence pubkey` in one module, per the Backend Fail-Closed Contract (AGENTS.md):
signing/verification errors raise `EvidenceSigningError` -- this module never silently emits an
unsigned receipt when signing was requested, and never reports a forged/tampered receipt as valid.

Design (see docs/plans/backlog-100/cluster-124-evidence-signing.md for the full spec):
  * Algorithm: Ed25519 (asymmetric -- gotcontext, a SEPARATE trust domain, verifies without ever
    holding tg's private key). `audit_manifest.py`'s HMAC-SHA256 pattern is correct for that
    module's use case (same-operator local tamper-evidence) but wrong here: a symmetric key would
    have to be shared with every verifier, which could also then forge receipts.
  * Canonicalization (`tg-canonical-json-v1`): compact, key-sorted, ASCII-only JSON
    (`canonical_receipt_bytes`) with only `signature` and `receipt_sha256` excluded -- the whole
    `signing` block (including the `algorithm` claim) is INSIDE the signed bytes, so an attacker
    cannot downgrade the algorithm or swap the embedded public key without invalidating both the
    digest and the signature.
  * `receipt_sha256` and the Ed25519 signature are computed over the SAME canonical bytes, so a
    receipt's keyless integrity digest and its signature always agree by construction.
  * S2 trust-bootstrap: an embedded public key is self-authenticating for CONSISTENCY (anyone can
    generate a keypair, sign with it, and embed the matching public key -- that only proves
    internal consistency, not who signed it) but never for AUTHENTICITY. `verify_receipt` always
    reports the signer's key fingerprint; only a caller-supplied, out-of-band pinned key list
    (`--trusted-key` / `TG_EVIDENCE_TRUSTED_KEYS`) can upgrade `key_trusted` to `True`, and
    `--require-trusted` is the hard gate that fails `valid` closed on an unpinned key. This
    mirrors `audit_manifest.verify_audit_manifest`'s refusal to trust a manifest's own embedded
    `signature.key_path` (audit_manifest.py:779-786).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from tensor_grep.cli._index_lock import replace_with_retry
from tensor_grep.cli.audit_manifest import _utc_now_iso

ALGORITHM = "ed25519"
CANONICALIZATION = "tg-canonical-json-v1"

_SIGNING_KEY_ENV = "TG_EVIDENCE_SIGNING_KEY"
_TRUSTED_KEYS_ENV = "TG_EVIDENCE_TRUSTED_KEYS"
_DEFAULT_KEY_DIRNAME = ".tensor-grep"
_DEFAULT_KEY_SUBDIR = "keys"
_DEFAULT_KEY_FILENAME = "evidence_ed25519.key"

# AGENTS.md "pre-auth unbounded read" pattern, applied to a local file instead of a socket: a real
# evidence receipt is a few KB; refuse to even attempt to parse anything drastically larger before
# `json.loads` runs (DoS guard for `tg evidence verify` on an attacker-supplied receipt file).
_MAX_RECEIPT_FILE_BYTES = 5 * 1024 * 1024  # 5 MB


class EvidenceSigningError(RuntimeError):
    """Fail-closed signing/verification precondition error (never produces a partial receipt)."""


# ---------------------------------------------------------------------------
# Missing-crypto fail-closed guard (defense-in-depth; see AGENTS.md Backend
# Fail-Closed Contract). `cryptography` is a declared core dependency
# (pyproject.toml [project].dependencies), so the imports above always
# succeed in a correctly-installed environment -- this sentinel exists so a
# broken/partial install still fails closed instead of silently emitting an
# unsigned receipt while --sign was requested, and so tests can simulate
# that failure mode without process-level import-cache gymnastics (a real
# ImportError can only be observed once, at first module import).
# ---------------------------------------------------------------------------
_CRYPTOGRAPHY_IMPORT_ERROR: Exception | None = None


def _require_cryptography() -> None:
    if _CRYPTOGRAPHY_IMPORT_ERROR is not None:
        raise EvidenceSigningError(
            "The `cryptography` package is required for EvidenceReceipt signing but is "
            f"unavailable ({_CRYPTOGRAPHY_IMPORT_ERROR}). Reinstall tensor-grep or run "
            "`pip install cryptography>=48.0.1`."
        )


# ---------------------------------------------------------------------------
# Canonicalization + digest
# ---------------------------------------------------------------------------


def canonical_receipt_bytes(receipt: dict[str, Any]) -> bytes:
    """`tg-canonical-json-v1`: compact, key-sorted, ASCII-only JSON with `signature` and
    `receipt_sha256` excluded (self-referential -- computed FROM these bytes). Everything else,
    including the `signing` block, is included, so the algorithm/key-id claims are authenticated.
    """
    canonical = dict(receipt)
    canonical.pop("signature", None)
    canonical.pop("receipt_sha256", None)
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def receipt_digest(receipt: dict[str, Any]) -> str:
    """The keyless integrity/dedup/chain digest -- always attached as `receipt_sha256`, signed or
    not."""
    return hashlib.sha256(canonical_receipt_bytes(receipt)).hexdigest()


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


def _default_signing_key_path() -> Path:
    # Per-USER home, deliberately NEVER the per-repo .tensor-grep/ that audit_manifest.py writes
    # to (a repo-scoped signing key would ship inside a cloned/shared repo).
    return Path.home() / _DEFAULT_KEY_DIRNAME / _DEFAULT_KEY_SUBDIR / _DEFAULT_KEY_FILENAME


def resolve_signing_key_path(flag: str | Path | None) -> Path:
    """Precedence: --signing-key flag > TG_EVIDENCE_SIGNING_KEY env > the per-user default."""
    if flag is not None:
        return Path(flag).expanduser().resolve()
    env_value = os.environ.get(_SIGNING_KEY_ENV)
    if env_value:
        return Path(env_value).expanduser().resolve()
    return _default_signing_key_path()


def resolve_trusted_public_keys(
    flag_values: list[str] | None, *, env_var: str = _TRUSTED_KEYS_ENV
) -> list[str]:
    """Merge repeatable --trusted-key flag values with the comma-separated TG_EVIDENCE_TRUSTED_KEYS
    env fallback into one ordered list of base64 Ed25519 public keys."""
    values = list(flag_values) if flag_values else []
    env_value = os.environ.get(env_var)
    if env_value:
        values.extend(part.strip() for part in env_value.split(",") if part.strip())
    return values


# ---------------------------------------------------------------------------
# Key material
# ---------------------------------------------------------------------------


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def key_id_from_public_b64(public_key_b64_value: str) -> str:
    """`sha256:<hex>` fingerprint of the raw 32-byte public key -- the value `verify_receipt`
    compares (via `hmac.compare_digest`) against a caller-supplied trusted-key set. Never trust a
    claimed `key_id` label; always recompute it from the actual key bytes (S2)."""
    try:
        raw = base64.b64decode(public_key_b64_value, validate=True)
    except (ValueError, TypeError) as exc:
        raise EvidenceSigningError(f"Malformed base64 Ed25519 public key: {exc}") from exc
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def load_private_key(path: str | Path) -> Ed25519PrivateKey:
    _require_cryptography()
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise EvidenceSigningError(
            f"Evidence signing key not found: {resolved}\n"
            "Run `tg evidence keygen` to create one, or pass --signing-key / set "
            f"{_SIGNING_KEY_ENV}."
        )
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise EvidenceSigningError(
            f"Evidence signing key could not be read: {resolved} ({exc})"
        ) from exc
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except Exception as exc:
        raise EvidenceSigningError(
            f"Evidence signing key at {resolved} is not a valid raw 32-byte Ed25519 private key: "
            f"{exc}"
        ) from exc


def _write_private_key_atomic(path: Path, raw_private_bytes: bytes, *, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to write THROUGH a pre-existing symlink at the target path regardless of --force: a
    # symlink there could redirect the private key material somewhere the caller does not expect
    # (write-side symlink-attack class; see AGENTS.md "Symlink-follow disclosure", the read-side
    # sibling of this same concern).
    if path.is_symlink():
        raise EvidenceSigningError(f"Refusing to write a signing key through a symlink: {path}")
    if path.exists() and not force:
        raise EvidenceSigningError(
            f"Signing key already exists: {path} (pass --force to overwrite)"
        )

    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    # audit S3 / atomic-write-permission-window pattern (session_store._write_json_atomic): create
    # the temp AT 0600 from byte one via os.open(O_CREAT|O_EXCL) so the private key is never
    # briefly world-readable, and O_EXCL refuses to follow a pre-existing temp/symlink.
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw_private_bytes)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    replace_with_retry(tmp_path, path)


def generate_keypair(out_path: str | Path, *, force: bool = False) -> dict[str, str]:
    """Generate a new Ed25519 keypair: the private key is written to `out_path` at 0600 (refusing
    to overwrite an existing file/symlink unless `force=True`); the public key is written
    alongside at `<out_path>.pub` (0644, informational)."""
    _require_cryptography()
    # Check for a symlink BEFORE `.resolve()`: `Path.resolve()` follows symlinks, so resolving
    # first and then checking `is_symlink()` on the RESULT would always see the real target file
    # (never a symlink) and silently defeat the write-side symlink-attack guard in
    # `_write_private_key_atomic` below. Check the expanded-but-unresolved path first.
    expanded = Path(out_path).expanduser()
    if expanded.is_symlink():
        raise EvidenceSigningError(f"Refusing to write a signing key through a symlink: {expanded}")
    resolved = expanded.resolve()
    private_key = Ed25519PrivateKey.generate()
    raw_private = private_key.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    _write_private_key_atomic(resolved, raw_private, force=force)

    pub_b64 = public_key_b64(private_key)
    key_id = key_id_from_public_b64(pub_b64)
    pub_path = resolved.with_name(resolved.name + ".pub")
    pub_path.write_text(pub_b64 + "\n", encoding="utf-8")
    try:
        os.chmod(pub_path, 0o644)
    except OSError:
        pass

    return {
        "private_key_path": str(resolved),
        "public_key_path": str(pub_path),
        "public_key": pub_b64,
        "key_id": key_id,
    }


def public_key_info(private_key_path: str | Path) -> dict[str, str]:
    """`tg evidence pubkey`: derive the public key + key_id from a private key file, without ever
    printing/logging the private key material itself."""
    private_key = load_private_key(private_key_path)
    pub_b64 = public_key_b64(private_key)
    return {"public_key": pub_b64, "key_id": key_id_from_public_b64(pub_b64)}


# ---------------------------------------------------------------------------
# Chain linking (previous_receipt_sha256)
# ---------------------------------------------------------------------------


def previous_receipt_digest(path: str | Path) -> str:
    """Mirrors `audit_manifest._previous_manifest_digest`: prefer the prior receipt's own stored
    `receipt_sha256`, else fall back to the sha256 of its raw file bytes (covers a pre-P2 receipt
    that predates this field)."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise EvidenceSigningError(f"Previous evidence receipt not found: {resolved}")
    raw = resolved.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return hashlib.sha256(raw).hexdigest()
    if isinstance(payload, dict):
        digest = payload.get("receipt_sha256")
        if isinstance(digest, str) and digest:
            return digest
    return hashlib.sha256(raw).hexdigest()


def verify_receipt_chain(receipt: dict[str, Any], *, previous_path: str | Path) -> dict[str, Any]:
    """The `tg evidence verify --previous` chain check: compares `receipt["previous_receipt_sha256"]`
    against the digest of the file at `previous_path`. Kept separate from `verify_receipt` so the
    latter's return shape matches the spec exactly; `--previous` is optional at the CLI layer."""
    claimed = receipt.get("previous_receipt_sha256")
    if claimed is None:
        return {
            "chain_valid": False,
            "chain_error": "Receipt has no previous_receipt_sha256 to verify against --previous.",
        }
    if not isinstance(claimed, str) or not claimed:
        return {
            "chain_valid": False,
            "chain_error": "previous_receipt_sha256 must be a non-empty string when present.",
        }
    try:
        actual = previous_receipt_digest(previous_path)
    except EvidenceSigningError as exc:
        return {"chain_valid": False, "chain_error": str(exc)}
    chain_valid = hmac.compare_digest(claimed, actual)
    return {
        "chain_valid": chain_valid,
        "chain_error": (
            None if chain_valid else "previous_receipt_sha256 does not match the --previous digest."
        ),
    }


# ---------------------------------------------------------------------------
# Sign / verify
# ---------------------------------------------------------------------------


def sign_receipt(
    receipt: dict[str, Any],
    *,
    private_key_path: str | Path,
    previous_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    """Return a NEW receipt dict (never mutates `receipt`) with `signing`, `receipt_sha256`, and
    `signature` attached (and `previous_receipt_sha256` if given). Raises `EvidenceSigningError`
    -- never returns a partially-signed receipt -- if the key cannot be resolved/loaded or
    `cryptography` is unavailable (Backend Fail-Closed Contract: --sign must never silently fall
    back to an unsigned emission)."""
    _require_cryptography()
    private_key = load_private_key(private_key_path)
    pub_b64 = public_key_b64(private_key)
    key_id = key_id_from_public_b64(pub_b64)

    signed_receipt = dict(receipt)
    signed_receipt.pop("signature", None)
    signed_receipt.pop("receipt_sha256", None)
    if previous_receipt_sha256 is not None:
        signed_receipt["previous_receipt_sha256"] = previous_receipt_sha256
    signed_receipt["signing"] = {
        "algorithm": ALGORITHM,
        "key_id": key_id,
        "public_key": pub_b64,
        "signed_at": _utc_now_iso(),
        "canonicalization": CANONICALIZATION,
        "receipt_id": str(uuid.uuid4()),
    }

    canonical_bytes = canonical_receipt_bytes(signed_receipt)
    digest_hex = hashlib.sha256(canonical_bytes).hexdigest()
    signature_bytes = private_key.sign(canonical_bytes)

    signed_receipt["receipt_sha256"] = digest_hex
    signed_receipt["signature"] = {"value": base64.b64encode(signature_bytes).decode("ascii")}
    return signed_receipt


def _fingerprint_of_trusted_candidate(candidate: str) -> str | None:
    try:
        return key_id_from_public_b64(candidate)
    except EvidenceSigningError:
        return None  # malformed trusted-key entry never matches; does not crash verify


def verify_receipt(
    receipt: dict[str, Any],
    *,
    trusted_public_keys: list[str] | None = None,
    require_trusted: bool = False,
) -> dict[str, Any]:
    """Verify a receipt's digest and (if present) its Ed25519 signature.

    S2 trust-bootstrap: an embedded public key can always self-verify (that only proves internal
    consistency -- anyone can generate a keypair and sign with it). `key_trusted` is `True` only
    when the embedded key's fingerprint matches an entry in `trusted_public_keys` (compared with
    `hmac.compare_digest`); `require_trusted=True` folds that into `valid` (fails closed on an
    unpinned or absent key). Never raises for a tampered/untrusted receipt -- that outcome is
    reported in the returned dict, matching `audit_manifest.verify_audit_manifest`.
    """
    errors: list[str] = []

    canonical_bytes = canonical_receipt_bytes(receipt)
    expected_digest = hashlib.sha256(canonical_bytes).hexdigest()
    stored_digest = receipt.get("receipt_sha256")
    digest_valid = isinstance(stored_digest, str) and hmac.compare_digest(
        stored_digest, expected_digest
    )
    if not digest_valid:
        errors.append("Receipt digest does not match receipt_sha256 (tampered or malformed).")

    signing_block = receipt.get("signing")
    signature_block = receipt.get("signature")
    signed = isinstance(signing_block, dict) and isinstance(signature_block, dict)

    trusted_set = list(trusted_public_keys) if trusted_public_keys else []
    trust_requested = bool(trusted_set) or require_trusted

    algorithm: str | None = None
    fingerprint: str | None = None
    key_trusted: bool | None = None
    signature_valid: bool

    if not signed:
        if trust_requested:
            # Symmetric to audit_manifest.py:813-815 ("signing key provided but manifest
            # unsigned"): asking for trust verification against an unsigned receipt is a hard
            # failure, never a silent pass.
            signature_valid = False
            errors.append("Trusted-key verification was requested but the receipt is unsigned.")
        else:
            signature_valid = True  # nothing to cryptographically check; digest-only receipt
    else:
        _require_cryptography()
        # `signed` was computed from these exact isinstance checks, but mypy cannot narrow through
        # the intermediate bool -- re-assert here (true by construction) rather than re-run the
        # isinstance checks inline at every `.get()` call site below.
        assert isinstance(signing_block, dict)
        assert isinstance(signature_block, dict)
        algorithm = str(signing_block.get("algorithm") or "")
        public_key_field = signing_block.get("public_key")
        signature_value = signature_block.get("value")
        if algorithm != ALGORITHM:
            signature_valid = False
            errors.append(f"Unsupported signing algorithm: {algorithm or '<empty>'}")
        elif not isinstance(public_key_field, str) or not public_key_field:
            signature_valid = False
            errors.append("Signing block is missing a public_key.")
        elif not isinstance(signature_value, str) or not signature_value:
            signature_valid = False
            errors.append("Signature block is missing a value.")
        else:
            fingerprint = key_id_from_public_b64(public_key_field)
            try:
                public_key = Ed25519PublicKey.from_public_bytes(
                    base64.b64decode(public_key_field, validate=True)
                )
                public_key.verify(base64.b64decode(signature_value, validate=True), canonical_bytes)
                signature_valid = True
            except (InvalidSignature, ValueError, TypeError) as exc:
                signature_valid = False
                errors.append(f"Signature does not verify against the embedded public key: {exc}")

        if trusted_set:
            key_trusted = fingerprint is not None and any(
                candidate_fingerprint is not None
                and hmac.compare_digest(fingerprint, candidate_fingerprint)
                for candidate_fingerprint in (
                    _fingerprint_of_trusted_candidate(candidate) for candidate in trusted_set
                )
            )
        elif require_trusted:
            key_trusted = False  # nothing configured to trust against -> fail closed

    valid = digest_valid and signature_valid
    if signed and require_trusted:
        valid = valid and bool(key_trusted)

    return {
        "valid": valid,
        "checks": {
            "digest_valid": digest_valid,
            "signature_valid": signature_valid,
            "key_trusted": key_trusted,
        },
        "signed": signed,
        "key_id": fingerprint,
        "fingerprint": fingerprint,
        "algorithm": algorithm,
        "trust": {
            "trusted_keys_configured": bool(trusted_set),
            "require_trusted": require_trusted,
        },
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Bounded receipt file read (DoS guard; `tg evidence verify <path>`)
# ---------------------------------------------------------------------------


def read_receipt_file(
    path: str | Path, *, max_bytes: int = _MAX_RECEIPT_FILE_BYTES
) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise EvidenceSigningError(f"Evidence receipt not found: {resolved}")
    try:
        with resolved.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError as exc:
        raise EvidenceSigningError(
            f"Evidence receipt could not be read: {resolved} ({exc})"
        ) from exc
    if len(raw) > max_bytes:
        raise EvidenceSigningError(
            f"Evidence receipt at {resolved} exceeds the {max_bytes}-byte verify limit; refusing "
            "to parse (DoS guard)."
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceSigningError(
            f"Evidence receipt at {resolved} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise EvidenceSigningError(f"Evidence receipt at {resolved} must be a JSON object.")
    return payload
