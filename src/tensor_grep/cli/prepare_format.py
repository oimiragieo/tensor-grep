"""Formatting and bounded `--compact` serialization for `tg prepare`."""

from __future__ import annotations

import json
from typing import Any

import typer

PREPARE_COMPACT_MAX_BYTES = 4000

_PREPARE_PROTECTED_KEYS: frozenset[str] = frozenset({
    "version",
    "schema_version",
    "routing_backend",
    "routing_reason",
    "prepare_version",
    "path",
    "query",
    "primary_target",
    "next_action",
    "blast_radius_floor",
    "context_consistency",
    "ask_user_before_editing",
    "confidence",
    "rollback",
    "validation_commands",
    "possibly_incomplete",
    "partial",
    "partial_reason",
    "deadline_limit",
    "result_incomplete",
    "incomplete_reason",
    "compact_truncated",
    "omissions",
})

_PREPARE_TRIM_ORDER: tuple[str, ...] = (
    "alternative_targets",
    "validation_plan",
    "coordination",
    "scan_limit",
)


def _compact_json_bytes(payload: dict[str, Any]) -> tuple[str, int]:
    serialized = json.dumps(payload, separators=(",", ":"))
    return serialized, len(serialized.encode("utf-8"))


def _shrink_string(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        if limit <= 0:
            return ""
        if len(value) > limit:
            return value[: max(0, limit - 3)] + "..." if limit > 3 else value[:limit]
    return value


def _record_omissions(bounded: dict[str, Any], omitted_counts: dict[str, int]) -> None:
    if not omitted_counts:
        return
    bounded["compact_truncated"] = True
    existing = bounded.get("omissions")
    if isinstance(existing, dict):
        compact_omitted = existing.get("compact_omitted")
        merged = dict(compact_omitted) if isinstance(compact_omitted, dict) else {}
        new_sections = 0
        for section, count in omitted_counts.items():
            if section not in merged and count > 0:
                new_sections += 1
            merged[section] = int(merged.get(section, 0)) + count
        existing["compact_omitted"] = merged
        omitted_sections = existing.get("omitted_section_count")
        if isinstance(omitted_sections, int) and not isinstance(omitted_sections, bool):
            existing["omitted_section_count"] = omitted_sections + new_sections
        existing["omitted_count"] = sum(merged.values())
        return
    bounded["omissions"] = {
        "compact_omitted": dict(omitted_counts),
        "omitted_count": sum(omitted_counts.values()),
    }


def _shrink_protected_subfields(bounded: dict[str, Any], max_str: int) -> None:
    omitted_counts: dict[str, int] = {}
    floor = bounded.get("blast_radius_floor")
    if isinstance(floor, dict):
        shrunk_floor: dict[str, Any] = {
            k: floor[k]
            for k in (
                "callers_count",
                "source",
                "truncated",
                "partial",
                "deadline_exceeded",
                "possibly_incomplete",
            )
            if k in floor
        }
        existing_top_omitted = int(floor.get("top_callers_omitted") or 0)
        top_callers = floor.get("top_callers")
        if isinstance(top_callers, list) and top_callers:
            if max_str >= 48:
                kept: list[dict[str, Any]] = []
                dropped_top = 0
                for c in top_callers[:3]:
                    if not isinstance(c, dict):
                        dropped_top += 1
                        continue
                    file_val = c.get("file")
                    sym_val = c.get("symbol")
                    if (isinstance(file_val, str) and len(file_val) > max_str) or (
                        isinstance(sym_val, str) and len(sym_val) > max_str
                    ):
                        dropped_top += 1
                        continue
                    kept.append({k: c[k] for k in ("file", "line", "symbol") if k in c})
                if kept:
                    shrunk_floor["top_callers"] = kept
                omitted_from_slice = max(0, len(top_callers) - 3) + dropped_top
                omitted_count = existing_top_omitted + omitted_from_slice
                if omitted_count:
                    shrunk_floor["top_callers_omitted"] = omitted_count
                if omitted_from_slice:
                    omitted_counts["blast_radius_floor.top_callers"] = (
                        omitted_counts.get("blast_radius_floor.top_callers", 0) + omitted_from_slice
                    )
            else:
                shrunk_floor["top_callers_omitted"] = existing_top_omitted + len(top_callers)
                omitted_counts["blast_radius_floor.top_callers"] = omitted_counts.get(
                    "blast_radius_floor.top_callers", 0
                ) + len(top_callers)
        elif existing_top_omitted:
            shrunk_floor["top_callers_omitted"] = existing_top_omitted
        existing_callers_omitted = int(floor.get("callers_omitted") or 0)
        if "callers" in floor and isinstance(floor["callers"], list) and floor["callers"]:
            shrunk_floor["callers_omitted"] = existing_callers_omitted + len(floor["callers"])
        elif existing_callers_omitted:
            shrunk_floor["callers_omitted"] = existing_callers_omitted
        bounded["blast_radius_floor"] = shrunk_floor

    ask = bounded.get("ask_user_before_editing")
    if isinstance(ask, dict):
        shrunk_ask: dict[str, Any] = {"required": bool(ask.get("required"))}
        if max_str >= 48 and isinstance(ask.get("reasons"), list) and ask["reasons"]:
            shrunk_ask["reasons"] = [_shrink_string(r, max_str) for r in ask["reasons"][:2]]
        bounded["ask_user_before_editing"] = shrunk_ask

    conf = bounded.get("confidence")
    if isinstance(conf, dict):
        bounded["confidence"] = {k: conf[k] for k in ("overall", "level", "band") if k in conf}

    rb = bounded.get("rollback")
    if isinstance(rb, dict):
        shrunk_rb: dict[str, Any] = {}
        for k in ("supported", "command", "checkpoint_id", "mode"):
            if k not in rb:
                continue
            val = rb[k]
            if k in ("command", "checkpoint_id"):
                if isinstance(val, str) and len(val) > max_str:
                    omitted_key = f"rollback.{k}"
                    omitted_counts[omitted_key] = omitted_counts.get(omitted_key, 0) + 1
                    continue
                shrunk_rb[k] = val
            else:
                shrunk_rb[k] = _shrink_string(val, max_str)
        bounded["rollback"] = shrunk_rb

    vc = bounded.get("validation_commands")
    if isinstance(vc, list):
        limit_items = 3 if max_str >= 48 else 1
        kept_vc: list[Any] = []
        dropped_vc = 0
        for cmd in vc:
            if isinstance(cmd, str) and len(cmd) > max_str:
                dropped_vc += 1
                continue
            if len(kept_vc) < limit_items:
                kept_vc.append(cmd)
            else:
                dropped_vc += 1
        bounded["validation_commands"] = kept_vc
        if dropped_vc:
            omitted_counts["validation_commands"] = (
                omitted_counts.get("validation_commands", 0) + dropped_vc
            )

    next_action = bounded.get("next_action")
    if isinstance(next_action, dict):
        action = next_action.get("action")
        shrunk_action = dict(action) if isinstance(action, dict) else {}
        for action_key in ("instruction", "path"):
            if action_key in shrunk_action:
                action_val = shrunk_action[action_key]
                if isinstance(action_val, str) and len(action_val) > max_str:
                    shrunk_action.pop(action_key, None)
                    omitted_key = f"next_action.action.{action_key}"
                    omitted_counts[omitted_key] = omitted_counts.get(omitted_key, 0) + 1
        shrunk_next: dict[str, Any] = {"action": shrunk_action}
        for branch in ("on_success", "on_failure"):
            if isinstance(next_action.get(branch), dict):
                branch_dict = dict(next_action[branch])
                if "reason" in branch_dict:
                    branch_dict["reason"] = _shrink_string(branch_dict["reason"], max_str)
                if "suggested_path" in branch_dict:
                    suggested = branch_dict["suggested_path"]
                    if isinstance(suggested, str) and len(suggested) > max_str:
                        branch_dict.pop("suggested_path", None)
                        omitted_counts[f"next_action.{branch}.suggested_path"] = (
                            omitted_counts.get(f"next_action.{branch}.suggested_path", 0) + 1
                        )
                shrunk_next[branch] = branch_dict
        bounded["next_action"] = shrunk_next

    primary = bounded.get("primary_target")
    if isinstance(primary, dict):
        shrunk_primary: dict[str, Any] = {}
        for k in ("file", "line", "end_line", "symbol", "kind"):
            if k not in primary:
                continue
            val = primary[k]
            if k in ("file", "symbol"):
                if isinstance(val, str) and len(val) > max_str:
                    omitted_key = f"primary_target.{k}"
                    omitted_counts[omitted_key] = omitted_counts.get(omitted_key, 0) + 1
                    continue
                shrunk_primary[k] = val
            else:
                shrunk_primary[k] = _shrink_string(val, max_str)
        bounded["primary_target"] = shrunk_primary

    cc = bounded.get("context_consistency")
    if isinstance(cc, dict):
        bounded["context_consistency"] = {
            k: _shrink_string(cc[k], max_str)
            for k in ("status", "mode", "stale", "reason")
            if k in cc
        }

    if max_str <= 48:
        for top_key in ("query", "path", "routing_reason", "partial_reason", "incomplete_reason"):
            if top_key in bounded:
                bounded[top_key] = _shrink_string(bounded[top_key], max_str)
    _record_omissions(bounded, omitted_counts)


def format_prepare_compact_json(
    payload: dict[str, Any], *, max_bytes: int = PREPARE_COMPACT_MAX_BYTES
) -> str:
    """Serialize `tg prepare` payload to compact JSON <= `max_bytes` UTF-8 bytes."""
    bounded = dict(payload)
    serialized, size = _compact_json_bytes(bounded)
    if size <= max_bytes:
        return serialized

    for key in _PREPARE_TRIM_ORDER:
        if key in bounded:
            bounded.pop(key, None)
            bounded["compact_truncated"] = True
            serialized, size = _compact_json_bytes(bounded)
            if size <= max_bytes:
                return serialized

    for key in sorted(bounded.keys(), key=lambda k: len(json.dumps(bounded[k])), reverse=True):
        if key not in _PREPARE_PROTECTED_KEYS:
            bounded.pop(key, None)
            bounded["compact_truncated"] = True
            serialized, size = _compact_json_bytes(bounded)
            if size <= max_bytes:
                return serialized

    for max_str in (120, 48, 16, 4):
        _shrink_protected_subfields(bounded, max_str)
        bounded["compact_truncated"] = True
        serialized, size = _compact_json_bytes(bounded)
        if size <= max_bytes:
            return serialized

    # Final unconditional fallback: shrink/trim remaining fields until <= max_bytes.
    for key in ("query", "path", "routing_reason", "partial_reason", "incomplete_reason"):
        if key in bounded and isinstance(bounded[key], str):
            bounded[key] = _shrink_string(bounded[key], 0)
    fallback_omissions: dict[str, int] = {}
    vc = bounded.get("validation_commands")
    if isinstance(vc, list) and vc:
        fallback_omissions["validation_commands"] = len(vc)
        bounded["validation_commands"] = []
    if "next_action" in bounded:
        bounded.pop("next_action", None)
        fallback_omissions["next_action"] = 1
    _record_omissions(bounded, fallback_omissions)
    serialized, size = _compact_json_bytes(bounded)
    if size <= max_bytes:
        return serialized

    for key in list(bounded.keys()):
        if key not in {
            "version",
            "schema_version",
            "compact_truncated",
            "possibly_incomplete",
            "omissions",
        }:
            bounded.pop(key, None)
            if key in {
                "rollback",
                "primary_target",
                "validation_commands",
                "next_action",
                "ask_user_before_editing",
                "confidence",
                "context_consistency",
            }:
                _record_omissions(bounded, {key: 1})
            serialized, size = _compact_json_bytes(bounded)
            if size <= max_bytes:
                return serialized

    while size > max_bytes:
        changed = False
        if "omissions" in bounded:
            bounded.pop("omissions", None)
            changed = True
        else:
            for key in ("version", "schema_version", "possibly_incomplete"):
                if key not in bounded:
                    continue
                val = bounded[key]
                if isinstance(val, bool):
                    continue
                if isinstance(val, str) and len(val) > 1:
                    bounded[key] = val[: max(1, len(val) // 2)]
                    changed = True
                    break
                bounded.pop(key, None)
                changed = True
                break
            if not changed:
                for key in ("possibly_incomplete", "schema_version", "version"):
                    if key in bounded:
                        bounded.pop(key, None)
                        changed = True
                        break
        if not changed:
            break
        bounded["compact_truncated"] = True
        serialized, size = _compact_json_bytes(bounded)

    return serialized


def format_prepare_text(payload: dict[str, Any]) -> str:
    """Render concise human-readable text output for `tg prepare`."""
    primary = payload.get("primary_target", {})
    floor = payload.get("blast_radius_floor", {})
    claim_hook = payload.get("coordination", {}).get("claim", {})
    lines = [
        f"# Prepare: {payload.get('path', '')}",
        f"query={payload.get('query', '')}",
        f"primary={primary.get('file')}#L{primary.get('line')} {primary.get('symbol')}",
        f"confidence={payload.get('confidence', {}).get('overall', 0)}",
        f"ask_required={bool(payload.get('ask_user_before_editing', {}).get('required'))}",
        f"blast_radius_floor={floor.get('callers_count', 0)} callers (source={floor.get('source')})",
        f"validation={len(payload.get('validation_commands', []))} commands",
        f"claim_submitted={bool(claim_hook.get('submitted'))}",
    ]
    if payload.get("partial"):
        lines.append(f"partial=true partial_reason={payload.get('partial_reason')}")
    return "\n".join(lines)


def emit_prepare_output(payload: dict[str, Any], *, json_output: bool, compact: bool) -> None:
    """Emit `tg prepare` output to stdout in text, full JSON, or compact JSON mode."""
    if json_output:
        if compact:
            typer.echo(format_prepare_compact_json(payload))
        else:
            typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(format_prepare_text(payload))


def write_prepare_out(out: str, payload: dict[str, Any]) -> None:
    """Persist full prepare capsule JSON atomically, refusing symlinks."""
    from pathlib import Path

    from tensor_grep.cli.session_store import _write_json_atomic

    expanded_out = Path(out).expanduser()
    if expanded_out.is_symlink():
        raise OSError(f"Refusing to write the prepare capsule through a symlink: {expanded_out}")
    _write_json_atomic(expanded_out.resolve(), payload)


def emit_test_json_error(
    message: str, config: str | None, *, json_output: bool, quiet: bool = False
) -> int:
    """Emit `tg test` error in JSON or stderr text mode and return exit code 1."""
    import sys

    if json_output:
        print(
            json.dumps(
                {
                    "version": 1,
                    "routing_backend": "AstWorkflow",
                    "routing_reason": "test",
                    "config": config,
                    "passed": False,
                    "total_cases": 0,
                    "failed_cases": 0,
                    "failures": [],
                    "error": {"code": "invalid_input", "message": message},
                },
                indent=2,
            )
        )
    elif not quiet:
        print(f"Error: {message}", file=sys.stderr)
    return 1


def emit_test_json_result(
    config_path: str,
    backends: list[str],
    total_cases: int,
    failures: list[str],
    *,
    json_output: bool = True,
    backend_desc: str = "",
) -> int:
    """Emit structured JSON or text result for `tg test` and return exit code."""
    import sys

    if json_output:
        print(
            json.dumps(
                {
                    "version": 1,
                    "routing_backend": "AstWorkflow",
                    "routing_reason": "test",
                    "config": config_path,
                    "backends": backends,
                    "total_cases": total_cases,
                    "failed_cases": len(failures),
                    "passed": len(failures) == 0,
                    "failures": failures,
                },
                indent=2,
            )
        )
        return 1 if failures else 0

    print(f"Testing AST rules using {backend_desc} from {config_path}...")
    if failures:
        for failure in failures:
            print(f"[test] FAIL {failure}", file=sys.stderr)
        print(f"Rule tests failed. cases={total_cases} failures={len(failures)}", file=sys.stderr)
        return 1
    print(f"All tests passed. cases={total_cases}")
    return 0
