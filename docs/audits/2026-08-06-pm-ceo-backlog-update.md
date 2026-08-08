# CEO Backlog Update — 2026-08-06 PM (dumbed down)

> Supersedes the **live** unfinished disposition in the morning packet
> (`docs/audits/2026-08-06-ceo-backlog-update.md`). Keep the morning file as the receipt for A70–A76
> and for the pre-stamp “6 READY” counts. This file is the live closed-world snapshot.

## Bottom line (one screen)

Public install is still **`tensor-grep 1.110.0`**. Docs closes (#963/#964/#965) landed on `main`; tip
at write: **`bb4fdae`**. **No new release** from those merges (docs/test titles).

What changed since the morning CEO note: we **stopped treating six board READY rows as build
licenses**. Thinktank said amend the spine; #964 stamped them **BLOCKED**. Task 2A repairs exist
**locally / draft only** — still **not** cleared for merge. Absolute “Jarvis enterprise launch ready”
is **not** claimed. No spend. No nonfinancial CEO question. **#169** is still the only money stop.

Closed-world board: **28 rows total**, **17 unfinished** =
**0 READY** + **6 BLOCKED** + **0 IN_FLIGHT** + **5 CEO_GATED** + **6 DEMAND_GATED**.
(Terminal: **7 SHIPPED** + **4 RETIRED**.) Index version **`2026-08-06.3`**.

## What worked

- **Morning CEO packet + A70–A76** landed (#963) and stuck as durable law text.
- **False READY stamped BLOCKED** (#964): #89, #90, F5, F6, F8, MCP-SURFACE — board now matches
  BACKLOG reconcile (0 start-now product builds).
- **R0 research packets** written (CEO recommendations + DEMAND receipts) without flipping gates.
- **Closeout campaign state** refreshed (#965) with honest Task 2A Sol R2 status (not GREEN).
- **Published product still healthy**: `uvx --from tensor-grep==1.110.0 tg --version` →
  `tensor-grep 1.110.0`. Phase 0+1 CUJ lock (#958) + wheel dogfood (#962) remain the launch *bar*,
  not a claim that every enterprise surface is finished.
- **Thinktank AMEND_SPINE held**: MCP-SURFACE / F5–F8 product waves correctly stayed STOP.

## Every unfinished backlog item (17) — plain English

### Blocked — not build licenses (6)

1. **#89 — WSL path → Windows search.** `/mnt/c/...` into Windows-native search still fails for a
   real path. Owned by Task 2A→2B. Do not product-GREEN until Sol `SHIP` + real Windows CI.
2. **#90 — WSL scan looks “clean” when it is not.** Raw Linux path can report zero matches while the
   translated path finds hits. Doctor half shipped (#571); scan half waits Task 2A/2B/2C.
3. **F5 — Edit-ready / claims fence (Task 8).** Step 2 typed snapshot exists (#943). Steps 3–5 need
   `rust_core/**` + `tests/e2e/**` → CI/cloud, not shared-box cargo.
4. **F6 — Edit verification / `verify-edit` (Tasks 6–7).** Step 0 only (#939). Remainder is
   multi-week (schemas, evidence, WSL path-domain, native verify-edit + e2e).
5. **F8 — Federated workspace prepare (Tasks 12–13).** Not started as a product surface; blocked on
   rust front-door + path_domain + e2e parity → CI/cloud.
6. **MCP-SURFACE — MCP incomplete-result / tool_surface disclosure (Task 4).** Blocked on Task 2C.
   Live MCP contract version is **`1.7.0`**. Task 4 plans `1.8.0→1.9.0` and must not bump from a
   nonexistent `1.8.0` base.

### CEO decision-gated — nonfinancial (4) — recommendations only, status unchanged

7. **#48 — Startup architecture.** Keep hybrid native front door + Python sidecar; do not fund a
   rewrite unless pip/uv parity is a business priority.
8. **#72 — Public benchmark claim.** HOLD old public speed wording; only a zero-spend fresh quality
   run may reopen wording, and wording still needs CEO approval.
9. **#77 / F9 — Ledger enforcement.** Stay local opt-in advisory; no auth/CI blocking.
10. **#131 — Publish GPU native assets.** Optional experimental NVIDIA asset, CPU default/fallback,
    **no** speed claim. Physical proof/spend is separate (#169).

### CEO financial stop (1)

11. **#169 — Physical GPU proof / spend.** The only mandatory money gate. Do not rent/buy hardware
    without approval.

### Demand / research gated (6) — needs research or external demand before build

12. **#255 — Many-pattern dedup / compression / native investment.** Needs demand + bounded parity
    experiment or approved investment case.
13. **DD-006 — Daemon load / DoS.** Needs measured concurrent-load evidence, not a speculative rewrite.
14. **AST-DSL-PARITY — Full structural DSL parity.** Needs customer demand + preprocessor-aware oracle.
15. **MCP-LEAN-DEFAULT — Lean MCP default.** Needs client demand + compatibility evidence.
16. **CONTINUOUS-REFRESH — Warm session / search-index serving.** Needs measured demand + approved
    persistent-index design (daemon today holds a symbol map, not a search index).
17. **RUST-REPLACE-SYMLINK — Direct-leaf replace symlink policy.** Needs concrete threat model +
    downstream compatibility decision.

## Terminal rows (11) — still part of the closed world (ALL backlog)

### SHIPPED (7)

18. **#37** — grammar-dependent Windows test marked (#908).
19. **#109** — CUDA implicit-walk ceiling (#605).
20. **#859** — AST writer census / anchored publication (#913/#918/#920).
21. **F7** — language registry + cross-file waves (#950/#952/#955/#957; closure #963).
22. **CPU-BACKEND** — Python/Rust backend honesty (#923/#925; closure #963).
23. **REF-CALL-REGISTRY** — registry-driven refs/callers (#915/#940; closure #963).
24. **#36** — skill-library drift audit corrections (#903).

### RETIRED (4)

25. **#22** — GPU exit-2 calibration retired (exit contract clarified).
26. **F2** — anonymous-agent sentinel retained on purpose.
27. **F10** — MaxSim late-rerank DROP (uninstallable + golden-set negative; #953).
28. **DD-004** — typed-boundary loud failure already banked (#953).

## Research still needed (before any of these become builds)

| ID | Research ask | Packet / note |
|---|---|---|
| #48 #72 #77 #131 | Nonfinancial CEO decisions — recommendation packets only; **do not flip status** | `docs/audits/2026-08-06-ceo-gated-recommendation-packets.md` |
| #169 | Financial / hardware — **ask before spend** | same packet; only money stop |
| #255 | Bounded many-pattern dedup parity experiment design | `docs/audits/2026-08-06-demand-gated-research-receipts.md` |
| DD-006 | Concurrent daemon load / DoS measurement plan | same |
| AST-DSL-PARITY | Demand signal + preprocessor-aware oracle shape | same |
| MCP-LEAN-DEFAULT | Client demand + compatibility matrix for lean default | same |
| CONTINUOUS-REFRESH | Warm-session demand + search-index service design | same |
| RUST-REPLACE-SYMLINK | Untrusted-destination threat model + compatibility | same |
| Task 2A | Sol exact-byte on **tip under review** (not only archaeological RED SHA) + Windows CI | local/draft; not cleared |

## Task 2A (security gate — not a READY row)

- Historical RED object: `6367614960327b1a4e00301c8bfdb9b2e4bb453e` (local; never on `origin/main`).
- Repair lineage advanced locally / draft PR **#966** (`test: Task 2A FIX-FIRST Sol R3 (not GREEN)`).
- Sol has returned **FIX-FIRST** rounds; implementer receipts ≠ clearance.
- **STOP:** no #89/#90 GREEN; no claim of Windows CI clearance without an Actions run on the exact tip
  (A68). Re-derive tip SHA before any gate (`gh pr view 966` / worktree `rev-parse`).

## Lessons since the morning CEO update (A77–A82)

1. **A77 — Stdin+heredoc pollers lie.** Piping `gh pr checks` into a heredoc that consumes stdin can
   yield an empty checklist that reads as ALL_TERMINAL — #963 merged while checks were still pending.
   Write checks to a file; require named heavy lanes present.
2. **A78 — Usage-limit seats are FAILED.** Pro/Opus/Sol “hit your usage limit” is a failed seat
   (extends A10/A58/A74), not pending approval. Reset ~2026-08-14 unless Spend Limit allows earlier.
3. **A79 — Status stamps must retarget governance pins.** Stamping READY→BLOCKED reddened CI until
   tracker tests allowed lawful BLOCKED for program-owned rows.
4. **A80 — Gate tip bytes, not the archaeological SHA.** Docs naming `6367614` while the repair tip
   moved (`fef0267` → later commits) is an A51 artifact mismatch waiting to happen.
5. **A81 — HIGH repair receipts ≠ Sol SHIP.** Self-GREEN / receipt files / “all 10 applied” claims are
   hypotheses until exact-byte Sol `SHIP`.
6. **A82 — AMEND_SPINE over “build all READY”.** When board READY contradicts BACKLOG BLOCKED, START_NOW
   is docs/R0/D1 only — not MCP/F5–F8 product builds.

## Next (engineering, no CEO question)

1. Task 2A: Sol exact-byte on the **current tip** → draft only until Windows CI on that SHA.
2. Keep F5/F6/F8/MCP-SURFACE BLOCKED; route rust/e2e halves to CI/cloud.
3. Do not flip CEO_GATED or spend #169.
4. When Anthropic/Sol quota returns, re-seat security audits (A74/A78) — substitute SHIP is provisional.

No spend requested. No nonfinancial CEO question.
