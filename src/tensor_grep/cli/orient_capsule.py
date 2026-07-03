"""One-call codebase orientation capsule (`tg orient`).

Reuses repo_map's import graph (in-degree centrality) + AST symbol-source chunkers to produce a
bounded, AI-readable "explain this repo" capsule: the most central files, entry points, a symbol
map, and AST-boundary snippets within a token budget. Pure-CPU, no API key, no GPU.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map as _repo_map

_CHARS_PER_TOKEN = 3.5

# Documentation suffixes excluded from the code-centrality ranking: a doc file is never a useful
# "central CODE file", and in doc-heavy repos (many cross-linked CLAUDE.md / README) it would
# otherwise dominate the graph and bury the real architecture.
_CENTRAL_DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".adoc", ".txt"})

_ENTRY_NAMES = {
    "main.py",
    "__main__.py",
    "cli.py",
    "app.py",
    "server.py",
    "main.ts",
    "index.ts",
    "index.js",
    "main.js",
    "app.ts",
    "main.rs",
    "lib.rs",
}


def _central_files_from_map(rm: dict[str, Any], *, max_central_files: int) -> list[dict[str, Any]]:
    """Rank source files by import in-degree (foundational = imported-by-many); top-N with symbols."""
    all_files = [str(f) for f in rm.get("files", [])]
    if not all_files:
        return []
    # "Central files" surface CODE architecture. Documentation files (heavily cross-referenced in
    # doc-heavy repos — e.g. 36 CLAUDE.md files) must not rank as central, and must not absorb a code
    # import via a stem collision (config.md shadowing config.py in by_stem). Exclude docs from the
    # graph entirely; fall back to all files only if the repo is pure docs so we still return context.
    code_files = [
        f for f in all_files if Path(f).suffix.lower() not in _CENTRAL_DOC_SUFFIXES
    ] or all_files
    code_file_set = set(code_files)
    imports_by_file: dict[str, list[str]] = {
        str(entry["file"]): [str(i) for i in entry.get("imports", [])]
        for entry in rm.get("imports", [])
        if str(entry["file"]) in code_file_set
    }
    # build_repo_map records imports as module names ("hub"), not file paths ("hub.py"); resolve them
    # to files by stem so the import graph has real edges. Docs are excluded so they cannot shadow a
    # code module.
    by_stem: dict[str, str] = {}
    for source in code_files:
        by_stem.setdefault(Path(source).stem, source)
    resolved_imports: dict[str, list[str]] = {}
    for source, modules in imports_by_file.items():
        targets: list[str] = []
        for module in modules:
            candidate = by_stem.get(module) or by_stem.get(module.split(".")[-1])
            if candidate and candidate != source:
                targets.append(candidate)
        resolved_imports[source] = targets
    reverse_importers = _repo_map._reverse_importers(code_files, resolved_imports)
    # Centrality = in-degree (how many files import this one). NOTE: the reused reverse-import
    # PageRank, seeded by all files, ranks IMPORTERS above the imported -- backwards for "show me the
    # core files" -- so we rank by import in-degree directly.
    centrality = {source: float(len(reverse_importers.get(source, ()))) for source in code_files}
    ranked = sorted(code_files, key=lambda source: (-centrality[source], source))
    result: list[dict[str, Any]] = []
    for file_path in ranked[:max_central_files]:
        file_symbols = [
            {"name": str(s["name"]), "kind": str(s["kind"])}
            for s in rm.get("symbols", [])
            if str(s.get("file")) == file_path
        ][:6]
        result.append({
            "file": file_path,
            "graph_score": round(centrality[file_path], 6),
            "symbols": file_symbols,
        })
    return result


def _detect_entry_points(rm: dict[str, Any]) -> list[dict[str, Any]]:
    """Heuristic: files named main.py / cli.py / index.ts / lib.rs etc."""
    result: list[dict[str, Any]] = []
    for file_path in rm.get("files", []):
        if Path(str(file_path)).name.lower() in _ENTRY_NAMES:
            result.append({"file": str(file_path), "reason": "entry-name-heuristic"})
    return result


def _ast_chunked_snippet(path_str: str, symbols: list[dict[str, Any]]) -> str | None:
    """Return the source of the first resolvable symbol via the AST/regex symbol-source chunkers."""
    path = Path(path_str)
    for sym in symbols:
        name = str(sym.get("name", ""))
        if not name:
            continue
        sources = _repo_map._python_symbol_sources(path, name)
        if not sources:
            sources = _repo_map._js_ts_parser_symbol_sources(path, name)
        if not sources:
            sources = _repo_map._rust_parser_symbol_sources(path, name)
        if not sources:
            sources = _repo_map._regex_symbol_sources(path, name)
        if sources:
            return str(sources[0].get("source", ""))
    return None


def build_orient_capsule(
    path: str | Path = ".",
    *,
    max_central_files: int = 10,
    max_snippet_files: int = 5,
    max_tokens: int = 3000,
    max_repo_files: int | None = None,
    render_profile: str = "compact",
) -> dict[str, Any]:
    """Build a bounded codebase orientation capsule (no API key, no GPU)."""
    from tensor_grep.cli.repo_map import DEFAULT_AGENT_REPO_MAP_LIMIT

    effective_max_repo_files = (
        max_repo_files if max_repo_files is not None else DEFAULT_AGENT_REPO_MAP_LIMIT
    )
    rm = _repo_map.build_repo_map(path, max_repo_files=effective_max_repo_files)

    central_files = _central_files_from_map(rm, max_central_files=max_central_files)
    entry_points = _detect_entry_points(rm)

    symbol_map: dict[str, list[dict[str, Any]]] = {}
    for cf in central_files:
        file_path = cf["file"]
        syms = [
            {
                "name": str(s["name"]),
                "kind": str(s["kind"]),
                "line": int(s.get("line", s.get("start_line", 0)) or 0),
            }
            for s in rm.get("symbols", [])
            if str(s.get("file")) == file_path
        ][:8]
        if syms:
            symbol_map[file_path] = syms

    snippets: list[dict[str, Any]] = []
    token_budget_used = 0
    for cf in central_files[:max_snippet_files]:
        file_path = cf["file"]
        snippet_text = _ast_chunked_snippet(file_path, symbol_map.get(file_path, []))
        if not snippet_text:
            continue
        snippet_tokens = _repo_map._estimate_tokens(snippet_text)
        if token_budget_used + snippet_tokens > max_tokens:
            remaining_chars = int((max_tokens - token_budget_used) * _CHARS_PER_TOKEN)
            if remaining_chars < 80:
                break
            snippets.append({
                "file": file_path,
                "source": snippet_text[:remaining_chars],
                "truncated": True,
            })
            token_budget_used = max_tokens
            break
        snippets.append({"file": file_path, "source": snippet_text, "truncated": False})
        token_budget_used += snippet_tokens

    lines: list[str] = [f"# Codebase orientation: {rm['path']}"]
    lines.append("\n## Central files (by import-graph centrality)")
    for cf in central_files:
        lines.append(f"- {cf['file']}  graph_score={cf['graph_score']}")
    if entry_points:
        lines.append("\n## Entry points (heuristic name detection)")
        for ep in entry_points:
            lines.append(f"- {ep['file']}  ({ep['reason']})")
    lines.append("\n## Symbol map (top symbols per central file)")
    for file_path, syms in symbol_map.items():
        sym_list = ", ".join(f"{s['kind']} {s['name']}" for s in syms)
        lines.append(f"- {file_path}: {sym_list}")
    if snippets:
        lines.append("\n## Key snippets (AST-boundary chunks)")
        for snip in snippets:
            lines.append(f"\n### {snip['file']}")
            lines.append(f"```\n{snip['source'].rstrip()}\n```")

    total_token_estimate = _repo_map._estimate_tokens("\n".join(lines))
    truncated = any(s.get("truncated") for s in snippets) or token_budget_used >= max_tokens

    return {
        "path": rm["path"],
        "central_files": central_files,
        "entry_points": entry_points,
        "symbol_map": symbol_map,
        "snippets": snippets,
        "token_estimate": total_token_estimate,
        "token_budget_label": (
            f"~{total_token_estimate} tokens (heuristic len/3.5); snippet budget {max_tokens}"
        ),
        "truncated": truncated,
        "scan_limit": effective_max_repo_files,
        "routing_reason": "orient",
    }


def build_orient_capsule_json(path: str | Path = ".", **kwargs: Any) -> str:
    """JSON form of :func:`build_orient_capsule`."""
    return json.dumps(build_orient_capsule(path, **kwargs), indent=2)
