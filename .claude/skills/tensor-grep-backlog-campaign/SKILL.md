---
name: tensor-grep-backlog-campaign
description: >-
  Use when asked to deep-dive, audit, fix, or drain the tensor-grep backlog, or to investigate and
  rank next work and produce SPEC/TDD plans without implementing. Triggers include "work the
  backlog", "what next", "investigate and plan", and backlog-completion campaigns. This is the
  meta-orchestrator for the repo skill library; load tensor-grep-change-control before editing.
---

# tensor-grep backlog campaign

**META-ORCHESTRATOR** for backlog drain. Sequences *which* sibling skill to load; **one home per fact** — do not re-derive procedures that live in the retiring-fellow library (Sonnet-class audience, ground-truth verified).

End-to-end: audit → plan → research → implement → verify → ship. **Load `tensor-grep-change-control` before ANY edit, merge, or release claim.**

This skill has two layers:
1. **Universal pipeline** — `standard-dev-workflow`.
2. **tensor-grep overlay** — shipping discipline (drain, venv, FFI, registration, IDF).

---

## Mission

Deep-dive this codebase end-to-end: bugs, security/infra risks, workflow problems, edge cases, dead/unwired code, and improvements. Work the project backlog to completion with durable receipts — not "looks done" summaries.

You have stale training data. **Never act on memory alone** for external facts, competitor patterns, library behavior, or release mechanics. Ground claims with `use-exa` (and `agy`/`use-gemini` for a third opinion when stakes are high).

---

## CEO communication

**Chat short and business-focused; depth in files.** Plan-only mode ("investigate," "what next," "write a spec") stops after Phase 0 plan docs — **no code** unless CEO explicitly asks to implement.

For “list all backlog,” produce a **closed-world** snapshot, not a highlight reel. Separate:
active/buildable; environment-blocked; nonfinancial decision-gated; financial/spend-gated;
demand/research-gated; and terminal corrections that stale trackers still show. Every live row gets one
stable ID/alias, owner/decision maker, and reopen/start trigger. Preserve mixed outcomes instead of
flattening them into “shipped.”

Also separate **artifact states**. A green PR head, newer uncommitted worktree bytes, an approved plan
hash, a merge SHA, and a published wheel are five different claims. The CEO update names which one each
receipt proves and never lets a green older artifact imply approval of a newer one.

---

## Phase 0 — Investigate, rank, and SPEC/TDD plan

### Superpowers map

Load **`using-superpowers`** first. brainstorming→prompt-engineering; writing-plans; dispatching-parallel-agents→Workflow/subagents; test-driven-development; verification-before-completion→`task-completion-verifier`; executing-plans deferred in plan-only mode.

### Tool audit (tensor-grep — CEO sees only blockers)

| Tool | Use |
|---|---|
| `use-exa` | ripgrep/ast-grep competitive, semantic-search prior art, packaging CI patterns |
| ref-context / Context7 | Typer, maturin, ripgrep API docs |
| `tg` / `tensor-grep-diagnostics-and-tooling` | Repo navigation — authority `tg --help` |
| `gh` CLI | PRs, release CI status |
| `claude-in-chrome` | docs site / install UX if deployed |
| Gmail/Calendar/Drive | **CEO approval only** |

If a required provider is not visible in the initial tool list, search the deferred callable-tool
catalog before declaring it unavailable. Exa can be exposed there. Record a genuine provider failure,
but do not silently substitute stale model memory for required recency research.

### Investigate (parallel tracks)

Prioritize: `AGENTS.md`, `CLAUDE.md`, `docs/BACKLOG.md`, `docs/SESSION_HANDOFF.md`, `pyproject.toml`, `rust_core/`, `.github/workflows/ci.yml`, `tests/`, `.claude/skills/tensor-grep-*`.

Tracks: repo/docs · test/CI/ruff-format-preview gate · registration/routing · Rust FFI/maturin · benchmark/dogfood · Exa research · AGENTS.md roadmap (semantic search, registration-check).

### Planning artifacts

```
docs/plans/requirements-tensor-grep-<YYYYMMDD-HHMM>.md
docs/plans/design-tensor-grep-<YYYYMMDD-HHMM>.md
docs/plans/tasks-tensor-grep-<YYYYMMDD-HHMM>.md
```

(requirements / design / tasks per SPEC+TDD; design must cite **4 command + 2 flag registration sites** if touching CLI; tasks include `uv run --no-sync` gates, dogfood harness, drain/push-race if release-bearing)

### Rank + verify

Score: user/agent value, correctness risk, release readiness, testability, push-race coupling. Update `docs/BACKLOG.md`.

Before CEO summary: `verify-plan-against-code` on all seams; Phase 0f checklist; no implementation in plan-only mode.

### CEO response format

Executive summary · what worked · **all backlog by disposition** · research already done · research still
needed · spend/financial gates · lessons since the prior update · evidence · next action.

Write the detailed snapshot to a dated audit file and update `MEMORY.md`, `docs/SESSION_HANDOFF.md`, and
the canonical tracker in the same change. A CEO chat summary is not durable state.

**CEO approval before:** financially consequential spend or procurement unless the user has already
granted that authority. For nonfinancial publication, claim, contract, and merge decisions, follow the
current user's explicit authority and the repository gates; do not manufacture a question when the user
has told you to ask only about money.

---

## Hard rules (always on)

