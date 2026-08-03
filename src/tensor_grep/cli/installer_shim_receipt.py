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
    with no non-TxR fallback. When ``txr`` is omitted, fails closed until GREEN
    wires real Kernel Transaction Manager ops — there is no production-global
    TXR fault hook or public setter.
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
    transaction = txr.create_transaction()
    calls.append("CreateTransaction")
    if call_log is not None:
        call_log.record("CreateTransaction", transaction)
    try:
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
        try:
            txr.rollback_transaction(transaction)
            if call_log is not None:
                call_log.record("RollbackTransaction", transaction)
        except BaseException as cleanup_err:
            try:
                original.add_note(f"cleanup also failed: {cleanup_err!r}")
            except Exception:
                pass
        raise original
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
