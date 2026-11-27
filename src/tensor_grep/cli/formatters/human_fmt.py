"""Human-first plain-text formatters for CLI commands."""

from __future__ import annotations

import json
from typing import Any


def _item_label(item: object) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        file_part = item.get("file") or item.get("path") or ""
        name_part = item.get("symbol") or item.get("name") or item.get("caller") or ""
        line_part = item.get("line")
        loc = f"{file_part}:L{line_part}" if file_part and line_part else str(file_part)
        if loc and name_part:
            return f"{loc} ({name_part})"
        return str(loc or name_part or item)
    return str(item)


def _format_section(title: str, items: list[Any], *, max_items: int = 10) -> list[str]:
    if not items:
        return []
    lines = [f"{title}:"]
    for item in items[:max_items]:
        lines.append(f"  - {_item_label(item)}")
    if len(items) > max_items:
        lines.append(f"  ... ({len(items) - max_items} more)")
    return lines


def format_classify_human(
    results: list[dict[str, Any]],
    budgeted_lines: list[str],
    line_budget: dict[str, Any],
) -> str:
    """Render log classification results in human-readable plain text."""
    out: list[str] = []
    for idx, (entry, raw_line) in enumerate(zip(results, budgeted_lines, strict=False), start=1):
        snippet = raw_line.strip()
        if len(snippet) > 120:
            snippet = snippet[:117] + "..."
        label = entry["label"]
        confidence = float(entry["confidence"])
        if snippet:
            out.append(f"L{idx}: {label} ({confidence:.2f})  {snippet}")
        else:
            out.append(f"L{idx}: {label} ({confidence:.2f})")
    if line_budget.get("truncated"):
        omitted = line_budget.get("omitted_lines", 0)
        out.append(f"... ({omitted} more lines omitted; pass --max-lines 0 for all)")
    return "\n".join(out)


def format_context_human(payload: dict[str, Any], *, max_items: int = 10) -> str:
    """Render context pack summary with top files, symbols, and tests."""
    files = list(payload.get("files") or [])
    tests = list(payload.get("tests") or [])
    symbols = list(payload.get("symbols") or [])
    imports = list(payload.get("imports") or [])
    lines = [
        f"Context pack for {payload['path']}",
        f"query={payload['query']}",
        f"files={len(files)} tests={len(tests)}",
        f"symbols={len(symbols)} imports={len(imports)}",
    ]
    lines.extend(_format_section("Top files", files, max_items=max_items))
    lines.extend(_format_section("Top symbols", symbols, max_items=max_items))
    lines.extend(_format_section("Top tests", tests, max_items=max_items))
    return "\n".join(lines)


def format_edit_plan_human(payload: dict[str, Any], *, max_items: int = 10) -> str:
    """Render edit-plan summary with top files, symbols, and tests."""
    files = list(payload.get("files") or [])
    tests = list(payload.get("tests") or [])
    symbols = list(payload.get("symbols") or [])
    lines = [
        f"Edit plan for {payload['path']}",
        f"query={payload['query']}",
        f"files={len(files)} tests={len(tests)} symbols={len(symbols)}",
    ]
    lines.extend(_format_section("Top files", files, max_items=max_items))
    lines.extend(_format_section("Top symbols", symbols, max_items=max_items))
    lines.extend(_format_section("Top tests", tests, max_items=max_items))
    return "\n".join(lines)


def format_blast_radius_human(payload: dict[str, Any], *, max_items: int = 10) -> str:
    """Render blast-radius summary with top callers, files, and tests."""
    definitions = list(payload.get("definitions") or [])
    callers = list(payload.get("callers") or [])
    files = list(payload.get("files") or [])
    tests = list(payload.get("tests") or [])
    consumers = list(payload.get("import_graph_consumers") or [])
    lines = [
        f"Blast radius for {payload['symbol']} in {payload['path']}",
        (
            f"definitions={len(definitions)} callers={len(callers)} "
            f"files={len(files)} tests={len(tests)} import_consumers={len(consumers)}"
        ),
    ]
    lines.extend(_format_section("Top callers", callers, max_items=max_items))
    lines.extend(_format_section("Top files", files, max_items=max_items))
    lines.extend(_format_section("Top tests", tests, max_items=max_items))
    return "\n".join(lines)


def format_blast_radius_plan_human(payload: dict[str, Any], *, max_items: int = 10) -> str:
    """Render blast-radius-plan summary with top files, symbols, and tests."""
    files = list(payload.get("files") or [])
    tests = list(payload.get("tests") or [])
    symbols = list(payload.get("symbols") or [])
    lines = [
        f"Blast radius plan for {payload['symbol']} in {payload['path']}",
        f"files={len(files)} tests={len(tests)} symbols={len(symbols)}",
    ]
    lines.extend(_format_section("Top files", files, max_items=max_items))
    lines.extend(_format_section("Top symbols", symbols, max_items=max_items))
    lines.extend(_format_section("Top tests", tests, max_items=max_items))
    return "\n".join(lines)


