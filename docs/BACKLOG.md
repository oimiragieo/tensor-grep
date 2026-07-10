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
| #484 | **#95 Part 1** — confine every MCP tool's primary `path=`/`root=`/`file=` to the server root (+ new `TG_MCP_ROOT` override); closes an arbitrary-directory READ across ~35 tools + the `tg_session_file_importers` `/etc` LIVE VULN | mcp_server + 7 test files | 3924 unit + 363 MCP + 2 integration pass, ruff/mypy clean; **adversarial Opus gate = SHIP** (fail-closed, no escape found, 55-case runtime ratchet, `/etc` traced-closed, contract 1.1.0) |

**Push-race (2026-07-10):** **v1.54.6 mid-publish** (from #483/#57 caller-cap). #484 waits for the `chore(release): v1.54.6` stamp + PyPI, then drains one-per-publish. #484 is a deliberate behavior change (out-of-cwd MCP reads now rejected → set `TG_MCP_ROOT`); shipped `feat`/minor with a reviewer-override note, NOT a major bump.

**SHIPPED this session (live on PyPI):** **v1.54.4** (#480 glob/`-t`/`-T`/`--iglob` walk-DoS, 4 adversarial gate rounds) · **v1.54.5** (#482 launcher-shim ~150ms/call) · **v1.54.6 releasing** (#483/#57 `CALLER_SCAN_FILE_CEILING` 512→2000 + 2 latent bugs fixed; real-repo measured +1.8s/~10% for a complete scan). Earlier: v1.54.2 (#84), v1.54.3 (#478/#52 --deadline hard bound).

**BUILDING (background agents):** **#96** answer-first payloads (`--max-tests`/`--max-tokens` + omissions envelope; root-cause = defs/refs dump the whole-repo test manifest) · **#86** late-rerank T7-T10.

**RECOVERY NOTE (2026-07-10):** #95's build agent DIED on a session-usage limit mid-build; its 1268 uncommitted lines were preserved → harvested → real-venv re-verified → gated → PR #484. Session-limit death ≠ lost work.

**Deep-dive #81 audit drain (v1.51.x) — 100% MERGED + LIVE:** #455/#457/#460-#470 (14 findings; every security PR passed the adversarial Opus gate).

**Late-rerank (task #86, the #1 ColGrep competitive response) — T0-T6 MERGED (default-OFF behind `TG_LATE_RERANK`):** #471-#474. **T7-T10 building** (T8 golden-set ship gate = the decision).

---

## CURRENT LIVE BACKLOG (2026-07-09, refreshed) — action after the queue drains

### Agentic audit (CEO-relayed 2026-07-09) — the "make models prefer tg" P0s + SaaS thesis. Memory: `tensor-grep-agentic-audit-saas-thesis-2026-07-09`. **Phase-0 (#94-98) = same work as the SaaS foundation.** The #480 collision cleared (merged as v1.54.4); builds now proceed one-per-file (repo_map.py, mcp_server.py, main.py held by different in-flight items).
- `[P0, #94, Part B SHIPPED / Part A designed]` **Latency — the #1 preference-killer** (6-33s/call). (B) launcher-shim ~150ms tax — **SHIPPED (#482 → v1.54.5).** (A) daemon-as-default fast path — DESIGNED (`docs/plans/design-tensor-grep-94-*`), load-bearing → council/Opus gate; sequenced AFTER Part B gets real-world mileage. ALSO fixes flaky #83.
- `[P0, #95, Part 1 PR'd / Part 2 remains]` **MCP moat exposure** (highest-leverage). **Part 1 = the security floor: global path-confinement — PR #484 (Opus gate SHIP), drains after v1.54.6.** Part 2 (the FEATURES, build after #484 merges): `tg_orient` tool, `--rank`/`--semantic` on `tg_search` (+ GPU-oversell string fix), `tg_doctor`+`deadline`, `inline_rules` custom rules. Design: `docs/plans/design-tensor-grep-95-mcp-moat-exposure-2026-07-09.md`. Gate follow-ups → task #102.
- `[P0, #96, BUILDING]` **Answer-first payloads + universal `--max-tokens`** on defs/refs/callers (callers = 200KB/464 entries, no output cap). Root cause: defs/refs COPY the whole-repo test manifest verbatim (2×). Fix: relevance-filter + extract blast-radius's output-limit helper + omissions envelope. Design: `docs/plans/design-tensor-grep-96-answer-first-payloads-2026-07-09.md`. Build agent running (unblocked by #57 merge).
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
- `[done, #57]` raise the 512 `CALLER_SCAN_FILE_CEILING` → 2000 — **SHIPPED (#483 → v1.54.6);** real-repo measured +1.8s for a complete (was falsely-truncated) scan; +cache-maxsize 1024→2048 + 2 latent bugs fixed.
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
