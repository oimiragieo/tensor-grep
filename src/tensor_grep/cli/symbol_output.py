"""Symbol-command text rendering and result-shape predicates (kept out of main.py: size ratchet)."""

from __future__ import annotations

from typing import Any


def defined_without_results(payload: dict[str, Any], result_key: str) -> bool:
    """True for a resolved symbol whose callers/references list is empty (complete, not absent)."""
    return (
        result_key in {"callers", "references"}
        and bool(payload.get("definitions"))
        and not payload.get(result_key)
        and not payload.get("no_match")
    )


def source_text_lines(payload: dict[str, Any]) -> list[str]:
    lines = [
        f"Source for {payload['symbol']} in {payload['path']}",
        f"sources={len(payload['sources'])} files={len(payload['files'])}",
    ]
    for block in payload["sources"]:
        lines.append(
            f"{block.get('file')}:{block.get('start_line', '?')}-{block.get('end_line', '?')}"
        )
        lines.append(str(block.get("source", "")).rstrip("\n"))
    return lines
