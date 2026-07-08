---
name: tensor-grep-backlog-campaign
description: Use when asked to deep-dive, audit, fix, or drain tensor-grep backlog — OR investigate/rank next work and produce SPEC/TDD plans (docs/plans/requirements|design|tasks-*.md) without implementing. Triggers: "work the backlog", "what next", "investigate and plan", backlog-completion campaign. META-ORCHESTRATOR — 18-skill library. Semantic-search flagship: tensor-grep-semantic-search-campaign. Scale/hang campaign: tensor-grep-large-repo-scale-campaign. Load tensor-grep-change-control before edit.
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

Executive summary · evidence · skills used · tools (gaps only) · plan pointers · research · next action.

**CEO approval before:** merge during in-flight release, public PyPI claim comms, irreversible contract changes without pinned test updates.

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
10. **WIP CAP (2026-07-08 receipt)** — do NOT dispatch a new BUILD while **>5 PRs are undrained** OR the **main gate is red**. Generating fixes faster than the ~40–66 min/publish drain empties the queue produces "churning, not completing" (backlog stays constant-size while PRs pile up). Design fork: complete-then-start, not start-then-hope. A red main gate is a drop-everything hotfix that jumps the queue ahead of any new build. Check `gh pr list` count and `gh run list --branch main` conclusion before authorizing a new fan-out.
11. **Mandatory adversarial security gate before merge** — every security-class PR (`apply_policy` / `mcp_server` / `cpu_backend` / `index_lock`/`session_daemon` / auth / money / migration) gets an adversarial "try-to-BREAK-it, cite `file:line`, default FIX-FIRST-if-uncertain" review **before merge**, in addition to (not instead of) the mandatory `codex` gate below. This is not a rubber stamp: on the 2026-07-08 session it returned SHIP on 3 PRs and caught real issues on 2 — a symlink-follow RCE bypass (`.resolve()` followed the symlink; fixed with `os.path.abspath`) and a lock-release TOCTOU (accepted-as-documented after proving a heartbeat thread makes it unreachable). `codex` is the nominal 2nd-vendor tool but its WSL path is unreliable on this box — an Opus **Agent** subagent (`model: opus`) is the reliable substitute when `codex` is dead, not a reason to skip the gate. Verdict shape: `SHIP` | `FIX-FIRST(+file:line+repro+minimal-fix)`.

---

## Skill library — retiring-fellow taxonomy (18 skills, `.claude/skills/`)

**Ground-truth rule:** verify commands/paths against repo + `tg --help`; re-read each skill's "Provenance and maintenance" when drift suspected. **No skill routes around `tensor-grep-change-control`.**

| # | Skill | Load when… |
|---|---|---|
| **CORE** | | |
| 1 | `tensor-grep-change-control` | Any edit, merge, release, registration change |
| 2 | `tensor-grep-debugging-playbook` | Symptom triage — routing, bootstrap, dogfood crash |
| 3 | `tensor-grep-failure-archaeology` | Settled battles — don't re-fight reverted fixes |
| 4 | `tensor-grep-architecture-contract` | Front door, 4+2 registration, backend fail-closed |
| 5 | `code-search-and-retrieval-reference` | Domain theory (BM25, IDF, agent-capsule semantics) |
| 6 | `tensor-grep-config-and-flags` | Flags, env vars, adding a flag checklist |
| 7 | `tensor-grep-build-and-env` | maturin, uv, Windows/WSL toolchain |
| 8 | `tensor-grep-run-and-operate` | Running `tg`, install, upgrade, doctor |
| 9 | `tensor-grep-diagnostics-and-tooling` | `tg --profile`, benchmarks, measure don't guess |
| 10 | `tensor-grep-validation-and-qa` | CI gates, governance tests, dogfood harness |
| 11 | `tensor-grep-docs-and-writing` | AGENTS.md pins, changelog, release docs |
| 12 | `tensor-grep-release-and-positioning` | Push-race, semver, post-publish dogfood depth |
| **ADVANCED** | | |
| 13 | `tensor-grep-semantic-search-campaign` | **Flagship:** local hybrid semantic search / CPU moat program |
| 14 | `tensor-grep-benchmark-and-proof-toolkit` | Speed claims, benchmark artifacts |
| 15 | `tensor-grep-research-frontier` | SOTA gaps (GPU, LSP, semantic) |
| 16 | `tensor-grep-research-methodology` | Hypothesis → falsifiable milestone discipline |
| 17 | `tensor-grep-large-repo-scale-campaign` | Hang/scale-honesty: unscoped-search refusal, `--deadline` end-to-end, exit-2 partial contract |
| 18 | `tensor-grep-backlog-campaign` | This skill — the meta-orchestrator itself |
| — | `tensor-grep` + `REFERENCE.md` | **Using** `tg` to navigate any repo |

