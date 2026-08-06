"""Behaviorless Round-60 seam for InstallerShimReceiptV1 (Task 2A / #89).

Data types + fail-closed primitive OS shells only. No deterministic test crypto
in the production namespace. No final-authority bool/trace adapters that tests
can hand production. Green-phase production must replace stubs.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

INSTALLER_SHIM_RECEIPT_VERSION = 1
INSTALLER_SHIM_RECEIPT_SCHEMA = "InstallerShimReceiptV1"
PROTECTED_INSTALLER_STATE_RELATIVE = "Microsoft\\Windows\\TensorGrep\\InstallerState"
CNG_KEY_NAME = "TensorGrepInstallerShimReceiptV1"
_MAX_RECEIPT_BYTES = 64 * 1024
_MAX_DEPTH = 8
_DIGEST_LEN = 64
_HEX_ALPHABET = frozenset("0123456789abcdefABCDEF")


@dataclass(frozen=True, slots=True)
class InstallerShimReceiptV1:
    """Strict receipt binding. Construction does not imply authority."""

    version: int
    release_tag: str
    checksums_asset_sha256: str
    install_command_digest: str
    managed_directory_identity: str
    installer_state_identity: str
    selected_release_asset_identity: str
    generated_shim_bytes_digest: str
    cng_public_key_thumbprint: str


@dataclass(frozen=True, slots=True)
class OpenedDirectoryIdentity:
    """No-follow opened directory volume/file identity."""

    volume_file_id: str
    path_text: str
    is_reparse_or_junction: bool = False


@dataclass(slots=True)
class _ProtectedCloseState:
    """Mutable close bookkeeping kept OUT of frozen identity equality/hash."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    closed: set[int] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class ProtectedRootOpen:
    """Primitive: protected ProgramData root open + retained raw identity/SD.

    Orchestration must evaluate ``security_descriptor`` / ACL itself — never
    trust a handed ``acl_ok`` bool or a fake handle as authority.

    Callers own close via context manager / ``close()``. GREEN wires the opener
    closer; ``close()`` is idempotent, thread-safe, and retry-safe if the closer
    raises. Close-state is excluded from equality/hash. Ownership is independent
    of any final ACL bool.
    """

    handle: int
    volume_file_id: str
    security_descriptor: bytes | None = None
    _closer: Callable[[int], None] | None = None
    _close_state: _ProtectedCloseState = field(
        default_factory=_ProtectedCloseState, compare=False, hash=False
    )

    def close(self) -> None:
        closer = self._closer
        if closer is None:
            return
        with self._close_state.lock:
            if self.handle in (0, -1):
                return
            if self.handle in self._close_state.closed:
                return
            closer(self.handle)
            self._close_state.closed.add(self.handle)

    def __enter__(self) -> ProtectedRootOpen:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class CngSignResult:
    """CNG sign output bound to a persisted named key identity.

    ``key_name`` must remain stable across reopen. ``exportable`` is an
    observable of the key creation flags — GREEN must create non-exportable
    keys; tests independently attempt attacker export rather than trusting this
    field alone.
    """

    signature: bytes
    public_key_thumbprint: str
    key_name: str = CNG_KEY_NAME
    exportable: bool = False


@dataclass(frozen=True, slots=True)
class CngKeyBinding:
    """Persisted named CNG key identity observables for integration REDs."""

    key_name: str
    public_key_thumbprint: str
    exportable: bool = False


@dataclass(frozen=True, slots=True)
class TxrPrimitiveTrace:
    """Observable primitive TxR call sequence (not a final authority verdict)."""

    calls: tuple[str, ...] = ()


@dataclass
class PrimitiveCallLog:
    """Test-observable log of primitive OS operations (injected)."""

    entries: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def record(self, name: str, *args: Any) -> None:
        self.entries.append((name, args))


class ProtectedRootOpener(Protocol):
    def open_protected_root(self) -> ProtectedRootOpen: ...


