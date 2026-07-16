"""Confirms an inbound callback originated from a trusted partner."""

import hmac


def validate_hmac_signature(raw_body, provided_tag, shared_secret):
    expected = hmac.new(shared_secret, raw_body, "sha256").hexdigest()
    return hmac.compare_digest(expected, provided_tag)
