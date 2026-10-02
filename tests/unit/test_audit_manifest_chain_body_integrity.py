"""The manifest chain must bind the previous manifest's BODY, not just its stored digest field.

`verify_audit_manifest(current, previous_manifest=prev)` used to compare `current`'s
`previous_manifest_sha256` against `prev`'s STORED `manifest_sha256` field without recomputing it from
`prev`'s contents. Editing `prev`'s body while leaving its digest field intact therefore still verified
(`valid: true`, no errors). A chain that only checks a claimed digest is not tamper-evident for the
record it links to.
"""

from __future__ import annotations

import json
from pathlib import Path

from tensor_grep.cli import audit_manifest as am


def _manifest(body: dict, previous_sha: str | None = None) -> dict:
    manifest = dict(body)
    if previous_sha is not None:
        manifest["previous_manifest_sha256"] = previous_sha
    manifest["manifest_sha256"] = am._sha256_hex(am._canonical_manifest_bytes(manifest))
    return manifest


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _verify(tmp_path: Path) -> dict:
    return am.verify_audit_manifest(tmp_path / "cur.json", previous_manifest=tmp_path / "prev.json")


def _chained_pair(tmp_path: Path) -> dict:
    previous = _manifest({"schema": "t", "created_at": "2026-01-01T00:00:00Z", "note": "ORIGINAL"})
    current = _manifest(
        {"schema": "t", "created_at": "2026-01-02T00:00:00Z"}, previous["manifest_sha256"]
    )
    _write(tmp_path / "prev.json", previous)
    _write(tmp_path / "cur.json", current)
    return previous


def test_an_intact_previous_manifest_verifies(tmp_path: Path) -> None:
    _chained_pair(tmp_path)

    result = _verify(tmp_path)

    assert result["checks"]["chain_valid"] is True
    assert result["valid"] is True
    assert result["errors"] == []


def test_a_previous_manifest_whose_body_was_edited_fails_the_chain(tmp_path: Path) -> None:
    previous = _chained_pair(tmp_path)
    tampered = dict(previous)
    tampered["note"] = "TAMPERED -- body edited, manifest_sha256 field left as is"
    _write(tmp_path / "prev.json", tampered)
    assert am._sha256_hex(am._canonical_manifest_bytes(tampered)) != tampered["manifest_sha256"]

    result = _verify(tmp_path)

    assert result["checks"]["chain_valid"] is False
    assert result["valid"] is False
    assert any(
        "previous manifest" in error.lower() and "body" in error.lower()
        for error in result["errors"]
    )


def test_a_previous_manifest_that_predates_the_digest_field_still_links_by_raw_bytes(
    tmp_path: Path,
) -> None:
    # Compatibility: no stored digest -> nothing to cross-check; the link is the raw-bytes hash.
    prev_path = tmp_path / "prev.json"
    prev_path.write_text(json.dumps({"schema": "t", "note": "legacy"}), encoding="utf-8")
    raw_digest = am._sha256_hex(prev_path.read_bytes())
    current = _manifest({"schema": "t", "created_at": "2026-01-02T00:00:00Z"}, raw_digest)
    _write(tmp_path / "cur.json", current)

    result = _verify(tmp_path)

    assert result["checks"]["chain_valid"] is True
    assert result["valid"] is True


def test_a_non_json_previous_manifest_still_links_by_raw_bytes(tmp_path: Path) -> None:
    prev_path = tmp_path / "prev.json"
    prev_path.write_text("not json at all", encoding="utf-8")
    raw_digest = am._sha256_hex(prev_path.read_bytes())
    current = _manifest({"schema": "t", "created_at": "2026-01-02T00:00:00Z"}, raw_digest)
    _write(tmp_path / "cur.json", current)

    result = _verify(tmp_path)

    assert result["checks"]["chain_valid"] is True


def test_a_body_that_cannot_be_recanonicalised_fails_the_chain_without_crashing(
    tmp_path: Path, monkeypatch
) -> None:
    # A previous manifest that parses but cannot be re-serialised (e.g. nesting deep enough for
    # RecursionError on dump) must fail closed -- "not intact" -- never crash the verifier.
    _chained_pair(tmp_path)
    real = am._canonical_manifest_bytes
    calls = {"n": 0}

    def flaky(manifest: dict) -> bytes:
        calls["n"] += 1
        if manifest.get("note") == "ORIGINAL":  # only the previous manifest, not the current one
            raise RecursionError("maximum recursion depth exceeded while encoding")
        return real(manifest)

    monkeypatch.setattr(am, "_canonical_manifest_bytes", flaky)

    result = _verify(tmp_path)

    assert result["checks"]["chain_valid"] is False
    assert result["valid"] is False


def test_a_non_ascii_signature_value_fails_closed_without_crashing(tmp_path: Path) -> None:
    # Sol gate R2 (HIGH, live, pre-existing): `signature` is excluded from the digest, so a manifest
    # can carry {"signature": {"value": "é"}} and stay digest-valid; hmac.compare_digest then raised
    # TypeError on the non-ASCII str and crashed the verifier whenever a signing key was supplied.
    key = tmp_path / "key"
    key.write_bytes(b"top-secret")
    manifest = _manifest({"schema": "t", "created_at": "2026-01-02T00:00:00Z"})
    manifest["signature"] = {"kind": "hmac-sha256", "key_path": str(key), "value": "é"}
    (tmp_path / "cur.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = am.verify_audit_manifest(tmp_path / "cur.json", signing_key=key)

    assert result["checks"]["signature_valid"] is False
    assert result["valid"] is False