class CngPrimitives(Protocol):
    def sign_canonical(self, canonical_bytes: bytes) -> CngSignResult: ...

    def verify_canonical(
        self, canonical_bytes: bytes, signature: bytes, public_key_thumbprint: str
    ) -> bool: ...

    def reopen_named_key(self, key_name: str) -> CngKeyBinding: ...

    def attempt_export_private_key(self, key_name: str) -> bytes: ...

    def delete_named_key(self, key_name: str) -> None: ...


class NoFollowOpener(Protocol):
    def open_directory_nofollow(self, token: str) -> OpenedDirectoryIdentity: ...

    def open_leaf_nofollow(self, token: str) -> OpenedDirectoryIdentity: ...


class TxrPrimitives(Protocol):
    def create_transaction(self) -> int: ...

    def transacted_registry_open(self, transaction: int, key_path: str) -> int: ...

    def transacted_registry_write(self, key_handle: int, value: str) -> None: ...

    def commit_transaction(self, transaction: int) -> None: ...

    def rollback_transaction(self, transaction: int) -> None: ...

    def close_registry_key(self, key_handle: int) -> None: ...

    def close_transaction(self, transaction: int) -> None: ...


def parse_installer_shim_receipt(raw: bytes | str | Mapping[str, Any]) -> InstallerShimReceiptV1:
    """Strict bounded schema/type/value/length parser.

    Behaviorless shell: raises until GREEN implements exact contracts (no coerce).
    """
    _ = raw
    raise NotImplementedError(
        "parse_installer_shim_receipt is a behaviorless shell until GREEN implements "
        "exact schema/type/value/length contracts without coercion"
    )


def canonical_receipt_bytes(receipt: InstallerShimReceiptV1) -> bytes:
    """Canonical UTF-8 JSON bytes for CNG binding (behaviorless until GREEN)."""
    _ = receipt
    raise NotImplementedError("canonical_receipt_bytes is not implemented")


def evaluate_protected_root_security_descriptor(security_descriptor: bytes | None) -> bool:
    """Orchestration ACL/SD evaluation — behaviorless until GREEN."""
    _ = security_descriptor
    raise NotImplementedError(
        "evaluate_protected_root_security_descriptor must evaluate the raw SD/ACL"
    )


def open_protected_installer_state(
    *,
    opener: ProtectedRootOpener | None = None,
    call_log: PrimitiveCallLog | None = None,
) -> ProtectedRootOpen:
    """Primitive orchestration entry: open protected ProgramData root + SD check."""
    if call_log is not None:
        call_log.record("open_protected_installer_state")
    if opener is None:
        raise NotImplementedError("protected ProgramData root open requires Windows opener")
    opened = opener.open_protected_root()
    try:
        if call_log is not None:
            call_log.record(
                "protected_root_opened",
                opened.handle,
                opened.volume_file_id,
                opened.security_descriptor,
            )
        # Only refuse intrinsically invalid null/invalid handles — a positive integer
        # handle value (including 7) is not intrinsically fake.
        if opened.handle in (0, -1):
            raise PermissionError("null/invalid protected-root handle refused")
        # Never trust a handed bool — evaluate the raw security descriptor.
        if not evaluate_protected_root_security_descriptor(opened.security_descriptor):
            raise PermissionError("protected installer state ACL refused")
        return opened
    except BaseException as original:
        try:
            opened.close()
        except BaseException as cleanup_err:
            try:
                original.add_note(f"cleanup also failed: {cleanup_err!r}")
            except Exception:
                pass
        raise original


def windows_cng_primitives() -> CngPrimitives:
    """Default Windows CNG NCrypt factory seam (behaviorless until GREEN).

    GREEN must create/open the persisted named key ``CNG_KEY_NAME`` as
    non-exportable, sign/verify canonical receipt bytes, support reopen by
    name, and delete on cleanup. Tests independently attempt private-key
    export via test-side NCrypt ctypes rather than trusting ``exportable``.
    """
    raise NotImplementedError(
        "windows_cng_primitives requires Windows CNG NCrypt named-key "
        f"create/open for {CNG_KEY_NAME!r}; no production HMAC fallback"
    )


