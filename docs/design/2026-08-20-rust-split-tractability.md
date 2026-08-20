# Design: tractability of splitting the oversized Rust files

**Status:** analysis only, no code changed. **Date:** 2026-08-20.
**Decides:** whether `rust_core/src/main.rs`, `gpu_native.rs`, `native_search.rs`, and `index.rs`
can be split under `scripts/file_size_budget.py`'s limits, and what the *actual* obstacle is —
given that this development box cannot run `cargo build`/`check`/`test`/`clippy` (shared desktop;
every Rust verification here is a CI round-trip).

This is the Rust sibling of `docs/design/2026-08-19-split-floor-escape.md`. That doc found Python's
obstacle was a silent one: `monkeypatch.setattr(mod, "f", ...)` rebinds an attribute on `mod`, and a
bare call to `f` inside `mod` resolves through the *defining* module's globals — move `f` and a test
can still pass while production runs the wrong code. **Rust has no equivalent failure mode.**
Visibility crossing a module boundary is a compile error, not a silent pass. That is the headline
finding here: the Rust obstacle class is fundamentally cheaper to get wrong safely, because the
compiler is the oracle. The cost is that this box cannot run that oracle, so this document produces
hypotheses plus the exact minimal CI experiment to settle each one — never a claim resting on “I
read the code and it looks fine.”

---

## 1. Re-derived table (measured, not estimated)

Command: `python scripts/file_size_budget.py --report`, run from a fresh worktree at
`origin/main` (7ee3a27e), 2026-08-20.

Core-Rust (`rust_core/src/*.rs`, limit 1,500) violations, in order:

| file | lines | over limit by |
|---|---|---|
| `rust_core/src/main.rs` | 15,126 | 13,626 |
| `rust_core/src/gpu_native.rs` | 4,952 | 3,452 |
| `rust_core/src/native_search.rs` | 3,563 | 2,063 |
| `rust_core/src/index.rs` | 3,092 | 1,592 |
| `rust_core/src/backend_ast.rs` | 2,553 | 1,053 |
| `rust_core/src/backend_ast_workflow.rs` | 2,109 | 609 |
| `rust_core/src/backend_cpu.rs` | 1,817 | 317 |
| `rust_core/src/python_sidecar.rs` | 1,519 | 19 |

Test-Rust (`rust_core/tests/*.rs`, limit 2,000) violations:

| file | lines | over limit by |
|---|---|---|
| `rust_core/tests/test_schema_compat.rs` | 4,412 | 2,412 |
| `rust_core/tests/test_routing.rs` | 2,995 | 995 |
| `rust_core/tests/test_ast_rewrite.rs` | 2,509 | 509 |
| `rust_core/tests/test_index.rs` | 2,356 | 356 |
| `rust_core/tests/test_public_native_cli_parity.rs` | 2,318 | 318 |

The brief named four core files (`main.rs`, `gpu_native.rs`, `native_search.rs`, `index.rs`); this
run found four more (`backend_ast.rs`, `backend_ast_workflow.rs`, `backend_cpu.rs`,
`python_sidecar.rs`) at smaller overages, all sharing the same production-code + inline
`#[cfg(test)] mod tests` shape (§3). They get one paragraph each in §6 rather than full verdicts,
since they were not the brief's subject and the pattern generalizes cleanly.

The `rust_core/tests/*.rs` violations are integration-test files — each is already its own
compilation unit with no privacy relationship to its siblings. Splitting one into two files
(`tests/test_index.rs` + `tests/test_index_incremental.rs`, say) needs no visibility change at all:
integration tests only ever see the library's `pub` surface, so there is nothing to break. This
class is TRACTABLE by inspection and not analyzed further here — the interesting question is
entirely in the `src/` files, and specifically in whether an inline `#[cfg(test)] mod tests` block
can move out of them.

---

## 2. The load-bearing structural fact: bin crate vs lib crate

`rust_core/Cargo.toml`:

```
[lib]
name = "tensor_grep_rs"
crate-type = ["cdylib", "rlib"]
...
[[bin]]
name = "tg"
path = "src/main.rs"
```

`rust_core/src/main.rs` compiles as the **binary crate root** (`tg`), a separate compilation unit
from the **library crate** `tensor_grep_rs` (`rust_core/src/lib.rs`, which declares
`pub mod gpu_native; pub mod index; pub mod native_search; pub mod backend_ast; ...` —
`rust_core/src/lib.rs:9-17`). `gpu_native.rs`, `native_search.rs`, and `index.rs` are library
*modules*; `main.rs` is not — it is the binary's `main()` plus everything it calls, importing
`tensor_grep_rs::*` for the pieces that *are* in the library
(`rust_core/src/main.rs:27-52`, e.g. `use tensor_grep_rs::index::TrigramIndex;`).

