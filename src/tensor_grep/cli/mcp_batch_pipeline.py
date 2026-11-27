"""Typed step-chaining pipeline helpers for MCP tg_batch (arXiv:2611.19123)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

MAX_REF_LENGTH = 256
MAX_REF_DEPTH = 8

_REF_SEGMENT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)(?:\[(\d+)\])?$")
_STEPS_PREFIX_RE = re.compile(r"^\$steps\[(-?\d+)\](?:\.(.*))?$")
_PREV_PREFIX_RE = re.compile(r"^\$prev(?:\.(.*))?$")


class PipelineRefError(ValueError):
    """Raised when a tg_batch pipeline reference is malformed, unsafe, or unresolvable."""


def is_pipeline_ref(value: object) -> bool:
    """Return True iff value uses the $prev or $steps[...] pipeline reference prefix."""
    if not isinstance(value, str):
        return False
    return (
        value == "$prev"
        or value == "$steps"
        or value.startswith((
            "$prev.",
            "$prev[",
            "$steps.",
            "$steps[",
        ))
    )


def parse_pipeline_ref(
    ref: str,
    step_index: int,
) -> tuple[int, tuple[tuple[str, int | None], ...]]:
    """Parse and validate a $prev.<field> or $steps[i].<field> reference."""
    if len(ref) > MAX_REF_LENGTH:
        raise PipelineRefError(f"pipeline reference exceeds {MAX_REF_LENGTH} characters")

    remainder: str | None = None
    prev_match = _PREV_PREFIX_RE.match(ref)
    if prev_match is not None:
        if step_index <= 0:
            raise PipelineRefError(f"step {step_index} cannot reference $prev")
        target_index = step_index - 1
        remainder = prev_match.group(1)
    else:
        steps_match = _STEPS_PREFIX_RE.match(ref)
        if steps_match is None:
            raise PipelineRefError(f"invalid pipeline reference syntax: {ref!r}")
        target_index = int(steps_match.group(1))
        if target_index < 0 or target_index >= step_index:
            raise PipelineRefError(
                f"step {step_index} cannot reference step {target_index}; "
                "must reference an earlier step"
            )
        remainder = steps_match.group(2)

    if not remainder:
        raise PipelineRefError(f"pipeline reference must specify a field path: {ref!r}")

    parts = remainder.split(".")
    if len(parts) > MAX_REF_DEPTH:
        raise PipelineRefError(
            f"pipeline reference exceeds maximum depth of {MAX_REF_DEPTH} segments"
        )

    segments: list[tuple[str, int | None]] = []
    for part in parts:
        seg_match = _REF_SEGMENT_RE.match(part)
        if seg_match is None:
            raise PipelineRefError(f"disallowed or invalid pipeline field segment: {part!r}")
        field_name = seg_match.group(1)
        if field_name.startswith("_"):
            raise PipelineRefError(f"private or dunder field access is forbidden: {field_name!r}")
        index_str = seg_match.group(2)
        list_index = int(index_str) if index_str is not None else None
        segments.append((field_name, list_index))

    return target_index, tuple(segments)


def extract_step_dependencies(item: Mapping[str, object], step_index: int) -> set[int]:
    """Collect earlier step indices referenced by any argument in item."""
    deps: set[int] = set()

    def _visit(node: object, depth: int) -> None:
        if depth > MAX_REF_DEPTH:
            raise PipelineRefError("step arguments exceed maximum nesting depth")
        if is_pipeline_ref(node):
            target_idx, _ = parse_pipeline_ref(str(node), step_index)
            deps.add(target_idx)
        elif isinstance(node, Mapping):
            for key, val in node.items():
                if depth == 0 and key in {"id", "kind"}:
                    continue
                _visit(val, depth + 1)
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            for elem in node:
                _visit(elem, depth + 1)

    _visit(item, 0)
    return deps


def resolve_pipeline_ref(
    ref: str,
    step_index: int,
    results_by_index: Mapping[int, Mapping[str, object]],
) -> object:
    """Resolve a validated pipeline reference against completed step results."""
    target_idx, segments = parse_pipeline_ref(ref, step_index)
    step_entry = results_by_index.get(target_idx)
    if step_entry is None or "error" in step_entry or "result" not in step_entry:
        raise PipelineRefError(f"referenced step {target_idx} failed or produced no result")

    current: object = step_entry["result"]
    for field_name, list_index in segments:
        if field_name.startswith("_"):
            raise PipelineRefError(f"private or dunder field access is forbidden: {field_name!r}")
        if not isinstance(current, Mapping) or field_name not in current:
            raise PipelineRefError(f"field {field_name!r} not found in result of step {target_idx}")
        current = current[field_name]
        if list_index is not None:
            if not isinstance(current, Sequence) or isinstance(current, (str, bytes, bytearray)):
                raise PipelineRefError(
                    f"field {field_name!r} in step {target_idx} is not indexable"
                )
            if list_index < 0 or list_index >= len(current):
                raise PipelineRefError(
                    f"index {list_index} out of bounds for {field_name!r} in step {target_idx}"
                )
            current = current[list_index]
    return current


def resolve_item_arguments(
    item: Mapping[str, object],
    step_index: int,
    results_by_index: Mapping[int, Mapping[str, object]],
) -> dict[str, object]:
    """Return a copy of item with all pipeline references resolved to concrete values."""

    def _resolve_node(node: object, depth: int) -> Any:
        if depth > MAX_REF_DEPTH:
            raise PipelineRefError("step arguments exceed maximum nesting depth")
        if is_pipeline_ref(node):
            return resolve_pipeline_ref(str(node), step_index, results_by_index)
        if isinstance(node, Mapping):
            return {
                k: (v if depth == 0 and k in {"id", "kind"} else _resolve_node(v, depth + 1))
                for k, v in node.items()
            }
        if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            return [_resolve_node(elem, depth + 1) for elem in node]
        return node

    resolved = _resolve_node(item, 0)
    assert isinstance(resolved, dict)
    return resolved


def compact_final_batch_response(
    result_slots: Sequence[dict[str, object]],
    *,
    max_bytes: int = 4000,
) -> list[dict[str, object]]:
    """Return only the final batch result slot, compacting its payload within max_bytes."""
    if not result_slots:
        return []
    final_slot = dict(result_slots[-1])
    payload = final_slot.get("result")
    if isinstance(payload, dict):
        from tensor_grep.cli.mcp_compact import compact_mcp_json_result

        compacted = json.loads(
            compact_mcp_json_result(json.dumps(payload), max_bytes=max(128, max_bytes))
        )
        final_slot["result"] = compacted
    elif isinstance(payload, str):
        encoded = payload.encode("utf-8")
        if len(encoded) > max_bytes:
            keep = max(0, max_bytes - 32)
            final_slot["result"] = (
                encoded[:keep].decode("utf-8", errors="ignore") + "\n...[truncated]"
            )
            final_slot["result_incomplete"] = True
    return [final_slot]