def cng_sign_receipt(
    receipt: InstallerShimReceiptV1,
    *,
    cng: CngPrimitives | None = None,
    call_log: PrimitiveCallLog | None = None,
) -> CngSignResult:
    """Orchestration: canonical bytes → CNG sign primitive (no test HMAC in production)."""
    if call_log is not None:
        call_log.record("cng_sign_receipt")
    if cng is None:
        cng = windows_cng_primitives()
    canonical = canonical_receipt_bytes(receipt)
    if call_log is not None:
        call_log.record("cng_sign_canonical", len(canonical))
    return cng.sign_canonical(canonical)


def cng_verify_receipt(
    receipt: InstallerShimReceiptV1,
    *,
    signature: bytes,
    public_key_thumbprint: str,
    cng: CngPrimitives | None = None,
    call_log: PrimitiveCallLog | None = None,
) -> bool:
    """Orchestration: derive authority from CNG verify primitive — not a handed bool."""
    if call_log is not None:
        call_log.record("cng_verify_receipt", public_key_thumbprint)
    if cng is None:
        cng = windows_cng_primitives()
    canonical = canonical_receipt_bytes(receipt)
    if call_log is not None:
        call_log.record("cng_verify_canonical", len(canonical), len(signature))
    return cng.verify_canonical(canonical, signature, public_key_thumbprint)


def path_token_matches_retained_managed_directory(
    token: str,
    *,
    retained_managed_directory_identity: str,
    opener: NoFollowOpener | None = None,
    call_log: PrimitiveCallLog | None = None,
) -> bool:
    """Orchestration: no-follow open + identity compare (no magic token / final bool adapter)."""
    if call_log is not None:
        call_log.record("path_token_match", token)
    if opener is None:
        raise NotImplementedError("no-follow directory open requires Windows opener")
    opened = opener.open_directory_nofollow(token)
    if call_log is not None:
        call_log.record(
            "nofollow_opened",
            opened.volume_file_id,
            opened.is_reparse_or_junction,
        )
    if opened.is_reparse_or_junction:
        return False
    return opened.volume_file_id == retained_managed_directory_identity


