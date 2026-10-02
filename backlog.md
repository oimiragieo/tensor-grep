# backlog.md — full dependency-mapped task list
# Generated 2026-09-03 11:20 ET from docs/TASK_BOARD.md canonical status index + orchestrator state + Zvec Parity Wave

## Legend
```
Status: SHIPPED ✓ | READY_TO_SHIP 🚀 | IN_PROGRESS 🔧 | BLOCKED ⛔ | CEO_GATED 🔑 | DEMAND_GATED 📊 | RETIRED ✗
Deps: → "required before this can start"
Opens: → "unlocked once this completes"
```

---

## READY_TO_SHIP 🚀

### WAVE-2-DESIGN-PACKET (IMPLEMENTED 2026-10-01; independent audit + one deferred item open)
- **Status:** IMPLEMENTED, NOT YET INDEPENDENTLY AUDITED. Receipts below; no Codex re-audit has run on these SHAs.
- **Done (SHA + verify command):**
  - Mutation test, static + dynamic: `1a9de6a` (also teaches `core/import_edges.py` to report string-literal `import_module()`/`__import__()`). Verify: `pytest tests/unit/test_import_edges_baseline.py`; red-arm seen: with the pre-`1a9de6a` walker the test fails "Mutation control (dynamic) ... not detected".
  - Handler census denominator (106 excluded, 9+27+106=142): `533c178`. Verify: read `docs/audits/handler-census-gaps-2026-09-30.md` scan-coverage section.
  - AWS positive control with a detectable non-EXAMPLE key (`AKIAIOSFODNN7ZXCVBNM`, 20 chars): `648843e`. Verify: `GITLEAKS_BIN=<gitleaks v8.30.1> pytest tests/unit/test_gitleaks_positive_control_validation.py` (1 passed); WITHOUT a v8 binary it SKIPS, which is not a pass.
  - Red-CI fix (this closeout): removed the deliberately-red `tests/unit/test_gitleaks_scan_completeness.py` committed in `00695cd`; it failed every ci.yml test lane (`FileNotFoundError: gitleaks`) and ruff since that push. Verify after push: `gh run list --workflow ci.yml --event push --limit 3` (last 3 push runs before the fix: all `failure`, run 36819097882 at `03b8153`).
