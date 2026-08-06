# CEO-gated recommendation packets — 2026-08-06

Research-only. **No status flips.** Every item below ends with `STATUS REMAINS CEO_GATED`.

Sources: `docs/BACKLOG.md` CEO-gated section; `docs/gpu_crossover.md`; Task board rows; prior 2026-08-03/05 audits.

---

## #48 — Native front-door startup architecture

**Recommendation:** Accept the shipped hybrid managed native front door + Python sidecar; do **not** authorize a larger rewrite unless pip/uv parity or cold-start SLA becomes a measured P0 with a named consumer.

**Evidence:** Managed native front-door + sidecar env contract is released through current PyPI line; AGENTS.md “Native vs Python Reality” and installer dogfood receipts treat Python as sidecar/fallback, not the normal exact-text first hop.

**STATUS REMAINS CEO_GATED**

**Reopen if:** Measured cold-start gap vs `rg` with a committed harness and a customer SLA that the hybrid path cannot meet without architectural change.

---

## #72 — Public benchmark claim

**Recommendation:** HOLD any public speed multiplier (historical 7.5× / 6.4× conflict). Allow only a zero-spend fresh quality-gated benchmark with pinned harness SHA; public wording still needs CEO approval before marketing use.

**Evidence:** BACKLOG CEO packet; research-frontier / benchmark skills forbid marketing claims without accepted baseline artifacts.

**STATUS REMAINS CEO_GATED**

**Reopen if:** A sealed, reproducible harness run is committed and CEO explicitly approves the public sentence.

---

## #77 / F9 — Ledger enforcement scope

**Recommendation:** Keep ledger **local opt-in advisory only**; no auth/CI blocking gate.

**Evidence:** `tg ledger` is advisory multi-agent coordination; BACKLOG CEO packet; enterprise-agent hard-stops treat enforcement as a product decision, not a silent default.

**STATUS REMAINS CEO_GATED**

**Reopen if:** An enterprise customer requires blocking enforcement and accepts the auth/identity threat model in writing.

---

## #131 — GPU-flavor native-asset publication

**Recommendation:** Optional experimental NVIDIA asset with CPU default/fallback and **no speed claim**. Physical proof/spend stays under **#169 FINANCIAL_HOLD**.

**Evidence:** `docs/gpu_crossover.md` — no crossover at any measured scale; Phase 1 asset publish ≠ promotion; public managed GPU proof workflow still HOLD.

**STATUS REMAINS CEO_GATED**

**Reopen if:** #169 spend approved **and** `public_gpu_proof=true` on the dispatch-only proof workflow.

---

## #169 — pointer only

**FINANCIAL_HOLD** — physical GPU proof environment or spend. Not a recommendation packet; mandatory money stop. No spend proposed in this campaign.