def mutate_user_path_txr_only(
    *,
    path_preimage: str,
    intended_image: str,
    remove_token_identity: str,
    txr: TxrPrimitives | None = None,
    call_log: PrimitiveCallLog | None = None,
    registry_key_path: str | None = None,
    post_write_fault: Callable[[], None] | None = None,
) -> TxrPrimitiveTrace:
    """TxR orchestration over injectable primitives (RED until GREEN Windows TxR).

    When ``txr`` is supplied (test adapter), runs CreateTransaction →
    transacted open/write → optional per-call ``post_write_fault`` (scoped to
    this call only) → CommitTransaction, or RollbackTransaction on failure
    with no non-TxR fallback. Exact-once reverse close ownership (A66):
    ``close_registry_key`` then ``close_transaction`` on success, BaseException,
    and cleanup-failure paths (primary error preserved via ``add_note``).
    When ``txr`` is omitted, fails closed until GREEN wires real Kernel
    Transaction Manager ops — there is no production-global TXR fault hook or
    public setter.
    """
    _ = path_preimage, remove_token_identity
    if call_log is not None:
        call_log.record("mutate_user_path_txr_only")
    if txr is None:
        raise NotImplementedError(
            "TxR-only PATH mutation requires CreateTransaction → transacted "
            "open/write → CommitTransaction (no emulated fallback)"
        )
    key_path = registry_key_path if registry_key_path is not None else "Environment"
    calls: list[str] = []
    key_handle: int | None = None
    key_closed = False
    txn_closed = False
    primary_error: BaseException | None = None
    transaction: int | None = None
    try:
        # HIGH#6: create_transaction inside try so call_log.record() cannot leak txn.
        transaction = txr.create_transaction()
        calls.append("CreateTransaction")
        if call_log is not None:
            call_log.record("CreateTransaction", transaction)
        key_handle = txr.transacted_registry_open(transaction, key_path)
        calls.append("transacted_open")
        if call_log is not None:
            call_log.record("transacted_open", transaction, key_path)
        txr.transacted_registry_write(key_handle, intended_image)
        calls.append("transacted_write")
        if call_log is not None:
            call_log.record("transacted_write", key_handle, intended_image)
        if post_write_fault is not None:
            post_write_fault()
        txr.commit_transaction(transaction)
        calls.append("CommitTransaction")
        if call_log is not None:
            call_log.record("CommitTransaction", transaction)
    except BaseException as original:
        primary_error = original
        if transaction is not None:
            try:
                txr.rollback_transaction(transaction)
                calls.append("RollbackTransaction")
                if call_log is not None:
                    call_log.record("RollbackTransaction", transaction)
            except BaseException as cleanup_err:
                try:
                    original.add_note(f"cleanup also failed: {cleanup_err!r}")
                except Exception:
                    pass
        # Fall through to reverse close ownership, then re-raise.
    finally:
        # Exact-once reverse cleanup: key handle before transaction handle.
        if key_handle is not None and not key_closed:
            try:
                txr.close_registry_key(key_handle)
                key_closed = True
                calls.append("close_registry_key")
                if call_log is not None:
                    call_log.record("close_registry_key", key_handle)
            except BaseException as close_err:
                if primary_error is not None:
                    try:
                        primary_error.add_note(f"cleanup also failed: {close_err!r}")
                    except Exception:
                        pass
                else:
                    primary_error = close_err
        if transaction is not None and not txn_closed:
            try:
                txr.close_transaction(transaction)
                txn_closed = True
                calls.append("close_transaction")
                if call_log is not None:
                    call_log.record("close_transaction", transaction)
            except BaseException as close_err:
                if primary_error is not None:
                    try:
                        primary_error.add_note(f"cleanup also failed: {close_err!r}")
                    except Exception:
                        pass
                else:
                    primary_error = close_err
    if primary_error is not None:
        raise primary_error
    return TxrPrimitiveTrace(calls=tuple(calls))


def abort_disposable_txr_and_read_registry_preimage(
    *,
    path_preimage: str,
    registry_key_path: str,
    txr: TxrPrimitives | None = None,
) -> str:
    """Abort a real disposable TxR after write and independently read registry preimage.

    Behaviorless until GREEN implements real Kernel Transaction Manager ops.
    Proves no non-TxR fallback by returning the independent registry read.
    """
    _ = path_preimage, registry_key_path, txr
    raise NotImplementedError(
        "abort_disposable_txr_and_read_registry_preimage requires real TxR abort "
        "after transacted write plus independent registry preimage read"
    )


