"""Compact MCP tool catalog and BM25 progressive discovery (arXiv:2602.14878)."""

from __future__ import annotations

import json
import math
import re

_LEGACY_OPT_IN_ENV = "TG_MCP_LEGACY_TOOLS=1"
_TOKEN_RE = re.compile(r"[a-z0-9]+")

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


def _parse_catalog() -> tuple[tuple[str, str, tuple[str, ...], str, bool], ...]:
    rows: list[tuple[str, str, tuple[str, ...], str, bool]] = []
    for line in _CATALOG_SPEC.strip().splitlines():
        name, summary, params_csv, tags, legacy_flag = line.split("|")
        params = tuple(p for p in params_csv.split(",") if p)
        rows.append((name, summary, params, tags, legacy_flag == "1"))
    return tuple(rows)


_CATALOG = _parse_catalog()


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower().replace("_", " "))


_DOC_TOKENS: tuple[list[str], ...] = tuple(
    _tokenize(f"{name} {summary} {tags} {' '.join(params)}")
    for name, summary, params, tags, _ in _CATALOG
)
_AVG_DOC_LEN: float = sum(len(doc) for doc in _DOC_TOKENS) / max(1, len(_DOC_TOKENS))


def _bm25_scores(query_tokens: list[str]) -> list[float]:
    n_docs = len(_CATALOG)
    if not query_tokens or n_docs == 0:
        return [0.0] * n_docs
    k1, b = 1.5, 0.75
    unique_q = dict.fromkeys(query_tokens)
    df: dict[str, int] = {tok: sum(1 for doc in _DOC_TOKENS if tok in doc) for tok in unique_q}
    scores: list[float] = []
    for idx, doc in enumerate(_DOC_TOKENS):
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
            denom = tf + k1 * (1.0 - b + b * (doc_len / _AVG_DOC_LEN))
            score += idf * (tf * (k1 + 1.0)) / denom
        if not _CATALOG[idx][4] and score > 0.0:
            score += 0.05
        scores.append(score)
    return scores


_MAX_DISCOVER_LIMIT = 20


def discover_mcp_tools(intent: str, limit: int = 5) -> str:
    """Return top matching MCP tools for a natural-language intent as JSON."""
    bounded_intent = intent[:4096] if isinstance(intent, str) else ""
    safe_limit = 5 if isinstance(limit, bool) or not isinstance(limit, int) else limit
    clamped_limit = max(1, min(safe_limit, _MAX_DISCOVER_LIMIT, len(_CATALOG)))
    if not bounded_intent.strip():
        return json.dumps(
            {
                "version": 1,
                "routing_backend": "MCPRuntime",
                "routing_reason": "mcp-tool-discovery",
                "sidecar_used": False,
                "intent": bounded_intent,
                "limit": clamped_limit,
                "total_catalog_tools": len(_CATALOG),
                "tools": [],
            },
            indent=2,
        )
    scores = _bm25_scores(_tokenize(bounded_intent))
    ranked = sorted(
        range(len(_CATALOG)),
        key=lambda i: (-scores[i], _CATALOG[i][4], _CATALOG[i][0]),
    )[:clamped_limit]
    tools = [
        {
            "name": _CATALOG[i][0],
            "summary": _CATALOG[i][1],
            "parameters": list(_CATALOG[i][2]),
            "legacy_opt_in_env": _LEGACY_OPT_IN_ENV if _CATALOG[i][4] else None,
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
            "total_catalog_tools": len(_CATALOG),
            "tools": tools,
        },
        indent=2,
    )
