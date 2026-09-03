# CEO backlog update (dumbed-down) — 2026-08-29

**HEAD:** `8a879b286a470866dec12ba6aac80ff80aa61adc`  
**Public release:** `v1.113.5` (PyPI 4/4 verified this session via `uvx --from tensor-grep==1.113.5`)  
**Main CI:** run `32737390852` — `completed` / `success` on HEAD  
**Open PRs:** 0  

## Spend

| Field | Value |
|---|---|
| Authorized (cumulative) | **$0** |
| Spent this session | **$0** |
| Change since 2026-08-15 CEO packet | **$0** (no Tier-3 Sol/Fable/codex seats invoked) |

## Phase / item / event

| SDLC (contract-driven-sdlc) | Plan item | Wayfinder | Feature folder |
|---|---|---|---|
| **Sprint 0 — audit / requirements** (no production code) | Orchestrator **pass-2 deep-dive** → `backlog.md` | *(none)* | `.orchestrator/` + root `backlog.md` (untracked) |

**Event:** Pass-2 audit complete; 28 canonical board rows, **16 unfinished**, **0 READY**, **0 IN_FLIGHT**. Latest merge: `docs: retire #48` (#1121).

## Next

1. **ENV-SYNC** — `uv sync --frozen` (local venv was **1.113.3** vs pyproject **1.113.5**; build was in flight at packet time).  
2. **SEC-001-SPEC** — RED test for bootstrap native `--` sentinel; **A3 adversarial gate** before any fix PR.  
3. **DOCS-RECONCILE** — stamp TASK_BOARD live snapshot (still says 17 unfinished / lists `#48` under CEO_GATED in prose).  
4. **HYGIENE-FORMAT** — 15 markdown files fail `ruff format --check --preview` (docs-only PR).  
5. Do **not** start blocked-row product code until `docs/plans/2026-08-22-blocked-row-unblock-campaign.md` is CEO-approved.

---

## What worked (verified, with receipts)

| Check | Command / artifact | Verdict |
|---|---|---|
| Main CI on HEAD | `gh run list --branch main --workflow=ci.yml --limit 1` → run `32737390852`, sha `8a879b2` | **PASS** |
| Governance pytest slice | `uv run --no-sync pytest tests/unit/test_skill_index_sync.py tests/unit/test_skill_library_drift.py tests/unit/test_task_board_freshness.py -q` | **20 passed** |
| File-size ratchet | `python scripts/file_size_budget.py` | **PASS** (905 files, 0 regressions) |
| Bare-call ratchet | `python scripts/bare_call_ratchet.py` | **PASS** (0 bare calls) |
| Published wheel | `uvx --refresh-package tensor-grep --from tensor-grep==1.113.5 tg --version` | **tensor-grep 1.113.5** |
| SEC-001 evidence | `bootstrap.py:1413-1414` vs `main.py:895` | **CONFIRMED GAP** (bootstrap lacks `--`) |
| Routing help contract (isolated) | `pytest tests/e2e/test_routing_parity.py::test_top_level_help_visible_commands_match_public_contract -q` | **1 passed** (~2.7s) |
| #48 retirement on main | `git log -1` → #1121 docs retire #48 | **MERGED** (row already `RETIRED` in index) |

### Not run this session (cannot claim)

| Requested gate | Status |
|---|---|
| `verify-feature` / `feature-verify` | **No such script/tool in repo** — UNVERIFIED |
| `review-router` | **Not found** — UNVERIFIED |
| `use-codex` **audit-until-clear** on HEAD | **Not invoked** — UNVERIFIED |
| **dogfood-the-shipped-artifact** full harness (`scripts/dogfood/`) | **Not run** (CPU-heavy; PyPI version-only probe above) |
| Full suite **7445** tests | **Not run** end-to-end (CI green on HEAD is the arbiter) |
| Routing parity **full file** e2e | **Not re-run** this packet (prior pass: 45/46 flaky; isolated pass) |

---

## Open backlog — 100% (canonical `docs/TASK_BOARD.md` index)

**Counts:** 28 rows total → **16 unfinished** = 0 READY, 0 IN_FLIGHT, **6 BLOCKED**, **4 CEO_GATED**, **6 DEMAND_GATED**.  
*(Prose elsewhere that says 17 unfinished or 5 CEO_GATED is **stale** — `#48` is RETIRED; not CEO-blocked.)*

### BLOCKED (6) — need environment, plan, or sequencing

| ID | Blocker receipt | AI-doable? |
|---|---|---|
| **#89** | WSL host: `path_not_found` on `/mnt/c/...` without translation; PR #966 closed stale — **no WSL CI arm** | Spec + RED on real WSL only |
| **#90** | WSL: raw-path scan `matched_rules=0` vs translated control; same WSL gap as #89 | Same |
| **F5** | Tasks 8 edit-ready steps 3–5 touch `rust_core/**` + e2e; **shared-box cargo ban** → CI/cloud | After unblock plan |
| **F6** | Mixed: Python/schema slices buildable; native verify-edit + e2e **CI/cloud** | Partial — S1 slices only with gate |
| **F8** | Receipt **stale** (`path_domain.rs` absent on main); shared-box may still apply — **re-derive touch points** | After scope refresh |
| **MCP-SURFACE** | Contract `_TG_MCP_SERVER_CONTRACT_VERSION = "1.7.0"` at `mcp_server.py:188`; Task 2C sequencing — **not rust-blocked** | After 2C / contract bump plan |

### CEO_GATED (4)

| ID | Blocker receipt | AI-doable? |
|---|---|---|
| **#72** | Board row: CEO approval for **new public benchmark claim**; 2026-08-23 audit: **no public surface to retract** — gate is on *publishing*, not fixing | Research done; **CEO taste/spend** |
| **#77** | CEO decision on **#77/F9 ledger-enforcement scope** | CEO policy |
| **#131** | CEO decision on **publishing GPU-flavor native assets** | CEO + #169 |
| **#169** | **Only financial hard stop** — CEO approval for GPU proof infra/spend | CEO only |

### DEMAND_GATED (6)

| ID | Blocker receipt | AI-doable? |
|---|---|---|
| **#255** | No bounded many-pattern dedup demand; council **LEAVE** (2026-08-14) | No until demand |
| **DD-006** | Demand **satisfied** (2026-08-14 probe); design **merged** PR #1015; product build **not authorized** (A122) | **CEO build go** for PERF+HONESTY |
| **AST-DSL-PARITY** | No consumer perf block; Exa/council **LEAVE** | Defer |
| **MCP-LEAN-DEFAULT** | Spec direction OK; **sequenced after Task 2C** | Defer |
| **CONTINUOUS-REFRESH** | Warm-index service — scoping only; no build auth | Defer |
| **RUST-REPLACE-TOCTOU** | Residual races documented; no demand trigger | Defer |

### Orchestrator audit queue (not board rows — **AI-doable** unless noted)

| ID | Priority | Blocker / next step |
|---|---|---|
| **ENV-001** | P0 | Local venv drift — run `uv sync --frozen` |
| **ENV-002** | P0 | `tg doctor` rust version mismatch |
| **ENV-003** | P2 | Full-file routing parity flake — **MONITOR** (isolated test passes) |
| **SEC-001** | P0 | Bootstrap `--` gap — **RED + A3** before fix |
| **SEC-002–012** | P1 | Security bucket — cited in `backlog.md`; no implementation started |
| **ARCH-001–014** | P1–P3 | Architecture/governance debt — see `backlog.md` |
| **HYGIENE-001–003** | P3 | Format 15 md files; test TODO triage |

**Draft plan NOT approved:** `docs/plans/2026-08-22-blocked-row-unblock-campaign.md`

---

## Research (decision-blocking only)

| Item | Why research | Council defer |
|---|---|---|
| **#89 / #90** | Needs **real WSL repro**, not Exa — environment-blocked | **cap-off-path** — no more desk research until WSL host |
| **DD-006 build** | Design on main; remaining work is **implementation authorization**, not research | **research-council-defer** — specs sufficient |
| **SEC-001 fix approach** | Mechanism known (mirror `main.py:895`); needs **A3**, not Exa | Defer external research |

Non-blocking demand rows: **no new research** (AST-DSL, CONTINUOUS-REFRESH, MCP-LEAN already receipted LEAVE).

---

## Thinktank (internal $0 — pass-2 orchestrator)

| Question | Verdict | One-line why |
|---|---|---|
| Is routing parity 1-fail a shipped bug? | **NO — monitor** | Isolated test passes; env/order artifact |
| Promote SEC-001 to fix without gate? | **NO** | Verified gap; CWE-88 class needs A3 |
| Implement handler ledger now? | **NO** | Census slice first; no drive-by pins |
| Approve blocked-row plan? | **NO** | DRAFT — CEO/council |
| Full local pytest now? | **NO** | CPU; CI green on HEAD |

**CEO escalations:** none requiring split council — only **explicit approvals** (blocked plan, #169 spend, #131 GPU publish, #72 if ever publishing a benchmark number).

---

## False-green / completion-signal audit

| Claim | detect-the-false-green | verify-completion-signal |
|---|---|---|
| "Audit complete" | **PASS** — artifacts exist (`backlog.md`, baselines, bucket findings) | **PASS** — not exit-code-only |
| "CI green" | **PASS** — run ID + sha bound | **PASS** |
| "Routing parity broken" | **FAIL claim** — isolated arm passes; full-file flake unconfirmed this packet | **UNVERIFIED** full file |
| "Shipped security fixed" | **N/A** — no fix merged | — |
| "Dogfood clean" | **NOT CLAIMED** | **UNVERIFIED** — harness not run |

---

## Lessons since 2026-08-15 CEO packet (≥5)

1. **Board prose rots faster than the index** — `#48` is RETIRED in the checklist but campaign prose still lists it under CEO_GATED; trust **mechanical index**, not paragraphs (A71/A144 class).  
2. **Local venv drift is a false-green machine** — `1.113.3` local vs `1.113.5` tag makes doctor and CLI gates untrustworthy until `uv sync --frozen` (ENV-001).  
3. **Bootstrap and main are two front doors** — SEC-001 shows argv hardening on `main.py` does not protect `bootstrap._run_native_tg_search` (A83 registration completeness).  
4. **Split-floor measurement beats hope** — three giants **cannot** reach 1,500-line budget without DI; lowering the pin is dishonest (A130).  
5. **Tier-0 audit ≠ Tier-3 clearance** — finding SEC-001 in explore does not substitute Sol/codex/dogfood on the fix PR (A18/A81).  
6. **Flaky e2e needs the discriminating arm** — help-order test passes alone in 2.7s; full-file batch is the suspect instrument, not necessarily product (ENV-003).  
7. **Requested verify tooling may not exist** — `verify-feature`, `review-router` absent; do not invent receipts (instrument honesty).

---

*Packet generated from live commands on HEAD; not from memory alone.*
