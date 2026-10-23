"""Human-readable review-bundle verification diagnostics."""

from __future__ import annotations

import json
from typing import Any, cast

import typer


def _echo_terminal_line(message: str) -> None:
    safe_message = "".join(
        json.dumps(character, ensure_ascii=True)[1:-1]
        if (
            ord(character) < 32
            or 127 <= ord(character) <= 159
            or 0xD800 <= ord(character) <= 0xDFFF
        )
        else character
        for character in message
    )
    typer.echo(safe_message)


def render_review_bundle_verification(payload: dict[str, Any]) -> None:
    """Render existing verification results without changing acceptance policy."""
    _echo_terminal_line(f"Review bundle: {payload['bundle_path']}")
    _echo_terminal_line(f"valid={payload['valid']}")
    _echo_terminal_line(f"manifest_signature_valid={payload['manifest_signature_valid']}")
    if payload.get("manifest_signature_error"):
        error = json.dumps(payload["manifest_signature_error"], ensure_ascii=True)
        _echo_terminal_line(f"manifest_signature_error: {error}")
    for component, check in cast(dict[str, dict[str, object]], payload["checks"]).items():
        _echo_terminal_line(
            f"{component}: valid={check['valid']} "
            f"expected={check['expected']} actual={check['actual']}"
        )
    bundle_integrity = cast(dict[str, object], payload["bundle_integrity"])
    _echo_terminal_line(
        "bundle_integrity="
        f"{bundle_integrity['valid']} "
        f"expected={bundle_integrity['expected']} actual={bundle_integrity['actual']}"
    )
    against_check = payload.get("against")
    if isinstance(against_check, dict):
        _echo_terminal_line(
            f"against={against_check['ref']} valid={against_check['valid']} "
            f"resolved_commit_sha={against_check['resolved_commit_sha']}"
        )
        if against_check.get("error"):
            _echo_terminal_line(f"  against_error: {against_check['error']}")
    for receipt_check in cast(list[dict[str, object]], payload.get("receipts") or []):
        _echo_terminal_line(
            f"receipt[{receipt_check['index']}]: valid={receipt_check['valid']} "
            f"receipt_sha256={receipt_check.get('receipt_sha256')}"
        )
        freshness = receipt_check.get("freshness")
        if isinstance(freshness, dict) and freshness.get("error"):
            _echo_terminal_line(f"  freshness_error: {freshness['error']}")
        signature = receipt_check.get("signature")
        if isinstance(signature, dict) and signature.get("errors"):
            for signature_error in cast(list[object], signature["errors"]):
                _echo_terminal_line(f"  signature_error: {signature_error}")
    policy = payload.get("policy")
    if isinstance(policy, dict):
        _echo_terminal_line(
            f"policy: min_receipts={policy['min_receipts']} "
            f"valid_receipt_count={policy['valid_receipt_count']} "
            f"expect_key_ids={policy['expect_key_ids']} valid={policy['valid']}"
        )
        for policy_reason in cast(list[object], policy.get("reasons") or []):
            _echo_terminal_line(f"  policy_reason: {policy_reason}")
