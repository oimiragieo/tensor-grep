# Plan: the world-class closeout campaign

**Date:** 2026-08-20. **Revision:** r3. Council round 1 returned REVISE from three seats
(dispositioned in appendix A); round 2 returned REVISE from two live seats with narrow, textual
findings (dispositioned in appendix B). **Status:** DRAFT — awaiting re-audit. Not a build licence.
**Base:** `origin/main` at `7dfff2f` (`refactor: split mcp_server.py into
mcp_rewrite_tools/mcp_audit_tools/mcp_symbol_tools (#1051)`). Public product `v1.111.0`.
**Branch this plan lives on:** `docs/worldclass-closeout-plan`.

Every row below carries the command that produced its number. Where a number in the briefing input
disagreed with the command, **the command wins and the disagreement is written down** — four of them
did (see "Premise corrections" immediately below). Nothing here is recalled.

---

## 0. Premise corrections found while deriving (read first)

A plan against work that is already done has perfectly resolving citations. Step 0 of
`verify-plan-against-code` is the premise check; it fired four times.

| briefed premise | derived truth | receipt |
|---|---|---|
| "~30 oversized files… the 33-remaining oversized files" | **30** violations, all grandfathered, 0 regressions | `python scripts/file_size_budget.py --report` prints `violations: 30   grandfathered: 30` |
| "the ~160-handler audit IOU" | **128** broad handlers sit in the 19 excluded modules; 160 was a *hypothetical* ceiling (137 to 160) for the `cli/main.py` slice alone, per that file's own comment. The audit-relevant subset is the **54** the file's own detector cannot prove disclose a reason | AST census, W1.1 |
| "gpu_native needs a CI experiment" (implying the design doc's experiment is unrun) | The experiment **already ran and passed** for two files: `index.rs` (#1048, `0f2d09f`) and `native_search.rs` (#1049, `961829f`), both now carrying `#[path = "…_tests.rs"] mod tests;`. The `#[path]` mechanism is therefore **measured, not hypothesised** — for a *library* crate. It is still unproven for a **binary** crate root (`main.rs`) and under `--features cuda` (`gpu_native.rs`) | `grep -rn '#\[path' rust_core/src/` returns 2 hits; `git log --oneline -- rust_core/src/` |
| "continued size reduction toward the 1,500 limit on the 3 giants" | Route A is **complete on all three** (`scripts/bare_call_pins.json` holds `"bare_calls": {}`; the ratchet reports `3 modules, 0 bare calls`) and all three have already been split (#1052 / #1053 / #1051). They are nonetheless **still floor-bound**: `measure_split_floor.py` reports `SPLIT CANNOT REACH THE LIMIT` for all three. The remaining lever is **not** another split | `python scripts/measure_split_floor.py` |

The fourth is the one that reshapes the plan: **wave 3 cannot be "split them more".** See W3.

---

## 1. Standing constraints (the council will check these)

These are not preamble. A slice that violates one is rejected regardless of its diff.

1. **SHARED DESKTOP.** No local `cargo build`/`check`/`test`/`clippy`, no benchmark harness, no
   `tests/e2e/test_routing_parity.py` (it shells `cargo run`). **CI is the Rust compiler.** Every
   Rust verdict in this plan is a CI round-trip and is budgeted as one.
2. **Both ratchets run on every slice**, not only the one a slice thinks it touches:
   `python scripts/file_size_budget.py --report` and `python scripts/bare_call_ratchet.py`. The Route A
   correction doc records a push that ran only the new gate and grew a file past its pin
   (`docs/design/2026-08-20-route-a-adversarial-review.md`, section 6, "Cost, recorded rather than hidden").
3. **Four-shape monkeypatch sweep on any code move.** The bare-call ratchet counts `ast.Call` on an
   `ast.Name` and nothing else. Before moving any function, re-derive all four shapes by hand:
   (a) bare call to a patched name; (b) **attribute call on a patched module** (`subprocess.run(...)`);
   (c) **patched CONSTANT read as a plain Name load** (`_MAX_NATIVE_ASSET_DOWNLOAD_BYTES`);
   (d) **function-local `import X` shadowing a patched module global** — qualifying that one is a
   behaviour change disguised as a mechanical one. All four are named with receipts in
   `docs/design/2026-08-19-split-floor-escape.md`, section 7.
4. **Union-merge before queueing concurrent PRs.** Two slices green alone can red `main` together.
   Rebase onto the real target and run the union of both slices' suites before pushing the second.
5. **`git stash` is forbidden.** Worktrees share `.git`'s stash refs. Stage your own paths
   (`git add <my-paths>` then `git commit`) and verify the isolation with
   `git diff --cached --name-status` against `git diff --name-only`.
6. **Release class is read from the merged commit subject, not from the PR title's intent.** Two
   systems disagree: `_RELEASE_INTENTS` in `scripts/validate_pr_title_semver.py` gates what the TITLE
   may say; `[tool.semantic_release]` in `pyproject.toml` (default angular parser, patch types `fix`
   and `perf` only) decides what SHIPS. `refactor:` and `chore:` do **not** publish. Ask both before
   merging anything whose value is only realised once published.
7. **Relocated code is excluded from a census WITH ITS REASON, never by a ceiling bump.** Moving a file
   does not audit it. Raising `TOTAL_BROAD_HANDLERS_CEILING` to absorb a `git mv` is the exact failure
   the file's own comment forbids.
8. **One merge per publish window.** The `Semantic Release` job runs about 6 minutes; merging anything
   during it rejects the in-flight push.
9. **No zero is reported without a control.** Every census and grep in this plan states what a positive
   control returned, or is labelled `EMPTY — proves nothing`.
10. **Every acceptance command names its execution environment.** Commands are tagged `[LOCAL]` —
    **Git Bash on the shared Windows desktop**, the only local shell this campaign uses — or `[CI]`,
    meaning a GitHub Actions runner and never this box. `[LOCAL]` commands in this plan are written as
    `python -c` or `python -m` invocations rather than POSIX `test`/`tail`/exit-code-of-grep idioms,
    because those three were flagged as non-portable on this host. Where a `[LOCAL]` command's value is
    an exit code, the expected code is stated. A slice may not substitute a POSIX equivalent.
11. **No slice edits a gate, pin, or allowlist it does not own.** Ownership is in the collision map
    below and is total: there is exactly one writer per file for the whole campaign.

### Shared files — the collision map

| file | owner | rule |
|---|---|---|
| `scripts/file_size_allowlist.json` | **W4, and W4 alone** | W4 is the **allowlist integrator**. No other wave, and no individual W4 slice acting on its own, edits it. Every pin change — including the ones caused by W5's Rust extractions and by W4-f's re-pin — is made by the integrator in the same PR as the change that moved the line count, or in an immediately following PR that lands before the next Rust merge. **Slice-local allowlist edits are prohibited.** |
| `tests/unit/test_silent_failure_hardening.py` | **W1, serialized** (see W1.2) | Exclusively W1's, and **serialized between W1 slices** — "W1 owns it" does not arbitrate W1-a against W1-c. One census-integration commit per slice, in the stated order. |
| `tests/unit/test_handler_dispositions.py` + `docs/audits/2026-08-20-handler-dispositions.json` | **W1, serialized** | New in r2. Same serialization as above; the ledger is append-only per slice. |
| `scripts/bare_call_pins.json` | nobody | read-only for all waves; a diff here is a defect, not an edit |
| `pyproject.toml`, `uv.lock` | **W2-b**, for one merge window | Added in r2. Brief window, but the map exists to prevent discovering a collision at merge time. No other slice may touch either file while W2-b is open. |
| `docs/TASK_BOARD.md`, `docs/BACKLOG.md` | **orchestrator, at closeout** | append-only; never by a slice agent, never mid-wave |
| `src/tensor_grep/cli/mcp_server.py` and the three `mcp_*_tools.py` | **W1-a** | W1-a owns the source; W2 only reads it; W4's file-size work excludes it |
| `rust_core/src/main.rs` | **W5-b** | W5-b makes the only content change; its pin is moved by the W4 integrator |

---

## 1.5 Canonical item registry (the definition of "campaign complete")

Item IDs are canonical and stable. Cutting an item, marking it optional, or delegating its execution
**does not remove it from this table** — it changes its DISPOSITION, which the closeout manifest
(section 4.8) verifies. This exists because a bare item count is unstable: r1 counted an "optional"
tripwire, a "not in this plan" design flag, and a delegated re-pin all as items, so "19" could silently
mean three different things.

| ID | title | disposition | executed by |
|---|---|---|---|
| `W1-a` | MCP-surface handler audit | REQUIRED | W1 |
| `W1-b` | doctor + front-door handler audit | REQUIRED | W1 |
| `W1-c` | `cli/main.py` handler audit | REQUIRED | W1 |
| `W1-d` | lang strays + the eight zero-handler modules + the disposition-ledger gate | REQUIRED | W1 |
| `W2-a` | MCP 2.0 decision record | REQUIRED | W2 |
| `W2-b` | `mcp` floor bump to the maintained head | REQUIRED | W2 |
| `W2-c` | scheduled deferral-expiry evidence | **REQUIRED** (was OPTIONAL in r1; council made it mandatory and replaced its mechanism) | W2 |
| `W3-a` | beyond-Route-A costing | REQUIRED, **DESIGN-ONLY** | W3 |
| `W4-a` | `python_sidecar.rs` test extraction | REQUIRED | W4 |
| `W4-b` | `backend_ast.rs` + `backend_ast_workflow.rs` extraction | REQUIRED | W4 |
| `W4-c` | `backend_cpu.rs` marker manifest, then decide | REQUIRED (the manifest; the extraction is conditional on it) | W4 |
| `W4-d` | the three Python test giants | REQUIRED | W4 |
| `W4-e` | `test_schema_compat.rs` + `test_routing.rs` | REQUIRED | W4 |
| `W4-f` | allowlist integration: the `main.rs` stale-pin re-pin (W4.6) plus every pin W5-a/W5-b/W4-b/W4-c/W4-e moves | REQUIRED (was "W3-b" in r1; renumbered into the wave that owns the file) | W4 |
| `W5-a` | `gpu_native.rs` test extraction | REQUIRED | W5 |
| `W5-b` | `main.rs` test extraction | REQUIRED | W5 |
| `W5-c` | `main.rs` architecture pass | **DEFERRED — flag only.** Deliverable is the flag and a filed row; **no implementation is authorised** | none |
| `W6-a` | `tg ledger` rebuild guide | REQUIRED | W6 |
| `W6-b` | rebuild-guides README entry | REQUIRED | W6 |
| `FU-1..FU-4` | four filed follow-ups (section 3.5) | **FILED, NOT BUILT** — closeout verifies the rows exist in `docs/BACKLOG.md` | orchestrator |

**Campaign complete** means: every REQUIRED row merged and its acceptance command reproduced in the
closeout manifest; `W5-c` present as a filed row with no code; `FU-1..FU-4` present as filed rows.

---

## 2. Wave structure and the total merge order

W1, W2, W6 and W4-a/W4-d are mutually independent by file set.
W3-a is **gated on W1** (its costing reads the modules W1 is editing).
W4-b, W4-c, W4-e, W5-a, W5-b are all Rust and all share one CI lane.

    W1 (W1-d -> W1-a -> W1-b -> W1-c, serialized on the gate file) --+--> W3-a (design only)
    W2 (a, c parallel; b in its own release window) -----|
    W6 (a -> b) ----------------------------------------+
    W4-a, W4-d  (Python + one small Rust, independent)
    RUST CI LANE, strictly serial:
        W5-a -> W5-b -> W4-b -> W4-c -> W4-e
        (W4-f re-pins after EACH of these, before the next starts)

**The diagram has now been wrong twice, the same way.** r1 showed W4 and W5 as fully parallel lanes
while the text said W4-b and W4-e were CI-serialised behind W5; r2 fixed that and then wrote the W1
order as `a -> d -> b -> c` while W1.2, the merge order and appendix A all said
`W1-d -> W1-a -> W1-b -> W1-c`. Both are the same defect class — **a diagram is a second copy of the
body, and a second copy drifts.** The order is defined in exactly one place, the total merge order
below; the diagram is a picture of it and must be re-read against it on every revision.

**Total merge order** (one PR at a time; `[REL]` marks a publishing merge that needs its own window):

1. `W1-d`  2. `W1-a`  3. `W1-b`  4. `W1-c`  5. `W6-a`  6. `W6-b`  7. `W4-a`  8. `W4-d`(**witness commit first**, then ×3 split PRs — see W4.4)
9. `W5-a` 10. `W4-f`(pin) 11. `W5-b` 12. `W4-f`(pin, **including the `main.rs` stale-pin re-pin**)
13. `W4-b` 14. `W4-f`(pin) 15. `W4-c` 16. `W4-f`(pin) 17. `W4-e` 18. `W4-f`(pin)
19. `W2-a` 20. `W2-c` 21. **`W2-b` `[REL]`** 22. `W3-a` 23. closeout manifest.

**Every Rust merge is followed by a `W4-f` pin step — all five, without exception.** r2 listed pin
steps after W5-a, W5-b and W4-b but omitted them after W4-c and W4-e, while W4.2 scopes W4-f to
"every pin W4-c/W4-e moves". An extraction that shrinks a file without lowering its pin leaves the
allowlist above the measured count, which the budget ratchet fails on its next run — so the omission
would have reddened `main` at the following step. If a `W4-f` step finds nothing to change (the
extraction did not move a line count), it is recorded as a **no-op** in the closeout manifest rather
than skipped silently; a skipped step and an empty step are not the same evidence.

`W2-b` is deliberately last among code changes: it is the only publishing merge, and putting it after
the Rust lane means no Rust CI round-trip is ever queued inside a release window.

---

## W1 — The excluded-handler audit IOU (PRIORITY 1, security-adjacent)

**Why priority 1.** `tests/unit/test_silent_failure_hardening.py` censuses broad `except Exception:` and
bare `except:` handlers across `src/tensor_grep`, and pins the reviewed population at
`TOTAL_BROAD_HANDLERS_CEILING = 137` (locate:
`grep -n 'TOTAL_BROAD_HANDLERS_CEILING' tests/unit/test_silent_failure_hardening.py`).
Nineteen modules are excluded by `_EXCLUDED_MODULES` (locate: `grep -n '_EXCLUDED_MODULES' <same file>`).
Those exclusions are **honest** — the comment above them correctly refuses to launder a `git mv` into an
audit — but they are, right now, **unaudited error paths in the CLI front door, the MCP tool surface,
the native-front-door installer and the Windows launcher**. That is the security-adjacent surface, not
an incidental one.

### W1.1 — Derived evidence (the receipt)

Census run against `7dfff2f`, reusing the test module's OWN classifier functions (`_is_broad_handler`,
`_body_records_reason`, `_body_reraises`) with the exclusion filter lifted, so the audit and the gate
cannot disagree about what counts:

     46  cli/main.py             35  cli/mcp_server.py        12  cli/doctor_report.py
     10  cli/mcp_symbol_tools.py  8  cli/mcp_audit_tools.py    5  cli/native_frontdoor.py
      5  cli/windows_launcher.py  4  cli/mcp_rewrite_tools.py  1  cli/ast_scan.py
      1  cli/repo_map_lang_js.py  1  cli/repo_map_lang_rust.py
      0  each of: _main_binding, doctor_payload, repo_map, repo_map_cache,
         repo_map_lang_java, repo_map_lang_python, repo_map_output_budget,
         repo_map_regex_fallback

    EXCLUDED TOTAL broad handlers: 128
    in-census ceiling:             137
    not-provably-disclosing:        54

**The census script is not a one-shot.** r1 reported these numbers from a throwaway, which makes them
attested rather than reproducible. **W1-d commits it** as `scripts/handler_census.py`, taking
`--include-excluded` so any reviewer can re-derive both populations. A number in a plan that only its
author can reproduce is a claim, not a receipt.

**Positive control on the instrument:** `scripts/handler_census.py` without `--include-excluded` must
reproduce **137** exactly — the number the shipped gate asserts. A census that cannot reproduce the
known number is not trusted to count the unknown one. **Negative control:** eight of the nineteen
excluded modules return **0**, and those zeros are real absences (the modules exist and parse), not a
dead scan — which the 128 non-zero total proves.

The 54 are the audit target. They are *not* 54 defects: the module docstring records that a
naive keyword detector "both over- and under-counts", and that several sites its own AST shape could
not prove logged were confirmed LOGGED-DEGRADE by reading. **Each of the 54 must be read and
classified individually** (SILENT-SWALLOW / LOGGED-DEGRADE / INTENTIONAL-BOUNDARY), not swept.

### W1.2 — Slices, serialized (one writer, one order)

All four slices edit the same gate file, so they are **strictly serialized in this order** and each
lands as one merged PR before the next begins:

| order | slice | source files owned | handlers | not-provably-disclosing | effort |
|---:|---|---|---:|---:|---|
| 1 | **W1-d** | `cli/repo_map_lang_js.py`, `cli/repo_map_lang_rust.py`, **plus all eight zero-handler modules**, **plus** `scripts/handler_census.py`, the disposition ledger and its gate | 2 | 2 | **M** |
| 2 | **W1-a** | `cli/mcp_server.py`, `cli/mcp_symbol_tools.py`, `cli/mcp_audit_tools.py`, `cli/mcp_rewrite_tools.py` | 57 | 25 | **L** |
| 3 | **W1-b** | `cli/doctor_report.py`, `cli/native_frontdoor.py`, `cli/windows_launcher.py`, `cli/ast_scan.py` | 23 | 21 | **M** |
| 4 | **W1-c** | `cli/main.py` | 46 | 12 | **L** |

**W1-d goes first and is bigger than r1 said**, for three reasons the council named:
(a) the **eight zero-handler modules had no owner** in r1 while W1.4 expected zero exclusions at the
end — a completeness gap that would have left the wave unable to close;
(b) it builds the disposition ledger and its gate, so slices 2-4 are held to it;
(c) it is the cheapest place to prove the whole acceptance protocol before a large slice commits to it.

**W1-a is the security priority once the protocol exists.** It is the network-reachable surface (an MCP
tool answering an untrusted client), and it is where a swallowed exception becomes an
empty-but-successful tool result — the exact shape `AGENTS.md`'s Backend Fail-Closed Contract forbids.

### W1.3 — TDD approach: what RED comes first

The RED is **not** "the handler is wrong". It is **"the census cannot yet see this module"**.

1. **RED-1 (the gate is blind).** Delete the slice's modules from `_EXCLUDED_MODULES` without changing
   the ceiling. Run the test. It must fail with a count ABOVE the current ceiling naming exactly this
   slice's modules. *If it passes, the census is not reading them and the whole slice is measuring
   nothing — stop and fix the instrument.* This is the arm that makes every later green mean something.
   For the eight zero-handler modules RED-1 **cannot** fire (there is nothing to count), and that is
   the point: their removal from the exclusion set must be accompanied by the census script printing
   `0` for each, which is a labelled zero with the parse-succeeded control beside it.
2. **RED-2 (per SILENT-SWALLOW found).** For each handler classified SILENT-SWALLOW, write a test that
   drives the real failure and asserts the caller observes it — a raised `BackendExecutionError`, a
   populated `fallback_reason`, or a non-zero exit — and confirm it is RED on the *pre-fix* bytes.
3. **RED-3 (W1-a only, new in r2 — the intentional-boundary arm).** For **every network-facing handler
   W1-a classifies as INTENTIONAL-BOUNDARY**, write a behavioural test proving the boundary is
   fail-closed: the MCP tool must return an explicit error or a populated failure reason, never a
   clean empty success. A classification of "intentional" is a *claim about behaviour* and gets a
   behavioural test; only LOGGED-DEGRADE on a non-network path is discharged by reading. This closes
   the council's finding that r1 tested only the sites it chose to call defective.
4. **GREEN.** Harden the SILENT-SWALLOW sites. Then remove the slice's modules from `_EXCLUDED_MODULES`
   and update the ceiling **in the same commit**.
5. **The ceiling rule, corrected.** r1 said "new ceiling = 137 + this slice's delta". That is only true
   for the first slice to merge. The rule is:
   **new ceiling = the CURRENTLY MERGED ceiling on `origin/main` + this slice's remaining broad handlers
   after hardening, re-derived at rebase time immediately before merge.** Write both the base and the
   delta in the commit body. If the base has moved since the slice branched, re-derive — do not
   arithmetic-forward from a stale base. Two slices computed from 137 concurrently is a semantic-merge
   collision (Oracle Form 10), which this repo has hit twice.

### W1.4 — The disposition ledger (new in r2 — the gate the council said was missing)

r1's acceptance was **arithmetically satisfiable by a no-op audit**: classify all 54 as boundaries, raise
the ceiling by the full count, suite green, nothing proved. The fix is a committed artifact plus a test
that can fail.

**Artifact:** `docs/audits/2026-08-20-handler-dispositions.json` — one record per broad handler in the
formerly-excluded population, keyed by a fingerprint. **Which fields are identity and which are
advisory is stated, because r2 claimed line-shift stability while the schema carried a `lineno`:**

- **IDENTITY** = the triple `(module, enclosing_symbol, handler_index_within_symbol)`. This is what
  uniqueness and completeness are computed over, and it is stable under any edit that does not rename
  the symbol or reorder handlers within it.
- **ADVISORY** = `lineno`. It exists so a human can jump to the site. It is **never** part of identity,
  so a line shift cannot orphan a record or manufacture a duplicate. It is still *checked*, but only
  for plausibility: the locatability assertion requires it to fall inside the enclosing symbol's span,
  which catches a record edited by hand against a stale copy of the file without making ordinary code
  motion a failure.
- If a symbol is renamed or its handlers are reordered, the identity changes and the record must be
  re-derived, not hand-patched — that is the intended cost of a rename, and the completeness assertion
  is what surfaces it.

The record shape:

    {"module": "cli/mcp_audit_tools.py",
     "lineno": 642,
     "enclosing_symbol": "_audit_history_payload",
     "handler_index_within_symbol": 0,
     "category": "SILENT-SWALLOW" | "LOGGED-DEGRADE" | "INTENTIONAL-BOUNDARY",
     "evidence": "re-raises as BackendExecutionError at :651",
     "reason": "<one sentence, why this category and not the adjacent one>",
     "hardened_in": "<PR number, or null for the two non-defect categories>"}

**Gate:** `tests/unit/test_handler_dispositions.py`, which must enforce, each as its own assertion:
- **completeness, scoped to what has actually been audited so far** — the ledger is append-only and
  the slices merge serially, so a full-population check would be unsatisfiable at every intermediate
  merge, and an implementer facing an unsatisfiable assertion invents an unstated scope. The rule is
  therefore explicit: **at any commit, every broad handler in the modules REMOVED from
  `_EXCLUDED_MODULES` so far (cumulatively, up to and including this slice) has exactly one record,
  and no record exists for a module still excluded.** Both directions are asserted — a missing record
  fails, and so does a record for a module nobody has audited yet, which is how a slice claiming
  credit for work it did not do gets caught. The population is derived from
  `scripts/handler_census.py` against the current exclusion set, never from the ledger. Expected
  ledger sizes per merge follow from W1.1's per-module counts: **W1-d -> 2, W1-a -> 59, W1-b -> 82,
  W1-c -> 128.** `len(ledger) == 128` is therefore only true at W1-c, and it is reached by the
  cumulative rule rather than asserted as a hardcoded final number;
- **uniqueness** — no IDENTITY triple appears twice (`lineno` is not consulted);
- **locatability** — every record's IDENTITY triple resolves to a real broad handler in the current
  tree, and its advisory `lineno` falls within that symbol's span (a `lineno` outside it fails loudly
  rather than passing silently);