This split matters for exactly one question that recurs through this doc: **can an inline
`#[cfg(test)] mod tests` block move to `rust_core/tests/` as an integration test?** Integration
tests are separate crates that link only against a library crate's `pub` API. A binary crate
exports nothing for them to link against at all — so for `main.rs` the answer is structurally "no"
regardless of how many functions are `pub`, whereas for the three library files it depends on how
much of what the tests touch is actually `pub`.

Verified (`grep -c '^pub fn ' rust_core/src/main.rs` restricted to code outside `mod tests`): **0**
`pub fn` in `main.rs`, 238 private `fn`. `main.rs:2985` (`mod tests { use super::*; ... }`) reaches
all 238 through `use super::*`, which is Rust's ordinary rule that a child module inherits access to
every private item of its ancestor modules — this does not depend on which physical file the child
module's body lives in, only on it remaining a module of the same crate.

For the three library files, the pub-vs-private split:

| file | prod lines (before `mod tests`) | `pub fn` | `pub(crate) fn` | private `fn` |
|---|---|---|---|---|
| `gpu_native.rs` | 4,442 (through `main.rs`-analog line 4442) | 11 | 0 | 90 |
| `native_search.rs` | 2,683 | 8 | 0 | 85 |
| `index.rs` | 1,753 | 21 | 0 | 44 |

(Command: a small Python scan over `rust_core/src/{file}` counting `^\s*pub fn `,
`^\s*pub\(crate\) fn `, `^\s*fn ` up to the `#[cfg(test)]` line found by
`grep -n '#\[cfg(test)\]$'`.) In all three, private functions outnumber `pub` ones roughly 4-8×, and
`grep -n 'pub(crate)' rust_core/src/{index,native_search,gpu_native,main}.rs` returns exactly **1**
hit total across all four files — this codebase does not use `pub(crate)` as an intermediate
visibility tier, it is `pub` or private. So for these three files too, `mod tests` mostly reaches
private items, and moving those tests to `rust_core/tests/` would require making the tested internals
`pub` — a public-API change, not a mechanical file move, and out of scope for a line-count fix.

---

## 3. What Rust's module system actually blocks (and what it doesn't)

**Does not block:** moving a file's contents to a different *file* within the same crate. Rust
resolves modules by declaration (`mod foo;` + `foo.rs` or `foo/mod.rs`, or `#[path = "..."] mod
foo;`), not by directory layout matching some Python-style bare-name binding. An inline
`#[cfg(test)] mod tests { ... }` block converted to `#[cfg(test)] mod tests;` with the body moved to
a sibling file (e.g. `#[path = "index_tests.rs"] mod tests;` inside `index.rs`) is the *same module*
in the *same crate* — every private item it reached via `use super::*` before, it still reaches
after, because ancestor-descendant privacy is a property of the module tree, not the filesystem.
This is standard, specification-level Rust module behavior, not something specific to this
codebase — I am treating it as a structural fact rather than a hypothesis, the same way the sibling
Python doc treats "Python resolves bare names through the defining module's globals" as a fact. It
is nonetheless **unverified in this repo** in the literal sense (nobody has run `cargo check` on the
post-split tree), so §5 still names it as the first CI experiment before trusting it operationally.

**Does block, but loudly:** moving a *private production* function into a child submodule while a
sibling function that still lives in the parent calls it by bare name. Rust's visibility check is
part of name resolution during compilation — an under-visible item is a `E0603`/`E0624`-class
compile error, not a runtime behavior change. This is the fundamental contrast with the Python split
floor: there, the dangerous outcome is "the test passes and production is silently wrong"; here, the
dangerous outcome is "it does not compile," which `cargo check` reports immediately and which this
box's CI already gates on (`static-analysis`/`test-rust-core` jobs). There is no Rust equivalent of
"locked to this file by a silent 4-7x multiplier" — the equivalent quantity here is "how many
call sites need a visibility bump," and every one of them is compiler-verifiable, not merely
graph-reachable-by-inspection.

