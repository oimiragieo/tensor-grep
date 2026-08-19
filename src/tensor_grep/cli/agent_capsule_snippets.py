from __future__ import annotations

from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.agent_capsule_gpu_support import (
    _command_ref as _command_ref,
)
from tensor_grep.cli.agent_capsule_gpu_support import (
    _line_map as _line_map,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_dict as _as_dict,
)
from tensor_grep.cli.agent_capsule_targets import (
    _as_list_of_dicts as _as_list_of_dicts,
)


def _expanded_line_map(
    source: dict[str, Any],
    rendered_source: str,
) -> list[dict[str, Any]]:
    rendered_lines = rendered_source.splitlines()
    if not rendered_lines:
        return []

    raw_line_map = _as_list_of_dicts(source.get("line_map"))
    if not raw_line_map:
        return _line_map(rendered_source, source.get("start_line") or 1)

    rendered_to_original: dict[int, int] = {}
    for item in raw_line_map:
        if item.get("line") is not None:
            rendered_index = len(rendered_to_original) + 1
            try:
                rendered_to_original[rendered_index] = int(str(item["line"]))
            except (TypeError, ValueError):
                continue
            continue
        try:
            rendered_start = int(str(item["rendered_start_line"]))
            rendered_end = int(str(item["rendered_end_line"]))
            original_start = int(str(item["original_start_line"]))
        except (KeyError, TypeError, ValueError):
            continue
        for offset, rendered_line in enumerate(range(rendered_start, rendered_end + 1)):
            rendered_to_original[rendered_line] = original_start + offset

    if not rendered_to_original:
        return _line_map(rendered_source, source.get("start_line") or 1)

    fallback_line: int | None = (
        None if _as_dict(source.get("source_budget")).get("truncated") else 0
    )
    return [
        {
            "line": rendered_to_original.get(index)
            if index in rendered_to_original
            else (index if fallback_line == 0 else fallback_line),
            "text": line,
        }
        for index, line in enumerate(rendered_lines, start=1)
    ]


def _source_refetch_ref(
    source: dict[str, Any],
    query: str,
    path: str,
    max_files: int,
) -> dict[str, Any]:
    symbol = source.get("symbol") or source.get("name")
    source_path = str(source.get("file") or "").strip()
    refetch_path = source_path or path
    if symbol:
        return _command_ref(["tg", "source", refetch_path, symbol, "--json"])
    return _command_ref([
        "tg",
        "context-render",
        refetch_path,
        query,
        "--json",
        "--max-files",
        max_files,
    ])


def _raw_context_ref(
    query: str,
    path: str,
    *,
    max_files: int,
    max_sources: int,
    max_tokens: int | None,
    max_repo_files: int | None,
    model: str | None,
    semantic_provider: str,
) -> dict[str, Any]:
    argv: list[object] = [
        "tg",
        "context-render",
        path,
        query,
        "--json",
        "--max-files",
        max_files,
        "--max-sources",
        max_sources,
    ]
    if max_tokens is not None:
        argv.extend(["--max-tokens", max_tokens])
    if max_repo_files is not None:
        argv.extend(["--max-repo-files", max_repo_files])
    if model:
        argv.extend(["--model", model])
    if semantic_provider != "native":
        argv.extend(["--provider", semantic_provider])
    return _command_ref(argv)


def _build_snippets(
    payload: dict[str, Any],
    *,
    query: str,
    path: str,
    max_files: int,
    max_tokens: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    snippets: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    used_tokens = 0
    for source in _as_list_of_dicts(payload.get("sources")):
        body = str(source.get("rendered_source") or source.get("source") or "")
        token_estimate = repo_map._estimate_tokens(body)
        source_budget = source.get("source_budget")
        budget_token_estimate = token_estimate
        if isinstance(source_budget, dict) and source_budget.get("truncated"):
            budget_token_estimate = int(
                source_budget.get("original_token_estimate") or token_estimate
            )
        if max_tokens is not None and used_tokens + budget_token_estimate > max_tokens:
            ref = _source_refetch_ref(source, query, path, max_files)
            omitted.append({
                "kind": "source",
                "file": source.get("file"),
                "symbol": source.get("symbol") or source.get("name"),
                "reason": "token budget exhausted",
                "command": ref["command"],
                "argv": ref["argv"],
            })
            continue
        used_tokens += token_estimate
        snippets.append({
            "file": str(source.get("file") or ""),
            "symbol": source.get("symbol") or source.get("name"),
            "start_line": source.get("start_line") or 1,
            "end_line": source.get("end_line") or source.get("start_line") or 1,
            "source": body,
            "line_map": _expanded_line_map(source, body),
            "token_estimate": token_estimate,
            "evidence": ["parser-backed", "heuristic"],
        })
    return snippets, omitted, used_tokens
