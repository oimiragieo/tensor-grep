# CEO Backlog Update — 2026-08-11 (dumbed down)

> Supersedes the live unfinished disposition in the 2026-08-06 PM packet
> (`docs/audits/2026-08-06-pm-ceo-backlog-update.md`). This file is the live closed-world snapshot.
> Tip at write: **`17ded5a`** (docs) / release **`v1.110.14`** (`a6242bb`).

## Bottom line (one screen)

Public install is **`tensor-grep 1.110.14`** — tag, GitHub release assets, and PyPI all serve it.
Since the last CEO note we shipped **8 code fixes + a stack of docs**, all merged, main CI green:

- **v1.110.11** (M16): Rust `tg scan` no longer drops composite rules / custom severity.
- **v1.110.12** (M17): a reused index can never serve a mismatched stored root.
- **v1.110.13** (A90): unknown commands now fail closed (exit 2, `unknown_command` + `nearest[]` on
  BOTH front doors) instead of silently printing search help.
- **v1.110.14** (doctor PATH honesty): doctor now shows `pypi_latest` / `installed_behind_pypi` /
  `shadow_launchers[]` / `installation_health` and a loud warning when your PATH `tg` is foreign,
  stale, or behind PyPI.
- **Audit fixes** H2/M1/M3/M14 (count-matches native route, checkpoint symlink refusal, LSP file-ops
  confinement, MCP contract-version ratchet), plus **#977** spend-smart CI (docs-only PRs skip the
  expensive matrix).
- **Skills/docs evolution at scale**: both "capture the 24h wave" waves landed — #1001 (audit of all
  skills, 21 stale stamps + 7 tier contradictions fixed, new `release-drift-check` skill, laws
  A94–A96) and #1002 (coverage gap analysis, new `worldclass-roadmap` skill, five existing skills
  extended, laws' lessons folded in).

