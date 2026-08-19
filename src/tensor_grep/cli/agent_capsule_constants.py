from __future__ import annotations

import builtins
import keyword
import re

_CAPSULE_LSP_CONFIDENCE_BOOST_ENV = "TG_CAPSULE_LSP_CONFIDENCE_BOOST"
_CAPSULE_LSP_CONFIDENCE_CAP = 0.85
_CAPSULE_LSP_CONFIDENCE_LANGUAGES = {"javascript", "php", "python", "rust", "typescript"}

# dogfood finding 1: `tg agent`'s CLI front door defaults --deadline to this value (mirrors
# codemap.DEFAULT_CLI_DEADLINE_SECONDS) so a whole-repo call with no explicit --deadline still
# terminates in bounded time. `build_agent_capsule`'s own `deadline_seconds: float | None = None`
# signature default stays unbounded (a direct library call is unaffected) -- only the CLI's cold
# fallback (main.py's `agent()` body, applied AFTER the warm-daemon gate so a default call still
# reaches the daemon -- see that function's own comment) reads this constant.
DEFAULT_AGENT_CLI_DEADLINE_SECONDS = 60.0

# F4: the exact `_build_snippets` omission reason for a source cut by the capsule's OWN token
# budget (agent_capsule.py `_build_snippets`) -- distinct from the generic "not present in
# capsule snippets" fallback `_capsule_context_consistency` uses when the primary file never
# appeared among the rendered sources at all (a genuine ranking miss, not a budget cut).
_CAPSULE_TOKEN_BUDGET_OMISSION_REASON = "token budget exhausted"
# Historical uplift ceiling for a *corroborated* token-budget-only primary omission. Deliberately
# below the uncapped 0.9 default and matched to the >=0.75 "no ask-user" threshold (agent_capsule.py
# `ask_user_before_editing` construction) -- this is a bounded relief from the 0.55 safety floor,
# not a return to full confidence. Kept for documentation of the original F4 floor; the active
# uplift ceiling used by `_apply_capsule_token_budget_confidence_uplift` is
# `_CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP` below.
_CAPSULE_TOKEN_BUDGET_CONFIDENCE_UPLIFT_CAP = 0.75
# T2: a render-token-budget-only cut (`payload["truncated"]` cut some OTHER, lower-ranked source --
# the primary's OWN snippet still fits) is the SAME class of artifact as the capsule-own-budget
# primary omission above: a render/token-budget signal, not a resolution-quality signal. Once
# blast-radius call-site collection has graph-corroborated the primary, BOTH cases may rise to this
# higher ceiling rather than sitting at the historical 0.75 floor.
_CAPSULE_GRAPH_CORROBORATED_CONFIDENCE_CAP = 0.8
# The exact downgrade-reason strings that mean "confidence was reduced ONLY by a token/render
# budget artifact, not by a genuine resolution-ambiguity signal". Any OTHER downgrade reason
# (language mismatch, validation misalignment, alternative-target tie, marker-helper demotion)
# must disqualify the corroborated-resolution uplift below.
_CAPSULE_BUDGET_ONLY_DOWNGRADE_REASONS = frozenset({
    "primary file omitted from capsule snippets by token budget",
    "context omitted by token or render budget",
})

# PR-1 (1D): a truncated repo SCAN (as opposed to the capsule's own render/token OUTPUT budget) is
# a genuine ambiguity signal, never a budget-only artifact -- deliberately kept OUT of
# `_CAPSULE_BUDGET_ONLY_DOWNGRADE_REASONS` above so the T2 uplift's `other_reasons` scan
# disqualifies the corroborated-resolution uplift even if the dedicated `scan_truncated`
# early-return in `_capsule_token_budget_uplift_eligible` is ever refactored away.
_CAPSULE_SCAN_TRUNCATED_DOWNGRADE_REASON = "repository scan truncated before ranking completed"
_CAPSULE_SCAN_TRUNCATED_ASK_REASON = (
    "repository scan was truncated; the ranked primary may not be the true target"
)

