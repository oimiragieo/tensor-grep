# CEO Backlog Update — 2026-08-03 (continuation)

## Bottom line

The public product is healthy. Planning PR #911 is merge-ready on its last observed exact head.
The backlog is not done. Task 2A is correctly blocked — not merge-ready.

Public release stays `v1.102.1` on PyPI and GitHub. `origin/main` is
`8024125612d5fb42481acde34d94ad39bbaa3c3e`. There is one open issue (`#48`), one open PR (`#911`),
and no spend.

At the last external observation, PR #911 head
`01f276fa7c0d3d0e04fdb5feae78c29c1b194773` was CLEAN/MERGEABLE with CI run `30842604458`,
security run `30842604251`, and CodeQL success. Do not encode a commit’s own green verdict inside
that same commit: derive the live head with `gh pr view 911 --json headRefOid`, and require an
exact-head completed run for whatever head a human actually merges. Newer docs bytes need their own
exact-head proof.

Task 2A RED is local only at exact SHA `6367614960327b1a4e00301c8bfdb9b2e4bb453e` (branch and HEAD
match, unpushed, no Actions run, no GREEN). Sol’s exact-byte verdict is `FIX-FIRST` with ten HIGH
blockers. Older rejected SHAs `4efcad9` / `8df269d` remain historical; they are not the current RED
artifact.

Closed-world queue unchanged: **28 canonical rows**, **23 unfinished** = **10 READY**, **5
CEO_GATED**, **8 DEMAND_GATED**. Research recommendations below are recommendations only — they do
not silently reclassify any row.

## What worked

- Public packaging and release health remain clean at `v1.102.1`.
- PR #910’s closed-world status index remains the live machine-parsed contract.
- PR #911’s last observed exact head cleared CI, security, and CodeQL while staying CLEAN/MERGEABLE.
- Round-60 plan approval still stands on the named hashes; the authorized GREEN phase has not
  started. Sol found accidental public behavior inside the RED scaffold, and it must be removed.
- Task 2A RED made real progress Sol retained: full Counter/census/job/vector/Cargo executable
  binding; real foreign-chain/catalog fixtures; scoped rg/sidecar overrides; discoverable close
  ownership.
- Local root bounded replay (manifest **157** unique nodes = **148** Python + **9** Rust; jobs **95**
  Python and **62** native): native **44F/9P**; installer **13F/18P/4S**; ledger **32F/3P**; win32
  **2F/11P/12S**; CI governance **6P**; Ruff and preview format clean. Failures are expected RED
  surface, not GREEN clearance.
- Exa research produced safe default recommendations without pretending competitor existence equals
  customer demand.

## Every unfinished canonical backlog item (23)

### Ready to build after its own gates (10)

1. **#89 — WSL→Windows search paths.** Translate typed filesystem operands before a Windows-native
   search; Task 2A owns the RED/GREEN path. Still `READY` until a real implementation draft PR exists.
2. **#90 — WSL scan false-clear.** Prevent Windows ast-grep from receiving an untranslated Linux path
   and reporting a misleading clean result; shares Task 2A/2B/2C with #89.
3. **#859 — writer census and anchored publication.** Class-level AST writer census and every unsafe
   user-facing publication path, including generated Python.
4. **F5 — edit-ready/claims fence.** Strict, attributable, race-safe edit readiness (Task 8).
5. **F6 — edit verification.** Shared verification service and `verify-edit` surface (Tasks 6–7).
6. **F7 — language registry/cross-file resolution.** Registry-driven navigation and cross-file waves
   (Tasks 10–11).
7. **F8 — federated workspace prepare.** Bounded multi-root service, CLI, and MCP parity (Tasks 12–13).
8. **MCP-SURFACE — incomplete-result disclosure.** Task-4 MCP disclosure residue.
9. **CPU-BACKEND — backend twins.** Harden Rust and Python CPU backends without deleting public API
   or retaining unsafe retry behavior (Task 5).
10. **REF-CALL-REGISTRY — shared prepare service.** Extract the references/callers preparation
    service before its consumers (Task 9).

