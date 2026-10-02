# Wave 2 Final-SHA Audit Spec (NOT YET RUN)

**Repo:** tensor-grep
**Working dir:** C:\dev\projects\tensor-grep
**Commits to audit:** `1a9de6a` (mutation control + walker), `533c178` (handler census denominator), `648843e` (gitleaks positive control). Base `03b8153`. Audit the bytes at `origin/main` HEAD, not a description.
**Model:** gpt-6-sol, effort high, `--sandbox read-only --ignore-user-config`. Pong first.

## Claims to validate (do not re-derive; try to BREAK them)

1. `src/tensor_grep/core/import_edges.py` now reports string-literal `import_module("x")` / `__import__("x")`; `tests/unit/test_import_edges_baseline.py::test_a_new_backward_import_would_be_caught` plants one static and one dynamic backward edge and requires both. Red arm already seen: with the pre-`1a9de6a` walker the test fails "Mutation control (dynamic) ... not detected".
2. `docs/audits/handler-census-gaps-2026-09-30.md`: 142 modules = 9 audited + 27 unaudited + 106 excluded (19 original + 87 "others"). The 87 is a SUBTRACTION (142-36-19), not an enumerated list.
3. `tests/unit/test_gitleaks_positive_control_validation.py` passes against gitleaks 8.30.1 with `AKIAIOSFODNN7ZXCVBNM`; it SKIPS without a v8 binary (CI has none).

## Hostile demands (each needs file:line + a command you ran)

- Walker: find a cross-package backward import that still escapes (`builtins.__import__`, `importlib.util.spec_from_file_location`, `getattr(importlib, "import_module")`, a non-literal argument). Say whether silently skipping non-literals is acceptable or must be reported UNRESOLVED. Also find a FALSE POSITIVE from the any-receiver `.import_module(` match.
- Census: enumerate the real module set from `src/tensor_grep` and check 142 and the 106 split. A count that cannot be re-derived is MEDIUM.
- Positive control: confirm the key is not allowlisted AND that deleting the credential commit really returns exit 0 for the right reason. Confirm the test's skip path cannot be mistaken for a pass in CI.

## Severity

CRITICAL: a gate that cannot go red / a credential class undetected. HIGH: a real escape the new test does not cover. MEDIUM: ambiguity informing Wave 3. LOW: wording. Raise nothing without file, line and observable consequence.

## Output

Write findings to `.build/deep-audit-verify/RECEIPTS.md` under "Codex Sol Wave 2 Audit". Last line must be exactly one of:

```
[codex-audit] DONE Wave 2 critical=N high=N medium=N low=N
RECOMMENDED: APPROVE
RECOMMENDED: REVISE -- <blockers>
```

## Constraints

Read-only; no commits; git read-only; do not modify product code.

## RUN RECORD (2026-10-01)

- Attempt 1 (spec only, seat reads the tree): no verdict. The seat reported its read-only sandbox rejected every command launch (pwd, git, file reads) -- a failed seat, not an approval.
- Attempt 2 (all artifacts inlined, ~33 KB, brief builder fails closed on a missing region): `[codex-audit] DONE Wave 2 critical=1 high=1 medium=2 low=0` / `RECOMMENDED: REVISE`. Source-only: "command run: none". Dispositions and probes are recorded in `backlog.md` (WAVE-2-DESIGN-PACKET open item 3). This is R1 of at most 5; a verdict of AUDIT_CLEAR requires a later round on the post-fix SHA.
- Round 2 (inlined, `c1bea5a`): `[codex-audit] DONE Wave 2 R2 critical=0 high=3 medium=0 low=0` / `RECOMMENDED: REVISE`. Three HIGH: static root `from tensor_grep import cli` escape, keyword/`package=` dynamic forms, opaque import calls passing as "no edge". Each probed and confirmed by the orchestrator, then fixed (see `backlog.md` WAVE-2 item 3). Seat found no new class on the receiver-agnostic match and could not substantiate the R1 CRITICAL. R3 is required on the post-fix SHA.
- Round 3 (inlined, `ed0150e`): `[codex-audit] DONE Wave 2 R3 critical=0 high=6 medium=0 low=0` / `RECOMMENDED: REVISE`. Six HIGH, each a new class: `__init__.py` relative-import resolution, child module hiding behind a parent edge, root star import, relative `__import__` level, unresolved sites frozen by name not count, `runpy.run_module`. All probed and confirmed by the orchestrator, then fixed (see `backlog.md` WAVE-2 item 3). R4 required on the post-fix SHA; R5 is the cap.
- Round 4 (inlined, `473d025`): `[codex-audit] DONE Wave 2 R4 critical=0 high=3 medium=0 low=0` / `RECOMMENDED: REVISE`. (1) opaque call retargeted without changing the frozen count, (2) star import from another layer package, (3) namespace/extension children hiding behind a parent edge. All probed; fixed by pinning reviewed `_EXPORTS`/`_MAIN_MODULE` targets (with a mutation control), surfacing cross-layer star imports as unresolved sites, and widening `_is_submodule`. The seat noted findings 2 and 3 need a frozen bare-package edge that the baseline does not contain. R5 is the cap.
