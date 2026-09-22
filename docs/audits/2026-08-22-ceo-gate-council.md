## Recent campaign notes (2026-08-22) - CEO-GATE COUNCIL: 5-seat verdicts on #48/#72/#77/#131/#169 + RULESETS

Council run 2026-08-22 (`tt_council.sh`, 7 seats dispatched). **5 substantive seats**: `claude`
(fable-5), `droid_kimi`, `droid_deepseek`, `droid_glm`, `cursor`. **2 non-votes**: `agy` and `codex`
both hit sandbox/hook read failures and reported `CANNOT_READ_REQUIRED_FILE` **without fabricating
verdicts** -- the correct behaviour, and the reason a read-failure seat is discarded rather than
counted. Quorum (4) met. Question file: `.claude/thinktank_ceo_gates.md`.

**These are RECOMMENDATIONS. Every row stays `CEO_GATED` until the operator decides.**

| item | vote | council position |
|---|---|---|
| **#48** native front door | 4 agree / 1 nuanced dissent | Accept the shipped hybrid; retire the larger rewrite unless pip/uv parity is explicitly prioritised. |
| **#72** public benchmark | **5/5 WITHDRAW** | Withdraw the public 7.5x. Do NOT swap to 6.4x. No headline multiple until a committed, noise-calibrated harness exists. |
| **#77 / F9** ledger scope | 5/5 agree | Local opt-in advisory only; no auth/CI blocking. |
| **#131** GPU asset | 5/5 agree | Ship the optional experimental NVIDIA asset, CPU default + fallback, **no speed claim**. |
| **#169** GPU spend | 5/5 later, not now | Do not fund now. Rent when triggered (~$10-30 bounded campaign, $0.50-2.00/hr class), never purchase. |
| **RULESETS** | 4/5 pick (b) | Add a `tensor-grep[scan]` extra + `install-scan` remediation; keep advertise-then-disclose on the base install. Not a hard dependency. |

### #72 is the urgent one, and it is unanimous

Not one seat defended keeping the 7.5x public. The reasoning is consistent across providers: two
conflicting measurements (7.5x, later 6.4x) with **no committed harness** means neither number is
defensible, so revising the figure inherits the same defect as keeping it. Honest interim wording
exists -- task-class prose ("materially fewer tokens on definition-lookup in internal runs") -- but
it must not be a single multiple. Publishing again requires the four-part noise spec: measurement
surface, no-op control establishing the noise floor, SNR threshold, kill condition.

### Minority views, preserved (a 2-vs-3 minority is right ~25% of the time)

`droid_deepseek` dissented twice and both are worth keeping:

1. **#48** -- accept the hybrid but pin the cold-start floor in a **public ADR** rather than
   "retiring" the rewrite, so the decision stays revisitable against a measured number.
2. **RULESETS** -- the current advertise-then-disclose is the **least** acceptable option, because
   the shipped `rulesets_unavailable_reason` disclosure was an honesty **PATCH**, not a product
   **DECISION**. Its ordering: stop advertising what cannot run, THEN add the extra. This is a
   sharper reading of the same evidence the (b) majority used and should not be discarded as noise.

### #169 changed shape once a seat's claim was VERIFIED

`droid_kimi` argued #169 needs **$0** because the operator already has local NVIDIA hardware. That
is a factual claim about the environment, not about the repo, so it was checked rather than taken:

```
$ nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
NVIDIA GeForce RTX 4070, 12282 MiB
NVIDIA GeForce RTX 5070, 12227 MiB
```

Confirmed. This materially narrows #169: GPU **correctness** work needs no spend at all. The
residual question is only whether a **public** GPU claim needs a clean-room runner that a shared
desktop cannot be -- which is a much smaller decision than "fund a GPU proof environment", and it
is downstream of #72's harness discipline anyway.

### EXTERNAL GROUNDING (Exa, 2026-08-22) — added because a 5-seat consensus is not evidence

The council was run WITHOUT external research first. That is the documented correlated-hallucination
risk (consensus is not verification), so the two load-bearing items were then grounded against
outside sources. **Both changed materially.** Sources are cited so a reader can check them.

#### #72 — the external standard is stricter than the council's, and supplies the missing specifics

The council said "withdraw and re-measure with a committed harness" but gave no protocol. The
literature does:

