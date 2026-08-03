# CEO backlog update — 2026-08-02

> **Interim snapshot, superseded for execution.** Task 2 completed after this narrative was written.
> The machine-readable current source is canonical status index `2026-08-02.3` in
> `docs/TASK_BOARD.md`, backed by `2026-08-02-backlog-reconciliation.md`. Its bounded treatment and
> control arms reproduced #89 search and #90 scan path-domain defects; both are `READY`, and one
> amended/re-reviewed typed-path program must precede the remaining plan. Historical wording below
> that says “Task 2 may resume,” “Task 2 will,” #89 is environment-blocked, or #90 is retired is
> retained only as the pre-reconciliation CEO snapshot and must not drive dispatch.

## Plain-English status

The released product works, the release pipeline is healthy, and the queue is not empty.

- Public version `v1.102.1` is available from PyPI and GitHub Releases.
- PR #910 repaired the live task-board structure, passed its exact 39-job CI run, merged as
  `8024125612d5fb42481acde34d94ad39bbaa3c3e`, and passed the focused merged-artifact tests (7/7).
- GitHub had zero open PRs and one open issue (#48) at this snapshot.
- The closeout implementation has not started. Required exact-hash re-reviews correctly rejected prior
  plan revisions after finding task-order, schema, parent-swap, config-confinement, tracker-lifecycle,
  deferred-security ownership, and tests-after gaps. After 18 rounds, architecture, security, and TDD
  all returned `SHIP` on the same final status-stamped hashes. Task 2 may now resume.
- No financial spend was incurred or authorized.
- Docs/skill governance passed 93/93 and all three changed skills passed `quick_validate.py` under
  UTF-8 mode. The broader agent-readiness run passed 11/13; its two failures were local-environment
  findings, not behavior regressions: editable warmup spent 240 seconds compiling and timed out, and
  the no-sync worktree executable reported 1.102.0 while source/release state is 1.102.1.

## What worked

1. The public wheel and native CPU assets install and report `tensor-grep 1.102.1`.
2. The real multi-OS CI matrix caught no regression on PR #910: 39 jobs completed, none failed or
   remained unfinished.
3. Independent review caught a malformed Markdown/Python example and stale PR counts that ordinary
   green checks missed; both were fixed before merge.
4. Post-merge verification proved the repaired board structure on the actual squash commit, not only
   on the PR branch.
5. Live-code deep dives invalidated weak premises in the originally approved plan before any build:
   the writer population was incomplete, a public Rust API could not safely be deleted, and a Python
   CPU adapter still carried the same unsafe fallback already fixed in its sibling.
6. Current external research is already folded into the plan: MCP discovery/versioning/tool contracts,
   SLSA provenance, Tree-sitter definition/reference/query contracts, and agent-safe change-control
   patterns.

## All live backlog

This is the interim closed-world work list for this campaign snapshot, reconciled from the canonical
prioritized/historical `docs/BACKLOG.md`, the current board/handoff, and GitHub. Task 2 will make the
`docs/TASK_BOARD.md` canonical status index the machine-parsed live-state view. Old shipped narrative
is not repeated here as live work.

### Active and buildable under the approved plan

1. **Tracker truth (Task 2):** replace stale prose-derived status with a machine-parsed canonical
   status index; retire F1/#22 and F2; record #90 honestly as mixed; close #109/#36/#37 with receipts;
   refresh `SESSION_HANDOFF`; correct the old #859 audit claim.
   **Completed, with its final disposition corrected by the superseding notice above.**
2. **#89/#90 typed cross-domain paths (amendment required):** bridge only typed filesystem operands
   when a WSL caller delegates to a Windows process; never rewrite patterns/globs/arbitrary argv;
   search must not return `path_not_found` for an existing root and scan must not turn unreadable
   files into a false clear result.
3. **#859 atomic-writer class fix (Task 3):** census every CLI artifact writer, including generated
   Python and aliased calls; fix the three live violations; preserve per-command overwrite versus
   create-if-absent behavior; defeat leaf and parent symlink/junction races.
4. **MCP surface disclosure (Task 4):** disclose `full` versus `lean` from the same frozen registry
   decision and move contract `1.7.0` to `1.8.0`.
5. **CPU backend twins (Task 5):** keep and harden public Rust `CpuBackend.replace_in_place`; propagate
   directory child failures; remove both Python `TypeError` compatibility retries without losing
   `invert_match` or falling open to Python regex.
6. **Pure edit verification (Task 6):** add a versioned, bounded, deterministic verification service
   and evidence-ingestion contract.
7. **Public `tg verify-edit` (Task 7):** expose the verification service through Python and compiled
   native front doors with exact JSON, exit-code, cap, path, and help parity.
8. **Strict `tg edit-ready` (Task 8):** combine prepare, named claims, baselines, validation, and
   fail-closed claim fencing without changing legacy prepare/ledger behavior.
9. **Registry-driven refs/callers (Task 9):** remove dispatch duplication without changing output.
10. **Parser-backed in-file depth (Task 10):** separate Java, C#, PHP, C, and C++ reference/caller waves,
   each with real AST-shape fixtures, same-name decoys, and grammar-missing honesty.
11. **Truthful cross-file resolution (Task 11):** separate Java source roots, Go modules, PHP Composer
    PSR-4, C# projects, C compile databases, and C++ compile databases; never guess from a same-named
    file.
12. **Federated multi-root prepare (Task 12):** internal service plus CLI/native parity for bounded
    sibling-repository preparation.
13. **MCP multi-root prepare (Task 13):** confined `tg_workspace_prepare`, contract `1.9.0`, full/lean
    registry parity, and real stdio tests.
14. **Known-item dispositions (Task 14):** decide, retire, or retain DD-004, DD-006, F10, the C++ macro
    ceiling, #255, and the CEO-owned items with explicit owners and triggers.
15. **Closeout (Task 15):** independent specification/security/test/API/docs audits until every seat
    says SHIP; lint/format/typecheck/full CI; one-release-at-a-time drain; merged-source and published-
    wheel dogfood with raw receipts.

Ownership is the numbered Task for every row above. A row starts when the preceding dependency has
merged and the exact main gate is complete; it reopens after shipping only when its named contract test
or receipt fails. Task 15 owns the final cross-program audit and disposition pass.

### Environment-blocked at the interim snapshot

- **#89** (owner: Task 2; trigger: an available WSL/Linux environment): reproduce WSL `/mnt/c` absolute-path behavior on an available WSL/Linux environment. If the
  environment is unavailable it remains blocked; unavailability is not retirement evidence.
- **ENV-VENV-DRIFT** (owner: campaign steward; trigger: a trusted main venv): the campaign worktree's `uv run --no-sync tg doctor` resolves a stale 1.102.0
  executable; `uv run tg --version` attempted an editable Rust rebuild and timed out after 240 seconds.
  Reconcile the real main venv before using local CLI version probes as campaign evidence. The same
  no-sync environment lacks PyYAML, so `scripts/validate_release_assets.py` could not start locally;
  PR/main CI remains the release-asset oracle until the venv is repaired.

### Nonfinancial decision-gated

Under the current instruction these do not cause a user question. Task 14 owns the evidence-backed
decision; each item reopens if its named evidence gate changes.

- **#48:** scope the remaining public-shim startup-overhead problem. The “beat rg by widening the same
  native walk” route is already a measured negative.
- **#72:** decide whether to publish the measured 7.5× token-efficiency proof point after a fresh claim
  and comparator audit.
- **#77 / F9:** decide the narrow identity/auth/security scope for ledger-backed local agent
  coordination and any CI/review-bundle overlap gate.
- **#131:** decide whether to publish the already-built GPU-flavor native assets. This publishes assets;
  it does not prove or promote a speed crossover.

### Financial approval required

- **#169** (decision maker: CEO; trigger: priced hardware/cloud proposal): decide whether to fund/attach
  the physical GPU proof environment. No hardware/cloud spend is authorized by this snapshot.

### Demand-gated / research before build

- **#255** (owner: Task 14; trigger: bounded no-spend parity design): many-pattern dedup/parity experiment and choice among multi-day cross-language moat work;
  no GPU/cloud spend without approval.
- **F10** (owner: Task 14; trigger: current caller/config census): MaxSim caller/config/public-contract census, then either create a supported install path or
  remove the unreachable surface.
- **DD-004** (owner: Task 14; trigger: stable typed-boundary proof): locate a stable typed error boundary before replacing raw CPU-backend `RuntimeError`.
- **DD-006** (owner: Task 14; trigger: bounded concurrency measurement): measure concurrent daemon load/DoS behavior before adding a worker semaphore.
- **AST-DSL-PARITY** (owner: Task 14; trigger: demonstrated DSL demand): full DSL parity remains demand-gated; C++ macro/preprocessor shapes need an honest
  oracle before changing the documented ceiling.
- **MCP-LEAN-DEFAULT** (owner: Task 14; trigger: client-demand and compatibility proof): keep default `full` until client demand and compatibility evidence justify a
  breaking default flip; surface disclosure itself is active Task 4.
- **CONTINUOUS-REFRESH** (owner: Task 14; trigger: measured warm-session need): measure a real warm-session/search-index design before building a daemonized
  refresh path.
- **RUST-REPLACE-SYMLINK** (owner: Task 14; trigger: concrete untrusted-destination threat model or
  downstream compatibility decision): decide the public Rust direct-file leaf-symlink contract under a
  separately reviewed no-follow/API plan; do not hide it inside `CPU-BACKEND`.
- **Research themes owned by existing IDs:** context/session latency (`CONTINUOUS-REFRESH`), token
  economy (`#72`), call-site evidence and target-selection accuracy (`REF-CALL-REGISTRY`/`F7`),
  classify provider/cache UX (`F5`), and managed cross-OS ast-grep plus LSP proof-mode
  (`AST-DSL-PARITY`). Their start/reopen triggers are the corresponding canonical rows above.

## Corrections that are not live backlog

- **F1/#22:** retire under the current 0/1/2 contract; complete no-match is exit 1, incomplete is exit
  2, and an unhonored GPU request is disclosed in-band.
- **F2:** retire; legacy anonymous-agent compatibility was explicitly considered and retained.
- **#90 (superseded by Task-2 treatment/control):** this interim report called the WSL half a
  non-defect. The current receipt disproves that premise; only the PR #571 doctor half is shipped and
  the scan portability half is `READY`.
- **#109:** shipped in PR #605.
- **#36/#37:** shipped in PR #903/#908.
- **#858:** the historical codemap writer is now helper-backed; #859 remains the broader class-level
  population/race contract.

## Research ledger

### Already completed and folded into the plan

- MCP tool discovery, server versioning, tool metadata, and current GitHub MCP precedent.
- SLSA provenance and source-verification requirements for evidence receipts.
- Tree-sitter definition/reference/call query roles and generated node-type contracts.
- Agent-safe controlled-change and impact-analysis patterns.

### Still needs a bounded experiment or current evidence

1. Startup-overhead component benchmark for #48, with launcher attribution and accepted baselines.
2. Fresh token/accuracy/comparator rerun before any #72 public claim.
3. Identity/auth/threat-model design for #77/F9.
4. Published GPU asset/install UX plus self-hosted correctness-and-speed proof for #131/#169.
5. A minimal many-pattern dedup parity experiment for #255.
6. MaxSim reachability, installability, latency, and quality census for F10.
7. Concurrent daemon load/DoS measurement for DD-006.
8. Native/Python error-boundary census for DD-004.
9. Live grammar fixtures and project-config semantics for each language wave.
10. Continuous-refresh architecture and warm-session latency measurement.
11. Rust direct-leaf-symlink downstream compatibility and untrusted-destination threat-model evidence.

## Lessons retained from this campaign

1. A green test suite does not validate prose or PR metadata; examples, counts, and titles are part of
   the artifact.
2. Plan approval expires when a live-code premise changes. Amend and re-run thinktank before building.
3. A site regression is not a class-level census; #859 was falsely closed by a codemap-only test.
4. Census by defect surface, not implementation stereotype. Include generated source, aliases,
   shadowing, and independently derived raw candidates.
5. Destination resolution order is security-sensitive: resolving a leaf before a no-follow writer
   erases the evidence that it was a symlink.
6. Parent-directory swaps are a separate race from leaf swaps; handle-relative publication and
   directory creation need their own Event-gated tests on Unix and Windows.
7. A fix must cross to its twin. The RustCoreBackend retry was removed while CPUBackend kept two copies.
8. “No in-repo caller” does not authorize deletion of a public Rust `rlib` API.
9. Mixed outcomes stay mixed. One shipped half plus one retired half is not “fully shipped.”
10. Producer/consumer dogfood must record both exit statuses and avoid creating a repository artifact
    that changes the state being verified.
11. Exact CI evidence means one run ID, its exact head SHA, a stable job population, and zero unfinished
    or failing jobs; a growing PR check rollup is not completion evidence.
12. Remote `main`, the main CI head, and a semantic-release skip-CI commit can be different SHAs; record
    each claim against the artifact it actually proves.
13. Hash reviews need one named canonical artifact and method; clean-filter-equivalent Windows
    worktrees can have different raw mixed-line-ending hashes.
14. Validate the cross-task dependency graph. A service or CLI cannot be tested before it exists, and
    a command-discovery failure is not a behavioral RED.
15. Parent anchoring applies to lock creation, protected-index RMW, and bounded project-config reads,
    not just final artifact publication.
16. Every deferred security/compatibility behavior needs its own stable ID, owner, threat boundary, and
    reopen trigger; otherwise closeout silently loses it.
17. Once a numbered draft PR exists, its canonical tracker row must move to `IN_FLIGHT` in that PR;
    a separate post-merge change certifies `SHIPPED`.

## Evidence snapshot

- Merged `main`: `8024125612d5fb42481acde34d94ad39bbaa3c3e`.
- PR #910 CI: run `30777042942`, head `22128c5767e8ae7eb5984baed0a904c8dc6d93e5`,
  39 completed jobs, 0 failing, 0 unfinished.
- PR #910 merged-artifact test: `tests/unit/test_task_board_freshness.py`, 7 passed.
- Latest public release: `v1.102.1`; clean `uvx` reported `tensor-grep 1.102.1`.
- Open PRs: 0. Open GitHub issues: #48 only.
- New main CI from the docs merge: run `30778356638`, exact head `8024125612d5fb42481acde34d94ad39bbaa3c3e`,
  completed successfully with 39/39 jobs.
- Final approved campaign-plan hashes: design
  `F627B23F5881C63AE525FC7226A4FF51C1EA249DB43DB1BD8B57EDDEA4E4C994`; implementation
  `E30DCCCDC62459D28AA272CB5E251CDB92FBFC6D0BA23A312BA524AF9ED8216B`; architecture/security/TDD
  verdicts all `SHIP` on this exact status-stamped pair.
- Local docs/skill gate: 93 tests passed; all three changed skills validated. Agent readiness: 11
  passed, 2 environment failures (`repo-cli-build-warmup` timeout; `repo-doctor` 1.102.0/1.102.1
  mismatch). `validate_release_assets.py` was blocked before execution by missing `yaml` in the
  no-sync venv.
