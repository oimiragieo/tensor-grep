from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.agent_capsule_constants import (
    _CAPSULE_INLINE_CALLER_ANNOTATION_ENV as _CAPSULE_INLINE_CALLER_ANNOTATION_ENV,
)


def _capsule_inline_caller_annotation_enabled() -> bool:
    """Opt-in flag (default OFF). Same polarity as `_capsule_outbound_dependencies_enabled`, for a
    stronger reason than DAR's "pending a measured golden-set win": this feature MUTATES an
    EXISTING field's byte content (`snippets[i]["source"]`, `["line_map"]`, `["token_estimate"]`)
    rather than only adding new sibling keys, so a default-on flip would silently change output
    shape for every existing consumer/test on this repo. Enable via `TG_CAPSULE_INLINE_CALLERS` in
    {"1", "true", "yes", "on"} (case-insensitive).
    """
    raw = os.environ.get(_CAPSULE_INLINE_CALLER_ANNOTATION_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _inline_annotation_comment_prefix(file_path: str) -> str | None:
    """Line-comment token for the primary snippet's language, or None to skip annotation entirely
    on a language this renderer does not confidently recognize -- fail-closed: never guess wrong on
    comment syntax and risk corrupting the excerpt's copy-usability. Deliberately mirrors the exact
    suffix sets `repo_map._render_source_block`/`repo_map._is_comment_line` already use for
    comment-aware rendering, so "languages this feature understands" cannot drift from "languages
    the renderer already strips comments for" into a second, independently-maintained list.
    """
    suffix = Path(file_path).suffix
    if suffix == ".py":
        return "#"
    if suffix in repo_map._JS_TS_SUFFIXES or suffix in repo_map._RUST_SUFFIXES:
        return "//"
    return None


def _top_caller_symbol_names(
    rm: dict[str, Any],
    related_call_sites: list[dict[str, Any]],
    *,
    limit: int,
) -> list[str]:
    """Resolve each call site's ENCLOSING function/method name via the already-built `rm`'s
    per-file symbol table (`repo_map._enclosing_symbol_for_line` -- the same helper
    `_related_spans_from_blast_radius` already uses for the edit-plan-seed's related spans), an
    in-memory lookup against data the capsule already holds, not a new file scan. Never fabricates
    a name: a call site whose enclosing symbol cannot be resolved (a module-level call, or a
    language gap) is silently skipped rather than guessed. Deduplicated, order-preserving, capped
    at `limit` so the rendered fact stays a single short line.
    """
    names: list[str] = []
    seen: set[str] = set()
    for site in related_call_sites:
        if len(names) >= limit:
            break
        file_path = str(site.get("file") or "")
        if not file_path:
            continue
        try:
            line = int(site.get("line") or 0)
        except (TypeError, ValueError):
            continue
        if line <= 0:
            continue
        enclosing = repo_map._enclosing_symbol_for_line(rm, file_path, line)
        if not enclosing:
            continue
        name = str(enclosing.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _build_inline_caller_annotation_text(
    comment_prefix: str,
    call_site_evidence: dict[str, Any],
    top_names: list[str],
) -> str | None:
    """Compact caller/fan-in fact, ~1 line: an INVERSE-only (who-calls-me) fact -- per the
    CodeAnchor paper's own finding that forward-edge annotations add tokens without the
    reliability win, this deliberately never renders callees/imports, only callers. Returns None
    when `call_site_evidence` was never actually collected (status "disabled"/"skipped"/"error") --
    honest absence, never a fabricated "callers=0" for a symbol nobody looked up.
    """
    status = call_site_evidence.get("status")
    if status not in ("collected", "collected_no_call_sites"):
        return None
    returned = int(call_site_evidence.get("returned_call_sites", 0) or 0)
    omitted = int(call_site_evidence.get("omitted_call_sites", 0) or 0)
    truncated = omitted > 0 or bool(call_site_evidence.get("partial"))
    count_text = f"{returned}+" if truncated else str(returned)
    if top_names:
        fact = f"callers={count_text} (top: {', '.join(top_names)})"
    else:
        fact = f"callers={count_text}"
    return f"{comment_prefix} tg: {fact}"
