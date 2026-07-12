"""TDD for task #124: the EvidenceReceipt Ed25519 signing layer.

Bidirectional-oracle discipline throughout: every positive case (a correctly signed/chained
receipt) must PASS, and every negative case (tampered, unsigned-but-trust-requested, wrong key,
oversized file, missing crypto) must FAIL -- a broken oracle that always says "valid" would be
worse than no oracle at all for load-bearing crypto.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import bootstrap, evidence_signing
from tensor_grep.cli.main import app

runner = CliRunner()


def _sample_receipt(**overrides: Any) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "EvidenceReceipt",
        "routing_reason": "evidence-receipt-emit",
        "sidecar_used": False,
        "kind": "evidence-receipt",
        "receipt_schema_version": 1,
        "created_at": "2026-07-12T00:00:00Z",
        "revision": {"status": "present", "commit_sha": "a" * 40, "dirty": False},
    }
    receipt.update(overrides)
    return receipt


# ---------------------------------------------------------------------------
# 1. sign -> verify roundtrip
# ---------------------------------------------------------------------------


def test_sign_then_verify_roundtrip(tmp_path: Path) -> None:
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)

    signed = evidence_signing.sign_receipt(_sample_receipt(), private_key_path=key_path)
    result = evidence_signing.verify_receipt(signed)

    assert result["valid"] is True
    assert result["checks"] == {
        "digest_valid": True,
        "signature_valid": True,
        "key_trusted": None,
    }
    assert result["signed"] is True
    assert result["algorithm"] == "ed25519"
    assert result["key_id"] == result["fingerprint"]
    assert result["errors"] == []


# ---------------------------------------------------------------------------
# 2. tampered -> rejected (flip a byte -> signature_valid=False, non-zero CLI exit)
# ---------------------------------------------------------------------------


def test_tampered_field_is_rejected(tmp_path: Path) -> None:
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    signed = evidence_signing.sign_receipt(_sample_receipt(), private_key_path=key_path)

    tampered = dict(signed)
    tampered["revision"] = dict(tampered["revision"])
    original_sha = tampered["revision"]["commit_sha"]
    tampered["revision"]["commit_sha"] = "b" + original_sha[1:]  # flip one character

    result = evidence_signing.verify_receipt(tampered)

    assert result["checks"]["digest_valid"] is False
    assert result["checks"]["signature_valid"] is False
    assert result["valid"] is False
    assert result["errors"]


def test_tampered_receipt_exits_nonzero_via_cli(tmp_path: Path) -> None:
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    signed = evidence_signing.sign_receipt(_sample_receipt(), private_key_path=key_path)
    tampered = dict(signed)
    tampered["revision"] = {**tampered["revision"], "commit_sha": "0" * 40}
    receipt_path = tmp_path / "tampered.json"
    receipt_path.write_text(json.dumps(tampered), encoding="utf-8")

    result = runner.invoke(app, ["evidence", "verify", str(receipt_path)])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# 3. no-key fail-closed: --sign with no resolvable key -> non-zero, NO receipt written;
#    unsigned default still emits.
# ---------------------------------------------------------------------------


def test_sign_with_no_resolvable_key_raises_and_never_returns_a_partial_receipt(
    tmp_path: Path,
) -> None:
    missing_key = tmp_path / "does_not_exist" / "key"

    with pytest.raises(evidence_signing.EvidenceSigningError, match="not found"):
        evidence_signing.sign_receipt(_sample_receipt(), private_key_path=missing_key)


def test_cli_emit_sign_with_no_key_exits_nonzero_and_writes_no_file(tmp_path: Path) -> None:
    out_path = tmp_path / "receipt.json"
    missing_key = tmp_path / "does_not_exist" / "key"

    result = runner.invoke(
        app,
        [
            "evidence",
            "emit",
            str(tmp_path),
            "--sign",
            "--signing-key",
            str(missing_key),
            "--out",
            str(out_path),
        ],
    )

    assert result.exit_code != 0
    assert not out_path.exists(), "a failed --sign must never leave a receipt on disk"


def test_cli_emit_without_sign_still_emits_an_unsigned_receipt(tmp_path: Path) -> None:
    out_path = tmp_path / "receipt.json"

    result = runner.invoke(app, ["evidence", "emit", str(tmp_path), "--out", str(out_path)])

    assert result.exit_code == 0, result.output
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["receipt_sha256"]
    assert "signature" not in payload
    assert "signing" not in payload


# ---------------------------------------------------------------------------
# 4. canonical-determinism: shuffled dict order -> identical bytes + signature still verifies
# ---------------------------------------------------------------------------


def test_canonical_bytes_and_signature_are_independent_of_dict_key_order(tmp_path: Path) -> None:
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    signed = evidence_signing.sign_receipt(_sample_receipt(), private_key_path=key_path)

    shuffled = {key: signed[key] for key in reversed(list(signed.keys()))}
    shuffled["signing"] = {
        key: signed["signing"][key] for key in reversed(list(signed["signing"].keys()))
    }
    assert list(shuffled.keys()) != list(signed.keys()), "sanity: order must genuinely differ"
    assert list(shuffled["signing"].keys()) != list(signed["signing"].keys())

    assert evidence_signing.canonical_receipt_bytes(
        signed
    ) == evidence_signing.canonical_receipt_bytes(shuffled)
    assert evidence_signing.receipt_digest(signed) == evidence_signing.receipt_digest(shuffled)

    result = evidence_signing.verify_receipt(shuffled)
    assert result["valid"] is True


# ---------------------------------------------------------------------------
# 5. cross-process verify: the verifier only ever sees the receipt + a pinned public key, never
#    the private key -- the asymmetric property HMAC (audit_manifest.py) cannot offer.
# ---------------------------------------------------------------------------


def test_cross_process_verify_with_pinned_trusted_public_key(tmp_path: Path) -> None:
    key_path = tmp_path / "key"
    keypair = evidence_signing.generate_keypair(key_path)
    signed = evidence_signing.sign_receipt(_sample_receipt(), private_key_path=key_path)

    # simulate the verifier side: only the receipt (already JSON-round-tripped) and the pinned
    # public key are available -- no private key material anywhere in this call.
    receipt_on_the_wire = json.loads(json.dumps(signed))
    result = evidence_signing.verify_receipt(
        receipt_on_the_wire, trusted_public_keys=[keypair["public_key"]]
    )

    assert result["valid"] is True
    assert result["checks"]["key_trusted"] is True
    assert result["key_id"] == keypair["key_id"]


# ---------------------------------------------------------------------------
# 6. untrusted-key detected (S2): an attacker can re-sign with their OWN key and embed their OWN
#    public key -- self-consistent (signature_valid=True) but never trusted.
# ---------------------------------------------------------------------------


def test_untrusted_key_is_detected_and_require_trusted_gates_validity(tmp_path: Path) -> None:
    legit_key_path = tmp_path / "legit_key"
    legit_keypair = evidence_signing.generate_keypair(legit_key_path)
    attacker_key_path = tmp_path / "attacker_key"
    evidence_signing.generate_keypair(attacker_key_path)

    # The attacker re-signs the receipt entirely with their own key (embedding their own public
    # key in the process) -- this is the only thing anyone without the legit private key can do.
    forged = evidence_signing.sign_receipt(_sample_receipt(), private_key_path=attacker_key_path)

    default_result = evidence_signing.verify_receipt(
        forged, trusted_public_keys=[legit_keypair["public_key"]]
    )
    assert default_result["checks"]["digest_valid"] is True
    assert default_result["checks"]["signature_valid"] is True  # internally self-consistent
    assert default_result["checks"]["key_trusted"] is False  # but NOT the pinned identity

    strict_result = evidence_signing.verify_receipt(
        forged,
        trusted_public_keys=[legit_keypair["public_key"]],
        require_trusted=True,
    )
    assert strict_result["valid"] is False


# ---------------------------------------------------------------------------
# 7. wrong trusted key: a legitimately signed receipt verified against a mismatched pinned key.
# ---------------------------------------------------------------------------


def test_verify_with_wrong_trusted_key_reports_untrusted_not_a_crash(tmp_path: Path) -> None:
    signer_key_path = tmp_path / "signer_key"
    evidence_signing.generate_keypair(signer_key_path)
    other_key_path = tmp_path / "other_key"
    other_keypair = evidence_signing.generate_keypair(other_key_path)

    signed = evidence_signing.sign_receipt(_sample_receipt(), private_key_path=signer_key_path)

    result = evidence_signing.verify_receipt(
        signed, trusted_public_keys=[other_keypair["public_key"]]
    )

    assert result["checks"]["signature_valid"] is True
    assert result["checks"]["key_trusted"] is False
    assert result["key_id"] != other_keypair["key_id"]


def test_verify_with_malformed_trusted_key_entry_never_matches_and_never_crashes(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    signed = evidence_signing.sign_receipt(_sample_receipt(), private_key_path=key_path)

    result = evidence_signing.verify_receipt(signed, trusted_public_keys=["not-valid-base64!!!"])

    assert result["checks"]["key_trusted"] is False


# ---------------------------------------------------------------------------
# 8. key-file perms 0600 + O_EXCL refuses overwrite + symlink refused
# ---------------------------------------------------------------------------


def test_generate_keypair_creates_the_temp_at_a_restrictive_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-platform (works on Windows too): confirm the temp is created via os.open with a mode
    that grants no group/other bits, mirroring session_store's own atomic-write-permission-window
    regression test -- never create-world-readable-then-chmod."""
    key_path = tmp_path / "key"
    created_modes: list[int] = []
    real_open = os.open

    def _spy_open(path: Any, flags: int, mode: int = 0o777, *args: Any, **kwargs: Any) -> int:
        if flags & os.O_CREAT:
            created_modes.append(mode)
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(evidence_signing.os, "open", _spy_open)

    evidence_signing.generate_keypair(key_path)

    assert created_modes, "the private key temp must be created via os.open(O_CREAT, mode)"
    assert all((mode & 0o077) == 0 for mode in created_modes)


