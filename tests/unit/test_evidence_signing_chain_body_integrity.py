"""The receipt chain must bind the previous receipt's BODY, not just its stored digest field.

`verify_receipt_chain` mirrors `audit_manifest`'s chain check: it compared `previous_receipt_sha256`
against the previous receipt's STORED `receipt_sha256` without recomputing it from the body, so editing
the previous receipt while leaving its digest field intact still verified.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tensor_grep.cli import evidence_signing


def _receipt(note: str) -> dict:
    receipt = {"schema": "tg-evidence-receipt", "created_at": "2026-07-12T00:00:00Z", "note": note}
    receipt["receipt_sha256"] = evidence_signing.receipt_digest(receipt)
    return receipt


def _current_linking_to(digest: str) -> dict:
    return {"schema": "tg-evidence-receipt", "previous_receipt_sha256": digest}


def test_an_intact_previous_receipt_verifies(tmp_path: Path) -> None:
    previous = _receipt("ORIGINAL")
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")

    chain = evidence_signing.verify_receipt_chain(
        _current_linking_to(previous["receipt_sha256"]), previous_path=previous_path
    )

    assert chain == {"chain_valid": True, "chain_error": None}


def test_a_previous_receipt_whose_body_was_edited_fails_the_chain(tmp_path: Path) -> None:
    previous = _receipt("ORIGINAL")
    tampered = dict(previous)
    tampered["note"] = "TAMPERED -- body edited, receipt_sha256 field left as is"
    assert evidence_signing.receipt_digest(tampered) != tampered["receipt_sha256"]
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(tampered), encoding="utf-8")

    chain = evidence_signing.verify_receipt_chain(
        _current_linking_to(previous["receipt_sha256"]), previous_path=previous_path
    )

    assert chain["chain_valid"] is False
    assert "body" in str(chain["chain_error"]).lower()


def test_a_previous_receipt_that_predates_the_digest_field_still_links_by_raw_bytes(
    tmp_path: Path,
) -> None:
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(
        json.dumps({"schema": "tg-evidence-receipt", "note": "pre-P2"}), "utf-8"
    )
    link = evidence_signing.previous_receipt_digest(previous_path)

    chain = evidence_signing.verify_receipt_chain(
        _current_linking_to(link), previous_path=previous_path
    )

    assert chain == {"chain_valid": True, "chain_error": None}


def test_a_non_json_previous_receipt_still_links_by_raw_bytes(tmp_path: Path) -> None:
    previous_path = tmp_path / "previous.json"
    previous_path.write_text("not json at all", encoding="utf-8")
    link = evidence_signing.previous_receipt_digest(previous_path)

    chain = evidence_signing.verify_receipt_chain(
        _current_linking_to(link), previous_path=previous_path
    )

    assert chain == {"chain_valid": True, "chain_error": None}


def test_a_body_that_cannot_be_recanonicalised_fails_the_chain_without_crashing(
    tmp_path: Path, monkeypatch
) -> None:
    previous = _receipt("ORIGINAL")
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")
    real = evidence_signing.receipt_digest

    def flaky(receipt: dict) -> str:
        if receipt.get("note") == "ORIGINAL":
            raise RecursionError("maximum recursion depth exceeded while encoding")
        return real(receipt)

    monkeypatch.setattr(evidence_signing, "receipt_digest", flaky)

    chain = evidence_signing.verify_receipt_chain(
        _current_linking_to(previous["receipt_sha256"]), previous_path=previous_path
    )

    assert chain["chain_valid"] is False


def test_a_non_ascii_stored_digest_in_the_previous_receipt_fails_closed_without_crashing(
    tmp_path: Path,
) -> None:
    # Sol gate R1 (HIGH, live): hmac.compare_digest raises TypeError on a non-ASCII str, so a hostile
    # previous receipt carrying {"receipt_sha256": "é"} crashed the verifier.
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps({"receipt_sha256": "é"}), encoding="utf-8")

    chain = evidence_signing.verify_receipt_chain(
        _current_linking_to("0"), previous_path=previous_path
    )

    assert chain["chain_valid"] is False


def test_a_non_ascii_claimed_link_fails_closed_without_crashing(tmp_path: Path) -> None:
    # The CURRENT receipt's own previous_receipt_sha256 is attacker-controlled too.
    previous = _receipt("ORIGINAL")
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")

    chain = evidence_signing.verify_receipt_chain(
        _current_linking_to("é" * 64), previous_path=previous_path
    )

    assert chain["chain_valid"] is False


def test_a_lone_surrogate_digest_fails_closed_without_crashing(tmp_path: Path) -> None:
    previous_path = tmp_path / "previous.json"
    # Raw string: the file holds the JSON ESCAPE text, which json.loads turns into a lone surrogate.
    previous_path.write_text(r'{"receipt_sha256": "\ud800"}', encoding="utf-8")

    chain = evidence_signing.verify_receipt_chain(
        _current_linking_to("\ud800"), previous_path=previous_path
    )

    assert chain["chain_valid"] is False


def test_emit_refuses_to_chain_onto_a_previous_receipt_whose_body_was_edited(
    tmp_path: Path,
) -> None:
    # Creation must not mint a link to a record already known to be corrupt: the resulting chain
    # would fail verification later and would have lied in the meantime.
    tampered = _receipt("ORIGINAL")
    tampered["note"] = "TAMPERED"
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(evidence_signing.EvidenceSigningError, match="body"):
        evidence_signing.previous_receipt_digest(previous_path)


def test_emit_still_chains_onto_an_intact_previous_receipt(tmp_path: Path) -> None:
    previous = _receipt("ORIGINAL")
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")

    assert evidence_signing.previous_receipt_digest(previous_path) == previous["receipt_sha256"]


def test_verify_receipt_with_a_non_ascii_stored_digest_fails_without_crashing() -> None:
    # `verify_receipt` is the main receipt verification; its own digest check also called
    # hmac.compare_digest on the attacker-controlled stored digest.
    result = evidence_signing.verify_receipt({
        "schema": "tg-evidence-receipt",
        "receipt_sha256": "é",
    })

    assert result["valid"] is False


def test_digests_equal_never_raises_on_non_ascii_or_surrogates() -> None:
    assert evidence_signing._digests_equal("é", "é") is True
    assert evidence_signing._digests_equal("é", "e") is False
    assert evidence_signing._digests_equal("\ud800", "abc") is False
    assert evidence_signing._digests_equal("abc", "abc") is True


def test_verify_receipt_with_a_nan_body_fails_closed_without_crashing() -> None:
    # Sol gate R3 (HIGH, live, pre-existing): canonical_receipt_bytes uses allow_nan=False, but
    # json.loads accepts the text `NaN`, so a hostile receipt file made verify_receipt raise
    # ValueError before it could report an invalid digest -- breaking its never-raises contract.
    receipt = json.loads('{"schema": "tg-evidence-receipt", "note": NaN, "receipt_sha256": "0"}')

    result = evidence_signing.verify_receipt(receipt)

    assert result["valid"] is False


def test_verify_receipt_with_a_nan_body_and_a_signature_block_fails_closed() -> None:
    receipt = json.loads(
        '{"schema": "tg-evidence-receipt", "note": NaN, "receipt_sha256": "0",'
        ' "signing": {"algorithm": "ALGORITHM", "public_key": "AAAA"},'
        ' "signature": {"value": "AAAA"}}'.replace("ALGORITHM", evidence_signing.ALGORITHM)
    )

    result = evidence_signing.verify_receipt(receipt)

    assert result["valid"] is False
