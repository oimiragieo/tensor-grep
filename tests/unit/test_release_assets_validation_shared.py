"""Shared helpers for tests/unit/test_release_assets_validation_*.py siblings."""

import re


def _detag(content: str) -> str:
    """Restore SHA-pinned action refs (``@<sha> # vX``) to their logical tag (``@vX``) so
    tests that assert or string-manipulate version strings keep working against the
    SHA-pinned workflow files (supply-chain hardening)."""
    return re.sub(r"@[0-9a-f]{40} # (\S+)", r"@\1", content)
