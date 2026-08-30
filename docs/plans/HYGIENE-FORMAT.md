# HYGIENE-FORMAT — RETIRED (premise falsified)

| Field | Value |
|---|---|
| Status | **RETIRED** — do not merge `docs/hygiene-format-2026-08-30` |
| Date | 2026-08-30 |
| Base SHA | `e6ba187faadd1a3cd5b1f8d5922bc220f0b544f6` |

---

## Original goal

Clear P3 **HYGIENE-001** — reformat 15 markdown files failing local `ruff format --check --preview`.

---

## Premise check (A75) — FALSIFIED

**Probe:** For each of the 15 frozen paths, pipe the **git blob** (not working-tree disk) through ruff:

```powershell
git show "origin/main:$f" | uv run --no-sync ruff format --check --preview --stdin-filename $f -
```

**Result:** **15/15 PASS** on `origin/main` blobs.

**False RED cause:** Windows `core.autocrlf=true` + `*.md` absent from `.gitattributes` `eol=lf`. Fresh worktree disk shows **16** would-be reformatted files; canonical checkout with prior session disk state shows **0**. Neither reflects a blob defect CI would ship.

**Hollow commit hazard:** Branch `docs/hygiene-format-2026-08-30` @ `c8a978b` committed orchestrator/plan files only — **zero** markdown formatting diffs vs `origin/main`.

---

## Optional follow-up (separate slice)

Add `*.md text eol=lf` to `.gitattributes` so Windows devs get deterministic format checks on disk. Not required for CI green on Linux.

---

## Frozen population (reference — no action)

The 15 paths listed in the 2026-08-30 draft remain the historical audit set; blobs already comply on `origin/main`.
