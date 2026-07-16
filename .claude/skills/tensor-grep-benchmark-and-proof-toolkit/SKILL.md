---
name: tensor-grep-benchmark-and-proof-toolkit
description: Use when about to claim, review, or dispute a speedup/regression in tensor-grep (tg vs rg, hot-cache, AST, agent-workflow, or GPU changes) — which benchmark script to run, how to read check_regression.py, the noise-floor/absolute-jitter rule for sub-10ms rows, the fair-baseline rule (never compare tg against a strawman comparator), and the launcher-attribution rules (tg_launcher_mode, tg_launcher_command_kind, stale-binary refusal) that make a benchmark artifact claim-quality instead of noise.
---

# tensor-grep Benchmark and Proof Toolkit

Prove it, do not eyeball it. This repo is benchmark-governed (AGENTS.md: "treat the repo as a
benchmark-governed, contract-heavy codebase; do not optimize by guesswork"). This skill is the
runbook for producing a benchmark artifact that is actually trustworthy, picking the right script
for the change you made, and reading its output correctly.

## When NOT to use this skill (go to a sibling instead)

- Writing or reviewing the actual code change → `tensor-grep-change-control` (registration sites,
  backend fail-closed contract) or `verify-plan-against-code`.
- Chasing a bug/regression's root cause, not proving a speed number → `tensor-grep-debugging-playbook`
  or `superpowers:systematic-debugging`.
- Deciding whether to dogfood the shipped binary after a release → `dogfood-the-shipped-artifact`.
- General CLI usage (`tg search`, `tg orient`, flags) → `tensor-grep-config-and-flags` or the
  `tensor-grep` usage skill (`.claude/skills/tensor-grep/SKILL.md`).
- Researching a novel GPU/ML technique before building it → `tensor-grep-research-frontier`.
- You just need a code-search/navigation tool right now, not a benchmark → `tensor-grep` skill.

## The one rule everything else derives from

> Never claim a speedup (or accept a regression as fine) without a measured number from the
> **current accepted baseline**, run through the **right script**, read with **absolute-tolerance
> awareness**, produced by a **claim-safe launcher**. (AGENTS.md "Benchmark Rules" / "Performance
> Discipline", `docs/benchmarks.md` "Acceptance Rules")

Five corollaries, all stated in AGENTS.md:

1. Compare against the current accepted baseline, not memory.
2. Reject a candidate that is only "faster" in a microprofile while slower end-to-end.
3. Keep both cold-start and repeated-query measurements in mind — they are different regimes.
4. Do not update docs/PAPER.md with a speed claim until the benchmark line is **accepted**.
5. If a candidate is correct but slower, **revert it and record the attempt** — do not ship a clean
   regression because "the code is nicer."

## Decision table — which script for which change

| You changed... | Run this | Why |
|---|---|---|
| plain-search routing, launcher/startup path, control-plane dispatch | `benchmarks/run_benchmarks.py` | the main `tg` vs `rg` cold-path comparison |
| StringZilla index, CPU regex prefilter, persisted cache/decode/posting-list | `benchmarks/run_hot_query_benchmarks.py` | repeated-query / hot-cache regime, not cold start |
| native CPU large-file / many-file / fixed-multi-pattern route | `benchmarks/run_native_cpu_benchmarks.py` | isolates native CPU from Python front-door overhead |
| AST single-query matching (`tg run` vs `ast-grep`) | `benchmarks/run_ast_benchmarks.py` | AST single-query gate |
| `run` / `scan` / `test` startup, AST workflow batching/orchestration | `benchmarks/run_ast_workflow_benchmarks.py` | AST workflow startup, not single-query speed |
| `tg agent` capsule routing, confidence/alternatives, validation alignment, rollback/edit-order, whole edit loop | `benchmarks/run_agent_workflow_benchmarks.py` + `benchmarks/run_agent_success_harness.py` | product-wedge workflow evidence; **not** a cold exact-text speed claim |
| GPU / NLP backend (`--gpu-device-ids`, CyBERT) | `benchmarks/run_gpu_benchmarks.py` (Python sidecar scale/correctness) or `benchmarks/run_gpu_native_benchmarks.py` (native CUDA crossover) | GPU is experimental — see the GPU section below before trusting any GPU number |
| context-render / edit-plan latency | `benchmarks/run_context_render_benchmarks.py` | editor-plane latency, not search speed |
| blast-radius latency | `benchmarks/run_blast_radius_benchmarks.py` | impact-analysis latency |
| repo-map / retrieval quality (not speed) | `benchmarks/run_repo_retrieval_benchmarks.py` | recall/precision/MRR/nDCG/F1/token-budget, a quality metric, not a timing one |
| `tg find` / `tg_find` ranking, `TG_FIND_DENSE_WEIGHT`, RRF channels, chunker, late-rerank | `benchmarks/eval_late_rerank_quality.py` | quality gate (ndcg@10/recall@10) on the NL golden set + literal/identifier golden slices — NOT a speed benchmark; see `tensor-grep-semantic-search-campaign` STATUS UPDATE 2 |
| tokens-per-correct-answer / token-economy (the moat metric — CANDIDATE, not yet a committed script, gated on #72) | none committed yet — see "6. Token-economy" below | a task-level cost metric (tokens spent to reach a correct answer, oracle-validated), not a latency metric — do not conflate with any row above |

Full matrix with default artifact paths: `docs/benchmarks.md` § "Benchmark Matrix" (19 scripts as of
v1.49.3 — re-verify with the command in Provenance below, the list drifts).

If your change does not obviously map to one row, run `benchmarks/run_benchmarks.py` first (the
broadest cold-path net) and widen from there — do not invent a new ad hoc timing script.

## Recipe per method

### 1. End-to-end cold `tg` vs `rg` (the default)

```powershell
python benchmarks/run_benchmarks.py --output artifacts/bench_run_benchmarks.json
python benchmarks/check_regression.py --baseline auto --current artifacts/bench_run_benchmarks.json
```

`--baseline auto` resolves `benchmarks/baselines/run_benchmarks.<platform>.json` from the recorded
`environment.platform` (Windows → `run_benchmarks.windows.json`, Linux → `run_benchmarks.ubuntu.json`;
`check_regression.resolve_auto_baseline_path`, `benchmarks/check_regression.py:15-36`). It refuses
to run on an unsupported platform rather than silently comparing against nothing.

Useful flags on `run_benchmarks.py` (`benchmarks/run_benchmarks.py:675-736`):
- `--binary PATH` — pin the exact `tg` binary under test (default:
  `rust_core/target/release/tg[.exe]`).
- `--native` — force `tg search --cpu` and add the native large-file/many-file scenarios.
- `--launcher-mode {auto,explicit_binary,explicit_fast_binary,discovered_cli_binary,
  python_module_launcher,python_module_rust_first,...}` — pin the launcher shape for a
  control-plane experiment (see "Fair-benchmark rules" below — never compare across modes silently).
- `--allow-claim-unsafe-launcher` — bypass the stale-binary refusal for *exploratory* timing only;
  never use this on a run whose numbers you intend to put in docs/PAPER.md.

### 2. Hot / repeated-query cache paths

```powershell
python benchmarks/run_hot_query_benchmarks.py --output artifacts/bench_hot_query_benchmarks.json
```

Use for StringZilla index changes, CPU regex prefilter changes, persisted cache/decode/posting-list
changes. `repeated_regex_native` must stay on native/Rust routing (`cpu_rust_regex` or similar) —
if your probe forces a Python fallback you are benchmarking the wrong thing (AGENTS.md Benchmark
Rules). This script self-grades: it computes `improvement_pct` and a `status: PASS|FAIL` per row
against `--max-regression-pct` (default 5.0) — see the noise-floor section for why sub-10ms rows
need extra care here specifically.

### 3. AST single-query and AST-workflow

```powershell
python benchmarks/run_ast_benchmarks.py --output artifacts/bench_run_ast_benchmarks.json
python benchmarks/run_ast_workflow_benchmarks.py --output artifacts/bench_run_ast_workflow_benchmarks.json
```

The first is `tg run` vs `ast-grep` on one query (ratio gate: `tg/sg <= 1.1` per `docs/benchmarks.md`
§ "ast-grep vs tensor-grep AST mode"). The second is `run`/`scan`/`test` **startup and orchestration**
— use it when you touched AST workflow batching, not query matching itself.

### 4. Agent capsule / edit-loop workflow

```powershell
python benchmarks/run_agent_workflow_benchmarks.py --output artifacts/bench_agent_workflow.json
python benchmarks/run_agent_success_harness.py --output artifacts/bench_agent_success_harness.json
```

This is **workflow evidence, not a cold exact-text search speed claim** (docs/benchmarks.md is
explicit about this — the artifact literally embeds the string `"agent-native workflow benchmark;
not a cold exact-text speed claim"` as a positioning field). Use it for `tg agent` capsule routing,
confidence/alternative-target honesty, validation-command filtering, rollback visibility, edit-order
guidance, or whole-loop latency (`search_s`/`plan_s`/`apply_s`/`verify_s` medians). Do not use it to
argue `tg` beats `rg` — that claim needs script #1.

### 5. GPU / NLP backend

```powershell
python benchmarks/run_gpu_benchmarks.py --output artifacts/bench_run_gpu_benchmarks.json
```

Treat `SKIP` as expected infrastructure state, not a fake failure — CyBERT may skip when Triton is
unreachable, and the whole artifact reports top-level `status: "SKIP"` when no operational GPU
device is detected (`run_gpu_benchmarks.py:1525`, `benchmark_pattern`/`devices` still recorded so the
skip is diagnosable). **GPU is experimental and currently not a promotion-ready path** — read the
"GPU claims need a stricter bar" section below before trusting any GPU number as a win.

### 6. Token-economy / tokens-per-correct-answer (CANDIDATE — gated on #72, not yet a committed benchmark)

A **different metric class** from every row above: not wall-clock, but **tokens an agent must spend to
reach a verified-correct answer** (a task-level cost proxy, oracle-validated per task rather than a raw
F1/speed number). This is the metric the arxiv research landscape converged on as the actual moat
measure (`tensor-grep-arxiv-research-landscape-2026-07-07`: "token-economy > F1" consensus) — it is
what a fleet of agents actually pays for, not a lab F1 score.

**First real run (2026-07-08, internal, oracle-validated, NOT YET a committed `benchmarks/` script)**:
Sverklo `bench:primitives` (Zenodo 10.5281/zenodo.19802051) on `expressjs/express@4.21.1`, 25 tasks (10
definition-lookup P1, 10 references P2, 5 file-deps P4). Gated tokens-per-correct-answer (F1>=0.8):
**tg P1 = 1,243 tok vs rg = 9,328 tok -> tg 7.5x BETTER** (moat validated on definitions). **tg P4 file-deps
= 53,631 tok vs rg = 5,367 tok -> tg ~10x WORSE (at the time)** — tg had no scoped "what does file X
import / who imports X" primitive, only whole-repo `tg map`, so every P4 query paid the whole-repo
capsule cost regardless of the single file asked (task #74).

**P4 CLOSED AND RE-PROVEN (2026-07-16, `v1.76.12` #619).** `#460` (`05f49b8`) shipped the fix — scoped
`tg imports FILE` / `tg importers FILE [ROOT]` — and the SAME Sverklo P4 slice was re-run independently
(deterministic, $0, `scratchpad/bench/aggregate.py`): **53,631 tok -> 2,387 tok, ~10x WORSE -> ~2.24x
BETTER than rg**, F1 preserved and improved (0.542 -> 0.606), bidirectional-oracle PASSED 25/25. **The
moat is now proven on both P1 and P4.** Raw artifacts and scripts still live at `scratchpad/bench/`
(`results.json`, `run_bench.py`, `score.py`, `validate_oracle.py`, `aggregate.py`) — **still not
promoted to `benchmarks/` or `docs/benchmarks.md`**, so the harness-committal gap in the paragraph below
is unchanged even though the P4 number itself is now closed; full memory:
`tensor-grep-benchmark-proofpoint-2026-07-08` (original) + `tensor-grep-drain-resume-2026-07-12.md` /
`tensor-grep-find-campaign-2026-07-16.md` (re-proof receipts).

**Why this is gated, not accepted, and what #72 covers**: (a) the run above is one repo / one language
(JS) / 25 tasks — a real signal, not a general claim, and the full 180-task/6-repo suite (flask/fastapi
in Python plausibly show a bigger win) has not run; (b) the harness (`run_bench.py`/`score.py`/
`validate_oracle.py`) has not been committed to `benchmarks/`, given a `docs/benchmarks.md` Matrix row,
or wired into `check_regression.py`-style acceptance; (c) publishing this number externally is
CEO-gated (public positioning), separate from whether the harness itself is accepted internally. Do
**not** cite the 1,243/9,328, 53,631/5,367, or the re-proof 2,387 numbers above as an accepted
claim-quality artifact until #72 lands a committed script + `artifacts/bench_*.json` — treat them as a
research finding pointing at real moat validation on BOTH axes now (P1 definitions + P4 file-deps), not
yet a benchmark-governed line. Follow this skill's
same discipline once #72 ships: fair-baseline (`rg` is already the right comparator here), noise-floor
(deterministic CLIs, no run-to-run variance in this metric class so the usual jitter rule is moot, but
oracle correctness bugs are the equivalent failure mode — bidirectionally validate the oracle before
trusting a score), and no doc/PAPER.md claim until the artifact is accepted.

### 7. `tg find` retrieval-quality golden-set gates (SHIPPED, v1.77.0+, #189)

Unlike §6 (an uncommitted CANDIDATE), `benchmarks/eval_late_rerank_quality.py` is a **committed,
running** ndcg@10/recall@10 quality gate for `tg find` / `tg_find` ranking changes (dense weight, RRF
channels, chunker, late-rerank) — see the decision-table row above and
`tensor-grep-semantic-search-campaign` STATUS UPDATE 2. Two rigor requirements before trusting a gate
run here, both learned the hard way on this harness:

1. **Corpus-hardness gate.** Before trusting a "rrf beats bm25" delta, assert BM25-alone scores
   near-floor on the HARD subset of the golden set (the vocabulary-mismatch queries the dense leg is
   supposed to rescue) — the same GATE 0b trap as §Phase-0 baselines elsewhere in this repo's
   campaigns: an easy/keyword-discriminating corpus lets BM25 saturate at recall 1.0 and makes any
   fusion delta look meaningless by comparison, or conversely hides a real fusion win inside noise.
2. **Paired win/loss/tie report, not a bare mean.** A 40-query aggregate mean can hide a distribution
   where one lucky query drives the whole delta. Report per-query win/loss/tie counts (e.g. "positive
   in all 4 categories, essentially wins-or-ties per query, a single ndcg loss out of 40" — the actual
   `tg find` gate-run shape) before gating a ship decision on the mean alone. See the global skill
   `paired-test-power-discipline`.

Bidirectionally validate any new golden query the same way as elsewhere in this skill: a correct answer
must PASS the grader and a wrong/empty answer must FAIL it, before trusting a delta computed against it.

## Noise-floor / jitter discipline

Sub-10ms timings are dominated by process-spawn and OS-scheduler jitter, not by the code you changed.
Two concrete, code-verified mechanisms exist for this:

1. **`min-baseline-time-s` floor in `check_regression.py`** (default `0.1`s,
   `benchmarks/check_regression.py:69-74`; enforced in `perf_guard.check_regressions` and
   `detect_comparator_drift`, `src/tensor_grep/perf_guard.py:76,109`): any row whose **baseline**
   time is below this threshold is skipped entirely for regression comparison — "tiny baseline
   durations are noisy on shared CI runners and can trigger false positives from scheduler jitter."
2. **Absolute jitter tolerance in `run_hot_query_benchmarks.py`**
   (`NATIVE_REGEX_ABSOLUTE_JITTER_S = 0.005`, `benchmarks/run_hot_query_benchmarks.py:14`): the
   `repeated_regex_native` row's PASS/FAIL uses
   `max(relative_tolerance_s, absolute_tolerance_s)` — i.e. a **percentage-only** gate would flag a
   2ms → 4ms wobble as a "100% regression" when it is pure noise, so an absolute 5ms floor is added
   on top of the `--max-regression-pct` (default 5%) relative gate
   (`benchmarks/run_hot_query_benchmarks.py:202-226`).

**Rule of thumb when you add a new hot/cache benchmark row**: if the expected timing is under ~10ms,
do not gate on percentage delta alone — add an absolute-seconds floor the way
`NATIVE_REGEX_ABSOLUTE_JITTER_S` does, or you will chase phantom regressions caused by nothing but
scheduler noise. This is also the general `noise-floor-before-quantitative-claims` skill's territory
if you are building a new (non-tg) measurement harness from scratch.

## Fair-benchmark rules

These are what separates a claim-quality artifact from a number you cannot defend in a PR review.

### Launcher/command-kind attribution (never blend routes into one number)

`run_benchmarks.py` records, per artifact, **both**:
- `environment.tg_launcher_mode` — which of the 9 launcher-mode experiments produced the command
  (`auto`, `explicit_binary`, `explicit_fast_binary`, `explicit_binary_positional`,
  `explicit_binary_positional_early_rg`, `explicit_binary_early_rg`, `discovered_cli_binary`,
  `python_module_launcher`, `python_module_rust_first` — `run_benchmarks.py:380-390`).
- `environment.tg_launcher_command_kind` — what the *concrete* resolved command actually is:
  `native_exe`, `cmd_shim`, `powershell_shim`, `uv`, `python_module`, or `unknown`
  (`classify_tg_launcher_command`, `run_benchmarks.py:162-178`).

**Why both**: `tg_launcher_mode` is the experiment you asked for; `tg_launcher_command_kind` is what
you actually got. A `.cmd` shim, `uv` wrapper, or Python-module route on the discovered/default path
adds wrapper/interpreter overhead that has nothing to do with your code change. If
`command_kind != native_exe`, the script prints a top-level warning
(`benchmark_launcher_warnings`, `run_benchmarks.py:181-191`) and the artifact carries it in `warnings`.
**Never compare two artifacts with different `tg_launcher_command_kind` values and call the delta a
code-level win or loss** — the delta may just be shim overhead.

### Refuse stale in-tree binaries by default

`run_benchmarks.py`, `run_native_cpu_benchmarks.py`, and `run_cold_path_attribution.py` all call
`inspect_native_tg_binary` and check `version_status`. If the resolved binary is `in-tree-*` (built
from `rust_core/target/{debug,release}`) and its version does **not** match the expected package
version, the script prints `[blocker]` lines and **exits 2** unless you pass
`--allow-claim-unsafe-launcher` (`run_benchmarks.py:212-226,768-771`). This exists because a stale
in-tree binary silently benchmarks *last week's* code while you think you're measuring today's
change. Only pass `--allow-claim-unsafe-launcher` for exploratory timing you will not cite in a PR
or doc — the flag name says so on purpose.

### No comparing across launcher kinds, ever, without saying so

`docs/benchmarks.md` § "Artifact Conventions": *"This prevents native-exe, `.cmd` shim, `uv`, or
Python-module overhead from being combined into one search-speed claim."* If you must report two
launcher modes side by side (as the roadmap-1 control-plane probes in `docs/benchmarks.md` do), label
each row with its `tg_launcher_mode`/`tg_launcher_command_kind` explicitly and state which one is the
control-plane experiment vs. the accepted baseline — do not average them.

### Regression gate mechanics you must understand before reading a red/green result

`check_regression.py` (full flow: `benchmarks/check_regression.py:39-146`):
1. Loads current + resolves baseline (`--baseline auto` or an explicit path).
2. **Suite mismatch** → hard fail (exit 2) if `baseline.suite != current.suite` — you cannot diff a
   hot-query artifact against a cold-path baseline even by accident.
3. **Environment mismatch** (`detect_environment_mismatch`, `perf_guard.py:122-155`) — different
   `platform`/`machine`/`python_version` (major.minor only) between baseline and current → refuses
   comparison unless `--allow-env-mismatch` is passed.
4. **Comparator drift** (`detect_comparator_drift`, `perf_guard.py:87-119`) — reports (but does not
   fail on) any change in the `rg_time_s` comparator itself; a drifting `rg` baseline is host noise,
   not a `tg` regression, but it should make you suspicious of the whole run.
5. **Regression check** (`check_regressions`, `perf_guard.py:48-84`) — per row, per suite-specific
   time key (`SUITE_TIME_KEYS`: `run_benchmarks` → `tg_time_s`; `run_hot_query_benchmarks` →
   `first_s`/`second_s`; anything else falls back to any key ending `_time_s`/`_s`), fails if
   `pct_delta > --max-regression-pct` (default 5.0) **and** `base_time >= --min-baseline-time-s`
   (default 0.1s — the noise floor from above).

## GPU claims need a stricter bar (read before trusting any GPU number)

State of the GPU program as of v1.75.4 (re-verify against `docs/gpu_crossover.md`, which is the
current source of truth; AGENTS.md "Roadmap Sequencing" predates the v1.75.x wave and can lag):
**GPU Phase-0 SHIPPED** (v1.75.0-v1.75.4, PRs #593-#597) and is locally correctness-proven (RTX 4070
`sm_89` / RTX 5070 `sm_120`, 1GB/5GB match+file-set identity), but gated OFF the public release by the
CI Actions var `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE` (default `native-frontdoor`, CPU-only; GPU
asset publishing needs the non-default `native-frontdoor-gpu`) — Phase 1 is now a **reversible
flag-flip**, not a multi-week rebuild. That flip changes only whether the built assets are published;
it does **not** promote GPU, change the CPU-default auto-recommendation, or prove a speed crossover.
Keep the honesty floor: no speed crossover is proven vs `rg`/`tg_cpu`, GPU auto-recommendation stays
`false`, and the reviewer-gated `public-gpu-proof.yml` speed-crossover gate remains unmet
(`docs/CONTRACTS.md:80-82`). Do not treat any GPU number you produce as promotion evidence; it is
implementation history at best.

Two hard-earned rules if you do run a GPU benchmark:

1. **The fair baseline for multi-pattern is a single `rg -F -e ... -e ...` invocation, not a
   sequential loop of single-pattern `rg` calls.** A sequential-`rg` comparator makes any batched
   multi-pattern route look artificially faster. `docs/benchmarks.md` explicitly names this: *"the
   fair baseline is `rg -F -e ... -e ...`; sequential `rg` loops are exploratory amortization
   evidence only."*
2. **CPU fallback or sidecar routing must never look like GPU proof.** Any GPU-requested run that
   actually executed on `NativeCpuBackend` or `GpuSidecar` must carry `gpu_evidence_status =
   "unsupported"`, `gpu_proof = false`, `native_gpu_unavailable`, and `not_gpu_proof_reason`
   (Backend Fail-Closed Contract, AGENTS.md). If you see a fast GPU-flag row with no `sidecar_used`
   or `native_gpu_unavailable` field, the artifact is not trustworthy — the routing wasn't verified.

## Worked example: the fixed-multi-pattern native CPU route (a fair-baseline correction, not a clean win)

This is the load-bearing lesson for this whole skill: **a change that looks like a win against the
wrong comparator can be a loss against the right one.**

**What shipped** (`87d4ca4 fix: accelerate fixed multi-pattern native search`, v1.11.3, then hardened
by `27386f8 fix: harden fair fixed multi-pattern search`): a safe Aho-Corasick single-pass native CPU
route for fixed-string multi-pattern search (`rust_core/src/native_search.rs`), replacing what would
otherwise be N sequential single-pattern searches, with fallback preserved for unsupported semantics.

**The naive comparator would have called this a big win**: N sequential `rg` invocations (one process
spawn + one full-corpus scan per pattern) is obviously slower than one Aho-Corasick pass over the
corpus.

**The fair-baseline correction changed the verdict.** `rg` itself supports multi-pattern in a single
invocation (`rg -F -e pat1 -e pat2 ...`), and *that* — not the sequential loop — is the correct
comparator. Measured on the public managed v1.11.5 dogfood (`docs/gpu_crossover.md:10`), 100 fixed
no-match patterns over a 1GB corpus:

| Comparator | Time | Note |
|---|---|---|
| `rg` single-invocation multi-pattern (`rg -F -e ... -e ...`) | **0.169s** | the fair baseline |
| `tg` CPU multi-pattern (Aho-Corasick native route) | 0.394s | ~2.3x slower than fair `rg` |
| `tg --gpu-device-ids 0` | 0.448s | fell back to `NativeCpuBackend` — not a GPU number at all |

A second mixed-pattern (2665 emitted matches) row told the same story: `rg` `0.105s` vs `tg` CPU
`2.220s` vs the GPU-requested row `2.211s` (also `NativeCpuBackend` fallback).

**What the repo actually did with this result** (this is the discipline worth copying): it did **not**
revert the Aho-Corasick route — the code is still correct and is a real improvement over a sequential
loop, so `benchmarks/run_native_cpu_benchmarks.py` still exercises it — but it marked the row
non-gating: `thresholds.large_file_200mb_fixed_multi_pattern_rows_are_diagnostic: true` and
`gated: False` on both `large_file_200mb_fixed_multi_pattern_no_match` and `_count` cases
(`benchmarks/run_native_cpu_benchmarks.py:330-349,396`). Docs were corrected in the same spirit:
`docs/gpu_crossover.md` states the fair-baseline number plainly instead of the flattering
sequential-`rg` framing, and `docs/benchmarks.md` records it as "still failed the credibility bar
against the fair baseline" rather than as an accepted win.

**The reusable lesson**: when you batch/amortize N operations into one pass, benchmark against the
comparator's *own* batched primitive if it has one (`rg -F -e ... -e ...`, not N `rg` calls) — a
sequential-loop strawman will make almost any batching change look like a win. Ship the code if it's
a real structural improvement, but gate the release/doc claim on the fair-baseline number, and mark
the row diagnostic (not release-gating) until it actually beats that number.

## Pre-flight checklist before writing a speed claim anywhere (PR description, docs/PAPER.md, AGENTS.md)

- [ ] Ran the script from the decision table that matches what you changed (not a generic one).
- [ ] Compared against `--baseline auto` (or the explicit current-accepted baseline file), not a
      number from memory or an old PR.
- [ ] Checked `environment.tg_launcher_command_kind == native_exe` (or explicitly disclosed
      otherwise) — no shim/interpreter overhead hiding in the number.
- [ ] Did not pass `--allow-claim-unsafe-launcher` (or if you did, the claim is explicitly labeled
      exploratory, not accepted).
- [ ] For any row under ~10ms, verified there is an absolute-seconds tolerance, not a bare percentage
      gate.
- [ ] For any multi-pattern/batched claim, compared against the comparator's own batched primitive,
      not a sequential-loop strawman.
- [ ] For any GPU claim, confirmed `gpu_proof`/`native_gpu_unavailable`/`sidecar_used` fields show a
      real native route, not CPU/sidecar fallback wearing a GPU label.
- [ ] `check_regression.py` exit code is 0, or the regression is explicitly accepted and recorded in
      `docs/PAPER.md` as an intentional non-goal (AGENTS.md Performance Discipline rule 5).
- [ ] The artifact JSON is committed/attached, not just a terminal screenshot — someone else must be
      able to re-run `check_regression.py` against it.

## Provenance and maintenance

Facts here re-verified at tensor-grep **v1.49.3** (2026-07-08); the §6 P4 close-out, the new §7
`tg find` retrieval-quality section, and the decision-table row were added and verified **v1.78.1**
(2026-07-16) — the rest was not re-walked in this pass. Re-verify before trusting a stale number:

- Script inventory / artifact paths drift: `grep -n "| .* | \`benchmarks/run_" docs/benchmarks.md`
  or re-read the "Benchmark Matrix" table.
- Regression thresholds (`--max-regression-pct`, `--min-baseline-time-s`) and noise-floor constants:
  `python -c "import ast,sys"` not needed — just re-`Read` `src/tensor_grep/perf_guard.py` and
  `benchmarks/check_regression.py` argparse defaults; also
  `grep -n "NATIVE_REGEX_ABSOLUTE_JITTER_S" benchmarks/run_hot_query_benchmarks.py`.
- Launcher-mode enum / command-kind classifier: `grep -n "launcher_mode not in\|def classify_tg_launcher_command" benchmarks/run_benchmarks.py`.
- Stale-binary refusal behavior: `grep -n "allow-claim-unsafe-launcher\|benchmark_claim_blockers" benchmarks/run_benchmarks.py benchmarks/run_native_cpu_benchmarks.py`.
- Fixed-multi-pattern worked-example numbers: `grep -n "fixed multi-pattern\|multi_pattern" docs/gpu_crossover.md docs/benchmarks.md CHANGELOG.md` — these are historical (v1.11.x) and
  will not change, but the *current* GPU status (Phase-0 SHIPPED as of v1.75.0-v1.75.4, PRs #593-#597;
  Phase 1 = a reversible flag-flip on `TENSOR_GREP_RELEASE_NATIVE_ASSET_PROFILE`, default
  `native-frontdoor` CPU-only) should be re-checked since it is the fact most likely to have moved
  further -- no speed crossover is proven vs `rg`/`tg_cpu` regardless of publish-flag state
  (`docs/CONTRACTS.md:80-82`).
- GPU Phase-0/Phase-1 status: `grep -n "RELEASE_NATIVE_ASSET_PROFILE\|native-frontdoor-gpu" .github/workflows/ci.yml` and re-read `docs/gpu_crossover.md`'s current top section.
- Current release tag: `grep -n "release_docs_current_tag" AGENTS.md`.
- Token-economy harness promotion status (#72 — still uncommitted as of 2026-07-08?):
  `ls benchmarks/ | grep -i token` and `grep -n "token" docs/benchmarks.md` (expect no hits until #72 lands).
