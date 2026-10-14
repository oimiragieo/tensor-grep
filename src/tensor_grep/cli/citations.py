from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import typer


def _explicit_rg_format_requested(
    argv: list[str] | None = None,
    *,
    format_value: str | None = None,
    any_occurrence: bool = False,
) -> bool:
    del format_value
    tokens = list(sys.argv[1:] if argv is None else argv)
    if argv is None:
        if not tokens or tokens[0] != "search":
            return False
        tokens = tokens[1:]
    elif tokens and tokens[0] == "search":
        tokens = tokens[1:]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--format":
            index += 1
            is_rg = index < len(tokens) and tokens[index] == "rg"
            if not any_occurrence or is_rg:
                return is_rg
        if token.startswith("--format="):
            is_rg = token.split("=", 1)[1] == "rg"
            if not any_occurrence or is_rg:
                return is_rg
        index += 1
    return False


def validate_find_citation_modes(citations: bool, ndjson: bool) -> None:
    if citations and ndjson:
        typer.echo("--citations cannot be combined with --ndjson or --format rg", err=True)
        raise typer.Exit(2)


def exit_if_find_citations(
    result: Any, path: str | Path, citations: bool, json_output: bool, ndjson: bool
) -> None:
    if citations:
        raise typer.Exit(emit_find_citations(result, path, json_output, ndjson))


def exit_if_search_citations(
    result: Any, paths: Sequence[str | Path], citations: bool, json_output: bool
) -> None:
    if citations:
        sys.exit(emit_search_citations(result, paths, json_output))


def validate_search_citation_modes(
    citations: bool,
    ndjson: bool,
    explicit_rg_format: bool,
    *,
    files: bool = False,
    files_without_match: bool = False,
    files_with_matches: bool = False,
    debug: bool = False,
    stats: bool = False,
    trace: bool = False,
    count: bool = False,
    count_matches: bool = False,
) -> None:
    if citations and (ndjson or explicit_rg_format):
        typer.echo("--citations cannot be combined with --ndjson or --format rg", err=True)
        raise typer.Exit(2)
    incompatible = [
        flag
        for present, flag in (
            (files, "--files"),
            (files_without_match, "--files-without-match"),
            (files_with_matches, "--files-with-matches"),
            (debug, "--debug"),
            (stats, "--stats"),
            (trace, "--trace"),
            (count, "--count"),
            (count_matches, "--count-matches"),
        )
        if present
    ]
    if citations and incompatible:
        typer.echo(f"--citations cannot be combined with {', '.join(incompatible)}", err=True)
        raise typer.Exit(2)


def validate_search_citation_cli(
    citations: bool,
    ndjson: bool,
    *,
    format_type: str | None,
    files: bool = False,
    files_without_match: bool = False,
    files_with_matches: bool = False,
    debug: bool = False,
    stats: bool = False,
    trace: bool = False,
    count: bool = False,
    count_matches: bool = False,
) -> bool:
    """Return whether an explicit ``--format rg`` was requested (for downstream search routing)."""
    explicit_rg_format = _explicit_rg_format_requested(format_value=format_type)
    validate_search_citation_modes(
        citations,
        ndjson,
        explicit_rg_format or (citations and _explicit_rg_format_requested(any_occurrence=True)),
        files=files,
        files_without_match=files_without_match,
        files_with_matches=files_with_matches,
        debug=debug,
        stats=stats,
        trace=trace,
        count=count,
        count_matches=count_matches,
    )
    return explicit_rg_format


def validate_search_citation_from_locals(scope: dict[str, Any]) -> bool:
    """Search-command wrapper so ``main.py`` stays one physical line for the size ratchet."""
    return validate_search_citation_cli(
        bool(scope["citations"]),
        bool(scope["ndjson"]),
        format_type=scope.get("format_type"),
        files=bool(scope.get("files")),
        files_without_match=bool(scope.get("files_without_match")),
        files_with_matches=bool(scope.get("files_with_matches")),
        debug=bool(scope.get("debug")),
        stats=bool(scope.get("stats")),
        trace=bool(scope.get("trace")),
        count=bool(scope.get("count")),
        count_matches=bool(scope.get("count_matches")),
    )