def discover_installer_shim_receipt(
    *,
    protected_opener: ProtectedRootOpener | None = None,
    path_entries: Sequence[str] | None = None,
    managed_binary_directory: str | None = None,
    caller_selected_state_path: str | None = None,
    planted_receipt_bytes: bytes | None = None,
    call_log: PrimitiveCallLog | None = None,
    _read_protected_receipt: Callable[[ProtectedRootOpen], InstallerShimReceiptV1] | None = None,
) -> InstallerShimReceiptV1 | None:
    """Authority discovery via protected-root primitive only (no ambient magic).

    Retained protected-root open is always closed — including the behaviorless
    raise path — so the handle cannot leak. Cleanup failures preserve the
    original error (annotated via ``add_note``). ``_read_protected_receipt`` is a
    private per-call injection seam for close-ownership tests only.
    """
    _ = path_entries, managed_binary_directory, caller_selected_state_path, planted_receipt_bytes
    if call_log is not None:
        call_log.record("discover_installer_shim_receipt")
    if protected_opener is None:
        return None
    opened = open_protected_installer_state(opener=protected_opener, call_log=call_log)
    try:
        if _read_protected_receipt is not None:
            result = _read_protected_receipt(opened)
        else:
            raise NotImplementedError(
                "reading receipt bytes from protected root is not implemented (behaviorless)"
            )
    except BaseException as original:
        try:
            opened.close()
        except BaseException as cleanup_err:
            try:
                original.add_note(f"cleanup also failed: {cleanup_err!r}")
            except Exception:
                pass
        raise original
    # Success path: close is deliberate — cleanup failures must surface (not
    # swallowed) and close() remains retry-safe so no retained handle leaks.
    opened.close()
    return result


def install_command_digest_is_authority() -> bool:
    """Install-command digest is audit metadata only, never authority."""
    return False


def install_ps1_uses_txr_only(content: str) -> bool:
    """Structural check used by REDs against scripts/install.ps1 (not sole RED)."""
    compact = content.replace(" ", "").replace("'", '"')
    has_create = "CreateTransaction" in content
    has_commit = "CommitTransaction" in content
    has_env = '[Environment]::SetEnvironmentVariable("Path"' in compact
    return has_create and has_commit and not has_env


# ---------------------------------------------------------------------------
# Pure SDDL DACL grammar (A65 / HIGH#6) — platform-neutral, fail-closed.
# ---------------------------------------------------------------------------

_ALLOWED_DACL_FLAG_TOKENS = frozenset({"P", "AR", "AI"})
_KNOWN_ACE_TYPES = frozenset({"A", "D", "OA", "OD", "AU", "AL", "OU", "OL"})
# Exact SDDL ACE flag tokens. Unknown flags reject the whole DACL.
_KNOWN_ACE_FLAG_TOKENS = frozenset({
    "OI",
    "CI",
    "NP",
    "IO",
    "ID",
    "SA",
    "FA",
    "CR",
    "SR",
    "SI",
    "NS",
})
_INHERIT_ONLY_ACE_FLAG = "IO"

_WRITE_FULL_RIGHT_TOKENS = frozenset({
    "GA",
    "GW",
    "GX",
    "FA",
    "FW",
    "WD",
    "WO",
    "SD",
    "WA",
    "WE",
    "DC",
    "LC",
    "CR",
    "SW",
    "WP",
    "DT",
    "KA",
    "KW",
})
_READ_LIKE_RIGHT_TOKENS = frozenset({
    "GR",
    "FR",
    "FX",
    "RC",
    "RE",
    "RA",
    "RD",
    "CC",
    "RP",
    "LO",
    "AS",
    "KR",
    "KX",
})
_KNOWN_RIGHT_TOKENS = _WRITE_FULL_RIGHT_TOKENS | _READ_LIKE_RIGHT_TOKENS
_RESTRICTED_WRITE_FULL = frozenset({
    "GA",
    "GW",
    "FA",
    "FW",
    "WD",
    "WO",
    "SD",
    "WA",
    "WE",
    "DC",
    "CR",
    "SW",
    "WP",
    "DT",
    "KA",
    "KW",
})
_ALLOWED_WRITE_TRUSTEE_ALIASES = {
    "SY": "SY",
    "S-1-5-18": "SY",
    "BA": "BA",
    "S-1-5-32-544": "BA",
}


