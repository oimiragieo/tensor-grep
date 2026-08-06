# DEMAND-gated research receipts — 2026-08-06

Research-only. **Do not flip** `docs/TASK_BOARD.md` from this file alone. `PROPOSED_RETIRE` needs a
separate docs PR that carries the measurement in-body (A71).

Sources: `docs/BACKLOG.md` demand section; `docs/TASK_BOARD.md`; prior campaign audits; live greps
where noted.

---

## Already retired (confirm; do not reopen)

| ID | Disposition | Receipt |
|---|---|---|
| **F10** MaxSim | Confirm against board after #963 lands — prior plan `docs/plans/2026-08-06-enterprise-launch-completion-plan.md` / #953 wave treated as RETIRED with DROP receipt | If board still DEMAND_GATED after #963, leave until stamp PR; do not re-open MaxSim activation |
| **DD-004** typed backend-error boundary | Same — recommendation was retire-as-standalone and bank the rule | Leave/confirm stamp; do not invent a new typed-error project |

---

## Live DEMAND rows

### #255 — many-pattern dedup / compression / native investment

- **Premise:** Demand for a bounded many-pattern dedup parity experiment or approved compression/native investment.
- **Check:** Banked as #255; Aho-Corasick live dedup over-count guarded not fixed (#694 era). No new measured customer demand in this campaign.
- **Disposition:** **LEAVE DEMAND_GATED** (evidence date 2026-08-06)
- **Reopen:** Bounded parity experiment approved with RED oracle that discriminates over-count vs correct count.

### DD-006 — concurrent daemon load / DoS evidence

- **Premise:** Measured concurrent daemon load or denial-of-service evidence.
- **Check:** No new load measurement attached this campaign; session daemon bounds exist from prior hardening but are not a DoS campaign closeout.
- **Disposition:** **LEAVE DEMAND_GATED** (2026-08-06)
- **Reopen:** Reproducible concurrent-load receipt with authenticated provenance (A63).

### AST-DSL-PARITY — full structural DSL / preprocessor-aware oracle

- **Premise:** Demand for full native↔ast-grep DSL parity.
- **Check:** Task #141 remains demand-gated; metavar fail-closed already at 3 sites; native-shaped fallback deliberate. No new consumer demand measured.
- **Disposition:** **LEAVE DEMAND_GATED** (2026-08-06)
- **Reopen:** Named consumer needing metavariable-native performance with an oracle suite.

### MCP-LEAN-DEFAULT — default surface flip

- **Premise:** Client demand + compatibility evidence before changing default MCP surface.
- **Check:** Phase-1 consolidation shipped; lean default flip removes many names from the wire. Live contract `1.7.0` does not advertise `tool_surface`. 2026 clients often solve via deferred loading.
- **Disposition:** **LEAVE DEMAND_GATED** (2026-08-06)
- **Reopen:** Compatibility matrix from real clients + CEO/product approval for default flip (still after Task 2C ladder for related bumps).

### CONTINUOUS-REFRESH — warm-session search index service

- **Premise:** Measured warm-session demand + approved search-index service design.
- **Check:** Prior research: daemon holds symbol map not search index; big-refactor; free partial win is long-lived MCP process warming CPUBackend caches. No approved design.
- **Disposition:** **LEAVE DEMAND_GATED** (2026-08-06)
- **Reopen:** Measured warm-query SLA gap with approved index service design (not speculative daemon rewrite).

### RUST-REPLACE-SYMLINK — Rust direct-file leaf-symlink behavior

- **Premise:** Concrete untrusted-destination threat model or downstream compatibility decision.
- **Check:** Deferred security behavior needs canonical owner (A49). No new threat-model packet this campaign beyond naming the gate.
- **Disposition:** **LEAVE DEMAND_GATED** (2026-08-06)
- **Reopen:** Written threat model with Event-gated parent/leaf swap RED (A38) or explicit compatibility decision.

---

## Summary table

| ID | Disposition |
|---|---|
| F10 / DD-004 | CONFIRM retirement stamp after #963; do not reopen |
| #255 | LEAVE DEMAND_GATED |
| DD-006 | LEAVE DEMAND_GATED |
| AST-DSL-PARITY | LEAVE DEMAND_GATED |
| MCP-LEAN-DEFAULT | LEAVE DEMAND_GATED |
| CONTINUOUS-REFRESH | LEAVE DEMAND_GATED |
| RUST-REPLACE-SYMLINK | LEAVE DEMAND_GATED |

No `PROPOSED_RETIRE` with fresh measurement in this pass — avoid silent flips.
