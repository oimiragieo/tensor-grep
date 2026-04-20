# v1.4.3 Count-Path Bootstrap Overhead Design

## Goal

Reduce the default `tg search -c` cold-path overhead on Windows by avoiding unnecessary native-launcher resolution on rg-passthrough searches, while also hardening trusted native launcher detection for the existing native-triggered search shapes.

## Outcome

Executed on 2026-04-19.

- Accepted: harden `resolve_native_tg_binary()` so Windows `PythonXY\Scripts\tg.exe` shims are not treated as native binaries.
- Accepted: align the editable `uv.lock` package version with the shipped `1.4.2` release metadata so release-asset validation stays truthful.
- Rejected: the bootstrap-side `-c` short-circuit. The measured artifact `artifacts/bench_run_benchmarks.count_path_candidate_uv.json` still failed the governed regression gate, so that code path was reverted.
- Final release read: this slice closes as a narrow correctness/docs patch, not as a `perf(search)` win.

## Why This Work Exists

`v1.4.2` fixed correctness issues, but the current cold-path performance story still has one explicitly documented regression: [docs/PAPER.md](../../PAPER.md) records the default `count_matches` / `-c` lane as the next narrow row to unwind after the rejected front-door widening experiments.

The repo history already rules out the wrong move:

- do not reopen broad default-front-door passthrough widening
- do not mix AST or unrelated correctness work into this slice
- do not publish new speed claims without a benchmark artifact and regression check

Current local root-cause evidence narrows the problem further:

1. `src/tensor_grep/cli/bootstrap.py` resolves the native `tg` binary before it knows whether the current search shape can ever use it.
2. Plain `-c` searches stay on the rg passthrough lane, so that native-resolution work is pure overhead on the target row.
3. `src/tensor_grep/cli/runtime_paths.py` currently misclassifies `...\\PythonXY\\Scripts\\tg.exe` console-entrypoint shims as native `tg` binaries when they are outside the active environment, which is an existing correctness risk for the current native-triggered paths.

## External Validation

The external research still supports keeping this slice narrow:

- REI's extended regex-indexing results show that query-specific fast paths are where practical gains appear, not from broad control-plane complexity: <https://arxiv.org/pdf/2510.10348>
- `ContextBench` shows that extra agent scaffolding alone produces limited gains; precise, efficient retrieval and execution paths matter more: <https://arxiv.org/abs/2602.05892>
- Recent practitioner writeups keep converging on local indexing, incremental refresh, and low-overhead execution surfaces rather than larger default stacks:
  - <https://www.cursor.sh/blog/fast-regex-search>
  - <https://pub.towardsai.net/i-built-an-ast-powered-code-mcp-that-saves-70-tokens-heres-how-it-works-3dbe58746729>

This supports a minimal `perf:` patch that attacks one proven row instead of a broader feature release.

## Scope

In scope:

- trust-safe native binary detection for the `tg` launcher path
- bootstrap short-circuiting so rg-passthrough `-c` searches do not pay native-resolution cost
- regression tests that prove the new route is taken only when it is safe
- benchmark validation on the governed cold-path harness

Out of scope:

- `--count-matches` support in the native binary
- broad default-front-door widening for other flags
- AST, GPU, MCP, docs-contract, or release-workflow changes
- README / benchmark-table / paper speed claims before acceptance

## Design

### 1. Harden native binary detection

Update `src/tensor_grep/cli/runtime_paths.py` so `resolve_native_tg_binary()` rejects Windows `PythonXY\\Scripts\\tg.exe` style console launchers, not just launchers that live in the currently active environment. This keeps existing native-triggered delegation limited to trusted in-tree builds or true native installs.

### 2. Skip native resolution for rg-passthrough count searches

Update `src/tensor_grep/cli/bootstrap.py` so native `tg` resolution only happens when the search shape can actually use the native binary: existing native-triggered flags or `TG_RUST_FIRST_SEARCH`. Plain `-c` searches should remain on the rg passthrough lane, but they should stop doing native-resolution work that the lane never consumes.

### 3. Preserve current fallbacks

Bootstrap behavior after the change should remain stable:

- plain supported searches still passthrough to `rg`
- tg-specific or unsupported shapes still fall back to the full CLI
- existing native-triggered shapes still attempt native delegation, now with safer launcher filtering

### 4. Validate by benchmark, not by guess

Acceptance requires:

- focused red-green tests for runtime-path safety and bootstrap routing order
- full local gates after code stabilizes
- `python benchmarks/run_benchmarks.py`
- `python benchmarks/check_regression.py --baseline auto --current ...`

The candidate is accepted only if the `5. Count Matches` row improves and the governed benchmark line does not regress.

## Risks And Mitigations

- Risk: native-triggered search paths could recurse into a Python launcher instead of a real native binary.
  - Mitigation: harden `resolve_native_tg_binary()` first and lock it with tests.
- Risk: bootstrap reordering could accidentally change search routing.
  - Mitigation: keep the native-trigger set unchanged and add focused bootstrap tests for both rg passthrough and native-triggered cases.
- Risk: the change is correct but not faster.
  - Mitigation: reject the patch after benchmark measurement and record the attempt in `docs/PAPER.md` instead of shipping it.