def extract_sddl_dacl_body(sddl: str) -> str | None:
    """Extract the DACL body after ``D:`` up to a top-level ``S:`` SACL or end.

    Must NOT use ``D:([^S]*)`` — that truncates at the ``S`` inside ``SY``.
    """
    idx = sddl.find("D:")
    if idx < 0:
        return None
    body_start = idx + 2
    depth = 0
    i = body_start
    while i < len(sddl):
        ch = sddl[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None
        elif depth == 0 and sddl.startswith("S:", i):
            return sddl[body_start:i]
        i += 1
    if depth != 0:
        return None
    return sddl[body_start:]


def _parse_dacl_flags(flags: str) -> set[str] | None:
    """Parse DACL flags as exact ``P`` / ``AR`` / ``AI`` tokens only."""
    if flags == "":
        return set()
    tokens: set[str] = set()
    i = 0
    while i < len(flags):
        if i + 1 < len(flags) and flags[i : i + 2] in _ALLOWED_DACL_FLAG_TOKENS:
            tokens.add(flags[i : i + 2])
            i += 2
            continue
        if flags[i] in _ALLOWED_DACL_FLAG_TOKENS:
            tokens.add(flags[i])
            i += 1
            continue
        return None
    return tokens


def _parse_ace_flag_tokens(ace_flags: str) -> set[str] | None:
    """Parse ACE flags as exact known 2-char tokens; unknown/garbage → None."""
    if ace_flags == "":
        return set()
    if len(ace_flags) % 2 != 0:
        return None
    tokens = {ace_flags[i : i + 2] for i in range(0, len(ace_flags), 2)}
    if not tokens <= _KNOWN_ACE_FLAG_TOKENS:
        return None
    return tokens


def _parse_sddl_right_tokens(rights: str) -> set[str] | None:
    """Tokenize SDDL rights into exact 2-char codes; unknown/numeric → None."""
    if rights == "":
        return set()
    if rights.lower().startswith("0x") or any(ch.isdigit() for ch in rights):
        return None
    if len(rights) % 2 != 0:
        return None
    tokens = {rights[i : i + 2] for i in range(0, len(rights), 2)}
    if not tokens <= _KNOWN_RIGHT_TOKENS:
        return None
    return tokens


def evaluate_programdata_sddl_dacl(sddl: str) -> bool:
    """Pure SDDL DACL evaluator for ProgramData protected-root authority.

    Requires a protected DACL (exact ``P`` among only ``P``/``AR``/``AI``),
    parses every ACE with exactly 6 semicolon fields, validates ACE types and
    ACE flags against closed vocabularies, ignores inherit-only (``IO``) ACEs for
    *effective* object authority, accepts write/full allow ACEs (including
    ``KA``/``KW``) only for exact SYSTEM/BA, requires both trustees present with
    effective write/full, and rejects unknown / inherit-only-only / garbage forms
    (A65 / HIGH#6).
    """
    if not sddl or not isinstance(sddl, str):
        return False
    dacl_body = extract_sddl_dacl_body(sddl)
    if dacl_body is None:
        return False
    paren = dacl_body.find("(")
    flags = dacl_body if paren < 0 else dacl_body[:paren]
    flag_tokens = _parse_dacl_flags(flags)
    if flag_tokens is None or "P" not in flag_tokens:
        return False
    if paren < 0:
        return False

    ace_bodies: list[str] = []
    i = paren
    while i < len(dacl_body):
        if dacl_body[i] != "(":
            return False
        depth = 0
        start = i + 1
        j = i
        closed = False
        while j < len(dacl_body):
            ch = dacl_body[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    ace_bodies.append(dacl_body[start:j])
                    i = j + 1
                    closed = True
                    break
                if depth < 0:
                    return False
            j += 1
        if not closed:
            return False
    if not ace_bodies:
        return False

    write_trustees_seen: set[str] = set()
    for ace in ace_bodies:
        parts = ace.split(";")
        if len(parts) != 6:
            return False
        ace_type, ace_flags, rights, _object_guid, _inherit_guid, trustee = parts
        if ace_type not in _KNOWN_ACE_TYPES:
            return False  # unknown ACE type = garbage
        ace_flag_tokens = _parse_ace_flag_tokens(ace_flags)
        if ace_flag_tokens is None:
            return False  # unknown / garbage ACE flags
        right_tokens = _parse_sddl_right_tokens(rights)
        if right_tokens is None:
            return False
        # Inherit-only ACEs do not establish effective authority on this object.
        if _INHERIT_ONLY_ACE_FLAG in ace_flag_tokens:
            continue
        if ace_type != "A":
            continue
        if not (right_tokens & _RESTRICTED_WRITE_FULL):
            continue
        trustee = trustee.strip()
        alias = _ALLOWED_WRITE_TRUSTEE_ALIASES.get(trustee)
        if alias is None:
            return False
        write_trustees_seen.add(alias)
    return write_trustees_seen == {"SY", "BA"}


# ---------------------------------------------------------------------------
# CNG NCryptExportKey contract (A64 / HIGH#7)
# ---------------------------------------------------------------------------

# NCRYPT_ALLOW_EXPORT_FLAG is an NCRYPT_EXPORT_POLICY_PROPERTY *key* flag.
# It is NOT a valid NCryptExportKey dwFlags bit — using it makes "any error"
# look like non-exportability (invalid-parameter), which proves nothing.
NCRYPT_SILENT_FLAG = 0x00000040
NCRYPT_ALLOW_EXPORT_FLAG = 0x00000001

NTE_BAD_KEY_STATE = 0x8009000B
NTE_PERM = 0x80090010
NTE_NOT_SUPPORTED = 0x80090029  # often wrong blob/alg — NOT exact non-exportable proof

# Exact non-exportable refusal only. NTE_NOT_SUPPORTED is excluded because an
# invalid blob type / alg on an *exportable* key also returns it (A64 / HIGH#7).
EXACT_NON_EXPORTABLE_NCRYPT_STATUSES: frozenset[int] = frozenset({
    NTE_BAD_KEY_STATE,
    NTE_PERM,
})

# Key-creation export-policy bits (NCRYPT_EXPORT_POLICY_PROPERTY) — NOT export dwFlags.
NCRYPT_ALLOW_PLAINTEXT_EXPORT_FLAG = 0x00000002


def ncrypt_export_key_dwflags() -> int:
    """Valid ``NCryptExportKey`` dwFlags — silent only; never allow-export."""
    return NCRYPT_SILENT_FLAG


def refuse_invalid_ncrypt_export_flags(dw_flags: int) -> None:
    """Raise when ``dw_flags`` includes invalid export bits (A64 / HIGH#7)."""
    if not isinstance(dw_flags, int) or isinstance(dw_flags, bool):
        raise TypeError("dw_flags must be an int")
    if dw_flags & NCRYPT_ALLOW_EXPORT_FLAG:
        raise ValueError(
            "NCRYPT_ALLOW_EXPORT_FLAG is not a valid NCryptExportKey dwFlags bit "
            "(key export-policy property only; refuses any-error false proof)"
        )
    allowed = NCRYPT_SILENT_FLAG
    if dw_flags & ~allowed:
        raise ValueError(f"invalid NCryptExportKey dwFlags: 0x{dw_flags:08x}")


def classify_ncrypt_private_export_status(status: int) -> str:
    """Classify an ``NCryptExportKey`` status for non-exportability proof.

    Returns:
      - ``"exported"`` — status 0 (exportable positive control path)
      - ``"non_exportable"`` — exact refusal class only
      - ``"invalid_operation"`` — any other error (must NOT count as proof)
    """
    if not isinstance(status, int) or isinstance(status, bool):
        raise TypeError("status must be an int")
    # Normalize to unsigned 32-bit for HRESULT-style codes passed as signed.
    code = status & 0xFFFFFFFF
    if code == 0:
        return "exported"
    if code in {s & 0xFFFFFFFF for s in EXACT_NON_EXPORTABLE_NCRYPT_STATUSES}:
        return "non_exportable"
    return "invalid_operation"
