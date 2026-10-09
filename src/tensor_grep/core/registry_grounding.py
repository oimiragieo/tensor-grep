"""Explicit registry metadata, fetched in a bounded isolated child process."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

MAX_RESPONSE_BYTES = 256 * 1024
MAX_REQUESTS = 4
_HOSTS = {"pypi": "pypi.org", "npm": "registry.npmjs.org", "crates": "crates.io"}
# Fixed code, -I isolation, no caller-controlled Python or URL authority, and no redirects.
_FETCH = """
import json, sys, urllib.request, urllib.parse
url, timeout, cap = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
parts = urllib.parse.urlsplit(url)
if parts.scheme != 'https' or parts.hostname not in {'pypi.org','registry.npmjs.org','crates.io'} or parts.port or parts.username:
    raise ValueError('registry authority refused')
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('registry redirect refused')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
request = urllib.request.Request(url, headers={'User-Agent':'tensor-grep-grounding','Accept':'application/json'})
with opener.open(request, timeout=timeout) as response:
    length = response.headers.get('Content-Length')
    if length and int(length) > cap:
        raise ValueError('registry response too large')
    data = response.read(cap + 1)
if len(data) > cap:
    raise ValueError('registry response too large')
value = json.loads(data.decode('utf-8'))
if parts.hostname == 'pypi.org':
    info = value.get('info', {})
    result = {'name':info.get('name'), 'version':info.get('version'), 'requires_python':info.get('requires_python')}
elif parts.hostname == 'registry.npmjs.org':
    result = {'name':value.get('name'), 'version':value.get('version'), 'engines':value.get('engines')}
else:
    info = value.get('crate', {})
    result = {'name':info.get('name'), 'version':info.get('max_version')}
print(json.dumps(result))
"""


def _url(ecosystem: str, name: str) -> str:
    if ecosystem not in _HOSTS or not re.fullmatch(
        r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9][A-Za-z0-9_.-]*", name
    ):
        raise ValueError("registry package identifier refused")
    encoded = quote(name, safe="")
    if ecosystem == "pypi":
        return f"https://pypi.org/pypi/{encoded}/json"
    if ecosystem == "npm":
        return f"https://registry.npmjs.org/{encoded}/latest"
    return f"https://crates.io/api/v1/crates/{encoded}"


def _cache_root() -> Path:
    return (
        Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
        / "tensor-grep"
        / "registry-grounding-v1"
    )


def _ensure_cache_directory(root: Path) -> None:
    """Create the named cache path while pinning verified nonlinked parents."""
    from tensor_grep.io.confined import (
        _pin_windows_parents,
        _real_components,
        _verify_parents,
    )

    anchor = Path(root.anchor)
    current = anchor
    for component in root.relative_to(anchor).parts:
        child = current / component
        parents = _real_components(anchor, child)
        with _pin_windows_parents(parents):
            if not os.path.lexists(child):
                if os.name == "nt":
                    child.mkdir()
                else:
                    descriptor = os.open(
                        current,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    )
                    try:
                        _verify_parents(parents)
                        os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    finally:
                        os.close(descriptor)
            _real_components(anchor, child / ".receipt")
        current = child


def _metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {
        "name",
        "version",
        "requires_python",
        "engines",
    }:
        raise ValueError("registry metadata shape refused")
    for key, item in value.items():
        if key == "engines" and isinstance(item, dict):
            if len(item) > 32 or any(
                not isinstance(name, str)
                or not isinstance(spec, str)
                or len(name) > 256
                or len(spec) > 256
                for name, spec in item.items()
            ):
                raise ValueError("registry engines metadata refused")
        elif item is not None and (not isinstance(item, str) or len(item) > 1024):
            raise ValueError("registry metadata value refused")
    return value


def registry_receipts(
    dependencies: list[dict[str, Any]], *, deadline_monotonic: float | None = None
) -> dict[str, Any]:
    deadline = min(deadline_monotonic or float("inf"), time.monotonic() + 5.0)
    receipts: list[dict[str, Any]] = []
    root = _cache_root()
    for row in dependencies[:MAX_REQUESTS]:
        if time.monotonic() >= deadline:
            break
        receipt: dict[str, Any] = {
            "ecosystem": row["ecosystem"],
            "name": row["name"],
            "api_compatibility_verified": False,
        }
        try:
            url = _url(row["ecosystem"], row["name"])
            receipt["url"] = url
            key = hashlib.sha256(url.encode()).hexdigest()
            cached_path = root / f"{key}.json"
            # Cache is advisory and caller-writable: validate shape and exact origin; never use
            # cached metadata as executable instructions or API compatibility evidence.
            try:
                from tensor_grep.io.confined import read_confined

                cached = json.loads(read_confined(Path(root.anchor), cached_path, 8192))
                fetched = datetime.fromisoformat(cached["fetched_at"])
                if fetched.tzinfo is None:
                    raise ValueError("registry receipt lacks timezone")
                metadata = _metadata(cached["metadata"])
                age = time.time() - fetched.timestamp()
                if (
                    cached.get("url") == url
                    and 0 <= age < 86400
                    and isinstance(cached.get("metadata"), dict)
                ):
                    receipts.append({
                        **receipt,
                        "metadata": metadata,
                        "fetched_at": cached["fetched_at"],
                        "status": "cached",
                        "provenance": "caller-writable-registry-cache",
                        "api_compatibility_verified": False,
                    })
                    continue
            except (OSError, ValueError, KeyError, TypeError, RecursionError):
                pass
            remaining = max(0.001, deadline - time.monotonic())
            completed = subprocess.run(
                [sys.executable, "-I", "-c", _FETCH, url, str(remaining), str(MAX_RESPONSE_BYTES)],
                capture_output=True,
                timeout=remaining,
                check=False,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
                },
            )
            if completed.returncode or len(completed.stdout) > 8192:
                raise ValueError("registry request failed or response refused")
            metadata = _metadata(json.loads(completed.stdout.decode("utf-8")))
            receipt.update(
                status="fetched",
                fetched_at=datetime.now(UTC).isoformat(),
                metadata=metadata,
                response_sha256=hashlib.sha256(completed.stdout).hexdigest(),
            )
            try:
                from tensor_grep.io.confined import publish_confined

                _ensure_cache_directory(root)
                publish_confined(
                    Path(root.anchor), cached_path, json.dumps(receipt).encode("utf-8")
                )
            except (OSError, ValueError):
                receipt["cache_status"] = "not_saved"
        except subprocess.TimeoutExpired:
            receipt.update(status="unavailable", reason="shared_deadline")
        except (OSError, ValueError, RecursionError) as exc:
            receipt.update(status="unavailable", reason=str(exc))
        receipts.append(receipt)
    return {
        "receipts": receipts,
        "omitted_requests": max(0, len(dependencies) - len(receipts)),
        "max_requests": MAX_REQUESTS,
        "metadata_does_not_prove_api_compatibility": True,
    }
