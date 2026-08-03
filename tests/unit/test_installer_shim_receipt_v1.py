"""Round-60 RED group 1: InstallerShimReceiptV1 + TxR PATH + ProgramData authority.

Task 2A / #89 Step 1. Parser is a behaviorless shell with exact contract tests.
Authority/CNG/identity/TxR use primitive OS seams — no final-authority bool/trace
adapters, no production-namespace test crypto. Mandatory Windows integration
nodes exercise real ProgramData/CNG/no-follow/TxR. install.ps1 string scan is
not the sole RED.

These tests MUST fail against unmodified / behaviorless seams. Do not weaken.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tensor_grep.cli import installer_shim_receipt as receipt_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"
RETAINED_ID = "vol=1;file=managed"
_WINDOWS = sys.platform == "win32"


def task2a_owned(fn):  # type: ignore[no-untyped-def]
    """Ownership marker for closed-world AST census (must match helper name)."""
    fn._task2a_owned = True  # type: ignore[attr-defined]
    return fn


def _windows_required(fn):
    fn._task2a_windows_required = True  # type: ignore[attr-defined]
    fn._task2a_owned = True  # type: ignore[attr-defined]
    return fn


def _require_windows() -> None:
    if not _WINDOWS:
        pytest.skip("Windows installer integration node")


def _valid_receipt_dict(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "version": 1,
        "schema": "InstallerShimReceiptV1",
        "release_tag": "v1.102.1",
        "checksums_asset_sha256": "aa" * 32,
        "install_command_digest": "bb" * 32,
        "managed_directory_identity": RETAINED_ID,
        "installer_state_identity": "vol=1;file=state",
        "selected_release_asset_identity": "vol=1;file=asset",
        "generated_shim_bytes_digest": "cc" * 32,
        "cng_public_key_thumbprint": "dd" * 32,
    }
    base.update(overrides)
    return base


def _receipt_obj(**overrides: object) -> receipt_mod.InstallerShimReceiptV1:
    raw = _valid_receipt_dict(**overrides)
    return receipt_mod.InstallerShimReceiptV1(
        version=int(raw["version"]),  # type: ignore[arg-type]
        release_tag=str(raw["release_tag"]),
        checksums_asset_sha256=str(raw["checksums_asset_sha256"]),
        install_command_digest=str(raw["install_command_digest"]),
        managed_directory_identity=str(raw["managed_directory_identity"]),
        installer_state_identity=str(raw["installer_state_identity"]),
        selected_release_asset_identity=str(raw["selected_release_asset_identity"]),
        generated_shim_bytes_digest=str(raw["generated_shim_bytes_digest"]),
        cng_public_key_thumbprint=str(raw["cng_public_key_thumbprint"]),
    )


# Test-local crypto only (never imported from production namespace).
@dataclass
class _TestSigner:
    seed: bytes = b"tensor-grep-round60-test-signer"

    @property
    def public_key_thumbprint(self) -> str:
        return hashlib.sha256(self.seed + b":pub").hexdigest()

    def sign(self, canonical_bytes: bytes) -> bytes:
        return hashlib.sha256(self.seed + b":sig:" + canonical_bytes).digest()


@dataclass
class _TestCng:
    signer: _TestSigner = None  # type: ignore[assignment]
    key_name: str = receipt_mod.CNG_KEY_NAME
    deleted: list[str] = None  # type: ignore[assignment]
    export_attempts: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.signer is None:
            self.signer = _TestSigner()
        if self.deleted is None:
            self.deleted = []
        if self.export_attempts is None:
            self.export_attempts = []

    def sign_canonical(self, canonical_bytes: bytes) -> receipt_mod.CngSignResult:
        return receipt_mod.CngSignResult(
            signature=self.signer.sign(canonical_bytes),
            public_key_thumbprint=self.signer.public_key_thumbprint,
            key_name=self.key_name,
            exportable=False,
        )

    def verify_canonical(
        self, canonical_bytes: bytes, signature: bytes, public_key_thumbprint: str
    ) -> bool:
        if public_key_thumbprint != self.signer.public_key_thumbprint:
            return False
        return hmac_compare(signature, self.signer.sign(canonical_bytes))

    def reopen_named_key(self, key_name: str) -> receipt_mod.CngKeyBinding:
        if key_name != self.key_name:
            raise FileNotFoundError(key_name)
        return receipt_mod.CngKeyBinding(
            key_name=self.key_name,
            public_key_thumbprint=self.signer.public_key_thumbprint,
            exportable=False,
        )

    def attempt_export_private_key(self, key_name: str) -> bytes:
        self.export_attempts.append(key_name)
        raise PermissionError(f"CNG key {key_name!r} is non-exportable")

    def delete_named_key(self, key_name: str) -> None:
        self.deleted.append(key_name)


def hmac_compare(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b, strict=True):
        result |= x ^ y
    return result == 0


@dataclass
class _RecordingProtectedOpener:
    identity: str = "vol=pd;file=state"
    handle: int = 42
    security_descriptor: bytes | None = b"\x01\x00sd"

    def open_protected_root(self) -> receipt_mod.ProtectedRootOpen:
        return receipt_mod.ProtectedRootOpen(
            handle=self.handle,
            volume_file_id=self.identity,
            security_descriptor=self.security_descriptor,
        )


@dataclass
class _RecordingNoFollow:
    mapping: dict[str, receipt_mod.OpenedDirectoryIdentity]

    def open_directory_nofollow(self, token: str) -> receipt_mod.OpenedDirectoryIdentity:
        return self.mapping.get(
            token,
            receipt_mod.OpenedDirectoryIdentity("vol=0;file=missing", token),
        )

    def open_leaf_nofollow(self, token: str) -> receipt_mod.OpenedDirectoryIdentity:
        return self.open_directory_nofollow(token)


@dataclass
class _RecordingTxr:
    fail_mode: str | None = None

    def create_transaction(self) -> int:
        return 1

    def transacted_registry_open(self, transaction: int, key_path: str) -> int:
        _ = transaction, key_path
        if self.fail_mode == "unsupported":
            raise OSError("TxF unsupported")
        return 2

    def transacted_registry_write(self, key_handle: int, value: str) -> None:
        _ = key_handle, value
        if self.fail_mode == "race":
            raise OSError("concurrent PATH change")

    def commit_transaction(self, transaction: int) -> None:
        _ = transaction
        if self.fail_mode == "commit":
            raise OSError("CommitTransaction failed")

    def rollback_transaction(self, transaction: int) -> None:
        _ = transaction


@task2a_owned
def test_parser_accepts_valid_receipt_positive_control() -> None:
    receipt = receipt_mod.parse_installer_shim_receipt(_valid_receipt_dict())
    assert receipt.version == 1
    assert receipt.release_tag == "v1.102.1"


@task2a_owned
@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({**_valid_receipt_dict(), "version": True}, "version|type|bool"),
        ({**_valid_receipt_dict(), "version": "1"}, "version|type|string"),
        ({**_valid_receipt_dict(), "schema": "OtherSchema"}, "schema"),
        ({**_valid_receipt_dict(), "checksums_asset_sha256": "g" * 64}, "digest|alphabet"),
        ({**_valid_receipt_dict(), "install_command_digest": "a" * 63}, "digest|length"),
        ({**_valid_receipt_dict(), "release_tag": ["v1"]}, "nonstring|type"),
        ({**_valid_receipt_dict(), "managed_directory_identity": {"x": 1}}, "nonstring|type"),
        ({**_valid_receipt_dict(), "installer_state_identity": True}, "nonstring|type|bool"),
    ],
    ids=[
        "bool_version",
        "string_version",
        "arbitrary_schema",
        "digest_alphabet",
        "digest_length",
        "list_identity",
        "dict_identity",
        "bool_identity",
    ],
)
def test_parser_refuses_schema_type_value_length_negatives(payload: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        receipt_mod.parse_installer_shim_receipt(payload)


@task2a_owned
def test_parser_refuses_corrupt_duplicate_unknown_oversized_deep() -> None:
    with pytest.raises(ValueError, match=r"corrupt|duplicate|unknown|bounded|depth"):
        receipt_mod.parse_installer_shim_receipt("{not-json")
    dup = (
        '{"version":1,"schema":"InstallerShimReceiptV1","release_tag":"v1",'
        '"release_tag":"v2","checksums_asset_sha256":"' + "a" * 64 + '",'
        '"install_command_digest":"' + "b" * 64 + '",'
        '"managed_directory_identity":"x","installer_state_identity":"y",'
        '"selected_release_asset_identity":"z",'
        '"generated_shim_bytes_digest":"' + "c" * 64 + '",'
        '"cng_public_key_thumbprint":"' + "d" * 64 + '"}'
    )
    with pytest.raises(ValueError, match="duplicate"):
        receipt_mod.parse_installer_shim_receipt(dup)
    hostile = dict(_valid_receipt_dict())
    hostile["unknown_authority"] = True
    with pytest.raises(ValueError, match="unknown"):
        receipt_mod.parse_installer_shim_receipt(hostile)
    oversized = json.dumps(_valid_receipt_dict()).encode("utf-8") + (b"x" * (65 * 1024))
    with pytest.raises(ValueError, match="bounded"):
        receipt_mod.parse_installer_shim_receipt(oversized)
    deep_json = {"version": 1, "schema": "InstallerShimReceiptV1"}
    cursor: dict = deep_json
    for _i in range(12):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    with pytest.raises(ValueError, match=r"depth|bounded|unknown|missing"):
        receipt_mod.parse_installer_shim_receipt(deep_json)


@task2a_owned
def test_parser_refuses_install_command_digest_only_receipt() -> None:
    digest_only = {
        "version": 1,
        "schema": "InstallerShimReceiptV1",
        "install_command_digest": "b" * 64,
    }
    with pytest.raises(ValueError, match=r"missing|authority"):
        receipt_mod.parse_installer_shim_receipt(digest_only)
    assert receipt_mod.install_command_digest_is_authority() is False


@task2a_owned
def test_no_ambient_discover_magic_without_protected_state() -> None:
    planted = json.dumps(_valid_receipt_dict()).encode("utf-8")
    assert (
        receipt_mod.discover_installer_shim_receipt(
            path_entries=[r"C:\evil\InstallerShimReceiptV1.json"],
            planted_receipt_bytes=planted,
        )
        is None
    )
    assert (
        receipt_mod.discover_installer_shim_receipt(
            managed_binary_directory=r"C:\Users\x\.tensor-grep\bin",
            planted_receipt_bytes=planted,
        )
        is None
    )
    assert (
        receipt_mod.discover_installer_shim_receipt(
            caller_selected_state_path=r"D:\attacker\state",
            planted_receipt_bytes=planted,
        )
        is None
    )
    assert receipt_mod.discover_installer_shim_receipt() is None


@task2a_owned
def test_protected_state_injection_is_sole_positive_authority() -> None:
    log = receipt_mod.PrimitiveCallLog()
    opener = _RecordingProtectedOpener()
    # Behaviorless SD evaluation → RED until GREEN evaluates the raw descriptor.
    with pytest.raises(NotImplementedError, match=r"security_descriptor|SD|ACL"):
        receipt_mod.discover_installer_shim_receipt(protected_opener=opener, call_log=log)
    assert any(name == "open_protected_installer_state" for name, _ in log.entries)
    # Null handle is refused; a non-zero positive handle (including 7) is not
    # intrinsically fake — authority comes from SD evaluation, not the integer.
    with pytest.raises((PermissionError, NotImplementedError)):
        receipt_mod.open_protected_installer_state(
            opener=_RecordingProtectedOpener(handle=0),
            call_log=receipt_mod.PrimitiveCallLog(),
        )


@task2a_owned
def test_cng_binding_negative_and_positive_via_primitives() -> None:
    receipt = _receipt_obj()
    log = receipt_mod.PrimitiveCallLog()
    cng = _TestCng()
    # Without CNG primitives: fail closed (no production HMAC).
    with pytest.raises(NotImplementedError, match="CNG"):
        receipt_mod.cng_verify_receipt(
            receipt,
            signature=b"\x01" * 32,
            public_key_thumbprint="0" * 64,
            call_log=log,
        )
    # Positive: injected CNG primitive must produce signed output and verify True
    # after GREEN (canonical_receipt_bytes still raises today → intended RED).
    signed = receipt_mod.cng_sign_receipt(receipt, cng=cng, call_log=log)
    assert signed.signature
    assert len(signed.public_key_thumbprint) == 64
    assert signed.key_name == receipt_mod.CNG_KEY_NAME
    assert signed.exportable is False
    assert (
        receipt_mod.cng_verify_receipt(
            receipt,
            signature=signed.signature,
            public_key_thumbprint=signed.public_key_thumbprint,
            cng=cng,
            call_log=log,
        )
        is True
    )
    # Named-key reopen stability + non-exportability (independent attacker export).
    rebound = cng.reopen_named_key(signed.key_name)
    assert rebound.key_name == signed.key_name
    assert rebound.public_key_thumbprint == signed.public_key_thumbprint
    assert rebound.exportable is False
    with pytest.raises(PermissionError, match="non-exportable"):
        cng.attempt_export_private_key(signed.key_name)
    assert cng.export_attempts == [signed.key_name]
    # Tamper rejection.
    tampered = bytes(b ^ 0xFF for b in signed.signature)
    assert (
        receipt_mod.cng_verify_receipt(
            receipt,
            signature=tampered,
            public_key_thumbprint=signed.public_key_thumbprint,
            cng=cng,
        )
        is False
    )
    cng.delete_named_key(signed.key_name)
    assert cng.deleted == [signed.key_name]


@task2a_owned
def test_opened_directory_same_identity_aliases_positive() -> None:
    opener = _RecordingNoFollow({
        r"C:\Users\x\.tensor-grep\bin": receipt_mod.OpenedDirectoryIdentity(
            RETAINED_ID, r"C:\Users\x\.tensor-grep\bin"
        ),
        r"c:\users\x\.tensor-grep\bin": receipt_mod.OpenedDirectoryIdentity(
            RETAINED_ID, r"c:\users\x\.tensor-grep\bin"
        ),
        r"C:\Users\x\TENSOR~1\bin": receipt_mod.OpenedDirectoryIdentity(
            RETAINED_ID, r"C:\Users\x\TENSOR~1\bin"
        ),
        r"\\?\C:\Users\x\.tensor-grep\bin": receipt_mod.OpenedDirectoryIdentity(
            RETAINED_ID, r"\\?\C:\Users\x\.tensor-grep\bin"
        ),
        r"C:\Users\x\.tensor-grep\bin\\": receipt_mod.OpenedDirectoryIdentity(
            RETAINED_ID, r"C:\Users\x\.tensor-grep\bin\\"
        ),
    })
    log = receipt_mod.PrimitiveCallLog()
    for token in opener.mapping:
        assert (
            receipt_mod.path_token_matches_retained_managed_directory(
                token,
                retained_managed_directory_identity=RETAINED_ID,
                opener=opener,
                call_log=log,
            )
            is True
        )
    assert any(name == "nofollow_opened" for name, _ in log.entries)


@task2a_owned
def test_junction_reparse_and_wrong_identity_negative() -> None:
    opener = _RecordingNoFollow({
        r"C:\Users\x\.tensor-grep\junction-alias": receipt_mod.OpenedDirectoryIdentity(
            RETAINED_ID,
            r"C:\Users\x\.tensor-grep\junction-alias",
            is_reparse_or_junction=True,
        ),
        r"C:\other\bin": receipt_mod.OpenedDirectoryIdentity("vol=9;file=other", r"C:\other\bin"),
    })
    assert (
        receipt_mod.path_token_matches_retained_managed_directory(
            r"C:\Users\x\.tensor-grep\junction-alias",
            retained_managed_directory_identity=RETAINED_ID,
            opener=opener,
        )
        is False
    )
    assert (
        receipt_mod.path_token_matches_retained_managed_directory(
            r"C:\other\bin",
            retained_managed_directory_identity=RETAINED_ID,
            opener=opener,
        )
        is False
    )


@task2a_owned
def test_txr_happy_path_sequence_create_open_write_commit() -> None:
    log = receipt_mod.PrimitiveCallLog()
    # Behaviorless orchestration must fail today; GREEN must emit this exact sequence.
    trace = receipt_mod.mutate_user_path_txr_only(
        path_preimage=r"C:\old;C:\managed",
        intended_image=r"C:\old",
        remove_token_identity=RETAINED_ID,
        txr=_RecordingTxr(),
        call_log=log,
    )
    assert trace.calls == (
        "CreateTransaction",
        "transacted_open",
        "transacted_write",
        "CommitTransaction",
    )
    assert [name for name, _ in log.entries if name.startswith(("Create", "transacted", "Commit"))]


@task2a_owned
@pytest.mark.parametrize("fail_mode", ["unsupported", "race", "commit"])
def test_txr_failure_arms_rollback_without_fallback(fail_mode: str) -> None:
    log = receipt_mod.PrimitiveCallLog()
    # Behaviorless orchestration must fail today; GREEN must RollbackTransaction
    # with no non-TxR fallback.
    with pytest.raises(OSError):
        receipt_mod.mutate_user_path_txr_only(
            path_preimage=r"C:\old;C:\managed",
            intended_image=r"C:\old",
            remove_token_identity=RETAINED_ID,
            txr=_RecordingTxr(fail_mode=fail_mode),
            call_log=log,
        )
    assert any(name == "RollbackTransaction" for name, _ in log.entries)


@task2a_owned
def test_install_ps1_path_mutation_is_txr_only_no_cas_fallback() -> None:
    content = INSTALL_PS1.read_text(encoding="utf-8")
    assert receipt_mod.install_ps1_uses_txr_only(content), (
        "scripts/install.ps1 must mutate User PATH only through CreateTransaction "
        "→ transacted open/write → CommitTransaction with no "
        "[Environment]::SetEnvironmentVariable Path fallback"
    )
    assert "CreateTransaction" in content
    assert "CommitTransaction" in content


@_windows_required
def test_windows_programdata_protected_root_integration() -> None:
    """ProgramData ACL positive: independent raw-SD inspection is the authority.

    Do not accept production ``evaluate_protected_root_security_descriptor`` True
    as sole proof — independently require SYSTEM + Administrators authority and
    no permissive user write ACE.
    """
    _require_windows()
    with receipt_mod.open_protected_installer_state() as opened:
        assert opened.handle not in (0, -1)
        assert opened.volume_file_id
        assert opened.security_descriptor is not None
        sd = opened.security_descriptor
        # Independent test-side inspection (not production evaluator).
        assert _independently_inspect_programdata_sd(sd), (
            "raw security descriptor must grant exact SYSTEM and Administrators "
            "authority with no permissive user write ACE"
        )
        # Production evaluator may agree, but is never sole proof.
        _ = receipt_mod.evaluate_protected_root_security_descriptor(sd)


# --- Pure SDDL DACL parser (platform-neutral; Windows conversion is separate) ---

# Exact DACL control flags only (SDDL control-flag grammar for this protected root).
_ALLOWED_DACL_FLAG_TOKENS = frozenset({"P", "AR", "AI"})

_WRITE_FULL_RIGHT_TOKENS = frozenset({
    "GA",  # GENERIC_ALL
    "GW",  # GENERIC_WRITE
    "GX",  # GENERIC_EXECUTE (paired with write grants in some DACLs; not sole write)
    "FA",  # FILE_ALL_ACCESS
    "FW",  # FILE_GENERIC_WRITE
    "WD",  # WRITE_DAC
    "WO",  # WRITE_OWNER
    "SD",  # DELETE
    "WA",  # WRITE_ATTRIBUTES
    "WE",  # WRITE_EXTENDED_ATTRIBUTES
    "DC",  # Delete Child / directory write-class
    "LC",  # List Children (kept known; not write/full alone)
    "CR",  # Control Access
    "SW",  # Standard Write
    "WP",  # Write Property
    "DT",  # Delete Tree
    "KA",  # KEY_ALL_ACCESS — restricted write authority (never read-like)
    "KW",  # KEY_WRITE — restricted write authority (never read-like)
})
# Rights that are NOT write/full by themselves (read/execute/control).
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
    "KR",  # KEY_READ — read-like only
    "KX",  # KEY_EXECUTE — read-like only
})
_KNOWN_RIGHT_TOKENS = _WRITE_FULL_RIGHT_TOKENS | _READ_LIKE_RIGHT_TOKENS
# Write/full authority tokens that must be restricted to SY+BA only.
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
    "KA",  # KEY_ALL_ACCESS
    "KW",  # KEY_WRITE
})
_ALLOWED_WRITE_TRUSTEE_ALIASES = {
    "SY": "SY",
    "S-1-5-18": "SY",
    "BA": "BA",
    "S-1-5-32-544": "BA",
}


def _extract_sddl_dacl_body(sddl: str) -> str | None:
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
    """Parse DACL flags as exact ``P`` / ``AR`` / ``AI`` tokens only.

    Unknown flags, spaces, or stray text return None (conservative reject).
    """
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


def _parse_sddl_right_tokens(rights: str) -> set[str] | None:
    """Tokenize SDDL rights into exact 2-char codes.

    Rejects hex/numeric rights and any unknown token conservatively (returns None).
    """
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

    Requires a protected DACL (exact ``P`` flag token among only ``P``/``AR``/``AI``),
    parses every ACE with exactly 6 semicolon fields, accepts write/full allow
    ACEs (including ``KA``/``KW`` registry write authority) only for exact
    SYSTEM (SY / S-1-5-18) and Builtin Administrators (BA / S-1-5-32-544),
    requires both trustees present with write/full, and rejects stray text
    outside flags/ACE records, unbalanced closes, malformed ACEs, numeric /
    unknown rights conservatively.
    """
    if not sddl or not isinstance(sddl, str):
        return False
    dacl_body = _extract_sddl_dacl_body(sddl)
    if dacl_body is None:
        return False
    # Flags prefix before the first ACE '('.
    paren = dacl_body.find("(")
    flags = dacl_body if paren < 0 else dacl_body[:paren]
    flag_tokens = _parse_dacl_flags(flags)
    if flag_tokens is None or "P" not in flag_tokens:
        return False
    if paren < 0:
        return False

    # Exact ACE scan: only consecutive ``(...)`` records; any stray text rejects.
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
            return False  # unmatched / unbalanced close
    if not ace_bodies:
        return False

    write_trustees_seen: set[str] = set()
    for ace in ace_bodies:
        parts = ace.split(";")
        if len(parts) != 6:
            return False
        ace_type, _flags, rights, _object_guid, _inherit_guid, trustee = parts
        right_tokens = _parse_sddl_right_tokens(rights)
        if right_tokens is None:
            return False
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


