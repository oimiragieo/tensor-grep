# Plan: the world-class closeout campaign

**Date:** 2026-08-20. **Status:** DRAFT — awaiting thinktank adversarial audit. Not a build licence.
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

### Shared files — the collision map

One writer per file set. The two **proven** collision points, plus three more this campaign creates:

| file | why it collides | rule |
|---|---|---|
| `scripts/file_size_allowlist.json` | every size slice edits it | **W4 owns it.** W1/W2/W5/W6 must not touch it. If a W1 handler fix changes a line count, W1 stops and hands the pin edit to W4. |
| `tests/unit/test_silent_failure_hardening.py` | ceiling plus exclusion set | **W1 owns it exclusively.** No other wave may edit it, including to remove a module it deleted. |
| `scripts/bare_call_pins.json` | currently `{}` — any wave that reintroduces a bare call reds it | read-only for all waves; a diff here is a defect, not an edit |
| `docs/TASK_BOARD.md` and `docs/BACKLOG.md` | every wave wants to append | **append-only, at closeout, by the orchestrator** — never by a slice agent, never mid-wave |
| `src/tensor_grep/cli/mcp_server.py` | W2 reads it; W4 must not move it | W2 owns; W4's file-size work excludes it |

---

## 2. Wave structure

Waves W1, W2, W4, W6 are mutually independent by file set and may run concurrently.
W3 is **gated on W1** (it proposes changing the patched-symbol set that W1 is reading).
W5 is **CI-serialised** — one experiment per round-trip, no fan-out.

    W1 (handler audit)  ---+---> W3 (floor lever, design-first)
    W2 (MCP decision)   ---|
    W4 (size: 27 files) ---|
    W6 (rebuild guide)  ---+
    W5 (Rust CI experiments) --- serial, own lane

Item count: **W1 = 4, W2 = 3, W3 = 2, W4 = 5, W5 = 3, W6 = 2. Total 19.**

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
`_body_records_reason`, `_body_reraises`) so the audit and the gate cannot disagree about what counts:

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

**Positive control on the instrument:** the same script, run over the NON-excluded population, must
reproduce 137 exactly — it is the number the shipped gate asserts. A census that cannot reproduce the
known number is not trusted to count the unknown one. **Negative control:** eight of the nineteen
excluded modules return **0**, and those zeros are real absences (the modules exist and parse), not a
dead scan — which the 128 non-zero total proves.

The 54 are the audit target. They are *not* 54 defects: the module docstring records that a
naive keyword detector "both over- and under-counts", and that several sites its own AST shape could
not prove logged were confirmed LOGGED-DEGRADE by reading. **Each of the 54 must be read and
classified individually** (SILENT-SWALLOW / LOGGED-DEGRADE / INTENTIONAL-BOUNDARY), not swept.

### W1.2 — Slices (one writer per file set)

| slice | files owned | handlers | not-provably-disclosing | effort |
|---|---|---:|---:|---|
| **W1-a** MCP surface | `cli/mcp_server.py`, `cli/mcp_symbol_tools.py`, `cli/mcp_audit_tools.py`, `cli/mcp_rewrite_tools.py` | 57 | 25 | **L** |
| **W1-b** doctor plus front door | `cli/doctor_report.py`, `cli/native_frontdoor.py`, `cli/windows_launcher.py`, `cli/ast_scan.py` | 23 | 21 | **M** |
| **W1-c** `cli/main.py` | `cli/main.py` | 46 | 12 | **L** |
| **W1-d** the two lang strays | `cli/repo_map_lang_js.py`, `cli/repo_map_lang_rust.py` | 2 | 2 | **S** |

**W1-a goes first.** It is the network-reachable surface (an MCP tool answering an untrusted client),
and it is where a swallowed exception becomes an empty-but-successful tool result — the exact shape
`AGENTS.md`'s Backend Fail-Closed Contract forbids. W1-d is the cheapest and should run concurrently
as the shape-check: two handlers, one PR, proving the acceptance protocol before the large slices
commit to it.

### W1.3 — TDD approach: what RED comes first

The RED is **not** "the handler is wrong". It is **"the census cannot yet see this module"**.

1. **RED-1 (the gate is blind).** In `tests/unit/test_silent_failure_hardening.py`, delete the slice's
   modules from `_EXCLUDED_MODULES` without changing the ceiling. Run the test. It must fail with a
   count ABOVE 137 naming exactly this slice's modules. *If it passes, the census is not reading them
   and the whole slice is measuring nothing — stop and fix the instrument.* This is the arm that makes
   every later green mean something.