def test_generate_keypair_final_mode_is_0600_on_posix(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission bits are only meaningful on POSIX")
    key_path = tmp_path / "key"

    evidence_signing.generate_keypair(key_path)

    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_generate_keypair_refuses_overwrite_without_force(tmp_path: Path) -> None:
    key_path = tmp_path / "key"
    first = evidence_signing.generate_keypair(key_path)

    with pytest.raises(evidence_signing.EvidenceSigningError, match="already exists"):
        evidence_signing.generate_keypair(key_path)

    # the original key must be untouched by the refused attempt
    assert evidence_signing.public_key_info(key_path) == {
        "public_key": first["public_key"],
        "key_id": first["key_id"],
    }


def test_generate_keypair_force_overwrites_cleanly(tmp_path: Path) -> None:
    key_path = tmp_path / "key"
    first = evidence_signing.generate_keypair(key_path)
    second = evidence_signing.generate_keypair(key_path, force=True)

    assert second["key_id"] != first["key_id"]
    assert evidence_signing.public_key_info(key_path)["key_id"] == second["key_id"]


def test_generate_keypair_refuses_to_write_through_a_symlink_even_with_force(
    tmp_path: Path,
) -> None:
    real_target = tmp_path / "real_target_key"
    evidence_signing.generate_keypair(real_target)
    link_path = tmp_path / "link_key"
    try:
        link_path.symlink_to(real_target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported / not privileged on this platform")

    with pytest.raises(evidence_signing.EvidenceSigningError, match="symlink"):
        evidence_signing.generate_keypair(link_path, force=True)

    # the symlink's target must be untouched by the refused attempt
    assert link_path.is_symlink()


# ---------------------------------------------------------------------------
# 9. digest-only unsigned receipt is still verifiable (keyless integrity check)
# ---------------------------------------------------------------------------


def test_unsigned_digest_only_receipt_is_verifiable(tmp_path: Path) -> None:
    receipt = _sample_receipt()
    receipt["receipt_sha256"] = evidence_signing.receipt_digest(receipt)

    result = evidence_signing.verify_receipt(receipt)

    assert result["valid"] is True
    assert result["signed"] is False
    assert result["checks"] == {
        "digest_valid": True,
        "signature_valid": True,
        "key_trusted": None,
    }
    assert result["key_id"] is None


def test_trusted_key_against_unsigned_receipt_is_invalid(tmp_path: Path) -> None:
    """Symmetric to audit_manifest.py:813-815: a caller that supplies --trusted-key but the
    receipt turns out to be unsigned must get a hard failure, never a silent pass."""
    receipt = _sample_receipt()
    receipt["receipt_sha256"] = evidence_signing.receipt_digest(receipt)
    key_path = tmp_path / "key"
    keypair = evidence_signing.generate_keypair(key_path)

    result = evidence_signing.verify_receipt(receipt, trusted_public_keys=[keypair["public_key"]])

    assert result["valid"] is False
    assert result["errors"]


# ---------------------------------------------------------------------------
# 10. chain: previous_receipt_sha256 match / mismatch
# ---------------------------------------------------------------------------


def test_previous_receipt_digest_prefers_stored_receipt_sha256(tmp_path: Path) -> None:
    previous = _sample_receipt()
    previous["receipt_sha256"] = evidence_signing.receipt_digest(previous)
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")

    digest = evidence_signing.previous_receipt_digest(previous_path)

    assert digest == previous["receipt_sha256"]


def test_chain_link_matches_when_previous_receipt_sha256_agrees(tmp_path: Path) -> None:
    previous = _sample_receipt()
    previous["receipt_sha256"] = evidence_signing.receipt_digest(previous)
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")

    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    current = evidence_signing.sign_receipt(
        _sample_receipt(created_at="2026-07-12T00:05:00Z"),
        private_key_path=key_path,
        previous_receipt_sha256=evidence_signing.previous_receipt_digest(previous_path),
    )

    chain = evidence_signing.verify_receipt_chain(current, previous_path=previous_path)

    assert chain == {"chain_valid": True, "chain_error": None}


def test_chain_link_mismatches_when_previous_receipt_sha256_is_stale(tmp_path: Path) -> None:
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(_sample_receipt(receipt_sha256="0" * 64)), encoding="utf-8")

    current = _sample_receipt(previous_receipt_sha256="f" * 64)

    chain = evidence_signing.verify_receipt_chain(current, previous_path=previous_path)

    assert chain["chain_valid"] is False
    assert chain["chain_error"]


def test_chain_link_invalid_when_receipt_has_no_previous_field(tmp_path: Path) -> None:
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(_sample_receipt()), encoding="utf-8")

    chain = evidence_signing.verify_receipt_chain(_sample_receipt(), previous_path=previous_path)

    assert chain["chain_valid"] is False
    assert chain["chain_error"]


