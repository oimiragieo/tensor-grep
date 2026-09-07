# P14 provenance census (Task 13 checkbox 1)

**Scope of this doc:** inventory only — matches docs/plans/2026-09-07-agentic-quality-simplification.md
Task 13's first checkbox ("Inventory existing per-fact and aggregate evidence first"). No code
changed by this slice; this is the starting-fact-base for the follow-up slices that build the
actual `provenance` envelope.

## What already exists (2026-09-07, verified against `src/tensor_grep/cli/repo_map.py`)

A `"provenance"` **string** field already exists on some result shapes — this is narrower than
the plan's framing might suggest, and the follow-up slice must extend/preserve it rather than
invent a parallel field name:

- `_symbol_navigation_provenance_for_path(path) -> str` (`repo_map.py:2416`) returns one of
  three string values derived from `lang_registry`'s per-language spec:
  - `spec.provenance_when_parsed` — a parser (tree-sitter) is registered and actually parses the
    file at that path.
  - `spec.provenance_when_missing` — a parser is registered for the language but did not parse
    this specific file (e.g. syntax error, unsupported construct).
  - `"heuristic"` — no `LanguageSpec` is registered for the path's extension at all (falls back
    to the text/regex prefilter path).
- Call sites (`repo_map.py:4332`, `:4506`, `:5354`, `:6017`) attach this string to **aggregate
  per-file / per-entry** results (repo-map file entries, symbol-navigation summaries) — not to
  individual `defs`/`refs`/`callers` fact rows.
- A second, unrelated `"provenance"` usage exists at `repo_map.py:2980`/`:2994`
  (`target.get("provenance", "heuristic")`) inside the target-selection/confidence-scoring path
  (AGT-05's territory) — a different consumer of the same string vocabulary, not a fact-level
  envelope either.

## What does NOT exist yet (the actual gap Task 13 targets)

- No `defs`/`refs`/`callers` (or MCP `mcp_symbol_tools.py`) individual result row carries a
  provenance object of any kind — the existing field above is per-file/per-target, not per-fact.
- No `method` (parser vs regex_fallback vs cache-of-parser), `source_revision`
  (snapshot-identity), `freshness` (current vs unknown/stale), or `confidence_kind`
  (evidence-tier, explicitly NOT a fabricated probability) fields exist anywhere in these
  result shapes.
- `mcp_symbol_tools.py` was greped for `provenance`/`confidence_kind`/`regex_fallback` and has
  zero matches — the MCP-facing symbol tools are provenance-blind today.
- No LSP-precise-provider pilot integration exists on the defs/refs/callers path (the existing
  `lsp_external_provider.py` and `benchmarks/run_provider_navigation_bakeoff.py` are reusable
  per the plan but are not currently wired into the everyday symbol-graph query path).

## Existing evidence-tier vocabulary to reuse, not replace

The three-value string (`provenance_when_parsed` / `provenance_when_missing` / `"heuristic"`)
already IS a coarse evidence tier. The follow-up slice's `method`/`confidence_kind` fields
should be defined as a refinement of this existing vocabulary (e.g. `provenance_when_parsed`
maps to `method: "parser"`), not a competing taxonomy — introducing a second incompatible
vocabulary for the same underlying signal would itself be a new drift class.

## What this slice did NOT touch

No production code path was changed. `defs`/`refs`/`callers` result shapes, `mcp_symbol_tools.py`,
and the LSP pilot integration are unstarted and remain open under P14 in `docs/BACKLOG.md`.
