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

# Auto de-weight (never hard-exclude) bundled vendor/skill/generated CODE subtrees so `tg orient`/
# `tg agent` surface real product code without a manual `--ignore` (#55 PR6). A subtree fires ONLY on
# STRONG-1 (nested package manifest) AND (STRONG-2 (import island) OR WEAK (name prior)):
#   STRONG-1 -- a directory below the repo root contains its own manifest, reusing the same marker
#     set `_path_has_project_marker` (main.py) uses for broad-scan workspace-project detection.
#   STRONG-2 -- an import island: no file OUTSIDE the subtree resolves an import INTO it (computed
#     from the same resolved-import graph the centrality ranking builds).
#   WEAK -- a name prior (vendor/, third_party/, skills/, external_repos/, _vendored/, node_modules/):
#     a TIE-BREAKER only, never sufficient alone.
# A monorepo subproject that HAS a manifest but IS imported across the repo is protected by STRONG-2
# (not an island) -- de-weight, never exclude, is what keeps a false positive from hiding real product
# code (the file can still surface if it is genuinely central even after the multiplier).
_DEWEIGHT_FACTOR = 0.25
_VENDOR_NAME_PRIOR = frozenset({
    "vendor",
    "third_party",
    "skills",
    "external_repos",
    "_vendored",
    "node_modules",
})

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


def _code_files_and_import_graph(
    rm: dict[str, Any],
) -> tuple[list[str], dict[str, list[str]], dict[str, set[str]]]:
    """Shared code-only import graph (docs/config/data suffixes excluded): returns
    ``(code_files, resolved_imports, reverse_importers)``. Used by both the centrality ranking and
    the vendored-subtree import-island detection so the two heuristics see the identical graph."""
    all_files = [str(f) for f in rm.get("files", [])]
    if not all_files:
        return [], {}, {}
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
    return code_files, resolved_imports, reverse_importers


