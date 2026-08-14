# CEO-gated recommendation packets - 2026-08-13 campaign (W7)

Delta on the standing 2026-08-06 packets (`docs/audits/2026-08-06-ceo-gated-recommendation-packets.md`),
which are the base and are **reused, not rewritten**. Research-only. **No status flips.** Each of the
five sections below ends with the literal terminator line naming the unchanged status.

Thinktank council: dispatched 2026-08-14 (tt_council.sh, 8 seats, 300s per-seat timeout).
7 of 8 seats returned anchored verdicts; copilot TIMEOUT (a failed seat, not a blocker - A10;
synthesized from the survivors). Seat 1 sat `claude-sonnet-5` in place of `claude-fable-5`
(Fable 5 quota exhausted at dispatch time - recorded substitution per the seat-health gate).
Verdicts: 7/7 `HYBRID-ACCEPTED / ADVISORY-ONLY`. Raw logs: `.orchestrator/tt_w7/`.

---

## #48 - Native front-door startup architecture

**Recommendation (council-confirmed):** HYBRID-ACCEPTED - accept the shipped hybrid managed native
front door + Python sidecar as the settled architecture. Do **not** authorize a larger rewrite
unless pip/uv parity or cold-start SLA becomes a measured P0 with a named consumer.

**2026-08-13 delta vs the 2026-08-06 packet:** the council added the concrete architectural seam
and a reversible implementation proposal. The board's own #48 row already records the honest
negative ("tg's native walk *is* rg's walk, same `ignore` crate, so widening relocates cost rather
than removing it"). Frozen under this option: `~/.tensor-grep/bin/tg.exe` resolution order, the
installer, and `rust_core/src/main.rs`. Reversible proposal if the CEO ever approves a formal
close: (1) a `docs/decisions/` ADR recording the accept + reopen trigger; (2) an additive
`tg_launcher_mode`/`cold_start_sla_measured` stamp in the cold-path benchmark artifact schema so a
future P0 claim is measured, never asserted; (3) no changes to the frozen seam.

**Reopen if:** a committed harness run shows a measured cold-start gap vs `rg` **and** a named
customer SLA the hybrid path cannot meet.

STATUS REMAINS CEO_GATED

---

## #72 - Public benchmark claim

**Recommendation:** HOLD any public speed multiplier (the historical 7.5x / 6.4x conflict is
unresolved). Allow only a zero-spend fresh quality-gated benchmark with pinned harness SHA; public
wording still needs CEO approval before marketing use.

**2026-08-13 delta vs the 2026-08-06 packet:** the 2026-08-12 competitor token-reduction receipts
strengthen the *context* (the "fewer tokens than grep" wedge is real and increasingly visible in
the ecosystem) but do not move the gate. The multiplier conflict is untouched by this campaign.

**Reopen if:** a sealed, reproducible harness run is committed and the CEO explicitly approves the
public sentence.

STATUS REMAINS CEO_GATED

---

## #77 / F9 - Ledger enforcement scope

**Recommendation (council-confirmed):** ADVISORY-ONLY - keep `tg ledger` local opt-in advisory
only; no auth/CI blocking gate.

**2026-08-13 delta vs the 2026-08-06 packet:** the council named the rejected gate shape and the
accepted reversible extension, cited to the code. `ledger_store.py`'s module contract says claims
are "advisory only - ... never a block" and the findings ledger is "advisory, TTL-bounded
coordination state"; the enterprise-CUJ surface treats ledger as coordination, not an auth-adjacent
gate. Rejected: any CI check / pre-commit hook / `tg` exit-code path that consumes claim-overlap
state to fail a build or block a merge. Accepted if ever approved: an additive overlap-report hint
field (display-only, never exit-code), plus a contract test pinning "ledger commands never affect
`tg`'s exit code" so a future PR cannot silently promote the ledger to blocking.

**Reopen if:** an enterprise customer requires blocking enforcement and accepts the auth/identity
threat model in writing.

STATUS REMAINS CEO_GATED

---

## #131 - GPU-flavor native-asset publication

**Recommendation:** Optional experimental NVIDIA asset with CPU default/fallback and **no speed
claim**. Physical proof/spend stays under **#169 FINANCIAL_HOLD**.

**2026-08-13 delta vs the 2026-08-06 packet:** none. No new crossover evidence was produced this
campaign; `docs/gpu_crossover.md` stands as the authority.

**Reopen if:** #169 spend approved **and** `public_gpu_proof=true` on the dispatch-only proof
workflow.

STATUS REMAINS CEO_GATED

---

## #169 - pointer only

**FINANCIAL_HOLD** - physical GPU proof environment or spend. Not a recommendation packet;
mandatory money stop. No spend proposed in this campaign. No packet, no recommendation, no spend
option presented.

STATUS REMAINS CEO_GATED
