# Feature batch

Parked queue for session closeout. **No new feature started.**
Rows without `features/<name>/spec.md` are named out loud: they have **not** been through `new-feature`.

| # | Feature | Spec | Status | Failed passes | PR |
|---|---|---|---|---|---|
| 1 | docs-reconcile | *(no spec.md — plan is `docs/plans/DOCS-RECONCILE.md`)* | done | 0 | #1123 |
| 2 | handler-census-w2-wave1 | *(no spec.md — plan is `docs/plans/HANDLER-CENSUS-W2.md`)* | done | 0 | #1124 |
| 3 | handler-census-w2-wave2 | *(no spec.md — same plan, wave 2, ARCH-002+SEC-007)* | done | 0 | #1125 (`5816afe`, `b470750`) |

Queue empty as of 2026-09-10 session closeout. All three rows shipped and merged (verified: `gh pr view 1123/1124` MERGED, `5816afe`/`b470750` on `origin/main`). Reconciled during closeout after the plan doc's own header ("ALL WAVES COMPLETE") was cross-checked against real commits rather than trusted at face value.