def _detect_vendored_subtrees(rm: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Auto-detect bundled vendor/skill/generated CODE subtrees to DE-WEIGHT (never hard-exclude).

    Returns ``{tree_path: {"reasons": [...]}}``. Fires ONLY on STRONG-1 (nested package manifest)
    AND (STRONG-2 (import island) OR WEAK (name prior)) -- see the module-level comment above
    ``_DEWEIGHT_FACTOR`` for the full rule. Requires ``rm["path"]`` to point at a real, existing
    directory (a synthetic/relative-path test fixture with no "path" key returns ``{}`` rather than
    guessing against the process CWD)."""
    path_value = rm.get("path")
    if not path_value:
        return {}
    try:
        root = Path(str(path_value)).resolve()
    except OSError:
        return {}
    if not root.is_dir():
        return {}

    all_files = [str(f) for f in rm.get("files", [])]
    if not all_files:
        return {}

    from tensor_grep.cli.main import _BROAD_WORKSPACE_PROJECT_MARKERS

    # Candidate directories: every ancestor (strictly below root) of every scanned file. Bounded by
    # the already-scanned file set -- never an independent filesystem walk.
    candidate_dirs: set[Path] = set()
    for file_str in all_files:
        try:
            rel = Path(file_str).relative_to(root)
        except ValueError:
            continue
        parts = rel.parts[:-1]
        for i in range(1, len(parts) + 1):
            candidate_dirs.add(Path(*parts[:i]))
    if not candidate_dirs:
        return {}

    # STRONG-1: directory contains its own manifest.
    manifest_dirs: dict[Path, str] = {}
    for rel_dir in candidate_dirs:
        abs_dir = root / rel_dir
        for marker in sorted(_BROAD_WORKSPACE_PROJECT_MARKERS):
            try:
                if (abs_dir / marker).exists():
                    manifest_dirs[rel_dir] = marker
                    break
            except OSError:
                continue
    if not manifest_dirs:
        return {}

    # Keep only the OUTERMOST manifest directory in any nested chain -- a deeper manifest inside an
    # already-flagged subtree does not start a second, overlapping subtree.
    subtree_rel_roots: list[Path] = []
    for rel_dir in sorted(manifest_dirs, key=lambda p: len(p.parts)):
        if any(
            _repo_map._path_is_relative_to(root / rel_dir, root / existing)
            for existing in subtree_rel_roots
        ):
            continue
        subtree_rel_roots.append(rel_dir)

    _code_files, _resolved_imports, reverse_importers = _code_files_and_import_graph(rm)
    code_file_set = set(_code_files)

    result: dict[str, dict[str, Any]] = {}
    for rel_dir in subtree_rel_roots:
        abs_dir = root / rel_dir
        tree_files = {f for f in code_file_set if _repo_map._path_is_relative_to(Path(f), abs_dir)}
        if not tree_files:
            continue
        is_island = all(reverse_importers.get(f, set()) <= tree_files for f in tree_files)
        name_hits = sorted({part.lower() for part in rel_dir.parts} & _VENDOR_NAME_PRIOR)

        if not (is_island or name_hits):
            continue

        reasons = [f"nested-manifest:{manifest_dirs[rel_dir]}"]
        if is_island:
            reasons.append("import-island")
        if name_hits:
            reasons.append(f"name-prior:{name_hits[0]}")

        result[str(abs_dir)] = {"reasons": reasons}

    return result


def _file_centrality_scores(rm: dict[str, Any]) -> tuple[list[str], dict[str, float]]:
    """Composite per-file centrality (capped fan-in + fan-out + symbol density) over the non-doc,
    non-config code files in `rm`. Shared by `_central_files_from_map` (top-N central-file ranking)
    and `_suggested_scope_from_map` (directory rollup, audit #93 SUB-2) so both features read off
    the exact same score -- never a second, driftable scoring system."""
    code_files, resolved_imports, reverse_importers = _code_files_and_import_graph(rm)
    if not code_files:
        return [], {}
    code_file_set = set(code_files)
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
    return code_files, centrality


def _central_files_from_map(
    rm: dict[str, Any],
    *,
    max_central_files: int,
    auto_deweight: bool = True,
    deweighted_trees: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Rank source files by import in-degree (foundational = imported-by-many); top-N with symbols.

    Files inside a detected vendored/skill subtree (see ``_detect_vendored_subtrees``) have their
    composite score multiplied by ``_DEWEIGHT_FACTOR`` -- DE-WEIGHTED, never removed, so a genuinely
    central file inside such a tree can still surface. The de-weight is applied HERE (not in
    `_file_centrality_scores`) so `_suggested_scope_from_map` keeps reading the raw, un-de-weighted
    score -- matching the WIP's original scope (central_files only)."""
    code_files, centrality = _file_centrality_scores(rm)
    if not code_files:
        return []
    if deweighted_trees is None:
        deweighted_trees = _detect_vendored_subtrees(rm) if auto_deweight else {}
    tree_roots = list(deweighted_trees.keys())
    for source in list(centrality):
        candidate = Path(source)
        for tree_root in tree_roots:
            try:
                candidate.relative_to(tree_root)
            except ValueError:
                continue
            centrality[source] *= _DEWEIGHT_FACTOR
            break
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


# suggested_scope (audit #93 SUB-2): a truncated scan gives an agent an incomplete map with no
# guidance on how to narrow it. When the top-level-directory rollup of `_file_centrality_scores`
# shows a clear winner, suggest re-scoping to it; a tie or near-tie degrades to None rather than
# guess (ranking-safety-floor discipline, memory: tensor-grep-idf-ranking-fragility-2026-06-29 --
# this inherits the same flat, no-IDF-style composite score as central_files, so a wrong scope
# guess would actively misdirect an agent, which is worse than no hint at all). The margin is a
# ratio, not a fixed delta, so it scales with repos of very different absolute centrality sizes.
_SUGGESTED_SCOPE_MIN_MARGIN_RATIO = 1.5


def _top_level_dir(file_path: str, root: Path) -> str | None:
    """First path component of `file_path` relative to `root`, or None for a file that lives
    directly at the repo root (no subdirectory exists there to re-scope into)."""
    try:
        relative = Path(file_path).relative_to(root)
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 2:
        return None
    return parts[0]


def _suggested_scope_from_map(rm: dict[str, Any]) -> dict[str, Any] | None:
    """Centrality-weighted directory rollup: sum each code file's composite centrality
    (`_file_centrality_scores`) up to its top-level directory, rank directories, and suggest the
    top one only when it clearly outranks the runner-up. Returns None (never a guess) when there
    are no candidate subdirectories, the signal is entirely flat (all zero), or the top two
    directories are tied/near-tied. Callers gate the call itself on the repo map's
    ``scan_limit.possibly_truncated`` -- a complete scan has nothing left to narrow."""
    code_files, centrality = _file_centrality_scores(rm)
    if not code_files:
        return None
    root = Path(str(rm.get("path", ".")))
    dir_scores: dict[str, float] = {}
    for file_path in code_files:
        top_dir = _top_level_dir(file_path, root)
        if top_dir is None:
            continue
        dir_scores[top_dir] = dir_scores.get(top_dir, 0.0) + centrality.get(file_path, 0.0)
    if not dir_scores:
        return None
    ranked_dirs = sorted(dir_scores, key=lambda d: (-dir_scores[d], d))
    top_score = dir_scores[ranked_dirs[0]]
    if top_score <= 0:
        return None  # no signal at all -- nothing to distinguish a "highest-value" directory
    if len(ranked_dirs) > 1:
        runner_up_score = dir_scores[ranked_dirs[1]]
        if runner_up_score > 0 and top_score < runner_up_score * _SUGGESTED_SCOPE_MIN_MARGIN_RATIO:
            return None  # no clear winner -- degrade to null rather than risk a misleading guess
    return {
        "dirs": [str(root / ranked_dirs[0])],
        "confidence": "heuristic",
    }


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
    auto_deweight: bool = True,
) -> dict[str, Any]:
    """Build a bounded codebase orientation capsule (no API key, no GPU).

    ``auto_deweight`` (default on) DE-WEIGHTS -- never hard-excludes -- auto-detected bundled
    vendor/skill/generated CODE subtrees in the centrality ranking (see
    ``_detect_vendored_subtrees``); pass ``auto_deweight=False`` (CLI: ``--no-auto-deweight``) to
    disable. This is independent of ``--ignore``, which still hard-excludes explicit globs."""
    from tensor_grep.cli.repo_map import DEFAULT_AGENT_REPO_MAP_LIMIT

    effective_max_repo_files = (
        max_repo_files if max_repo_files is not None else DEFAULT_AGENT_REPO_MAP_LIMIT
    )
    rm = _repo_map.build_repo_map(path, max_repo_files=effective_max_repo_files)
    rm = _apply_ignore_globs(rm, ignore)

    deweighted_trees = _detect_vendored_subtrees(rm) if auto_deweight else {}
    central_files = _central_files_from_map(
        rm, max_central_files=max_central_files, deweighted_trees=deweighted_trees
    )
    entry_points = _detect_entry_points(rm)

    # suggested_scope (audit #93 SUB-2): gate on the underlying repo map's OWN scan_limit dict
    # (`rm["scan_limit"]["possibly_truncated"]`, set by `repo_map.build_repo_map` -- NOT this
    # capsule's own simplified `scan_limit` int returned below, and NOT the snippet/token-budget
    # `truncated` flag computed further down). A complete scan has no incomplete map to narrow.
    scan_limit_info = rm.get("scan_limit")
    scan_possibly_truncated = bool(
        isinstance(scan_limit_info, dict) and scan_limit_info.get("possibly_truncated")
    )
    suggested_scope = _suggested_scope_from_map(rm) if scan_possibly_truncated else None

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
    budget_truncated = False
    for cf in central_files[:max_snippet_files]:
        file_path = cf["file"]
        snippet_text = _ast_chunked_snippet(file_path, symbol_map.get(file_path, []))
        if not snippet_text:
            continue
        snippet_tokens = _repo_map._estimate_tokens(snippet_text)
        if token_budget_used + snippet_tokens > max_tokens:
            # Budget can't fit this snippet whole -> content is being cut or dropped either way
            # (a partial snippet below, or an outright break). This is the accurate truncation
            # signal; the old `token_budget_used >= max_tokens` proxy false-flagged a snippet that
            # landed EXACTLY on the budget with nothing left to drop.
            budget_truncated = True
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

    deweighted_trees_list = [
        {"path": tree_path, "reasons": list(info["reasons"])}
        for tree_path, info in sorted(deweighted_trees.items())
    ]

    lines: list[str] = [f"# Codebase orientation: {rm['path']}"]
    lines.append("\n## Central files (by import-graph centrality)")
    for cf in central_files:
        lines.append(f"- {cf['file']}  graph_score={cf['graph_score']}")
    if deweighted_trees_list:
        lines.append("\n## De-weighted vendor/skill subtrees (auto-detected, NOT excluded)")
        for tree in deweighted_trees_list:
            lines.append(f"- {tree['path']}  ({', '.join(tree['reasons'])})")
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
    truncated = any(s.get("truncated") for s in snippets) or budget_truncated

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
        "suggested_scope": suggested_scope,
        "routing_reason": "orient",
        "deweighted_trees": deweighted_trees_list,
        "auto_deweight": auto_deweight,
    }


def build_orient_capsule_json(path: str | Path = ".", **kwargs: Any) -> str:
    """JSON form of :func:`build_orient_capsule`."""
    return json.dumps(build_orient_capsule(path, **kwargs), indent=2)