**This skill** = meta-orchestrator for generic backlog drain (skill #18 of the library it indexes).
**Semantic-search flagship → #13**, not here. **Scale/hang campaign → #17**, not here.

**Also load:** `tensor-grep` (usage), global `~/.claude/skills/` (`verify-plan-against-code`, `dogfood-the-shipped-artifact`, …). **NO `docs/skill_index.md`** — use `AGENTS.md` skills section + table above.

---

## Backlog + session continuity

- **`docs/BACKLOG.md`** — canonical task ledger: id, P0–P3, status, agent, description, linked PR.
  **Create it on session 0 if absent** (seed from memory anchor + `gh pr list` + session task store, then discard session store as SoT).
- **GitHub (`gh pr list`)** — PR source of truth for open/merged work.
- **Memory anchor** — on every backlog change, update via `MEMORY.md` / `~/.claude/projects/<slug>/memory/feedback_tensor_grep_backlog.md` with: P0 queue, in-flight PRs, last shipped tag, push-race waiter state, "resume here".
- **Restart order:** memory anchor → `docs/BACKLOG.md` → `docs/SESSION_HANDOFF.md` → `AGENTS.md` → GitHub. Never use the ephemeral session task store as source of truth.
- **CEO status** = BACKLOG top items + blockers + spend + next 3 actions.

**Steward cron (this repo):** `c56ce9a9` at :23 — "Backlog-completion campaign tick". Re-arm if missing.

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

### 5 — Pre-dispatch gate (`verify-plan-against-code`)
Adversarial seam verification (`file:line`). BLOCK build until clean.

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
  + semantic-release + publish-pypi). The ~6-min figure is only the semantic-release job in isolation
  — don't cadence the cron faster than the real window or every fire just re-observes "still in flight".
- Failed release **self-heals** on next push (tag-derived). Don't panic-rerun.
- Respect **Hard Rule 10 (WIP CAP)**: if >5 PRs are undrained or the main gate is red, the fire should
  refuse to dispatch a *new build* (merging the existing queue is still fine/expected).

### Verify in the REAL venv

Worktrees have no built `.venv` — agent "tests pass" is a hypothesis.

```powershell
uv run --no-sync ruff check .
uv run --no-sync ruff format --check --preview .
uv run --no-sync mypy src/tensor_grep          # src only, NOT tests
uv run --no-sync pytest tests/<targeted>.py    # scoped locally on this desktop
```

- **`uv run --no-sync` is mandatory** — plain `uv run` re-syncs away the `[dev]` tree-sitter tree.
- **`ruff format --preview` is a SEPARATE gate from `ruff check`** — check-only misses format CI (#424). Never pass `--preview` to `ruff check`. Bare `ruff format` without `--preview` reverts preview style.
- **Full pytest + Rust test/clippy matrix + benchmarks + release-asset builds → PR/main CI only** (`AGENTS.md:174` — high-memory; don't run full suite locally unless user explicitly approves).
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

- `tg callers` for callables; **grep / `tg scan`** for sets/decorators/dispatch tables (`callers` cannot see them — `AGENTS.md:165`).
- Change a pinned contract → update its governance test in the **same PR**.

### CLI hygiene

ASCII-only CLI output (emoji → cp1252 crash). `git commit -m` backticks → bash substitution; use `-F`/heredoc.

### Latency / ranking work

Profiler is the oracle: `tg … --profile` on the actual slow command before designing.

**IDF blast-radius (`AGENTS.md:168`):** BM25/IDF surfaces (`--rank`, agent-capsule, semantic search) are sensitive to corpus changes — adding query-adjacent terms lowers corpus-wide IDF and can silently flip rankings (invisible to call graph). Harden tie/marker detection for IDF shifts; **never relax a failing ranking test** (that masks real degradation). Tracked: capsule-hardening Task #4 (ledger B3).

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

## Sibling skills (detail in library table above)

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
`grep -n '^version = ' pyproject.toml`). This skill has no pinned `file:line` code citations of its
own to drift — it indexes the 18-skill library, which DOES carry code citations; re-verify the
count with `ls .claude/skills | grep -c '^tensor-grep-'` (expect the library table above minus the
non-`tensor-grep-*` entries) before trusting the "18 skills" stamp on a later session. Process
receipts dated 2026-07-08 (WIP CAP, adversarial security gate, resume-from-transcript, don't-kill-
on-staleness, harvest pattern, self-firing drain-cron) come from the same session's `session_learnings`
ledger — treat them as durable orchestration discipline, not code facts that can be grep-verified.