The launch bar (Phase 0+1 CUJ lock #958 + published-wheel dogfood #962, 22/22 PASS) still holds.
**No new open PRs** beyond the park: Task 2A draft **#966** (RED by design). No release is in flight.
**No spend. #169 is still the only money stop. No nonfinancial CEO question is open** — the CEO
recommendation packets are unchanged.

Closed-world board: **28 rows total / 17 unfinished** =
**0 READY** + **6 BLOCKED** + **0 IN_FLIGHT** + **5 CEO_GATED** + **6 DEMAND_GATED**.
(Terminal: **7 SHIPPED** + **4 RETIRED**.) Index version **`2026-08-11.1`**.

## What worked

- **The tool shipped real code, fast, with the gates holding.** Eight fixes across four releases in
  three days, every one behind codex/CI, and the first two Rust ones proved why the "first CI
  compile is the typecheck" rule (A87) exists — both #987/#988 passed static audits, both failed
  first compile (E0599/E0308/E0382). The gates caught it; nothing shipped broken.
- **A90 closed the trust hole.** `tg edit-ready --help` used to pretend the command existed (search
  help, exit 0). Now exit 2 on both front doors with the nearest real commands named. Agents can
  stop believing fiction.
- **Doctor got honest about PATH.** `shadow_launchers` + `installation_health` + a human warning line
  means a foreign/stale `tg` is now unmissable — the A96-era doctor surface is a real diagnostic, not
  a field dump.
- **CI cost cut (#977).** Docs-only PRs stop burning the 6-runner matrix. The `changes` job gates
  heavy jobs on code-touch; `release` still runs everything on main push (so no lost publish).
- **The skill library was made self-maintaining.** Two waves: an audit that fixed 28+ real inaccuracies
  (stamps, tier claims, doctor fields), then a coverage analysis that mapped every session law to a
  skill and created the `release-drift-check` + `worldclass-roadmap` skills. Post-merge verification
  on the merged artifacts (A29) confirmed all of it landed.
- **All lessons durable-captured** in AGENTS.md/CLAUDE.md (laws A83–A96) and the skills.

## Every unfinished backlog item (17) — plain English

### Blocked — not build licenses (6)

1. **#89 — WSL path → Windows search.** `/mnt/c/...` into Windows-native search still fails for a
   real path. Owned by Task 2A→2B. Do not product-GREEN until Sol `SHIP` + real Windows CI.
2. **#90 — WSL scan looks "clean" when it is not.** Raw Linux path can report zero matches while the
   translated path finds hits. Doctor half shipped (#571); scan half waits Task 2A/2B/2C.
3. **F5 — Edit-ready / claims fence (Task 8).** Step 2 typed snapshot shipped (#943). Steps 3–5 need
   `rust_core/**` + `tests/e2e/**` → CI/cloud (shared-box cargo ban).
4. **F6 — Edit verification / `verify-edit` (Tasks 6–7).** Step 0 shipped (#939). Remainder is
   multi-week (schemas, evidence, WSL path-domain, native verify-edit + e2e). This is the world-class
   S1 moat; escrow design is in the roadmap skill.
5. **F8 — Federated workspace prepare (Tasks 12–13).** Not a product surface yet; blocked on rust
   front-door + path_domain + e2e parity → CI/cloud.
6. **MCP-SURFACE — MCP incomplete-result / tool_surface disclosure (Task 4).** Blocked on Task 2C.
   Live MCP contract version is **`1.7.0`**; Task 4 plans `1.8.0→1.9.0` and must not bump from a
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
24. **#36** — skill-library drift audit corrections (#903; reopen on a new failing skill-drift receipt —
    the new `release-drift-check` skill is the standing sweep).

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
| Task 2A | Sol exact-byte on the **current tip under review** (not only archaeological RED SHA) + Windows CI | local/draft #966; not cleared |
| S1 verify-edit (world-class) | Escrow signing runtime (CI-held key that the editing agent cannot use) — design exists (#1002 skill), build is F6-scoped | `docs/plans/2026-08-09-worldclass-roadmap.md` |

## Task 2A (security gate — not a READY row)

- Historical RED object: `6367614960327b1a4e00301c8bfdb9b2e4bb453e` (local; never on `origin/main`).
- Repair lineage: draft PR **#966** (`test: Task 2A FIX-FIRST Sol R3 (not GREEN)`) — RED by design,
  do-not-merge. Sol has returned FIX-FIRST rounds; implementer receipts ≠ clearance.
- **STOP:** no #89/#90 GREEN; no Windows CI clearance without an Actions run on the exact tip (A68).
  Re-derive tip SHA before any gate (`gh pr view 966` / worktree `rev-parse`).

## Lessons since the last CEO update (A77–A82 packet superseded by A83–A96 + the skill waves)

1. **A83 — Front-door argv rewrites shadow each other.** A normalizer that rewrites argv quietly
   changes what flags the real command receives — a flag that works in a unit test can vanish through
   the real binary. Census the rewrite list AND the target parser, not just the guarded door.
2. **A87 — Static review is not a typecheck.** Two Rust audit-fixes looked clean to codex review and
   both failed the FIRST real CI compile (E0599/E0308/E0382). "SHIP" from AI review is
   `SHIP-PROVISIONAL` until the code actually compiles in CI — the first compile IS the Rust gate.
3. **A88 + A89 — Dogfood fixtures must BITE, and parity arms must be real.** A "hostile" directory that
   isn't actually unreadable makes the test prove nothing (check the setup bit before trusting the
   result); and a parity test whose "real" arm is a stub proves only that the stubs agree with each
   other. Enumerate the real producer.
4. **A90 + A91 — Unknown commands fail closed on BOTH doors; a Python feature must enroll the native
   binary.** `tg edit-ready --help` used to print search help, exit 0 — agents believed a command
   existed. Now exit 2 with `nearest[]`. And every new command/flag needs all 4 registration sites
   (`KNOWN_COMMANDS`, native passthrough, parity test, `@app.command`; search flags also the two
   front-door allowlists) or it silently misroutes — a feature that is honest only as "no core-rust
   logic" is invisible through the real binary.
5. **A92 + S1 — Evidence must be escrowed to a key the editing agent does NOT hold.** An agent
   attesting "validation passed" is self-attestation (Oracle Form 8). The moat is a CI-held-key-signed
   receipt (stdout-hash + exit + duration) with fail-closed-on-drift (base_sha + tree fingerprint).
   Occasio's OIDC/Sigstore GitHub-Actions attestations are the industry precedent — this is a real
   pattern, not an invention.
6. **A93 — Self-dogfood is self-consistency, not demand.** 22/22 PASS on tg dogfooding tg proves tg
   works for itself, not that customers want the next feature. Premise-check every "banked/shipped"
   roadmap claim against origin/main before a design council reads it.
7. **A94–A96 — The skill library rots one release after every refresh; make it self-maintaining.**
   21 stale version stamps + 7 tool-tier contradictions were found ONE release after the last audit.
   Fix: `tensor-grep-release-drift-check` is a standing post-release sweep (NOT a pytest — the numbers
   drift by design). Also: a "**N skills** VERIFIED CORRECT — do not fix" note is itself a contract
   site — update it in the same change that breaks it; and non-ASCII punctuation (em/en dashes)
   defeats byte-exact edit tools — splice by line index in a script, never re-type the line.
8. **The 24-hour-evolution capture loop works.** "Capture what we learned → audit all skills → close
   coverage gaps with new/existing skills (Exa-grounded) → re-register index/registry/workflow → post-
   merge verify on the merged artifact" produced #1001 + #1002 with zero merge-race issues and green
   gates throughout. Run it again after the next code release.

## Next (engineering, no CEO question)

1. Task 2A: Sol exact-byte on the **current tip** → draft only until Windows CI on that SHA.
2. Keep F5/F6/F8/MCP-SURFACE BLOCKED; route rust/e2e halves to CI/cloud.
3. Do not flip CEO_GATED or spend #169.
4. After the next code release, run the `tensor-grep-release-drift-check` sweep (A94) before any new
   skill work.
5. When Anthropic/Sol quota returns, re-seat security audits (A74/A78) — substitute SHIP is provisional.

No spend requested. No nonfinancial CEO question.
