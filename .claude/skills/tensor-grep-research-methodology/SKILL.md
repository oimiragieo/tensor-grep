---
name: tensor-grep-research-methodology
description: Use when you have a hunch, a candidate optimization, a novel technique, or an "I think X is faster/better/possible" idea for tensor-grep and must turn it into an ACCEPTED result or a DOCUMENTED RETIREMENT — the research discipline. Covers the evidence bar (one mechanism must explain every observation INCLUDING the negatives, and survive an assigned adversarial refutation whose every claim cites file:line), predicting the number + noise band BEFORE you run, the idea lifecycle (experimental default-OFF -> dogfood/benchmark -> council-verify -> conscious flag-flip to adopted OR retirement recorded in docs/PAPER.md so it is never retried), verifying an AI-drafted plan against real code, and where good ideas actually come from. The scientific-method META sibling to change-control (gates) and research-frontier (targets).
---

# tensor-grep research methodology

This is the **how-do-we-know-it-is-true runbook** — the scientific method for `tensor-grep`. It answers a
different question than its siblings: not *"is this change allowed?"* (that is `tensor-grep-change-control`)
and not *"what should we work on?"* (that is `tensor-grep-research-frontier`), but **"what has to be true
before a hunch is allowed to be called a result — and what do we do with the hunch when it loses?"**

`tensor-grep` is described in its own docs as a **benchmark-governed, contract-heavy codebase** where you
**do not optimize by guesswork** (`AGENTS.md:15`). The whole product wedge is trustworthy context for an
agent — "the product wedge is **not** 'faster grep'" (`AGENTS.md:177`) — so a result that *looks* right but
was never actually proven is not a small sin here; it is the exact failure the product exists to prevent.
This skill is the discipline that keeps hunches honest.

## Who this is for

Two readers at once — write and act to the **lower bound** of each:

- A **Sonnet-class AI** in a cheap autonomous session: you need the hard gates and copy-pasteable checks so
  you cannot rationalize noise into a win or ship your first-guess mechanism.
- A **mid-level human engineer** with zero repo context: you need the *why* — the theory of evidence — so
  you apply the bar to a new idea you have never seen before.

## When to use this skill vs a sibling

| Your situation | Use |
|---|---|
| You have an idea/hunch/candidate and must prove or retire it (this whole flow) | **this skill** |
| "Is this change *allowed* to land? which GATE applies?" (registration, fail-closed, push-race, TDD) | `tensor-grep-change-control` |
| "What are the worthwhile TARGETS / open research questions to pursue?" | `tensor-grep-research-frontier` |
| "Has this idea already been TRIED and lost?" (settled battles) | `tensor-grep-failure-archaeology` |
| Actually running a benchmark / reading `check_regression.py` / launcher attribution | `tensor-grep-benchmark-and-proof-toolkit` |
| A specific live bug / red CI / hang to diagnose | `tensor-grep-debugging-playbook` |
| The eval/oracle/grader version (a whole lane fails, a grader passes an empty answer) | `trustworthy-cuj-scoring` |
| The internals/contracts an experiment must respect (front door, fail-closed backend) | `tensor-grep-architecture-contract` |
| How a semantic-search experiment specifically is scoped/run | `tensor-grep-semantic-search-campaign` |

**This skill does not route around change-control.** It tells you when a result is *believable*; it never
tells you a change is *shippable*. A believable result still passes every gate in `tensor-grep-change-control`
(TDD-first, dogfood the real binary, one-merge-per-tick, autonomy stops at a draft PR). If this skill and
change-control ever seem to conflict, change-control wins — stop and reconcile.

---

## Part 1 — The evidence bar (what makes a result "accepted")

A result is **accepted** in this repo only when it clears all three tests below. Miss any one and what you
have is a **candidate**, not a result. Label it that way in the PR, the doc, and your own head.

### Test A — one mechanism must explain EVERY observation, including the negatives

