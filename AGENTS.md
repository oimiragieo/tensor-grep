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
`docs/TASK_BOARD.md` is the live-state view.
**Subagents:** treat each live item's
description + files + status as your brief. **CEO status** must enumerate every live disposition—active,
environment-blocked, nonfinancial decision-gated, financial/spend-gated, demand/research-gated, and
mixed/terminal corrections—not merely SHIPPING or P0/P1 highlights.

The standing multi-model pipeline for any substantive item: deep-dive → **Fable audit** (find + fix-idea,
cite `file:line`) → **Exa** recency + competitive research (you are trained on stale data — verify current
facts) → plan (superpowers skills) → thinktank/Fable review the plan → **Sonnet build, TDD** → verify in the
REAL venv (`uv run --no-sync`; a worktree "tests pass" is a hypothesis, re-run in the main venv) →
`ruff check` + `ruff format --preview` + `mypy` → codex/Fable review the PR → **PR → drain**
(burst-then-hold merging, the push-race rule) → repeat until no issues. Isolate code agents with
`isolation:'worktree'`. Match model to task per the `model-router` skill. Run the
common-sense gate before pending any question to the CEO. Keep docs (this file, `docs/BACKLOG.md`,
`docs/SESSION_HANDOFF.md`, skills, CLAUDE.md) synchronized as work lands.

## Campaign Orchestration Disciplines (2026-07-08, hard-won)

These rules apply when running a multi-PR drain+build campaign, so that fixes land instead of piling up. Each rule below answers a concrete failure mode of such campaigns.
Receipts: docs/agent-laws-receipts.md#campaign-orchestration-disciplines-2026-07-08-hard-won

- **A1 — WIP cap.** Dispatch no new build while more than 5 PRs are undrained or the `main` gate is red, because generating faster than the ~40–66 min/publish drain empties keeps the backlog constant-size. A red gate is a drop-everything hotfix that jumps the queue.
  Receipts: docs/agent-laws-receipts.md#a1
- **A2 — A self-firing drain-cron beats a long-lived background drain.** Use a short-lived per-fire cron that merges green PRs (`gh pr merge --squash --delete-branch`), because a long-lived `drain.sh &` background process dies during long CI/publish waits. Per fire, apply the push-race gate: if no release-bearing `main` run exists, merge the green PRs in one burst; otherwise merge nothing until that run is `completed` AND its `chore(release)` tag is on PyPI. If the run completes without publishing (red, or semantic-release made no release), the window closes at completion; A32 governs the hotfix.
  Receipts: docs/agent-laws-receipts.md#a2
- **A3 -- Mandatory adversarial security gate before merge.** Every security PR (touching `apply_policy` / `mcp_server` / `*_backend` / an index-or-session lock / auth / money / migration / native asset / installer / doctor-probe construction) gets an Opus "try to BREAK it, cite `file:line`, default FIX-FIRST if uncertain" review before merge, because the gate is not a rubber stamp and catches real issues such as symlink bypasses and lock-release TOCTOUs. Verdict shape: `SHIP` | `FIX-FIRST(+file:line + repro + minimal fix)`.
  Receipts: docs/agent-laws-receipts.md#a3
- **A4 — Resume a dead agent from its transcript.** Revive a background subagent that died with "terminated early due to an API error: 500" by `SendMessage` to its `agentId`, because its partial work is intact; do not re-dispatch fresh, which loses that work.
  Receipts: docs/agent-laws-receipts.md#a4
- **A5 — Don't kill a build on staleness.** Trust the completion notification and do not kill a complex build on a "stale > N min" heuristic, because such builds legitimately run >10–15 min between output flushes. Diagnose a suspected hang from the kill-note's last line, not an mtime guess.
  Receipts: docs/agent-laws-receipts.md#a5
- **A6 — Anti-hang test protocol.** Wrap every test run in a shell `timeout` (`timeout 120 uv run --no-sync … pytest …`; `pytest-timeout` is not installed, so a `--timeout=N` flag aborts collection — see A141), and write the fix before the red-phase adversarial test, because a ReDoS/deadlock red-test run against un-fixed code is the hang it tests. Distinguish slow-but-protected from hung by exit code (124 timeout / 137 SIGKILL), not elapsed time.
  Receipts: docs/agent-laws-receipts.md#a6
- **A7 — Harvest a worktree agent's work, then re-verify.** Treat a worktree agent's "tests pass" as a hypothesis, because its venv may lack the compiled `rust_core` ext. Cherry-pick its commit onto a fresh branch off `origin/main`, re-verify in the real venv with `ruff`/`format --preview`/`mypy` and a live smoke, then run the gate, then open the PR.
  Receipts: docs/agent-laws-receipts.md#a7
- **A8 — Fable is reachable only via `Agent(model:fable)`.** Dispatch Fable design/audit seats as `Agent` subagents, never inside a `Workflow`, because a Workflow `agent()` call cannot reach Fable and silently falls back to the session model.
  Receipts: docs/agent-laws-receipts.md#a8
- **A9 -- Probe liveness via `SendMessage` before any `TaskStop`.** Do not judge a background subagent by its output-file mtime/size, because those are unreliable (0KB while foreground-compiling); send a `SendMessage` probe instead. A reply of "Message queued...at its next tool round" means alive; "had no active task; resumed from transcript" means it was paused. Corroborate with Pyright `<new-diagnostics>` on its file-writes plus the active build-process count; this is the mechanism A5's "trust the completion notification" relies on. Load the global skill `agent-liveness-probe` before killing, restarting, or `TaskStop`-ing anything that looks stalled.
  Receipts: docs/agent-laws-receipts.md#a9
- **A10 -- A no-verdict council seat is a failed seat, not a blocker.** Treat a council seat that produces no anchored verdict (for example a codex seat hung on an MCP-auth spin, 0KB output) as failed: kill it, sweep the orphaned processes it left behind, and synthesize from the surviving Opus lenses rather than waiting on it.
  Receipts: docs/agent-laws-receipts.md#a10
- **A11 -- Design-review-before-build.** Follow this sequence: Fable designs a plan -> a thinktank council certifies the plan itself is sound and ready (not findings, not a diff) -> bake must-fixes into the plan -> Sonnet builds TDD-first (worktree, foreground-gate) -> mandatory adversarial Opus gate (including native-asset/installer/doctor-probe work, see A3) -> drain under the burst-then-hold merge rule. The sequence catches CI-reddening fixes, ordering bugs, and oversold claims before any code is built.
  Receipts: docs/agent-laws-receipts.md#a11
- **A12 -- CPU-safe shared-server discipline.** This desktop is a SHARED machine (Operating Rule #3) with other AI/omega-* services running concurrently, so CPU-heavy work (loading/inferring a dense-embedding model, a full-corpus rerank sweep, a wide benchmark matrix, a cold `cargo check`) MUST NOT run locally, because it starves them; route it to cloud `Agent` subagents or GitHub Actions CI. A bounded probe (a handful of queries, not the full golden set) is fine to sanity-check wiring; push the real evaluation to CI or a subagent. The cron tick itself is cloud-side and not the problem; local process spawns (codex/droid/gemini/cargo/rustc) are.
  Receipts: docs/agent-laws-receipts.md#a12
- **A13 — Rapid-window batch-merge collapses N release cycles to 1 (C-batch).** Several independently-green, already-CI-passing PRs may land ~15-20s apart in one gate-open window as a single combined release; intermediate concurrency-cancelled/rejected-looking runs on the earlier pushes are benign as long as the newest `main` run goes fully green. This is deliberate, unlike an accidental two-writer push-race collision. It is the burst half of the merge rule in "Push Discipline"; A142 is the hold half.
  Receipts: docs/agent-laws-receipts.md#a13
- **A14 — Event-driven release watching + a cron floor (C-event).** Prefer a background `gh run watch <run-id> --exit-status` (chained off its own ~10-min expiry notification) over blind long-interval polling when waiting on a release. Pair it with a cron floor (e.g. :02/:32-style offsets) whose prompt embeds the full remaining pipeline instructions, so completion survives a crash or context loss.
  Receipts: docs/agent-laws-receipts.md#a14
- **A15 — Session-only crons die on crash/reboot; always recreate (C-cron).** Use a `CronCreate` drain-cron for durability, because a `/loop` invocation is session-bound. Keep `MEMORY.md` as the crash-safe state carrier so a recreated cron can resume correctly.
  Receipts: docs/agent-laws-receipts.md#a15
- **A16 — Pin-first ranking gate (C-pin).** Before touching any scorer/graph/ranking code, write a test that pins the current ranked output green on base; after the change the only acceptable diff is the intended one, and any legitimate-entry reorder is a stop-finding, not noise to relax away (example: `test_blast_radius_legitimate_dependent_ranking_pin`).
  Receipts: docs/agent-laws-receipts.md#a16
- **A17 — Scheduler-independent concurrency tests (C-concurrency).** Never assert wall-clock thread overlap, because a starved runner serializes legitimately and false-fails. Assert the contract with `threading.Event` handshakes plus bounded acquire attempts (independence case plus the converse mutual-exclusion case), as in `test_index_lock_is_per_root_not_global`.
  Receipts: docs/agent-laws-receipts.md#a17
- **A18 — A build agent's self-gate is a hypothesis, not clearance (C-independent-gate, extends A3).** Re-draft until a separate, independently-framed gate (not the build agent's own review) says SHIP, because that gate can return SHIP-WITH-NITS on one pass and a distinct verdict on a re-drafted pass of the same PR.
  Receipts: docs/agent-laws-receipts.md#a18
- **A19 — Fold safety/honesty nits before merge; bank cosmetic ones (C-nit).** Fold a gate finding that changes observable behavior (a fail-open read, a misleading status, a missing migration-honesty note) into the same PR before merge. Bank a purely cosmetic nit (naming, comment wording, a stale citation) as a follow-up and batch-close it later.
  Receipts: docs/agent-laws-receipts.md#a19
- **A20 — Published-wheel verdict-table dogfood closes a campaign (C-wheel).** Before declaring a multi-PR campaign done, probe every fixed item against the actually published wheel in a clean env (`uvx --from tensor-grep@<ver>`), with one PASS/FAIL row per item backed by the raw JSON rather than a verdict word. Pre-build fixtures, read the raw JSON before scoring (a probe-shape misread reads as a false fail), and watch for pipe exit-code masking (`cmd | tail` reports `tail`'s exit code, not `cmd`'s).
  Receipts: docs/agent-laws-receipts.md#a20
- **A21 — The per-task-pinned accuracy gate is the loop-4 instrument (C-loop4).** `tests/eval/test_agent_accuracy.py::test_agent_accuracy_gate` (`assert not misses`) is a capability-regression gate, distinct from a contract test, that surfaces ranking/routing regressions a code-review gate rationalizes away. Turn every real misroute found in the wild into a new permanent pinned task.
  Receipts: docs/agent-laws-receipts.md#a21
- **A22 — Sequential-drain-union-rebase for N PRs on a shared file.** When several parallel PRs edit the same file (e.g. `test_lang_registry`, the pyproject `ast` extra, `uv.lock`), drain one at a time and rebase each onto the prior, unioning the assertions (assert the full set, never take one side). Re-run the test suite after every rebase, because a clean rebase is not proof of correctness (a silent auto-merge can drop an import such as a `lang_*` module).
  Receipts: docs/agent-laws-receipts.md#a22
- **A23 — A "stopped" agent notification may mean the work already landed, not that it was lost (2026-07-24).** On any "stopped"/"no completion record" notification, inspect the worktree's `git status`/`git log` before re-dispatching, because the agent may have committed before exiting without a completion summary and re-dispatching would duplicate or conflict with finished work.
  Receipts: docs/agent-laws-receipts.md#a23
- **A24 — A worktree agent can commit on a DETACHED HEAD; push the SHA, not the branch name (2026-07-24).** Before pushing, compare `git rev-parse HEAD` against `git rev-parse <branch>`; if they differ, push the SHA explicitly (`git push origin <sha>:refs/heads/<name>`) and open the PR against that branch, because pushing the branch name pushes a ref still at `main`'s tip and GitHub rejects the PR with "No commits between main and `<branch>`". See `tensor-grep-debugging-playbook` for the symptom-table row.
  Receipts: docs/agent-laws-receipts.md#a24
- **A25 — Session-scoped crons/monitors die silently on a CLI restart or reboot; always re-verify, never re-dispatch on an assumption (2026-07-24).** After any restart or crash, re-create the recurring backstop and confirm it with `CronList` rather than assuming a recorded id/schedule is still armed, because the backstop vanishes with no visible error (same lesson as A15). Keep durable state (queue, in-flight PRs, "resume here") in the task store and `MEMORY.md`, which survive a restart.
  Receipts: docs/agent-laws-receipts.md#a25
- **A26 — Verify a session-scoped cron/monitor in both directions: it can be dead when you assume it's alive, or alive when you assume it's dead (extends A25, 2026-07-24).** After any restart or recreate, call `CronList`, read every returned entry (do not just count them), and explicitly delete any superseded duplicate, because a presumed-dead cron can still be alive alongside its replacement and fire stale instructions that look authoritative (for example gating a PR that already merged).
  Receipts: docs/agent-laws-receipts.md#a26

- **A27 — A class fix must cross to its twin, or the twin re-fires the same defect (2026-07-26).** When you retire an approach in a test or helper, `grep` for its shape across siblings in the same turn and port the fix, because a docstring explaining why a form was abandoned is worthless in a file that still uses that form (the ledger twin kept the retired overlap form of `test_index_lock_is_per_root_not_global`). For concurrency, two independent locks are only guaranteed not to block each other, never to be simultaneously held; assert the blocking contract (Event-gated), never wall-clock overlap.
  Receipts: docs/agent-laws-receipts.md#a27
- **A28 — Relay a gate verdict to the artifact, not just your own transcript (2026-07-26).** Post the verdict as a PR comment (`gh pr comment`) with its evidence (what was probed, what the control arm showed) before moving on, because a verdict only the steward session can see is lost work that the next session re-runs or contradicts, and it lets the author un-draft without waiting.
  Receipts: docs/agent-laws-receipts.md#a28
- **A29 — Verify the fix on the merged artifact, not only pre-merge (2026-07-26).** After merge, confirm the fix on `main` structurally (for example `"_seen" in fn.__code__.co_varnames`, not by re-reading the diff) and re-run the fixture against `main`, because pre-merge only proves the bug is real, and a squash can drop a hunk or a conflict resolution can mangle it.
  Receipts: docs/agent-laws-receipts.md#a29
- **A30 — Make pruning decidable instead of banned (2026-07-26).** Prune agent branches per branch with `git merge-base --is-ancestor <branch> main` (an ancestor has its commits already in `main`, so deletion loses nothing) and delete with `git branch -d`, never `-D`, so git independently refuses anything unmerged. `git branch --merged` under-reports after squash-merges, and a closed PR is not a merged PR.
  Receipts: docs/agent-laws-receipts.md#a30
- **A31 — Order the drain by release impact, not by PR number (2026-07-26).** Land non-releasing PRs (`refactor:`/`docs:`/`test:`/`bench:`/`chore:`) first, because only `fix:`/`perf:`/`feat:` trigger semantic-release and a non-releasing merge creates no publish to race; its gate is just "the main run completed" (~6 min versus ~30–60 min per release cycle). The hold protects an in-flight publish; it is not a per-PR serialisation.
  Receipts: docs/agent-laws-receipts.md#a31
- **A32 — The drain gate is "newest main run completed", not "completed green" (2026-07-26).** Merge the hotfix for a red `main` without waiting for green, because requiring green before merging the thing that makes it green is a deadlock; then confirm `main` recovered with a subsequent green run. Keep everything else parked while red, because merging onto a broken `main` compounds it and obscures which commit owns the failure.
  Receipts: docs/agent-laws-receipts.md#a32

- **A33 — `release-intent` being skipped proves nothing; the publish job runs on every main push (2026-07-26).** Do not infer "no release, no push to race" from `release-intent` being skipped, because it is a PR-title validator (`if: github.event_name == 'pull_request'`) and is always skipped on a push. The job that matters is `release` ("Semantic Release"), gated on `github.ref == 'refs/heads/main' && github.event_name == 'push'`; it `needs:` the full test matrix so it appears in the job list late. **The only safe signal is the newest `ci.yml` run on main reaching `completed`; merging onto an in-flight run rejects its release push (non-fast-forward), because two pushes race.** A rejected release self-heals on the next push, so do not rerun it. `tag == PyPI` is not sufficient either, since a run can have tagged and still be mid-publish. Second window: once `Semantic Release` has succeeded, the push race is over but the publish tail (wheels, native assets, `publish-pypi`) is still running, and a new run's concurrency group cancels that tail, leaving a tag with no PyPI artifact (the version-soup state #47 detects); wait for PyPI to actually serve the new version.
  Receipts: docs/agent-laws-receipts.md#a33

- **A34 — Prose and PR metadata are part of the artifact (2026-08-02).** Gate titles, bodies, comments, examples, and status counts against the final commit just as you gate code, because they can be wrong while code and tests are green (malformed examples, counts with an unstated denominator). After scope changes, refresh and re-review PR metadata; "0 unchecked" and "0 total" are different claims.
  Receipts: docs/agent-laws-receipts.md#a34
- **A35 — Plan approval expires when a premise changes (2026-08-02).** Treat any material premise change (an incomplete writer population, a public method, a surviving twin fallback) as invalidating the old verdict. Amend the plan, hash the exact new artifact, and re-run the thinktank before build.
  Receipts: docs/agent-laws-receipts.md#a35
