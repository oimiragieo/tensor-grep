# tensor-grep — Project Backlog & PR Tracker

> **Canonical prioritized work list.** Kept in sync with the CLI task store (`TaskUpdate`) and
> GitHub (`gh pr list` is the source of truth for PRs). **Subagents:** read this for *what* to work
> and *how*. **CEO status** = summarize the SHIPPING + P0/P1 sections. Update this doc whenever a PR
> opens/merges or the queue changes.

**Process (the standing framework):** deep-dive → Fable audit (find + fix-idea, cite `file:line`) →
Exa recency + competitive research → plan (superpowers) → thinktank/Fable review the plan → **Sonnet
build, TDD** → verify in the REAL venv (`uv run --no-sync`; a worktree "tests pass" is a hypothesis) →
`ruff check` + `ruff format --preview` + `mypy` → codex/Fable review the PR → **PR → drain**
(one-merge-per-publish, push-race rule) → repeat until no issues. Isolate code agents with
`isolation:'worktree'`. Match model to task (haiku scan / sonnet build / opus+fable review-synthesis).
Always run the common-sense gate before pending a question to the CEO.

**Legend:** `P0` ship-blocking / #1 product gap · `P1` HIGH bug / moat · `P2` MED / coherence ·
`P3` LOW / refactor / feature. Status: `[shipping]` open PR · `[ready]` designed, buildable ·
`[research]` needs Exa/Fable/profile first · `[blocked]` gated · `[done]`.

---

## SHIPPING — open PRs (drain one-per-publish)
| PR | Fix | Files | Verified |
|----|-----|-------|----------|
| #479 | #37 — make public-docs governance reads cwd-independent (kills the ordering-pollution flake) | test_public_docs_governance | 43t repo-root AND foreign-cwd (reproduced then fixed the flake) |
| #480 | #88 — bound the bare `--glob` walk when no PATH given (broad globs refuse fast, were exit-124 hangs) | main.rs, main.py, mcp_server | 684 py + 70 rust + real-binary dogfood; **adversarial Opus gate RUNNING** (touches mcp_server) |

