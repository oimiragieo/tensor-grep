# Plan — #276: the native `--json`/`--ndjson` envelope must admit incompleteness

Status: DRAFT (awaiting thinktank review)
Author: backlog-steward session, 2026-07-26
Goal: #292 (trustworthy tg) / the CEO enterprise-readiness answer of 2026-07-26

## 1. The defect, stated precisely

`tg` emits `--json` and `--ndjson` for machine consumers. On the **native (Rust) path**, when the
walk hits an unreadable directory, the process prints one line to **stderr** and continues — but the
JSON envelope still reports success and the process still exits 0.

A consuming agent that parses stdout therefore **cannot distinguish "no matches exist" from "I could
not finish looking."** That is the exact centre of the trust goal, on the fastest and most
machine-facing path.

This is not speculative. The code says so itself, at `rust_core/src/native_search.rs:1642-1649`:

> NOTE (task #280): printing is only half the contract. `rg` also exits 2 on an unreadable path
> while still emitting its matches, and the JSON envelope should carry `result_incomplete` +
> `incomplete_reason_class` the way the Python routes do since #276 slice 1 (c0c3404). Neither is
> wired here yet — that needs an error count threaded through `SearchStats` into
> `emit_json_matches` and the exit code, and is the next slice. Until then this path is honest on
> stderr but still reports success.

## 2. Verified seam map

Every claim below was read from the tree on 2026-07-26, not recalled.

| Seam | Location | Current state |
| --- | --- | --- |
| Streaming walker, Err arm | `rust_core/src/native_search.rs:1253` | `eprintln!` → `WalkState::Continue`; count dropped |
| Streaming walker, second Err site | `rust_core/src/native_search.rs:1295` | same |
| Collector walker (`collect_walked_files`) | `rust_core/src/native_search.rs:1650` | same |
| GPU twin | `rust_core/src/gpu_native.rs:3899` | same (CUDA-gated, dormant) |
| Stats struct | `rust_core/src/native_search.rs:75-82` | `SearchStats` has **no** error/incompleteness field |
| JSON emitter | `rust_core/src/native_search.rs:2305` | `emit_json_matches(config, stats)` — emits no incompleteness |
| Exit code | `rust_core/src/main.rs:1590` | `execute_ripgrep_search(&rg_args)?` returns the code |

The **Python** routes already carry the contract (shipped in slice 1):

- `src/tensor_grep/backends/ripgrep_backend.py:144,150` — sets `result_incomplete = True`,
  `incomplete_reason_class = "unreadable_path"`
- `src/tensor_grep/cli/formatters/json_fmt.py:126-140` — writes both keys into the envelope,
  **conditionally** (`result_incomplete` only when true; `incomplete_reason_class` only when not None)
- `src/tensor_grep/cli/main.py:4394-4409` — the `incomplete_reasons` list + first-cause-wins
  `incomplete_reason_class`

So the native path is not missing a *design*. It is missing the *wiring* of a design that already
ships on the sibling path. **This plan must not invent a second vocabulary.**

## 3. Prior art (Exa research, 2026-07-26)

Eight prior arts were surveyed. Five design decisions recur across SARIF 2.1.0, Elasticsearch,
GraphQL, Semgrep, OTLP and Google AIP-233:

1. **A boolean is necessary but never sufficient** — pair it with a *closed* reason enum. No surveyed
   format ships a bare `incomplete: true`.
2. **Give a count, not just a flag** — ES `_shards {total, successful, skipped, failed}`, OTLP
   `rejected_spans`, Semgrep `paths.scanned[]`/`skipped[]`. The consumer must be able to ask "how
   much of the work got done" quantitatively.
3. **Attribute the failure to *where* it happened** — GraphQL `errors[].path`, ES
   `_shards.failures[]`, Semgrep `skipped_target.path`. "3 directories were unreadable" is
   actionable to an agent in a way "something failed" is not.
4. **Additive and empty-by-default** — the happy-path shape must not change, so existing consumers
   see no behaviour change (OTLP's "empty == absent"; AIP-233's explicit backward-compat argument).
5. **Exit code and in-band marker are complementary, never substitutable.** GraphQL always returns
   HTTP 200 and relies entirely on in-band `errors`, precisely because a status-only signal is
   invisible to a client that does not check it.

### The finding that matters most

**ripgrep has this exact bug, and has deliberately declined to fix it.** Its `--json` schema
(`begin`/`end`/`match`/`context` + a `summary` whose `stats` is
`{elapsed, searches, searches_with_match, bytes_searched, bytes_printed, matched_lines, matches}`)
contains **no error field at all**; I/O errors go to stderr only and completeness is signalled
*solely* by exit code 2. The maintainer has explicitly rejected adding per-error classification as
scope creep ([ripgrep#2861](https://github.com/BurntSushi/ripgrep/issues/2861)).

This converts #276 from "catch up to the competition" into "**the one place tg can genuinely lead**"
— which is directly the answer to task #307 (tg currently *ties* rg on the trust benchmark). A
consumer piping `--json` into `jq` never sees an exit code; for that consumer ripgrep is structurally
silent and tg would be structurally honest.

**Rejected from the research:** SARIF's `reportingDescriptorReference` catalog indirection (built for
third-party rule authors; tg owns its whole reason list — wrong altitude), and Semgrep's default
`semgrep ci` behaviour of swallowing internal errors into exit 0 (the opposite of fail-closed).

## 4. Design

### 4.1 Reuse the existing vocabulary — do not rename

The envelope keys are **already contracted and ratcheted**:

- `result_incomplete: true` — emitted only when true (`json_fmt.py:126`)
- `incomplete_reason_class: str` — a **closed** vocabulary: `unreadable_path` | `timeout` |
  `deadline` | `scan_limit`

Per #293, the cause vocabularies are closed, documented and ratcheted, and a previously-filed rename
was judged **wrong**. Per the standing rule, `truncation_cause` (hyphenated) and
`incomplete_reason_class` / `partial_reason` (underscored) are two distinct vocabularies that must
**not** be unified. The native path adopts `incomplete_reason_class = "unreadable_path"` verbatim.

The research agent's suggested `incomplete_reason` (singular) is **rejected** — it would create a
third near-synonym for a field that already exists under a different name.

### 4.2 What is genuinely new

Research decisions 2 and 3 (a count, and a location) are the parts tg does **not** have on either
path. They are the honest enrichment and should be added — but scoped:

```jsonc
{
  "matches": [ /* unchanged */ ],
  "result_incomplete": true,               // existing key, now also on the native path
  "incomplete_reason_class": "unreadable_path",  // existing closed vocabulary
  "incomplete_paths_count": 3              // NEW: the count (research decision 2)
}
```

**Decision to put to thinktank:** whether to also ship a per-path `skipped[]` array (research
decision 3). Arguments against shipping it in this slice:

- The walk runs in `build_parallel()` across threads; accumulating an unbounded path list under a
  mutex is a new allocation + contention path on the hot walker, on a tool whose entire positioning
  is speed.
- An unbounded array is a DoS-shaped surface: a tree with 50k unreadable entries produces a 50k-entry
  JSON array that an agent must then parse.
- The count alone already discriminates "complete" from "incomplete", which is the trust defect.

Proposed resolution: ship the **count** in this slice; defer the array to a follow-up, bounded to
the first N paths with an explicit `truncated` marker, and only if a real consumer asks. Record the
deferral rather than leaving it implied. *(This is exactly the demand-gating discipline in
`instrumented-build-gate` — do not build the speculative half.)*

### 4.3 Exit code

Native path must exit **2** when `result_incomplete` is true, matching both `rg`'s behaviour and
tg's own Python path. In-band and exit-code signals are complementary (research decision 5): ship
both, and do not let either become the sole channel.

### 4.4 Backward compatibility

`result_incomplete` and `incomplete_reason_class` are emitted **only when incomplete**
(`json_fmt.py` already does this conditionally). `incomplete_paths_count` likewise. The happy-path
envelope is byte-identical to today. This satisfies research decision 4 and means no consumer breaks.

## 5. Implementation slices

Each slice is independently shippable and independently verifiable.

**Slice A — thread the count through the native stats.**
1. Add `walk_errors: usize` to `SearchStats` (`native_search.rs:75-82`). `Default` gives 0.
2. In all three `native_search.rs` Err arms (`:1253`, `:1295`, `:1650`), increment an
   `Arc<AtomicUsize>` alongside the existing `eprintln!`. Keep the stderr line — it is the `rg`-parity
   behaviour and #263 added it deliberately.
3. Fold the atomic into `SearchStats.walk_errors` where the walkers build their stats.

**Slice B — emit it.**
4. `emit_json_matches` (`:2305`): when `stats.walk_errors > 0`, add `result_incomplete: true`,
   `incomplete_reason_class: "unreadable_path"`, `incomplete_paths_count: <n>`.
5. Same for the `--ndjson` emitter (find and confirm its seam — **not yet located; do this before
   coding**, do not assume it shares `emit_json_matches`).

**Slice C0 — MAKE EVERY EXIT-CODE CONSUMER THREE-STATE-AWARE. This gates slice C.**

> **Adversarial review verdict: HOLD**, on exactly the open question §8.5 flagged. Exit 2 is *not*
> safe today. The blast radius is **six sites, not one**, and I verified the three sharpest myself
> rather than taking the review's word for it. Shipping slice C without C0 would convert an honest
> partial into a total loss of MCP results, a broken `tg calibrate`, a failing `tg dogfood`, and a
> red CI matrix.

| # | Consumer | Site | What breaks |
| --- | --- | --- | --- |
| C0a | **`tg` spawns itself** | `rust_core/src/crossover.rs:354` — `if !status.success() { bail!(...) }`, on a child spawned from `env::current_exe()` (`main.rs:6747`) | A self-spawned `tg search` exiting 2 on an unreadable subtree kills the whole `tg calibrate`, which then fails with a **misattributed** cause |
| C0b | **`tg dogfood`** — a shipped user-facing command | `scripts/agent_readiness.py:348` — `if completed.returncode not in {0, 1}` → `ReadinessError`; also `:145`, `:1077` | ~30 `tg search` invocations; `dogfood.py:429` defaults `include_shell_probes=True`, so a plain `tg dogfood` runs them. Also the `windows-agent-readiness` CI gate (`ci.yml:186-193`) |
| C0c | e2e byte-fidelity test | `tests/e2e/test_native_json_byte_fidelity.py:100-103` — `assert proc.returncode == 0` | CI-gated via `TG_REQUIRE_RG_PARITY=1` (`ci.yml:702-710`), so it cannot silently skip |
| C0d | benchmark harnesses | `benchmarks/run_gpu_native_benchmarks.py:371`, `benchmarks/run_rg_parity_benchmarks.py:62` | Report `FAIL` rather than partial |
| C0e | MCP | `mcp_server.py:1922` | Total loss of results |

**Telling detail** (`agent_readiness.py:341-347`): a per-case exit-2 tolerance *already exists* for
`root-option-first-count-matches` when rg is absent. It is per-case, not general — so the sweep
cannot absorb a new exit-2 class without an explicit edit. That is the proof this is a real gate and
not a theoretical one.

**The C0 contract:** `0`/`1` → parse output. `2` → parse output **and** read the incompleteness
marker. `>2` → error. Note `run_gpu_native_benchmarks.py` already special-cases `returncode == 2`
for classified causes (`:1209-1214`, `:3212-3215`) — the pattern exists; it was simply never applied
to `benchmark_search_command`.

**Confirmed SAFE, no change needed** (pass-through, no `check=True`): `main.py:3793-3808`
`_delegate_to_native_tg_search` — the real production Python→native path, which `sys.exit()`s the raw
code at `:7755-7763` — plus `bootstrap.py:1415`/`:1482`, `scripts/dogfood/`, and
`tests/helpers/rg_parity.py:453-469`.

**Slice C — exit code.**
6. Only after C0: propagate so an incomplete native search exits 2 (`main.rs:1590` path).

**Slice D — the twin (discipline A27).**
7. `gpu_native.rs:3899` carries the identical defect. A class fix must cross to its twin **in the
   same campaign** — the ledger/index-lock incident proved that a docstring explaining a retired
   approach is worthless in the file still using it. CUDA-gated and dormant, but fix it.

**Slice E — contract + governance.**
8. Update `docs/CONTRACTS.md` §0 with the native path's obligation and the new count field.
9. Contract changes are pinned by governance tests — update them in the **same PR** or CI reddens.
10. Check whether the MCP server surfaces these fields; if so, `_TG_MCP_SERVER_CONTRACT_VERSION`
    must bump (a 5th registration site).

## 6. Verification — and the trap to avoid

**Rust ⇒ CI is the only oracle.** The CPU-safe rule forbids `cargo build/check/test/clippy` and
`maturin` on this box. `rustfmt --check` **is** allowed (it is not a compiler) and must be run before
pushing — it enforces `chain_width`/`fn_call_width` (60), not just `max_width`, and its printed diff
should be applied verbatim.

**Bidirectional oracle (mandatory, Form 1).** A test that passes both with and without the fix is
not verification. The control arm must FAIL:

- Fixture: a directory the walker cannot read, plus at least one readable file that *does* match.
- Assert (treatment): envelope has `result_incomplete == true`,
  `incomplete_reason_class == "unreadable_path"`, `incomplete_paths_count >= 1`, exit code 2, **and
  the matches from the readable file are still present** (keep-partial, per #281).
- Assert (control): on the unpatched tree the same fixture yields no `result_incomplete` key and
  exit 0. If it does not, the fixture is not biting and the test proves nothing.

**Form 6 — prove the fixture applies.** The #281 lesson: a permission fixture that silently fails to
apply (CI runs as root; Windows ACLs differ from POSIX modes) makes the test pass for the wrong
reason. Assert the precondition explicitly — the unreadable path must actually be unreadable *in the
test's own environment* — and skip loudly rather than pass quietly if it cannot be made so.

**Cross-platform.** Windows ACL vs POSIX mode semantics differ; agents running pytest on Windows
only have masked cross-platform CI failures before. Wait for the full ubuntu+macos+windows matrix
before merging.

## 7. Risks

| Risk | Mitigation |
| --- | --- |
| Renaming/duplicating the closed vocabulary | Reuse `incomplete_reason_class` verbatim; #293 says the rename was wrong |
| Fixture doesn't bite (root/ACL) | Assert the precondition; skip loudly, never pass quietly |
| Hot-walker contention from new bookkeeping | `AtomicUsize` increment only; explicitly defer the unbounded path array |
| Twin left unfixed | Slice D is not optional (A27) |
| Contract test drift reddens CI | Slice E in the same PR |
| Local green ≠ CI green | Rust cannot be compiled here; CI is the arbiter, full matrix before merge |

## 8. Open questions for thinktank

1. Ship `incomplete_paths_count` now, or boolean+class only, deferring **all** counting?
2. Per-path `skipped[]` array — defer (proposed) or ship bounded?
3. Does the `--ndjson` path share `emit_json_matches`, or is it a separate seam needing its own fix?
4. Should the native path distinguish `unreadable_path` from a *deadline* hit mid-walk, or is
   first-cause-wins (the Python convention at `main.py:4399`) correct here too?
5. Is exit 2 safe for every native caller, or does some internal consumer treat non-zero as fatal and
   thereby convert an honest partial into a hard failure? **This must be checked before shipping** —
   it is the classic "obvious fix is wrong" shape.