- **vocabulary** — `category` is one of the three; no free text;
- **evidence non-emptiness** — `evidence` and `reason` are both non-empty and not equal to each other.

**Perturbation arms, all four run and their results stated in the PR body** (a gate nobody has watched
fail is not a gate): (i) delete one record → completeness fails, naming the missing fingerprint;
(ii) duplicate one record → uniqueness fails; (iii) shift one `lineno` outside its symbol → locatability
fails; (iv) set one `category` to `"probably-fine"` → vocabulary fails. Revert; the file must be
byte-identical between the clean arms.

**Named human reviewer.** The classification table is a **required PR artifact** and the **per-slice
codex audit seat (section 4.3) is its designated reviewer**, with the A3 Opus seat (section 4.2.1) as
the security reviewer for W1-a and W1-b. The ledger makes the judgement *auditable*; it does not make it
*automatic*, and this plan does not claim it does.

### W1.5 — Acceptance tests

`[LOCAL]` Git Bash on the shared desktop:

    python -m pytest tests/unit/test_silent_failure_hardening.py tests/unit/test_handler_dispositions.py -q

Expected: all pass (exit 0).

    python -c "import importlib.util as u; s=u.spec_from_file_location('sfh','tests/unit/test_silent_failure_hardening.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print('excluded', len(m._EXCLUDED_MODULES), 'ceiling', m.TOTAL_BROAD_HANDLERS_CEILING)"

