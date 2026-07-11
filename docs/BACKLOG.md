# tensor-grep — Project Backlog & PR Tracker

> **Canonical prioritized work list.** Kept in sync with the CLI task store (`TaskUpdate`) and
> GitHub (`gh pr list` is the source of truth for PRs). **CEO status** = summarize SHIPPING + P0/P1.
> Update whenever a PR opens/merges or the queue changes. Task-store IDs (`#NNN`) cross-referenced.
> Last refreshed 2026-07-10 (drain @ v1.58.11).

**Process:** deep-dive/audit (cite `file:line`) → verify-against-code → Sonnet TDD build in
`isolation:'worktree'` → real-venv verify (`uv run --active --no-sync`; copy `rust_core.pyd`, set
VIRTUAL_ENV+PYTHONPATH — a worktree "tests pass" is a hypothesis) → `ruff check` + `ruff format
--preview` + `mypy` (+ `cargo fmt --check`/`clippy` for Rust) → **mandatory adversarial Opus gate** if
it touches apply_policy/mcp/cpu_backend/index_lock/session_daemon/backends → PR → drain
(one-merge-per-publish). Match model to task. Common-sense gate before pending the CEO.

**Legend:** `P0` ship-blocking/#1 gap · `P1` HIGH bug/moat · `P2` MED · `P3` LOW. Status:
`[shipping]` open PR · `[ready]` buildable · `[wip-blocked]` cap-blocked (>5 PRs) · `[blocked]` gated · `[done]`.

