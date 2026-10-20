"""Small text-normalization helper shared by the workflow-content validators."""

from __future__ import annotations

import re


def _normalize_pinned_actions(content: str) -> str:
    """Rewrite SHA-pinned action refs (``owner/repo@<40hex> # vX``) back to their logical tag
    (``owner/repo@vX``) for VALIDATION ONLY, so the workflow-content checks keep asserting against
    the version comment rather than the (intentionally pinned) commit SHA. The raw workflow files
    stay SHA-pinned; ``validate_actions_sha_pinned`` separately enforces that they ARE pinned.
    """
    return re.sub(r"@[0-9a-f]{40} # (\S+)", r"@\1", content)


def _validate_uv_security_pins(content: str) -> list[str]:
    errors: list[str] = []
    for version in re.findall(r"""\buv==([^\s\"';&|]+)""", content):
        match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)", version)
        if match is None or tuple(map(int, match.groups())) < (0, 13, 4):
            errors.append(f"uv bootstrap requires stable uv>=0.13.4; found {version}")
    return errors
