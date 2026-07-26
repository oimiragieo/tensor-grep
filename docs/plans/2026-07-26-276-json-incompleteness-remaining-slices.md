# Task 276: Remaining Slices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Citation convention — `task NNN` vs `#NNN`.** This repo runs two independent numbering
> spaces that overlap, and rendered Markdown does not distinguish them: GitHub auto-links any
> bare `#NNN`, including one that names a *local task*. Every such citation therefore resolved to
> an unrelated merged PR — `#276` (this campaign) linked to "chore: post-release Docker dogfood
> harness"; `#319` linked to a GPU fixed-string PR. So: **local tasks are written `task NNN`,
> with no `#`, and only real GitHub issues/PRs keep the `#`.** The two `#` refs below (#795, #793)
> are genuine PRs and are meant to link.

**Goal:** Close the remaining seams of task 276 so every machine-facing `tg` output path can say "I could not finish looking" — and say it *truthfully*.

**Status: REVISED 2026-07-26 after adversarial review falsified two of my own tasks.** The revision notes are kept inline rather than cleaned up, because the errors are instructive and a reader who does not know what was wrong will reintroduce it.

**Tech Stack:** Rust (`rust_core/`, `ignore` crate walk, `serde_json`), Python CLI (`src/tensor_grep/cli/`), pytest, GitHub Actions.

## Global Constraints

- **Rust is CI-verified ONLY.** `cargo`/`rustc`/`maturin`/`clippy` are forbidden here (CPU cost, shared box). `rustfmt --check` IS allowed and required before pushing. CI is the only oracle for Rust correctness.
- **Closed vocabulary, do not extend.** `incomplete_reason_class` ∈ {`unreadable_path`, `scan_limit`, `deadline`, `timeout`}. Separate from MCP's hyphenated `truncation_cause` — do NOT unify (task 293).
- **Additively conditional.** Emit only when non-empty so a complete result stays byte-identical.
- **Fail-closed guidance is an ALLOW-LIST** (task 282).
- **Bidirectional oracle per task.** Restore the defect, watch it fail, then fix. **And first ask whether the defect can occur at all** — see the Task 1 post-mortem.
- `ruff format --preview` before pushing Python/Markdown (`docs/plans` is NOT in `extend-exclude`).

## What the review changed

Three lenses reviewed the first draft. Two returned; both found real errors, and they **disagreed with each other** on task 321. The disagreement was resolved by reading the code directly rather than by counting votes.

| Claim | First draft | Verified reality |
|---|---|---|
| task 319 Drop-guard defect | "CONFIRMED real" | **Unreachable.** `searched_files += 1` (`:536`) precedes `binary_match_files += 1` (`:547`), so `binary_match_files > 0 ⟹ searched_files > 0` and the guard cannot fire. My test reached the state only by assigning the field directly — an oracle that cannot discriminate. |
| task 321 | "MIS-FILED" | **Correctly filed, live defect.** See Task 2. |
| `collect_walked_files` | `:1621` | **`:1709`** (`:1621` is inside `search_file_count`, `:1609`) |
| `search_ndjson` | `:619` | **`:629`** |
| `merge_search_stats` | `:1413` | **`:1404`** (`:1413` is the `walk_errors` line inside it) |
| Coverage | native + gpu only | **`main.rs` holds a whole second envelope** — see Task 5. |

Verified-correct anchors: `walk_errors :91`, envelope `:842-846`, emit `:2436-2438`, site 1 `:1330`, site 2 `:1378`, Drop guard `:711`, `gpu_native.rs:3879` / `:3892`.

## Dependency reality

The producer fields (`walk_errors`, the three envelope fields) exist only on `origin/fix-276-slice-a-walk-error-count`, not on `main`. Most tasks therefore sequence behind #795 → #793 → the drain gate.

**Exception found by review:** the `is_empty()` refactor targets code that exists on `main` today (`origin/main:native_search.rs:686-692`). It is the refactor #795 should rebase *onto*, not a dependant. Its only #795-coupled line is `walk_errors == 0`.

---

### Task 1: `SearchStats::is_empty()` — defense-in-depth, NOT a live bug fix

**POST-MORTEM, read this before writing the test.** The first draft called this "The defect, verified" and ranked it first. It is not a defect: the guard cannot fire, because every writer of `binary_match_files` is preceded by `searched_files += 1` in the same block (`:536`/`:547`, and `:1136`/`:1147`). I asserted a defect from *reading the guard's field list* without checking whether the state it guards is reachable — the same error I flag in others, committed while writing a plan that warns about it.

**The refactor is still worth doing**, on honest grounds: it converts "did you remember the new field?" from a per-call-site question into a single-site one, so a *future* field that IS reachable cannot be forgotten. Ship it as a readability/robustness change with that justification, and **do not write a test claiming to reproduce a live loss.**

- [ ] **Step 1:** Add `is_empty()` enumerating every countable field; replace the inline guard.
- [ ] **Step 2:** Test the *invariant*, not a fake bug: assert `is_empty()` is false for each field set individually. That is a real property with a real failure mode (a field added to the struct and not to `is_empty`).
- [ ] **Step 3:** `rustfmt --check`, CI green, commit — targeting `main`, ahead of #795.

---

### Task 2: separate output-write failure from input-read failure (task 321) — LIVE DEFECT

**task 321 IS CORRECTLY FILED.** My first draft called it mis-filed and proposed `if err.is_io()`. That was wrong three ways, all verified: the error at `:1378` is `anyhow::Error` (`search_path` returns `anyhow::Result`, `:509`; the sibling guard takes `&anyhow::Error`, `:307`); `ignore::Error` never reaches there (`:13` is the only `ignore` import), so the `Glob`/`UnrecognizedFileType` variants I named are unreachable; and **`is_io()` does not exist on `anyhow::Error`** — no such method anywhere in `rust_core/`, so the fix would not compile. A plan built on it would have closed a live defect and shipped a type error.

**The real defect.** `search_path` returns `Err` for *output* failures as well as input ones: the buffered flush at `:554` and the binary-match warning at `:546`, both reaching `:104-108`. The only filter before the increment is `search_path_error_is_broken_pipe` (`:1366`), which matches **only** typed `io::ErrorKind::BrokenPipe` (`:307-311`). So `ENOSPC` on a full disk, `EIO` on a dead terminal, or a Windows `ERROR_INVALID_HANDLE` is counted as a walk error and published as `incomplete_reason_class: "unreadable_path"` (`:2437`) — a *wrong* disclosure, which is worse than a missing one.

**Live exit-code regression rides along.** `main.rs:8373` exits 2 when `walk_errors > 0`, so an untyped broken pipe now exits 2 instead of `BROKEN_PIPE_EXIT_CODE = 1`. The comment at `:300-306` calls missing the untyped case "not a correctness regression" — true before slice B, false now. A stale comment asserting the old assumption is the tell.

**Reachable in the envelope today:** `count` is tested before `json` (`:512-514`) and `--count` has no `conflicts_with = "json"`, so `tg search -c --json PAT DIR` writes count lines mid-walk *and* emits the envelope.

- [ ] **Step 1:** Tag the write sites (`:546`, `:554`) distinguishably — marker error or `anyhow` context, matched via the existing `err.chain()` pattern (`:307-311`).
- [ ] **Step 2:** Failing test injecting a **failing output target** (`NativeOutputTarget::Buffer`, `:98`), not a failing input path. Assert a non-`BrokenPipe` write failure does NOT yield `unreadable_path`.
- [ ] **Step 3:** Confirm RED on CI. Control: with the fix reverted, `walk_errors` non-zero and envelope shows `unreadable_path`.
- [ ] **Step 4:** Widen the `:1366` abort guard to any output-target write failure — the rationale at `:1360-1362` applies verbatim to `ENOSPC`/`EIO`.
- [ ] **Step 5:** Fix the now-false comment at `:300-306`; note the exit-code fix in the PR body; `rustfmt --check`; commit.

---

### Task 3: rename `incomplete_paths_count` INSIDE #795 — do not build a path set (task 320)

**REVERSED from the first draft.** I proposed carrying `HashSet<PathBuf>`. The code I would have edited **documents that exact design as rejected**, at `native_search.rs:82-90`: the count is "deliberately a COUNT, not a path list", because an unbounded per-path Vec behind a mutex is "both a contention point on the hot walker and a DoS surface (a tree with 50k unreadable entries would produce a 50k-entry payload)". I proposed the rejected design without citing the comment that rejected it.

My justification was also self-contradictory: I said renaming costs a major bump because the name "is already shipped in #795" — while the same plan states #795 is unmerged. **An unmerged field is not a shipped contract.**

**Rename cost verified as ZERO.** `CONTRACTS.md:171` binds *existing* (published) fields; `:172` allows new fields in minor versions. A repo-wide grep for `incomplete_paths_count` returns exactly one file — this plan. Nothing named it has ever shipped.

**Two further hazards the HashSet design carries**, beyond the documented rejection:
- **Memory:** ~8 bytes today vs ~8.5 MB at the 50k unreadable paths the shipped comment names, and ~170 MB at 1M (ACL-denied mount). An incompleteness signal that OOMs on the most incomplete tree is the defect inverted.
- **Nondeterminism:** `SearchStats` derives `Serialize` (`:74`). A `HashSet` serializes in per-process random order, so any stats dump stops being byte-reproducible — it would break the determinism gate (task 311) by construction. If a set is ever used it must be `BTreeSet`.

**And the first draft's Step 4 was unimplementable under this plan's own rules:** a "capped" disclosure is a new field with new meaning, while the Global Constraints forbid new vocabulary.

- [ ] **Step 1:** Rename inside #795 before merge — `incomplete_event_count` or `walk_error_count`. Two-line diff (`:846` struct, `:2438` emit) plus #795's own CONTRACTS bullet. Keep it DISTINGUISHABLE from MCP's `unreadable_path_count` (`CONTRACTS.md:176`), which is a genuine per-path count (`directory_scanner.py:227`, once per failed `os.scandir`) — converging them repeats the task 293 mistake.
- [ ] **Step 2:** Update `docs/CONTRACTS.md` in the same commit.
- [ ] **Step 3:** **If a path-shaped signal is genuinely wanted, copy the SHIPPED precedent, do not invent one.** `directory_scanner.py:75, 116-117, 227-230` already pairs an uncapped count with a bounded `unreadable_path_sample` (`_MAX_UNREADABLE_PATH_SAMPLE = 5`): O(5 × path-len), no set, no dedup, no cap vocabulary, and it gives a human an actionable path. Already in the product, already contract-documented, and it crosses the A27 twin rule for free.
- [ ] **Step 4:** Before building any distinct-path counting, VERIFY the premise the first draft asserted without citation — that the walker can emit more than one error for one path. `is_io()` at `:1328` already discards the non-I/O variants and a single-element `Partial` collapses to its inner error (`:1321-1322`), i.e. one increment. If the premise is false, the rename alone closes task 320.

---

### Task 4: `collect_walked_files` count channel (task 315)

**Files:** `rust_core/src/native_search.rs:1709` (corrected anchor).

Its `Err` arm holds an `ignore::Error`, so `is_io()` IS valid here — but that justification must stand on its own, not inherit from Task 2, which concerns a different type at a different site.

- [ ] Failing test → RED on CI → return the count (or accept `&mut SearchStats`) → green + control red → `rustfmt --check`.

---

### Task 5: the SECOND envelope, in `main.rs` (task 317, expanded)

**The first draft named the wrong file.** `collect_native_multi_pattern_matches` (`main.rs:8180`) returns `Vec<SearchMatchJson>` and discards stats — `let stats = execute_native_search(...)` at `:8217` consumes only `stats.matches` at `:8219`. There is nothing to thread at `native_search.rs:889-963`.

**A whole second envelope is uncovered.** `SearchResultJson` is built at `main.rs:12987-13005` and printed at `:13007` — structurally parallel to the native envelope but carrying **none** of the three fields. Six live call sites: `emit_json_search_results` (`:12968`) from `:8256`, `:8952`, `:11214`; `emit_ndjson_search_results` (`:13314`) from `:8264`, `:12462`, `:12797`.

- [ ] **Step 1:** Thread stats through `collect_native_multi_pattern_matches` → `emit_multi_pattern_native_results` (`:8249`) → `emit_json_search_results`.
- [ ] **Step 2:** Cover the warm-index (`:8952`), AST (`:11214`) and GPU (`:12462`, `:12797`) routes, or record explicitly which cannot go incomplete and why.
- [ ] **Step 3:** Byte-identity control on a clean tree.

---

### Task 6: exit-code parity (NEW — was unowned)

#795 implements exit 2 only inside `run_native_search_with_optional_rg_fallback` (`main.rs:8373-8375`). The multi-pattern route ends at `if !has_matches { exit(1) }` (`:8275`) and otherwise returns `Ok(())` → exit 0. Once Task 5 lands the fields, `tg search -e A -e B --json` over an unreadable tree would emit `result_incomplete: true` **and exit 0** — an envelope contradicting its own exit code, worse than either defect alone.

- [ ] Make the exit-2 rule reachable from every route that can now report incompleteness; test one case per route.

---

### Task 7: `--ndjson` terminal summary record (task 314)

**Files:** `rust_core/src/native_search.rs:629` (corrected anchor).

**The record shape is DETERMINED by two opposing consumer constraints — it is not a free choice.** Because the record is conditional on incompleteness, nothing breaks on today's clean-tree fixtures; every finding below is latent and fires the first time a consumer meets an unreadable tree, which is exactly what this work causes.

- **Must INCLUDE the common envelope.** `tests/integration/test_harness_adoption.py:105-109` asserts `all(row["version"] == 1 ...)` and `all("routing_backend" in row ...)` — a record without them KeyErrors.
- **Must OMIT `file` and `path`.** `tests/e2e/test_native_renderer_file_set_invariant.py:143-164` builds its file set from `obj.get("file") or obj.get("path")`. Every ndjson row carries `path` (the search root, `native_search.rs:880`), so including it injects the ROOT into the file set and breaks the `files == plain` invariant.
- `result_incomplete` is therefore the natural discriminator — absent on every match row.

**One unavoidable consumer edit, and it is a crash not a soft failure:** `tests/helpers/rg_parity.py:525-531` loops every line with unguarded `row["file"]`/`row["text"]` → `KeyError` inside the comparator. The `:513` guard does not rescue it. Fix it in the same commit to skip a row lacking `file`.

**`--format rg --ndjson` is NOT an rg-compat route — there is no rg contract to match.** `main.py:5309`'s `_can_passthrough_rg` contains `and not ndjson_mode` unconditionally, and the compat route keys on `--json` only (`:5293`). rg's own `summary` event carries `elapsed_total`/`stats` and **no incompleteness field** — rg signals unreadable paths on stderr + exit 2, never in the stream. Matching its shape buys task 276 nothing and collides with `rg_parity.py:513`'s discriminator.

- [ ] **Step 1:** Define the record in `docs/CONTRACTS.md` **and** `docs/harness_api.md:1016` **and** `docs/harness_cookbook.md:546` — the latter two state "each line is … a single match row" and are pinned by their own tests.
- [ ] **Step 2:** Failing test → RED → emit → clean stream emits NO trailing record.
- [ ] **Step 3:** The "stream ENDS with the record" assertion needs its own harness — `test_output_golden_contract.py:268` sorts lines and cannot see terminal position.
- [ ] **Step 4:** `docs/examples/search.ndjson` is asserted row-by-row to carry `file`/`line`/`text` (`test_harness_api_docs.py:521-525`); safe today, breaks on regeneration.

---

### Task 8: gpu_native.rs twin (task 316)

**Files:** `gpu_native.rs:3879`, `:3892`. Mirror Tasks 2/4; verify the cuda test actually RUNS in CI (task 279 exists because they were only type-checked).

---

### Task 9: MCP contract (NEW — was unowned)

`_TG_MCP_SERVER_CONTRACT_VERSION` is `mcp_server.py:120` (`1.5.0`). Two obligations: (a) MCP's `tg_search` carries `result_incomplete` + free-text `incomplete_reason` but **zero** occurrences of `incomplete_reason_class` — the closed vocabulary never reaches the most machine-facing surface tg has; (b) Task 7's new record type and Task 5's fields are wire changes. Precedent for the bump: `CHANGELOG.md:1358`.

---

### Task 10: `docs/CONTRACTS.md` (task 318) — do LAST

The bullet at `:174` makes four separable claims (backend allow-list; the `:1631`/`:3890` defect anchors; "nobody can compile Rust here"; "exits 0 where rg exits 2"). The first draft's pinning test covered only the backend set, leaving the other three to rot — the exact failure mode this task exists to close.

- [ ] Pin all four → confirm RED against the current doc → rewrite keeping the ALLOW-LIST form (task 282) → `ruff format --preview` → commit doc + test together.

## Self-Review

**What this revision fixes:** two tasks whose premises were false (1, 2), one that re-litigated a documented rejection (3), four wrong anchors, a whole uncovered envelope (5), and two unowned obligations — exit codes (6) and the MCP contract (9).

**Standing limit:** Tasks 4-8 carry step outlines rather than full code, because their shape depends on the tagging scheme chosen in Task 2 Step 1.

**Unverified, flagged rather than assumed:** whether the warm-index and AST routes can actually go incomplete (their envelopes lack the fields; their walkers were not traced), and whether MCP search can ever be answered by the native binary.
