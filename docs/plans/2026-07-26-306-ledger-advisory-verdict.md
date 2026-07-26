# Plan — #306: should the tg ledger stay advisory, or become enforced?

Status: DRAFT (awaiting thinktank review)
Author: backlog-steward session, 2026-07-26
Goal: #292 (trustworthy tg) / the CEO enterprise-readiness answer of 2026-07-26

## 1. Verdict up front

**Stay advisory. Do not build mandatory locking.** The research says the current design is *right*,
and that the gap the CEO update described ("the ledger is advisory, not enforced — it won't stop two
agents colliding, only warn") is **not a defect to fix but a design decision to defend and
strengthen**.

This inverts the naive plan. Writing it down matters: the obvious fix here is the wrong one, which
is exactly the "obvious fix is often wrong" law.

What *should* change is three cheap things: **claim expiry (TTL)**, **louder default visibility**,
and an **opt-in enforcement hook at the orchestration layer** — shipped default-OFF.

## 2. Why enforcement is the wrong answer

### 2.1 The design authorities explicitly rejected it

- **POSIX / Linux.** `flock`/`fcntl` locks are advisory *by design* — "not enforced and are useful
  only between cooperating processes." Linux's mandatory-locking implementation was officially
  documented as **unreliable** (subject to a check/write race), made optional in 4.5, and **removed
  entirely in 5.15+**. It was never part of POSIX.
  ([man7](https://man7.org/linux/man-pages/man2/fcntl_locking.2.html))
- **Google Chubby** — the most battle-tested lock service in existence — states it outright:
  *"We rejected mandatory locks, which make locked objects inaccessible to clients not holding their
  locks."* ([Chubby, OSDI'06](https://research.google.com/archive/chubby-osdi06.pdf))

### 2.2 tg is structurally unable to enforce

Enforcement requires the enforcer to see **every** write path to the resource. tg never can: a human
editor, a shell script, `git checkout`, or a different agent harness all write files without going
through tg. That is precisely the cooperating-processes situation advisory locking was designed for.

A lock tg cannot enforce is worse than no lock, because it *reads* as a guarantee.

### 2.3 Locking trades a cheap problem for an expensive one

A merge conflict is annoying and human/CI-resolvable. A stranded lock from a crashed or stalled
holder is a **liveness bug** that blocks everyone. And lease-based locks are not a safe escape:
Jepsen measured etcd 3.4.3 mutexes losing **~18% of acknowledged updates** under a 2s TTL with 5s
process pauses. An LLM agent turn routinely exceeds any TTL you would pick (model backoff, a long
build) — Kleppmann's GC-pause scenario at agent-turn granularity.
([Kleppmann](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html))

### 2.4 The controlled experiment says soft coordination actively *hurts*

This is the most on-point evidence found. CAID / OpenHands ablated coordination strategies:

| Strategy | Score (PaperBench) |
| --- | --- |
| Single agent (baseline) | 57.2% |
| **"Soft isolation"** — a manager instructing agents not to touch each other's files | **55.5%** ← *worse than one agent* |
| **git-worktree physical isolation** | **63.3%** |

Their conclusion: *"Isolation is not an engineering convenience. It is the prerequisite for
multi-agent collaboration to work at all."*
([OpenHands](https://www.openhands.dev/blog/asynchronous-software-engineering-agents),
[arXiv 2603.21489](https://arxiv.org/pdf/2603.21489))

A lock only stops the *symptom* (simultaneous write) while leaving agents reasoning over a workspace
whose state still shifts under them mid-task.

### 2.5 The whole field independently converged on isolation, not locks

OpenHands/CAID, Claude Code Agent Teams, Cursor background agents, Devin, and the Aider community all
land on **git worktrees / separate VMs**. Notably, Claude Code Agent Teams uses a file lock for
*exactly one* thing — the shared task-claim metadata — and worktrees for the actual file edits. That
is the correct scope for a lock, and it is the scope tg's ledger already occupies.

## 3. The problem is real, though — the collisions are measured

Advisory is right, but this is not "there's no problem":

- **AgenticFlict** (arXiv 2604.03551): 142,652 agent PRs across 59,412 repos → **27.67% merge-conflict
  rate** (15.24% Copilot → 31.85% Codex); 4.36 conflicted files / 540 conflicting lines per
  conflicting PR.
- **AIDev-pop** (arXiv 2607.04697): **79.4% of agent PRs are temporally co-active** with another;
  3-way merge replay of 747 pairs → **19.8% conflict for same-agent pairs, 41.7% cross-agent**
  (non-overlapping 95% CIs). ~42% of conflicts are structural (modify/delete, add/add), not line
  overlaps.

So the ledger earns its keep — as a **visibility and coordination layer**, not a gate. And the
~80%-of-co-active-pairs-never-conflict figure is the argument against blanket serialization: locking
everything forfeits real parallelism to prevent a probabilistic, resolvable cost.

## 4. What to actually build — REVISED after adversarial review

> **Review verdict: PROCEED_WITH_CHANGES.** The "stay advisory" conclusion survived. The work list
> did not. Two of my three proposals were wrong, and I verified both corrections against the tree
> before accepting them. Recording the errors rather than quietly deleting them — this plan fell
> into the exact staleness trap the enterprise scorecard warns about, one document later.

### ~~W1 — Claim TTL/expiry~~ — **ALREADY SHIPPED. Deleted.**

My premise ("a crashed agent's claim poisons the visibility layer indefinitely") was **false**.
Verified: `ledger_store.py:106` sets `_DEFAULT_TTL_SECONDS = 900`, overridable via `--ttl` /
`TG_LEDGER_CLAIM_TTL_SECONDS`. `docs/CONTRACTS.md:223` documents the whole behaviour, including that
a record with a missing or unparseable `expires_at` is treated as **already-expired, not immortal**,
and that expiry prunes lazily on the next write while a pure `list` never rewrites the index.
`ledger_store.py:8-9` states it outright: *"A dead agent's claim simply TTL-expires, so
crash-semantics need no special handling."*

The 900s default is inside the band I proposed. My cited "community converges on 5–10 min" was also
weaker than the in-repo survey at `.claude/skills/tensor-grep-ledger/SKILL.md:60-63`, which records
comparators at 1800s and 120s — i.e. outside my stated band.

**Replacement (the thing actually missing): claim RENEWAL / heartbeat**, named as not-built at
`docs/CONTRACTS.md:225`. This dissolves the Kleppmann-pause worry without changing any semantics: an
advisory claim that can be extended never silently lapses mid-turn, and nothing blocks on it either
way.

### W2 — Surface overlaps on the READ path (re-scoped; my version named the wrong gap)

The hook already exists; the defect is narrower and sharper than I described. `main.py:10692-10697`
gives `tg prepare` a default `claim_hook` of `{command, argv, submitted: False, advisory: True}` —
**no overlap data at all** — and `main.py:10698-10718` populates `overlaps` *only* inside `if claim:`,
from `submit_claim`'s return.

**So today, to learn who else holds a claim through `prepare`, you must write one.** That read/write
asymmetry is the real defect.

**Hard constraint:** `tests/integration/test_prepare_oneshot_cuj.py:265-278` pins *"prepare without
`--claim` must not write the ledger."* W2 must therefore be a **read-only** `list_claims` /
`_find_overlaps` surface. A builder reaching for `submit_claim` here breaks a pinned test.

**Caveat to ship with it:** `_find_overlaps` (`ledger_store.py:546-573`) intersects `symbols ∩
symbols` and `files ∩ files` only — never symbol→file. Agent A claiming `--symbol foo` and agent B
claiming `--files a.py` (where `foo` lives in `a.py`) produce **no overlap**. Volunteering "no
overlaps" by default would turn that blind spot into a confident false negative. Either resolve
symbols to files before intersecting, or state the limitation in the payload. Silence must not read
as a guarantee — that is the whole point of #292.

### ~~W3 — opt-in hook gate~~ — **DROPPED. It contradicts a shipped contract.**

`docs/CONTRACTS.md:225` states plainly: *"no enforcement mechanism of any kind — `tg ledger` reports;
it never blocks, queues, or serializes an actual edit."* Hard stop #7 says the same
(`enterprise-agent/SKILL.md:37`), and the ledger skill calls treating a claim as a lock *"a misuse of
the contract, not a bug in it"*.

W3 as written also contradicted **W4 in this same document** — one section proposing to deny edits
while the next proposed reinforcing the rule against denying them. That is the kind of internal
contradiction a plan should never survive with.

If any form is ever revived it must **disclose, never deny**, and would be a contract change
requiring validator-test updates in the same PR.

### W4 — Position it honestly in the docs (unchanged)

The ledger is a *complement* to worktree isolation, never a substitute. Hard stop #7 should carry the
reasoning from §2 rather than standing as a bare rule.

Correction: the "council-verify → dry-run → conscious flag-flip" enablement rule I cited is the
*workspace* `CLAUDE.md`, not this repo's. The governing in-repo analogue is change-control §4
(`.claude/skills/tensor-grep-change-control/SKILL.md:66-72`).

## 4b. The finding the review surfaced — downgraded, but real

The review's headline blocking finding was that `undo_checkpoint` silently destroys a concurrent
agent's work. **I verified it and it is overstated.** #297's fix is present on both arms:
`checkpoint_store.py:1345-1357` routes every unlink through `_bytes_or_abort_undo`, which captures
the bytes or **aborts**, and `:1373-1381` does the same before every `shutil.copy2` overwrite,
pushing the prior content into `committed_overwrites` for the rollback handler. It is fail-closed,
not fail-open, and there is no unrecoverable loss of the kind described.

What *is* real, and narrower: **`undo_checkpoint` has no notion that a different agent changed a file
since the snapshot was taken.** Reverting the tree to the snapshot is exactly what undo is for, so
this is not a bug in undo — but in a multi-agent setting it silently reverts work undo never knew
existed. That is a *disclosure* gap, not a locking one, and it sits squarely in #292: the fix is for
undo to detect and report divergence from the snapshot, not to take a lock. Filed separately.

This is also the reason review findings are hypotheses until cited-checked — the recommendation
("add a staleness check at tg's own write path") is right; the stated mechanism was not.

## 5. What NOT to build (explicit)

| Do not build | Why |
| --- | --- |
| Mandatory/OS-level file locking | POSIX declined it; Linux removed it in 5.15+ as unreliable |
| Redlock-style quorum locking | No multi-node partition exists on one machine; "neither fish nor fowl" |
| A lease lock with no fencing check at the write path | Jepsen: 18% lost updates under lease-only mutexes |
| OT/CRDT merging of agent edits | Wrong granularity — a source file is not well-formed under arbitrary interleaving; no keystroke stream exists here |
| "Soft isolation" (prompt-level instructions) as a scaling strategy | Measured *below* the single-agent baseline |
| Dropping the ledger once worktrees exist | Worktrees solve simultaneous overwrite, not merge-time conflict — the ledger still adds value |

## 6. Verification

- W1: bidirectional — a claim older than TTL must be absent from `list`; a fresh one must be present.
  If both arms show the same thing, the fixture isn't biting (clock granularity).
- W2: assert `tg prepare` surfaces a live foreign claim **and** stays silent when none exists. The
  silent arm is the control; without it the test cannot fail.
- W3: default-OFF must be *proven* off — assert the hook does nothing until explicitly enabled.
- Concurrency tests must assert the **blocking contract**, never wall-clock overlap. Two independent
  locks are only guaranteed not to *block* each other, never to be simultaneously *held* — this
  exact mistake red-ed main on 2026-07-26.

## 7. Open questions for thinktank

1. Is W3 worth building at all, or does it re-introduce the failure mode CAID measured (soft
   coordination on a shared workspace) in hook clothing?
2. TTL default — 5, 10, or 30 minutes? An agent turn that exceeds it silently loses its claim, which
   is the Kleppmann pause scenario in miniature.
3. Should `tg prepare` **refuse** (ask_user) on a live foreign claim, or merely report it? Refusing
   edges toward enforcement without the safety machinery.
4. Does surfacing claims automatically leak agent identity/activity in a way that matters for a
   multi-tenant enterprise?
