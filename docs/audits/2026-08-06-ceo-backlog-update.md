# CEO Backlog Update — 2026-08-06 (dumbed down)

## Bottom line (one screen)

The product is healthy and **newer than the last CEO note**. Public install is **`tensor-grep 1.110.0`**
(tag `v1.110.0`). `origin/main` tip at write time: `5341754` (docs dogfood receipt after the CUJ lock).

Since the **2026-08-03** CEO update we shipped a lot of real product work (languages, backends,
enterprise CUJ lock, published-wheel dogfood) and retired two research rows with receipts. The backlog
is **not done**. Task 2A (WSL↔Windows security RED) is **still correctly blocked**. No spend. No
question for the nonfinancial CEO gates. **#169** is still the only money stop.

Closed-world board after this reconcile: **28 rows total**, **17 unfinished** =
**6 READY** + **0 IN_FLIGHT** + **5 CEO_GATED** + **6 DEMAND_GATED**.
(Terminal: **7 SHIPPED** + **4 RETIRED**.)

## What worked

- **Public release moved**: last CEO note said `v1.102.1`; today PyPI/`tg --version` is **`1.110.0`**.
- **#911 dependency-floor PR merged** (2026-08-04) — the security gate that blocked the old green head
  did its job; floors landed.
- **Language depth (F7)**: all ten top languages are parser-backed in-file; Task 11 cross-file waves
  for Java/PHP/C#/C/C++ merged (`#950`/`#952`/`#955`/`#957`).
- **Backend honesty (CPU-BACKEND)**: Python twin no longer drops `invert_match` on a TypeError retry
  (`#923`); Rust public `replace_in_place` hardened (`#925`).
- **Registry seam (REF-CALL-REGISTRY)**: refs/callers dispatch is registry-driven (`#915`) and pinned
  (`#940`).
- **Research cleanup**: **F10 MaxSim** and **DD-004** RETIRED with dated receipts (`#953`) — no longer
  pretend they are live build work.
- **Enterprise launch bar**: prepare → signed evidence → review-bundle verify is locked in CI (`#958`)
  and dogfooded on the **published** `1.110.0` wheel (`#962` /
  `docs/audits/2026-08-06-enterprise-w5-dogfood.md`).
- **Workspace refuse honesty**: multi-project parent refuse uses `workspace_root_refused` (`#956`).
- **Board instrumentation**: freshness is ordinal CHANGELOG distance (`#933`); backlog self-consistency
  gate (`#951`); free-form “campaign note” under the canonical index is illegal (caught by CI on #962).

## Every unfinished backlog item (17) — plain English

### Ready to build (6)

1. **#89 — WSL path → Windows search.** Typing a `/mnt/c/...` path into a Windows-native search still
   fails for a real path. Blocked on Task 2A RED reaching Sol `SHIP` + real Windows CI first.
2. **#90 — WSL scan looks “clean” when it is not.** Windows ast-grep can get a Linux path and report
   zero matches while the translated path finds hits. Same Task 2A/2B gate as #89.
3. **F5 — Edit-ready / claims fence.** Strict, race-safe “I am ready to edit” coordination (Task 8).
   Partial typed API exists (`#943`); full fence not launch-claimed.
4. **F6 — Edit verification / `verify-edit`.** Shared verification service + public CLI (Tasks 6–7).
5. **F8 — Federated workspace prepare.** Multi-root prepare service/CLI/MCP (Tasks 12–13).
6. **MCP-SURFACE — Incomplete-result disclosure on MCP.** Task 4 residue; wire-contract fence still
   applies (do not expand MCP shapes without Task 2C sequencing).

### CEO decision-gated — nonfinancial (4) — recommendations only, status unchanged

7. **#48 — Startup architecture.** Recommendation: keep the shipped hybrid (native front door +
   Python sidecar); do not fund a rewrite unless pip/uv parity is a business priority.
8. **#72 — Public benchmark claim.** Recommendation: HOLD the old public 7.5x wording; only a
   zero-spend fresh six-repo/180-task quality-gated run may reopen public wording, and wording still
   needs CEO approval.
