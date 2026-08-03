# CEO Backlog Update — 2026-08-03

## Bottom line

The public product is healthy at `v1.102.1`. The latest exact `main` CI run, `30793797849`, completed
successfully on `8024125612d5fb42481acde34d94ad39bbaa3c3e`; PyPI and the GitHub release both serve
`v1.102.1`. There is one open issue (`#48`), one open PR (`#911`), and no financial spend.

The planning/tracker PR's old head is green, but the backlog is not implemented. PR #911 at
`d12b4779439fb133fff134d89883a0678941a897` proves its committed tracker/governance version; it does
not prove the newer local Round-60 bytes. Those plan bytes are now approved: Cursor Auto found and
closed three stale contradictions, independent TDD returned `SHIP`, Sol returned substantive `SHIP`,
and Sol confirmed final raw hashes design
`31D8E071F1778A59888890445A0620000548AB270EFBE11F5F2E01A70E3D862B` / implementation
`AA64D0BA88BF98F07809065BD0E813B320C1CA7089804CDC1CD17FBB0B0826B3`. PR #911's previous
CI-proven head `bd07475092ec23187c45b65aa2fb8d3f2d2bfee8` passed run `30836698168`; later docs updates
require their own exact-head run. Task 2A RED design is active, but exact-byte Sol rejected `4efcad9`
and `8df269d`; no GREEN product implementation has started.

## What worked

- Public packaging and release health are clean at `v1.102.1`.
- PR #910 replaced the hand-maintained highlight reel with a machine-parsed, closed-world status index.
- Real WSL treatment/control tests overturned stale tracker claims: #89 and #90 are reproducible
  cross-domain defects and are correctly `READY`.
- PR #911 is clean/mergeable and its exact committed head passed every dispatched check.
- The plan loop did its job: architecture accepted the transaction shape, while security stopped eight
  enforceability gaps and TDD stopped a split-counter/off-by-one escape before code existed.
- Exa research found a conservative product direction for graph coding: add bounded, lexically anchored
  local graph context to the existing CLI/MCP loop instead of creating a mandatory graph runtime.

## Every unfinished canonical backlog item (23)

### Ready to build after its own gates (10)

1. **#89 — WSL→Windows search paths.** Translate typed filesystem operands before a Windows-native
   search; Task 2A's nine plan findings are approved but not implemented.
2. **#90 — WSL scan false-clear.** Prevent Windows ast-grep from receiving an untranslated Linux path
   and reporting a misleading clean result; shares Task 2A/2B/2C with #89.
3. **#859 — writer census and anchored publication.** Build the class-level AST writer census and fix
   every unsafe user-facing publication path, including generated Python.
4. **F5 — edit-ready/claims fence.** Make edit readiness strict, attributable, and race-safe (Task 8).
5. **F6 — edit verification.** Add the shared verification service and `verify-edit` surface (Tasks 6–7).
6. **F7 — language registry/cross-file resolution.** Finish registry-driven language navigation and
   cross-file resolution waves (Tasks 10–11).
7. **F8 — federated workspace prepare.** Add bounded multi-root service, CLI, and MCP parity (Tasks 12–13).
8. **MCP-SURFACE — incomplete-result disclosure.** Close the Task-4 MCP disclosure residue.
9. **CPU-BACKEND — backend twins.** Harden Rust and Python CPU backends without deleting public API
   or retaining unsafe retry behavior (Task 5).
10. **REF-CALL-REGISTRY — shared prepare service.** Extract the references/callers preparation service
    before its consumers (Task 9).

### CEO decision-gated, nonfinancial (4)

11. **#48 — startup architecture.** Decide the native-front-door startup direction.
12. **#72 — public benchmark claim.** Decide whether to publish a fresh, reproducible performance/
    retrieval claim; old numbers are not automatically reusable.
13. **#77/F9 — ledger enforcement scope.** Decide how broadly cross-agent coordination is enforced.
14. **#131 — GPU native assets.** Decide whether to publish the GPU-flavor native asset surface.

The current instruction says not to ask about nonfinancial choices, so these stay explicit and no
blocking question is raised.

### Financial approval required (1)

15. **#169 — physical GPU proof.** Requires approval before renting/buying hardware or incurring spend.

### Demand/research-gated (8)

16. **#255 — many-pattern dedup/compression/native investment.** Reopen only for demand plus a bounded
    parity experiment or approved investment.