Expected at W1 start: `excluded 19 ceiling 137`. Expected after W1-d: `excluded 9`. After W1-a:
`excluded 5`. After W1-b: `excluded 1`. After W1-c: **`excluded 0`**, with a ceiling equal to the sum
the four commit bodies show.

    python -c "import json,sys; d=json.load(open('docs/audits/2026-08-20-handler-dispositions.json')); from collections import Counter; c=Counter(r['category'] for r in d); print(len(d), dict(c)); sys.exit(0 if len(d)==128 else 1)"

Expected after W1-c: prints `128` and the three-way category split, exit 0. Before W1-c it exits 1,
which is the correct failure — the population is not yet fully dispositioned.

**Security notes.** W1-a and W1-b are the security-bearing halves and both get the **A3 adversarial
gate** (section 4.2.1). A swallowed exception in `native_frontdoor.py` sits on the **checksum-gated
asset install** path — a broad handler there can convert a failed verification into a silent success,
which is the `supply-chain-hardening` fail-closed rule inverted. Any handler on that path is
SILENT-SWALLOW **by default** and must be argued out of it, not into it. `windows_launcher.py`'s
handlers sit on PATH and COM manipulation.

**Effort: W1 total is L.**

---

## W2 — MCP 2.0 exposure decision (PRIORITY 2 — verify, then decide)

### W2.1 — Derived evidence

Re-derived in-tree, at `7dfff2f` (the research receipt's anchors drifted through the #1051 split, so
these are re-located by symbol):

- `grep -n 'mcp>=' pyproject.toml` returns `586:    "mcp>=1.27.2,<2",`
- `grep -n 'from mcp' src/tensor_grep/cli/mcp_server.py` returns
  `24:from mcp.server.fastmcp import FastMCP` and
  `26:from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS` — tg delegates protocol-version
  negotiation to the SDK entirely.
- `grep -n '_TG_MCP_SERVER_CONTRACT_VERSION *=' src/tensor_grep/cli/mcp_server.py` returns **`188`**
  (value `"1.7.0"`). The research receipt cited `:191`; it drifted. **Cite the symbol, not the line.**
  This is tg's own contract number and is unrelated to the wire protocol — do not conflate them.
- Guard on the upper bound: `tests/unit/test_mcp_dependency_is_upper_bounded.py`.

### W2.2 — What Exa verified, and how it changed the decision

Two lookups, both decision-changing; no research theatre beyond them.

1. **The spec revision is real and is as described.** Primary source fetched 2026-08-20:
   `https://modelcontextprotocol.io/specification/2026-07-28/changelog`. Confirmed verbatim: sessions
   and `Mcp-Session-Id` removed (SEP-2567); the `initialize` / `notifications/initialized` handshake
   removed, version and capabilities now in `_meta` (SEP-2575); **`server/discover` MUST be implemented**
   (SEP-2575); `subscriptions/listen` replaces the GET endpoint and `resources/subscribe`; `ping` and
   `logging/setLevel` removed; tasks moved to an extension (SEP-2663); MRTR replaces server-initiated
   requests, and all results carry `resultType` (SEP-2322).
2. **The upstream maintainers themselves recommend the pin tg already has.** PyPI `mcp` package
   metadata, fetched 2026-08-20 (`https://pypi.org/pypi/mcp/json`): latest is **2.0.0**; the README says
   *"v1.x lives on the `v1.x` branch, continues to receive critical bug fixes and security patches…
   Since `pip install mcp` now installs 2.x, keep a `<2` upper bound on your requirement (for example
   `mcp>=1.28,<2`) until you've migrated."* Latest v1.x is **1.29.0, uploaded 2026-07-28**.

**This flipped the item from "plan a migration" to "PIN AND DEFER, with named triggers, a watcher, and
one floor bump."** Migrating now would be a rewrite (`FastMCP` was deleted in 2.0) bought with no user
demand and no security pressure — and it would collide head-on with the `MCP-SURFACE` row, which is
fenced behind Task 2C at contract 1.7.0.

### W2.3 — W2-a: the decision record (effort S)

Deliverable: `docs/design/2026-08-20-mcp-2-0-exposure-decision.md`, containing a **structured YAML
front-block** (so the acceptance test validates semantics, not a repeated heading — r1's
`grep -c 'REOPEN TRIGGER'` could be satisfied by three copies of the same word):

    decision: PIN_AND_DEFER
    revalidate_by: 2027-02-20            # 6 months; a time-bounded trigger, see T6
    monitoring_owner: tensor-grep-release-drift-check post-release sweep
    triggers:
      - id: T1  type: upstream_maintenance_end   source: <URL>  checked: 2026-08-20
      - id: T2  type: client_incompatibility     bar: "a NAMED client with a reproduction case that
                cannot be resolved by a client-side pin; a single speculative issue does NOT qualify"
      - id: T3  type: internal_unblock           detail: "Task 2C clears, unblocking MCP-SURFACE"
      - id: T4  type: python_platform_support_loss  detail: "maintained 1.x drops a Python version tg supports"
      - id: T5  type: transitive_dep_unpatchable    detail: "a transitive dependency of 1.x gains an
                advisory with no fix reachable under the <2 bound"
      - id: T6  type: time_bounded_revalidation     detail: "revalidate_by elapses with no other trigger"

Six trigger classes, not three: the council found T4, T5 and T6 missing, and it is right that an
indefinite decision depending only on event discovery rots. T2's bar is stated because r1 left
"a named client" open enough that one speculative issue could force a premature migration.

`[LOCAL]` acceptance:

    python -c "import re,sys,pathlib; p=pathlib.Path('docs/design/2026-08-20-mcp-2-0-exposure-decision.md'); t=p.read_text(encoding='utf-8'); ids=set(re.findall(r'id:\s*(T[1-6])',t)); types=set(re.findall(r'type:\s*(\w+)',t)); ok = ids=={'T1','T2','T3','T4','T5','T6'} and len(types)==6 and 'revalidate_by:' in t and 'monitoring_owner:' in t; print(sorted(ids), sorted(types)); sys.exit(0 if ok else 1)"

