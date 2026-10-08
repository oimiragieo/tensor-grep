"""Private HTTP freshness worker; its parent owns the absolute process deadline."""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

_MAX_INDEX_BYTES = 4 * 1024 * 1024
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc|dev|post)[0-9]+)?")


def probe_indices(
    deadline: float,
    urls: Sequence[str],
    headers: dict[str, str],
    emit: Callable[[dict[str, Any]], None],
) -> None:
    from tensor_grep.cli.native_frontdoor import (
        _candidate_versions_from_pypi_json,
        _candidate_versions_from_pypi_simple_index,
        _highest_tensor_grep_version,
    )

    for stage, url in zip(("json-index", "simple-index"), urls, strict=True):
        started = time.monotonic()
        remaining = deadline - started
        if remaining <= 0:
            break
        event: dict[str, Any] = {"stage": stage, "version": None}
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=remaining) as response:
                # The process deadline covers DNS, headers and trickling reads;
                # this separate cap bounds memory even with an eager response.
                raw = response.read(_MAX_INDEX_BYTES + 1)
            if len(raw) > _MAX_INDEX_BYTES:
                raise ValueError("PyPI index response exceeds the body limit")
            versions = (
                _candidate_versions_from_pypi_json(json.loads(raw.decode("utf-8")))
                if stage == "json-index"
                else _candidate_versions_from_pypi_simple_index(
                    raw.decode("utf-8", errors="replace")
                )
            )
            # Emit only the best version at this surface, keeping the pipe
            # protocol bounded independently of the number of releases.
            event["version"] = _highest_tensor_grep_version([
                v for v in versions if len(v) <= 64 and _VERSION.fullmatch(v)
            ])
        except Exception as exc:
            event["error"] = type(exc).__name__
        event["elapsed"] = time.monotonic() - started
        emit(event)


def main() -> None:
    request = json.loads(sys.argv[1])
    deadline = time.monotonic() + float(request["timeout_seconds"])
    probe_indices(
        deadline,
        request["urls"],
        request["headers"],
        lambda event: print(json.dumps(event), flush=True),
    )


if __name__ == "__main__":
    main()