1. **`common-sense-check`** — act on reversible, sub-$150, non-public, non-destructive work; don't ask permission while a paid resource burns.
2. **`prevent-secret-leak`** — no secrets in tracked files; env-var reads only.
3. **`tensor-grep-change-control`** — load before ANY edit/merge/release in this repo.
4. **Orchestrator role** — main session coordinates; delegate to subagents / Workflow / CLIs. Don't burn context on scans an implementer could do.
5. **Read before write on shared symbols** — `tensor-grep-code-audit` (`tg callers`, `tg blast-radius`, `tg doctor`). Authority is `tg --help`, not grep.
6. **Audit ≠ fix** — `codebase-audit` / `frontend-audit` are READ-ONLY; implementation is a separate gated loop.
7. **Plans are hypotheses** — `verify-plan-against-code` BEFORE multi-file dispatch; `subagent-verification-workflow` + `worktree-fanout-verification-gate` AFTER.
8. **Never trust a self-report** — re-run verification gates yourself in the real venv; worktree/subagent "tests pass" is a hypothesis.
9. **Draft-PR-only autonomy** — endpoint is a draft PR a human merges. Never auto-merge.
10. **WIP CAP (2026-07-08 receipt)** — do NOT dispatch a new BUILD while **>5 PRs are undrained** OR the **main gate is red**. Generating fixes faster than the ~40–66 min/publish drain empties the queue produces "churning, not completing" (backlog stays constant-size while PRs pile up). Design fork: complete-then-start, not start-then-hope. A red main gate is a drop-everything hotfix that jumps the queue ahead of any new build. Check `gh pr list` count and `gh run list --branch main` conclusion before authorizing a new fan-out. **Build-vs-merge decoupling:** the WIP cap and one-merge-per-tick both gate *merge* timing, not when work may *start* -- a PR sequenced "after vX publishes" purely for a CODE-COLLISION reason (it touches the same file, or wants vX's already-merged code as its base) may branch and build off the just-merged `main` in parallel with an in-flight release; only the final merge stays gated. This saves ~40 min/PR across a campaign. See `tensor-grep-change-control` Part 7 for the full pattern (named after merge-queue/speculative-CI, release-train, and build-once-promote-everywhere). **Batch-merge exception (C-batch, v1.93.0/#703-706):** several INDEPENDENTLY already-CI-green PRs may land ~15-20s apart in one tight window and still produce ONE combined, fully-published release — this is not a WIP-cap or one-merge-per-tick violation, provided the operator watches the LAST run in the sequence to full completion (intermediate `cancelled`/rejected-push runs are benign). Do not confuse a monitored rapid batch with an accidental push-race collision (the v1.17.23/#318/#319 incident) — the discipline is watching the final run through, not merging blind. Full mechanism + the v1.93.0 receipt: `tensor-grep-change-control` Part 7 (C-batch).
11. **Mandatory adversarial security gate before merge** — every security-class PR (`apply_policy` / `mcp_server` / `cpu_backend` / `index_lock`/`session_daemon` / auth / money / migration / **native asset, installer, or doctor-probe construction**) gets an adversarial "try-to-BREAK-it, cite `file:line`, default FIX-FIRST-if-uncertain" review **before merge**, in addition to (not instead of) the mandatory `codex` gate below. This is not a rubber stamp: on the 2026-07-08 session it returned SHIP on 3 PRs and caught real issues on 2 — a symlink-follow RCE bypass (`.resolve()` followed the symlink; fixed with `os.path.abspath`) and a lock-release TOCTOU (accepted-as-documented after proving a heartbeat thread makes it unreachable). The native-asset/installer/doctor-probe addition is the v1.75.2/v1.75.3 GPU Phase-0 precedent -- PR #596 (P0-5, loud nvidia-to-cpu installer downgrade) was held in draft with an explicit "Opus gate pending before merge" per its council-reviewed plan, because a silent wrong-flavor install or a misleading `doctor` probe status is a security-relevant integrity failure, not a UX nit. `codex` is the nominal 2nd-vendor tool but its WSL path is unreliable on this box — an Opus **Agent** subagent (`model: opus`) is the reliable substitute when `codex` is dead, not a reason to skip the gate. Verdict shape: `SHIP` | `FIX-FIRST(+file:line+repro+minimal-fix)`.

    **Review-economy rider (2026-08-03):** if a broad model/council prompt times out, retry the exact
    disputed paragraph and invariant rather than weakening the gate or replaying the whole corpus. A
    no-verdict seat is failed, not approval and not an infinite wait. Cursor/other economical model work
    remains a hypothesis until Sol validates the exact resulting bytes/prompt.

    **RED/CI evidence rider (2026-08-03, AGENTS A61–A82):** behavioral RED pins the exact expected
    reason — crash/import/panic/setup errors are not RED. Route/start evidence comes from the actual
    producer/constructor and test-owned OS/raw proof, never a hardcoded bool or production self-attest
    hook. Containment authenticates writer/client provenance and proves alive-before → dead-after plus
    cleanup (not Event/EOF/PID text). Crypto negatives need a valid API operation, exact refusal class,
    and an exportable/trusted positive control. Security grammar validates full sections/types/flags/
    effective authority and rejects unknown/inherit-only forms. Resource-owning protocols name close
    primitives and prove exact-once reverse cleanup on success, BaseException, and cleanup failure while
    preserving the primary error. RED scaffolds cannot enable partial public behavior or unbounded work
    before the guard. Immutable-SHA CI clearance needs a real run, expected per-node outcomes, raw
    artifacts, and the exact population — no run is no clearance. Security green is point-in-time:
    a fresh fixable advisory blocks merge and is upgraded across all live floors, the lock, validators,
    and remediation text before a new exact-head audit — never ignored. **A77–A82 (2026-08-06 PM):**
    file-based PR-check pollers (never stdin+heredoc empty→ALL_TERMINAL); usage-limit seats FAILED;
    status-stamp PRs retarget governance pins; gate tip bytes not archaeological RED SHAs; HIGH
    receipts ≠ Sol SHIP; AMEND_SPINE when READY∩reconcile-BLOCKED.

12. **Order the drain by RELEASE impact, not PR number (2026-07-26 receipt).** Only `fix:`/`feat:` trigger semantic-release. `docs:`/`test:`/`bench:`/`chore:` complete without publishing, so they create no publish to race — their gate is just "the newest main run completed" (~6 min — the GATE duration, i.e. the wait for that main run itself to complete; a DIFFERENT referent from the semantic-release-job-alone figure in the drain-cron section below) versus a full release cycle (~30–60 min, longer under runner scarcity). Landing the non-releasing PRs first took a 12-deep queue to 7 in about an hour that would otherwise have bought two merges. **One-per-publish protects an in-flight PUBLISH; it is not a per-PR serialisation.** Two riders: check for file collisions first (two PRs both editing `docs/CONTRACTS.md` will conflict once either lands), and re-poll `mergeable` after each merge — GitHub returns `UNKNOWN` for a few seconds while it recomputes, and `UNKNOWN` is not `CLEAN`.
13. **The gate is "newest main run COMPLETED", not "completed GREEN" (2026-07-26 receipt).** When `main` is red, the fix FOR that red must still be mergeable — requiring green before merging the thing that makes it green is a deadlock. Merge the hotfix, then confirm `main` actually recovered on a later run; that recovery is the evidence the fix worked, not the merge itself. Everything else stays parked while red: merging onto a broken `main` compounds it and obscures which commit owns the failure.
14. **A concurrent agent's PR gets an INDEPENDENT gate, and the verdict goes on the PR (2026-07-26, #786).** A PR arriving from another session/worktree is not self-gated by definition, so gate it — then post the verdict as a PR comment with its evidence (what was probed, what the control arm showed). A gate that lives only in your transcript is lost work: the next session re-runs it or reaches a different conclusion, and the author cannot un-draft without waiting on you. Cost: one `gh pr comment`.
15. **Verify the fix on the MERGED artifact, not only pre-merge (2026-07-26).** Pre-merge proves the BUG is real (control arm on the unpatched tree); it says nothing about whether the FIX behaves on `main` after a squash. Re-run the treatment arm against merged `main` — and check the guard is present *structurally* (e.g. `"_seen" in fn.__code__.co_varnames`) rather than by re-reading the diff.
16. **Make branch pruning DECIDABLE (2026-07-26).** "Don't bulk-nuke agent branches, they may hold WIP" let ~70 husks accumulate. `git merge-base --is-ancestor <branch> main` is a per-branch proof: an ancestor of `main` has its commits already in `main`, so deleting loses nothing. Delete with `git branch -d` (never `-D`) so git independently refuses anything unmerged — if the two checks disagree, stop. Receipt: 61 deleted / 0 refused / 2 kept. **`git branch --merged` under-reports after squash-merges, and a CLOSED PR is not a merged PR** — one survivor had exactly that shape.

---

## Skill library — retiring-fellow taxonomy (count DERIVED, never stamped — see derive box below)

**Ground-truth rule:** verify commands/paths against repo + `tg --help`; re-read each skill's "Provenance and maintenance" when drift suspected. **No skill routes around `tensor-grep-change-control`.**

**Do NOT maintain a numbered table here — tables rot.** This file's table went stale three times
(20→26→27 headings) and then omitted 7 on-disk skills
(`tensor-grep-argv-normalization-and-shadowing`, `tensor-grep-codex-gated-audit-loop`,
`tensor-grep-cross-platform-path-confinement`, `tensor-grep-hermetic-hostile-tests`,
`tensor-grep-index-fingerprint-freshness`, `tensor-grep-release-drift-check`,
`tensor-grep-worldclass-roadmap` — verified absent 2026-08-12). Derive the population instead:

```bash
ls -1d .claude/skills/tensor-grep-*/ | wc -l      # every tensor-grep-* library skill (was 26/27 in
                                                  # older passes; derived 34 at v1.110.14, 2026-08-12)
ls -1d .claude/skills/code-search-and-retrieval-reference   # +1: the domain-theory skill
# the bare .claude/skills/tensor-grep/ usage skill is EXCLUDED by definition (usage docs for the
# tool itself, not a library entry) — AGENTS.md's gated "**N skills**" sentence counts the same way
```

**Load-when routing:** use AGENTS.md's "Carrying the project forward -- the in-repo skill library"
index-by-intent (grep that heading) — it carries the Change-safely / Understand / Operate / Advance /
Extend / Orchestrate buckets and is pinned to the real folder set by
`tests/unit/test_skill_index_sync.py`, which this file is not.

**This skill** = meta-orchestrator for generic backlog drain — one entry of the library it indexes
(the old table's row-number citations rotted with the table; derive the set above).
**Semantic-search flagship → `tensor-grep-semantic-search-campaign`**, not here.
**Scale/hang campaign → `tensor-grep-large-repo-scale-campaign`**, not here.

**Also load:** `tensor-grep` (usage), global `~/.claude/skills/` (`verify-plan-against-code`, `dogfood-the-shipped-artifact`, …). **NO `docs/skill_index.md`** — use `AGENTS.md` skills section + the derive box above. **`.claude/skill_rules.json`** is a separate, harness-level mechanism: project-local keyword/intent triggers consumed by the global `skill_activation_gate.py` hook to auto-fire a skill on a matching prompt. It seeds only SOME of the library skills (count derived, never stamped — same derive box). **Do not trust a number or a name list here** -- this sentence enumerated 12 skills in prose and was wrong three ways by 2026-08-02: the real count was 14, and it named `tensor-grep-large-repo-scale-campaign` as having zero rule when it had since gained one. Derive it:

```bash
python -c "import json,os; d=open('.claude/skill_rules.json').read(); \n  lib={x for x in os.listdir('.claude/skills') if x.startswith('tensor-grep-')}; \n  print(sorted(x for x in lib if x not in d))"
```

Its silence on a topic is not evidence a skill doesn't apply; the AGENTS.md index-by-intent stays authoritative for manual routing.

---

## Backlog + session continuity

- **`docs/BACKLOG.md`** — canonical prioritized/historical task ledger: id, P0–P3, description, receipts,
  and shipped history. **Create it on session 0 if absent** (seed from memory anchor + `gh pr list` +
  session task store, then discard session store as SoT).
- **`docs/TASK_BOARD.md` canonical status index** — the machine-parseable live state for the closed-world
  canonical ID set. Historical prose in either document is not a live-status oracle. Until this index
  exists, a dated reconciliation audit must derive live state from BACKLOG + SESSION_HANDOFF + GitHub and
  say that it is an interim snapshot.
- **GitHub (`gh pr list`)** — PR source of truth for open/merged work.
- **Memory anchor** — on every backlog change, update via `MEMORY.md` / `~/.claude/projects/<slug>/memory/feedback_tensor_grep_backlog.md` with: P0 queue, in-flight PRs, last shipped tag, push-race waiter state, "resume here".
- **Restart order:** memory anchor → `docs/BACKLOG.md` → `docs/SESSION_HANDOFF.md` → `AGENTS.md` → GitHub. Never use the ephemeral session task store as source of truth.
- **CEO status** = closed-world live backlog + blockers + research + spend + next 3 actions; do not omit
  demand-gated or mixed-terminal rows merely because they are not immediately buildable.
- **Artifact attribution** — store PR head, squash merge, main-CI head/run, release commit/tag, and PyPI
  proof separately. They may be different SHAs. Exact CI proof includes run ID, head SHA, stable job
  population, and zero unfinished/failing jobs.
- **Plan artifact identity** — choose one canonical worktree/blob and one SHA-256 method for every
  thinktank seat. Mixed line endings can make clean-filter-equivalent Windows worktrees hash differently.
- **Dependency/lifecycle gate** — prove each service/registration/producer precedes its consumer/test;
  when a numbered draft PR exists, its owning row must be `IN_FLIGHT` in that PR. Every deferred
  security behavior needs a canonical ID, owner, threat boundary, and reopen trigger.

**Steward cron (this repo):** the backlog-steward tick is **session-scoped** — it re-arms with a NEW id and
schedule every session, so any recorded id goes stale immediately. Do NOT trust a previously-recorded id
(this file, MEMORY.md, a handoff note). Verify the live cron via `CronList` at session start; re-arm if
absent. (Verified 2026-07-16: three different sources cited three different ids/schedules — all stale.)
Re-confirmed **2026-07-24**: a session-scoped cron also dies SILENTLY on a mid-session CLI restart or a
PC reboot, not only at session boundary — the same steward was lost twice in one session this way, once
to each. There is no error or notification when it dies; the only way to know is to check. After ANY
restart or crash — not only at the start of a fresh session — re-verify with `CronList` and re-arm if
absent, and keep the durable state (queue, in-flight PRs, "resume here") in the task store + `MEMORY.md`,
which survive a restart even when the cron itself does not (AGENTS.md A25).

**Verify in BOTH directions — a presumed-dead cron can still be alive (AGENTS.md A26, same session).**
The mirror failure to the one above: a cron assumed dead from an earlier loss turned out to still be
running, ALONGSIDE its freshly re-created replacement, and fired a stale instruction (telling a later
tick to gate a PR that had already merged). Do not just re-arm and move on after a restart — call
`CronList`, read every entry it returns (not just the count), and explicitly delete any superseded
duplicate. A stale backstop that still fires looks authoritative and can act on data that is no longer
true; it is strictly worse than having no backstop at all.

---

## Risk-calibrated ceremony

| Risk | Ceremony |
|---|---|
| Load-bearing / security / perf / concurrency / FFI / release / public-ship / >$50 | Full: Fable design audit → Exa → thinktank (verbatim) → verify-plan-against-code → TDD build → review loop → draft PR |
| Contained bug fix (flag, exit code, null-check, single-site) | Lean: Fable-audit-found → Sonnet TDD fix → **verify in real venv** → CI parity → draft PR. **No 5-model council.** |

**Primary execution path:** Cursor **`Agent` tool subagents**
- **Fable** (`model: fable`) — design audit, synthesis, plan review, final vs-plan check
- **Sonnet** — TDD implementation, routine review

**Fable constraints:**
- Use **Agent subagent `model: fable`**. Do NOT rely on `claude -p --model claude-fable-5` headless.
- **Workflow tool cannot reach Fable** — silently falls back to session model. Use Agent subagents for Fable; Workflow for haiku/sonnet file-grounded fan-out only.
- **Fable is ~2× token cost.** Cap Fable parallel fan-out at **≤2–3** (vs ≤3–5 for sonnet/haiku).
- Fable's classifier may route explicit vuln-hunting to Opus — frame audits as correctness/quality to stay on Fable; run explicit security audits on Opus.

**Resume-from-transcript, not re-dispatch (broadened 2026-07-08 — ANY transient failure, not just session-limit kills).** A background subagent (Fable or otherwise) that dies mid-task — a session-limit kill (`had-no-active-task`) **or** a transient `"Agent terminated early due to an API error: 500"` — is resumed via `SendMessage` to its agent ID, not re-dispatched fresh: the transcript carries the partial work forward. Message it plainly: *"you hit a transient error, your work is intact, continue + <the finish criteria>."* Receipt: happened 3x in one session (2 builds + 1 security-gate agent hit a transient API 500) and all 3 recovered cleanly with zero lost work. Re-dispatching fresh instead of resuming loses everything the agent had already done.

**Don't kill a build on staleness (2026-07-08 receipt).** A complex build (a routing redesign, heavy test-rewiring) can legitimately run 10–15+ minutes between visible output flushes. A "stale, no output for N minutes → kill it" heuristic **destroys a working agent** — this exact heuristic killed an in-progress build TWICE on this session before the kill-notes proved it had been actively rewiring tests the whole time, not hung. Trust the harness's own completion notification; only intervene on a **genuine** hang, and diagnose from the kill-note's last line (or `anti-hang-test-protocol`'s exit-124/137 signal), never from an elapsed-time guess alone.

**A "stopped" agent may have already committed — check the worktree before re-dispatching (2026-07-24 receipt).** A build agent's process exited after committing but before emitting its own completion summary; the notification read as "no completion record found," which looks like the work is lost. It was not: `git -C <worktree> status`/`log` showed a clean tree with the real commits present and correct. Re-dispatching fresh on a "stopped" notification without checking the worktree first risks duplicating (or conflicting with) work that already landed — run `git -C <worktree> status` and `git -C <worktree> log --oneline -5` BEFORE deciding the work needs to be redone. Full incident: AGENTS.md Campaign Orchestration A23.

**A worktree agent can commit on a detached HEAD — verify `HEAD` matches the branch ref before pushing/opening a PR (2026-07-24 receipt).** `git push origin <branchname>` can push a stale branch REF (still at `main`'s tip) while the agent's real commit sits at the worktree's detached `HEAD`, unreachable from that ref — GitHub then rejects the PR with "No commits between main and `<branch>`," which reads like the work vanished a second, distinct way. Compare `git rev-parse HEAD` against `git rev-parse <branchname>`; if they differ, push the SHA explicitly (`git push origin <sha>:refs/heads/<branchname>`) before opening the PR. See `tensor-grep-debugging-playbook` §16 for the full triage row and AGENTS.md's A24.

**External CLIs:**
- **codex** — **MANDATORY** adversarial gate on every **money / auth / security / migration** diff before merge (separate quota; catches agent over-claims). **Fallback** for general peer review when Agent subagents are throttled. When `codex`'s WSL path is unreliable, an **Opus** Agent subagent is the reliable substitute for the mandatory gate (Hard Rule 11) — never skip the gate outright.
- **agy / cursor-agent** — fallback only when Agent subagents are throttled or unavailable.

---

## Phase pipeline (default: `standard-dev-workflow`)

### 0 — Orient
Read `CLAUDE.md`, `AGENTS.md`, `docs/SESSION_HANDOFF.md`, memory anchor, `docs/BACKLOG.md`, open PRs.

### 1 — Prompt engineering (`prompt-engineering`)
Bounded spec: scope, non-goals, required reads, verification gates, risk tier (lean vs full), noise band for quantitative claims.

### 2 — Plan (`superpowers:writing-plans` + skill discovery)
Numbered plan; per-step skill + risk + verification gate. TDD-first for behavior changes.

### 3 — Research (`use-exa` + optional `agy`) — REQUIRED before non-trivial execution
Fold findings as ADDED / CONTRADICTED / SUPERSEDED. Competitive/prior-art → derive edge cases → plan + tests BEFORE implementation.
**Wire-format / provider-contract claims** on money/auth paths (webhook field values, event semantics, SDK response shapes) must be Exa-verified against the provider's **live docs** before shipping — a wrong premise silently breaks working billing.

### 4 — Council review (`use-thinktank` / Fable) — risk-tiered
Mandatory for load-bearing / security / concurrency / FFI / public-ship / >$50. Pass verbatim plan. Skip for contained fixes.
- Multi-round hash-frozen approval loop (2026-08-13 receipt): freeze the artifact hash, run the 8-seat council, apply ONLY the verified findings, re-hash, re-run until N/N APPROVE (5 rounds to 7/7 on docs/plans/2026-08-13-backlog-completion-plan.md). Failed seats are not votes. A step whose content depends on a future verdict must be written NOW as a named GATE with command + trigger + re-approval rule — "EXPAND AT WAVE START" reads as a placeholder to half the council. Cite the artifact hash in every round's question file. (A108)


### 5 — Pre-dispatch gate (`verify-plan-against-code`)
Adversarial seam verification (`file:line`). BLOCK build until clean.

**Run that skill's Step 0 (the PREMISE check) FIRST on every board item.** Seam verification proves
the anchors are real; it cannot tell you the item is already CLOSED — a plan against a fixed bug has
citations that resolve perfectly. Reproduce the defect or find the fixing commit before dispatching.
Receipt: the 2026-08-01/02 reconcile found **17 of ~24** open items already shipped, refuted, or
by-design, and one agent was dispatched at finished work (#58). The machine-parsed canonical status
index in `docs/TASK_BOARD.md` is the live-status view; `docs/BACKLOG.md` remains the canonical
prioritized/historical ledger. GitHub remains the PR-state oracle.

**Extend the premise check from board items to BRANCHES/WORKTREES (2026-08-12).** Before dispatching
a repair on a stale-looking branch, premise-check its CONTENT, not just its existence: `git cherry
<target> <branch>` proves only PATCH-ID distinctness, never novel content (the same fix can land as a
different patch); enumerate the branch's touched paths, diff each endpoint against the target, and
confirm the symbols/tests actually exist on the target; use blob identity
(`git rev-parse <rev>:<path>`) + pickaxe (`git log -S"<exact string>"`) for historical-blob claims.
A branch whose "missing" work already landed elsewhere is the same finished-work dispatch as #58.

### 6 — Implement

| Work type | Tool |
|---|---|
| Design / audit / synthesis / hardest review | **Fable Agent subagent** — primary |
| Bounded builds, TDD, refactors | **Sonnet Agent subagent** — primary |
| Entire-repo unlimited-token sweep | `use-cursor` auto only, no `--model`, never WSL-nohup |
| Money/auth/security/migration adversarial gate | **`use-codex` via `codex-headless.ps1` — MANDATORY** |
| General peer audit (fallback) | `use-codex` when Agent path throttled |
| Third opinion (fallback) | `agy` / `use-gemini` |

Pass subagents Phase-1 spec verbatim + relevant BACKLOG item + carry-forward audit lessons.

### 7 — Review loop (calibrate to risk)
- **Contained fix:** Fable-audit-found → Sonnet-TDD-build → verify-in-real-venv → CI parity → draft PR.
- **Load-bearing:** add thinktank → code-reviewer/Opus → **codex adversarial gate (mandatory on money/auth/security/migration)** → Fable vs plan + code. Repeat until zero must-fix findings.
- Always: project CI parity gates; dogfood shipped binary for CLI/routing changes (`scripts/dogfood/`).

Exit only on `task-completion-verifier` PASS with receipts **you** reproduced in the real venv.

### 8 — Ship + document
- Merge via the **self-firing drain-cron** pattern (one PR at a time; see push-race below) — never a
  long-lived backgrounded drain loop.
- Update `docs/BACKLOG.md`, memory anchor, `docs/SESSION_HANDOFF.md`, `AGENTS.md` if practice changed.
- Record proven Workflow recipes in `workflow-ledger` if used.

---

## tensor-grep operating rules (govern shipping here)

### Merge / release — the self-firing drain-cron, not a backgrounded loop (2026-07-08 receipt)

**A long-lived `bash drain_loop.sh &` background process is the wrong shape.** It kept **dying**
during the long CI/publish waits on this session (and once, an inner `&` inside a `run_in_background`
wrapper orphaned it entirely) — a ~40–66 min wait window is a long time for a backgrounded shell
process to survive uninterrupted. Note there is no `scratchpad/drain_v2.sh` checked into this repo —
any ad hoc drain script an agent writes lives in the OS scratch/temp dir (session-ephemeral), never
committed at that path; do not cite it as a repo-relative file.

**The fix: a per-fire, short-lived cron/loop tick that does at most ONE merge, then exits.** Each
fire is cheap and stateless — nothing to be killed, because nothing stays running between fires.
Arm it with the `loop` skill (`/loop 30m <the one-shot prompt below>`) or an equivalent external
scheduler — never a backgrounded `&` shell loop. Cadence **~30 min** matches the achievable
~1-PR-per-publish rate (a release-bearing merge's own wait window is ~40–66 min, so firing much
faster than that just re-checks a still-in-flight release).

**`/loop` vs `CronCreate` — pick based on how long the drain needs to survive, not habit.** `/loop`
is **session-bound**: it stops firing the moment the current session ends (context compaction, a
crash, the user closing the terminal) — fine for a drain you expect to finish within one sitting, but
it silently dies on anything longer. `CronCreate` schedules a job that **survives session end and a
process crash**, re-arming on its own cron schedule independent of whether this session is still
alive — the right choice for a multi-hour/multi-day drain campaign. **Whichever you use, re-verify it
is still armed at the start of a NEW session** (a session-scoped `/loop` from a prior session is
already dead; a `CronCreate` job persists but its id/schedule should still be confirmed via a listing
call, not assumed from a stale note — see the Steward-cron caution below).

**Event-driven alternative to blind polling: `gh run watch <run-id> --exit-status` (or the `Monitor`
tool) beats a fixed cadence for latency-sensitive waits.** The 30-min cadence above is sized for
*drain* throughput (don't check faster than a release can possibly finish), but if you specifically
need to know the MOMENT one release finishes (to chain the next action immediately rather than wait
out to the next cron tick), block on the run directly instead of polling on a timer — `gh run watch`
returns as soon as the run's conclusion is known, and `Monitor` gives the same event-driven wakeup for
a backgrounded process. Use the cadence-based cron for the drain loop itself; use the event-driven
watch for "tell me the instant this ONE release publishes so I can act."

**One-shot logic per fire** (pseudocode; adapt the `gh` calls to the live PR queue):

```bash
# ONE fire = ONE merge attempt, then exit. No internal loop, no backgrounding.
latest_tag_on_pypi() { ... }                    # compare latest git tag vs PyPI's latest version
main_ci_completed()  { [ "$(gh run list --branch main --workflow ci.yml --limit 1 \
                             --json status -q '.[].status')" = "completed" ]; }

# Push-race check FIRST: refuse to merge into an in-flight release window.
latest_tag_on_pypi && main_ci_completed || { echo "release in flight, skip this fire"; exit 0; }

# Pick the lowest-numbered CLEAN, mergeable PR (WIP-cap-respecting: Hard Rule 10).
pr=$(gh pr list --state open --json number,mergeStateStatus \
      -q 'map(select(.mergeStateStatus=="CLEAN")) | sort_by(.number) | .[0].number')
[ -n "$pr" ] || { echo "nothing CLEAN to merge"; exit 0; }

gh pr merge "$pr" --squash --delete-branch
```

- **One merge per fire, one fire per cadence tick** — never merge two PRs in the same fire even if
  both look CLEAN; the next fire will pick up the next one after the push-race check re-clears.
- **Push-race check is mandatory on every fire, not just the first**: the latest `chore(release): vX`
  tag must be confirmed on PyPI AND the latest `main` CI run must show `conclusion: success` before
  merging anything — including `docs:`/`chore:` PRs, which don't bump version but are still unsafe to
  interleave mid-release.
- **Real wait window ~40–66 min** per release-bearing merge (native-build-smoke + benchmark-regression
  + semantic-release + publish-pypi). The ~6-min figure here is only the **semantic-release job in
  isolation** (a DIFFERENT referent from Hard rule 12's ~6-min non-releasing GATE duration, "newest
  main run completed") — don't cadence the cron faster than the real window or every fire just
  re-observes "still in flight".
- **Green-gap batch merge (2026-08-12):** when NO release is in flight or planned, non-releasing
  `docs:`/`test:`/`chore:` PRs may merge back-to-back within ONE green gap — their only gate is the
  newest main run completed (Hard rule 12). This makes the referents of the two rules explicit:
  "one merge per fire" above governs RELEASING PRs (one-per-fire stays for them outside a monitored
  C-batch — Hard rule 10's exception); it does not serialize non-releasing PRs against each other in
  a release-free gap. The moment a release is in flight or next in the queue, everything falls back
  to the push-race wait (change-control Part 7 "Precedence").
- Failed release **self-heals** on next push (tag-derived). Don't panic-rerun.
- Respect **Hard Rule 10 (WIP CAP)**: if >5 PRs are undrained or the main gate is red, the fire should
  refuse to dispatch a *new build* (merging the existing queue is still fine/expected).

### Verify in the REAL venv

Worktrees have no built `.venv` — agent "tests pass" is a hypothesis.

The real venv is OS-owned. From WSL, never pass the Windows checkout (`/mnt/c/...`) as `uv --project`:
`uv` can delete/replace the incompatible Windows `.venv` with a Linux one. Use the WSL worktree's own
venv for RED iteration, then run the canonical gate from PowerShell in the Windows checkout. If crossed,
move the bad venv aside and rebuild from Windows with `uv sync --frozen` before trusting any receipt.

```powershell
uv run --no-sync ruff check .
uv run --no-sync ruff format --check --preview .
uv run --no-sync mypy src/tensor_grep          # src only, NOT tests
uv run --no-sync pytest tests/<targeted>.py    # scoped locally on this desktop
```

- **`uv run --no-sync` is mandatory** — plain `uv run` re-syncs away the `[dev]` tree-sitter tree.
- **`ruff format --preview` is a SEPARATE gate from `ruff check`** — check-only misses format CI (#424). Never pass `--preview` to `ruff check`. Bare `ruff format` without `--preview` reverts preview style.
- **Full pytest + Rust test/clippy matrix + benchmarks + release-asset builds → PR/main CI only** (`grep -n "full pytest, full Rust test/clippy matrices" AGENTS.md`; was `AGENTS.md:385`, now `:487` — drifted +102 lines since the 2026-07-23 pass, see Provenance — high-memory; don't run full suite locally unless user explicitly approves).
- Rust changes: `maturin develop` + `cargo test --manifest-path rust_core/Cargo.toml`.

### Concurrent shared-checkout

While a background code-agent uses the **shared checkout**: NO `git reset --hard`, checkout, or branch-switch. Isolate writers with `isolation: 'worktree'`. Orchestrator: read-only/`gh` only. Harvesting a worktree's **committed** work is main-loop-safe (even under rate limit). Before integrate: `git worktree remove --force <path>` → checkout branch in main → re-run gates above.

### Harvest pattern (worktree -> PR, 2026-07-08 receipt)

A worktree agent's "tests pass" is a **hypothesis**, not a fact — its venv may use a copied or
absent native extension, so a green result there proves nothing about the real build. The proven
harvest sequence: (1) cherry-pick the worktree agent's commit onto a **fresh branch off
`origin/main`**; (2) **re-verify in the real venv** (which has the built Rust extension) — ruff
check + `ruff format --preview` + mypy + a live smoke test, not just the worktree's self-report;
(3) run the mandatory adversarial security gate (Hard Rule 11) if the diff touches a security-class
surface; (4) THEN open the PR. Clean up after: `git checkout main; git reset --hard origin/main; git
worktree remove --force <path>`. Never open a PR straight from a worktree's own "all green" claim.


### Subagent fan-out on shared-pin surfaces (2026-08-20, 11-PR tri-split receipts)

The multi-writer discipline when several agents split/move code concurrently. Full law:
`AGENTS.md`, "An Environment DIFFERENCE Can Be The Only Instrument That Sees A Defect".

- **One agent owns each shared file per wave.** `scripts/file_size_allowlist.json`'s cli
  entries are ADJACENT LINES: two green PRs that each edit one line conflict pairwise.
  Brief every agent to change exactly its own line; resolve at merge with the lower pin.
- **Union-merge all open PRs touching a shared pin/census before queueing any.** A branch cut
  before a gate merged is green against a world without the gate — twice this campaign the
  union failed where every branch passed.
- **Every split brief must require the FOUR-shape patch sweep** (`patch("dotted")`,
  `patch.object`, `monkeypatch.setattr`, `mod.X =`) against the target module, and must state:
  on a box without the native binary, local green CANNOT clear the native-path branch — CI is
  the only arm that takes it. Three rounds of one bypass class shipped before this was written
  down.
- **Relocated handlers/emitters must be excluded from source censuses with the reason inline**
  (moving a file does not audit it), and source-scanning tests must census the FAMILY
  (`glob("repo_map*.py")` with a `len(paths) > 1` scan-ran control), or the count silently
  stops covering the emitter that moved.
- **A monitor's output tail shows what HAPPENED, not what it WATCHES.** A quiet tail on a
  dynamic monitor reads identically to a stale baked list; read the stored command (TaskStop
  echoes it) before replacing a monitor you suspect. Receipt: 2026-08-20, a healthy
  gh-pr-list-every-pass monitor was stopped on the strength of its own quiet tail.
- **A stop-notification proves the agent has NO live background children.** When a subagent
  stops saying it is waiting on its own background work, the notification itself refutes the
  story -- SendMessage-nudge it to run the verification foreground and finish the slice.
- **When slice B builds the tested form of something slice A stubbed on a shared surface,
  B's brief must name the reconciliation duty** (replace A's ad-hoc wiring, keep stable IDs
  so cross-references resolve). Receipt: W2-a's untested inline snippet vs W2-c's module.
- **Monitors: key on `PR:head-sha`** (first-terminal-state keying goes silent after a re-push)
  and print an explicit exit line so stream-end is not mistaken for a hang.
- **A dead agent's worktree**: diff it against its branch before reasoning about its CI — both
  session-limit deaths this campaign left verified-but-uncommitted fixes, i.e. local-green /
  CI-red on the same head.
- **Read every agent report as a hypothesis and verify against the code.** The fleet's reports
  were good, and still: one agent's "environment failure" was a real bypass, another's line
  counts were exact, a third corrected MY count. Verification is where half this campaign's
  defects were caught.

### Splitting an oversized file — the PRE-SPLIT citation sweep (2026-08-19, three receipts)

Waves 2, 3 and 4 of the file-size campaign each shipped, each went red in CI, and each red
had the **same** single cause: shrinking a file dangled `file.py:NNN` citations in
`.claude/skills/`. Three times is not bad luck, so the sweep moves BEFORE the split.

**Before you split `X.py`, and again before you push:**

```bash
grep -rnE 'X[a-z_]*\.py:[0-9]+' .claude/skills/ AGENTS.md CLAUDE.md docs/
```

Then re-anchor every hit **by symbol**, never by a fresh line number
(`AGENTS.md`, "Cite the SYMBOL, not the line") — hand over the locating grep instead:

```markdown
`agent_capsule_constants.py` (find it: `grep -n "_CAPSULE_INLINE_CALLER_ANNOTATION_ENV = " src/…`)
```

**A split can also change the FILE, not just the line.** Wave 4 moved
`_CAPSULE_INLINE_CALLER_ANNOTATION_ENV` into a new `agent_capsule_constants.py`, so the old
citation was wrong in both coordinates. Grep for the symbol across `src/`, not inside the
file you split.

🚨 **The gate cannot catch the dangerous half.** `test_skill_library_drift.py` fails a
citation that points **past the end** of a file. It says nothing about one that still
resolves and now points at unrelated code. Wave 4 shrank `agent_capsule.py` 3,652 → 926 and
`code-search-and-retrieval-reference/SKILL.md`'s `:294` stayed **green** while landing inside
a different function — CI caught six of seven, and the seventh would have shipped. The grep
above is what found it; the gate is not a substitute. `/tg-skill-audit`
(`.claude/workflows/tg-skill-audit.js`) covers that half deliberately.

### FFI / Rust-core

`maturin develop` (cargo at `C:/Users/oimir/.cargo/bin/cargo.exe`, ~15s) → call the real `.pyd`. Never trust `*args/**kwargs` mocks ("mock-green-but-dead bridge").

### Contract-heavy registration

**New command — 4 sites** (miss one → silent misroute to ripgrep):

| # | Site | File |
|---|---|---|
| 1 | `KNOWN_COMMANDS` | `src/tensor_grep/cli/commands.py` |
| 2 | `Commands::X` + dispatch arm | `rust_core/src/main.rs` |
| 3 | `PUBLIC_TOP_LEVEL_COMMANDS` | `tests/e2e/test_routing_parity.py` |
| 4 | `@app.command` | `src/tensor_grep/cli/main.py` |

**New search flag — 2 front doors:**

| # | Site | File |
|---|---|---|
| 1 | `SEARCH_PYTHON_PASSTHROUGH_FLAGS` | `rust_core/src/main.rs` |
| 2 | `_TG_ONLY_SEARCH_FLAGS` | `src/tensor_grep/cli/bootstrap.py` |

- `tg callers` for callables; **grep / `tg scan`** for sets/decorators/dispatch tables (`callers` cannot see them — `AGENTS.md`, `grep -n "cannot see set/list/decorator registrations" AGENTS.md`; was `:412`, now `:887` — drifted +102 lines since the 2026-07-23 pass, see Provenance).
- Change a pinned contract → update its governance test in the **same PR**.

### CLI hygiene

ASCII-only CLI output (emoji → cp1252 crash). `git commit -m` backticks → bash substitution; use `-F`/heredoc.

### Latency / ranking work

Profiler is the oracle: `tg … --profile` on the actual slow command before designing.

**IDF blast-radius** (`grep -n "This IDF blast-radius is invisible to the call graph" AGENTS.md`; was `AGENTS.md:379`, now `:481` — drifted +102 lines since the 2026-07-23 pass, see Provenance): BM25/IDF surfaces (`--rank`, agent-capsule, semantic search) are sensitive to corpus changes — adding query-adjacent terms lowers corpus-wide IDF and can silently flip rankings (invisible to call graph). Harden tie/marker detection for IDF shifts; **never relax a failing ranking test** (that masks real degradation). Tracked: capsule-hardening Task #4 (ledger B3).

### Dogfood

`scripts/dogfood/` against installed binary — CliRunner bypasses bootstrap front door.

---

## Orchestration scale

- Subagents: few independent side tasks.
- Workflow: dozens–hundreds of scoped units — map-ledger-first, scoped reads, haiku/sonnet scan; **not** Fable.
- Chunk parallel launches **≤3–5** for sonnet/haiku; **≤2–3 for Fable** (higher token cost → session limit kills).
- On throttle: harvest completed worktree commits (main-loop-safe, follow the harvest pattern above); **resume** any agent that died mid-task (session-limit kill OR a transient API 500, not just Fable) via `SendMessage`, don't re-dispatch; retry in smaller waves; external CLI only after Agent retry fails. Don't confuse a resumable transient failure with a genuinely stale/hung agent — see the don't-kill-on-staleness note above before intervening on either.
- **WRITE fan-out:** agents ignore "return-patch / don't commit" and **write the shared tree anyway**. You MUST use `isolation: 'worktree'` OR give each agent **non-overlapping file scopes** + integrate serially. Never rely on return-patch to keep the tree clean.
- After WRITE fan-out: orchestrator serial integration + full CI after.

## Windows / local-env blocked

When local verification fails on a Windows/env issue (torch/onnxruntime DLL, missing optional dep): Exa-confirm it's **env-not-code**, then **CI is authoritative** — do NOT chase heavy installs that break the venv (e.g. `optimum[onnxruntime]` clobbering torch). PG+ONNX suites verify in CI, not locally.

---

## Competitive analysis + edge cases (per major finding)

Exa competitive/prior-art scan → derive edge cases competitors handle or miss → add to plan + tests BEFORE implementation. Thinktank 3-seat on edge-case list only if productization stakes are high.

---

## Model routing

| Role | Tool |
|---|---|
| Orchestration | Main session |
| Strategic audit / synthesis | Fable Agent subagent |
| Implementation / routine review | Sonnet Agent subagent |
| Explicit security audit / hard debug | Opus |
| Money/auth/security/migration adversarial gate | codex (mandatory) |
| General peer audit / sweeps (fallback) | codex / agy / cursor |

---

## What this skill refuses

- Skipping Exa on external/competitive claims.
- Dispatching unverified plans.
- Trusting worktree/subagent "done" without real-venv re-verification.
- Parallel writes without worktree isolation + full CI after.
- Destructive git on shared checkout while a code-agent is live.
- Merging during an in-flight release (push-race).
- Using grep for symbol intelligence when `tg` applies.
- 5-model council on a contained bug fix.
- Running full pytest/Rust matrix/benchmarks locally without user approval.
- Relaxing a failing ranking test to mask IDF degradation.
- Relying on "return-patch / don't commit" to keep the shared tree clean during parallel writes.
- Re-dispatching an agent that hit a session-limit kill or a transient API 500 instead of resuming it via `SendMessage`.
- Killing an agent on an elapsed-time staleness heuristic without checking whether it is actively (if slowly) still working.
- Skipping mandatory codex gate — or its Opus substitute — on money/auth/security/migration diffs.
- Dispatching a new BUILD while >5 PRs are undrained or the main gate is red (Hard Rule 10).
- Running a long-lived backgrounded drain loop instead of a per-fire, short-lived drain-cron tick.

---

## Sibling skills (detail via the derive box + AGENTS.md index above)

- `tensor-grep-change-control` — gates (load before edit)
- `tensor-grep-semantic-search-campaign` — flagship CPU-moat program
- `tensor-grep-release-and-positioning` — push-race depth
- `worktree-fanout-verification-gate` — post-fan-out integration
- `standard-dev-workflow` — universal 8-phase pipeline

## Authoring discipline (retiring-fellow rules)

- Load siblings for depth; **one home per fact**; verify commands against repo + `tg --help`.
- Re-read sibling "Provenance and maintenance" when facts may have drifted.
- No skill routes around `tensor-grep-change-control`.

## Provenance and maintenance

Process/orchestration facts re-verified **2026-07-08** against **v1.49.3** (`pyproject.toml`,
`grep -n '^version' pyproject.toml`); the **skill-count table was re-verified 2026-07-14 against
v1.75.4** (see the docs-accuracy PR that added this note); the **Steward-cron line was de-hardcoded
2026-07-16 against v1.78.1** after three sources (this file, MEMORY.md, a handoff note) were each
found citing a different stale cron id/schedule — the session-scoped nature of the tick means any
recorded id is a landmine, not a fact to stamp; the **skill-count table was re-verified again
2026-07-22 against v1.93.2**, registering 6 new skills (`tensor-grep-prepare`, `tensor-grep-ledger`,
`tensor-grep-find-and-route`, `tensor-grep-multi-project-search`, `tensor-grep-enterprise-review-bundle`,
`tensor-grep-gpu`) and adding the C-batch batch-merge exception + the `/loop`-vs-`CronCreate`
reconciliation. This skill has no pinned `file:line` code
citations of its own to drift — it indexes the library, which DOES carry code citations;
re-verify the count by DERIVING it, never by trusting a stamped number:
`ls -1d .claude/skills/tensor-grep-*/ | wc -l` for the `tensor-grep-*` folders, plus the one
`code-search-and-retrieval-reference` folder; the bare `tensor-grep` usage skill is EXCLUDED by
definition (usage docs for the tool itself, not a library entry). Receipt (recorded once, do not
re-stamp): the figure was 26/27 in older passes and went stale three times (20→26→27); derived
**34** at v1.110.14 (2026-08-12). The count is
ALSO pinned by `tests/unit/test_skill_index_sync.py`, but that gate compares the NAME SET against
AGENTS.md/CLAUDE.md and does NOT read this number -- so a stale figure here passes CI. Re-run the
derive command, do not trust any stamp. Process
receipts dated 2026-07-08 (WIP CAP, adversarial security gate, resume-from-transcript, don't-kill-
on-staleness, harvest pattern, self-firing drain-cron) come from the same session's `session_learnings`
ledger — treat them as durable orchestration discipline, not code facts that can be grep-verified.

**Re-verified 2026-07-23 against v1.95.0** (`git cat-file blob origin/main:pyproject.toml` →
`version = "1.95.0"`). Findings: (1) the "Skill library" heading had drifted to say **"20 skills"**
while the table below it already listed 26 numbered rows and line 131-133 already said "#26... up
from #20" — a stale leftover from before the 2026-07-22 table growth that nobody updated in the same
pass; fixed the heading to **26**. (2) The skill-count table itself is unchanged and still accurate:
`git ls-tree -r --name-only origin/main -- .claude/skills/` returns the same 27 folders (25
`tensor-grep-*` + the bare `tensor-grep` usage row + `code-search-and-retrieval-reference`) as the
2026-07-22/v1.93.2 count; the Java/PHP language-registry work (`#725`/`#724`, merged into v1.94.0 and
v1.95.0) added no new skill directory. A candidate `tensor-grep-add-language` skill did **not** exist
as of v1.95.0 — **it does now** (verified 2026-07-27, `ls .claude/skills/tensor-grep-add-language`),
and it is row 27 above. Cite it freely; the v1.95.0 caveat is retained only to explain why an older
reader was told otherwise. (3) Added a `.claude/skill_rules.json` pointer to the "Also load" line —
the file exists on disk (confirmed via `git cat-file blob`) but wasn't referenced anywhere in this
skill; it's a harness auto-trigger config, distinct from both this table and the (still-absent)
`docs/skill_index.md`. (4) Fixed 3 stale `AGENTS.md:NNN` line citations that had drifted from unrelated
insertions elsewhere in that file (930 lines at v1.95.0) — content at each anchor is unchanged, only
the line number moved: the callers-blind-spot cite `:165`→`:412`, the IDF-blast-radius cite
`:168`→`:379`, and the high-memory/full-suite cite `:174`→`:385`. Re-grep the phrase (not the number)
before trusting any line cite into a fast-moving doc like `AGENTS.md` on a future pass.

**Re-verified 2026-07-24 against v1.98.3** — added the "verify in BOTH directions" addendum to the
Steward-cron section (AGENTS.md A26): the 2026-07-24 session found a presumed-dead cron was actually
still alive alongside its replacement, firing a stale instruction. This is the mirror case to the
already-documented "assumed alive, actually dead" direction (A25) — `CronList` and inspect every
returned entry after a restart, don't just re-arm and assume the old one is gone.

**Drift-gate pass (this pass): the three AGENTS.md line citations this skill fixed 2026-07-23 (`:165→:412`,
`:168→:379`, `:174→:385`) had ALL drifted again, by the same +102 lines each** (`:412→:514`,
`:379→:481`, `:385→:487` — confirmed by grepping the actual sentence, not by incrementing the old
number). This is the exact "five previous maintenance passes re-stamped these by hand, and every one
shipped anchors that were already wrong" pattern `AGENTS.md`'s own "Cite the SYMBOL, not the line"
section warns about, now observed a sixth time on this skill's own citations. Per that section's rule,
the three citations above were rewritten as `grep -n "<distinctive phrase>" AGENTS.md` instructions with
the was→now drift kept as the receipt, rather than re-stamped with a fourth hardcoded number that will
just as certainly go stale on the next `AGENTS.md` growth pass. Do not "fix" them back to bare
`AGENTS.md:NNN` citations.

## Tracker closeout (2026-08-06)

- Premise-check READY items before dispatch (**A75** / #935) — already-shipped work looks actionable.
- When implementation PRs merge, do not leave the row `IN_FLIGHT` “for next cycle” (**A72**). Same
  turn: `SHIPPED` + Closure PR + Merged SHA, or the CEO snapshot lies.
- Never put free-form bullets under `## Canonical status index` (**A71**).


## Retention (2026-08-15) — gate waivers vs build licenses

- **A117:** “Skip Fable” (or quota-blocked Fable) is not a product-build license. Explicit operator
  waiver covers the named docs/design packet after Sol exact-commit APPROVE; product still needs a
  deliberate go + TDD + A3 where security-class.
- **A122:** Enumerate unfinished rows with mixed dispositions (demand SATISFIED / design landed /
  build not started) — do not flatten to READY or SHIPPED.
- CEO packet template: what worked, ALL unfinished rows by bucket, research list, 5+ lessons since
  prior CEO update, then retain laws in AGENTS/CLAUDE/MEMORY/skills in the same change.