# ---------------------------------------------------------------------------
# 11. missing-crypto fail-closed (monkeypatch the import guard sentinel)
# ---------------------------------------------------------------------------


def test_missing_cryptography_fails_closed_for_keygen_and_sign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        evidence_signing,
        "_CRYPTOGRAPHY_IMPORT_ERROR",
        ImportError("simulated: cryptography unavailable"),
    )

    with pytest.raises(evidence_signing.EvidenceSigningError, match="cryptography"):
        evidence_signing.generate_keypair(tmp_path / "key")

    with pytest.raises(evidence_signing.EvidenceSigningError, match="cryptography"):
        evidence_signing.load_private_key(tmp_path / "key")

    with pytest.raises(evidence_signing.EvidenceSigningError, match="cryptography"):
        evidence_signing.sign_receipt(_sample_receipt(), private_key_path=tmp_path / "key")


def test_missing_cryptography_fails_closed_for_verifying_a_signed_receipt_but_not_unsigned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Build a legitimately signed receipt FIRST, while crypto is genuinely available.
    key_path = tmp_path / "key"
    evidence_signing.generate_keypair(key_path)
    signed = evidence_signing.sign_receipt(_sample_receipt(), private_key_path=key_path)

    unsigned = _sample_receipt()
    unsigned["receipt_sha256"] = evidence_signing.receipt_digest(unsigned)

    monkeypatch.setattr(
        evidence_signing,
        "_CRYPTOGRAPHY_IMPORT_ERROR",
        ImportError("simulated: cryptography unavailable"),
    )

    with pytest.raises(evidence_signing.EvidenceSigningError, match="cryptography"):
        evidence_signing.verify_receipt(signed)

    # A pure digest check never touches Ed25519 primitives, so it must keep working even when
    # `cryptography` is reported unavailable -- the guard must be scoped to the signed path only.
    result = evidence_signing.verify_receipt(unsigned)
    assert result["valid"] is True


