# Backlog Closeout Campaign — 2026-08-12 (rev 6, codex-amended x3)

> Rev 6 folds codex r5 findings M5'/M6' (wording precision on rev 5's own folds). Rev 5 hash
> superseded: `2E96E74C882D87B237E40A628995FDECDF8EA5A15FE0C3CDA190B8E485915295`.

> **For agentic workers:** REQUIRED SUB-SKILLS: superpowers:test-driven-development,
> superpowers:executing-plans, tensor-grep-change-control, verify-plan-against-code.
> Work ONE ranked item per iteration. NEVER `git add .` / `git add -A` (shared tree carries
> stale in-flight-looking edits — see WS0). All implementation happens in worktrees; the main
> checkout stays untouched on its stale branch.
>
> Rev 2 folded in: ground-truth seat corrections (all 10 base claims TRUE; 3 numeric/wording
> fixes), adversarial seat F1–F8 (round-1 verdict CHANGES_REQUIRED: F1 F2 F3 F4), and the
> research seat's Part A/B receipts. Rev 3 folds the round-2 APPROVED verdict's LOW advisories
> NEW-1..NEW-4 (pre-cleared to fold without another council round). Superseded hashes:
> rev 1 `5AA5C492A0BD9D12D21E26010AB4089FF296DCF4C4FDCE2B0683CCDBF138341D`,
> rev 2 `DC63D3265A7CAB17AAF3E247034A610D51019A93EAF4E65A4FEA127410EF4261`.
> Council record: adversarial seat APPROVED (rev 2 + fold-ins); ground-truth seat all-TRUE;
> codex cross-vendor seat FAILED on this artifact (wedged on blocked web-search, exit 1, no
> verdict — retried once; a failed seat is recorded, not imitated). The codex seat remains
> MANDATORY and load-bearing where the plan requires it: the WS1 step-5 Sol audit.

**Objective:** drive every live board row to an honest **terminal, advanced, or premise-reverified**
state this campaign: advance the one actionable BLOCKED key (Task 2A), premise-recheck the other
BLOCKED rows, refresh research receipts for all DEMAND_GATED rows with fresh Exa evidence, restate
the 5 CEO_GATED packets (no silent flips), and record the stale-branch reconciliation so no future
session re-chases shipped work. Zero fabricated completions: rows that stay gated are reported
gated, with receipts.

## Ground truth (verified live 2026-08-12; independently re-verified by ground-truth seat)

- `origin/main` = `b6dc0a6` (#1003). Public product **v1.110.14**: local `tg 1.110.14`,
  PyPI serves `1.110.14`, CHANGELOG head `v1.110.14 (2026-08-11)`. No release in flight
  (3 newest main runs completed/success at audit time). Only open PR: **#966** (draft,
  `test:` title, CONFLICTING/DIRTY, head `8f1fc30`).
- Board `docs/TASK_BOARD.md` @ origin/main, index `2026-08-11.1`: **0 READY, 0 IN_FLIGHT,
  6 BLOCKED (#89 #90 F5 F6 F8 MCP-SURFACE), 5 CEO_GATED (#48 #72 #77 #131 #169),
  6 DEMAND_GATED (#255 DD-006 AST-DSL-PARITY MCP-LEAN-DEFAULT CONTINUOUS-REFRESH
  RUST-REPLACE-SYMLINK)**; 7 SHIPPED + 4 RETIRED (machine-counted).
- Local checkout: stale branch `audit/h6-cudf-backend` @ `d9e477b` (base `bb4fdae`, pre-#968),
  21 modified + 17 untracked entries. Reconciliation (receipts in WS0 doc):
  - `git cherry`: H3 `-` (upstream); H6 `+` by patch-id but content IS on main
    (`cudf_backend.py:328-336` normalization; `test_cudf_backend.py:699` "H6 audit").
  - 10 dirty code/test files: 6 byte-identical to main; 4 differ and are BEHIND main
    (63 insertions / 554 deletions gross vs main, **net −491**; worktree blobs are exact
    historical main blobs — receipts per file). The other 11 dirty files (7 skills, AGENTS.md,
    SESSION_HANDOFF, TASK_BOARD, one audit doc) are stale 2026-08-06-era snapshots superseded
    on main. Untracked `tests/unit/test_ast_invert_match_fail_closed.py` is byte-identical
    (blob `5eaa676c`) to main's tracked copy (#976). **Novel unlanded content: NONE.**
    Disposition: preserve untouched; document; cleanup PROPOSED only (except `nul`, see WS0).
- Task 2A: branch head advanced this campaign `8f1fc30` (R5) → union-merge `3e2fe17` → format
  `fbe1128` (pushed; PR #966 flipped CONFLICTING→MERGEABLE). Receipt
  `docs/receipts/task2a-round60-red.md` (#9d) is a **c550a84-era snapshot**: its per-suite
  counts and its "157 nodes (148 py + 9 rust)" census PREDATE the R2–R5 repair commits
  (**ERRATUM-1 / codex H2**). Live census at R5+ head (H1, derived by parsing
  `tests/fixtures/task2a_windows_node_manifest.json`): **169 nodes = 157 python + 12 rust;
  job split test-python 103 / native-build-smoke 66**. The executed positive control is
  therefore a FRESH pre-merge per-node baseline at `8f1fc30`
  (`.orchestrator/t2a_baseline_8f1fc30/`, WSL py3.12 pinned venv), captured BEFORE the merge;
  the receipt-count match arm was expected to fail and did — deltas trace to R2–R5 commit
  diffs, receipts in the campaign log. Drift since merge-base `ac68e62`: 37 merged PRs;
  5-file conflict overlap; only `cli/main.py` conflicted textually (union resolution
  documented at the site).
- Capabilities probed: codex-cli 0.147.0 (Sol seat), cursor-agent 2026.08.11 (WSL, free),
  gh authed, Exa live (17-query wave completed), tg 1.110.14. NOT in this runtime: Claude
  headless fable/opus, CronCreate. **Substitutions (recorded):** fable seat → codex Sol
  read-only + independent verifier subagent; thinktank → council of codex Sol + ≥2 independent
  subagent lenses; CronCreate → `.orchestrator/state.json` persistence only (no cron armed;
  no claim otherwise). **Correlated-risk disclosure:** ≥2 council lenses may share a model
  family; mitigation = cross-vendor codex seat is mandatory for security-class verdicts, and
  only citation-backed findings are actioned (consensus alone promotes nothing).

## Hard stops (binding)

1. No `#89`/`#90`/Task-2A GREEN claim until Sol exact-byte `SHIP` **and** a real Windows CI run
   whose evidence names head SHA + base SHA + the merge-ref checkout SHA (A44/A68 phrasing:
   the PR run tests `merge(head, base@run-time)`, and that is the claim recorded — never
   "the immutable head" alone). RED-by-design suites stay RED.
2. No local `cargo build/test/check/clippy`, no `tests/e2e/test_routing_parity.py` on this
   shared box (W3). Rust compiles happen in PR CI only. `rustfmt --check` allowed.
3. No #169 spend; no public benchmark claim (#72); no GPU asset publish (#131); no ledger
   enforcement (#77); no startup rewrite (#48). CEO_GATED rows get restated packets only.
4. Never stage/modify the 21 pre-existing dirty files or prior sessions' untracked docs; never
   `git add .`; new work only in worktrees; stage explicit paths only.
5. Release discipline: `fix:`/`feat:`/`perf:` release, one-per-publish, newest-main-run
   COMPLETED gate; `docs:`/`test:`/`chore:`/`ci:` batch in green gaps; `refactor:` passes the
   title gate but does NOT publish (angular default). This campaign plans NO releasing merge;
   pushing `task2a-round60-red` updates draft #966 only (ci.yml push-trigger is main-only —
   verified `ci.yml:4-7`).
6. Test hygiene: never run two pytest processes concurrently on this box; wrap every suite in
   a shell timeout with per-test `--timeout` (anti-hang protocol). (Rev 1 cited a nonexistent
   `pytest -m product` marker — that was another repo's convention; corrected.)

## Workstreams (ranked)

### WS0 — Hygiene & reconciliation receipt (docs-only)
1. Author `docs/audits/2026-08-12-stale-branch-reconciliation.md`: cherry receipts, per-file
   diff-vs-main table incl. blob-identity proofs, the 11 stale docs/skills files, untracked
   inventory with per-item disposition (delete PROPOSED / keep), worktree-husk census
   (~44 worktrees; A30 ancestor-check pruning PROPOSED, not executed).
2. BLOCKED-row premise recheck (one command each, results in the same doc): F5/F8 shared-box
   ban → CI/cloud premise still true; **F6 is a MIXED disposition (codex M3, A41): the
   roadmap's own ground-truth correction says F6 is NOT purely rust/e2e-blocked — Python/
   schema/evidence-signing slices are buildable-first (S1's de-block move), only the native
   verify-edit + e2e halves are CI/cloud-routed — the recheck records both halves**;
   MCP-SURFACE: `_TG_MCP_SERVER_CONTRACT_VERSION` still `1.7.0` on origin/main; #89/#90
   owned by Task 2A→2B/2C (advanced by WS1 this campaign).
3. Sole permitted working-tree mutation in the main checkout: remove the `nul` artifact via
   Git Bash `rm -f ./nul` or `Remove-Item -LiteralPath "\\?\C:\dev\projects\tensor-grep\nul"`
   (NEW-2: plain `Remove-Item ./nul` cannot address the reserved device name; `Test-Path`
   false-negatives on it). **Acceptance (corrected per F5/NEW-4/L1/M2/M8): no TRACKED file
   modified. The PRE-SESSION untracked set is enumerated (M8) and none of it is touched
   beyond the sanctioned `nul` removal: `.claude/thinktank_backloground3.md`,
   `.claude/thinktank_f7task11.md`, `docs/audits/2026-08-05-closed-world-census.md`,
   `docs/audits/2026-08-05-enterprise-launch-campaign-state.md`,
   `docs/audits/2026-08-05-enterprise-launch-readiness-census.md`,
   `docs/audits/2026-08-05-thinktank-codex-amend-spine.md`,
   `docs/plans/2026-08-05-enterprise-launch-campaign-plan.md`,
   `docs/plans/2026-08-06-agentic-cli-audit-campaign.md`,
   `docs/plans/2026-08-06-enterprise-launch-completion-plan.md`,
   `docs/plans/2026-08-08-backlog-completion-plan.md`, `src/.tg_index`, `subdir/`,
   `tests/unit/test_ast_invert_match_fail_closed.py` (13 entries; `nul` was the 14th,
   removed per sanction). SESSION artifacts are a disjoint set (this plan, the WS0 audit,
   the WS2 receipts — committed via the WS3 worktree with explicit paths; `.orchestrator/`
   and `.claude/codex_task_*` stay local, never committed, and the state file is refreshed
   at every phase transition rather than trusted as current — M7).**

### WS1 — Task 2A resume (primary engineering lane)
Executed in the existing clean worktree `.claude/worktrees/task2a-w4-repair` (re-verify clean +
`8f1fc30` immediately before use; A23/A26).
1. **Union-MERGE `origin/main` INTO `task2a-round60-red`** (per F1: merge, not rebase — history
   stays fast-forward-pushable, no force semantics, #966 flips mergeable, `8f1fc30` stays
   reachable). A22 union discipline: no take-one-side; expected 5-file conflict surface above;
   ci.yml re-weave into the #977 changes-gated shape is the highest-risk hunk. Builder:
   cursor-agent (WSL, free) may draft mechanical resolutions under an explicit brief;
   Orchestrator resolves judgment hunks and owns every gate.
2. **Post-merge verification (environment pinned per F2):** run in WSL with a WSL-LOCAL venv
   (never the Windows `.venv`; A60), `PYTHONPATH=src`, each suite as
   `timeout 240 … -m pytest <suite> -q -rA --timeout=15 --maxfail=0`. Oracle = **per-node**
   comparison: parse nodeid→outcome for the **six Task 2A suites (the five #9d receipt suites
   PLUS `test_task2a_sol_r2_fixfirst.py`, which R2–R5 added as a sixth verification surface —
   M6)**. **The SOLE gating comparator is the fresh six-suite pre-merge baseline at `8f1fc30`
   (M6'); the #9d receipt is non-gating historical/erratum evidence only (it has no comparator
   for the sixth suite and its counts predate R2–R5 — ERRATUM-1).** Diff against that
   baseline. Rules: (a) any collection/import/setup error = GATE FAIL, never acceptable
   RED (A61); (b) intended REDs must fail with the same reason-class (assertion-message
   fingerprint spot-check on a sample of ≥5 nodes per suite plus EVERY node whose outcome
   changed); (c) a union-justified delta (e.g. `test_task2a_ci_wiring_contract.py` control
   nodes tracking main's #977 ci.yml shape) is permitted only with a per-node justification
   appended to the receipt doc. **Per-node baseline source (NEW-1, corrected by H2): the #9d
   receipt is a c550a84-era snapshot whose counts CANNOT reproduce at the R5 head — the
   baseline is a fresh pre-merge per-node run at `8f1fc30` in the pinned env, captured BEFORE
   step 1 (executed 2026-08-12; the receipt-mismatch is ERRATUM-1 with commit-diff receipts,
   not a gate failure).** Also:
   `git diff --check`; `ruff check` on touched Python;
   **whole-repo `ruff format --check --preview .`** (F7); `mypy src/tensor_grep` if imports
   changed. No cargo (CI is the Rust oracle).
3. **Push (fast-forward):** `git push origin task2a-round60-red` from the worktree. Confirm
   #966 leaves CONFLICTING; update PR title/body to R5+union truth (stays `test:` + draft +
   do-not-merge).
4. **CI evidence (A43/A44/A68 + M1 receipt topology), PER ROUND (M5'):** for **the currently
   pushed head of each round** (round 0 = `fbe1128`; later rounds name their own SHA), capture
   run ID, job population vs floor, head SHA, base SHA, merge-ref checkout SHA; compare
   executed node population against the **169-node manifest census (157 py + 12 rust; H1)**.
   Attribution: each run belongs to exactly the head it tested; the live receipt is recorded
   in the WS3 docs PR and/or a PR #966 comment (A28) — NEVER committed to the tested branch
   as if the run covered a commit that contains it.
   **4a. Known CI-wiring reachability defect (codex H3, to be confirmed by the live run):**
   the unioned `ci.yml` runs blanket `pytest tests` BEFORE the Task 2A collector steps; the
   intended-RED suites fail the blanket step so the collector is skipped and the Windows
   census cannot execute — `test_task2a_ci_wiring_contract.py` checks collector CONTENT, not
   reachability/order. Repair item **R1** for step 5's loop: deselect the owned RED nodes
   from the blanket run (or reorder the collector ahead of it), plus a reachability/ordering
   ratchet in the ci-wiring contract suite.
5. **Sol exact-byte audit:** codex Sol read-only on the pushed head; verdict `SHIP` or
   `FIX-FIRST(+file:line+repro)`. On FIX-FIRST → repair loop (cursor builds TDD, I gate,
   re-push, re-audit). **Every repaired head repeats step 4 — a fresh live CI run on the new
   head is part of each round's evidence; a pre-repair run cannot confirm anything about the
   repaired bytes (M5/A51).** Timebox: 2 repair rounds this campaign; if still FIX-FIRST, park
   with round receipts appended — board stays BLOCKED, honestly.
- Acceptance: #966 non-conflicting on current main, with a real Actions run recorded per step 4
  and a Sol verdict recorded; board rows #89/#90 text updated to the new head in WS3;
  **no GREEN claim** regardless of outcome.

### WS2 — Research receipts (DELIVERED, to be committed in WS3)
- 17-query Exa wave complete: Part A frontier (8 arXiv 2025-26 papers + 6 industry receipts —
  net: CONFIRMS the edit-control-plane thesis; retrieval commoditizing) and Part B per-row
  evidence. Artifact: `docs/audits/2026-08-12-research-receipts.md`.
- Dispositions carried into WS3 with **deterministic parser-legal board outcomes (codex M4)**:
  - #255, DD-006, AST-DSL-PARITY: Status unchanged `DEMAND_GATED`; Trigger text gains the
    2026-08-12 receipt citation.
  - **MCP-LEAN-DEFAULT: Status stays `DEMAND_GATED`** (Task 2C fence per the MCP-SURFACE
    ladder); Trigger cites the receipts (industry converged on lean/deferred surfaces) and
    names Task 2C as the sequencing gate.
  - **CONTINUOUS-REFRESH: Status stays `DEMAND_GATED`**; Trigger becomes "approved scoping/
    design pass (not a build)" citing the 2026-08-12 warm-index receipts.
  - **RUST-REPLACE-SYMLINK: Status flips `DEMAND_GATED` → `READY`** — the reopen condition
    ("concrete untrusted-destination threat model") is now satisfied by the 2026 CVE class
    receipts (sed CVE-2026-5958, uutils GHSA-239g-2685-54x3, Capgo CVE-2026-56236) carried
    in-body per A71; Trigger: design-council pass first, then TDD build (no-follow-by-default
    or documented boundary + Event-gated swap test); nonfinancial, reversible, evidence-backed.

### WS3 — Docs closeout PR (batch, no release)
- One `docs:` PR from a fresh worktree off `origin/main` carrying: this plan (status-stamped),
  WS0 reconciliation audit, WS2 receipts doc, BACKLOG.md dated entry, TASK_BOARD campaign note
  + row-text updates (#89/#90 head reference; the three PROPOSED_REOPEN dispositions recorded
  on the demand rows as evidence links — Status flips only where the packet justifies it),
  SESSION_HANDOFF refresh. Gates: board governance tests (A71 parser, freshness), 4-step local
  gate, PR CI green, merge in a green gap (docs: does not release).

### WS4 — World-class roadmap slice (QUEUE-ONLY this campaign; F6 corrected)
- Verify the roadmap doc's seams still resolve on `b6dc0a6` (done: `Commands::VerifyEdit` +
  `PUBLIC_TOP_LEVEL_COMMANDS` parity named at `worldclass-roadmap.md:63`; S1 at `:56-71`) and
  queue the S1 Python-side slice as the next campaign's candidate. **No build this campaign
  under any condition; the slice first needs its own design-council pass** (roadmap is DESIGN,
  not a build plan).

### WS5 — CEO decision surfacing (report only)
- Restate the 5 packets (`docs/audits/2026-08-06-ceo-gated-recommendation-packets.md`) with
  recommendations + WS2 evidence deltas in the final report. No flips.

## Verification & completion definition (this campaign)

- Every WS acceptance evidenced with commands/run IDs/SHAs in the campaign log + WS0/WS2 docs.
- Final closed-world status enumerates ALL 28 rows (A45).
- Dogfood: no releasing merge is planned, so dogfood = readiness regression on the shipped
  wheel (`tg --version`, scoped `scripts/agent_readiness.py` probes) after WS3 merges.
- New defects found en route: BACKLOG.md dated entry + TASK_BOARD row; never silently fixed
  out of scope.
