# Baseline dev-architecture audit — 2026-09-06

Audited `tensor-grep` against `~/.claude/skills/baseline-dev-architecture/SKILL.md` (the 9
defaults + AI anti-pattern table). Findings below; only the low-risk one was fixed this turn.

## Fixed this turn

**Root scratch sprawl (AI anti-pattern, confirmed).** 7 stale `.tmp_files_without_match_repro*`
directories (dated April 2026, 2 trivial fixture files each), `.tmp_council_...`-adjacent
`scratch_u2028.txt`, and an 80KB stale `.tmp_ruff_format_check.txt` dump (Aug 30) sat at the repo
root. Verified all were git-untracked (`git ls-files` empty match) and unreferenced by any
tracked test or source file (`grep -rn` empty) before deleting. Left two permission-restricted
directories (`.pytest_tmp_review_...`, `.tmp_council_20260803_f2`) untouched — unclear ownership,
higher risk, not worth forcing.

## Real findings, deferred with reasoning (not blind-fixed)

1. **God-directory / god-files in `src/tensor_grep/cli/`** — `repo_map.py` (15,210 lines),
   `main.py` (13,523), `mcp_server.py` (5,701) sit in one flat, layer-first `cli/` folder mixing
   dozens of unrelated capabilities (session daemon, ledger, checkpoint store, doctor report,
   codemap, LSP provider, MCP tool families). This matches the skill's "layer-first folders that
   scatter a feature" and "god-files that only grow" anti-patterns exactly.
   **Why not fixed now:** this is not undiscovered debt — it's an actively-managed, ratcheted
   campaign already in motion (`docs/design/2026-08-19-split-floor-escape.md`, the
   `file-size-budget`/`bare-call-ratchet` gates, and the `file-split-package-pattern` skill). Two
   extractions happened THIS SESSION as part of that exact campaign (`session_resume_service.py`,
   `reranker.py` additions). A rushed, out-of-process split now would fight the same Python
   bare-call-to-monkeypatched-symbol constraint that campaign is deliberately working through in
   small, test-verified steps. Recommend continuing that campaign, not restarting it.

2. **No Python import-boundary linter** (`import-linter` / `pytestarch`) — the skill is explicit
   that Python's `_private`/`__all__` conventions enforce *nothing*; without a linter, the
   `cli`/`core`/`backends`/`io` split is a naming convention, not an enforced boundary.
   **Why not fixed now:** the skill's own adoption guidance for an EXISTING codebase is
   "freeze, then burn down" — turn on the linter with a baseline file capturing every current
   violation so CI fails only on NEW ones, never retrofit-and-fix in one pass (a raw enable would
   likely surface hundreds of pre-existing violations across a 100K+-line codebase and get
   disabled within a week). This is real, multi-step work (generate baseline, wire CI, socialize
   the ratchet) that deserves its own backlog item and PR, not a rushed same-turn change.
   **Recommendation:** add as a new backlog item — "Adopt `import-linter` with a frozen baseline
   for `src/tensor_grep`'s module boundaries" — scoped as its own AI-doable unit.

## Not re-flagged (already governed)

- Duplicate-version files (`_v2`, `_old`, `.bak`): none found at a repo-wide grep.
- Convenience-typed public boundaries (bare `except Exception` in public signatures): this repo
  already has a dedicated, actively-enforced ratchet for this exact anti-pattern
  (`tests/unit/test_silent_failure_hardening.py`, `TOTAL_BROAD_HANDLERS_CEILING`) — stricter than
  the generic skill guidance, not weaker. No action needed.
- Dead code / commented-out blocks: not swept this pass (would need a dedicated `scan-before-declaring-dead` pass, out of scope for this audit's time budget).
