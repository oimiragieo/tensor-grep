from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.agent_capsule_targets import (
    _as_dict as _as_dict,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_list_of_dicts as _as_list_of_dicts,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_list_of_strings as _as_list_of_strings,
)
from tensor_grep.cli.agent_capsule_targets import (
    _dedupe as _dedupe,
)


def _alternative_targets(
    payload: dict[str, Any],
    target: dict[str, Any],
    *,
    limit: int | None = 4,
) -> list[dict[str, Any]]:
    primary_file = str(target.get("file") or "")
    candidate_targets = _as_dict(payload.get("candidate_edit_targets"))
    file_matches = {
        str(match.get("path") or ""): match
        for match in _as_list_of_dicts(payload.get("file_matches"))
        if match.get("path")
    }
    alternatives: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()

    for symbol in _as_list_of_dicts(candidate_targets.get("symbols")):
        file_path = str(symbol.get("file") or "")
        if not file_path or file_path == primary_file:
            continue
        symbol_name = str(symbol.get("name") or "")
        key = (file_path, symbol_name or None)
        if key in seen:
            continue
        seen.add(key)
        match = file_matches.get(file_path, {})
        score = max(int(symbol.get("score", 0) or 0), int(match.get("score", 0) or 0))
        line = symbol.get("line") or symbol.get("start_line") or 1
        alternative: dict[str, Any] = {
            "file": file_path,
            "symbol": symbol_name or None,
            "kind": symbol.get("kind") or "unknown",
            "line": int(line) if isinstance(line, int) or str(line).isdigit() else 1,
            "language": repo_map._target_language_for_path(file_path),
            "confidence": repo_map._confidence_from_score(score),
            "reasons": list(match.get("reasons") or []),
            "evidence": list(match.get("provenance") or ["heuristic"]),
        }
        for proof_field in (
            "semantic_provider",
            "provenance",
            "lsp_provider_response",
            "lsp_proof",
            "lsp_operation",
            "lsp_resolution_basis",
        ):
            if proof_field in symbol:
                alternative[proof_field] = symbol[proof_field]
        if alternative.get("lsp_proof") is True:
            evidence_value = alternative.get("evidence")
            evidence_items = evidence_value if isinstance(evidence_value, list) else []
            alternative["evidence"] = _dedupe([
                "lsp-confirmed",
                *[str(item) for item in evidence_items if item is not None and str(item)],
            ])
        alternatives.append(alternative)

    return alternatives if limit is None else alternatives[:limit]


def _line_map(source: str, start_line: object) -> list[dict[str, Any]]:
    try:
        current_line = int(str(start_line))
    except (TypeError, ValueError):
        current_line = 1
    return [
        {"line": current_line + index, "text": line}
        for index, line in enumerate(source.splitlines())
    ]


def _command_ref(argv: list[object]) -> dict[str, Any]:
    args = [str(arg) for arg in argv]
    return {
        "argv": args,
        "command": subprocess.list2cmdline(args),
    }


def _normalize_gpu_device_ids(device_ids: list[int] | None) -> list[int]:
    if not device_ids:
        return []
    normalized: list[int] = []
    seen: set[int] = set()
    for raw_device_id in device_ids:
        try:
            device_id = int(raw_device_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid GPU device id: {raw_device_id!r}") from exc
        if device_id < 0:
            raise ValueError(
                f"Invalid GPU device id: {device_id}. Device IDs must be non-negative."
            )
        if device_id in seen:
            continue
        seen.add(device_id)
        normalized.append(device_id)
    return normalized


def _agent_gpu_query_terms(query: str, *, limit: int = 8) -> list[str]:
    terms: list[str] = []
    for term in repo_map._symbol_query_terms(query):
        cleaned = str(term).strip()
        if len(cleaned) < 3:
            continue
        terms.append(cleaned)
    return _dedupe(terms)[:limit]


def _summarize_agent_gpu_json_result(
    result: dict[str, Any],
    *,
    match_preview_limit: int = 3,
    redact_probe_paths: bool = False,
) -> dict[str, Any]:
    summary = {key: value for key, value in result.items() if key != "payload"}
    if redact_probe_paths:
        if "argv" in summary:
            summary["argv"] = [
                "<agent-gpu-probe-root>" if "tg-agent-gpu-probe-" in str(arg) else str(arg)
                for arg in _as_list_of_strings(summary.get("argv"))
            ]
        if "command" in summary:
            summary["command"] = subprocess.list2cmdline([
                str(arg) for arg in summary.get("argv", [])
            ])

    payload = _as_dict(result.get("payload"))
    if not payload:
        return summary

    payload_summary: dict[str, Any] = {}
    for key in (
        "version",
        "routing_backend",
        "routing_reason",
        "sidecar_used",
        "query",
        "path",
        "total_matches",
        "total_files",
        "requested_gpu_device_ids",
        "routing_gpu_device_ids",
    ):
        if key in payload:
            if redact_probe_paths and key == "path":
                payload_summary[key] = "<agent-gpu-probe-root>"
            else:
                payload_summary[key] = payload[key]

    pipeline = _as_dict(payload.get("pipeline"))
    if pipeline:
        payload_summary["pipeline"] = {
            key: pipeline[key]
            for key in (
                "pattern_count",
                "pattern_batch_count",
                "single_dispatch",
                "cpu_staging_bytes",
                "transfer_time_ms",
                "kernel_time_ms",
                "wall_time_ms",
                "transfer_throughput_bytes_s",
            )
            if key in pipeline
        }

    matches = _as_list_of_dicts(payload.get("matches"))
    preview: list[dict[str, Any]] = []
    for match in matches[:match_preview_limit]:
        text = str(match.get("text") or "")
        preview.append({
            "file": (
                "<agent-gpu-probe-file>"
                if redact_probe_paths
                else match.get("file") or match.get("path")
            ),
            "line": match.get("line") or match.get("line_number"),
            "pattern_id": match.get("pattern_id"),
            "pattern_text": match.get("pattern_text") or match.get("pattern"),
            "text_preview": text[:160] if text else None,
        })
    payload_summary["matches_preview"] = preview
    payload_summary["matches_omitted"] = max(0, len(matches) - len(preview))
    summary["payload"] = payload_summary
    return summary


def _resolve_match_file(file_value: object, search_root: Path) -> str | None:
    raw_file = str(file_value or "").strip()
    if not raw_file:
        return None
    candidate = Path(raw_file)
    if not candidate.is_absolute():
        candidate = search_root / candidate
    try:
        return str(candidate.resolve())
    except OSError:
        return str(candidate)