# ---------------------------------------------------------------------------
# 12. verify DoS guard: an oversized receipt file is rejected before json.loads
# ---------------------------------------------------------------------------


def test_read_receipt_file_rejects_oversized_file(tmp_path: Path) -> None:
    huge_path = tmp_path / "huge.json"
    padding = "x" * (evidence_signing._MAX_RECEIPT_FILE_BYTES + 1024)
    huge_path.write_text('{"padding": "' + padding + '"}', encoding="utf-8")

    with pytest.raises(evidence_signing.EvidenceSigningError, match="exceeds"):
        evidence_signing.read_receipt_file(huge_path)


def test_read_receipt_file_accepts_a_normal_sized_receipt(tmp_path: Path) -> None:
    small_path = tmp_path / "small.json"
    small_path.write_text(json.dumps(_sample_receipt()), encoding="utf-8")

    payload = evidence_signing.read_receipt_file(small_path)

    assert payload["kind"] == "evidence-receipt"


def test_read_receipt_file_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(evidence_signing.EvidenceSigningError, match="not found"):
        evidence_signing.read_receipt_file(tmp_path / "does_not_exist.json")


def test_previous_receipt_digest_rejects_oversized_file(tmp_path: Path) -> None:
    """The `--previous` chain reader (reachable from BOTH emit and verify) must be DoS-bounded
    exactly like the primary receipt read -- never an unbounded `read_bytes()` that a huge file can
    OOM. Uses a small explicit cap so the test stays fast."""
    huge_path = tmp_path / "huge_previous.json"
    huge_path.write_text('{"receipt_sha256": "' + ("a" * 4096) + '"}', encoding="utf-8")

    with pytest.raises(evidence_signing.EvidenceSigningError, match="exceeds"):
        evidence_signing.previous_receipt_digest(huge_path, max_bytes=1024)