### CEO decision-gated, nonfinancial (4)

11. **#48 — startup architecture.** Recommendation only: accept the shipped hybrid native managed
    front door plus Python sidecar; retire a larger rewrite unless pip/uv parity gains business
    priority. Status stays `CEO_GATED`.
12. **#72 — public benchmark claim.** Recommendation only: HOLD public 7.5x — the old one-repo/
    25-task claim conflicts with later 6.4x and there is no committed current harness. Allow only a
    zero-spend fresh six-repo/180-task quality-gated benchmark; public wording still needs approval.
    Status stays `CEO_GATED`.
13. **#77/F9 — ledger enforcement scope.** Recommendation only: safe default is local opt-in advisory
    only; no auth/CI blocking. Status stays `CEO_GATED`.
14. **#131 — GPU native assets.** Recommendation only: optional experimental NVIDIA asset with CPU
    default/fallback and no speed claim. Physical proof/spend stays separate under #169. Status stays
    `CEO_GATED`.

No question is being asked for these nonfinancial gates under the current instruction.

### Financial approval required (1)

15. **#169 — physical GPU proof.** The only mandatory financial stop. Requires approval before
    renting/buying hardware or incurring spend.

### Demand/research-gated (8)

16. **#255 — many-pattern dedup/compression/native investment.** Reopen only for demand plus a
    bounded parity experiment or approved investment.
17. **F10 — MaxSim.** Recommendation only: perform a caller/installability census, then retire if
    unreachable. Status stays `DEMAND_GATED` until that census closes it honestly.
18. **DD-004 — typed backend errors.** Recommendation only: likely retire as a standalone row and
    bank the typed-boundary rule as durable guidance. Status stays `DEMAND_GATED` until an explicit
    retirement receipt lands.
19. **DD-006 — daemon load/DoS.** Reopen with measured concurrent-load evidence.
20. **AST-DSL-PARITY — full structural DSL parity.** Needs demand and a preprocessor-aware oracle.
21. **MCP-LEAN-DEFAULT — lean MCP default.** Needs client demand and compatibility evidence.
22. **CONTINUOUS-REFRESH — warm session/index serving.** Needs measured latency demand and an
    approved persistent-index design.
23. **RUST-REPLACE-SYMLINK — direct-leaf replacement.** Needs a concrete threat model and downstream
    compatibility decision.

Keep other demand gates without pretending competitor existence is customer demand.

### Terminal five (not unfinished)

The same closed-world index also carries five terminal rows: shipped `#36`, `#37`, `#109`; retired
`#22`, `F2`.

## Task 2A RED — correctly blocked (not merge-ready)

Exact local artifact: SHA `6367614960327b1a4e00301c8bfdb9b2e4bb453e`, branch/HEAD match, unpushed,
no Actions run, no GREEN clearance. Sol exact-byte verdict: **`FIX-FIRST`** with **10 HIGH**
blockers:

1. No real immutable-SHA Windows CI clearance.
2. Runners accept crashes/setup failures as behavioral RED.
3. PCRE2 construction oracle is hardcoded and outside the census.
4. Job heartbeat is parent-forgeable, with multiline ambiguity.
5. Real default Job cleanup is not independently proven.
6. SDDL accepts unknown / inherit-only / garbage grammar.
7. CNG export uses an invalid flag and accepts any error without an exportable positive control.
8. TxR protocols omit exact close ownership.
9. Python producer hook self-attests before actual start.
10. Accidental public `-f`/`--file` GREEN performs an unbounded read before the ledger.

Retained verified improvements (do not regress while repairing): full Counter/census/job/vector/Cargo
executable binding; real foreign-chain/catalog fixtures; scoped rg/sidecar overrides; discoverable
close ownership.

Local bounded replay remains RED evidence only: native 44F/9P; installer 13F/18P/4S; ledger 32F/3P;
win32 2F/11P/12S; CI governance 6P. Manifest 157 unique nodes (148 Python + 9 Rust); jobs 95 Python
and 62 native.

## Dependency-ordered work plan

1. **Planning PR:** human may merge #911 once the head being merged has exact completed green
   evidence. Do not treat Task 2A as cleared by this merge.
