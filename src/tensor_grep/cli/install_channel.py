from __future__ import annotations

import json
from typing import Any


def _read_direct_url_payload() -> dict[str, Any] | None:
    try:
        from importlib.metadata import distribution

        dist = distribution("tensor-grep")
        payload = dist.read_text("direct_url.json")
    except Exception:
        return None
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def get_install_provenance() -> dict[str, str] | None:
    payload = _read_direct_url_payload()
    if payload is None:
        return None

    url = str(payload.get("url") or "")
    vcs_info = payload.get("vcs_info")
    if not isinstance(vcs_info, dict):
        return None

    requested_revision = str(vcs_info.get("requested_revision") or "").strip()
    commit_id = str(vcs_info.get("commit_id") or "").strip()
    if not url or "github.com/oimiragieo/tensor-grep" not in url:
        return None

    normalized_revision = requested_revision.removeprefix("refs/heads/")
    channel = "main" if normalized_revision == "main" else "git"
    provenance = {
        "channel": channel,
        "source": url,
    }
    if requested_revision:
        provenance["requested_revision"] = requested_revision
    if commit_id:
        provenance["commit"] = commit_id
    return provenance


def infer_install_channel() -> str:
    provenance = get_install_provenance()
    if provenance is None:
        return "stable"
    return provenance.get("channel", "stable")


def format_display_version(base_version: str) -> str:
    provenance = get_install_provenance()
    if provenance is None:
        return base_version

    channel = provenance.get("channel", "stable")
    commit = provenance.get("commit", "")
    short_commit = commit[:7] if commit else ""

    if channel == "main":
        return f"{base_version}+main.{short_commit}" if short_commit else f"{base_version}+main"
    if channel == "git":
        return f"{base_version}+git.{short_commit}" if short_commit else f"{base_version}+git"
    return base_version