17. **F10 — MaxSim.** Reopen for a reviewed activation or retirement decision backed by role-aware data.
18. **DD-004 — typed backend errors.** Reopen when a stable typed boundary has evidence and consumers.
19. **DD-006 — daemon load/DoS.** Reopen with measured concurrent-load evidence.
20. **AST-DSL-PARITY — full structural DSL parity.** Needs demand and a preprocessor-aware oracle.
21. **MCP-LEAN-DEFAULT — lean MCP default.** Needs client demand and compatibility evidence.
22. **CONTINUOUS-REFRESH — warm session/index serving.** Needs measured latency demand and an approved
    persistent-index design.
23. **RUST-REPLACE-SYMLINK — direct-leaf replacement.** Needs a concrete threat model and downstream
    compatibility decision.

The same closed-world index also carries five terminal rows that are not unfinished backlog: shipped
`#36`, `#37`, `#109`; retired `#22`, `F2`.

## Dependency-ordered work plan

1. **Plan gate:** Round-60 exact-hash Cursor→TDD→Sol approval is complete. Push the update to PR #911
   and require exact-head CI before the product PR claims this planning receipt.
2. **Cross-domain foundation:** Task 2A/#89 search first; Task 2B/#90 scan consumes the same contract;
   Task 2C closes run/index/MCP mutation twins. A separate closure PR moves #89/#90 to `SHIPPED` only
   after merged and published-artifact proof.
3. **Independent P0 hardening:** Task 3/#859 writer census, then Task 4/MCP-SURFACE, then Task 5/
   CPU-BACKEND. They are independent in code but stay sequential under the WIP/release gate.
4. **Edit workflow chain:** Task 6 creates the pure F6 service → Task 7 exposes `verify-edit` → Task 8
   consumes it for F5 `edit-ready`/claims-fence. A consumer cannot precede its service/registration.
5. **Navigation chain:** Task 9/REF-CALL-REGISTRY extracts shared dispatch → Task 10 adds five
   parser-backed language waves → Task 11 adds six truthful cross-file resolution waves (F7).
6. **Workspace chain:** Task 12 creates the federated F8 service/CLI → Task 13 exposes MCP parity.
7. **Graph-coding value gate:** Task 14 may prototype only after the navigation/workspace foundations;
   pin current ranking first and retire the candidate if correctness, latency, memory, or token value
   loses. It does not silently create a new default or persistent runtime.
8. **Closeout:** Task 15 records every demand/CEO/financial disposition; Task 16 performs independent
   audit, safe drain, merged-artifact checks, and published-wheel verdict-table dogfood.

The one-at-a-time starting order is therefore **2A → 2B → 2C → 3 → 4 → 5 → 6 → 7 → 8 → 9 →
10 → 11 → 12 → 13 → gated 14 → 15 → 16**. Nonfinancial CEO rows stay explicit under the current
instruction; #169 and any paid #255 experiment stop for financial approval.

## Task 2A plan gate — known blockers, not shipped code

These findings remain owned by #89/#90 rather than becoming flattering top-level “features”:

1. A signer Organization string is forgeable; require the Microsoft root chain policy, production-root
   thumbprints, exact offline WinTrust flags, and a foreign same-Organization RED.
2. Installer receipt authority must come only from a protected fixed installer-state root and a bound
   non-exportable CNG signature; PATH and install-command digests cannot authorize ownership.
3. PATH atomicity must name and use transacted registry APIs or fail closed—an abstract lock/CAS is not
   a Windows primitive.
4. Kill-on-close Jobs must deny both breakaway modes and child creation must omit the breakaway flag.
5. The search-input ledger must exist before every bootstrap/full/native/rg/sidecar route; an
   uninstrumented PCRE2 route must start zero downstream children.
6. PATH removal must compare an opened directory's volume/file identity, covering case, 8.3,
   extended-path, separator, and junction aliases.
7. CI receipts must be checked against independently derived live Actions/artifact identity plus JUnit
   and Rust node census; self-attested JSON is not evidence.
8. The exact cache-only/revocation WinTrust flags are a testable contract, not prose.
9. Per-file and combined pattern/ignore budgets need independent cap−1/cap/cap+1 REDs; split counters
   and rejecting the inclusive cap must fail unchanged tests.

## Exa research completed