2. **RED-2 (per SILENT-SWALLOW found).** For each handler classified SILENT-SWALLOW, write a test that
   drives the real failure and asserts the caller observes it — a raised `BackendExecutionError`, a
   populated `fallback_reason`, or a non-zero exit — and confirm it is RED on the *pre-fix* bytes.
   A handler classified LOGGED-DEGRADE or INTENTIONAL-BOUNDARY gets **no code change and no test**;
   it gets one line in the classification table with the reason.
3. **GREEN.** Harden only the SILENT-SWALLOW sites (narrow the exception type, re-raise as
   `BackendExecutionError`, or attach a visible reason). Then remove the slice's modules from
   `_EXCLUDED_MODULES` and set the ceiling to the newly-measured total **in the same commit**.
4. **The ceiling may only move by the arithmetic the slice can show.** New ceiling equals 137 plus this
   slice's remaining broad handlers after hardening. Write that sum in the commit body. A ceiling that
   cannot be derived from a printed subtraction is a laundered `git mv`.

### W1.4 — Acceptance test (executable)

    python -m pytest tests/unit/test_silent_failure_hardening.py -q

Expected: all tests in the file pass, with the slice's modules **absent** from `_EXCLUDED_MODULES` and
the ceiling equal to the printed sum.

Plus the exclusion-shrink proof, which is the thing a reviewer actually needs:

    python -c "import importlib.util; s=importlib.util.spec_from_file_location('sfh','tests/unit/test_silent_failure_hardening.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('excluded modules:', len(m._EXCLUDED_MODULES), 'ceiling:', m.TOTAL_BROAD_HANDLERS_CEILING)"

Expected at W1 start: `excluded modules: 19 ceiling: 137`.
Expected after all four slices: `excluded modules: 0` and a ceiling the four commit bodies sum to.

**Security notes.** W1-a and W1-b are the security-bearing halves. A swallowed exception in
`native_frontdoor.py` sits on the **checksum-gated asset install** path — a broad handler there can
convert a failed verification into a silent success, which is the `supply-chain-hardening` fail-closed
rule inverted. Any handler on that path is SILENT-SWALLOW **by default** and must be argued out of it,
not into it. `windows_launcher.py`'s handlers sit on PATH and COM manipulation. Treat both as
security review, not hygiene.

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

**This flipped the item from "plan a migration" to "PIN AND DEFER, with a named trigger and one cheap
floor bump."** Without the second lookup the honest-looking move is to schedule a 2.0 migration; the
maintainers' own guidance says the supported posture for a not-yet-migrated server is exactly the
upper bound tg carries, on a line that still receives security patches. Migrating now would be a large
rewrite (`FastMCP` was deleted in 2.0) bought with no user demand and no security pressure — and it
would collide head-on with the `MCP-SURFACE` row, which is fenced behind Task 2C at contract 1.7.0.

### W2.3 — Items

**W2-a — Record the decision: PIN AND DEFER (effort S).**
Write `docs/design/2026-08-20-mcp-2-0-exposure-decision.md` stating the posture, both lookups with URL
and fetch date, and **three named reopen triggers, any one of which reopens**:
(i) an upstream announcement of a v1.x end-of-support date, or a v1.x security advisory with no v1.x
patch; (ii) a named client that speaks only 2026-07-28 and cannot reach tg; (iii) Task 2C clearing,
which unblocks `MCP-SURFACE` and makes a contract bump legal anyway.

Acceptance:

    test -f docs/design/2026-08-20-mcp-2-0-exposure-decision.md && grep -c 'REOPEN TRIGGER' docs/design/2026-08-20-mcp-2-0-exposure-decision.md

Expected: `3`.

**W2-b — Bump the floor to the patched line (effort S, security-adjacent).**
`mcp>=1.27.2,<2` becomes `mcp>=1.29.0,<2` in `pyproject.toml`, lock regenerated.
*RED first:* a test asserting the floor is at least 1.29.0 must fail on the pre-change pin.

Acceptance:

    python -m pytest tests/unit/test_mcp_dependency_is_upper_bounded.py -q
    grep -n 'mcp>=' pyproject.toml

Expected: tests pass, and the grep prints `"mcp>=1.29.0,<2",`.