2. **After merged-base proof:** Cursor repairs the ten RED blockers on an isolated RED worktree;
   Sol repeats exact-byte review until `SHIP`.
3. **Only then:** push the Task 2A draft and obtain real Windows CI (immutable-SHA population,
   expected per-node outcomes, raw artifacts). No run is no clearance.
4. **Cross-domain foundation:** Task 2A/#89 search → Task 2B/#90 scan → Task 2C run/index/MCP twins;
   separate closure PR moves #89/#90 to `SHIPPED` only after merged and published-artifact proof.
5. **Independent P0 hardening:** Task 3/#859 → Task 4/MCP-SURFACE → Task 5/CPU-BACKEND under the
   WIP/release gate.
6. **Edit workflow chain:** Task 6 F6 service → Task 7 `verify-edit` → Task 8 F5.
7. **Navigation chain:** Task 9 REF-CALL-REGISTRY → Task 10 language waves → Task 11 cross-file (F7).
8. **Workspace chain:** Task 12 F8 service/CLI → Task 13 MCP parity.
9. **Graph-coding value gate:** Task 14 only after foundations; pin ranking first; retire if value
   loses.
10. **Closeout:** Task 15 dispositions; Task 16 independent audit, drain, merged-artifact checks,
    published-wheel dogfood.

Starting order remains **2A → 2B → 2C → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → gated 14 →
15 → 16**. Nonfinancial CEO rows stay explicit; #169 is the only mandatory financial stop.

## Exa research completed (recommendations only)

Prior graph-coding receipts remain:

- [LARGER](https://arxiv.org/html/2605.16352) — lexically anchored local graph neighborhoods.
- [RANGER](https://arxiv.org/html/2509.25257v1) — research input, not a wholesale architecture copy.
- [Augment Context Services](https://docs.augmentcode.com/context-services/overview) — incremental
  projection before a long-lived service.
- [Greptile](https://www.greptile.com/) — compact dependency/call context need, not “graph” as a label.
- Microsoft primitives already named in Round 60: root-chain policy, offline WinTrust, Job Objects,
  transacted registry.

Current exact-commit / primary-source receipts (additions for this continuation):

- [ripgrep README at exact commit `7525479`](https://github.com/BurntSushi/ripgrep/blob/7525479a9576f1ca4c2d04339d78e47ff5ae9b05/README.md)
  — cold exact-text baseline honesty.
- [ast-grep performance guide](https://ast-grep.github.io/blog/optimize-ast-grep.html) — structural-search
  baseline, not a silent DSL-parity claim.
- [CodeGraph at exact commit `49c11f`](https://github.com/colbymchenry/codegraph/blob/49c11fc2e0c02170742be8411e66a31af611f4b7/README.md)
  — agent context-tool comparator.
- Sverklo at exact commit `fd90c1`: [manifest](https://github.com/sverklo/sverklo/blob/fd90c186d355c3b14032b315328399d7b58b4faa/benchmark/src/datasets/manifest.json),
  [benchmark README](https://github.com/sverklo/sverklo/blob/fd90c186d355c3b14032b315328399d7b58b4faa/benchmark/README.md), and
  [token estimator](https://github.com/sverklo/sverklo/blob/fd90c186d355c3b14032b315328399d7b58b4faa/benchmark/src/estimator.ts)
  — benchmark population, reproducibility, and estimated-token discipline.
- [Gortex benchmark at exact commit `fc6d62`](https://github.com/zzet/gortex/blob/fc6d62d8b7c0b9f1aea23dd8597b6ed8a88cc24c/BENCHMARK.md)
  — comparator and claim-scope caution.
- [Official MCP tools spec](https://modelcontextprotocol.io/specification/draft/server/tools) —
  tool-surface defaults and compatibility.
- [GitHub Actions concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency) —
  push-race / drain gate mechanics.
- [NVIDIA CUDA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/latest/index.html) — #131/#169
  asset vs physical-proof separation.
- [ColBERT](https://github.com/stanford-futuredata/ColBERT),
  [Zoekt](https://github.com/sourcegraph/zoekt), and Rust
  [`fs`](https://docs.rs/rustc-std-workspace-std/latest/std/fs/index.html) /
  [`OpenOptions`](https://docs.rs/rustc-std-workspace-std/latest/std/fs/struct.OpenOptions.html) —
  late-rerank, indexed-search scale, and no-follow/open-flags security primitives.

Decision recommendations (do not change tracker status by themselves):

| Row | Recommendation | Status remains |
|---|---|---|
| #48 | Accept shipped hybrid native managed front door + Python sidecar; retire larger rewrite unless pip/uv parity is prioritized | `CEO_GATED` |
| #72 | HOLD public 7.5x; only a zero-spend fresh six-repo/180-task quality-gated benchmark may reopen wording, and public wording still needs approval | `CEO_GATED` |
| #77 | Safe default: local opt-in advisory only; no auth/CI blocking | `CEO_GATED` |
| #131 | Optional experimental NVIDIA asset with CPU default/fallback and no speed claim; #169 stays separate | `CEO_GATED` |
| DD-004 | Likely retire as standalone; bank typed-boundary rule | `DEMAND_GATED` |
| F10 | Caller/installability census, then retire if unreachable | `DEMAND_GATED` |

## Research still needed

- Repair the ten Sol HIGH blockers on SHA `6367614...` until exact-byte `SHIP`.
- Obtain real immutable-SHA Windows CI for Task 2A (expected per-node outcomes + raw artifacts).
- TxR availability/fail-closed UX on every supported Windows runner (no invented fallback).
- Microsoft root allowlist maintenance without weakening the foreign-root control.
- Graph projection value gate against current `map`/`callers`/`blast-radius` (pin ranking first).
- Continuous refresh: cold vs warm repeated-agent measurement before a daemon index.
- MaxSim: caller/installability census before activation or retirement.
- Daemon DoS: scheduler-independent concurrent-load evidence.
- AST parity: preprocessor-aware C/C++ and cross-OS grammar oracles.
- GPU physical proof remains financial-gated (#169).

## Lessons learned since the prior CEO update

Prior 2026-08-03 lessons A51–A60 still hold. New retained laws from the Task 2A RED gate:

1. **A61 — Behavioral RED pins the exact expected reason.** Crash, import, panic, and setup errors
   are not behavioral RED.
2. **A62 — Route/start evidence comes from the actual producer/constructor** and test-owned OS/raw
   evidence — never a hardcoded bool or a production hook that self-attests.
3. **A63 — Containment proof authenticates writer/client provenance** and proves alive-before →
   dead-after plus cleanup — not Event/EOF/PID text alone.
4. **A64 — Crypto negative proof uses a valid API operation**, an exact refusal class, and an
   exportable/trusted positive control.
5. **A65 — Security grammar validates full sections/types/flags/effective authority**, and rejects
   unknown and inherit-only forms — not substring principals.
6. **A66 — Every resource-owning protocol names close primitives** and proves exact-once reverse
   cleanup on success, `BaseException`, and cleanup failure while preserving the primary error.
7. **A67 — RED scaffolds cannot enable partial public behavior** or unbounded work before the guard.
8. **A68 — Immutable-SHA CI clearance needs a real run**, expected per-node outcomes, raw artifacts,
   and the exact population; no run is no clearance.

Also retained from earlier in this campaign: green is artifact-specific; architecture `SHIP` ≠
security clearance; name real OS primitives; PATH never discovers authority; containment denies
escape; caps fire at every door; static manifest ≠ live receipt; narrow review retries; discover
deferred tools; keep WSL/Windows venv roots disjoint.

## Next action

Human may merge PR #911 when the head being merged has exact completed green evidence. After
merged-base proof, Cursor repairs the ten Task 2A RED blockers; Sol repeats until exact-byte
`SHIP`; then push the draft and obtain real Windows CI. Do not call Task 2A merge-ready. #89/#90
stay `READY` until the real implementation PR exists. No spend. #169 remains the only mandatory
financial stop; no question is being asked for the nonfinancial CEO gates.
