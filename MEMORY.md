# tensor-grep durable campaign memory

Last updated: 2026-08-06 PM (CEO update)

## Resume here

1. Public product is **`v1.110.0`**. Live CEO packet:
   `docs/audits/2026-08-06-pm-ceo-backlog-update.md`. Morning receipt (A70–A76 / pre-stamp READY):
   `docs/audits/2026-08-06-ceo-backlog-update.md`. Board index **`2026-08-06.3`**.
2. Closed-world: **28 / 17 unfinished** = **0 READY**, **6 BLOCKED**, 0 IN_FLIGHT, 5 CEO_GATED,
   6 DEMAND_GATED. F7, CPU-BACKEND, REF-CALL-REGISTRY remain **SHIPPED**.
3. **Task 2A remains correctly blocked** — draft #966 FIX-FIRST lineage; do not call merge-ready.
   Gate the **tip under review**, not only archaeological RED `6367614…` (A80). Sol SHIP + Windows
   CI outstanding (A68/A81).
4. Enterprise launch bar: CUJ `#958` + wheel dogfood `#962`. STOP: W3 rust/e2e shared-box cargo,
   MCP wire-contract fence (live `1.7.0`), #169 spend, silent CEO-gate flips, MCP/F5–F8 product builds.
5. No spend. No nonfinancial CEO question. #169 is the only financial stop.
6. Laws **A70–A76** (morning) + **A77–A82** (PM): stdin poller; usage-limit FAILED seats; status-pin
   retarget; tip-vs-archaeology; receipts≠Sol; AMEND_SPINE.

## External state at the snapshot

- Public release: `v1.110.0`; PyPI reports `tensor-grep 1.110.0`.
- `origin/main` tip at PM CEO write: `bb4fdae` (re-derive before merge).
- Open PRs: derive with `gh pr list --state open` (draft #966 Task 2A FIX-FIRST at write).
- Financial spend: none incurred or authorized.
- Anthropic Pro usage-limit seats FAILED until ~2026-08-14 unless Spend Limit (A78).

## Queue (unfinished 17)

- BLOCKED: #89, #90, F5, F6, F8, MCP-SURFACE.
- CEO_GATED nonfinancial: #48, #72, #77, #131 (recommendations only; unchanged).
- Financial: #169 only.
- DEMAND/research: #255, DD-006, AST-DSL-PARITY, MCP-LEAN-DEFAULT, CONTINUOUS-REFRESH,
  RUST-REPLACE-SYMLINK.

## Retained laws from 2026-08-06 PM (additions)

- **A77** Stdin+heredoc `gh pr checks` poller can empty → false ALL_TERMINAL (#963 early merge).
- **A78** Usage-limit / provider-error seats are FAILED (not pending); re-seat when quota returns.
- **A79** READY→BLOCKED stamps must update governance pins in the same PR.
- **A80** Clearance binds to tip bytes under review, not archaeological RED SHA names.
- **A81** Implementer HIGH receipts ≠ Sol SHIP.
- **A82** AMEND_SPINE: START_NOW = docs/R0/D1 when READY∩reconcile-BLOCKED; no MCP/F5–F8 builds.

Prior laws A34–A76 and dated MEMORY bodies remain historically true for their dates; do not
flatten mixed dispositions. Full closed-world status: `docs/TASK_BOARD.md` +
`docs/audits/2026-08-06-pm-ceo-backlog-update.md`.
