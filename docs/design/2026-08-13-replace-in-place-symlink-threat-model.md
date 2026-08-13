# Threat model: `replace_in_place` symlink policy (RUST-REPLACE-SYMLINK)

> Base: `origin/main` `c04fccf44ee7f3efd2294eadf00a8578b53bbe06` (2026-08-13). All code citations
> verified against this tree. **Rev 3 (council round 2):** the contested junction fact was
> settled by a bounded probe on the pinned toolchain (§5); GATE-W3A-1 resolves to **(a) REFUSE**
> (§4). Tense fixed throughout: everything the guard/tests will do is marked **planned (W3B)**,
> because none of it exists on `origin/main` yet.

## 1. What the code does today (citations verified against origin/main)

`rust_core/src/backend_cpu.rs`:

- **Directory mode today: static child symlinks are skipped; child leaf TOCTOU remains open.**
  `walk_directory_entries` (fn signature at `:493`) calls `WalkDir::new(path_obj)` at `:507`.
  walkdir 2.5.0 (`rust_core/Cargo.toml`, pinned) defaults to `follow_links(false)` for entries —
  so a pre-existing child symlink is not descended — **but `follow_root_links(true)` for the
  root**, so a root that is itself a symlink or junction IS followed. Both
  `replace_directory_literal` (`:519`) and `replace_directory_regex` (`:545`) additionally skip
  entries where `!entry.file_type().is_file()`. This is static-symlink safety only: an entry
  enumerated as a regular file is later re-opened by pathname (`OpenOptions::open` at `:590`/
  `:647`), so a child exchanged for a symlink after enumeration is still followed (the leaf race,
  §4).
- **The explicit-file arm follows.** `replace_in_place` (`:440`) branches on
  `path_obj.is_file()` at `:451` (`Path::is_file()` follows), then opens at `:590` (literal) and
  `:647` (regex) — both follow — and mmap-rewrites.
- **Reachability, stated honestly.** `replace_in_place` is `pub fn` with **no `tg` CLI caller**;
  the only in-tree callers are `rust_core/src/backend_cpu.rs` itself and
  `rust_core/tests/test_replace.rs`. Library-surface hardening ahead of exposure (A40), not a
  live CLI exploit.

## 2. The CVE class (reused receipts, not re-derived)

From `docs/audits/2026-08-12-research-receipts.md`:

- **sed CVE-2026-5958** — in-place `-i` symlink-follow overwrite of the link target (TOCTOU).
- **uutils coreutils GHSA-239g-2685-54x3 / CVE-2026-35356/35359** — same class in Rust
  `coreutils`.
- **Capgo CLI CVE-2026-56236** — in-place write following a caller-supplied symlink.
- **rsync GHSA-4h9m-w5ff-j735** — path-check vs open race in a copy/overwrite primitive.

Shared shape: *a security decision made from a path string that the kernel re-resolves at open
time*.

## 3. The planned guard and what it covers (W3B, planned — nothing here exists on main yet)

**Planned (W3B):** a `symlink_metadata(path_obj)` check placed immediately after
`let path_obj = Path::new(path);` and **before the `fixed_strings` fast-path branch**, failing
closed on `Err(stat)` AND on `file_type().is_symlink()`:

- **covers** every route into `replace_file_literal` and `replace_file_regex` (fixed-string fast
  path and regex path both sit behind the branch; no second entry point from
  `replace_in_place`).
- **covers** a symlink OR junction ROOT before `is_dir()` can hand it to `WalkDir` — per the
  probe in §5, junctions report `is_symlink() == true` on the pinned toolchain.
- **does NOT claim** the race is closed: `symlink_metadata` then `open` remains racy (A38).
  The shipped guard converts a 100%-reliable static-symlink overwrite into a race an attacker
  must win. The residual race — and any walk-time reparse-point descent that survives the
  probe-backed guard — is owned by `RUST-REPLACE-TOCTOU` (§4).

## 4. GATE-W3A-1 resolution — (a) REFUSE

Rounds 1–2 split between "junctions are invisible to the guard" (FOLD) and "junctions are
symlinks on this toolchain" (REFUSE). The bounded probe (§5) settled it: **junctions ARE
`is_symlink()`-true on the pinned Rust 1.96.0**, so the static guard refuses them as a free
consequence — leaf AND root. **Outcome: (a) REFUSE.**

Consequences folded into W3B:

- A **Windows junction test** pins the behavior: `replace_in_place` on a junction (root or a
  junction encountered as a directory child) returns `Err` and the target directory's files are
  untouched. Note a junction can only target a **directory** (`mklink /J` refuses file targets),
  so there is no junction-as-file-leaf case to test; file leaves are symlink-file territory
  (already covered by the main refuse test). The fixture asserts the junction BITES first
  (`symlink_metadata(...).file_type().is_symlink()` is true before the call — A88 fixture
  discipline), with a skip-with-reason (`CANNOT_MEASURE`) if the runner cannot create
  junctions.
- The board Trigger for `RUST-REPLACE-SYMLINK` claims exactly what is built: static no-follow
  guard (symlink_metadata, fail-closed) covering symlinks and junctions, root refusal, and the
  residual-race characterization pin.
- `RUST-REPLACE-TOCTOU` (filed in W8) keeps the **leaf race** (swap window between stat and
  open) and any residual walk-time reparse descent; its trigger names the leaf-open mechanisms
  (`O_NOFOLLOW` on POSIX, `FILE_FLAG_OPEN_REPARSE_POINT` / handle-reopen on Windows). Note the
  mechanism boundary stated honestly: those flags gate LEAF opens; they do not by themselves
  gate walk-time directory descent — that half belongs to the same row's trigger text, not to
  this PR's claims.

## 5. Junction probe receipt (bounded probe, pinned toolchain 1.96.0, 2026-08-13)

Tiny std-only crate, `cargo run --release` on this box, real `cmd /c mklink /J` junction:

```text
mklink status: exit code: 0 stdout: Junction created for ...\junc_link <<===>> ...\target_dir
is_symlink: true
is_symlink_dir: true
is_symlink_file: false
Path::is_file: false
Path::is_dir: true
OpenOptions::open through junction: true
```

Read: on the pinned toolchain, a junction reports `is_symlink() == true` (supersedes the
"junctions are NOT symlinks" note in A88 for this toolchain), `Path::is_file`/`is_dir` follow
it, and `OpenOptions::open` opens through it. So today `replace_in_place(path_to_junction_root)`
walks the junction target and rewrites its files, and the planned pre-branch guard closes that
route.

## 6. Contract changes W3B makes (planned; the current tree has none of these)

1. **Missing-path behavior changes.** Today a nonexistent path is a silent `Ok(())` no-op
   (pinned by `test_rust_replace_in_place_direct_file_nonexistent_path_is_currently_a_silent_no_op`
   at `rust_core/tests/test_replace.rs`, both literal and regex arms). Fail-closed stat handling
   turns that into an `Err` naming the path — "could not determine the type" must never mean
   "assume safe". The pin test FLIPS from `Ok` to `Err` in the same PR and is renamed to match
   the new contract. **The same flip covers broken-symlink paths** (stat succeeds, type is a
   symlink, guard refuses) — named here so it is a planned change, not a surprise.
2. **Symlink/junction root now refuses.** New W3B test: a directory-target symlink (POSIX) and a
   junction (Windows) passed as `path` return `Err`, and the target directory's files are
   untouched. Junctions target directories only, so the junction arm exercises the directory
   route; there is no file-target-junction case.

## 7. Compatibility decision: no-follow-by-default, fail-closed

**Chosen:** `replace_in_place` refuses a path that is a symlink (or junction), with an `Err`
naming the path and instructing the caller to pass the resolved target explicitly. Stat errors
fail closed too.

**Rejected alternative:** an opt-in `--follow-symlinks`-style parameter would replicate the sed
surface that earned CVE-2026-5958, and no consumer is requesting it.

## 8. Council record

- Round 1: 8 dispatched, 7 verdict-bearing (1 TIMEOUT = failed). 1 APPROVED / 6
  CHANGES_REQUIRED → rev 2 (root-follow falsification, FOLD default, contract-change naming).
- Round 2: 8/8 verdict-bearing. 4 APPROVED / 4 CHANGES_REQUIRED → rev 3 (junction probe, (a)
  REFUSE, static-vs-TOCTOU wording, planned-vs-present tense).
- Round 3: 6 verdict-bearing (2 failed seats = not votes). 4 APPROVED / 2 CHANGES_REQUIRED, both
  on the same nit → rev 4: the impossible file-target-junction case removed (junctions target
  directories only; the junction arm tests the directory route).
- The empirical junction question consumed two rounds because two seats asserted opposite facts
  without a common probe; the bounded probe above is the only artifact both can cite.