- **A36 — A site regression is not a class census (2026-08-02, #859).** A class-level claim needs an independently derived closed-world population, mutation controls, and a zero-violation assertion, because a single fixed site proves only that site.
  Receipts: docs/agent-laws-receipts.md#a36
- **A37 — Census the defect surface, including generated interpreters (2026-08-02).** Discover writers from production write/spawn roots, then resolve aliases, local imports, rebinding/shadowing, generated `python -c` source, and raw candidate calls. Fail closed on dynamic/unparseable generated payloads, and sanction an exact callsite/operation/destination-provenance fingerprint, never a whole function.
  Receipts: docs/agent-laws-receipts.md#a37
- **A38 — Leaf resolution order and parent anchoring are separate security contracts (2026-08-02).** Preserve the raw leaf identity: do not call `.resolve()`/`realpath()` before a no-follow writer, because it erases leaf-symlink identity. Anchor directory creation, temp creation, and publication to opened identity-verified parent handles, because even with a safe leaf check an attacker can swap a parent or junction before mkdir/publication, and Event-gate both leaf and parent swaps on Unix and Windows.
  Receipts: docs/agent-laws-receipts.md#a38
- **A39 — Class fixes cross to twins (2026-08-02, extends A27).** After a class fix, grep sibling adapters/helpers (for example `RustCoreBackend` and `CPUBackend`) for the retired shape and add a population ratchet, because otherwise the twin re-fires the same defect.
  Receipts: docs/agent-laws-receipts.md#a39
- **A40 — No in-repo caller does not authorize public-API deletion (2026-08-02).** Retain and harden public signatures (for example Rust `CpuBackend.replace_in_place`, exported in an `rlib`) unless a deliberate breaking/deprecation/migration decision authorizes removal, because downstream callers are invisible to an in-repository census. Pin the exact public function type at compile time.
  Receipts: docs/agent-laws-receipts.md#a40
- **A41 — Preserve mixed dispositions (2026-08-02).** Do not flatten `shipped + retired`, `fixed + blocked`, or `implemented + demand-gated` into one flattering word. Track each sub-outcome and close the parent honestly.
  Receipts: docs/agent-laws-receipts.md#a41
- **A42 — Producer→consumer dogfood must not change what it verifies (2026-08-02).** Prefer bounded stdin/captured stdout over materializing a verification result inside the repository, because that can dirty the very state the consumer attests. Keep producer and consumer exits separately and pin the full matrix: `0→0`, `1→0`, valid `2→0`, malformed consumer `2` with no receipt.
  Receipts: docs/agent-laws-receipts.md#a42
- **A43 — Exact CI completion includes the job population (2026-08-02).** Capture the exact workflow run ID and head SHA, require the run `completed`, record its job-count floor, and prove zero unfinished/failing jobs, because a PR check rollup can grow while jobs are still being created and a momentary list is not completion.
  Receipts: docs/agent-laws-receipts.md#a43
- **A44 — Attribute each SHA to what it proves (2026-08-02).** Record each claim against the exact artifact and run that proves it, because `origin/main`, the newest main-CI head, a PR head, a squash merge, and a semantic-release `[skip ci]` commit can all differ; never cite the newest convenient SHA for all arms.
  Receipts: docs/agent-laws-receipts.md#a44
- **A45 — Durable CEO status is a closed-world snapshot, not a hand-picked top five (2026-08-02).** Separate active/buildable, environment-blocked, CEO/financial-gated, demand/research-gated, and terminal corrections. Give every live item one stable ID/owner/trigger, assert that the canonical set has no unowned extras or omissions, and update `docs/SESSION_HANDOFF.md` in the same change; `MEMORY.md` is untracked (A158), so refresh it separately.
  Receipts: docs/agent-laws-receipts.md#a45
- **A46 — Hash the canonical artifact, and state the hash method (2026-08-02).** For plan gates, hash the designated canonical worktree bytes (or canonical Git blob), record which, and make every seat verify that same method/path before auditing, because clean-filter-equivalent content can have different raw mixed-line-ending bytes on Windows and a bare "SHA-256" can disagree without a semantic change.
  Receipts: docs/agent-laws-receipts.md#a46
- **A47 — Validate the task dependency graph, not only each task (2026-08-02).** Before approval, prove every required producer/service/registration exists before its first consumer/test, because a task can import a service or invoke a command that a later task creates. A test that fails only at command discovery is not a behavioral RED.
  Receipts: docs/agent-laws-receipts.md#a47
- **A48 — Directory-handle anchoring covers locks, state, and configuration reads (2026-08-02).** Create/open a stable fence, read/publish its protected index, and read repository-controlled configs relative to verified confined handles, because leaf no-follow flags do not stop an intermediate parent swap. Bound file/count/aggregate bytes and Event-test swaps before create, after lock, and before publish/read.
  Receipts: docs/agent-laws-receipts.md#a48
- **A49 — Every deferred security behavior needs a canonical owner (2026-08-02).** Assign a stable ID, disposition, threat boundary, owner, and reopen trigger to any known security/compatibility choice, because it cannot be allowed to disappear inside a broader shipped row or a bare "follow-up".
  Receipts: docs/agent-laws-receipts.md#a49
- **A50 — The implementation PR owns its live tracker transition (2026-08-02).** Open the draft on an independently failing RED, immediately commit `IN_FLIGHT` with the real PR number and ordered PR history, and keep the separate post-merge closure PR for `SHIPPED`, because a `READY` row left unchanged after the draft exists permits duplicate dispatch and false CEO status.
  Receipts: docs/agent-laws-receipts.md#a50
- **A51 — Green and approval are artifact-specific (2026-08-03).** A run or verdict clears exactly the named SHA/hash it inspected, never later local edits, a sibling worktree, or "the same plan" by description. Record PR head, local plan hashes, review hashes, and merge SHA separately.
  Receipts: docs/agent-laws-receipts.md#a51
- **A52 — Architecture `SHIP` is not security clearance (2026-08-03).** Get security-class work its own adversarial `SHIP` on the same bytes, because a different lens's approval cannot substitute: an architecturally coherent design can still have forgeable signer/receipt authority, unenforceable PATH atomicity, or breakaway containment gaps.
  Receipts: docs/agent-laws-receipts.md#a52
- **A53 — Security plans name enforceable primitives (2026-08-03).** Name the concrete API, flags, authority root, identity comparison, failure behavior, and adversarial control for "atomic CAS," "trusted signer," "owned PATH entry," and "kill descendants," because those are goals, not Windows contracts. If the platform primitive is unavailable, fail closed instead of inventing a weaker fallback.
  Receipts: docs/agent-laws-receipts.md#a53
- **A54 — Authority is never discovered from an untrusted search path (2026-08-03).** PATH, an adjacent binary directory, an environment variable, a caller-supplied path, or an install-command digest cannot establish installer ownership. Start from a fixed protected state root, retain its identity, verify its cryptographic binding, and treat path strings only as hints to objects whose opened identities match.
  Receipts: docs/agent-laws-receipts.md#a54
- **A55 — Containment includes escape denial (2026-08-03).** Pin the absence of both Job breakaway flags and `CREATE_BREAKAWAY_FROM_JOB` alongside `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and run a real descendant-breakaway RED, because kill-on-close is incomplete if breakaway is permitted and "primary process died" is not process-tree proof.
  Receipts: docs/agent-laws-receipts.md#a55
- **A56 — A resource cap must fire at every door (2026-08-03).** Bootstrap, full CLI, direct native, native→rg, native→sidecar, and every matcher engine must join the same no-refund ledger before route selection or child creation. Independently test the inclusive cap and mixed-source aggregate; separate counters and an uninstrumented PCRE2 route are fail-closed defects.
  Receipts: docs/agent-laws-receipts.md#a56
- **A57 — Static manifests and live receipts have different authority (2026-08-03).** A committed manifest defines the exact nodes/jobs that must run and therefore contains no live run ID. A live receipt proves an execution only after a verifier independently re-derives the Actions/artifact tuple and cross-checks Python JUnit plus the Rust node census, because self-attested JSON is not anti-replay proof.
  Receipts: docs/agent-laws-receipts.md#a57
- **A58 — Retry review by narrowing, not by weakening (2026-08-03).** When a broad Cursor/council prompt times out, retry the disputed paragraph and invariant, preserve the original severity, and send the resulting work to the independent adversarial gate (Sol seat). Record a no-verdict seat as failed and replace it; it is neither approval nor an infinite blocker.
  Receipts: docs/agent-laws-receipts.md#a58
- **A59 — Discover deferred capabilities before declaring a required tool absent (2026-08-03).** Search the callable-tool catalog first (a tool can be available through the deferred catalog after appearing absent), then record genuine provider failure and use an approved fallback. Also select the newest canonical worktree before review; never promote an older dirty copy.
  Receipts: docs/agent-laws-receipts.md#a59
- **A60 — Never point WSL `uv` at the Windows checkout's `.venv` (2026-08-03).** WSL/Cursor worktrees use a WSL-local venv (or CI), and Windows verification runs from PowerShell in the canonical Windows checkout, because a WSL `uv run --no-sync --project /mnt/c/...` treats the Windows venv as incompatible, removes it, and creates an empty Linux venv in the same path. If this happens, move the incompatible venv aside, recreate it from Windows with `uv sync --frozen`, verify imports/version, and only then resume gates.
  Receipts: docs/agent-laws-receipts.md#a60
- **A61 — Behavioral RED pins the exact expected reason (2026-08-03).** A RED that accepts a crash, import failure, panic, or setup error as success is not behavioral proof. Pin the exact expected refusal or reason class and reject any arm that dies before exercising the contract.
  Receipts: docs/agent-laws-receipts.md#a61
- **A62 — Route/start evidence comes from the real producer (2026-08-03).** Hardcoded bools and production hooks that self-attest before the actual start are forgeable. Take route/start proof from the actual producer or constructor plus test-owned OS or raw evidence.
  Receipts: docs/agent-laws-receipts.md#a62
- **A63 — Containment proof authenticates provenance and lifecycle (2026-08-03).** Event signals, EOF, or PID text alone are not containment. Authenticate writer/client provenance, prove alive-before then dead-after, and prove cleanup independently of parent-forgeable heartbeats.
  Receipts: docs/agent-laws-receipts.md#a63
- **A64 — Crypto negative proof needs a valid operation and positive control (2026-08-03).** A negative crypto test must use a valid API operation, assert an exact refusal class, and carry an exportable/trusted positive control, because invalid flags that accept any error prove nothing.
  Receipts: docs/agent-laws-receipts.md#a64
- **A65 — Security grammar validates full authority, not substrings (2026-08-03).** SDDL and similar grammars must validate full sections, types, flags, and effective authority. Unknown, inherit-only, and garbage forms fail closed, and a substring principal match is not acceptance.
  Receipts: docs/agent-laws-receipts.md#a65
- **A66 — Resource-owning protocols name exact close ownership (2026-08-03).** Every protocol that acquires a resource names its close primitives and proves exact-once reverse cleanup on success, on `BaseException`, and on cleanup failure, while preserving the primary error.
  Receipts: docs/agent-laws-receipts.md#a66
- **A67 — RED scaffolds cannot enable partial public behavior (2026-08-03).** A test or temporary public flag must not unlock unbounded work or a GREEN path before the guard/ledger is active. A public `-f`/`--file` read that runs before the resource ledger is a fail-closed defect.
  Receipts: docs/agent-laws-receipts.md#a67
- **A68 — Immutable-SHA CI clearance needs a real run (2026-08-03).** Clearance requires a real CI run on the immutable SHA, expected per-node outcomes, raw artifacts, and the exact population. No run means no clearance, and a local RED replay is not Windows CI proof.
  Receipts: docs/agent-laws-receipts.md#a68
- **A69 — Security green is point-in-time, not durable clearance (2026-08-03).** A fresh advisory-database finding on the current head blocks merge even when an older head was green. Fix every fixable advisory by raising every live direct/constraint floor and regenerating the lock, update pinned validator tests and user remediation strings, replay the affected feature, and obtain a new exact-head audit; never add an ignore for a vulnerability that has a fixed release.
  Receipts: docs/agent-laws-receipts.md#a69


- **A70 — Ambient default signing keys pollute `--sign` no-key REDs (2026-08-06).** Clearing `TG_EVIDENCE_SIGNING_KEY` is not enough when `~/.tensor-grep/keys/evidence_ed25519.key` exists, because emit still signs and the negative arm looks green. Isolate `HOME`/`USERPROFILE` (or remove the default key) before claiming fail-closed.
  Receipts: docs/agent-laws-receipts.md#a70
- **A71 — Free-form bullets under `## Canonical status index` are illegal (2026-08-06).** The tracker parser accepts only `Status:` / `PR:` / `Trigger:` checklist rows there, and a campaign note under that heading reds `test_ceo_demand_duplication_is_rejected`. Put prose under a separate heading such as `## Campaign note`.
  Receipts: docs/agent-laws-receipts.md#a71
- **A72 — Merged implementation with a stale `IN_FLIGHT` row is board debt (2026-08-06).** Feature code on `main` is not tracker-closed until the row is `SHIPPED` with Implementation PRs, Closure PR, and Merged SHA (A50).
  Receipts: docs/agent-laws-receipts.md#a72
- **A73 — Bare published wheel ≠ semantic/`tg find` surface (2026-08-06).** `uvx --from tensor-grep==X` without `[semantic]` / `tg install-dense` has no `model2vec`, so find degrades with `rank_fallback_reason`. Enterprise CUJ dogfood uses prepare/search/evidence/review-bundle/ledger unless dense extras are installed first.
  Receipts: docs/agent-laws-receipts.md#a73
- **A74 — Quota-blocked Sol/Fable SHIP is provisional (2026-08-06).** An orchestrator substitute verdict is not an independent vendor seat. Re-dispatch the independent seat (Sol seat; Opus for security-adversarial passes) when quota returns for security or load-bearing claims, and do not treat the substitute as durable clearance.
  Receipts: docs/agent-laws-receipts.md#a74
- **A75 — Premise-check the ready-to-build queue before dispatch (2026-08-06).** A plan against a fixed defect has perfectly resolving citations, so reproduce the defect (or prove its absence on `origin/main`) as Step 0 before dispatching any item marked ready.
  Receipts: docs/agent-laws-receipts.md#a75
- **A76 — Board freshness is ordinal CHANGELOG distance (2026-08-06).** Patch subtraction and major.minor sentinels false-red on minor bumps, and no tolerance absorbs a sentinel of `tolerance+1`. Measure ordinal distance in CHANGELOG.md.
  Receipts: docs/agent-laws-receipts.md#a76
- **A77 — Stdin+heredoc merge pollers manufacture ALL_TERMINAL (2026-08-06 PM).** Piping `gh pr checks` into a shell construct whose heredoc consumes stdin can yield an empty checklist that a naive poller treats as every check done. Write checks to a file (or capture argv that cannot steal stdin), require heavy lanes present by name/count, and never treat "0 pending over an empty rollup" as clearance (extends A43 / Form 8).
  Receipts: docs/agent-laws-receipts.md#a77
- **A78 — Provider usage-limit / error seats are FAILED (2026-08-06 PM).** A Sol/Opus/Fable seat that dies with a usage-limit or equivalent provider error is a failed seat, not pending approval and not a soft wait (extends A10/A58/A74). Record FAIL, do not promote a substitute SHIP to durable security clearance, and re-dispatch when quota restores.
  Receipts: docs/agent-laws-receipts.md#a78
- **A79 — Status-stamp PRs must retarget governance pins (2026-08-06 PM).** Stamping a board row READY to BLOCKED without updating tracker tests that assert `Status: READY` (or forbid BLOCKED on program owners) reds CI on the truth-fix. Enumerate the pins that name the old status in the same PR as the stamp.
  Receipts: docs/agent-laws-receipts.md#a79
- **A80 — Gate the tip under review, not the archaeological RED SHA (2026-08-06 PM).** Sol, CI, and merge clearance bind to the exact tip bytes (A51), so after a rebase cite the current tip; citing the old RED SHA is an artifact mismatch.
  Receipts: docs/agent-laws-receipts.md#a80
- **A81 — Implementer HIGH receipts ≠ Sol SHIP (2026-08-06 PM).** Local commits, receipt files, and "HIGH findings applied" self-reports are hypotheses. The item stays FIX-FIRST until exact-byte Sol returns `SHIP` on the named tip (extends Form 1 / A74 / never-trust-self-report).
  Receipts: docs/agent-laws-receipts.md#a81
- **A82 — AMEND_SPINE when READY∩reconcile-BLOCKED (2026-08-06 PM).** On a Thinktank `AMEND_SPINE`, drop the blocked items from the build spine and start only with docs/R0/D1 (board stamp plus recommendation packets) until the gating task has Sol SHIP and Windows CI. Board READY is not a build license when the BACKLOG reconcile says BLOCKED (pairs with A71/A75).
  Receipts: docs/agent-laws-receipts.md#a82
- **A83 — Front-door argv rewrite shadowing (2026-08-09).** An argv normalizer that rewrites one CLI shape into another (`SEARCH_OPTION_FIRST_FLAGS` to `tg search ...`, `normalize_top_level_search_args`) redirects a positional validator's coverage, so `tg PAT --gpu-device-ids 0 --count-matches` never reaches `run_positional_cli` and the search path can silently drop `gpu_device_ids`. A fix is closed only when it guards every door the rewritten argv can reach; census the rewrite list and the target parser, not just the door you added the guard to (the registration-completeness law applied to argv normalization).
  Receipts: docs/agent-laws-receipts.md#a83
- **A84 — Cross-platform path semantics: platform-gate the drive-absolute strip (2026-08-09).** A Windows-only path normalization applied unconditionally (stripping the leading `/` from `/C:/...` drive-absolute URIs) turns a root-anchored URI into a relative path on POSIX and flips a confinement check from refused to passed. Gate any platform-meaningful path transformation on `os.name == "nt"` (or its POSIX analogue) and pin both arms in a cross-platform test, because only the real CI matrix catches the flip.
  Receipts: docs/agent-laws-receipts.md#a84
- **A85 — Env-independent gated tests (2026-08-09).** A test that must pass in both the dev env and CI pytest envs (which can lack the ast-grep binary, native tree-sitter, the dense model, or the compiled rust_core ext) must be env-independent by construction: force a controlled deterministic seam for the optional engine (dense-unavailable force; controlled AstBackend shim) and never env-detect. A test that passes locally and fails CI on a missing engine is a defect in the test, and the census/ratchet must RED on a deleted member, a missing stamp, or an allowlisted family raising an unexpected exception type.
  Receipts: docs/agent-laws-receipts.md#a85
- **A86 — Stale-ready labels: "ready"/"green" must cite the head's own completed run (2026-08-09).** Any ready or green label must cite the head SHA's own completed check-run set (A44/A51), and before merging a long-lived branch, rebase onto current main and re-verify, because green against a stale base is a Form 10 (branch-unit) false green.
  Receipts: docs/agent-laws-receipts.md#a86
- **A87 — Static review ≠ typecheck; CI is the only compile oracle for Rust (2026-08-09).** Static or logic review cannot typecheck, so a Rust PR's gate must include "first CI cargo run compiles" before any codex SHIP verdict is treated as durable. Structural arguments about Rust are hypotheses until the compiler and tests run.
  Receipts: docs/agent-laws-receipts.md#a87
- **A88 — Dogfood fixtures must bite (Form 6 applied to the published-wheel dogfood too, 2026-08-09).** Verify that the hostile setup actually applied before trusting a dogfood result, because a pass on a fixture that never applied proves nothing. For junction fixtures, `mklink /J` requires that the link path not already exist (the target directory may be populated), so remove the link path first as `_plant_ancestor_link_or_skip` does, and check that junction resolution (`os.path.islink()`, parent-resolve containment) differs from the plain tree. On the pinned Rust 1.96.0 toolchain a real junction reports `is_symlink: true` via `symlink_metadata` and `OpenOptions::open` follows it (A107; see docs/design/2026-08-13-replace-in-place-symlink-threat-model.md section 5).
  Receipts: docs/agent-laws-receipts.md#a88
- **A89 — Real-artifact test arms beat fake-backed ones in parity oracles (2026-08-09).** Whenever a parity or oracle test can drive the real producer cheaply, it must, because a fake-backed arm can certify a lie as several arms of agreement (for example span fakes that hid production reading the wrong ast-grep JSON fields, where the real 0.42.1 output uses `range.byteOffset.start/end`). Add a real `ast-grep --json` subprocess arm (extends the Verification-Oracle family).
  Receipts: docs/agent-laws-receipts.md#a89
- **A90 — Fail closed on unknown subcommands; never fall through to search (2026-08-09).** An unknown top-level command must exit non-zero with `error.code=unknown_command` and `nearest[]` on both front doors (Python `KNOWN_COMMANDS` and native `normalize_top_level_search_args`/`is_known_python_command`), never be swallowed into search. Otherwise `bootstrap.py` `_normalize_search_invocation` returns the unknown first arg as search args and `tg edit-ready --help` prints `Usage: tg search` with exit 0, so an agent concludes a nonexistent command exists.
  Receipts: docs/agent-laws-receipts.md#a90
- **A91 — "No core-Rust logic" never means "no native touch" (2026-08-09).** The public surface is the managed native `tg.exe`, so a Python/sidecar feature that misses native front-door enrollment (`Commands::X` passthrough plus the `PUBLIC_TOP_LEVEL_COMMANDS` parity test) is invisible through the real binary. Every Python-first slice states its both-front-door and 4-site-registration enrollment in the same slice.
  Receipts: docs/agent-laws-receipts.md#a91
- **A92 — Executed evidence must be escrowed to a key the verified principal does not hold (2026-08-09).** A verify-edit PASS requires escrowed subprocess evidence (captured stdout-hash, exit code, duration) signed by a key pinned via `TG_EVIDENCE_TRUSTED_KEYS` that the editing principal cannot use (CI-held), because "validation ran green" certified by the editing agent is self-attestation (Oracle Form 8). Absent that, the verdict is UNVERIFIED with a reason, never PASS; a ticket must also carry `base_sha` plus a working-tree fingerprint and verify must fail closed on drift.
  Receipts: docs/agent-laws-receipts.md#a92
- **A93 — Self-dogfood is self-consistency, not demand, and roadmap premises need ground truth before the council (2026-08-09).** A pass of tg dogfooding tg proves tg works for itself, so self-triaged "bad oracle" rows need external-customer grounding (S1-S7 demand). Any plan entering the design council must first premise-check its "already shipped" or "partially banked" claims against origin/main (A75), or the council certifies fiction.
  Receipts: docs/agent-laws-receipts.md#a93
- **A94 — Skill/doc version stamps rot one release after the last refresh; freshness is a maintenance sweep (2026-08-11).** Every "verified against vX" line and hand-written derivation count is a snapshot, so run the `tensor-grep-release-drift-check` skill after every release: version-stamp grep below the current tag, re-derived counts (language tier via `_symbol_navigation_descriptor()`, skill count = `tensor-grep-*` folders plus `code-search-and-retrieval-reference` with the bare `tensor-grep` usage skill excluded, tree-sitter package count), and known-state facts. Dated receipts (`docs/audits/*`, ledgers) get append-only SUPERSEDED blocks and are never silently rewritten, while a skill's present-tense instruction is rewritten in place with history in `git log`; this is a command like `.claude/skill_anchor_audit.py`, deliberately not a pytest, because the numbers drift by design and a hard gate would red every PR.
  Receipts: docs/agent-laws-receipts.md#a94
- **A95 — A "verified correct — do not fix" note is part of the contract it guards (2026-08-11).** Update the note in the same change that breaks it, because a fix-note that outlives its own stated number tells the next agent a stale count is right. Adding a skill folder is a multi-site edit: the count, the re-derivation echo, the bucket list name, and the AGENTS.md mirror, with the note itself as one of the sites.
  Receipts: docs/agent-laws-receipts.md#a95
- **A96 — Non-ASCII punctuation in governed docs defeats byte-exact `edit`-tool matches; splice by line index (2026-08-11).** Em dashes (U+2014) and en dashes (U+2013) make `edit` replacements fail with "oldString not found" while the text looks identical. Write a `.py` script file that reads with `encoding="utf-8"`, locates the target by line index with an assertion (`assert "needle" in line[i]`), splices, and writes back with `newline=""`; do not quote the line or use `python -c` from PowerShell.
  Receipts: docs/agent-laws-receipts.md#a96
- **A97 — An interrupted/aborted tool call may have already applied; read the target state before re-applying (2026-08-13).** After any interrupted or ambiguous tool result, read the file back before retrying and never re-apply blind, because a double-apply duplicates sections and reads as two authoritative copies.
  Receipts: docs/agent-laws-receipts.md#a97
- **A98 — A spot-check census of N files is a claim about the one file checked (2026-08-13).** A census over N files needs a mechanical per-file diff or an explicit per-file disposition, because generalizing from one member misses content that only a per-file check would find (the population is the defect class).
  Receipts: docs/agent-laws-receipts.md#a98
- **A99 — An audit/verification tool must be bound to the artifact it audits (2026-08-13).** A verifier records the audited root, HEAD SHA, and a path/blob manifest, and a coverage claim requires exact set equality between the expected population and the reported coverage. A truthy response that omits members is PARTIAL, a null lane is CANNOT_VERIFY, and a CLEAN verdict needs non-zero sampled evidence, never clean-on-empty.
  Receipts: docs/agent-laws-receipts.md#a99
- **A100 — A workflow/tool that advertises a capability must actually execute it (2026-08-13).** An unconsumed schema or un-run phase is a false advertisement, so wire advertised structure (phases, `phase(...)`/`agent(...)`, a terminal `return`) to execution, and label a stub that merely looks like a tool as such before anything depends on it.
  Receipts: docs/agent-laws-receipts.md#a100
- **A101 — The third recurrence of the same flake is a structural-fix signal, not a rerun signal (2026-08-13).** A rerun self-heals once; on the third sighting fix the probe (raise the timeout or make it tolerant) instead of rerunning. Record the recurrence count beside the flake so the next session sees it is not a fresh one-off.
  Receipts: docs/agent-laws-receipts.md#a101
- **A102 — Input-brief facts are hypotheses; the builder must verify them against the tree before writing on them (2026-08-13).** Like an implementer's output report (A81), a brief's stated facts are hypotheses until re-derived from the tree. A seat verifies each load-bearing input fact before writing on it and reports any brief fact that fails verification rather than silently propagating it.
  Receipts: docs/agent-laws-receipts.md#a102


- **A103 — A RED-arm baseline swap must snapshot the builder's uncommitted bytes before touching the file (2026-08-13).** Reverting a file to its pre-fix revision (`git checkout origin/main -- <file>`, an `Out-File`/patch apply) in a builder's worktree destroys uncommitted work in that file. Copy the current bytes aside first, and prefer re-editing the single mutated line back over reverting the whole file (single-file variant of the "git stash is unsafe once parallel worktrees exist" law).
  Receipts: docs/agent-laws-receipts.md#a103
- **A104 — The A3 adversarial gate is a real-finding convergence loop; it ends only on independent SHIP, never on round count (2026-08-13).** Budget 10+ rounds for a security PR, because nearly every round yields a genuine FIX-FIRST (a fault-injection seam that bails before the stat, a trailing-separator stat bypass, residuals without a filed owner row, a cited board row nobody filed, an unobservable skip path). The independent gate still fires after the builder's self-gate is green (A18).
  Receipts: docs/agent-laws-receipts.md#a104
- **A105 — Normalize the path before a no-follow stat, and own the residuals a leaf-stat cannot cover (2026-08-13).** On POSIX, `lstat("dirlink/")` resolves through the final symlink, so strip trailing separators (e.g. `Path::components().collect()`) before the stat or a trailing-slash path bypasses `is_symlink()`. `symlink_metadata` lstats the leaf only, so a symlink in a non-leaf ancestor and the directory-root swap window are residuals that must be named in the code comment, the threat model, and a filed follow-up row, never silently absorbed (A38/A48/A49).
  Receipts: docs/agent-laws-receipts.md#a105
- **A106 — A green test that can silently skip is a hazard; promote skips to panics via an env var armed in CI (2026-08-13).** Every skip site in an environment-dependent test whose silent skip would masquerade as coverage panics with an explicit message when its env var is set (`TG_REQUIRE_SYMLINK_TESTS` for the symlink guard tests), and CI arms it (A88 / Oracle Form 3).
  Receipts: docs/agent-laws-receipts.md#a106
- **A107 — A contested platform fact is settled by a bounded probe on the pinned toolchain, not by council vote (2026-08-13).** Write a short std-only `cargo run --release` probe on the pinned Rust 1.96.0 and have every seat cite it. A law whose embedded claim is superseded must itself carry an append-only SUPERSEDED note (A88's "junctions are NOT symlinks" is wrong on this toolchain, where a junction reports `is_symlink: true`), and every skill quoting it is corrected in place (A94 scope rule).
  Receipts: docs/agent-laws-receipts.md#a107
- **A108 — Plan-council convergence: hash-freeze each round, fix only the confirmed findings, failed seats are not votes, and a verdict-dependent step is a named gate (2026-08-13).** Fix the confirmed findings, re-hash the artifact, and re-run until N/N APPROVE, recording no-verdict seats as FAILED and excluding them. Never use an "EXPAND AT WAVE START" marker for a step that depends on a future verdict; write it now as a named gate with an exact command, a concrete pass/fail trigger, and a re-approval rule covering the FAIL branch (A35/A46/A51).
  Receipts: docs/agent-laws-receipts.md#a108
- **A109 — Bounded test handshakes use capacity-1 channels, never a capacity-0 rendezvous (2026-08-13).** A capacity-0 `sync_channel` `send` blocks forever when its peer never arrives. Use capacity-1 channels (non-blocking sends) plus `recv_timeout` on every receive, and treat an expiry as a deadlock detector that panics `CANNOT_MEASURE:`, never a verdict (A17).
  Receipts: docs/agent-laws-receipts.md#a109
- **A110 — `git commit --amend` is safe only while the branch has never been pushed (2026-08-13).** Before amending, `git log --oneline origin/<branch>` must print nothing (no remote ref); otherwise make an ordinary second commit, because an amend after a push rewrites history sibling agents may have fetched. No force-push.
  Receipts: docs/agent-laws-receipts.md#a110
- **A111 — Commit the plan you cite (2026-08-14).** Docs merged onto main must not cite plan/spec paths that do not exist in the merged tree, because an untracked approved plan breaks every citation downstream. When committing a previously-untracked approved artifact, record the pre-format witness hash and the committed hash (A46 extension).
  Receipts: docs/agent-laws-receipts.md#a111
- **A112 — A plan-frozen control threshold is met verbatim or the arm is CANNOT_MEASURE (2026-08-14).** If a looped probe's control reports a different number than the plan froze (for example 1600 where the plan froze `failures == 20`), add a single-shot arm that reports exactly the frozen number; recharacterizing the frozen number as illustrative is a plan violation, not a fix.
  Receipts: docs/agent-laws-receipts.md#a112
- **A113 — Claim only what the raw artifact discriminates (2026-08-14).** Only the discriminated arm may be given a specific class (one connect-timeout among arms that all timed out), an undifferentiated `TimeoutError` cannot be upgraded to a specific class in prose, and environment readings the harness did not record (CPU%) are observations, not data.
  Receipts: docs/agent-laws-receipts.md#a113
- **A114 — A corrected census is not closed until its location inventory is mechanically re-derived (2026-08-14).** Totals can be right while the named lines are wrong, and a census note's own prose is auditable content. Re-derive locations with a script, never from memory of the file.
  Receipts: docs/agent-laws-receipts.md#a114
- **A115 — Wave receipts are per-row tables, not group sentences (2026-08-14).** Give each row its own command and output in a table, because "six rows, six commands, six recorded results" as one sentence is a claim, not a receipt (A98 applied to board waves).
  Receipts: docs/agent-laws-receipts.md#a115
- **A116 — Never let `uv run` create a venv inside a bare worktree (2026-08-14).** `uv run pytest` in a worktree without `.venv` creates an empty broken venv (`No module named pytest`). Run worktree tests from the main checkout's venv targeting worktree paths (`uv run --no-sync python -m pytest "<worktree>/tests/..."`) and remove any accidentally created worktree `.venv` immediately.
  Receipts: docs/agent-laws-receipts.md#a116
- **A117 — Operator “skip Fable” waives that design-audit seat for the named docs packet only (2026-08-15).** It does not authorize product code, spend, CEO_GATED flips, or treating a quota substitute as durable clearance (extends A74). Record the waiver on the PR; Sol/Codex exact-commit APPROVE is still required for the packet bytes.
  Receipts: docs/agent-laws-receipts.md#a117
- **A118 — Local `gh pr merge` failure is not remote truth when another worktree owns `main` (2026-08-15).** `fatal: 'main' is already used by worktree` can abort locally after GitHub already merged. Judge by `gh pr view --json mergedAt`, use the merge API if needed, and never assume "failed" means "not merged" or double-merge.
  Receipts: docs/agent-laws-receipts.md#a118
- **A119 — Docs-only PR job skips are not a cheap main push (2026-08-15).** The PR `changes` gate may skip expensive jobs, but a `push` to `main` always runs the full matrix, so do not forecast main wall-clock from PR skipped-job green.
  Receipts: docs/agent-laws-receipts.md#a119
- **A120 — Enclosing shell timeout must strictly exceed probe duration (+ frozen grace) (2026-08-15).** A shell `timeout` equal to the probe's wall duration means the probe cannot finish cleanly (Sol REVISE). Freeze duration, grace, and outer timeout as three numbers.
  Receipts: docs/agent-laws-receipts.md#a120
- **A121 — Raising `request_queue_size` without a finite fail-closed aggregate pre-auth concurrency cap enlarges DoS admission (2026-08-15).** `ThreadingMixIn` spawns a thread per accept, so a larger listen backlog without R7 is an incomplete DD-006-PERF design.
  Receipts: docs/agent-laws-receipts.md#a121
- **A122 — Demand SATISFIED + design packet on main is not SHIPPED (2026-08-15).** Parent DD-006 still needs both DD-006-PERF and DD-006-HONESTY product code under a separate deliberate build go (TDD + A3), so do not close the board row on docs alone.
  Receipts: docs/agent-laws-receipts.md#a122

- **A123 — A PR whose base is a feature branch gets zero CI, and the absence renders as "skipping" (2026-08-21).** `ci.yml` filters `pull_request: branches: ["main"]`, which matches the base ref, so a stacked PR shows only a skipped `Dependabot Automation` check while `gh` reports `MERGEABLE`. Before merging, assert `baseRefName == "main"` (`gh pr list --state open --json number,baseRefName`) and treat a "skipping"-only rollup as an absent gate, not a pass. `gh pr edit --base main` alone does not restore CI (close/reopen does), and after the parent squash-merges, rebase the child with `git rebase --onto origin/main <parent-tip>` to drop the absorbed commits.
  Receipts: docs/agent-laws-receipts.md#a123
- **A124 — Verify a release per-artifact, by expected filename set, never by the version appearing (2026-08-21).** A tag or version string can read as "released" while PyPI holds only some of the files (a missing wheel or sdist) or none, so `pip install` yields different versions per platform. Check the expected filename set for every release, not just the newest, because several past releases were incomplete.
  Receipts: docs/agent-laws-receipts.md#a124
- **A125 — "Advertised" is not "installed", and a maintainer's machine is the wrong population (2026-08-21).** A capability the CLI advertises (for example `tg scan --ruleset`) can fail on a stock `pip install tensor-grep` when its dependency is in no extra and no native binary is bundled, while working on a dev box that has the tool installed separately. Run any acceptance test for a capability in a clean container off the published artifact. The standard to follow is `tg find`, which degrades visibly, still returns results, and names its fix (`tg install-dense`).
  Receipts: docs/agent-laws-receipts.md#a125
- **A126 — A file split must reproduce its baseline pass and skip counts (2026-08-21).** Capture both the pass and skip counts before touching anything, because a split that reports fewer passes plus new skips can look green while invented `pytest.skip(...)` guards disable tests that pass in CI. Never silence a post-split failure with an environment probe; a bare worktree has no compiled native extension, so native or embedded arms fail there and pass in CI, which is an environment artifact to report rather than guard around.
  Receipts: docs/agent-laws-receipts.md#a126
- **A127 — Read exit codes unpiped (2026-08-21).** A pipe such as `docker build … | tail` or `| head` reports the last command's status, not the build's, so it can show exit 0 with no image produced or mask a correct rc=1. For any command whose status you will act on, run `cmd > log 2>&1; echo $?` and verify the artifact itself (`docker images …`), the one claim a misread pipe cannot fake.
  Receipts: docs/agent-laws-receipts.md#a127
- **A128 — "Pre-existing / environment / not mine" is a hypothesis, not a finding (2026-08-21).** Each such dismissal has hidden a real defect: a `tg scan` exit 0 on a missing path was a security-surface false-zero, a CI-only AST failure came from an `ast-grep`/`sg` CLI binary on PATH (a different signal from the `ast_grep_py` package), and a locally failing guardrail test was a broken shim (A129). Run the cheap discriminating measurement before dismissing, because checking costs minutes and dismissing ships the defect.
  Receipts: docs/agent-laws-receipts.md#a128
- **A129 — Resolve a caller's module namespace by leaf name, not a dotted prefix (2026-08-21).** `tests/` has no `__init__.py`, so pytest's prepend import mode names modules by basename (`test_cli_modes_blast_radius`, not `tests.unit.test_cli_modes_blast_radius`). A `startswith("tests.unit.…")` check therefore matches nothing and the stack walk falls through to `return globals()`, silently reproducing the stale-fakes failure the shim exists to prevent, because falling back to a real namespace looks like success.
  Receipts: docs/agent-laws-receipts.md#a129
- **A130 — The file-size ratchet forbids growth: pay for an addition, never raise the pin (2026-08-21).** Never raise the pin to let a new unreviewed handler pass; instead move code out (for example a scan helper into `scan_guardrails.py`) so the file does not grow. The limit is currently unreachable for the three giants (`repo_map.py`, `main.py`, `mcp_server.py`) because monkeypatch targets lock them to their facades (`scripts/measure_split_floor.py` reports `SPLIT CANNOT REACH THE LIMIT`), so either reduce monkeypatch coupling or state the exception honestly rather than carry an allowlist entry implying a completion that cannot come.
  Receipts: docs/agent-laws-receipts.md#a130
- **A131 — Docker ignores `.gitignore`, and its patterns are root-anchored (2026-08-21).** Transient directories and nested virtualenvs (for example `rust_core/.venv/`) abort the build context sender because a bare `.venv/` only excludes the top-level one. Prefix every transient pattern in `.dockerignore` with `**/` and exclude the family (`.tmp*/`) rather than the instances that happened to bite.
  Receipts: docs/agent-laws-receipts.md#a131
- **A132 — Same name, different meaning: classify, never sweep (2026-08-21).** `_BROAD_GENERATED_SCAN_DIR_NAMES` exists in both `cli/main.py` and `cli/scan_guardrails.py` as deliberately different sets, so collapsing them during a helper move would silently change behaviour at the call site. Also, a guard's own docstring can trip its own grep: assert on the code (the assignment), not the substring.
  Receipts: docs/agent-laws-receipts.md#a132
- **A133 — A queued run is not protected by `cancel-in-progress`; merge churn kills releases (2026-08-21).** `cancel-in-progress` governs only runs already in progress, so a run still queued in the same concurrency group is superseded by the next push, and on this runner-scarce repo every merge could cancel the previous release run before it started. This is a second, independent cause of "tagged but not published", separate from PYPI-SIZE-CAP. **Burst half of the merge rule (A142 is the hold half): batch every green PR into one burst, then STOP pushing and let a single run publish them all, because the release is cumulative from the last tag and merging more before the run starts loses nothing.** Afterwards poll the burst's run by ID (`gh run view <id> --json status`) until it reads `completed`, never gate on `--limit 1` (A139); if the run completes without publishing (red, or semantic-release made no release), the window closes at completion and A32 governs the hotfix.
  Receipts: docs/agent-laws-receipts.md#a133
- **A134 — On a runner-scarce repo, re-pushing to "re-trigger CI" starves it (2026-08-21).** PR refs have `cancel-in-progress` true, so each rebase-push, fix-push, or empty-commit push cancels the queued predecessor and the head SHA can end up with no run at all. Stop pushing, and before concluding CI is broken check queue depth (`gh run list --limit N --json status`), because a sibling branch's run sitting queued identifies scarcity rather than a dispatch fault.
  Receipts: docs/agent-laws-receipts.md#a134
- **A135 — A green-detector that counts checks cannot tell a matrix run from CodeQL (2026-08-21).** A rule like `total > 5 and pending == 0 -> GREEN` is satisfied by CodeQL and Dependabot entries alone, so PRs with zero `ci.yml` runs read as terminal green. Assert the checks that matter by name, for example `testcount=$(echo "$rollup" | grep -o '"test-' | wc -l); [ "$testcount" -lt 4 ] && echo NO-CI`, which extends A123 (an absent gate renders as a pass) to your own monitors.
  Receipts: docs/agent-laws-receipts.md#a135
- **A136 — A blocked UI action is not a blocked capability (2026-08-21).** When an interface blocks an action (PyPI has no delete API and its web UI needs a typed confirmation), inspect the mechanism underneath before accepting the limit or handing over a manual click-list. The delete there is an ordinary form POST (`csrf_token` + `confirm_delete_version`) to the release manage URL, which can be driven from the already-authenticated page with no credentials or typing.
  Receipts: docs/agent-laws-receipts.md#a136
- **A137 — One change can trip several independent ratchets, and each wants a different answer (2026-08-21).** A new `except Exception` must satisfy both the disposition ledger (records what it is) and the broad-handler population pin (bounds how many exist), and a moved function can trip the file-size ratchet and the silent-loss census at once. Satisfy each on its own terms and say which case you are in: a relocation re-pins only after proving the total is unchanged and the sites are byte-identical, whereas growth must be hardened or dispositioned, never re-pinned; write that distinction beside the number so a relocation is not cited as precedent for absorbing growth.
  Receipts: docs/agent-laws-receipts.md#a137
- **A138 — A replacement assertion must be probe-verified to discriminate (2026-08-21).** When replacing a flaky wall-clock bound, probe the real surface first: asserting the absence of `partial` / `result_incomplete` was vacuous because neither string appears on the plain-text surface, and a deadline-burning run and a refusal both exit 2, so only "prints matches vs prints none" separates them. Perturb the final assertion (invert it and confirm it fails, then revert and confirm the file is byte-identical) before trusting it.
  Receipts: docs/agent-laws-receipts.md#a138
- **A139 — `gh run list --limit 1` returns the newest run and hides the one actually executing (2026-08-21).** A newer pending run can sit behind an in-progress one, so a windowed list makes a healthy release run look stuck. Once you know which run you care about, watch it by ID (`gh run view <id>`), never by a windowed list; the same trap applies to `gh run list --commit` with `--limit`.
  Receipts: docs/agent-laws-receipts.md#a139
- **A140 — `pending` and `queued` are different states and mean different things (2026-08-21).** `status: queued` means waiting for a runner, while `status: pending` with 0 jobs means held by the concurrency group because an earlier run in the same group is still active, which with `cancel-in-progress: false` on `main` is the system working correctly. Before declaring a run broken, list every non-completed run repo-wide (`gh api "repos/<o>/<r>/actions/runs?per_page=30" -q '.workflow_runs[]|select(.status!="completed")'`) and find what holds the group. Two main merges can produce two releases, one per run, so check which commits each run carries before claiming what shipped.
  Receipts: docs/agent-laws-receipts.md#a140
- **A141 — An unrecognised pytest argument can report success through a wrapper (2026-08-21).** A command such as `pytest tests/unit -q --timeout=300` fails at argument parsing when `pytest-timeout` is not installed, yet a background wrapper can report `[exited with code 0]` although the suite never ran. Read the tail of the output, not the exit status, because a test command that dies before collection is the most convincing false green; see A127 and the plugin-availability class (`-p no:cacheprovider`).
  Receipts: docs/agent-laws-receipts.md#a141
- **A142 — Corrects A133: "Batch the merges, then stop" must stop the moment a run is in progress, not merely before the next one (2026-08-21).** A133's "batching is free" holds only for the burst that creates the run. **A release-bearing run's window is the whole run from creation to the release push, because merging while `Semantic Release` is pushing its `chore(release)` commit rejects that push (`! [rejected] main -> main (fetch first)`) and the release fails even with a fully green matrix.** A `queued` / `pending` / `jobs=0` run still pushes last, so any status other than `completed` (`queued`, `pending`, `waiting`, `requested`, `in_progress`) is an open window. Merges that create the run (your own burst) are fine; once the burst is over, or when any release-bearing run already exists, merge nothing until it is `completed` and its `chore(release)` commit and PyPI publish have landed. List runs with `gh run list --branch main --workflow=ci.yml --limit 5 --json status,headSha,databaseId` (A139), then poll that run by ID (`gh run view <id> --json status,conclusion`); if it completes without publishing (red, or no release made), the window closes at completion and A32 governs the hotfix. A failed release self-heals on the next push because the successor run carries the same unreleased commits cumulatively.
  Receipts: docs/agent-laws-receipts.md#a142

- **A143 — A gate that can only be satisfied by a false statement is a defect, not a standard (2026-08-22).** A governance test that pins one literal outcome (for example "the latest complete public distribution is also `<tag>`") becomes a mandate to lie the moment reality takes the other branch. Assert the shape of a definite statement, never one of its possible values: accept either the completeness claim or an explicit ``**`<tag>` is TAGGED AND NOT PUBLISHED`` disclosure, both of which name the tag so vague prose cannot satisfy either.
  Receipts: docs/agent-laws-receipts.md#a143

- **A144 — A doc-staleness gate with a tolerance is a time bomb: it arms itself with every release and detonates on an unrelated commit (2026-08-22).** A gate like `test_task_board_reconcile_stamp_is_not_many_releases_stale` (tolerance 5 releases) fails whatever the next commit contains once enough releases ship. After a multi-release day, reconcile the board before the next merge, and when such a gate fires ask "how many releases since the last stamp" first, not "what did this PR break", because blaming the PR sends you auditing an innocent diff.
  Receipts: docs/agent-laws-receipts.md#a144

- **A145 — A ratchet's offender set can be defined by the tests, not the source, so a test-only change can red a file the diff never touched (2026-08-22).** `scripts/bare_call_ratchet.py` counts calls to names the suite patches on a module, so adding one `monkeypatch.setattr(cli_main, "dense_available", ...)` turns existing untouched bare calls into unpinned offenders. Diff the pins file before theorising a clobbered rebase, compare the patch set rather than the call sites, and note that its message "the pins file is empty but targets still have bare calls — the gate is off" is correct when every Route A target is converted.
  Receipts: docs/agent-laws-receipts.md#a145

- **A146 — An ambient env var can turn a fail-closed test green, which is the worst direction for a harness to be wrong in (2026-08-22).** `test_missing_python_reports_actionable_error` clears `PATH` but not `TG_SIDECAR_PYTHON`, so a globally exported interpreter makes it exit 0 instead of the required exit 2. Export nothing a CI job does not export, because a false red wastes an hour while a false green on a fail-closed test silently retires the guard (same class as A128).
  Receipts: docs/agent-laws-receipts.md#a146

- **A147 — A path filter must watch what a lane reads, not only what it is (2026-08-22).** `ci.yml`'s `code` filter did not watch `docs/audits`, where the handler-disposition ledger (test input for `test_handler_dispositions.py`) lives, so a ledger-only PR was classified docs-only and skipped every `test-python` lane including the consuming test. Derive the filter from what each lane reads rather than patching paths one incident at a time, because it has been wrong in both directions (the `scripts/` hole is the reverse case).
  Receipts: docs/agent-laws-receipts.md#a147

- **A148 — Verify a customer-facing claim on a clean install; the maintainer's machine is the wrong population (2026-08-22).** A check that runs where the tool was built cannot falsify a claim about where it is installed: `tg scan --ruleset` worked on a dev box with `ast-grep` on PATH but returns `rulesets_runnable=false` in a clean `python:3.12-slim` container. Check any claim about install-time behaviour in a fresh container off the published artifact, or treat it as unchecked (see A125).
  Receipts: docs/agent-laws-receipts.md#a148

- **A149 — A check is only evidence for the property it can observe (2026-08-23).** An import smoke asserting `hasattr(session_store, name)` passed while CI's mypy with implicit re-export disabled failed all consumers, so runtime presence was weaker than the gate it stood in for. Use the explicit `X as X` re-export form and run the real gate locally rather than approximating it; likewise `ruff format` without `--preview` rewrites preview styling across whole files and CI checks `--check --preview`.
  Receipts: docs/agent-laws-receipts.md#a149

- **A150 — A line number re-stamped once will be wrong again; remove it (2026-08-23).** A cited `file:line` anchor drifts (one cited `TG_REQUIRE_RG_PARITY` line moved onto unrelated commentary), and a re-stamp is just the next stale anchor. Cite the symbol or a bare grep with no line number.
  Receipts: docs/agent-laws-receipts.md#a150

- **A151 — `git -C <dir>` silently answers about the parent repo (2026-08-23).** For a directory with no git metadata, git walks up and confidently reports the enclosing repo's branch and dirty state. Test what a directory is (`-f "$d/.git"` holding `gitdir:`, plus whether `.git/worktrees/<name>` exists) rather than trusting `git -C "$d"`; a cold orphan (admin entry gone, directory present) is invisible to `git worktree list`, `prune`, and `remove` (mechanics: `~/.claude/skills/harvest-agent-worktrees`, "The COLD case").
  Receipts: docs/agent-laws-receipts.md#a151

- **A152 — A probe whose result licenses destruction runs its control first (2026-08-23).** Run a positive control (for example the same pattern over the repo's own `src/`) before trusting a size or emptiness figure that decides whether something gets deleted, because a false zero here authorises an irreversible delete.
  Receipts: docs/agent-laws-receipts.md#a152

- **A153 — The maintenance sweep rots, and it rots invisibly (2026-08-23).** An artifact whose stated purpose is freshness (such as `tensor-grep-release-drift-check`) reads as evidence that it ran, so audit the auditor first. Do not re-stamp known-state facts to a new version unless the checks were re-run at that version, because re-stamping converts "stale but honest" into "current and false".
  Receipts: docs/agent-laws-receipts.md#a153

- **A154 — A monitor that cannot read its own signal manufactures the appearance of supervision (2026-08-23).** `jq` is not on PATH here, so `gh pr checks N --json bucket | jq ...` yields an empty string that fails every numeric comparison and leaves the "all clear" branch unreachable. Use `gh`'s built-in `--jq` instead of an external pipe, treat "timed out without producing output" as "never worked" rather than "still running", and give every monitor a probe self-check that aborts when blind: `probe=$(...); case "$probe" in ''|*[!0-9]*) echo ABORT; exit 2;; esac`.
  Receipts: docs/agent-laws-receipts.md#a154
- **A155 — Pre-push silent-failure and hygiene ratchet preflight (2026-09-03).** Before pushing to `main` or opening a PR for changes touching `src/`, run `tests/unit/test_silent_failure_hardening.py` (`test_broad_exception_handler_population_does_not_regress`) and `ruff format --preview --check .` until both exit 0, because a bare `except Exception:` in `src/` violates the AST silent-failure ratchet and breaks every `test-python` and `test-gpu-nvidia` lane. Narrow exceptions to explicit typed tuples such as `(FileNotFoundError, KeyError, PermissionError, ValueError, json.JSONDecodeError)` or `(UnicodeDecodeError, OSError, ValueError)`.
  Receipts: docs/agent-laws-receipts.md#a155
- **A156 — Route A late lookup and explicit re-export for monkeypatched symbols (2026-09-04).** When tests patch a symbol (for example `collect_device_inventory`), internal callers in the module must call it through the late attribute lookup `_self.SYMBOL(...)` rather than a bare call, because a bare call trips `test_bare_call_ratchet.py` (`test_every_target_is_either_pinned_or_converted`) in CI even when the function-level test passes. Under mypy `implicit_reexport = false` the imported symbol must be explicitly re-exported (`from pkg import SYMBOL as SYMBOL`), otherwise `_self.SYMBOL` raises `attr-defined`.
  Receipts: docs/agent-laws-receipts.md#a156
- **A157 — Sanitizing error wire responses under hostile metaclasses and pattern bindings (2026-09-04).** In MCP error sanitization (SEC-007), check exact type identity with `type(type(exc)) is type` and linear iteration `type(exc) is trusted_cls`, not `isinstance(exc, TrustedClass)` or `type(exc) in SET`, because hostile metaclasses can override `__eq__` and `__hash__`. AST ratchet enforcement must verify both caller boundaries and positional argument slots in error sinks, so a parameter swap cannot pass raw exception text as the message string.
  Receipts: docs/agent-laws-receipts.md#a157
- **A158 — Separation of the public open-source tree from internal agent governance (2026-09-04).** Public repositories must present clean root layouts: internal agent maps, scratch, and audit trails (`.build/`, `.wayfinder/`, `.orchestrator/`, `MEMORY.md`) belong in `.gitignore` and must never be tracked on public GitHub. Standard collaboration infrastructure (`.github/` workflows and issue templates, `docs/`, `tests/`, `src/`) stays public.
  Receipts: docs/agent-laws-receipts.md#a158
- **A159 — CodeQL clear-text logging taint on confinement and error diagnostics (2026-09-04).** CodeQL flags clear-text logging of potential secrets when exception objects or candidate path variables are logged directly (`print(f"...: {candidate}: {exc}", file=sys.stderr)`). Server-side debugging logs must sanitize or label the message type so raw tainted variables do not trigger secret-leak alerts, while keeping debugging visibility.
  Receipts: docs/agent-laws-receipts.md#a159

- **A160 — A majority APPROVE does not clear a verified defect (2026-09-13).** Count content votes for the stopping rule (two consecutive clean rounds on the same unchanged hash, at least 4 content votes), but never let a majority overrule a finding you have verified against real code, because seats that approve verify what the plan points at while a seat reconstructing control flow from inlined source checks what it does not. Verify a finding before acting on it (some council findings are refuted by a citation that resolved or a line number already correct), and dispatch the inlined-source seat together with the council, not late.
  Receipts: docs/agent-laws-receipts.md#a160

- **A161 — A brief that both inlines a file and carries an abstention clause produces an abstention (2026-09-13).** A generic "if you cannot read the file, say CANNOT_READ_REQUIRED_FILE" clause makes a seat abstain on a blocked shell call even though the plan is inlined in its brief. When a brief inlines its sources, say so first and state that a blocked file read is not an abstention condition, scoping abstention to "a passage you need is genuinely absent from this brief". Pin that preamble in the brief builder and never retype it per round, because the builder had already silently dropped source regions.
  Receipts: docs/agent-laws-receipts.md#a161

- **A162 — Fixing one passage of a plan invalidates others, and re-reading cannot find it (2026-09-13).** A plan is a control flow, not prose, so re-reading confirms each passage in isolation, which is the check that cannot fail. After any plan edit, derive the cross-references: `grep -nE "Step [0-9]|Task [0-9]"` for step references, `grep -n` every file path and CI job name against the real tree, and confirm every edited file appears in `Files:`, in File Structure, and in the `git add`. Write post-conditions as assertions in the patch script so a non-unique anchor writes nothing, and do not trust your own expected count in those assertions (Form 9: the reviewer's expected number is the broken half).
  Receipts: docs/agent-laws-receipts.md#a162

## Current Handoff
release_docs_current_tag: v1.123.11


The current tagged release state is `v1.123.11`, and the latest complete public PyPI/release-asset distribution is also `v1.123.11` — verified PER-ARTIFACT, 4/4: the `macosx_11_0_arm64`, `manylinux_2_39_x86_64` and `win_amd64` wheels plus the sdist. HISTORICAL, still true of those tags: `v1.111.2` is TAGGED AND NOT PUBLISHED (ZERO files on PyPI) and `v1.111.1` carries only 2 of its 4 artifacts (no `win_amd64` wheel, no sdist), so installs on those lines resolved inconsistently per platform. Both were PYPI-SIZE-CAP casualties; the cap was cleared on 2026-08-21 (713 → 287 releases, 10.734 → 4.747 GB, ~280 releases of headroom), which is why `v1.111.3` could publish at all. See `docs/BACKLOG.md`. Per A124, verify a release by its expected filename set, never by the version appearing — a partial publish leaves 'latest' resolving on some platforms and silently stale on others. The stable installer, release-native asset publication, managed-native `tg upgrade` refresh path, stale tensor-grep-owned `tg.com` bridge refresh after upgrade, native-front-door CLI parity fixes, Windows `.cmd` quoted-pattern launcher fix, native-first Windows PATH ordering, top-level validation-command contract, local default `classify`, classify provider provenance, fixed multi-pattern native CPU search, GPU scale benchmark correctness gates, launcher-route observability, benchmark launcher attribution, scoped GPU device probing, benchmark launcher warnings, opt-in `tg agent` Actionable Context Capsule, mixed-language capsule confidence/validation alignment, GPU benchmark recommendation hygiene, edit JSON/rollback safety, explicit language/file-name agent ranking, Windows validation-command quoting, docs/version governance, `$file` / `{file}` validation placeholder substitution, native CUDA correctness gates, ambiguous capsule alternative-target surfacing, root help-menu diagnostics, foreign launcher diagnostics, benchmark promotion-gate taxonomy, agent workflow benchmark governance, capsule alternative-confidence capping, generic provider-token `secrets-basic` regex rules, release-docs synchronization, release wheel Cargo prefetch retries, native GPU/search accuracy hardening, explicit Windows Python subprocess launcher repair, agent capsule hardcase routing, Windows subprocess bridge ranking hardening, and long-lived agent-loop memory/cache caps are released through `v1.123.11` GitHub assets and PyPI. Follow-up work should focus on context/session latency, GPU production viability, token economy, call-site evidence, AST parity roadmap, classify provider/cache UX, and keeping docs synchronized with release proof.

- PyPI pinned install: `uvx --refresh-package tensor-grep --from tensor-grep==1.123.11 tg --version` reports `tensor-grep 1.123.11`
- GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.123.11>

**2026-08-15 CEO/backlog update (historical).** Public product was then **`v1.110.16`**.
Closed-world: **29 rows / 17 unfinished** = 0 READY, 0 IN_FLIGHT, 6 BLOCKED, 5 CEO_GATED,
6 DEMAND_GATED (8 SHIPPED + 4 RETIRED). DD-006 design packet merged (#1015 / `0710219`); demand
SATISFIED earlier; **product build not started**. Fable waived for that docs packet only (A117).
New laws **A117–A122**. Detail: `docs/audits/2026-08-15-ceo-backlog-update.md`.


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

**2026-07-14 Current-Handoff addendum -- GPU Phase-0 hardening wave (v1.75.1-v1.75.4, audit #171).** The wave closed audit #171's P0-1 through P0-5 GPU findings: the doctor/agent GPU probes bridge a WSL path-domain mismatch (failing closed to a distinct `path_domain_mismatch` status), report a structured `native_error_kind` taxonomy (`failed_path_bridging` / `failed_input` / `failed_gpu_unavailable` / `failed_other`), warn on an out-of-range `--gpu-device-ids`, and CI runs a `cargo check --features cuda` anti-bit-rot gate on every PR. See `docs/gpu_crossover.md` for the GPU promotion-status read and the Roadmap Sequencing section for the Phase 0/1/2 framing.
Receipts: docs/agent-laws-receipts.md#release-history-2026-07-14-gpu-phase-0-hardening-wave-v1751-v1754-audit-171

**2026-07-16 addendum -- `tg find` CPU semantic moat (v1.77.0-v1.78.1, campaign #189).** `tg find` is whole-repo natural-language code search (BM25 + local CPU dense embeddings -> weighted RRF -> budget-fitted `file:line` output), shipped as a CLI command through the standard 4-site registration path and as its own MCP `tg_find` tool (contract in `docs/harness_api.md`). It fails closed (`BackendExecutionError` -> exit 2; chunk-cap / `--max-repo-files` / `--deadline` truncation -> `result_incomplete=true` + exit 2, never a silent partial-as-complete), and a BM25-only degrade is visible via `rank_fallback_reason`. The `TG_FIND_DENSE_WEIGHT` adaptive knob is default-OFF (byte-identical no-op at `1.0`) and has not been flipped. A new MCP tool is a 5th registration site: bump the MCP contract version.
Receipts: docs/agent-laws-receipts.md#release-history-2026-07-16-tg-find-cpu-semantic-moat-v1770-v1781-campaign-189

**2026-07-22 Current-Handoff addendum -- session-capture wave (v1.91.1 -> v1.93.2, 15 shipped items).** The wave shipped a cold-path SLA fix, a ranking-accuracy fix, honesty/fail-closed fixes and a UX/coordination batch; its durable rules are A13, A16, A17, A18, A19 and A21. Intra-file rayon parallelism exists only on the `backend_cpu.rs` PyO3/FFI fallback path; the default `native_search.rs` streaming path stays deliberately serial for its tested >=25ms first-match contract, so do not cite one engine's numbers for the other. Research retirements (durable, do not re-chase): cAST structural chunking is rejected as default (see `tensor-grep-failure-archaeology` Battle 17); dense int8/PCA compression is deferred; many-pattern Aho-Corasick has a live dedup over-count bug, guarded not fixed; warm-session search serving is a big refactor; GPU-for-search has no crossover at any scale and the shipped kernel is brute-force, not PFAC (publish stays on hold). Verify every "cheap win" against the live code before building.
Receipts: docs/agent-laws-receipts.md#release-history-2026-07-22-session-capture-wave-v1911-v1932

- Recent fix commits (the full list of 70+ historical fix commits is in the receipts file; the three below are the pinned release-governance anchors):
  - `2100122 fix: harden release docs stamp governance`
  - `361e0db fix: harden public GPU unavailable routing`
  - `87d4ca4 fix: accelerate fixed multi-pattern native search`
Receipts: docs/agent-laws-receipts.md#release-history-recent-fix-commits
Historical release proof (v1.8 to v1.13.x run IDs and per-version dogfood notes) is retained in the receipts file for the audit trail. It is not proof of the current release; the authoritative facts are the `release_docs_current_tag` line and the current-tag fields above.
- Public `v1.12.12` dogfood: release CI, assets, PyPI, and `uvx --refresh-package tensor-grep --from tensor-grep==1.12.12 tg --version` verified `tensor-grep 1.12.12`; the release includes `b601366 fix: harden agent output budget hygiene` while preserving `2aebac6 fix: harden ast cli contract hygiene (#140)`, `bbc08e4 fix: harden rg flag contract aliases (#139)`, `21627d2 fix: harden v1.12.8 dogfood contracts`, `f848748 fix: route cold rg-shaped searches to rg (#137)`, `da44a2f fix: harden v1.12.6 dogfood cli contracts`, bounded map/context output, `tg run --pattern`, Windows subprocess bridge ranking hardening, `a78e33c fix: harden post-release docs governance`, `361e0db fix: harden public GPU unavailable routing`, `2100122 fix: harden release docs stamp governance`, and the `87d4ca4 fix: accelerate fixed multi-pattern native search` CPU lane from `v1.11.3`. Explicit public GPU requests without sidecar configuration report native GPU unavailable and fall back to `NativeCpuBackend`; public managed GPU is not promotion-ready.
- Public `v1.10.7` dogfood: release CI, assets, PyPI, managed `tg upgrade`, fresh `cmd /c tg --version`, fresh `pwsh -NoProfile -Command "tg --version"`, and managed native `tg.exe` all verified `tg 1.10.7`. The remaining public-launcher blocker was Python `subprocess.run(["tg", ...])` resolving a foreign Together CLI `tg.exe` when Windows `CreateProcess` chooses `.exe` ahead of the tensor-grep `.com` bridge in the same directory.
- Prior public update dogfood: `tg update` from `v1.9.3` initially hit PyPI propagation lag, then installed sidecar `tensor-grep==1.9.4`, scheduled/refreshed the managed native front door, and verified `tg 1.9.4`. Profiled PowerShell, `cmd`, `pwsh -NoProfile`, WSL, Git Bash, and direct managed native `tg.exe` resolved `tg 1.9.4`; `tg doctor --json` reported `version = 1.9.4`, `rust_binary_version_status = matches`, `search_acceleration_backend = standalone-native-tg`, `path_tg_first_launcher_kind = cmd-shim`, `fresh_shell_path_tg_first_launcher_kind = managed-native`, and a `path_tg_launcher_warning` for current shells that still route through the compatibility shim before fresh-shell PATH.
- Public launcher dogfood: `cmd /c tg`, direct managed `tg.cmd`, native `tg.exe`, and Python `subprocess.run([...])` preserve fresh quoted no-match phrases and return exit `1` without false-positive stdout.
- `v1.11.0` GitHub release: <https://github.com/oimiragieo/tensor-grep/releases/tag/v1.11.0> exists, but main CI run `25834508800` was cancelled during release-native asset publication; `publish-success-gate` failed and PyPI latest remains `1.10.10`.
Receipts: docs/agent-laws-receipts.md#release-history-historical-release-proof-pre-v11711
- Session handoff: `docs/SESSION_HANDOFF.md`
- Current follow-up work is tracked in `docs/SESSION_HANDOFF.md`: keep release-native assets verified, preserve the managed installer fallback when assets are absent, keep sidecar and native front-door versions aligned after `tg upgrade`, keep current-process vs fresh-shell launcher routing visible in `tg doctor`, preserve benchmark launcher command-kind attribution and warnings, harden the opt-in `tg agent` context capsule/token-economy surface without changing raw search contracts, keep mixed-language capsule confidence/validation alignment honest, and keep GPU/provider paths experimental until correctness, speed, and UX are proven.

Earlier release lines (pre-v1.17) fixed the Windows `--files-with-matches` rg-backed argument-vector failure, raw rg-style no-path `--files-with-matches` output, malformed pinned Windows installer extras, root-based path-list output, `-0/--null` path-list/count parsing, `tg ast-info --json`, argv-safe PowerShell shims, UTF-8 path-list output, inaccessible PATH-entry handling, managed shim installation, stale Python package cleanup when an old `Python*\Scripts\tg.exe` shadows managed shims, argv-safe `.cmd` bridging, Git Bash / WSL no-extension shims, WSL-aware `/mnt/c/...` paths, LF-only generated bash shims, one-line default version output with verbose details behind `--verbose`, public `Usage: tg` help text, explicit `doctor` diagnostics for stale in-tree native binaries, implicit stale-native skipping for dev searches, public `--format rg` help text for exact ripgrep-style output, context-render/MCP trust invariants, validation command provenance, sorted rg parity edges for files-with-matches, files-without-match, replacement output, and PCRE2 output, multiline rg parity forwarding, exact-symbol context ranking over camel/snake bridge heuristics, explicit language/file-name ranking for Python intent, session stale-file filtering and no-runner validation consistency, embedded checkpoint fallback for MCP rewrite apply when standalone native `tg` is unavailable, inline scan rule severity/message preservation, uppercase `API_KEY` secret scanning, explicit broad generated-root scan refusal unless callers bound the search or opt in, managed native front-door refresh after `tg upgrade`, native-front-door parity for `tg search --files`, `tg search --multiline` / `-U`, `tg search --null`, `tg run -r`, and `tg classify --format json`, classify fallback before expensive provider/model setup when unavailable, GPU benchmark no-match correctness handling, Windows `.cmd` quoted multi-word no-match patterns from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])`, Windows installer User PATH ordering that puts the managed native front-door directory ahead of compatibility shim directories, top-level `validation_commands` on both `context-render` and `edit-plan` JSON, deterministic local default `classify` unless `TENSOR_GREP_CLASSIFY_PROVIDER=cybert` opts into CyBERT/Triton, GPU benchmark defaults/correctness checks for 1GB and 5GB scale rows, explicit GPU device probing that does not initialize or warn about unselected GPUs, benchmark script warnings when timings include shim or interpreter overhead, stale in-tree native binary benchmark refusal by default, parseable edit JSON and rollback on validation failure, quoted Windows validation commands with spaces, `$file` / `{file}` validation placeholder substitution, per-edited-file validation for directory rewrites, and docs-governance tests aligned with current release metadata.

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
- Prefer `blast-radius` over `impact SYMBOL` when direct symbol impact matters.
- Windows launcher/path-list hardening should force UTF-8 for managed shims and Python path-list output; still scope broad file-list commands to avoid generated-tree volume.
- If `cmd /c tg --version`, `pwsh -NoProfile -Command "tg --version"`, or Python `subprocess.run(["tg", "--version"])` resolves a tensor-grep-owned or self-identifying tensor-grep `Python*\Scripts\tg.exe` ahead of the managed native front door, treat it as installer regression evidence. The Windows installer and `tg repair-launcher` should remove verified-owned launchers or back up self-identifying orphaned tensor-grep launchers instead of only warning about them. If that command reports another product's version, treat it as a foreign PATH-shadow blocker: report remediation and keep readiness failing, but do not delete or overwrite the unrelated launcher unless the operator explicitly runs `tg repair-launcher --allow-foreign-rename`, which backs it up first. Python subprocess resolution is a separate Windows contract because `CreateProcess` can choose a foreign same-directory `tg.exe` even when shells prefer a tensor-grep `tg.com` bridge through `PATHEXT`.
- Normal PowerShell should invoke `tg` or `tg.ps1`. Directly invoking `C:\Users\oimir\bin\tg.cmd` from PowerShell with an unescaped metacharacter such as `|` is still a `cmd.exe` parser limitation; quote the argument for `cmd.exe` or use the PowerShell shim. The quoted multi-word no-match pattern case from `cmd.exe`, direct `tg.cmd`, and Python `subprocess.run([...])` is a public launcher contract and must not split into a shorter false-positive search plus bogus paths.
- Implicit native-binary resolution must ignore stale in-tree binaries such as `rust_core/target/debug/tg.exe` and `rust_core/target/release/tg.exe`. `uv run tg doctor --json` should report them under `skipped_native_tg_binaries`, set `rust_binary_version_status = stale-skipped`, and keep `search_acceleration_backend = rust-core-extension` when the embedded extension is available. Rebuild with `cargo build --manifest-path rust_core/Cargo.toml --release` (CPU-heavy on the shared box — see A12; prefer CI) or pin `TG_NATIVE_TG_BINARY` to opt in to a specific standalone binary.
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
- An unscoped `tg search PATTERN` (no path) on a large tree is fast-refused once the implicit walk exceeds `IMPLICIT_SEARCH_WALK_FILE_CEILING` (`src/tensor_grep/io/scan_limits.py`, #702). Always scope `tg search` to an explicit path (e.g. `tg search PATTERN src/`).
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

1. `SEARCH_PYTHON_PASSTHROUGH_FLAGS` in `rust_core/src/search_flag_registry.rs` (imported by `rust_core/src/main.rs`) — the native binary's allowlist.
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

This is the most repeated failure mode in this project: something that looks like verification is not. Before trusting any green signal, ask what the check would show if the thing it verifies were broken; if the answer is "the same", it is not verification.
Receipts: docs/agent-laws-receipts.md#the-verification-oracle-family-ten-forms-2026-07-25-7th-8th-2026-07-26-9th-2026-07-27-10th-2026-07-28

**Form 1 — normalize-both-sides (masks defects; the dangerous direction).** A comparator applies the same lossy transform to both arms, so a real divergence cancels out and reads as parity. Check what the normalizer discards (CRLF, encoding, separators inside matched text); a known accepted limit, such as `_normalize_line` in `tests/helpers/rg_parity.py` folding `\\` to `/` across the whole line, must be pinned by a characterization test with a discriminability control, and that test is meant to fail, and be deleted, when the limit is closed.
Receipts: docs/agent-laws-receipts.md#form-1

**Form 2 — harness-corrupts-output (manufactures false failures).** A harness that post-processes output before comparing (for example `run_tg` in `test_output_golden_contract.py` rewriting `\` to `/` on the whole line) can corrupt a byte-correct result, so a golden diff is evidence about the harness+product pair, never the product alone. Normalize only the part that needs it (`_normalize_output_line` splits at the marker and normalizes only the path prefix) before reading a diff as a product defect.
Receipts: docs/agent-laws-receipts.md#form-2

**Form 3 — test-never-executes. Skipped is not passed.** A test that skips in every CI job reports proof that never ran, for example because a CI step names one hardcoded file or the env gate it needs (`TG_REQUIRE_RG_PARITY`) is not set in a job that also builds the binary. Read the skip count, and grep that the required env gate is set in a job that builds the binary; keep an invariant that every marker-bearing suite is matched by the pattern CI really runs, parsed out of `ci.yml` rather than copied.
Receipts: docs/agent-laws-receipts.md#form-3

**Form 4 — gate-diagnosis-wrong.** A gate can be right about the conclusion and wrong about the cause, so a gate's clearance is a hypothesis and its root-cause story is too. Verify the diagnosis, not only the finding, before relaying it to a build agent. Evidence from isolation (a helper measured alone) is not outcome-level evidence, so any claim that "X causes user-visible Y" needs a control arm through the real entry point; never publish an untested cause, because an explanation that merely fits the evidence is a hypothesis. When RED cannot be observed (CPU-SAFE forbids compiling), state a structural argument from pinned source plainly as an argument, never as an observation.
Receipts: docs/agent-laws-receipts.md#form-4

**Form 5 — the repro's topology deletes the mechanism (2026-07-25, PR #750).** A simplified repro can remove the very thing under test: a fix proved on non-git directories only is a no-op inside a git repo, where `.gitignore` is applied natively. Ask whether the repro deletes the mechanism, and when the executable arm is unavailable give the non-executable arm a second, different fixture that varies topology (git vs non-git, nested vs root, one file vs many); a fix and its tests that share one fixture shape leave that shape as an untested assumption.
Receipts: docs/agent-laws-receipts.md#form-5

**Form 6 — the fixture never applied (2026-07-25, #281).** The assertion can be fine while the setup silently no-opped (for example `icacls` failing to apply a deny ACE), so the hostile arm was never hostile and the probe would declare the bug absent. Write a precondition check that asserts the fixture bites before the probe runs (read the directory and require a `PermissionError`; abort otherwise) for every hostile fixture: permission denials, network partitions, disk-full, killed processes, corrupted files. On Windows apply a deny ACE through PowerShell with the SID from `WindowsIdentity::GetCurrent().User` rather than an `icacls` account string; `icacls <dir> /reset` takes no account name, so if `/reset` also fails the DACL belongs to a different SID.
Receipts: docs/agent-laws-receipts.md#form-6

**Form 7 — the measurement that cannot discriminate (2026-07-26, #302).** A benchmark column where every arm ties at the floor separates nothing and reads as a finding. Every scored dimension needs at least one run where arms differ, or it is deleted with the reason written down. Every probe also carries a positive control: show the same probe returns non-zero somewhere it should before trusting a zero (for example assert the registry is non-empty and print the loaded module's `__file__`, or run the same grep form against a term known to match).
Receipts: docs/agent-laws-receipts.md#form-7

**Form 8 — the split oracle (2026-07-26).** A precondition proved in a different run is not this run's precondition: two correct checks sitting beside a conclusion neither supports. For every conclusion, ask which run produced the evidence for the premise and whether it is the same run that produced the thing being judged; assert the premise inside that run (for example `assert proc.returncode == 2` before reading the summary), and make the failure message name the real cause. Prose describing a test as bidirectional is not evidence that it is, so keep the independent audit step.
Receipts: docs/agent-laws-receipts.md#form-8

**Form 9 — the reviewer's expected number is the broken half (2026-07-27, #334).** A census mismatch is a two-sided hypothesis, and the wrong side is often the expectation brought to it. Read the breakdown and enumerate the members before filing a finding, and re-derive any number another agent hands you (corrected line numbers computed against a stale worktree were wrong); where the class recurs, replace the number with a command that regenerates it (`.claude/skill_anchor_audit.py`, see "Model the class, don't enumerate the cases").
Receipts: docs/agent-laws-receipts.md#form-9

**Form 10 — the oracle's unit is the branch, and the defect lives in the merge (2026-07-28).** Two PRs can each be fully green and still break main together when the collision is semantic and exists only in the union (one asserted exactly one disclosure line, the other added a second), because each PR's checks ran against its own base. Before pushing, rebase onto the real target and run the union, and grep the whole suite for assertions about any output shape you change, since the file you edit is not the boundary of the blast radius; a union is current only as of its own timestamp, so a slice that lands afterwards needs the merge-time gate described in "Seven Instruments, One Empty Queue, And A Release That Reddened Every Open PR".
Receipts: docs/agent-laws-receipts.md#form-10

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

`src/tensor_grep/cli/repo_map.py` is one of the repo's giant files and its line numbers shift between releases. Its seam citations
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

A control that runs cleanly and moves a variable adjacent to the one that matters produces a confident negative that closes the investigation, for example confirming `rust_core` (the Python extension module) is present when the dispatch gate is `resolve_native_tg_binary()` (the compiled `tg` binary). Name the variable as the symbol the code branches on and write the arm as "I set `<symbol>` to `<value>`"; a negative control earns authority only by reproducing the failure in one arm, and a mechanism that reproduces the output is sufficient, not proven operative, until measured (`scripts/diagnose_gpu_delegation_route.py` measures both controls). An ambient dependency in a shared fixture is a hidden arm, so when local and CI disagree, diff the fixture's coverage against the code path, not just the platform.
Receipts: docs/agent-laws-receipts.md#a-control-that-moves-the-wrong-variable-falsely-exonerates-the-right-hypothesis-2026-07-31-868

### A SOURCE-SCANNING census is satisfied by a COMMENT, and blind to POSITION (2026-07-31, #872)

A census that matches a bare substring in a region containing prose is satisfied by the comment explaining the guard, so deleting the real guard stays green. Match AST nodes or check behavior; assert the actual property (`--` before every positional, not merely present), derive the census population from the code at the time of writing and diff it against your own diff, run a control arm's pattern against formatted code (`ruff format --preview` eliminates some forms), and check whether the cheaper behavioral test is really blocked before settling for a source scan. The unit is the artifact, not the function: enumerate the things being built, not the places they are built in.
Receipts: docs/agent-laws-receipts.md#a-source-scanning-census-is-satisfied-by-a-comment-and-blind-to-position-2026-07-31-872

### The check and the defect AGREED with each other, so neither could catch the other (2026-08-01)

A check built from the same wrong model that produced the defect is mutually consistent with it, and that reads as green; the escape is a third thing neither controls, such as the real consumer, the seam the value crosses, the real base commit, the measurement, or the guard's actual input list. Concretely: before adding a flag to a shared builder, enumerate its consumers and ask which parse the thing it changes (`-q` in `RipgrepBackend._build_cmd` broke the three consumers that parse stdout); state in a control arm what the consumer does with the value, not what the callee accepts; build the probe at the seam the value crosses, with an `assert captured` arm so an inert capture fails; state a plan's base commit and prove it with `git rev-parse origin/main`; hand a writing seat the measurements, not just the sources; and open a guard to read which files it consumes before citing it as covering your artifact. A law that cites a number must cite the derivation, for example:

    python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import repo_map as r;print(r._symbol_navigation_descriptor())"
Receipts: docs/agent-laws-receipts.md#the-check-and-the-defect-agreed-with-each-other-so-neither-could-catch-the-other-2026-08-01

### Building ONE checker produced THREE wrong readings, and an extreme rate is the tell (2026-08-01)

An instrument that is aimed wrong yields a confident number rather than an error, and a rate of 0% or 100% is a property of the instrument far more often than of the subject. Before reporting either extreme, check what would have to be true of the world for it to be genuine; make the control fixture unambiguous (a known-good arm citing a basename that exists several times reads 0/0/0, the same as a dead checker); and aim at what CI sees by resolving against `git ls-files` rather than the filesystem, since untracked litter and stale worktrees change the answer.
Receipts: docs/agent-laws-receipts.md#building-one-checker-produced-three-wrong-readings-and-an-extreme-rate-is-the-tell-2026-08-01

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

**Whenever prose or code decides "is it safe to trust this signal?", enumerate the safe cases and reject everything else, because a deny-list in a fail-closed paragraph fails open on the first value its author did not anticipate.** This is the documentation twin of the Backend Fail-Closed Contract (for example, absence of `incomplete_reason_class` is trustworthy only on the Python `CPUBackend` route or the `rg`-backend route). When you widen what an existing flag means, grep its consumers, because a comment stating the old assumption is the tell that a downstream reader is about to be wrong.
Receipts: docs/agent-laws-receipts.md#fail-closed-guidance-must-be-an-allow-list-not-a-deny-list-2026-07-25-282

## Slice By What CI Can Actually Verify (2026-07-25, #280)

CPU-SAFE forbids compiling, so CI is the only oracle for Rust and change size is a correctness concern: ship the portion whose correctness is provable now and defer the rest as its own slice. Leave the gap at the code site, not only in the tracker, with a comment naming exactly what is still missing and why, because a partial fix with no marker at the seam reads as complete to the next reader.
Receipts: docs/agent-laws-receipts.md#slice-by-what-ci-can-actually-verify-2026-07-25-280

## Model The Class, Don't Enumerate The Cases (2026-07-25, #745/#749/#272)

When review rounds keep finding new instances of the same class, replace reviewer imagination with a model of the class, such as `.claude/rg_argv_differential_fuzz.py`, an independent model of ripgrep's argv grammar diffed against tg's parser and wired into CI's release-blocking `static-analysis` step. A modelled gate must itself be proven non-decorative (reverting one line must fail it, mutation kills, reproducible `--seed`, oracle validated against the real tool), and a cross-tool differential covers only the intersection of the two surfaces, so tg-only flags need their own invariant (a registry-parity test, a CI-coverage invariant). Prefer an invariant over an enumeration; for skill `file:line` anchors that rot continuously, `.claude/skill_anchor_audit.py` resolves every cited path, flags lines past EOF, and reports where a named symbol is actually defined. Prove a new checker tier can fire before believing a clean run, anchor symbol matching to definition sites so it does not cry wolf, and keep it a maintenance command rather than a pytest, because pinning these numbers in CI would red every PR that adds a line to `main.py`.
Receipts: docs/agent-laws-receipts.md#model-the-class-dont-enumerate-the-cases-2026-07-25-745749272

## A Field That Is `""` Instead Of `null` Defeats Your Default (2026-07-28, four instances in one session)

`gh`'s check API returns `conclusion: ""`, not `null`, while a `CheckRun` is running, so jq's `.conclusion // "PENDING"` never fires and a merge gate reports 0 pending while jobs run. For a `CheckRun`, branch on `.status == "COMPLETED"`; for a `StatusContext`, branch on `.state` (`__typename` tells them apart). Guard the total as well, since jobs register progressively and "almost nothing pending" over a partial roster is the same false green, give the probe a control run against something known to be in flight, and print the raw tally beside the verdict.
Receipts: docs/agent-laws-receipts.md#a-field-that-is-instead-of-null-defeats-your-default-2026-07-28-four-instances-in-one-session

## A Ratchet That Narrows Still Reads Green (2026-07-28)

A guard that silently starts covering less keeps reporting success (a detector requiring `Exit(2)` within 4 lines of an `if` dropped two gates whose `raise` sat 5-7 lines down, and skipping its own helper by name does not scale). Define the thing counted by behavior, not by name or proximity (a gate is a site that exits 2, wherever it lives), and assert coverage by name as well as by count, because a count floor says something vanished but never which. Where an exemption is right, name it with its reason (`inventory` discloses via `render_inventory_text`) so the next reader does not "fix" it.
Receipts: docs/agent-laws-receipts.md#a-ratchet-that-narrows-still-reads-green-2026-07-28

## A Probe That Cannot See Past Its File Reports A Delegating Caller As Silent (2026-07-28)

A source-level census that answers "does X do Y" is really answering "does X do Y in this file", so a command that delegates rendering to another module (`inventory` via `render_inventory_text` in `cli/inventory.py`) reads as having no disclosure. Before trusting such a census, ask what it would say about a delegating caller, and re-run it asking which sites delegate their rendering elsewhere.
Receipts: docs/agent-laws-receipts.md#a-probe-that-cannot-see-past-its-file-reports-a-delegating-caller-as-silent-2026-07-28

## A Disclosure Must Precede The Data It Qualifies (2026-07-27, #329)

A truncation warning goes above the payload and advisory commentary (such as the zero-callers "not dead code" caveat on a complete result) goes below it, because a trailing warning is read as a footnote and a truncated result then gets trusted as exhaustive. Three emitters follow the rule today (`_emit_symbol_command_result`, the `blast-radius` counts block, `_render_blast_radius_mermaid`); `code-map`, `route-test`, `session open` and `agent` still trail their disclosure, and `map`, `context`, `context-render`, `edit-plan`, `blast-radius-render`, `blast-radius-plan` and `scan` exit 2 or 0 with no text disclosure, so state the scope when citing this rule. When touching any disclosure surface: pin position (`out.index(marker) < out.index(first_payload_line)`, with a premise assertion that the payload was emitted) rather than presence; share one ordering via `_completeness_caveat_lines` in `cli/main.py`, which returns `(leading_banner, trailing_note)` (JSON carries `caveat` as a field and is unaffected); and enumerate every `typer.echo` or renderer that can reach stdout for the command, because a hand-written literal invites the wrong label (`note:` for a truncation) and wrong-knob advice (naming `--max-callers`/`--max-files` for a `--max-repo-files` cap), which sourcing text from `_scan_truncation_warning` prevents.
Receipts: docs/agent-laws-receipts.md#a-disclosure-must-precede-the-data-it-qualifies-2026-07-27-329

## Backend Fail-Closed Contract

Every `ComputeBackend` MUST raise `BackendExecutionError` on a real failure — never return a clean empty / `0-match` `SearchResult` (see `backends/base.py`), and never silently swap to a different engine that cannot preserve the requested semantics. The search loop catches `BackendExecutionError` to fall back **visibly** (e.g. to CPU); a swallowed failure or a silent engine swap reaches the user (or a coding agent) as a trustworthy "no matches" — the one failure a context tool cannot afford.

This contract is violated repeatedly. The recurring anti-pattern is a bare `except Exception:` that returns an empty result or falls through to a different engine. Instances fixed across audits: the Rust/PCRE2 bridge (ran `--pcre2` through the Python-regex engine), the ast-grep wrapper OOM mask (a killed subprocess read as a clean 0-match), the tree-sitter query swallow (invalid pattern → silent 0-match), and CyBERT's classify fallback (keyword-heuristic hits labeled as real model output). When a path CAN fall back to a different engine:

- **Fail closed** for any flag/contract the fallback cannot preserve (e.g. `--pcre2` through a non-PCRE2 engine): raise, do not swap.
- If a degraded fallback is legitimate (e.g. heuristic classification when the model is down), make the swap **visible**: set a `fallback_reason` (and a distinct `routing_reason`) on the `SearchResult` so JSON/CLI consumers can tell degraded output from real output. Never label heuristic output as model output.
- Validate an untrusted response shape before indexing (e.g. a model's class count vs a fixed label list) so a mismatch degrades gracefully instead of raising an uncaught `IndexError` that a broad `except` then swallows.

The same discipline applies beyond backends: any router/pipeline that can silently override an explicit user intent (e.g. an explicit `--gpu` request quietly routed to CPU) must instead raise `ConfigurationError` or emit a diagnostic. A systemic `SafeBackendMixin` + a fault-injection conformance CI gate (every registered backend must raise, not return empty, when its engine call fails) is the planned structural fix so this stops recurring one file at a time.

## AST Native/Wrapper Two-Engine Divergence (task #141)

`tg`'s AST surfaces (`tg run`, `tg scan`, the MCP `tg_ast_search` tool) can be served by two backends with two different, incompatible query DSLs: `AstGrepWrapperBackend` (`backends/ast_wrapper_backend.py`) shells out to the `ast-grep` binary and understands the full ast-grep pattern language, including metavariables (`$NAME`, `$$$ARGS`), selectors, and strictness options; `AstBackend` (`backends/ast_backend.py`) parses in-process via tree-sitter and understands only a narrow native query shape (a bare identifier, or an s-expression starting with `(`) — it has **no concept of ast-grep metavariables at all**. Given `$NAME` it cannot reproduce the wrapper's capture semantics.

This divergence is already fail-closed at four sites (grep the symbol, never a line number — these shift release to release; a regression test locks each one in, see below):

1. `Pipeline._supports_native_ast_pattern` (`core/pipeline.py`) — the shared classifier. Only a bare identifier (`re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pattern)`) or a pattern starting with `(` counts as native-shaped; anything containing `$` (or any other non-identifier character, or more than one token) returns `False`.
2. `Pipeline.__init__`'s AST branch (`core/pipeline.py`) — when `_supports_native_ast_pattern` is `False` and the ast-grep wrapper is unavailable, it raises `ConfigurationError` via `_raise_explicit_ast_configuration_error` instead of silently falling through to native tree-sitter.
3. `_select_ast_backend_for_pattern` (`cli/ast_workflows.py`, with a second copy in `cli/ast_scan.py`; the `tg run`/`tg scan` selector) — mirrors the same classification (`pattern_kind == "wrapper"`) and raises the identical `ConfigurationError` when the wrapper is required but absent.
4. `tg_ast_search` (`cli/mcp_server.py`) wraps the `Pipeline(...)` construction in `try/except ConfigurationError` and converts it to the structured `{"error": {"code": "unavailable", ...}}` JSON shape instead of letting a raw exception escape as an unhandled FastMCP `ToolError`. Note: this call site never threads `query_pattern` into the `SearchConfig` it builds, so `_supports_native_ast_pattern` is unconditionally `False` there — every `tg_ast_search` pattern (metavariable-shaped or not) requires the wrapper at this construction step; native `AstBackend` is structurally unreachable through the MCP tool regardless of the caller's pattern.

Regression coverage locking this in: `tests/unit/test_pipeline.py` (`test_supports_native_ast_pattern_should_reject_ast_grep_metavariable_syntax`, `test_should_reject_ast_grep_metavariable_pattern_when_wrapper_is_unavailable`) and `tests/unit/test_ast_workflows.py` (`test_select_ast_backend_should_reject_ast_grep_metavariable_pattern_when_wrapper_is_unavailable`) each assert `ConfigurationError` for a genuine `$NAME`/`$$$ARGS` pattern with the wrapper unavailable — even with the native backend AVAILABLE, to prove its presence never lets a metavariable pattern silently mis-route. `tests/unit/test_mcp_server_search.py` (`test_tg_ast_search_fails_closed_for_metavariable_pattern_when_wrapper_unavailable`) drives the real `Pipeline` (not a mock) through `tg_ast_search` to prove the same refusal surfaces as the structured JSON error at the MCP boundary.

**The native-shaped-pattern fallback is deliberate, not a bug.** When ast-grep is absent but a pattern IS native-shaped (a bare identifier or an s-expression), both `Pipeline` and `_select_ast_backend_for_pattern` fall through to the native `AstBackend` instead of refusing — this is intentional so a CPU-only box without the `ast-grep` binary installed still gets *some* AST search capability rather than none. Do not "fix" this into a hard refusal; that would regress a deliberately-supported capability.

**Reconciling the two DSLs (native metavariable support, or making native the CPU-perf default) is task #141 and stays demand-gated** — it is a design pass, not a small change, and only worth doing once a concrete consumer needs native-tree-sitter performance for patterns the wrapper already serves correctly. See `docs/BACKLOG.md` for current status.

## Roadmap Sequencing (2026-07-02, GPU phase structure added 2026-07-14)

The GPU native-backend program runs a 3-phase sequence gated on evidence, not on a blanket "hold until N CPU wins ship" rule.

- Phase 0 (shipped, gated OFF by default): the correctness taxonomy, the loud non-promotional CPU fallback, the `doctor`/proof fields, WSL path-domain probe bridging, doctor probe failure taxonomy, honest `--gpu-device-ids` validation, `calibrate` remediation messaging, and the loud nvidia->cpu installer downgrade warning. The native `cuda` Cargo feature compiles into release assets only when the repository variable `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` is set to `native-frontdoor-gpu`; the default (`native-frontdoor`) never builds or ships a GPU asset.
- Phase 1 (reversible flag-flip, not authorized): flipping that variable ships a GPU asset but does not promote it. No crossover is proven (GPU stays slower than `rg` / `tg_cpu` for single-pattern search at every scale from 10MB to 5GB; even the 100-pattern fixed-string lane loses to fair-baseline `rg -F -e ...`; see `docs/gpu_crossover.md`), and the dispatch-only gate `.github/workflows/public-gpu-proof.yml` has not produced a `public_gpu_proof = true` / `public_managed_promotion_ready = true` verdict (requirements pinned in [docs/CONTRACTS.md](docs/CONTRACTS.md), "Public managed GPU promotion"). Do not flip the variable to promote GPU as a default route until that gate passes. The shipped `gpu_text_search_positions` kernel is a position-parallel brute-force byte-compare, not a PFAC/Aho-Corasick automaton (PFAC is documented future work), so do not describe it as PFAC. Public CUDA-asset publishing is on a deliberate CEO HOLD, and release checksums ship 3 CPU-only rows; do not re-propose "just publish the GPU asset" without re-reading this verdict.
- Phase 2 (self-hosted GPU CI runner, CEO-gated): proving the crossover at 1GB/5GB in CI needs a self-hosted GPU runner wired into `public-gpu-proof.yml`, a recurring infra cost and access-control surface, so provisioning is a CEO decision, not an engineering-capacity one.

The original CPU-only "3 wins before GPU advances" gate is superseded by this structure. Its first win shipped: local hybrid semantic search (BM25 + CPU dense embeddings fused with RRF, no API key, no GPU) as `tg search --semantic` (`retrieval_dense.py` + `retrieval_fusion.py`, default-OFF, gated on the `semantic` extra; see the `tensor-grep-semantic-search-campaign` skill), modeled on MinishLab `Semble` (tree-sitter chunking + `potion-code-16M` Model2Vec + BM25 + RRF). The other two items, `tg registration-check` as a first-class command and a Bloom-filter n-gram chunk prefilter for the slow non-literal-regex full-scan path in `rust_core`, remain live backlog independent of GPU phase gating.

Rationale: the project's docs place raw search speed (where GPU competes) in the parity tier, not the moat, and the heuristic auto-GPU route is effectively dead code whenever ripgrep is installed. The moat is the agent-native context layer (`orient` / `callers` / blast-radius / the token-efficient capsule), so engineering capacity funds that first. Explicit `--gpu-device-ids` stays supported and **must fail loud when it cannot be honored, because a silent fallback would misreport the engine (see the Backend Fail-Closed Contract)**.
Receipts: docs/agent-laws-receipts.md#roadmap-sequencing-2026-07-02-gpu-phase-structure-added-2026-07-14

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
  - `supply-chain-hardening` — before writing any download / extract / install / self-upgrade / toolchain-bootstrap code, apply its checks (the skill and "CI / Release Rules" (a)–(h) below carry the current list). Shipped patterns: #283/#284/#285/#287.
  - `worktree-fanout-verification-gate` — before integrating agent branches from a worktree fan-out: remove worktrees before checkout (`git worktree remove --force <path>` — else checkout is blocked and tests silently run main's code); re-run pytest/ruff/mypy in the real venv (worktrees have no `.venv`; agents' "tests pass" claims are hypotheses until then); run `ruff format --preview` on ALL agent-touched files (not only hand-fixed ones); and treat scoped-local-green as a hypothesis, not a merge signal.
  - `anti-hang-test-protocol` — hang-class test hygiene: wrap every test run in a shell timeout, and write the fix BEFORE the red-phase adversarial test (a ReDoS/deadlock red-test executed against un-fixed code IS the hang it is testing).
  - `instrumented-build-gate` — measure real demand before building a speculative feature.
  - `agent-liveness-probe` — before killing, restarting, or `TaskStop`-ing a background subagent that looks stalled, probe liveness via `SendMessage` rather than trusting output-file mtime/size (see A9 above).
  - `profile-guided-byte-identical-optimization` — find a lever on the shipped wheel + prove output
    byte-identical; the warm/cold measurement trap (see "Optimization Discipline" above).
  - `detect-the-false-green` — before trusting a passing suite, a zero-match grep, a clean gate run, or a count that confirms your prediction.
  - `author-a-probe-that-cannot-lie` — before writing any script whose number you will act on (positive control, blind-vs-busy empties, arm interleaving, shared-box pollution).
  (the global-skill half of this list is manually maintained — no CI gate — diff it by hand against `CLAUDE.md`'s copy.)
- **Carrying the project forward -- the in-repo skill library** (`.claude/skills/tensor-grep-*` + `code-search-and-retrieval-reference`, **37 skills**): the onboarding handbook so a new engineer or an autonomous agent session can debug, extend, validate, and advance `tg` without the original authors. Each auto-loads by its `description`; load the one matching your task. Index by intent -- this exact bucket list is kept byte-identical with `CLAUDE.md`'s skill index; `tests/unit/test_skill_index_sync.py` fails if either doc drifts from the real `.claude/skills/` folder set, and `tests/unit/test_skill_library_drift.py` additionally pins every `file:line` citation (must resolve to a git-tracked file, line in range) and the stated `**N skills**` count against the folders that sentence names. **Neither gate can tell you a skill is CORRECT** — they prove a citation resolves, not that the cited line still contains the claimed symbol. Anchors drift 14-500 lines while resolving perfectly; run `/tg-skill-audit` (`.claude/workflows/tg-skill-audit.js`) for that half, and never fix drift by re-stamping a new line number (see "Cite the SYMBOL, not the line" above):
  - **Change safely:** `tensor-grep-change-control` (the gates), `tensor-grep-debugging-playbook`, `tensor-grep-failure-archaeology` (don't re-fight settled battles), `tensor-grep-validation-and-qa`, `tensor-grep-hermetic-hostile-tests` (env-independent gated tests + hostile fixtures that must BITE), `tensor-grep-cross-platform-path-confinement` (junction vs symlink vs drive-absolute confinement, Windows+POSIX), `tensor-grep-release-drift-check` (post-release sweep: version stamps, derived counts, known-state facts vs the current tag, SUPERSEDED append-only fix discipline), `tensor-grep-local-ci-parity-harness` (run the shared-box-banned lanes in a CPU-capped container; the measured container-vs-runner divergences; act vs a hand-written harness).
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
8. ADVERSARIAL AUDIT (3 lenses + chairman) — run a citation-enforced adversarial audit of the integrated diff; this is a mandatory stage distinct from the pre-build planning council (the post-build audit caught a HIGH CUDA-fork hazard that 203 passing tests missed). A finding with no `file:line` citation is discarded. Re-audit → fix-wave → re-audit until ZERO must-fix findings remain. The endpoint is a PR that the orchestrator self-merges once verified green, independently reviewed, and push-race clear (change-control Part 1 §1).
9. Get a bounded, read-only independent review of each PR diff before merge from a different model family than the builder (e.g. Gemini or codex); treat its findings as hypotheses until checked against local files and tests. A no-verdict seat is a failed seat, not approval.
10. Push each branch, wait for PR CI, squash-merge intentionally, then watch main CI. Release-bearing work is not complete until semantic-release, assets, PyPI, and public release dogfood pass.

Maintain a per-slice evidence ledger in each slice's PR description (`CHANGELOG.md` and GitHub release notes carry the release record; the inline ledger this file used to hold drifted out of date and was retired). Each slice entry must record PR order, slice scope, Exa research anchors, thinktank or planning consensus, subagent ownership, independent review result (Gemini or another model family), validation commands, PR CI, and main CI. Optional or triggered items may be marked `not applicable` only with a rationale. For release-bearing slices, additionally require semantic-release, release assets, PyPI, and public release dogfood evidence.

## Required Local Validation

Run these before push for normal code changes. Locally, run the targeted suites for the areas you touched plus ruff, mypy and the ratchets; the full `pytest -q` runs in CI or in the CPU-capped `scripts/ci-local` container, never as a local pre-push step on this shared box (A12):

```powershell
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q <the tests/unit files for the areas you touched>
uv run python scripts/file_size_budget.py --report
uv run python scripts/bare_call_ratchet.py --report
```

CI runs `ruff format --check --preview .`. Running only `uv run ruff check .` is not enough to prove formatter parity, and running `ruff format` WITHOUT `--preview` actively REVERTS preview-style formatting on disk — a "clean" bare `ruff format` will undo CI-mandated style and red the next `ruff format --check --preview` run even when local lint passes. Always pass `--preview` to `ruff format` locally; never pass it to `ruff check`. The trailing `.` (whole repo) is load-bearing too: under `--preview`, ruff formats Python code fences INSIDE Markdown, so a scoped run (`ruff format --check --preview src/tensor_grep tests`) passes locally yet MISSES an unformatted `docs/**/*.md` snippet — which reds CI's release-gating `static-analysis` job and blocked v1.67.0. Always run the whole-repo `.` form; never a `src`/`tests` subset.

A full `uv run pytest -q` can take substantially longer than 70-90 seconds on this Windows machine when the full JS/TS and e2e surface is hot; use a timeout of at least 120 seconds for narrow suites, and give the full suite a much larger timeout in CI or the `scripts/ci-local` container.

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

Read the step conclusions before believing a run's red conclusion: a genuine test failure records `Run Pytest: failure`, while an empty conclusion on every step after a successful one means the job was killed and nothing was measured about the code. Print every step with its conclusion (`gh run view <id> --json jobs`) rather than filtering, because a `conclusion not in ("success","skipped",None)` filter lets empty strings through and reports not-run steps as failures.

Discharge such a red by measurement, not plausibility: re-check the same job on the commit that supersedes it, and if that job passes on a superset tree the earlier red carried no information, because a correctly diagnosed flake still holds its authority to block until a control clears it. Also check the raw byte count of `gh run view <id> --log-failed` before interpreting a filtered result, because an expired or still-running run prints only `log not found: <job-id>` and a grep over it returns empty; every zero needs a control proving the probe can return non-zero.
Receipts: docs/agent-laws-receipts.md#a-red-run-with-no-failing-step-is-an-interrupted-run-2026-07-27-339

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
(e) uv's `.ps1` installer LACKS binary checksum verification (uv issue #13074) while the `.sh` self-verifies (uv >=0.11.0, pinned 0.11.25); Windows fix = download the pinned uv RELEASE BINARY + verify a COMMITTED dual-arch (x86_64 + aarch64) SHA-256 fail-closed before use (implemented in `scripts/install.ps1` + `scripts/uv_checksums.json`); discipline: ALWAYS download + `Get-FileHash` to CONFIRM a committed SHA — never trust an agent's "fetched from the sidecar" value.
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

Before building from a filed task description, read the current source (`git cat-file blob origin/main:<path>`, not the drifting local checkout) and confirm the defect still exists, because task text is a snapshot of someone's belief; `git log -S"<the exact claim>"` finds the commit that closed it.

Every contract statement needs a governance test pinned to the source, not to the doc, because a test that only greps the doc for its own words is circular and passes forever after the code stops behaving that way. Assert a premise about the code (the producer still emits the field) alongside the claim about the prose, so both arms can fail.
Receipts: docs/agent-laws-receipts.md#check-whether-it-already-shipped-and-pin-what-you-document-2026-07-27-328333

## Your Reading Is A Hypothesis; The Mechanical Check Is The Oracle (2026-07-27, #316/#307)

Write the check so its result does not depend on your prior, because prose looks like code and a human-shaped read agrees with whatever you already believed. A semantic read is not a typecheck (for example `Result::expect_err` needs `T: Debug`, which a return-type change can remove), so CI red is sufficient and CI green is not.

A coarse grep counts prose as code: match the structural form (`"field"`, `def name(`, `fn name(`) and read each hit before concluding. A census expectation can also be wrong in both directions, so when a census disagrees with you check the breakdown before filing, because the finding is as often in the expectation as in the tree.
Receipts: docs/agent-laws-receipts.md#your-reading-is-a-hypothesis-the-mechanical-check-is-the-oracle-2026-07-27-316307

## Push Discipline

Do not push from a dirty worktree if `origin/main` moved and the local tree has unrelated changes.

A branch push or open PR starts PR CI only. It is not a release, not a released version, and not complete release state. Release versioning starts only after a release-bearing PR is squash-merged to `main`, because semantic-release reads the final `main` commit subject.

**The merge rule (one rule, two halves):** When no release-bearing `main` run exists, merge every green PR in one burst; then merge nothing until that run's `chore(release)` commit and PyPI publish land. The window is the whole run from creation to the release push, not the job's current state — a pending/jobs=0 run still pushes last. Concurrent squash-merges to `main` can race at the semantic-release step and produce a skipped release or a wrong version bump. `chore:` / `docs:` / `test:` titles do not bump the version — but that is NOT a licence to merge them while a prior release is in flight (see the push-race note directly below). "Safe to interleave" means *after the in-flight release has fully published* (its `chore(release): vX` commit is on `main` and PyPI shows the new version), not merely after its PR CI is green. If the run completes without publishing (red, or semantic-release made no release), the window closes at completion; A32 governs the hotfix.

**Decide whether a run is release-bearing by the commit range since the last tag, not by `release-intent`
(2026-07-27, corrected by A33).** The push-race bites a merge that lands *while a RELEASE job is
pushing*, and whether one is in flight is checkable. A run is release-bearing iff `git log --format='%s' <last-tag>..<run headSha> | grep -E '^(fix|feat|perf)'` is non-empty (releases are cumulative). `release-intent` is a PR-only title validator
and is always skipped on main pushes, so its state there proves nothing. On main,
`fix:`/`feat:`/`perf:` commits release; `docs:`/`test:`/`chore:`/`ci:`/`build:`/`bench:` do not, and
`refactor:` passes the title gate but does not publish under the default angular parser. Non-releasing
PRs join a burst when no release-bearing run exists and wait like everything else while one does;
`gh run view <id> --json jobs` shows whether the `Semantic Release` job is running.

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

Use this schema (title gate: `scripts/validate_pr_title_semver.py::_RELEASE_INTENTS`; publisher: `[tool.semantic_release]` in `pyproject.toml`, default angular parser):

- `feat: ...` => minor release
- `fix: ...` or `perf: ...` => patch release
- `feat!: ...` or `fix!: ...` => major release
- `refactor: ...` => accepted by the title gate as patch intent, but does NOT publish
- `docs: ...`, `test: ...`, `chore: ...`, `ci: ...`, `build: ...`, `bench: ...` => no release

Release-bearing PRs use `Squash and merge`. Semantic-release parses the commit subject on `main`: GitHub uses the PR title only for multi-commit PRs, so on a single-commit PR fix the commit subject (or add a commit) — retitling alone is a no-op.

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
- **cargo/rustc are off `PATH` here, and CPU-SAFE (A12) forbids local `cargo`/`rustc`/`clippy`/`maturin` builds on this shared box** — CI is the Rust oracle; only `rustfmt --check` runs locally. On a non-shared machine (`C:/Users/oimir/.cargo/bin/cargo.exe`, or prepend `~/.cargo/bin` to `PATH`), a "hanging" Rust build is usually slow LTO that *completes* (`maturin develop` ~15 s, a `--release` build minutes); do not kill it as hung. (The build command for stale in-tree binaries is under the doctor note above.)
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
- **Baseline/control arms:** `tests/conftest.py`'s `sys.path.insert` overrides `PYTHONPATH`; see "Required Local Validation" above for the mechanism and the second-checkout remedy.
- **Enumerate mechanically, never from recollection — this recurred THREE times in one session (2026-07-25).** A commit-count stated from memory was 6; `git rev-list` said 8. A path probed from a remembered name (`.pytest_tmp_review_472dffd9`) was a truncation of the real one and `Test-Path` returned false for a path that never existed, briefly "closing" a live task. A worktree-husk count estimated at 11 was 12 when derived from `git worktree list --porcelain`. In every case the mechanical derivation was one command away. If you are about to state a count, a filename, or a site list — derive it.
- **Windows symlink creation needs privilege.** Tests that create symlinks must `pytest.skip` on `OSError` / `NotImplementedError`, or they false-fail on an unprivileged run.
- **A stray `nul` file in the tree is a Windows `2>nul` redirect artifact.** Use `2>$null` (PowerShell) or `2>/dev/null` (bash); clean up with `rm -f ./nul`.
- **CRLF makes a local bare `ruff format --check` false-alarm** over LF-committed blobs. Run `ruff format --preview <files>` (which normalizes) before commit — see "Required Local Validation" for why `--preview` is mandatory and must never be passed to `ruff check`.
- **The full local gate is four steps, not two — and re-run them after your LAST edit.** `ruff check` + `pytest` passing is NOT green: the CI "Formatting & Linting" job also runs `ruff format --check --preview .` (a *formatter*, distinct from the `ruff check` *linter* — a post-edit line-wrap or over-long comment passes `ruff check` but fails `ruff format --check`) AND `mypy src/tensor_grep` (catches type errors nothing else flags, e.g. assigning to a `Final` attribute like click's `UsageError.message` — mutate it and mypy errors; raise a fresh `UsageError(...)` instead). Running only `ruff check` + `pytest` — or running the gate before an *intermediate* edit that a later edit then invalidates — cost two drain-blocking CI failures in a single session (a mypy `Final`-assign and a `ruff format` line-wrap). Run all four (`ruff check` · `ruff format --check --preview` · `mypy src/tensor_grep` · targeted `pytest`) AFTER the final edit; the full suite is CI's job.
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

A probe that could not run yet looks the same on screen as one that ran and found nothing, so before believing a negative, prove on this input, at this moment, that the instrument can return non-zero. Observed false negatives: an `awk` range that never matched, `pytest --collect-only | grep -c` after `-x` aborted collection, `gh run view --log` on an in-progress run, and `uvx --from tensor-grep==X` against a stale uv index cache.

- `--refresh` is not always enough for a package index: `uv cache clean <pkg>` is, and the discriminator between "release failed" and "cache stale" is the PyPI files endpoint (`/pypi/<pkg>/<version>/json` -> `urls[]`), because a version string can update before an index serves it.
- An `in_progress` run makes its logs unavailable even when a job inside it has concluded, so query the job's `conclusion` and failing step name, which are available, instead of reading an empty log as clean.
Receipts: docs/agent-laws-receipts.md#a-blocked-instrument-and-a-definitive-negative-look-identical-2026-08-01-883-887

## Two Different Audits Beat More Seats Of The Same Shape (2026-08-01)

Consensus is not coverage: seats of the same shape share blind spots no matter how many providers they span, and a single differently-shaped audit (for example `codex gpt-5.6-sol`) can catch defects a multi-provider council all missed. Escalate breadth when the question is "what is wrong with this?" and collapse to direct verification when the question is "is this specific claim true?". Direct verification is only as good as its probe, so give each check a positive control.
Receipts: docs/agent-laws-receipts.md#two-different-audits-beat-more-seats-of-the-same-shape-2026-08-01

## Release Class Is Part Of The Fix (2026-08-01, #883)

Before merging, read what the PR title does to the release in `scripts/validate_pr_title_semver.py` rather than recalling it, because a security fix in a `chore:` PR maps to `"none"`, merges, and never publishes while the tracker says shipped. `fix`/`feat`/`perf` publish; `chore`/`docs`/`test`/`ci`/`build`/`refactor`/`bench` do not (see the later `refactor` law for the two-authority detail).
Receipts: docs/agent-laws-receipts.md#release-class-is-part-of-the-fix-2026-08-01-883

## Separate ROUTING From EVALUATION Before Asserting End-To-End (2026-08-01, #884)

Split an end-to-end assertion into routing (the front door forwards the flag instead of clap-rejecting it, needs only the binary) and evaluation (the sidecar can answer, needs the PyO3 extension), because `native-build-smoke` runs `cargo build --bin tg` and never builds the extension. Assert routing unconditionally and evaluation only where the engine exists; the routing-only arm still discriminates because a fail-closed refusal is textually impossible for a clap rejection to produce. Measure against the published wheel first before relaxing any assertion, so a real defect is not defined away.
Receipts: docs/agent-laws-receipts.md#separate-routing-from-evaluation-before-asserting-end-to-end-2026-08-01-884

## A Job That Cannot Reach A Surface Makes That Surface Invisible (2026-08-01, #884)

When adding a test that crosses a boundary, check that some CI job can execute both sides, because `test-python` has the Python deps but no release binary while `native-build-smoke` has the binary but only `pytest`, so native-to-sidecar delegation was untestable end to end and a test in the wrong job skips silently green. Derive a job's dependencies from `pyproject.toml` rather than hand-listing them, because a hardcoded list rots.
Receipts: docs/agent-laws-receipts.md#a-job-that-cannot-reach-a-surface-makes-that-surface-invisible-2026-08-01-884

## Harden A Rule Only When It Is Mechanically Detectable AND Rarely Wrong (2026-08-01, #886)

Add a gate only when a violation is detectable without interpretation and a false positive would be rare; when only one holds, record the retirement with its measurement so the next session does not re-derive it, because an over-eager gate teaches people to reach for `--no-verify`, which discredits every honest gate. Example: a tolerance on the `docs/TASK_BOARD.md` reconcile stamp (>5 releases behind fails) is deterministic and shipped, while a citation gate over board items was retired because a citation proves only that a reference resolves, not that the defect exists.
Receipts: docs/agent-laws-receipts.md#harden-a-rule-only-when-it-is-mechanically-detectable-and-rarely-wrong-2026-08-01-886

## I Reproduced An Error A SKILL Explicitly Warned About, By Writing Prose From Memory (2026-08-02)

Ask which artifact actually performs the action, and print the authority next to the belief. Two authorities govern `refactor:` and they disagree: `scripts/validate_pr_title_semver.py::_RELEASE_INTENTS` maps `refactor`->`patch` but only gates what the PR title may be and publishes nothing, while `[tool.semantic_release]` in `pyproject.toml` is the publisher and configures no `commit_parser`/`allowed_tags`/`patch_tags`, so the default angular parser applies and only `fix` and `perf` patch. A `refactor:` merge therefore makes no release (it ships with the next `fix:`/`feat:`), and a refactor-only run leaves `main` unpublished while trackers read "shipped".

Check both with `grep -A12 _RELEASE_INTENTS scripts/validate_pr_title_semver.py` (what the title gate accepts) and the `[tool.semantic_release]` block (what ships); the decisive check is the Semantic Release job log for the merge, because a warning in a skill is not a guard and a faithful derivation from a file that does not do the thing is still wrong.
Receipts: docs/agent-laws-receipts.md#i-reproduced-an-error-a-skill-explicitly-warned-about-by-writing-prose-from-memory-2026-08-02

## Three Independent Gates Found Four Defects I Did Not (2026-08-02, #904)

The mandatory adversarial gate finds defects that self-verification misses, so do not rely on your own "zero regressions" report. Test a bound where it can fail: a parity fixture too small to reach the ceiling (a 32-file fixture against a 2000-file ceiling) exercises the one population where the bound is a no-op. When a change adds counters across several call sites, instrument every site (here `_context_tests` has two) and assert the accumulation invariant with coverage that fails on a one-character revert (`+=`).

A gate's suggested remedy is also a hypothesis: naive accumulation can report `scanned=12` against `total=8`, while accumulating both keeps `scanned <= total` an invariant. Re-derive a blocking finding yourself before accepting it, and re-gate after fixing, because "I fixed the gate's findings" is the self-report a gate exists to distrust. Do not add an assertion that is true in both arms; it checks nothing.
Receipts: docs/agent-laws-receipts.md#three-independent-gates-found-four-defects-i-did-not-2026-08-02-904

## A Board Can Be Perfectly FRESH And Structurally WRONG (2026-08-02, #909/#910)

`test_task_board_freshness` checks the reconcile stamp's recency, never the document's structure, so deleting a section header from `docs/TASK_BOARD.md` silently refiles its items under the preceding section and can make blocked work look actionable. Print counts beside their expected values in status updates, because a mismatch (for example `UNSHIPPED ARTIFACTS = 4` where 1 was expected) is what exposes a swallowed header. Fix the cause upstream: anchor a string replacement on the text being replaced, not on a scan for the next sibling (`s.find("\n- [", i)` ends at the next item and a header between items falls inside that span); no gate exists because the violated property is diff-level and a test reads the file, not the intent.
Receipts: docs/agent-laws-receipts.md#a-board-can-be-perfectly-fresh-and-structurally-wrong-2026-08-02-909910

## `ast.walk` Inside `ast.walk` Counts Every Call Once Per Enclosing Scope (2026-08-02)

Walk the AST once and filter, because `for fn in ast.walk(tree) for n in ast.walk(fn)` re-counts each call once per scope containing it (reported 10 call sites where the truth was 2). An AST probe is code and needs the scrutiny of the thing it certifies, more so when it is the last check before a release claim.
Receipts: docs/agent-laws-receipts.md#astwalk-inside-astwalk-counts-every-call-once-per-enclosing-scope-2026-08-02

## Scoping The PATCH SITE Does Not Scope The OBSERVABLE (2026-08-02, #904)

A uniquely-called patch site does not give a uniquely-caused observable: when a test patches a clock (global), advancing it anywhere trips the next downstream check, which sets shared booleans (`partial` / `deadline_exceeded`) that any of the module's `time.monotonic` readers can set. Enumerate the writers of the value you assert, not just the callers of the function you patch, and confirm the function under test actually accepts the parameter the test claims to exercise.

A shared boolean cannot attribute a cause, so make the fix expose an attribution (`test_candidates_scanned`/`_total`) rather than writing a cleverer test. Revert a scoped rig that still passes both arms, and keep the finding, because it adds code without a check and reads as "fixed".
Receipts: docs/agent-laws-receipts.md#scoping-the-patch-site-does-not-scope-the-observable-2026-08-02-904

## A Missing OPTIONAL Dep Makes A Suite Misleading In BOTH Directions (2026-08-02, #905)

Gate the whole module on the optional dependency (for example `tree_sitter`), not just the loud failures, because without a parser some tests fail with messages that read like product bugs while others pass vacuously (an assertion that a parse did NOT happen cannot distinguish an early exit from an absent grammar). Prove the gate is a condition rather than a blanket skip (gated on `tree_sitter` skips, gated on `json` does not fire).

Dogfood the real CLI before calling a silent-empty helper a product defect: `_js_ts_references_and_calls` returns `[], []` without a parser, yet `tg refs` on a `.js` file in the same venv still finds the references via another route.
Receipts: docs/agent-laws-receipts.md#a-missing-optional-dep-makes-a-suite-misleading-in-both-directions-2026-08-02-905

## A Population Floor Must Be Calibrated On The SAME Population (2026-08-02)

Name the population when writing a threshold ("39 jobs in ci.yml", never a bare "40"), because a floor taken from 48 checks in a PR rollup (all workflows) false-fired when applied to the 39 jobs of one `ci.yml` run. The rollup count and a run's job count are different measurements, and `gh run rerun --failed` legitimately produces fewer jobs (failed plus dependents), so a floor calibrated on a full run false-alarms on every rerun.
Receipts: docs/agent-laws-receipts.md#a-population-floor-must-be-calibrated-on-the-same-population-2026-08-02

## A List Written At DISPATCH Time Is Stale By DEFINITION (2026-08-02)

Derive the set at use time, never at authoring time: run `gh pr list --state open` on every pass instead of baking PR numbers into a cron, eligibility scan, or merge drain, because PRs opened afterward are orphaned with nothing watching them. Accumulate multi-line entries before matching, because a line-based filter truncates the item it is judging (a CEO-GATED marker on line 3 was missed). If a loop can enumerate, it must not be handed a list.
Receipts: docs/agent-laws-receipts.md#a-list-written-at-dispatch-time-is-stale-by-definition-2026-08-02

## A Constraint's REASON Defines Its Scope, Not Its Wording (2026-08-02)

State a rule's reason and check that it holds in the new case before applying it. The WIP cap on open PRs exists because release-bearing PRs drain as one burst per release run and any merge inside that run's window rejects the release push, so it does not apply to non-releasing `docs:`/`test:` PRs, which batch freely. A constraint on one class is not a constraint on its neighbour.
Receipts: docs/agent-laws-receipts.md#a-constraints-reason-defines-its-scope-not-its-wording-2026-08-02

## Briefing A MECHANISM Is Asserting A HYPOTHESIS (2026-08-02)

Brief a subagent with the symptom and the evidence and require it to re-derive the mechanism, because a brief that states a mechanism as fact invites a wrong fix, and the PR body then ships the wrong explanation as the project record. Grep counts must be tracked-only (gitignored `.tensor-grep/checkpoints/` snapshots inflate them), and sibling-imitation claims and trigger claims need checking against the code. When corrected, fix the artifact (PR body, plan, doc), not just the next message.
Receipts: docs/agent-laws-receipts.md#briefing-a-mechanism-is-asserting-a-hypothesis-2026-08-02

## After A Fix, A Grep Hit Is Often The Fix's OWN DOCUMENTATION (2026-08-02)

When verifying success, count AST nodes (`ast.Name` / `ast.Attribute`), never string containment over a region that also contains prose, because a docstring explaining the fixed trap still matches (`ast.unparse` includes docstrings and produced a false `REGRESSION` verdict on a correct wheel). A verification probe is code and deserves at least the scrutiny of the code it verifies.

```python
refs = [
    n
    for n in ast.walk(fn)
    if (isinstance(n, ast.Name) and n.id == TARGET)
    or (isinstance(n, ast.Attribute) and n.attr == TARGET)
]
```
Receipts: docs/agent-laws-receipts.md#after-a-fix-a-grep-hit-is-often-the-fixs-own-documentation-2026-08-02

## `git stash` Is UNSAFE Once Parallel Worktrees Exist (2026-08-02)

Never `git stash` while another worktree is live, because git worktrees share `.git`'s stash refs, so a `git stash` / `git stash pop` red-arm revert can pop a different agent's stash. For a red-arm revert use `git checkout -- <file>` against a known commit, or a patch file. To rescue an orphaned stash non-destructively, run `git branch <rescue-name> stash@{0}`, which creates a permanent ref without checking out or popping.
Receipts: docs/agent-laws-receipts.md#git-stash-is-unsafe-once-parallel-worktrees-exist-2026-08-02

## Committed Is Not Shipped (2026-08-02)

A subagent report of "committed, not pushed, per instructions" is an obligation, not a completion: land the artifact in the same turn you consume its findings, because otherwise the work exists nowhere anyone else can reach. Derive the eligible-item list rather than trusting memory, and reconcile the board at completion, not "next cycle", because stale entries accumulate one deferral at a time and can send a dispatched agent after finished work.
Receipts: docs/agent-laws-receipts.md#committed-is-not-shipped-2026-08-02

## Seven Instruments, One Empty Queue, And A Release That Reddened Every Open PR (2026-08-04)

Most wrong verdicts come from the instrument, not the subject. Rules:

- A sentinel is not a threshold: before tuning a knob, confirm the failing value is a measurement and not a sentinel (`_releases_behind` in `tests/unit/test_task_board_freshness.py` returned tolerance+1 on any major.minor mismatch, so no tolerance value could fix it; the fix is ordinal distance in CHANGELOG.md, which semantic-release rewrites in the same commit as the version stamp).
- Grep for a guard's purpose, not one spelling of it: a zero cannot separate ABSENT from PRESENT-IN-ANOTHER-SHAPE (`_policy_file_arg` returns `f"./{relative}"` for dash-led names, neutralizing flag injection without `--`; re-derive with `grep -n 'f"./' src/tensor_grep/cli/apply_policy.py`), and a hit can be the fix's leftover name or docstring. Check behaviour and count AST nodes, never substrings.
- A merge/settle gate must require the heavy lanes to be present by name or count, never "nothing pending", because a `needs:`-gated job (for example `needs: smoke`) has no check-run at all until its gate completes: it is absent, not pending.
- After any release lands, every open PR's green is stale because per-PR CI cannot see a release published after its last run (Form 10 with time as the second slice). Check CHANGELOG.md head against the run's timestamp, re-run or rebase before merging, and read the failure count, not the red-row count (seven red lanes can be one gate).
- Dogfood through the adapter the product uses: install the same extras, import the module that performs registration, call the exact signature the product calls, and assert the optional deps are present first, or the zero you measure is your environment. A fixture must not carry the property under test in its own input.
- Premise-check the queue: a plan written against an already-fixed defect has perfectly resolving citations, so only reproducing the defect catches it (see "Check whether it already shipped").
- A branch's status oracle is its PR state (`gh pr list --head <branch> --state all`), never `git merge-base --is-ancestor` or `--merged`, because a squash-merged branch is never an ancestor of main; `--is-ancestor` is safe only as a pruning proof (A30). Skip any worktree with uncommitted files even when its PR is merged.
- Never edit a worktree a live agent owns and never `git add -A` in a shared tree; a "completed" notification is not proof the agent stopped writing, so probe file mtimes first, because committing a concurrently rewritten file produced a module using undefined constants.
- Correct the artifact chain (doc, PR title and body, memory file) when a claim is falsified, and never rewrite a dated receipt in place: append a SUPERSEDED entry (live chain: `tensor-grep-enterprise-agent`'s language-coverage row).
Receipts: docs/agent-laws-receipts.md#seven-instruments-one-empty-queue-and-a-release-that-reddened-every-open-pr-2026-08-04

## Session Lessons (2026-08-07, campaign continuation)

Full detail is in `docs/SESSION_HANDOFF.md` "Session Lessons (2026-08-07)".

1. Never copy working-tree files from a stale local checkout into a worktree branch, because it silently reverts merged changes; apply the delta onto a fresh `origin/main` worktree, or run `git diff origin/main -- <file>` first.
2. `gh pr merge --delete-branch` can abort locally on a dirty tree while the remote merge succeeds; judge by `gh pr view <n> --json mergedAt`, never by the command's exit, and in a dirty shared tree merge without `--delete-branch`.
3. A red main run that skips Semantic Release is recoverable by the next main push; diagnose reds by the failing job's per-probe summary and whether the same job passed on the PR CI before calling it environmental.
4. A ratchet test firing is a positive signal, not a product regression: fix the class and lower the ratchet's recorded count in the same PR (its failure message says so).
5. CI's `ruff format --check --preview .` formats Python code fences inside Markdown, so preview-format any committed Markdown containing code fences, or `Formatting & Linting` reds while scoped `.py` checks pass.
6. Tight byte/token envelope tests are platform-fragile (local tmp_path length vs CI); re-pin with a documented margin plus substance asserts when legitimate field growth tips one.
Receipts: docs/agent-laws-receipts.md#session-lessons-2026-08-07-campaign-continuation

## CI Cost Discipline (2026-08-07, from a real account-cutoff incident)

Look up the cost deliberately before every workflow change, because you cannot see the cost at the moment you cause it (`macos-latest` is a ~10x rate, 3 OS x 2 Python is six billed runners, and a private repo bills every minute). Pitfalls:

1. Do not add `paths-ignore` to a required check: branch protection never sees the run and docs PRs become unmergeable. Keep the job; skip the steps.
2. A local Docker run is pre-flight, not the gate: the CI run is the merge arbiter, because a local green proves your machine, not the commit.
3. A rule written in CLAUDE.md/AGENTS.md that nothing enforces is a comment; use hooks and CI.
4. Measure first and fix the repos that are the bill, not all of them.

Sampling-window trap: recent runs are the window an incident corrupts (billing-blocked runs never ran and showed a false `$0.00`; a "cron every 80 seconds" was queued schedule events replaying), so prove your probe can return non-zero on a known case and sample from before the incident. Order of controls: cap it, fix the structure, write the skill, optionally the rules, because the cap is the only control that survives every other control failing; spend caps and budget alerts belong in the repo/pipeline, not only in prose.

Enforced mechanism: a cheap `changes` job detects whether a PR's diff touches code (`src/`, `rust_core/`, `tests/`, `.github/workflows/`, `pyproject.toml`, `Cargo.toml`, `Cargo.lock`, `uv.lock`), and expensive cross-platform jobs gate on `if: github.event_name != 'pull_request' || needs.changes.outputs.code == 'true'` with `needs: [smoke, changes]`.
1. Gate on PR only: `release` `needs:`s every gating job and a skipped dependency skips a dependent unless it uses `always()`, so gating on `push` silently loses the publish; main pushes always run the full matrix.
2. A job skipped by an `if:` counts as success for branch protection, whereas `paths-ignore` on the trigger gives no status and deadlocks merges, so a job-level `if:` skip is the only safe cost lever.

Update validator-backed pins that asserted the literal old shape (`needs: smoke`) to assert substance in the same change, and treat a council's "these tests survive it" as a hypothesis until the tests run.
Receipts: docs/agent-laws-receipts.md#ci-cost-discipline-2026-08-07-from-a-real-account-cutoff-incident

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

In a heavily pre-hardened repo, budget verification effort on the assumption that your measurement is wrong before the code is, because reading code confirms what the code says and instrument failures were caught only by controls, never by re-reading.
Receipts: docs/agent-laws-receipts.md#the-instrument-fails-more-than-the-subject-12-vs-5-in-one-audit-2026-08-19

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

State a measurement tool's direction of error in its docstring, because it keeps a wrong number safe: `scripts/measure_split_floor.py` declares its result a lower bound (over the limit is decisive, under it is encouraging, not a guarantee), and it originally omitted the patched functions themselves (`monkeypatch.setattr(mod, "f", ...)` forces `f` to be defined in `mod`), reporting a floor of 1,190 against a real 1,527. Dispatched agents must re-derive from the code rather than trust the brief and report a mismatch as a finding.
Receipts: docs/agent-laws-receipts.md#a-tool-honest-about-its-direction-of-error-stays-useful-when-it-is-wrong-2026-08-19

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

Ask what a passing gate does not cover, because each of these checks works as designed and still passes a neighbouring defect:

| the gate | what it catches | what walks past it |
|---|---|---|
| `test_skill_library_drift` | a citation pointing past the end of a file | a citation that still resolves and now points at unrelated code |
| a PR's own CI | that branch against its own base | a conflict with a second PR that is also green |
| `for i in 1 2 3; do ... done` | a transient failure on attempts 1-2 | exhaustion: the loop exits 0 on its last iteration |
| `file_size_budget` | growth of an allowlisted file | growth re-pinned in the same commit |
| a 30s wall-clock bound | a drain regression | PowerShell startup, the thing actually measured |
| `gh pr checks` | a job that ran and failed | a job that never instantiated (`${{ matrix.os }}` unexpanded) |

After shrinking or splitting a file, grep every citation into it, because the drift gate's silence covers exactly the citations it cannot judge (a citation still inside the new line count can stop describing the symbol it names). A split moves symbols between files, so grep the symbol across `src/`, never inside the file you split.
Receipts: docs/agent-laws-receipts.md#a-green-gate-bounds-one-failure-mode-never-the-family-it-belongs-to-2026-08-19-merge-wave

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

When a test's mechanism is "patch X to force branch B", ask which environments can take the other branch, because a box that cannot take it cannot falsify the patch's delivery: a bare call to `_resolve_native_tg_binary_for_mcp` in a split child escaped the `(None, None)` patch, and local green was no evidence since there is no built native binary locally, while CI (binary built) took the native path.

`cost_split_floor_routes.patch_sites` models only the `setattr` shapes, but tests patch in four: `patch("dotted.string")`, `patch.object(mod, "name")`, `monkeypatch.setattr(mod, "name", ...)`, and `mod.X = ...`; the ratchet is also blind to patched attribute calls and patched constants. Sweep per target module every name tests patch on it by all four shapes, intersect with what the module bound on `origin/main`, and require zero bare uses in every extracted child.
Receipts: docs/agent-laws-receipts.md#an-environment-difference-can-be-the-only-instrument-that-sees-a-defect-2026-08-20-tri-split-fan-out

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