def _independently_inspect_programdata_sd(security_descriptor: bytes) -> bool:
    """Windows conversion helper → pure SDDL DACL evaluator.

    Converts the raw SECURITY_DESCRIPTOR to SDDL, then evaluates via
    ``evaluate_programdata_sddl_dacl`` (never substring-searches SY/BA alone,
    never uses ``D:([^S]*)``).
    """
    import ctypes
    from ctypes import wintypes

    if not security_descriptor:
        return False
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.ULONG),
    ]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    buf = ctypes.create_string_buffer(security_descriptor)
    out = wintypes.LPWSTR()
    out_len = wintypes.ULONG()
    # DACL_SECURITY_INFORMATION | OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION
    si = 0x00000001 | 0x00000002 | 0x00000004
    if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        buf, 1, si, ctypes.byref(out), ctypes.byref(out_len)
    ):
        raise OSError("ConvertSecurityDescriptorToStringSecurityDescriptorW failed")
    try:
        sddl = ctypes.wstring_at(out)
    finally:
        kernel32.LocalFree(out)
    return evaluate_programdata_sddl_dacl(sddl)


@task2a_owned
def test_programdata_sddl_dacl_parser_platform_neutral_vectors() -> None:
    """Platform-neutral vectors for the pure SDDL DACL parser (no Win32 APIs).

    Proves S: SACL boundary with SY trustees (the ``D:([^S]*)`` trap), exact
    SY+BA acceptance (including ``KA``/``KW`` write authority), permissive /
    foreign write rejection, exact ``P``/``AR``/``AI`` flag parsing, stray text /
    unmatched close / extra ACE fields rejection, missing-P rejection, and
    malformed / numeric-rights / unknown-rights rejection.
    """
    import re

    # Positive: protected DACL, SY+BA write/full, trailing SACL must not truncate SY.
    with_sacl = "O:SYG:SYD:PAI(A;;FA;;;SY)(A;;FA;;;BA)S:AI(AU;SAFA;DCLCRPCRSDWDWO;;;WD)"
    assert evaluate_programdata_sddl_dacl(with_sacl) is True
    # Control: the retired regex truncates at SY and would drop BA — prove why
    # the exact extractor is required.
    retired = re.search(r"D:([^S]*)", with_sacl)
    assert retired is not None
    assert "BA" not in retired.group(1), "premise: retired [^S]* truncates before BA"
    assert "BA" in (_extract_sddl_dacl_body(with_sacl) or "")

    # Positive without SACL, SID forms for trustees.
    sid_form = "D:P(A;;FA;;;S-1-5-18)(A;;FA;;;S-1-5-32-544)"
    assert evaluate_programdata_sddl_dacl(sid_form) is True

    # KA (KEY_ALL_ACCESS) / KW (KEY_WRITE) are restricted write — SY+BA pair OK.
    assert evaluate_programdata_sddl_dacl("D:P(A;;KA;;;SY)(A;;KA;;;BA)") is True
    assert evaluate_programdata_sddl_dacl("D:P(A;;KW;;;SY)(A;;KW;;;BA)") is True
    assert evaluate_programdata_sddl_dacl("D:P(A;;KA;;;SY)(A;;KW;;;BA)") is True

    # Foreign KA / KW write ACEs must reject (same class as FA/GA permissive).
    assert evaluate_programdata_sddl_dacl("D:P(A;;KA;;;SY)(A;;KA;;;BA)(A;;KA;;;WD)") is False
    assert evaluate_programdata_sddl_dacl("D:P(A;;KW;;;SY)(A;;KW;;;BA)(A;;KW;;;AU)") is False

    # Permissive user write/full ACE must reject.
    permissive = "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FA;;;WD)"
    assert evaluate_programdata_sddl_dacl(permissive) is False
    permissive_au = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;FW;;;AU)"
    assert evaluate_programdata_sddl_dacl(permissive_au) is False

    # Missing protected-DACL P flag must reject (AI alone is insufficient).
    missing_p = "D:AI(A;;FA;;;SY)(A;;FA;;;BA)"
    assert evaluate_programdata_sddl_dacl(missing_p) is False

    # Garbage / unknown DACL flags and stray text outside ACE records.
    assert evaluate_programdata_sddl_dacl("D:PX(A;;FA;;;SY)(A;;FA;;;BA)") is False
    assert evaluate_programdata_sddl_dacl("D:P junk(A;;FA;;;SY)(A;;FA;;;BA)") is False
    assert evaluate_programdata_sddl_dacl("D:P(A;;FA;;;SY)GARBAGE(A;;FA;;;BA)") is False

    # ACE field count must be exactly 6; extra fields / unmatched close reject.
    assert evaluate_programdata_sddl_dacl("D:P(A;;FA;;;SY;EXTRA)(A;;FA;;;BA)") is False
    assert evaluate_programdata_sddl_dacl("D:P(A;;FA;;;SY)(A;;FA;;;BA") is False
    assert evaluate_programdata_sddl_dacl("D:P(A;;FA;;;SY)(A;;FA;;;BA))") is False

    # Malformed ACE / numeric rights / unknown rights / missing trustee set.
    assert evaluate_programdata_sddl_dacl("D:P(A;;FA;;;SY)") is False  # missing BA
    assert evaluate_programdata_sddl_dacl("D:P(A;;FA;;;SY)(A;;FA)") is False  # malformed
    assert evaluate_programdata_sddl_dacl("D:P(A;;0x1f01ff;;;SY)(A;;FA;;;BA)") is False
    assert evaluate_programdata_sddl_dacl("D:P(A;;ZZ;;;SY)(A;;FA;;;BA)") is False
    assert evaluate_programdata_sddl_dacl("") is False
    assert evaluate_programdata_sddl_dacl("O:SYG:SY") is False