**Drain discipline (hard-won 2026-07-10):** verify publish via `/simple` full wheel-pattern
`tensor.grep-1.58.N` OR the release run's publish-pypi=success — NOT a top-level "completed/success"
(can be a non-release run), NOT `grep | head` (head masks grep's exit). Stamp-on-main = Semantic
Release done (safe once /simple lists it). A run `in_progress` on "Python Semantic Release" = native
wheel compile (~65min normal), don't panic-rerun. **WIP CAP: no new build while >5 PRs undrained.**

---

## ⭐ CURRENT STATE (2026-07-11) — authoritative; every section BELOW is HISTORICAL until the next full refresh

- **0 open PRs** (`gh pr list` = 0, the source of truth). Live PyPI **v1.61.2**; **v1.62.0 releasing** (main `d812749` `chore(release): v1.62.0`; #525 orient auto-deweight). **WIP=0.**
- **The authoritative current backlog is the CLI task store; two live CEO-relayed fix-queues:**
  - **#134 External audit 2026-07-11** — correctness batch [A1 explicit-`--index` silently drops CLI options / A2 semantic-corpus-cap self-bypass (DoS) / A3 late-rerank real cancellation / A4 index-persistence load-once+atomic+lock / A5 = this refresh] + GPU capability/packaging integrity (remove the NVIDIA-artifact-without-native-CUDA matrix) + doc regressions. Gate-free now: A2/A3/A5/P0-1. Opus-gated → Jul-13: A1/A4/GPU-cap. CEO-gated: GPU resident engine + distribution.
  - **#133 v1.61.2 dogfood** — P0 unscoped-search refuse-fast (root-caused: the rg-passthrough self-walks the whole tree; fix = bounded pre-passthrough probe) · P1 evidence-UX / suggested_scope-in-agent / edit-plan structured validation_plan / dynamic-imports.
- **SHIPPED since v1.57.1:** v1.58.x (daemon Tier-1 #492; flaky+moat+multi-pattern #499-#505; tg_repo_map 2000-cap #114) · v1.59-1.60 (codex-audit HIGH security wave #508-#512, all Opus-gated; EvidenceReceipt `tg evidence emit` #510) · **v1.61.0** (#498 daemon symbol-cache = #113 latency) · **v1.61.1** (#522 search nested-gitignore) · **v1.61.2** (#523 orient nested-gitignore + #524) · **v1.62.0 releasing** (#525 auto-deweight, 3 CI-caught fixes). No-release batch: #514 (GPU PFAC doc-honesty correction) · #520 (GPU dead-code) · #521 (#129 de-flake) · #513.
- **Strategic (audit):** near-term product = **signed PR evidence + change-governance**; GPU offered later for batch security/compliance scans; no-SaaS (gotcontext.ai is the SaaS shell, not tg).

---

## SHIPPING — 8 open PRs (drain security-first, one-per-publish) — task #117

| PR | Fix | Gate |
|----|-----|------|
| #512 | **H5** subprocess-hang: bound Rust→Python passthrough + POSIX `setpgid` (gate caught a Linux/macOS killpg no-op) | SHIP (2-round) — NEXT |
| #509 | **H2** apply_policy RCE: beneath-or-equal exec confinement | SHIP+must-fix |
| #511 | **H1** `--index` 5 silent-wrong-results + gitignore secret-leak (fail-closed) | SHIP+must-fix |
| #507 | **#116** LSP unbounded-header DoS | no-gate |
| #504 | **#93 SUB-1** dynamic-import recall (importlib/`import()`) | no-gate |
| #506 | **#93 SUB-2** orient `suggested_scope` (degrade-to-null) | no-gate (feat) |
| #510 | **P1** EvidenceReceipt `tg evidence emit` (CEO wire-able feature) | no-gate (feat) |
| #498 | **#113** daemon symbol-response cache (20.9x callers/16.8x blast) | SHIP (feat) — LAST → then flip #94 |

Push-race: v1.58.11 releasing (for #508). Poller re-armed per merge; merge #512 when 1.58.11 on /simple.

## SHIPPED — live on PyPI up to **v1.58.10**; v1.58.11 releasing

This drain batch: #499→v1.58.5 (tg_repo_map 512→2000) · #500→v1.58.6 (#110 write-path symlink TOCTOU) ·
#503→v1.58.7 + #505→v1.58.8 (two flaky-test root fixes) · #501→v1.58.9 (multi-pattern `-e`/`-f`) ·
#502→v1.58.10 (#49 MCP stdio byte-framing+DoS) · **#508→v1.58.11 releasing** (**H3/H4** checkpoint
arbitrary-read + disk-DoS — first codex-audit security fix live). Earlier: v1.58.0-v1.58.4 (daemon
Tier-1, native DoS, blast_radius+GPU-honesty, dual-help, ReDoS fail-closed).

---

## CODEX EXTERNAL AUDIT — HIGH WAVE COMPLETE (#123 [done])
All 5 HIGH verified still-real + fixed + adversarial-Opus-gated + PR'd (H1→#511, H2→#509, H3+H4→#508,
H5→#512, P1→#510). **The gate caught 3 real defects that would've shipped** (H5 POSIX no-op, H1
smart_case 5th silent-wrong, H2 defanged test).

## CEO DIRECTIVE 2026-07-10 (#99 [done]) — after the codex audit
**Do NOT build the SaaS.** Build tg features gotcontext.ai can wire into + focus on the tool
**WORKING** + optimally **PERFORMING**. Workstreams: (A) correctness=audit bugs; (B) perf=#94 + MED
perf; (C) wire-able=EvidenceReceipt (#124). gotcontext stays the CEO's product; we hand it clean
signed consumable tg outputs.

---

## CURRENT LIVE BACKLOG (post-drain — all `[wip-blocked]` until the 8-PR drain clears <5)

### P0 — correctness / "tool working" (build FIRST when a slot frees)
- **#128** Codex MED fix-queue (verified still-real): **MED-3 ast-grep malformed-JSON→empty = fail-closed
  violation FIRST** (backends gate) · MED-5 nested-`.gitignore` in both Python walkers · MED-4 daemon
  no worker-semaphore (session_daemon gate) · MED-1 uncapped `--rank` rechunk.
- **#130** v1.58.9 dogfood fix-queue: **inventory `--deadline`→files=0 silent-empty** · **`tg refs` not
  deadline-bounded** (45s hang) · **checkpoint create `IsADirectoryError`** on nested-repo entry ·
  **doctor false `ast_grep.available:true`** under WSL (probe-run, not shim-resolve).
- **#131** GPU audit honesty/correctness (LOW-urgency — GPU experimental/paused, but claims matter):
  **F2 GPU benchmark parses `line_number`, real JSON emits `line`** (invalid proof) · F1 remove the PFAC
  doc claim (it's brute-force) · F3 GPU fail-closed capability matrix (silently drops flags) · F10 dead
  GPU code cleanup.

### P1 — moat / features
- **#94** flip warm daemon to DEFAULT (20.9x proven, fail-open) — AFTER #498 merges (all blockers cleared).
- **#124** EvidenceReceipt **P2** (HMAC signing + `tg evidence verify`) → **P3** (MCP tool, gated). P1=#510
  in drain. Gaps: persist checkpoint undo_argv + manifest rollback field.
- **#118** #93 **SUB-3** unscoped-refuse (hard-refuse vendored-top-level; fixes the dogfood 60s-hang) —
  dep #501 cleared, awaits WIP<5. + SUB-2 `suggested_ignore` companion.
- **#130 features**: edit-plan structured `validation_plan` (agent-parity) · imports/importers
  `sys.path.insert` awareness (beyond #504) · confidence-lift when validation corroborates.
- **#98** MCP consolidation (47→~10 task-shaped, non-breaking + `TG_MCP_TOOL_SURFACE=lean`) — design done.
- **#108** daemon Tier-2: serve orient/agent capsules via the warm daemon.
- **#86** late-rerank T7-T10 (T8 golden-set ship/no-ship gate = the decision).

### P2 / follow-ups
- Gate follow-ups: **#125** checkpoint try/finally + symlink · **#126** apply_policy fail-open edge
  hardening (8.3/junction/`\\?\`, re-gate) · **#127** index gitignore-in-non-git-dir · **#129** de-flake
  help-probe timeout test.
- **#121** native `--count-matches` no-rg hard-error · **#115** symlink-sweep main.rs writes · **#92**
  classify `--stdin`/`--text` · **#122** this doc's post-drain refresh cycle.
- Dead-code (audit tail): semantic_index.py unwired BM25 · backend_cpu.rs replace_in_place · sidecar.py
  `_classify_lines`.

### Blocked on a Linux box
- **#89** WSL `/mnt/c` path resolution · **#90** ast-grep Linux/WSL + doctor honesty · **#109** cuda
  implicit-walk ceiling.

---

## CEO-FACING / strategic (the CEO's call — not auto-fired)
- **#72** benchmark proof-point publish (tokens-per-correct-answer). Reinforced by the dogfood + GPU
  "published accuracy gate" enterprise-gap.
- **#77** `tg ledger` local agent context-sharing (thinktank-reviewed; gate behind semantic-search).
- **GPU program (#131 strategic half)** — the GPU deep-dive recommends a ~24wk/2-engineer rebuild
  (Phase 0 truth → Phase 1 resident+matcher experiments = the FUNDING GATE → sharded streaming →
  fixed-string matcher → packaging → resident service → bounded regex) toward **a local/BYOC
  high-throughput scanning plane (secret/PII/license/policy rule-packs)**. ⚠ **This SaaS-scanning
  recommendation directly re-opens the #99 wedge the CEO CLOSED ("no SaaS") 2026-07-10** — a genuine
  strategic fork for the CEO. Only the Phase-0 honesty/benchmark fixes (#131 above) are non-gated.
- **Enterprise gaps** (dogfood-surfaced, design-scale): **multi-root workspace primitive** (orient/
  search/blast across sibling repos, no manual fan-out) · target-selection accuracy gate (top-k/MRR) ·
  cross-OS managed ast-grep · LSP proof-mode (availability ≠ navigation proof).

## References
- Cross-session resume anchor (memory): `tensor-grep-drain-resume-2026-07-09.md` (live drain/audit/dogfood/GPU state).
- Full process rules: [AGENTS.md](https://github.com/oimiragieo/tensor-grep/blob/main/AGENTS.md).
