# 2026-08-02: `rust_core::backend_cpu::CpuBackend::replace_in_place` -- retain + harden

Task 5 (Rust half) of `docs/plans/2026-08-02-backlog-closeout-implementation-plan.md`. Branch
`fix/rust-replace-in-place-hardening`. This is a caller-census + error-propagation hardening pass
on a public Rust method -- it does not touch the Python `cpu_backend.py` adapter (that half of
Task 5 already shipped as PR #923).

## Step 1: search-instrument results (before any edit)

Run from the repo root against `origin/main`:

```
rg -n -w "replace_in_place" rust_core src tests docs
rg -n "replace_in_place|PyO3|pymethods|pub use|extern.*C|match.*replace" rust_core/src src
```

**Positive control (`search_with_paths`, a known-called sibling public `CpuBackend` method):**

```
rust_core\src\backend_cpu.rs:302:    pub fn search_with_paths(
rust_core\src\backend_cpu.rs:377:            .search_with_paths(pattern, path, ignore_case, fixed_strings, invert_match)?
```

Real caller found (`search` calls `search_with_paths` at `backend_cpu.rs:377`) -- confirms the
search methodology surfaces a method known to be called. A second control, `count_matches`,
surfaced its own real caller in `rust_core/src/lib.rs:185` (the PyO3 `#[pymethods]` bridge) plus
dozens of Python-side call sites. **Conclusion: the search technique works; a zero result for
`replace_in_place` is not an artifact of a broken grep.**

**Target (`replace_in_place`) results:**

```
rust_core\src\backend_cpu.rs:417 (approx, pre-refactor)  pub fn replace_in_place(
rust_core\tests\test_replace.rs                          9 call sites, all test fixtures
docs\BACKLOG.md                                           2 prose references (both now updated)
docs\plans\2026-08-02-backlog-closeout-implementation-plan.md   plan prose
docs\plans\2026-08-02-backlog-closeout-design.md          design prose
```

No hit in `rust_core/src/lib.rs`'s `#[pymethods]` block, no `pub use` re-export, no match in
`src/` (the Python side). **`replace_in_place` has zero in-repo Rust callers and is not exposed
through the PyO3 FFI bridge today.**

## Step 1 conclusion (per the plan's explicit instruction)

`replace_in_place` is a `pub fn` on `pub struct CpuBackend`, in the public `backend_cpu` module,
which is part of `tensor_grep_rs`'s `rlib` crate-type target (`crate-type = ["cdylib", "rlib"]` in
`rust_core/Cargo.toml`). An in-repo zero-caller result does **not** authorize deleting or
narrowing a public method on an `rlib`: any downstream Rust crate that depends on
`tensor_grep_rs` as a library (not just the `cdylib`/PyO3 wheel this repo ships) can call it, and
this repo cannot enumerate those consumers. The method and its exact
`fn(&CpuBackend, &str, &str, &str, bool, bool) -> anyhow::Result<()>` signature are retained
unchanged. Removal, if ever warranted, requires a separate breaking-API decision with deprecation
and migration planning -- out of scope here.

## What changed

`rust_core/src/backend_cpu.rs`:

- Directory-mode error handling was previously `let _ = self.replace_file_literal(...)` /
  `let _ = self.replace_file_regex(...)` inside a `WalkDir::new(...).into_iter().filter_map(|e|
  e.ok())` loop -- both a `WalkDir` walk error and a per-child replace/write failure were silently
  discarded, and the public method always returned `Ok(())` regardless.
- The directory-walk step now collects entries up front
  (`.collect::<Result<Vec<_>, _>>()`) instead of processing while walking, so a walk failure is
  caught distinctly from a per-child replace failure, and is propagated as `Err(...)` naming the
  failing directory.
- Each per-child literal/regex replace failure now propagates as `Err(...)` naming the failing
  child path and which operation (literal/regex replace) failed, instead of being swallowed.
- The public `replace_in_place` unconditionally delegates through this same core for both file and
  directory modes (direct-file mode already delegated through `replace_file_literal`/
  `replace_file_regex` via `?`, unchanged).
- A compile-time public-signature guard was added to `rust_core/tests/test_replace.rs`:
  `const _: fn(&CpuBackend, &str, &str, &str, bool, bool) -> anyhow::Result<()> =
  CpuBackend::replace_in_place;`
- Three narrow `#[cfg(test)]`-gated fault-injection seams (`ReplaceFaultInjection`, a
  `std::sync::Mutex`-backed field on `CpuBackend`, `Mutex` rather than `RefCell` because the
  `#[pyclass] RustBackend` wrapping `CpuBackend` requires `Sync`) let the in-file `#[cfg(test)] mod
  tests` unit tests force: a directory-walk failure, a literal-mode child failure, and a
  regex-mode child failure -- each independently, each proven to fire through the real delegated
  core (not a shadow/parallel path), each proven RED against the pre-fix discarding behavior and
  GREEN after the fix. These fields/checks compile away entirely from the normal (non-`--cfg
  test`) `rlib` build that `rust_core/tests/*.rs` external integration tests and every downstream
  consumer link against, so no external test and no consumer can reach them.

## Documented follow-ups (explicitly out of scope for this task)

> **RESOLVED by `RUST-REPLACE-SYMLINK` (2026-08-13, PR #1010):** both bullets below were
> open when this doc was written; the campaign closed them. The nonexistent-path silent
> `Ok(())` is now an `Err` naming the path (fail-closed `symlink_metadata`, pinned by
> `test_rust_replace_in_place_direct_file_nonexistent_path_errors_with_the_path_named`), and a
> direct symlink/junction leaf or root is now REFUSED (threat model
> `docs/design/2026-08-13-replace-in-place-symlink-threat-model.md`). The residual
> stat-vs-open leaf race and walk-time child swap are owned by `RUST-REPLACE-TOCTOU`. The
> historical text is retained below, superseded.

- **`RUST-REPLACE-NONEXISTENT_PATH` -- RESOLVED.** A nonexistent direct-file path was a silent
  `Ok(())` no-op (pinned by the since-renamed
  `test_rust_replace_in_place_direct_file_nonexistent_path_is_currently_a_silent_no_op`); it is
  now an `Err` naming the path per the fail-closed guard.
- **`RUST-REPLACE-SYMLINK` -- SHIPPED (guard).** Direct-leaf-symlink follow was unchanged; it is
  now refused at the leaf AND the root (symlinks and Windows junctions on the pinned toolchain).
  The residual race stays open under `RUST-REPLACE-TOCTOU`.

Neither follow-up was silently absorbed into "CPU-BACKEND done" -- both remain open rows,
separate from this task's closure, per `docs/BACKLOG.md`.

## Gates

- `cargo fmt --all -- --check`: clean after `cargo fmt --all` was applied to the new code.
- `cargo clippy --all-targets -- -D warnings`: 3 pre-existing findings in `native_search.rs` and
  `rg_passthrough.rs`, byte-identical to a clean `origin/main` checkout (verified independently in
  a separate clone) -- unrelated to this change, no clippy findings in `backend_cpu.rs`.
- `cargo test -p tensor_grep_rs` (`--no-fail-fast`): 648 tests passed across 33 test binaries
  (unit + integration + doctests). 3 pre-existing failures in `test_sidecar_ipc.rs`
  (`test_tg_classify_stdout_matches_python_module` and its `--stdin`/`--text` siblings) are a
  Windows CRLF-vs-LF byte mismatch, verified identical on a clean `origin/main` checkout --
  unrelated to `replace_in_place`/`backend_cpu.rs`.