@task2a_owned
def test_protected_root_open_close_ownership_without_acl_bool() -> None:
    """Close ownership RED: opener failure path must close without an ACL bool verdict."""
    closed: list[int] = []

    class _OwningOpener:
        def open_protected_root(self) -> receipt_mod.ProtectedRootOpen:
            return receipt_mod.ProtectedRootOpen(
                handle=99,
                volume_file_id="vol=pd;file=state",
                security_descriptor=b"\x01\x00sd",
                _closer=closed.append,
            )

    # Behaviorless SD evaluation raises — must still close the retained handle.
    # Do not accept any final ACL bool as authority for this ownership contract.
    with pytest.raises(NotImplementedError, match=r"security_descriptor|SD|ACL"):
        receipt_mod.open_protected_installer_state(opener=_OwningOpener())
    assert closed == [99]
    # Direct context-manager ownership is also idempotent.
    closed.clear()
    opened = receipt_mod.ProtectedRootOpen(
        handle=77,
        volume_file_id="vol=x",
        security_descriptor=None,
        _closer=closed.append,
    )
    with opened:
        assert closed == []
    assert closed == [77]
    opened.close()
    assert closed == [77]


def _owning_opener(closed: list[int], handle: int = 88) -> _RecordingProtectedOpener:
    class _Owning(_RecordingProtectedOpener):
        def open_protected_root(self) -> receipt_mod.ProtectedRootOpen:
            return receipt_mod.ProtectedRootOpen(
                handle=handle,
                volume_file_id=self.identity,
                security_descriptor=self.security_descriptor,
                _closer=closed.append,
            )

    return _Owning(handle=handle)


