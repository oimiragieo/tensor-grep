# 2026-08-01 Backlog Campaign Implementation Plan (rev 2, post round-1 audit)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Round-1 audit response

Round 1 (`docs/audits/2026-08-01-plan-audit-round1.md`, `docs/audits/2026-08-01-codex-plan-audit.md`)
was a unanimous BLOCK: council 6/6, codex `gpt-5.6-sol` BLOCK with 7 must-fix. Every must-fix was
re-verified against `0126cb3b` by this reviser before being folded in; none was disproved. Two
refinements and one NEW finding beyond the audit are called out below the table.

| MF# | How addressed | file:line evidence |
|---|---|---|
| MF1 | PR-B (Task 5) is REFRAMED as a **policy reversal**, not a bug fix. The pinned test `test_tokenless_server_stays_backward_compatible` is retired with an in-code reason, the PR body states plainly that it reverses documented-intentional behaviour, and the human merger is flagged. The fail-closed choice is re-argued from scratch given the pin (see Task 5 "Why fail-closed still stands"); the keep-the-pin alternative is documented and rejected with reasons, not waved off. | pin: `tests/unit/test_session_daemon_security.py:58-65`; sole production constructor: `src/tensor_grep/cli/session_daemon.py:2068-2069` |
| MF2 | Census re-derived by CALLING each construction site: **16 direct tokenless constructors** — 11 in `test_session_cli.py`, 3 in `test_session_serve.py`, 2 in `test_session_daemon_security.py` — each listed with `file:line` and a per-site disposition in Task 5 Step 4. The two previously-named "harness files" are REMOVED from the census with evidence (their `_real_daemon` helpers default `token="test-token"`). | default `token=""`: `session_daemon.py:1737`; the 16 sites enumerated in Task 5 Step 4; false positives: `tests/unit/test_symbol_daemon_autostart.py:73-75`, `tests/unit/test_session_daemon_version_skew.py:35-38` |
| MF3 | Chose **enumerate-and-migrate** (not moving the validation). Task 3 Step 1a migrates all 15 invalid `"ERROR"` `--ltl` fixtures in `tests/unit/test_cli_modes.py` to valid LTL grammar, each named with `file:line`; the already-valid sibling at `:10453` is the population positive control. Rationale for keeping CLI-boundary validation is stated in Task 3. Count refinement: the grep hits 16 LINES, of which 15 are invalid and 1 (`:10453`) is already valid — the migration population is 15 tests, not 16. | `grep -c '"--ltl"' tests/unit/test_cli_modes.py` = 16; invalid sites `:3838,13282,13322,13345,13368,13393,13430,13544,13578,13610,13635,13699,13733,13770,13795`; valid control `:10453` |
| MF4 | The `test_routing_parity.py` case is **removed as a red-arm receipt** (it skips pre-fix AND post-fix: `_skip_if_native_binary_missing` at `tests/e2e/test_routing_parity.py:165-167`; `test-python` builds no release `tg`, `ci.yml:442-446`; the binary-building job runs only `tests/e2e/test_native_*.py`, `ci.yml:658-660,718-726`). The Rust unit assertion is now explicitly the ONLY mandatory pre-merge Rust red arm, with the exact CI job named: `test-rust-core` stable legs, `cargo test --no-default-features` (`ci.yml:448-513`). Per codex must-fix 3, the full-path guard is rebuilt as a NEW `tests/e2e/test_native_ltl_passthrough.py` inside the `native-build-smoke` glob, with a no-silent-skip mechanism (Task 4 Step 3). | `ci.yml:628` (job), `:658-660` (build), `:718-726` (glob run), `:711-717` (glob-census comment that must be updated) |
| MF5 | The invalid-subexpression test is RELABELLED: baseline GREEN, kept as a **regression guard** (it pins that the new boundary preserves the existing invalid-regex convention), never cited as a red receipt. The per-test red-arm table in Task 3 states each test's pre-fix baseline explicitly. | `_is_invalid_regex_error` at `cli/main.py:3985-3995`; `_exit_invalid_regex` at `:4902-4911` |
| MF6 | PR-D (Task 2) now carries the **mandatory adversarial security gate** (new Step 6) — the trigger is the SURFACE (`apply_policy` + native asset), not the diff shape. Binary `SHIP | FIX-FIRST(file:line + repro)` verdict recorded on the PR before un-drafting. | `AGENTS.md:48-53` (rule A3) |
| MF7 | PR-C (Task 1) expanded to fix ALL known instances: `docs/CONTRACTS.md:253-263` (planned), **plus** `docs/CONTRACTS.md:240` (section 9 "Slice 2 ... still roots itself at PATH taken literally"), **plus** `ledger_store.py::_ledger_physical_root` docstring (`:434-438`, "claims ONLY"), **plus a FOURTH instance this revision found that the audit did not cite**: the section-banner comment at `ledger_store.py:389-391` ("record_finding/find_findings (Slice 2) below deliberately keep plain `_resolve_root`, untouched") — disproved by the same call sites. AST-neutrality proof extended to `ledger_store.py`. A whole-file sweep step guards against a fifth. | call sites: `ledger_store.py:658,797,854,1198,1335`; the already-correct module docstring `:48-57` is the in-file contradiction proof |
| MF8 | Explicit sequencing added (Merge task + PR-A preamble): after C+D merge, capture the newest main run BY RUN ID and wait for `status=completed`; rebase PR-A onto the merged C+D tip and re-run the union before it is mergeable; the A-to-B gate now says `status=completed` on A's captured main run ID **plus** PyPI serving the new version, explicitly. | shared files: `cli/main.py` (PR-C+PR-A), `rust_core/src/main.rs` (PR-D+PR-A); gate law: `AGENTS.md:44-47` |
| also-fix | `jsonlib` is now USED: the JSON test parses the envelope and asserts the full `_search_error_payload` shape (`version`, `schema_version`, `ok`, `error`, `detail`). The "one-line" claim is now ASSERTED (exactly one non-empty line, prefixed `Error:` — the presenter emits exactly one stderr line at `cli/main.py:4865`). | `_search_error_payload` at `cli/main.py:4838-4847`; `typer.echo(f"Error: ...", err=True)` at `:4865`; ruff F-family per `pyproject.toml:94-99` |

**Kept, per the audit's "confirmed CORRECT — do not re-litigate":** the `BackendExecutionError`
taxonomy call (runtime engine-failure type, `backends/base.py:7-12`, retried via
`_search_with_cpu_fallback`, `cli/main.py:8279-8284`) and the `sidecar.py::_classify_lines`
dead-code deletion (held under four lenses). Both unchanged below.

**Decision changes in this revision (not just details):**
1. Task 5 changed from "fix a latent fail-open bug" to "reverse a pinned, documented-intentional
   policy, with the pin retired on the record" — the recommendation (fail closed) survives, but the
   argument had to be rebuilt because the round-1 framing hid a deliberate prior decision.
2. Task 4's full-path belt moved from `test_routing_parity.py` (structurally unobservable) to a new
   `test_native_*.py` suite that the binary-building CI job actually executes, with skip escalated
   to failure in that job.
3. Task 3 grew a test-migration sub-task (15 fixtures) that the round-1 plan did not know it needed.

No must-fix was found to be WRONG. Two precision notes: (a) the migration population is 15 tests
(16 grep lines minus the valid control at `:10453`); (b) beyond the audit's "third lie", a fourth
stale instance exists at `ledger_store.py:389-391` and is now in scope.

---

**Goal:** Drain the verified-open tensor-grep backlog: one MED user-facing bug (`--ltl` raw traceback), one latent front-door registration gap (`--ltl` missing from the native passthrough list), one **policy reversal** on a security surface (`session_daemon.is_authorized` tokenless fail-open, currently PINNED as intentional), four doc/docstring lies (main.py disclosure docstring; CONTRACTS.md sections 9 and 10; ledger_store.py helper docstring + banner comment), one dead function — plus documented retirements for everything else.

**Architecture:** Four PRs. Two non-releasing (docs:, chore:) merge first as a batch; two releasing (fix:) merge one-per-tick afterward. Every behavior change is TDD-first with an observed RED arm; every stated red arm has been checked for observability (a check that skips or passes in both arms is NOT a red arm and is labelled accordingly). The Rust change's RED arm is observed in CI (never locally — shared server).

**Tech Stack:** Python 3.11+ (uv, pytest, ruff 0.15.20-pinned in CI, mypy strict), Rust (rust_core, CI-verified only), Typer CLI, semantic-release on PR titles.

**Base commit:** `0126cb3b8dc67cf4e6310dfe65250f93a016c835` (`git rev-parse origin/main`, verified == local `main` HEAD, clean tree, 2026-08-01). Re-run `git rev-parse origin/main` before starting; if it moved, re-verify every `file:line` anchor below by SYMBOL (anchors here drift 14-500 lines — grep the symbol, never trust the number).

---

## GROUND-TRUTH CORRECTION (one refinement, orchestrator otherwise confirmed)