- **Open:**
  1. **Gitleaks re-scan WITHOUT `--no-merges`** -- DEFERRED by operator decision 2026-10-01 ("no need for gitleaks right now"). Still a real coverage gap: credentials introduced in merge-commit resolutions are not scanned. Owner: next session if the operator re-opens it.
  2. **CI has no gitleaks binary** (no `.github/` reference to gitleaks) so the positive-control test skips in CI and guards nothing there. Fix = install v8.30.1 with a pinned checksum in a CI lane, or accept it as a local-only audit proof and say so.
  3. **Independent audit of `1a9de6a`/`533c178`/`648843e` -- Codex Sol R1 = REVISE (critical=1 high=1 medium=2), source-only (artifacts inlined; attempt 1 failed: seat's sandbox rejected every command launch, no verdict).** Dispositions, each probed by me, not taken on the seat's word:
     - HIGH `builtins.__import__("tensor_grep.cli.x")` escapes the walker -- CONFIRMED by probe, FIXED (walker now matches attr `__import__`; parametrized `test_dynamic_import_literal_forms`, red-then-green). Same pass fixed a docstring-vs-code mismatch I introduced (relative literals were returned despite the docstring). STILL UNDETECTED, pinned as documented gaps by test rows: `getattr(importlib, "import_module")(...)` and non-literal `import_module(var)`; deciding whether to report non-literals as UNRESOLVED is open (idea 5).
     - MEDIUM any-receiver `.import_module(` false edge (`registry.import_module(...)`) -- CONFIRMED, ACCEPTED deliberately (comment in the code), not a defect.
     - MEDIUM census 142/106 unverifiable by the seat -- total 142 re-counted by me (`find src/tensor_grep -name '*.py'` = 142 incl. `__init__.py`); disjointness of the 36 scanned and 19 excluded sets still NOT verified (idea 4).
     - CRITICAL positive control skips in CI -- FACT CONFIRMED (no gitleaks in CI); severity is the seat's call: it is a local audit proof, not a CI gate. Still OPEN with open item 2; cheapest fail-closed step is a `TG_REQUIRE_GITLEAKS=1` opt-in that fails instead of skips. NOT done: operator deferred gitleaks work 2026-10-01.
     - **R2 (on `c1bea5a`, source-only, inlined): REVISE, critical=0 high=3.** All three probed and CONFIRMED, all FIXED in the follow-up commit: (a) static `from tensor_grep import cli` / `from .. import cli` produced NO edge (walker only read `node.module`) -- the repo's primary import style; now expands names when the module is the package root, control test proves `from tensor_grep import __version__` invents no edge; (b) `import_module(name="...")` and `import_module(".x", package="...")` were resolvable but dropped -- now resolved via `importlib.util.resolve_name`; (c) opaque import calls (non-literal, `getattr(importlib,"import_module")(...)`) passed as "no edge" -- new `compute_unresolved_dynamic_import_modules` surfaces them and `unresolved_dynamic_import_modules` in the baseline JSON freezes the 3 existing ones (backends/__init__, core/__init__, cli/_main_binding), each reviewed: the `_EXPORTS` targets are 16/16 inside backends and 9/9 inside core, `_main_binding` imports the literal `tensor_grep.cli.main`. R2 also called the any-receiver match "defensible" and could not substantiate the R1 CRITICAL.
     - STILL undetected: `imp = importlib.import_module; imp("...")` (needs dataflow; the assignment itself is not an unresolved CALL, so it is not surfaced either).
     - **R3 (on `ed0150e`, source-only, inlined): REVISE, high=6, a NEW class each.** All six probed on temp trees; all CONFIRMED and FIXED: (1) relative imports from a package `__init__.py` were mis-resolved one level too high (`from .. import cli` / `from ..cli import x` in `core/__init__.py` gave no edge; the oldest bug, from Task 07) -- `_resolve_relative_import(..., is_package=)`; (2) `from tensor_grep.cli import runtime_paths` recorded only the parent, so a child could hide behind a frozen parent edge -- the child is now recorded when it is a real module on disk (non-module names invent no edge; no live escape existed because the baseline has no bare-package edge); (3) root star import was invisible -- surfaced as an unresolved site; (4) `__import__(..., level=N)` relative form treated as absolute -- now unresolved; (5) freezing unresolved modules BY NAME let a second opaque call in a frozen module pass -- now frozen as `{module: count}` (`unresolved_import_sites`; replaces the R2 `unresolved_dynamic_import_modules` list and `compute_unresolved_dynamic_import_modules`, now `compute_unresolved_import_sites`); (6) `runpy.run_module` bypassed both checks -- now a recognised callee. The real tree is UNCHANGED by all of this (no hidden edge was masked; same 3 unresolved modules at count 1 each).
     - Explicitly NOT covered, by design: path-based loaders (`spec_from_file_location`), `exec`/`eval`, `ctypes`, and alias assignment (`imp = importlib.import_module; imp("...")`).
     - **R4 (on `473d025`, source-only, inlined): REVISE, high=3.** Probed on temp trees and against the real tree. (1) An existing opaque call can change TARGET without changing the frozen count (e.g. a `_EXPORTS` entry retargeted `core.x` -> `cli.x`) -- CONFIRMED in principle, FIXED by pinning the reviewed targets: `test_reviewed_opaque_import_targets_stay_inside_their_layer` (backends 16/16 in-layer, core 9/9 in-layer) and `test_main_binding_late_import_targets_the_reviewed_cli_main_module`; mutation control seen (retargeting a real `core/__init__.py` entry to `tensor_grep.cli.main` turned it RED, restored with `git checkout`). (2) Star import from ANOTHER layer package can pull children via `__all__` -- CONFIRMED, FIXED (surfaced as an unresolved site; own-package star is not). (3) Importable namespace packages and compiled `.pyd`/`.so` children hid behind the parent edge -- CONFIRMED, FIXED (`_is_submodule`). Real tree UNCHANGED (zero star imports in `src`; the only extension modules are root-level `rust_core.*`, not inside a layer). R4 itself said findings 2 and 3 need a pre-existing frozen bare-package edge; the baseline has none, so neither was a live escape.
     - **R5 = THE CAP (on `945cc7d`, source-only, inlined; brief demanded live-vs-precondition-gated and a snippet per finding): REVISE, high=2, BOTH live (no precondition).** Both reproduced on temp trees. (1) `pkgutil.resolve_name("tensor_grep.cli")` imports a module and the walker did not see it -- FIXED: recognised when bound from pkgutil (`import pkgutil [as x]` / `from pkgutil import resolve_name [as y]`), `pkg.mod:attr` form handled; CONTROL: `importlib.util.resolve_name` (the walker's own call; resolves a relative name, imports nothing) is NOT matched. (2) An existing opaque call could be re-targeted while the frozen COUNT stayed 1 (reproduced: `import_module(_E[name])` edited to import `tensor_grep.cli.runtime_paths` for one name, every baseline green) -- FIXED: sites are frozen by their `ast.unparse` SOURCE TEXT (`unresolved_import_sites` is now `{module: [text]}`; `ast.unparse` not `ast.dump`, whose output changed in Python 3.13 and would differ across the CI lanes). Mutation control seen: editing the real `core/__init__.py` call target (leaving `_EXPORTS` untouched) turned BOTH baseline guards RED naming the exact site; restored with `git checkout`. 50 tests pass; real tree unchanged.
     - **Process ruling at the cap:** five rounds, five NEW classes each, no AUDIT_CLEAR ever reached. ONE scoped re-review of the R5 fix diff was then run (fix-wave rule, not a sixth full round): **F1 ADDRESSED, F2 ADDRESSED**, and it raised ONE issue introduced by that diff (HIGH-labelled, "REVISE"): the `pkgutil` alias scope is file-wide, so an unrelated rebinding of the same alias name elsewhere in a file can read as an import (e.g. `def a(): import pkgutil as p` + `def b(): p = registry; p.resolve_name("tensor_grep.cli")` -> a false `core -> cli` edge). RULING: deliberate, not a defect -- it is a false POSITIVE that fails LOUD for a human to review, the safe direction for a ratchet, and the same trade-off already accepted for the receiver-agnostic match; lexical-scope tracking risks the opposite error of hiding a real import. Documented in `_pkgutil_scope` and pinned by `test_pkgutil_alias_scope_is_file_wide_by_design` so changing it is a conscious decision. The seat also could not verify `from pkgutil import *` (CANNOT_VERIFY): that is a real, cheap gap, so FIXED (`resolve_name` is bound by the star) with `test_star_import_from_pkgutil_binds_resolve_name`; red arm seen (the committed walker misses the star-bound call, the fixed one catches it). These last two changes have NOT had a further seat round: the review loop is capped. WAVE 3 STATUS: no AUDIT_CLEAR was ever returned in 5 rounds + 1 scoped re-review; every finding it raised that was a live escape is fixed and test-pinned, the rest are parked below with rulings. Wave 3 is therefore UNBLOCKED BY JUDGMENT, not by an independent clearance -- an explicit operator call, not an audit result. Residual, PARKED (not live escapes in this tree, recorded in the walker docstring): alias assignment (`imp = importlib.import_module; imp("x")`), path-based loaders (`spec_from_file_location`), `exec`/`eval`, `ctypes`, and `importlib.util.find_spec`/`module_from_spec`. The structural lesson: a name-matching AST walker can only ever be an incomplete denylist; the durable control is the frozen layer baselines plus the pinned source-text of every opaque site, and a real import-linter adoption (P13) would replace this walker.
  4. **Stale plan text**: `docs/superpowers/plans/2026-10-01-wave-2-design-packet.md` still lists the 34-char key that can never match; superseded by `648843e` (append-only note added).
- **Wave 3 (HANDLER-CENSUS-W3) unblock:** CONDITIONAL on open item 3; the census reconciliation it consumes is done but unaudited.
- **Local gitleaks v8 for the positive-control test:** `C:\tmp\tensor-grep\KEEP-gitleaks-8.30.1-verified\gitleaks.exe` (sha256 of the zip matches `gitleaks_8.30.1_checksums.txt`, re-verified 2026-10-01). Run: `GITLEAKS_BIN=C:\tmp\tensor-grep\KEEP-gitleaks-8.30.1-verified\gitleaks.exe pytest tests/unit/test_gitleaks_positive_control_validation.py`.
- **Closeout 2026-10-01 receipts:** pushed `f5f55ee` (drop red-by-design test) and `7335bd9` (TASK_BOARD reconcile to v1.123.1, size gate 79,863/80,000); closed superseded PRs #1182 (tip `abc58469`) and #1183 (tip `59649793`) -- merging either would have reverted Wave 2; `abc5846` (stray `.test_credential.py`, fake creds at repo root) intentionally NOT merged.
- **Open, not mine to change:** PR #1184 (Dependabot, PyJWT 2.13.0->2.15.0, uv group) -- repo law: a fixable advisory bump must be applied across direct floors, lock and validators, then re-audited; not auto-merged.
- **TASK_BOARD AGT-07 / AGT-01 -- CHECKED 2026-10-01 (was UNVERIFIED):** AGT-01 is accurate and unchanged: its cited `dd3c594`, `bb635941` (#1159), `cc833b06` (#1160) all exist with the stated subjects, and nothing touching its files landed after v1.121.2. AGT-07's READY status is right but its wording was STALE: it still listed "migrate one producer/consumer" as remaining, yet that slice landed 2026-09-09 as `756769f` (#1137: `mcp_server.py` builds the typed `CompletenessEvidence` and projects it back, covered by `test_completeness_projection.py` + `test_mcp_incomplete_envelope.py`); #1167 (`cff35c7`) added `completeness_output.py` and does NOT touch `CompletenessEvidence`. The real remainder per the plan's Task 09 is the SIBLING CLI consumer (`cli/main.py` has no `CompletenessEvidence` use) after the old/new equivalence run; the plan's mutation-control checkbox was not verified either way. Row reworded (size-neutral, board 79,881/80,000).
- **CI verdict is NOT recorded here** (it would be stale the moment it is written). Read it for the SHA you care about: `gh api "repos/oimiragieo/tensor-grep/actions/runs?head_sha=<sha>"` -- not `gh run list`, which has intermittently returned another repo's runs. Every push to main runs the FULL suite (`changes` job forces `code=true` on `main-push`), ~13 min; the pre-fix failures were exactly 1 test per lane (gitleaks demo, then the board stamp).

## NEXT-SESSION IMPROVEMENTS (ideas, not started)

1. **Pre-push CI-red guard for committed tests:** a repo check that fails when a test under `tests/unit/` has no `skip`/`importorskip` guard yet shells out to a non-vendored binary (`gitleaks`, `curl`). The red-by-design test reddened main for 4 pushes. Seat: Sonnet builder + Opus gate; Pool: Claude. Dep: none.
2. **Post-push CI verdict as a gate in closeout:** `scripts/` helper printing the push-run conclusion + failing test NAMES for HEAD, so "done" cannot be written over a red run. Seat: Sonnet. Dep: none.
3. **Gitleaks lane in CI (pinned v8.30.1 + checksum) running the positive control and a no-`--no-merges` history scan**, closing open items 1 and 2 together. Needs operator go (Actions minutes). Seat: Sonnet + Codex Sol audit. Dep: operator decision on the deferred merge-commit scan.
4. **Census-by-enumeration for the handler gap:** replace the "87 unaudited others" subtraction with an enumerated, AST-derived module list checked into `docs/audits/`. Seat: Sonnet. Dep: Wave 3.
5. **Walker hardening follow-ups for `import_edges.py`:** `builtins.__import__`, `importlib.util.spec_from_file_location`, and non-literal `import_module(var)` are undetected; decide whether to report non-literal calls as UNRESOLVED instead of silently skipping. Seat: Sonnet + adversarial verify. Dep: audit of `1a9de6a`.

---

## COMPLETED ✓ (Recent)

### DEEP-AUDIT-VERIFY (Wave 1 verified-gap closeout)
- **Status:** AUDIT COMPLETE WITH KNOWN LIMITATIONS (2026-10-01)
- **Verification:**
  - ✅ Sonnet intent: VERIFIED (all 3 MAP.md checks passed)
  - ✅ Droid adversarial: AUDIT_CLEAR (R1→R3, all gaps resolved)
  - ⏳ Codex Sol security: REVISE (2 CRITICAL scope limitations, 2 MEDIUM gaps)
- **Disposition:** Three core work items (G1/G2/G3) delivered and audited. Codex identified audit scope limitations (gitleaks --no-merges, positive-control weakness, mutation test scope, census denominator) — all addressable in Wave 2 design packet.
- **Receipt:** `.build/deep-audit-verify/RECEIPTS.md` (transient, full audit trail captured)
- **Components (only what is not already shipped):**
  1. **P13 mutation-control fix** -- OWNED BY P13 (`docs/BACKLOG.md` P13), listed here as a pointer only: `tests/unit/test_import_edges_baseline.py::test_a_new_backward_import_would_be_caught` does set arithmetic and never runs the walker; replace with a planted-file run of `compute_violation_module_edges`, and record the 6-edge classification under P13.
  2. **One-time full-history secret scan** -- gitleaks, redacted, with a planted positive control and a commit-count coverage check. No workflow runs one today.
  3. **Broad-handler census gap list** -- RECEIVED 2026-09-30 (Codex Luna + verification seat): 27 unaudited modules with ~100 broad handlers; 9 audited backend modules with 46 handlers pending Wave 2 classification; 0 tuple handlers with broad parent exceptions; ledger completeness: 248 records across 21/28 audited modules. Report: `docs/audits/handler-census-gaps-2026-09-30.md`. (PR in flight: T3 of Wave 1 execution.)
- **Closed as already shipped (not work):** import-linter-gate -- the module-granularity violation ratchet shipped in P13 (`552dea5`); dependency advisories -- `.github/workflows/audit.yml` runs pip-audit, cargo audit, cargo deny; import-graph truth -- the 6 frozen `->cli` edges are one-way imports into leaf modules (5 module-level, 1 function-local, 0 TYPE_CHECKING-only), no module-level cycle.
- **Dropped:** duplication/dead-code verification (error payloads and path ops are security-sensitive; simplification is out of scope).
- **Depends:** None
- **Opens:** HANDLER-CENSUS-W3 (dispositions for the census gap list); a secret-remediation incident only if the scan finds a live credential.
- **Effort:** Small (3 independent PRs, ~1 week; no release)
- **Why:** The audit's zero-result sweeps were false-green candidates. Only these three gaps survived verification; everything else was already shipped or out of scope.

---

## IN_PROGRESS 🔧

*(no unblocked slices in flight; ARCH-002 backend census closed; remaining items blocked on WSL hardware or A12 shared-box cargo ban)*

---

## SHIPPED ✓ (Recent)

### HANDLER-CENSUS-W2-b — GPU backend handler census & error hardening (cudf, torch, cybert)
- **Status:** SHIPPED (Verified on 5816afe by Sonnet 5 + Codex Sol dual GO)
- **Components:** In-slice hardening: narrowed `deobfuscate_payload` in `cybert_backend.py` from broad `except Exception:` to `(ValueError, binascii.Error)` (ceiling ratcheted 267 -> 266 per Rule A137); added logging disclosures to RMM fallbacks and CuPy capability probe in `cudf_backend.py` and traced inference retry in `cybert_backend.py`. Appended 21 records to `docs/audits/2026-08-20-handler-dispositions.json` (176 total: 11 LOGGED-DEGRADE, 10 INTENTIONAL-BOUNDARY, 0 SILENT-SWALLOW). Enrolled `backends/cudf_backend.py`, `backends/torch_backend.py`, `backends/cybert_backend.py` in `_EXPLICIT_AUDITED_MODULES`.
- **Verification:** 11/11 disposition tests pass, 2/2 silent failure hardening tests pass, ruff/mypy clean. Closes ARCH-002 across all 9 backend modules and all 46 backend broad handlers.

### HANDLER-CENSUS-W2-c — AST, Rust, and StringZilla backend handler dispositions
- **Status:** SHIPPED (Verified on 1aee5a4 by Sonnet 5 + Codex Sol dual GO)
- **Components:** 8 handlers across `backends/ast_backend.py` (2), `backends/ast_wrapper_backend.py` (3), `backends/rust_backend.py` (2), `backends/stringzilla_backend.py` (1) dispositioned in `docs/audits/2026-08-20-handler-dispositions.json` (155 total); `_EXPLICIT_AUDITED_MODULES` extended in `tests/unit/test_handler_dispositions.py`.
- **Verification:** 11/11 tests pass in `test_handler_dispositions.py`, 2/2 tests pass in `test_silent_failure_hardening.py`, ruff/mypy clean.

### HANDLER-CENSUS-W2-a — cpu_backend + ripgrep handler hardening
- **Status:** SHIPPED (Released in v1.114.1)
- **Components:** 17 handlers dispositioned; decode/search exception separation in `cpu_backend.py`.
- **Verification:** 11/11 tests pass in `test_handler_dispositions.py`, ruff/mypy clean.

### ZVEC-PARITY-AGENT-ENHANCE — AST Container Enrichment & Multi-Agent MCP Installer
- **Status:** SHIPPED (Released in v1.114.0)
- **Components:** `--enrich-ast` container enrichment + multi-agent installer (`tg install`/`tg uninstall` for claude, cursor, codex, opencode, qwen).
- **Verification:** 22/22 unit tests pass, 68/68 routing tests pass, ruff/mypy clean.


---

## BLOCKED ⛔

### #89 — WSL path-domain `path_not_found`
- **Status:** BLOCKED
- **AI-Doable:** NO (Environment-blocked)
- **Blocker Receipt:** Real WSL host required (`path_not_found` on existing `/mnt/c`); PR #966 closed as wrong scope on 2026-08-20. Needs a dedicated WSL CI runner or hardware testbed.
- **Deps:** Real WSL host environment
- **Opens:** #90, F8 partial

### #90 — WSL raw-path scan `matched_rules=0`
- **Status:** BLOCKED
- **AI-Doable:** NO (Environment-blocked)
- **Blocker Receipt:** WSL raw-path scan reports `matched_rules=0` while translated-path control reports `total_matches=6`. Doctor half shipped in PR #571; scan half waits on typed-path + real WSL CI runner.
- **Deps:** #89 (shares real WSL host environment)
- **Opens:** nothing current

### F5 — Edit-ready Steps 3–5 (Rust/e2e)
- **Status:** BLOCKED
- **AI-Doable:** NO (Environment/infra-blocked)
- **Blocker Receipt:** Touches `rust_core/**` + `tests/e2e/**`; shared-box cargo/e2e ban (Operating Rule A12) prohibits cold full-suite local native cargo builds to prevent desktop resource starvation. Step 2 shipped in PR #943.
- **Deps:** CI/cloud runner unblock (CEO or dedicated cloud builder)
- **Opens:** F6 native half, F8 workspace Rust

### F6 — Edit verification (mixed disposition)
- **Status:** BLOCKED (Partial — Python slices buildable)
- **AI-Doable:** PARTIAL (Python half AI-doable; native half blocked)
- **Blocker (native half):** Rust/e2e shared-box ban.
- **Buildable today:** Python/schema/evidence-signing S1 slices → need scoped plan + A3 gate.
- **Deps (Python half):** Task 2C for MCP ordering.
- **Deps (native half):** F5 / CI unblock.
- **Opens:** MCP-SURFACE (Task 4 sequenced after 2C which is in F6).

### F8 — Workspace program (Tasks 12–13)
- **Status:** BLOCKED
- **AI-Doable:** PARTIAL
- **Blocker Receipt:** `path_domain.rs` absent on origin/main; requires an architectural design naming workspace APIs before Rust implementation can begin.
- **Deps:** Design pass; F5 (Rust cargo ban on shared box).
- **Opens:** future workspace search slices.

### MCP-SURFACE — Task 4 MCP surface disclosure
- **Status:** BLOCKED
- **AI-Doable:** YES (once sequence unblocks)
- **Blocker Receipt:** Strictly sequenced after Task 2C (F6 Python chain). Live `_TG_MCP_SERVER_CONTRACT_VERSION = "1.7.0"` at `src/tensor_grep/cli/mcp_server.py:188`.
- **Deps:** F6 Python slices (Task 2C completes).
- **Opens:** MCP-LEAN-DEFAULT (DEMAND_GATED).

---

## CEO_GATED 🔑

### #72 — New public benchmark claim
- **Status:** CEO_GATED
- **AI-Doable:** NO (Policy-gated)
- **Blocker Receipt:** CEO approval required for any new public speed or throughput claim.
- **Deps:** CEO explicit approval
- **Opens:** public benchmark docs & marketing materials

### #77 — Ledger enforcement scope
- **Status:** CEO_GATED
- **AI-Doable:** NO (Policy-gated)
- **Blocker Receipt:** CEO policy decision required on F9 / ledger scope.
- **Deps:** CEO explicit directive
- **Opens:** F9 ledger enforcement

### #131 — GPU-flavor native asset publish
- **Status:** CEO_GATED
- **AI-Doable:** NO (Financial/Policy-gated)
- **Blocker Receipt:** CEO approval required to build and publish separate GPU-flavored binary wheels; gated on #169.
- **Deps:** #169 (parent financial spend gate)
- **Opens:** GPU benchmark claims (#72 partial)

### #169 — Physical GPU proof / spend
- **Status:** CEO_GATED — **Only mandatory financial stop**
- **AI-Doable:** NO (Spend-gated)
- **Blocker Receipt:** Requires CEO approval for physical hardware or cloud GPU spend (RunPod, Lambda, etc.).
- **Deps:** CEO spend authorization ($)
- **Opens:** #131, Phase 2 GPU CI runner

---

## DEMAND_GATED 📊

### #255 — Many-pattern dedup parity experiment
- **Status:** DEMAND_GATED
- **AI-Doable:** YES (when demand occurs)
- **Trigger Condition:** Named 100+-pattern user or approved compression/native investment. Standing council verdict: LEAVE (2026-08-14).
- **Deps:** Customer demand receipt
- **Opens:** nothing on current board

### DD-006 — Concurrent daemon DoS hardening (PERF + HONESTY build)
- **Status:** DEMAND_GATED
- **AI-Doable:** YES
- **Trigger Condition:** Demand condition SATISFIED (2026-08-14 bounded probe); design PR #1015 merged (`0710219`). Awaiting CEO "build go" authorization.
- **Deps:** CEO authorization → TDD + A3 adversarial gate
- **Opens:** MCP-LEAN-DEFAULT (sequencing)

### AST-DSL-PARITY — Full structural DSL parity
- **Status:** DEMAND_GATED
- **AI-Doable:** YES (when demand occurs)
- **Trigger Condition:** Concrete consumer blocked on ast-grep metavariable parity. Council verdict: LEAVE (2026-08-14).
- **Deps:** Consumer requirement
- **Opens:** advanced structural query support

### MCP-LEAN-DEFAULT — Lean MCP surface by default
- **Status:** DEMAND_GATED
- **AI-Doable:** YES (when sequenced)
- **Trigger Condition:** Client demand and compatibility evidence for changing default MCP surface (up to 85% token savings).
- **Deps:** MCP-SURFACE (Task 4)
- **Opens:** token-efficiency gains across LLM harnesses

### CONTINUOUS-REFRESH — Warm search-index daemon
- **Status:** DEMAND_GATED
- **AI-Doable:** YES (scoping pass)
- **Trigger Condition:** Approved scoping/design pass for warm search-index service.
- **Deps:** Scoping authorization
- **Opens:** sub-millisecond warm search latency

### CALL-CHAIN — Transitive incoming callers (`tg callers --transitive`)
- **Status:** DEMAND_GATED
- **AI-Doable:** YES (after authorization)
- **Trigger Condition:** Bounded demand probe SATISFIED or a CEO demand waiver (A93: the audit's competitor comparison is not demand), then design packet + Sol exact-commit APPROVE, then a deliberate build go (A117/A122 ladder). The only real product gap left by `docs/audits/2026-09-30-premise-check.md` (Findings 2 and 5).
- **Spec:** `.build/DEEP-AUDIT-PLAN.md` section 3 (plan v2): bounded depth/fan-out, cycle-aware, per-edge `match_basis` honesty, ambiguous names not expanded, one shared deadline, exit 2 on cut-short; the Rust door is an unchanged passthrough; MCP contract 1.8.0 -> 1.9.0.
- **Deps:** None code-wise; P14 vocabulary alignment for edge provenance.
- **Opens:** a possible consumer for docs/BACKLOG F5 (2026-09-10) centrality ranking (not root F5).

### RUST-REPLACE-TOCTOU — Residual TOCTOU races in replace_in_place
- **Status:** DEMAND_GATED
- **AI-Doable:** YES
- **Trigger Condition:** Characterization pin in `backend_cpu.rs` inverting.
- **Deps:** RUST-REPLACE-SYMLINK ✓ (shipped in PR #1010)
- **Opens:** full replace_in_place safety across directory swap windows

---

## SHIPPED ✓

### #36 — Skill drift audit
- **Shipped:** PR #903
- **Opens:** Maintenance baseline

### #37 — Grammar-dependent Windows test
- **Shipped:** PR #908
- **Opens:** CI reliability on Windows

### #109 — CUDA implicit-walk ceiling
- **Shipped:** PR #605
- **Opens:** GPU search stability

### #859 — Task 3 AST writer census + publication fix
- **Shipped:** PR #913, #918, #920 (`211d850c`)
- **Opens:** Class-level AST writer ratchets

### F7 — Language registry + cross-file resolution (Tasks 10–11)
- **Shipped:** PR #950, #952, #955, #957, #963 (`9f854d49`)
- **Opens:** Multi-language symbol navigation (Java, C#, PHP, C, C++)

### CPU-BACKEND — Task 5 Rust/Python backend hardening
- **Shipped:** PR #923, #925, #963 (`f29c9484`)
- **Opens:** Correct fail-closed semantics in CPU fallback

### REF-CALL-REGISTRY — Task 9 registry-driven refs/callers
- **Shipped:** PR #915, #940, #963 (`3dbe85b1`)
- **Opens:** Semantic code navigation across references

### RUST-REPLACE-SYMLINK — Fail-closed symlink/junction guard
- **Shipped:** PR #1010 (`d31a051f`, v1.110.16)
- **Opens:** RUST-REPLACE-TOCTOU

---

## RETIRED ✗

### #22 — Exit code semantics
- **Retired:** exit 0/1/2 contract permanently locked; `gpu_request_unhonoured` is in-band.

### F2 — Anonymous-agent compatibility sentinel
- **Retired:** Deliberately retained for backward compatibility.

### #48 — Native front-door rewrite
- **Retired:** Closed "not planned" 2026-08-24 applying standing 5/5 council verdict.

### F10 — MaxSim / late rerank
- **Retired:** Negative on golden set (ndcg@10 0.068 vs 0.305 RRF); model capacity limitation.

### DD-004 — Typed BackendExecutionError boundary
- **Retired:** INFO/WEAKENED loud `RuntimeError` re-raise at `cpu_backend.py:811` is fail-closed.

---

## Full Dependency Graph (Text & Topological Map)

```
========================================================================================
                                DEPENDENCY MAP
========================================================================================

[READY TO SHIP]
  ZVEC-PARITY-AGENT-ENHANCE (Verified Green 22/22, Sol SHIP) ──► Production v1.114.0

[IN PROGRESS]
  PR #1124 (Merged) ──► HANDLER-CENSUS-W2-a ──► Sol AUDIT_CLEAR ──► HANDLER-CENSUS-W2-b

[CEO GATES & FINANCIAL SPEND]
  #169 ($ Financial Stop) ────┬──► #131 (GPU Native Assets) ──► #72 (GPU Benchmark Claims)
                              └──► Phase 2 GPU CI Cloud Runner

[ENVIRONMENT BLOCKS]
  Real WSL Host ─────────────► #89 (WSL Path Domain) ──► #90 (WSL Raw-Path Scan)
  CI/Cloud Runner (Non-local) ► F5 (Rust/e2e Steps 3-5)
                                     │
                                     ├──► F6 Native Half
                                     └──► F8 Workspace Rust

[MCP & EDIT SEQUENCING]
  F6 Python Slices (Task 2C) ──► MCP-SURFACE (Task 4) ──► MCP-LEAN-DEFAULT (Demand)

[DEMAND TRIGGERS]
  CEO Build Authorization ────► DD-006 (Concurrent Daemon DoS Hardening)
  RUST-REPLACE-SYMLINK (✓) ───► RUST-REPLACE-TOCTOU (Pin Inversion Acceptance)
  Demand probe | CEO waiver ──► CALL-CHAIN design packet ──► Sol APPROVE ──► build go ──► S1 ──► S2
========================================================================================
```

*All rows reconciled mechanically from `docs/TASK_BOARD.md` + live git working tree.*