9. **#77 / F9 — Ledger enforcement.** Recommendation: stay local opt-in advisory; no auth/CI blocking.
10. **#131 — Publish GPU native assets.** Recommendation: optional experimental NVIDIA asset, CPU
    default/fallback, **no** speed claim. Physical proof/spend is separate (#169).

### CEO financial stop (1)

11. **#169 — Physical GPU proof / spend.** The only mandatory money gate. Do not rent/buy hardware
    without approval.

### Demand / research gated (6) — needs research or external demand before build

12. **#255 — Many-pattern dedup / compression / native investment.** Needs demand + a bounded parity
    experiment or approved investment case.
13. **DD-006 — Daemon load / DoS.** Needs measured concurrent-load evidence (research + probe), not
    a speculative rewrite.
14. **AST-DSL-PARITY — Full structural DSL parity.** Needs customer demand + a preprocessor-aware
    oracle design.
15. **MCP-LEAN-DEFAULT — Lean MCP default.** Needs client demand + compatibility evidence.
16. **CONTINUOUS-REFRESH — Warm session / search-index serving.** Needs measured latency demand + an
    approved persistent-index design (big refactor; daemon today holds a symbol map, not a search index).
17. **RUST-REPLACE-SYMLINK — Direct-leaf replace symlink policy.** Needs a concrete threat model and
    downstream compatibility decision.

## Terminal rows (11) — not unfinished

**SHIPPED (7):** `#36`, `#37`, `#109`, `#859`, **`F7`**, **`CPU-BACKEND`**, **`REF-CALL-REGISTRY`**
(this reconcile closes the three IN_FLIGHT program rows whose implementation PRs already merged).

**RETIRED (4):** `#22`, `F2`, **`F10`**, **`DD-004`**.

## Still blocked (not on the 17, but still load-bearing)

- **Task 2A RED** remains local/unpushed at historical SHA
  `6367614960327b1a4e00301c8bfdb9b2e4bb453e` with Sol `FIX-FIRST` / 10 HIGH blockers unless a newer
  RED artifact replaces it. No Actions run ⇒ no clearance. Do not call it merge-ready.
- **STOP this wave (unchanged):** W3 rust/e2e halves on shared-box cargo ban; MCP lean/full disclosure
  wire-contract fence; silent CEO-gate reclassification; #169 spend.

## What needs research (explicit list)

| Item | Why research, not “just build” |
|---|---|
| #255 | Demand + parity experiment design before native/compression spend |
| DD-006 | Need concurrent-load measurements / DoS evidence |
| AST-DSL-PARITY | Demand + preprocessor-aware oracle |
| MCP-LEAN-DEFAULT | Client demand + compatibility matrix |
| CONTINUOUS-REFRESH | Latency demand + persistent-index architecture |
| RUST-REPLACE-SYMLINK | Threat model + compatibility decision |
| #72 (if CEO wants a public claim) | Fresh six-repo/180-task quality-gated benchmark harness (zero-spend path only) |
| #131/#169 (if CEO wants GPU publish/proof) | Asset vs physical-proof separation; #169 is financial |

Exa/primary-source research from 2026-08-03 remains valid for #48/#72/#77/#131 recommendations;
F10/DD-004 research closed into RETIRED receipts.

## 5+ lessons since the 2026-08-03 CEO update

1. **Ambient default signing keys pollute `--sign` “no key” probes.** Clearing `TG_EVIDENCE_SIGNING_KEY`
   is not enough if `~/.tensor-grep/keys/evidence_ed25519.key` exists. Isolate `HOME`/`USERPROFILE`
   (or remove the default key) or the NEG arm signs and looks green. Receipt: W5 dogfood / #962.
2. **Free-form bullets under `## Canonical status index` break the tracker parser.** Only
   `Status:`/`PR:`/`Trigger:` checklist rows may live there. Campaign prose goes in a separate
   heading. Receipt: #962 CI red → fixed by moving the note.
3. **Merged code with a stale `IN_FLIGHT` row is board debt, not unfinished product.** Reconcile
   F7 / CPU-BACKEND / REF-CALL-REGISTRY to `SHIPPED` at completion (A50 / “committed is not shipped”
   twin: **merged is not tracker-closed**).
4. **Bare `uvx tensor-grep==X` is not the semantic/`tg find` surface.** No `model2vec` ⇒ find
   degrades; enterprise CUJ dogfood must use prepare/search/evidence/review-bundle/ledger (or install
   `tensor-grep[semantic]` / `tg install-dense` first).
5. **Quota-blocked Sol/Fable is not “SHIP forever.”** An orchestrator substitute SHIP is provisional;
   re-dispatch the independent vendor seat when quota returns for security/load-bearing claims.
6. **Premise-check the ready queue before dispatching builds.** #935: six “ready” items were already
   shipped — plans against fixed bugs have perfectly resolving citations.
7. **Board freshness is ordinal CHANGELOG distance, not patch arithmetic.** A minor bump must not
   fire a sentinel that no tolerance can absorb (`#933`).

Codified as **A70–A76** in `AGENTS.md`; mirrored into skills + `MEMORY.md`.

## Next (engineering, no CEO question)

1. Keep Task 2A blocked until RED → Sol `SHIP` → real Windows CI.
2. Drain READY items only behind WIP/release gates: F5/F6/F8/MCP-SURFACE after their own plans;
   #89/#90 only after Task 2A.
3. Do not build DEMAND_GATED rows without demand/research packets.
4. Optional: re-run Sol on #958 CUJ when quota restores (orchestrator SHIP already posted).

No spend requested. No nonfinancial CEO question.

## SUPERSEDED — board stamp 2026-08-06.2 (do not rewrite the counts above)

The closed-world line **6 READY** above was true at write time. PR #964 stamped the six false-READY
rows (**#89**, **#90**, **F5**, **F6**, **F8**, **MCP-SURFACE**) to **BLOCKED** under program ownership
(Task 2A / Tasks 4–13). Live unfinished disposition after that stamp: **0 READY** + **6 BLOCKED** +
**5 CEO_GATED** + **6 DEMAND_GATED** (still **17 unfinished** / **28 rows**). This appendix is the
correction; the dated counts above stay as the receipt.