_MCP_BUILDER_VALUE_NAMES = (
    "query",
    "path",
    "max_files",
    "max_repo_files",
    "max_sources",
    "max_symbols_per_file",
    "max_render_chars",
    "max_tokens",
    "model",
    "optimize_context",
    "render_profile",
    "provider",
    "profile",
    "gpu_device_ids",
    "gpu_timeout_s",
    "deadline",
    "plan_hops",
    "grounding",
    "rerank",
)


def _repository_root(scope_path: Path) -> Path:
    return next(
        (parent for parent in (scope_path, *scope_path.parents) if (parent / ".git").exists()),
        scope_path,
    )


def citation_envelope(
    citations: list[dict[str, Any]],
    *,
    root: str | Path,
    path_anchor: str | Path | None = None,
    result_incomplete: bool = False,
    incomplete_reason: str | None = None,
    omitted: int = 0,
) -> dict[str, Any]:
    scope_path = Path(root).expanduser().resolve()
    if scope_path.is_file():
        scope_path = scope_path.parent
    root_path = _repository_root(scope_path)
    anchor_path = (
        Path(path_anchor).expanduser().resolve() if path_anchor is not None else scope_path
    )
    if anchor_path.is_file():
        anchor_path = anchor_path.parent
    merged: list[dict[str, Any]] = []
    for item in citations:
        raw_path = item.get("path") or item.get("file")
        if not raw_path:
            continue
        file_path = Path(str(raw_path)).expanduser()
        if not file_path.is_absolute():
            file_path = anchor_path / file_path
        resolved_path = file_path.resolve()
        try:
            resolved_path.relative_to(root_path)
        except ValueError:
            continue
        relative = resolved_path.relative_to(root_path).as_posix()
        start = item.get("start_line", item.get("line_number", item.get("line")))
        end = item.get("end_line", start)
        if start is None or end is None or isinstance(start, bool) or isinstance(end, bool):
            continue
        try:
            start_line = int(start)
            end_line = int(end)
        except (TypeError, ValueError):
            continue
        if start_line < 1 or end_line < start_line:
            continue
        current = {"path": relative, "start_line": start_line, "end_line": end_line}
        if (
            merged
            and merged[-1]["path"] == relative
            and start_line <= merged[-1]["end_line"] + 1
            and end_line + 1 >= merged[-1]["start_line"]
        ):
            merged[-1]["start_line"] = min(merged[-1]["start_line"], start_line)
            merged[-1]["end_line"] = max(merged[-1]["end_line"], end_line)
        else:
            merged.append(current)
    return {
        "version": 1,
        "citations": merged,
        "result_incomplete": bool(result_incomplete),
        "incomplete_reason": incomplete_reason,
        "omitted": max(0, int(omitted)),
    }


def result_citations(
    result: Any,
    root: str | Path,
    *,
    path_anchor: str | Path | None = None,
    symbols: bool = False,
    max_results: int | None = None,
    max_files: int | None = None,
) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for match in getattr(result, "matches", ()):
        if getattr(match, "rg_kind", None) == "context":
            continue
        path = getattr(match, "file", None)
        if not path:
            continue
        if max_results is not None and len(citations) >= max_results:
            break
        if path not in seen_files:
            if max_files is not None and len(seen_files) >= max_files:
                continue
            seen_files.add(path)
        item: dict[str, Any] = {
            "path": path,
            "line": getattr(match, "line_number", None),
        }
        if symbols and item["path"] and item["line"]:
            from tensor_grep.cli.ast_enrichment import enrich_match_with_container

            container = enrich_match_with_container(
                str(item["path"]), int(item["line"]), parser_backed_only=True
            )
            if container:
                item["start_line"], item["end_line"] = container["range"]
        citations.append(item)
    omitted = max(0, int(getattr(result, "total_matches", 0) or 0) - len(citations))
    return citation_envelope(
        citations,
        root=root,
        path_anchor=path_anchor,
        result_incomplete=bool(getattr(result, "result_incomplete", False)),
        incomplete_reason=getattr(result, "incomplete_reason", None),
        omitted=omitted,
    )