@task2a_owned
def test_discover_closes_protected_root_on_success_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    monkeypatch.setattr(
        receipt_mod,
        "evaluate_protected_root_security_descriptor",
        lambda _sd: True,
    )
    receipt = _receipt_obj()

    def _reader(_opened: receipt_mod.ProtectedRootOpen) -> receipt_mod.InstallerShimReceiptV1:
        return receipt

    out = receipt_mod.discover_installer_shim_receipt(
        protected_opener=_owning_opener(closed),
        _read_protected_receipt=_reader,
    )
    assert out is receipt
    assert closed == [88]


@task2a_owned
def test_discover_closes_protected_root_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    monkeypatch.setattr(
        receipt_mod,
        "evaluate_protected_root_security_descriptor",
        lambda _sd: True,
    )
    with pytest.raises(NotImplementedError, match="behaviorless"):
        receipt_mod.discover_installer_shim_receipt(
            protected_opener=_owning_opener(closed),
        )
    assert closed == [88]


@task2a_owned
def test_discover_closes_protected_root_on_base_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    monkeypatch.setattr(
        receipt_mod,
        "evaluate_protected_root_security_descriptor",
        lambda _sd: True,
    )

    def _reader(_opened: receipt_mod.ProtectedRootOpen) -> receipt_mod.InstallerShimReceiptV1:
        raise BaseException("discover base-exception arm")

    with pytest.raises(BaseException, match="discover base-exception"):
        receipt_mod.discover_installer_shim_receipt(
            protected_opener=_owning_opener(closed),
            _read_protected_receipt=_reader,
        )
    assert closed == [88]


