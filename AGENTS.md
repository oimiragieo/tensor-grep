# AGENTS.md

This file explains how agents should work in `tensor-grep`.

## Goal

`tensor-grep` is trying to become a fast, scalable search tool that combines:

- `ripgrep`-class text search
- AST / structural search
- indexed repeated-query acceleration
- optional GPU / ML paths
- AI-harness-friendly search and edit behavior

The repo should be treated as a benchmark-governed, contract-heavy codebase. Do not optimize by guesswork.

## Backlog & working process

The canonical prioritized/historical work ledger lives in **[docs/BACKLOG.md](docs/BACKLOG.md)**. GitHub
(`gh pr list`) is the source of truth for PRs. The machine-parsed canonical status index in
`docs/TASK_BOARD.md` is the live-state view once Task 2 creates it; until then, use the latest dated
closed-world reconciliation audit referenced at the top of BACKLOG. **Subagents:** treat each live item's
description + files + status as your brief. **CEO status** must enumerate every live disposition—active,
environment-blocked, nonfinancial decision-gated, financial/spend-gated, demand/research-gated, and
mixed/terminal corrections—not merely SHIPPING or P0/P1 highlights.

The standing multi-model pipeline for any substantive item: deep-dive → **Fable audit** (find + fix-idea,
cite `file:line`) → **Exa** recency + competitive research (you are trained on stale data — verify current
facts) → plan (superpowers skills) → thinktank/Fable review the plan → **Sonnet build, TDD** → verify in the
REAL venv (`uv run --no-sync`; a worktree "tests pass" is a hypothesis, re-run in the main venv) →
`ruff check` + `ruff format --preview` + `mypy` → codex/Fable review the PR → **PR → drain**
(one-merge-per-publish, the push-race rule) → repeat until no issues. Isolate code agents with
`isolation:'worktree'`. Match model to task (haiku scan / sonnet build / opus+fable review). Run the
common-sense gate before pending any question to the CEO. Keep docs (this file, `docs/BACKLOG.md`,
`docs/SESSION_HANDOFF.md`, skills, CLAUDE.md) synchronized as work lands.

## Campaign Orchestration Disciplines (2026-07-08, hard-won)

Running a multi-PR drain+build campaign so fixes *land* instead of piling up. Each rule is a fix for a
concrete failure observed this session.

- **A1 — WIP cap.** No new *build* dispatch while >5 PRs are undrained OR the `main` gate is red. A red
  gate is a drop-everything hotfix that jumps the queue. Prevents "churning not completing" — generating
  faster than the ~40–66 min/publish drain empties (backlog stays constant-size = the smell).
- **A2 — A self-firing drain-cron beats a long-lived background drain.** A short-lived per-fire cron that
  merges ONE lowest-CLEAN PR (`gh pr merge --squash --delete-branch`, push-race-checked) is robust; a
  long-lived `drain.sh &` background process kept *dying* during the long CI/publish waits (and an inner
  `&` in a `run_in_background` wrapper orphaned it). Each fire is short-lived, so nothing can be killed
  mid-run. Push-race gate per fire: the latest `chore(release)` tag must be on PyPI AND `main` CI
  `completed` before merging.
- **A3 -- Mandatory adversarial security gate before merge.** Every security PR -- touching `apply_policy`
  / `mcp_server` / `*_backend` / an index-or-session lock / auth / money / migration / native asset /
  installer / doctor-probe construction -- gets an Opus "try to BREAK it, cite `file:line`, default
  FIX-FIRST if uncertain" review *before* merge. Not a rubber stamp: this session it returned SHIP on some
  and caught real issues on others (a symlink RCE bypass; a lock-release TOCTOU). The native-asset /
  installer / doctor-probe trigger was added after the v1.75.1-v1.75.3 GPU wave (#594-#596: WSL
  path-domain probe bridging, doctor probe failure taxonomy, calibrate/installer remediation) ran every PR
  through this same gate and it returned real `SHIP-WITH-NIT` / `SHIP` verdicts off 8/8 clean probes rather
  than a rubber stamp. `codex` is the nominal second vendor but its WSL path is unreliable -> Opus is the
  reliable substitute. Verdict shape: `SHIP` | `FIX-FIRST(+file:line + repro + minimal fix)`.
- **A4 — Resume a dead agent from its transcript.** A background subagent that dies with "terminated
  early due to an API error: 500" is REVIVED by `SendMessage` to its `agentId` (partial work intact) — do
  NOT re-dispatch fresh (loses the work). Happened 3× this session; all recovered.
- **A5 — Don't kill a build on staleness.** A complex build (a redesign + heavy test rewiring) legitimately
  runs >10–15 min between output flushes. A "stale > N min" heuristic kill destroys a *working* agent (a
  build was killed twice before its kill-note proved it was mid-work). Trust the completion notification;
  diagnose a suspected hang from the kill-note's last line, not an mtime guess.
- **A6 — Anti-hang test protocol.** Wrap every test run in a shell `timeout` (`timeout 120 uv run
  --no-sync … pytest … --timeout=15`), and write the fix *before* the red-phase adversarial test — a
  ReDoS/deadlock red-test executed against un-fixed code IS the hang it is testing. Distinguish
  slow-but-protected from hung by exit code (124 timeout / 137 SIGKILL), not elapsed time.
- **A7 — Harvest a worktree agent's work, then re-verify.** A worktree agent's "tests pass" is a
  hypothesis (its venv may lack the compiled `rust_core` ext). Cherry-pick its commit onto a fresh branch
  off `origin/main`, re-verify in the real venv + `ruff`/`format --preview`/`mypy` + a live smoke, THEN the
  gate, THEN PR.
- **A8 — Fable is reachable only via `Agent(model:fable)`.** A Workflow `agent()` call cannot reach Fable —
  it silently falls back to the session model. Dispatch Fable design/audit seats as `Agent` subagents,
  never inside a `Workflow`.
- **A9 -- Probe liveness via `SendMessage` before any `TaskStop`.** A background subagent's output-file
  mtime/size is UNRELIABLE (0KB for 40-57 min while foreground-compiling). The reliable alive-vs-paused
  tell is a `SendMessage` probe: a reply of "Message queued...at its next tool round" means ALIVE;
  "had no active task; resumed from transcript" means it WAS PAUSED. Corroborate with Pyright
  `<new-diagnostics>` on its file-writes plus the active build-process count. Cross-ref A5 ("don't kill on
  staleness") -- this probe is the mechanism A5's "trust the completion notification" actually relies on.
  Codified as the global skill `agent-liveness-probe` — load it before killing, restarting, or
  `TaskStop`-ing anything that looks stalled.
- **A10 -- A no-verdict council seat is a FAILED seat, not a blocker.** The codex thinktank seat can hang
  on an MCP-auth spin (cloudflare/sentry `invalid_token`) -> 0KB output, no anchored verdict. Treat it as
  FAILED: kill it, sweep the orphaned processes it left behind (20+ stale codex processes found in one
  session), and synthesize from the surviving Opus lenses instead of waiting on it.
- **A11 -- Design-review-before-build** (CEO directive #174). Fable designs a plan -> a thinktank council
  certifies the PLAN itself is sound and ready (not findings, not a diff) -> bake must-fixes into the plan
  -> Sonnet builds TDD-first (worktree, foreground-gate) -> mandatory adversarial Opus gate (now including
  native-asset/installer/doctor-probe work, see the A3 extension above) -> drain one PR per publish. This
  sequence caught a CI-reddening fix, an ordering bug, and a GPU-oversell claim BEFORE any code was built
  this session.
- **A12 -- CPU-safe shared-server discipline.** This desktop is a SHARED machine (Operating Rule #3); other
  AI/omega-* services run concurrently. CPU-heavy work (loading/inferring a dense-embedding model, a full-
  corpus rerank sweep, a wide benchmark matrix, a cold `cargo check`) must NOT run locally and starve them —
  route it to cloud `Agent` subagents or GitHub Actions CI. The entire `tg find` build+eval campaign (#189)
  ran this way: zero local CPU. A bounded probe (a handful of queries, not the full golden set) is fine to
  sanity-check wiring; push the real evaluation to CI/a subagent. The cron tick itself is cloud-side and is
  not the problem — local process SPAWNS (codex/droid/gemini/cargo/rustc) are. Receipt: a 2026-07-16 GPU
  deep-dive fanned out local codex+droid + a cold cuda `cargo check` and saturated the CPU (3 orphaned codex
  procs killed).
- **A13 — Rapid-window batch-merge collapses N release cycles to 1 (C-batch).** Several independently-green,
  already-CI-passing PRs can land ~15-20s apart in one gate-open window as a SINGLE combined release;
  intermediate concurrency-cancelled/rejected-looking runs on the earlier pushes in that window are benign
  as long as the newest `main` run goes fully green. Receipts: v1.91.0 and v1.93.0 (the latter combining
  #703-706: run `29890576036` rejected-only, `29890612228` published). Distinguish deliberately from the
  ACCIDENTAL v1.17.23/#318/#319 push-race (an unintended two-writer collision, not a planned drain).
- **A14 — Event-driven release watching + a cron floor (C-event).** Prefer a background `gh run watch
  <run-id> --exit-status` (chained off its own ~10-min expiry notification) over blind long-interval polling
  when waiting on a release; pair it with a cron floor (e.g. :02/:32-style offsets) that embeds the FULL
  remaining pipeline instructions in the prompt itself so completion survives a crash or context loss.
- **A15 — Session-only crons die on crash/reboot; always recreate (C-cron).** A `/loop` invocation is
  session-bound and is not a durability substitute for a `CronCreate` drain-cron; `MEMORY.md` is the
  crash-safe state carrier that lets a recreated cron resume correctly (proven across a real PC crash
  mid-campaign).
- **A16 — Pin-first ranking gate (C-pin).** Before touching any scorer/graph/ranking code, write a test that
  pins the CURRENT ranked output GREEN on base; after the change, the only acceptable diff is the intended
  one — any legitimate-entry reorder is a STOP-finding, not noise to relax away. Receipt: #709,
  `test_blast_radius_legitimate_dependent_ranking_pin`.
- **A17 — Scheduler-independent concurrency tests (C-concurrency).** Never assert wall-clock thread overlap
  (a starved runner serializes legitimately and false-fails); assert the CONTRACT with `threading.Event`
  handshakes plus bounded acquire attempts (independence case + the converse mutual-exclusion case). This
  killed a 2-release flaky. Receipt: #701, `test_index_lock_is_per_root_not_global`.
- **A18 — A build agent's self-gate is a hypothesis, not clearance (C-independent-gate, extends A3).** A
  SEPARATE, independently-framed gate can still return SHIP-WITH-NITS on one pass and a distinct verdict on
  a re-drafted pass of the same PR — re-draft until the independent gate (not the build agent's own review)
  says SHIP. Receipt: #698.
- **A19 — Fold safety/honesty nits before merge; bank cosmetic ones (C-nit).** A gate finding that changes
  observable behavior (a fail-open read, a misleading status, a missing migration-honesty note) folds into
  the SAME PR before merge; a purely cosmetic nit (naming, comment wording, a stale citation) is banked as a
  follow-up and batch-closed later. Receipts: #704/#706 folded pre-merge, #708 batch-closed the banked
  cosmetic set.
- **A20 — Published-wheel verdict-table dogfood closes a campaign (C-wheel).** Before declaring a multi-PR
  campaign done, probe every fixed item against the ACTUALLY PUBLISHED wheel in a clean env (`uvx --from
  tensor-grep@<ver>`), one PASS/FAIL row per item backed by the raw JSON, not a verdict word alone —
  pre-build fixtures, read the raw JSON before scoring (a probe-shape misread reads as a false fail), and
  watch for pipe exit-code masking (`cmd | tail` reports `tail`'s exit code, not `cmd`'s). Receipt:
  2026-07-22, 7/7 clean.
- **A21 — The per-task-pinned accuracy gate is the loop-4 instrument (C-loop4).**
  `tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate` (`assert not misses`) surfaces exactly the
  kind of ranking/routing regression a code-review gate rationalizes away — it caught #250 (a `tg prepare`
  CLI-dispatcher misroute), which was then fixed and locked as a new permanent pinned task. Every real
  misroute found in the wild becomes a new permanent pinned task; this is a capability-regression gate,
  distinct from a contract test.
- **A22 — Sequential-drain-union-rebase for N PRs on a shared file.** When several parallel PRs each
  edit the SAME file (e.g. `test_lang_registry`, the pyproject `ast` extra, `uv.lock`), drain ONE at a
  time and rebase each onto the prior, UNIONing the assertions (assert the FULL set, never
  take-one-side). A CLEAN rebase (no conflict marker) is NOT proof of correctness — a silent auto-merge
  dropped a `lang_*` import, caught only by re-running pytest (`ImportError`). ALWAYS re-run the test
  suite after every rebase.
- **A23 — A "stopped" agent notification may mean the work already landed, not that it was lost
  (2026-07-24).** A build agent's process exited after committing but before emitting its own
  completion summary; the orchestrator's notification said no completion record was found — which
  reads like the work vanished. It had not: `git -C <worktree> status`/`log` showed a clean tree with
  both commits present and correct. **Rule:** on any "stopped"/"no completion record" notification,
  inspect the worktree's `git status`/`git log` BEFORE re-dispatching fresh work — re-dispatching would
  have duplicated (or conflicted with) work that was already done.
- **A24 — A worktree agent can commit on a DETACHED HEAD; push the SHA, not the branch name
  (2026-07-24).** `git push origin <branchname>` pushed the branch ref (still sitting at `main`'s tip,
  since the agent's commit landed on a detached `HEAD` rather than that branch) instead of the new
  commit — GitHub then rejected the PR with "No commits between main and `<branch>`," which reads like
  the work vanished a second, distinct way from A23. It had not: the commit was sitting at the
  worktree's `HEAD`, just not reachable from the branch ref being pushed. **Rule:** before pushing,
  compare `git rev-parse HEAD` against `git rev-parse <branch>` — if they differ, push the SHA
  explicitly (`git push origin <sha>:refs/heads/<name>`), then open the PR against that branch. See
  `tensor-grep-debugging-playbook` for the symptom-table row.
- **A25 — Session-scoped crons/monitors die silently on a CLI restart or reboot; always re-verify,
  never re-dispatch on an assumption (2026-07-24).** This is the same lesson as A15 (session-only
  crons die on crash/reboot) reconfirmed a session later — a steward cron was lost TWICE in one
  session, once to a CLI restart and once to a PC reboot, and both times the backstop vanished with no
  error, not a visible failure. **Rule:** after any restart or crash, re-create the recurring backstop
  and CONFIRM it with `CronList` rather than assuming a previously-recorded id/schedule is still armed;
  keep the durable state (queue, in-flight PRs, "resume here") in the task store + `MEMORY.md`, which
  survive a restart even when the cron itself does not.
- **A26 — Verify a session-scoped cron/monitor in BOTH directions: it can be dead when you assume
  it's alive, or ALIVE when you assume it's dead (extends A25, 2026-07-24).** This session lost its
  steward cron twice (once to a CLI restart, once to a PC reboot) and re-created it each time — but
  one presumed-dead cron from an earlier loss turned out to still be alive, running ALONGSIDE its
  replacement and firing stale instructions (it told a later tick to gate a PR that had already
  merged). A25 covers the "assumed alive, actually dead" direction; this is the mirror failure.
  **Rule:** after any restart or recreate, call `CronList` and read every returned entry — don't just
  count them or trust that the old id is gone — and explicitly delete any superseded duplicate. A
  stale backstop that still fires is worse than none: it looks authoritative and can act on data (a
  PR that already merged, a queue state that already moved on) that is no longer true.

- **A27 — A class fix must cross to its TWIN, or the twin re-fires the same defect (2026-07-26).**
  `test_index_lock_concurrency.py::test_index_lock_is_per_root_not_global` evolved ratio → overlap →
  Event-gated, and its docstring records WHY each form was retired. The ledger twin,
  `test_ledger_concurrency.py::test_claim_index_lock_is_per_root_not_global`, kept the retired
  *overlap* form and duly red-ed `main` in exactly the way the sibling's docstring predicts
  (`project_a=[1396.734, 1397.125] project_b=[1397.281, 1397.687]` — thread B was simply not
  scheduled into the instrumented section until after A left it). The class fix had been generalised
  correctly and then applied in ONE of two files. **Rule:** when you retire an approach in a test or
  helper, `grep` for its shape across siblings the same turn and port it — a docstring explaining why
  a form was abandoned is worthless in the file that still uses that form. Corollary for concurrency
  specifically: two independent locks are only guaranteed not to BLOCK each other, never to be
  *simultaneously held*; assert the blocking contract (Event-gated), never wall-clock overlap.
- **A28 — Relay a gate verdict to the ARTIFACT, not just your own transcript (2026-07-26).** PR #786
  arrived from a concurrent worktree agent; it got a full independent gate (design / bidirectional
  oracle / not-stacked) that then lived only in the steward session. A verdict nobody else can see is
  lost work: the next session either re-runs the gate or, worse, reaches a different conclusion.
  **Rule:** post the verdict as a PR comment with its evidence (what was probed, what the control arm
  showed) before moving on. Cost: one `gh pr comment`. It is also what lets the author un-draft
  without waiting on you.
- **A29 — Verify the fix on the MERGED artifact, not only pre-merge (2026-07-26).** Pre-merge proves
  the BUG is real (control arm on the unpatched tree). It does not prove the FIX behaves on `main` —
  a squash can drop a hunk, a conflict resolution can mangle it, and a green merge is not evidence
  about the code. For #786 the post-merge arm was one command: confirm the guard is present
  (`"_seen" in fn.__code__.co_varnames` — structurally, not by re-reading the diff) and re-run the
  cycle fixture against `main`. Both directions closed on the artifact that ships.
- **A30 — Make pruning DECIDABLE instead of banned (2026-07-26).** "Don't bulk-nuke agent branches,
  they may hold WIP" left ~70 husks accumulating indefinitely. `git merge-base --is-ancestor <branch>
  main` converts it to a per-branch proof: an ancestor of `main` has its commits already in `main`, so
  deleting it provably loses nothing. 61 deleted (with `git branch -d`, never `-D`, so git
  independently refuses anything unmerged — the two checks agreed 61/0), 2 kept. **`git branch
  --merged` under-reports after squash-merges, and a CLOSED PR is NOT a merged PR** — one of the two
  survivors had exactly that shape and a naive sweep would have destroyed it.
- **A31 — Order the drain by RELEASE impact, not by PR number (2026-07-26).** Only `fix:`/`feat:`
  trigger semantic-release; `docs:`/`test:`/`bench:`/`chore:` complete without publishing. A
  non-releasing merge therefore creates no publish to race — its gate is just "the main run
  completed", ~6 min, versus ~30–60 min for a release cycle. Landing the non-releasing PRs first took
  the queue 12 → 7 in about an hour that would otherwise have bought two merges. The one-per-publish
  rule protects an in-flight PUBLISH; it is not a per-PR serialisation.
- **A32 — The drain gate is "newest main run COMPLETED", not "completed GREEN" (2026-07-26).** When
  `main` is red, the fix for that red must still be mergeable — requiring green before merging the
  thing that makes it green is a deadlock. Merge the hotfix, then confirm `main` actually recovered
  (a subsequent green run), which is the real evidence the fix worked. Everything ELSE stays parked
  while red, because merging onto a broken `main` compounds it and obscures which commit owns the
  failure.

- **A33 — `release-intent` being SKIPPED proves nothing; the publish job runs on every main push
  (2026-07-26, cost: one reddened release).** Before merging into an in-flight main run I checked its
  job list, saw `release-intent` *skipped*, and concluded "this run publishes nothing, so there is no
  push to race". Wrong on two counts. `release-intent` has `if: github.event_name == 'pull_request'`
  — it is a PR-title validator and is ALWAYS skipped on a push, so it says nothing about whether a
  release will happen. The job that matters is `release` ("Semantic Release"), gated on
  `github.ref == 'refs/heads/main' && github.event_name == 'push'`, and because it `needs:` the full
  test matrix it does not even appear in the job list until late. Merging landed a second push on top
  of it and its `git push` was rejected non-fast-forward (`Failed to push branch (main) to remote`,
  run `30223536622`). It self-heals on the next push — do NOT rerun — but the release was lost.
  **The only safe signal is the newest `ci.yml` run on main reaching `completed`.** `tag == PyPI` is
  not sufficient either: a run can have tagged and still be mid-publish.
  **Second window, different failure:** once `Semantic Release` HAS succeeded, the push race is over
  but the publish tail (wheels, native assets, `publish-pypi`) is still running. Merging then starts
  a new run whose concurrency group CANCELS the tail, leaving a tag with no PyPI artifact — the
  version-soup state #47 exists to detect. Wait for PyPI to actually serve the new version.

- **A34 — Prose and PR metadata are part of the artifact (2026-08-02).** PR #910 was code/test green,
  but an independent read found a malformed Markdown/Python example and counts whose denominator was
  unstated. Gate titles, bodies, comments, examples, and status counts against the final commit just as
  you gate code. After scope changes, refresh and re-review PR metadata; “0 unchecked” and “0 total” are
  different claims.
- **A35 — Plan approval expires when a premise changes (2026-08-02).** A unanimous plan review did not
  survive the live-code deep dive: the writer population was incomplete, a Rust method was public, and
  a Python backend twin still carried the retired fallback. Any material premise change invalidates the
  old verdict. Amend the plan, hash the exact new artifact, and re-run the thinktank before build.
- **A36 — A site regression is not a class census (2026-08-02, #859).** The codemap-specific fix/test
  was recorded as satisfying a class-level writer ratchet, while three production writers and generated
  helper source remained outside the population. A class claim needs an independently derived closed-
  world population, mutation controls, and a zero-violation assertion; a single fixed site proves only
  that site.
- **A37 — Census the defect surface, including generated interpreters (2026-08-02).** Discover writers
  from production write/spawn roots, then resolve aliases, local imports, rebinding/shadowing, generated
  `python -c` source, and raw candidate calls. Fail closed on dynamic/unparseable generated payloads.
  Sanction an exact callsite/operation/destination-provenance fingerprint, never a whole function.
- **A38 — Leaf resolution order and parent anchoring are separate security contracts (2026-08-02).**
  Calling `.resolve()`/`realpath()` before a no-follow writer erases leaf-symlink identity. Even with a
  safe leaf check, an attacker can swap a parent or junction before mkdir/publication. Preserve the raw
  leaf identity; anchor directory creation, temp creation, and publication to opened identity-verified
  parent handles; Event-gate both leaf and parent swaps on Unix and Windows.
- **A39 — Class fixes cross to twins (2026-08-02, extends A27).** `RustCoreBackend` removed an unsafe
  `TypeError` signature-compatibility retry while `CPUBackend` kept two copies that dropped
  `invert_match`. After a class fix, grep sibling adapters/helpers for the retired shape and add a
  population ratchet; otherwise the twin re-fires the same defect.
- **A40 — No in-repo caller does not authorize public-API deletion (2026-08-02).** Rust
  `CpuBackend.replace_in_place` is exported in an `rlib`; downstream callers are not visible to an
  in-repository census. Retain and harden public signatures unless a deliberate breaking/deprecation/
  migration decision authorizes removal. Pin the exact public function type at compile time.
- **A41 — Preserve mixed dispositions (2026-08-02).** #90's doctor half shipped while its bounded WSL
  half was retired as non-reproducing/non-defect. Do not flatten `shipped + retired`, `fixed + blocked`,
  or `implemented + demand-gated` into one flattering word. Track each sub-outcome and close the parent
  honestly.
- **A42 — Producer→consumer dogfood must not change what it verifies (2026-08-02).** Materializing a
  verification result inside the repository can dirty the very state the consumer is meant to attest.
  Prefer bounded stdin/captured stdout, keep producer and consumer exits separately, and pin the full
  matrix: `0→0`, `1→0`, valid `2→0`, malformed consumer `2` with no receipt.
- **A43 — Exact CI completion includes the job population (2026-08-02).** A PR check rollup can grow
  while jobs are still being created. Capture the exact workflow run ID and head SHA, require the run
  `completed`, record its job-count floor, and prove zero unfinished/failing jobs. Do not infer
  completion from a momentary rollup list.
- **A44 — Attribute each SHA to what it proves (2026-08-02).** `origin/main`, the newest main-CI head,
  a PR head, a squash merge, and a semantic-release `[skip ci]` commit can all differ. Record each claim
  against the exact artifact and run that proves it; never cite the newest convenient SHA for all arms.
- **A45 — Durable CEO status is a closed-world snapshot, not a hand-picked top five (2026-08-02).**
  Separate active/buildable, environment-blocked, CEO/financial-gated, demand/research-gated, and
  terminal corrections. Give every live item one stable ID/owner/trigger and assert that the canonical
  set has no unowned extras or omissions. Update `MEMORY.md` and the handoff in the same change.
- **A46 — Hash the canonical artifact, and state the hash method (2026-08-02).** Two clean Windows
  worktrees held clean-filter-equivalent plan content but different raw mixed-line-ending bytes. A bare
  “SHA-256” can therefore disagree without a semantic change. For plan gates, hash the designated
  canonical worktree bytes (or canonical Git blob), record which, and make every seat verify that same
  method/path before auditing.
- **A47 — Validate the task dependency graph, not only each task (2026-08-02).** Round 16 found Task 6
  importing a service not created until Task 8 and demanding a subprocess command not registered until
  Task 7. Before approval, prove every required producer/service/registration exists before its first
  consumer/test. A test that fails only at command discovery is not a behavioral RED.
- **A48 — Directory-handle anchoring covers locks, state, and configuration reads (2026-08-02).** Leaf
  no-follow flags do not stop an intermediate parent swap. Create/open a stable fence, read/publish its
  protected index, and read repository-controlled configs relative to verified confined handles; bound
  file/count/aggregate bytes and Event-test swaps before create, after lock, and before publish/read.
- **A49 — Every deferred security behavior needs a canonical owner (2026-08-02).** The Rust direct-file
  symlink behavior was called a follow-up but had no ID, owner, or closeout state. A known security/
  compatibility choice cannot disappear inside a broader shipped row: assign a stable ID, disposition,
  threat boundary, owner, and reopen trigger.
- **A50 — The implementation PR owns its live tracker transition (2026-08-02).** A `READY` row left
  unchanged after its draft PR exists permits duplicate dispatch and false CEO status. Open the draft on
  an independently failing RED, immediately commit `IN_FLIGHT` with the real PR number and ordered PR
  history, and keep the separate post-merge closure PR for `SHIPPED`.
- **A51 — Green and approval are artifact-specific (2026-08-03).** PR #911's committed head was green
  while newer Round-60 plan bytes existed only in its worktree. A run or verdict clears exactly the named
  SHA/hash it inspected—never later local edits, a sibling worktree, or “the same plan” by description.
  Record PR head, local plan hashes, review hashes, and merge SHA separately.
- **A52 — Architecture `SHIP` is not security clearance (2026-08-03).** The Round-59 transaction shape
  was coherent enough for architecture `SHIP` and still had forgeable signer/receipt authority,
  unenforceable PATH atomicity, and breakaway containment gaps. Security-class work needs its own
  adversarial `SHIP` on the same bytes; a different lens's approval cannot substitute.
- **A53 — Security plans name enforceable primitives (2026-08-03).** “Atomic CAS,” “trusted signer,”
  “owned PATH entry,” and “kill descendants” are goals, not Windows contracts. Name the concrete API,
  flags, authority root, identity comparison, failure behavior, and adversarial control. If the platform
  primitive is unavailable, fail closed instead of inventing a weaker fallback.
- **A54 — Authority is never discovered from an untrusted search path (2026-08-03).** PATH, an adjacent
  binary directory, an environment variable, a caller-supplied path, or an install-command digest cannot
  establish installer ownership. Start from a fixed protected state root, retain its identity, verify its
  cryptographic binding, and treat path strings only as hints to objects whose opened identities match.
- **A55 — Containment includes escape denial (2026-08-03).** `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` is
  incomplete if either Job breakaway flag or `CREATE_BREAKAWAY_FROM_JOB` is permitted. Pin all three
  absences and run a real descendant-breakaway RED; “primary process died” is not process-tree proof.
- **A56 — A resource cap must fire at every door (2026-08-03).** Bootstrap, full CLI, direct native,
  native→rg, native→sidecar, and every matcher engine must join the same no-refund ledger before route
  selection or child creation. Independently test the inclusive cap and mixed-source aggregate; separate
  counters and an uninstrumented PCRE2 route are fail-closed defects, not implementation details.
- **A57 — Static manifests and live receipts have different authority (2026-08-03).** A committed
  manifest defines the exact nodes/jobs that must run and therefore contains no live run ID. A live
  receipt proves this execution only after a verifier independently re-derives the Actions/artifact
  tuple and cross-checks Python JUnit plus Rust node census. Self-attested JSON is not anti-replay proof.
- **A58 — Retry review by narrowing, not by weakening (2026-08-03).** A broad Cursor/council prompt can
  time out while exact-paragraph reviews converge quickly. Retry the disputed paragraph and invariant,
  preserve the original severity, and send the resulting work to Sol. A no-verdict seat is recorded as
  failed and replaced; it is neither approval nor an infinite blocker.
- **A59 — Discover deferred capabilities before declaring a required tool absent (2026-08-03).** Exa
  was available through the deferred tool catalog after appearing absent from the initial surface.
  Search the callable-tool catalog first, then record genuine provider failure and use an approved
  fallback. Also select the newest canonical worktree before review; never promote an older dirty copy.
- **A60 — Never point WSL `uv` at the Windows checkout's `.venv` (2026-08-03).** A WSL
  `uv run --no-sync --project /mnt/c/...` probe treated the Windows virtual environment as incompatible,
  removed it, and created an empty Linux venv in the same path. That turned a dependency check into
  shared-environment mutation and forced a locked Windows rebuild. WSL/Cursor worktrees use a WSL-local
  venv (or CI); Windows verification runs from PowerShell in the canonical Windows checkout. Never cross
  those environment roots. If this happens, move the incompatible venv aside, recreate it from Windows
  with `uv sync --frozen`, verify imports/version, and only then resume gates.
- **A61 — Behavioral RED pins the exact expected reason (2026-08-03).** A RED that accepts crash,
  import failure, panic, or setup error as success is not behavioral proof. Pin the exact expected
  refusal/reason class and reject any arm that dies before exercising the contract.
- **A62 — Route/start evidence comes from the real producer (2026-08-03).** Hardcoded bools and
  production hooks that self-attest before actual start are forgeable. Route/start proof comes from the
  actual producer/constructor plus test-owned OS or raw evidence.
- **A63 — Containment proof authenticates provenance and lifecycle (2026-08-03).** Event signals,
  EOF, or PID text alone are not containment. Authenticate writer/client provenance, prove
  alive-before → dead-after, and prove cleanup independently of parent-forgeable heartbeats.
- **A64 — Crypto negative proof needs a valid operation and positive control (2026-08-03).** A
  negative crypto test must use a valid API operation, assert an exact refusal class, and carry an
  exportable/trusted positive control. Invalid flags that accept “any error” prove nothing.
- **A65 — Security grammar validates full authority, not substrings (2026-08-03).** SDDL and similar
  grammars must validate full sections, types, flags, and effective authority. Unknown, inherit-only,
  and garbage forms fail closed; substring principal matches are not acceptance.
- **A66 — Resource-owning protocols name exact close ownership (2026-08-03).** Every protocol that
  acquires a resource names its close primitives and proves exact-once reverse cleanup on success,
  `BaseException`, and cleanup failure while preserving the primary error.
- **A67 — RED scaffolds cannot enable partial public behavior (2026-08-03).** A test or temporary
  public flag must not unlock unbounded work or a GREEN path before the guard/ledger is active.
  Accidental public `-f`/`--file` reads before the resource ledger are fail-closed defects.
- **A68 — Immutable-SHA CI clearance needs a real run (2026-08-03).** Clearance requires a real CI
  run on the immutable SHA, expected per-node outcomes, raw artifacts, and the exact population.
  No run is no clearance; a local RED replay is not Windows CI proof.
- **A69 — Security green is point-in-time, not durable clearance (2026-08-03).** A fresh advisory-
  database finding on the current head blocks merge even when an older head was green. Fix every
  fixable advisory by raising every live direct/constraint floor and regenerating the lock; update
  pinned validator tests and user remediation strings, replay the affected feature, and obtain a
  new exact-head audit. Never add an ignore for a vulnerability that has a fixed release.


- **A70 — Ambient default signing keys pollute `--sign` no-key REDs (2026-08-06).** Clearing
  `TG_EVIDENCE_SIGNING_KEY` is not enough when `~/.tensor-grep/keys/evidence_ed25519.key` exists —
  emit still signs and the NEG arm looks green. Isolate `HOME`/`USERPROFILE` (or remove the default
  key) before claiming fail-closed. Receipt: W5 published-wheel dogfood / PR #962.
- **A71 — Free-form bullets under `## Canonical status index` are illegal (2026-08-06).** The tracker
  parser accepts only `Status:` / `PR:` / `Trigger:` checklist rows. A campaign note under that
  heading reds `test_ceo_demand_duplication_is_rejected`. Put prose in a separate heading (e.g.
  `## Campaign note`). Receipt: PR #962 first CI.
- **A72 — Merged implementation with a stale `IN_FLIGHT` row is board debt (2026-08-06).** Feature
  code on `main` is not tracker-closed until the row is `SHIPPED` with Implementation PRs + Closure
  PR + Merged SHA (A50). F7 / CPU-BACKEND / REF-CALL-REGISTRY sat IN_FLIGHT after their impl PRs
  merged until the 2026-08-06 CEO reconcile.
- **A73 — Bare published wheel ≠ semantic/`tg find` surface (2026-08-06).** `uvx --from tensor-grep==X`
  without `[semantic]` / `tg install-dense` has no `model2vec`; find degrades with
  `rank_fallback_reason`. Enterprise CUJ dogfood uses prepare/search/evidence/review-bundle/ledger
  unless dense extras are installed first.
- **A74 — Quota-blocked Sol/Fable SHIP is provisional (2026-08-06).** An orchestrator substitute
  verdict is not an independent vendor seat. Re-dispatch Sol/Fable when quota returns for
  security/load-bearing claims; do not treat the substitute as durable clearance.
- **A75 — Premise-check the ready-to-build queue before dispatch (2026-08-06, #935).** Six of six
  “ready” items were already shipped. A plan against a fixed defect has perfectly resolving citations;
  reproducing the defect (or proving absence on `origin/main`) is Step 0.
- **A76 — Board freshness is ordinal CHANGELOG distance (2026-08-06, #933).** Patch subtraction and
  major.minor sentinels false-red on minor bumps; no tolerance absorbs a sentinel of
  `tolerance+1`. Measure ordinal distance in CHANGELOG.md.
- **A77 — Stdin+heredoc merge pollers manufacture ALL_TERMINAL (2026-08-06 PM).** Piping
  `gh pr checks` into a shell construct whose heredoc consumes stdin can yield an **empty** checklist
  that a naive poller treats as “every check done.” Receipt: #963 squash-merged while ~10 PR checks
  were still pending; main push CI later went green, but the merge gate had already lied. **Write
  checks to a file** (or capture argv that cannot steal stdin), require heavy lanes **present by
  name/count**, and never treat “0 pending over an empty rollup” as clearance (extends A43 / Form 8).
- **A78 — Provider usage-limit / error seats are FAILED (2026-08-06 PM).** A Sol/Opus/Fable seat that
  dies with “hit your usage limit” (or equivalent provider error) is a **failed seat**, not pending
  approval and not a soft wait (extends A10/A58/A74). Record FAIL; do not promote substitute SHIP to
  durable security clearance; re-dispatch when quota/Spend Limit restores (Pro cycle noted ~2026-08-14).
- **A79 — Status-stamp PRs must retarget governance pins (2026-08-06 PM).** Stamping board READY→BLOCKED
  without updating tracker tests that assert `Status: READY` (or that forbid BLOCKED on program owners)
  reds CI on the truth-fix. Enumerate pins that name the old status in the **same** PR as the stamp.
- **A80 — Gate the tip under review, not the archaeological RED SHA (2026-08-06 PM).** Docs and MEMORY
  may still name historical RED `6367614…` while the repair branch tip has rebased and advanced. Sol,
  CI, and merge clearance bind to the **exact tip bytes** (A51); citing the old SHA after rebase is an
  artifact mismatch.
- **A81 — Implementer HIGH receipts ≠ Sol SHIP (2026-08-06 PM).** Local commits, receipt files, and
  “HIGH1–10 applied” self-reports are hypotheses. Task 2A stays FIX-FIRST until exact-byte Sol returns
  `SHIP` on the named tip (extends Form 1 / A74 / never-trust-self-report).
- **A82 — AMEND_SPINE when READY∩reconcile-BLOCKED (2026-08-06 PM).** Thinktank `AMEND_SPINE`: drop
  MCP/F5–F8/#89/#90 from the build spine; START_NOW = docs/R0/D1 (board stamp + recommendation packets)
  until Task 2A Sol SHIP + Windows CI. Board READY is not a build license when BACKLOG reconcile says
  BLOCKED (pairs with A71/A75).
- **A83 — Front-door argv REWRITE shadowing (2026-08-09, #979).** An argv normalizer that rewrites one CLI shape into another (`SEARCH_OPTION_FIRST_FLAGS` → `tg search …`, `normalize_top_level_search_args`) redirects a "positional" validator's coverage: `tg PAT --gpu-device-ids 0 --count-matches` never reaches `run_positional_cli` — it becomes the search form, and the search path can silently drop `gpu_device_ids` (RipgrepSearchArgs has no gpu field) while rg-passthroughing. A fix is only closed when it guards EVERY door the rewritten argv can reach. Before claiming a door is closed, trace the normalizer: which flags does it rewrite, and which gate actually sees the rewritten form? (Census the rewrite list + the target parser, not just the door you added the guard to. This is the registration-completeness law applied to argv normalization.)
- **A84 — Cross-platform path semantics: platform-gate the drive-absolute strip (2026-08-09, #983).** A Windows-only path normalization applied unconditionally (strip the leading `/` from `/C:/…` drive-absolute URIs) re-creates the escape on POSIX: the root-anchored URI becomes a RELATIVE path that resolves inside cwd, flipping a confinement check from refused→passed. Any path-shape transformation that is platform-meaningful must be gated on `os.name == "nt"` (or its POSIX analogue) AND both arms pinned in a cross-platform test. The real CI matrix is the only oracle that catches the flip — a Windows-local green proves nothing about the POSIX arm.
- **A85 — Env-independent gated tests (2026-08-09, #984).** A test that must pass in BOTH the dev env AND CI pytest envs (which can lack optional engines: ast-grep binary, native tree-sitter, dense model, compiled rust_core ext) must be env-independent BY CONSTRUCTION: force a controlled deterministic seam for the optional engine (dense-unavailable force; controlled AstBackend shim) so the verdict is identical everywhere — never env-detect. A test that passes locally and fails CI on a missing engine is a DEFECT in the test, not the product. Mutation-control: the census/ratchet must RED on a deleted member, a missing stamp, or an allowlisted family raising an unexpected exception type.
- **A86 — Stale-ready labels: "ready"/"green" must cite the head's own completed run (2026-08-09, #967/#977).** Two PRs labeled merge-ready carried heads predating the base by several merges; each showed 7 stale tracker-freshness failures that were base-staleness, not content. Any "ready" / "green" label must cite the head SHA's own completed check-run set (A44/A51), and before merging a long-lived branch, rebase onto current main and re-verify — a green-against-stale-base is a Form-10 (branch-unit) false green.
- **A87 — Static review ≠ typecheck; CI is the ONLY compile oracle for Rust (2026-08-09, #987/#988).** Two Rust audit-fix PRs (M16/M17) each passed multiple codex adversarial static reviews ("no compile defect found"), then the FIRST real CI run found genuine compile errors — E0599 `starts_with` on `&OsStr`, E0308 mismatched types, E0382 borrow-of-moved (`canonical_root`). Static/logic review cannot typecheck; a Rust PR's gate must include "first CI cargo run compiles" BEFORE any codex SHIP verdict is treated as durable. Structural arguments about Rust are hypotheses until the compiler and tests run.
- **A88 — Dogfood fixtures must BITE (Form 6 applied to the published-wheel dogfood too, 2026-08-09, M1).** Probing the shipped M1 checkpoint junction-containment fix, the wheel "passed" — because the hostile fixture never applied: `mklink /J` silently failed to create a junction when the target directory was NON-EMPTY, so the tree had no junction and the snapshot was trivially safe. Verify the hostile setup actually bites BEFORE trusting a dogfood result: check `os.path.islink()`/junction resolution differs from the plain tree (on Windows, junctions are NOT symlinks — `Path.is_symlink()` is False on a junction; the parent-resolve containment is the guard that matters). A dogfood PASS on a fixture that never applied proves nothing.
  *ERRATUM (2026-08-12 retention audit): the receipt above attributes the silent failure to a
  NON-EMPTY target; the actual `mklink /J` contract is that the LINK path must not already exist
  (the target directory MAY be populated — verified empirically, and the canonical helper
  `_plant_ancestor_link_or_skip` removes the link path first with a populated target). The law
  stands; the mechanism sentence is corrected.*
  *SUPERSEDED (2026-08-13, A107 probe receipt): the sentence above claims junctions are NOT
  symlinks. On the PINNED Rust 1.96.0 toolchain a real `mklink /J` junction reports
  `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` via
  `symlink_metadata` (bounded std-only probe, positive+negative controls) and
  `OpenOptions::open` follows it. The Python `os.path.islink()` half of the claim stays true;
  the Rust-std half is toolchain-version-dependent. Probe receipt:
  docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5.*
  *SUPERSEDED (2026-08-13, A107 probe receipt): the sentence above claims junctions are NOT
  symlinks. On the PINNED Rust 1.96.0 toolchain a real `mklink /J` junction reports
  `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` via
  `symlink_metadata` (bounded std-only probe, positive+negative controls) and
  `OpenOptions::open` follows it. The Python `os.path.islink()` half of the claim stays true;
  the Rust-std half is toolchain-version-dependent. Probe receipt:
  docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5.*
  *SUPERSEDED (2026-08-13, A107 probe receipt): the sentence above claims junctions are NOT
  symlinks. On the PINNED Rust 1.96.0 toolchain a real `mklink /J` junction reports
  `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` via
  `symlink_metadata` (bounded std-only probe, positive+negative controls) and
  `OpenOptions::open` follows it. The Python `os.path.islink()` half of the claim stays true;
  the Rust-std half is toolchain-version-dependent. Probe receipt:
  docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5.*
  *SUPERSEDED (2026-08-13, A107 probe receipt): the sentence above claims junctions are NOT
  symlinks. On the PINNED Rust 1.96.0 toolchain a real `mklink /J` junction reports
  `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` via
  `symlink_metadata` (bounded std-only probe, positive+negative controls) and
  `OpenOptions::open` follows it. The Python `os.path.islink()` half of the claim stays true;
  the Rust-std half is toolchain-version-dependent. Probe receipt:
  docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5.*
  *SUPERSEDED (2026-08-13, A107 probe receipt): the sentence above claims junctions are NOT
  symlinks. On the PINNED Rust 1.96.0 toolchain a real `mklink /J` junction reports
  `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` via
  `symlink_metadata` (bounded std-only probe, positive+negative controls) and
  `OpenOptions::open` follows it. The Python `os.path.islink()` half of the claim stays true;
  the Rust-std half is toolchain-version-dependent. Probe receipt:
  docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5.*
  *SUPERSEDED (2026-08-13, A107 probe receipt): the sentence above claims junctions are NOT
  symlinks. On the PINNED Rust 1.96.0 toolchain a real `mklink /J` junction reports
  `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` via
  `symlink_metadata` (bounded std-only probe, positive+negative controls) and
  `OpenOptions::open` follows it. The Python `os.path.islink()` half of the claim stays true;
  the Rust-std half is toolchain-version-dependent. Probe receipt:
  docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5.*
  *SUPERSEDED (2026-08-13, A107 probe receipt): the sentence above claims junctions are NOT
  symlinks. On the PINNED Rust 1.96.0 toolchain a real `mklink /J` junction reports
  `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` via
  `symlink_metadata` (bounded std-only probe, positive+negative controls) and
  `OpenOptions::open` follows it. The Python `os.path.islink()` half of the claim stays true;
  the Rust-std half is toolchain-version-dependent. Probe receipt:
  docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5.*
  *SUPERSEDED (2026-08-13, A107 probe receipt): the sentence above claims junctions are NOT
  symlinks. On the PINNED Rust 1.96.0 toolchain a real `mklink /J` junction reports
  `is_symlink: true` / `is_symlink_dir: true` / `is_symlink_file: false` via
  `symlink_metadata` (bounded std-only probe, positive+negative controls) and
  `OpenOptions::open` follows it. The Python `os.path.islink()` half of the claim stays true;
  the Rust-std half is toolchain-version-dependent. Probe receipt:
  docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5.*
- **A89 — Real-artifact test arms beat fake-backed ones in parity oracles (2026-08-09, #987).** M16's three-arm composite-count parity test passed with SPAN FAKES while production read the WRONG ast-grep JSON fields (`range.start.index` vs the real 0.42.1 `range.byteOffset.start/end`), so the "parity" was pinned against the bug. Only adding a REAL `ast-grep --json` subprocess arm surfaced the divergence. Whenever a parity/oracle test can drive the real producer cheaply, it must — a fake-backed arm can certify a lie as three arms of agreement. (Extends the Verification-Oracle family: the oracle's INPUT was fake, so the agreement was between the test and its own fiction.)
- **A90 — Fail closed on unknown subcommands; never fall through to search (2026-08-09, #993 / world-class H1).** The Python bootstrap door (`bootstrap.py:374-383` `_normalize_search_invocation`) returns every unknown-first-arg as search args, so `tg edit-ready --help` prints `Usage: tg search` exit 0 — an agent concludes a nonexistent command exists. Same family as the "registration-completeness" and "scope-honesty" laws, but about the CLI DISPATCH surface: an unknown top-level command must exit non-zero with `error.code=unknown_command` and `nearest[]`, on BOTH front doors (Python `KNOWN_COMMANDS` + native `normalize_top_level_search_args`/`is_known_python_command`), never be swallowed into search. A feature that isn't on the CLI must not be faked by a search fallthrough.
- **A91 — "No core-Rust logic" never means "no native touch" (2026-08-09, #993).** The public surface is the managed native `tg.exe`; a Python/sidecar feature that misses the native front-door enrollment (`Commands::X` passthrough + `PUBLIC_TOP_LEVEL_COMMANDS` parity test) is invisible through the real binary and its first dogfood fails with the very unknown-command bug it fixes. Every "Python-first" slice must state its both-front-door + 4-site-registration enrollment in the same slice, or it is honest only as "no core-rust LOGIC," never as "no native touch."
- **A92 — Executed evidence must be escrowed to a key the verified principal does NOT hold (2026-08-09, #993 / S1).** "validation ran green" certified by the editing agent is self-attestation (Oracle Form 8 — the split-oracle/self-report family). A verify-edit PASS requires escrowed subprocess evidence — captured stdout-hash + exit code + duration, signed by a key pinned via `TG_EVIDENCE_TRUSTED_KEYS` that the editing principal cannot use (CI-held). Absent that, the verdict is UNVERIFIED with a reason, never PASS. Also: verification without a tree fingerprint certifies drift — a ticket must carry `base_sha` + working-tree fingerprint and verify fails closed on drift, or a rebase/sibling edit can certify a state nobody prepared (TOCTOU/drift = the push-race class inside a ticket flow).
- **A93 — Self-dogfood is self-consistency, not demand, and roadmap premises need ground-truth before the council (2026-08-09, #993).** 22/22 PASS on tg dogfooding tg proves tg works for itself; the 5 self-triaged "bad oracle" rows need EXTERNAL-customer grounding (S1-S7 demand). And two of eight "banked" roadmap claims were false until a ground-truth seat checked origin/main (`prepare_service` fn name; `session prepare/resume` are actually UNBUILT). Any plan entering the design council must first premise-check its "already shipped"/"partially banked" claims against origin/main (A75), or the council certifies fiction.
- **A94 — Skill/doc version stamps rot one release after the last refresh; freshness is a maintenance sweep, not a one-time event (2026-08-11).** The 2026-08-11 audit found 21 stale version stamps + 7 language-tier contradictions in the in-repo `.claude/skills/` library ONE release after the previous refresh — every "verified against vX" line and every hand-written derivation count is a snapshot, not a promise. The standing mechanism is now the `tensor-grep-release-drift-check` skill: version-stamp grep below the current tag, re-derived counts (language tier via `_symbol_navigation_descriptor()`, skill count = `tensor-grep-*` folders + `code-search-and-retrieval-reference` with the bare `tensor-grep` usage skill deliberately excluded, tree-sitter package count), and known-state facts — with append-only SUPERSEDED blocks for any dated claim that is now wrong (leave the old sentence as dated history, mark it, never silently rewrite or delete). Run it after EVERY release; it is a command like `.claude/skill_anchor_audit.py`, deliberately NOT a pytest (the numbers drift by design and a hard gate would red every PR).
- **A95 — A "verified correct — do not fix" note is part of the contract it guards, and it must be updated in the SAME change that breaks it (2026-08-11).** CLAUDE.md's "**32 skills** is VERIFIED CORRECT" note carried its own re-derivation (`ls .claude/skills/ | grep -c '^tensor-grep-'` = 31 + 1). Adding a 34th folder meant updating the count to 33, the re-derivation echo (32 + 1), the bucket list name, AND the AGENTS.md mirror — a three-site edit where the "do not fix" note itself was one of the sites. A fix-note that outlives its own stated number is the deny-list failure mode wearing a confident hat: it tells the next agent the count is right when it is stale.
- **A96 — Non-ASCII punctuation in governed docs defeats byte-exact `edit`-tool matches; splice by line index, never by quoting the line (2026-08-11).** Em dashes (U+2014) and en dashes (U+2013) in skill prose (e.g. "straight field dump —", "saddle ~5s") made three consecutive `edit`-tool replacements fail with "oldString not found" while the text LOOKED identical — the tool matches exact bytes and PowerShell `python -c` mangling made the fixes worse. The reliable path: a script file (`write` a `.py`, run it) that reads with `encoding="utf-8"`, locates by line INDEX + assertion, splices the target lines, and writes back with `newline=""` — assertions (`assert "needle" in line[i]`) prove you hit the right lines.
- **A97 — An interrupted/aborted tool call may have ALREADY APPLIED; read the target state before re-applying (2026-08-13).** During the retention campaign an `edit` call returned "Tool execution aborted" yet had actually landed; re-applying the same content duplicated whole sections across AGENTS.md, SESSION_HANDOFF.md, and the reconciliation doc (the independent gate caught them as the top finding). After any interrupted/ambiguous tool result, READ the file back before retrying — never re-apply blind. A double-apply duplicate is worse than the original gap, because it reads as two authoritative copies of the same section and a later reader trusts whichever they hit first.
- **A98 — A spot-check census of N files is a claim about the ONE file checked (2026-08-13).** The stale-branch reconciliation declared all 11 dirty docs "stale snapshots, behind not novel" on the strength of ONE file's header (SESSION_HANDOFF.md) and missed two NEVER-COMMITTED sections living in the dirty AGENTS.md (Session Lessons 2026-08-07 + CI Cost Discipline) that a cleanup would have deleted forever. A census over N files needs a mechanical per-file diff or an explicit per-file disposition; generalizing from one member is "the population is the defect" class. Receipt: ERRATUM-2 in `docs/audits/2026-08-12-stale-branch-reconciliation.md`.
- **A99 — An audit/verification tool must be bound to the artifact it audits (2026-08-13).** The pre-hardening `tg-skill-audit.js` hardcoded a repo root, recorded no SHA or file manifest, and counted ANY truthy cluster response as full coverage — so it could audit the WRONG checkout and still report 6/6 covered (the split-oracle class). A verifier must record audited root + HEAD SHA + a path/blob manifest, and a coverage claim requires EXACT set equality between the expected population and the reported coverage; a truthy response that omits members is PARTIAL, a null lane is CANNOT_VERIFY, and a CLEAN verdict needs non-zero sampled evidence — never clean-on-empty.
- **A100 — A workflow/tool that advertises a capability must actually execute it; metadata-only is decoration (2026-08-13).** `tg-audit-fix-loop.js` advertised five phases (Seam/RED/GREEN/Gate/Verify) and defined two schemas but contained ZERO `phase(...)`/`agent(...)`/terminal `return` — it was not an executable workflow. An unconsumed schema or un-run phase is a false advertisement of capability. Advertised structure must be wired to execution, and a stub that merely looks like a tool must be labeled as such (or wired) before anything depends on it.
- **A101 — The third recurrence of the same flake is a structural-fix signal, not a rerun signal (2026-08-13).** The `windows-agent-readiness` `public-version-powershell` probe flaked 3× in 3 runs (30s timeout while `-NoProfile` passed in <1s). A rerun self-heals ONCE; the third sighting means fix the probe (raise the timeout / make it tolerant), not keep rerunning. Record the recurrence count beside the flake so the next session sees "3×" instead of treating it as a fresh one-off.
- **A102 — Input-brief facts are hypotheses; the builder must verify them against the tree before writing on them (2026-08-13).** Two of seven retention fix-wave seats corrected facts IN THEIR OWN BRIEFS (the dense-weight flip first released v1.79.0, not v1.93.2; route-test #672 shipped v1.81.21, not v1.100.0). A brief's stated facts — like an implementer's output report (A81) — are hypotheses until re-derived from the tree. A seat must verify each load-bearing input fact before writing on it and must report any brief fact that fails verification rather than silently propagating it.


- **A103 — A RED-arm baseline swap must snapshot the builder's uncommitted bytes before touching the
  file (2026-08-13).** Reverting a file to its pre-fix revision (`git checkout origin/main -- <file>`,
  an `Out-File`/patch apply) inside a builder's worktree destroys whatever uncommitted work the
  builder had in that file; this session's W2A probe-retry work was clobbered exactly that way and
  re-applied from the spec. Before any baseline swap, copy the current bytes aside; prefer re-editing
  the single mutated line back instead of reverting the whole file. Same hazard family as the
  "git stash is unsafe once parallel worktrees exist" law, single-file variant.
- **A104 — The A3 adversarial gate is a real-finding convergence loop; it ends only on independent
  SHIP, never on round count (2026-08-13).** W3B's symlink guard took 13 gate rounds plus a final
  codex pass, and nearly every round produced a genuine FIX-FIRST, not a nit: a fault-injection seam
  that bailed before the stat (invisible to a fail-open rewrite), a trailing-separator stat bypass,
  residuals without a filed owner row, a board row the shipped code cited but nobody had filed, and
  an unobservable skip path. Each is a reusable finding class; budget 10+ rounds for a security PR.
  The independent gate still fires after the builder's self-gate is green (A18).
- **A105 — Normalize the path BEFORE a no-follow stat, and own the residuals a leaf-stat cannot
  cover (2026-08-13).** On POSIX, `lstat("dirlink/")` resolves THROUGH the final symlink, so a guard
  that stats the raw caller string lets a trailing-slash path bypass `is_symlink()` and hand a link
  root to a follow-root walk. Strip trailing separators (e.g. `Path::components().collect()`) before
  the stat. Separately, `symlink_metadata` lstats the LEAF only: a symlink in a non-leaf ancestor
  component and the directory-ROOT swap window (stat a real dir, then `is_dir()`/walk re-resolves)
  are additional residuals that must be named in the code comment, the threat model, AND a filed
  follow-up row — never silently absorbed (A38/A48/A49).
- **A106 — A green test that can silently skip is a hazard; promote skips to panics via an env var
  armed in CI (2026-08-13).** The W3B guard tests' Windows skip branches printed a line and
  returned, so a run where every node skipped read green while proving nothing about the security
  fix. The shipped mechanism: every skip site panics with an explicit message when
  `TG_REQUIRE_SYMLINK_TESTS` is set, and CI arms it. Apply the same promotion to any
  environment-dependent test whose silent skip would masquerade as coverage (A88 / Oracle Form 3).
- **A107 — A contested platform fact is settled by a bounded probe on the PINNED toolchain, not by
  council vote; a law whose embedded claim is superseded must itself carry the SUPERSEDED marker
  (2026-08-13).** Two W3A council rounds split on whether Windows junctions report
  `is_symlink()==true` to Rust with seats asserting opposite facts and no common probe. A ~30s
  std-only `cargo run --release` probe on the pinned Rust 1.96.0 settled it (`is_symlink: true`,
  `is_symlink_dir: true`) and became the only artifact all seats cite. Consequence: A88's
  parenthetical "junctions are NOT symlinks" is wrong for this toolchain and must carry an
  append-only SUPERSEDED note in the law itself and in every skill quoting it (A94).
- **A108 — Plan-council convergence: hash-freeze each round, fix only the confirmed findings, failed
  seats are not votes, and a verdict-dependent step is a named GATE, never an expansion marker
  (2026-08-13).** The campaign plan converged through 5 council rounds: fix the confirmed findings,
  re-hash the artifact, re-run until N/N APPROVE, with no-verdict seats recorded FAILED and excluded.
  "EXPAND AT WAVE START" was read as "the steps are not written" by half of round 1 — a step whose
  content depends on a future verdict must be written NOW as a named gate with an exact command, a
  concrete pass/fail trigger, and a re-approval rule covering the FAIL branch (A35/A46/A51).
- **A109 — Bounded test handshakes use capacity-1 channels, never a capacity-0 rendezvous
  (2026-08-13).** A capacity-0 `sync_channel` `send` blocks forever when its peer never arrives —
  the unbounded hang the round-3 council caught in a "bounded" swap-gate draft. Capacity-1 channels
  (non-blocking sends) plus `recv_timeout` on every receive bound every wait; an expiry is a
  deadlock detector and panics `CANNOT_MEASURE:`, never a verdict (A17).
- **A110 — `git commit --amend` is safe only while the branch has never been pushed; check for a
  remote-tracking ref first (2026-08-13).** After a push, amend rewrites history sibling agents may
  have fetched. The W1B rule: `git log --oneline origin/<branch>` must print nothing (no remote ref)
  before amending; otherwise make an ordinary second commit. No force-push.
- **A111 — Commit the plan you cite (2026-08-14).** Docs merged onto main must not cite
  plan/spec paths that do not exist in the merged tree; an untracked council-approved plan
  breaks every citation downstream (codex H-02). When committing a previously-untracked
  approved artifact, record the pre-format witness hash AND the committed hash (A46 extension).
- **A112 — A plan-frozen control threshold is met verbatim or the arm is CANNOT_MEASURE
  (2026-08-14).** A looped probe whose control reports 1600 where the plan froze
  `failures == 20` needs a single-shot arm that reports exactly 20; recharacterizing the frozen
  number as "illustrative" is a plan violation, not a fix (codex C-01).
- **A113 — Claim only what the raw artifact discriminates (2026-08-14).** 5/5 arms timed
  out, but only the ONE discriminated arm may be called connect-timeout; an undifferentiated
  `TimeoutError` cannot be upgraded to a specific class in prose, and environment readings
  (CPU%) the harness did not record are observations, not data (codex H-01).
- **A114 — A corrected census is not closed until its location inventory is mechanically
  re-derived (2026-08-14).** Totals can be right while the named lines are wrong; a census
  note's own prose is auditable content, and three audit rounds on one paragraph is the tell
  (codex L-01/L-03). Re-derive locations with a script, never from memory of the file.
- **A115 — Wave receipts are per-row tables, not group sentences (2026-08-14).** "Six rows,
  six commands, six recorded results" asserted as one sentence is a claim, not a receipt; each
  row gets its own command and output in a table (codex C-02; A98 applied to board waves).
- **A116 — Never let `uv run` create a venv inside a bare worktree (2026-08-14).**
  `uv run pytest` in a worktree without `.venv` creates an empty broken venv (`No module named
  pytest`); run worktree tests from the MAIN checkout's venv targeting worktree paths
  (`uv run --no-sync python -m pytest "<worktree>/tests/..."`) and remove any accidentally-created
  worktree `.venv` immediately.
- **A117 — Operator “skip Fable” waives that design-audit seat for the named docs packet only
  (2026-08-15).** It does not authorize product code, spend, CEO_GATED flips, or treating a
  quota substitute as durable clearance (extends A74). Record the waiver on the PR; Sol/Codex
  exact-commit APPROVE still required for the packet bytes.
- **A118 — Local `gh pr merge` failure is not remote truth when another worktree owns `main`
  (2026-08-15).** `fatal: 'main' is already used by worktree` can abort locally after GitHub
  already merged. Judge `gh pr view --json mergedAt`; use the merge API if needed; never assume
  “failed” means “not merged,” and never double-merge.
- **A119 — Docs-only PR job skips are not a cheap main push (2026-08-15).** The PR `changes`
  gate may skip expensive jobs; `push` to `main` always runs the full matrix. Do not forecast
  main wall-clock from PR skipped-job green.
- **A120 — Enclosing shell timeout must strictly exceed probe duration (+ frozen grace)
  (2026-08-15).** A shell `timeout` equal to the probe’s wall duration is Sol REVISE: the
  probe cannot finish cleanly. Freeze duration, grace, and outer timeout as three numbers.
- **A121 — Raising `request_queue_size` without a finite fail-closed aggregate pre-auth
  concurrency cap enlarges DoS admission (2026-08-15).** `ThreadingMixIn` spawns a thread per
  accept; a larger listen backlog without R7 is incomplete DD-006-PERF design (Sol BLOCKER-1).
- **A122 — Demand SATISFIED + design packet on main is not SHIPPED (2026-08-15).** Parent
  DD-006 still needs both DD-006-PERF and DD-006-HONESTY product code under a separate
  deliberate build go (TDD + A3). Do not close the board row on docs alone.

- **A123 — A PR whose BASE is a feature branch gets ZERO CI, and the absence renders as
  "skipping" (2026-08-21).** `ci.yml` filters `pull_request: branches: ["main"]`, and that filter
  matches the **base** ref. Measured: #1068 and #1070 each had exactly one check across their whole
  life (`Dependabot Automation` / `skipped`) while `gh` reported `MERGEABLE`. Control: #1065, same
  `test/` branch prefix but base `main`, `SUCCESS=39`. **Both went RED the moment real CI ran.**
  Before merging anything, assert `baseRefName == "main"`
  (`gh pr list --state open --json number,baseRefName`); a "skipping"-only rollup is an ABSENT
  gate, not a pass. `gh pr edit --base main` alone does NOT restore CI (it fires action `edited`,
  not a default trigger type) — close/reopen does. After the parent squash-merges, rebase the child
  with `git rebase --onto origin/main <parent-tip>` to drop the absorbed commits.
- **A124 — Verify a release PER-ARTIFACT, by expected filename set, never by the version
  appearing (2026-08-21).** `v1.111.1` published 2 of 4 files (no `win_amd64` wheel, no sdist), so
  `pip install` gave different versions per platform. `v1.111.2` then tagged with **zero** PyPI
  files. Two different broken shapes, both of which read as "released" from a tag or a version
  string. Sweep ALL releases, not just the newest — three were incomplete.
- **A125 — "Advertised" is not "installed", and a maintainer's machine is the WRONG POPULATION
  (2026-08-21).** `tg rulesets` lists six security rulesets with rule counts; `tg scan --ruleset`
  exits 1 on a stock `pip install tensor-grep` because `ast_grep_py` is in no dependency and no
  extra and the wheel bundles no native binary. It looked fine from a dev box that has a
  separately-installed native `tg`. **Any acceptance test for a capability must run in a clean
  container off the PUBLISHED artifact**, or it passes while the defect ships. The sibling shows
  the standard: `tg find` degrades visibly, still returns results, and names its fix
  (`tg install-dense`).
- **A126 — A file split must reproduce its baseline PASS *and* SKIP counts (2026-08-21).** A
  drafted split reported "484 passed, 5 skipped" and looked green; the pre-split baseline was
  **489 passed, 0 skipped**. It had invented three `pytest.skip("... unavailable in this
  environment")` guards that would have permanently disabled tests which pass in CI. Capture both
  counts before touching anything, and never silence a post-split failure with an environment
  probe. A bare worktree has no compiled native extension, so native/embedded arms fail there and
  pass in CI — that is an environment artifact to report, not to guard around.
- **A127 — Read exit codes UNPIPED (2026-08-21, twice in one session).** `docker build … | tail`
  reported **exit 0 while producing no image** — that was `tail`'s status. Captured unpiped:
  `REAL_BUILD_EXIT=1`. The same trap nearly produced a false bug report against `tg defs` (`| head`
  masking a correct rc=1). For any command whose status you will act on:
  `cmd > log 2>&1; echo $?`, and verify the ARTIFACT (`docker images …`) — the one claim a misread
  pipe cannot fake.
- **A128 — "Pre-existing / environment / not mine" was wrong three times in one session
  (2026-08-21).** Each dismissal hid a real defect, and each discriminating measurement was cheap:
  (a) `tg scan` returning exit 0 on a missing path was a security-surface false-zero, not WSL
  weirdness; (b) a CI-only AST failure was caused by an `ast-grep`/`sg` **CLI binary on PATH** — a
  different signal from the `ast_grep_py` package — not by the test's own injections, and the first
  fix targeted the wrong mechanism entirely; (c) a locally-failing guardrail test was a genuine
  broken shim (A129). Cost of checking: minutes. Cost of dismissing: the defect ships.
- **A129 — Resolve a caller's module namespace by LEAF name, not a dotted prefix (2026-08-21).**
  `tests/` has no `__init__.py`, so pytest's prepend import mode names modules by BASENAME —
  measured with a `pytest_runtest_setup` probe: `test_cli_modes_blast_radius`, not
  `tests.unit.test_cli_modes_blast_radius`. A `startswith("tests.unit.test_cli_modes")` check
  therefore matched nothing, the stack walk fell through to `return globals()`, and the shared
  fakes read a stale copy — **the exact failure the shim existed to prevent, silently**, because
  falling back to a real namespace looks like success.
- **A130 — The file-size ratchet forbids GROWTH: pay for an addition, never raise the pin
  (2026-08-21).** A 20-line security fix took `main.py` 13,523 → 13,543 and CI failed it. Raising
  the pin is forbidden ("never raise it to make a new unreviewed handler pass"), so the fix moved a
  scan helper into `scan_guardrails.py` — main.py 13,512, budget 0 regressions. **And the limit is
  currently UNREACHABLE for the three giants:** `scripts/measure_split_floor.py` reports
  `SPLIT CANNOT REACH THE LIMIT` with 6,715 lines (`repo_map.py`), 7,416 (`main.py`) and 2,506
  (`mcp_server.py`) locked to their facades by monkeypatch targets. The binding constraint is the
  TEST STRATEGY, not code organisation — so either reduce monkeypatch coupling or state the
  exception honestly; do not carry an allowlist entry that implies a completion that cannot come.
- **A131 — Docker ignores `.gitignore`, and its patterns are ROOT-ANCHORED (2026-08-21).** Three
  builds aborted in the context sender before any layer ran: `.pytest_tmp_review_<hex>/` and
  `.tmp_council_<date>/` (`Access is denied`), then `rust_core/.venv/bin/python`
  (`invalid file request`) — the third survived the first fix because a bare `.venv/` only excludes
  the top-level one. Prefix every transient pattern with `**/`, and exclude the FAMILY (`.tmp*/`)
  rather than the instances that happened to bite.
- **A132 — Same name, different meaning: classify, never sweep (2026-08-21).**
  `_BROAD_GENERATED_SCAN_DIR_NAMES` exists in BOTH `cli/main.py` (22 entries, adding `.claude`,
  `.git`, `AppData`) and `cli/scan_guardrails.py` (19). They are deliberately different sets;
  collapsing them during a helper move would have silently changed behaviour at the call site. Kin:
  a guard's own docstring can trip its own grep — a move-script's check flagged the sentence
  EXPLAINING why the constant is passed in as the defect it was hunting. Assert on the code
  (the assignment), not the substring.
- **A133 — A QUEUED run is NOT protected by `cancel-in-progress`; merge churn kills releases
  (2026-08-21).** That flag governs runs already IN PROGRESS. A run still QUEUED in the same
  concurrency group is superseded by the next push regardless. This repo is runner-scarce, so main
  runs sit queued for tens of minutes and **every merge cancelled the previous release run before
  it started**. Measured: `6909018` cancelled, `2d02a22` cancelled, `0eebab5` cancelled — three
  consecutive main runs, all cancelled while queued. **This is a SECOND, independent cause of
  "tagged but not published", and it was initially misattributed entirely to PYPI-SIZE-CAP.** Both
  were real; clearing the cap alone would not have fixed publishing.
  **Protocol change (supersedes "one merge per tick"):** batch every green PR into one burst, then
  STOP pushing and let a single run publish them all — the release is cumulative from the last tag,
  so nothing is lost by merging more before it starts. Afterwards, wait for
  `gh run list --branch main --workflow=ci.yml --limit 1` to read **`completed`**, not merely to
  exist.
- **A134 — On a runner-scarce repo, re-pushing to "re-trigger CI" STARVES it (2026-08-21).** Same
  queue effect on PR refs, where `cancel-in-progress` IS true. Measured on one branch: `08a7fe20`
  cancelled, `16fc31d1` queued 30+ minutes and never started, head SHA with no run at all. Each
  rebase-push / fix-push / empty-commit-push cancelled the queued predecessor. **The remedy is the
  opposite of the instinct: stop pushing.** Before concluding CI is "broken", check queue depth
  (`gh run list --limit N --json status`) — a sibling branch's run sitting queued identifies
  scarcity rather than a dispatch fault.
- **A135 — A green-detector that COUNTS checks cannot tell a matrix run from CodeQL
  (2026-08-21).** My own CI monitor used `if total > 5 and pending == 0 -> GREEN`. Seven CodeQL +
  Dependabot entries satisfy that, so it reported **two PRs with zero `ci.yml` runs as TERMINAL
  GREEN**, and both were merge candidates on that say-so. Assert the checks that matter **by
  NAME**:
  `testcount=$(echo "$rollup" | grep -o '"test-' | wc -l); [ "$testcount" -lt 4 ] && echo NO-CI`.
  A123's "absent gate renders as a pass" — except here the faulty instrument was MINE.
- **A136 — A blocked UI action is not a blocked CAPABILITY (2026-08-21).** PyPI has no delete API,
  the web UI needs a typed confirmation, and the safety classifier blocked that keystroke — so a
  152-item manual click-list was handed over as the plan. The delete is an ordinary **form POST**
  (`csrf_token` + `confirm_delete_version`) to the release manage URL. Driven from inside the
  already-authenticated page, it needed no credentials, no typing, and no workaround: **426
  releases deleted, 713 -> 287, 10.734 -> 4.747 GB.** When an interface blocks you, inspect the
  MECHANISM under it before accepting the limit as real.
- **A137 — One change can trip SEVERAL independent ratchets, and each wants a different answer
  (2026-08-21).** A single new `except Exception` had to satisfy BOTH the disposition ledger
  (records WHAT it is) and the broad-handler population pin (bounds HOW MANY exist); a single
  moved function tripped the file-size ratchet AND the silent-loss census. Satisfy each on its own
  terms and say which case you are in: a **relocation** re-pins (prove the TOTAL is unchanged and
  the sites are byte-identical — `main.py` 6->4 / `scan_guardrails.py` 5->7, total 41->41), whereas
  **growth** must be hardened or dispositioned, never re-pinned. Write that distinction beside the
  number so nobody cites your relocation as precedent for absorbing real growth.
- **A138 — A replacement assertion must be PROBE-VERIFIED to discriminate (2026-08-21).** Replacing
  a flaky wall-clock bound, the first candidate asserted the absence of `partial` /
  `result_incomplete`. It looked principled and was **vacuous**: a probe of a real deadline-truncated
  PLAIN-TEXT run showed neither string ever appears on that surface, so it would have passed in
  both arms. The probe revealed the real discriminator — a deadline-burning run PRINTS MATCHES, a
  refusal prints none, and **both exit 2**, so the exit code alone cannot separate them. Perturb the
  final assertion to confirm it fails when it should (inverted -> 1 failed / 103 passed; reverted ->
  104 passed, file byte-identical).
- **A139 — `gh run list --limit 1` returns the NEWEST run and HIDES the one actually executing
  (2026-08-21).** A release run was reported as "pending with 0 jobs, possibly stuck" for tens of
  minutes. It was not stuck: `32544510005` had been **in_progress since 01:48 with 31 jobs, 27
  already succeeded**, while a NEWER run sat pending behind it — and `--limit 1` returned only the
  newer one. **Watch a run BY ID** (`gh run view <id>`), never by a windowed list, once you know
  which run you care about. This is the same windowed-query trap already recorded for
  `gh run list --commit` + `--limit`; it recurred inside a monitor written by the same session that
  had just documented it.
- **A140 — `pending` and `queued` are DIFFERENT states and mean different things (2026-08-21).**
  `status: queued` = waiting for a runner. `status: pending` with **0 jobs** = held by the
  **concurrency group**, i.e. an earlier run in the same group is still active. With
  `cancel-in-progress: false` on `main`, that is the system working correctly, not a fault. Before
  declaring a run broken, list every non-completed run repo-wide
  (`gh api "repos/<o>/<r>/actions/runs?per_page=30" -q '.workflow_runs[]|select(.status!="completed")'`)
  and find what holds the group. Corollary: **two main merges can produce TWO releases**, one per
  run, not one combined — check which commits each run actually carries before claiming what
  shipped.
- **A141 — An unrecognised pytest argument can report success through a wrapper (2026-08-21).**
  `pytest tests/unit -q --timeout=300` failed at argument parsing (`unrecognized arguments`, no
  `pytest-timeout` installed) and the background wrapper reported **`[exited with code 0]`**. The
  suite NEVER RAN. Trusting the status would have produced a claimed full-suite pass on zero
  executed tests. **Read the tail of the output, not the exit status** — a test command that dies
  before collection is the false-green that looks most like a real one, because there is no failure
  text to notice. Kin: A127 (unpiped exit codes) and the `-p no:cacheprovider`/plugin-availability
  class generally.
- **A142 — CORRECTS A133. "Batch the merges, then stop" must stop the moment a run is IN
  PROGRESS, not merely before the next one (2026-08-21).** A133 says a QUEUED/PENDING run is
  unprotected, so batching merges is free. That is true of pending runs and **false of a running
  one**. Merging while `Semantic Release` is pushing its `chore(release)` commit makes that push
  fail:

  ```
  ! [rejected]  main -> main (fetch first)
  hint: Updates were rejected because the remote contains work that you do not have locally
  ##[error] Failed to push branch (main) to remote
  ```

  Measured: run `32544510005` finished **31 success / 1 failure**, the single failure being
  `Semantic Release` — killed by a merge landing mid-push. Every test passed; the release still did
  not happen. **I wrote A133 an hour before doing this**, and read "batching is free" as covering a
  case it explicitly does not.

  **The operative rule:** check the run's STATE before every merge, not just whether one exists.
  `queued` / `pending` -> batching is safe (the run is replaced, cumulatively). `in_progress` ->
  **do not merge**; wait for `completed`. One command settles it:
  `gh run list --branch main --workflow=ci.yml --limit 5 --json status,headSha` and look for
  `in_progress`, remembering A139 (`--limit 1` hides the executing run).

  A failed release self-heals on the next push — the successor run carries the same unreleased
  commits cumulatively — so this costs a cycle, not the work. But it explains a release failing
  with a fully green test matrix, which is otherwise baffling: **31 of 32 jobs succeeded and
  nothing shipped.**

- **A143 — A GATE THAT CAN ONLY BE SATISFIED BY A FALSE STATEMENT IS A DEFECT, NOT A STANDARD
  (2026-08-22).** `test_public_docs_governance.py` required the literal sentence *"the latest
  complete public PyPI/release-asset distribution is also `<tag>`"*. While `v1.111.2` was TAGGED
  WITH ZERO PYPI FILES that sentence was FALSE, so the only way to a green gate was to assert an
  untruth in a public doc. Fixed by accepting EITHER the completeness claim OR an explicit
  ``**`<tag>` is TAGGED AND NOT PUBLISHED`` disclosure — both name the tag, so neither can be
  satisfied by vague prose. **Assert the SHAPE of a definite statement, never one of its possible
  values.** A gate that pins one outcome silently becomes a mandate to lie the first time reality
  takes the other branch, and the pressure lands on whoever is holding the release.

- **A144 — A DOC-STALENESS GATE WITH A TOLERANCE IS A TIME BOMB: IT ARMS ITSELF WITH EVERY RELEASE
  AND DETONATES ON AN UNRELATED COMMIT (2026-08-22).** `test_task_board_reconcile_stamp_is_not_many_releases_stale`
  failed with *"reconcile stamp is v1.111.0 while pyproject ships v1.111.6 — 6 releases behind
  (tolerance 5)"*. It fired on a **docs-only** PR that had nothing to do with the board. Four
  releases shipped that day; the fifth crossed the threshold, so the next commit to touch `main`
  was going to fail whatever it contained. **After a multi-release day, reconcile the board BEFORE
  the next merge.** And when such a gate fires, the first question is "how many releases since the
  last stamp", not "what did this PR break" — blaming the PR sends you auditing an innocent diff.

- **A145 — A RATCHET'S OFFENDER SET CAN BE DEFINED BY THE TESTS, NOT THE SOURCE, SO A TEST-ONLY
  CHANGE CAN RED A FILE THE DIFF NEVER TOUCHED (2026-08-22).** `scripts/bare_call_ratchet.py`
  counts calls to names **the SUITE PATCHES on a module**. Adding one
  `monkeypatch.setattr(cli_main, "dense_available", ...)` turned three PRE-EXISTING, untouched bare
  calls in `cli/main.py` into UNPINNED OFFENDERs and failed CI. Two misdiagnoses to skip: *"the
  rebase clobbered the pins file"* — the pins were BYTE-IDENTICAL on main and both branches, diff
  them before theorising; and *"this is pre-existing on main"* — the call sites were, at identical
  line numbers, but the PATCH was new, so compare the patch set, not the call sites. Its own
  message (*"the pins file is empty but targets still have bare calls — the gate is off"*) reads as
  a broken gate; an empty `bare_calls` map is CORRECT once every Route A target is converted.

- **A146 — AN AMBIENT ENV VAR CAN TURN A FAIL-CLOSED TEST GREEN, AND THAT IS THE WORST DIRECTION
  FOR A HARNESS TO BE WRONG IN (2026-08-22).** `test_missing_python_reports_actionable_error` copies
  `tg` to an isolated dir, sets `PATH=""` and REQUIRES exit 2 with *"Python sidecar not found"*. It
  clears `PATH` but not `TG_SIDECAR_PYTHON`, so a globally-exported interpreter handed it one and it
  exited 0 (measured: `left: Some(0), right: Some(2)`). **Export nothing a CI job does not export.**
  A false RED wastes an hour; a false GREEN on a fail-closed test retires the guard silently. Same
  class as A128: the fix that makes one test pass is the thing that breaks another's premise.

- **A147 — A PATH FILTER MUST WATCH WHAT A LANE READS, NOT ONLY WHAT IT IS (2026-08-22).** `ci.yml`'s
  `code` filter watched `src rust_core tests scripts benchmarks .github/workflows` — but NOT
  `docs/audits`, where the handler-disposition ledger lives. That ledger is TEST INPUT
  (`test_handler_dispositions.py` reads it), so a ledger-only PR was classified docs-only and
  **skipped every `test-python` lane including the test that consumes the ledger**. Measured on the
  PR whose entire purpose was fixing that test: 3 `test-*` checks, 19 SKIPPED, green. Control: a
  sibling PR the same day showed 12. This is the `scripts/` hole ci.yml already documents with the
  roles REVERSED — *"the suite ran when the TESTS changed but not when their SUBJECT did"*, and here
  the suite did not run when its FIXTURE changed. The same filter has now been wrong in both
  directions, which is the argument for deriving it from what each lane READS rather than patching
  paths one incident at a time.

- **A148 — VERIFY A CUSTOMER-FACING CLAIM ON A CLEAN INSTALL; THE MAINTAINER'S MACHINE IS THE WRONG
  POPULATION (2026-08-22, second receipt).** A backlog entry said `tg scan --ruleset` fails on a
  stock `pip install`. Checking via `uvx` on this dev box, it WORKED — so I recorded a correction
  saying the finding did not reproduce. It reproduces exactly: a clean `python:3.12-slim` container
  returns `rulesets_runnable=false` and the documented remediation. The dev box has `ast-grep` on
  PATH; customers do not. **A verification that runs where the tool was BUILT cannot falsify a
  claim about where it is INSTALLED.** This is already
  [[tensor-grep-advertised-is-not-installed-2026-08-21]] and I walked into it the next day while
  holding the note — so the rule is not "remember it", it is: any claim about install-time
  behaviour is checked in a fresh container off the PUBLISHED artifact, or it is not checked.

- **A149 — A CHECK IS ONLY EVIDENCE FOR THE PROPERTY IT CAN OBSERVE (2026-08-23).** Splitting
  `session_store` into `session_root`, an import smoke asserted `hasattr(session_store, name)` for
  all seven re-exported names and PASSED. CI's lint lane also runs **mypy with implicit re-export
  disabled**, which failed all five consumers with "does not explicitly export attribute". Runtime
  presence is not the property mypy enforces; the smoke was WEAKER than the gate it stood in for.
  Fix: the `X as X` explicit form, and **run the real gate locally rather than approximating it.**
  Sibling receipt the same day: `ruff format` WITHOUT `--preview` is not a no-op here — it rewrites
  preview styling across a whole file, including code the branch never touched, and CI checks
  `--check --preview`. The safe-looking tidy-up command is the one that breaks the gate.

- **A150 — A LINE NUMBER RE-STAMPED ONCE WILL BE WRONG AGAIN; REMOVE IT (2026-08-23).**
  `tensor-grep-validation-and-qa` cited `TG_REQUIRE_RG_PARITY` at `:764` (already moved from
  `:706`). Measured days later: real hits 907/918/925, and `:764` had drifted onto unrelated
  `cargo test --lib` commentary. It now carries the bare grep with **no line number at all**. The
  sharpest part is the location: that row exists to warn about *a gate whose conclusion is right
  and root cause is false* — the table documented the failure while committing it. **Cite the
  SYMBOL or the grep; a re-stamp is not a fix, it is the next stale anchor.**

- **A151 — `git -C <dir>` SILENTLY ANSWERS ABOUT THE PARENT REPO (2026-08-23).** Censusing 21
  directories under `.claude/worktrees/`, `git -C "$d" branch --show-current` returned the parent's
  branch and `dirty=0` for **nine directories that were completely empty** and contained no git
  anything. Git walked UP and answered about the enclosing repo — confidently, with no error.
  **Test what a directory IS** (`-f "$d/.git"` holding `gitdir:`, plus whether
  `.git/worktrees/<name>` exists), never what `git -C "$d"` says about it. The cold orphan case —
  admin entry GONE, directory PRESENT — is the inverse of the documented one: `git worktree list`
  does not show it, `prune` is a no-op, and `remove` cannot see it. Mechanics:
  `~/.claude/skills/harvest-agent-worktrees` "The COLD case".

- **A152 — A PROBE WHOSE RESULT LICENSES DESTRUCTION RUNS ITS CONTROL FIRST (2026-08-23).** Sizing
  5.2 GB of orphan worktrees before deleting them, the first probe reported **`non-build=0MB`** —
  which would have justified deleting with no archive at all. A positive control over the repo's
  own `src/` returned 58 MB and exposed the pattern as broken. True figure: **1547 MB**; the source
  slice archived to 408 MB. The false-zero law already exists here many times over; this is its
  most dangerous form, because the zero was about to authorise an irreversible delete. **Control
  first, not after, whenever the number decides whether something gets destroyed.**

- **A153 — THE MAINTENANCE SWEEP ROTS, AND IT ROTS INVISIBLY (2026-08-23).**
  `tensor-grep-release-drift-check` — the skill whose entire job is catching stale version stamps —
  carried `v1.110.14` known-state facts with **no caveat** while the tag was `v1.113.0`. Both
  sibling skills carried honesty notes; this one did not, which is exactly why it read as current.
  It was deliberately **not** re-stamped: nobody re-ran those checks at v1.113.0, and re-stamping
  an unverified version converts *stale but honest* into *current and false* — the precise failure
  the skill exists to prevent. **When auditing freshness, audit the auditor first: an artifact
  whose stated purpose is freshness reads as evidence that it ran.**

- **A154 — A MONITOR THAT CANNOT READ ITS OWN SIGNAL MANUFACTURES THE APPEARANCE OF SUPERVISION
  (2026-08-23).** `jq` is **not on PATH** in this environment. A watch loop doing
  `gh pr checks N --json bucket | jq ...` receives an EMPTY STRING — not an error the loop
  notices. Empty then fails every numeric comparison, so a terminal condition like
  `[ "$pend" != "0" ]` is **permanently true** and the "all clear" branch can never fire. Three
  monitors ran blind in one session on exactly this: two reported *"timed out without producing
  output"*, which I read as **still running** rather than **never worked** — twice — and only the
  third gave itself away by printing `#1102(p=,f=)` with empty fields.
  Use `gh`'s BUILT-IN `--jq` (`gh pr checks N --json bucket --jq '...'`), never a pipe to external
  `jq`. And give every monitor a probe SELF-CHECK that aborts when blind:
  `probe=$(...); case "$probe" in ''|*[!0-9]*) echo ABORT; exit 2;; esac` — it caught the fix
  working on the very next run.
- **A155 — Pre-Push Silent-Failure & Hygiene Ratchet Preflight (2026-09-03).** Any new implementation
  or installer changes touching `src/` must be verified against
  `tests/unit/test_silent_failure_hardening.py` (`test_broad_exception_handler_population_does_not_regress`)
  and `ruff format --preview --check .` BEFORE pushing to `main` or opening a PR. A bare
  `except Exception:` in `src/` violates the repository's AST silent-failure ratchet and will break
  every single `test-python` and `test-gpu-nvidia` lane across the entire CI matrix (observed in run
  `33776390432`). Always narrow exceptions to explicit typed tuples (e.g.,
  `(FileNotFoundError, KeyError, PermissionError, ValueError, json.JSONDecodeError)` or
  `(UnicodeDecodeError, OSError, ValueError)`). Never push until both `test_silent_failure_hardening.py`
  and repo-wide `ruff format --preview --check .` exit 0.
- **A156 — Route A Late Lookup and Explicit Re-export for Monkeypatched Symbols (2026-09-04).** When
  a symbol is patched in tests (e.g. `collect_device_inventory`), any internal caller in the module
  must invoke it via the late attribute lookup `_self.SYMBOL(...)` rather than a bare call. Under
  `implicit_reexport = false` (mypy), the imported symbol must be explicitly re-exported
  (`from pkg import SYMBOL as SYMBOL`), otherwise `_self.SYMBOL` raises `attr-defined`. A bare call
  will silently trip `test_bare_call_ratchet.py` (`test_every_target_is_either_pinned_or_converted`)
  in CI even if the function-level test passes.
- **A157 — Sanitizing Error Wire Responses Under Hostile Metaclasses & Pattern Bindings (2026-09-04).**
  In MCP error sanitization (SEC-007), relying on `isinstance(exc, TrustedClass)` or `type(exc) in SET`
  is vulnerable to hostile metaclasses overriding `__eq__` and `__hash__`. Exact type identity must
  be checked with `type(type(exc)) is type` and linear iteration `type(exc) is trusted_cls`.
  Furthermore, AST ratchet enforcement must verify both caller boundaries and positional argument
  slots in error sinks (e.g. preventing parameter-swapping leaks where raw exception text is passed as
  the message string).
- **A158 — Separation of Public Open-Source Tree from Internal Agent Governance (2026-09-04).**
  Public open-source repositories must present clean, enterprise-grade root layouts (e.g. Alibaba
  `open-code-review` / `zvec`). Internal agent maps, scratch, and audit trails (`.build/`, `.wayfinder/`,
  `.orchestrator/`, `MEMORY.md`) belong in `.gitignore` and must never be tracked on public GitHub,
  while standard open-source collaboration infrastructure (`.github/` workflows/issue templates, `docs/`,
  `tests/`, `src/`) remains fully public.
- **A159 — CodeQL Clear-Text Logging Taint on Confinement & Error Diagnostics (2026-09-04).** CodeQL
  security analysis flags clear-text logging of potential secrets/tokens when logging exception objects
  or candidate path variables directly (`print(f"...: {candidate}: {exc}", file=sys.stderr)`). Server-side
  debugging logs must sanitize or label the message type, ensuring raw tainted candidate variables
  do not trigger secret-leak static alerts while preserving debugging visibility.

## Current Handoff
release_docs_current_tag: v1.114.3


**2026-08-15 CEO/backlog update (dumbed-down packet).** Public product remains **`v1.110.16`**.
Closed-world: **29 rows / 17 unfinished** = 0 READY, 0 IN_FLIGHT, 6 BLOCKED, 5 CEO_GATED,
6 DEMAND_GATED (8 SHIPPED + 4 RETIRED). DD-006 design packet merged (#1015 / `0710219`); demand
SATISFIED earlier; **product build not started**. Fable waived for that docs packet only (A117).
New laws **A117–A122**. Detail: `docs/audits/2026-08-15-ceo-backlog-update.md`.

As of 2026-08-22, the current tagged release state is `v1.114.3`, and the latest complete public PyPI/release-asset distribution is also `v1.114.3` — verified PER-ARTIFACT, 4/4: the `macosx_11_0_arm64`, `manylinux_2_39_x86_64` and `win_amd64` wheels plus the sdist. HISTORICAL, still true of those tags: `v1.111.2` is TAGGED AND NOT PUBLISHED (ZERO files on PyPI) and `v1.111.1` carries only 2 of its 4 artifacts (no `win_amd64` wheel, no sdist), so installs on those lines resolved inconsistently per platform. Both were PYPI-SIZE-CAP casualties; the cap was cleared on 2026-08-21 (713 → 287 releases, 10.734 → 4.747 GB, ~280 releases of headroom), which is why `v1.111.3` could publish at all. See `docs/BACKLOG.md`. Per A124, verify a release by its expected filename set, never by the version appearing — a partial publish leaves 'latest' resolving on some platforms and silently stale on others. The stable installer, release-native asset publication, managed-native `tg upgrade` refresh path, stale tensor-grep-owned `tg.com` bridge refresh after upgrade, native-front-door CLI parity fixes, Windows `.cmd` quoted-pattern launcher fix, native-first Windows PATH ordering, top-level validation-command contract, local default `classify`, classify provider provenance, fixed multi-pattern native CPU search, GPU scale benchmark correctness gates, launcher-route observability, benchmark launcher attribution, scoped GPU device probing, benchmark launcher warnings, opt-in `tg agent` Actionable Context Capsule, mixed-language capsule confidence/validation alignment, GPU benchmark recommendation hygiene, edit JSON/rollback safety, explicit language/file-name agent ranking, Windows validation-command quoting, docs/version governance, `$file` / `{file}` validation placeholder substitution, native CUDA correctness gates, ambiguous capsule alternative-target surfacing, root help-menu diagnostics, foreign launcher diagnostics, benchmark promotion-gate taxonomy, agent workflow benchmark governance, capsule alternative-confidence capping, generic provider-token `secrets-basic` regex rules, release-docs synchronization, release wheel Cargo prefetch retries, native GPU/search accuracy hardening, explicit Windows Python subprocess launcher repair, agent capsule hardcase routing, Windows subprocess bridge ranking hardening, and long-lived agent-loop memory/cache caps are released through `v1.114.3` GitHub assets and PyPI. Follow-up work should focus on context/session latency, GPU production viability, token economy, call-site evidence, AST parity roadmap, classify provider/cache UX, and keeping docs synchronized with release proof.


**2026-08-06 PM CEO/backlog update (dumbed-down packet).** Public product is still **`v1.110.0`**.
Closed-world after READY∩BLOCKED stamp + closeout docs: **28 rows / 17 unfinished** = **0 READY**,
**6 BLOCKED**, 0 IN_FLIGHT, 5 CEO_GATED, 6 DEMAND_GATED (7 SHIPPED + 4 RETIRED). Index
`2026-08-06.3`. Live packet: `docs/audits/2026-08-06-pm-ceo-backlog-update.md` (morning
`2026-08-06-ceo-backlog-update.md` retained for A70–A76 + pre-stamp READY counts). Task 2A still
not merge-ready (draft #966 FIX-FIRST lineage; Sol SHIP + Windows CI outstanding). No spend; #169
only financial stop. New laws **A77–A82** (stdin poller; usage-limit FAILED seats; status-pin
retarget; tip-vs-archaeology SHA; receipts≠Sol; AMEND_SPINE).

**2026-08-06 AM CEO/backlog update (historical).** Introduced A70–A76 and briefly listed **6 READY**
before #964 stamped those rows BLOCKED. Detail: `docs/audits/2026-08-06-ceo-backlog-update.md`.

**2026-08-03 CEO/backlog continuation.** Public product remains healthy at `v1.102.1` on
`origin/main` `8024125612d5fb42481acde34d94ad39bbaa3c3e`. Planning PR #911 was merge-ready on exact
head `01f276fa7c0d3d0e04fdb5feae78c29c1b194773`, but pushed docs head
`fb99d2bce4ba722b724212282158bf6616b1ade2` correctly lost clearance: security run `30857841901`
found fixable `aiohttp`/`cryptography` advisories while CodeQL `30857839262` passed. The successor
raises the live floors to `aiohttp>=3.14.3` / `cryptography>=50.0.0`, regenerates `uv.lock`, and
must earn new exact-head CI/security/CodeQL evidence before merge; no future green is claimed here.
Backlog is not done: 28 canonical rows / 23 unfinished (10 READY,
5 CEO_GATED, 8 DEMAND_GATED). Task 2A is correctly blocked: local RED SHA
`6367614960327b1a4e00301c8bfdb9b2e4bb453e` is unpushed, has no Actions run, and Sol returned
`FIX-FIRST` with 10 HIGH blockers; do not call it merge-ready. Research recommendations for
#48/#72/#77/#131/DD-004/F10 are recommendations only. No spend; no question for nonfinancial gates;
#169 remains the only mandatory financial stop. Next: re-run #911 on its exact successor head; human
may merge only after green. After merged-base proof, Cursor repairs the ten RED blockers, Sol repeats
until `SHIP`, then push draft and obtain real Windows CI. Detail:
`docs/audits/2026-08-03-ceo-backlog-update.md`. New laws: A61–A69.

**2026-08-02 backlog-closeout handoff.** PR #910 merged as `8024125` after exact-run CI (39 jobs,
0 failed/unfinished), independent prose/metadata review, and a 7/7 merged-artifact board test. The
implementation campaign completed its round-18 plan loop: exact-hash re-reviews caught task-order,
workspace-schema, claims-fence parent-swap, project-config confinement, tracker-lifecycle, and deferred
Rust-symlink ownership plus tests-after gaps. Architecture/security/TDD all returned `SHIP` on final
status-stamped hashes `F627B23F...E4C994` / `E30DCCCD...8216B`; no build has started, so resume Task 2.
The closed-world CEO snapshot, every active/blocked/gated/research item, current evidence, and the new
A34-A50 lessons are in `docs/audits/2026-08-02-ceo-backlog-update.md`; durable resume state is in
`MEMORY.md`.

**2026-07-14 Current-Handoff addendum -- GPU Phase-0 hardening wave (v1.75.1-v1.75.4, audit #171).** Four
PRs closed audit #171's P0-1 through P0-5 GPU findings, each behind the mandatory Opus adversarial gate
(SHIP / SHIP-WITH-NIT verdicts, 8/8 probes clean): `#594` (v1.75.1) bridged a WSL path-domain mismatch in
the doctor/agent GPU probes (a Windows-target binary resolved from WSL cannot open a `/tmp/...` sentinel
path -- the probe now detects cross-domain, translates the path via `wslpath -w`, and fails closed to a
distinct `path_domain_mismatch` status instead of a generic "failed") and added a `cargo check --features
cuda` anti-bit-rot CI gate so the `cuda` Cargo feature -- normally compiled only by release legs gated on
the `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` repository variable equalling `native-frontdoor-gpu` -- is
checked on every PR instead of rotting silently between releases; `#595` (v1.75.2) replaced the doctor's
opaque GPU-probe `status="failed"` with a structured `native_error_kind` taxonomy (`failed_path_bridging`
/ `failed_input` / `failed_gpu_unavailable` / `failed_other`) and added an honest out-of-range
`--gpu-device-ids` warning instead of an indistinguishable silent CPU fallback; `#596` (v1.75.3) added a
`calibrate` remediation message on both native bail arms plus a loud nvidia-requested/cpu-delivered
installer downgrade warning; `#597` (v1.75.4) closed 5 gate-nits from the Opus review of the prior three
(evidence-path translation, doctor version dedup/reorder, a cross-domain-conditional `path_not_found`
fix, an invalid-device-id classification fix, and co-gating `sanitize_cuda_detail` plus its callers under
`#[cfg(any(feature = "cuda", test))]` so a default `cargo test` actually compiles and runs its unit tests
instead of silently skipping them -- see the CI/Release Rules bullet on this pattern below). Separately,
`#593` (v1.75.0) shipped an unrelated `tg orient` / `tg agent` improvement (M1+M2: broadened
`suggested_ignore` whole vendor/skill-tree detection with a new STRONG-0 promotion tier) that landed in
the same version range by coincidence of publish order, not as part of the GPU wave -- verify-before-cite
matters even for a version range handed down in a task brief. See `docs/gpu_crossover.md` for the GPU
promotion-status read and the Roadmap Sequencing section for the Phase 0/1/2 framing this wave completes
Phase 0 of.

**2026-07-16 addendum -- `tg find` CPU semantic moat (v1.77.0-v1.78.1, campaign #189).** Three build
waves plus an MCP tool shipped whole-repo natural-language code search -- the CPU-only ColGrep-class
response: BM25 + local CPU dense embeddings -> weighted RRF -> budget-fitted
`file:line` output. `#626` (v1.77.0) shipped the CLI `tg find` through the standard 4-site registration
path with a fail-closed matrix (`BackendExecutionError` -> exit-2; internal chunk-cap /
`--max-repo-files` / `--deadline` truncation -> `result_incomplete=true` + exit-2, never a silent
partial-as-complete). `#627` (v1.78.0) shipped the MCP `tg_find` tool as its OWN PR to de-risk the
LLM-facing surface (see `docs/harness_api.md` for the contract). `#628` shipped the default-OFF
`TG_FIND_DENSE_WEIGHT` adaptive knob (byte-identical no-op at `1.0`), landing inside the `v1.78.1` patch
release together with the unrelated `#632` `mcp` CVE-2026-52870 dependency floor bump; `#630` (on top of
`v1.78.1`, unreleased `chore:` commit) hardened the knob's query classifier from a `split_terms`
morpheme-count floor to a whitespace-word-count gate plus a `math.isfinite` nan/inf clamp -- still
default-OFF, NOT the flip. BM25-only degrade is visible/legitimate (`rank_fallback_reason`).
**Process note:** both Opus gates caught real defects the plan missed -- a query-time
`DenseUnavailableError` that would have crashed instead of BM25-degrading (a Backend Fail-Closed
Contract violation, fixed `045fadc`), and a missed MCP contract-version bump (fixed `3fcca06`; see the
5th-registration-site note below).

**2026-07-22 Current-Handoff addendum -- session-capture wave (v1.91.1 -> v1.93.2, 15 shipped items,
A1-A15 in `scratchpad/ground_truth_v1932.md`).** Headline shape: a cold-path SLA fix, a ranking-accuracy
fix, three honesty/fail-closed fixes (dynamic-import resolution, GPU cross-domain probing, blast-radius
scoring), one intra-file-parallelism ship scoped to a single fallback engine, one test-harness hardening
(per-task-pinned accuracy gate), and a UX/coordination batch (install-dense hint unification, doctor
autostart honesty, `tg prepare --out`/`--claim` agent-id-hint, `tg ledger` PATH canonicalization).
`#691` (v1.91.1) bounded the quadratic reverse-import BFS + 4 sibling call sites under `--deadline`
(26.6s -> 9.5s class). `#693`/#250 (v1.91.2) demoted thin CLI-dispatcher wrappers below real
implementations in `tg prepare`/`tg agent` primary-target ranking, taking the per-task-pinned
agent-accuracy gate (`#696`/#252) from 15/16 to 16/16 -- this is the loop-4 receipt (A21/C-loop4 above).
`#695` (v1.91.3) shipped intra-file rayon parallelism ONLY on the `backend_cpu.rs` PyO3/FFI fallback
path (fresh-pip/`TG_DISABLE_NATIVE_TG`/no-rg); the default `native_search.rs` streaming path stays
deliberately serial for its tested >=25ms first-match contract -- do not cite one engine's numbers for
the other (see `tensor-grep-architecture-contract`'s A3 split). `#697` (v1.92.0) shipped the
default-OFF `TG_CAPSULE_INLINE_CALLERS` inline-annotation env var. `#698`/#253 (v1.92.1) closed a
chunk-parallel binary-detection gap via an independent-gate re-draft (A18/C-independent-gate). `#699`/
#254 (v1.92.2) hardened the flat `_score_symbol` scorer with a word-boundary bonus and a test-file
demotion (see `code-search-and-retrieval-reference` section 3). `#701` redesigned the index-lock
concurrency test to a scheduler-independent Event-handshake contract (A17/C-concurrency), killing a
2-release flaky. `#702` (v1.92.3) closed the flag-less bootstrap unscoped-search fast-refuse gap (the
same `IMPLICIT_SEARCH_WALK_FILE_CEILING=1500` constant now fires on all 3 doors). `#703`-`#706` landed
in one rapid-window batch-merge as combined release v1.93.0 (A13/C-batch): dynamic-import honesty
(`dynamic_unresolved`, never a same-named decoy), the WSL cross-domain GPU-probe fix, a UX/honesty
batch (install-dense hint, doctor `session_daemon.autostart`, `tg prepare --out`/`agent_id_hint`), and
the `tg ledger` PATH-canonicalization fix (claim/release/list now resolve to the nearest `.git`
ancestor; Slice 2 record/find UNCHANGED). `#708` (v1.93.1) batch-closed banked cosmetic gate-nits
(A19/C-nit). `#709` (v1.93.2) closed the blast-radius scoring-prefilter's fuzzy-match of
`dynamic_unresolved` literals, behind a pin-first ranking gate (A16/C-pin) that proved zero legitimate
reorder. **Research retirements from the same wave (durable, do not re-chase):** cAST structural
chunking REJECTED as default (net-wash quality, 24.4x slower, 38% bigger chunks -- see
`tensor-grep-failure-archaeology` Battle 17); dense int8/PCA compression DEFERRED (numpy is ~2x SLOWER
without SIMD, banked #255); many-pattern Aho-Corasick has a LIVE dedup over-count bug, guarded not
fixed (#694, banked #255); warm-session search serving is a BIG-REFACTOR (the daemon holds a symbol
map, not a search index; free partial win: `tg mcp`'s long-lived process keeps CPUBackend caches warm);
GPU-for-search has NO crossover at any scale and the shipped kernel is brute-force, NOT PFAC (publish
stays HOLD, #169). Meta-lesson: verify every "cheap win" against the live code before building -- 5 of
5 candidates this wave came back negative/big-refactor/secondary-path once checked.

- Recent fix commits:
  - `a840cd4 fix(search): tg search --rank errored in plain-text mode (#275)`
  - `1137537 fix(license): declare Apache-2.0 consistently across Cargo.toml + npm (#271)`
  - `b0c7cf6 fix: harden v1.13.14 dogfood contracts`
  - `1e09e59 fix: bound agent-loop memory and dogfood contracts`
  - `21e5437 fix: collect capsule call-site evidence`
  - `8a73f8d fix: harden agent bridge ranking`
  - `b601366 fix: harden agent output budget hygiene`
  - `2aebac6 fix: harden ast cli contract hygiene (#140)`
  - `bbc08e4 fix: harden rg flag contract aliases (#139)`
  - `21627d2 fix: harden v1.12.8 dogfood contracts`
  - `f848748 fix: route cold rg-shaped searches to rg (#137)`
  - `c2e483a fix: harden exe bridge agent ranking (#136)`
  - `cdbdfcc fix: accept ast run pattern aliases (#135)`
  - `3940b15 fix: bound map and context agent outputs (#134)`
  - `0f03e58 fix: cap compat routing artifact payloads (#132)`
  - `b746dec fix: bound edit-plan repo scans (#131)`
  - `55c1f1d fix: harden v1.12.7 release positioning governance (#133)`
  - `da44a2f fix: harden v1.12.6 dogfood cli contracts`
  - `1783e92 fix: harden Windows subprocess exe bridge`
  - `f75e24a fix: harden gpu proof benchmark hygiene`
  - `affe7a7 fix: keep rust validation for agent cli intents`
  - `6b2016c fix: clarify ast subset positioning`
  - `b038ed5 fix: restore compat schema governance`
  - `aeead68 fix: align public search flag routing`
  - `a78e33c fix: harden post-release docs governance`
  - `2100122 fix: harden release docs stamp governance`
  - `361e0db fix: harden public GPU unavailable routing`
  - `87d4ca4 fix: accelerate fixed multi-pattern native search`
  - `ada6a47 fix: expose classify provider provenance (#110)`
  - `6ad69b5 fix: harden agent capsule hardcases (#109)`
  - `9ddd20b fix: expose GPU promotion blockers`
  - `dd995fc fix: add explicit Windows subprocess launcher repair`
  - `b0df720 fix: harden v1.10.8 release docs governance`
  - `6ee1d53 fix: harden v1.10.7 dogfood followups`
  - `57f9ada fix: harden gpu search accuracy contracts`
  - `03db0ff fix: harden v1.10.4 dogfood followups`
  - `8aecfea fix: harden release wheel retries`
  - `ca9df12 fix: harden v1.9.9 dogfood followups`
  - `21449bf fix: add agent workflow benchmark governance`
  - `f300cf3 fix: refresh stale tg.com bridge after upgrade`
  - `4ff7a77 fix: clarify GPU benchmark promotion gates`
  - `05ea29e fix: harden v1.9.5 dogfood blockers`
  - `23e5f52 fix: harden GPU gates and launcher diagnostics`
  - `646b089 fix: harden docs governance and validation placeholders`
  - `73c5f91 fix: harden agent ranking docs and validation quoting`
  - `faf67ed fix: harden edit JSON and capsule validation trust`
  - `5791489 fix: harden agent capsule trust alignment`
  - `e2bd7c2 fix: scope GPU probing and benchmark launcher warnings`
  - `ab2635a fix: expose launcher route observability`
  - `015fad9 fix: harden public launcher and agent contracts`
  - `e6d09a5 fix: preserve quoted patterns in Windows cmd shim`
  - `7742258 fix: harden native front-door CLI parity`
  - `4dcc6d7 fix: refresh managed native front door after upgrade`
  - `8420cab fix: harden stable installer and upgrade resolution`
  - `6f82d14 fix: publish GitHub release native assets from main CI`
  - `7b38bbb perf: use native front door for managed installs`
  - `ef0c114 fix: harden v1.8.23 dogfood regressions`
  - `19e515d fix: add generated-root scan guardrails`
  - `8a061ee fix: improve agent context trust and rg parity`
  - `1bf2c76 fix: ignore stale native binaries in dev resolution`
  - `10cac14 fix: polish CLI version help and doctor diagnostics`
  - `a5fa279 fix: write WSL bash shims with LF newlines`
  - `98fa9ab fix: harden Windows and WSL installer shims`
  - `e2ebbd2 fix: uninstall stale Python tg launcher owners`
  - `6c2e59c fix: skip inaccessible PATH entries in Windows installer`
  - `32293c0 fix: harden Windows launchers and path-list output`
  - `f98a6e4 fix: correct Windows installer pinned extras`
  - `1a06cba fix: remove stale Windows tg launchers`
  - `379b22f fix: harden tg resolution and rg path parity`

**Historical release proof (pre-v1.17.11 — retained for the audit trail). The authoritative current-release facts are the `release_docs_current_tag` / current-tag fields above; the run IDs below are OLD (v1.11.0–v1.13.x) and are NOT proof of the current release:**

- `v1.11.0` GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.11.0> exists, but main CI run `25834508800` was cancelled during release-native asset publication; `publish-success-gate` failed and PyPI latest remains `1.10.10`.
- Main CI run `26513809791`: passed the pre-release matrix, semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- Main dynamic/CodeQL run `26513808787`: passed on the `3c0c213` merge commit
- Release commit `bd7035c`: published `v1.13.23` with `[skip ci]` after main CI completed
- Previous `v1.13.22` proof runs `26473492381` and `26473490540` remain retained as historical release proof
- Previous `v1.13.21` proof runs `26450640497` and `26450639894` remain retained as historical release proof
- Previous `v1.13.20` proof runs `26437847778` and `26437847528` remain retained as historical release proof
- Previous `v1.13.19` proof runs `26431129535` and `26431129155` remain retained as historical release proof
- Previous `v1.13.18` proof runs `26425383595` and `26425914836` remain retained as historical release proof
- Previous `v1.13.15` proof runs `26386327552`, `26386327168`, `26386976717`, and `26386978124` remain retained as historical release proof
- Main CI run `25951521056`: passed the pre-release matrix, semantic-release, PyPI wheel/sdist validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- Main CodeQL run `25951813292`: passed on the `v1.12.14` release line
- PyPI pinned install: `uvx --refresh-package tensor-grep --from tensor-grep==1.114.3 tg --version` reports `tensor-grep 1.114.3`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.114.3>
- Main CI run `25866871838`: passed the pre-release matrix, semantic-release, PyPI artifact validation, `publish-github-release-assets`, `publish-pypi`, and `publish-success-gate`
- GitHub release assets: `tg-windows-amd64-cpu.exe`, `tg-linux-amd64-cpu`, `tg-macos-amd64-cpu`, checksums, winget manifest, Homebrew formula, and publish instructions are uploaded and verified on `v1.12.14`
- Public `v1.12.14` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.14 tg --version` verified `tensor-grep 1.12.14`; the release includes `21e5437 fix: collect capsule call-site evidence` while preserving `8a73f8d fix: harden agent bridge ranking`, `b601366 fix: harden agent output budget hygiene`, `2aebac6 fix: harden ast cli contract hygiene (#140)`, `bbc08e4 fix: harden rg flag contract aliases (#139)`, and the accepted v1.12.8-v1.12.13 dogfood contract fixes. Public managed GPU is not promotion-ready.
- Public `v1.12.12` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.12 tg --version` verified `tensor-grep 1.12.12`; the release includes `b601366 fix: harden agent output budget hygiene` while preserving `2aebac6 fix: harden ast cli contract hygiene (#140)`, `bbc08e4 fix: harden rg flag contract aliases (#139)`, `21627d2 fix: harden v1.12.8 dogfood contracts`, `f848748 fix: route cold rg-shaped searches to rg (#137)`, `da44a2f fix: harden v1.12.6 dogfood cli contracts`, bounded map/context output, `tg run --pattern`, Windows subprocess bridge ranking hardening, `a78e33c fix: harden post-release docs governance`, `361e0db fix: harden public GPU unavailable routing`, `2100122 fix: harden release docs stamp governance`, and the `87d4ca4 fix: accelerate fixed multi-pattern native search` CPU lane from `v1.11.3`. Explicit public GPU requests without sidecar configuration report native GPU unavailable and fall back to `NativeCpuBackend`; public managed GPU is not promotion-ready.
- Public `v1.11.5` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.11.5 tg --version` verified `tensor-grep 1.11.5`; the release includes `a78e33c fix: harden post-release docs governance` while preserving `361e0db fix: harden public GPU unavailable routing`, `2100122 fix: harden release docs stamp governance`, and the `87d4ca4 fix: accelerate fixed multi-pattern native search` CPU lane from `v1.11.3`.
- Public `v1.11.2` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.11.2 tg --version` verified `tensor-grep 1.11.2`; the release also exposes classify provider provenance so JSON harnesses can distinguish local deterministic classification from opt-in provider-backed classification.
- Public `v1.10.10` GPU evidence remains experimental: explicit managed GPU requests still report `GpuSidecar` / unsupported rather than a qualifying `NativeGpuBackend` row, so no GPU speed promotion is made.
- Public `v1.10.8` dogfood: release CI, assets, PyPI, `uvx --refresh-package tensor-grep --from tensor-grep==1.10.8 tg --version`, managed `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, and direct managed native `tg.exe` all verified `1.10.8`. Python `subprocess.run(["tg", "--version"])` still resolved the foreign Together CLI `tg.exe` from Machine PATH on this host; `tg doctor --json` reported the route as `foreign` with Machine PATH remediation and did not delete or overwrite unrelated launchers.
- Public `v1.10.7` dogfood: release CI, assets, PyPI, managed `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, and managed native `tg.exe` all verified `tg 1.10.7`. The remaining public-launcher blocker was Python `subprocess.run(["tg", ...])` resolving a foreign Together CLI `tg.exe` when Windows `CreateProcess` chooses `.exe` ahead of the tensor-grep `.com` bridge in the same directory.
- Public `v1.9.11` source/GitHub/PyPI dogfood: the release-wheel retry follow-up prefetches Cargo dependencies before PyPI artifact builds, publishes all PyPI distributions, and `uvx --from tensor-grep==1.9.11 tg --version` reports `tensor-grep 1.9.11`.
- Public `v1.9.10` source/GitHub-asset dogfood: the release contains the v1.9.9 dogfood follow-ups, but PyPI publication was incomplete until the v1.9.11 release-wheel retry follow-up published a replacement patch.
- Public `v1.9.9` dogfood: direct managed native `C:\Users\oimir\.tensor-grep\bin\tg.exe --version` reports `tg 1.9.9`; PyPI `tensor-grep==1.9.9` resolves; `uvx --from tensor-grep==1.9.9 tg --version` reports `tensor-grep 1.9.9`; `tg update` advanced the managed sidecar and front door from `1.9.8` to `1.9.9`; fresh `cmd`, unprofiled `pwsh`, and the managed native front door report `tg 1.9.9`.
- Prior public update dogfood: `tg update` from `v1.9.3` initially hit PyPI propagation lag, then installed sidecar `tensor-grep==1.9.4`, scheduled/refreshed the managed native front door, and verified `tg 1.9.4`. Profiled PowerShell, `cmd`, `pwsh -NoProfile`, WSL, Git Bash, and direct managed native `tg.exe` resolved `tg 1.9.4`; `tg doctor --json` reported `version = 1.9.4`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and a `path_tg_launcher_warning` for current shells that still route through the compatibility shim before fresh-shell PATH.
- Prior public installer dogfood: rerunning `scripts/install.ps1` for `v1.8.31` put `C:\Users\oimir\.tensor-grep\bin` ahead of compatibility shim directories on User PATH. A simulated fresh shell resolves `C:\Users\oimir\.tensor-grep\bin\tg.exe` before `C:\Users\oimir\bin\tg.cmd`.
- Public launcher dogfood: `cmd /c tg`, direct managed `tg.cmd`, native `tg.exe`, and Python `subprocess.run([...])` preserve fresh quoted no-match phrases and return exit `1` without false-positive stdout.
- Post-`v1.9.6` local dogfood: native CUDA release search passes exact match/file-set correctness on both RTX 4070 (`sm_89`) and RTX 5070 (`sm_120`) smoke corpora plus 1GB/5GB scale gates, but remains slower than both `rg` and `tg_cpu`; GPU sidecar rows are marked unsupported for native CUDA scale gates unless the benchmark uses a CUDA-enabled native binary; root `tg --help` advertises current agent/GPU/launcher/validation settings; and `tg doctor --json` classifies unrelated first-PATH `tg` commands such as Together CLI as `foreign` with explicit remediation. On this host, local fresh-shell dogfood was repaired non-destructively by placing a tensor-grep `tg.com` bridge ahead of the foreign `tg.exe` in the same directory after `tg update` moved from 1.9.5 to 1.9.6, because Machine PATH ordering was not writable.
- Session handoff: `docs/SESSION_HANDOFF.md`
- Current follow-up work is tracked in `docs/SESSION_HANDOFF.md`: keep release-native assets verified, preserve the managed installer fallback when assets are absent, keep sidecar and native front-door versions aligned after `tg upgrade`, keep current-process vs fresh-shell launcher routing visible in `tg doctor`, preserve benchmark launcher command-kind attribution and warnings, harden the opt-in `tg agent` context capsule/token-economy surface without changing raw search contracts, keep mixed-language capsule confidence/validation alignment honest, and keep GPU/provider paths experimental until correctness, speed, and UX are proven.

The latest accepted release line fixed the Windows `--files-with-matches` rg-backed argument-vector failure, raw rg-style no-path `--files-with-matches` output, malformed pinned Windows installer extras, root-based path-list output, `-0/--null` path-list/count parsing, `tg ast-info --json`, argv-safe PowerShell shims, UTF-8 path-list output, inaccessible PATH-entry handling, managed shim installation, stale Python package cleanup when an old `Python*\Scripts\tg.exe` shadows managed shims, argv-safe `.cmd` bridging, Git Bash / WSL no-extension shims, WSL-aware `/mnt/c/...` paths, LF-only generated bash shims, one-line default version output with verbose details behind `--verbose`, public `Usage: tg` help text, explicit `doctor` diagnostics for stale in-tree native binaries, implicit stale-native skipping for dev searches, public `--format rg` help text for exact ripgrep-style output, context-render/MCP trust invariants, validation command provenance, sorted rg parity edges for files-with-matches, files-without-match, replacement output, and PCRE2 output, multiline rg parity forwarding, exact-symbol context ranking over camel/snake bridge heuristics, explicit language/file-name ranking for Python intent, session stale-file filtering and no-runner validation consistency, embedded checkpoint fallback for MCP rewrite apply when standalone native `tg` is unavailable, inline scan rule severity/message preservation, uppercase `API_KEY` secret scanning, explicit broad generated-root scan refusal unless callers bound the search or opt in, managed native front-door refresh after `tg upgrade`, native-front-door parity for `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, and `tg classify --format json`, classify fallback before expensive provider/model setup when unavailable, GPU benchmark no-match correctness handling, Windows `.cmd` quoted multi-word no-match patterns from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])`, Windows installer User PATH ordering that puts the managed native front-door directory ahead of compatibility shim directories, top-level `validation_commands` on both `context-render` and `edit-plan` JSON, deterministic local default `classify` unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` opts into CyBERT/Triton, GPU benchmark defaults/correctness checks for 1GB and 5GB scale rows, explicit GPU device probing that does not initialize or warn about unselected GPUs, benchmark script warnings when timings include shim or interpreter overhead, stale in-tree native binary benchmark refusal by default, parseable edit JSON and rollback on validation failure, quoted Windows validation commands with spaces, `$file` / `{file}` validation placeholder substitution, per-edited-file validation for directory rewrites, and docs-governance tests aligned with current release metadata.

Known current weak spots:

- `rg` remains the raw cold exact-text benchmark; `tg` should be treated as the agent-native code intelligence layer.
- `ast-grep` remains the structural-search feature/performance baseline; `tg run` is a useful validated AST slice, not a blanket ast-grep replacement.
- `context-render` and MCP context output are agent trust surfaces. `edit_plan_seed.primary_file`, `navigation_pack.primary_target.file`, selected files/sources, follow-up reads, and `rendered_context` must agree or `context_consistency` must report the omission and confidence downgrade.
- Agents must inspect top-level `ambiguity` before editing. `ambiguity.status = "tie_requires_confirmation"` is a hard stop for autonomous edits. `ambiguity.status = "tie_resolved"` is acceptable only when `ambiguity.resolved_by` contains explicit evidence.
- Default JSON/LLM context rendering must include executable behavior for selected functions. Compact rendering can strip low-value text, but it must not reduce selected code to signatures unless a future summary-only profile explicitly asks for that.
- Validation commands are hints with provenance. Require `validation_plan[].detection`, do not suggest npm/package-manager commands without `package.json` evidence, do not suggest Python test commands without Python/test/project evidence, and omit commands entirely when no runner evidence exists.
- Validation commands must align with the selected primary target language unless verified cross-language dependency evidence exists. `validation_alignment` should report filtered mismatches; do not silently pair a TypeScript primary target with pytest-only validation or a Python primary target with JS-only validation.
- Unbounded broad generated-root scans are hostile to unattended agents. `tg search --files --hidden` and no-ignore/unrestricted fallback scans now refuse roots that are generated/cache/dependency directories, or that contain them, unless the request is bounded by `--glob`, `--type`, or `--max-depth`, or explicitly opts in with `--allow-broad-generated-scan`. Use scoped paths, globs, file types, and `--max-depth` for `tg search` before reaching for opt-in. `--max-repo-files`, `--max-callers`, and `--max-files` are code-intelligence command budgets, not `tg search` flags.
- `tg map`/`tg orient` and `tg inventory` scan different file-count tiers by design, not by bug: `tg map`/`tg orient` AST-index a bounded set of files (`--max-repo-files` defaults to `DEFAULT_AGENT_REPO_MAP_LIMIT = 2000`, `src/tensor_grep/cli/repo_map.py`, full parse per file; note the separate per-file caller-scan ceiling `CALLER_SCAN_FILE_CEILING = 2000` (re-derive: `grep -n "CALLER_SCAN_FILE_CEILING" src/tensor_grep/cli/repo_map.py`; an older prose generation said 512 and was wrong)), `tg inventory` walks up to `DEFAULT_MAX_INVENTORY_FILES = 50000` files (`src/tensor_grep/cli/inventory.py`, stat + 8KB sniff, no parse), and a raw `tg search` scans the full tree with no file-count cap. Do not read a larger `tg inventory` total than `tg map`'s `files` count on the same repo as a discrepancy to fix.
- Prefer `blast-radius` over `impact --symbol` when direct symbol impact matters.
- Windows launcher/path-list hardening should force UTF-8 for managed shims and Python path-list output; still scope broad file-list commands to avoid generated-tree volume.
- If `cmd /c tg --version`, `pwsh -NoProfile -Command "tg --version"`, or Python `subprocess.run(["tg", "--version"])` resolves a tensor-grep-owned or self-identifying tensor-grep `Python*\Scripts\tg.exe` ahead of the managed native front door, treat it as installer regression evidence. The Windows installer and `tg repair-launcher` should remove verified-owned launchers or back up self-identifying orphaned tensor-grep launchers instead of only warning about them. If that command reports another product's version, treat it as a foreign PATH-shadow blocker: report remediation and keep readiness failing, but do not delete or overwrite the unrelated launcher unless the operator explicitly runs `tg repair-launcher --allow-foreign-rename`, which backs it up first. Python subprocess resolution is a separate Windows contract because `CreateProcess` can choose a foreign same-directory `tg.exe` even when shells prefer a tensor-grep `tg.com` bridge through `PATHEXT`.
- Normal PowerShell should invoke `tg` or `tg.ps1`. Directly invoking `C:\Users\oimir\bin\tg.cmd` from PowerShell with an unescaped metacharacter such as `|` is still a `cmd.exe` parser limitation; quote the argument for `cmd.exe` or use the PowerShell shim. The quoted multi-word no-match pattern case from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` is a public launcher contract and must not split into a shorter false-positive search plus bogus paths.
- Implicit native-binary resolution must ignore stale in-tree binaries such as `rust_core/target/debug/tg.exe` and `rust_core/target/release/tg.exe`. `uv run tg doctor --json` should report them under `skipped_native_tg_binaries`, set `rust_binary_version_status = stale-skipped`, and keep `search_acceleration_backend = rust-core-extension` when the embedded extension is available. Rebuild with `C:/Users/oimir/.cargo/bin/cargo.exe build --manifest-path rust_core/Cargo.toml --release` or pin `TG_NATIVE_TG_BINARY` to opt in to a specific standalone binary.
- Raw unsorted output ordering is semantic parity, not golden stdout parity. Use `--sort path` when deterministic path ordering matters and `--format rg` when automation needs exact ripgrep-style text formatting. Sorted files-with-matches, files-without-match, and replacement output are rg parity regression surfaces in the validated compatibility set.
- `tg search --json` is tensor-grep aggregate JSON, not ripgrep JSON Lines. `tg search --format rg --json` is the explicit ripgrep JSON Lines compatibility route and deliberately emits raw rg events without the tensor-grep envelope. `tg search --ndjson` is tensor-grep's flattened streaming row schema, not the rg event schema. Do not describe default `--json` or `--ndjson` as rg JSON compatibility.
- `edit-plan`, MCP `tg_edit_plan`, and session edit-plan should keep the agent command-surface budget flags aligned with `agent` / `context-render` (`--max-files`, `--max-sources`, `--max-tokens`, and related schema fields) while preserving the core contract that edit-plan emits no rendered source text.
- `tg new` must never silently ignore unknown scaffold arguments and write root files. Unsupported shapes should fail before writing; supported rule/test/util scaffolds must respect `--base-dir` and create only the requested item.
- Stable managed install scripts and `tg upgrade` are part of the public launcher contract. When release-native assets exist, the public front door should launch the matching native `tg` binary first and set `TG_SIDECAR_PYTHON` / `TG_NATIVE_TG_BINARY`; Python remains the sidecar or fallback, not the normal exact-text first hop. On Windows, put the managed native front-door directory ahead of compatibility shim directories on User PATH so `cmd`, unprofiled PowerShell, and Python subprocess calls resolve `~/.tensor-grep/bin/tg.exe` before the slower argv-safe `.cmd` bridge. A release that updates installer URLs is incomplete until GitHub release assets are uploaded and verified, not merely PyPI-published. Stable installers should clear stale package metadata before resolving `tensor-grep`, check native installer command exit codes before committing the staged install, and stage the new managed environment plus front-door files before replacing an existing install. `tg upgrade` should skip yanked PyPI releases, never report "latest PyPI version" from unchanged local metadata without verifying the target Python can import `tensor_grep`, refresh the managed release-native front door to the verified sidecar version, schedule a Windows retry helper when the running native `tg.exe` is locked, and require the scheduled Windows self-upgrade helper to verify the expected version too.
- `tg doctor --json` should expose launcher route state, not just version parity. Check `path_tg_first_launcher_kind`, `fresh_shell_path_tg_first_launcher_kind`, `python_subprocess_path_tg_first_launcher_kind`, `path_tg_launcher_warning`, and any `*_is_foreign` / `*_foreign_remediation` fields before interpreting Windows benchmark results; an existing shell can still be using the slower compatibility shim after User PATH has been fixed for fresh shells, Python subprocesses can resolve differently from shells, and unrelated tools can own a different `tg` command.
- Cold-path benchmark artifacts should include both `tg_launcher_mode` and `tg_launcher_command_kind`. Benchmark scripts should emit top-level warnings when the timed `tg` command is a `.cmd` shim, `uv`, Python-module route, or stale in-tree native tg binary. Stale in-tree native binaries must block claim-quality benchmark scripts by default unless the operator passes `--allow-claim-unsafe-launcher` for exploratory timing. Do not compare or market timings until native-exe, `.cmd` shim, `uv`, Python-module, and stale-binary routes are separated in the artifact with `tg_binary_version_status`.
- The native front door must not reject public flags advertised by the Python CLI. If a surface is still Python-backed, route it to the sidecar deliberately and add a public-native regression test plus dogfood coverage for the installed command shape. Current parity-sensitive examples are `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, `tg classify --format json`, advertised rg-style search flags, and option-first root `tg ...` forwarding.
- `classify` should be quiet and deterministic by default. It should use local heuristics unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` explicitly opts into the CyBERT/Triton provider, and provider failures should fall back before tokenizer/model loading.
- GPU benchmark correctness must treat no-match as a real comparator outcome. `rg` exit code `1` with empty output is valid when `tg` also returns no matches. GPU scale gates should include 1GB and 5GB rows and exact match/file-set correctness for every >=1GB GPU corpus before any GPU promotion claim. Explicit `--gpu-device-ids` routing must not initialize or warn about unselected GPUs.
- GPU benchmark auto-recommendation must remain false unless required 1GB/5GB correctness checks pass and a selected GPU beats both `rg` and `tg_cpu` at the required scale and declared workload class. The current CUDA-native speed wedge is many fixed strings over a large corpus; single-pattern cold grep remains an `rg` lane. Unsupported-device inventory warnings must not be attached to unrelated selected-GPU timing rows. Any GPU-requested CPU fallback or sidecar route must surface `gpu_evidence_status = unsupported`, `gpu_proof = false`, `native_gpu_unavailable`, and `not_gpu_proof_reason`; unsupported rows must use `promotion_evidence = false`. Public managed GPU promotion additionally requires managed NVIDIA front-door provenance from `tg-native-metadata.json`, direct `rg --json` 1GB/5GB match-identity correctness, and `benchmarks/run_gpu_native_benchmarks.py --public-managed-proof` producing `public_managed_promotion_ready = true` and `public_gpu_proof = true` from the dispatch-only `public-gpu-proof.yml` workflow; local CUDA-feature binaries are implementation evidence, not public managed promotion proof.
- `edit-plan` and `context-render` JSON should expose top-level `validation_commands` so agents do not need command-specific parsing to find the validation list.
- Token-efficiency work must be opt-in and contract-aware. Lessons from `rtk` point toward a bounded agent output profile with hard caps, grouped excerpts, truncation, and omission counts; do not change raw `--format rg`, `--json`, or `--ndjson` semantics to save tokens.
- The product wedge is not "faster grep." It is an agentic code-intelligence runtime: given a task, identify what matters, explain why, emit bounded context, suggest validation, preserve rollback, and report confidence. `tg agent` / Actionable Context Capsule is the opt-in command for that workflow.
- The Actionable Context Capsule contract includes the primary file/function, route rationale, bounded source snippets with line maps, detected validation commands, risk level, suggested edit order, checkpoint or rollback metadata, omission counts, confidence, call-site evidence status, and an "ask user before editing" recommendation when uncertainty or risk is high. Capsule v1 leaves `related_call_sites` empty unless verified call-site evidence is explicitly collected.
- Capsule confidence must be honest when query language hints, exact symbol intent, primary target language, selected snippets, and validation commands disagree. In mismatch cases, cap both `confidence.overall` and `primary_target.confidence`, expose `query_language_hints`, `primary_target_language`, `validation_alignment`, and `validation_filtered_count` in `context_consistency`, and require ask-before-editing.
- Future search-intent routing should label evidence honestly as `parser-backed`, `rg-backed`, `graph-derived`, `heuristic`, `LSP-confirmed`, or `stale/uncertain`. The router can combine text search, AST, symbol graph, imports, tests, and docs, but it must report the route instead of hiding backend choice.
- LSP provider availability is not proof of working semantic navigation. Treat `tg lsp-setup` / `tg doctor --with-lsp` availability as install evidence only; provider-backed navigation must report `health_status`, `health_check`, `lsp_proof`, `lsp_evidence_status`, and `not_lsp_proof_reason` when it falls back to native evidence. A navigation row counts as LSP proof only when it carries `lsp_provider_response = true` from a completed provider request; `provenance = "lsp-*"` alone is not enough. Keep `lsp` / `hybrid` optional and experimental until real provider-backed requests are latency-bounded, reliable, and measurably better on accepted hardcase artifacts.
- `tg callers` and `tg blast-radius` JSON carry an additive `result_incomplete` field (v1.17.0, #281). `result_incomplete = true` means the scan hit an output or scan cap and the call-site list is TRUNCATED — do not treat a truncated zero-caller result as confirmed dead code. A clean scan that resolves zero callers emits a separate "resolved zero-caller" caveat, and even then is not proof of dead code: the call graph cannot see set/list/decorator/dispatch-table registration sites. Cross-check with `tg scan` or pattern grep before removing a zero-caller symbol.
- `tg callers` is Python-first (`docs/harness_api.md`): call-site resolution matches Python AST call nodes most reliably and can under-match or run for minutes on large TypeScript/JS repos. Dogfood receipt (v1.19.3): on a TS-heavy repo, `tg refs` returned 14 reference sites for a symbol where `tg callers` returned 1. Prefer `tg refs` for TS/JS symbol navigation; still cross-check with `tg scan`/grep per the registration-completeness blind-spot note above.
- Running `tg search PATTERN` with no path (or `tg search --glob X -l` without a scoped path) against this repo hangs ~600 s then errors: tg's own index dirs (`.tensor-grep/`, `_tg_refs/`, `.tg_semantic_index/`) and the vendored `benchmarks/external_repos/` tree are not auto-excluded and hit the default `TG_RG_TIMEOUT_SECONDS=600`. Scoped search runs in ~0.4 s. Workaround: always scope `tg search` to an explicit path (e.g. `tg search PATTERN src/`). Planned fix: own-dir excludes + fail-fast timeout + trigram-hybrid index.
- BM25/IDF-ranked surfaces (`tg search --rank`, agent-capsule, local semantic search) are sensitive to corpus changes: adding code that introduces or repeats query-adjacent terms lowers those terms' corpus-wide IDF, which can flip a ranking result and silently degrade a safety behavior. This IDF blast-radius is invisible to the call graph (no caller/callee edge exists for a ranking shift). Harden tie/marker detection to be robust to IDF shifts rather than relaxing a failing test — relaxing masks a real degradation. Tracked as capsule-hardening Task #4 (ledger B3).

## Operating Rules

1. Start with a failing test when behavior changes.
2. Make the smallest defensible change.
3. Run local gates before pushing, but keep them scoped on this desktop unless the user explicitly approves heavy validation. Prefer targeted tests locally and use PR/main CI for full pytest, full Rust test/clippy matrices, benchmark suites, release asset builds, and other high-memory gates.
4. Benchmark every hot-path change.
5. Reject regressions even if the code is otherwise clean.
6. Do not change workflow, release, or docs contracts without updating the validator-backed tests.
7. Do not run `wsl --shutdown`, restart WSL, stop Docker/WSL services, kill WSL processes, or reboot/restart the host as memory cleanup without explicit user approval. Other agents use WSL. If memory pressure is observed, first collect read-only process/memory evidence, stop only tensor-grep-owned processes you started, and ask before touching unrelated processes.
8. On ANY red CI check — not only a release-publish failure — decode the structured job result FIRST:
   `gh run view <id> --json jobs`, find the failing job, read its actual `--log-failed` / the failing
   test's −/+ diff, before theorizing from a traceback. A contract change (ruff / exit-code / JSON schema)
   is usually PINNED by a governance test; update the pin in the SAME PR rather than loosening the test.
   See `tensor-grep-debugging-playbook`, and the push-race-specific instance under Push Discipline.

## Adding a Command or Flag

Adding a top-level `tg COMMAND` requires four registration points or the new command silently misroutes:

1. `KNOWN_COMMANDS` in `src/tensor_grep/cli/commands.py` — the Python-side known-command registry.
2. A `Commands::X` passthrough variant and a matching dispatch arm in `rust_core/src/main.rs` — the native front door must know about it.
3. `PUBLIC_TOP_LEVEL_COMMANDS` in `tests/e2e/test_routing_parity.py` — the contract test that enforces parity between Python and native.
4. A `@app.command` function in `main.py` — the Typer app entry point.

Adding a search flag (e.g. `tg search --myflag`) requires two front doors or the flag leaks to ripgrep and causes an `rg: unrecognized flag` crash at runtime:

1. `SEARCH_PYTHON_PASSTHROUGH_FLAGS` in `rust_core/src/main.rs` — the native binary's allowlist.
2. `bootstrap._TG_ONLY_SEARCH_FLAGS` in `src/tensor_grep/cli/bootstrap.py` — the Python bootstrap's allowlist (the Python front door runs before the Typer app and forwards plain searches to rg).

Missing either slot lets the flag reach ripgrep for users who install the published binary while your CliRunner tests pass cleanly — exactly how the `--rank` crash shipped undetected.

**Registration-completeness is a universal bug class, not a tg quirk.** "Add a thing that must be registered in N places, miss one, it fails *quietly*" hit tg here (the `--rank` flag missed one of two front doors) and a downstream user's billing code (a new `/v1` route missed the cron registration + a `test_route_scope_coverage` exemption — green tests, broken route). Before claiming any registration change is done, **enumerate all N sites**. `tg callers <registration-function>` lists every *callable* registration in ~1s — but the call graph **cannot see set/list/decorator registrations** (an allow-list like `bootstrap._TG_ONLY_SEARCH_FLAGS`, `@router.post`, dispatch tables), and those are often the missed site (`--rank` lives in a *set*, not a call — `callers` would never have found it), so **grep / `tg scan` those**. Confirm your new entry appears in *all* sites. This is the default audit path (`tg callers` for blast radius → `tg scan` for pattern bugs → `tg doctor --with-lsp` for diagnostics); the principle is Hard Rule 6 in `verify-plan-against-code`, and the call-graph blind spots are in `tensor-grep-code-audit` (P7).

As of v1.17.1 (#282), the CI registration-completeness gate is BLOCKING — a registration mismatch fails the CI run, not just warns. The checker's member extractor is now string/comment-aware, so `#`-commented entries are no longer surfaced as false registered members.

**A new MCP tool function is a FIFTH registration site, not one of the four above.** Every tool's JSON
envelope embeds `mcp_contract_version` from the single `_TG_MCP_SERVER_CONTRACT_VERSION` constant
(`mcp_server.py`) — bump it whenever a tool's request/response shape changes. Same "enumerate all N
sites" bug class: the `tg_find` MCP PR (#627) shipped with an un-bumped contract version, caught only by
the mandatory adversarial Opus gate, not by tests or CI.

## Adding a Language (symbol-graph tier)

tg's deep symbol-graph tier covers all 10 of the top-10 languages (Python, JS, TS,
Java, C#, Go, Rust, PHP, C, C++ — priority per TIOBE Jul-2026 + Stack Overflow 2025
+ GitHub Octoverse 2025 consensus). As of Task 10E (C++, the final wave of the
top-10 language-support campaign), all 10 registered languages carry in-file
parser-backed refs/callers — the foundational-tier (defs/source/imports/agent
only, no `references_and_calls`) is now EMPTY; ask
`repo_map._symbol_navigation_descriptor()` rather than trust this sentence, since
it has been wrong before (see `lang_c.py`/`lang_cpp.py` for the C/C++ landing
history — C shipped foundational-only first, C++ followed the same path, both
were promoted to parser-backed later in the same campaign).
Adding one is a **registration-completeness**
problem (see the universal bug class above): the CURRENT pattern is
`lang_registry.register_language(LanguageSpec(...))` plus a self-contained
`src/tensor_grep/cli/lang_<x>.py` module mirroring `lang_go.py` — NOT the inline
`_rust_*` / `_parser_for_source_suffix` machinery (that is the STALE style; Rust and
Python predate the registry). Java used inline+registry (mirrors Rust); C# and PHP
used module+registry (mirrors Go). Both are contract-consistent.

**Five critical seams — miss one = a silent half-integration.** Enumerate the seams
`lang_go.py` touches and hit ALL:

1. `_imports_and_symbols_for_path` — `tg imports`.
2. `_imports_with_lines_for_path` — `tg imports` line spans.
3. `build_symbol_source_from_map` — `tg source`.
4. `_target_language_for_path` — **MOST-FORGOTTEN.** Feeds the `tg agent` capsule
   confidence gate; without it a Java target won't filter a mismatched Python/pytest
   validation suggestion.
5. `_SUPPORTED_FILE_DEPENDENCY_LANGUAGES`.

**Fail closed (per the Backend Fail-Closed Contract).** Grammar-missing → labeled gap
(`provenance_when_missing="grammar-missing"`, NO regex fallback). Deferred caller-graph
fields → explicit `None` → an honest `resolution_gaps` entry (treat zero as UNKNOWN,
never silent proven-zero). Symbol-kind mapping:
class/interface/struct/enum/record/trait → "class"; method/constructor/function →
"function".

**Live-verify the grammar node shapes** — dump the real tree-sitter AST, do not guess.
e.g. C# `using Alias = Target;` puts the alias identifier BEFORE the target, so
`_csharp_using_directive_target` must record the TARGET, not the alias.

**Verify the plan against the real code before dispatch** (see "Verify AI-Drafted Plans
Against the Real Code" below): a brief that says "mirror inline `_rust_*`" is STALE —
all three build agents caught it against the grown `lang_registry`. Verify against
CURRENT code, not a mental model.

**Positioning (the tiered model):** text search = ANY language (rg passthrough);
structural scan/rewrite = 26 langs (`tg ast-info`, via ast-grep which tg WRAPS); deep
symbol-graph = the tree-sitter grammars (the 10 above). tg = rg (text) + ast-grep
(structural) + a symbol/retrieval/capsule LAYER on top. NOT "faster grep."

**Parallel-drain hygiene:** a new grammar touches `test_lang_registry`, the pyproject
`ast` extra, and `uv.lock` — apply the uv.lock hand-splice (Local Dev Gotchas) and the
A22 sequential-drain-union-rebase discipline (Campaign Orchestration Disciplines).

See `.claude/skills/tensor-grep-add-language/SKILL.md` for the full registration
checklist (field-by-field `LanguageSpec` reference, live-verified `repo_map.py` seam
locations, and the deferred C/C++ scoping notes) — this section is the gist, not the copy.

## Dogfood the Real Binary, Not CliRunner

The `tg` entry point is `tensor_grep.cli.bootstrap:main_entry`. It intercepts plain text searches and forwards them to ripgrep **before** the Typer app sees the argv. `CliRunner` invokes the Typer app directly and bypasses this front door entirely — so bugs in the bootstrap routing layer are invisible to unit tests.

After adding or changing a search flag or command, dogfood the **installed published binary** using the harness at `scripts/dogfood/` (Dockerfile + `dogfood_features.py`). The harness installs the real PyPI wheel and runs every public command shape through the actual `tg` binary. Do not rely on `CliRunner` alone for routing coverage.

## Verify AI-Drafted Plans Against the Real Code Before Building

Before implementing a plan produced by an AI subagent or any external planning pass, check every factual claim in the plan against the real source files by citing `file:line`. A claim with no citation should be treated as a hypothesis, not a fact.

This matters because AI-generated plans have a consistent failure mode: they identify plausible-sounding edit locations that do not match the actual code structure (dead code paths, renamed symbols, already-fixed lines). A verification pass that reads the real files before implementation is not overhead — it is the gate that prevents wasted cycles. A council or read-only review that cites file:line evidence caught 5 blockers in two unverified plans in a single session.

Re-run any validation a subagent claims to have passed — subagents can assert success without executing. For PRs that ship generated or detached code (install scripts, Windows self-upgrade helpers), adversarial-review by EXECUTING the code, not only reading it: `compile()` + `exec()` the generated string and assert the behavior (e.g. that the checksum gate fires BEFORE `os.replace`, and that the fail-closed branch is reachable). Test behavior, not substrings.

**A banked "fix hypothesis" is a guess, not a plan (task #736, 2026-07-24).** A one-line note carried
forward in memory claimed the C function-pointer-variable mis-kind was fixable by "requiring
`function_declarator` outermost." `verify-plan-against-code` FALSIFIED it before a line of fix code
was written: a function-pointer *variable*'s declarator chain also has `function_declarator`
outermost — same as a real function prototype — so that tell cannot distinguish them. The real tell
was one level deeper: what that node's OWN `declarator` field wraps (`parenthesized_declarator`
wrapping a `pointer_declarator` -> variable, exclude; wrapping a bare `identifier` -> a
redundant-paren real function `int (foo)(void);`, keep). Re-derive a banked hypothesis — including
your own prior session's note — against the real AST/code before dispatching it as a plan; a
carried-forward guess is not exempt from the same verification a fresh AI-drafted plan gets.

After building, run a mandatory post-build ADVERSARIAL AUDIT — a distinct named stage from the pre-build planning council. This audit caught a HIGH CUDA-fork hazard that 203 passing tests missed. A finding or claim with no `file:line` citation is DISCARDED. Re-audit → fix-wave → re-audit until ZERO must-fix findings remain; that zero-finding state is the convergence gate before promoting a build to a draft PR.

**A gate's disclosed edge case is in-scope while the PR is still draft, not a new backlog item (same
task #736).** An independent Opus gate returned SHIP on the C function-pointer fix but disclosed that
the first cut now dropped a rarer case (redundant-paren prototypes, `int (foo)(void);`) it hadn't
before — trading one cosmetic bug for a narrower one. Because the PR was still draft, the refinement
landed in the SAME PR before un-drafting, shipping with zero new known-limitations. Treat a
SHIP-with-disclosed-edge verdict as work still owed on the open PR, not a "ship now, file a follow-up"
signal — the cost of fixing it right there is near zero; the cost of a new tracked gap is real.

**A passing test proves nothing until you have seen it FAIL on the pre-fix baseline (#737, 2026-07-24).**
An independent Opus gate on the C++ function-pointer-variable fix found the new shape-9 test pinned only
the IN-CLASS member-fn-ptr shape (`class C { void (C::*mp)(int); };`) — which tree-sitter already
excluded on pre-fix `origin/main`, through an unrelated code path (it emits `['ERROR',
'pointer_declarator']`, caught by a `len(named_children) != 1` early return that never reaches the fix's
new logic at all). The shape the fix actually repaired — file-scope `void (C::*mp)(int);`, which wraps a
`qualified_identifier` — had NO test guarding it. The fix itself was correct; the first test written for
it was a no-regression pin, not a bug guard, and would have passed unmodified even without the fix. Rule:
**"I added a test, it's green" is not coverage** — before trusting a new test, confirm it goes RED when
run against the pre-fix code.

**A false claim inside a COMMENT is durable misdirection — gate the prose with the same rigor as code
(#739, 2026-07-24).** A later pass of a checkpoint-hot-path deflake correctly fixed the test itself but
added a section-header comment justifying leaving a sibling test's wall-clock ratio alone because it
"genuinely correlates and cancels load." Measured directly: the sibling's baseline (`build_repo_map`)
runs ~0.0031-0.0088s, so `baseline * 6.0` never exceeds ~0.05s and `max(ratio, 8.0)` selects the 8.0s
floor UNCONDITIONALLY — the ratio arm never fires. That is the exact degenerate-`max()` reasoning an
earlier pass of the same PR had just been rejected for, now restated as justification in a comment,
which is a place CI can never fail on. A wrong assertion eventually reds a run; a wrong comment just
misleads the next reader indefinitely. Review comments and docstrings adversarially — with citations or
measurements, not a plausibility check — not just the code they describe.

**Diff review is not measurement review (#739, 2026-07-24).** For the same PR, a set of diff-level
checks — test-only diff, zero `src/` changes, the perturbation cleanly reverted, production call sites
intact — were each individually correct and collectively insufficient: only an independent
re-MEASUREMENT (profiling the real baseline, not reading the ratio formula) caught the degenerate-
baseline bug above. For any de-flake, perf claim, or otherwise QUANTITATIVE fix, the gate must
re-measure the actual numbers, not just re-read the diff for shape.

**Your verification instrument can be the thing that's wrong (2026-07-24).** Reviewing a sibling docs
PR (#740), a spot-check for whether its "DEFERRED" honesty caveat was actually present used `grep -ciE
"DEFERRED\|deferred"` and returned ZERO hits — which briefly read as the agent claiming a caveat it
never wrote. The caveat was there, verbatim; the command was broken. In `grep -E` (extended regex),
`\|` matches a **literal pipe character**, not alternation — extended-regex alternation is a bare `|`;
the backslash-escaped `\|` form is basic-regex/`sed` syntax, not `-E`. So the search was for the
9-character literal string `DEFERRED|deferred`, which of course matched nothing. Rule: when a
verification check contradicts an otherwise-careful report, re-test the INSTRUMENT against
known-present content before concluding the report is false — a false negative from a malformed
pattern is indistinguishable from a real absence, and acting on it sends a spurious correction. Same
family as this repo's git-bash/MSYS `gh --json`-parsing quirks (favor `python` over `jq`/raw `/`-path
expressions there for the same reason): the tool didn't fail loudly, it silently did something other
than what its syntax suggests. Concretely here: `grep -E` alternation is a bare `|`, not `\|`.

**Prove "docs-only"/"comment-only" instead of eyeballing it (2026-07-24).** Twice in the same session a
follow-up commit needed to be certified behavior-neutral to justify skipping a redundant re-run of the
full gate. Method: parse both revisions of the file with `ast`, strip docstrings (plain comments never
enter the AST at all), and compare `ast.dump(tree)` between the two versions — an identical dump proves
zero behavioral change, and is strictly stronger than reading the diff by eye. Used on `lang_cpp.py` and
on `test_index_lock_concurrency.py`'s comment-only revisions; cheap enough to run on every claimed no-op
commit before skipping a gate on the strength of "it's just a comment."

## The Verification-Oracle Family — ten forms (2026-07-25; 7th + 8th 2026-07-26, 9th 2026-07-27, 10th 2026-07-28)

**The single most repeated failure mode this project has.** Every form shares one shape: *something that
looks like verification isn't.* Before trusting ANY green signal, ask: **what would this check show if the
thing it verifies were BROKEN?** If the answer is "the same", it is not verification.

**Form 1 — normalize-both-sides (masks defects; the dangerous direction).** A comparator applies the same
lossy transform to both arms, so a real divergence cancels out and reads as parity. Task #262: the
rg-parity oracles were CRLF- and encoding-blind. A surviving instance in `tests/helpers/rg_parity.py:560`
(`_normalize_line` folds `\\` → `/` across the WHOLE line, so a separator divergence inside MATCHED TEXT
is invisible) is a *consciously accepted* limit — and is now PROVEN lossy rather than argued, pinned by a
characterization test with a discriminability control (PR #748). If you close that limit, that test starts
failing; that is the intended signal, delete it and update the comment.

**Form 2 — harness-corrupts-output (manufactures false failures).** `test_output_golden_contract.py::run_tg`
ran `line.replace("\\", "/")` on the whole output line, turning a binary notice's literal `\0` into `/0`
*after* a byte-correct subprocess call. The product was right; the harness lied. The orchestrator then read
the golden diff as evidence about the PRODUCT and sent an agent hunting an emitter that did not exist.
**A golden diff is evidence about the harness+product PAIR, never the product alone, when the harness
post-processes before comparing.** Fixed in #746 via `_normalize_output_line` (splits at the marker,
normalizes only the path prefix).

**Form 3 — test-never-executes. SKIPPED IS NOT PASSED.** `tests/e2e/test_native_json_byte_fidelity.py` was
written specifically to prove the #266 emitter fix; its own header named CI as the oracle. It SKIPPED in
every CI job, because `native-build-smoke`'s pytest step named ONE HARDCODED FILE. A green suite reported
proof that never ran. **Always read the SKIP count, and grep whether the env gate your test needs
(`TG_REQUIRE_RG_PARITY`) is actually set in a job that also builds the binary.** Fixed in #746 (glob) and
class-fixed in #749 (an invariant asserting every marker-bearing suite is matched by the pattern CI really
runs, parsed out of `ci.yml` rather than copied).

**Form 4 — gate-diagnosis-wrong.** The gate that *found* Form 3 was right about the conclusion and wrong
about the cause: it reported `TG_REQUIRE_RG_PARITY` set in "zero workflow files" and "no CI job both builds
the binary and runs pytest" — both false (`ci.yml:599,653,657`). The orchestrator relayed that root cause to
a build agent **without checking it**, which nearly produced CI plumbing that already existed.
**"A gate's clearance is a hypothesis" applies to its ROOT-CAUSE STORY too, not just its verdicts.**
Verify the diagnosis, not only the finding.

**Corollary — isolation-level evidence is not outcome-level evidence, and the rule binds PROSE.** In #747
the orchestrator measured a bootstrap helper IN ISOLATION (`workspace_root_guard=False`) and wrote it up as
a user-visible guard bypass. The gate ran the control arm through real `main_entry()`: the refusal fires
IDENTICALLY in both arms — the defect was LATENT, masked by full-CLI routing. A confidently-wrong comment is
**And never PUBLISH an untested cause.** A PR body told other contributors "a `pip install -e .[dev]`
here left 5 of 11 declared grammars absent", framed as a warning -- the command was never run. The real
cause was a stale interpreter carrying tensor-grep 1.83.0, ~18 releases behind, predating those
grammars' entry into the extras. An explanation that merely FITS the evidence is a hypothesis;
shipping it as a finding, especially one addressed to other people, is fabrication.

worse than none. Any claim of the form "X causes user-visible Y" needs the control arm, not just the
mechanism.

**Corollary — when you cannot observe RED, say so.** CPU-SAFE forbids compiling, so a Rust fix often cannot
watch its own new test fail pre-fix. The correct move is a STRUCTURAL argument from pinned source (e.g.
"`trim_end_matches(['\n','\r'])` is a deterministic std call that strips both, so the pre-fix 11-byte value
cannot equal the asserted 12-byte one" / "the pre-fix struct had no `bytes` field at all") **stated plainly
as an argument**, never dressed up as an observation. Gates are expected to judge whether the chain closes,
not to penalise the disclosure.

**Form 5 — the repro's TOPOLOGY deletes the mechanism (2026-07-25, PR #750).** The subtlest one, and it
defeats an *honest* structural argument. #750 fixed `--no-ignore-vcs` on the native walk and proved RED on a
live binary — but every fixture, in both the reproduction AND the four new unit tests, was a **non-git**
directory (`tempfile::tempdir()`). Non-git is precisely the one topology where the proposed mechanism
(`add_ignore` skipping) is sufficient, because the `ignore` crate's `require_git(true)` leaves its native git
machinery dormant there. **Inside a git repo — tg's dominant case — `.gitignore` is applied natively and the
fix is a no-op.** The gate reproduced the bug surviving the fix. Nothing in the PR, hand-trace included,
*could* have caught it: the RED demonstrated was a strict subset of the real defect.

The rule: **ask whether your simplified repro deletes the very thing you are testing.** When the executable
arm is unavailable, the non-executable arm needs a SECOND, DIFFERENT fixture — vary the TOPOLOGY (git vs
non-git, nested vs root, one file vs many), not just the flags. A fixture family that shares one structural
property cannot discriminate on that property. Related receipt: an earlier session's repro removed the
`asyncio.to_thread` boundary the bug actually lived across, and so could never have shown it.

Corollary for reviewers: when a fix and its tests share a fixture shape, that shape is an untested
assumption. Ask what topology the mechanism behaves differently in, and demand one case there.

**Form 6 — the FIXTURE never applied (2026-07-25, #281).** Forms 1-5 assume the setup worked and the
comparison was wrong. This one inverts it: the assertion is fine, the **setup silently no-opped**, so
the "hostile" arm was never hostile. Probing whether the native front door drops its JSON payload on a
walk error needs a genuinely unreadable directory. `icacls` failed to apply the deny ACE **twice** —
`"No mapping between account names and security IDs was done. Successfully processed 0 files"` for both
`%USERNAME%` and `MACHINE\user` — and printed that on stderr while exiting in a way easy to skim past.
Had the probe run anyway, the directory would have been perfectly readable, tg would have returned a
complete result, and the honest-looking conclusion would have been *"no defect — the payload is intact."*
The bug would have been declared absent by a test that never tested anything.

What saved it was a **precondition check that asserts the fixture BITES before the probe runs** — read
the directory and require a `PermissionError`; print `STILL VACUOUS` and abort otherwise. Write that
check for every hostile fixture: permission denials, network partitions, disk-full, killed processes,
corrupted files. **A fixture is a claim about the world, and claims get verified.**

Two diagnostics worth keeping: an account-name **mapping** failure is not a **privilege** failure —
`icacls <dir> /reset` takes no account name and works unelevated on a directory your own user locked,
so if `/reset` ALSO fails the DACL belongs to a different SID (that is how #268 was proven genuinely
operator-gated rather than a tooling quirk). And to APPLY a deny ACE reliably on Windows, go through
PowerShell with the SID from `WindowsIdentity::GetCurrent().User`, not an `icacls` account string.

**Form 7 — the MEASUREMENT that cannot discriminate (2026-07-26, #302).** Forms 1-6 are about tests.
This one is about benchmarks and scorecards, where the same question applies unchanged: *what would
this column show if a tool were GOOD at it?* The trust benchmark's `vanished-file` column scores **0
for every tool on both platforms**. A column where every arm ties at the floor separates nothing — it
cannot distinguish a tool that handles the case well from one that ignores it entirely — yet a reader
scanning six zeros concludes "they are all bad at this", which the data does not support. A
tied-at-floor column is worse than no column, because it looks like a finding. **Rule:** every
scored dimension needs at least one run where arms differ, or it gets deleted with the reason
written down.

**Generalised beyond scored columns: EVERY PROBE CARRIES A POSITIVE CONTROL (2026-07-27).** A zero
means "measured nothing" or "never actually checked", and the two are indistinguishable in the
number. Before trusting a zero, show the SAME probe returns non-zero somewhere it should. Two
receipts in one session: a language-registry probe read "5 registered, 0 foundational" and looked
like a clean answer -- it was run against a 2-commit-stale checkout and the truth was 10/5, exposed
only by asserting the registry was non-empty AND printing the loaded module's `__file__`; and a grep
for an unsourced benchmark figure returned 0 files, which proved nothing until the identical grep
form returned 162 hits for `ripgrep`. Tracked as #302; the fixture almost certainly deletes the file *before* the search
starts, so every tool correctly reports nothing — the race the column claims to measure never opens.

**Form 8 — the SPLIT ORACLE (2026-07-26).** *A precondition proved in a DIFFERENT run is not
THIS run's precondition.* Caught by an external codex audit in a test whose own docstring described
it as bidirectional. `tests/unit/test_trust_benchmark_premise.py` pins the claim "rg cannot signal an
incomplete scan inside its JSON stream" with two arms: ARM 1 runs `rg --json` over a tree containing
an unreadable directory and asserts the summary carries no incompleteness marker; ARM 2 asserts rg
exits 2. **ARM 1 never asserted its OWN run exited 2.** On a tree where the directory turns out to be
readable, rg exits 0, completes the scan, and correctly emits no marker — and ARM 1 passes, then
reports "rg hides incompleteness" on the evidence of a scan that was never incomplete.

What made it feel safe is the shape to learn: a helper DID verify the directory was unreadable — *to
the test process* — and ARM 2 DID assert exit 2. Both true, neither load-bearing for ARM 1.
"Unreadable to pytest" is a different claim from "rg's scan was incomplete", and ARM 2 is a separate
subprocess. Two correct checks sitting beside a conclusion neither supports.

**Rule:** for every conclusion ask *which run produced the evidence for the premise, and is it the
same run that produced the thing I am judging?* If the answer is "a sibling test", "an earlier
fixture", or "the helper checked it", the oracle is split. The fix is usually one line — move the
premise assertion INTO the run that draws the conclusion (`assert proc.returncode == 2` before
reading the summary) — and the failure message should name the real cause (*"something made the
locked directory readable to rg"*), not blame the assertion, because a split oracle fails
confusingly precisely because the assertion is fine. **A control in another process controls
nothing.** This is the mirror of the setup-not-assertion trap: that one asks *did this check run
against the thing I think it did?*; this one asks *did this RUN establish the condition my
conclusion needs?*

Second-order, and the reason the independent audit step keeps paying for itself: an external
reviewer found this in work that had already been self-reviewed AND given a careful docstring
asserting its rigour. **Prose describing a test as bidirectional is not evidence that it is.**

**Form 9 — the REVIEWER'S EXPECTED NUMBER is the broken half (2026-07-27, #334).** Forms 1-8 all
assume the checker is wrong about the CODE. This one inverts the subject: a census mismatch is a
**two-sided hypothesis**, and the wrong side is often the expectation you brought to it. It fired in
both directions in one session — an envelope seam expected at 2 sites was really 3, and four comments
suspected of claiming "observed no walk" were each individually correct. Both were a keystroke from
being filed as product defects on the strength of a number that merely felt wrong. **Read the
breakdown before filing the finding**: a count that disagrees with your expectation is a prompt to
enumerate the members and look at each, not evidence of a bug.

The same law governs a finding handed to you by another agent. The 2026-07-27 skill audit reported
real drift, but every corrected line number in it was itself wrong — computed against a worktree 28
commits behind `origin/main`. Right finding, wrong expected value. **Re-derive before you act on
someone else's number**, and where the class recurs, replace the number with a command that
regenerates it (`.claude/skill_anchor_audit.py` — see "Model The Class").

**Form 10 — the ORACLE'S UNIT IS THE BRANCH, and the defect lives in the MERGE (2026-07-28).** Every
form above assumes the check is looking at the right *code*. This one is about looking at the right
*tree*. PRs #835 and #836 were each fully green — 48 checks apiece, bidirectional control arms, an
independent adversarial gate on one of them — and **main went red the moment they were both on it**.
#835 asserted that exactly ONE line of `--mermaid` output mentions `INCOMPLETE RESULT`; #836 added a
second disclosure line on purpose. Git merged them with **no textual conflict**, because there is
none: the collision is semantic and exists only in the union. CI never evaluated that union, since
each PR's checks ran against its own base.

Cost: main red, a release lost (`Semantic Release` skipped, so v1.101.8 was never produced), and
`tag == PyPI` kept reading "gate open" precisely *because* the release had died rather than finished.

It then recurred on the very next PR. #837 was rebased onto a main that still lacked the fix, and
came back with the identical `assert 2 == 1` — the same defect inherited rather than introduced.

**The rule: before pushing, rebase onto the REAL target and run the union.** A branch that is green
against a stale base has verified a tree nobody will ever ship. Two smells that a merge is
semantically live even when git is silent: (a) the PRs touch the same OUTPUT SHAPE, even in different
files or different functions; (b) one PR adds to a rendering that another PR *counts*. Grep the whole
suite for assertions about the shape you are changing — **the file you are editing is not the
boundary of the blast radius.** #836 did correctly update the identical assertion in
`test_leading_truncation_banner.py`, and missed its twin in a file that PR never opened.

The 2026-08-04 session added the TIME variant: the colliding slice can LAND AFTER your union run
(a release publishing mid-review put a still-green #928 out of tolerance at merge), so a union is
only current as of its own timestamp -- see "Seven Instruments, One Empty Queue, And A Release That
Reddened Every Open PR" below for the merge-time gate.

### Form 1, applied to GUARDS: run every new ratchet against the PRE-FIX revision

Not an eleventh form — Form 1 (*what would this check show if the thing were BROKEN?*) pointed at the
guard you just wrote. It earns its own heading because the failure is invisible in the usual way: the
guard is **green on the fixed file**, which is exactly what you expect, so nothing prompts you to
question it.

Receipt (2026-07-28, PR #848). After fixing a smoke probe that swallowed its child's error, the
ratchet asserted that no `subprocess.run` pairs `capture_output=True` with `check=True`. Green on the
fix. Run against the pre-fix file it was **also green — zero violations** — because the offending
call lived inside a string handed to `python -c`, so no call node in that file ever carried those
keywords. The guard was protecting a shape the file has never contained, and would have shipped as
permanent proof of nothing.

**The check:**

```bash
git cat-file blob HEAD:path/to/file.py > /tmp/prefix.py   # NOT `git show rev:path` -- MSYS mangles it
# run the guard's matcher against /tmp/prefix.py and require a NON-ZERO violation count
```

A guard that reports 0 on the code that caused the incident is decoration. Re-aim it at the property
that actually differed — here, the nesting — and re-run until the pre-fix count is non-zero (it
became 2).

Two recurring traps once the guard does bite:

- **Quoting vs. asserting.** A source-scanning guard trips on the docstrings that *quote* the
  forbidden pattern in order to explain it — third occurrence in this campaign, after the
  `CONTRACTS.md` anchor-rot record. Exclude prose **structurally** (AST docstring nodes by identity),
  never by pattern-matching the wording, or any future edit silently re-breaks the checker.
- **Match nodes, not text.** `ast.walk` over real call/constant nodes cannot be fooled by a comment,
  a test fixture, or a changelog entry that mentions the pattern.

### A probe that discards the child's error is worse than no probe

`subprocess.run(..., capture_output=True, check=True)` raises a `CalledProcessError` whose string
form is argv + exit status. The captured streams hang off the exception object and are **never
printed**. The probe then reports THAT something failed while withholding WHY — and a confident
wrong theory is more expensive than an admitted unknown.

Receipt (2026-07-28): `validate-pypi-artifacts` failed on the v1.101.10 release run (30363114542),
skipping `publish-pypi` and leaving the version tagged but unpublished. The log's entire diagnostic
content was `... returned non-zero exit status 1`. **Three** hypotheses — dependency drift, an `mcp`
1.28.1→2.0.0 major bump visible in the dep diff, and the diff of the PR that triggered the release —
were each built and falsified against that log before it became clear the cause had been thrown away
at capture time.

- **Before theorising from a CI failure, ask: did this log ever contain the cause?** If the runner
  captured output, the answer may be no, and every hypothesis built on it is unfalsifiable.
- Never pair `capture_output=True` with `check=True` in a diagnostic. Print argv, exit status,
  stdout AND stderr, then exit. stdout matters as much as stderr for `tg` — refusals and
  incompleteness envelopes go there, and a stderr-only report hides the common case.
- **Don't drive a command through a nested interpreter.** Each layer re-wraps the error: the child's
  real failure becomes a `CalledProcessError` inside the child, which the outer `check=True` wraps
  into a second one naming only `python -c`. The message is lost at the first.
- A `CalledProcessError` also cannot represent the other half — the command **succeeds** and prints
  the wrong thing. That path needs its own reporter, or it surfaces as a bare `AssertionError` with
  no payload.

This is the CI-probe twin of the false-zero law: there, an instrument returns EMPTY and the zero
reads as a clean bill; here, an instrument returns a REASON-LESS failure and the exit status reads as
a diagnosis.

### Trace the SIGNAL PATH to the instrument before believing a negative

The parent pattern behind the false-zero law, the both-arms law, and the setup-lies law. Those name
symptoms; this names the cause: **a negative result has two indistinguishable explanations — the
phenomenon is absent, or the signal never reached the probe.** Nothing in the output separates them,
and confidence is highest exactly when the probe is your own, because you know what you *intended*
it to measure.

Six wrong conclusions in one session (2026-07-28), every one the same failure — the instrument
silently lacked REACH:

| instrument | the hop it could not reach | the false conclusion |
|---|---|---|
| CI log of the failing smoke | `capture_output=True` discarded the child's stderr | 3 hypotheses "tested" against a log with no cause in it |
| a newly written ratchet | the offending call lived in a STRING, not a call node | "guard added" — 0 violations on the code that caused the incident |
| `uv run --no-sync tg` | resolves `.venv/site-packages`, not `src/` | two separate wrong verdicts, one of them a phantom Python-vs-Rust divergence |
| an mcp 1.x-vs-2.0 A/B | on Windows the rewrite routes to `AstBackend` and never imports `mcp_server` | "mcp is not the cause" — it **was** the cause |
| an external audit's `git status` | WSL git over a `core.autocrlf=true` Windows checkout | "338-file dirty tree", P0, *discard it* (destructive) |
| `rustfmt --check <3 files>` | the change edited 4 | "format clean" → CI red |

**The rule: name every hop the phenomenon must travel to reach your probe, and verify each one.**

```
mcp 2.0 breaks tg  ⇒  pattern parsed → command routed → backend selected → mcp_server imported
                                                        ^^^^^^^^^^^^^^^^ Windows exits here
```

Hop 3 was never checked, so hop 4 could not fire, so the probe was structurally incapable of a
positive. One `print(routing_backend)` would have shown it.

Cheapest checks first:

- **Does the probe load MY code?** `PYTHONPATH=src` **and** assert `module.__file__`. Never
  `uv run tg` for a behavioural claim — a dev box can carry three disagreeing `tg`s (PATH-installed,
  `.venv` site-packages, `src/`).
- **Can the probe return non-zero at all?** Run it against the pre-fix revision and require a
  non-zero count (see Form 1 applied to guards, above).
- **Did the failing path actually execute?** Print the branch/route taken, not just the result.
- **Is the probe's scope the whole change?** A gate is only as wide as the file list you hand it.
- **Whose environment produced this snapshot?** A WSL git over a Windows checkout, a stale branch, a
  clean clone — each yields a confident number about a tree nobody is working on.

**Corollary, and the one that cost the most: a simplification that removes the mechanism can never
discriminate.** The mcp A/B was a *correct* experiment on the wrong path. When a repro is simpler
than the failing scenario, ask which hop the simplification deleted — and if the answer is "the one
the bug lives in", the result is not evidence in either direction.

### A lockfile immunises everyone except the canary

`mcp` 2.0.0 removed `mcp.server.fastmcp`, which `cli/mcp_server.py` imports at module scope and
`tg run --rewrite` reaches lazily. Any fresh `pip install tensor-grep` broke the rewrite path. It
blocked `publish-pypi` on two consecutive releases — v1.101.10 and v1.101.11 tagged and never
published — against a declared `mcp>=1.27.2` with no upper bound.

Nobody saw it because **every in-repo consumer installs from `uv.lock`** (pinned 1.28.1): the full
suite, every dev environment, and every CI leg were immune *by construction*. The only component
that resolves fresh is the PyPI artifact smoke venv. One canary, firing correctly twice while it was
treated as the problem.

- **A lockfile is a blindfold as well as a seatbelt.** It guarantees your tests cannot observe what a
  new user gets. Know which component resolves fresh — its failures are user-facing by definition.
- **When only the fresh-resolve component fails, suspect the DECLARED constraint, not the
  component.**
- **Cap majors on anything imported by submodule path.** `from x.y.z import W` is a promise about
  another project's internal layout; a major bump is entitled to break it.
- **Guard the cap by reading `pyproject.toml`, not by importing** — an import-based check passes
  under the lock forever. Pair it with a control arm (the CVE floor survives the cap) and a premise
  (the locked version satisfies the range; a cap excluding the lock is the silent-downgrade trap).
- **A cap is not a port.** Say so in the comment *and* the guard, or someone lifts it to "clean up".

### A checker nobody can run is indistinguishable from no checker

`.claude/skill_anchor_audit.py` exists precisely to catch `file:line` drift in the skill library. It
had not caught any, for two reasons that both look like "the tool is fine":

1. It **crashed** — `path.is_file()` raised `OSError WinError 1920` on a dangling symlink inside a
   nested venv, aborting before it checked a single anchor. The skip list already excluded that
   tree; it was applied *after* the `stat()`.
2. Once it ran, it **drowned its own signal** — 762 `AMBIGUOUS_PATH` findings, because the index
   walked `.venv/` and `.claude/worktrees/*`, so `pyproject.toml:43` resolved to 14 copies.

Both fixed, and the real signal appeared immediately: 3 genuine `SYMBOL_MOVED`. **Before trusting
that a class of defect is absent, confirm its detector RUNS and DISCRIMINATES.** A crash and a
clean bill are the same silence from outside. Same family as the false-zero law, applied to your
own tooling.

### Cite the SYMBOL, not the line — and never re-stamp

`src/tensor_grep/cli/repo_map.py` is over 19,000 lines and grows every release. Its seam citations
in `tensor-grep-change-control` were adrift by **283 to 515 lines** — all five of them:

```
_imports_and_symbols_for_path     6244 -> 6627      build_symbol_source_from_map  15815 -> 16326
_target_language_for_path         7383 -> 7867      _SUPPORTED_FILE_DEPENDENCY_L  16633 -> 17148
_imports_with_lines_for_path      6440 -> 6832      <- the 5th; see below
```

🚨 **That table said "all five of them" and listed FOUR, for four days.** The omitted member,
`_imports_with_lines_for_path`, was the one still stale — 392 lines adrift, the largest of the five
— and it sat inside the very fix that introduced the never-re-stamp law. The skill's own grep
instruction (`tensor-grep-change-control`, the 5-language-seam census) names all five symbols
correctly; only the *execution* was short by one, and the prose asserted completeness over it.

**A census and its own count are two artifacts, and the count is not evidence about the census.**
When you write "all N", COUNT the rows you actually wrote — do not carry N over from the sentence
that motivated the work. This is the same failure as the `population is the defect` family, arriving
inside the remedy for it: the fix was correct, the claim of completeness was not, and the claim is
what everyone downstream read.

**Five previous maintenance passes re-stamped these by hand, and every one shipped anchors that
were already wrong** (the auditor's own docstring records this). Re-stamping is not a fix; it is
the defect on a slower clock. Replace the number with a `grep <symbol>` instruction and keep the
`was -> now` drift beside it as the receipt.

Corollary: **"confirmed byte-stable" has a short half-life, and asserting it discourages the check
that would catch the drift.** `tensor-grep-architecture-contract` claimed all 4 command-registration
sites byte-stable at v1.95.0 while being wrong about 4 of 4 — and contradicted its own correct table
two paragraphs above.

### A rate limit is not a result

A fan-out that throttles returns **zero findings for the skills it never read**, and zero findings
is what a clean audit also returns. 3 of 13 agents hit an API rate limit; that silently meant 7 of
28 skills were unaudited. Retrying the same run (`resumeFromRunId`, so successes replay from cache)
recovered 13/13 and surfaced **15 more findings**, including all five stale seams above.

- Label a throttled batch UNAUDITED, never "clean", at the point of reporting.
- Retry in smaller waves before concluding anything; the throttle usually clears in minutes.
- If it re-limits, fall back to a CLI on a separate quota — it does not share the Anthropic limit.

### A fresh context is a DIFFERENT reviewer, not a BETTER one

Delegation is a review layer -- but the gain is ORTHOGONALITY, not quality, and treating it as a
quality upgrade is how an unverified claim ships. Both halves were measured in one fan-out
(4 sonnet agents, one backlog item each):

**What a fresh context caught that I could not.**
* Handed a brief naming `exit_on_native_multi_pattern_ceiling_refusal` as the site to fix, the Rust
  agent **refused the brief**: that helper is not the live path (a bare `tg search PAT --json` is
  single-pattern, and structured output always sets `allow_rg_fallback: false`, so it lands in a
  catch-all instead). It fixed both. Doing exactly what I asked would have left the reported command
  broken.
* Another agent found a latent `merge_runtime_routing` bug -- `sidecar_used` never propagated to the
  aggregate -- while writing a control arm that could not otherwise have worked.

**What a fresh context reproduced exactly.** That same agent changed a PRE-EXISTING test assertion
from `exit 0` to `exit 2` to match its intent, without verifying the fix fires on that path. It does
not; CI caught `assert 0 == 2` on 7 legs. That is precisely the mistake an external audit had caught
in MY control arms one day earlier.

**So apply the SAME verification bar to a subagent's work as to your own.** Its TDD claim needs the
same red-arm receipt. A chairman prompt should explicitly flag any agent claiming TDD without
quoting an actual failure message -- "I TDD'd it" is a claim, not evidence.

Corollary: **an agent that pushes back on your brief is the valuable one.** Write briefs that invite
it -- state the goal and the evidence, and say plainly that "the premise is false" or "already
fixed, here is the citation" is a valid result. An agent that only ever confirms is a mirror.

### An external audit's ID scheme can COLLIDE with your repo's PR numbers

An audit arrived citing `#858`-`#865` with a "next fix order". Every one of those numbers resolves
to a PR merged in this repo within the previous 24 hours -- `#860` is a CWE-88 argv fix, `#865` a
skills guard -- and none corresponded to the finding the audit meant. Following its fix order as PR
references would have sent a reader to seven unrelated merged PRs.

**Resolve an external report's identifiers against ITS OWN register before treating them as local.**
Same family as the task-ID/PR-number collision this repo already documents; the new part is that it
arrives from outside, where you did not choose the numbering.

The audit was otherwise well-calibrated -- it downgraded its own finding HIGH->LOW in Wave-2, which
is what makes the rest of its severities worth trusting. **Severity discipline runs both ways: do
not inflate a LOW to look thorough, and note when a reporter deflates their own.**

### Review layers are ORTHOGONAL, not redundant -- each is blind to a class the others catch

The strongest receipt in this campaign. ONE feature (the defaulted-scope search note) went through
four independent review layers. They found **12 defects with essentially zero overlap**:

| layer | what only IT could catch | found |
|---|---|---|
| **plan audit** (reads intent, pre-code) | two acceptance criteria no state satisfies together; a severity self-downgrade | 5 |
| **external code audit** (reads the diff cold) | three "control arms" that still PASS with the fix reverted; a false-positive note for filter-scoped searches; a `--quiet` contract change | 4 |
| **CI** (runs other platforms) | `--stats` takes a DIFFERENT dispatch route on Windows vs Linux; a verbatim string pin in a test never opened | 2 |
| **live dogfood** (runs the real product) | a THIRD dispatch route (`--json`) neither earlier fix reached | 1 |

None was reachable from another layer's vantage point. A plan audit cannot know Windows routes
differently; CI cannot know a criterion contradicts another in prose; a local suite cannot know the
shipped binary takes a third path.

**Skipping a layer does not cost a FRACTION of the defects -- it costs a CATEGORY.** Budget the
layers, not the individual reviews.

### One symptom, reported repeatedly, can be N DIFFERENT bugs

A live dogfood reported "bare `tg search` is silent on zero results" across **four consecutive
releases**. It read as one stubborn bug and a series of inadequate fixes. It was **three distinct
dispatch routes**:

```
bare text                 -> bootstrap rg passthrough        #857
--ast/--rank/--semantic   -> Python CLI is_empty branch      #862
--json                    -> bootstrap native delegation     #862 (later)
```

Every fix was correct and each looked like it closed the feature, because the reporter's next
invocation took a different route. There is no single chokepoint.

**When a symptom survives a fix you verified, do not assume the fix was wrong -- enumerate the
ROUTES that reach the symptom and find which one the reporter took.** Tell: your repro passes and
theirs fails with no environmental difference. Trace `sys.exit` and compare the exit LINE.

### A control arm that survives the revert is not a control arm

An external audit found **three of four** control arms I had written still passed with the fix
reverted -- they exercised pre-existing helpers rather than the new behaviour. They looked like
rigour and tested nothing about the change.

```bash
git diff origin/main -- <file> > /tmp/fix.patch
git apply -R /tmp/fix.patch && pytest <new-tests>   # MUST fail
git apply    /tmp/fix.patch && pytest <new-tests>   # MUST pass
```

Related: **before changing any user-facing string, grep every test and doc for it** -- a verbatim
prose pin in a file you never open will red the build. When fixing such a pin, assert on SUBSTANCE
rather than re-pinning the new sentence, or you have only relocated the tripwire.

### A control that moves the WRONG variable falsely EXONERATES the right hypothesis (2026-07-31, #868)

The law above catches a control that cannot fail. This is its mirror image and it is more
dangerous, because it produces a confident *negative* that closes the investigation: a control that
runs cleanly, discriminates properly, and moves a variable **adjacent to** the one that matters.

`#868` sat RED for days with the cause recorded as UNKNOWN and two hypotheses "falsified by
controls". The live hypothesis was *native-extension presence changes the search dispatch route*.
The control ran a full `uv sync`, confirmed **`rust_core` PRESENT**, re-ran the test, watched it
pass -- and killed the hypothesis.

`rust_core` is the Python **extension module**. The dispatch gate is
`resolve_native_tg_binary()`, which looks for the compiled **`tg` binary**. Two different
artifacts, adjacent names, and the hypothesis was right the whole time:

```
main.py:7521   _warn_unavailable_gpu_device_ids(...)      <- the warning CI showed, fires here
main.py:7862   native_tg_binary = resolve_native_tg_binary()
main.py:7877   sys.exit(_delegate_to_native_tg_search(...))   <- EXITS HERE
main.py:8408   <the new exit-code rule>                        <- ~530 lines later, unreachable
```

The real control, one variable, everything else byte-identical:

```
ARM A  resolve_native_tg_binary() -> None       => Python route     => exit 2  (test passes)
ARM B  resolve_native_tg_binary() -> Path(...)  => delegation route => exit 0  (reproduces CI
                                                    byte-for-byte, warning and all)
```

**Name the variable as the SYMBOL the code branches on, not as the capability you believe it
stands for.** "Is the native stuff installed" is a story; `resolve_native_tg_binary()` is a call
site. Write the arm as *"I set `<symbol>` to `<value>`"* and the substitution becomes impossible to
make silently.

Two corollaries, both cheap:

- **A negative control earns its authority by reproducing the failure in one arm.** ARM B here did
  not merely differ -- it produced CI's exact exit code and exact stdout. A control that only shows
  "still passes" has ruled out nothing; it has shown that whatever you changed was not it.
- **An ambient dependency in a shared fixture is a hidden arm.** `_patch_cli_dependencies` patches
  `Pipeline`, `DirectoryScanner` and `RipgrepBackend.is_available`, but not
  `resolve_native_tg_binary` -- so every test using it silently takes a different route on a dev box
  than in CI. When local and CI disagree, **diff the FIXTURE's coverage against the code path**, not
  just the platform.

**⚠ CORRECTION TO THIS SECTION, SAME DAY -- AND THE CORRECTION IS THE SHARPER LESSON.** The text
above says "the hypothesis was right the whole time". That is NOT established, and writing it as
though it were repeats the very error the section is about, one level up.

ARM B proves the mechanism is SUFFICIENT to produce CI's output. It does not prove that mechanism
is the one FIRING. Counter-evidence found afterwards: `.github/workflows/ci.yml:688` states
`test-python` never builds `rust_core/target/release/tg`, and `resolve_native_tg_binary`
(`cli/runtime_paths.py:278`) needs either an in-tree build (absent) or a PATH binary that is not a
Python console shim. So it plausibly returns `None` on that job.

(A later structural finding cuts the other way and is also not a measurement:
`rust_core/Cargo.toml:58` declares `[[bin]] name = "tg"` in the SAME manifest maturin builds, so
`pip install -e` may produce that binary as a side effect. Two structural arguments pointing
opposite ways is exactly the state in which you stop arguing and measure --
`scripts/diagnose_gpu_delegation_route.py` does, with both controls.)

**A MECHANISM THAT REPRODUCES THE OUTPUT IS NOT PROOF IT IS THE OPERATIVE ONE.** Reproducing a
failure byte-for-byte feels like a root cause and is only a candidate; the discriminating question
is whether the variable you forced is the one the real environment sets. I dispatched a fix on this
and had to recall it. Say "sufficient" until you have measured "operative".

### A SOURCE-SCANNING census is satisfied by a COMMENT, and blind to POSITION (2026-07-31, #872)

Four findings from one adversarial gate, all against a census *I* wrote to close a class -- and the
census was the weakest artifact in the PR, precisely because it was the part everyone (me included)
treated as the proof rather than the claim.

**1. The better you document a guard, the less it is checked.** The census asserted the literal
`"--"` appeared in each builder's body. Three of five members could have their real
`command.append("--")` DELETED and stay green, because the comment *explaining why the sentinel
matters* still contained the string. This is the mirror of the quoting-vs-asserting trap: there,
prose containing a forbidden pattern caused a FALSE POSITIVE; here, prose containing the required
pattern causes a FALSE NEGATIVE. Match AST nodes, or check behaviour -- never a bare substring in a
region that includes prose.

**2. Presence is a proxy. Ask what the actual PROPERTY is.** "Does `--` appear in this function"
is not "is `--` before every positional". The same PR shipped a sentinel sitting BETWEEN two
positionals -- present, and useless, since the first one was still parsed as a flag. The census
could not tell it from a correct one. **A proxy that cannot distinguish the fix from the bug is not
a check.**

**3. A census that omits the code its own PR edited was assembled from MEMORY, not derived.** The
list had 5 members; the plan that specified it had enumerated 10, and one of the omissions was a
builder that same PR had just added. Re-derive the population from the code at the moment you write
the list, then diff it against your own diff.

**4. A control arm that only matches a form the FORMATTER eliminates can never fire.** The
"sentinel must be unconditional" arm keyed on a regex matching single-line
`if cond: cmd.append("--")`. This repo's mandatory `ruff format --preview` expands that to two lines
on sight, so the arm could not fire on any code that passes the gate. **Run your control arm's
pattern against formatted code before believing it** -- this is the setup-lies law applied to a
regex.

**And the justification for going source-based was itself false**: I claimed a behavioural test
"would skip itself" because these builders shell out. They do not -- every one is a pure
list-returning function or is capturable at its runner, and `test_native_argv_end_of_options.py`
had been calling one directly since #860. **Check whether the cheaper, stronger test is actually
blocked before settling for the weaker one.**

Corollary on granularity: **a function is not the unit; the artifact is.** Two argv builders lived
86 lines apart inside one function, and a whole-body string match let the first one's sentinel
"cover" the second one's bare positional. Enumerate the things being built, not the places they are
built in.

### The check and the defect AGREED with each other, so neither could catch the other (2026-08-01)

Six instances in one day, one shape: **the check was built from the same wrong model that produced
the defect, so the two were mutually consistent -- and mutual consistency reads as green.** Every
oracle form above asks "what would this check show if the thing were broken?"; this is the case
where the answer is "the same" BECAUSE check and defect share an author and a premise. The escape is
always a third thing neither of them controls: the real consumer, the seam the value crosses, the
real base commit, the measurement, the guard's actual input list.

**1. Suppressing the OUTPUT is not suppressing the ANSWER.** `-q` was added to
`RipgrepBackend._build_cmd` -- the shared argv builder where ~30 other flags live, so it looked like
the natural home. `_build_cmd` has FOUR consumers; only ONE streams. The other three PARSE rg's
stdout, and `-q` makes rg print nothing. Measured on the real binary:

    rg --count-matches needle f.txt -> "2"      with -q -> ""
    rg -l             needle f.txt -> "f.txt"   with -q -> ""
    rg --json         needle f.txt -> 5 lines   with -q -> 1

So `tg search -q --count` on a MATCHING file reported `total_matches=0`, exit 1 -- a false no-match
AND an exit-contract violation. Shipped in #876, fixed in #880. Before adding a flag to a SHARED
builder, enumerate its consumers and ask which of them CONSUME the thing the flag changes; a flag
that alters output belongs to the consumers that stream, not the ones that parse. (The same law
already binds when WIDENING a flag's meaning -- "grep its CONSUMERS" under Fail-Closed Guidance
below; this is it at authoring time.)

**2. My own test ASSERTED the bug.** The control arm required `-q` to appear ALONGSIDE
`--count`/`-l`, reasoning "rg accepts it and suppression wins on stdout". True of rg; irrelevant to
tg, which CONSUMES that stdout. The revert discipline above ("a control arm that survives the
revert...") could not have caught this one: the arm DID track the change -- it pinned the change's
wrong premise. When writing a control arm, state what the CONSUMER does with the value, not what
the callee accepts. "The tool permits X" is not "our use of X is correct".

**3. A test built at the WRONG SEAM cannot see the defect.** That same test built its argv via
`_build_cmd` -- precisely where the flag was wrongly placed -- so it was structurally incapable of
showing the difference. Form 5's law one seam up: the probe shared the defect's location instead of
its topology. Retargeted to capture at `run_subprocess`, where the argv actually leaves, with an
`assert captured` arm so an inert capture FAILS rather than returning an empty value that passes
everything. Build the probe at the seam the VALUE CROSSES, not the seam that is convenient to call.

**4. A stale checkout makes a planner describe a SHIPPED defect as hypothetical.** A planning agent
stated its base as one commit while its citations matched another, and `origin/main` was 15 minutes
ahead of both. Its Item 1 "warned" about the exact trap that was already live on main; two of its
items were ALREADY SHIPPED. A plan must state its base commit AND prove it
(`git rev-parse origin/main`), and an auditor must re-derive that base rather than accept the
header. (Extends "Check Whether It Already Shipped" and Form 9's re-derive rule from tasks and
handed numbers to PLANS.)

**5. A writing seat reproduces the FLATTERING version when it lacks the measurement.** A fable@high
seat writing onboarding docs flattened the symbol-graph tiers into "10 languages, uniform depth" --
the exact claim `docs/BACKLOG.md` forbids in as many words, and the exact fact MEASURED hours
earlier -- 5 parser-backed (go/js/py/rust/ts), 5 foundational defs+imports-only
(c/cpp/c#/java/php), derived by ASKING THE PRODUCT (`repo_map._symbol_navigation_descriptor()`),
not by reading the registry field.

    **AND THIS SENTENCE SHIPPED THE WRONG NUMBER, WHICH IS THE POINT.** The first cut of this law
    said "3 parser-backed, 5 regex-fallback, 2 unresolved". I had hand-counted the
    `references_and_calls` field at each `register_language` call, found js/ts ABSENT rather than
    `None`, labelled them "unresolved -- confirm before relying", never confirmed, and then quoted
    the unconfirmed guess AS THE MEASUREMENT. The figures summed to 10, so it survived a sanity
    check. `.claude/skills/tensor-grep-enterprise-agent/SKILL.md` says **"Never hand-count this"**,
    gives the exact command, and records that this line was ALREADY WRONG TWICE (it once said 4/10
    with go demoted). Mine was the third. The command is one line:

    ```
    python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import repo_map as r;print(r._symbol_navigation_descriptor())"
    ```

    **A law that cites a number must cite the DERIVATION, not the number.** Caught by an
    independent audit that ran the command instead of reading the sentence -- in a file that is
    trusted precisely because people do not re-derive it. The seat did not have that measurement in
context. Hand a writing seat the MEASUREMENTS, not just the sources: absent a number, a capable
writer produces the plausible, tidier version -- and it reads as authoritative.

**6. A doc claimed a guard covered it; the guard reads TWO OTHER FILES.** The same doc asserted
`tests/unit/test_skill_index_sync.py` kept its skill table in sync. That test reads exactly
`AGENTS.md` and `CLAUDE.md` (:22-23) and had never heard of the doc -- which was already FIVE
skills short of the 28 on disk, missing `tensor-grep` itself. Before citing a guard as covering
your artifact, open the guard and read WHICH FILES it consumes; a guard is scoped to its inputs,
and being adjacent to one is not being covered by it. (The Skills section already records one
artifact invisible to this same test for the same reason: `skill_rules.json`, which has no
`SKILL.md`.)

### Building ONE checker produced THREE wrong readings, and an extreme rate is the tell (2026-08-01)

The section above is about a check and a defect agreeing. This is its acquisition-side twin: an
instrument that is simply **aimed wrong**, which yields a confident number rather than an error.
Three readings in a row while building a single skill-citation checker
(`tests/unit/test_skill_library_drift.py`), each individually plausible, each caught only because a
control arm ran first:

| reading | cause | what would have been reported |
|---|---|---|
| **100%** of citations broken | the probe walked up looking for a `.claude` marker and found the **user home** (`C:\Users\<me>\.claude`), which also has one | "92 broken citations" — in a directory that is not this repo |
| **60.5%** broken | it resolved only repo-relative paths; skills also cite by bare basename and partial suffix | "a library 60% rotten" — the exact false-positive flood that drowned an earlier auditor |
| **100%** ambiguous | a filesystem walk also enumerated 6 stale agent worktrees and 20 checkpoint snapshots, each a full source tree, so every citation matched 7–21 paths | "clean" — it checked **nothing** and said so as success |

**A rate at 0% or 100% is a property of the instrument far more often than of the subject.** Real
populations are lumpy. Before reporting either extreme, ask what would have to be true of the world
for it to be genuine, and check that instead.

**And the control fixture must itself be unambiguous.** The first known-good arm cited
`pyproject.toml:1` — a basename this repo has several of, so it resolved to nothing and the arm read
`0/0/0`, which is byte-identical to a dead checker. A control that cannot pass proves as little as
one that cannot fail.

**Aim at what CI sees.** The fix for the third reading was to resolve against `git ls-files` rather
than the filesystem. Untracked litter is invisible to CI and to reviewers, and it silently changed
the answer.

### A gate that fires at EVERY historical revision is guarding a shape the repo never had

Form 1 says run every new ratchet against the pre-fix revision. This is the failure that rule
catches when the ratchet is *wrong*, and it is easy to miss because the ratchet looks vindicated.

I read `**28 skills**` beside 29 folders on disk and called it live shipped drift. Then I ran the
proposed count gate across history and it fired at **all three** revisions checked — 20-vs-21,
26-vs-27, 27-vs-28. A defect introduced at some commit does not exist before that commit; **a
constant verdict across arms that should differ is a broken check**, exactly as a constant verdict
across a treatment and control is.

The number was correct. The sentence *defines* what it counts —
"(`.claude/skills/tensor-grep-*` + `code-search-and-retrieval-reference`, **N skills**)" — which
deliberately excludes the bare `tensor-grep` usage skill listed on its own line above. Under that
definition history reads 20/20, 26/26, 27/27, silent everywhere. **Read the DEFINITION beside a
number before calling it wrong**, and had this shipped it would have forced someone to "fix" a
correct doc into a wrong one.

**The real drift of this class was one section away, and BOTH halves of the two-file edit were wrong
in OPPOSITE directions.** `AGENTS.md`'s header said "nine forms" while the section enumerates Form 1
through Form 10 — stale for four days. `tensor-grep-validation-and-qa`, which had the count right
and *documented the miscount in prose*, misdated forms 8–9 to 2026-07-27 when Form 8's own text
reads 2026-07-26. Each doc was half correct, so **reading either one alone confirmed it**; only the
`**Form N —**` headings settle it. Both prose counts are now derived from those headings and gated
by `test_skill_library_drift.py`, because "re-derive the number when you add one" was already
written down, agreed with, and half-applied.

### A LONG DOCUMENT CONTRADICTS ITSELF, and the reader believes whichever half they reach first

The 2026-08-01 sweep of all 28 skills found **six documents holding both a claim and its refutation
at the same time**. Not one of them was flagged by any gate, because every gate this repo owns
compares a document to the CODE — nothing compares a document to ITSELF.

| document | what one part said | what another part of the SAME file said |
|---|---|---|
| `tensor-grep-run-and-operate` | §3: "`defs`/`source` do **not**" take `--deadline` | §12's table lists both as taking it, and the pitfall table at :745 warns *"don't trust a stale 'these don't take it' claim"* |
| `AGENTS.md` (never-re-stamp) | "adrift ... **all five of them**" | the table beneath it listed **four**, and the omitted one was the still-stale one |
| `tensor-grep-add-language` | "8 call sites" | its own "Current status" section, two paragraphs above: **10** |
| `tensor-grep-diagnostics-and-tooling` | cited `run_benchmarks.py:212-243` | its own provenance log had `:194-225`, correct |
| `tensor-grep-large-repo-scale-campaign` | Phase 0/1: blame "the still-open #390 daemon-path gap" | its own §2 documents #390 as **CLOSED** |
| `tensor-grep-validation-and-qa` + `AGENTS.md` | the skill had the oracle COUNT right and the DATES wrong | AGENTS.md had the dates right and the count wrong — each half correct, and reading either alone confirmed it |

Two of those, `architecture-contract` and `code-search-reference`, additionally cited *one* line
number for *two different functions*, and cited the same symbol at two different wrong lines.

**Why this shape is dangerous and hard to see.** A contradiction is invisible to the author, who
holds one mental model and reads only the part expressing it. It is invisible to grep, which
matches a string without knowing another string disagrees. And it is invisible to a reader, because
prose does not announce that it is in an argument — the reader resolves it by whichever half they
reached first, silently and with full confidence. **A file that says X in §3 and not-X in §12 is
worse than a file that is simply wrong**, because it will confirm whatever the reader already
believed and produce two people who cannot reproduce each other's result.

**What to do about it.** When you correct a fact, grep the WHOLE document for the claim you are
changing, not just the line you noticed — the correction and the error live in different sections by
construction, since a document long enough to contradict itself is long enough that you edited only
one place. Then prefer a DERIVATION over an assertion (`tg defs --help | grep deadline`), because
two derivations cannot disagree while two sentences can. And treat a pitfall table warning against a
belief as a strong hint that the belief is asserted elsewhere in the same file — in this sweep, it
was.

### GREP IS AN INSTRUMENT, and mine was wrong four times in one session

Four probes, four believable numbers, four different causes — three of them produced while auditing
for exactly this class of failure:

| probe | returned | why it was wrong |
|---|---|---|
| `grep -ic "check and the bug"` on this file | **0** | the doc says "check and the **defect**" — a PARAPHRASE miss. Four of seven lesson-capture probes read "absent"; all four were present in different words |
| `grep -cE "was [0-9]+ ?->"` for repair receipts | **0** | the file's format is ``was `:1444`, now `:1466` `` — a FORMAT assumption. I nearly rejected a correct 38-anchor repair on it |
| `grep -coE 'was \`:[0-9]+\`'` (the "fix" for the above) | **0** | GNU grep ERE treats `` \` `` as a **start-of-buffer anchor**, not a literal backtick — a REGEX-DIALECT trap that can never match anything |
| `grep -ci "byte-stable"` before vs after a cleanup | **2 → 3** | it counted WARNINGS ABOUT the phrase as instances OF it, so a correct removal read as an increase |

Re-counting in Python settled it: 34 receipts, 78 grep instructions, 12 anchors verified unchanged.
**The agent's self-report was accurate; my verification was broken three times running.**

**A grep zero is UNRESOLVED, never ABSENT.** Grep locates candidates; only reading adjudicates.
Before believing a count, confirm the pattern matches ONE known-present instance — the same positive
control any probe needs. Grep cannot distinguish an assertion from a sentence about that assertion
(the source-census law below), a paraphrase from a gap, or your regex dialect from the one you meant.
When a count disagrees with a careful reader's report, suspect the pattern first.

### A checker that cannot tell a PRODUCER from a PRESENTER reports correct code as broken

A class ratchet flagged 9 functions as "reads incompleteness but never discloses". **All 9 were
false positives**: its disclosure list held only helper names while real emitters use literal banner
text (`PARTIAL:`, `INCOMPLETE`), and its read-matcher matched `payload["partial"]` -- which also
matches the ASSIGNMENT `payload["partial"] = True`, flagging a payload BUILDER whose whole job is to
stamp the field.

**Triage every candidate before reporting any.** Shipping those 9 would have been 9 false P0s --
worse than the gap, because it teaches readers to ignore the tool.

### Your own PLAN is the least-audited artifact you produce

Code gets tests. PRs get review. A plan gets *written, and then followed* — and its errors
propagate into every item built from it. On 2026-07-29 a plan of mine went through an adversarial
audit before any code was written. It had file:line citations, a security classification, and
acceptance tests with control arms — every outward sign of rigour. It also contained **five
separate errors**, none of which the ceremony caught:

| what I wrote | what was true |
|---|---|
| give zero-config `--claim` a stable per-checkout id | **silently re-breaks #845** — `ledger_store.py:582-586` suppresses when two claims share a non-sentinel id, so two agents in one checkout would drop each other's overlaps |
| B2 is "not the CWE-88 class, CLI self-argv only" | `AGENTS.md` defines that class with **no CLI carve-out** and names this exact builder as tracked sweep work — the downgrade licensed skipping the security gate |
| A1b acceptance: "clean against HEAD" | already satisfied by my OWN earlier fix, so the arm could not discriminate |
| B2 control arm: "argv byte-identical" | wrong invariant — 5 legitimate test updates would have read as a regression |
| "check the MCP surface's exit code" | **category error**: MCP returns payloads, not exit codes |

**THE MECHANICAL TELL, and it is checkable.** The #845 error was detectable without any domain
knowledge: the plan listed *"stable across invocations in one checkout"* AND *"the #845
self-suppression survives"* as acceptance criteria **in the same document**, and no state satisfies
both whenever two agents share a checkout. Before shipping a plan, take its acceptance criteria
pairwise and ask: *is there a state that satisfies both?* Contradictory criteria are the cheapest
plan defect to find and the most expensive to discover during implementation.

Corollaries:
- **A plan that recommends a fix to a bug YOU shipped recently deserves extra suspicion** — you are
  reasoning from the mental model that produced the bug.
- **Never downgrade a severity class in your own plan.** If the repo's taxonomy names the class,
  the taxonomy wins; a self-assessed downgrade is how a mandatory gate gets skipped.
- **An acceptance test that already passes on HEAD is not an acceptance test.** Ask what state
  would make it fail; if the answer is "none, given work already merged", it is a tautology.

### Consensus is not verification — correlated hallucination, measured

In the same audit, **2 of 3 independent lenses agreed on the WRONG answer** for the ledger-identity
fork, and one dissented. Majority would have shipped the regression. Only re-deriving from source
(`ledger_store.py:582-586`) settled it.

- Never promote a claim because several reviewers said it. Promote it because it carries a citation
  you checked.
- An all-one-model council has elevated correlated risk by construction — say so in the synthesis,
  and treat unanimity on an un-cited claim as a smell rather than a green light.
- Surface the minority view even when not promoting it.

### A CLI seat that answers your smoke test can still fail the real work

The local thinktank passed its pong gate on both seats, then produced nothing usable: agy returned
**0 bytes**, codex returned 179KB of file exploration and **no verdict** — its only `RECOMMENDED:`
line was *my own question template echoed back*. Grepping with `head -1` instead of `tail -1` would
have reported a fabricated approval of my own plan.

- **The gate was easier than the workload.** A pong proves auth, not capacity for a long task.
- Treat a no-verdict seat as FAILED, not pending; sweep its wedged processes and move on.
- Before trusting any council output, confirm the verdict line is not your own prompt echoed back.

### Fixing the instance is not fixing the class — in DOCS too

`AGENTS.md` already carries "model the class, don't enumerate the cases" for code. It applies
verbatim to documentation. A dogfood reported that one skill wrongly said ledger Slice 2 was "still
literal-path-rooted"; that skill was corrected. **A grep of the library found the identical false
claim in three more skills** — and the class grep beat a 12-agent parallel audit, which found only
two of the three.

The dangerous shape is specifically **a doc asserting something is BROKEN when it is fixed**: a
reader hits the symptom, files it as expected behaviour, and works around a feature that works. When
a dogfood falsifies one doc claim, grep every doc for that claim before closing it.

**MAINTENANCE: this family is MIRRORED, so adding a form is a TWO-FILE EDIT, always.** This section
is canonical; `tensor-grep-validation-and-qa`'s Part 0 carries the same family for cheap-session
readers. Grep BOTH files for the next number before assigning it, and update the mirror in the same
commit. Miss it and you get two different lessons sharing one number — this file's Form 8 (the SPLIT
ORACLE) briefly collided with a different Form 8 added to the skill, and the skill was simultaneously
missing the real Form 8 entirely, so its readers had 7 of 8 and no way to know. Two defects from one
one-file edit. The rule generalises past this family to any numbered list split across two docs.

**Running the probe: the LOCATION trap.** A perturbation proves nothing if the thing you perturbed
survives elsewhere. Verifying the `truncation_cause` doc ratchet, the first probe removed ONE
occurrence of `unreadable-path` from `docs/CONTRACTS.md` and the test still passed — which reads as
"toothless ratchet". It was not: the string appears twice, and the check is a substring scan over the
whole file. Removing EVERY occurrence failed the test correctly. **Before concluding a guard is
broken, confirm your perturbation actually removed the property it guards** — count the occurrences
first. This is the setup-not-assertion failure again (a check that "passes in both arms" was really a
probe that never created a second arm).

## Fail-Closed Guidance Must Be An Allow-List, Not A Deny-List (2026-07-25, #282)

A trust check written as "confirm it is NOT *X*" fails open the moment a value appears that the author
did not anticipate. Receipt: the `incomplete_reason_class` paragraph in `docs/CONTRACTS.md` told an agent
to confirm `routing_backend` is **not** `"RustCoreBackend"` before trusting the field's ABSENCE as proof
of a complete scan. Two things were wrong at once. The constant was wrong — measurement on the shipped
v1.98.11 asset shows `--json` and `--cpu --json` both emit `"NativeCpuBackend"` (`routing_reason`
`json_output` / `force_cpu`), corroborated by the `NativeCpuBackend` row in `docs/routing_policy.md`;
`RustCoreBackend` is the PyO3 backend in `backends/rust_backend.py` and is not what a user sees there.
And the SHAPE was wrong — even with the right constant, an agent meeting any third backend name would
conclude "not the native engine" and trust an absence that proves nothing. **A deny-list in a fail-closed
paragraph fails open by construction.** Fixed in `d35d243` as a positive allow-list: absence is
trustworthy ONLY on the Python `CPUBackend` route or the `rg`-backend route; every other value means it
proves nothing.

Generalise: this is the documentation twin of the Backend Fail-Closed Contract. Whenever prose or code
decides *"is it safe to trust this signal?"*, enumerate the SAFE cases and reject everything else. And
when you widen what an existing flag MEANS, grep its CONSUMERS — a comment stating the old assumption is
the tell that a downstream reader is about to be wrong (receipt: `mcp_server.py:4794` ORs
`scanner.scan_truncated` into a `max_repo_files`-shaped payload under a comment explaining that the flag
means a *budget cap*; #276 slice 1 made it also mean "unreadable path", which no budget increase fixes).

## Slice By What CI Can Actually Verify (2026-07-25, #280)

CPU-SAFE forbids compiling, so **CI is the only oracle for Rust** — which makes change SIZE a
correctness concern, not a style preference. A large native change landed blind burns CI cycles and
arrives unverifiable; the discipline is to ship the portion whose correctness is provable now and defer
the rest as its own slice.

Receipt: #280 wanted stderr parity AND exit-2 AND a JSON envelope marker on the native engine. The
stderr half is a self-contained 4-line change per site, `rustfmt --check`-clean locally, mirroring a
sibling that already exists. The other half needs an error count threaded through `SearchStats` into
`emit_json_matches` and the process exit code, changing a signature used at three call sites plus a
test. Those shipped separately.

**The rule that makes this honest rather than lazy: leave the gap AT THE CODE SITE, not only in the
tracker.** Both collectors carry a comment naming exactly what is still missing (exit code, envelope
field) and why. A partial fix with no marker at the seam reads as complete to the next reader — that is
the same fail-open-by-inference defect as the deny-list above, wearing different clothes.

## Model The Class, Don't Enumerate The Cases (2026-07-25, #745/#749/#272)

When round N+1 of review keeps finding *a new instance of the same class*, the fix is a **model of the
class**, not another reviewer. Five gate rounds on #745 each surfaced one more argv form nobody had thought
of (`-u` ungated → `-f`/`-e<attached>` → `-ieneedle` mid-bundle → PATH-before-flag ordering →
offset-vs-consumption). Round six replaced reviewer imagination with `.claude/rg_argv_differential_fuzz.py`:
an INDEPENDENT model of ripgrep's argv grammar, diffed against tg's parser over 70,040 cases in ~3s, wired
into CI's release-blocking `static-analysis` step.

A modelled gate must itself be proven non-decorative: reverting one line surfaced 72 distinct shapes (exit
1), mutation-killed 6/6, `--seed` reproducible, and its oracle validated against real `rg --debug`
path-counts 301/301. **A green gate that cannot fail is worse than no gate.**

**Know the model's hard limit.** A cross-tool differential bounds itself at the INTERSECTION of the two
tools' surfaces — an rg-grammar model can never cover tg-only flags, which is exactly how #272
(`--format`/`--lang` missing from `_SEARCH_FLAGS_WITH_VALUES`) stayed invisible. Anything outside the
intersection needs its own invariant: for #272 a registry-parity test asserting every `--x=` prefix has
`--x` registered as value-taking; for #749 a CI-coverage invariant. Prefer an invariant over an enumeration
every time — an enumeration is correct when written and silently incomplete on the next addition.

**Second instance of the same law: skill-library `file:line` anchors (2026-07-27, #334).** The skills cite
source anchors so a claim can be jumped to. `repo_map.py` is past 19,000 lines and `main.py` past 17,000, so
those anchors rot continuously, and **five** consecutive maintenance passes re-stamped them by hand — each
shipping numbers that were already wrong, including the 2026-07-27 audit whose own "corrections" had been
computed against a worktree 28 commits behind `origin/main`. Same tell, same fix: `.claude/skill_anchor_audit.py`
resolves every cited path, flags any line past EOF, and — for a citation naming a backticked symbol — reports
where that symbol is actually **defined**. It found **92** stale anchors against the ~15 the human audit had;
88 were unambiguous enough to fix mechanically.

Two lessons from building it, both from the control arm rather than from review:
- Its symbol tier was **structurally incapable of firing** on the real corpus at first, because a citation
  sits inside its own code span so the preceding text ends with a backtick that the pattern rejected. It
  looked healthy and reported nothing. **Prove a new tier can fire before believing a clean run.**
- Matching a symbol *anywhere* in the file made `tg`, `find`, `list` and `None` "move" constantly — 114
  findings, mostly noise. Anchoring to **definition sites** cut it to 92 real ones. A checker that cries wolf
  gets switched off, and a switched-off gate is worse than none, which is also why this is a maintenance
  command rather than a pytest: pinning these numbers in CI would red every PR that adds a line to `main.py`.

## A Field That Is `""` Instead Of `null` Defeats Your Default (2026-07-28, four instances in one session)

`gh`'s check API returns `conclusion: ""` — an EMPTY STRING, not `null` — while a `CheckRun` is still
running. jq's `//` substitutes only for `null` and `false`, so the idiomatic
`.conclusion // "PENDING"` **never fires**, every in-progress check reads as resolved, and a merge
gate reports **0 pending while jobs are running**. It is the most dangerous shape a probe can have:
it fails toward "everything is fine".

It landed four times in one session, in four different probes, including twice AFTER the warning had
been written into two cron definitions — the second of those in an ad-hoc `gh run view --json jobs
--jq 'select(.conclusion==null)'` that returned an empty list for a run with two jobs still going.

- **For a `CheckRun`, branch on `.status == "COMPLETED"`. For a `StatusContext`, branch on `.state`.**
  The rollup mixes both node types; `__typename` tells them apart.
- **Guard the TOTAL too.** Jobs register progressively, so a freshly-pushed PR legitimately shows
  `pending=2, total=11` when the real matrix is ~48. "Almost nothing pending" over a partial roster is
  the same false green in a different coat.
- **Give the probe a control.** Run it against something you KNOW is in flight and confirm it returns
  non-zero before you trust a zero from it. This is the workspace's "a ZERO means measured-nothing or
  DID-NOT-MEASURE" law applied to a merge gate — the place a false zero is most expensive.
- Print the raw tally beside the verdict. Every one of the four was caught that way and by nothing else.

## A Ratchet That Narrows Still Reads Green (2026-07-28)

A guard that silently starts covering LESS is worse than no guard, because it keeps reporting
success. Two instances, same session, same file:

- A gate-detector matched `if _scan_incomplete(...)` and then required `Exit(2)` within **4 lines** of
  the `if`. Both `agent` gates put their `raise` 5-7 lines down, so **both silently dropped out of
  coverage**. The only reason it surfaced: the control arm named **9** gates where the previous form
  named **12**. Nothing in the passing run said anything was missing.
- The same detector had earlier been taught to skip its own helper BY NAME. That does not scale — the
  next non-gate use of the predicate arrived from a different PR and was flagged as a missing
  disclosure inside the one function whose entire job is producing that disclosure.

So: **define the thing you are counting by BEHAVIOUR, not by name or proximity** (a gate is a site
that exits 2, wherever it lives), and **assert coverage BY NAME as well as by count** — a count floor
tells you something vanished but never *which*, and the members that drop out are exactly the
irregular ones the guard existed for. Where an exemption is genuinely right, NAME it with its reason
(`inventory` discloses via `render_inventory_text`, one call away in another module) so the next
reader does not "fix" it into disclosing twice.

## A Probe That Cannot See Past Its File Reports A Delegating Caller As Silent (2026-07-28)

Auditing which commands disclose an incomplete scan, a script scanned `cli/main.py` and reported
`inventory` as having NO disclosure. It has a good one — `render_inventory_text` ends with
`[!] truncated at max_files=N (cause=X); counts are a floor, not complete.` — and it lives one call
away in `cli/inventory.py`. Acting on the report added a second, duplicate banner.

The audit only became correct when it was re-run asking a **different question**: *which sites
delegate their rendering elsewhere?* — which returned `inventory`, and only `inventory`. When a
source-level census answers "does X do Y", it is really answering "does X do Y **in this file**".
Before trusting it, ask what it would say about a caller that delegates.

## A Disclosure Must Precede The Data It Qualifies (2026-07-27, #329)

Emitting the incompleteness signal is only half the contract — **where** it lands decides whether it is
read. A trailing `warning: INCOMPLETE RESULT: ...` line is the easiest thing to append and the most
ignored: the consumer (human or model) treats the prefix as the document and a final line as a footnote,
so a caller-set truncated at a file cap still gets trusted as exhaustive. The rule is therefore that a
truncation warning goes **above** the payload and advisory commentary (the zero-callers "not dead code"
caveat, whose result is COMPLETE) goes **below** it. The asymmetry is the rule, not an inconsistency.

**That is the rule, not yet the state of the CLI.** Three emitters are wired to it today
(`_emit_symbol_command_result`, the `blast-radius` counts block, `_render_blast_radius_mermaid`).
Measured against the rest: `code-map`, `route-test`, `session open` and `agent` still TRAIL their
disclosure, and `map`, `context`, `context-render`, `edit-plan`, `blast-radius-render` and
`blast-radius-plan` exit `2` while saying **nothing** in text at all — the ABSENT case, which is worse
than a mispositioned one. Worst of the set: `scan`, a SECURITY ruleset, printed `Scan completed.
total_matches=N` and exit `0` over files it could not open. Write the scope down when you state this
rule; the first version of this section said `tg`'s text emitters "therefore" do it, which reads as a
completeness claim about a CLI where most of them do not.

Three consequences when you touch any disclosure surface:

- **Position is part of the contract; test it, don't test presence.** `assert "warning:" in out` passes
  identically before and after the fix — oracle Form 7. Pin `out.index(marker) < out.index(first_payload_line)`
  with a premise assertion that the payload line was actually emitted, so an inert renderer cannot make the
  ordering comparison vacuously true.
- **Define the ordering once and share it.** `_completeness_caveat_lines` (`cli/main.py`) returns
  `(leading_banner, trailing_note)` for every text emitter — the symbol commands, `blast-radius`, and the
  `--mermaid` renderer — so they cannot drift into different orderings. JSON output is deliberately
  unaffected: `caveat` is a field there, and field order carries no reading bias.
- **Enumerate the command's emitters, not the ones you were shown.** *This section's own first cut
  missed one.* `blast-radius` has THREE emitters, and `_render_blast_radius_mermaid` — the
  **agent-facing** one — kept appending its disclosure after every graph node. A comment three lines
  from the edited site even named it (*"the mermaid renderer also reads payload.result\_incomplete"*),
  which is the tell: knowing a twin exists is not crossing to it. The miss also carried two defects that
  a shared helper makes structurally impossible, and a hand-written literal invites: it said `note:` for a
  **truncation** (inverting the very warning-vs-advisory split defined one function above), and it hardcoded
  *"raise `--max-callers`/`--max-files`"* for **every** cause — naming the only two knobs that cannot lift a
  `--max-repo-files` scan cap. Wrong-knob remediation advice is the failure #762 fixed on the MCP surface;
  sourcing the text from `_scan_truncation_warning` retires all three at once. Before calling a disclosure
  fix done, grep the command for every `typer.echo` / renderer that can reach stdout and classify each.

## Backend Fail-Closed Contract

Every `ComputeBackend` MUST raise `BackendExecutionError` on a real failure — never return a clean empty / `0-match` `SearchResult` (see `backends/base.py`), and never silently swap to a different engine that cannot preserve the requested semantics. The search loop catches `BackendExecutionError` to fall back **visibly** (e.g. to CPU); a swallowed failure or a silent engine swap reaches the user (or a coding agent) as a trustworthy "no matches" — the one failure a context tool cannot afford.

This contract is violated repeatedly. The recurring anti-pattern is a bare `except Exception:` that returns an empty result or falls through to a different engine. Instances fixed across audits: the Rust/PCRE2 bridge (ran `--pcre2` through the Python-regex engine), the ast-grep wrapper OOM mask (a killed subprocess read as a clean 0-match), the tree-sitter query swallow (invalid pattern → silent 0-match), and CyBERT's classify fallback (keyword-heuristic hits labeled as real model output). When a path CAN fall back to a different engine:

- **Fail closed** for any flag/contract the fallback cannot preserve (e.g. `--pcre2` through a non-PCRE2 engine): raise, do not swap.
- If a degraded fallback is legitimate (e.g. heuristic classification when the model is down), make the swap **visible**: set a `fallback_reason` (and a distinct `routing_reason`) on the `SearchResult` so JSON/CLI consumers can tell degraded output from real output. Never label heuristic output as model output.
- Validate an untrusted response shape before indexing (e.g. a model's class count vs a fixed label list) so a mismatch degrades gracefully instead of raising an uncaught `IndexError` that a broad `except` then swallows.

The same discipline applies beyond backends: any router/pipeline that can silently override an explicit user intent (e.g. an explicit `--gpu` request quietly routed to CPU) must instead raise `ConfigurationError` or emit a diagnostic. A systemic `SafeBackendMixin` + a fault-injection conformance CI gate (every registered backend must raise, not return empty, when its engine call fails) is the planned structural fix so this stops recurring one file at a time.

## AST Native/Wrapper Two-Engine Divergence (task #141)

`tg`'s AST surfaces (`tg run`, `tg scan`, the MCP `tg_ast_search` tool) can be served by two backends with two different, incompatible query DSLs: `AstGrepWrapperBackend` (`backends/ast_wrapper_backend.py`) shells out to the `ast-grep` binary and understands the full ast-grep pattern language, including metavariables (`$NAME`, `$$$ARGS`), selectors, and strictness options; `AstBackend` (`backends/ast_backend.py`) parses in-process via tree-sitter and understands only a narrow native query shape (a bare identifier, or an s-expression starting with `(`) — it has **no concept of ast-grep metavariables at all**. Given `$NAME` it cannot reproduce the wrapper's capture semantics.

This divergence is already fail-closed, at three verified sites (re-verified against `origin/main` `1135d30`; grep the symbol, not the line number, since these shift release to release — a regression test locks each one in, see below):

1. `Pipeline._supports_native_ast_pattern` (`core/pipeline.py:52-60`) — the shared classifier. Only a bare identifier (`re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pattern)`) or a pattern starting with `(` counts as native-shaped; anything containing `$` (or any other non-identifier character, or more than one token) returns `False`.
2. `Pipeline.__init__`'s AST branch (`core/pipeline.py:230-233`) — when `_supports_native_ast_pattern` is `False` and the ast-grep wrapper is unavailable, it raises `ConfigurationError` via `_raise_explicit_ast_configuration_error` instead of silently falling through to native tree-sitter.
3. `_select_ast_backend_for_pattern` (`cli/ast_workflows.py:928-1004`, the `tg run`/`tg scan` selector) — mirrors the same classification (`pattern_kind == "wrapper"`) and raises the identical `ConfigurationError` at line 990 when the wrapper is required but absent.
4. `tg_ast_search` (`cli/mcp_server.py:4630-4653`) wraps the `Pipeline(...)` construction in `try/except ConfigurationError` and converts it to the structured `{"error": {"code": "unavailable", ...}}` JSON shape instead of letting a raw exception escape as an unhandled FastMCP `ToolError`. Note: this call site never threads `query_pattern` into the `SearchConfig` it builds, so `_supports_native_ast_pattern` is unconditionally `False` there — every `tg_ast_search` pattern (metavariable-shaped or not) requires the wrapper at this construction step; native `AstBackend` is structurally unreachable through the MCP tool regardless of the caller's pattern.

Regression coverage locking this in: `tests/unit/test_pipeline.py` (`test_supports_native_ast_pattern_should_reject_ast_grep_metavariable_syntax`, `test_should_reject_ast_grep_metavariable_pattern_when_wrapper_is_unavailable`) and `tests/unit/test_ast_workflows.py` (`test_select_ast_backend_should_reject_ast_grep_metavariable_pattern_when_wrapper_is_unavailable`) each assert `ConfigurationError` for a genuine `$NAME`/`$$$ARGS` pattern with the wrapper unavailable — even with the native backend AVAILABLE, to prove its presence never lets a metavariable pattern silently mis-route. `tests/unit/test_mcp_server_search.py` (`test_tg_ast_search_fails_closed_for_metavariable_pattern_when_wrapper_unavailable`) drives the real `Pipeline` (not a mock) through `tg_ast_search` to prove the same refusal surfaces as the structured JSON error at the MCP boundary.

**The native-shaped-pattern fallback is deliberate, not a bug.** When ast-grep is absent but a pattern IS native-shaped (a bare identifier or an s-expression), both `Pipeline` and `_select_ast_backend_for_pattern` fall through to the native `AstBackend` instead of refusing — this is intentional so a CPU-only box without the `ast-grep` binary installed still gets *some* AST search capability rather than none. Do not "fix" this into a hard refusal; that would regress a deliberately-supported capability.

**Reconciling the two DSLs (native metavariable support, or making native the CPU-perf default) is task #141 and stays demand-gated** — it is a design pass, not a small change, and only worth doing once a concrete consumer needs native-tree-sitter performance for patterns the wrapper already serves correctly. See `docs/BACKLOG.md` for current status.

## Roadmap Sequencing (2026-07-02, GPU phase structure added 2026-07-14)

The GPU native-backend program runs a 3-phase sequence gated on evidence, not a blanket "hold until N CPU
wins ship" rule:

- **Phase 0 -- shipped, gated OFF by default.** The correctness taxonomy, the loud non-promotional CPU
  fallback, the `doctor`/proof fields, and (v1.75.1-v1.75.4, audit #171 P0-1..P0-5 -- see the Current
  Handoff addendum above) the WSL path-domain probe bridging, doctor probe failure taxonomy, honest
  `--gpu-device-ids` validation, `calibrate` remediation messaging, and the loud nvidia->cpu installer
  downgrade warning are all SHIPPED and locally correctness-proven (RTX 4070 / RTX 5070, 1GB/5GB). The
  native `cuda` Cargo feature only compiles into release assets when the repository variable
  `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` is explicitly set to `native-frontdoor-gpu`; the shipped
  default (`native-frontdoor`) never builds or ships a GPU asset, so Phase 0 landing is a code-complete,
  correctness-gated capability with zero public exposure until an operator opts in.
- **Phase 1 -- reversible flag-flip, not yet authorized.** Flipping the release variable to build and ship
  a GPU native asset is a reversible, single-variable change, but shipping the ASSET is not the same as
  PROMOTING it: no crossover has been proven (GPU remains slower than `rg` / `tg_cpu` for single-pattern
  search; see `docs/gpu_crossover.md`), and the public promotion gate
  (`.github/workflows/public-gpu-proof.yml`, dispatch-only) has not been run to a `public_gpu_proof =
  true` / `public_managed_promotion_ready = true` verdict -- the exact requirements are pinned in
  [docs/CONTRACTS.md](docs/CONTRACTS.md) (the "Public managed GPU promotion" bullets, currently around
  lines 80-82). Do not flip the variable to promote GPU as a default route until that gate passes.
  **2026-07-21 re-adjudication (B-GPU):** re-tested across 10MB-5GB corpora -- still **no crossover at
  any scale** (historical worst ~30-35x slower at 5GB; even the best-case 100-pattern fixed-string lane
  loses to fair-baseline `rg -F -e ...`), and the shipped `gpu_text_search_positions` kernel is a
  **position-parallel brute-force byte-compare, not a PFAC/Aho-Corasick automaton**
  (`docs/gpu_crossover.md:133-138` -- PFAC remains documented future work, not shipped code). Public
  CUDA-asset publishing is on a deliberate **HOLD** (CEO decision, #169); release checksums currently
  ship 3 CPU-only rows. Do not describe the shipped kernel as PFAC, and do not re-propose "just publish
  the GPU asset" without re-reading this verdict first.
- **Phase 2 -- self-hosted GPU CI runner, CEO-gated.** Proving Phase 1's crossover claim at 1GB/5GB scale
  in CI (rather than only on local RTX 4070/5070 dogfood boxes) requires a self-hosted GPU-capable runner
  wired into `public-gpu-proof.yml`. That is a real recurring infra cost and access-control surface, so
  provisioning it is explicitly CEO-gated, not an engineering-capacity decision.

The original CPU-only "3 wins before GPU advances" gate (2026-07-02) is superseded by this phase
structure, but its first win already shipped and validates the sequencing logic: **local hybrid semantic
search** (BM25 + CPU dense embeddings fused with RRF, no API key, no GPU) -- the #1 validated user ask --
shipped as `tg search --semantic` (`retrieval_dense.py` + `retrieval_fusion.py`, default-OFF, gated on
the `semantic` extra; see the `tensor-grep-semantic-search-campaign` skill). Reference architecture:
MinishLab `Semble` (tree-sitter chunking + `potion-code-16M` Model2Vec + BM25 + RRF, CPU-only, MIT). The
other two original CPU-only items -- `tg registration-check` productized as a first-class command, and a
Bloom-filter n-gram chunk prefilter for the slow non-literal-regex full-scan path in `rust_core` -- have
not shipped and remain live backlog items, independent of GPU phase gating.

Rationale (unchanged): the project's own docs place raw search speed (where GPU competes) in the
**parity tier, not the moat**; the heuristic auto-GPU route is effectively dead code whenever ripgrep is
installed (the common case). The moat is the **agent-native context layer** (`orient` / `callers` /
blast-radius / the token-efficient capsule), so engineering capacity funds that first. Explicit
`--gpu-device-ids` stays supported and must fail loud when it cannot be honored (see the Backend
Fail-Closed Contract).

## Security Hardening Patterns (Round-3 audit lens)

A round-3 security sweep (shipped v1.17.23–v1.17.25) fixed four recurring classes. Each is a **sweep target**, not a skill: current models already apply the fix when *writing fresh code* (baseline-tested), so these live here to be checked proactively — the bugs lived in already-committed code where no one re-verified. When you touch the named area, confirm the pattern holds.

- **Symlink-follow disclosure** (any tree walk or copy that snapshots/restores a user/repo tree). Following symlinks copies the *content* of out-of-root targets into the snapshot — and can re-materialize them on restore. Use `os.walk(root, followlinks=False)` + `shutil.copy2(src, dst, follow_symlinks=False)`. Fixed in `checkpoint_store.py` (`_filesystem_snapshot_entries` + all 3 copy sites).
- **Pre-auth unbounded read / no timeout** (any socket/pipe handler that reads *before* authenticating). Bound the read (`readline(max_bytes + 1)` + refuse over-cap) and set a socket timeout **before** the auth check, or an unauthenticated client exhausts memory or pins a worker thread. Fixed in `session_daemon.py` (`_read_bounded_request_line` + handler `timeout`).
- **Atomic-write permission window** (any temp-then-rename of a sensitive file, e.g. a token). Create the temp at the restrictive mode from byte one via `os.open(path, O_WRONLY | O_CREAT | O_EXCL, mode)` — never `write_text()`-then-`chmod`, which leaves a world-readable window; `O_EXCL` also refuses a pre-existing temp/symlink. Fixed in `session_store.py` (`_write_json_atomic`).
- **Native-argv flag injection** (CWE-88; the MCP-276 threat class — a *live* CVE family in MCP servers: CVE-2026-5058 aws-mcp-server, CVE-2026-23744, CVE-2026-30623 Anthropic MCP SDK). Any builder that appends a user/LLM-controlled value as a positional to a subprocess/native `tg`/`rg`/`git` command. A list-argv (`shell=False`) stops *shell* injection but **not** *flag* injection: a value beginning with `-` is parsed by the child's own option parser as a flag. Insert a `--` end-of-options sentinel **before** the user positionals. CAVEATS worth knowing: `--` protects only what comes *after* it (a user positional *before* `--` is still injectable); it does not gate `--flag=VALUE`; and not every binary honors it — **dogfood the real binary** (`tg search -- --weird` matches; `tg search --weird` errors). The three defenses layer — validate the value, list-argv, and `--` — and none alone is complete.

  **THE SWEEP IS NOW A TEST, NOT A SENTENCE, AND THAT CHANGE COST A LIVE HOLE.** This bullet used to
  end with *"**remaining tg sweep** (tracked): the other native-argv builders"*. Prose did not hold
  it. #860 fixed `cli/main.py::_build_native_tg_search_command` and the class was recorded closed —
  while `cli/agent_capsule.py::_agent_gpu_evidence` was still appending a **caller-supplied** path as
  a bare positional, found only by an independent plan review on 2026-07-31 (#872). Tracking a sweep
  by name in a doc means the next person must re-derive the population from memory, and they will
  get it wrong the same way. The population now lives in
  `tests/unit/test_argv_sentinel_covers_every_builder.py`, enumerated **by symbol**, with a
  blind-census arm: a symbol that stops resolving is an explicit FAILURE, never a skip.

  Two rules that fell out of it: the sentinel is **unconditional** at every site (an "only when the
  value starts with `-`" guard reads as equivalent and leaves the silent case open — and in
  `_agent_gpu_evidence` a WSL `wslpath` branch rewrites the path *after* the caller supplies it); and
  **uniformity is the security property** — the doctor GPU probe carries the sentinel although both
  its positionals are tg-generated, because a sweep whose members each carry a private risk
  assessment is a sweep nobody can check.

## EvidenceReceipt Signing (Ed25519)

`tg evidence emit` always attaches a keyless `receipt_sha256` integrity digest; `--sign` additionally Ed25519-signs it (`tg evidence verify` / `keygen` / `pubkey`), so a separate downstream consumer (e.g. gotcontext) can verify a receipt without ever holding a key that could forge one — the reason this uses an asymmetric algorithm rather than `tg audit`'s same-operator HMAC-SHA256 (`audit_manifest.py`). All crypto is isolated in `src/tensor_grep/cli/evidence_signing.py`. Two points worth knowing when touching this area:
- **S2 trust-bootstrap**: an embedded public key only proves internal self-consistency, never authenticity — `verify` always reports the signer's fingerprint (recomputed from the actual key bytes, never a claimed label) and only upgrades `key_trusted` to `True` against an out-of-band pinned `--trusted-key`/`TG_EVIDENCE_TRUSTED_KEYS`, compared with `hmac.compare_digest`. `--require-trusted` is the flag that fails `valid` closed on an unpinned key.
- **Fail-closed**: `--sign` with no resolvable key (or `cryptography` unavailable) is a non-zero exit with no receipt written — never a silent unsigned fallback (the `--pcre2` anti-pattern this contract exists to prevent). Full wire format + canonicalization rule: [docs/CONTRACTS.md](docs/CONTRACTS.md#8-evidencereceipt-signing-tg-evidence-emit---sign--tg-evidence-verify); design spec: `docs/plans/backlog-100/cluster-124-evidence-signing.md`.

## Skills

Three kinds of skills apply to this repo; load the relevant one before non-trivial work.

**User-level composition is not listed here.** Load `~/.claude/skills/skill-library-map/` (Cursor mirror: `~/.cursor/skills/skill-library-map/`) then 1-3 leaves. Plan vs answer-key vs execute vs verify is `compose-build-pipeline`, not a new skill.

- **Using `tg` itself** — `.claude/skills/tensor-grep/SKILL.md` (+ `REFERENCE.md`): the agent-usage skill for the command surface (`search`, `search --rank`, `orient`, `map`, `agent`, `session`, AST, blast-radius). Keep it in sync whenever commands/flags change.
- **Working ON `tg` (build + release discipline)** — reusable global skills at `~/.claude/skills/`:
  - `dogfood-the-shipped-artifact` — after a release, install the published wheel in clean Docker and run the REAL `tg` binary across every feature; never trust CliRunner (it bypasses the bootstrap front door). Harness: `scripts/dogfood/`.
  - `verify-plan-against-code` — before building an AI/subagent-drafted plan, verify every seam claim (file paths, the command/flag registration sites above, routing) against the real code with `file:line` citations; bake corrections in first.
  - `supply-chain-hardening` — before writing any download / extract / install / self-upgrade / toolchain-bootstrap code, apply the 5 checks (zip-slip guard, byte-capped/time-bound downloads, fail-closed checksum incl. detached helpers, `--locked` pinned CI tools, fail-closed unverified toolchains). Shipped patterns: #283/#284/#285/#287.
  - `worktree-fanout-verification-gate` — before integrating agent branches from a worktree fan-out: remove worktrees before checkout (`git worktree remove --force <path>` — else checkout is blocked and tests silently run main's code); re-run pytest/ruff/mypy in the real venv (worktrees have no `.venv`; agents' "tests pass" claims are hypotheses until then); run `ruff format --preview` on ALL agent-touched files (not only hand-fixed ones); and treat scoped-local-green as a hypothesis, not a merge signal.
  - `anti-hang-test-protocol` — hang-class test hygiene: wrap every test run in a shell timeout, and write the fix BEFORE the red-phase adversarial test (a ReDoS/deadlock red-test executed against un-fixed code IS the hang it is testing).
  - `instrumented-build-gate` — measure real demand before building a speculative feature.
  - `agent-liveness-probe` — before killing, restarting, or `TaskStop`-ing a background subagent that looks stalled, probe liveness via `SendMessage` rather than trusting output-file mtime/size (see A9 above).
  - `profile-guided-byte-identical-optimization` — find a lever on the shipped wheel + prove output
    byte-identical; the warm/cold measurement trap (see "Optimization Discipline" above).
  (the global-skill half of this list is manually maintained — no CI gate — diff it by hand against `CLAUDE.md`'s copy.)
- **Carrying the project forward -- the in-repo skill library** (`.claude/skills/tensor-grep-*` + `code-search-and-retrieval-reference`, **37 skills**): the onboarding handbook so a new engineer or a Sonnet-class session can debug, extend, validate, and advance `tg` without the original authors. Each auto-loads by its `description`; load the one matching your task. Index by intent -- this exact bucket list is kept byte-identical with `CLAUDE.md`'s skill index; `tests/unit/test_skill_index_sync.py` fails if either doc drifts from the real `.claude/skills/` folder set, and `tests/unit/test_skill_library_drift.py` additionally pins every `file:line` citation (must resolve to a git-tracked file, line in range) and the stated `**N skills**` count against the folders that sentence names. **Neither gate can tell you a skill is CORRECT** — they prove a citation resolves, not that the cited line still contains the claimed symbol. Anchors drift 14-500 lines while resolving perfectly; run `/tg-skill-audit` (`.claude/workflows/tg-skill-audit.js`) for that half, and never fix drift by re-stamping a new line number (see "Cite the SYMBOL, not the line" above):
  - **Change safely:** `tensor-grep-change-control` (the gates), `tensor-grep-debugging-playbook`, `tensor-grep-failure-archaeology` (don't re-fight settled battles), `tensor-grep-validation-and-qa`, `tensor-grep-hermetic-hostile-tests` (env-independent gated tests + hostile fixtures that must BITE), `tensor-grep-cross-platform-path-confinement` (junction vs symlink vs drive-absolute confinement, Windows+POSIX), `tensor-grep-release-drift-check` (post-release sweep: version stamps, derived counts, known-state facts vs the current tag, SUPERSEDED append-only fix discipline), `tensor-grep-local-ci-parity-harness` (run the shared-box-banned lanes in a CPU-capped container; the 12 container-vs-runner divergences; act vs a hand-written harness).
  - **Understand:** `tensor-grep-architecture-contract`, `code-search-and-retrieval-reference` (domain theory), `tensor-grep-config-and-flags`, `tensor-grep-argv-normalization-and-shadowing` (front-door rewrites, `--` hygiene, shape-monotonic routing), `tensor-grep-index-fingerprint-freshness` (index reuse/staleness identity, M17).
  - **Operate:** `tensor-grep-build-and-env`, `tensor-grep-run-and-operate`, `tensor-grep-diagnostics-and-tooling`, `tensor-grep-docs-and-writing`, `tensor-grep-release-and-positioning`, `tensor-grep-workspace-dogfood` (multi-repo stress dogfood), `tensor-grep-enterprise-agent` (enterprise readiness gaps + agent hard-stops), `tensor-grep-worldclass-roadmap` (the edit-control-plane roadmap: S1 verify-edit escrow, S2-S7 contracts, H1), `tensor-grep-prepare` (one-call edit readiness), `tensor-grep-ledger` (advisory multi-agent claim/finding-reuse), `tensor-grep-find-and-route` (whole-repo hybrid find + route-test), `tensor-grep-multi-project-search` (scoped cross-repo search), `tensor-grep-enterprise-review-bundle` (review-bundle create/verify), `tensor-grep-gpu` (experimental GPU probes).
  - **Advance (SOTA):** `tensor-grep-semantic-search-campaign`, `tensor-grep-benchmark-and-proof-toolkit`, `tensor-grep-research-frontier`, `tensor-grep-research-methodology`, `tensor-grep-large-repo-scale-campaign` (bounding scale/deadline on large repos), `tensor-grep-demand-gate-measurement` (the bounded demand-gate measurement method with the DD-006 worked example), `tensor-grep-design-authorization-ladder` (demand→design packet→Sol→optional Fable waiver→deliberate build; A117/A122).
  - **Extend:** `tensor-grep-add-language` (the symbol-graph language-onboarding checklist).
  - **Orchestrate:** `tensor-grep-backlog-campaign` (the multi-PR drain+build campaign playbook), `tensor-grep-codex-gated-audit-loop` (the per-item codex-gated fix loop: RED→codex gate→re-audit→SHIP; env-independent gated tests).
- When working ON tensor-grep, use `tg search`/`tg defs`/`tg callers` for code navigation rather than generic grep/find — this exercises the tool's own surfaces and catches routing regressions early (mind the scoped-path workaround above).
- `.claude/skill_rules.json` is Claude-Code harness config for the global `skill_activation_gate.py` hook (trigger keywords that auto-suggest a skill) — it is **not a product contract** and is invisible to `test_skill_index_sync.py` (it has no `SKILL.md`); update its per-skill trigger entries when a skill is added/renamed, but do not treat its content as authoritative over a skill's own frontmatter `description`.

These encode the "Adding a Command or Flag", "Dogfood the Real Binary", and "Verify AI-Drafted Plans" sections above as reusable, project-independent skills.


## Dogfood follow-up workflow

When public dogfood identifies multiple independent fixes, preserve the process that has been working:

1. Turn each concrete failure or feature gap into PR-sized slices; do not collapse independent fixes into one broad PR.
2. Before implementation, use Exa research for current external contracts and tooling behavior that the fix depends on, especially `rg`, `ast-grep`, CUDA/GPU, packaging, GitHub Actions, and agent-evaluation surfaces.
3. Run a thinktank or equivalent independent planning review when the dogfood item changes product positioning, benchmark interpretation, GPU promotion criteria, or release workflow. The council must cite `file:line` for every seam claim; uncited claims are hypotheses, not facts.
4. Before fan-out: commit the corrected plan to the shared branch OR inline the full slice spec in every agent prompt. Worktrees branch off HEAD and will not contain uncommitted files — a plan written but not committed is invisible to fan-out agents. Decompose the corrected plan into worktree-isolated agent slices.
5. For each slice, write or update the contract test first, implement the smallest fix, run the targeted suite, then run lint and format before moving on.
6. ORCHESTRATOR VERIFICATION GATE — after every agent branch returns, the orchestrator must verify before integration: (a) remove each worktree (`git worktree remove --force <path>`) before checking out the branch in the main repo — an un-removed worktree blocks checkout and causes a main-repo test run to silently execute main's code, not the branch's; (b) re-run pytest/ruff/mypy in the real venv, since worktrees have no `.venv` and agents' "tests pass" / "N tests green" claims are hypotheses until re-run there; (c) run `ruff format --preview` on EVERY file in `git diff main --name-only`, not only hand-fixed files — agents couldn't run ruff, so their files come back un-`--preview`-formatted; (d) treat scoped-local-green as a hypothesis, not a merge signal — lint/format run repo-wide, one unrelated failing test reddens the whole test-python job, and corpus side-effects are outside scoped test scope. See the global skill `worktree-fanout-verification-gate`.
7. Integrate the verified slices onto one branch, resolving any overlaps.
8. ADVERSARIAL AUDIT (3 lenses + chairman) — run a citation-enforced adversarial audit of the integrated diff; this is a mandatory stage distinct from the pre-build planning council (the post-build audit caught a HIGH CUDA-fork hazard that 203 passing tests missed). A finding with no `file:line` citation is discarded. Re-audit → fix-wave → re-audit until ZERO must-fix findings remain. The endpoint is a DRAFT PR; never auto-merge.
9. Ask Gemini for a bounded read-only review of each PR diff before merge; treat its findings as hypotheses until checked against local files and tests.
10. Push each branch, wait for PR CI, squash-merge intentionally, then watch main CI. Release-bearing work is not complete until semantic-release, assets, PyPI, and public release dogfood pass.

Maintain a per-slice evidence ledger in `docs/SESSION_HANDOFF.md`, `SKILL.md`, and this file when operating practice changes. Each slice entry must record PR order, slice scope, Exa research anchors, thinktank or planning consensus, subagent ownership, Gemini review result, validation commands, PR CI, and main CI. Optional or triggered items may be marked `not applicable` only with a rationale. For release-bearing slices, additionally require semantic-release, release assets, PyPI, and public release dogfood evidence.

Current dogfood slice ledger:

- PR order: 13; scope: close the `v1.13.20` dogfood daemon-upgrade and LSP-diagnostic follow-up by snapshotting pre-upgrade session daemon state, restarting the daemon after direct or scheduled Windows upgrade handoff loss, stripping inherited Python runtime variables from managed LSP provider launch environments, and suppressing stale Pyright SRE mismatch stderr tails once a current provider request proves healthy while preserving failed-proof stderr; Exa anchors: CPython/uv SRE mismatch reports connecting the error to mismatched Python runtime/stdlib environment; thinktank/planning consensus: read-only subagent reviews required using the pre-upgrade daemon root and preserving failed-proof stderr; subagent ownership: Popper and Copernicus read-only plan review, implementation local; Claude Opus review: PASS with low findings, addressed by preserving non-SRE suppressed stderr as `provider_recent_stderr` and carrying daemon restart roots into the scheduled Windows helper; validation: targeted upgrade/LSP tests, focused LSP suites, ruff, preview format, mypy, and diff whitespace passed locally; PR CI: PR #233 passed; main CI: semantic-release published `v1.13.21` at `1b62da7`, main CI run `26450640497` passed, CodeQL/dynamic run `26450639894` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.21 tg --version` proof passed.
- PR order: 12; scope: harden the `v1.13.19` built-in dogfood timeout gap by giving `tg dogfood` a wrapper timeout, passing an incremental child `--output` to `scripts/agent_readiness.py`, preserving partial running reports, and cleaning up the launched child process tree by PID only; Exa anchors: Python subprocess timeout semantics and psutil process-tree termination guidance; thinktank/planning consensus: not applicable because this is an internal harness lifecycle fix, with Zeno read-only subagent review confirming the timeout and descendant-cleanup root cause; subagent ownership: Zeno read-only call-path review, implementation local; Claude Opus review: no blocker/high findings (`OPUS_REVIEW: PASS`); validation: targeted dogfood/readiness/docs tests, ruff, preview format, mypy, and diff whitespace passed locally; PR CI/main CI: PR #231 passed, squash merge produced `6525853`, semantic-release published `v1.13.20` at `c41d475`, main CI run `26437847778` passed, CodeQL/dynamic run `26437847528` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.20 tg --version` proof passed.
- PR order: 11; scope: harden the `v1.13.18` daemon-cache dogfood gap by letting capped or truncated implicit session snapshots bypass added-file stale detection for daemon-routed top-level `context-render` / `edit-plan` cache writes while preserving explicit added-file refresh for complete sessions; Exa anchors: not applicable because this is internal daemon/session cache behavior; thinktank/planning consensus: systematic-debugging trace plus read-only subagent review isolated the stale-detection failure before `response_cache.put()` and required an added-file refresh regression test; subagent ownership: Wegener read-only plan/diff review, implementation local; Claude Opus review: no blocking findings, optional capped-modification stale-refresh test added; validation: targeted docs/session tests pass (`47 passed`), `uv run --no-sync ruff check .`, `uv run --no-sync ruff format --check --preview . --exclude .tmp --exclude .tensor-grep --exclude src/.tensor-grep`, `uv run --no-sync mypy src/tensor_grep`, and `git diff --check` pass locally; full pytest/Rust matrices and benchmark suites intentionally deferred to PR/main CI unless the user approves heavy desktop validation; PR CI/main CI: PR #230 passed, squash merge produced `0c9155f`, semantic-release published `v1.13.19` at `b9197a6`, main CI run `26431129535` passed, CodeQL/dynamic run `26431129155` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.19 tg --version` proof passed.
- PR order: 10; scope: harden `v1.13.17` dogfood regressions by making non-JSON rg-shaped explicit no-ignore searches prefer ripgrep passthrough when `rg` is available while preserving the native fallback when it is not, preserving tensor-grep aggregate JSON semantics, resolving top-level `context-render` / `edit-plan` daemon requests to absolute directory roots so repeated relative invocations can populate and hit the daemon response cache, and documenting desktop memory-safety operating rules for local validation; Exa anchors: official ripgrep guide/manpage behavior for `--no-ignore` and `-u` disabling ignore filtering; thinktank/planning consensus: read-only subagent review agreed the no-ignore fast path should stay in the rg-shaped non-JSON lane and the daemon cache fix should normalize request paths at the top-level caller boundary; subagent ownership: McClintock read-only plan/diff review, implementation local; Claude Opus review: accepted findings for direct JSON/NDJSON passthrough tests, no-ignore-vcs coverage, guarded daemon path normalization, daemon-start assertions, and absolute cleanup; validation: targeted daemon path/cache tests, targeted Rust routing test, ruff, preview format check, cargo fmt check, and diff whitespace check passed locally; full pytest/Rust matrices and benchmark suites intentionally deferred to PR/main CI unless the user approves heavy desktop validation; PR CI/main CI: PR #229 passed, squash merge produced `77a73b2`, semantic-release published `v1.13.18` at `4a0dad0`, main CI run `26425383595` passed, CodeQL/dynamic run `26425914836` passed, and public `uvx --refresh-package tensor-grep --from tensor-grep==1.13.18 tg --version` proof passed.
- PR order: 7; scope: close concrete `v1.13.11` dogfood regressions by deduplicating `defs --provider hybrid` native/LSP definition rows while preserving LSP proof, bounding checkpoint discovery cache priming at the user-home boundary so Windows standalone `checkpoint create` does not write `C:\Users\.tensor-grep`, separating MCP protocol/CLI version fields in capabilities, sharpening the PowerShell `Start-Process`/`tg.ps1` MCP stdio warning, suppressing stale LSP stderr tails once a provider request proves healthy, routing `tg audit --help` to audit help instead of search, and broadening `secrets-basic` fake API key detection; Exa anchors: official MCP lifecycle/version negotiation docs and LSP 3.17 `Location`/range semantics for merge identity; thinktank/planning consensus: compressed read-only review through subagents because the separate thinktank spawn hit the agent thread limit; Aquinas recommended explicit MCP protocol versus CLI fields, Cicero recommended post-merge LSP/native dedupe with LSP proof preservation and quiet successful provider status, and Ohm recommended home-bounded checkpoint discovery plus explicit native-`tg.exe` MCP stdio warning; subagent ownership: Aquinas (MCP), Cicero (hybrid/LSP), Ohm (checkpoint/doctor/audit); Gemini review: unavailable because `gemini-3-flash-preview --approval-mode plan` stalled after startup/tool noise and was killed without a report; validation: targeted checkpoint, semantic-provider, LSP-provider, trust/audit, MCP, doctor, scan, docs, and integration tests pass locally; `uv run pytest -q` passes (`2451 passed, 16 skipped`); `uv run ruff check .`; `uv run ruff format --check --preview .`; `uv run mypy src/tensor_grep`; full Rust crate tests; cargo fmt check; `uv run python scripts/agent_readiness.py --no-shell-probes --no-wsl-probe --json` passes (`13 passed, 0 failed`); direct Windows checkpoint-create smoke, direct agent-studio hybrid-defs smoke, audit-help smoke, MCP-capabilities smoke, public-command contract smoke, and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 1; scope: accept and forward remaining rg config-override flags (`--pcre2-unicode`, `--ignore`, `--messages`, `--require-git`, `--no-hidden`) in native/Python search and add installed-public sweep coverage; Exa anchors: ripgrep manpage option inversion/config behavior plus ripgrep guide automatic-filtering defaults; thinktank/planning consensus: local planning review, external council not applicable for this parser/forwarding contract slice; subagent ownership: not applicable; Gemini review: unavailable because Gemini CLI 0.42.0 hung on a one-token read-only model probe and was killed; validation: Rust crate tests, full pytest, lint, format, mypy, and diff whitespace checks pass locally; PR CI/main CI: pending.
- PR order: 1; scope: make `run_agent_success_harness.py` refuse stale in-tree native `tg` binaries by default and mark `--allow-claim-unsafe-launcher` runs as exploratory; Exa anchors: not applicable beyond existing benchmark-governance policy; thinktank/planning consensus: local planning review aligned with `run_benchmarks.py` stale-binary refusal; subagent ownership: not applicable; Gemini review: unavailable because Gemini CLI 0.42.0 hung on a one-token read-only model probe and was killed; validation: Rust crate tests, full pytest, lint, format, mypy, and diff whitespace checks pass locally; PR CI/main CI: pending.
- PR order: 1; scope: accept and forward the 25 remaining ripgrep inverse/config-override flags found by `parser_sweep_1_12_31_codex.json`, including `--no-auto-hybrid-regex`, `--no-pcre2-unicode`, `--no-text`, `--no-binary`, `--no-follow`, `--ignore-dot`, `--ignore-vcs`, `--no-json`, and `--no-stats`, and batch those 25 installed-public sweep probes into one command to avoid adding dogfood latency; Exa anchors: current ripgrep guide/manpage behavior for config override flags plus local `rg 15.1.0` acceptance sweep; thinktank/planning consensus: local planning review only, external council not applicable because this is parser/forwarding contract work and does not alter GPU/LSP/product positioning; subagent ownership: not applicable, no subagents requested for this turn; Gemini review: unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted parser/backend/readiness tests, full `test_public_native_cli_parity`, direct built-native acceptance of all 25 flags, full Python/Rust suites, lint, format, mypy, diff whitespace, and fast readiness pass locally; PR CI/main CI: pending.
- PR order: 1; scope: add `world_class_readiness.status = "not_claimed"` plus `agent_target_selection_metrics` to `tg dogfood` reports so a PASS cannot be mistaken for full rg replacement, full ast-grep replacement, public GPU promotion, production LSP proof, or enterprise target-selection accuracy; Exa anchors: ripgrep JSON/config-override docs, ast-grep CLI docs, Cursor/Sourcegraph agentic context docs, and NVIDIA CUDA profiling/transfer guidance; thinktank/planning consensus: Gemini plan-mode read-only review rejected a separate `next_pr_slices` planning array as source-of-truth duplication and recommended adding the missing target-selection surface to the existing limitations contract; subagent ownership: not applicable, no Codex subagents requested for this turn; Gemini review: completed for planning, final diff-review retry unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted dogfood/docs tests, full Python/Rust suites, lint, format, mypy, diff whitespace, and fast readiness pass locally; PR CI/main CI: pending.
- PR order: 2; scope: make GPU promotion workload-scoped in benchmark artifacts and public dogfood/docs, including `promotion_scope = "declared_workload_class_only"`, fair many-pattern baseline `rg -F -e ... -e ...`, and candidate classes for `many_fixed_patterns_single_dispatch` / `resident_repeated_query`; Exa anchors: CUDA-grep final/checkpoint reports on transfer amortization and many-regex workloads, NVIDIA CUDA Graphs and pinned-memory async transfer docs, and ripgrep `-F`/`-e` multiple-pattern docs; thinktank/planning consensus: read-only GPU proof and release-governance seats both recommended an artifact/schema hardening PR rather than CUDA kernel work; subagent ownership: Jason reviewed GPU performance/proof, Lovelace reviewed release/governance; Gemini review: unavailable because `gemini-3.1-pro-preview` returned an invalid empty stream and `gemini-2.5-flash` stalled after startup; validation: targeted GPU benchmark contract, dogfood, public docs, benchmark-script, and readiness tests; `uv run ruff check .`; `uv run ruff format --check --preview .`; `uv run mypy src/tensor_grep`; `cargo fmt --manifest-path rust_core/Cargo.toml --check`; `cargo test --manifest-path rust_core/Cargo.toml`; `uv run pytest -q` (`2248 passed, 16 skipped`); `uv run python scripts/agent_readiness.py --no-shell-probes --no-wsl-probe --json` (`12 passed, 0 failed`); and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 3; scope: add public managed GPU proof plumbing with `tg-native-metadata.json`, Python upgrade/install script metadata writers, `--public-managed-proof`, and artifact fields `public_managed_promotion_ready` / `public_gpu_proof`; Exa anchors: NVIDIA Blackwell compatibility guidance, cudarc 0.19 CUDA 13/dynamic-loading docs, and GitHub Actions GPU runner docs; thinktank/planning consensus: Gemini plan-mode review rejected path-shape-only proof and recommended explicit managed front-door provenance; subagent ownership: attempted read-only Codex explorer, but the agent thread limit was reached, so implementation stayed local; Gemini review: planning review completed with file-read limitation, final diff review not run yet; validation: targeted runtime/installer/GPU benchmark/docs tests (`91 passed`), `uv run pytest -q` (`2261 passed, 16 skipped`), `uv run ruff check .`, `uv run ruff format --check --preview .`, `uv run mypy src/tensor_grep`, and `git diff --check` pass locally; PR CI/main CI: pending.
- PR order: 4; scope: add a dispatch-only public managed GPU proof workflow and strengthen the native GPU proof gate so public promotion requires fixed GPU runner labels, managed NVIDIA asset verification, direct `rg --json` 1GB/5GB correctness, `NativeGpuBackend`, `sidecar_used = false`, and speed wins over both `rg` and `tg_cpu`; Exa anchors: GitHub Actions self-hosted/GPU runner docs, NVIDIA Blackwell/CUDA compatibility docs, CUDA compute-capability docs, and ripgrep JSON output semantics; thinktank/planning consensus: Mill/Mencius/Descartes agreed to separate public proof workflow/governance from local CUDA implementation evidence and to reject weak `promotion_ready` summaries; subagent ownership: Mill reviewed workflow scope, Mencius reviewed release/security workflow requirements, Descartes reviewed benchmark proof semantics; Gemini review: unavailable; `gemini-3.1-pro-preview` stalled after startup with no report and was stopped; validation: targeted GPU benchmark contract, benchmark-script, release-workflow validator, and release asset validator tests pass locally; PR CI/main CI: pending.
- PR order: 1; scope: close the `v1.12.33` rg column-override edge by accepting and forwarding `--column --no-column` through both `tg search --format rg ...` and root-level `tg --format rg ...`, add installed-native sweep coverage, improve stale repo-local `uv run tg` warmup diagnostics, and pin the `ripgrep binary resolution` capsule hardcase; Exa anchors: ripgrep inverse/config-override docs where last flag wins, ripgrep JSON/output docs for preserving rg-vs-tg schema boundaries, Sourcegraph/Cody context docs for agent target-selection evidence, LSP initialize-timeout evidence for keeping LSP experimental, and CUDA-grep transfer-amortization notes for keeping GPU unpromoted; thinktank/planning consensus: two read-only seats recommended this narrow contract/readiness/capsule regression slice and explicitly rejected raw-speed, GPU, LSP, or ast-grep claim changes; subagent ownership: thinktank seats Lagrange and Hegel reviewed the plan, implementation stayed local due tight parser/readiness coupling; Gemini review: attempted with gemini CLI 0.42.0 / gemini-2.5-flash in read-only plan mode; unavailable because the model returned an invalid empty stream / malformed tool call; validation: targeted rg contract/parity tests, readiness stale-entrypoint and flag-sweep tests, agent hardcase test, Rust parser unit test, Rust public-native parity test, full Rust crate tests, full pytest, lint, format, mypy, fast readiness, and diff whitespace passed locally; PR CI/main CI: PR #163 passed, squash merge produced `c0cb613`, main CI run `26094452260` passed semantic-release, GitHub release assets, PyPI publish, and `publish-success-gate`; release/public proof: `v1.12.34` tag/release assets exist and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.34 tg --version` reports `tensor-grep 1.12.34`.

## Required Local Validation

Run these before push for normal code changes:

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

CI runs `ruff format --check --preview .`. Running only `uv run ruff check .` is not enough to prove formatter parity, and running `ruff format` WITHOUT `--preview` actively REVERTS preview-style formatting on disk — a "clean" bare `ruff format` will undo CI-mandated style and red the next `ruff format --check --preview` run even when local lint passes. Always pass `--preview` to `ruff format` locally; never pass it to `ruff check`. The trailing `.` (whole repo) is load-bearing too: under `--preview`, ruff formats Python code fences INSIDE Markdown, so a scoped run (`ruff format --check --preview src/tensor_grep tests`) passes locally yet MISSES an unformatted `docs/**/*.md` snippet — which reds CI's release-gating `static-analysis` job and blocked v1.67.0. Always run the whole-repo `.` form; never a `src`/`tests` subset.

`uv run pytest -q` can take substantially longer than 70-90 seconds on this Windows machine when the full JS/TS and e2e surface is hot; use a timeout of at least 120 seconds for narrow suites and a much larger timeout for the full suite when running it through automation.

**`tests/conftest.py`'s `sys.path.insert` OUTRANKS `PYTHONPATH` — a `PYTHONPATH`-only baseline swap
gives a FALSE red-green (2026-07-24).** The standing stale-venv discipline ("pin `PYTHONPATH` to the
worktree `src`, verify `tensor_grep.__file__` resolves into the worktree") proves WHICH TREE you
imported for a normal test run, but it is NOT sufficient to prove a test genuinely fails on a baseline
commit. `tests/conftest.py:10` does `sys.path.insert(0, str(SRC_DIR))`, where `SRC_DIR` is derived from
`conftest.py`'s own `__file__` location — this takes precedence over `PYTHONPATH` on `sys.path`. A gate
that tried to prove a fix's regression test genuinely fails on `origin/main` (by reverting one source
file to its pre-fix state and re-running with `PYTHONPATH` pointed at that reverted tree) got a FALSE
"passed on main," because the *worktree's* `conftest.py` silently re-pointed imports back at the
worktree's own `src`, not the reverted one, regardless of `PYTHONPATH`. Verifying
`tensor_grep.__file__` does not catch this either — it correctly reports the worktree's file, which is
exactly the wrong answer for a baseline check. **Rule:** to prove a test fails on a baseline commit
(pre-fix `origin/main`, a specific tag, a reverted file), use a FULLY ISOLATED TREE COPY with the file
reverted INSIDE that copy — never a `PYTHONPATH` swap layered on top of the working tree's own
`conftest.py`. This applies doubly to an independent gate proving a fix's red-phase test is real, since
gates are the primary consumers of a red-green baseline.

For focused changes, run the relevant narrow suite first, then the full suite if the change is intended to land:

```powershell
uv run pytest tests/unit/test_cpu_backend.py -q
uv run pytest tests/unit/test_cli_bootstrap.py -q
uv run pytest tests/unit/test_release_assets_validation_*.py -q
```

For fast pre-push dogfood on agent-critical surfaces, run the agent-readiness dogfood gate:

```powershell
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
tg dogfood --output artifacts/dogfood_readiness.json
```

This 3-5 minute gate checks public shell version resolution, `public-version-python-subprocess`, `public-windows-launcher-quoted-patterns`, installed-public advertised search flag acceptance via `public-search-advertised-flag-sweep`, repo doctor sanity, `context_consistency`, `agent-capsule`, `agent-capsule-mixed-language`, `agent-capsule-hardcases`, deterministic rg edge parity, broad generated-root scan guardrails, AST smoke, MCP context-render smoke, and docs claim hygiene. `tg dogfood` wraps the same readiness gate with a one-page verdict and JSON envelope. It complements, not replaces, the full local validation gate.

For release dogfood, include this compact public path checklist:

```powershell
gh release view <tag>
pip index versions tensor-grep
uvx --refresh-package tensor-grep --from tensor-grep==<tag> tg --version
tg upgrade
cmd /c tg --version
pwsh -NoProfile -Command "tg --version"
tg doctor --json
```

`tg doctor --json` must show matching sidecar/native versions and should expose any current-shell, fresh-shell, or Python-subprocess foreign launcher route.

## Benchmark Rules

Never claim a speedup without measured numbers.

Use the right benchmark for the area you changed:

### End-to-end CLI text search

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
```

This is the main `tg` vs `rg` comparison. Use this for:

- plain search routing
- startup / launcher changes
- text-search control-plane changes

### Repeated-query / hot cache paths

```powershell
python benchmarks/run_hot_query_benchmarks.py --output artifacts/bench_hot_query_benchmarks.json
```

Use this for:

- StringZilla index changes
- CPU regex prefilter changes
- persisted cache / decode / posting-list changes

`repeated_regex_native` must stay on native/Rust routing such as `cpu_rust_regex`; do not force a Python fallback in hot-query probes. For sub-10ms benchmark rows, use an absolute jitter tolerance in addition to ratio checks.

### AST single-query benchmark

```powershell
python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json
```

### AST workflow startup benchmark

```powershell
python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json
```

Use this for:

- `run`
- `scan`
- `test`
- AST workflow startup / batching / wrapper orchestration

### Agent capsule / edit-loop workflow benchmark

```powershell
python benchmarks/run_agent_workflow_benchmarks.py --output artifacts/bench_agent_workflow.json
python benchmarks/run_agent_success_harness.py --output artifacts/bench_agent_success_harness.json
```

Use this for:

- `tg agent` capsule routing
- confidence / alternative target surfacing
- validation alignment and filtering
- rollback, edit order, and whole-loop edit latency
- end-to-end query intent -> context -> edit seed -> apply -> verify -> rollback success

This is workflow evidence, not a cold exact-text search speed claim.

### GPU / NLP backend benchmark

```powershell
python benchmarks/run_gpu_benchmarks.py --output artifacts/bench_run_gpu_benchmarks.json
```

Notes:

- `cyBERT` may skip if Triton is unavailable.
- Treat `SKIP` as expected infrastructure state, not a fake failure.

### Retrieval-quality (NL search) benchmark

```powershell
python benchmarks/eval_late_rerank_quality.py --output artifacts/bench_find_quality.json
```

Use for `tg find` / `tg_find` ranking changes (`TG_FIND_DENSE_WEIGHT`, RRF channels, chunker, late-rerank).
This is a QUALITY benchmark (ndcg@10 / recall@10 on the NL golden set + literal/identifier golden slices),
NOT a speed benchmark — run it IN ADDITION to the CLI search benchmark when the change touches the CPU
search path. Bidirectionally-oracle-validate any new golden query before trusting a delta (an empty/wrong
answer must FAIL the grader). Add a per-query paired win/loss/tie report before gating a ship on a bare
40-query mean (see the global `paired-test-power-discipline` skill). `TG_LATE_RERANK` is RETIRED
(2026-08-05, task F10): re-measured AFTER the role-aware encoder fix it regressed decisively vs
plain BM25 (ndcg@10 0.068 vs RRF 0.305; root cause is model capacity, not the encoder) — a
validated dead end, not a paused build; `retrieval_late.py`'s module docstring is the authority
and re-flipping the same encoder will not change the verdict.

## Performance Discipline

Use these rules consistently:

1. Compare against the current accepted baseline, not memory.
2. Reject candidates that are slower or only “faster” in a microprofile while slower end-to-end.
3. Keep both cold-start and repeated-query measurements in mind.
4. Do not update docs or the paper with speed claims until the benchmark line is accepted.
5. If a candidate is correct but slower, revert it and record the attempt.

## Optimization Discipline (how to discover a lever and PROVE equivalence)

"Benchmark Rules" says which script to run; "Performance Discipline" sets the
acceptance bar. This is the third layer: how to FIND a lever and prove an
output-preserving optimization is byte-identical. See the global skill
`profile-guided-byte-identical-optimization`.

1. **Measure-first — never project.** Do not declare a surface "optimized-out" by
   reasoning; measure it. A validation-scan lever was deferred as "no clean path" (only
   a recall-risky `score>0` gate had been considered); a fresh profiling probe found a
   BYTE-IDENTICAL substring pre-check that had been missed → shipped ~68% faster.
2. **Profiling-probe.** cProfile the hot commands on the PUBLISHED wheel
   (`uvx --from tensor-grep==<ver>`), rank hot fns by cumtime%, EXCLUDE already-shipped
   work, hunt redundant-work levers (a file parsed/walked >once; N full-tree passes
   mergeable into 1; an index rebuilt per call). Output ranked levers with `file:line`
   + measured %. An empty result is an honest null (valuable).
3. **Byte-identical PROOF (load-bearing).** When merging/skipping work, prove output
   byte-identical TWO ways: (a) ENUMERATE every producer/branch and argue exhaustiveness
   (AST node types are mutually exclusive; a token is always a substring of its string;
   candidate names ⊆ file text ⇒ no-term-in-text ⇒ bonus 0); (b) DIFFERENTIAL FUZZ —
   run OLD-vs-NEW over N real files, assert 0 mismatches (386-file / 26-case receipts).
   An INDEPENDENT Opus gate is the proof-of-record; a build agent's self-verify is a
   hypothesis.
4. **Warm dogfood HIDES a cold-path win.** A warm end-to-end run measures the CACHED
   path where the optimized fn doesn't run → false read (`tg orient` warm dogfood read
   −36% on a fn that is actually ~54% FASTER). To verify a cold-path optimization:
   microbench the FUNCTION directly (isolate the change) OR clear the cache between
   reps. NEVER a warm end-to-end run.
5. **Microbench on the SHIPPED wheel.** Isolate the target fn on the published wheel,
   single pass over DISTINCT inputs (fresh process = cold cache), old-vs-new + assert
   OUTPUT-IDENTITY (`total == total` both sides = byte-identical AND faster). Receipts:
   ast.walk-merge 961→446 ms (~54%); validation-scan 3657→1172 ms (~68%).

## A Red Run With No Failing STEP Is An Interrupted Run (2026-07-27, #339)

`CI red is sufficient; CI green is not` makes a red run on `main` blocking by default — correct, and it
leaves a question that costs a cycle if you answer it by argument: is this red telling me something about
the code? Read the **step** conclusions before you believe the **run** conclusion.

    success   Set up job / checkout / Install uv / Setup Python / Install Rust dependencies
    (empty)   Install Dependencies (Unix with retry)
    (empty)   Run Pytest
    (empty)   Post Run actions/checkout

A genuine test failure records `Run Pytest: failure`. An **empty** conclusion on every step after a
successful one means the job was KILLED before those steps finished — no step failed, so nothing was
measured about the code. `gh run view <id> --json jobs` gives you this; print EVERY step with its
conclusion rather than filtering, because a naive `conclusion not in ("success","skipped",None)` filter
lets empty strings through and reports not-run steps as failures.

**Discharge it by measurement, never by plausibility.** *A correctly-diagnosed flake still holds its
AUTHORITY* — deciding a red is environmental does not remove its power to block, so the exit is a control,
not a story. The cheap control: re-check **the same job on the commit that SUPERSEDES it**. Receipt —
run 30282929109 (`a1bbdac3`, a docs-only commit) went red on `test-python (macos-latest, py3.12)`; the
superseding commit `9e0df69` contains that tree plus another PR, and its `test-python (macos-latest,
py3.12)` is `success`. Same job, superset tree, passes ⇒ the earlier red carried no information. That
verdict needs no theory of the cause, which is the point: the timing did not cleanly fit a
concurrency-cancel and the cause was never established, yet the question was still settled.

**The log-expiry false zero, which sits in the middle of this.** `gh run view <id> --log-failed` on an
expired (or still-running) run prints `log not found: <job-id>` and nothing else, so a `grep -E "FAILED|assert"`
over it returns EMPTY — indistinguishable from "no failures found". Check the raw byte count before
interpreting the filtered result: 26 bytes of `log not found` is a measurement that did not happen. Same
family as any probe that returns EMPTY: the number cannot tell you whether it measured nothing
or never measured at all, so every zero needs a control proving the probe CAN return non-zero.

## CI / Release Rules

CI is not just a test runner. It enforces:

- formatting
- linting
- typing
- cross-platform behavior
- release workflow contracts
- package-manager workflow contracts
- artifact/version parity

Any new download / extract / install / self-upgrade helper must apply the v1.17.2–v1.17.5 supply-chain patterns (see the `supply-chain-hardening` skill): (a) zip-slip guard — validate every member path against the resolved dest before `extractall` (reuse the production `_safe_extract_zip`); (b) time-bound + byte-capped downloads — `urlopen(timeout=...)` / socket timeout + a byte cap (256 MiB for native assets); (c) checksum-gated fail-closed installs — embed the expected SHA from `CHECKSUMS.txt` and verify before `os.replace`, INCLUDING in the detached Windows self-upgrade helpers; (d) `--locked` + exact version pins for CI tools (e.g. `cargo-audit==0.22.2 --locked`, `cargo-deny --locked`) — an unpinned `cargo install` can pull a breaking upstream release mid-CI.
(e) uv's `.ps1` installer LACKS binary checksum verification (uv issue #13074) while the `.sh` self-verifies (uv >=0.11.0, pinned 0.11.25); Windows fix = download the pinned uv RELEASE BINARY + verify a COMMITTED dual-arch (x86_64 + aarch64) SHA-256 fail-closed before use (implemented in `scripts/install.ps1` + a new `scripts/uv_checksums.json`, landing with PR #302 — not yet on `main`); discipline: ALWAYS download + `Get-FileHash` to CONFIRM a committed SHA — never trust an agent's "fetched from the sidecar" value.
(f) ACCEPTED BOOTSTRAP TRUST BOUNDARY (documented, not a gap): the toolchain bootstrappers are trusted-over-HTTPS + version-pinned, NOT checksum-gated like the release artifacts WE download — uv's `.sh` self-verifies its binary (uv >=0.11.0, pinned 0.11.25), and rustup is fetched via `curl https://sh.rustup.rs | sh` in the semantic-release `build_command` (pyproject.toml) then pinned with `rustup default 1.96.0` (rustup self-verifies the toolchain). This is a deliberately different posture from (a)-(e), which checksum-gate artifacts WE fetch/extract. De-piping rustup to a pinned-binary + committed-checksum download is a tracked follow-up — it touches the release `build_command`, so it is ATTENDED (do not change it autonomously).
(g) **Runtime-dependency CVE response (#632 / v1.78.1).** Unlike (a)–(f) (code WE write), a disclosed CVE
in a THIRD-PARTY runtime dependency is caught by the `Dependency & License Audit` workflow's strict-on-
fixable `pip-audit` / `cargo-audit` gate — and it reds **every open PR**, unrelated to any diff. Decode the
audit's OWN structured output for the exact package + fixed-version; bump the `pyproject.toml`/`Cargo.toml`
FLOOR (e.g. `mcp>=1.2.0` → `mcp>=1.27.2`), NOT just a lock relock — a floor-only relock can silently regress
below the patch on a future bare resolve. Regenerate the lockfile, then re-run the FULL dependent test
surface unmodified (`tests/unit/test_mcp_server_*.py`, `tests/unit/test_mcp_tg_find.py`,
`tests/integration/test_mcp_stdio_protocol.py`, `tests/unit/test_harness_api_docs.py`) — a passing
dependency bump with zero code changes is the expected GOOD outcome, not a reason to skip verification.

(h) **Rustup's PINNED-toolchain fetch has NO retry (#720-#722).** `rust_core/rust-toolchain.toml`
pins a version, so `cargo test` triggers an on-demand rustup toolchain fetch — and unlike
Setup-Rust's `curl | sh` (`--retry 10`), rustup's own toolchain download is not retried, so a
macOS runner network blip flakes the job (#720/#721). Pre-fetch the pinned toolchain in a 3×
retry loop in the Setup Rust step (#722).

Any Rust helper reachable only from a `#[cfg(feature = "cuda")]`-gated test must be re-gated
`#[cfg(any(feature = "cuda", test))]` -- co-gating every helper it transitively calls -- instead of
staying plain `#[cfg(feature = "cuda")]`. A default `cargo test` (no `--features cuda`, what CI's
release-gating static-analysis job runs) never compiles a plain-`cuda`-gated test at all, so a test
written against a plain-`cuda`-gated helper silently never runs; separately, un-gating only the test
without also re-gating
the helper leaves the helper with zero default-build callers and fails `cargo clippy -- -D warnings` on
`dead_code`. `any(feature = "cuda", test)` solves both at once: the helper compiles whenever `cuda` is
enabled (unchanged production behavior) OR whenever `cfg(test)` is set, so the test has something to call
and is not itself dead code, while staying absent from the default non-test release build. Precedent:
`GpuRouteFailureKind` / `sanitize_cuda_detail` / `classify_gpu_route_failure` in `rust_core/src/main.rs`
(gate-nit #172 NIT-4 / MF-1, shipped in `#597` / v1.75.4).

(i) **A dynamic value that grows a SHARED envelope can break a payload-RATIO governance test on the
SMALL side (#733/#734).** PR #733 made `coverage.language_scope`/`symbol_navigation` dynamic and
registry-derived (~28 -> ~116 chars). `_envelope()` in `repo_map.py` stamps that field byte-identically
onto BOTH `tg map` (a large payload) and `tg importers` (a deliberately tiny one), so the same fixed
growth was ~5% of the large payload but ~37% of the small one — tripping
`test_importers_payload_is_far_smaller_than_map`'s `< 0.1 * map` invariant and reddening `main`. Fix
(#734): strip the shared `_envelope()` keys SYMMETRICALLY from both payloads before comparing, deriving
the excluded key set LIVE (`set(repo_map._envelope(project))`), never a hand-copied literal, so the
test stays robust to any FUTURE envelope growth instead of re-breaking on the next honesty fix. Rule:
when a shared self-description helper grows a field, audit every test that compares two payloads'
byte sizes, not just the payload the new field conceptually belongs to.

**Same incident, a second trap: PATH LENGTH shifts payload-byte governance tests across platforms.**
The test above PASSED on Windows and FAILED on Linux CI for the SAME code — Windows' longer
`AppData\Local\Temp` tmp paths inflate both payloads and dilute the fixed-envelope fraction back under
the 10% threshold; Linux's shorter `/tmp` paths do not. Rule: reproduce a byte-size/ratio assertion
that uses pytest's `tmp_path` with a short `--basetemp` before trusting a local green — a Windows-local
pass does not prove the same assertion holds on Linux CI.

**A self-gate's test SUBSET is not the full CI matrix.** The build agent's own pre-merge gate on #733
ran a real but partial suite that omitted `tests/unit/test_file_deps.py` — the exact file containing
the test #734 later had to fix — so a fully deterministic failure reached `main` anyway. Rule: when
reporting what a self-gate verified, state explicitly which suites ran and which were skipped; treat
the CI run itself, not a self-gate's suite selection, as the merge arbiter (this repo's "never trust a
self-report" rule applied to test SCOPE, not just test RESULT).

**A "relative" timing assertion is only relative while its baseline exceeds CLOCK RESOLUTION (#739,
2026-07-24).** A first-pass de-flake of `test_create_checkpoint_uncontended_hot_path_unaffected` stubbed
the `git rev-parse` subprocess call in `_detect_checkpoint_scope` so that `elapsed < max(baseline * 6.0,
8.0)` would "cancel load," and lowered the floor from 8.0 to 2.0. With the subprocess stubbed, the
baseline measured EXACTLY 0.0 across 8 runs. The reason: Windows'
`time.get_clock_info('monotonic').resolution` is **0.015625s** (one 64Hz tick), and the stubbed baseline
— a `.resolve()` call, an immediate raise, and a 1-file `os.walk` — completed in under that single tick,
so `time.monotonic()` read the identical value before and after and `elapsed` measured exactly `0.0`
(min == max == 0.0000 across all 8 runs). With `baseline == 0.0`, `baseline * 6.0` is also `0.0`, so the
ratio silently collapsed into a pure `elapsed < 2.0` bound, 4x TIGHTER than the 8.0s that had just
flaked. On the exact CI failure it cited (run `30123607322`, `8.75 < 8.0`) it would still have gone red,
by a wider margin. Rule: before trusting a `max(baseline * N, floor)` assertion as genuinely relative,
confirm the baseline is measurably non-trivial (well above the platform's clock resolution — check with
`time.get_clock_info('monotonic').resolution`) — otherwise it has silently degenerated into the floor
alone.

**PROFILE before "fixing" a timing flake — a structurally-true root cause can still be magnitude-wrong
(same #739).** The above fix's diagnosis (a real subprocess spawn adds noise) was correct in kind but
wrong in size: cProfile showed the git spawn was only 6-12% of elapsed, while
`_prime_bounded_discovery_caches_for_root`'s fsync-heavy discovery-cache I/O (685 `read_text` + 1385
`stat` + 8 `fsync` calls per checkpoint, growing with accumulated cache state) was ~93%. fsync-heavy I/O
on a contended CI disk explains a multi-second spike; a process spawn does not. Removing the wrong 6-12%
of cost while tightening the bound 4x made the flake worse, not better — measure the actual cost
breakdown before writing a fix, not just the plausible-sounding mechanism.

**Prefer a STRUCTURAL assertion over a timing one wherever the invariant allows (same #739).** The
eventual fix wraps `index_lock` plus the suspect expensive calls to emit ordered ENTER/EXIT markers into
a shared list, and asserts no expensive-work marker falls between the lock's acquire and release
markers. Marker ORDER is fixed by single-threaded sequential execution — a loaded runner delays every
marker uniformly without ever reordering them — so the assertion is unflakeable BY CONSTRUCTION rather
than by a wider tolerance. Verified green on windows-latest py3.11 AND py3.12 (run `30130861182`), the
exact platform/version combination that had flaked twice. Cross-reference:
`tensor-grep-validation-and-qa` Part 1 point 14 (the same structural-over-wall-clock principle, applied
there to concurrency contracts).

Do not casually edit:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `scripts/validate_release_assets.py`

If you change workflow, docs, or release behavior, expect to update validator-backed tests too.

Read `docs/CI_PIPELINE.md` before editing CI, release, Dependabot, or audit automation. That file is the canonical contract for how the pipeline is supposed to behave and what follow-up validators must change with it.

Important test surface:

- `tests/unit/test_release_assets_validation_*.py`
- workflow/package-manager/release validator suites

## Routing / Architecture Guidance

Be honest about workload classes.

- Cold generic text search:
  - `rg` is still the baseline.
  - control-plane overhead matters more than backend cleverness.
- Repeated text search:
  - indexing can beat cold grep-style tools.
- AST workflows:
  - batching and orchestration matter as much as backend logic.
- GPU:
  - only wins when workload size and arithmetic intensity amortize transfer and startup cost.

Do not assume:

- more caching is always faster
- compiled onefile binaries are always faster
- GPU is always faster
- a micro-optimization is worth landing without end-to-end proof

## Native vs Python Reality

The repo has proven:

- Python-side startup cuts help
- repeated-query indexing helps
- AST batching helps
- onefile Nuitka binaries are not currently the speed path on Windows for plain passthrough

If the goal is to close the remaining gap to raw `rg`, the likely next step is a more native launcher/control-plane path, not more Python micro-tuning.

## Check Whether It Already Shipped, And Pin What You Document (2026-07-27, #328/#333)

Two failure modes at opposite ends of the same lifecycle, both cheap to prevent.

**A queued task may already be DONE.** Task #328's fix was already live — merged as `4195cbf`
(PR #815) and an ancestor of `v1.100.2`, i.e. in the *published wheel*, while the task still read
`pending`. The task text is a snapshot of what someone believed when they filed it; `origin/main`
is what is true. Before building anything from a filed description, read the current source
(`git cat-file blob origin/main:<path>` — not the local checkout, which drifts and goes dirty) and
confirm the defect still exists. `git log -S"<the exact claim>"` finds the commit that closed it.

**A docs-only fix ships UNPINNED, so it drifts.** #815 was one file, ten insertions, zero tests —
nothing failed if either paragraph was deleted or reworded, which is the #318 failure mode exactly.
Every contract statement needs a governance test, and that test must be **pinned to the SOURCE, not
to the doc**: a test that only greps the doc for its own words is circular and passes forever after
the code stops behaving that way. Assert a PREMISE about the code (the producer still emits the
field, the two counts still come from separate blocks) alongside the CLAIM about the prose, so both
arms can fail — reword the paragraph and the claim fires; rename the producer and the premise fires.

## Your Reading Is A Hypothesis; The Mechanical Check Is The Oracle (2026-07-27, #316/#307)

Three times in one session a confident reading was wrong and a mechanical check was right. The
pattern is the same each time: prose *looks* like code, and a human-shaped read of it agrees with
whatever you already believed.

- **A semantic read is not a typecheck.** Before pushing #316 I read the two walk-ceiling tests and
  concluded a return-type change was safe because they "only use `result` as `Result<_, String>`
  with `.expect_err`". `Result::expect_err` requires `T: Debug` to print the unexpected `Ok`; the
  old `T` was a bare `Vec<PathBuf>` (Debug for free), a struct is not. `cuda-feature-check` caught
  it in one cycle. This is why *CI red is sufficient and CI green is not* — reviewers read for
  semantics, they do not typecheck.
- **A coarse grep counts PROSE as code.** `grep -c budget_remediable` reported hits in two CLI
  files and nearly killed a real finding as "already shipped"; every hit was inside a *comment*
  referencing the MCP fix. Functional emitters: one. The same trap fired on `gpu_native.rs`, where
  `grep -c result_incomplete` returned 2 and both were inside the comment's own prose. Match the
  structural form (`"field"`, `def name(`, `fn name(`) and **read each hit** before concluding.
- **A census expectation can be wrong in BOTH directions.** Twice the mismatch was *my* expected
  number, not the code — an under-counted set of existing envelopes, and four sibling comments that
  were all correct on inspection. When a census disagrees with you, check the breakdown before
  filing; the finding is as often in the expectation as in the tree.

The general rule: write the check so its result does not depend on your prior. Then when it
disagrees with you, that disagreement is information rather than noise.

## Push Discipline

Do not push from a dirty worktree if `origin/main` moved and the local tree has unrelated changes.

A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state. Release versioning starts only after a release-bearing PR is squash-merged to `main`, because semantic-release reads the final `main` commit subject.

Merge one release-bearing PR at a time and wait for main CI + semantic-release to finish before merging the next. Concurrent squash-merges to `main` can race at the semantic-release step and produce a skipped release or a wrong version bump. `chore:` / `docs:` / `test:` titles do not bump the version — but that is NOT a licence to merge them while a prior release is in flight (see the push-race note directly below). "Safe to interleave" means *after the prior release has fully published* (its `chore(release): vX` commit is on `main` and PyPI shows the new version), not merely after its PR CI is green.

**READ the type, do not assume it (2026-07-27).** The push-race bites a merge that lands *while a
RELEASE job is pushing* — so the discriminator is whether one is in flight, and that is a fact you
can check rather than a risk you have to sit out. Open the newest main run and look at
`release-intent`: **`skipped` means no release will be cut for that commit**, so there is no push
to reject. On that evidence `test:`-titled #817 and `docs:`-titled #820 were merged back-to-back
(the earlier run's cancellation by the later push is benign — see the `cancelled != failure` note),
while `fix:`-titled #821 was held for the full one-per-publish cycle. Batch the non-releasing,
serialize the releasing; `gh run view <id> --json jobs` is the whole test.
**SUPERSEDED in part by A33 (2026-07-26):** the `release-intent` discriminator above is only
valid for deciding what a PR-triggered run will do. On a MAIN push run `release-intent` is
ALWAYS skipped (it is a PR-only title validator), so its skip state there proves nothing about
whether the `Semantic Release` job will publish; on main pushes decide by the commit-title type
(`fix:`/`feat:`/`perf:` release; `docs:`/`test:`/`chore:`/`ci:`/`build:` do not; `refactor:`
passes the title gate but does NOT publish under the default angular parser). The dated receipts
in this paragraph stand; the general rule yields to A33 where they conflict.

### Release publish is not instant — the push-race (hard-won, re-confirmed 2026-07-02)

The real publish is the **`Semantic Release` job inside `.github/workflows/ci.yml`** (gated `github.ref == 'refs/heads/main' && github.event_name == 'push'`), NOT `release.yml` (which is `workflow_dispatch`-only, so a manually-pushed `v*` tag can no longer bypass semantic-release). That job **compiles the native assets before it publishes, so it runs for ~6 minutes** — and that whole window is a race window.

If ANY other merge lands on `main` during that window — *including a no-release `docs:`/`chore:` PR* — the merge advances `main`, and the in-flight release job's final `git push origin main` (the `chore(release)` version-bump commit) is **rejected non-fast-forward** (`! [rejected]  main -> main`), so **that version never publishes**. The CI concurrency group is necessary but INSUFFICIENT: it serializes runs, not the human/agent act of clicking merge. Receipt: `v1.17.23` (a security batch, #318) failed to publish because the GPU-pause `docs:` PR (#319) was merged while #318's release job was still compiling assets.

Recovery — **do NOT panic-rerun**: the failure self-heals. The next push-to-`main` CI run re-runs `Semantic Release`, and because the version is **derived from the git tags** (not the failed run's state), it recomputes the correct next version and covers the orphaned `fix:`/`feat:` commit. Just confirm that next run's `Semantic Release` job succeeds and the tag/PyPI version appears; the fix's *code* was already on `main` regardless — only the publish step was behind.

Diagnosing a "didn't publish": decode the structured job result FIRST (`gh run view <id> --json jobs` → find `Semantic Release` → read `--log-failed`). Do not theorize from tracebacks. A `! [rejected]  main -> main` line is the push-race signature; a genuinely different failure is a different problem.

Preferred approach:

1. use a clean replay worktree
2. rebase/reset to current `origin/main`
3. rerun narrow checks and relevant benchmarks
4. push only the accepted change
5. open a PR with the correct conventional title and wait for PR CI/CodeQL to pass
6. if the change is release-bearing and intended to ship now, squash-merge the PR to `main`
7. wait for main CI and semantic-release complete successfully, plus CodeQL, `publish-github-release-assets`, PyPI/package artifact validation, `publish-pypi`, and `publish-success-gate`
8. also check the `release-tag-smoke` JOB's own conclusion inside the release run (`gh run view <id>
   --json jobs`), not just latest-main-green -- it is `needs`-gated on `[release, publish-success-gate]`
   (not `continue-on-error`), checks out the actual published tag and runs `agent_readiness.py` against
   it, and sat red across `v1.64.4`+ while PyPI kept publishing, masking a real regression for 4 releases
9. verify the GitHub release assets, PyPI latest version, and any affected public installer/update path. PyPI/public installer availability is verified before final release status is reported
10. after semantic-release completes, `git fetch origin main --tags` and fast-forward local `main` to the release commit before reporting the final version state

Do not report a release-bearing fix as complete after only a branch push, open PR, or green PR checks. The final report must name the PR, merge commit, main CI run, CodeQL run, released tag/version, PyPI/package publish status, and any local/public installer dogfood result.

For docs/test/chore-only work, use a non-release PR title, wait for PR CI, and merge only when requested or clearly required. After merge, main CI should pass but semantic-release should skip release publishing.

### Build ahead of a release gate (pipelining)

The push-race gate above blocks the *merge* step, not the *build* step. Once `origin/main` has advanced
past a collision-blocked PR's base, that PR can safely rebase, rebuild, and re-run its full local/CI
validation **in parallel with an in-flight release** -- only the final squash-merge must still wait for
the prior release to fully publish (the `chore(release)` commit on `main` plus PyPI). Doing the
rebase/rebuild/verify work eagerly, instead of sitting idle until the release window closes, saves
roughly 40 minutes per PR across a multi-PR drain campaign. This is the same shape as three well-known
patterns, named here so it is recognizable rather than reinvented: a **merge queue** / speculative CI
(validate against a projected future base before the real merge lands), a **release train** (fixed
publish cadence; work queues up between departures without blocking on any single publish), and
**build-once-promote-everywhere** (a single verified artifact is promoted through gates rather than
rebuilt at each one). Only the merge itself is push-race-gated; the build is not.

## PR Title And Release Intent

AI-generated PRs must use conventional titles so CI can infer semantic-release intent.

Use this schema:

- `feat: ...` => minor release
- `fix: ...` or `perf: ...` => patch release
- `feat!: ...` or `fix!: ...` => major release
- `docs: ...`, `test: ...`, `chore: ...`, `ci: ...`, `build: ...` => no release

Release-bearing PRs must use `Squash and merge` so the validated PR title becomes the commit subject on `main`.

- **Scope a PR's DIFF to what its TITLE promises.** The title becomes the changelog headline and a
  reviewer reads it as the contract for what is inside. When correct, reversible, unrelated work
  surfaces mid-PR, SPLIT it: a repo_map contract extension found while fixing a CLI cause-flag was
  pulled out of that PR and shipped as its own (#336/#826), and a docs/BACKLOG reconcile was kept off
  the session-laws capture PR (#337 vs #824). Being correct, reversible and yours does not earn a spot
  in the diff — only matching the title does.
Do not manually create release tags when semantic-release is active.

## Local Dev Gotchas (Windows, hard-won)

Small, non-obvious traps that have each cost a real cycle on this desktop. None are version-specific.

- **`uv run` in a bare worktree creates an empty `.venv` (A116, 2026-08-14).** Run worktree tests from the MAIN checkout's venv targeting worktree paths; delete any accidentally-created worktree `.venv` immediately.
- **`git commit -m "..."` with backticks runs command substitution.** A message containing `` `...` `` (e.g. a fenced identifier) is interpreted by the shell and mangles the commit. Use `git commit -F <file>` or a single-quoted `<<'EOF'` heredoc for any message with backticks, `$`, or `!`.
- **cargo/rustc are off `PATH` here — and a "hanging" Rust build is almost always a false alarm.** Use `C:/Users/oimir/.cargo/bin/cargo.exe` (or prepend `~/.cargo/bin` to `PATH`). What looks like a hang is slow LTO that *completes*: `maturin develop` is ~15 s, a `--release` build is minutes. Do not kill it as hung; let it finish. (The build command for stale in-tree binaries is under the doctor note above.)
- **Verify FFI / PyO3 bridge changes against the REAL compiled extension, not mocks.** This is the "Dogfood the Real Binary" trap one layer down: mock-based tests passed green while the *real* bridge was dead (it dropped every forwarded flag and silently fell back to the Python engine). Prove a bridge change with a live runtime call into the built extension, then confirm the flag actually reached `rg`.
- **After a squash-merge, apply follow-up fixes by SYMBOL, not by line number.** Merges shift every line below the change; a plan that says "fix `main.py:8468`" is stale the moment anything above it lands. Re-anchor on the function/const name (grep or `tg defs`) before editing.
- **A dependency UPPER-cap can silently downgrade the whole install on a newer Python.** If an upper bound (e.g. `typer<0.25`) has no release compatible with a new Python, `pip`/`uv` resolve the *entire package* DOWN to a stale version with NO error — `requires-python>=X` has no upper bound to catch it. When a fresh Python yields a stale `tg`, suspect a transitive cap (typer/click/pydantic), not `requires-python`.
- **A rule listing forbidden OPERATIONS is not a ban on the whole toolchain — check whether its REASON applies (2026-07-25, cost 3 CI cycles).** CPU-SAFE forbids `cargo`/`rustc`/`clippy`/`maturin` because they are *expensive on a shared box*. **`rustfmt` is not a compiler**: no codegen, parses+formats in milliseconds, `rustfmt.exe` is on PATH, and there is no `rustfmt.toml` so local defaults == CI's. Three CI cycles were burned hand-deriving format diffs from logs before anyone asked whether the rule's reason applied. Run `rustfmt --check` locally before pushing Rust. (It enforces `chain_width`/`fn_call_width` = 60, not just `max_width` = 100 — apply its printed diff verbatim; a hand-rolled width check is a heuristic, never authoritative.)
- **A local test-failure SPIKE is usually a missing optional dep — repair the instrument before
  theorising (2026-07-27).** A local run showed 90 failed / 80 passed across the `lang_*` suite and I
  spent two ticks building a correlation argument that the failures were unrelated (4 missing
  tree-sitter grammars ↔ 4 failures; csharp present ↔ csharp passing) before checking the interpreter.
  `python -m pip install --only-binary=:all: tree-sitter-{c,cpp,java,php,go}` (wheels only, so nothing
  compiles on a shared box) turned the inference into an observation: **170 passed, 0 failed**. Cause
  was a STALE interpreter carrying tensor-grep 1.83.0, ~18 releases behind — those grammars entered
  the extras after it; `pyproject.toml` declares all 11 unconditionally and is not at fault. When a
  measuring device gives false readings, repairing it is cheaper and far stronger than reasoning
  about the noise.
- **Cumulative CPU time is not current CPU rate (2026-07-25).** Two orphaned `find /` scans showed 20,548 s and 6,073 s of accumulated CPU — 7.4 CPU-hours — and killing both moved total load 74% → 73%. They had been accumulating slowly for hours, not burning now. Same shape as the cProfile trap: *cumulative ≠ blocking*. Before attributing a slow box to a process, measure its current rate, not its lifetime total. (Related: orphaned children outlive the shell that spawned them — `find /` on Windows via git-bash traverses virtual mounts and effectively never terminates.)
- **`MSYS_NO_PATHCONV=1` is REQUIRED for `git cat-file blob origin/main:path` on this box.** Without it git-bash mangles the ref into `origin\main;path` and the command fails *misleadingly* — it reads as "that path does not exist on origin/main", which twice produced a confident wrong conclusion (once nearly reporting a committed CI gate as a phantom). Same family as the "parse `gh --json` via python, never jq" rule.
- **`tests/conftest.py:8-15` does `sys.path.insert(0, SRC_DIR)` from `__file__`, which OVERRIDES `PYTHONPATH`.** A gate running a control arm with `PYTHONPATH=<baseline>/src` got a FALSE PASS because conftest silently re-pointed imports at the worktree. For any baseline/control arm in this repo, use a scratch mini-repo or a full second checkout as pytest's rootdir — and verify `tensor_grep.__file__` resolves where you think before trusting RED or GREEN.
- **Enumerate mechanically, never from recollection — this recurred THREE times in one session (2026-07-25).** A commit-count stated from memory was 6; `git rev-list` said 8. A path probed from a remembered name (`.pytest_tmp_review_472dffd9`) was a truncation of the real one and `Test-Path` returned false for a path that never existed, briefly "closing" a live task. A worktree-husk count estimated at 11 was 12 when derived from `git worktree list --porcelain`. In every case the mechanical derivation was one command away. If you are about to state a count, a filename, or a site list — derive it.
- **Windows symlink creation needs privilege.** Tests that create symlinks must `pytest.skip` on `OSError` / `NotImplementedError`, or they false-fail on an unprivileged run.
- **A stray `nul` file in the tree is a Windows `2>nul` redirect artifact.** Use `2>$null` (PowerShell) or `2>/dev/null` (bash); clean up with `rm -f ./nul`.
- **CRLF makes a local bare `ruff format --check` false-alarm** over LF-committed blobs. Run `ruff format --preview <files>` (which normalizes) before commit — see "Required Local Validation" for why `--preview` is mandatory and must never be passed to `ruff check`.
- **The full local gate is four steps, not two — and re-run them after your LAST edit.** `ruff check` + `pytest` passing is NOT green: the CI "Formatting & Linting" job also runs `ruff format --check --preview .` (a *formatter*, distinct from the `ruff check` *linter* — a post-edit line-wrap or over-long comment passes `ruff check` but fails `ruff format --check`) AND `mypy src/tensor_grep` (catches type errors nothing else flags, e.g. assigning to a `Final` attribute like click's `UsageError.message` — mutate it and mypy errors; raise a fresh `UsageError(...)` instead). Running only `ruff check` + `pytest` — or running the gate before an *intermediate* edit that a later edit then invalidates — cost two drain-blocking CI failures in a single session (a mypy `Final`-assign and a `ruff format` line-wrap). Run all four (`ruff check` · `ruff format --check --preview` · `mypy src/tensor_grep` · `pytest`) on the touched files AFTER the final edit.
- **Editing a CRLF file in text mode flips every line ending.** Python
  `open(path, newline="\n")` (or any text-mode write) on a CRLF-committed file
  (`ci.yml`, `uv.lock` are CRLF) rewrites ALL line endings — an 11-line change becomes
  a 1443-line diff. Fix: BINARY read (`rb`) + byte-replace preserving `\r\n` + binary
  write (`wb`). Same failure one layer down from the `ruff format --check` CRLF
  false-alarm above.
- **`uv lock` churns ~280 unrelated lines; hand-splice a new dep instead.** A raw
  `uv lock` reformats GPU/CUDA marker exprs (local-vs-CI uv-version mismatch). For a new
  dependency, hand-splice ONLY its `[[package]]` block (alphabetical) plus its
  requires-dist / optional-dependency refs. VERIFY with
  `uv export --format requirements.txt --all-extras --no-emit-project --locked` (must
  exit 0) — the exact `audit.yml` "Dependency & License Audit" gate that reddens every
  new-dep PR.

## Documentation Discipline

When a candidate is accepted or explicitly rejected, update:

- `docs/PAPER.md` if it changes the optimization history or benchmark story
- `README.md` / `docs/benchmarks.md` only after accepted benchmark changes

The paper should preserve failed attempts too, so future agents do not retry the same losing ideas.

## A BLOCKED Instrument And A Definitive Negative Look Identical (2026-08-01, #883-#887)

The false-zero law elsewhere in this file covers a probe that RAN and measured nothing. This is its
third face: a probe that **could not run yet** and answered anyway. Same shape on screen, opposite
meaning, and it fired **four times in one campaign**:

| probe | said | actually meant |
|---|---|---|
| `awk` range over `ci.yml` | job does not build the binary | the range pattern never matched; job builds it fine |
| `pytest --collect-only \| grep -c` | the forbidden module is excluded | `-x` aborted collection two files earlier |
| `gh run view --log` | 0 lines, so no failures | logs are undownloadable while the RUN is in progress (the JOB had already failed) |
| `uvx --from tensor-grep==X` | that version does not exist | stale uv index cache; the 4 wheels were on PyPI |

Every one was caught by a positive control, never by re-reading. The one time the control was
skipped, the wrong answer reached a committed audit document and survived until an outside seat
disproved it.

**Rules:**
- **Before believing a negative, prove the instrument can return non-zero right now.** Not in
  principle — on this input, at this moment.
- **`--refresh` is not always enough for a package index.** `uv cache clean <pkg>` is, and the
  discriminator between "release failed" and "cache stale" is the PyPI files endpoint
  (`/pypi/<pkg>/<version>/json` → `urls[]`). A version string can update before an index serves it.
- **A run being `in_progress` makes its logs unavailable even when a JOB inside it has already
  concluded.** Query the job's `conclusion` and its failing STEP name, which ARE available, rather
  than reading an empty log as a clean bill.

## Two Different Audits Beat More Seats Of The Same Shape (2026-08-01)

An 8-seat thinktank council and one `codex gpt-5.6-sol` pass audited the same plan. They overlapped
on **one finding out of nine**.

- Six seats across five providers ALL missed a 15-test collision and a red arm that could not fail.
  They converged on the most legible defect — a named test whose comment described itself — and
  stopped.
- Codex alone caught both, plus a mandatory gate the plan had waived.

**Consensus is not coverage.** Seats of the same shape share blind spots no matter how many
providers they span. Escalate BREADTH when the question is "what is wrong with this?"; collapse to
DIRECT VERIFICATION once the question is "is this specific claim true?" — round 2 of that same audit
needed no council at all, just four commands with positive controls.

Corollary, learned the hard way in the same round: **direct verification is only as good as the
probe.** Two of that round's answers were wrong until codex disproved them.

## Release Class Is Part Of The Fix (2026-08-01, #883)

A CWE-88 security fix sat in a `chore:`-titled PR. `scripts/validate_pr_title_semver.py` maps
`chore` → `"none"`, so it would have merged and **never published**. Users stay exposed while the
tracker says shipped.

**Before merging, ask what the PR title does to the release, and read the mapping rather than
recalling it.** `fix`/`feat`/`perf` publish; `chore`/`docs`/`test`/`ci`/`build`/`refactor`/`bench`
do not. A fix that does not ship is not a fix — and "merged" is the most convincing possible
evidence for something that did not happen.

## Separate ROUTING From EVALUATION Before Asserting End-To-End (2026-08-01, #884)

A new e2e test asserted `returncode == 0` for `--ltl` through the native binary. It failed on all
four OSes and would have failed **forever**: `native-build-smoke` runs `cargo build --bin tg`, which
never builds the PyO3 extension the LTL engine needs.

Two separable properties had been fused:

- **ROUTING** — the front door forwards the flag instead of clap-rejecting it. Needs only the binary.
- **EVALUATION** — the sidecar can answer. Needs the extension.

The fix asserts routing unconditionally and evaluation only where the engine exists, and the
routing-only arm still discriminates because a fail-closed refusal is textually impossible for a clap
rejection to produce. **Measured against the published wheel first** to confirm real users were
unaffected before relaxing anything — relaxing an assertion without that check is how a real defect
gets defined away.

## A Job That Cannot Reach A Surface Makes That Surface Invisible (2026-08-01, #884)

`test-python` has the Python deps but never builds the release binary. `native-build-smoke` builds
the binary but installed only `pytest`. So **no job could test native→sidecar delegation end to
end** — and the only possible symptom was a test nobody had written yet.

It surfaced only because an audit forced a new test into the job where a skip becomes a hard failure.
In its original location it would have skipped silently and reported green.

**When adding a test that crosses a boundary, check that some job can actually execute BOTH sides.**
Fixed by deriving the deps from `pyproject.toml` rather than hand-listing them — a hardcoded list
would rot exactly like the six prose enumerations this campaign fixed, and like the CI comment that
miscounted this very glob.

## Harden A Rule Only When It Is Mechanically Detectable AND Rarely Wrong (2026-08-01, #886)

`docs/TASK_BOARD.md` went stale a fourth time; nine open items were already fixed. Two candidate
gates were on the table, and the outcome split:

- **SHIPPED** — a *tolerance* on the reconcile stamp (>5 releases behind fails). Deterministic, no
  network, silent on a normal 1–2 release lag. The board's own header had rejected the STRICT
  equality form for firing every release, and never considered a tolerance.
- **RETIRED WITH REASONS** — a citation gate over board items. Measured: only 3 of 24 open items cite
  a file or symbol, and even those prove a citation *resolves*, not that the defect exists. The
  worked example is `--quiet`, listed OPEN for months after being fixed with a perfectly resolving
  citation.

**Harden when a violation is detectable without interpretation AND a false positive would be rare.**
When only one holds, write the retirement down with its measurement — a documented retirement stops
the next session re-deriving it, and an over-eager gate teaches people to reach for `--no-verify`,
which discredits every honest gate beside it.

## I Reproduced An Error A SKILL Explicitly Warned About, By Writing Prose From Memory (2026-08-02)

`tensor-grep-release-and-positioning` carries this row, verbatim:

> `refactor:` | patch | **Not listed in `AGENTS.md`'s prose table, but the validator script treats
> it as patch -- trust the script over the prose**

Hours later I wrote a fresh release-class summary into `CLAUDE.md` and listed `refactor` among the
non-releasing types. I "corrected" it against `scripts/validate_pr_title_semver.py`
(`"refactor": "patch"`) and wrote **"a `refactor:`-titled PR PUBLISHES."**

**That correction was ALSO wrong, and it was wrong for the same reason as the original: I derived
from ONE authority when there are TWO.** Measured 2026-08-04 on PR #915 (a `refactor:` PR, merged
`3faf500`): the Semantic Release job logged *"No release will be made, 1.102.4 has already been
released!"*, `publish-pypi` was SKIPPED, no tag was cut, PyPI stayed at 1.102.4.

The two authorities, and what each actually governs:

- `scripts/validate_pr_title_semver.py::_RELEASE_INTENTS` maps `refactor`→`patch`. It gates what the
  PR TITLE may be. **It publishes nothing.**
- `[tool.semantic_release]` in `pyproject.toml` is the publisher, and it configures **no**
  `commit_parser`, `allowed_tags`, or `patch_tags` — so python-semantic-release uses its DEFAULT
  angular parser, whose patch types are `fix` and `perf` ONLY. `refactor` is not among them.

So the title gate ACCEPTS `refactor:` as a patch-intent title and the engine then makes no release.
The code is not lost — an unreleased `refactor:` ships with the next `fix:`/`feat:` merge — but a
refactor-ONLY run leaves `main` unpublished while every tracker reads "shipped".

- **The skill had already diagnosed this exact field as a prose-vs-script drift risk, named the
  remedy, and I still re-derived the wrong value from memory.** A warning is not a guard.
- **Never summarise a mapping; derive it -- and first ask WHICH artifact actually performs the
  action.** Deriving faithfully from a real file is still wrong if that file does not do the thing.
  Ask both: `grep -A12 _RELEASE_INTENTS scripts/validate_pr_title_semver.py` for what the title gate
  ACCEPTS, and the `[tool.semantic_release]` block for what actually SHIPS. **The decisive check is
  neither: read the Semantic Release job log for the merge and see what it decided.**
- Found only because a routine "what should Semantic Release decide for this merge?" check printed
  the real dict beside my recollection. **Print the authority next to the belief** -- the disagreement
  is invisible if you only print one.

## Three Independent Gates Found Four Defects I Did Not (2026-08-02, #904)

The strongest single receipt in this repo for the mandatory adversarial gate. My own verification
reported **zero regressions across 41 derived files** on a `perf:` change that was carrying a HIGH
defect. Every one of the four was found by an outside reader:

1. **HIGH** -- `_context_tests` has TWO call sites; only one carried the counter, so a budget
   expiring in the uncounted scan reported `scanned == total`, the attribution exonerating the very
   stage that stopped.
2. **MEDIUM** -- the impact call site's comment ("a tighter source list cannot change any output
   impact actually returns") was FALSIFIED by measurement: `association.confidence` strong->weak.
3. **MEDIUM** -- the invariant the whole fix rested on (`+=`) had ZERO coverage; a one-character
   revert kept all 38 sibling tests green.
4. **NIT** -- I then added `assert scanned <= total` INSIDE the fix for a can't-fail-check finding.
   It is true in both arms.

**The root cause of 1-3 is a single sentence: every parity arm ran a 32-file fixture where the
2000-file ceiling is UNREACHABLE** -- the one population where the bound cannot fail. I tested the
bound where it was a no-op and called it parity.

- **A gate's verdict is also a hypothesis.** Its suggested remedy for the HIGH was wrong: naive
  accumulation reports `scanned=12` against `total=8` when a completed scan meets a stopped one.
  Accumulating BOTH keeps `scanned <= total` an invariant.
- **Re-derive the blocking finding yourself before accepting it**, and re-gate after fixing --
  "I fixed the gate's findings" is exactly the self-report a gate exists to distrust.

## A Board Can Be Perfectly FRESH And Structurally WRONG (2026-08-02, #909/#910)

A docs edit deleted `## BLOCKED — environment` from `docs/TASK_BOARD.md` and merged. The three
items did not vanish -- they were **silently refiled under the preceding section**, which is worse
than losing them: a dispatcher reading the new heading could pick up hardware-blocked work as
actionable.

`test_task_board_freshness` passed throughout. It checks the reconcile stamp's RECENCY, never the
document's STRUCTURE.

- **What caught it was a COUNT printed beside its expected value** in the hourly update:
  `UNSHIPPED ARTIFACTS = 4` where it should be 1, and `1 + 3 = 4` named the swallowed header.
- **No gate was built, and the retirement is the finding.** Measured: an orphaned-item check reads 0
  both before and after (the items kept *a* header); a duplicate-name check is unrelated; a pinned
  SET of expected headers is a list written at authoring time, which this session has four receipts
  of rotting. The violated property is DIFF-LEVEL -- *an edit intending to change one item must not
  remove a header* -- and a test reads the file, not the intent.
- **The real fix is upstream:** anchor a string replacement on the text being REPLACED, not on a
  scan for the next sibling. `s.find("\n- [", i)` ends at the next ITEM, and a header between two
  items is inside that span.

## `ast.walk` Inside `ast.walk` Counts Every Call Once Per Enclosing Scope (2026-08-02)

Verifying the merged #904 artifact, `for fn in ast.walk(tree) for n in ast.walk(fn)` reported **10**
call sites where the truth was **2** -- each call re-counted once per scope containing it. I made
this exact mistake twice in one session, and the second time it was in the probe certifying that a
release had landed correctly.

Walk the tree ONCE and filter. An AST probe is code, and it deserves the scrutiny of the thing it
is about to certify -- more, when it is the last check before a release claim.

## Scoping The PATCH SITE Does Not Scope The OBSERVABLE (2026-08-02, #904)

Three tests named `*_context_tests_deadline_folds_into_partial` passed on a baseline where
`_context_tests` **did not accept a `deadline_monotonic` parameter at all**. A test cannot observe a
parameter that does not exist. They were asserting `partial` / `deadline_exceeded` -- **shared
booleans that any of this module's 24 `time.monotonic` readers can set**.

The obvious fix looked airtight and FAILED. Re-scope the rig from `_score_file_path` (five call
sites) to `_test_graph_score`, which an AST walk confirms is called from `_context_tests` and
NOWHERE else. Implemented; mutation asserted applied (3 scoped call sites, 0 global); **all three
still passed**, counts byte-identical. Because the clock is global: advancing it anywhere trips the
next DOWNSTREAM check, which sets the shared boolean by itself.

- **A uniquely-called patch site does not give a uniquely-caused observable.** Enumerate the
  **WRITERS of the value you assert** -- a different population from the callers of the function you
  patch.
- **A shared boolean cannot attribute a cause.** Discriminating required the FIX to expose an
  attribution (`test_candidates_scanned`/`_total`), not a better test.
- **Revert a non-fix and keep the finding.** A scoped rig that still passes both arms adds code
  without adding a check and reads to the next person as "fixed".

## A Missing OPTIONAL Dep Makes A Suite Misleading In BOTH Directions (2026-08-02, #905)

`tree-sitter` is an optional extra, so a plain install has no parser. In that env
`tests/unit/test_parse_product_cache.py` did not merely go noisy:

- **5 tests FAILED** with messages that read like product bugs ("expected at least one reference
  to computeWidgetTotal") -- inviting a hunt for a broken emitter.
- **Several PASSED VACUOUSLY, and that is the worse half.** Their whole claim is that a parse did
  NOT happen (`assert calls["n"] == 0, "...must not parse"`). With nothing able to parse, that
  assertion cannot tell a correct early-exit from an absent grammar.

**Gate the MODULE, not the loud failures.** Fixing only the 5 visible reds would have left the quiet
vacuous passes exactly as they were. And prove the gate is a CONDITION, not a blanket skip: gated on
`tree_sitter` -> 1 skipped; gated on `json` -> 5 failed / 9 passed, i.e. it does not fire.

**Dogfood before calling it a product defect.** `_js_ts_references_and_calls` returns `[], []` with
no parser -- textbook silent-empty on a tier-1 language. The real CLI refuted it: `tg refs` on a
`.js` file in that same venv still finds the references (`result_incomplete: false`, exit 0),
because the pipeline has another route. Reading the helper says "defect"; running the product says
"fine".

## A Population Floor Must Be Calibrated On The SAME Population (2026-08-02)

A merge monitor carried a `>= 40 jobs` floor so a partially-dispatched run could not read as green.
It fired on a complete run. The floor came from **48 CHECKS in a PR rollup (all workflows)** and was
being applied to **39 JOBS in one `ci.yml` run** -- different populations, so the number was never
comparable. A sibling control settled it: `main`'s own `ci.yml` run also has exactly 39.

- **Name the population when you write a threshold** -- "39 jobs in ci.yml", never a bare "40".
- **`gh run rerun --failed` legitimately produces FEWER jobs** (failed + dependents only), so a
  floor calibrated on a full run false-alarms on every rerun. Expected, not a red flag.
- The rollup count and the run's job count are both useful and are not the same measurement.

## A List Written At DISPATCH Time Is Stale By DEFINITION (2026-08-02)

Three instances in ONE session, each in code I had just written, and the third **within an hour of
writing the law about the first two**:

| instrument | hardcoded | what it missed |
|---|---|---|
| the hourly backlog cron | PRs #886, #887 | #888, opened after the cron was armed -- it sat green with nothing watching it |
| my eligibility scan | first line of each board item | a **CEO-GATED** item marked ELIGIBLE, because the gate sat on line 3 of a 4-line entry and even said "not an AI-doable item" |
| my merge drain | PRs #891/#892/#893 | #894 and #895, opened after -- orphaned, no mechanism would land them |

**Derive the set at USE time, never at authoring time.** `gh pr list --state open` on every pass, not
a list baked in when the loop was written. Multi-line entries must be ACCUMULATED before matching --
a line-based filter silently truncates the item it is judging.

This is the same defect as the six prose enumerations this repo fixed the day before, and the CI
comment that miscounted its own glob. **Writing the law does not immunise you against it**: the third
instance was authored after the first two were documented. The only durable fix is structural -- if a
loop can enumerate, it must not be handed a list.

## A Constraint's REASON Defines Its Scope, Not Its Wording (2026-08-02)

The WIP cap ("do not exceed ~3 open PRs") exists because **release-bearing** PRs drain one-per-publish
and merging two into one publish window rejects the release push. I applied it to non-releasing
`docs:`/`test:` PRs, which batch freely -- and throttled a five-item fan-out to one item per hour
until the CEO called it out.

Before applying a rule to a new case, state the rule's REASON and check that it holds there. The
identical failure is on record from 2026-07-27 (a Workflow-only no-clock policy generalised to a
hand-run script) and 2026-07-24 ("do not COMMIT this file" read as "do not FIX this file"). Third
receipt for one law: **a constraint on one class is not a constraint on its neighbour.**

## Briefing A MECHANISM Is Asserting A HYPOTHESIS (2026-08-02)

Three times in one session I handed a subagent a mechanism and the subagent proved it wrong. It was
right all three times:

- **"the escalation grep returns 16"** -- 14 of those were inside gitignored `.tensor-grep/checkpoints/`
  snapshots. Tracked-only: **2**. The agent's number was right and mine was contaminated.
- **"imitate `nlp_backend_unavailable_fallback`, it sets `fallback_reason` like every sibling"** -- it
  does neither. That branch is a silent swap too. My brief AND the investigation doc repeated the same
  wrong claim.
- **"the classifier drift triggers on metavar patterns (`$NAME`/`$$$ARGS`)"** -- metavars were
  **already safe in both copies**; they fail the native-pattern regex either way. The real reachable
  trigger is a native-SHAPED pattern plus `ast_selector`/`ast_strictness`/`ast_stdin`/`glob`.

**Brief the SYMPTOM and the evidence; require the agent to re-derive the mechanism.** A brief that
states the mechanism as fact invites an implementer to build the wrong fix and call it done -- and the
PR body then ships your wrong explanation as the project's record. When corrected, fix the ARTIFACT
(PR body, plan, doc), not just the next message.

## After A Fix, A Grep Hit Is Often The Fix's OWN DOCUMENTATION (2026-08-02)

The census-satisfied-by-a-comment trap has a mirror on the other side of the repair, and it fires
exactly when you are verifying success:

- `grep -c "cast(ComputeBackend,"` returned **1** after the NameError fix -- the hit was the docstring
  explaining the trap.
- `grep -c "requires_ast_grep_wrapper"` returned **1** in `main.py` after the shim collapse -- the hit
  was the docstring explaining the drift.

Both read as "still broken". Both were fully fixed.

**Self-demonstration, one turn after writing this law.** Dogfooding v1.101.31, my own probe tested
`'requires_ast_grep_wrapper' in ast.unparse(fn)` -- and `ast.unparse` INCLUDES THE DOCSTRING. It
printed `VERDICT: REGRESSION` on a correct wheel. The fix is to count AST **nodes**
(`ast.Name` / `ast.Attribute`), never string containment over a region that also contains prose:

```python
refs = [
    n
    for n in ast.walk(fn)
    if (isinstance(n, ast.Name) and n.id == TARGET)
    or (isinstance(n, ast.Attribute) and n.attr == TARGET)
]
```

A verification probe is code, and it deserves the same scrutiny as the code it verifies -- **more**,
when it is about to tell you something shipped correctly.

## `git stash` Is UNSAFE Once Parallel Worktrees Exist (2026-08-02)

Git worktrees **share `.git`'s stash refs**. Five agents working in five worktrees are all reaching
into one drawer. A red-arm revert via `git stash` / `git stash pop` popped a DIFFERENT agent's stash
and produced a conflict in a file that agent had never touched.

Worse, that stash was **orphaned** -- its branch had no live worktree, so any parallel agent could
have destroyed it. Preserved non-destructively with `git branch <rescue-name> stash@{0}`, which
creates a permanent ref without checking out or popping.

**For a red-arm revert, use `git checkout -- <file>` against a known commit, or a patch file.** Never
`git stash` while another worktree is live. Second receipt for the lurking-stash hazard; parallelism
is what made a known risk actually bite.

## Committed Is Not Shipped (2026-08-02)

A 27 KB research document sat **committed locally and never pushed** in its worktree. Its findings
were read, reported to the CEO, and acted on -- while the artifact itself existed nowhere anyone else
could reach. Discovered only by DERIVING the eligible-item list rather than trusting my own memory of
what I had handled.

**A subagent that reports "committed, not pushed, per instructions" has handed you an obligation, not
a completion.** Land it in the same turn you consume its findings, or it is invisible work.

Corollary, from the same session: **reconcile the board at completion, not "next cycle."** A board
goes stale in the gap between finishing work and recording it; 17 stale entries accumulated exactly
one deferral at a time, and one of them cost a dispatched agent (#58, already finished).

## Seven Instruments, One Empty Queue, And A Release That Reddened Every Open PR (2026-08-04)

The language-promotion campaign (Java/C#/PHP/C/C++ waves, #927-#935) plus the v1.103.0 release
window. Cost: main red twice, three false "ships broken" verdicts against a correct published wheel,
a false live-CWE-88 report against a guarded file, and one committed module using constants it did
not define. As always, most rows are the instrument, not the subject.

| instrument | the believable answer | the truth, and what caught it |
|---|---|---|
| `_releases_behind` in `tests/unit/test_task_board_freshness.py` | "the stamp exhausted its tolerance" | the helper returned the SENTINEL (`_MAX_RELEASES_BEHIND + 1`) on ANY major.minor mismatch, so v1.103.0 -- a MINOR bump -- made a stamp ONE release behind red main. NO tolerance value could fix it: the sentinel is tolerance+1 by construction and the assert is `<= tolerance`. Fixed by ordinal distance in CHANGELOG.md, which semantic-release rewrites in the SAME commit as the version stamp (#933; the test's own docstring carries the receipt) |
| grep for `"--"` argv sentinels in `apply_policy.py` | zero hits -> "live CWE-88 vector" | the guard exists in a shape the grep never named: `_policy_file_arg` returns `f"./{relative}"` for dash-led names, which neutralizes flag injection without any `--`. Re-derive: `grep -n 'f"./' src/tensor_grep/cli/apply_policy.py`. Symmetrically, a HIT can be the fix's leftover NAME: `codemap.py`'s `_atomic_write_text` survives as a thin wrapper DELEGATING to `atomic_write_bytes` |
| a settle probe: `all(bucket != "pending")` over a PR's check-runs | "every lane ran; none pending" | jobs gated `needs: smoke` (`grep -c 'needs: smoke' .github/workflows/ci.yml` -> 12, plus `release` naming smoke in its needs list) have NO check-run at all until smoke finishes -- ABSENT, not pending. The assertion was VACUOUSLY TRUE over the 11-check pre-smoke view, which structurally cannot contain a test lane. Proof: 11 -> 39 check-runs the instant smoke ended |
| per-PR CI, green at merge time | "safe to merge" | v1.103.0 published 21:06Z; #928 merged green 21:32Z and reddened main (run 30952799876) -- its checks ran against a base predating the release, so the identical commit was out of tolerance at merge. Form 10 with TIME as the second slice: union-testing cannot catch it, because the colliding slice did not exist when the union ran |
| dogfood of the published wheel | "the feature ships broken", three separate times | (1) the bare wheel lacks the `ast` extra, so the grammar was absent; (2) the control script read an empty `LANGUAGE_REGISTRY` because registration happens on `repo_map` IMPORT, which the probe never performed; (3) the probe omitted the keyword-only `parser=` argument the product itself passes. Three clean, believable zeros, all in my probe |
| a C++ "unseen base class" fixture | "defect: it confirmed a call it could not see" | the fixture declared `struct Base` IN THE SAME FILE -- the probe's INPUT carried the property under test, so the confirmation was correct. Re-run with a genuinely invisible base: zero confirmed, as designed |
| `git merge-base --is-ancestor <branch> main` as a branch-status probe | "seven language branches still active" | a squash-merged branch is NEVER an ancestor of main, so is-ancestor reads every merged branch as live. All seven were merged. (A30 uses the same check safely -- as a PRUNING proof, where the false "active" is the conservative direction; as a STATUS oracle it is wrong on every squash-merge) |

The rules, each priced by a row above:

- **A sentinel is not a threshold, and the wrong diagnosis discredits the right fix.** "Tolerance
  exhausted" licenses raising the tolerance -- which cannot work when the failing value IS
  tolerance+1 -- and that fix's failure then argues against the correct ordinal-distance diagnosis.
  Before tuning any knob, confirm the failing value is a MEASUREMENT and not a sentinel.
- **Grep for the guard's PURPOSE, not one spelling of it.** A zero cannot separate ABSENT from
  PRESENT-IN-ANOTHER-SHAPE; a hit can be the fix's own name or docstring (the 2026-08-02 grep-hit
  law is this rule's mirror). Check behaviour; count AST nodes, never substrings.
- **A merge/settle gate must require the heavy lanes to be PRESENT by name or count**, never
  "nothing pending" -- a `needs:`-gated job is invisible, not pending, before its gate completes.
- **After any release lands, every open PR's green is STALE.** Before merging, check whether a
  release published since the PR's last CI run (CHANGELOG.md head vs the run's timestamp); if so,
  re-run or rebase first. Per-PR CI cannot see this by construction. And read the FAILURE COUNT,
  not the red-row count: #930's "7 failing lanes" were ONE gate (6331 passed, 1 failed).
- **Dogfood through the adapter the product uses, and assert the optional deps are present FIRST.**
  Install the same extras, import the module that performs registration, call the exact signature
  the product calls -- or the zero you measure is your environment.
- **Premise-check the queue: SIX of six ready-to-build items were already shipped** (#58, #858,
  #859, #862, #864, #865; recorded in docs/BACKLOG.md, PR #935). A plan written against a fixed
  defect has perfectly resolving citations, so anchor-checking cannot catch it -- only reproducing
  the defect can. The 2026-07-27 "Check Whether It Already Shipped" law, now measured at a 100%
  rate on one queue.
- **A branch's status oracle is its PR state** (`gh pr list --head <branch> --state all`), never
  `--is-ancestor` or `--merged`. And skip any worktree with uncommitted files even when its PR is
  merged -- another agent's WIP can live there.
- **Never edit a worktree a live agent owns, and never `git add -A` in a shared tree.** A
  "completed" notification is not proof the agent stopped writing -- probe file mtimes first.
  Committing a concurrently-rewritten file produced a module using constants it did not define.
- **Correct the ARTIFACT chain, and never rewrite a dated receipt.** A wrong claim, once falsified,
  gets fixed in the doc, the PR title AND body, and the memory file -- a wrong record re-teaches
  the wrong lesson. A dated receipt's quoted output is never edited in place: append a SUPERSEDED
  entry (the live chain: `tensor-grep-enterprise-agent`'s language-coverage row).

## Session Lessons (2026-08-07, campaign continuation)

Dated, drop-in lessons from the audit-campaign drain; the full detail is in `docs/SESSION_HANDOFF.md` "Session Lessons (2026-08-07)".

1. **Never copy working-tree files from a stale local checkout into a worktree branch.** A checkout 2 releases behind `origin/main` makes its working files stale; copying them into a fresh worktree branch silently reverts merged changes (caught by diff review on M13 — reverted the P1 twin-sweep + P2's byte-guard and H4 seed). Apply the DELTA onto a fresh `origin/main` worktree, or `git diff origin/main -- <file>` first.
2. **`gh pr merge --delete-branch` can abort locally on a dirty tree while the remote merge SUCCEEDS.** Judge by `gh pr view <n> --json mergedAt`, never by the command's exit; in a dirty shared tree, merge without `--delete-branch`.
3. **A red main run that SKIPS Semantic Release is recoverable by the NEXT main push** (verified live on v1.110.1, after #968's run red on a confirmed `windows-agent-readiness` flake). Diagnose reds by the failing JOB's per-probe summary + "did the same job pass on the PR CI", before calling it environmental.
4. **A ratchet test firing is a POSITIVE signal**: fix the class → LOWER the ratchet's recorded count in the SAME PR (its failure message says so); it is not a product regression.
5. **CI's `ruff format --check --preview .` formats Python code fences INSIDE Markdown** — preview-format any committed Markdown containing code fences, or `Formatting & Linting` reds while scoped `.py` checks pass.
6. **Tight byte/token envelope tests are platform-fragile** (local tmp_path length vs CI; #525 history) — re-pin with a documented margin + substance asserts when a legitimate field growth tips one.

## CI Cost Discipline (2026-08-07, from a real account-cutoff incident)

**You cannot see this cost at the moment you cause it.** You edit YAML; the bill arrives weeks later through a chain of multipliers none of which announce themselves. `macos-latest` is a word in a matrix, not a ~10× rate. 3 OS × 2 Python is four lines of config and **six billed runners**. A private repo looks identical to a public one while billing every minute. Before every workflow change, look the cost up deliberately — intuition has no signal here.

Four things agents get wrong, in the order you'll hit them:
1. **`paths-ignore` first on a REQUIRED check.** Branch protection never sees the run, waits forever, and every docs PR becomes permanently unmergeable — you converted a cost problem into a delivery outage. Keep the job; skip the steps.
2. **"Just run it locally in Docker"** as the gate. The CI run is the merge arbiter; a local green proves YOUR machine, not the commit. Fine for pre-flight, wrong for the gate.
3. **Writing the rule in CLAUDE.md/AGENTS.md.** A documented remedy that nothing enforces is a comment — the author violated several laws the same day they catalogued them; hooks and CI caught it, prose didn't.
4. **Fixing all the repos.** 3 of 29 were 84% of the bill. Measure first, then fix the ones that ARE the bill.

**The sampling-window trap (ours, emphasised):** when you investigate a CI incident, your natural window is recent runs — which is exactly the window the incident is corrupting. Sampling the 40 most recent runs showed a confident `$0.00` cost because they were billing-blocked and never ran; a "cron firing every 80 seconds" was the block replaying queued schedule events. Before believing any zero, prove your probe can return non-zero on a case you know consumed minutes, and sample from before the incident.

**The ordering that matters:** **cap it → fix the structure → write the skill → optionally the rules.** The cap is first because it's the only control that survives every other control failing — the same reason a pod fire carries `--max-cost-usd`: the cap holds when the careful design has a hole nobody's found yet. Cost-cap and spend-alert controls belong in the repo/pipeline (GitHub Actions budget alerts), not only in prose.

**The enforced mechanism (shipped 2026-08-08, #977 — beyond prose):** a cheap `changes` job detects whether a PR's diff touches code (`src/`, `rust_core/`, `tests/`, `.github/workflows/`, `pyproject.toml`, `Cargo.toml`, `Cargo.lock`, `uv.lock`), and the expensive/cross-platform jobs gate on `if: github.event_name != 'pull_request' || needs.changes.outputs.code == 'true'` with `needs: [smoke, changes]`. Two hard facts that made it safe:
1. **PR-only gating is mandatory — `release` `needs:`s every gating job, and a SKIPPED dependency skips a dependent unless it uses `always()`.** Gate on `push` and the publish is silently lost. Main pushes always run the full matrix; only docs-only PRs skip.
2. **A job skipped by an `if:` counts as SUCCESS for branch protection; `paths-ignore` on the trigger gives NO status → merge deadlock.** Job-level `if:` skip is the only safe cost lever.
Validator-backed pins that asserted the literal old shape (`needs: smoke`) must be updated to assert SUBSTANCE in the same change — and a council's "these tests survive it" is a hypothesis until the tests are actually run.

> Provenance (2026-08-12 retention audit): these two sections existed ONLY in the dirty
> `audit/h6-cudf-backend` working tree — never committed to any ref (pickaxe-verified across
> `--all`). The 2026-08-12 stale-branch reconciliation classified that tree's dirty docs as
> "stale snapshots, BEHIND not novel" on a one-file spot-check; this content is the counterexample
> and was landed verbatim by the retention PR rather than cleaned up.

## Bottom Line

Work like this:

1. test first
2. smallest change
3. local lint/type/test
4. benchmark
5. reject regressions
6. push only measured wins or required correctness/CI fixes

Do not use code-intelligence budget flags as `tg search` options; scope `tg search` with paths, globs, file types, and depth.


## The Instrument Fails More Than The Subject: 12 vs 5 In One Audit (2026-08-19)

A full enterprise audit + 13-PR remediation. **Eight classic security vectors were probed and
eight came back already hardened.** The real defects were ~5, and **12 instrument failures**
occurred while finding them — **5 of the 12 were the auditor's own probes, not the codebase's.**

In a repo this heavily pre-hardened, that ratio is the finding: budget verification effort on
the assumption that your measurement is wrong before the code is. Every one of the 12 was caught
by a CONTROL, never by re-reading code — reading code confirms what the code says, and in each
case the code and the measurement disagreed.

### A path filter is an ENUMERATION, and enumerations drift — three holes, one class

`ci.yml`'s cost-smart `changes` job gates the expensive lanes on a hand-written path list. Three
populations were missing from it, and each produced a **green PR that had tested nothing
relevant**:

| unwatched | what silently skipped | fixed |
|---|---|---|
| `scripts/`, `benchmarks/` | every test lane — a 3,500-line refactor merged having run zero tests | #1022 |
| `docs/`, `.claude/skills/` | the doc/skill governance suites, in a repo whose documented failure mode is docs contradicting the product | #1024 |
| `docs/` -> `static-analysis` | **the formatter** — `ruff format` formats Python inside markdown fences, so a docs PR reddened `main` and surfaced on an unrelated code PR | #1027 |

**None of the three was found by reading `ci.yml`.** All three were found by noticing
**unexpanded matrix placeholders** in a check-run list — `test-python (${{ matrix.os }}, ...)` is
what a never-instantiated job looks like, and it is visually identical to a pass in any summary
that counts `failures == 0`. **Require EXPANDED lane names before calling a PR green.**

The third was self-inflicted: the remediation shipped a doc through the hole the previous fix had
just documented.

### A limitation you have WRITTEN DOWN is not a limitation you have APPLIED

The monkeypatch binding auditor is blind to modules loaded via `spec_from_file_location`. That was
documented in the tool's own PR message (#1018) — and a refactor brief was written against it two
hours later ("zero patch sites, risk is low"; the real count was ~150). Only converting it into a
MECHANISM stopped it: the tool now prints a `SPEC-LOADED` warning and exits 1 rather than
reporting a confident zero. **A prose limitation gets violated by its own author.**

### A CI job's NAME is not its failure

`Formatting & Linting` failed on **mypy** — that job runs both. Reproducing the wrong tool wasted
a cycle; reading the log gave the answer in one step.

### Splitting a file has a HARD FLOOR set by test-patch topology

Python resolves bare names through the **defining module's** globals, so every function
referencing a monkeypatched name by bare identifier must stay physically co-located with wherever
the test's `setattr` lands. For `run_gpu_native_benchmarks.py` that call-graph closure is
**1,752 lines / 17 functions** — the file cannot reach a 1,500-line limit by splitting at all.

**Derive the closure of monkeypatched names before scoping a split wave. A line count is not a
split plan.** Expect the same wall on `cli/main.py` and `cli/repo_map.py`; those need dependency
injection, not file moves. Lowering a ratchet pin is the honest outcome there — forcing the number
down means changing behaviour to satisfy a gate, which is the failure the gate exists to prevent.

### mypy strict makes a facade re-export a SPLIT-ONLY failure class

After a split, `from .impl import X` in the facade is a PRIVATE binding under
`implicit_reexport = false`: runtime resolves it, mypy fails `attr-defined`. It cannot appear
before the split, because the symbol was locally defined and nothing was re-exported. Use
`from .impl import X as X`, and re-check that `ruff --fix` did not merge the import blocks back
together — its organiser silently dropped six names from a merged block in this same campaign.

### Four more, each a probe rather than a subject

- **`cp` from a stale tree into a fresh worktree reverted 65 commits of `ci.yml`** — the `changes`
  job, ten `needs:` guards, a security-test guard from PR #1010, and a matrix exclusion — and
  MERGED GREEN, because nothing compares a workflow file to the version it replaces. Edit the
  fresh copy in place.
- **A byte-identical control run from `/tmp`** returned exit 1 and 0 bytes because the script
  resolves its repo root from `__file__`; the verdict read "DIFFERS". A 0-byte difference is the
  shape most easily misread as a real finding.
- **A CI monitor built on `jq`, which is not installed here**, emitted nothing for twenty minutes.
  Silence is indistinguishable from "still running".
- **`grep -c` counted the fix's own docstring** — twice — and each time appeared to contradict a
  correct agent. Count AST nodes, not substrings.

### What worked, and is worth repeating

- **A grandfathered fail-closed RATCHET beat a big-bang refactor.** A 7-seat council was 7/7
  against the big bang. The ratchet then caught the campaign's OWN work three times, and each time
  the correct move was to obey it rather than re-pin around it.
- **A gate that derives its own census.** Every hand-scoped count of the violating population was
  wrong (19 -> 33 -> 35). Numbers must come from the product at execution time.
- **Subagents told to distrust the brief.** Briefs were wrong in FOUR consecutive waves and the
  agent caught it every time. State plainly that prior briefs were wrong and that a false premise
  is a finding, not a failure.
- **AST-equality as the split proof** (`ast.dump(ast.parse(ast.unparse(node)))`, 0 missing / 0
  extra / 0 mismatched) — stronger than a passing suite, and obtainable when no runtime baseline
  exists.

## A Tool Honest About Its Direction Of Error Stays Useful When It Is Wrong (2026-08-19)

Same-day sequel to the instrument law above, and the sharpest single receipt in it.

`scripts/measure_split_floor.py` was built to answer "can this file reach the line limit by
splitting?" It measures the lines welded to a module by test patches. Its first version **omitted
the most obvious members of that set: the patched functions themselves.**
`monkeypatch.setattr(mod, "f", ...)` rebinds an attribute on `mod`, so `f` must be defined in `mod`
— but the tool locked only the functions that *reference* `f`. All nine of `agent_capsule.py`'s
patched symbols are top-level functions in it, and none was counted.

Reported floor **1,190** ("split is viable"). Real floor **1,527** — above the limit.

**Two things made this a correction instead of a wasted wave:**

1. **The tool declared its direction of error.** Its docstring said *"this is a LOWER bound; a
   number over the limit is decisive, a number under it is encouraging, not a guarantee."* That
   sentence is the whole reason a wrong number stayed safe: the "cannot split" verdicts were
   unaffected (undercounting only pushes them further over), and the one verdict the error could
   corrupt — "viable" — was already labelled as non-binding.
2. **The dispatched agent re-derived rather than trusting the brief**, and reported the mismatch as
   a finding. Fifth consecutive wave in this campaign where the brief was wrong and the agent
   caught it.

### The transferable rules

- **When you build a measuring tool, state which way it errs, in the tool.** Not "this is
  approximate" — *which direction*, and *which conclusion that makes unsafe*. A tool that says "I
  under-report" lets a reader keep the half of its output that still holds.
- **The dangerous direction is the permissive one.** Here a too-low floor reads as permission to
  act. Bias any estimate that gates an action toward over-reporting the obstacle.
- **Cross-validate a new tool against an independently derived answer before briefing from it.**
  The corrected tool now returns 10 functions / 1,527 lines, matching the agent's independent
  derivation exactly. That agreement is what makes it trustworthy — not that it ran clean.
- **A locked function can be locked transitively without being patched itself**, and that is the
  escape hatch. `build_agent_capsule_from_map` (834 lines) was locked only because it bare-called
  three patched names; rewriting those three call sites to qualified lookups freed the whole
  function. Route A from `docs/design/2026-08-19-split-floor-escape.md` works at function
  granularity, so a file can be rescued by converting a handful of call sites rather than all 337.

## A Green Gate Bounds One Failure Mode, Never The Family It Belongs To (2026-08-19, merge wave)

Six receipts from landing five PRs in one evening. Every one is a check that was **working
exactly as designed** and still let something through, because the thing it caught and the
thing that bit were neighbours rather than the same defect.

| the gate | what it genuinely catches | what walked past it |
|---|---|---|
| `test_skill_library_drift` | a citation pointing **past the end** of a file | a citation that still **resolves** and now points at unrelated code |
| a PR's own CI | that branch, against the base it was cut from | a conflict with a second PR that is also green |
| `for i in 1 2 3; do … done` | a transient failure on attempts 1–2 | **exhaustion** — the loop exits 0 on its last iteration regardless |
| `file_size_budget` | growth of an allowlisted file | growth re-pinned in the same commit, which the gate then calls clean |
| a 30s wall-clock bound | a drain regression | PowerShell's startup, which is what it was actually measuring |
| `gh pr checks` | a job that ran and failed | a job that **never instantiated** (`${{ matrix.os }}` unexpanded) |

**The wrong-target citation is the sharpest one.** Wave 4 took `agent_capsule.py` from 3,652 to
926 lines. CI failed six citations for pointing past 926 — correct, and it fixed itself into
looking complete. A seventh, `code-search-and-retrieval-reference/SKILL.md`'s `:294`, stayed
**green**, because 294 is inside 926; it had simply stopped describing the symbol it named. Only
a grep for every citation into the split file found it. **After shrinking a file, the gate's
silence covers exactly the citations it cannot judge.**

And a split moves symbols between FILES, not just lines: `_CAPSULE_INLINE_CALLER_ANNOTATION_ENV`
landed in a new `agent_capsule_constants.py`, so its citation was wrong in both coordinates.
Grep the SYMBOL across `src/`, never inside the file you split.

### The corollaries worth carrying

- **A retry added to fix a hang can silently reintroduce the failure it was guarding.** Bounding
  the CI ripgrep install needed three attempts around `apt-get`; a bash `for` loop exits 0 on its
  final iteration whether or not the body worked, so an exhausted retry would have handed the job
  on with no `rg` — and the rg-parity suites then resolve nothing and **silently skip**, the exact
  outcome that step exists to prevent. Assert the POSTCONDITION (`command -v rg`), never the
  loop's status.
- **A ratchet you re-pin is a cost, not a pass.** `file_size_budget` failed its own author on its
  first real encounter (`main.rs` 15094 → 15159). Folding a stale paragraph recovered a few lines;
  the residual +33 was genuine, so the pin moved. That is legitimate and it is also the weakest
  possible outcome — record the bump and the reason it was not avoidable, or the gate quietly
  becomes a comment.
- **Two green PRs are not a green union, and the collision does not need to be subtle.** #1025
  retires two allowlist entries, #1033 retires a third, and the entries are adjacent lines — git
  conflicts, both PRs are green, and neither CI run can see it because each ran against its own
  base. Merge a union locally and run the gate on it BEFORE queueing.
- **Before blaming the runner for a hung job, look at its siblings in the same run.**
  `native-build-smoke (ubuntu-latest)` sat 2h21m, then 29m, then 39m on `apt-get install ripgrep`
  while macOS (9m51s), macOS-intel (11m35s) and windows (11m39s) finished normally in the SAME
  runs. A degraded box moves its siblings; they never moved. Then it passed in 6 minutes on the
  next attempt — so the flake is intermittent, which is the argument for `timeout-minutes`
  rather than for reruns.
- **An unbounded step is indistinguishable from a slow one.** It emits nothing, so hours pass
  before anyone looks. Any step that reaches the network gets a timeout.

### Instrument notes from the same wave

- **`gh run view --log-failed` returns EMPTY while the RUN is in progress, even when the JOB has
  completed and failed.** Query the job by id (`gh api …/actions/jobs/<id>/logs`). An empty log
  read as "no failures" is the false zero this file has now paid for repeatedly.
- **Grep the log for `error` and you match `--error-format=json` in every rustc invocation.**
  Strip the timestamp, anchor the pattern, and count before reading.
- **A cancellation you performed reads as a 9-job failure.** The tell is unexpanded
  `${{ matrix.os }}` in the check name: those jobs never instantiated, so they cannot have failed.
- **Python eats a trailing backslash in a non-raw string.** Anchor text copied out of Rust or
  bash that ends lines with `\` silently joins, and the replace then matches nothing — twice in
  one session. Use `r"""…"""` for any anchor carrying an escape.
- **Require `count == 1` before any scripted replace.** That assertion caught two different tests
  in `main.rs` sharing a byte-identical timed-run block; a blind replace would have edited the
  wrong test and looked fine.

## An Environment DIFFERENCE Can Be The Only Instrument That Sees A Defect (2026-08-20, tri-split fan-out)

Eleven PRs from eight subagents split the three giant modules in one day (`main.py`
17,983 → 13,523, `repo_map.py` 19,762 → 15,243, `mcp_server.py` 8,028 → 5,341, plus two Rust
test extractions). The campaign's durable finding is about evidence, not refactoring.

**The bare-call patch bypass was STRUCTURALLY invisible on the dev box.** Tests patch
`mcp_server._resolve_native_tg_binary_for_mcp` to `(None, None)` to force the embedded path.
A split child called it BARE, so the patch never intercepted. Locally there is no built native
binary, so the embedded branch is taken either way and the test passes — **local green was not
weak evidence, it was NO evidence**, because the only branch the bug lives on cannot be taken
here. CI (binary built) resolved the real binary, took the native path, and the mock was never
called. Three rounds of this class shipped before the sweep was made exhaustive.

- When a test's mechanism is "patch X to force branch B", ask which environments can take the
  OTHER branch. A box that cannot take it cannot falsify the patch's delivery.
- `cost_split_floor_routes.patch_sites` models only the `setattr` shapes. The full set tests
  actually use is FOUR: `patch("dotted.string")`, `patch.object(mod, "name")`,
  `monkeypatch.setattr(mod, "name", …)`, and `mod.X = …`. A "0 bare calls" verdict from a
  narrower model is narrower than it reads — the `main.py` split agent additionally showed the
  ratchet is blind to patched *attribute* calls and patched *constants*.
- Sweep per target module: every name tests patch on it, by all four shapes, intersected with
  what the module bound on `origin/main` — then zero bare uses in every extracted child.

### The rest of the fan-out's receipts, compressed

- **Union-merge every concurrently-open PR touching a shared pin/census BEFORE queueing.** It
  caught two defects no branch's own CI could see: branches cut before #1046's handler ceiling
  existed were green against a world without the gate, and three PRs' adjacent-line allowlist
  edits conflicted pairwise while each was green alone. (Second campaign this week; now a
  standing step, not a discovery.)
- **A monitor keyed on first-terminal-state goes silent forever after a re-push.** Key on
  `PR:head-sha` so every push gets its own verdict, and print an explicit exit line ("no open
  PRs remain") so stream-end is distinguishable from a hang. Same family as the job-KEY-vs-NAME
  selector failure the 2026-08-19 law records: the guard was on the result, the defect was in
  the population.
- **A dead subagent's worktree is a local-green/CI-red generator.** Both session-limit deaths
  left verified-but-UNCOMMITTED fixes: the worktree passed, CI on the same head failed. When an
  agent dies, diff its worktree against its branch before reasoning about its CI.
- **Briefing the traps prevents repeats, not new members of the class.** Agents given the full
  trap list still hit: rustfmt disagreeing with a dedent (the one thing they could not
  compile-check — resolved by applying CI's own diff verbatim), a decorator running at
  sibling-import time, `Path.write_text` CRLF on `eol=lf` files, and a ratchet's module column.
  The briefs made them CATCH these; catching is the realistic goal.
- **Relocated code must not re-audit itself into a census.** Moving a file does not audit it:
  extend the exclusion set with the reason inline, never raise the ceiling on the strength of a
  `git mv`. Three split PRs each needed this, each in their own merge round.