**Precedent in this repo:** none. `rust_core/src/` is flat — `ls rust_core/src/` shows `cli.rs`,
`backend_ast.rs`, etc. as single files; there is no existing `foo/mod.rs` directory-style module and
no existing `#[path = ...]`-declared file split (`grep -rn '#\[path' rust_core/src/` returns
nothing). Doing this for the first time in this repo is not a reason to doubt the mechanism (it is
ordinary Rust), but it does mean there is no local worked example to diff against, which is another
reason the CI experiment in §5 should run before a real split, not be inferred from reading the docs.

---

## 4. Per-file verdicts

### `rust_core/src/main.rs` — 15,126 lines — **NEEDS-CI-EXPERIMENT, then TRACTABLE-partial**

- `#[cfg(test)] mod tests { ... }` spans **`main.rs:2984`–`main.rs:7473`** (4,489 lines, computed by
  brace-depth balance from the `mod tests {` line — not estimated). Extracting it to
  `#[path = "main_tests.rs"] mod tests;` would drop `main.rs` to **10,637 prod lines** — still
  **7.1×** over the 1,500 limit. Test-extraction alone does not get this file under budget; it is
  necessary but nowhere near sufficient.
- `command_template` (`main.rs:4169`) is defined *inside* `mod tests`, private, and called by 6
  other test functions at `main.rs:4255,4288,4306,4328,4438,4483` — all of which are themselves
  inside the same `mod tests` block (2985-7473). This confirms the brief's premise: it is a
  same-module test helper. It does **not** block the file-move above (it moves along with the whole
  `mod tests` body, and `use super::*` still reaches everything it needs); it *would* block moving
  individual tests to separate files unless `command_template` also moved to wherever they land, or
  became a `pub(super)`/shared helper module.
