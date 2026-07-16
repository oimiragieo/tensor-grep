"""Names a static bundle so browsers cache it correctly."""

import hashlib


def fingerprint_static_bundle(bundle_bytes):
    return hashlib.sha1(bundle_bytes).hexdigest()[:10]
