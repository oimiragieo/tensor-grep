"""Compact MCP tool catalog, Tool Attention (ISO) discovery (arXiv:2604.21816), and schema compaction."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_LEGACY_OPT_IN_ENV = "TG_MCP_LEGACY_TOOLS=1"
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "a",
    "an",
    "and",
    "are",
    "by",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
})

# Format: name | summary | comma_params | tags | 0(always-on)/1(legacy)
_CATALOG_SPEC = """\
tg_navigate|Symbol defs, source, refs, callers, and file imports/importers.|action,symbol,file,path,provider,max_repo_files,deadline|navigate symbol defs source refs callers imports importers|0
tg_impact|Symbol change-impact, blast radius, plan, and rendered bundle.|action,symbol,path,max_depth,max_files,max_tokens,deadline|impact blast radius callers transitive dependencies plan render|0
tg_query|Text regex, AST structural, whole-repo semantic find, or trigram index search.|action,pattern,query,lang,path,workspace_roots,citations|query search text regex ast find semantic index trigram|0
tg_context|Ranked edit-planning context pack, edit plan, render, or agent capsule.|action,query,path,max_files,max_tokens,plan_hops,citations|context pack edit plan render agent capsule|0
tg_explore|Repository orientation, repo map, system diagnostics, and GPU devices.|action,path,max_tokens,max_central_files,with_lsp|explore orient repo map doctor diagnostics gpu devices|0
tg_session|Cached repo-map session lifecycle and session-scoped context/blast radius.|action,session_id,query,symbol,file,path,refresh_on_stale|session cache open list show refresh context blast radius importers|0
tg_scan|Run built-in or inline ast-grep security ruleset scans and list rulesets.|action,ruleset,inline_rules,path,language,baseline_path|scan ruleset security lint ast-grep compliance|0
tg_audit|Verify rewrite audit manifests, list history, diff manifests, and review bundles.|action,manifest_path,previous_manifest,current_manifest,bundle_path|audit manifest verify history diff review bundle|0
tg_checkpoint|Create, list, and undo workspace edit checkpoints.|action,checkpoint_id,path,label|checkpoint create list undo rollback snapshot|0
tg_rewrite|Plan, apply, or diff native AST structural code rewrites.|action,pattern,replacement,lang,path,verify,checkpoint|rewrite ast plan apply diff refactor|0
tg_mcp_capabilities|Report MCP tool availability, contract version, and runtime.||capabilities runtime native embedded version tools|0
tg_classify_logs|Classify anomalies and patterns in a log file.|file_path,structured_json|classify logs anomalies errors diagnostics|0
tg_batch|Execute up to 16 search, find, defs, refs, callers, or source queries concurrently.|queries,path,citations,item_deadline,deadline|batch parallel concurrent search find defs refs callers source|0
tg_coverage|Report repo-map session and AST-cache coverage inventory.|path|coverage cache inventory stale unindexed|0
tg_discover_tools|Search the full MCP tool catalog by natural-language intent.|intent,limit|discover tools catalog search intent legacy|0
tg_rulesets|List built-in security rulesets.||rulesets scan|1
tg_ruleset_scan|Run built-in or inline ruleset scan.|ruleset,inline_rules,path|ruleset scan security|1
tg_repo_map|Return deterministic repository inventory.|path,max_repo_files|repo map inventory|1
tg_orient|One-call orientation capsule for an unfamiliar repo.|path,max_tokens|orient summary|1
tg_doctor|System, GPU, cache, AST, and LSP diagnostics.|path,config,with_lsp|doctor diagnostics|1
tg_context_pack|Ranked repository context pack.|query,path,max_tokens|context pack|1
tg_edit_plan|Machine-readable edit-planning bundle.|query,path,max_files,max_tokens|edit plan|1
tg_context_render|Prompt-ready rendered context bundle.|query,path,max_files,citations|context render|1
tg_agent_capsule|Agent context capsule with optional hop planning.|query,path,max_tokens,plan_hops,citations|agent capsule context|1
tg_session_edit_plan|Cached-session edit-planning bundle.|session_id,query,path|session edit plan|1
tg_session_context_render|Cached-session rendered context bundle.|session_id,query,path|session context render|1
tg_session_blast_radius|Cached-session symbol blast radius.|session_id,symbol,path,max_depth|session blast radius|1
tg_symbol_blast_radius_plan|Machine-readable symbol blast-radius plan.|symbol,path,max_depth|symbol blast radius plan|1
tg_session_blast_radius_render|Cached-session rendered blast-radius bundle.|session_id,symbol,path,max_depth|session blast radius render|1
tg_session_blast_radius_plan|Cached-session blast-radius planning bundle.|session_id,symbol,path,max_depth|session blast radius plan|1
tg_symbol_defs|Exact definition locations for a symbol.|symbol,path,provider|symbol defs definition|1
tg_symbol_source|Exact source blocks for a symbol definition.|symbol,path,provider|symbol source definition|1
tg_symbol_impact|Likely impacted files and tests for a symbol.|symbol,path,deadline|symbol impact tests|1
tg_symbol_refs|References to a symbol across the repo.|symbol,path,deadline|symbol refs references|1
tg_symbol_callers|Call sites of a symbol and impacted tests.|symbol,path,deadline|symbol callers call sites|1
tg_file_imports|Imports of a single file.|file|file imports dependencies|1
tg_file_importers|Files that import a single file.|file,path,deadline|file importers reverse dependencies|1
tg_session_file_importers|Cached-session files importing a file.|session_id,file,path|session file importers|1
tg_symbol_blast_radius|Callers plus transitive file/test blast radius.|symbol,path,max_depth,deadline|symbol blast radius callers transitive|1
tg_symbol_blast_radius_render|Prompt-ready symbol blast-radius bundle.|symbol,path,max_depth,max_files|symbol blast radius render|1
tg_search|Regex or literal text search returning structured JSON.|pattern,path,glob,citations|search grep text regex|1
tg_ast_search|Structural AST search using ast-grep/tree-sitter.|pattern,lang,path|ast search structural|1
tg_find|Whole-repo hybrid semantic search.|query,path,limit,max_tokens,citations|find semantic bm25 search|1
tg_devices|Routable GPU inventory.|json_output|devices gpu hardware|1
tg_audit_manifest_verify|Verify rewrite audit manifest digest and signature.|manifest_path,signing_key|audit manifest verify|1
tg_audit_history|List audit manifest history.|path|audit history|1
tg_audit_diff|Diff two audit manifest JSON files.|previous_manifest,current_manifest|audit diff|1
tg_review_bundle_create|Create review bundle with audit and scan artifacts.|manifest_path,scan_path,output_path|review bundle create|1
tg_review_bundle_verify|Verify review bundle integrity and checksums.|bundle_path|review bundle verify|1
tg_checkpoint_create|Create an edit checkpoint.|path,label|checkpoint create|1
tg_checkpoint_list|List edit checkpoints.|path|checkpoint list|1
tg_checkpoint_undo|Undo an edit checkpoint.|checkpoint_id,path|checkpoint undo|1
tg_session_open|Create a cached repo-map session.|path,max_repo_files|session open|1
tg_session_list|List cached repo-map sessions.|path|session list|1
tg_session_show|Show cached repo-map session payload.|session_id,path|session show|1
tg_session_refresh|Refresh a cached repo-map session.|session_id,path|session refresh|1
tg_session_context|Context pack from a cached session.|session_id,query,path|session context|1
tg_rewrite_plan|Native AST rewrite plan.|pattern,replacement,lang,path|rewrite plan ast|1
tg_rewrite_apply|Apply native AST rewrites with optional verify.|pattern,replacement,lang,path,verify|rewrite apply ast|1
tg_index_search|Native trigram index search (native-required).|pattern,path|index search trigram native|1
tg_rewrite_diff|Unified diff preview for native AST rewrites.|pattern,replacement,lang,path|rewrite diff preview|1
"""

# Maps each of the 46 legacy tools to its covering meta-tool action label.
_LEGACY_META_ACTION_MAP: dict[str, str] = {
    "tg_symbol_defs": "defs",
    "tg_symbol_source": "source",
    "tg_symbol_refs": "refs",
    "tg_symbol_callers": "callers",
    "tg_file_imports": "imports",
    "tg_file_importers": "importers",
    "tg_symbol_impact": "impact",
    "tg_symbol_blast_radius": "blast_radius",
    "tg_symbol_blast_radius_plan": "blast_radius_plan",
    "tg_symbol_blast_radius_render": "blast_radius_render",
    "tg_search": "text",
    "tg_ast_search": "ast",
    "tg_find": "find",
    "tg_index_search": "index",
    "tg_context_pack": "pack",
    "tg_edit_plan": "edit_plan",
    "tg_context_render": "render",
    "tg_agent_capsule": "capsule",
    "tg_orient": "orient",
    "tg_repo_map": "repo_map",
    "tg_doctor": "doctor",
    "tg_devices": "devices",
    "tg_session_open": "open",
    "tg_session_list": "list",
    "tg_session_show": "show",
    "tg_session_refresh": "refresh",
    "tg_session_context": "context",
    "tg_session_edit_plan": "edit_plan",
    "tg_session_context_render": "context_render",
    "tg_session_blast_radius": "blast_radius",
    "tg_session_blast_radius_plan": "blast_radius_plan",
    "tg_session_blast_radius_render": "blast_radius_render",
    "tg_session_file_importers": "file_importers",
    "tg_ruleset_scan": "scan",
    "tg_rulesets": "rulesets",
    "tg_audit_manifest_verify": "manifest_verify",
    "tg_audit_history": "history",
    "tg_audit_diff": "diff",
    "tg_review_bundle_create": "bundle_create",
    "tg_review_bundle_verify": "bundle_verify",
    "tg_checkpoint_create": "create",
    "tg_checkpoint_list": "list",
    "tg_checkpoint_undo": "undo",
    "tg_rewrite_plan": "plan",
    "tg_rewrite_apply": "apply",
    "tg_rewrite_diff": "diff",
}

_META_ACTIONS_MAP: dict[str, tuple[str, ...]] = {
    "tg_navigate": ("defs", "source", "refs", "callers", "imports", "importers"),
    "tg_impact": ("impact", "blast_radius", "blast_radius_plan", "blast_radius_render"),
    "tg_query": ("text", "search", "ast", "find", "index"),
    "tg_context": ("pack", "edit_plan", "render", "capsule"),
    "tg_explore": ("orient", "repo_map", "doctor", "devices"),
    "tg_session": (
        "open",
        "list",
        "show",
        "refresh",
        "context",
        "edit_plan",
        "context_render",
        "blast_radius",
        "blast_radius_plan",
        "blast_radius_render",
        "file_importers",
    ),
    "tg_scan": ("scan", "rulesets"),
    "tg_audit": ("manifest_verify", "history", "diff", "bundle_create", "bundle_verify"),
    "tg_checkpoint": ("create", "list", "undo"),
    "tg_rewrite": ("plan", "apply", "diff"),
}

_SESSION_REQUIRED_TOOLS = frozenset({
    "tg_session_show",
    "tg_session_refresh",
    "tg_session_context",
    "tg_session_edit_plan",
    "tg_session_context_render",
    "tg_session_blast_radius",
    "tg_session_blast_radius_plan",
    "tg_session_blast_radius_render",
    "tg_session_file_importers",
})

_SESSION_OPENING_ACTIONS = frozenset({"open", "list"})

_NEXT_STEPS_MAP: dict[str, tuple[str, ...]] = {
    "tg_explore": ("tg_context(action='capsule')", "tg_navigate(action='defs')"),
    "tg_orient": ("tg_context(action='capsule')", "tg_navigate(action='defs')"),
    "tg_repo_map": ("tg_session(action='open')", "tg_context(action='edit_plan')"),
    "tg_navigate": ("tg_impact(action='blast_radius')", "tg_context(action='edit_plan')"),
    "tg_symbol_defs": ("tg_impact(action='blast_radius')", "tg_navigate(action='callers')"),
    "tg_symbol_callers": ("tg_impact(action='blast_radius')",),
    "tg_impact": ("tg_context(action='edit_plan')", "tg_rewrite(action='plan')"),
    "tg_symbol_blast_radius": ("tg_context(action='edit_plan')", "tg_rewrite(action='plan')"),
    "tg_query": ("tg_navigate(action='defs')", "tg_context(action='capsule')"),
    "tg_context": ("tg_checkpoint(action='create')", "tg_rewrite(action='plan')"),
    "tg_session": (
        "tg_session(action='open')",
        "tg_session(action='context', session_id='<id>')",
        "tg_session(action='blast_radius', session_id='<id>')",
    ),
    "tg_session_open": (
        "tg_session(action='context', session_id='<id>')",
        "tg_session(action='blast_radius', session_id='<id>')",
    ),
    "tg_checkpoint": ("tg_rewrite(action='apply')",),
    "tg_checkpoint_create": ("tg_rewrite(action='apply')",),
    "tg_rewrite": ("tg_audit(action='manifest_verify')", "tg_scan(action='scan')"),
    "tg_rewrite_plan": ("tg_rewrite(action='apply')",),
    "tg_rewrite_apply": ("tg_audit(action='manifest_verify')",),
    "tg_scan": ("tg_audit(action='bundle_create')",),
}


def legacy_action_for(tool_name: str) -> str:
    """Return the covering meta-tool action name for a legacy tool."""
    return _LEGACY_META_ACTION_MAP.get(tool_name, "?")


def _strip_schema_titles(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("title", None)
        for value in node.values():
            _strip_schema_titles(value)
    elif isinstance(node, list):
        for item in node:
            _strip_schema_titles(item)


def compact_mcp_tool_schemas(mcp_server_obj: Any) -> None:
    """Strip redundant JSON-schema titles from registered tool input and output schemas."""
    tool_manager = getattr(mcp_server_obj, "_tool_manager", None)
    list_tools = getattr(tool_manager, "list_tools", None)
    if not callable(list_tools):
        return
    for tool in list_tools():
        if isinstance(getattr(tool, "parameters", None), dict):
            _strip_schema_titles(tool.parameters)
        if isinstance(getattr(tool, "output_schema", None), dict):
            _strip_schema_titles(tool.output_schema)


@dataclass(frozen=True)
class _CatalogEntry:
    name: str
    summary: str
    params: tuple[str, ...]
    tags: str
    is_legacy: bool
    actions: tuple[str, ...]
    requires_session: bool
    session_actions_requiring_session: tuple[str, ...]
    next_steps: tuple[str, ...]


def _parse_catalog() -> tuple[_CatalogEntry, ...]:
    rows: list[_CatalogEntry] = []
    for line in _CATALOG_SPEC.strip().splitlines():
        name, summary, params_csv, tags, legacy_flag = line.split("|")
        params = tuple(p for p in params_csv.split(",") if p)
        actions = _META_ACTIONS_MAP.get(name)
        if actions is None:
            action = _LEGACY_META_ACTION_MAP.get(name)
            actions = (action,) if action else ()
        requires_session = name in _SESSION_REQUIRED_TOOLS
        if name == "tg_session":
            session_actions = tuple(a for a in actions if a not in _SESSION_OPENING_ACTIONS)
        elif requires_session:
            session_actions = actions
        else:
            session_actions = ()
        rows.append(
            _CatalogEntry(
                name=name,
                summary=summary,
                params=params,
                tags=tags,
                is_legacy=(legacy_flag == "1"),
                actions=actions,
                requires_session=requires_session,
                session_actions_requiring_session=session_actions,
                next_steps=_NEXT_STEPS_MAP.get(name, ()),
            )
        )
    return tuple(rows)


_CATALOG = _parse_catalog()


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower().replace("_", " "))


def _meaningful_query_tokens(text: str) -> list[str]:
    tokens = _tokenize(text)
    filtered = [tok for tok in tokens if tok not in _STOPWORDS]
    return list(dict.fromkeys(filtered or tokens))


def _entry_doc_tokens(entry: _CatalogEntry) -> list[str]:
    return _tokenize(
        f"{entry.name} {entry.summary} {' '.join(entry.actions)} {entry.tags} {' '.join(entry.params)}"
    )


_DOC_TOKENS: tuple[list[str], ...] = tuple(_entry_doc_tokens(entry) for entry in _CATALOG)
_AVG_DOC_LEN: float = sum(len(doc) for doc in _DOC_TOKENS) / max(1, len(_DOC_TOKENS))


def compute_intent_schema_overlap(query_tokens: Sequence[str], entry: _CatalogEntry) -> float:
    """Compute Tool Attention Intent-Schema Overlap ISO(q, t_i) in [0.0, 1.0].

    Weights matches on structured schema facets (tool name, action verbs, parameter/schema
    keywords) above surface summary prose so decoy tools stuffed with summary keywords
    cannot outrank genuine schema-matched tools.
    """
    unique_q = list(dict.fromkeys(query_tokens))
    if not unique_q:
        return 0.0
    name_tokens = {tok for tok in _tokenize(entry.name) if tok != "tg"}
    action_tokens = set(_tokenize(" ".join(entry.actions)))
    schema_tokens = set(_tokenize(f"{' '.join(entry.params)} {entry.tags}"))
    summary_tokens = set(_tokenize(entry.summary))

    weighted_sum = 0.0
    structural_hits = 0
    for tok in unique_q:
        if tok in name_tokens:
            weighted_sum += 1.0
            structural_hits += 1
        elif tok in action_tokens:
            weighted_sum += 0.85
            structural_hits += 1
        elif tok in schema_tokens:
            weighted_sum += 0.70
            structural_hits += 1
        elif tok in summary_tokens:
            weighted_sum += 0.45

    iso = weighted_sum / len(unique_q)
    if structural_hits == 0 and iso > 0.0:
        # Summary-only overlap without name/action/schema grounding is heavily dampened.
        iso *= 0.4
    return min(1.0, iso)


def _score_entries(
    query_tokens: list[str], entries: Sequence[_CatalogEntry]
) -> tuple[list[float], list[float]]:
    n_docs = len(entries)
    if not query_tokens or n_docs == 0:
        return [0.0] * n_docs, [0.0] * n_docs
    docs = (
        _DOC_TOKENS if entries is _CATALOG else tuple(_entry_doc_tokens(entry) for entry in entries)
    )
    avg_len = sum(len(doc) for doc in docs) / max(1, len(docs))
    k1, b = 1.5, 0.75
    unique_q = dict.fromkeys(query_tokens)
    df: dict[str, int] = {tok: sum(1 for doc in docs if tok in doc) for tok in unique_q}
    bm25_raw: list[float] = []
    iso_scores: list[float] = []
    for entry, doc in zip(entries, docs, strict=True):
        doc_len = len(doc)
        tf_map: dict[str, int] = {}
        for tok in doc:
            tf_map[tok] = tf_map.get(tok, 0) + 1
        score = 0.0
        for tok in unique_q:
            tf = tf_map.get(tok, 0)
            if tf == 0:
                continue
            idf = math.log(1.0 + (n_docs - df[tok] + 0.5) / (df[tok] + 0.5))
            denom = tf + k1 * (1.0 - b + b * (doc_len / avg_len))
            score += idf * (tf * (k1 + 1.0)) / denom
        bm25_raw.append(score)
        iso_scores.append(compute_intent_schema_overlap(list(unique_q), entry))

    max_bm25 = max(bm25_raw, default=0.0)
    combined: list[float] = []
    for entry, bm25, iso in zip(entries, bm25_raw, iso_scores, strict=True):
        bm25_norm = (bm25 / max_bm25) if max_bm25 > 0.0 else 0.0
        score = 0.45 * bm25_norm + 0.55 * iso
        if not entry.is_legacy and score > 0.0:
            score += 0.05
        combined.append(score)
    return combined, iso_scores


def _coerce_extra_entry(raw: Mapping[str, Any]) -> _CatalogEntry:
    name = str(raw.get("name", ""))
    summary = str(raw.get("summary", ""))
    params = tuple(str(p) for p in raw.get("parameters", ()))
    tags = str(raw.get("tags", ""))
    actions = tuple(str(a) for a in raw.get("actions", ()))
    next_steps = tuple(str(s) for s in raw.get("next_steps", ()))
    requires_session = bool(raw.get("requires_session", False))
    session_actions = tuple(
        str(a)
        for a in raw.get(
            "session_actions_requiring_session",
            actions if requires_session else (),
        )
    )
    return _CatalogEntry(
        name=name,
        summary=summary,
        params=params,
        tags=tags,
        is_legacy=bool(raw.get("is_legacy", True)),
        actions=actions,
        requires_session=requires_session,
        session_actions_requiring_session=session_actions,
        next_steps=next_steps,
    )


_MAX_DISCOVER_LIMIT = 20


def discover_mcp_tools(
    intent: str,
    limit: int = 5,
    extra_tools: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Return top matching MCP tools for a natural-language intent as JSON."""
    bounded_intent = intent[:4096] if isinstance(intent, str) else ""
    entries: Sequence[_CatalogEntry] = (
        _CATALOG
        if not extra_tools
        else tuple(_CATALOG) + tuple(_coerce_extra_entry(item) for item in extra_tools)
    )
    safe_limit = 5 if isinstance(limit, bool) or not isinstance(limit, int) else limit
    clamped_limit = max(1, min(safe_limit, _MAX_DISCOVER_LIMIT, len(entries)))
    if not bounded_intent.strip():
        return json.dumps(
            {
                "version": 1,
                "routing_backend": "MCPRuntime",
                "routing_reason": "mcp-tool-discovery",
                "sidecar_used": False,
                "intent": bounded_intent,
                "limit": clamped_limit,
                "total_catalog_tools": len(entries),
                "tools": [],
            },
            indent=2,
        )
    query_tokens = _meaningful_query_tokens(bounded_intent)
    scores, iso_scores = _score_entries(query_tokens, entries)
    ranked = sorted(
        range(len(entries)),
        key=lambda i: (-scores[i], -iso_scores[i], entries[i].is_legacy, entries[i].name),
    )[:clamped_limit]
    tools = [
        {
            "name": entries[i].name,
            "summary": entries[i].summary,
            "parameters": list(entries[i].params),
            "legacy_opt_in_env": _LEGACY_OPT_IN_ENV if entries[i].is_legacy else None,
            "intent_schema_overlap": round(iso_scores[i], 4),
            "requires_session": entries[i].requires_session,
            "session_actions_requiring_session": list(entries[i].session_actions_requiring_session),
            "next_steps": list(entries[i].next_steps),
        }
        for i in ranked
    ]
    return json.dumps(
        {
            "version": 1,
            "routing_backend": "MCPRuntime",
            "routing_reason": "mcp-tool-discovery",
            "sidecar_used": False,
            "intent": bounded_intent,
            "limit": clamped_limit,
            "total_catalog_tools": len(entries),
            "tools": tools,
        },
        indent=2,
    )
