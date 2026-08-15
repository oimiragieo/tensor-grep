# tensor-grep durable campaign memory

Last updated: 2026-08-15 (CEO update)

## Resume here

1. Public product is **`v1.110.16`**. CEO packet:
   `docs/audits/2026-08-15-ceo-backlog-update.md` (dumbed-down). Board index **`2026-08-13.1`**.
2. Closed-world: **29 / 17 unfinished** = **0 READY**, 0 IN_FLIGHT, 6 BLOCKED, 5 CEO_GATED,
   6 DEMAND_GATED (8 SHIPPED + 4 RETIRED).
3. **DD-006:** demand SATISFIED (W5B); design packet merged (**#1015** / `0710219`); Fable waived
   for that docs packet only (**A117**). **Product build NOT started** — needs deliberate go
   (DD-006-PERF + DD-006-HONESTY + A3). See design under `docs/design/dd006-accept-side-bound.md`.
4. **Task 2A RED remains correctly blocked** — draft **#966** advanced, not GREEN; do not merge.
5. STOP: W3 rust/e2e shared-box cargo ban, MCP wire-contract fence, #169 spend, silent CEO-gate flips.
6. No spend. No nonfinancial CEO question required this turn. #169 is the only financial stop.
7. New laws **A117–A122** (skip-Fable ≠ build; remote merge truth; docs PR skips ≠ cheap main;
   shell timeout > probe; backlog+R7; demand+design ≠ shipped). Prior **A111–A116** retained.
   - **A111:** Commit every cited plan/spec into the merged tree.
   - **A112:** A frozen control threshold must be met verbatim or the arm is CANNOT_MEASURE.
   - **A113:** Claim only the failure class the raw artifact distinguishes.
   - **A114:** Re-derive corrected census locations mechanically.
   - **A115:** Record wave receipts as per-row tables, not group claims.
   - **A116:** Do not let bare `uv run` create a worktree venv; use the main venv against worktree paths.

## External state at the snapshot

- Public release: `v1.110.16`; PyPI reports `tensor-grep 1.110.16`.
- `origin/main` tip at CEO write: `0710219` (may advance; re-derive before merge).
- Open PRs: derive with `gh pr list --state open` (expect #966 parked RED).
- Financial spend: none incurred or authorized.

## Queue (unfinished 17)

- READY: *(none)*.
- BLOCKED: #89, #90, F5, F6, F8, MCP-SURFACE.
- CEO_GATED nonfinancial: #48, #72, #77, #131 (recommendations only).
- Financial: #169 only.
- DEMAND/research: #255 (LEAVE), DD-006 (design on main; build gated), AST-DSL-PARITY (LEAVE),
  MCP-LEAN-DEFAULT, CONTINUOUS-REFRESH, RUST-REPLACE-TOCTOU.

## Retained laws from 2026-08-15 (additions)

- **A117** Skip-Fable waives that docs seat only — not product/spend/CEO flips (extends A74).
- **A118** Local `gh pr merge` failure ≠ remote; judge `mergedAt` / API when worktree owns `main`.
- **A119** Docs PR skipped jobs ≠ cheap main push (full matrix on main).
- **A120** Shell timeout must strictly exceed probe duration (+ grace).
- **A121** `request_queue_size` raise without aggregate pre-auth cap enlarges DoS (R7).
- **A122** Demand SATISFIED + design on main ≠ SHIPPED (need PERF+HONESTY build).

Prior laws A70–A116 remain historically true for their dates; do not flatten mixed dispositions.
Full closed-world status: `docs/TASK_BOARD.md` + `docs/audits/2026-08-15-ceo-backlog-update.md`.