def result_citations_json(
    result: Any,
    root: str | Path,
    *,
    symbols: bool = False,
    scan_limit: dict[str, Any] | None = None,
    max_results: int | None = None,
    max_files: int | None = None,
) -> str:
    import json

    envelope = result_citations(
        result, root, symbols=symbols, max_results=max_results, max_files=max_files
    )
    if scan_limit and scan_limit.get("possibly_truncated"):
        envelope["result_incomplete"] = True
        envelope["incomplete_reason"] = envelope["incomplete_reason"] or scan_limit.get(
            "truncation_cause", "scan_limit"
        )
    return json.dumps(envelope, indent=2)


def emit_result_citations(
    result: Any,
    root: str | Path,
    *,
    json_output: bool,
    symbols: bool = False,
) -> int:
    typer.echo(
        render_citations(result_citations(result, root, symbols=symbols), json_output=json_output),
        nl=json_output,
    )
    return 2 if bool(getattr(result, "result_incomplete", False)) else (1 if result.is_empty else 0)


def emit_find_citations(result: Any, path: str | Path, json_output: bool, ndjson: bool) -> int:
    if ndjson:
        typer.echo("--citations cannot be combined with --ndjson", err=True)
        raise typer.Exit(2)
    return emit_result_citations(result, path, json_output=json_output, symbols=True)


def emit_search_citations(result: Any, paths: Sequence[str | Path], json_output: bool) -> int:
    roots = [Path(item).expanduser().resolve() for item in paths]
    roots = [item.parent if item.is_file() else item for item in roots]
    try:
        path_anchor = Path(os.path.commonpath([str(item) for item in roots]))
    except ValueError:
        path_anchor = Path.cwd()
    repo_root = _repository_root(path_anchor)
    typer.echo(
        render_citations(
            result_citations(result, repo_root, path_anchor=path_anchor),
            json_output=json_output,
        ),
        nl=json_output,
    )
    return 2 if bool(getattr(result, "result_incomplete", False)) else (1 if result.is_empty else 0)


def _source_match_lines(source: dict[str, Any], query: str) -> list[int]:
    if not query:
        return []
    lines: list[int] = []
    line_map = source.get("line_map")
    if isinstance(line_map, list):
        for item in line_map:
            if isinstance(item, dict) and query.casefold() in str(item.get("text", "")).casefold():
                line = item.get("line")
                if isinstance(line, int) and line >= 1:
                    lines.append(line)
    rendered = source.get("rendered_source")
    if isinstance(rendered, str) and isinstance(line_map, list):
        for rendered_line, text in enumerate(rendered.splitlines(), 1):
            if query.casefold() not in text.casefold():
                continue
            for item in line_map:
                if not isinstance(item, dict):
                    continue
                first = item.get("rendered_start_line")
                last = item.get("rendered_end_line")
                original = item.get("original_start_line")
                original_last = item.get("original_end_line")
                if (
                    isinstance(first, int)
                    and isinstance(last, int)
                    and isinstance(original, int)
                    and isinstance(original_last, int)
                ):
                    if first <= rendered_line <= last:
                        if last - first == original_last - original:
                            lines.append(original + rendered_line - first)
                        else:
                            lines.append(original)
    deduped: list[int] = []
    seen: set[int] = set()
    for line in lines:
        if line not in seen:
            seen.add(line)
            deduped.append(line)
    return deduped