@task2a_owned
def test_discover_closes_protected_root_cleanup_failure_preserves_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    def _closer(handle: int) -> None:
        closed.append(handle)
        raise RuntimeError("cleanup boom")

    class _Owning:
        def open_protected_root(self) -> receipt_mod.ProtectedRootOpen:
            return receipt_mod.ProtectedRootOpen(
                handle=66,
                volume_file_id="vol=pd;file=state",
                security_descriptor=b"\x01\x00sd",
                _closer=_closer,
            )

    monkeypatch.setattr(
        receipt_mod,
        "evaluate_protected_root_security_descriptor",
        lambda _sd: True,
    )
    with pytest.raises(NotImplementedError, match="behaviorless") as exc_info:
        receipt_mod.discover_installer_shim_receipt(protected_opener=_Owning())
    assert closed == [66]
    notes = getattr(exc_info.value, "__notes__", [])
    assert any("cleanup also failed" in n for n in notes)


@task2a_owned
def test_discover_closes_protected_root_cleanup_failure_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Success-path cleanup failure must surface (not swallow) and still attempt close."""
    closed: list[int] = []

    def _closer(handle: int) -> None:
        closed.append(handle)
        raise RuntimeError("cleanup boom on success")

    class _Owning:
        def open_protected_root(self) -> receipt_mod.ProtectedRootOpen:
            return receipt_mod.ProtectedRootOpen(
                handle=55,
                volume_file_id="vol=pd;file=state",
                security_descriptor=b"\x01\x00sd",
                _closer=_closer,
            )

    monkeypatch.setattr(
        receipt_mod,
        "evaluate_protected_root_security_descriptor",
        lambda _sd: True,
    )
    receipt = _receipt_obj()

    def _reader(_opened: receipt_mod.ProtectedRootOpen) -> receipt_mod.InstallerShimReceiptV1:
        return receipt

    with pytest.raises(RuntimeError, match="cleanup boom on success"):
        receipt_mod.discover_installer_shim_receipt(
            protected_opener=_Owning(),
            _read_protected_receipt=_reader,
        )
    assert closed == [55]
    # Retry-safe: a second close must not re-invoke closer after recorded success...
    # but closer raised before marking closed, so retry is allowed — reopen ownership.
    opened = receipt_mod.ProtectedRootOpen(
        handle=55,
        volume_file_id="vol=x",
        security_descriptor=b"\x01",
        _closer=closed.append,
    )
    # Fresh object: close once succeeds.
    opened.close()
    opened.close()
    assert closed == [55, 55]


@task2a_owned
def test_discover_closes_protected_root_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    monkeypatch.setattr(
        receipt_mod,
        "evaluate_protected_root_security_descriptor",
        lambda _sd: True,
    )
    opener = _owning_opener(closed, handle=44)
    with pytest.raises(NotImplementedError, match="behaviorless"):
        receipt_mod.discover_installer_shim_receipt(protected_opener=opener)
    assert closed == [44]
    # Re-open a fresh ProtectedRootOpen and close twice — exact-once.
    closed.clear()
    opened = receipt_mod.ProtectedRootOpen(
        handle=44,
        volume_file_id="vol=x",
        security_descriptor=b"\x01",
        _closer=closed.append,
    )
    opened.close()
    opened.close()
    assert closed == [44]


@task2a_owned
def test_no_production_global_txr_fault_hook() -> None:
    """Finding 16: unlocked production-global TXR fault hook / public setter removed."""
    assert not hasattr(receipt_mod, "set_txr_post_write_fault_hook")
    assert not hasattr(receipt_mod, "_TXR_POST_WRITE_FAULT_HOOK")
    # Per-call post_write_fault is accepted on the disposable path signature.
    import inspect

    params = inspect.signature(receipt_mod.mutate_user_path_txr_only).parameters
    assert "post_write_fault" in params


@task2a_owned
def test_txr_per_call_fault_isolation_event_gated() -> None:
    """Event-gated two-call control: fault adapter A cannot be invoked by call B.

    No global state. Call A holds inside its per-call fault; call B runs with a
    distinct fault (or none) and must not observe A's fault adapter.
    """
    import threading

    a_entered = threading.Event()
    a_release = threading.Event()
    a_calls: list[str] = []
    b_calls: list[str] = []
    errors: list[BaseException] = []

    def fault_a() -> None:
        a_calls.append(threading.current_thread().name)
        a_entered.set()
        assert a_release.wait(timeout=30), "fault_a release timed out"
        raise OSError("fault A")

    def fault_b() -> None:
        b_calls.append(threading.current_thread().name)
        raise OSError("fault B")

    def _run_b() -> None:
        try:
            assert a_entered.wait(timeout=30), "call A never entered fault"
            receipt_mod.mutate_user_path_txr_only(
                path_preimage=r"C:\old",
                intended_image=r"C:\old;C:\b",
                remove_token_identity=RETAINED_ID,
                txr=_RecordingTxr(),
                post_write_fault=fault_b,
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            a_release.set()

    t = threading.Thread(target=_run_b, name="txr-call-B", daemon=True)
    t.start()
    with pytest.raises(OSError, match="fault A"):
        receipt_mod.mutate_user_path_txr_only(
            path_preimage=r"C:\old",
            intended_image=r"C:\old;C:\a",
            remove_token_identity=RETAINED_ID,
            txr=_RecordingTxr(),
            post_write_fault=fault_a,
        )
    t.join(timeout=30)
    assert not t.is_alive()
    assert a_calls == ["MainThread"] or (len(a_calls) == 1 and a_calls[0] != "txr-call-B"), (
        f"fault A invoked from unexpected thread(s): {a_calls!r}"
    )
    # Call B must have used its own fault adapter (or completed); never A's.
    assert "txr-call-B" not in a_calls
    assert all(name != "MainThread" for name in b_calls) or b_calls == ["txr-call-B"]
    assert len(b_calls) == 1 and b_calls[0] == "txr-call-B"
    assert any(isinstance(e, OSError) and "fault B" in str(e) for e in errors)


def _test_side_ncrypt_attempt_private_export(key_name: str) -> None:
    """Test-owned NCrypt open + private export attempt (exact non-exportability)."""
    import ctypes
    from ctypes import wintypes

    ncrypt = ctypes.WinDLL("ncrypt", use_last_error=True)
    NCRYPT_SILENT_FLAG = 0x00000040
    NCRYPT_ALLOW_EXPORT_FLAG = 0x00000001
    # NCryptOpenStorageProvider / NCryptOpenKey / NCryptExportKey
    ncrypt.NCryptOpenStorageProvider.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.LPCWSTR,
        wintypes.DWORD,
    ]
    ncrypt.NCryptOpenStorageProvider.restype = wintypes.LONG
    ncrypt.NCryptOpenKey.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    ncrypt.NCryptOpenKey.restype = wintypes.LONG
    ncrypt.NCryptExportKey.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPVOID,
        ctypes.POINTER(ctypes.c_ubyte),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
    ]
    ncrypt.NCryptExportKey.restype = wintypes.LONG
    ncrypt.NCryptFreeObject.argtypes = [wintypes.HANDLE]
    ncrypt.NCryptFreeObject.restype = wintypes.LONG

    provider = wintypes.HANDLE()
    status = ncrypt.NCryptOpenStorageProvider(
        ctypes.byref(provider), "Microsoft Software Key Storage Provider", 0
    )
    if status != 0:
        raise OSError(f"NCryptOpenStorageProvider failed: 0x{status:08x}")
    key = wintypes.HANDLE()
    try:
        status = ncrypt.NCryptOpenKey(
            provider,
            ctypes.byref(key),
            key_name,
            0,
            NCRYPT_SILENT_FLAG,
        )
        if status != 0:
            raise OSError(f"NCryptOpenKey failed: 0x{status:08x}")
        pcb = wintypes.DWORD(0)
        status = ncrypt.NCryptExportKey(
            key,
            None,
            "PRIVATEBLOB",
            None,
            None,
            0,
            ctypes.byref(pcb),
            NCRYPT_ALLOW_EXPORT_FLAG | NCRYPT_SILENT_FLAG,
        )
        # Non-exportable keys must refuse (NTE_BAD_KEY_STATE / NTE_PERM / similar).
        if status == 0:
            raise AssertionError(
                f"NCryptExportKey succeeded for supposedly non-exportable key {key_name!r}"
            )
        # Exact non-exportability: nonzero status is required.
        raise PermissionError(
            f"CNG key {key_name!r} refused private export (status=0x{status:08x})"
        )
    finally:
        if key.value:
            ncrypt.NCryptFreeObject(key)
        if provider.value:
            ncrypt.NCryptFreeObject(provider)


@_windows_required
def test_windows_cng_sign_verify_integration() -> None:
    """CNG: persisted named key via production default factory; non-exportable.

    Creates/opens through production, signs, verifies, reopens, verifies again,
    checks stable key name/thumbprint, rejects tampered receipt, then
    independently opens the named key with test-side NCrypt ctypes and attempts
    private export (exact non-exportability). Always deletes the test key.
    """
    _require_windows()
    receipt = _receipt_obj()
    key_name = receipt_mod.CNG_KEY_NAME
    try:
        signed = receipt_mod.cng_sign_receipt(receipt)
        assert signed.signature
        assert len(signed.public_key_thumbprint) == 64
        assert signed.key_name == key_name
        assert signed.exportable is False
        assert (
            receipt_mod.cng_verify_receipt(
                receipt,
                signature=signed.signature,
                public_key_thumbprint=signed.public_key_thumbprint,
            )
            is True
        )
        cng = receipt_mod.windows_cng_primitives()
        binding = cng.reopen_named_key(key_name)
        assert binding.key_name == key_name
        assert binding.public_key_thumbprint == signed.public_key_thumbprint
        assert binding.exportable is False
        assert (
            receipt_mod.cng_verify_receipt(
                receipt,
                signature=signed.signature,
                public_key_thumbprint=binding.public_key_thumbprint,
                cng=cng,
            )
            is True
        )
        tampered = _receipt_obj(release_tag="v-TAMPERED")
        assert (
            receipt_mod.cng_verify_receipt(
                tampered,
                signature=signed.signature,
                public_key_thumbprint=signed.public_key_thumbprint,
                cng=cng,
            )
            is False
        )
        with pytest.raises(PermissionError, match=r"non-exportable|refused private export"):
            _test_side_ncrypt_attempt_private_export(key_name)
    finally:
        try:
            receipt_mod.windows_cng_primitives().delete_named_key(key_name)
        except (NotImplementedError, OSError, PermissionError):
            pass


@_windows_required
def test_windows_nofollow_leaf_directory_integration(tmp_path: Path) -> None:
    _require_windows()
    import ctypes
    from ctypes import wintypes

    target = tmp_path / "managed"
    target.mkdir()

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    GENERIC_READ = 0x80000000
    share = 0x00000001 | 0x00000002 | 0x00000004
    handle = kernel32.CreateFileW(
        str(target),
        GENERIC_READ,
        share,
        None,
        3,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle in (wintypes.HANDLE(-1).value, 0):
        raise OSError("CreateFileW failed for nofollow fixture")
    try:
        info = _BY_HANDLE_FILE_INFORMATION()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError("GetFileInformationByHandle failed")
        retained_id = (
            f"{int(info.dwVolumeSerialNumber):08x}:"
            f"{int(info.nFileIndexHigh):08x}:{int(info.nFileIndexLow):08x}"
        )
    finally:
        kernel32.CloseHandle(handle)

    # Production must nofollow-open and compare retained volume/file identity,
    # never str(path) equality.
    matched = receipt_mod.path_token_matches_retained_managed_directory(
        str(target),
        retained_managed_directory_identity=retained_id,
    )
    assert matched is True
    assert matched is not (str(target) == retained_id)


@_windows_required
def test_windows_txr_registry_integration() -> None:
    _require_windows()
    import winreg

    disposable = r"Software\TensorGrepRound60Test\TxrPath"
    parent = r"Software\TensorGrepRound60Test"
    key = None
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, disposable, 0, winreg.KEY_ALL_ACCESS)
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, r"C:\old")
        winreg.CloseKey(key)
        key = None

        # Commit arm: write intended image and read it back.
        trace = receipt_mod.mutate_user_path_txr_only(
            path_preimage=r"C:\old",
            intended_image=r"C:\old;C:\managed",
            remove_token_identity=RETAINED_ID,
            registry_key_path=disposable,
        )
        assert trace.calls == (
            "CreateTransaction",
            "transacted_open",
            "transacted_write",
            "CommitTransaction",
        )
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, disposable, 0, winreg.KEY_READ) as rk:
            value, _regtype = winreg.QueryValueEx(rk, "Path")
        assert value == r"C:\old;C:\managed", (
            f"TxR commit must persist disposable registry value; observed {value!r}"
        )

        # Rollback / no-fallback: per-call post_write_fault (NOT a production-global hook).
        prior = value
        fired = {"n": 0}

        def _fault() -> None:
            fired["n"] += 1
            raise OSError("injected post-write pre-commit fault")

        with pytest.raises(OSError):
            receipt_mod.mutate_user_path_txr_only(
                path_preimage=prior,
                intended_image=r"C:\should-not-land",
                remove_token_identity=RETAINED_ID,
                registry_key_path=disposable,
                post_write_fault=_fault,
            )
        # Independent registry preimage read — no mock TxrPrimitives fallback.
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, disposable, 0, winreg.KEY_READ) as rk:
            after_rollback, _ = winreg.QueryValueEx(rk, "Path")
        assert after_rollback == prior, (
            "TxR rollback/no-fallback must leave disposable registry value unchanged"
        )
        assert fired["n"] >= 1, "per-call post_write_fault must fire after write/before commit"

        # Per-call identity is covered by test_txr_per_call_fault_isolation_event_gated
        # (Event-gated injectable adapters). Do not assert a never-written other_fired counter.

        observed = receipt_mod.abort_disposable_txr_and_read_registry_preimage(
            path_preimage=prior,
            registry_key_path=disposable,
        )
        assert observed == prior
    finally:
        if key is not None:
            winreg.CloseKey(key)
        for candidate in (disposable, parent):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, candidate)
            except OSError:
                pass
