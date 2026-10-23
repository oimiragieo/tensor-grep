"""Safe review-bundle text and unchanged real verification behavior."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import audit_manifest, evidence_signing
from tensor_grep.cli import review_bundle_render as renderer
from tensor_grep.cli.main import app

_SAFE_TRAILER = (
    "valid=True\n"
    "manifest_signature_valid=True\n"
    "core: valid=True expected=sha256:✓ actual=sha256:✓\n"
    "bundle_integrity=True expected=sha256:✓ actual=sha256:✓\n"
    "against=refs/heads/main valid=True resolved_commit_sha=abcdef\n"
    "receipt[0]: valid=True receipt_sha256=receipt:✓\n"
    "policy: min_receipts=1 valid_receipt_count=1 expect_key_ids=['key-1'] valid=True\n"
)


def _base_payload() -> dict[str, Any]:
    return {
        "bundle_path": "bundle.json",
        "valid": True,
        "manifest_signature_valid": True,
        "checks": {
            "core": {
                "valid": True,
                "expected": "sha256:✓",
                "actual": "sha256:✓",
            }
        },
        "bundle_integrity": {
            "valid": True,
            "expected": "sha256:✓",
            "actual": "sha256:✓",
        },
        "against": {
            "ref": "refs/heads/main",
            "valid": True,
            "resolved_commit_sha": "abcdef",
        },
        "receipts": [
            {
                "index": 0,
                "valid": True,
                "receipt_sha256": "receipt:✓",
            }
        ],
        "policy": {
            "min_receipts": 1,
            "valid_receipt_count": 1,
            "expect_key_ids": ["key-1"],
            "valid": True,
            "reasons": [],
        },
    }


def test_RenderReviewBundle_PrintablePayload_PreservesCompleteText(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _base_payload()
    payload["bundle_path"] = 'C:\\repo\\café "draft"\\bundle.json'
    payload["manifest_signature_error"] = 'signature "quoted" at C:\\bundle'
    payload["checks"]["core"]["expected"] = "hash:✓✨"
    payload["checks"]["core"]["actual"] = "hash:✓✨"
    payload["policy"]["expect_key_ids"] = ["key-✓"]

    renderer.render_review_bundle_verification(payload)

    expected = r"""Review bundle: C:\repo\café "draft"\bundle.json
valid=True
manifest_signature_valid=True
manifest_signature_error: "signature \"quoted\" at C:\\bundle"
core: valid=True expected=hash:✓✨ actual=hash:✓✨
bundle_integrity=True expected=sha256:✓ actual=sha256:✓
against=refs/heads/main valid=True resolved_commit_sha=abcdef
receipt[0]: valid=True receipt_sha256=receipt:✓
policy: min_receipts=1 valid_receipt_count=1 expect_key_ids=['key-✓'] valid=True
"""
    assert capsys.readouterr().out == expected


def test_RenderReviewBundle_HostileField_EscapesTerminalControls(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _base_payload()
    payload["bundle_path"] = "bundle\x1b"
    payload["manifest_signature_error"] = "sig\x01"
    payload["checks"] = {
        "component\x85": {
            "valid": False,
            "expected": "expected\x1b",
            "actual": "actual\x7f",
        }
    }
    payload["bundle_integrity"] = {
        "valid": False,
        "expected": "digest\x9f",
        "actual": "actual\tfield",
    }
    payload["against"] = {
        "ref": "ref\x1b",
        "valid": False,
        "resolved_commit_sha": "sha\x80",
        "error": "against\x1b",
    }
    payload["receipts"] = [
        {
            "index": 0,
            "valid": False,
            "receipt_sha256": "receipt\x90",
            "freshness": {"error": "fresh\x85"},
            "signature": {"errors": ["signature\x9f"]},
        }
    ]
    payload["policy"] = {
        "min_receipts": "min\x1b",
        "valid_receipt_count": "count\x85",
        "expect_key_ids": ["key\x1b"],
        "valid": False,
        "reasons": ["reason\x9f"],
    }

    renderer.render_review_bundle_verification(payload)

    expected = r"""Review bundle: bundle\u001b