**Push-race (2026-07-09):** **v1.54.3 mid-publish** (from #478/#52 deadline). #479/#480 wait for the `chore(release): v1.54.3` stamp + PyPI. #480 ALSO waits on its Opus gate PASS.

**MERGED this session (live/publishing):** #84 (validation-plan parity → v1.54.2) · #476 (docs/backlog reconcile + AGENTS.md doc-drift + skill accuracy) · #477 (#64 index-lock flake-harden) · **#478 (#52 --deadline hard wall-clock bound, 354s→10s, real-binary proven → v1.54.3)**.

**Deep-dive #81 audit drain (v1.51.x) — 100% MERGED + LIVE:** #455/#457/#460-#470 (14 findings; every security PR passed the adversarial Opus gate).

**Late-rerank feature (task #86, the #1 ColGrep competitive response) — T0-T6 MERGED (default-OFF behind `TG_LATE_RERANK`):** #471-#473 + #474 (#87 fetch total-deadline). **T7-T10 remaining** (golden-set ship gate is the decision).

---

## CURRENT LIVE BACKLOG (2026-07-09, refreshed) — action after the queue drains

### Agentic audit (CEO-relayed 2026-07-09) — the "make models prefer tg" P0s + SaaS thesis. Memory: `tensor-grep-agentic-audit-saas-thesis-2026-07-09`. **Phase-0 (#94-98) = same work as the SaaS foundation.** All BUILDS collide with #480 (main.py/mcp_server.py) → gated on #480 harvest; DESIGNS proceed now (read-only).
- `[P0, #94, DESIGN RUNNING]` **Latency — the #1 preference-killer** (6-33s/call, cold-start-dominated; empty-dir orient 6.5s vs native 63ms). (A) daemon-as-default fast path (lazy auto-start; MCP `tg_session_*` bypass it today); (B) collapse the ~270ms 2-chained-bash-shim WSL-probe launcher tax. ALSO fixes flaky #83. Design: agent aa95d03d → `docs/plans/design-tensor-grep-94-*` on return.
- `[P0, #95, DESIGN DONE]` **MCP moat exposure** (highest-leverage): add `tg_orient` tool, `--rank`/`--semantic`/rerank on `tg_search` (+ fix GPU-oversell string), custom rules on `tg_ruleset_scan`, `tg_doctor`+`deadline`. **SECURITY: ~35 MCP tools' primary `path=` UNCONFINED** — build confinement FIRST as the safety floor. Design: `docs/plans/design-tensor-grep-95-mcp-moat-exposure-2026-07-09.md`. NEXT: Opus gate on the design → build.
- `[P0, #96]` **Answer-first payloads + universal `--max-tokens`** on defs/refs/callers (callers = 200KB/464 entries, no output cap; --max-tokens inconsistent). Apply the capsule's omissions/follow_up_reads pattern.
- `[P0/P1, #97]` **help-stability + P1 batch:** bare `tg --help` renders 2 docs (clap vs Typer); exit-2-on-partial ambiguity; GPU-oversell string; `--model` silent no-op; harness_api doc-gen (38/45 tools); MCP path confinement (overlaps #95).
- `[P2, #98]` MCP tool consolidation (45→~10 task-shaped) + git-aware staleness receipts + workspace/multi-repo + the $0 Sverklo file-deps re-run. (AGENTS.md doc-drift half DONE in #476.)
- `[CEO-gated, #99]` **SaaS thesis** — local-first code-intel + governance plane for agent fleets. **CI-bot vs SAST wedge = the CEO's call** (needs design partners). npm never published (registry 404 — public-ship gate).

### 1.54.0 WSL2 dogfood (2026-07-09) — remaining
- `[P1, #89]` **WSL /mnt/c/ path resolution** fails in the native backend (path_not_found on a valid bind-mount). **Needs a Linux/WSL box to repro+verify.**
- `[P1, #90]` **tg scan ast-grep on Linux/WSL** (exit 127 Windows-shim) + doctor false-"available". **Needs a Linux box.**
- `[P2, #91]` `tg search --type <lang> --json` reports `total_matches:0` while plain search finds hits. (Collides with #480 search path.)
- `[P3, #92]` `tg classify --stdin/--text` literal mode.
- `[P2/P3, #93]` dogfood #84 tail: imports/importers dynamic-import awareness · orient suggested_scope hints · unscoped-refuse. (The validation-plan headline shipped in #475/v1.54.2.)

### Feature / other
- `[P1, #86]` **Late-rerank T7-T10** (fresh-session, heavy): T7 latency receipt · **T8 golden-set ship/no-ship gate** (the decision) · T9 8-site `--rerank` registration · T10 docs+NOTICE. Design: `docs/plans/design-tensor-grep-late-rerank-2026-07-09.md`.
- `[P0, #57]` raise the 512 `CALLER_SCAN_FILE_CEILING` — **now SAFE (the #52 deadline bound makes the larger scan interruptible)**; build on top of #478.
- `[re-verify]` #78 ReDoS simple-path residual + #76-pt2 islice giant-line bound (batch, gated). **#76 read-path exfil DONE** (#464/#469). #52/#64/#37/#84 DONE.
- `[P3]` flaky #83 (root cause = native startup latency, fixed by #94 — sidecar-timeout hypothesis REFUTED).
- `[CEO-gated]` #72 benchmark publish · #77 tg-ledger go/no-go.
- `[stale-WIP]` `tensor-grep-deweight` worktree (auto-deweight vendored trees, v1.40.3 base) — revive-or-retire next campaign.

> **NOTE (2026-07-09):** the P0/P1 sections below are from the 2026-07-07 v1.45.x campaign — MANY releases have shipped since (now v1.51.9). Re-verify each against current code before actioning; several (PERF parse-cache #432, the repo_map cluster) likely shipped in the v1.46-1.51 line. `gh pr list --state merged` + `git log` are authoritative.

---

## P0 — the #1 product gap
- `[SHIPPED — PR #432, draining]` **PERF — repo_map per-(path,mtime) parse-product cache.** One tree-sitter
  parse per (path,mtime) shared across symbol/ref/caller extractors, golden-parity-locked (172 oracle tests
  byte-identical). Measured ~-25% cold render / -45% parse on this repo, larger on TS. **Unblocks raising
  `CALLER_SCAN_FILE_CEILING` (512) — the next repo_map item after #432 merges.** (tasks #52/#57/#61, #65-C1)

## P1 — HIGH bugs / moat (Fable-designed)
**The gated repo_map cluster — all touch `repo_map.py`, so they serialize behind #432 (PERF) merging; build in this order:**
- `[gated on #432, design done]` **Caller-cap raise** — raise `CALLER_SCAN_FILE_CEILING` (512), now safe because
  the parse-cache (#432) makes the re-parse cheap. The dogfood #65-C1 "callers unusable on TypeScript" fix. Build FIRST.
- `[gated on #432, design done — 1D SHIPPED #433]` **1A — evidence-derived seed confidence.** The edit-plan seed
  never emits `confidence.overall`, so the capsule defaults to a fabricated 0.9; emit a real `overall`
  (`min(file,symbol)` = route-test's derivation) + a 0.55 capsule fallback. Spec: `scratchpad/build_spec_1D_pr1.md` (§1A). Build after the cap raise.
- `[gated on #432, design done]` **C-EDGE-1 — caller precision (competitive, dogfood-confirmed).** Name-based
  `tg callers` over-attributes a same-named LOCAL function's calls to the queried symbol (codegraph #67 / mycelium
  Bug 3 class). Fix = 3-tier resolution confidence (import-resolved 0.95 / name-only 0.75 / shadowed-local 0.5) reusing
  `import_graph_consumer_files` + shadow-def set — **recall-preserving** (drops nothing; tg's recall beats resolved-edge
  rivals). Spec: `scratchpad/build_spec_c_edge_1.md`. Build LAST (largest diff). Memory: `tensor-grep-competitive-caller-edgecases-2026-07-07`.
- `[gated — repo_map/agent_capsule]` **T1/T3 — caller-scan honesty + validation_plan population** (dogfood #65).
- `[SHIPPED]` R1 FFI kwargs (#430) · M9 routing-merge (#434) · L7 rg-timeout partial (#435).
- `[correctness MED tail]` M1 unify 3 blast-radius answers, M3 context/inventory/orient exit-2, M5 `--deadline` sibling
  loops (#61), M12 uplift eligibility (repo_map/agent_capsule — gated). **M13 cybert alignment: REJECTED** — a mis-diagnosis;
  `classify()` drop-filtering is the intended, tested contract (no caller zips at nonzero threshold). Do NOT re-attempt.

## P2 — coherence / MED
- `[ready]` **W2/W3/W4** (E2E audit): blast-radius-plan `files`-vs-`affected_files` contradiction (zero-risk);
  session doc-edit absorb (don't invalidate a code session on CLAUDE.md edits); repo-level scope config (`.tensor-grep.toml`) + `--ignore` parity on edit-plan.
- `[research]` **M2 — cold-render latency**: profile to confirm it's the 2000-cap cost, not a regression.
- `[ready]` **R2/R3 — finish the language registry** so the next language (Java) is a pure `lang_*.py` + one
  `register_language()` (no core edits). R6 — Go validation/test-discovery gaps.

## P3 — refactors / LOW / features
- `[ready]` **R4/R5 — safe repo_map split** (strangler-fig behind a facade): extract `validation_commands.py`,
  `trust.py`; `lang_common.py` leaf module to de-dup the 3× byte-identical helpers.
- `[ready]` **correctness LOW tail** L1-L12 + **audit LOWs** (#63: F15/F19/F22/F23/F26).
- `[ready]` **features** (dogfood #65/#35): `tg sweep`, `tg spine`, `tg doc-gap`, progress-NDJSON heartbeat,
  `docs-coverage --suggest-doc`, unified blast-radius mode, agent auto-scale tokens, suggested_scope/ignore.
- `[blocked/CEO-gated]` **strategic** (#62): languages beyond Go (multi-week); semantic-search promotion (evidence-gated).
- Older live tasks still relevant: #17 SafeBackendMixin CI gate, #18 `tg registration-check` CLI, #43 Q5 ReDoS ProcessPool, #45 LSP graph routing, #58 promote `tg route-test`.

---

## SHIPPED — this campaign (v1.45.x, 2026-07-07)
Path A T1 (typed ref_kind) · Stage 0 (lang registry) · Go language · Path B (local hybrid semantic search) ·
Cluster A (MCP unbounded scans) · Cluster B (exit-2 daemon) · F1–F8 dogfood fixes · crossbeam CVE · flaky-test hardening.
Plus the v1.45.x correctness+trust blitz — **MERGED:** #424 H1 · #425 T2 · #426 backends · #427 reliability ·
#428 MCP · #429 backlog-tracker · #430 R1 FFI. **DRAINING (#431–#436):** F15/F23 · PERF parse-cache · 1D truncation ·
M9 mixed-routing · L7 rg-timeout · SESSION_HANDOFF. All verified in the real venv; M13 correctly rejected as a mis-diagnosis.

## References
- Fable fix-queue detail (findings + worktree branches): `scratchpad/fable_blitz_fixqueue_2026-07-07.md` (temp — non-durable).
- Cross-session resume anchor (memory): `tensor-grep-fable-blitz-active-work-2026-07-07.md`.
- Full process rules: [AGENTS.md](https://github.com/oimiragieo/tensor-grep/blob/main/AGENTS.md). Prose narrative log: [SESSION_HANDOFF.md](SESSION_HANDOFF.md).