*Security note:* the reason to bump is that 1.29.0 is the head of the line receiving security patches;
a floor two minors behind means a resolver may legally install an unpatched SDK. Per the disclosed-CVE
floor rule this is a floor bump, not a cap change — the `<2` bound is deliberate and stays.
*Release class:* `fix:` — this one publishes, and should be merged alone in its own window.

**W2-c — A readiness tripwire, NOT a migration (effort M, OPTIONAL — council may cut it).**
One test that pins what tg would have to change, so the deferral does not rot into ignorance: assert
that `SUPPORTED_PROTOCOL_VERSIONS` as imported does **not** contain `"2026-07-28"`, and that
`_TG_MCP_SERVER_CONTRACT_VERSION` is `"1.7.0"`. When a future SDK bump adds the revision, this test
goes RED and *tells the maintainer the deferral has expired* — a tripwire rather than a comment.

Acceptance:

    python -m pytest tests/unit/test_mcp_protocol_revision_tripwire.py -q

Expected: passes today; and its perturbation arm (hand-inject `"2026-07-28"` into the asserted set)
must fail, naming the revision. **State the perturbation result in the PR body or the test is
decoration.**

*Honest risk the council should weigh:* a tripwire keyed to an SDK constant fires on an SDK bump, not
on a spec event — it can be green while the spec has moved. It is a *cheap partial*, and this plan
says so rather than selling it as coverage.

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

### W3.2 — W3-a: Design-only — what actually moves the floor (effort M, DESIGN ONLY)

Deliverable: `docs/design/2026-08-20-beyond-route-a.md`. **No code.** It must cost, with a measured
number per option, at least these three, and say which it recommends and why:

1. **Shrink the patched-symbol set** (49 / 66 / 66 symbols). The floor is a closure over what tests
   patch. Every symbol a test stops patching drops its whole transitive cone out of the locked set.
   The costing must name the top symbols by **cone size**, not by patch count, because one widely-called
   symbol can dominate. This is Route B's blast radius (75 test files) applied *surgically* to the few
   symbols that pay, rather than wholesale.
2. **Dependency injection at the seam** — pass the collaborator in rather than patching a module
   attribute. Removes the mechanism instead of relocating it. Costs a signature change per seam.
3. **Accept a documented exception** — keep the three at their pinned sizes permanently, with the
   floor measurement as the stated reason, and spend the campaign's remaining budget on W1/W4/W6.
   **This is a legitimate outcome and the plan says so.** A grandfather pin backed by a measured
   impossibility proof is honest engineering; a plan that refuses to consider it will produce a
   forced, expensive, low-value refactor.

Acceptance:

    test -f docs/design/2026-08-20-beyond-route-a.md
    grep -c 'RECOMMENDATION:' docs/design/2026-08-20-beyond-route-a.md
    python scripts/measure_split_floor.py

Expected: the file exists; exactly `1` RECOMMENDATION line; and the doc quotes the
`measure_split_floor.py` output verbatim rather than paraphrasing it.

*Gate:* W3-a is **blocked until W1 completes**, because option 1's costing reads the same modules W1 is
editing, and a cone measured mid-edit is a cone measured on a tree nobody will ship.

### W3.3 — W3-b: Re-pin `rust_core/src/main.rs` (effort S)

`scripts/file_size_allowlist.json` pins `rust_core/src/main.rs` at **15127**; the file measures
**15126**. The ratchet permits a shrink without a pin update, so this is green — and it means one line
of regrowth is currently free. Re-pin to the measured value.

Acceptance:

    python scripts/file_size_budget.py --report
    grep -n '"rust_core/src/main.rs"' scripts/file_size_allowlist.json

Expected: still `violations: 30   grandfathered: 30` and `0 regressions`; the grep prints `15126`.

**File ownership:** this edits the W4-owned allowlist, so **W3-b is executed BY W4**, listed here only
because the finding belongs to this section. It is the only cross-wave hand-off in the plan.

---

## W4 — The 27 other oversized files (PRIORITY 4)

**Owns `scripts/file_size_allowlist.json`.** Nothing else may touch it.

### W4.1 — Derived evidence

`python scripts/file_size_budget.py --report` reports 30 violations. Removing the three W3 giants leaves
**27**. Two structurally distinct classes:

