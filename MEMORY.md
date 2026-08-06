# tensor-grep durable campaign memory

Last updated: 2026-08-06 (CEO update)

## Resume here

1. Public product is **`v1.110.0`**. CEO packet:
   `docs/audits/2026-08-06-ceo-backlog-update.md`. Board index **`2026-08-06.1`**.
2. Closed-world: **28 / 17 unfinished** = 6 READY, 0 IN_FLIGHT, 5 CEO_GATED, 6 DEMAND_GATED.
   F7, CPU-BACKEND, REF-CALL-REGISTRY are **SHIPPED** (closeout in the 2026-08-06 CEO docs PR).
3. **Task 2A RED remains correctly blocked** — do not call merge-ready. Historical local SHA
   `6367614960327b1a4e00301c8bfdb9b2e4bb453e`, Sol `FIX-FIRST` / 10 HIGH unless a newer RED
   artifact replaces it. #89/#90 stay READY behind that gate.
4. Enterprise launch bar: CUJ integration `#958` + published-wheel dogfood `#962`. STOP: W3
   rust/e2e on shared-box cargo, MCP wire-contract fence, #169 spend, silent CEO-gate flips.
5. No spend. No nonfinancial CEO question. #169 is the only financial stop.
6. New laws **A70–A76** (ambient signing-key pollution; canonical-index free-form ban; tracker
   IN_FLIGHT debt; bare-wheel find; quota substitute SHIP; premise-check queue; ordinal freshness).

## External state at the snapshot

- Public release: `v1.110.0`; PyPI reports `tensor-grep 1.110.0`.
- `origin/main` tip at CEO write: `5341754` (may advance; re-derive before merge).
- Open PRs: derive with `gh pr list --state open` (empty at CEO write after #962).
- Open GitHub issues: #48 (and any newer — re-derive).
- Financial spend: none incurred or authorized.

## Queue (unfinished 17)

- READY: #89, #90, F5, F6, F8, MCP-SURFACE.
- CEO_GATED nonfinancial: #48, #72, #77, #131 (recommendations only; unchanged).
- Financial: #169 only.
- DEMAND/research: #255, DD-006, AST-DSL-PARITY, MCP-LEAN-DEFAULT, CONTINUOUS-REFRESH,
  RUST-REPLACE-SYMLINK.

## Retained laws from 2026-08-06 (additions)

- **A70** Ambient default Ed25519 key pollutes `--sign` no-key RED — isolate HOME/USERPROFILE.
- **A71** No free-form bullets under TASK_BOARD `## Canonical status index`.
- **A72** Merged impl + stale IN_FLIGHT = board debt; close with Closure PR + Merged SHA.
- **A73** Bare wheel lacks semantic extras; do not dogfood `tg find` quality without them.
- **A74** Quota-blocked Sol/Fable SHIP is provisional; re-gate when quota returns.
- **A75** Premise-check ready queue (#935) — already-shipped items look “ready.”
- **A76** Board freshness = ordinal CHANGELOG distance (#933).

Prior laws A34–A69 and the 2026-08-03 MEMORY body remain historically true for their dates; do not
flatten mixed dispositions. Full closed-world status: `docs/TASK_BOARD.md` +
`docs/audits/2026-08-06-ceo-backlog-update.md`.