valid=True
manifest_signature_valid=True
manifest_signature_error: "sig\u0001"
component\u0085: valid=False expected=expected\u001b actual=actual\u007f
bundle_integrity=False expected=digest\u009f actual=actual\tfield
against=ref\u001b valid=False resolved_commit_sha=sha\u0080
  against_error: against\u001b
receipt[0]: valid=False receipt_sha256=receipt\u0090
  freshness_error: fresh\u0085
  signature_error: signature\u009f
policy: min_receipts=min\u001b valid_receipt_count=count\u0085 expect_key_ids=['key\x1b'] valid=False
  policy_reason: reason\u009f
"""
    assert capsys.readouterr().out == expected


@pytest.mark.parametrize(
    ("terminal_input", "expected_line"),
    [
        ("\x1b]52;c;AAAA\x07", "Review bundle: \\u001b]52;c;AAAA\\u0007"),
        ("\x1b]0;title\x1b\\", "Review bundle: \\u001b]0;title\\u001b\\"),
        (
            "\x1b]8;;javascript:alert(1)\x07link\x1b]8;;\x07",
            "Review bundle: \\u001b]8;;javascript:alert(1)\\u0007link\\u001b]8;;\\u0007",
        ),
        ("\x1b[31mRED\x1b[0m", "Review bundle: \\u001b[31mRED\\u001b[0m"),
        ("\x1bP1;payload\x1b\\", "Review bundle: \\u001bP1;payload\\u001b\\"),
        ("\x1b^pm-payload\x1b\\", "Review bundle: \\u001b^pm-payload\\u001b\\"),
        ("\x1b_apc-payload\x1b\\", "Review bundle: \\u001b_apc-payload\\u001b\\"),
        ("\x1bXignored\x1b\\", "Review bundle: \\u001bXignored\\u001b\\"),
        ("\x1b(B", "Review bundle: \\u001b(B"),
        ("\x1bc", "Review bundle: \\u001bc"),
        ("\x1b", "Review bundle: \\u001b"),
        ("\x9d52;c;AAAA\x9c", "Review bundle: \\u009d52;c;AAAA\\u009c"),
        ("\x1b[31", "Review bundle: \\u001b[31"),
        ("\x1b]52;c;AAAA", "Review bundle: \\u001b]52;c;AAAA"),
    ],
)
def test_RenderReviewBundle_TerminalSequence_DisplaysLiteralEscape(
    terminal_input: str,
    expected_line: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _base_payload()
    payload["bundle_path"] = terminal_input

    renderer.render_review_bundle_verification(payload)

    actual_line, actual_trailer = capsys.readouterr().out.split("\n", 1)

    assert actual_line == expected_line
    assert actual_trailer == _SAFE_TRAILER


@pytest.mark.parametrize(
    ("character", "expected_line"),
    [
        ("\x00", "Review bundle: prefix\\u0000suffix"),
        ("\x08", "Review bundle: prefix\\bsuffix"),
        ("\t", "Review bundle: prefix\\tsuffix"),
        ("\r", "Review bundle: prefix\\rsuffix"),
        ("\n", "Review bundle: prefix\\nsuffix"),
        ("\x1b", "Review bundle: prefix\\u001bsuffix"),
        ("\x7f", "Review bundle: prefix\\u007fsuffix"),
        ("\x80", "Review bundle: prefix\\u0080suffix"),
        ("\x9f", "Review bundle: prefix\\u009fsuffix"),
        ("\ud800", "Review bundle: prefix\\ud800suffix"),
        ("\udfff", "Review bundle: prefix\\udfffsuffix"),
    ],
)
def test_RenderReviewBundle_ControlCodePoint_EmitsSingleSafeLine(
    character: str,
    expected_line: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _base_payload()
    payload["bundle_path"] = f"prefix{character}suffix"

    renderer.render_review_bundle_verification(payload)

    actual_line, actual_trailer = capsys.readouterr().out.split("\n", 1)

    assert actual_line == expected_line
    assert actual_trailer == _SAFE_TRAILER


def test_RenderReviewBundle_QuotedManifestDiagnostic_PreservesExistingRepresentation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _base_payload()
    payload["manifest_signature_error"] = 'bad "signature" at C:\\bundle'

    renderer.render_review_bundle_verification(payload)

    expected = r"""Review bundle: bundle.json