| source | what it adds that the council did not have |
|---|---|
| Codeflash benchmarking docs (`docs.codeflash.ai/codeflash-concepts/benchmarking`) | A concrete noise floor: **5% on a real machine, 10% on GitHub Actions**, with significance only above it. Also argues for the **MINIMUM** across runs, not the mean — noise is strictly additive, so the min is the least-contaminated estimate of intrinsic speed. Directly relevant: our CI is GitHub Actions, so a sub-10% effect measured there is not a result. |
| SIGPLAN Empirical Evaluation Guidelines via `pldi-reproducibility` | **Dozens of repetitions, not three**; **geomean for ratios**; state warmup vs steady-state vs cold-start as different claims; pin the whole toolchain ("GCC" is not a baseline); and **benchmark survivorship** — silently excluding the cases your tool loses on is "the most damaging silent choice". |
| `fak/BENCHMARK-GOVERNANCE.md` | **Never mix regimes** — a live wall-clock speedup and a "value-add" ratio have different baselines and are not comparable. Anti-inflation rules: one primary number, baseline always stated, no cherry-picking, reproduction command required, and **tombstone superseded claims (mark them, do not silently delete)**. Plus a THEORETICAL / MEASURED / VERIFIED status ladder that must appear beside every published number. |
| Multigrid, "Reporting a Benchmark Result Honestly" | Eight required context fields; an explicit `runs_discarded` honesty field; and **round to the precision you earned** — "if your presentation needs two decimal places to show a difference, there is no difference." |
| Doppler `benchmark-methodology.md` | **Interleaved paired sampling** (alternate arms adjacently, never all-A-then-all-B), **>= 20 valid pairs**, paired 95% CI on the difference, and an explicit stopping rule: an interval crossing zero with < 0.5% median difference is **parity — stop tuning**, and a point estimate must not be published as a win in that case. |
| NIST TN 1830 (Pieterse & Flater) | Confidence intervals are what separate a real difference from random fluctuation; comparing bare averages "often leads to incorrect conclusions". |

**What this changes about our situation.** The 7.5x-vs-6.4x discrepancy has an explanation nobody in
the council proposed: **they may be different REGIMES measured against different baselines**, which
under fak's rule are not comparable at all and neither supersedes the other. Before re-measuring,
the first question is not "which number is right" but "what baseline and regime did each one use" —
and if that cannot be recovered from the artifacts, both are unpublishable regardless of a re-run.

**Concrete protocol this yields for the re-measurement (supersedes the vaguer council wording):**
interleaved paired arms; >= 20 pairs; report median + paired 95% CI (and min, per Codeflash);
geomean if aggregating across a suite; >= 10% floor if measured on GitHub Actions; publish the
regime and baseline in the same sentence as the number; commit the raw pairs and a reproduction
command; tombstone the 7.5x rather than deleting it.

#### RULESETS — the shipped remediation IS the documented best practice, and there is a 3rd option

- `pypa/packaging.python.org` issue **#1605** ("Should include guidance on how to handle missing
  optional dependencies / extras at runtime") confirms the gap and the accepted workaround: catch
  the ImportError and **raise a helpful error naming the extra**, because the default experience is
  a bare `ModuleNotFoundError` with no hint. That is exactly what the 2026-08-21 remediation fix
  shipped — so that fix is aligned with the ecosystem norm, not a local invention.
- **PEP 771 (Default Extras)** is a third option the council never raised. It exists *precisely* for
  this failure mode: "In all three cases, installing the package without any extras results in a
  **broken installation**, and this is a commonly reported support issue." It would let
  `pip install tensor-grep` pull the scan backend by default while `tensor-grep[]` stays minimal.
  **Status check before anyone builds on it:** PEP 771 is a PROPOSAL — it must be confirmed
  accepted and supported by the pip version we support before it can be a shipping plan. Treat it
  as a watch item, not an available mechanism.
- Net: the (b) majority (add a `[scan]` extra + remediation) is consistent with current standards.
  `droid_deepseek`'s minority point stands and is reinforced — PEP 771's motivation is that an
  install which advertises a feature it cannot run is a *known* packaging anti-pattern, not merely
  an honesty nit.

### Council hygiene notes for the next run

- `agy` failed on a Google Cloud telemetry hook (`Cannot find module ...telemetry_hook_bundle.js`)
  blocking its file tools; `codex` was refused shell access by its read-only sandbox. Both seats are
  recoverable and neither failure is about this question.
- A seat that reports a read failure AND emits a `RECOMMENDED:` line is not automatically valid:
  `codex`'s verdict line honestly asked to be re-run with read access rather than issuing positions,
  so it is a non-vote on substance even though it carries the token.