**Class A — Rust inline `#[cfg(test)] mod tests` extraction. Mechanism PROVEN in CI.**
`index.rs` and `native_search.rs` already carry `#[path = "…_tests.rs"] mod tests;` and merged green
(#1048, #1049). Remaining candidates, with block spans measured in
`docs/design/2026-08-20-rust-split-tractability.md` sections 4 and 6:

| file | now | `mod tests` span | after extraction | clears 1,500? |
|---|---:|---|---:|---|
| `rust_core/src/python_sidecar.rs` | 1,519 | two top-level markers, `:1082` and `tests_h3` at `:1490-1519` (30 lines) | about 1,489 from `tests_h3` alone | **YES** |
| `rust_core/src/backend_ast_workflow.rs` | 2,109 | `:1579-2109` (530) | about 1,578 | no (1.05x) |
| `rust_core/src/backend_ast.rs` | 2,553 | `:2053-2553` (500); two further `#[cfg(test)]` attributes are nested inside items at `:53` and `:1428` and do NOT move | about 2,052 | no |
| `rust_core/src/backend_cpu.rs` | 1,817 | **six** top-level markers (`:282,303,309,315,1088,1778`) plus nine nested attributes inside functions — not one block | unknown | **must brace-balance every top-level marker first** |
| `rust_core/src/gpu_native.rs` | 4,952 | `:4443-4911` (468), with 41 lines of PROD code AFTER it | about 4,484 | no |
| `rust_core/src/main.rs` | 15,126 | `:2984-7473` (4,489) | about 10,637 | no (7.1x) |

> Two of those spans were **re-derived here and disagree with the design doc**, which was written
> before #1048/#1049 landed: `python_sidecar.rs`'s second marker is at `:1490`, not `:1491`, and
> `backend_cpu.rs` has **six** top-level markers, not five (`:1778` was missed). Command:
> `grep -n '#\[cfg(test)\]' rust_core/src/{python_sidecar,backend_ast,backend_ast_workflow,backend_cpu}.rs`.
> Neither changes a verdict, but a slice briefed off the design doc's numbers would move the wrong
> bytes — take the spans from this table, or re-run the grep.

**Class B — Rust integration tests and Python test files.** `rust_core/tests/*.rs` are already separate
compilation units seeing only the library's `pub` surface, so splitting one into two costs no
visibility change — tractable by inspection. Python test files split freely too, with one caveat
below. Worst offenders (`grep -c 'def test_'`):
`tests/unit/test_cli_modes.py` 17,204 lines / 545 tests; `tests/unit/test_benchmark_scripts.py`
10,689 / 236; `tests/unit/test_mcp_server.py` 9,729 / 360.

### W4.2 — Slices

| slice | scope | effort |
|---|---|---|
| **W4-a** | `python_sidecar.rs` — extract `tests_h3` (`:1490-1519`), **removes an allowlist entry entirely** | **S** |
| **W4-b** | `backend_ast.rs` plus `backend_ast_workflow.rs` — extract both, lower both pins | **M** |
| **W4-c** | `backend_cpu.rs` — brace-balance the five markers FIRST, then decide | **M** |
| **W4-d** | the three worst Python test files — split by theme, one file per PR | **L** |
| **W4-e** | `rust_core/tests/test_schema_compat.rs` (4,412) plus `test_routing.rs` (2,995) | **M** |

W4-b and W4-e are **CI-serialised behind W5** — they are Rust, and the shared-desktop ban makes every
one a round-trip. W4-a is small enough to ride as the first.

### W4.3 — TDD approach

For Rust: the RED is the **compiler**, and there is nothing to write. The discipline is instead the
**invariant**: the CI job must report the **same test count and the same per-test pass/fail** before and
after. A move that changes the test count is a move that lost tests — record both numbers in the PR
body. A green CI run with an unstated test count is not evidence.

For Python test splits (W4-d) the real hazard is not the split, it is the **duplicate**: pytest
collects by NAME, so a copy-paste split that leaves the original block in place shows the *same* test
count before and after while one copy silently shadows the other. **RED-first:** before splitting,
record `python -m pytest tests/unit/test_cli_modes.py --collect-only -q | tail -1`. After splitting,
the sum across the new files must equal it exactly, and an AST-identity check must show no test
function name defined twice across the resulting set.

### W4.4 — Acceptance tests

Per Rust slice (correctness in CI, size locally):

    python scripts/file_size_budget.py --report

Expected: `violations` decreases by the number of files the slice cleared (W4-a: 30 to 29),
`0 regressions`, and every remaining pin equal to its measured count. Rust correctness is the CI
`test-rust-core` job reporting the pre-move test count.

Per Python slice:

    python -m pytest tests/unit/test_cli_modes.py tests/unit/test_cli_modes_*.py --collect-only -q | tail -1
    python scripts/file_size_budget.py --report

Expected: collected count **identical** to the pre-split baseline recorded in the PR body; `0 regressions`.

**End-state acceptance for W4 as a whole:**

    python scripts/file_size_budget.py --report

Expected: `violations: 26` or fewer (30 minus at least `python_sidecar.rs` and the three Python test
giants), `0 regressions`, and no pin larger than its measured count.

**What W4 will NOT achieve, stated up front:** `main.rs`, `gpu_native.rs`, `backend_ast.rs`,
`backend_cpu.rs` and every Python test giant remain above their limits after extraction. W4 buys
**one** allowlist removal and a large pin reduction. Selling it as "clears the backlog" would be false.

---

## W5 — Rust follow-ups (PRIORITY 5, CI-serialised)

### W5.1 — Derived evidence

The `#[path]` mechanism is **measured** for library-crate modules (#1048, #1049 merged green). Two
cases remain structurally unproven, and the design doc says exactly why:

- **`gpu_native.rs` is compiled by only one CI job.** `#[cfg(feature = "cuda")] pub mod gpu_native;`
  (locate with `grep -n 'gpu_native' rust_core/src/lib.rs`) and `default = []` in `Cargo.toml`, so a
  default `cargo check` or `cargo test` **never touches the file**. Only `cuda-feature-check` in
  `.github/workflows/ci.yml` compiles it (locate with
  `grep -n 'cuda-feature-check' .github/workflows/ci.yml`). A change that breaks under
  `--features cuda` is invisible to `test-rust-core`.
- **`main.rs` is the binary crate root.** Zero `pub fn` outside `mod tests`, 238 private. Integration
  tests in `rust_core/tests/` link against the *library*, so `main.rs`'s tests **cannot** become
  integration tests — not "difficult", structurally impossible without moving the CLI into the library
  crate.

### W5.2 — Items

**W5-a — The `gpu_native.rs` CI experiment (effort M).**
Extract `#[cfg(test)] mod tests` (`gpu_native.rs:4443-4911`) to
`#[path = "gpu_native_tests.rs"] mod tests;`, leaving in place the 41 lines of production code that
follow it (`cuda_library_search_paths`, `push_cuda_bin_candidates`; locate with
`grep -n 'fn cuda_library_search_paths' rust_core/src/gpu_native.rs`).
**One file, one change, one round-trip.**

Acceptance in CI: the `cuda-feature-check` job green, AND `cargo test --features cuda --lib` reporting
the **same test count** as the pre-change run. Record both numbers.

Acceptance locally:

    python scripts/file_size_budget.py --report

Expected: the `rust_core/src/gpu_native.rs` pin drops 4,952 to about 4,484; still a violation;
`0 regressions`.

*The branch-on-mismatch fork, stated in advance:* if `cuda-feature-check` fails to compile, **stop and
re-derive** — do not assume the fix is obvious. The design doc names the one plausible mechanism (a
`Drop` impl for unsafe CUDA handle teardown separated from what it frees, `gpu_native.rs:681` and
`:719`) and flags it as the single place in the survey where a wrong split could compile clean and
still be wrong at runtime. A pure test-module move should not reach it; if it does, that is the finding.

*Security and soundness note:* this is unsafe-FFI-adjacent code. Test-module extraction only; **no
production-code split in this item.**

**W5-b — `main.rs` test-module extraction (effort M).**
`#[path = "main_tests.rs"] mod tests;` for `main.rs:2984-7473`. This is the one case the tractability
doc could not collapse to "same as the others" without compiling: **binary** crate root rather than
library. The helper `command_template` (`main.rs:4169`) is defined *inside* `mod tests` and called by
six sibling tests — it moves with the block, so it does not block this move, but it *would* block any
later attempt to split those tests across multiple files.

Acceptance in CI: `test-rust-core` green, same test count.
Acceptance locally: `python scripts/file_size_budget.py --report` shows the `main.rs` pin at about
10,637; still 7.1x over. *State plainly:* this does not clear the limit and is not claimed to.

**W5-c — `main.rs` architecture pass: DESIGN-ONLY, NEEDS ITS OWN COUNCIL (effort L — NOT in this plan).**
**FLAGGED FOR A SEPARATE COUNCIL. No implementation is authorised by this document.**
The residual roughly 10,637 lines are clap arg structs, the `Commands` dispatch and about 230 private
helpers with no existing internal module boundary. Splitting them means choosing submodules and
bumping visibility on the order of hundreds of call sites — every bump compiler-checked, but the
*grouping* is an architecture decision, not a line-count fix. It also collides with the four
command-registration sites `AGENTS.md` documents. **This plan's only deliverable for W5-c is the flag
itself**: it must not be handed to a build agent, and no slice in this campaign may touch `main.rs`
beyond W5-b's mechanical test-module move.

---

## W6 — Documentation: the junior-rebuildable bar (PRIORITY 6)

### W6.1 — Derived evidence

`docs/rebuild-guides/README.md` states the gap the directory exists to close and names the template:
*"`tg-checkpoint.md` — the worked template… **Future rebuild guides should follow this one's shape.**"*
`ls docs/rebuild-guides/` returns exactly four files (`README.md`, `tg-checkpoint.md`,
`verification-checklist.md`, `cache-and-schema-versioning.md`), i.e. **one** worked feature guide.

### W6.2 — W6-a: A second rebuild guide, subject `tg ledger` (effort M)

Selection is derived, not preferred. `tg ledger` matches every property the template's own selection
criteria imply — self-contained module, fully tested, stateful on-disk format:
`src/tensor_grep/cli/ledger_store.py` (1,417 lines, under its limit) plus four dedicated test files
(`ls tests/unit | grep -i ledger` returns `test_findings_ledger_is_repo_scoped.py`, `test_ledger_cli.py`,
`test_ledger_concurrency.py`, `test_ledger_store.py`). It is also the surface with a *known historical
trap worth documenting*: the PATH-footgun where `claim core/hooks` and `list .` resolved two different
stores until the nearest-`.git` canonical store landed (#706) — exactly the "trap a naive
reimplementation gets wrong, tied to a real guard and a real test" section the template requires.

Deliverable: `docs/rebuild-guides/tg-ledger.md`, following `tg-checkpoint.md`'s shape section for
section: problem solved; data flow; every file's contribution; on-disk format **verified by actually
running it against a throwaway scratch directory and reading the real artifact**; the traps, each tied
to a real guard and a real test; explicit out-of-scope.

Acceptance:

    python -m pytest tests/unit/test_public_docs_governance.py tests/unit/test_skill_library_drift.py -q
    python -m mkdocs build --strict

Expected: pytest passes; mkdocs `--strict` clean (a new doc under `docs/` must not break nav or leave a
dead link).

Plus the guide's own bidirectional check, which is what makes it a *guide* rather than prose: every
`file:line`-class claim in it must be **located by symbol plus grep**, and the verification tiers from
`docs/rebuild-guides/verification-checklist.md` (ran-and-observed / read-and-cited / unverified) must be
stated per claim. A guide that reports one undifferentiated "verified" fails its own checklist.

### W6.3 — W6-b: Update the directory README (effort S)

Add the `tg-ledger.md` entry. **Do not restate the guide count as a number** — this repo has been burned
by prose enumerations of its own contents repeatedly. Describe the entries; let `ls` be the count.

Acceptance:

    grep -c 'tg-ledger.md' docs/rebuild-guides/README.md
    grep -nE 'there are [0-9]+|[0-9]+ (rebuild )?guides' docs/rebuild-guides/README.md

Expected: `1` for the first; **zero hits** for the second (a non-zero exit from grep with no output is
the expected result, and that is the labelled meaning of this zero).

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
| **`main.rs` architecture pass** | W5-c — flagged, design-only, needs its own council. Named rather than omitted. |
| **Adopting Agent Retrieval Bench as a scored gate** | Verified real and usable (MIT; `pip install -e .`; `arb download-benchmark --version v2_edit2ripple`; HF dataset `eyuansu71/agent_retrieval_bench`; 58 `edit2ripple` and 82 abstention samples; arXiv:2607.24882, 2026-07-27). It is the first external harness that scores ranked-under-budget, ripple/blast-radius and **abstention with wrong-repository controls** — i.e. it would reward tg's fail-closed refusal instead of punishing it. Excluded anyway: the corpus is about 392K files and 7.9M chunks, a CPU-heavy download-and-index job forbidden on the shared desktop, and standing up a new scored gate mid-closeout invites hill-climbing against an uncalibrated signal. **Filed as a proposed DEMAND_GATED row, not built.** |
| **MCP 2.0 migration itself** | See W2.2. Upstream explicitly recommends the `<2` pin until migration; v1.x still receives security patches; `FastMCP` was removed in 2.0 so the migration is a rewrite; and there is no client demand. Deferred with three named triggers, not ignored. |
| **Lowering the limit from 1,500 to 1,000** | `docs/design/2026-08-19-split-floor-escape.md`, section 6, records that the brief and the audit template disagree, and that at 1,000 **eleven more files violate**. Changing the limit mid-campaign would rewrite every acceptance number in W4. |
| **Any release-bearing merge during the campaign's Rust rounds** | The publish window is about 6 minutes and a mid-window merge rejects the in-flight push. W2-b is the only `fix:` in this plan and gets its own window. |

---

## 4. The pipeline this plan follows

1. **Thinktank adversarial audit of THIS document**, then fix in place on
   `docs/worldclass-closeout-plan`, then re-audit, repeating until APPROVE. The plan is revised in
   place; no PR is opened before APPROVE. A no-verdict seat is a **failed seat**, not a blocker and
   not an approval (A10).
2. **Implementation via subagents**, one writer per file set, model-tiered explicitly per slice.
   `cursor-agent` may take **mechanical writes only** — and per the gate-evasion rule **cursor never
   touches gates, verifiers, registries, or allowlists**: `scripts/file_size_allowlist.json`,
   `scripts/bare_call_pins.json`, `scripts/file_size_budget.py`, `scripts/bare_call_ratchet.py`,
   `tests/unit/test_silent_failure_hardening.py` and every governance test are off-limits to it.
   Those edits are made by a Claude seat that also runs the perturbation arm.
3. **Codex audit against this plan** until `RECOMMENDED: APPROVE`, per slice, capped at 5 rounds with
   a new defect class each round or a declared clear.
4. **Merge** — one per publish window; union-merge check before queueing any concurrent PR.
5. **Lint and format** — `ruff format --preview` and `ruff check` are both CI gates; a local
   `ruff check` pass is not green.
6. **Dogfood the published artifact** for anything W2-b ships — `uvx --from tensor-grep==<new> tg …`
   in a clean env, both arms (a real symbol resolves; a fabricated one returns `no_match`).
7. **Findings appended to `docs/BACKLOG.md`** at closeout by the orchestrator, with the ARB row filed
   as proposed DEMAND_GATED and the W5-c council flag carried forward.

---

## 5. The three highest-risk items, named

1. **W1-a (MCP-surface handler audit).** Twenty-five not-provably-disclosing handlers on a
   network-reachable tool surface, where the wrong classification ships a swallowed exception as an
   empty successful tool result. The risk is not the fix — it is the **classification**, which is
   judgement, unbacked by any gate, on the largest slice.
2. **W5-a (`gpu_native.rs`).** The only file in the campaign compiled by a single narrow CI job, and
   the only place where a wrong move could compile clean and be wrong at runtime (unsafe CUDA `Drop`).
   A green default `test-rust-core` says nothing about it.
3. **W3-a (beyond Route A).** Highest risk of *wasted work*: the honest answer may be "accept the pins",
   and a plan that cannot say that out loud will instead produce a 787-edit Route B whose failure mode
   is the silent false green this whole campaign exists to prevent.

---

## 6. What could not be derived

- **The runtime cost of the `_self.` indirection already shipped.** The design doc requires a benchmark
  per conversion; the shared-desktop ban forbids running one here. Whether `_read_source_text_cached`
  (14 in-module calls, genuinely hot) regressed is **unmeasured**, not "fine". If the council wants it,
  it is a CI benchmark job, not a desktop run.
- **Any Rust compile fact.** Every Rust statement here is either a measured line count, a quoted CI
  result from a merged PR, or an explicitly-labelled hypothesis. None was verified by compiling.
- **Whether the 54 not-provably-disclosing handlers contain any real SILENT-SWALLOW.** The detector
  proves only that its AST shapes did not match. The census's own docstring records that manual review
  of the in-census population found **zero** survivors — so the honest prior is that most of the 54 are
  false positives. W1 is scoped as *classification*, and if it finds zero defects that is a successful
  wave, not a failed one.
