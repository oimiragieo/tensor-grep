"""One-call codebase orientation capsule (`tg orient`).

Reuses repo_map's import graph (in-degree centrality) + AST symbol-source chunkers to produce a
bounded, AI-readable "explain this repo" capsule: the most central files, entry points, a symbol
map, and AST-boundary snippets within a token budget. Pure-CPU, no API key, no GPU.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map as _repo_map

_CHARS_PER_TOKEN = 3.5

# Documentation suffixes excluded from the code-centrality ranking: a doc file is never a useful
# "central CODE file", and in doc-heavy repos (many cross-linked CLAUDE.md / README) it would
# otherwise dominate the graph and bury the real architecture.
_CENTRAL_DOC_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".adoc", ".txt"})
# Config/data suffixes also excluded (round-8 audit): a package.json / *.yaml / *.toml / *.lock has
# no import edges and no symbols, so in a config- or doc-heavy "harness" repo it would surface as a
# spurious "central" file over the real code (the recurring dogfood complaint that orient ranks
# non-code as central). build_repo_map's fallback-source set includes these, so they reach here.
_CENTRAL_CONFIG_DATA_SUFFIXES = frozenset({
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".xml",
    ".csv",
    ".lock",
    ".env",
})
# The full non-code exclusion applied to the centrality ranking (docs + config/data).
_CENTRAL_NON_CODE_SUFFIXES = _CENTRAL_DOC_SUFFIXES | _CENTRAL_CONFIG_DATA_SUFFIXES

# Composite-centrality tuning (see _central_files_from_map): cap in-degree so a widely-imported data
# sink can't dominate, and bound symbol density so one giant file can't either.
_CENTRAL_FAN_IN_CAP = 12
_CENTRAL_SYMBOL_DENSITY_CAP = 25

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
    # .tsx entrypoints (React/Ink CLIs): a real CLI entry is often main.tsx/app.tsx, not just the
    # index.ts barrel (dogfood 2026-07-03 — orient listed index.ts barrels, missed main.tsx).
    "main.tsx",
    "app.tsx",
    "cli.tsx",
    "index.tsx",
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
        f for f in all_files if Path(f).suffix.lower() not in _CENTRAL_NON_CODE_SUFFIXES
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
    # Composite centrality (dogfood 2026-07-03, v1.19.9): pure import in-degree surfaced LEAF data
    # files (constants.ts / figures.ts / barrel index.ts imported by many) at the top and buried the
    # real hubs (QueryEngine.ts, state.ts). A real architectural hub both RECEIVES and SENDS import
    # edges AND has substance (many symbols); a data sink only receives. So: cap the in-degree
    # contribution (a file imported by 50 is not proportionally more central than one imported by 12
    # -- past that it is a common utility/constant, not a hub), and ADD fan-out (imports others) +
    # symbol density. This demotes pure sinks without a fragile name/leaf heuristic.
    symbol_counts: dict[str, int] = {}
    for symbol in rm.get("symbols", []):
        symbol_file = str(symbol.get("file"))
        if symbol_file in code_file_set:
            symbol_counts[symbol_file] = symbol_counts.get(symbol_file, 0) + 1
    centrality: dict[str, float] = {}
    for source in code_files:
        fan_in = min(len(reverse_importers.get(source, ())), _CENTRAL_FAN_IN_CAP)
        fan_out = len(resolved_imports.get(source, []))
        density = min(symbol_counts.get(source, 0), _CENTRAL_SYMBOL_DENSITY_CAP)
        centrality[source] = float(fan_in + fan_out + density)
    ranked = sorted(code_files, key=lambda source: (-centrality[source], source))
    result: list[dict[str, Any]] = []
    for file_path in ranked[:max_central_files]:
        file_symbols = [
            {"name": str(s["name"]), "kind": str(s["kind"])}
            for s in rm.get("symbols", [])
            if str(s.get("file")) == file_path
        ][:6]
        rounded_score = round(centrality[file_path], 6)
        result.append({
            "file": file_path,
            # `graph_score` is the composite centrality; `score` is a stable alias so agents that
            # threshold on a generic `score` key find it populated (dogfood v1.20.0: "central_files
            # JSON still has score: null — surface the score so agents can threshold").
            "graph_score": rounded_score,
            "score": rounded_score,
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


def _apply_ignore_globs(rm: dict[str, Any], ignore: tuple[str, ...]) -> dict[str, Any]:
    """Drop files matching any --ignore glob (basename OR repo-relative posix path) from the map
    before ranking (1.35 dogfood): `tg orient . --ignore 'seo/**' 'core/skills/**'` excludes vendor /
    skill trees that would otherwise rank as 'central' on a doc- or harness-heavy repo, even though
    they are .py CODE (so the doc/config suffix exclusions don't catch them)."""
    if not ignore:
        return rm
    root = Path(str(rm.get("path", ".")))

    def _excluded(file_str: str) -> bool:
        candidate = Path(file_str)
        try:
            rel = candidate.relative_to(root).as_posix()
        except ValueError:
            rel = candidate.as_posix()
        return any(
            fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(candidate.name, glob) for glob in ignore
        )

    filtered = dict(rm)
    filtered["files"] = [f for f in rm.get("files", []) if not _excluded(str(f))]
    filtered["symbols"] = [
        s for s in rm.get("symbols", []) if not _excluded(str(s.get("file", "")))
    ]
    filtered["imports"] = [
        i for i in rm.get("imports", []) if not _excluded(str(i.get("file", "")))
    ]
    return filtered


def build_orient_capsule(
    path: str | Path = ".",
    *,
    max_central_files: int = 10,
    max_snippet_files: int = 5,
    max_tokens: int = 3000,
    max_repo_files: int | None = None,
    render_profile: str = "compact",
    ignore: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build a bounded codebase orientation capsule (no API key, no GPU)."""
    from tensor_grep.cli.repo_map import DEFAULT_AGENT_REPO_MAP_LIMIT

    effective_max_repo_files = (
        max_repo_files if max_repo_files is not None else DEFAULT_AGENT_REPO_MAP_LIMIT
    )
    rm = _repo_map.build_repo_map(path, max_repo_files=effective_max_repo_files)
    rm = _apply_ignore_globs(rm, ignore)

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