def _source_citations(
    source: dict[str, Any], root: str | Path, query: str
) -> tuple[list[dict[str, Any]], int]:
    source_path = source.get("file") or source.get("path")
    if not source_path:
        return [], 0
    root_path = Path(root).expanduser().resolve()
    if root_path.is_file():
        root_path = root_path.parent
    path = Path(str(source_path)).expanduser()
    if not path.is_absolute():
        path = root_path / path
    path = path.resolve()
    try:
        path.relative_to(root_path)
    except ValueError:
        return [], 0
    from tensor_grep.cli.ast_enrichment import enrich_match_with_container

    match_lines = _source_match_lines(source, query)
    if not match_lines:
        start = source.get("start_line")
        end = source.get("end_line")
        if not (
            (source.get("symbol") or (source.get("name") and source.get("kind")))
            and type(start) is int
            and type(end) is int
            and 1 <= start <= end
        ):
            return [], 0
        container = enrich_match_with_container(path, start, parser_backed_only=True)
        if not container or container["range"] != [start, end]:
            return [], 0
        return [{"path": str(path), "start_line": start, "end_line": end}], 0

    citations: list[dict[str, Any]] = []
    seen_ranges: set[tuple[str, int, int]] = set()
    for line in match_lines:
        container = enrich_match_with_container(path, line, parser_backed_only=True)
        start, end = container["range"] if container else (line, line)
        key = (str(path), start, end)
        if key in seen_ranges:
            continue
        seen_ranges.add(key)
        citations.append({"path": str(path), "start_line": start, "end_line": end})
    return citations, 0


def _source_citation(source: dict[str, Any], root: str | Path, query: str) -> dict[str, Any] | None:
    citations, _hidden = _source_citations(source, root, query)
    return citations[0] if citations else None


def payload_citations(payload: dict[str, Any], root: str | Path) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    unsupported = 0
    query = str(payload.get("query") or "")
    sources = payload.get("sources") or payload.get("snippets")
    if isinstance(sources, list):
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_citations, _hidden = _source_citations(source, root, query)
            if source_citations:
                citations.extend(source_citations)
            else:
                unsupported += 1
    if not citations:
        primary = payload.get("primary_target")
        if isinstance(primary, dict):
            source_citations, _hidden = _source_citations(primary, root, query)
            if source_citations:
                citations.extend(source_citations)
            else:
                unsupported += 1
    incomplete = bool(payload.get("result_incomplete", payload.get("partial", False))) or bool(
        unsupported
    )
    reason = payload.get("incomplete_reason") or payload.get("partial_reason")
    if unsupported and not reason:
        reason = "source_match_evidence_unavailable"
    omissions = payload.get("omissions")
    omitted_value = payload.get("omitted_sources", payload.get("omitted_source_count", 0))
    if isinstance(omissions, dict):
        omitted_value = omissions.get("omitted_section_count", omitted_value)
    omitted = int(omitted_value or 0)
    source_budget = payload.get("source_budget")
    if isinstance(source_budget, dict):
        omitted = max(
            omitted,
            int(source_budget.get("omitted_sources", 0) or 0)
            + int(source_budget.get("omitted_line_count", 0) or 0),
        )
    omitted_sections = payload.get("omitted_sections")
    if isinstance(omitted_sections, list):
        omitted = max(omitted, len(omitted_sections))
    if isinstance(sources, list):
        omitted = max(
            omitted,
            sum(
                int(source.get("focus", {}).get("omitted_line_count", 0) or 0)
                for source in sources
                if isinstance(source, dict) and isinstance(source.get("focus"), dict)
            ),
        )
    omitted += unsupported
    return citation_envelope(
        citations,
        root=root,
        result_incomplete=incomplete,
        incomplete_reason=reason if isinstance(reason, str) else None,
        omitted=omitted,
    )


def payload_citations_json(payload: dict[str, Any], root: str | Path) -> str:
    import json

    return json.dumps(payload_citations(payload, root), ensure_ascii=False, indent=2)


def serialize_mcp_payload(
    payload: dict[str, Any],
    root: str | Path,
    *,
    citations: bool,
    inject_contract_fields: Any,
) -> str:
    import json

    if citations:
        return payload_citations_json(payload, root)
    return cast(str, inject_contract_fields(json.dumps(payload, indent=2)))