def test_previous_receipt_digest_accepts_normal_file_and_prefers_stored_digest(
    tmp_path: Path,
) -> None:
    previous = _sample_receipt()
    previous["receipt_sha256"] = evidence_signing.receipt_digest(previous)
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")

    assert (
        evidence_signing.previous_receipt_digest(previous_path, max_bytes=1024)
        == previous["receipt_sha256"]
    )


# ---------------------------------------------------------------------------
# 13. real-binary dogfood e2e: bootstrap.main_entry() (the pyproject.toml entry point), NOT
# typer.testing.CliRunner -- CliRunner invokes the Typer `app` object directly and bypasses
# bootstrap.py's pre-Typer routing entirely (AGENTS.md "Dogfood the Real Binary, Not CliRunner").
# ---------------------------------------------------------------------------


def _run_main_entry(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()
    code = excinfo.value.code
    captured = capsys.readouterr()
    return (int(code) if isinstance(code, int) else 0), captured.out


def test_real_binary_dogfood_keygen_emit_sign_verify_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key_path = tmp_path / "keys" / "evidence_ed25519.key"
    receipt_path = tmp_path / "receipt.json"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setenv("TG_EVIDENCE_SIGNING_KEY", str(key_path))
    # never let a stray real ~/.tensor-grep/keys/ leak into this test via TG_EVIDENCE_TRUSTED_KEYS
    monkeypatch.delenv("TG_EVIDENCE_TRUSTED_KEYS", raising=False)

    keygen_code, keygen_out = _run_main_entry(
        ["tg", "evidence", "keygen", "--json"], monkeypatch, capsys
    )
    assert keygen_code == 0, keygen_out
    keygen_payload = json.loads(keygen_out)
    assert keygen_payload["key_id"].startswith("sha256:")
    assert key_path.exists()

    emit_code, emit_out = _run_main_entry(
        [
            "tg",
            "evidence",
            "emit",
            str(repo_dir),
            "--sign",
            "--out",
            str(receipt_path),
            "--json",
        ],
        monkeypatch,
        capsys,
    )
    assert emit_code == 0, emit_out
    assert receipt_path.exists()
    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_payload["signing"]["algorithm"] == "ed25519"
    assert receipt_payload["signing"]["key_id"] == keygen_payload["key_id"]

    verify_code, verify_out = _run_main_entry(
        ["tg", "evidence", "verify", str(receipt_path), "--json"], monkeypatch, capsys
    )
    assert verify_code == 0, verify_out
    verify_payload = json.loads(verify_out)
    assert verify_payload["valid"] is True
    assert verify_payload["checks"]["signature_valid"] is True
    assert verify_payload["checks"]["digest_valid"] is True

    # ASCII-only output across all three commands (Windows cp1252 crash guard, house style).
    for output in (keygen_out, emit_out, verify_out):
        output.encode("ascii")


def test_real_binary_dogfood_tampered_receipt_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key_path = tmp_path / "keys" / "evidence_ed25519.key"
    receipt_path = tmp_path / "receipt.json"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setenv("TG_EVIDENCE_SIGNING_KEY", str(key_path))

    _run_main_entry(["tg", "evidence", "keygen"], monkeypatch, capsys)
    _run_main_entry(
        ["tg", "evidence", "emit", str(repo_dir), "--sign", "--out", str(receipt_path)],
        monkeypatch,
        capsys,
    )

    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["revision"]["commit_sha"] = "tampered"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    verify_code, verify_out = _run_main_entry(
        ["tg", "evidence", "verify", str(receipt_path)], monkeypatch, capsys
    )

    assert verify_code == 1, verify_out


# ---------------------------------------------------------------------------
# 14. Opus-gate FIX-FIRST follow-ups: (a) the `--previous` file read is DoS-bounded on BOTH the
# emit and verify CLI paths, and (b) a visible stderr warning fires when a trusted key is supplied
# without --require-trusted (the un-enforced-trust footgun). Real front door, not CliRunner.
# ---------------------------------------------------------------------------


def _run_main_entry_full(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        bootstrap.main_entry()
    code = excinfo.value.code
    captured = capsys.readouterr()
    return (int(code) if isinstance(code, int) else 0), captured.out, captured.err


def _oversized_receipt_file(path: Path) -> Path:
    # Just over the wired 5 MB default cap, so this exercises the REAL default bound (not an
    # explicit small max_bytes) end-to-end through the CLI.
    padding = "x" * (evidence_signing._MAX_RECEIPT_FILE_BYTES + 4096)
    path.write_text('{"padding": "' + padding + '"}', encoding="utf-8")
    return path


def test_cli_verify_previous_oversized_fails_closed_not_oom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt = _sample_receipt(previous_receipt_sha256="a" * 64)
    receipt["receipt_sha256"] = evidence_signing.receipt_digest(receipt)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    huge_previous = _oversized_receipt_file(tmp_path / "huge_previous.json")

    code, out, err = _run_main_entry_full(
        ["tg", "evidence", "verify", str(receipt_path), "--previous", str(huge_previous)],
        monkeypatch,
        capsys,
    )

    # Fails closed (exit 1) with a bounded "exceeds" reason -- never an OOM. On verify the oversized
    # --previous is a soft chain failure (surfaced as chain_error on stdout); on emit it raises to
    # stderr. Check the combined output so the assertion is stream-agnostic.
    assert code == 1
    assert "exceeds" in (out + err)


def test_cli_emit_previous_oversized_fails_closed_not_oom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    out_path = tmp_path / "receipt.json"
    huge_previous = _oversized_receipt_file(tmp_path / "huge_previous.json")

    code, out, err = _run_main_entry_full(
        [
            "tg",
            "evidence",
            "emit",
            str(repo_dir),
            "--previous",
            str(huge_previous),
            "--out",
            str(out_path),
        ],
        monkeypatch,
        capsys,
    )

    assert code == 1
    assert "exceeds" in (out + err)
    assert not out_path.exists(), "a bounded-read failure must not leave a receipt on disk"


def _keygen_emit_signed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[Path, str]:
    key_path = tmp_path / "keys" / "evidence_ed25519.key"
    receipt_path = tmp_path / "receipt.json"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setenv("TG_EVIDENCE_SIGNING_KEY", str(key_path))
    monkeypatch.delenv("TG_EVIDENCE_TRUSTED_KEYS", raising=False)

    _code, keygen_out, _err = _run_main_entry_full(
        ["tg", "evidence", "keygen", "--json"], monkeypatch, capsys
    )
    public_key = json.loads(keygen_out)["public_key"]
    _run_main_entry_full(
        ["tg", "evidence", "emit", str(repo_dir), "--sign", "--out", str(receipt_path)],
        monkeypatch,
        capsys,
    )
    return receipt_path, public_key


def test_cli_verify_warns_when_trusted_key_without_require_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt_path, public_key = _keygen_emit_signed_receipt(tmp_path, monkeypatch, capsys)

    code, _out, err = _run_main_entry_full(
        ["tg", "evidence", "verify", str(receipt_path), "--trusted-key", public_key],
        monkeypatch,
        capsys,
    )

    # Behavior unchanged: the correctly-signed, correctly-pinned receipt is still valid (exit 0)...
    assert code == 0
    # ... but the un-enforced-trust footgun warning MUST fire, visibly, on stderr, ASCII-only.
    assert "warning:" in err
    assert "--require-trusted" in err
    err.encode("ascii")


def test_cli_verify_no_warning_when_require_trusted_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt_path, public_key = _keygen_emit_signed_receipt(tmp_path, monkeypatch, capsys)

    code, _out, err = _run_main_entry_full(
        [
            "tg",
            "evidence",
            "verify",
            str(receipt_path),
            "--trusted-key",
            public_key,
            "--require-trusted",
        ],
        monkeypatch,
        capsys,
    )

    assert code == 0  # correct key is pinned AND enforced -> valid
    assert "warning:" not in err


def test_cli_verify_no_warning_when_no_trusted_key_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt_path, _public_key = _keygen_emit_signed_receipt(tmp_path, monkeypatch, capsys)

    code, _out, err = _run_main_entry_full(
        ["tg", "evidence", "verify", str(receipt_path)], monkeypatch, capsys
    )

    assert code == 0
    assert "warning:" not in err