State the single mechanism you believe is causing the effect ("Aho-Corasick single-pass beats N sequential
scans", "IDF weighting is missing so common terms rank as high as rare ones"). Then list **all** the
observations it must account for — and deliberately include the ones that would *embarrass* it:

- **The negative controls.** A no-match is a real outcome your mechanism must survive, not an error to hide.
  GPU/search benchmarks treat `rg` exit code `1` with empty output as a **valid comparator outcome** when
  `tg` also returns zero matches (`AGENTS.md:173`) — a mechanism that only "works" on matching inputs and
  silently mis-handles the empty case has not been proven, it has been cherry-picked.
- **The edge/adversarial inputs.** CRLF, invalid UTF-8, BOM, binary/NUL-byte files, multiline. `rg`'s
  default engine matches invalid UTF-8 but **PCRE2 requires valid UTF-8 and transcodes** — so a mechanism
  that swaps engines changes *results*, not just speed (`AGENTS.md` Backend Fail-Closed Contract, `:233-244`).
- **The disconfirming measurement.** If your mechanism predicts a win and the fair measurement shows a
  loss, the mechanism is wrong (or incomplete) — you do not get to keep the mechanism and blame the ruler.

**Worked failure — the IDF ranking flip (a mechanism that did NOT explain everything).** A change that
added *zero* ranking terms silently flipped the agent capsule from "tie, ask the user" to "confidently pick
a marker no-op." The first-guess mechanism — "IDF weighting is missing" — was **disproven** by a thinktank,
and the minimal "just widen the candidate pool" fix was **proven insufficient** (`tensor-grep-failure-archaeology`
Battle 7). The real mechanism was a *stack*: a flat no-IDF scorer **+** a hard top-5 candidate cap **+** a
`file_score` flip **+** an alphabetical path tie-break. Lesson: your first single-cause story usually fails
to explain one of the observations. Keep pulling until one mechanism covers them all — and note that the
repo's rule for that surface is to **harden the tie/marker detection to be robust, not relax the failing
test**, because relaxing masks the real degradation (`AGENTS.md:185`).

### Test B — the hypothesis must PREDICT THE NUMBER (and the noise band) before you run

Before you run the benchmark or the eval, **write down the number you expect and the direction** — e.g.
"this should cut cold `tg` search from ~0.26s to ~0.20s, and the run-to-run jitter here is ~5ms so anything
under a ~20ms delta is noise." Committing to the prediction first is what stops the two classic
self-deceptions: (1) rationalizing scheduler jitter into a "win" after the fact, and (2) moving the goalpost
to whatever the run happened to produce.

This is not optional ceremony; it is forced by how measurement works here. Sub-10ms timings are dominated by
process-spawn and OS-scheduler jitter, not by your code — the benchmark harness enforces a `min-baseline-time-s`
floor and an absolute-jitter tolerance for exactly this reason (mechanics + constants in
`tensor-grep-benchmark-and-proof-toolkit`). If your predicted effect is **smaller than the noise band**, the
experiment cannot detect it *no matter what it prints* — redesign it (bigger corpus, more repetitions, a
regime where the effect is larger) before spending a run. For the eval/pass-rate version of this rule —
pre-state the SNR / noise floor before claiming any fix "improved" a score — load `trustworthy-cuj-scoring`
and `noise-floor-before-quantitative-claims`.

> The house rule this enforces (`tensor-grep-change-control` Part 1, rule 3): **no speed/improvement claim
> without a measured number vs the accepted baseline.** Predicting the number first just means you decided
> what would count as success *before* you were tempted by the result.

### Test C — the result must survive an ASSIGNED adversarial refutation, every claim citing file:line

A result you graded yourself is a hypothesis. The bar is that it survives a **distinct, adversarial** pass
whose job is to break it — and in this repo that pass has a hard evidentiary rule:

> **A finding or claim with no `file:line` citation is DISCARDED** (`AGENTS.md:231`).

Two named passes exist, and they are different stages, not one:

1. **Pre-build planning review / council** — before you implement, an independent review cites `file:line`
   for every seam claim in the plan; uncited claims are hypotheses, not facts (`AGENTS.md:227,:271`). A
   citation-enforced review of this kind **caught 5 blockers in two unverified plans in a single session**.
2. **Post-build adversarial audit** — a mandatory, *separately named* stage that adversarially reviews the
   integrated diff, re-audit -> fix-wave -> re-audit **until ZERO must-fix findings remain**; that
   zero-finding state is the convergence gate before a draft PR (`AGENTS.md:231,:295`). This stage once
   **caught a HIGH CUDA-fork hazard that 203 passing tests missed** — which is the whole point: green tests
   are not the adversary; a hostile reader with citations is.

When the `codex`/`gemini` council CLIs are unavailable, run the council as a `Workflow` of Claude lenses with
the same citation enforcement (`use-thinktank` fallback). The mechanism you claim must survive *someone
actively trying to show it is wrong or off-strategy*, not just survive not being examined.

**Worked failure — the fair-baseline refutation.** "Aho-Corasick single-pass beats N sequential scans" is
true against the wrong comparator (N separate `rg` process spawns) and **false** against the right one: `rg`
has its *own* batched primitive, `rg -F -e pat1 -e pat2 ...`, and against that the batched `tg` route was
~2.3x slower (`tensor-grep-benchmark-and-proof-toolkit` worked example; `AGENTS.md:174` names the fair
baseline). The mechanism did not survive an adversary who insisted on the comparator's own batched form. The
repo kept the (correct) code but **marked the row diagnostic, not release-gating** — see Part 5's retirement
discipline.

---

## Part 2 — Verify an AI-drafted plan against the real code (before you build)

Most ideas now arrive as an AI/subagent-drafted plan. Treat **every factual claim in that plan as a
hypothesis until it cites a `file:line` that actually resolves** (`AGENTS.md:223-231`). AI plans have a
consistent failure mode: plausible-sounding edit locations that do not match the real structure — dead code
paths, renamed symbols, already-fixed lines. Reading the real files before you implement is not overhead; it
is the gate that prevents wasted cycles.

Two research-specific traps, both hard-won here:

- **Never trust a self-report.** A subagent's "tests pass" / "N green" is a hypothesis until *external state*
  confirms it — an exit code, a real-binary dogfood, or a citation that resolves. Re-run any validation a
  subagent claims to have passed; worktree fan-out branches have no `.venv`, so their "tests pass" is
  literally un-runnable in their own tree (`AGENTS.md:229`; `tensor-grep-change-control` Part 1).
- **Green ≠ working when the test never touches the real boundary.** Mock-based FFI tests were green while
  the real PyO3 bridge was **dead** and dropped every forwarded flag (`tensor-grep-failure-archaeology`
  Battle 8). Prove an FFI/bridge mechanism with a **live call into the built extension** (`maturin develop`,
  then confirm the flag actually reached `rg`), and prove generated/detached code by **executing** it
  (`compile()` + `exec()` the string and assert the behavior, e.g. the checksum gate fires *before*
  `os.replace`), not by reading substrings (`AGENTS.md:229`).

The step-by-step of this pass and its citation rules live in the global skill `verify-plan-against-code` and
in `tensor-grep-change-control` Part 6 — this skill only tells you *why* it is part of the evidence bar: an
unverified plan is an unfalsified hypothesis wearing a to-do list.

---

## Part 3 — The idea lifecycle (default-OFF -> proven -> adopted, OR retired-in-writing)

Every non-trivial idea rides one track. It ends in exactly one of two terminal states — **adopted** or
**retired** — and *both* are written down. An idea that is neither adopted nor retired is a landmine: the
next agent re-discovers it and re-loses the same day.

| Stage | What happens | Gate to advance | Owner skill for the mechanics |
|---|---|---|---|
| 1. **Hypothesis** | State the mechanism (Test A) + the predicted number & noise band (Test B). | Plan verified against real code (Part 2). | this skill + `verify-plan-against-code` |
| 2. **Experimental / default-OFF** | Build behind a flag or an opt-in path; **ships default-OFF**. GPU, LSP, semantic, provider-`classify` all live here. | Behavior-change starts with a **failing test**; the flag defaults off. | `tensor-grep-change-control` (Part 1, rule 4) |
| 3. **Dogfood / benchmark** | Prove it on the **real published binary** and/or the **right benchmark** vs the accepted baseline. | Passes the measured bar you predicted; artifact carries launcher mode/kind; no stale in-tree binary. | `dogfood-the-shipped-artifact`, `tensor-grep-benchmark-and-proof-toolkit` |
| 4. **Council-verify** | Pre-build council + **post-build adversarial audit** (Test C), re-audit until zero must-fix findings. | Zero uncited/unresolved must-fix findings. | this skill (Test C) + `use-thinktank` |
| 5a. **Adopted (conscious flag-flip)** | A **human** flips the default on — never an agent, never auto-merge. Endpoint of any autonomous fan-out is a **draft PR**. | The flip is a deliberate act after 3+4, not a side effect of a merge. | `tensor-grep-change-control` (Part 1, rule 1) |
| 5b. **Retired (written down)** | The idea lost (regressed, or the gain was not stable enough to justify merge). **Record the attempt in `docs/PAPER.md`** so no future agent retries it. | The dead end is in the ledger with the number that killed it. | this skill (Part 5) |

Experimental-until-proven is a hard rule, not a preference: **GPU, LSP, semantic-search, and
provider-`classify` (`cybert`) stay default-OFF and labeled experimental until correctness AND speed AND UX
are all proven** — never market an unproven wedge (`tensor-grep-change-control` Part 1, rule 4; GPU is
currently *slower* than CPU and its P1 CUDA kernel is **paused**, `AGENTS.md:245-253`). "Experimental" is a
lifecycle stage with an exit gate, not a permanent excuse.

### The instrumented-build-gate fork (C12, added 2026-07-08) — for a speculative idea with appeal but no demand proof

Some ideas fail Test B differently than a mis-measured speed claim: the mechanism is plausible, the
build is cheap enough, but there is **no evidence anyone actually needs it** — a hunch about future
value, not a validated user ask (contrast with "local hybrid semantic search," which *is* a validated
#1 ask, `AGENTS.md:249`). Forcing a straight build-vs-drop choice on that kind of idea is a false
binary. Load the global skill **`instrumented-build-gate`** (folds into this stage of the lifecycle,
does not replace it) for the discipline: (1) **three** options, not two — build-now / do-nothing /
document-the-already-shipped-adjacent-value-and-instrument-to-measure; (2) capture any already-shipped
adjacent value as a near-$0 docs-only PR immediately, regardless of which fork you take; (3) if you
instrument, the probe must be minimal and safe — fail-open (a metrics/telemetry seam must never break
serving), PII-free (hash, never store raw user text), bounded (day-bucketed, LRU-capped, single-writer
atomic write), and behind a kill-switch, and it should extend an existing diagnostic surface (`doctor`
/ `status`) rather than register a brand-new command/flag (sidesteps the 4-registration-site hazard
entirely); (4) **write the time-boxed numeric threshold BEFORE the measurement window opens**, and do
not renegotiate it after seeing partial data — that is Test B's "predict the number first" rule applied
to a demand signal instead of a speed number; (5) a **failed** gate is still a **win**: it is a
document-only decision earned for near-$0, and it is written down like any other retirement (Part 5).

**Worked example (Problem 4d of `tensor-grep-research-frontier`, 2026-07-08).** A proposal to build a
local multi-agent "ledger" on top of `tg`'s session/daemon plane hit exactly this fork: the mechanism
was plausible (a repo-scoped session store + a warm loopback daemon already let one agent reuse
another's work) but there was no measured evidence of real concurrent-agent demand on this repo. The
three-way fork resolved it as DOCUMENT-NOW-BUILD-LATER-ON-GATE: ship a docs-only positioning page
(`docs/multi_agent_context_plane.md`) for free, plus a small opt-out demand-instrumentation patch on
the session daemon (concurrent-distinct-client counter + repeat-expensive-artifact counter, both
fail-open/PII-free/hashed, read back via existing `tg session daemon status` / `tg doctor` surfaces)
that earns or fails a pre-stated 2-week numeric gate before any ledger/claims layer is authorized. As
of this writing that build (`#456`) is **open, not merged to `main`** — cite it as an in-flight design
pattern, not a shipped result (`git log --oneline origin/main | head` to re-check).

### Retirement is a deliverable — the ledger

`docs/PAPER.md` is the repo's **research notebook**, and §3.10 "Optimization Ledger: Accepted Wins and
Rejected Dead Ends" exists for one reason it states outright: *"To avoid re-running the same failed ideas."*
When a candidate loses, you are not done until it is in that ledger. Real entries already there — treat them
as the format to copy:

- **PyO3 for directory walking** — expected an "astronomical speedup"; measured **48.818s (Rust PyO3
  `ignore` ext) vs 39.892s (pure Python `os.walk`)**; the FFI boundary (per-path GIL serialization) is the
  bottleneck, not the walk. Reverted; do not re-propose (`docs/PAPER.md` §3.7; `tensor-grep-failure-archaeology`
  Battle 1).
- **Onefile Nuitka binary as a speed path** — built `tg.exe` ~1.10–1.22s vs Python bootstrap ~0.33–0.48s;
  onefile extraction overhead dominates. "Not the current path to parity with raw `rg`" (`docs/PAPER.md`
  §3.10, and the note at §3.10's "Current honest state").
- **Native positional in-place `search --replace`** — retired because it violated the stable search contract
  by mutating files from a search-style flag; file edits stay on `run --rewrite ... --apply` (`docs/PAPER.md`
  §3.8). A correctness/strategy retirement, not a speed one.
- A whole list of **"correct but slower"** posting/decode/cache micro-optimizations that "looked
  mathematically plausible but lost empirically" (`docs/PAPER.md` §3.10 "Important rejected candidates").

The discipline is blunt (`tensor-grep-change-control` Part 1, rule 3): **if a candidate is correct but
slower, revert it and record the attempt in `docs/PAPER.md`.** Do not ship a clean regression because "the
code is nicer," and do not leave the dead end unwritten.

---

## Part 4 — Where good ideas actually come from (so you can go get more)

Good candidates here have not come from armchair invention; they have come from three repeatable sources.
When you need a *new* idea (that is the `tensor-grep-research-frontier` job), mine these — and when you
evaluate an incoming idea, weight it by which source it came from.

| Source | What it is | Repo receipts | How to mine it |
|---|---|---|---|
| **Dogfood** | Using the real `tg` binary on real work surfaces the highest-signal gaps. | `tg registration-check` is on the roadmap as a **"real-use-validated agent-native differentiator"** (`AGENTS.md:250`); the `scripts/dogfood/` harness has repeatedly caught contract bugs `CliRunner` could not see. | Run the shipped binary on a real task (`dogfood-the-shipped-artifact`); log every friction point. |
| **Competitive analysis** | Reading what peers/tools do and stealing the *idea* (not the code) with correct licensing. | The #1 roadmap item cites a concrete reference architecture — MinishLab **`Semble`** (tree-sitter chunking + `potion-code-16M` Model2Vec + BM25 + RRF, CPU-only, MIT) (`AGENTS.md:249`); `docs/PAPER.md` benchmarks `tg` against Aider/Cody/Cursor-class peers and Gemini/Copilot. | Structured web research (`use-exa`); produce a "steal-list" of ideas with license notes; ideas are free, code import needs the upstream notice. |
| **Audits** | A tiered adversarial read of already-committed code finds bugs no one re-verified. | The **Security Hardening (Round-3)** patterns are literally an **audit lens** — sweep targets to check proactively because the bugs lived in committed code where no one re-checked (`AGENTS.md:255-262`). | Run `codebase-audit` / `omega-deep-dive-bughunt`; every finding cites `file:line` or is discarded. |

**Worked example (C14) — a token-economy benchmark, not a speed benchmark, surfaced a moat gap.**
The Dogfood row above is usually read as "run the binary and look for friction"; the 2026-07-08
receipt shows a *benchmark* can be the dogfood instrument too, just measuring a different axis than
wall-clock. The first oracle-validated tokens-per-correct-answer run (Sverklo `bench:primitives`,
`express@4.21.1`, 25 tasks) found `tg` **7.5x better than grep on definition-lookup** — the expected
moat win — but roughly an **order of magnitude worse on file-dependency questions** ("what does file
X import"), because `tg` has no scoped file-dependency primitive and an agent pays a whole-repo
`tg map` to answer a single-file question. This is Test A working as designed: the mechanism ("the
context moat wins on token economy") predicted a win and a real measurement produced a *disconfirming*
result on one task class — which is not a refutation of the moat thesis, it is a **scoped gap** inside
it (`tensor-grep-research-frontier` Problem 4b's C14 sub-item, `#74`). Re-verify the exact multiplier
before citing it: the committed-artifact number and the informal receipt in memory
(`tensor-grep-benchmark-proofpoint-2026-07-08`) had not yet been reconciled as of this writing — see
`docs/benchmarks.md` and `tensor-grep-benchmark-and-proof-toolkit` before quoting a precise figure.

Two guardrails on idea *selection* (the frontier owns the full target list; this is just the methodology):

- **Weight ideas by the strategy, not by novelty.** Raw search speed is the **parity tier**; the moat is the
  **agent-native context layer** (`orient` / `callers` / blast-radius / the token-efficient capsule)
  (`AGENTS.md:177,:245-253`). A clever idea that only makes cold grep marginally faster is off-strategy even
  if it works.
- **A validated user ask outranks a speculative one.** "Local hybrid semantic search" is the top roadmap
  item because it is the **#1 validated user ask and the biggest competitive gap** (`AGENTS.md:249`), not
  because it is the most novel.

---

## Pre-acceptance checklist (run before you call a hunch a "result")

- [ ] **One mechanism** stated, and it explains **every** observation — including the negative controls
      (no-match), the edge inputs (CRLF/UTF-8/BOM/binary/multiline), and any disconfirming measurement.
- [ ] The **number and noise band were predicted BEFORE the run**; the predicted effect is **bigger than the
      noise floor** (else the experiment cannot detect it — redesign).
- [ ] Measured against the **accepted baseline** with the **right benchmark**, on the **real binary** (no
      stale in-tree binary, launcher `command_kind == native_exe` or explicitly disclosed) — mechanics in
      `tensor-grep-benchmark-and-proof-toolkit`.
- [ ] For any batched/amortized claim, compared against the **comparator's own batched primitive**
      (`rg -F -e ...`), not a sequential-loop strawman.
- [ ] Survived a **post-build adversarial audit**: re-audit -> fix-wave -> re-audit to **zero must-fix
      findings**, every surviving claim citing a **`file:line` that resolves** (uncited = discarded).
- [ ] The plan behind it was **verified against real code**; no subagent self-report was trusted un-re-run;
      any FFI/bridge proven with a **live extension call**, any generated code proven by **executing** it.
- [ ] Terminal state chosen and **written down**: adopted via a **conscious human flag-flip** (endpoint =
      draft PR), OR retired with the killing number recorded in **`docs/PAPER.md`** so it is never retried.
- [ ] Nothing here skipped a gate in `tensor-grep-change-control` (TDD-first, dogfood, one-merge-per-tick,
      no auto/admin-merge).

If you cannot tick a box, you have a **candidate**, not a result. Say "candidate" out loud.

---

## Provenance and maintenance

Volatile facts were originally dated **2026-07-02, release `v1.17.25`**; AGENTS.md citations
re-grepped and re-anchored **2026-07-08 against `v1.49.3`** (AGENTS.md gained a new orchestration
section that shifted most downstream line numbers by roughly +18/+19 — **do not trust a stale
number, re-grep it**; the table below carries the numbers verified at v1.49.3). Re-verify before
relying on any of them — a wrong methodology runbook lets a bad result through, which is worse than
none.

| Claim | Re-verify command |
|---|---|
| Current release tag | `grep -n release_docs_current_tag AGENTS.md` (currently `v1.49.3`) |
| "Benchmark-governed, do not optimize by guesswork" | `grep -n "benchmark-governed" AGENTS.md` (`:15`, unchanged) |
| Product wedge is not "faster grep" | `grep -n "not \"faster grep\"\|agentic code-intelligence" AGENTS.md` (`:177`) |
| Verify-plan + adversarial-audit + "no citation is DISCARDED" | `grep -n "DISCARDED\|ADVERSARIAL AUDIT\|caught 5 blockers\|CUDA-fork hazard" AGENTS.md` (`:227,:229,:231,:271,:295`) |
| Backend fail-closed / PCRE2-changes-results | `grep -n "Backend Fail-Closed\|BackendExecutionError" AGENTS.md`; `grep -n "class BackendExecutionError" src/tensor_grep/backends/base.py` (AGENTS.md block now `:233-244`) |
| No-match is a valid comparator outcome; fair many-fixed-strings baseline | `grep -n "no-match as a real comparator\|many fixed strings" AGENTS.md` (`:173,:174`) |
| Ranking flip: harden, don't relax the test | `grep -n "IDF blast-radius\|robust to IDF shifts" AGENTS.md` (`:185`) |
| Roadmap sequencing (3 CPU wins; GPU paused; Semble; #1 user ask; registration-check) | `grep -n "Roadmap Sequencing" -A 12 AGENTS.md` (`:245-253`; Semble item `:249`, registration-check item `:250`) |
| Security round-3 as an audit lens | `grep -n "Security Hardening Patterns" -A 8 AGENTS.md` (`:255-262`) |
| Retirement ledger (accepted wins + rejected dead ends) | `grep -n "Optimization Ledger\|Important rejected candidates\|Why Pure Python Traversals" docs/PAPER.md` (§3.7, §3.8, §3.10) |
| Lifecycle gates (autonomy draft-PR-only, self-report, no-claim-without-numbers, experimental-until-proven) | Read `tensor-grep-change-control` Part 1 |
| Noise-floor / jitter constants for the predict-the-number rule | Read `tensor-grep-benchmark-and-proof-toolkit` "Noise-floor / jitter discipline" |
| Instrumented-build-gate discipline (Part 3, C12) | global skill `instrumented-build-gate`; worked example memory `tensor-grep-a2a-ledger-audit-2026-07-08` |

If any command above no longer matches, update this skill in the same change — and check whether the sibling
that *owns* the fact (change-control, benchmark-toolkit, failure-archaeology, research-frontier) needs the
same update. **Do not trust the numbers stamped here on a future session without re-running the greps** —
AGENTS.md line numbers have already drifted once this way (a new section inserted upstream silently
invalidated every downstream citation in this file simultaneously).