Expected: prints all six ids and six **distinct** types, exit 0. Exit 1 if any id is missing or two
triggers share a type — which is what makes the repeated-heading loophole unreachable.

### W2.4 — W2-b: floor bump to the maintained head (effort S)

`mcp>=1.27.2,<2` becomes `mcp>=1.29.0,<2` in `pyproject.toml`, `uv.lock` regenerated.

**Framing, corrected.** r1 implied an unpatched-CVE rationale. **No advisory against 1.27.2 was found or
is cited.** The honest framing is a **maintenance policy**: the `<2` cap means tg's floor is the only
thing keeping a resolver from installing an old release off the branch that receives the security
patches, so the floor tracks the maintained head. If an advisory is later cited, it upgrades the
justification — it is not the justification today.

*RED first:* an assertion that the floor is at least 1.29.0, added to
`tests/unit/test_mcp_dependency_is_upper_bounded.py` (the file that already owns the bound), must fail
on the pre-change pin. State that RED result in the PR body.

`[LOCAL]` acceptance:

    python -m pytest tests/unit/test_mcp_dependency_is_upper_bounded.py -q
    python -c "import pathlib,sys; t=pathlib.Path('pyproject.toml').read_text(encoding='utf-8'); sys.exit(0 if '\"mcp>=1.29.0,<2\"' in t else 1)"
    python -c "import re,pathlib,sys; t=pathlib.Path('uv.lock').read_text(encoding='utf-8'); m=re.search(r'name = \"mcp\"\nversion = \"([^\"]+)\"',t); print('lock mcp:', m and m.group(1)); sys.exit(0 if m and m.group(1).startswith('1.') else 1)"

Expected: pytest exit 0; both `python -c` exit 0; the third prints a `1.x` version, proving the lock was
actually regenerated rather than left stale.

`[CI]` acceptance — three things r1's single pytest command did not prove:

- **lowest-bound resolution installs and imports.** A clean env resolved to the floor
  (`uv pip install --resolution lowest-direct 'tensor-grep[mcp]'` or the repo's equivalent), then
  `python -c "import mcp, mcp.server.fastmcp"` exits 0, on every supported Python in the matrix.
- **an actual MCP process smoke, not an import.** Start the server, complete an `initialize` handshake,
  call `tools/list`, and assert the response is non-empty and carries
  `_TG_MCP_SERVER_CONTRACT_VERSION == "1.7.0"`. An import proves the module loads; only a transport
  round-trip proves the SDK floor did not break the server. This same smoke is re-run against the
  **published wheel** in the closeout manifest (section 4.8).
- **lock consistency.** The repo's existing lock-parity check green on the regenerated lock.

*Release class:* `fix:` — this one publishes, and is the campaign's only publishing merge (order step 20).

### W2.5 — W2-c: scheduled deferral-expiry evidence (effort M, **REQUIRED**)

r1 proposed an SDK-constant tripwire and marked it optional. **All three seats rejected the mechanism**
and two required the item: the constant is imported from an installed **1.x** SDK, so it will
essentially never acquire `"2026-07-28"` — the test can stay green forever while the ecosystem moves.
It watched the wrong event.

**Replacement mechanism: a recurring re-derivation, not an inert assertion.** Wire trigger T1 into the
**`tensor-grep-release-drift-check` post-release sweep** (the skill already exists at
`.claude/skills/tensor-grep-release-drift-check/`), extending it to, on every post-release run:

1. fetch `https://pypi.org/pypi/mcp/json`, re-derive the latest **v1.x** version and its upload date;
2. compare to the floor in `pyproject.toml` and to `revalidate_by` in the decision record;
3. emit one of three **labelled** verdicts — `MAINTAINED` (a v1.x release newer than the floor exists,
   or the head equals the floor), `STALE` (no new v1.x release within the sweep's stated window **and**
   `revalidate_by` not yet elapsed — reported, not failing), `EXPIRED` (`revalidate_by` elapsed, or a v1.x
   maintenance-end notice found) — and **never** a bare zero. A fetch failure is `CANNOT_MEASURE`, which
   is loud and is not `MAINTAINED`.

*Perturbation arms, results stated in the PR body:* (i) set `revalidate_by` to a past date → `EXPIRED`;
(ii) point the fetch at an unreachable host → `CANNOT_MEASURE`, not `MAINTAINED`; (iii) revert → the
real verdict returns and the file is byte-identical.

`[LOCAL]` acceptance:

    python -m pytest tests/unit/test_release_drift_mcp_maintenance_probe.py -q

Expected: exit 0, including the three perturbation cases as parametrised arms with an offline fixture
(the test must not require network — the network call belongs to the sweep, the classification logic to
the test).

**Honest limitation, kept:** this watches PyPI maintenance status and the calendar. It does **not**
observe a client-compatibility event (T2) or Task 2C (T3); those remain human-discovered, and the
decision record names them as such. A watcher over two of six triggers is a real improvement over zero,
and this plan does not describe it as coverage.

---

## W3 — The three giants: the floor is the finding (PRIORITY 3)

### W3.1 — Derived evidence

`python scripts/measure_split_floor.py`, at `7dfff2f`:

| module | total | symbols tests patch | functions LOCKED | lines LOCKED to facade | verdict |
|---|---:|---:|---:|---:|---|
| `cli/main.py` | 13,523 | 49 | 62 | **7,416** | SPLIT CANNOT REACH THE LIMIT |
| `cli/repo_map.py` | 15,243 | 66 | 106 | **6,715** | SPLIT CANNOT REACH THE LIMIT |
| `cli/mcp_server.py` | 5,341 | 66 | 28 | **2,506** | SPLIT CANNOT REACH THE LIMIT |
| `cli/agent_capsule.py` | 926 | 9 | 9 | 693 | split is viable |

`python scripts/bare_call_ratchet.py` prints `bare-call ratchet OK: 3 modules, 0 bare calls, 0 regressions`.
`scripts/bare_call_pins.json` holds `"bare_calls": {}` with retirement notes for all three.

**Read this honestly.** Route A did what it promised — it removed the bare-call class entirely — and
the three splits (#1052 minus 4,460 lines; #1053 minus 4,519; #1051) banked the mechanical wins. And
**all three floors are still above 1,500.** More splitting is arithmetically incapable of reaching the
limit. Any plan row saying "keep splitting the giants" is a row that cannot succeed.

Note also that the residual floor is now dominated by a *different* mechanism than the one Route A
fixed. `measure_split_floor.py`'s own documentation says it is a **lower bound** that does not model
class methods, closures, `global` rebinding, or `spec_from_file_location`. So 7,416 is a floor on a floor.

### W3.2 — W3-a: costing the levers (effort M, DESIGN ONLY)

Deliverable: `docs/design/2026-08-20-beyond-route-a.md`. **No code.** It must cost, with a measured
number per option and a reproducible command behind every number, at least these three:

1. **Shrink the patched-symbol set** (49 / 66 / 66 symbols). The floor is a closure over what tests
   patch. Every symbol a test stops patching drops its whole transitive cone out of the locked set.
   Rank by **cone size**, not by patch count — one widely-called symbol can dominate. This is Route B's
   blast radius (75 test files) applied *surgically* to the few symbols that pay, rather than wholesale.
2. **Dependency injection at the seam** — pass the collaborator in rather than patching a module
   attribute. Removes the mechanism instead of relocating it. Costs a signature change per seam.
3. **Accept the pin, per module** — keep a module at its pinned size, with the floor measurement as the
   stated reason. **This is a legitimate outcome and the plan says so.** A grandfather pin backed by a
   measured impossibility proof is honest engineering.

**The predeclared decision rule** (written here, before any number is seen, so the author cannot
rationalise either outcome after the fact). For each module independently:

- **Pursue a lever** if its costing shows the module reaching **≤ 1,500** for **≤ 150 edits and ≤ 3 CI
  round-trips**, with no seam crossing a security surface named in W1.
- **Accept the pin** if the cheapest lever that reaches ≤ 1,500 costs **> 300 edits** or **> 6 CI
  round-trips**, or if no lever reaches ≤ 1,500 at any cost.
- **Escalate to a council** for anything between those bands. Silence is not acceptance.
- Acceptance is **per module, dated, and reopenable** — it records the residual floor and the cheapest
  known lever's cost so a future session can re-decide. It is explicitly **not** a permanent
  three-file blanket exception.

**Required schema.** The doc carries one row per (module, option) with these exact fields:
`module`, `option`, `cone_lines`, `candidate_seams`, `affected_tests`, `affected_callers`,
`estimated_edits`, `estimated_ci_rounds`, `risk`, `expected_residual_floor`, `derivation_command`.
It must also carry, for its recommendation, **the strongest argument against it and why that argument
is rejected** — a design doc that only argues its own side is advocacy.

`[LOCAL]` acceptance — a schema checker, because r1's `test -f` plus one-token grep could be passed by a
document containing no costing at all:

    python scripts/check_costing_doc.py docs/design/2026-08-20-beyond-route-a.md

Expected: exit 0, printing `9 rows (3 modules x 3 options), 11/11 fields present, 9 derivation commands, 1 RECOMMENDATION, 1 COUNTER-ARGUMENT`.
The checker (built by W3-a, ~60 lines) fails on a missing row, an empty field, a `derivation_command`
that is not a runnable command string, a missing counter-argument, or zero/multiple RECOMMENDATION lines.
Its own perturbation arms — blank one field, drop one row, delete the counter-argument — are run and
their results stated in the PR body.

    python scripts/measure_split_floor.py

Expected: byte-identical output to the block quoted in the doc (W3-a ships no code, so the floors must
not have moved; if they have, W1 changed them and the costing must be re-derived).

*Gate:* W3-a is **blocked until W1-c merges**, because option 1's costing reads the same modules W1 is
editing, and a cone measured mid-edit is a cone measured on a tree nobody will ship.

---

## W4 — The 27 other oversized files, and the allowlist integration (PRIORITY 4)

**W4 is the sole allowlist integrator.** Every pin change in the campaign — its own, and every one
caused by a W5 Rust extraction — is made here. No slice edits `scripts/file_size_allowlist.json`
independently, including W4's own slices: the integrator commits the pin in the same PR as the change
that moved the count, or in an immediately following PR that lands before the next Rust merge.

### W4.1 — Derived evidence

`python scripts/file_size_budget.py --report` reports 30 violations. Removing the three W3 giants leaves
**27**. Two structurally distinct classes:

**Class A — Rust inline `#[cfg(test)] mod tests` extraction. Mechanism PROVEN in CI.**
`index.rs` and `native_search.rs` already carry `#[path = "…_tests.rs"] mod tests;` and merged green
(#1048, #1049). Remaining candidates:

| file | now | `mod tests` span | after extraction | clears 1,500? |
|---|---:|---|---:|---|
| `rust_core/src/python_sidecar.rs` | 1,519 | two top-level markers, `:1082` and `tests_h3` at `:1490-1519` (30 lines) | about 1,489 from `tests_h3` alone | **YES** |
| `rust_core/src/backend_ast_workflow.rs` | 2,109 | `:1579-2109` (530) | about 1,578 | no (1.05x) |
| `rust_core/src/backend_ast.rs` | 2,553 | `:2053-2553` (500); two further `#[cfg(test)]` attributes are nested inside items at `:53` and `:1428` and do NOT move | about 2,052 | no |
| `rust_core/src/backend_cpu.rs` | 1,817 | **SIX** top-level markers (`:282,303,309,315,1088,1778`) plus seven nested attributes inside items (`:356,382,572,607,671,754,811`) — not one block | unknown until the manifest exists | **manifest first** |
| `rust_core/src/gpu_native.rs` | 4,952 | `:4443-4911` (468), with 41 lines of PROD code AFTER it | about 4,484 | no |
| `rust_core/src/main.rs` | 15,126 (pinned 15127 — see W4.6) | `:2984-7473` (4,489) | about 10,637 | no (7.1x) |

> Two of those spans were **re-derived here and disagree with the design doc**, which was written
> before #1048/#1049 landed: `python_sidecar.rs`'s second marker is at `:1490`, not `:1491`, and
> `backend_cpu.rs` has **six** top-level markers, not five (`:1778` was missed).
> **And the counting instrument matters here, three ways.** On `backend_cpu.rs`, a substring grep
> (`grep -c '#\[cfg(test)\]'`) returns **15**; matching the stripped line exactly returns **13**; matching
> at column 0 returns **6**. The gap between 15 and 13 is two lines of *prose about* `#[cfg(test)]` in the
> comment at `:277` and `:280` — a grep hit that is documentation, not code, which is the failure mode
> this repo has laws about. The gap between 13 and 6 is nested attributes inside items, which do not
> move with a `mod tests` extraction. **Only the column-0 count is the extraction population**, and
> W4-c's manifest must record all three so a reviewer can see which instrument produced which number.
> Neither changes a verdict, but a slice briefed off the design doc's numbers would move the wrong
> bytes — take the spans from this table, or re-run the grep. **r1 of this plan then reproduced the
> exact error it had just corrected**, writing "five markers" into the W4-c slice brief three lines
> below this note. That is the "corrected claim shipping beside its refuted duplicate" failure this
> repo has laws about; W4-c below now says six and, more importantly, no longer takes a count from prose
> at all — it derives a manifest first.

**Class B — Rust integration tests and Python test files.** `rust_core/tests/*.rs` are already separate
compilation units seeing only the library's `pub` surface, so splitting one into two costs no
visibility change. Python test files split freely, with one caveat below. Worst offenders
(`grep -c 'def test_'`): `tests/unit/test_cli_modes.py` 17,204 lines / 545 tests;
`tests/unit/test_benchmark_scripts.py` 10,689 / 236; `tests/unit/test_mcp_server.py` 9,729 / 360.

### W4.2 — Slices

| slice | scope | effort |
|---|---|---|
| **W4-a** | `python_sidecar.rs` — extract `tests_h3` (`:1490-1519`); **removes an allowlist entry entirely** | **S** |
| **W4-b** | `backend_ast.rs` plus `backend_ast_workflow.rs` — extract both, integrator lowers both pins | **M** |
| **W4-c** | `backend_cpu.rs` — **derive a marker manifest FIRST** (see below), then decide | **M** |
| **W4-d** | the three Python test giants — one file per PR, three PRs | **L** |
| **W4-e** | `rust_core/tests/test_schema_compat.rs` (4,412) plus `test_routing.rs` (2,995) | **M** |
| **W4-f** | allowlist integration: the `main.rs` stale-pin re-pin (W4.6) plus every pin W5-a/W5-b/W4-b/W4-c/W4-e moves | **S** |

**W4-c's manifest is the deliverable before any extraction.** Commit
`docs/audits/2026-08-20-backend-cpu-test-markers.json`: for every `#[cfg(test)]` in the file, its line,
whether it is **top-level or nested inside an item**, and — for top-level ones — the brace-balanced end
line and the resulting block length. Only then is it decided whether extraction clears 1,500. A slice
that starts from a prose count moves the wrong bytes; that is exactly how r1 went wrong.

`[LOCAL]` acceptance for the manifest:

    python -c "import json,re,sys,pathlib; m=json.load(open('docs/audits/2026-08-20-backend-cpu-test-markers.json')); src=pathlib.Path('rust_core/src/backend_cpu.rs').read_text(encoding='utf-8').splitlines(); hits=[i+1 for i,l in enumerate(src) if l.strip()=='#[cfg(test)]']; top=[r['line'] for r in m if r['top_level']]; print('file markers', len(hits), 'manifest', len(m), 'top-level', len(top)); sys.exit(0 if sorted(r['line'] for r in m)==sorted(hits) and len(top)==6 else 1)"

Expected: `file markers 13 manifest 13 top-level 6`, exit 0 — where "file markers" is the strict
stripped-line count (13), **not** the substring count (15, two of which are prose), and "top-level" is
the column-0 count (6), which is the only population an extraction moves. The assertion compares the
manifest to a **freshly derived** marker set, so a hand-typed manifest cannot pass. All three numbers
were derived on this tree at `7dfff2f`; if a future run disagrees, the file changed and the manifest is
re-derived, never re-typed.

### W4.3 — TDD approach

**Rust.** The RED is the compiler; there is nothing to write. The discipline is the **identity
invariant**, and r1's "same test count" was too weak — equal counts hide one test removed and one
added. The requirement is **exact pre/post test-NAME-SET equality**.

**Python (W4-d).** The hazard is not the split, it is the **duplicate**: pytest collects by NAME, so a
copy-paste split leaving the original block in place shows the same count while one copy silently
shadows the other. The AST duplicate-name check r1 promised in prose is now an executable command below.

### W4.4 — Acceptance tests

**Per Python family** `[LOCAL]` — all three families, not just `test_cli_modes*` as in r1.

**The pre-split manifest must be an independent witness, committed BEFORE the split.** r2 wrote the
baseline to an uncommitted `.tg-pre-*.txt` in the implementer's working tree, which makes the check a
**split oracle**: the arm that is supposed to constrain the split is regenerable by the person doing
the split, so re-capturing it afterwards passes trivially and proves nothing. The fix is sequencing
plus provenance:

1. In a **separate, earlier commit containing no source change** (`test:` class, non-releasing), write
   the three manifests to `tests/manifests/pre_split_<family>.txt`, commit them, and record their
   `sha256` in that commit's body. That commit is the witness; the split PR branches **from** it.
2. The split PR **may not modify those three files.** A diff touching them is an automatic reject --
   state it in the PR checklist — and the closeout manifest re-derives each `sha256` at the final SHA
   and compares it against the value in the witness commit body.

Capture command, run once, in the witness commit only:

    python -m pytest tests/unit/test_cli_modes.py --collect-only -q > tests/manifests/pre_split_cli_modes.txt
    python -m pytest tests/unit/test_benchmark_scripts.py --collect-only -q > tests/manifests/pre_split_bench.txt
    python -m pytest tests/unit/test_mcp_server.py --collect-only -q > tests/manifests/pre_split_mcp.txt
    python -c "import hashlib,pathlib; [print(hashlib.sha256(q.read_bytes()).hexdigest(), q) for q in sorted(pathlib.Path('tests/manifests').glob('pre_split_*.txt'))]"

then, after the split, per family (shown for `cli_modes`; identical for the other two):

    python -c "import subprocess,sys,glob; pre={l.split('::',1)[1] for l in open('tests/manifests/pre_split_cli_modes.txt') if '::' in l}; out=subprocess.run([sys.executable,'-m','pytest','--collect-only','-q',*glob.glob('tests/unit/test_cli_modes*.py')],capture_output=True,text=True).stdout; post=[l.split('::',1)[1] for l in out.splitlines() if '::' in l]; dupes=[n for n in set(post) if post.count(n)>1]; print('pre',len(pre),'post',len(post),'unique',len(set(post)),'dupes',len(dupes)); sys.exit(0 if set(post)==pre and not dupes else 1)"

Expected: `pre 545 post 545 unique 545 dupes 0`, exit 0. The **node-name set** must be identical (not
merely the count), and the duplicate detector must find zero — that is the AST identity gate, executable.

**Per Rust slice** `[CI]` — exact command, exact evidence:

- the job that must be green: `test-rust-core` (and, for `gpu_native.rs` only, `cuda-feature-check`);
- the PR body records the **workflow run ID and head SHA** of both the pre-change baseline run and the
  post-change run, plus the **complete job population** with zero unfinished or failing jobs. A
  `needs:`-gated job that never started is ABSENT, not pending, and does not count as green;
- the PR body records the **sorted test-name set** from both runs and asserts equality. A count is not
  accepted.

**Per Rust slice** `[LOCAL]`, size only:

    python scripts/file_size_budget.py --report

Expected: `0 regressions`, every remaining pin equal to its measured count, and `violations` at the value
this plan predicts for that step.

### W4.5 — End state: the floor and the ambition, stated separately

r1 gave `30 → 26` without labelling it as best-case, and in the same breath said every Python giant
stays over its limit. Both halves were loose. Corrected:

| slice | clears its limit? | violations removed |
|---|---|---:|
| W4-a `python_sidecar.rs` | **yes** (1,519 → ~1,489 vs limit 1,500) | 1 |
| W4-b `backend_ast*.rs` | **no** — 2,052 and 1,578 vs limit 1,500; pins drop, entries stay | 0 |
| W4-c `backend_cpu.rs` | **unknown until the manifest** — 1,817 vs 1,500 means it needs ≥317 lines of test block to clear | 0 or 1 |
| W4-d Python giants | **target yes** — a 545-test file split three ways lands each part under the 2,000 test limit, but only if the split is balanced; a 2-way split of 17,204 does not | 0 to 3 |
| W4-e Rust integration tests | **target yes** for `test_schema_compat.rs` (4,412, limit 2,000, needs ≥3 parts); `test_routing.rs` (2,995) needs 2 | 0 to 2 |
| W4-f re-pins | n/a — pins only | 0 |

**Conservative floor: `violations: 29`** (W4-a alone, the only guaranteed removal).
**Ambition: `violations: 23`** — the per-slice maxima in the table above are 1 + 0 + 1 + 3 + 2 + 0 =
**7**, and 30 - 7 = 23. (r2 wrote 22, which no row supports. The closeout manifest reconciles reality
either way, but this section exists precisely to stop loose arithmetic, so it does not get to contain
any.)
Anything in between is a success with its shortfall named per file. **The closeout manifest records the
actual number and, for every file that did not clear, the measured residual and its allowlist
disposition** — that is the reconciliation r1 was missing.

### W4.6 — The `main.rs` stale pin (the one number, stated once)

`scripts/file_size_allowlist.json` pins `rust_core/src/main.rs` at **15127**. The file measures
**15126**. The ratchet permits a shrink without a pin update, so this is green today — and it means one
line of regrowth is currently free. **W4-f re-pins it to the measured value.** Every other mention of
this pin in the plan refers here rather than restating the digits; r2 spelled the same pair three
different ways in three places (`15,126` / `15127` / `15126`), which is how a transcription error
enters a JSON file that a ratchet then enforces.

`[LOCAL]` acceptance:

    python scripts/file_size_budget.py --report
    python -c "import json,sys; a=json.load(open('scripts/file_size_allowlist.json'))['grandfathered']['rust_core/src/main.rs']; print('pin', a); sys.exit(0 if a==15126 else 1)"

Expected **after the W4-f re-pin and before W5-b**: `0 regressions`, and the pin prints `15126`
(exit 0). Run today, before the re-pin, the second command exits **1** printing `pin 15127` — that is
the RED arm, and it is the proof the assertion can fail. After W5-b the same
assertion is re-derived against the post-extraction measurement, not against this literal — the
literal is correct only for the pre-extraction file, and W4-f owns keeping it true.

---

## W5 — Rust follow-ups (PRIORITY 5, CI-serialised, no allowlist edits)

### W5.1 — Derived evidence

The `#[path]` mechanism is **measured** for library-crate modules (#1048, #1049 merged green). Two
cases remain structurally unproven:

- **`gpu_native.rs` is compiled by only one CI job.** `#[cfg(feature = "cuda")] pub mod gpu_native;`
  (locate: `grep -n 'gpu_native' rust_core/src/lib.rs`) and `default = []` in `Cargo.toml`, so a default
  `cargo check` or `cargo test` **never touches the file**. Only `cuda-feature-check` compiles it
  (`.github/workflows/ci.yml:684`). Two distinct steps exist there and must not be conflated:
  **`cargo check --features cuda --all-targets` at `ci.yml:723-725`** (compilation only) and
  **`cargo test --features cuda --lib` at `ci.yml:761-763`** (execution). The council asked whether the
  latter can actually run on a GPU-less runner. **Yes, and the workflow says so in its own words** —
  `ci.yml:744-747`: *"cuda-gated tests LINK and RUN on a GPU-less runner -- cudarc dlopens the driver,
  it does not link against it. The lib unit-test target passed 156 tests, including `gpu_native.rs`'s
  `#[cfg(test)]` module."* So W5-a has a real execution arm with a **stated baseline of 156 lib tests**,
  not just a compile arm. The same comment (`ci.yml:749-756`) records that the
  `tests/test_gpu_native*.rs` INTEGRATION targets are device-dependent and deliberately excluded —
  `--lib` is the invariant. W5-a must not widen that scope.
- **`main.rs` is the binary crate root.** Zero `pub fn` outside `mod tests`, 238 private. Integration
  tests in `rust_core/tests/` link against the *library*, so `main.rs`'s tests **cannot** become
  integration tests — not "difficult", structurally impossible without moving the CLI into the library
  crate.

### W5.2 — Items

**W5-a — the `gpu_native.rs` extraction (effort M).**
Extract `#[cfg(test)] mod tests` (`gpu_native.rs:4443-4911`) to
`#[path = "gpu_native_tests.rs"] mod tests;`, leaving in place the 41 lines of production code that
follow it (`cuda_library_search_paths` at `:4913`, `push_cuda_bin_candidates`; locate with
`grep -n 'fn cuda_library_search_paths' rust_core/src/gpu_native.rs`).
**One file, one change, one round-trip. No allowlist edit** — the pin drop 4,952 → ~4,484 is made by
W4-f at order step 10.

`[CI]` acceptance: `cuda-feature-check` green on both its steps, with the run ID, head SHA, complete job
population, and the **sorted test-name set** from `cargo test --features cuda --lib` before and after,
asserted equal — the workflow's own comment gives the expected magnitude (**156 lib tests**), so a run
reporting a materially different total is a finding, not a pass. Recording only the compile step is
insufficient and is explicitly not accepted.

*The branch-on-mismatch fork, stated in advance:* if it fails to compile, **stop and re-derive** — do not
assume the fix is obvious. The design doc names the one plausible mechanism (a `Drop` impl for unsafe
CUDA handle teardown separated from what it frees, `gpu_native.rs:681` and `:719`) and flags it as the
single place in the survey where a wrong split could compile clean and still be wrong at runtime. A pure
test-module move should not reach it; if it does, that is the finding.

*Security:* unsafe-FFI-adjacent code, so W5-a takes the **A3 adversarial gate** (section 4.2.1).
Test-module extraction only; **no production-code split in this item.**

**W5-b — `main.rs` test-module extraction (effort M).**
`#[path = "main_tests.rs"] mod tests;` for `main.rs:2984-7473`. This is the one case the tractability
doc could not collapse to "same as the others" without compiling: **binary** crate root rather than
library. The helper `command_template` (`main.rs:4169`) is defined *inside* `mod tests` and called by six
sibling tests — it moves with the block, so it does not block this move, but it *would* block any later
attempt to split those tests across multiple files.

`[CI]` acceptance: `test-rust-core` green; run ID, head SHA, job population, sorted test-name set equal
before and after. **No allowlist edit**; W4-f moves the `main.rs` pin down to the post-extraction
measurement (about 10,637) at order step 12.
*State plainly:* this does not clear the limit and is not claimed to.

**W5-c — `main.rs` architecture pass: DEFERRED, FLAG ONLY.**
**No implementation is authorised by this document, and no slice may act on it.**
The residual ~10,637 lines are clap arg structs, the `Commands` dispatch and ~230 private helpers with no
existing internal module boundary. Splitting them means choosing submodules and bumping visibility on the
order of hundreds of call sites — every bump compiler-checked, but the *grouping* is an architecture
decision that also collides with the four command-registration sites `AGENTS.md` documents. **Deliverable:
a filed row in `docs/BACKLOG.md` naming the required separate council.** No slice touches `main.rs` beyond
W5-b's mechanical move.

---

## W6 — Documentation: the junior-rebuildable bar (PRIORITY 6)

### W6.1 — Derived evidence

`docs/rebuild-guides/README.md` states the gap the directory exists to close and names the template:
*"`tg-checkpoint.md` — the worked template… **Future rebuild guides should follow this one's shape.**"*
`ls docs/rebuild-guides/` returns exactly four files (`README.md`, `tg-checkpoint.md`,
`verification-checklist.md`, `cache-and-schema-versioning.md`), i.e. **one** worked feature guide.

### W6.2 — W6-a: a second rebuild guide, subject `tg ledger` (effort M)

Selection is derived, not preferred. `tg ledger` matches every property the template's own selection
criteria imply — self-contained module, fully tested, stateful on-disk format:
`src/tensor_grep/cli/ledger_store.py` (1,417 lines, under its limit) plus four dedicated test files
(`ls tests/unit | grep -i ledger` returns `test_findings_ledger_is_repo_scoped.py`, `test_ledger_cli.py`,
`test_ledger_concurrency.py`, `test_ledger_store.py`). It is also the surface with a *known historical
trap worth documenting*: the PATH-footgun where `claim core/hooks` and `list .` resolved two different
stores until the nearest-`.git` canonical store landed (#706) — exactly the "trap a naive
reimplementation gets wrong, tied to a real guard and a real test" section the template requires.

Deliverable: `docs/rebuild-guides/tg-ledger.md`, following `tg-checkpoint.md` section for section:
problem solved; data flow; every file's contribution; on-disk format **verified by actually running it
against a throwaway scratch directory and reading the real artifact**; the traps, each tied to a real
guard and a real test; explicit out-of-scope.

`[LOCAL]` acceptance:

    python -m pytest tests/unit/test_public_docs_governance.py tests/unit/test_skill_library_drift.py -q
    python -m mkdocs build --strict

Expected: both exit 0.

Plus the guide's own bidirectional check: every `file:line`-class claim must be **located by symbol plus
grep**, and the verification tiers from `docs/rebuild-guides/verification-checklist.md`
(ran-and-observed / read-and-cited / unverified) must be stated per claim. A guide reporting one
undifferentiated "verified" fails its own checklist.

### W6.3 — W6-b: update the directory README (effort S)

Add the `tg-ledger.md` entry. **Do not restate the guide count as a number** — this repo has been burned
by prose enumerations of its own contents repeatedly. Describe the entries; let `ls` be the count.

`[LOCAL]` acceptance (r1 relied on grep's exit-code semantics, which was flagged as non-portable):

    python -c "import re,pathlib,sys; t=pathlib.Path('docs/rebuild-guides/README.md').read_text(encoding='utf-8'); has=t.count('tg-ledger.md'); counts=re.findall(r'there are \d+|\d+ (?:rebuild )?guides',t); print('entry',has,'count-sentences',counts); sys.exit(0 if has==1 and not counts else 1)"

Expected: `entry 1 count-sentences []`, exit 0. The empty list is a **labelled** zero: it means the regex
ran over real text and matched nothing, not that the file was unreadable — the `has==1` in the same
assertion is the positive control proving the file was read.

---

## 3. Explicitly out of scope, with reasons

An honest exclusion list survives audit; a silent omission does not.

| excluded | reason |
|---|---|
| **Task 2A / #89 / #90** (WSL typed-path) | BLOCKED on the board; PR #966 was CLOSED at CEO request and its branch retained as a parked RED scaffold. Needs a real WSL CI lane, which this campaign does not build. Touching it would advance a row the board says is not GREEN. |
| **`MCP-SURFACE`** (contract 1.7.0 to 1.8.0/1.9.0) | BLOCKED behind Task 2C. The row's own trigger says a bump "must not bump from a nonexistent base". W2 deliberately stops at the dependency floor and the decision doc. |
| **`MCP-LEAN-DEFAULT`** | DEMAND_GATED and sequenced *after* Task 2C on the same ladder. The 2026-08-20 receipts strengthen the direction (deterministic `tools/list` ordering plus required `ttlMs`/`cacheScope` are now spec-level pressure toward a cacheable surface) — but strengthening a direction is not clearing a fence. |
| **`DD-006`** product build | The demand condition is satisfied and the design packet merged (#1015), but A122 says docs alone do not SHIP the parent row, and reopening needs deliberate authorisation for the PERF plus HONESTY build. Not authorised here. |
| **`CONTINUOUS-REFRESH`** (warm index service) | DEMAND_GATED on an approved scoping pass. CodeNib's numbers (8.7x/25.4x update speedups; static index 4.7x faster than a live language server, arXiv:2607.25431) make the scoping pass more attractive, not already-approved. |
| **`#255`, `AST-DSL-PARITY`, `RUST-REPLACE-TOCTOU`** | DEMAND_GATED, all three re-derived LEAVE within the last eight days with unmet reopen arms. Re-litigating a fresh disposition is churn. |
| **`#48` / `#72` / `#77` / `#131` / `#169`** | CEO_GATED. #169 is the only mandatory financial stop. No silent gate flips. |
| **`F5` / `F6` / `F8`** | BLOCKED on `rust_core/**` plus `tests/e2e/**` under the shared-box cargo and e2e ban. W5 stays inside the mechanical test-module class precisely to avoid claiming any of these. |
| **`main.rs` architecture pass** | `W5-c` — flagged, deferred, needs its own council. Named rather than omitted. |
| **Adopting Agent Retrieval Bench as a scored gate** | Verified real and usable (MIT; `pip install -e .`; `arb download-benchmark --version v2_edit2ripple`; HF dataset `eyuansu71/agent_retrieval_bench`; 58 `edit2ripple` and 82 abstention samples; arXiv:2607.24882, 2026-07-27). It is the first external harness that scores ranked-under-budget, ripple/blast-radius and **abstention with wrong-repository controls** — i.e. it would reward tg's fail-closed refusal instead of punishing it. Excluded anyway: the corpus is about 392K files and 7.9M chunks, a CPU-heavy download-and-index job forbidden on the shared desktop, and standing up a new scored gate mid-closeout invites hill-climbing against an uncalibrated signal. **Filed as FU-4, not built.** |
| **MCP 2.0 migration itself** | See W2.2. Upstream explicitly recommends the `<2` pin until migration; v1.x still receives security patches; `FastMCP` was removed in 2.0 so the migration is a rewrite; and there is no client demand. Deferred with six named triggers and a watcher, not ignored. |
| **Lowering the limit from 1,500 to 1,000** | `docs/design/2026-08-19-split-floor-escape.md`, section 6, records that the brief and the audit template disagree, and that at 1,000 **eleven more files violate**. Changing the limit mid-campaign would rewrite every acceptance number in W4. |
| **Any release-bearing merge during the campaign's Rust rounds** | The publish window is about 6 minutes and a mid-window merge rejects the in-flight push. W2-b is the only `fix:` in this plan and is sequenced last among code changes for exactly this reason. |

### 3.5 Filed follow-ups (rows created at closeout; no code in this campaign)

The council named four gaps that are real but do not belong in a closeout. Each becomes a
`docs/BACKLOG.md` row with its reopen condition; the closeout manifest verifies the rows exist.

- **FU-1 — improve the broad-handler detector.** Its own docstring says it "both over- and
  under-counts"; W1 works around that with human review of 54 candidates. Dataflow-aware detection
  ("does this handler always log or re-raise?") would cut the false-positive rate and make the next
  audit cheap. Not in W1, because changing the detector mid-audit changes the population being audited.
- **FU-2 — a cold-path performance baseline in CI.** The runtime cost of the `_self.` indirection Route A
  shipped is **unmeasured** (section 6), and W1/W3/W4 all touch hot modules. A CI benchmark job timing
  `tg search` on a fixed corpus would give a baseline. Cannot be run on this box; needs a CI lane, which
  is its own change.
- **FU-3 — assess user-facing documentation.** The campaign's docs work (W6) is internal-developer
  documentation. `tg --help` text, changelog quality beyond CI automation, and API docs were **not
  assessed**, and "not assessed" is the honest word — not "adequate".
- **FU-4 — Agent Retrieval Bench as an external retrieval gate.** See the exclusions table.

### 3.6 Regression surfaces this campaign touches but does not re-verify

Stated because the council asked and the honest answer is "not covered", not "fine":

- **`tg find` retrieval quality.** W1-a edits the MCP tool surface, W1-c edits `cli/main.py`, and W3-a
  reads `cli/repo_map.py` — none of the acceptance commands exercise the hybrid BM25 + dense path. The
  live harness is `benchmarks/eval_late_rerank_quality.py` (**not** the retired
  `run_repo_retrieval_benchmarks.py`), and it is a CPU-heavy run forbidden on this box. **Mitigation, not
  coverage:** the `search-golden-parity` CI job (`.github/workflows/ci.yml:765`) runs on every code PR and
  is a required green for all W1 slices — it is a parity check, not a quality benchmark, and this plan
  does not claim otherwise. A real quality re-run rides FU-2's CI lane.

---

## 4. The pipeline this plan follows

1. **Thinktank adversarial audit of THIS document**, then fix in place on
   `docs/worldclass-closeout-plan`, then re-audit, repeating until APPROVE. No PR is opened before
   APPROVE. A no-verdict seat is a **failed seat**, not a blocker and not an approval (A10).
2. **Implementation via subagents**, one writer per file set, model-tiered explicitly per slice.
   `cursor-agent` may take **mechanical writes only** — and per the gate-evasion rule **cursor never
   touches gates, verifiers, registries, or allowlists**: `scripts/file_size_allowlist.json`,
   `scripts/bare_call_pins.json`, `scripts/file_size_budget.py`, `scripts/bare_call_ratchet.py`,
   `scripts/handler_census.py`, `scripts/check_costing_doc.py`,
   `tests/unit/test_silent_failure_hardening.py`, `tests/unit/test_handler_dispositions.py` and every
   governance test are off-limits to it. Those edits are made by a Claude seat that also runs the
   perturbation arms.
   - **4.2.1 — the A3 adversarial security gate is MANDATORY for `W1-a`, `W1-b`, and `W5-a`.**
     r1 omitted it, and all three seats caught the omission. AGENTS.md A3 triggers on
     `mcp_server` (W1-a), installer / native-asset / doctor-probe construction (W1-b's
     `native_frontdoor.py`, `windows_launcher.py`, `doctor_report.py`), and `*_backend`-class unsafe
     native code (W5-a). The gate is an **Opus seat instructed to try to BREAK it, citing `file:line`,
     defaulting to FIX-FIRST if uncertain** — it is a *separate seat* from the codex audit in 4.3 and
     one does not substitute for the other. No A3-triggering slice merges without it.
3. **Codex audit against this plan** until `RECOMMENDED: APPROVE`, per slice, capped at 5 rounds with a
   new defect class each round or a declared clear. For W1 slices the codex seat is also the **named
   reviewer of the disposition table** (W1.4).
4. **Merge** — the total order in section 2; one PR at a time; union-merge check before queueing any
   concurrent PR.
5. **Lint and format** — `ruff format --preview` and `ruff check` are both CI gates; a local
   `ruff check` pass is not green.
6. **Dogfood the published artifact** after W2-b publishes: in a clean env,
   `uvx --from tensor-grep==<new> tg …` both arms (a real symbol resolves; a fabricated one returns
   `no_match`) **and** the MCP process smoke from W2.4 — start the server from the published wheel,
   `initialize`, `tools/list`, assert non-empty and contract `1.7.0`. The symbol/no-match dogfood alone
   does not exercise the SDK floor change, which was r1's gap.
7. **Findings appended to `docs/BACKLOG.md`** at closeout by the orchestrator, including `W5-c` and
   `FU-1..FU-4`.
8. **The closeout manifest** — `docs/audits/2026-08-20-worldclass-closeout-manifest.md`, produced last,
   containing for every canonical item ID in section 1.5: disposition, PR number, merged SHA, the
   acceptance command **re-run at closeout** with its output, and for A3-gated slices the seat's verdict.
   Plus, once, at the final merged SHA: both ratchets green; `python -m pytest` full-suite result with
   run ID; the `violations:` number reconciled against W4.5's floor-and-ambition table with a per-file
   residual for anything that did not clear; `mkdocs build --strict` green; the published-artifact MCP
   smoke; and the `docs/TASK_BOARD.md` / `docs/BACKLOG.md` transitions. **A campaign is complete when
   this manifest exists and every command in it was re-run at the final SHA** — not when the last PR
   merges.

---

## 5. The three highest-risk items, named

1. **`W1-a` (MCP-surface handler audit).** Twenty-five not-provably-disclosing handlers on a
   network-reachable tool surface, where the wrong classification ships a swallowed exception as an
   empty successful tool result. The disposition ledger and the RED-3 behavioural arm for intentional
   boundaries make the judgement auditable and partly testable; the residual risk is the judgement
   itself, which is why this slice carries both the codex reviewer and the A3 seat.
2. **`W5-a` (`gpu_native.rs`).** The only file in the campaign compiled by a single narrow CI job, and
   the only place where a wrong move could compile clean and be wrong at runtime (unsafe CUDA `Drop`).
   A green default `test-rust-core` says nothing about it.
3. **`W3-a` (beyond Route A).** Highest risk of *wasted work*: the honest answer may be "accept the
   pins", and a plan that cannot say that out loud will instead produce a 787-edit Route B whose failure
   mode is the silent false green this whole campaign exists to prevent. The predeclared decision rule
   in W3.2 exists so that answer is reached by a threshold set before the numbers were seen, not
   rationalised after.

---

## 6. What could not be derived

- **The runtime cost of the `_self.` indirection already shipped.** The design doc requires a benchmark
  per conversion; the shared-desktop ban forbids running one here. Whether `_read_source_text_cached`
  (14 in-module calls, genuinely hot) regressed is **unmeasured**, not "fine". Filed as FU-2.
- **Any Rust compile fact.** Every Rust statement here is either a measured line count, a quoted CI
  result from a merged PR, a quoted workflow line, or an explicitly-labelled hypothesis. None was
  verified by compiling.
- **Whether the 54 not-provably-disclosing handlers contain any real SILENT-SWALLOW.** The detector
  proves only that its AST shapes did not match. The census's own docstring records that manual review
  of the in-census population found **zero** survivors — so the honest prior is that most of the 54 are
  false positives. **W1 is scoped as classification, and if it finds zero defects that is a successful
  wave, not a failed one.** What r2 adds is not a different expectation but a stronger evidence
  requirement: a committed ledger, a completeness gate with four perturbation arms, behavioural tests
  for every network-facing intentional boundary, and two named reviewers. A zero-defect W1 must now be a
  *proved* zero rather than an asserted one.
- **Whether `tg find` quality moves.** See section 3.6. Not covered, mitigated by parity only.

---

## Appendix A — disposition of council round 1

Seats: claude (4 findings, 3 blockers), codex (16 findings), droid/deepseek (3 blockers, 4 high).
All three returned REVISE. Every finding is taken except the two marked NOT TAKEN.

| finding | disposition |
|---|---|
| backend_cpu "five" vs derived "six" (all 3 seats) | **TAKEN** — W4.1 says SIX; W4-c no longer takes a count from prose at all but derives a committed marker manifest verified against a fresh scan. The recurrence is called out in the blockquote as its own lesson. |
| W1 concurrency + ceiling arithmetic (claude F3, codex 2) | **TAKEN** — W1 slices strictly serialized `W1-d → W1-a → W1-b → W1-c`; the rule is now "currently merged ceiling + delta, re-derived at rebase time". |
| eight zero-handler modules unowned (deepseek 3, 7C) | **TAKEN** — assigned to W1-d, with the labelled-zero control stated because RED-1 structurally cannot fire for them. |
| allowlist re-pin ownership (claude F1, codex 14) | **TAKEN** — W4 is the sole allowlist integrator for the whole campaign including W5's pins; "only cross-wave hand-off" deleted; r1's W3-b renumbered `W4-f`; total merge order published in section 2. |
| W1 acceptance satisfiable by a no-op audit (codex 1, claude) | **TAKEN** — disposition ledger keyed by fingerprint, `test_handler_dispositions.py` with completeness/uniqueness/locatability/vocabulary/evidence assertions and four perturbation arms; codex seat named as reviewer; RED-3 behavioural arm for network-facing intentional boundaries. |
| A3 gate not named (deepseek 6A) | **TAKEN** — section 4.2.1, mandatory for W1-a, W1-b, W5-a, explicitly a separate seat from the codex audit. |
| W2-c inert / must be mandatory (codex 5, claude F4) | **TAKEN** — REQUIRED, and the SDK-constant tripwire is **replaced** by a recurring PyPI re-derivation in the release-drift sweep with three labelled verdicts plus `CANNOT_MEASURE`. |
| W2-a acceptance decorative (codex 3) | **TAKEN** — structured trigger block; the checker requires six distinct ids AND six distinct types, so repeated headings cannot pass. |
| W2-b security framing unsupported (codex 4) | **TAKEN** — reframed as maintenance policy with the absence of an advisory stated; acceptance extended to lock regeneration, lowest-bound resolution on the supported matrix, and an MCP initialize/tool-list smoke. |
| missing trigger classes (codex 6) | **TAKEN** — T4 python/platform support loss, T5 unpatchable transitive dep, T6 time-bounded `revalidate_by`. |
| trigger (ii) bar unspecified (deepseek 3B) | **TAKEN** — T2 now requires a named client with a reproduction not resolvable by a client-side pin. |
| W3-a cannot fail (codex 7, deepseek 1B) | **TAKEN** — `scripts/check_costing_doc.py` with an 11-field schema, per-number derivation commands, and its own perturbation arms. |
| "accept the pins" decision rule (codex 8) | **TAKEN** — predeclared per-module thresholds (≤150 edits / ≤3 CI rounds pursue; >300 edits / >6 rounds accept; escalate between), dated and reopenable, never a blanket. |
| require a counter-argument (deepseek 4) | **TAKEN** — the costing doc must state the strongest argument against its recommendation. |
| W4 Python acceptance covers one of three, AST gate not executable (codex 10) | **TAKEN** — per-family pre/post node-ID manifests with set equality and an executable duplicate detector. |
| W4 end-state contradiction (codex 11, deepseek 1C) | **TAKEN** — W4.5 states floor 29 and ambition 22 with a per-slice clears-or-not table; the manifest records the actual and every residual. |
| Rust acceptance not identity-preserving (codex 12) | **TAKEN** — exact CI command, run ID, head SHA, complete job population, sorted test-NAME-SET equality; `cargo check --features cuda` at ci.yml:723-725 and `cargo test --features cuda --lib` at ci.yml:761-763 distinguished, with the runner's dlopen behaviour cited from ci.yml:759-760 as the proof the execution arm really runs. |
| item count unstable (codex 13) | **TAKEN** — section 1.5 canonical registry with dispositions; the bare "19" is gone. |
| no campaign-level receipt (codex 16) | **TAKEN** — section 4.8 closeout manifest, including the published-artifact MCP smoke. |
| portability of acceptance commands (codex 15) | **TAKEN** — constraint 10; every command tagged `[LOCAL]`/`[CI]`; POSIX `test`/`tail`/grep-exit idioms replaced with `python -c`. |
| collision map missing pyproject/uv.lock; diagram wrong (deepseek 7A, 7B) | **TAKEN** — both added; the diagram now shows the serial Rust lane. |
| census script not reproducible (deepseek 2B) | **TAKEN** — committed as `scripts/handler_census.py` by W1-d. |
| detector improvement as follow-up (deepseek 5B) | **TAKEN** — FU-1, with the reason it is not in W1 (changing the detector mid-audit changes the population). |
| user-facing docs unassessed (deepseek 6B) | **TAKEN** — FU-3, worded as "not assessed", not "adequate". |
| perf baseline (deepseek 6C) | **TAKEN** — FU-2. |
| `tg find` quality unverified (deepseek 6D) | **TAKEN as a stated gap, not as coverage** — section 3.6 names `search-golden-parity` as parity-only mitigation and routes the real quality run to FU-2's CI lane. Running `eval_late_rerank_quality.py` locally is forbidden by constraint 1. |
| W3-a acceptance should be labelled "reviewer gate, not machine gate" (deepseek 1B) | **NOT TAKEN as worded** — instead of labelling the weak gate honestly, r2 replaced it with a real machine gate (`check_costing_doc.py`). The label would have been honest; the checker is better. The *judgement* of whether the recommendation is correct remains a reviewer gate, and section 4.3 names that reviewer. |
| codex 9 (W4 premise contradiction), codex 2, codex 14 partial restatements | folded into the rows above; no separate action. |

---

## Appendix B — disposition of council round 2

Seats: cursor and droid/deepseek, both REVISE. Cursor confirmed every round-1 fix is present in the
operative text; deepseek confirmed the wave-deliverable phrasing is unambiguous. Findings were narrow
and textual, so r3 is surgical — no restructuring. All six taken.

| finding | disposition |
|---|---|
| §2 diagram writes the W1 order as `a -> d -> b -> c` while W1.2, the merge order and appendix A say `W1-d -> W1-a -> W1-b -> W1-c` | **TAKEN** — diagram corrected. Also recorded as a *recurrence*: this is the second round in a row a diagram contradicted its own body (r1's W4-vs-W5 lanes). §2 now states that the order is defined in exactly one place — the total merge order — and the diagram is a picture of it that must be re-read against it every revision. |
| W1.4 completeness is defined against the full formerly-excluded population, but slices merge serially with an append-only ledger, so intermediate merges cannot satisfy it and an implementer would invent an unstated scope | **TAKEN** — completeness is now explicitly *cumulative*: at any commit, every handler in the modules removed from `_EXCLUDED_MODULES` so far has exactly one record, **and** no record exists for a still-excluded module (both directions, so a slice cannot claim credit for unaudited work). Derived per-merge expectations written in: W1-d → 2, W1-a → 59, W1-b → 82, W1-c → 128, with `len(ledger) == 128` reached by the rule rather than hardcoded. |
| W4.4's node-ID set equality is a split oracle — the pre-split capture is an uncommitted `.tg-pre-*.txt` under the implementer's control, so regenerating it post-split passes trivially | **TAKEN** — the baseline becomes an independent witness: three manifests written to `tests/manifests/pre_split_*.txt` and committed in a **separate earlier commit with no source change**, their `sha256` recorded in that commit body, the split PR branched from it and forbidden to modify them, and the closeout manifest re-deriving each hash at the final SHA. The comparison command now reads the committed path. |
| the merge order omits `W4-f` re-pin steps after W4-c and W4-e, though W4.2 scopes W4-f to "every pin W4-c/W4-e moves" — the allowlist would drift and the ratchet fail on the next run | **TAKEN** — pin steps inserted after all five Rust merges (order is now 23 steps). Added the rule that a `W4-f` step which finds nothing to change is recorded as a **no-op**, not skipped: a skipped step and an empty step are not the same evidence. |
| W4.5 ambition arithmetic: per-slice maxima sum to 7 removals from 30, so ambition is 23, not 22 | **TAKEN** — corrected to 23 with the addition shown inline (1+0+1+3+2+0 = 7). The section exists to stop loose arithmetic, so it does not get to contain any. |
| define the ledger fingerprint precisely (line-shift stability is claimed while the schema carries `lineno`); normalize the `main.rs` pin spelling (three spellings in three places) | **TAKEN, both** — the fingerprint is now split into **IDENTITY** = `(module, enclosing_symbol, handler_index_within_symbol)`, over which uniqueness and completeness are computed, and **ADVISORY** = `lineno`, which is never part of identity but is still range-checked against the enclosing symbol's span; a rename or handler reorder changes identity and forces re-derivation. The pin gets one home, **W4.6**, which states `15127` pinned / `15126` measured once with its own acceptance command; every other mention now refers to W4.6 instead of restating digits. |