- [LARGER](https://arxiv.org/html/2605.16352) supports lexically anchored, confidence-filtered local
  graph neighborhoods inside an existing agent/CLI loop. This is the best fit for tensor-grep's
  deterministic, fail-closed contracts.
- [RANGER](https://arxiv.org/html/2509.25257v1) shows graph retrieval can serve entity and natural-
  language queries, but its persistent graph/MCTS/embedding stack adds operational and ranking
  nondeterminism. Treat it as research input, not an architecture to copy wholesale.
- [Augment Context Services](https://docs.augmentcode.com/context-services/overview) demonstrates
  incremental hash/diff indexing and reusable CLI/MCP/HTTP context. Tensor-grep should benchmark a
  bounded incremental projection before considering a long-lived service.
- [Greptile](https://www.greptile.com/) markets file/function/dependency graphs plus parallel review
  agents. The relevant user need is compact dependency/call context at review time, not “graph” as a
  label.
- Microsoft documentation confirms the exact implementation primitives now named in Round 60:
  [Microsoft-root chain policy](https://learn.microsoft.com/en-us/windows/win32/api/wincrypt/ns-wincrypt-cert_chain_policy_para),
  [offline WinTrust flags](https://learn.microsoft.com/en-us/windows/win32/api/wintrust/ns-wintrust-wintrust_data),
  [Job Object containment](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects), and
  [transacted registry keys](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regcreatekeytransactedw).

## Research still needed

- **Task 2A implementation spike:** verify TxR availability/support policy on every supported Windows
  runner and define the fail-closed UX when unavailable—no fallback implementation by assumption.
- **Microsoft root allowlist maintenance:** derive and test a production-safe thumbprint update policy
  without weakening the foreign-root control.
- **Graph projection value gate:** benchmark a small lexically anchored local-neighborhood projection
  against current `map`/`callers`/`blast-radius`; pin ranking first and reject it if accuracy, latency,
  memory, or token cost loses.
- **Continuous refresh:** measure cold vs warm repeated agent queries before designing a daemon index.
- **MaxSim:** repeat with query/document-role-aware encoding before activation or retirement.
- **Daemon DoS:** collect scheduler-independent concurrent-load evidence before changing limits.
- **AST parity:** establish preprocessor-aware C/C++ and cross-OS grammar oracles.
- **GPU:** physical proof remains financial-gated; no local shared-machine saturation.

## Lessons learned since the prior CEO update

1. **A green board can still be wrong.** Treatment/control evidence, not narrative age, restored #89/#90.
2. **Approval belongs to exact bytes.** A green PR head says nothing about uncommitted amended plan bytes.
3. **Architecture approval is not security clearance.** The same transaction design can be coherent and
   still have forgeable authority or unenforceable primitives.
4. **Security prose must name a real API.** “Atomic PATH CAS” was not implementable until the plan named
   TxR calls and fail-closed behavior.
5. **Names are not identities.** Organization strings and PATH spellings cannot replace root-policy,
   thumbprint, handle, volume, and file-identity checks.
6. **Containment must prohibit escape.** Kill-on-close is incomplete while breakaway is allowed.
7. **A cap not wired at every door does not exist.** Bootstrap, native, rg, sidecar, and PCRE2 routes all
   need the same no-refund ledger before any child starts.
8. **Self-attestation is not independent proof.** CI identity must be re-derived from the live run and
   cross-checked against both Python and Rust censuses.
9. **Inclusive boundaries need their own oracle.** A test of only “over the limit” misses rejecting the
   valid exact cap and misses split-counter escapes.
10. **Static and live evidence have different jobs.** A committed manifest says what must run; a live
    receipt proves what this run actually ran. Never put live run IDs into the static manifest.
11. **Narrow review retries beat broad timeouts.** Exact-paragraph retries converged faster and used less
    model budget than re-running a giant plan prompt.
12. **A no-verdict council seat is a failed seat, not a blocker.** Record it, clean up, and obtain an
    independent surviving verdict instead of waiting forever.
13. **Discover deferred tools before declaring one missing.** Exa was available in the deferred tool
    catalog even though it was not in the first visible list.
14. **The newest canonical worktree wins.** Hash and review one named artifact; never promote an older
    dirty root copy because it is convenient.
15. **Virtual environments have an OS owner.** WSL `uv --project /mnt/c/...` can replace the Windows
    `.venv`; keep WSL worktree and canonical Windows verification environments physically separate.

## Next action

PR #911 is pushed and exact-head CI is green. Repair the isolated Task 2A RED until Sol returns exact-byte
`SHIP`; only then start GREEN product implementation. PR #911 itself is not product code and does not
authorize its own merge; #89 stays `READY` until the real implementation PR exists.