**Item 2 is not fully latent.** "Latent because bootstrap intercepts first" is true only for the pip-installed entry point (`bootstrap.py:67` / `:525` route `--ltl` to Python before Typer). Users of the **native-frontdoor binary asset** enter through `rust_core/src/main.rs` directly: `--ltl` is absent from `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (verified: `--rank`/`--bm25`/`--semantic` present at `main.rs:314-318`, `--ltl` zero matches — positive control passed), so `search_format_python_passthrough_args` returns `None` and clap rejects `--ltl` as an unknown flag for those users. This upgrades item 2 from "decide fix-or-retire" to **FIX** (see Task 4).

---

## Global Constraints

- **THE DEV BOX IS A SHARED SERVER.** Never run locally: `cargo build/test/check/clippy`, `tests/e2e/test_routing_parity.py` (it self-compiles Rust via `cargo run`), any benchmark harness. All Rust verification is CI's job. The RED arm for the Rust change is observed in a CI run, per the "when you cannot observe RED locally, say so" rule (tensor-grep-validation-and-qa Part 0).
- **Local lint gate:** `uv run --no-sync ruff check .` and `uv run --no-sync ruff format --check --preview .`. **The local venv has ruff 0.16.0; the lock pins 0.15.20.** Local runs report ~2 check errors + 4 files-to-reformat in `docs/` that CI does NOT see. **DO NOT "fix" those** — they are a version artifact; reformatting them reddens main. Only act on findings in files you touched, and reproduce CI's verdict via the PR run before believing any local lint red.
- **Local test gate:** `uv run --no-sync pytest <narrow suite> -q`, then `uv run --no-sync pytest -q --maxfail=0` before push. `uv run --no-sync mypy src/tensor_grep` (strict mode — annotate everything).
- **`ruff format` always with `--preview`; never pass `--preview` to `ruff check`.**
- **Release classes:** `fix:`/`feat:`/`refactor:` RELEASE (merge ONE per publish tick; wait for the `chore(release)` commit on main + PyPI — ~40-66 min — before the next merge; query the release run BY RUN ID via `gh run view <id> --json status,conclusion`, never `gh run list --limit N`). `docs:`/`test:`/`chore:` do not release and may batch.
- **ASCII-only CLI output and test scripts** (emoji/non-ASCII -> cp1252 crash on Windows).
- **Bidirectional oracle per item:** every new test must be OBSERVED red on pre-fix code (git stash protocol locally for Python; CI run for Rust). A check that passes in both arms is broken; **a check that SKIPS in both arms is equally broken** (round-1 lesson — the parity-belt case). A control arm that survives the revert is not a control arm. Every stated red arm below carries its pre-fix baseline and HOW the implementer proves it; anything that cannot be proven observable is labelled "not a red arm".
- **No `inspect.getsource(...)` assertions.** Behavioural/subprocess assertions only.
- **Enumerate populations by CALLING each member** (run each affected test file / read each construction site in full — grep lines can truncate multi-line calls), never "A transitively covers B". Round-1 receipt: the daemon census was wrong in both directions until every constructor was called.
- **A grep zero is UNRESOLVED, not ABSENT** — pair every "0 occurrences" claim with a positive control that matches something known-present.
- **Autonomy is draft-PR-only.** Every PR ends as a draft PR; a human merges.
- **Do not touch `docs/audits/codex-specs/`** (another agent's untracked WIP) or any file outside the ones named per task, except under change-control Part 1 Rule 7 (found-it-fix-it, with a note in the PR).
- **Commit messages:** never backticks/`$`/`!` in `git commit -m` — use `git commit -F <file>` or a single-quoted heredoc.

## PR grouping and merge order

| PR | Title (exact prefix) | Contents | Releases? | Merge order |
|---|---|---|---|---|
| PR-C | `docs: correct disclosure docstring and ledger canonicalization docs` | Task 1 | No | 1st (batchable with PR-D) |
| PR-D | `chore: remove dead _classify_lines wrapper; record apply-policy sentinel retirement` | Task 2 | No | 2nd (batchable with PR-C) |
| PR-A | `fix: clean error for invalid --ltl query and register --ltl on the native front door` | Tasks 3 + 4 | **Yes (patch)** | 3rd — see collision gate below |
| PR-B | `fix: session daemon fails closed without a token (reverses pinned tokenless policy)` | Task 5 | **Yes (patch)** | 4th — after PR-A's release gate |

Work on all four branches may proceed in parallel (build-vs-merge decoupling); only MERGES are
sequenced. Branch each PR from current `main`.

**Collision-aware sequencing (MF8).** PR-C and PR-A both edit `src/tensor_grep/cli/main.py`;
PR-D and PR-A both edit `rust_core/src/main.rs`. A branch rebased only before its first push goes
stale the moment C/D land. Therefore:

1. Merge PR-C and PR-D (batch OK — no release in flight).
2. Capture the run ID of the newest `ci.yml` run on main triggered by that merge
   (`gh run list --workflow ci.yml --branch main --limit 1 --json databaseId,headSha` immediately
   after merging, verify `headSha` matches the merge commit; from then on query THAT ID only:
   `gh run view <id> --json status,conclusion`). Wait for `status == "completed"` and
   `conclusion == "success"` (a `cancelled` run superseded by a newer push is not actionable —
   re-capture on the newest main commit).
3. **Rebase PR-A onto the merged C+D tip** (`git fetch && git rebase origin/main`), verify
   `git merge-base --is-ancestor origin/main HEAD`, re-run the full local union
   (`pytest -q --maxfail=0`, mypy, ruff) on the rebased branch, force-push with lease. Only then is
   PR-A mergeable.
4. Merge PR-A. Capture its main-run ID the same way. The **A-to-B gate is explicit**: that captured
   run must reach `status == "completed"` with `conclusion == "success"`, AND the `chore(release)`
   commit must be on main, AND PyPI must serve the new version (`AGENTS.md:44-47`). All three;
   a tag or a still-running run is not the gate.
5. Rebase PR-B onto post-A main, re-run its union, then merge PR-B and repeat the release wait.

---

### Task 1 (PR-C, `docs:`): fix the FOUR document lies (was two)

**Files:**
- Modify: `src/tensor_grep/cli/main.py` (docstring only — the paragraph inside the helper whose body follows at the `if caveat is None:` check, currently `:11650-11655`; anchor by grepping `Three is the count of emitters CONVERTED`)
- Modify: `docs/CONTRACTS.md` (TWO sites: section 10 opening paragraph, currently `:253`, anchor by grepping `do NOT canonicalize`; AND the section 9 PATH-scoping bullet's closing sentence, currently `:240`, anchor by grepping `still roots itself at`)
- Modify: `src/tensor_grep/cli/ledger_store.py` (TWO prose-only sites: the `_ledger_physical_root` docstring, currently `:434-438`, anchor by grepping `Used by`; AND the section-banner comment currently `:389-391`, anchor by grepping `deliberately keep plain`)

**Interfaces:** none — prose only. Behavior-neutrality of BOTH `.py` edits is PROVEN, not eyeballed (step 5).

- [ ] **Step 1: Verify all four lies against current code (do not trust this plan's snapshot).**

```bash
grep -n "_emit_scan_incompleteness_banner(" src/tensor_grep/cli/main.py
```

Expected: ~12 call sites (verified 2026-08-01: 8813, 9378, 9825, 9862, 10079, 10163, 10301, 10336, 10728, 11287, 13122, 13210 plus the def at 11664). For EACH of the six commands the docstring claims "exit 2 while saying nothing in text at all" (`map`/`context`/`context-render`/`edit-plan`/`blast-radius-render`/`blast-radius-plan`), open the command body and confirm which banner call site it owns — enumerate by CALLING each member, not by matching the count. Also verify the docstring's OTHER claim ("`code-map`, `route-test`, `session open` and `agent` trail their disclosure") the same way — if any of those four has since been wired too, the replacement text must reflect it. Record the per-command findings in the PR description.

For the ledger lies, the ground truth is one derivation shared by all three prose sites:

```bash
grep -n "_ledger_physical_root" src/tensor_grep/cli/ledger_store.py
```

Expected: the def at `:434` plus call sites inside `submit_claim` (`:658`), `release_claim` (`:797`), `list_claims` (`:854`), `record_finding` (`:1198`), `find_findings` (`:1335`). If `record_finding`/`find_findings` do NOT appear, STOP — the ground truth is wrong and this task inverts (report back instead of editing). Note the in-file contradiction proof: the MODULE docstring (`ledger_store.py:48-57`) already states both Slice-2 entry points use the helper ("It has now been reported ... both entry points now use `_ledger_physical_root`"), while the helper's own docstring (`:434-438`) and the banner comment (`:389-391`) still assert the opposite.

- [ ] **Step 2: Rewrite the stale main.py docstring sentence.**

Replace the sentence spanning "and ``map``/``context``/... exit 2 while saying nothing in text at all." with a claim that cannot rot into a false enumeration (an enumeration in prose rots the moment the set grows — 2026-08-01 workspace law). Replacement shape (adjust to Step 1's findings):

```
Three is the count of emitters CONVERTED to this ORDERING helper, not of emitters that
disclose at all: the leading-banner path (``_emit_scan_incompleteness_banner``) now covers
the payload-emitting commands -- derive the current membership from that function's call
sites (grep ``_emit_scan_incompleteness_banner(``), never from this sentence. Commands
still trailing their disclosure (if any) are whatever that grep does NOT reach; re-derive,
do not enumerate here.
```

- [ ] **Step 3: Fix `docs/CONTRACTS.md` — BOTH stale instances, then sweep for more.**

**Site A (`:253`, section 10):** the sentence "**Unlike section 9's claims ..., `record`/`find` do NOT canonicalize `PATH` to the nearest `.git` ancestor** ... the pre-fix behavior, unchanged; ... (the same footgun claims had, not yet fixed for this slice)." Replace with:

```
`record`/`find` canonicalize `PATH` to the nearest `.git` ancestor on the SAME terms as
Slice 1 -- see the PATH-scoping bullet in section 9 for the derivation (`_ledger_physical_root`,
five call sites); a `record`/`find` from a different subtree of the same repository shares
the repo-root index.
```

Also reconcile the `:257` bullet's meta-commentary ("**this contract was never updated to say so**" and "has not been one since") — once `:253` is fixed those clauses describe a contradiction that no longer exists; trim them so the bullet keeps the derivation instruction but stops asserting a live inconsistency.

**Site B (`:240`, section 9, MISSED in round 1):** the closing sentence "This canonicalization applies to Slice 1 (claims) only; Slice 2 (`record`/`find`, below) is unaffected and still roots itself at `PATH` taken literally." Replace with a sentence stating both slices canonicalize on the same terms (point at the same five-call-site derivation; keep the non-git literal-path fallback caveat, which IS still true — `ledger_store.py:57`).

Then sweep the WHOLE document (documents contradict THEMSELVES; no gate compares a doc to itself):

```bash
grep -in "canonicaliz\|path-literal\|taken literally\|slice 2" docs/CONTRACTS.md
```

Classify every hit true/false against the call-site derivation; fix every false one, not just the two named here.

- [ ] **Step 4: Fix the TWO `ledger_store.py` prose lies** (same derivation):

**Site C (`:434-438`, `_ledger_physical_root` docstring):** currently "Used by ``submit_claim``/``release_claim``/``list_claims`` ONLY -- Slice 2 keeps plain ``_resolve_root`` (see module docstring)." — the module docstring it cites says the opposite (`:48-57`). Replace the second sentence with: used by all five entry points (`submit_claim`/`release_claim`/`list_claims`/`record_finding`/`find_findings`); keep the pointer to the module docstring, which is already correct.

**Site D (`:389-391`, section banner — found in this revision, not in the audit):** currently "Claims-subtree (Slice 1) only -- record_finding/find_findings (Slice 2) below deliberately keep plain `_resolve_root`, untouched." Rewrite to state the helper now serves both slices (same derivation pointer).

Then sweep this file too:

```bash
grep -in "slice 2\|literal" src/tensor_grep/cli/ledger_store.py
```

Classify every hit (the module docstring `:48-57` and the non-git fallback statements are TRUE and stay; anything still asserting Slice 2 bypasses the helper is false).

- [ ] **Step 5: Check nothing pins the old strings.**

```bash
grep -rn "do NOT canonicalize" tests/           # expect 0 -- pair with a positive control:
grep -rn "canonicalize" tests/unit/test_apply_policy.py | head -2   # expect >=1 hit (proves the instrument)
grep -rn "saying nothing in text at all" tests/  # expect 0
grep -rn "keep plain\|keeps plain" tests/        # expect 0 (ledger prose)
```

If any test pins the old text, update the test in the same commit (the assertion is the enforcement — tensor-grep-docs-and-writing).

- [ ] **Step 6: Prove BOTH .py edits behavior-neutral** — run the ast.dump docstring-stripping proof for `src/tensor_grep/cli/main.py` AND `src/tensor_grep/cli/ledger_store.py`:

```bash
python - <<'EOF'
import ast, subprocess
for fname in ("src/tensor_grep/cli/main.py", "src/tensor_grep/cli/ledger_store.py"):
    new = ast.parse(open(fname, encoding="utf-8").read())
    old_src = subprocess.run(["git", "show", f"HEAD:{fname}"], capture_output=True, text=True, encoding="utf-8").stdout
    old = ast.parse(old_src)
    for tree in (new, old):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body = node.body[1:] or [ast.Pass()]
    print(fname, "NEUTRAL" if ast.dump(new) == ast.dump(old) else "BEHAVIOR CHANGED - STOP")
EOF
```

Expected: `NEUTRAL` twice. Anything else: an edit touched more than prose — revert and redo. (The `:389-391` banner is a `#` comment, invisible to ast either way; the docstring edit is what this proof gates.)

- [ ] **Step 7: Run gates and commit.**

```bash
uv run --no-sync ruff check src/tensor_grep/cli/main.py src/tensor_grep/cli/ledger_store.py
uv run --no-sync ruff format --check --preview src/tensor_grep/cli/main.py src/tensor_grep/cli/ledger_store.py docs/CONTRACTS.md
uv run --no-sync pytest tests/unit -k "docs or governance or contract or ledger" -q
uv run --no-sync pytest -q --maxfail=0
git add src/tensor_grep/cli/main.py src/tensor_grep/cli/ledger_store.py docs/CONTRACTS.md
git commit -F - <<'EOF'
docs: correct disclosure docstring and ledger canonicalization docs

Four self-contradicting prose sites fixed by one shared derivation, not enumeration:
main.py disclosure docstring, CONTRACTS.md sections 9 and 10, ledger_store.py helper
docstring + banner comment (the last two contradicted their own module docstring).
EOF
```

**Blast radius:** none (prose; proven by Step 6). **Registration sites:** none.
**Oracle honesty note:** there is no red-arm test for a prose fix. The proof standard here is (a) each replaced claim verified against a fresh call-site census (Step 1), (b) the ast.dump neutrality proof for both .py files (Step 6), (c) governance suites green. Stated plainly as an argument, not dressed as an observation.

---

### Task 2 (PR-D, `chore:`): delete dead `_classify_lines`; record the argv-sentinel retirement

**Files:**
- Modify: `src/tensor_grep/sidecar.py` (delete `_classify_lines`, currently `:157-159`)
- Modify: `src/tensor_grep/cli/apply_policy.py` (comment only, at the `argv = [str(resolved_path), *argv[1:]]` site, currently `:707`)
- Modify: `rust_core/src/main.rs` (mirror comment only, above `let mut command = Command::new(program);`, currently `:11045`)
- Modify: `docs/BACKLOG.md`, `docs/TASK_BOARD.md` (close the two tracked lines)

- [ ] **Step 1: Re-verify zero callers, with positive controls (the census IS the oracle here — there is no red arm for dead-code removal; say so in the PR).**

```bash
# The candidate (word-boundary excludes _classify_lines_with_metadata and _heuristic_classify_lines):
grep -rn "\b_classify_lines\b" src/ tests/ benchmarks/ scripts/ rust_core/
# Positive control (proves the instrument sees this family of names):
grep -rn "_classify_lines_with_metadata" src/ | head -3
# String/dynamic references (registry, getattr, MCP tool tables -- tg callers is BLIND to these):
grep -rn "\"_classify_lines\"\|'_classify_lines'" src/ tests/
# Secondary lens:
tg callers src _classify_lines
```

Expected: candidate grep matches ONLY `sidecar.py:157` (the def) and doc files; positive control >= 1 hit; string grep 0 hits (paired with the positive control above, so the zero is labeled MEASURED). If ANY code caller appears: STOP, do not delete — report back. (Round-1 audit re-verified this census under four independent lenses and confirmed it: `tg callers` 0/0/0 with `result_incomplete=false`, positive control 4 callers across 3 files, no `__all__`/registry/`getattr` dispatch.)

- [ ] **Step 2: Delete the function (`sidecar.py::_classify_lines`, the 3-line wrapper), run the full gate.**

```bash
uv run --no-sync pytest -q --maxfail=0
uv run --no-sync mypy src/tensor_grep
uv run --no-sync ruff check .
```

A green suite + green strict mypy after deletion is the discriminator (an actually-used symbol reds import/collection or mypy).

- [ ] **Step 3: Add the retirement comment in `apply_policy.py`** immediately above `argv = [str(resolved_path), *argv[1:]]`:

```python
    # SENTINEL RETIREMENT (2026-08-01 backlog campaign): no `--` separator is inserted here,
    # deliberately. The CWE-88 argv-sentinel census is keyed on the artifact "OUR flags plus an
    # UNTRUSTED positional appended to a tool WE chose" (rg/ast-grep invocations). This argv is
    # the opposite shape: an operator-authored COMPLETE validation command; a blind `--` has no
    # defined semantics for an arbitrary command and can break it. The path-hijack half is
    # already closed above: argv[0] is resolved and refused if it shadows into the untrusted
    # repo. Do not "fix" this by adding a sentinel.
```

Mirror one-line comment (same reasoning, compressed) above `let mut command = Command::new(program);` in `rust_core/src/main.rs` (currently `:11045`) — comment-only Rust change; CI compiles it, never compile locally.

- [ ] **Step 4: Close the board lines.** In `docs/BACKLOG.md` (grep `_classify_lines`, currently `:1088`, `:1163`) and `docs/TASK_BOARD.md` (currently `:202`): mark the dead-code item DONE with this PR's number; add a line recording the apply_policy sentinel item RETIRED with the Step-3 reasoning. Grep both docs for any other mention of either item first (whole-doc sweep, same law as Task 1).

- [ ] **Step 5: Prove `apply_policy.py` comment-only via the same ast.dump technique as Task 1 Step 6** (single-file variant). Expected `NEUTRAL`.

- [ ] **Step 6: MANDATORY adversarial security gate (MF6 — the trigger is the SURFACE, not the diff shape).** `AGENTS.md:48-53` (rule A3) requires it for every PR touching `apply_policy` or a native asset; this PR touches both, and a "comments-only" diff does not exempt it (a retirement comment IS a security decision being recorded — the gate is what certifies the decision, not the bytes). Run a dedicated adversarial pass on **Opus** (per A3; never Fable — cyber-classifier reroute), verdict binary `SHIP` or `FIX-FIRST(file:line + repro + minimal fix)`. The reviewer must at minimum attack the retirement's premise: (a) is there ANY path where this argv carries an untrusted positional appended by tg (the census key)? (b) does the repo-local shadow refusal at `apply_policy.py:696-706` actually close the path-hijack half claimed closed? (c) does the Rust mirror site share the same shape? Record the verdict + evidence on the PR before un-drafting.

- [ ] **Step 7: Full local gate, commit, draft PR.** Note in the PR description: `rust_core/src/main.rs` change is comment-only; CI is the compile arbiter.

**Blast radius:** `_classify_lines` — none by census; `apply_policy.py`/`main.rs` — none (comments, proven for the Python side). **Registration sites:** none.

---

### Task 3 (PR-A part 1, `fix:`): `--ltl` invalid query -> clean exit-2 error, not a traceback

**What breaks today (reproduced 2026-08-01):**

```
tg search "def " --ltl src/tensor_grep/sidecar.py
  -> ~25-line ValueError traceback ("Unsupported LTL query. Use: 'A -> eventually B'"), exit 1
tg search "def -> eventually return" --ltl src/tensor_grep/sidecar.py   -> works, exit 0
tg search "def " --rank /nonexistent  -> "Error: search path does not exist: ...", exit 2  (the convention)
```

**Root cause (verified):** `backends/cpu_backend.py::_compile_ltl` raises bare `ValueError` (`cpu_backend.py:979`), reached via `_search_ltl` (`:996`) from `CPUBackend.search` (`:392`). In `cli/main.py`'s per-file loop, `backend.search` (`:8279`) only handles `BackendExecutionError` (`:8280`) and invalid-regex (`:8286`); `ValueError` re-raises at `:8288` as a raw traceback. Earlier agent's "clap-reject / missing Rust registration" diagnosis was WRONG for this symptom — the fix is Python-side.

**The fix (symbol granularity):** validate the LTL grammar ONCE at the CLI boundary, before the search loop, mirroring the existing clean-error taxonomy (`_exit_search_error`, def at `main.py:4854`, exits 2; existing codes: `empty_pattern`, `unsupported_flag`, `path_not_found`, `invalid_regex`, `configuration_error`). Do NOT convert `_compile_ltl`'s `ValueError` to `BackendExecutionError` — that would trigger the CPU-retry fallback at `main.py:8284` and mislabel an invalid QUERY as an ENGINE failure (Backend Fail-Closed Contract distinguishes these; confirmed correct by the round-1 audit). Do NOT change `_compile_ltl` itself (tests monkeypatch it — comment at `cpu_backend.py:988`).

**Why CLI-boundary validation, given it intercepts 15 existing tests (MF3 decision, stated):** the alternative — catching `ValueError` inside the per-file loop only when `--ltl` is set — would avoid the 15 migrations, but (a) it never fires when the path scan yields zero files, leaving the invalid query silently unreported in exactly the empty-corpus case; (b) it couples a user-input error to the engine-exception flow this fix exists to get OUT of; (c) the boundary check fires deterministically once, before any scan work, matching the `path_not_found` convention. The 15 migrations are mechanical pattern-string swaps in tests whose assertions are pattern-agnostic (they exercise routing/debug/stats against fake backends). Chosen: CLI boundary + explicit migration.

**Files:**
- Modify: `src/tensor_grep/cli/main.py` (inside the `search` command body, immediately before `scanner = DirectoryScanner(config)` — grep that line, currently `:8076`)
- Modify: `tests/unit/test_cli_modes.py` (15 fixture pattern migrations — Step 1a)
- Test: `tests/unit/test_ltl_invalid_query_clean_error.py` (new)

- [ ] **Step 1a: Migrate the 15 invalid `--ltl` fixtures FIRST (green-to-green compatibility migration — NOT a red arm).**

Population, derived by grep + reading each line (16 grep lines total; `:10453` already uses the valid `"AUTH_FAIL -> eventually DB_TIMEOUT"` and is the positive control proving the instrument — leave it untouched). Migrate the pattern argument `"ERROR"` to `"ERROR -> eventually ERROR"` (valid grammar per `_compile_ltl`'s `A -> eventually B` at `cpu_backend.py:975-981`; keeps the ERROR vocabulary so any incidental string assertion still matches) in each of:

1. `test_cli_should_parse_gpu_device_ids_into_search_config` — `tests/unit/test_cli_modes.py:3838`
2. `test_cli_debug_prints_pipeline_routing_reason` — `:13282`
3. `test_cli_stats_prints_summary_when_matches_found` — `:13322`
4. `test_cli_debug_prints_gpu_routing_details_when_available` — `:13345`
5. `test_cli_stats_prints_gpu_routing_details_when_available` — `:13368`
6. `test_cli_json_output_includes_routing_metadata_fields` — `:13393`
7. `test_cli_json_output_should_surface_distributed_worker_metadata_from_backend` — `:13430`
8. `test_cli_json_output_should_prefer_runtime_backend_metadata_over_pipeline_selection` — `:13544`
9. `test_cli_debug_should_print_runtime_routing_when_backend_falls_back` — `:13578`
10. `test_cli_stats_should_prefer_runtime_backend_metadata_when_backend_falls_back` — `:13610`
11. `test_cli_debug_should_print_gpu_chunk_plan_when_pipeline_selected_fallback_has_no_device_ids` — `:13635`
12. `test_cli_json_output_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan` — `:13699`
13. `test_cli_debug_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan` — `:13733`
14. `test_cli_stats_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan` — `:13770`
15. `test_cli_stats_prints_summary_when_no_matches` — `:13795`

Re-derive the population before editing (`grep -n '"--ltl"' tests/unit/test_cli_modes.py` and READ each hit); if the count moved, migrate what is actually there. **Baseline statement:** each of the 15 PASSES pre-fix and PASSES post-migration pre-fix (valid grammar is inert to their fake backends); WITHOUT this migration each would FAIL post-fix (exit 2 at the new boundary before the fake backend is reached — the round-1 blocker). Run the whole file after migrating:

```bash
uv run --no-sync pytest tests/unit/test_cli_modes.py -q
```

Expected: green on pre-fix code. This lands in the SAME PR, in the test-first commit.

- [ ] **Step 1b: Write the failing test.**

```python
"""Regression: an invalid --ltl query must exit 2 with a one-line clean error, never a traceback.

Pre-fix baseline: CPUBackend._compile_ltl's ValueError escapes the search command uncaught
(main.py's per-file loop handles only BackendExecutionError / invalid-regex), so the CLI
prints a raw Python traceback and exits 1. Convention control: every other expected CLI
error (path_not_found, invalid_regex, configuration_error) routes through
_exit_search_error and exits 2 with a single `Error: ...` line.
"""

from __future__ import annotations

import json as jsonlib
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app

runner = CliRunner()


def _target(tmp_path: Path) -> Path:
    target = tmp_path / "sample.py"
    target.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    return target


def test_invalid_ltl_query_exits_2_with_one_line_clean_error(tmp_path: Path) -> None:
    # RED ARM 1. Pre-fix: ValueError traceback, exit 1.
    result = runner.invoke(app, ["search", "def ", "--ltl", str(_target(tmp_path))])
    assert result.exit_code == 2
    lines = [line for line in result.output.splitlines() if line.strip()]
    # "one-line" is ASSERTED, not narrated: the presenter emits exactly one stderr line
    # (typer.echo(f"Error: ...", err=True) in _exit_search_error) and nothing else precedes
    # the exit on this path.
    assert len(lines) == 1
    assert lines[0].startswith("Error:")
    assert "A -> eventually B" in lines[0]
    assert "Traceback" not in result.output


def test_invalid_ltl_query_json_mode_emits_error_envelope(tmp_path: Path) -> None:
    # RED ARM 2. Pre-fix: traceback, exit 1, no envelope.
    result = runner.invoke(
        app, ["search", "def ", "--ltl", "--json", str(_target(tmp_path))]
    )
    assert result.exit_code == 2
    payload = jsonlib.loads(result.output.strip())
    # Full _search_error_payload presenter shape (version, schema_version, ok, error, detail)
    # -- parsed, not substring-matched.
    assert payload["ok"] is False
    assert payload["error"] == "invalid_ltl_query"
    assert "A -> eventually B" in payload["detail"]
    assert "version" in payload and "schema_version" in payload


def test_valid_ltl_query_still_works(tmp_path: Path) -> None:
    # GREEN control (not a red arm -- it survives the revert by design;
    # arms 1-2 above are what must go RED on pre-fix code).
    result = runner.invoke(
        app, ["search", "def -> eventually return", "--ltl", str(_target(tmp_path))]
    )
    assert result.exit_code == 0


def test_ltl_with_invalid_subexpression_regex_stays_on_invalid_regex_convention(
    tmp_path: Path,
) -> None:
    # REGRESSION GUARD, baseline GREEN -- NOT a red arm (round-1 MF5). Pre-fix, the
    # re.error from compiling "(" already routes through _is_invalid_regex_error ->
    # _exit_invalid_regex and exits 2. This test pins that the NEW boundary preserves
    # that convention (post-fix the same observable is produced at the boundary instead
    # of inside the per-file loop). It passes in both arms BY DESIGN and is never cited
    # as a red receipt.
    result = runner.invoke(
        app, ["search", "( -> eventually X", "--ltl", str(_target(tmp_path))]
    )
    assert result.exit_code == 2
    assert "Traceback" not in result.output
```

Notes for the implementer: (a) if `result.output` does not include stderr under this repo's Typer/click version, mirror how existing tests in `tests/unit/test_cli_modes.py` capture stderr for `_exit_search_error` paths (grep `path_not_found` there for the shape) — adapt the assertion SURFACE (including the one-line count, re-derived from the observed `path_not_found` convention output), not the assertion; (b) verify the JSON envelope keys against `_search_error_payload` (grep `def _search_error_payload`, currently `main.py:4838-4847`) before trusting the key list above. CliRunner is acceptable here because the fix lives INSIDE the Typer command body (bootstrap routing for `--ltl` is unchanged and already covered); the published-wheel dogfood in the Merge task covers the real front door.

**Per-test red-arm table (house law: baseline + proof, per test):**

| test | pre-fix baseline | proof of observability |
|---|---|---|
| `test_invalid_ltl_query_exits_2_with_one_line_clean_error` | FAILS (exit 1, traceback) | Step 2 run + Step 5 stash-revert |
| `test_invalid_ltl_query_json_mode_emits_error_envelope` | FAILS (exit 1, no envelope; `jsonlib.loads` raises on traceback text) | Step 2 run + Step 5 stash-revert |
| `test_valid_ltl_query_still_works` | PASSES (green control, by design) | labelled; never cited as red |
| `test_ltl_with_invalid_subexpression_regex_stays_on_invalid_regex_convention` | PASSES (baseline GREEN — `_is_invalid_regex_error` at `main.py:3985-3995` -> `_exit_invalid_regex` at `:4902-4911` already handles it) | labelled REGRESSION GUARD; never cited as red |

- [ ] **Step 2: Run it, observe RED (pre-fix).**

```bash
uv run --no-sync pytest tests/unit/test_ltl_invalid_query_clean_error.py -q
```

Expected: arms 1, 2 FAIL (exit_code 1, ValueError escapes); tests 3, 4 PASS (both labelled green-by-design above). Record the failure output — this is the red-arm receipt for the PR description.

- [ ] **Step 3: Implement.** In `main.py`'s `search` command, immediately before `scanner = DirectoryScanner(config)` (i.e. AFTER the multi-pattern combine block that reassigns `pattern` — grep `_combine_multi_patterns(file_sourced_patterns`), insert:

```python
    if ltl:
        # An invalid --ltl query is a USER error, not an engine failure: surface it once,
        # cleanly, through the same exit-2 taxonomy as path_not_found/invalid_regex --
        # never as CPUBackend._compile_ltl's raw ValueError traceback (exit 1), and never
        # as a BackendExecutionError (which would wrongly trigger the CPU-retry fallback).
        from tensor_grep.backends.cpu_backend import CPUBackend

        try:
            CPUBackend._compile_ltl(pattern, 0)
        except re.error as exc:
            _exit_invalid_regex(exc, json_mode=json)
        except ValueError as exc:
            _exit_search_error("invalid_ltl_query", str(exc), json_mode=json)
```

Check `re` is already imported at module top (it is — grep `^import re`); check `_exit_invalid_regex`'s signature at its def (grep `def _exit_invalid_regex`) and match it; check the actual local names for the `--ltl` flag and json mode in `search_command`'s signature and use those. Keep the message ASCII.

- [ ] **Step 4: Run the test file — arms 1-2 now PASS (4/4 green).** Then the two-arm control on the real repro:

```bash
uv run --no-sync pytest tests/unit/test_ltl_invalid_query_clean_error.py tests/unit/test_cli_modes.py -q
uv run tg search "def " --ltl src/tensor_grep/sidecar.py; echo "exit=$?"        # expect: 1-line Error, exit=2
uv run tg search "def -> eventually return" --ltl src/tensor_grep/sidecar.py; echo "exit=$?"  # expect matches, exit=0
```

(`uv run tg` loads the venv install — before trusting it, assert the import path: `uv run python -c "import tensor_grep, sys; print(tensor_grep.__file__)"` must resolve into THIS checkout's `src/`, per the stale-venv law. If it does not, run via `uv run python -m tensor_grep ...` from the repo root and confirm that surface instead.)

- [ ] **Step 5: Prove the red arm honestly (revert-in-place).**

```bash
git stash push -- src/tensor_grep/cli/main.py
uv run --no-sync pytest tests/unit/test_ltl_invalid_query_clean_error.py -q   # expect FAIL: arms 1,2 only
git stash pop
uv run --no-sync pytest tests/unit/test_ltl_invalid_query_clean_error.py -q   # expect PASS 4/4
```

Same-tree stash is a genuine in-place revert (the PYTHONPATH/conftest baseline trap does not apply — no tree swap). Record both outputs. Note the stash reverts only `main.py`; the migrated `test_cli_modes.py` fixtures stay migrated and stay green in both arms (valid grammar, fake backends), which is expected and is not a broken control — they are a compatibility migration, not an oracle.

- [ ] **Step 6: Narrow suites + full gate.**

```bash
uv run --no-sync pytest tests/unit/test_cpu_backend.py tests/unit/test_cli_modes.py tests/unit/test_r2_dos_batch.py -q
uv run --no-sync pytest -q --maxfail=0
uv run --no-sync mypy src/tensor_grep
uv run --no-sync ruff check . && uv run --no-sync ruff format --check --preview .
```

(`test_r2_dos_batch.py` and `test_cpu_backend.py` are the existing LTL-touching suites — enumerated from `grep -rln ltl tests/`; run each, don't reason they're covered.)

- [ ] **Step 7: Commit** (`git commit -F -` heredoc; message references the two-arm receipt and the 15-fixture migration).

**Blast radius (confirm before commit):** `_compile_ltl` callers — `tg callers src/tensor_grep _compile_ltl` plus `grep -rn "_compile_ltl" src/ tests/` (tg is blind to monkeypatch/string refs; tests monkeypatch it — confirm none of them route through the new validation site in a way that changes their behavior; the monkeypatched sites patch the BACKEND method, and the boundary calls the CLASS attribute — read each monkeypatch site and confirm whether the patch applies to the boundary call too, and if it does, whether that changes any assertion). The insertion touches only the `--ltl` branch of `search`; `--ltl` never reaches rg-passthrough (it is in `_TG_ONLY_SEARCH_FLAGS`, `bootstrap.py:67`) nor native delegation (`config.ltl` gate at `main.py:5460` — verify by reading that gate). The 15 migrated fixtures are the enumerated test-side radius.

**Registration sites touched:** none (flag already registered on the Python front door).

---

### Task 4 (PR-A part 2, `fix:`): register `--ltl` in `SEARCH_PYTHON_PASSTHROUGH_FLAGS`

**What breaks today:** native-frontdoor binary users running `tg search "A -> eventually B" --ltl PATH` get a clap unknown-flag rejection instead of Python-sidecar routing (see GROUND-TRUTH CORRECTION). The 2-front-door law (`SEARCH_PYTHON_PASSTHROUGH_FLAGS` in `rust_core/src/main.rs` + `_TG_ONLY_SEARCH_FLAGS` in `bootstrap.py`) requires both doors to agree; today only bootstrap knows `--ltl`.

**Red-arm honesty (MF4):** this task has exactly ONE mandatory pre-merge red arm — the Rust unit
test, observed RED in the `test-rust-core` job's stable legs (`cargo test --no-default-features`,
all three OSes, `ci.yml:448-513`). The round-1 plan also cited a `tests/e2e/test_routing_parity.py`
case as a red-arm receipt; that was WRONG and is withdrawn: `_skip_if_native_binary_missing`
`pytest.skip`s when the binary is absent (`test_routing_parity.py:165-167`), `test-python` never
builds a release `tg` (`ci.yml:442-446`), and the only job that builds one runs only
`tests/e2e/test_native_*.py` (`ci.yml:658-660,718-726`) — so that case would SKIP pre-fix and
post-fix, a check that cannot fail. The full-path guard is instead rebuilt in Step 3 inside the
suite the binary-building job actually executes.

**Files:**
- Modify: `rust_core/src/main.rs` — add `"--ltl"` to `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (const ends at `"--semantic"`, currently `:318`); add one Rust unit test mirroring `search_format_python_passthrough_args_routes_rank_flag_to_python` (currently `:4440-4457`).
- Add: `tests/e2e/test_native_ltl_passthrough.py` (new — inside the `test_native_*.py` glob)
- Modify: `.github/workflows/ci.yml` (native-build-smoke job env + the glob-census comment at `:711-717`)

- [ ] **Step 1: Write the Rust test FIRST, commit it ALONE, push, observe CI RED.** (CPU-SAFE: the red arm is observed in CI, never via local cargo. State this in the PR description. The observing job is `test-rust-core (<os>, stable)` — record the failing run ID and the failing test name from ITS log, not a roll-up.)

```rust
    #[test]
    fn search_format_python_passthrough_args_routes_ltl_flag_to_python() {
        // `tg search --ltl` is a Python-side temporal query (CPUBackend::_search_ltl);
        // it must delegate to the Python sidecar instead of being clap-rejected as an
        // unknown flag by the native front door -- mirrors the --rank case above.
        // Registered on BOTH front doors per the 2-front-door law (the other door is
        // bootstrap.py::_TG_ONLY_SEARCH_FLAGS).
        let raw_args = ["tg", "search", "--ltl", "open -> eventually close", "src"]
            .iter()
            .map(OsString::from)
            .collect::<Vec<_>>();

        assert_eq!(
            search_format_python_passthrough_args(&raw_args),
            Some(vec![
                "--ltl".to_string(),
                "open -> eventually close".to_string(),
                "src".to_string()
            ])
        );
    }
```

Place it adjacent to the `--rank`/`--semantic` tests. Push this commit alone on the PR branch; record the failing CI run ID + the failing test name from the `test-rust-core` stable-leg log — that is the red-arm receipt. If this test PASSES in CI on pre-fix code, STOP: the native front door already routes `--ltl` by some other mechanism, the ground truth's refinement is wrong, and this half of PR-A converts to a documented retirement (delete the const change, keep the test, record why in the PR + AGENTS.md front-door section).

- [ ] **Step 2: Add the fix**, one line + comment in the const:

```rust
    // --ltl is a Python-side temporal-query post-process (CPUBackend::_search_ltl); route it
    // to the sidecar so the native front door does not clap-reject the unknown flag. Paired
    // with bootstrap.py::_TG_ONLY_SEARCH_FLAGS (the 2-front-door law).
    "--ltl",
```

Push; observe the same CI test now GREEN in the same job (record the run ID — both halves of the arm, by ID).

- [ ] **Step 3: Full-path belt, rebuilt where it can actually run (replaces the withdrawn parity case).** Add `tests/e2e/test_native_ltl_passthrough.py`: locate the release binary the way `tests/e2e/test_native_plain_text_parity.py` does (READ that file first and mirror its discovery + require mechanism exactly), run `tg search "def -> eventually return" --ltl <small fixture>` through the native binary via subprocess WITH a hard timeout (anti-hang protocol), assert exit 0 + expected match output (pre-fix the native door clap-rejects `--ltl` with a nonzero exit, so this test is RED in any environment that has the binary). Skip/require contract: skip when the binary is absent (so `test-python` and local dev runs stay green) UNLESS a require-env is set, in which case a missing binary is a FAILURE — mirror the existing `TG_REQUIRE_RG_PARITY` escalation pattern; since this test needs no `rg`, add a `TG_REQUIRE_NATIVE_BINARY: "1"` env line to the `native-build-smoke` suite step (`ci.yml:718-726`) and gate the escalation on either var. **Update the glob-census comment at `ci.yml:711-717` in the same commit** — it currently says "exactly these two" files match the glob; that census is what keeps the glob honest, and this file makes it three. Observability: `native-build-smoke` runs on `pull_request` (workflow triggers at `ci.yml:2-7`; the job has `needs: smoke` and no `if:`, `ci.yml:626-630`), builds the release binary (`:658-660`), and runs the glob (`:718-726`) — so this arm is expected RED on the PR run for the Step-1 commit and GREEN after Step 2; record both from the `native-build-smoke` job logs by run ID. If the job unexpectedly does not execute the new file on the PR run (check the job log for the test id — SKIPPED IS NOT PASSED), the file stays as a post-merge guard but is NOT cited as a red arm, and the Rust unit test remains the only red-arm receipt; record which outcome occurred.

- [ ] **Step 4: Registration-completeness check.** Run the existing enforcement rather than inventing one: `uv run --no-sync pytest tests/unit/test_cli_bootstrap.py -q` (contains the source-of-truth cross-checks). Confirm the CI registration-completeness gate is green on the PR run.

**Blast radius:** the const is read only by `search_format_python_passthrough_args` (confirm: `grep -n "SEARCH_PYTHON_PASSTHROUGH_FLAGS" rust_core/src/main.rs` — expect the def + one consumer + tests; pair the claim with that grep's output in the PR). No Python behavior changes. The ci.yml edit adds one env var + updates one comment in one job.

**Registration sites touched:** front door #1 of 2 (`SEARCH_PYTHON_PASSTHROUGH_FLAGS`); front door #2 (`_TG_ONLY_SEARCH_FLAGS`) already has `--ltl` (`bootstrap.py:67`, checked at `:525`) — verify both with one grep each in the PR description.

**PR-A assembly:** Tasks 3+4 ship as ONE `fix:` PR (same feature surface, one release tick). Draft PR; request the standard adversarial pass (front-door routing change — cheap insurance even though it is not argv construction); **rebase onto the merged C+D tip and re-run the union before merge (collision gate, PR grouping section)**; human merges; then the A-to-B release gate (captured main run `status=completed` + `conclusion=success` + PyPI) before PR-B merges.

---

### Task 5 (PR-B, `fix:`): session daemon fails closed without a token — a POLICY REVERSAL, stated as one

**What this PR actually does (MF1 — reframed):** it does NOT fix an unexamined bug. The current
tokenless fail-open is PINNED as deliberate by
`tests/unit/test_session_daemon_security.py::test_tokenless_server_stays_backward_compatible`
(`:58-65`), whose comment reads "A server constructed without a token (legacy/in-test path) must
not reject requests." This PR **reverses that documented-intentional policy**: it retires the pin
with a written in-code reason, replaces it with a pin of the NEW policy, and says so plainly in the
PR body. **The human merger is explicitly deciding a policy reversal, not approving a bug fix** —
put that sentence, verbatim, at the top of the PR description.

**Why fail-closed still stands, argued against the pin (not past it):**

1. **The pin's protected class is empty in production.** The pin protects a "legacy/in-test path".
   Census of every `_ThreadedSessionDaemon(` construction in `src/` (grep, paired with the tests/
   grep as positive control): exactly ONE — `run_session_daemon_server` at
   `session_daemon.py:2069`, and it always passes a freshly generated token
   (`secrets.token_urlsafe(32)`, `:2068`). No legacy production constructor exists to keep
   compatible. Re-run this census before implementing; if a second production constructor has
   appeared, STOP and report — the argument below no longer holds as written.
2. **The pin's "in-test path" half is resolved by migration, not broken.** All 16 tokenless test
   constructions are migrated or explicitly classified in this same PR (Step 4), so no test is left
   depending on the old behavior.
3. **The behavior the pin protects is the hazard.** On an IPC socket, "no shared secret exists"
   reading as "everyone is authorized" is the textbook fail-open; this repo's own history
   (default-OFF-and-never-armed, guard-scoped-to-a-consumer) is a catalogue of latent branches like
   this one going hot later. Production behavior is unchanged either way TODAY — which is exactly
   why the cheap time to flip the default is now.

**The legitimate alternative, considered and rejected:** keep the pin, add a comment strengthening
the "misconfiguration is the caller's problem" stance. Rejected because it leaves a `return True`
on a security surface guarded only by prose, and because the pin's own justification ("legacy
path") names a population that measurably does not exist. If the human merger disagrees, the
documented retirement of THIS proposal is the recorded outcome — close the PR unmerged and record
the decision in `docs/BACKLOG.md`.

**Mechanism (verified):** `is_authorized` returns `True` for EVERY request when `self.token` is
falsy (`session_daemon.py:1764-1766`); the constructor defaults `token=""` (`:1737`).

**The fix (symbol granularity):** in `is_authorized`, change the falsy-token branch from `return True` to `return False`, with a comment stating the reversal. Do NOT also make the constructor raise — hold the invariant in ONE place (a second enforcement site is a future silent-divergence surface). Tokenless CONSTRUCTION stays legal (the lifecycle test at `:675` depends on that and sends no requests).

**Files:**
- Modify: `src/tensor_grep/cli/session_daemon.py` (`is_authorized`, currently `:1763-1770`)
- Modify: `tests/unit/test_session_daemon_security.py` (retire+replace the pin; add the direct test and the over-the-wire test)
- Modify: `tests/unit/test_session_cli.py`, `tests/unit/test_session_serve.py` (census migration, Step 4)

- [ ] **Step 1: Write the failing tests** (in `test_session_daemon_security.py`, mirroring the harness of `test_is_authorized_requires_matching_token` at `:44-51`, which stays as the positive control). TWO new tests — the direct method AND the presenter seam (the round-1 codex point: `is_authorized`'s consumer is `_SessionDaemonHandler.handle`, which turns a failed check into the `unauthorized` envelope; a direct-method test alone never proves the envelope):

```python
def test_tokenless_daemon_fails_closed(tmp_path: Path) -> None:
    # POLICY REVERSAL (2026-08-01 backlog campaign, PR-B): this test replaces
    # test_tokenless_server_stays_backward_compatible, which pinned the OPPOSITE behavior
    # ("legacy/in-test path must not reject"). Reversal rationale: the only production
    # constructor (run_session_daemon_server) always generates a token, so the pinned
    # "legacy" population is empty; on an IPC socket, "no shared secret exists" must never
    # read as "everyone is authorized". A tokenless daemon now refuses every request.
    server = session_daemon._ThreadedSessionDaemon(tmp_path.resolve(), ("127.0.0.1", 0), token="")
    try:
        assert server.is_authorized({}) is False
        assert server.is_authorized({"token": ""}) is False
        assert server.is_authorized({"token": "anything"}) is False
    finally:
        server.server_close()


def test_tokenless_daemon_request_gets_unauthorized_envelope(tmp_path: Path) -> None:
    # Over-the-wire arm: the presenter, not just the predicate. Mirror the
    # _SessionDaemonHandler.__new__ + BytesIO idiom used in test_session_serve.py
    # (e.g. the malformed-JSON test) with a WELL-FORMED tokenless request; assert the
    # response envelope's error code is the handler's unauthorized code (read
    # _SessionDaemonHandler.handle for the exact field/value before writing the assert).
    ...
```

(Match the file's existing construction/teardown idiom exactly — read its neighbors first. For the second test, read `_SessionDaemonHandler.handle`'s auth-refusal branch and pin the REAL envelope shape, not a guess.)

- [ ] **Step 2: Retire the old pin.** Delete `test_tokenless_server_stays_backward_compatible` (`:58-65`) in the same commit that adds the tests above, leaving a short comment at that spot (or in the new test's docstring, as written above) recording: what was pinned, that this PR reverses it deliberately, and the one-line reason. The PR body repeats it. This is the MF1 requirement: the pin is retired ON THE RECORD, not silently migrated.

- [ ] **Step 3: Run, observe RED.**

```bash
uv run --no-sync pytest tests/unit/test_session_daemon_security.py -q
```

Expected: both new tests FAIL (`is_authorized({})` currently `True`; the envelope arm gets a success envelope); `test_is_authorized_requires_matching_token` PASSES (positive control). The old pin is already deleted, so no test asserts the old behavior. Record the output.

- [ ] **Step 4: Implement** — replace the branch at `is_authorized` (keep the S3 comment, add the decision):

```python
        # audit S3: constant-time compare to avoid leaking the token via timing.
        # POLICY REVERSAL (2026-08-01, PR-B): a tokenless daemon fails CLOSED. This reverses
        # the earlier pinned behavior (tokenless => authorize everything, "legacy/in-test
        # path"). Production always generates a token in the serve path
        # (secrets.token_urlsafe(32)); a tokenless daemon is a misconfiguration and must
        # refuse everything, never accept everything.
        if not self.token:
            return False
```

- [ ] **Step 5: Migrate the census — all 16 direct tokenless constructions, each with a per-site disposition (MF2).** Re-derive first (`grep -rn "_ThreadedSessionDaemon(" tests/` and READ every hit in full — several constructions are multi-line and the grep line alone does not show the token), then apply. Census as of base commit, verified by calling each site:

**`tests/unit/test_session_cli.py` — 11 sites, all live-request harnesses:** `:2461, :2528, :2584, :2634, :2708, :2775, :2845, :2897, :2968, :3036, :3107`. Each starts a server and sends requests via `_daemon_request(..., token="")`. Migration: pass `token="tok"` at construction AND thread `"tok"` through each test's request call (the module's request helper injects the token when given one — see the `token: str = ""` injection at `session_daemon.py:518-523`).

**`tests/unit/test_session_serve.py` — 3 sites (OMITTED in round 1):**
- `:356` (`test_session_daemon_returns_invalid_request_for_malformed_json`): the request fails JSON parsing BEFORE auth, so the assertion is unaffected either way. Disposition: add `token="tok"` at construction for uniformity; no request token possible (the payload is malformed by design); assert unchanged `invalid_request`. Verify the parse-before-auth ordering by reading `handle` — if auth actually precedes parsing, this test's expected envelope changes to unauthorized and the test must be re-labelled; do not assume.
- `:393` and `:457` (valid direct-handler requests): become `unauthorized` post-fix unless migrated. Disposition: pair `token="tok"` at construction with `"token": "tok"` in each request payload dict.

**`tests/unit/test_session_daemon_security.py` — 2 sites (OMITTED in round 1):**
- `:60` — the old pin; retired and replaced in Steps 1-2.
- `:675` (`test_lifecycle_monitor_returns_when_both_limits_disabled`): constructs a daemon, never handles a request; `is_authorized` is never called. Disposition: DELIBERATELY left tokenless, with a one-line comment saying so (fail-closed forbids tokenless AUTHORIZATION, not tokenless construction — this test is the living proof construction stays legal).

**Removed from the round-1 census (false positives — the "wrong in both directions" half):** `tests/unit/test_orient_agent_daemon.py` and `tests/unit/test_graph_completeness_oracle.py` construct no daemons directly; both import a `_real_daemon` helper that defaults and forwards `token="test-token"` (`tests/unit/test_symbol_daemon_autostart.py:73-75`; the sibling helper in `tests/unit/test_session_daemon_version_skew.py:35-38` does the same). While re-deriving, also check no CALLER of those helpers overrides `token=""` — a helper defaulting safe does not prove every call site is.

Run EACH affected file individually (call the members, don't reason coverage):

```bash
uv run --no-sync pytest tests/unit/test_session_cli.py -q
uv run --no-sync pytest tests/unit/test_session_serve.py -q
uv run --no-sync pytest tests/unit/test_session_daemon_security.py tests/unit/test_session_daemon_metrics.py -q
uv run --no-sync pytest tests/unit/test_orient_agent_daemon.py tests/unit/test_graph_completeness_oracle.py tests/unit/test_symbol_daemon_autostart.py tests/unit/test_session_daemon_version_skew.py -q
```

All green. (If any harness deliberately exercises the UNAUTHENTICATED path, do not token it — assert the new refusal instead; classify each match, don't sweep.)

- [ ] **Step 6: Red-arm revert proof** (stash `session_daemon.py` only, re-run `test_session_daemon_security.py`, observe BOTH new tests red, pop, re-run green — record both outputs).

- [ ] **Step 7: Full gate + end-to-end sanity.**

```bash
uv run --no-sync pytest -q --maxfail=0
uv run --no-sync mypy src/tensor_grep
uv run tg session --help > /dev/null; echo $?    # smoke: CLI path intact
```

- [ ] **Step 8: MANDATORY adversarial security gate (change-control Part 1 Rule 5 / AGENTS.md A3).** `session_daemon` is on the security-surface list. Before un-drafting: a dedicated adversarial pass on **Opus — never Fable** (Fable's cyber classifier auto-reroutes mid-turn), verdict binary `SHIP` or `FIX-FIRST(file:line + repro)`. The reviewer must at minimum attempt: (a) a request that bypasses `is_authorized` via a non-dict/mutated payload; (b) timing-oracle regression on the `hmac.compare_digest` path; (c) whether any request handler runs work BEFORE the auth check (pre-auth DoS pattern, cf. `_read_bounded_request_line`); (d) NEW for the reversal framing: whether any consumer outside tests constructs tokenless (re-run the Step "Why fail-closed" census as part of the review). Record the verdict in the PR.

- [ ] **Step 9: Commit, draft PR.** PR body leads with the policy-reversal statement (see the framing block above), then the census table, then the receipts. Merge only after PR-A's release gate is fully satisfied (captured run ID `status=completed` + `conclusion=success` + PyPI).

**Blast radius:** `is_authorized` callers — `grep -n "is_authorized" src/tensor_grep/cli/session_daemon.py tests/` (expect: the handler call site(s) + tests; enumerate and read each). The behavioral change is confined to tokenless daemons, which production never constructs (`:2068-2069`) — the 16-site test migration is the whole visible radius.

**Registration sites touched:** none.

---

### Merge + close-out task (after all four PRs merged)

- [ ] Merge order with gates (MF8, full protocol in the PR grouping section): PR-C + PR-D batch -> capture newest main run BY ID, wait `status=completed` + `conclusion=success` -> rebase PR-A onto the merged tip, re-run union, merge PR-A -> capture ITS main run by ID; A-to-B gate = that run `status=completed` + `conclusion=success` AND `chore(release)` on main AND PyPI serving the new version (~40-66 min) -> rebase PR-B onto post-A main, re-run union, merge PR-B -> repeat the release wait.
- [ ] **Published-wheel verdict table (C-wheel).** After PR-B's version publishes, verify each fixed item against the PUBLISHED wheel in a clean env, one PASS/FAIL row + raw output each (read the raw output at least once; beware pipe exit-code masking — no `| tail` on the command under test):

```bash
uvx --from tensor-grep@<new-version> tg search "def " --ltl <some-file>       # expect: 1-line Error, exit 2
uvx --from tensor-grep@<new-version> tg search "def -> eventually return" --ltl <some-file>  # expect exit 0
# --ltl native front door: link the native-build-smoke run in which test_native_ltl_passthrough RAN
#   (job log shows the test id executed, not skipped) -- SKIPPED IS NOT PASSED.
# session_daemon: covered by the merged test suite on main -- link the main CI run ID in the row.
```

- [ ] Confirm `test_native_ltl_passthrough.py` RAN (not skipped) in the post-merge main `native-build-smoke` job log.
- [ ] Clean up worktrees/branches (verify each PR merged via `gh pr view <N> --json state` before deleting — squash merges hide from `git branch --merged`).

---

## NOT BUILDING

| Item | Disposition | Reason (receipt) |
|---|---|---|
| `--quiet` dropped by rg-passthrough | Already fixed | cfc3264; `-q` moved to streaming-only `search_passthrough` (`ripgrep_backend.py:491-511`); `tests/unit/test_quiet_survives_rg_passthrough.py` covers both arms |
| AGENTS.md argv-sweep staleness | Already fixed | `AGENTS.md:1796-1811` corrected |
| #115 / #125 | Board-stale, already closed | `docs/BACKLOG.md` is right (KILLED/CLOSED); `docs/TASK_BOARD.md:199-200` is the stale copy — board reconciliation is out of this campaign's scope beyond Task 2 Step 4's two lines |
| #15 MaxSim doc-honesty | Already fixed | `main.py:4758-4771` honest; `test_find_command.py:490-498` asserts real order inversion |
| #858 / #859 / #862 / #860b | Already fixed | `codemap.py:820` delegates to `atomic_write_bytes`; ratchet `test_codemap_write_refuses_symlink.py:51-57`; `agent_capsule.py:1740-1741` has the `--` sentinel; tip stamp at v1.101.27 |
| `--ndjson` zero-match divergence | Refuted — deliberate | Documented Python/Rust divergence, `json_fmt.py:253-287` (comment `:265-271`) |
| `main.rs` envelope literals (3rd literal untested) | Refuted — justified | 2/3 have serialization tests; 3rd is `#[cfg(feature="cuda")]` with in-code justification |
| apply_policy / main.rs `--` sentinel (ground-truth item 6) | **RETIRED with a recorded reason** (Task 2 Step 3), now ALSO gated by the Task 2 adversarial review (MF6) | The sentinel census is keyed on the artifact "our flags + untrusted positional appended to a tool we chose"; this argv is an operator-authored complete command — a blind `--` has undefined semantics there and can break the command. Path-hijack half already closed (`apply_policy.py:696-706` repo-local shadow refusal). Retirement recorded in-code + board so it is never re-chased. |
| `test_routing_parity.py` `--ltl` case (round-1 plan's own proposal) | **WITHDRAWN** | Structurally unobservable (skips pre- and post-fix — MF4); replaced by `test_native_ltl_passthrough.py` in the glob the binary-building job runs |

If the implementer finds any incidental defect while executing: fix it in the same turn or file a concrete tracked blocker (change-control Part 1 Rule 7) — never wave it past as out-of-scope.

## Skills to load, per phase

**Every phase (implementer, non-negotiable):**
- `tensor-grep-change-control` — the gates (registration sites, push-race, PR titles, pre-merge checklist)
- `tensor-grep-validation-and-qa` — oracle forms 1-10, what counts as proof, red-arm protocol
- `superpowers:test-driven-development` — RED before GREEN
- `superpowers:executing-plans` or `superpowers:subagent-driven-development` — task execution
- `superpowers:using-git-worktrees` — branch isolation (worktrees have no `.venv`; run gates in the real venv)

**Task 1 (docs):** `tensor-grep-docs-and-writing` (which doc owns which contract; governance-test layers).
**Task 2 (dead code / retirement):** `tensor-grep-code-audit` (tg callers/blast-radius + their blind spots); change-control Part 1 Rule 5 for the Step-6 gate.
**Tasks 3-4 (--ltl):** `tensor-grep-architecture-contract` (front doors, fail-closed contract, native delegation gate); `tensor-grep-debugging-playbook` on any unexpected red; `anti-hang-test-protocol` (the new native e2e subprocess test); `dogfood-the-shipped-artifact` (close-out wheel dogfood).
**Task 5 (daemon):** `tensor-grep-change-control` Part 1 Rule 5 (adversarial gate — a process, executed on Opus per `feedback-fable5-cyber-classifier-audit-on-opus`).
**Merge phase:** `tensor-grep-release-and-positioning` (push-race depth, release watching by run ID); `orchestrate-parallel-fix-waves` (the merged-tree union rule behind the MF8 collision gate).

**Skill-gap statement:** no needed skill is missing for this campaign. There is no dedicated skill for the session-daemon token/IPC security model — it is adequately covered by the in-code audit-S3 comments plus AGENTS.md's Security Hardening Patterns section; a new skill is not warranted for one bounded fix. (Named explicitly so nobody invents and cites a nonexistent skill.)

## Open questions / decisions needed

1. **Item 2 (`--ltl` native registration): FIX recommended, contingent on the CI red arm.** Recommendation: fix (Task 4) — the 2-front-door law plus a live defect for native-binary users. Contingency written into Task 4 Step 1: if the pre-fix Rust test is GREEN in CI, the front door already routes it and the item converts to documented retirement. Tradeoff: fixing costs a Rust const line inside an already-releasing PR (zero extra release ticks); retiring leaves the two front doors permanently disagreeing, which the registration law exists to forbid.
2. **Item 5 (tokenless daemon): FAIL CLOSED recommended — as a POLICY REVERSAL for the human merger to decide.** The full argument, the pinned opposite policy, the empty-in-production census behind the reversal, and the considered-and-rejected keep-the-pin alternative are in Task 5's framing block. This is a policy decision surfaced as one; if the merger declines, the documented retirement of the proposal is the recorded outcome.
3. **Board reconciliation** (`docs/TASK_BOARD.md` broadly stale beyond the two lines Task 2 touches): deliberately out of scope — flagging for a follow-up docs pass so it is a tracked decision, not an omission.

## Self-review record (rev 2)

- All 8 round-1 must-fixes addressed (table at top); none disproved; two precision refinements (15-vs-16 migration population; the withdrawn parity receipt) and one net-new finding (`ledger_store.py:389-391`, the fourth prose lie) folded in.
- Spec coverage: all 7 confirmed-open items dispositioned (1→Task 3, 2→Task 4, 3+4→Task 1 [now four sites], 5→Task 5 [policy reversal], 6→retired in Task 2, 7→Task 2); all already-fixed/refuted items in NOT BUILDING, plus the withdrawn parity case.
- Every red arm now carries a per-test pre-fix baseline and an observability proof, and every green-by-design test is labelled as such (Task 3's table; Task 4's honesty block; Task 5's two-arm protocol). No stated red arm skips or passes in both arms.
- Every `file:line` above re-verified against base commit `0126cb3b` on 2026-08-01 during this revision (grep/read receipts in the revision transcript); implementer must re-anchor by symbol if `origin/main` has moved.
- Names cross-checked against source this revision: `_exit_search_error` (def `main.py:4854`), `_search_error_payload` (`:4838-4847`), `_is_invalid_regex_error` (`:3985-3995`), `_exit_invalid_regex` (`:4902-4911`), `CPUBackend._compile_ltl` (staticmethod, `cpu_backend.py:974-981`), `search_format_python_passthrough_args`, `_ThreadedSessionDaemon` (`session_daemon.py:1733`, token default `:1737`), `is_authorized` (`:1763-1770`), `_SessionDaemonHandler.handle`, `run_session_daemon_server` (`:2063-2069`), `_classify_lines`, `_ledger_physical_root` (`ledger_store.py:434-439`), `_emit_scan_incompleteness_banner`, `_skip_if_native_binary_missing` (`test_routing_parity.py:165-167`), `native-build-smoke` (`ci.yml:627-628`).