valid=True
manifest_signature_valid=True
manifest_signature_error: "bad \"signature\" at C:\\bundle"
core: valid=True expected=sha256:✓ actual=sha256:✓
bundle_integrity=True expected=sha256:✓ actual=sha256:✓
against=refs/heads/main valid=True resolved_commit_sha=abcdef
receipt[0]: valid=True receipt_sha256=receipt:✓
policy: min_receipts=1 valid_receipt_count=1 expect_key_ids=['key-1'] valid=True
"""
    assert capsys.readouterr().out == expected


def _make_bundle(tmp_path: Path, *, hostile: bool) -> Path:
    manifest = {
        "version": 1,
        "kind": "rewrite-audit-manifest",
        "created_at": "2026-10-23T12:00:00Z",
        "lang": "python",
        "path": str(tmp_path),
        "plan_total_edits": 0,
        "applied_edit_ids": [],
        "checkpoint": None,
        "validation": None,
        "files": [],
        "previous_manifest_sha256": None,
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    ).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt = {"version": 1, "kind": "evidence-receipt", "sources": []}
    if hostile:
        receipt["signing"] = {
            "algorithm": "invalid\x1b]52;c;Y2xpcGJvYXJk\x07\x9b31m",
            "public_key": "unused",
        }
        receipt["signature"] = {"value": "unused"}
    receipt["receipt_sha256"] = evidence_signing.receipt_digest(receipt)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    bundle_path = tmp_path / "review-bundle.json"
    audit_manifest.create_review_bundle(
        manifest_path, receipt_paths=[receipt_path], output_path=bundle_path
    )
    return bundle_path


def test_VerifyReviewBundle_UnsignedReceipt_ReportsValidBundle(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path, hostile=False)

    result = CliRunner().invoke(app, ["review-bundle", "verify", str(bundle), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["valid"] is True
    assert payload["bundle_integrity"]["valid"] is True
    assert payload["receipts"][0]["signature"]["checks"]["digest_valid"] is True


def test_VerifyReviewBundle_HostileReceiptAlgorithm_EscapesTerminalDiagnostic(
    tmp_path: Path,
) -> None:
    bundle = _make_bundle(tmp_path, hostile=True)

    result = CliRunner().invoke(app, ["review-bundle", "verify", str(bundle)])

    assert result.exit_code == 1
    assert "Unsupported signing algorithm" in result.output
    assert "\x1b" not in result.output
    assert "\x07" not in result.output
    assert "\x9b" not in result.output
    assert "\\u001b]52;c;Y2xpcGJvYXJk\\u0007\\u009b31m" in result.output


def test_VerifyReviewBundle_HostileAlgorithmJson_PreservesStructuredRefusal(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path, hostile=True)

    result = CliRunner().invoke(app, ["review-bundle", "verify", str(bundle), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["valid"] is False
    assert payload["manifest_signature_valid"] is True
    assert payload["bundle_integrity"]["valid"] is True
    assert payload["receipts"][0]["signature"]["checks"]["digest_valid"] is True
    assert payload["receipts"][0]["signature"]["checks"]["signature_valid"] is False
    assert payload["receipts"][0]["signature"]["errors"] == [
        "Unsupported signing algorithm: invalid\x1b]52;c;Y2xpcGJvYXJk\x07\x9b31m"
    ]
    assert "\x1b" not in result.output
    assert "\x07" not in result.output
    assert "\x9b" not in result.output
