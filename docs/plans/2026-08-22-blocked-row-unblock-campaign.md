# Plan — unblock the cargo-gated backlog rows (2026-08-22)

**Status:** DRAFT, pending thinktank audit. Do not implement any wave before the council approves
this document and the orchestrator records the approval below.

## 0. Premise check (verify-plan-against-code Step 0)

A plan against an already-fixed problem has perfectly resolving citations. So the premise is stated
first and was MEASURED, not inferred:

| Premise | How it was checked | Result |
|---|---|---|
| F5 / F8 / Task 2C share ONE stated blocker | `docs/BACKLOG.md` rows quoted verbatim below | CONFIRMED |
| That blocker is "cargo + e2e forbidden on the shared box" | quoted from the rows themselves | CONFIRMED |
| A CPU-capped container removes it | ran `tests/e2e/test_routing_parity.py` in `scripts/ci-local/` | **68 passed** |
| The rust lane runs in that container | `scripts/ci-local/run.sh rust` | `rust lane exit: 0` |
| MCP contract live value is 1.7.0 (not 1.8.0) | `grep _TG_MCP_SERVER_CONTRACT_VERSION src/tensor_grep/cli/mcp_server.py` | `= "1.7.0"` at :188 |

The blocker rows, verbatim from `docs/BACKLOG.md`:

- **F5** edit-ready (Task 8) — *"Steps 3-5 modify `rust_core/**` and `tests/e2e/**` -- cargo and the
  e2e routing suite are forbidden on this shared box, so they need CI or a cloud seat."*
- **F8** workspace (Tasks 12-13) — *"modifies `rust_core/src/main.rs`, `path_domain.rs` and
  `tests/e2e/test_routing_parity.py`. Same constraint."*
- **Task 2C** — *"needs CI or a cloud seat; modifies `rust_core/src/main.rs`; verifying it requires
  `cargo`... Also needs a real WSL host for the `/mnt/c/...` path-domain arms."*
- **MCP-SURFACE** (Task 4) — *"BLOCKED on Task 2C"*; Task 4 bumps 1.8.0 -> 1.9.0 while live is
  **1.7.0**, so building it first bumps from a version that does not exist.

## 1. What this plan claims, and what it does NOT

**Claims:** `scripts/ci-local/` (PR #1093) removes the *cargo* half of the stated blocker for F5,
F8 and Task 2C, and therefore transitively for MCP-SURFACE.

**Does NOT claim:**
- It does not remove the **WSL path-domain** half of Task 2C's blocker (`/mnt/c/...` arms). That is
  a SEPARATE, still-open constraint and Task 2C cannot fully close without it.
- It does not make the harness a merge arbiter. Every wave still lands via GitHub CI.
- It does not cover windows-latest / macos-latest. Any row whose risk is OS-specific keeps that
  risk.

**This distinction is the plan's most falsifiable part and the council should attack it first.**

## 2. Ordering (dependency-forced, not preference)

```
#1093 (harness)  ->  Task 2C (1.7.0 -> 1.8.0)  ->  MCP-SURFACE (1.8.0 -> 1.9.0)
                 ->  F5 steps 3-5   (independent of the MCP chain)
                 ->  F8 tasks 12-13 (independent of the MCP chain)
```

Task 2C strictly precedes MCP-SURFACE: bumping 1.8.0 -> 1.9.0 from a live 1.7.0 is incoherent.
F5 and F8 are independent of that chain and of each other; they may run in either order but NOT in
parallel against the same files (`rust_core/src/main.rs` is touched by F8 and Task 2C — a
COUPLING that forbids parallel worktrees on those two).

## 3. Per-wave contract (identical shape for every wave)

Each wave is ONE PR. No wave starts before the previous one is MERGED and its release verified.

1. **RED first.** Write the failing test before the implementation, and record the exact failure
   reason. A crash, an import error, or a setup failure is NOT a valid RED (A61).
2. **Build.** Builder tier = cursor-agent (free). Cursor gets implementation and tests; cursor
   NEVER gets a gate, verifier, registry, or CI config (use-cursor gate-evasion receipt).
3. **Local verify in the harness** — `scripts/ci-local/run.sh` — NOT as proof, as a fast pre-filter.
4. **Codex audit** of the branch diff against this plan. Repeat until no findings.
5. **Push, wait for GitHub CI green**, merge. GitHub remains the arbiter.
6. **Dogfood the published artifact** per-file after any release.
7. **Reconcile the board AT completion**, never "next cycle".

## 4. Security posture (non-negotiable per wave)

- Task 2C / MCP-SURFACE touch a CONTRACT VERSION. A contract bump is a 5th registration site
  (`_TG_MCP_SERVER_CONTRACT_VERSION`) and needs its validator test in the same PR.
- F8 touches `path_domain.rs` — path confinement. Any change there gets the adversarial-security
  gate (A3), not just a code review: junction vs symlink vs drive-absolute arms, and a hostile
  fixture that is PROVEN to bite before the probe runs.
- No wave may widen a deadline, raise a ratchet pin, or add a `skip` to make a lane green.

## 5. Open questions FOR THE COUNCIL (do not answer these in the plan)

1. Does the WSL path-domain half of Task 2C's blocker make Task 2C un-closable in this campaign,
   and if so should MCP-SURFACE be re-scoped to depend on a PARTIAL 2C?
2. Is `rust_core/src/main.rs` coupling between F8 and Task 2C severe enough to force strict
   serialization, or is a file-level split viable?
3. F6 is described as "multi-week" and is NOT cargo-blocked. Should it be in this campaign at all,
   or explicitly deferred with a stated trigger?
4. Is there a cheaper first wave that would falsify the whole premise early — i.e. one small
   rust_core change whose only purpose is to prove the harness-to-CI path end to end?

## 6. Approval

- [ ] thinktank council APPROVED (record seat verdicts + the artifact hash reviewed)
- [ ] orchestrator recorded approval here before any implementation

**No wave may start while either box is unchecked.**
