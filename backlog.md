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
- **Wave 3 (HANDLER-CENSUS-W3) status (updated 2026-10-02):** UNBLOCKED BY JUDGMENT, not by an independent clearance -- item 3's audit ran to its cap with no AUDIT_CLEAR; every live escape is fixed and test-pinned, the rest parked with rulings. The 142-module total was re-counted by me; the "9 + 27 + 106" SPLIT is superseded by the measured census below (the "27 unaudited" was really 26, and the 36/19 disjointness stays unverified). STARTED 2026-10-02 (slice 1 below): Wave 3 is a campaign to give every broad handler a disposition record. The census doc's "27 modules / ~100 handlers" was WRONG -- measured with the project's own scanner it is **95 handlers in 26 modules** at the start (343 live, 248 recorded, 0 stale); erratum appended to `docs/audits/handler-census-gaps-2026-09-30.md`. Destination/method are in `.build/wave-3-handler-census/MAP.md` (git-ignored spine): slices of classify -> record -> only then change behaviour, each behaviour change behind a red test; the final slice makes completeness a test over ALL modules so the ceiling can't be bumped to absorb a new handler. Separately, P13 (adopt import-linter to replace the name-matching walker) is a multi-day item with no PR.
- **HANDLER-CENSUS-W3 slice 1 (`core/hardware` + `core/observability`, 12 handlers) -- DONE and SHIPPED 2026-10-02 as `6936269` -> release v1.123.5 (`456d29c`); CI run 36976442920 green on attempt 1; the PUBLISHED v1.123.5 win_amd64 wheel contains `_detected_ids_are_guessed` (the fix) and its `requires_dist` still carries `pyjwt>=2.15.0` and `urllib3>=2.8.0; extra == "nlp"`. Receipts:** unledgered 95 -> **83** (22 modules), ledger 248 -> **260** records, audited set 28 -> 32 modules, 0 stale; ceiling unchanged at 343 (the fixed handler stays broad). Real fail-open fixed: `memory_manager._get_detected_device_ids` fabricated a contiguous `range(get_device_count())` whenever device-ID enumeration RAISED, so an explicit `get_device_ids([1])` validated against guessed IDs and could route to the wrong GPU when real IDs are non-contiguous (e.g. [3,5]). Now an exception from an ID API that EXISTS sets `_detected_ids_are_guessed` and explicit IDs return `[]` (the pipeline raises its configuration error); a LEGACY detector that merely lacks the APIs keeps the fallback, decided by CAPABILITY (`callable(getattr(..))`/`_has_detector_method`), not by exception type. 6 tests in `tests/unit/test_memory_manager_enumeration_failure.py` (red against the committed bytes: 3 fail, seen; green after). The first draft discriminated by catching `AttributeError` -- my own brief's flaw, found in review: an AttributeError raised from INSIDE a present method would have fabricated IDs again (red-first test added) -- and a first refactor via `_has_detector_method` broke 2 existing MagicMock-based tests (`getattr_static` cannot see a mock's dynamic attributes), caught by running the neighbours. 279 tests in the 7 other files that use `MemoryManager` pass. 3 records are SILENT-SWALLOW, only 1 hardened; low-confidence ones: `get_device_count#1`, `_get_detected_device_ids#1`, `collect_device_inventory#0` (each hides its cause; classified by fail-closed direction). Unverified, not acted on: `get_vram_capacity_mb#1` returns 0 on a VRAM probe failure so a GPU gets CPU-RAM chunk sizing with no visible signal (safe direction, not a mis-route).
- **HANDLER-CENSUS-W3 slice 2 (leaf modules, 12 handlers, RECORD-ONLY) -- DONE 2026-10-02, receipts:** unledgered 83 -> **71** (16 modules), ledger 260 -> **272**, 0 stale, ledger CRLF preserved (no bare LF), 12 distinct reason strings, no source change, ceiling unchanged at 343; `test_handler_dispositions` + `test_silent_failure_hardening` 13 passed; 6 modules added to the audited set (`cli/runtime_paths`, `cli/freshness`, `cli/agent_capsule`, `cli/agent_capsule_call_sites`, `cli/audit_manifest`, `core/retrieval_chunker`). 6 INTENTIONAL-BOUNDARY / 4 LOGGED-DEGRADE / 3 SILENT-SWALLOW. Low confidence: `agent_capsule._collect_outbound_dependencies` (returns `([], {})`, justified as opt-in with no trust state) and both call-site collectors. Two OBSERVATIONS the slice surfaced ((1) is RESOLVED in the CHAIN-INTEGRITY bullet below; (2) is still open): (1) **audit-manifest chain tamper-evidence gap** -- `audit_manifest._previous_manifest_digest` returns the previous manifest's STORED `manifest_sha256` without recomputing it from the body, and `verify_audit_manifest(previous_manifest=...)` compares the link only against that stored field, so editing the previous manifest's body while leaving its digest field intact still yields `chain_valid=True` (verified by reading the code, `audit_manifest.py:1043+`; needs a red test to prove); security-shaped, so it needs the repo's adversarial gate; (2) the call-site collectors DISCLOSE failure in `call_site_evidence.status` but do not escalate to `partial`/`result_incomplete`, so a consumer checking only `related_call_sites == []` cannot tell "no callers" from "collection failed" (contract question, not proven wrong).
- **CHAIN-INTEGRITY FIX (audit manifests + evidence receipts) -- 2026-10-02, gated by 3 Codex Sol rounds; SHIPPED as `a1264b5` -> release v1.123.6 (`603f0aa`), CI run 36990890939 green on attempt 1, the artifact gate passed again on that release's builds, and the PUBLISHED v1.123.6 win_amd64 wheel contains `_previous_manifest_link`, `_previous_receipt_link` and both `_digests_equal` helpers (dead `_previous_manifest_digest` gone) with `pyjwt>=2.15.0` / `urllib3>=2.8.0` still in `requires_dist`.** REPRODUCED first: a previous manifest whose body was edited, digest field kept, still verified `valid: true` with `chain_valid: true` and no errors; the receipt twin (`previous_receipt_digest` / `verify_receipt_chain`) had the identical shape. FIXED: a chain now fails when the previous record's body does not match its own stored digest (one read via `_previous_manifest_link` / `_previous_receipt_link`; records that are non-JSON, not objects, or predate the digest field keep the raw-bytes link, so historical chains still verify), and `tg evidence emit --previous` (`previous_receipt_digest`) now REFUSES a corrupt predecessor. GATE: R1 (3 HIGH + 1 MEDIUM) -> the non-ASCII-digest crash was MY OWN bug (`hmac.compare_digest` raises TypeError on a non-ASCII `str`; I had called it on a hostile stored digest) plus the creation hole -> fixed; R2 accepted those and found one more live crash of the SAME class (manifest `signature.value`), so I swept EVERY attacker-reachable `hmac.compare_digest` into a never-raising `_digests_equal` helper per module (UTF-8 bytes, `surrogatepass`, still constant-time); R3 accepted the sweep and found `verify_receipt` crashing on a `NaN` body (`json.loads` accepts NaN, the canonicaliser refuses it) -> fixed (canonicalisation failure = invalid digest + unverifiable signature, never an exception). 22 new tests, RED against the committed bytes (seen: 6 of the 10 receipt tests fail on HEAD), green now; 195 evidence/manifest/history tests pass; ceiling 343 -> **341** (two broad handlers narrowed to the exceptions they can raise), ledger 272 -> **271** (the narrowed manifest handler's record retired), unledgered 71 -> **70**. PARKED with rulings (R2 agreed with both): (a) editing ONLY the predecessor's `signature` leaves a chain green -- the signature is excluded from the digest by design and was never part of chain verification (it is checked when the predecessor itself is verified); closing it changes what a chain attests and needs a PRODUCT decision; (b) rewriting an UNSIGNED head together with its predecessor is undetectable without an external anchor -- inherent to any hash chain; mitigation is signing + pinning a head digest out of band. STILL TRUE, NOT DONE: the manifest verifier reads the previous file UNBOUNDED (the receipt path is bounded); `verify_audit_manifest` raises on a non-JSON or pathologically nested CURRENT manifest by design; the round-3 fix had no further seat round (small, red-then-green); the generated `docs/code-map/src_tensor_grep_cli.md` still lists the removed `_previous_manifest_digest` (no freshness test covers it). Process lesson: a gate keeps finding the NEXT adjacent class, so after the first fix sweep the whole class yourself instead of fixing only the named site.
- **HANDLER-CENSUS-W3 slice 3 (`core/retrieval_dense` 4, `core/retrieval_late` 4, `cli/dogfood` 4; RECORD-ONLY) -- DONE 2026-10-02, receipts:** unledgered 70 -> **58** (13 modules), ledger 271 -> **283**, 0 stale, CRLF preserved, no source change, ceiling unchanged at 341; `test_handler_dispositions` + `test_silent_failure_hardening` 13 passed; the dogfood/dense/late test files 85 passed, 7 skipped. All 8 retrieval handlers are INTENTIONAL-BOUNDARY (they re-raise `BackendExecutionError` or exit 1 in `_fetch_cli`); `dogfood._terminate_process_tree` #0/#1 INTENTIONAL-BOUNDARY, #2 SILENT-SWALLOW, `dogfood.run_dogfood_readiness` #0 SILENT-SWALLOW (the two swallows can only lose diagnostics; the verdict is already forced to failed). Low confidence: those two SILENT-SWALLOWs, and `_terminate_process_tree#0` (the Windows fallback returns `[pid]` even if `taskkill` failed, so `killed_process_ids` can over-report). **OBSERVATION OUTSIDE THE LEDGER, verified by reading the code, NOT acted on:** `cli/agent_capsule_targets.py` (~line 714) catches the TUPLE `(DenseUnavailableError, ImportError, RuntimeError, ValueError, OSError)`; `BackendExecutionError` and `DenseUnavailableError` both subclass `RuntimeError` (`backends/base.py:7`, `core/retrieval_dense.py:52`), so a corrupt dense model that makes `load_dense_model` raise `BackendExecutionError` is swallowed and the capsule silently keeps the lexical ordering. Intent is stated inline ("never crash agent capsule on optional dense model faults") and `tg find` discloses its BM25 fallback, but here a consumer cannot tell "fusion ran and kept lexical" from "fusion failed" (absence of `semantic_fused` is also the normal case), and `main.py` lets the same error propagate, so the two paths disagree. It is a tuple, not a bare `except Exception`, so the handler ledger does not track it. DECIDED AND FIXED 2026-10-02 (a defensible design fork, not a CEO call): keep it fail-safe but DISCLOSE -- `tg find` already discloses its BM25 fallback, the inline comment says the dense leg must not crash the capsule, and nothing pins the target's key set (no schema; the builder emits `"primary_target": target` with no whitelist). A FAULT (extra installed but load/encode raised) now returns a COPY of the target with `semantic_fusion_unavailable: "<ExcClass>: <msg[:200]>"`; an expected absence (`dense_available()` False) and a normal "fusion ran, kept lexical" stay silent. Also dropped `DenseUnavailableError` from the except tuple and the in-`try` import: it is a RuntimeError subclass, and naming a name bound by an import inside the `try` raises UnboundLocalError if that import ever fails. 6 new tests (4 red on assertions first, 2 controls); 173 capsule/prepare tests pass.
- **RELEASE v1.123.7 (the three silent-degradation fixes) -- SHIPPED 2026-10-02, receipts:** `6471b51` (capsule dense-fault disclosure, prepare floor `possibly_incomplete` on a failed scan, bootstrap `--version` sentinel) pushed inside `2a720a2`, which FAILED CI: the `Formatting & Linting` job's file-size ratchet saw `cli/bootstrap.py` grow 1703 -> 1709 (a constant + comment I added; I had run ruff/mypy/pytest but not the job's other steps). `fec716c` inlined the literal (net 0 lines, pin NOT raised) and re-pointed the drift test at the real fallback; its run `37006956083` passed every lane, Semantic Release cut `62ee850 chore(release): v1.123.7`, `publish-pypi`/`publish-github-release-assets`/`release-tag-smoke` succeeded, and the PUBLISHED win_amd64 wheel was downloaded from PyPI and contains all three fixes (`semantic_fusion_unavailable` in `agent_capsule_targets`, `or error is not None` in `prepare_service`, `"0.0.0-unavailable"` in `bootstrap` with no bare-`0.0.0` return left). Slice 5 was held until the release published (a push mid-publish can cancel `publish-pypi`) and pushed after as `1e6121c`. Lesson: memory `feedback-run-every-step-of-the-ci-lint-job-not-just-ruff-2026-10-02`.
- **HANDLER-CENSUS-W3 slice 4 (`cli/prepare_service` 4, `cli/lsp_provider_setup` 4, `cli/ast_workflows` 3, `cli/bootstrap` 2; record-only) -- DONE 2026-10-02, receipts:** unledgered 58 -> **45** (9 modules), ledger 283 -> **296**, 0 stale, CRLF preserved, ceiling unchanged at 341; `test_handler_dispositions` 11 passed + `test_silent_failure_hardening` 2 passed (run separately -- a combined run hung 30+ min on the busy box); 205 module tests pass. 6 LOGGED-DEGRADE / 3 INTENTIONAL-BOUNDARY / 4 SILENT-SWALLOW. `lsp_provider_setup.py:242` is an `except BaseException` (unlink the partial download, re-raise) and is NOT recorded: the scanner ignores BaseException. Low confidence: prepare floor #0/#1 and `ast_workflows.scan_command#0` (its per-rule fallback may not behave exactly like the project-level search). TWO OBSERVATIONS, both FIXED the same day with red-first tests, then the three affected ledger records were rewritten to match: (1) **prepare blast-radius floor** -- both handlers returned `callers_count: 0` and `possibly_incomplete: False` with only an `error` string, i.e. "no callers, complete" for a scan that raised; `possibly_incomplete` is now True when `error` is set (`deadline_partial`, the exit-2 gate, stays deadline-only so exit codes are unchanged); 3 tests (2 red, 1 control: a successful no-callers scan stays complete); both records now `hardened_in: HANDLER-CENSUS-W3-d`. (2) **bootstrap `--version`** -- `_read_project_version_fallback` returned a bare `0.0.0` (reads like a real version) while `main.py` uses `0.0.0-unavailable`; bootstrap now has the same explicit sentinel (cannot import main, so a drift-guard test pins the two equal); record re-categorised SILENT-SWALLOW -> LOGGED-DEGRADE; 3 tests. NOT changed: the many other modules that use a bare `"0.0.0"` as their own internal "unknown" sentinel (`doctor_payload`, `runtime_paths`, `evidence_receipt`) -- those compare against it deliberately.
- **HANDLER-CENSUS-W3 slice 5 (`sidecar` 3, `cli/session_resume_service` 2, `cli/checkpoint_store` 1; record-only) -- DONE 2026-10-02, receipts:** unledgered 45 -> **39** (6 modules), ledger 296 -> **302**, 0 stale, CRLF preserved, ceiling unchanged at 341; re-counted independently with the project scanner after the implementer reported; `test_handler_dispositions` 11 passed, `test_silent_failure_hardening` 2 passed. 1 LOGGED-DEGRADE (`sidecar._classify_lines_with_metadata` falls back to the heuristic classifier and reports `provider_status`/`fallback_reason`), 5 INTENTIONAL-BOUNDARY (`sidecar._gpu_search` -> empty stdout + stderr + exit 1; `sidecar.main` -> traceback + `exit_code` 1 in the JSON response; the two `session_resume_service` dispatchers -> stderr + `typer.Exit(1)`; `checkpoint_store.undo_checkpoint` -> best-effort rollback then re-raise). No proven fail-open. TWO NOTES, not fixed: (1) `sidecar.main` returns process exit 0 with the failure ONLY in the JSON body -- that is the sidecar protocol, but a parent that checks the return code alone would miss it. (2) inside `undo_checkpoint`'s rollback, two narrow `except OSError: pass` clauses (~lines 1448-1460) swallow a failed rollback write; the original exception is still re-raised so the undo reports failure, but a partially un-restored tree is not disclosed. The scanner does not count narrow handlers, so they sit outside this ledger; worth a behavioural test before any change.
- **RELEASE v1.123.8 (receipt `tool.version` twin fix) -- SHIPPED 2026-10-02, receipts:** `f0342ac` (`fix(evidence)`) pushed with slice 6 as `8510917`; run `37019621289` passed every lane (it was held until the preceding board-reconcile run `37014854730` finished, because the v1.123.7 release commit had put `docs/TASK_BOARD.md` 6 releases behind its freshness gate -- reconciled against the 30 commits, the empty open-PR set and main's CI history, then re-stamped in `2315da8` at 79,952 of 80,000 bytes), Semantic Release cut `bd287c2 chore(release): v1.123.8`, `publish-pypi`/`publish-github-release-assets`/`release-tag-smoke` succeeded, PyPI lists 4/4 artifacts (macosx_11_0_arm64, manylinux_2_39_x86_64, win_amd64 wheels + sdist), and the PUBLISHED win_amd64 wheel contains `return "0.0.0-unavailable"` in `cli/evidence_receipt.py` with no bare `0.0.0` return left. I cancelled one run of my own by ID (`37014599934`, slice 5, doomed by the stale stamp, non-release) to free the concurrency slot; nothing else was cancelled.
- **HANDLER-CENSUS-W3 slice 6 (`cli/evidence_receipt` 3, `cli/session_store` 2, `cli/evidence_signing` 1) -- DONE 2026-10-02, receipts:** unledgered 39 -> **33** (4 modules), ledger 302 -> **308**, 0 stale, CRLF preserved, ceiling unchanged at 341; re-counted independently; `test_handler_dispositions` 11 passed (re-run AFTER my record edits), `test_silent_failure_hardening` 2 passed, file-size budget 0 regressions. These are security-shaped modules, so each handler was asked "can unverified/unsigned/unwritten data be treated as verified/signed/persisted?" -- the answer was NO for all six (I re-read `load_private_key` myself: every failure is wrapped in `EvidenceSigningError` with the cause chained, so no key object is ever returned). 2 LOGGED-DEGRADE (`session_store.refresh_session`, `evidence_receipt._blast_radius_block_recomputed` -> `status: unavailable` with a reason), 2 INTENTIONAL-BOUNDARY (`load_private_key`, `session_store.serve_session_stream` -- the failure reaches the client as an error response but is not logged server-side), and the two receipt version handlers. ONE TWIN FOUND AND FIXED (class sweep, per the 2026-10-02 lesson): `evidence_receipt._read_project_version_fallback` says it "mirrors main.py exactly" but main.py had been hardened to `0.0.0-unavailable` (A3 / W1-c) while this copy kept a bare `0.0.0`, which lands in a SIGNED receipt's `tool.version` and in codemap's `tool_version`. Now the same sentinel (net 0 lines in a 989-line file), with a drift-guard test that drives both version sources unreadable and compares to `main._VERSION_UNAVAILABLE_SENTINEL` (1 red on the exact assertion, 1 control); 159 receipt/codemap/version tests pass; the record moved SILENT-SWALLOW -> LOGGED-DEGRADE (`hardened_in: HANDLER-CENSUS-W3-f`), while `_cli_package_version` stays SILENT-SWALLOW because its pyproject-for-metadata substitution is still undisclosed (only the terminal case is visible). STILL BARE `0.0.0` BY DESIGN: `runtime_paths._read_project_version_fallback` and `doctor_payload` compare against it deliberately as their own "unknown" marker -- changing those needs their consumers changed together, not a literal swap.
- **HANDLER-CENSUS-W3 next slices (33 left in 3 modules, computed with the project scanner on 2026-10-02):** (e3) teardown-heavy, one slice each: `cli/session_daemon` (11, incl. the `:1947` rebuild-on-error path), `cli/lsp_server` (8), `cli/lsp_external_provider` (14) -- 33 handlers, all in long-lived server/teardown code where a naive NARROW can leak a process or socket, so each wants a behavioural teardown test BEFORE any change; (e4) final: completeness over ALL modules (so the ceiling cannot be bumped to absorb a new handler) + an `except BaseException` decision (12 handlers the scanner ignores). Slices 1-6 covered 67 handlers in 30 modules; (e1) and (e2) are DONE as slices 5 and 6.
- **Local gitleaks v8 for the positive-control test:** `C:\tmp\tensor-grep\KEEP-gitleaks-8.30.1-verified\gitleaks.exe` (sha256 of the zip matches `gitleaks_8.30.1_checksums.txt`, re-verified 2026-10-01). Run: `GITLEAKS_BIN=C:\tmp\tensor-grep\KEEP-gitleaks-8.30.1-verified\gitleaks.exe pytest tests/unit/test_gitleaks_positive_control_validation.py`.
- **Closeout 2026-10-01 receipts:** pushed `f5f55ee` (drop red-by-design test) and `7335bd9` (TASK_BOARD reconcile to v1.123.1, size gate 79,863/80,000); closed superseded PRs #1182 (tip `abc58469`) and #1183 (tip `59649793`) -- merging either would have reverted Wave 2; `abc5846` (stray `.test_credential.py`, fake creds at repo root) intentionally NOT merged.
- **FIXED 2026-10-01 (was wrongly parked as "not mine"): the `audit.yml` pip-audit gate had been RED on main for 3+ days** (`c2967cc` 09-30 and 10-01, `aa79844` 10-02): **16 known vulnerabilities in 2 packages** -- PyJWT 2.13.0 (13 PYSEC-2026-41xx, fixed in 2.14.0/2.15.0) and urllib3 2.7.0 (PYSEC-2026-4175/4176/4177, fixed in 2.8.0). Dependabot PR #1184 covered only PyJWT (`uv.lock` +3/-3) and sat behind main's old reds. Fixed across the repo's whole floor chain: `[tool.uv].constraint-dependencies` (`pyjwt>=2.15.0`, NEW `urllib3>=2.8.0`), the lock (PyJWT 2.15.1, urllib3 2.8.0, plus the two `[manifest]` constraint lines), the release validator's `expected_constraints`, the validator-test fixtures, and `tests/unit/test_security_dependency_floors.py` (3 red-first tests). The lock change is hand-spliced to 14 lines, not a `uv lock` rewrite, because the local uv 0.10.7 also rewrites unrelated `cuda-pathfinder`/`pywin32`/`numpy` markers (137 lines of noise). Verified by re-running CI's own invocation: `pip-audit` -> "No known vulnerabilities found, 2 ignored" (it reported 16 on the old lock); `uv export --locked` rc=0; `validate_release_assets.py` passes; 87 dependent tests pass. PR #1184 is superseded (closed). **CORRECTION to my own first commit (`7330b93`):** its message and this entry claimed the published metadata floor changed -- WRONG. `[tool.uv].constraint-dependencies` is lock-only ("NOT published metadata", per the validator's own comment) and PyPI v1.123.3 `requires_dist` carried only `cryptography>=50.0.0`: it repaired this repo's lock and the audit gate but NOT an existing install holding PyJWT 2.13.0. Reachability (`uv tree --invert`): PyJWT is in the BASE install (tensor-grep -> mcp[crypto] -> pyjwt), urllib3 only via the `nlp` extra (tritonclient[http] -> geventhttpclient). Follow-up commit publishes both: `pyjwt>=2.15.0` in `[project].dependencies` (like the existing direct `cryptography>=50.0.0`) and `urllib3>=2.8.0` in the `nlp` extra (like `aiohttp`), the validator now enforces EVERY published floor (it previously checked one string per category), the lock's `tensor-grep` entry gained the 4 matching lines, and 3 fixtures + 2 new tests (red against the old validator) + 1 published-metadata test cover it. RECEIPTS (closed 2026-10-02): `audit.yml` dispatched on main at `7330b93` -> "Dependency & License Audit" success (run 36956518077); the published floors shipped as **v1.123.4** (`b2990da`), PyPI `requires_dist` = `pyjwt>=2.15.0` (base) + `urllib3>=2.8.0; extra == "nlp"` (4 files); CI on `87a921d` green on attempt 1. DOGFOODED THE PUBLISHED ARTIFACT with pip (default only-if-needed upgrades) into an environment holding PyJWT 2.13.0 + urllib3 2.7.0: `tensor-grep==1.123.3` (the lock-only fix) installs ONLY tensor-grep and LEAVES BOTH VULNERABLE; `tensor-grep==1.123.4` installs `PyJWT-2.15.1`, and with `[nlp]` also `urllib3-2.8.0`. NOTE the trap: the same test with `uv pip install` upgraded PyJWT for BOTH releases (uv resolves more eagerly), so it could not discriminate -- test with pip. PYSEC-2026-4146 (no fix version listed on the old lock) no longer appears on 2.15.1. GAP CLOSED 2026-10-02: nothing had checked that a declared requirement reaches the BUILT artifact. `scripts/validate_pypi_artifacts.py` (the release-time `validate-pypi-artifacts` CI job, which already opened each wheel's METADATA for the version) now also requires every `[project].dependencies` and extra requirement from `pyproject.toml` to appear in each wheel's `METADATA` and each sdist's `PKG-INFO` with the same name, specifier and extra (other environment markers compared leniently). `main()` enables it by default (`--pyproject`, default the repo's); `validate(pyproject_path=None)` skips it for old callers. Proven on REAL artifacts, not just fixtures: published v1.123.4 wheel AND sdist -> 0 of 75 declared requirements missing (no false positive on real maturin formatting, `Requires-Dist: x>=1 ; extra == 'nlp'`); published v1.123.3 wheel AND sdist (the lock-only fix) -> exactly `pyjwt>=2.15.0` and `urllib3>=2.8.0 (extra 'nlp')` missing, i.e. the incident would have been blocked before publish. 7 new tests (RED on assertions first). FIRST LIVE RUN 2026-10-02 (release v1.123.5, CI run 36976442920): the real `validate-pypi-artifacts` job ran `validate_pypi_artifacts.py` on freshly built wheels + sdist with the requirements check on by default and printed "PyPI artifact validation passed." -- no false positive on a live build. (It still only runs on a release, `publish_pypi == 'true'`, and cannot be exercised locally end-to-end because a wheel build needs cargo, banned on this shared box.)
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