# v20 dogfood gap #2: `_primary_target` below returns an empty `{"file": "", ...}` primary when
# neither `navigation_pack.primary_target` nor `edit_plan_seed.primary_file` resolves -- safe, but
# useless to an agent when the underlying repo SCAN already truncated, since `rm` already holds
# every file/symbol the scan reached before the deadline cut it off. `primary_basis` on the
# primary target marks a heuristic best-effort substitute derived straight from that already-
# scanned data (see `_best_effort_primary_target_from_map`), distinct from a normal ranked
# resolution; the additive `partial_primary` flag is the machine-checkable twin of the same fact.
_BEST_EFFORT_PRIMARY_BASIS = "deadline_truncated_best_effort"
_BEST_EFFORT_PRIMARY_EVIDENCE = "deadline-truncated-best-effort"
# Hard item-count cap (deliberately NOT a live `deadline_monotonic` re-check) for each scoring
# pass inside `_best_effort_primary_target_from_map`: that helper only ever runs AFTER the scan
# has already been marked truncated (see its lone call site in `build_agent_capsule_from_map`), so
# gating it on the deadline again would make the whole block permanently dead code. Bounding by
# item count instead keeps the added cost deterministic and cheap (in-memory string scoring only,
# no I/O, no second scan) regardless of how large the already-capped `rm` still is -- the same
# discipline `_build_context_pack_from_map` applies with a live check at repo_map.py:7862, just
# expressed as a slice since a live check would never let this particular pass run at all.
_BEST_EFFORT_PRIMARY_SCAN_CAP = 500
# Opus-gate nit (SHIP-WITH-NITS, structural-cap hardening): today a best-effort primary lands at
# confidence 0.55 only EMERGENTLY -- because the empty upstream primary happens to force
# `primary_file_included`/snippets-empty style downgrades through `_confidence`'s existing ladder.
# That chain of reasoning is correct today but not guaranteed to stay true (e.g. a future change
# to the render payload shape, or to the T2 corroborated-resolution uplift's own `scan_truncated`
# disqualifier in `_capsule_token_budget_uplift_eligible`, could let a best-effort primary's
# `confidence.overall` climb back to/above the 0.75 no-ask threshold). This cap makes the
# guarantee STRUCTURAL instead: applied unconditionally, LAST, whenever `partial_primary` is set,
# so "partial_primary implies confidence.overall <= 0.55 AND primary_target.confidence <= 0.55"
# holds by construction -- independent of every other confidence computation in this function.
_BEST_EFFORT_PRIMARY_MAX_CONFIDENCE = 0.55


# DAR (Dependency-Aware Retrieval, arxiv steal #4): surface the primary target's OUTBOUND
# dependencies (imports + callees) as budget-isolated related-context, so an agent can edit
# without extra file reads. THE TRAP: `payload["symbols"]`/`payload["imports"]` (the whole-repo
# tables) are POPPED by compact rendering (`repo_map._COMPACT_CONTEXT_RENDER_OMITTED_KEYS`) --
# `build_agent_capsule` always requests `render_profile="full"` + `optimize_context=True`, which
# `repo_map._normalize_render_profile` downgrades to "compact". A naive `payload["imports"]` read
# is therefore SILENTLY EMPTY FOREVER. The data sources below are the ones that survive compact:
# a fresh single-file parse of the primary (`repo_map._imports_and_symbols_for_path`, cached), a
# call-token scan of the primary's OWN rendered snippet source, and the two compact survivors
# `file_summaries` / `candidate_edit_targets.symbols` for resolving callees to file+line.
_CAPSULE_OUTBOUND_DEPENDENCIES_ENV = "TG_CAPSULE_OUTBOUND_DEPS"
_CAPSULE_OUTBOUND_DEPENDENCY_TEXT_PREVIEW_CHAR_LIMIT = 240
_CAPSULE_OUTBOUND_DEPENDENCY_CALL_TOKEN_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_CAPSULE_OUTBOUND_DEPENDENCY_STOPWORDS = frozenset(keyword.kwlist) | frozenset(dir(builtins))
_CAPSULE_OUTBOUND_DEPENDENCY_KIND_PRIORITY = {"call+import": 0, "call": 1, "import": 2}


# CodeAnchor steal (arXiv 2606.26979, "How Much Static Structure Do Code Agents Need?"): render a
# lightweight caller/fan-in fact as an INLINE plain-text comment near the primary target's
# definition, inside the rendered source excerpt itself, rather than requiring a separate tool
# call -- the paper's own finding is that this ambient placement (not a structured sibling field)
# is what drove +3.4pp Pass@1 and halved run-to-run variance, and it directly targets the
# cross-paper "agents skip the graph tool 58% of the time" adoption gap (CodeCompass 2602.20048).
#
# SCOPE (verify-plan-against-code finding): this capsule already collects verified call-site
# evidence for the PRIMARY target ONLY (`_collect_capsule_call_site_evidence[_from_map]`, gated on
# confidence>=0.75 + an explicitly-requested symbol) via a real blast-radius scan the capsule pays
# for regardless of this feature. Annotating that one already-evidenced snippet is therefore a pure
# RENDERING-layer change -- no new graph computation. Annotating every OTHER rendered snippet would
# need a fresh per-symbol blast-radius scan this function does not already run, which is exactly
# the "big new per-symbol computation" this feature deliberately does NOT attempt; scope stays
# primary-target-only rather than forcing that cost.
_CAPSULE_INLINE_CALLER_ANNOTATION_ENV = "TG_CAPSULE_INLINE_CALLERS"
_CAPSULE_INLINE_CALLER_ANNOTATION_TOP_LIMIT = 2