def format_rewrite_plan_human(rewrite_json: str) -> str:
    """Render a rewrite-plan JSON payload as a unified diff or readable human summary."""
    payload = json.loads(rewrite_json)
    if payload.get("error"):
        err = payload["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        return f"Error: {message}"
    diff = payload.get("diff")
    if isinstance(diff, str) and diff.strip():
        return diff.rstrip("\n")
    edits = list(payload.get("edits") or [])
    total = payload.get("total_edits", len(edits))
    if not edits:
        return f"No matches found ({total} edits)."
    lines: list[str] = []
    seen_files: set[str] = set()
    for edit in edits:
        file_path = str(edit.get("file") or "<unknown>")
        seen_files.add(file_path)
        line_no = edit.get("line", 1)
        orig = str(edit.get("original_text") or "")
        repl = str(edit.get("replacement_text") or "")
        lines.append(f"--- a/{file_path}")
        lines.append(f"+++ b/{file_path}")
        lines.append(f"@@ -L{line_no} @@")
        for orig_line in orig.splitlines() or [orig]:
            lines.append(f"-{orig_line}")
        for repl_line in repl.splitlines() or [repl]:
            lines.append(f"+{repl_line}")
    lines.append(
        f"Planned {total} edit(s) across {len(seen_files)} file(s). Pass --apply to write changes."
    )
    return "\n".join(lines)


def format_scan_human(
    payload: dict[str, Any],
    *,
    scan_banner: str,
    config_path: object,
    refusal_reason: object = None,
    verbose: bool = False,
) -> tuple[list[str], list[str]]:
    """Render `tg scan` human-readable stdout and stderr lines."""
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    total_matches = int(payload.get("total_matches", 0) or 0)
    has_extras = bool(
        payload.get("partial")
        or payload.get("baseline")
        or payload.get("baseline_written")
        or payload.get("suppressions")
        or payload.get("suppressions_written")
    )
    if (verbose or total_matches > 0 or has_extras) and not refusal_reason:
        stdout_lines.append(f"{scan_banner} based on {config_path}...")
    if payload.get("partial"):
        remediation = str(payload.get("remediation") or "").strip()
        stdout_lines.append(
            f"warning: INCOMPLETE SCAN: {remediation}"
            if remediation
            else "warning: INCOMPLETE SCAN: part of the scope could not be read, so this result "
            "does NOT prove those files are clean."
        )
    if verbose or total_matches > 0:
        for finding in payload.get("findings") or []:
            stdout_lines.append(
                f"[scan] rule={finding['rule_id']} lang={finding['language']} "
                f"matches={finding['matches']} files={len(finding['files'])}"
            )
    backends = ",".join(payload.get("backends") or []) or "none"
    stdout_lines.append(
        "Scan completed. "
        f"rules={payload['rule_count']} matched_rules={payload['matched_rules']} "
        f"total_matches={total_matches} backends={backends}"
    )
    if payload.get("baseline"):
        b = payload["baseline"]
        stdout_lines.append(
            f"Baseline compared. new={b['new_findings']} existing={b['existing_findings']} "
            f"resolved={b['resolved_findings']}"
        )
    if payload.get("baseline_written"):
        bw = payload["baseline_written"]
        stdout_lines.append(f"Baseline written to {bw['path']} (count={bw['count']}).")
    if payload.get("suppressions"):
        sup = payload["suppressions"]
        if sup.get("path"):
            stdout_lines.append(
                f"Suppressions applied from {sup['path']} (suppressed={sup['suppressed_findings']})."
            )
        if sup.get("inline_suppressed_findings"):
            stdout_lines.append(
                f"Inline suppressions applied (suppressed={sup['inline_suppressed_findings']})."
            )
        for warning in sup.get("warnings") or []:
            stderr_lines.append(f"Warning: {warning}")
    if payload.get("suppressions_written"):
        sw = payload["suppressions_written"]
        stdout_lines.append(f"Suppressions written to {sw['path']} (count={sw['count']}).")
    return stdout_lines, stderr_lines


def audit_verify_error_payload(exc: Exception, *, manifest_path: str) -> dict[str, Any]:
    """Build a structured JSON error payload for `tg audit-verify --json`."""
    if isinstance(exc, FileNotFoundError):
        code = "not_found"
    elif isinstance(exc, (ValueError, json.JSONDecodeError)):
        code = "invalid_input"
    else:
        code = "internal_error"
    message = str(exc)
    return {
        "version": 1,
        "schema_version": 1,
        "routing_backend": "AstBackend",
        "routing_reason": "audit-manifest-verify",
        "manifest_path": manifest_path,
        "valid": False,
        "errors": [message],
        "error": {"code": code, "message": message},
    }