- Two more `#[cfg(test)]` items live **outside** `mod tests`, later in the file:
  `main.rs:8620-8631` (`enum IndexFlagPolicy`) and `main.rs:8633-8702` (`const
  INDEX_FLAG_POLICY`, a 70-entry classification table). They are referenced only from a test at
  `main.rs:6975-7019` (inside `mod tests`, i.e. *before* their own definition in the file — legal in
  Rust because item order doesn't matter for name resolution). Extracting `mod tests` to its own
  file does not disturb this: both const and enum stay module-level in `main.rs` (or move with it if
  desired), and `use super::*` inside the extracted file still reaches them either way.
- `main.rs` compiles as the `tg` **binary crate root** (§2), not part of the `tensor_grep_rs` library
  (0 `pub fn` outside `mod tests`, 238 private). So none of its logic — tested or not — is reachable
  from `rust_core/tests/*.rs` integration tests without either making dozens of internals `pub` (an
  API-surface change to a binary that currently exposes none) or restructuring so the CLI logic
  lives in the library crate and `main.rs` becomes a thin shim. Both are out of scope for a
  line-count fix and were flagged as "not decided here" in the sibling Python doc's closing line —
  same answer applies on the Rust side.
- **First move, concrete:** extract `mod tests` (main.rs:2984-7473) to `#[path = "main_tests.rs"]
  mod tests;` with the body in a new `rust_core/src/main_tests.rs`. Saves 4,489 lines (~30% of the
  file) for a mechanical, privacy-preserving move. This is necessary regardless of what else
  happens to `main.rs`, since nothing else here gets it under 1,500 either.
- **Beyond that, TRACTABLE only as a real architecture project, not a file split.** The remaining
  10,637 lines are the CLI's `clap` arg structs, the `Commands` dispatch, and ~230 private helper
  functions with no existing internal module boundary. Splitting *that* safely means choosing
  submodules (`mod flags;`, `mod dispatch;`, ...) and bumping each moved item's visibility to at
  least `pub(crate)` wherever a caller stays behind in `main.rs` proper — every one of those bumps
  is compiler-checked, but there are on the order of hundreds of candidate call sites and no
  natural single break like the ones found in §4.2-§4.4 below. This needs its own design pass
  (grouping candidate, adversarial review) before a build agent touches it; it is not a "first move"
  this document can respon­sibly hand off in one paragraph.

### `rust_core/src/gpu_native.rs` — 4,952 lines — **NEEDS-CI-EXPERIMENT (feature-gated)**

- The entire module is conditionally compiled: `rust_core/src/lib.rs:9-10` —
  `#[cfg(feature = "cuda")] pub mod gpu_native;` — and `cuda` is **not** in the default feature set
  (`Cargo.toml`: `default = []`). A default `cargo check`/`cargo build`/`cargo test` never touches
  this file at all.
- `.github/workflows/ci.yml:684-763` (`cuda-feature-check`) is the only CI job that compiles it —
  `cargo check --features cuda --all-targets` then `cargo test --features cuda --lib` — and per its
  own inline comment (`ci.yml:744-752`) the tests run for real (cudarc `dlopen`s the CUDA driver
  at runtime and gracefully reports "unable to load" on a GPU-less runner rather than failing to
  compile/link). So this file **is** exercised in CI, just on a narrower, feature-gated path than
  the other three — any split here is verifiable, but only through that one job, and a change that
  silently breaks under `--features cuda` would not show up in the default `test-rust-core` job at
  all.
- `#[cfg(test)] mod tests` here is comparatively small: `gpu_native.rs:4443-4911` (468 lines).
  Unusually, **~41 lines of production code follow the test module** —
  `fn cuda_library_search_paths()` and `fn push_cuda_bin_candidates()` at `gpu_native.rs:4912-4952`,
  both private, Windows CUDA-toolkit path discovery. Extracting `mod tests` alone only saves 468
  lines, dropping the file to ~4,484 — still **2.99×** over budget. `grep -c '#\[cfg(feature =
  "cuda")\]' rust_core/src/gpu_native.rs` returns **0**: nothing *inside* the file is further
  feature-gated (the whole-module gate at the `mod` declaration in `lib.rs` is the only one), so
  there is no free `#[cfg]` seam to split along.
- The file has natural structural boundaries by inspection (types/config `gpu_native.rs:346-841`;
  device/transfer benchmarking `846-1150`; the CUDA-graph capture/replay machinery
  `1152-1727`, including two `Drop` impls managing raw CUDA handles at `681` and `719` — code
  where getting ownership/lifetime wrong would be a runtime correctness bug, not just a compile
  error, so it deserves the most caution of anywhere in this file). 11 `pub fn`, 90 private `fn` —
  a split that moves any of those 90 into a child submodule needs a `pub(crate)` (or similar) bump
  per moved-but-still-called-from-parent item; whether that visibility work is small or large is
  exactly what §5's experiment would show, because I cannot verify it here without compiling
  `--features cuda`, which this box does not do.
- **Verdict: NEEDS-CI-EXPERIMENT before any verdict on the production-code split**, both because it
  is compiled by a narrower job than the other three files and because the CUDA-graph
  capture/replay code (`Drop` impls managing unsafe CUDA resources) is the one place in this whole
  survey where a wrong split could plausibly compile clean and still be wrong at runtime (a `Drop`
  impl separated from the resource it frees by a module boundary is a correctness question, not
  just a visibility one). Test-extraction is the same low-risk move as elsewhere but buys the least
  of the four files (468 / 4,952 = 9.4%).

### `rust_core/src/native_search.rs` — 3,563 lines — **TRACTABLE (test-extraction), NEEDS-CI-EXPERIMENT (prod split)**

- `#[cfg(test)] mod tests` spans `native_search.rs:2685-3563` — **879 lines**, the entire tail of
  the file (the module ends exactly at EOF, confirmed by `tail -5 rust_core/src/native_search.rs`
  landing inside the closing `}` of `mod tests`). Extracting it drops the file to **2,684 prod
  lines** — still 1.79× over budget, but the single biggest proportional win of the three
  library files (24.7% of the file).
- 8 `pub fn`, 85 private `fn` in the remaining production code. By inspection, the file has four
  natural bands: types/config (`native_search.rs:44-419`: `NativeSearchMatch`,
  `NativeMultiPatternMatch`, `SearchStats`, `NativeSearchConfig`), the parallel-walk worker
  (`419-857`: `ParallelWalkWorker` + its `Drop` impl at `746`), the search drivers (`937-1834`:
  `run_native_search`, `run_native_fixed_multi_pattern_search`, the streaming/sequential variants),
  and chunk-parallel search plus output emission (`2003-2680`: `search_file_chunk_parallel`,
  `plan_file_chunks`, the JSON/NDJSON emitters). Each band is a plausible submodule
  (`native_search/types.rs`, `.../walk.rs`, `.../drive.rs`, `.../emit.rs`), which is the same shape
  as the split that already exists in this repo at the crate level (many small `pub mod`s in
  `lib.rs` rather than one large one) — just one level deeper.
- **Verdict:** test-extraction is TRACTABLE today by the reasoning in §3 (same-crate module-file
  move, no privacy change). The production split into the four bands above is a NEEDS-CI-EXPERIMENT:
  it is very likely mechanical (bump each moved item's visibility only where the parent still calls
  it, let `cargo check` name every miss), but "very likely mechanical" is exactly the class of claim
  this document is told not to assert without compiling, and there are enough of these functions
  (85) that "the compiler will catch it" is a different risk profile from "there is nothing to
  catch." `ParallelWalkWorker`'s `Drop` impl (`native_search.rs:746`) is the one spot here worth the
  same caution as `gpu_native.rs`'s `Drop` impls — moving it away from the fields it manages should
  be a single self-contained submodule move, not split across the boundary.

### `rust_core/src/index.rs` — 3,092 lines — **TRACTABLE, closest to done**

- `#[cfg(test)] mod tests` spans `index.rs:1755-3092` — **1,338 lines**, again running to EOF
  (confirmed by `tail -5`). Extracting it drops the file to **1,754 prod lines** — only **254 lines
  (17%) over the 1,500 limit**, the closest of any of the four to clearing the bar from
  test-extraction alone.
- 21 `pub fn`, 44 private `fn` — the lowest private/public ratio of the four, and the file already
  has clean structural seams by inspection: a binary codec block
  (`index.rs:250-503`: `normalize_postings`, `read_u8/u32_le/u64_le/u128_le`, `write_varint_u32`,
  `read_varint_u32`, `bincode_serialize`, `bincode_deserialize`, `hex_to_trigram` — ~250 lines, no
  dependency on the rest of the file beyond raw bytes in/out), the core `TrigramIndex` struct plus
  its main `impl` block (`index.rs:151-249` and `718-1393`, the latter 675 lines by itself — the
  single largest indivisible-looking block in the file), and a regex-literal-prefilter block
  (`index.rs:1610-1750`: `select_regex_prefilter_literals`, `extract_edge_literal_plan`,
  `extract_inner_literal_plan`, `normalize_prefilter_literal`, `compare_regex_literal_plans`,
  `extract_trigrams` — self-contained regex-HIR analysis with no `TrigramIndex` state).
- **First move, concrete:** extract `mod tests` (index.rs:1755-3092) to `#[path =
  "index_tests.rs"] mod tests;`, landing at 1,754 lines. Then move the codec block
  (`index.rs:250-503`, ~250 lines) to `index/codec.rs` (converting `index.rs` to `index/mod.rs`) —
  it is called only from `bincode_serialize`/`bincode_deserialize`'s callers inside `TrigramIndex`'s
  impl, a small, countable set of call sites to re-point as `codec::bincode_serialize(...)`. That
  alone should land `index.rs` at or under the 1,500 limit; the regex-prefilter block is a second,
  independent, similarly self-contained candidate if more headroom is wanted.
- **Verdict: TRACTABLE.** This is the file where I am most confident a real split would go smoothly,
  because the seams are self-contained (codec functions take/return raw bytes; the prefilter
  functions take a `Hir`/pattern string and return a plan struct — neither touches `TrigramIndex`'s
  internal fields), the private/public ratio is the lowest of the four, and the arithmetic already
  gets it within reach without touching the large `impl TrigramIndex` block at all. It is still
  listed as needing the same one CI experiment as the others (§5) before being called done, because
  "self-contained by inspection" is a hypothesis about coupling, not a compiled fact.

---

## 5. The exact minimal CI experiment

One experiment settles the mechanism for all three library files and the binary at once, cheaply:

1. Pick **`index.rs`**, the file with the fewest private items and the clearest arithmetic case for
   a two-step win (§4.4).
2. Convert its inline `#[cfg(test)] mod tests { ... }` to `#[path = "index_tests.rs"] mod tests;`
   with the body moved verbatim into `rust_core/src/index_tests.rs`. No other change.
3. Push to a branch and let CI run its normal `test-rust-core` job (and, separately, confirm
   `cuda-feature-check` is unaffected since `index.rs` is not feature-gated).
4. Read the result:
   - **Compiles, same test count, same pass/fail per test** → confirms §3's "does not block" claim
     for the case with the most private items reached via `use super::*` (44 private `fn`, more
     than any of the codec/prefilter candidates alone) — proceed with the same move on
     `native_search.rs`, `gpu_native.rs` (inside the `cuda-feature-check` job), and `main.rs`
     (`#[path = "main_tests.rs"]`, verified as a **binary**-crate module rather than a library one —
     the one case §2 could not fully collapse to "same as the others" without compiling).
   - **Fails to compile** → something in this analysis is wrong (a macro, a `build.rs` step, or a
     tool that assumes `mod tests` is inline — none found by inspection, but inspection is not
     compilation) — stop and re-derive rather than assuming the fix is obvious.
5. A **second**, independent experiment for `index.rs` only: after step 2 succeeds, additionally
   extract the codec block (`index.rs:250-503`, pre-extraction line numbers) to `index/codec.rs`
   with `bincode_serialize`/`bincode_deserialize`/`hex_to_trigram` becoming `pub(crate)` (or `pub`
   if any external caller needs them — `grep -rn 'index::bincode_serialize\|index::hex_to_trigram'`
   across `rust_core/` first to check). This settles whether the "compiler catches every miss"
   claim in §3 holds in practice for a *production* move, not just a test-module move, which is the
   piece of this document with the least direct evidence.

Both experiments are one CI round-trip each, touch one file's physical layout with (for step 2) or
without (for step 5's codec split) any behavior change, and each has an unambiguous pass/fail
readout — compile or not, same tests or not.

---

## 6. The other four core-Rust violations (not the brief's subject, noted for completeness)

All four (`backend_ast.rs`, `backend_ast_workflow.rs`, `backend_cpu.rs`, `python_sidecar.rs`) follow
the identical shape confirmed by `grep -n '^mod tests\|^#\[cfg(test)\]$'` on each: production code,
then an inline `#[cfg(test)] mod tests { ... }` at the tail (`backend_ast.rs:2053-2553`, 500 lines;
`backend_ast_workflow.rs:1579-2109`, 530 lines; `python_sidecar.rs:1082-1490` plus a second block
`1490-1519` named `mod tests_h3`). `backend_cpu.rs` is the outlier — five separate `#[cfg(test)]`
markers (`backend_cpu.rs:282,303,309,315,1088`) rather than one block, suggesting several small
test-only items scattered through the file rather than one contiguous module; it would need the
same brace-balance measurement as §1 per marker before any move, not assumed to be one block.

None of these were re-derived to file:line precision here since they were not the brief's subject;
the §3 mechanism (module-to-file move preserves `use super::*` visibility regardless of physical
file) applies to all of them identically, and each is a smaller, lower-priority version of the same
NEEDS-CI-EXPERIMENT-then-TRACTABLE shape found in §4. `python_sidecar.rs` is only 19 lines over the
limit — its `mod tests_h3` block alone (`python_sidecar.rs:1491-1519`, 29 lines) would clear it.

---

## 7. What I could not determine without compiling

- **Whether `cargo check` actually accepts `#[path = "..."] mod tests;` pointing at a sibling file
  with zero other changes**, for any of these five files. This is standard, specification-level
  Rust and I am treating it as very likely true, but "very likely true by reading the reference" is
  not the same evidence class as a green CI run, and §5 names the one-file experiment that would
  make it a measured fact rather than an inference.
- **Whether any of the ~230 private functions in `main.rs`, the ~85 in `native_search.rs`, or the
  ~90 in `gpu_native.rs` are reached through a path this survey's `grep`-based visibility count
  cannot see** — a closure capturing a private free function by name, a `impl Trait for ...` method
  dispatched dynamically, or (unlikely here, none found by `grep -rn 'spec_from_file_location\|
  include!' rust_core/src/`) a build-time code-generation step. `grep -c 'include!' rust_core/src/*.rs`
  and the `#[path` search in §3 both returned zero, but a zero from a text-based scan is a weaker
  claim than a compiler telling you the same thing — flagged per this repo's own "a zero is
  MEASURED-NOTHING or a real negative" law, not asserted as a proven absence.
- **Whether moving `gpu_native.rs`'s CUDA-graph-capture code (`Drop` impls at `gpu_native.rs:681`
  and `719`, unsafe CUDA handle teardown) across a module boundary changes anything about drop
  order or unsafe-code soundness.** Rust's drop order is determined by scope, not module boundary,
  so a pure file-move should not affect it — but this is exactly the kind of unsafe-code claim this
  document was told to hold to a higher bar, and it only runs under the narrower
  `cuda-feature-check` CI job (§4.2), so a mistake here would be the easiest of anywhere in this
  survey to miss on a default `test-rust-core` run.
- **The actual edit count for any real production split** (analogous to the Python doc's "393 Route
  A edits" table from `scripts/cost_split_floor_routes.py`). No Rust-side equivalent tool exists in
  this repo, and building one accurately would need to model `pub`/`pub(crate)`/private crossing
  child-module boundaries per candidate split — plausible future work, out of scope for this
  analysis pass, and not something I estimated by hand rather than build, per this repo's own
  "measure, don't estimate" rule.