def invoke_mcp_payload_builder(
    builder: Any,
    values: dict[str, Any],
    root: str | Path,
    *,
    citations: bool,
    inject_contract_fields: Any,
    aliases: dict[str, str] | None = None,
) -> str:
    names = inspect.signature(builder).parameters
    aliases = aliases or {}
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in names.values()):
        arguments = {name: values[name] for name in _MCP_BUILDER_VALUE_NAMES if name in values}
        for name, source in aliases.items():
            if source in arguments:
                arguments[name] = arguments.pop(source)
    else:
        arguments = {
            name: values[source] for name in names if (source := aliases.get(name, name)) in values
        }
    return serialize_mcp_payload(
        builder(**arguments),
        root,
        citations=citations,
        inject_contract_fields=inject_contract_fields,
    )


def mcp_context_render_call(_self: Any, scope: dict[str, Any], path: str, citations: bool) -> str:
    return invoke_mcp_payload_builder(
        _self.build_context_render,
        scope,
        path,
        citations=citations,
        inject_contract_fields=_self._inject_mcp_contract_fields,
        aliases={"semantic_provider": "provider"},
    )


def mcp_agent_capsule_call(
    build_agent_capsule: Any, _self: Any, scope: dict[str, Any], path: str, citations: bool
) -> str:
    return invoke_mcp_payload_builder(
        build_agent_capsule,
        scope,
        path,
        citations=citations,
        inject_contract_fields=_self._inject_mcp_contract_fields,
        aliases={"semantic_provider": "provider", "deadline_seconds": "deadline"},
    )


def serialize_mcp_find_result(
    result: Any,
    root: str | Path,
    query: str,
    *,
    citations: bool,
    inject_contract_fields: Any,
) -> str:
    import json

    if citations:
        return result_citations_json(result, root, symbols=True)
    # Share the CLI serializer for ranking, partial results, and grounding evidence.
    from tensor_grep.cli.formatters.json_fmt import JsonFormatter

    payload = json.loads(JsonFormatter().format(result))
    payload = {"query": query, "path": str(root), **payload}
    return cast(str, inject_contract_fields(json.dumps(payload, indent=2)))


def serialize_workspace_citations(
    results_by_root: dict[str, Any], omitted_roots: list[str], root: str | Path
) -> str:
    import json

    scope_path = Path(root).expanduser().resolve()
    if scope_path.is_file():
        scope_path = scope_path.parent
    repository_root = next(
        (parent for parent in (scope_path, *scope_path.parents) if (parent / ".git").exists()),
        scope_path,
    )
    citations: list[dict[str, Any]] = []
    incomplete = bool(omitted_roots)
    reason: str | None = "deadline" if omitted_roots else None
    omitted = len(omitted_roots)
    for workspace_root, payload in results_by_root.items():
        if not isinstance(payload, dict):
            incomplete = True
            reason = str(payload) if payload else "workspace_error"
            omitted += 1
            continue
        if isinstance(payload.get("error"), dict) and payload["error"]:
            return json.dumps(payload, indent=2)
        for item in payload.get("citations", ()):
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                item_path = Path(item["path"])
                if not item_path.is_absolute():
                    repository_path = repository_root / item_path
                    item_path = (
                        repository_path
                        if repository_path.is_relative_to(Path(workspace_root))
                        else Path(workspace_root) / item_path
                    )
                citations.append({**item, "path": str(item_path)})
        incomplete = incomplete or bool(payload.get("result_incomplete"))
        if reason is None and isinstance(payload.get("incomplete_reason"), str):
            reason = payload["incomplete_reason"]
        omitted += int(payload.get("omitted", 0))
    return json.dumps(
        citation_envelope(
            citations,
            root=root,
            result_incomplete=incomplete,
            incomplete_reason=reason,
            omitted=omitted,
        ),
        indent=2,
    )


def emit_payload_citations(payload: dict[str, Any], root: str | Path, *, json_output: bool) -> None:
    typer.echo(
        render_citations(payload_citations(payload, root), json_output=json_output),
        nl=json_output,
    )


def render_citations(envelope: dict[str, Any], *, json_output: bool) -> str:
    if json_output:
        import json

        return json.dumps(envelope, ensure_ascii=False)
    return "".join(
        f"{item['path']}:{item['start_line']}-{item['end_line']}\n"
        for item in envelope["citations"]
    )
