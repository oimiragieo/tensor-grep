# Code Audit Report — tensor-grep

**Date:** 2026-08-21 · **Auditor:** orchestrated audit (file-size census, data-contract audit,
documentation rebuild assessment, plus live dogfood of the shipped and installed artifacts)
**Tree audited:** `origin/main` at `61a125c` and successors through the 2026-08-21 drain
**Installed binary dogfooded:** `tg 1.110.16` · **Published artifact dogfooded:** `tensor-grep 1.111.1`

Supporting artifacts, each with its own method section and controls:

- `docs/audits/2026-08-21-file-size-census.md` — 856 files scanned, 4 categories
- `docs/audits/2026-08-21-pypi-size-cap-decision-packet.md` — release-pipeline incident + policies
- Data-contract audit and documentation-rebuild assessment: findings folded in below

---

## 1. Audit Scope

**Reviewed:** every human-authored source file in the tree against four size limits; all 23
identified data-contract surfaces; documentation rebuild-readiness for the three most load-bearing
user-facing features; the release pipeline and its published artifacts; the CI gating topology; and
live behaviour of the installed `tg` binary and the published wheel.

**Excluded, and named here rather than silently dropped:** generated code, vendored dependencies,
minified assets, lockfiles, binaries, `.venv/`, `target/`, `node_modules/`, `__pycache__`, `.git`.
Rust `rust_core/` sources ARE included in the census (they are human-authored) and appear among the
offenders.

**Evidence that was unavailable, so the related checks are marked "Unable to verify" rather than
passed:**

- SARIF output was sized but not verified field-by-field against the OASIS SARIF schema; no
  confirmation exists that this repo validates against the official schema at all.
- `evidence_signing` fail-closed enforcement was read at the wrapper level only; the exact enforcing
  line was not opened.
- The AST cache on-disk schema (`project_data_v6.json`), `sgconfig.yml`, and the Python library API
  (`tensor_grep.api`) are documented in `docs/CONTRACTS.md` but their enforcing code was not opened.
- `--format rg --json` compatibility route: documented, implementation not opened.

---

## 2. Final Verdict

**FAIL.**

Three independent conditions in the pass/fail rules are each independently sufficient:

1. **Mandatory file-size limits are exceeded** — 30 violations, including one file at **30× its
   limit**. They are grandfathered by an in-repo ratchet, but a documented exception that has never
   been retired is not an approved exception; it is deferred work with a green light on it.
2. **A Critical and three High findings are unresolved** (§7).
3. **Documentation is not sufficient to rebuild the feature from scratch** for 2 of the 3
   load-bearing features assessed (§9).

The release pipeline is additionally **broken and platform-skewed** (§4, PYPI-SIZE-CAP), which
would be a release blocker on its own.

**This verdict is about governance surface, not about product quality.** The shipped artifact is
healthy: a clean-container dogfood of the published v1.111.1 passed **17/17** feature checks, and
the security-hardening audit of 137 broad exception handlers found the overwhelming majority already
correctly dispositioned. The failures concentrate in the *measurement and contract layer* — checks
that could not fail, schemas nothing validated, and files too large to review.

---

## 3. Executive Summary

**The dominant risk in this repository is not broken features. It is checks that report green
without having verified anything.** Of the substantive defects found in this pass, more were located
in the verification layer than in the product:

- `tests/schemas/tg_output.schema.json` describes the primary `tg search --json` contract and
  **nothing validated against it**. Its only references in the entire tree were a line-counting size
  census and a code-map listing. A schema no test loads cannot fail, so it cannot be evidence.
- Two pull requests carrying error-handling hardening sat merge-ready with **zero CI runs**, because
  a PR whose base is a feature branch never triggers `ci.yml` (which filters on the *base* ref), and
  `gh` renders that absence as `skipping`. **Both went red the moment real CI ran.**
- `tg scan --ruleset` returns **exit 0 with `matched_rules: 0` for a path that does not exist** — a
  clean bill of health from a security scanner that read nothing.

Strengths worth stating plainly, because an audit that only lists defects misrepresents the
codebase: the contract documentation in `docs/CONTRACTS.md` is unusually rigorous (it documents its
own anchor-rot history rather than deleting it, and refuses to hardcode line numbers in several
places); the handler-disposition ledger is symbol-anchored rather than line-anchored, with
perturbation arms that prove each check discriminates; and the evidence/review-bundle contracts
explicitly document that integrity does not imply authenticity — a caveat most implementations omit.

**Release implication:** do not cut a release until PYPI-SIZE-CAP is resolved. Publishing is
currently failing per-artifact, and the last release reached users as two different versions
depending on their platform.

---

## 4. Compliance Scorecard

| Control area | Status | Severity | Evidence | Required action |
|---|---|---|---|---|
| File sizing | **FAIL** | Critical | 30 violations / 856 files scanned; worst is `repo_map.py` at 15,243 vs a 1,500 limit | Execute the split program; retire allowlist entries as each lands |
| Specification alignment | **PARTIAL** | Medium | No single spec doc exists; `docs/CONTRACTS.md` serves as the de-facto contract spec and is strong | Designate CONTRACTS.md formally, or write the missing spec |
| Design alignment | **PARTIAL** | Medium | Design packets exist per-feature (`docs/design/`), but not for the largest modules | Design packet required before the `repo_map.py` split |
| Data contracts | **FAIL** | High | 23 surfaces; 16 contract-defining files over 500 lines; no `.schema.json` for MCP/ledger/checkpoint/evidence | Add machine-readable schemas for the wire surfaces |
| Implementation quality | **PASS (with findings)** | Medium | 137 broad handlers audited and dispositioned; 3 SILENT-SWALLOW found and hardened | Continue the census discipline |
| Unit tests | **PARTIAL** | High | ~7,000 tests, strong perturbation/control discipline; but a primary contract schema had no validating test until this audit | Validate every declared contract |
| Mocks & fixtures | **PASS** | Low | Fixtures are per-scenario, no oversized shared fixture found; no secrets or production data | None |
| Security | **PASS (with 1 High)** | High | Prior audits found 8/8 hardening targets already correct; but `tg scan` cannot distinguish clean from unread | Fix SCAN-SILENT-CLEAN-ON-MISSING-PATH |
| Operations / release | **FAIL** | Critical | PyPI at 10.734 GB vs a 10 GB cap; v1.111.1 published 2 of 4 artifacts | Execute an approved retention policy |
| CI gating integrity | **FAIL** | High | Stacked PRs receive no CI and render as `skipping` | Enforce `baseRefName == main` before merge |
| Documentation | **FAIL** | High | 2 of 3 features fail the junior-rebuild test; 2 self-contradictions | Write the named missing sections |

---

## 5. File-Size Validation

Limits: contracts/schemas/interfaces ≤ 500 · core/business logic ≤ 1,500 · unit tests ≤ 2,000 ·
mocks/fixtures ≤ 2,000. Physical lines, comments and blanks included.

**Census result: 856 files scanned (test 434, core 413, fixture 6, contract 3). 30 violations — core
20, test 10. No category scanned zero files**, which is stated explicitly because an empty scan is
indistinguishable from a clean one.

The repo's own gate (`scripts/file_size_budget.py`) encodes **exactly these four limits** — no
mismatch between the audit standard and the enforced standard. All 30 violations are grandfathered
in `scripts/file_size_allowlist.json` with a ratchet preventing regression.

| File path | Category | Lines | Limit | Status | Recommended action |
|---|---|---|---|---|---|
| `src/tensor_grep/cli/repo_map.py` | core | 15,243 | 1,500 | **VIOLATION (10.2×)** | Split by pipeline stage; isolate the edit-plan confidence gate first |
| `rust_core/src/main.rs` | core | 15,126 | 1,500 | **VIOLATION (10.1×)** | Split by command group |
| `src/tensor_grep/cli/main.py` | core | 13,523 | 1,500 | **VIOLATION (9.0×)** | Split by command family |
| `src/tensor_grep/cli/mcp_server.py` | core | 5,341 | 1,500 | **VIOLATION (3.6×)** | Split by tool family, keep contract-version constant in place |
| `rust_core/src/gpu_native.rs` | core | 4,952 | 1,500 | **VIOLATION (3.3×)** | Split by kernel/dispatch/probe |
| `rust_core/tests/test_schema_compat.rs` | test | 4,412 | 2,000 | **VIOLATION (2.2×)** | Split by schema family |
| `src/tensor_grep/cli/session_daemon.py` | core | 2,139 | 1,500 | VIOLATION | Split protocol vs lifecycle |
| `src/tensor_grep/cli/session_store.py` | core | 1,828 | 1,500 | VIOLATION | Split store vs index |
| `rust_core/src/backend_cpu.rs` | core | 1,817 | 1,500 | VIOLATION | — |
| `rust_core/src/index.rs` | core | 1,756 | 1,500 | VIOLATION | — |
| `src/tensor_grep/cli/bootstrap.py` | core | 1,696 | 1,500 | VIOLATION | Front door — split with extreme care |

**Resolved during this audit** (were violations, now compliant): `test_cli_modes.py` 17,204 → 9
modules; `test_benchmark_scripts.py` 10,689 → 7 modules; `test_mcp_server.py` 9,729 → 8 modules;
`test_release_assets_validation.py` 5,258 → 6 modules. Grandfathered count **30 → 27**.

**Near-miss watch list** (within 10%, currently compliant): `lsp_external_provider.py` (96.8%),
`checkpoint_store.py` (95.4%), `ast_workflows.py` (94.8%). The latter two were split in a
2026-07 wave and have **regrown** — evidence that splitting without a structural constraint is
temporary.

---

## 6. Requirements Traceability Matrix

No formal numbered requirements document exists; `docs/CONTRACTS.md` is the de-facto contract
specification. The matrix is therefore keyed to contract sections.

| Requirement / section | Summary | Implementation | Test evidence | Doc evidence | Status | Gap |
|---|---|---|---|---|---|---|
| Search JSON envelope | Primary `--json` contract | `core/result.py:72-213`, `formatters/json_fmt.py` | `tg_output.schema.json` + `test_search_json_schema_contract.py` (**added by this audit**) | `harness_api.md` §Search JSON | **Compliant (newly)** | Was untested until 2026-08-21 |
| Completeness triple | `result_incomplete` + reason + class | `core/result.py:135` | Now typed + validated | `CONTRACTS.md:192` | **Compliant (newly)** | Was absent from schema |
| `tg find` envelope | Hybrid search output | `cli/main.py:_execute_find` | Envelope shared with search | `harness_api.md` §Find JSON (**added**) | **Non-compliant** | Emits `routing_backend`/`routing_reason` as `null` |
| Ruleset scan | Security rule scanning | `cli/ast_scan.py`, `rule_packs.py` | `test_new_rule_packs.py` | `CONTRACTS.md` | **Non-compliant** | Exit 0 on missing path |
| MCP contract v1.7.0 | 58-tool wire surface | `mcp_server.py:188` | `test_mcp_contract_version_docs_are_pinned.py` | `architecture.md` | Compliant | No machine-readable schema |
| Evidence receipts | Ed25519 signed receipts | `evidence_receipt.py` | 3 test files | `CONTRACTS.md:244-250` | Compliant | Enforcing line unverified |
| Symbol graph | defs/refs/callers/blast-radius | `repo_map.py` | extensive | add-language skill | Compliant | Rebuild doc missing |
| Exit-code contract | 0/1/2 three-state | `main.py`, `bootstrap.py` | `test_main_cli_contracts.py` | `CONTRACTS.md:173` | **Partially compliant** | `tg scan` does not honour it |

---

## 7. Detailed Findings

### F-1 — `repo_map.py` is unreviewable at 15,243 lines and houses the auto-edit safety gate
**Severity: Critical** · `src/tensor_grep/cli/repo_map.py` · edit-plan confidence gate
(`_edit_plan_confidence_and_ask`, referenced `docs/CONTRACTS.md:140`)
**Evidence:** 15,243 physical lines against a 1,500 limit — 10.2×, the largest file in the tree.
**Risk:** the logic deciding whether an agent may auto-edit without asking the user lives inside a
file no reviewer can hold in working memory. A regression in the ask-gate is a *silent* escalation
of autonomy on a customer's codebase.
**Remediation:** measure the dependency graph first, then split by pipeline stage, isolating the
confidence gate into its own module. **Acceptance:** every resulting file < 1,500 lines; the gate in
a dedicated module with its own tests; all existing imports and monkeypatch targets still resolve.

### F-2 — `main.py` at 13,523 lines directly produces the CLI JSON and exit-code contract
**Severity: High** · `src/tensor_grep/cli/main.py`
**Risk:** field-emission consistency across dozens of commands cannot be verified by review at this
size. **Remediation:** split by command family behind re-export shims.

### F-3 — No machine-readable schema exists for MCP, ledger, checkpoint, or evidence payloads
**Severity: High** · repo-wide; only `tests/schemas/tg_output.schema.json` exists (confirmed by
`find . -iname "*.schema.json"`)
**Risk:** four wire surfaces with external consumers are specified only in prose. Prose cannot be
validated in CI, so producer drift is caught by whichever test happens to assert a substring.
**Remediation:** author JSON Schemas and validate real payloads against them, with the bidirectional
controls now established in `test_search_json_schema_contract.py`.

### F-4 — `tg find --json` violates the envelope contract it reuses
**Severity: High** · `tg find` JSON route
**Evidence (measured, `tg 1.110.16`):** emits `routing_backend: null`, `routing_reason: null`; both
are `required` and typed `{"type":"string","minLength":1}`. Validation fails with
`None is not of type 'string'`. Control: `tg search` on the same tree emits `"NativeCpuBackend"` /
`"json_output"`.
**Failure scenario:** an agent using a contract-aware parser throws, or mis-branches on routing.
**Remediation:** populate the fields, or give `find` its own schema and stop reusing the envelope's
field names. **Do not** relax the schema — that degrades `tg search`, which is correct.

### F-5 — `tg scan --ruleset` reports CLEAN, exit 0, for a path that does not exist
**Severity: High (security-surface honesty)** · `tg scan`
**Evidence (unpiped exit codes):** `tg scan <missing> --ruleset subprocess-safe` → **exit 0**,
`matched_rules: 0`. `tg search <missing>` → **exit 2**, `path_not_found`. `tg scan <real path with
one finding>` → exit 0, `matched_rules: 1`.
**Failure scenario:** a CI gate running `tg scan --ruleset secrets-basic <mistyped path>` reports the
repository clean and exits 0. The exit code cannot distinguish "clean" from "read nothing".
**Remediation:** fail closed with `tg search`'s existing `path_not_found` shape and exit code. Sweep
the sibling surfaces (`tg run`, config-file scan, MCP `tg_ruleset_scan`) — the census, not the
instance. **Acceptance:** bidirectional RED — a nonexistent path must exit non-zero (it currently
exits 0), and a real path with a known finding must still exit 0 with `matched_rules: 1`.

### F-6 — Stacked PRs receive no CI, and the absence renders as "skipping"
**Severity: High (process/gating)** · `.github/workflows/ci.yml`
**Evidence:** `pull_request: branches: ["main"]` matches the **base** ref. PRs #1068/#1070 had
exactly one check each across their entire life (`Dependabot Automation` / `skipped`) while
reporting `MERGEABLE`. Control: #1065, same branch prefix, base `main`, `SUCCESS=39`. **Both failed
immediately once real CI ran.**
**Remediation:** enforce `baseRefName == "main"` at merge time or via branch protection. Note the
bootstrap problem: a workflow that does not run on stacked PRs cannot police them.

### F-7 — `_RULE_PACKS` has no load-time validation
**Severity: Medium** · `src/tensor_grep/cli/rule_packs.py:5-1115`
A malformed entry surfaces as a `KeyError` at scan time, not at definition time. No
`test_rule_pack*.py` was found. **Candidate defect — not confirmed by execution.**

### F-8 — SARIF output is unverified against the SARIF standard
**Severity: Medium** · `src/tensor_grep/cli/sarif.py` (345 lines)
Two SARIF test files exist by name; no evidence the output is validated against the official OASIS
schema. Consumers are external tools, so drift is externally visible. **Unable to verify.**

### F-9 — Documentation cannot support a from-scratch rebuild
**Severity: High** — detailed in §9.

---

## 8. Test and Fixture Assessment

**Strengths.** Roughly 7,000 unit tests. The perturbation discipline is genuinely strong: the
handler-disposition suite ships arms that synthesize a broken ledger in memory and assert the check
catches it, so each check is proven to discriminate rather than assumed to. Fixtures are
per-scenario, contain no secrets or production data, and no oversized shared fixture was found
(0 fixture-category size violations).

**Gaps.**

1. **A declared contract with no validating test** — the search JSON schema, now closed.
2. **Env-dependent tests.** `test_w1c_sarif_version_disclosure.py` passes standalone and in most
   lanes but fails on the GPU lane, which runs the whole tree in one process. The repo's own law
   (A85) requires forcing the optional-engine seam rather than depending on what happens to be
   installed.
3. **Splits that silence rather than surface.** One drafted split reported "484 passed, 5 skipped"
   against a **489 passed / 0 skipped** baseline — three invented
   `pytest.skip("... unavailable in this environment")` guards would have permanently disabled tests
   that pass in CI. **Any split must reproduce its baseline's passed AND skipped counts.**
4. **No contract-compatibility tests** for MCP/ledger/checkpoint/evidence (follows from F-3).

---

## 9. Documentation Rebuild Assessment

Assessed against 20 required sections, for the three most load-bearing features.

| Feature | PRESENT | PARTIAL | MISSING | Rebuild verdict |
|---|---|---|---|---|
| `tg find` (BM25 + dense + RRF) | 5 | 8 | 7 | **NO** |
| MCP server surface (58 tools) | 6 | 7 | 7 | **NO** |
| Symbol graph | 9 | 7 | 4 | **CONDITIONAL** |

**`tg find` — NO.** The RRF fusion constant and the fusion pipeline's module layout exist only in
code. `docs/harness_api.md` — designated canonical for JSON contracts and carrying a section for
every sibling command — **had no Find section at all** (added by this audit, including its known
contract violation, since documenting an idealised contract the product does not honour teaches the
reader to distrust the doc).

**MCP server — NO.** `architecture.md` gives an excellent line-cited inventory of all 58 tools, but
the legacy-tool enable condition, tool lifecycle, and a consolidated security model are undocumented.
A junior would have to read `mcp_server.py` (5,341 lines) directly.

**Symbol graph — CONDITIONAL.** Well documented for *extending* — the `tensor-grep-add-language`
skill is a genuine rebuild recipe. But the general graph-construction algorithm has no
language-independent design doc, and `repo_map.py` is far too large to serve as its own
specification (which is F-1 restated from the documentation side).

**Contradictions found: 2.** Example: README designates `harness_api.md` as the canonical
JSON-contract doc and implies `tg find` has a documented contract, while that doc's command list had
no Find section — the doc asserted a pointer to content that did not exist.

---

## 10. Recommended Refactoring Plan

**Immediate (release blockers)**
1. **PYPI-SIZE-CAP.** Execute an approved retention policy. Irreversible; CEO-gated; policy chosen
   (A: frees 8.20 GB). Blocked only on an authenticated session — PyPI has no delete API.
   *Validation:* re-query the JSON API and assert the full 4-file set per release.
2. **F-5 scan silent-clean.** Fail closed on a missing scan root. *Validation:* the bidirectional
   RED above, swept across sibling surfaces.
3. **F-6 stacked-PR CI.** Enforce `baseRefName == main`. *Validation:* a stacked PR must FAIL the
   gate and a main-based PR must PASS it.

**Near-term**
4. **F-1 `repo_map.py`.** Measure the dependency graph, produce a design packet, isolate the
   confidence gate, then split. Depends on nothing; unblocks F-9's symbol-graph gap.
5. **F-4 find envelope.** Populate the routing fields or split the contract.
6. **F-3 machine-readable schemas** for the four unschema'd wire surfaces.
7. **Documentation:** write the sections named in §9.

**Longer-term**
8. `main.py` and `main.rs` splits (F-2).
9. Structural constraint (import-linter or equivalent) so split files cannot regrow — two previously
   split files are already back within 10% of the limit.
10. SARIF validation against the official schema (F-8).

---

## 11. Release Checklist

| # | Item | Owner | Status |
|---|---|---|---|
| 1 | Execute PyPI retention policy; verify 4-file set per release | _____ | **BLOCKED** — needs authenticated session |
| 2 | Re-publish the artifacts missing from v1.111.1 | _____ | Blocked on #1 |
| 3 | Fix `tg scan` missing-path fail-closed + sweep siblings | _____ | Open |
| 4 | Enforce `baseRefName == main` before merge | _____ | Open |
| 5 | Fix `tg find --json` routing fields | _____ | Open |
| 6 | `repo_map.py` design packet → split | _____ | Design in progress |
| 7 | Add schemas for MCP / ledger / checkpoint / evidence | _____ | Open |
| 8 | Write the missing documentation sections | _____ | Partially done |
| 9 | Make `test_w1c_sarif_version_disclosure` env-independent | _____ | Open |
| 10 | Retire allowlist entries as each split lands | _____ | 30 → 27 done |

---

## 12. Evidence and Limitations

**Commands and tools:** `scripts/file_size_budget.py --report`; `python -m pytest` (targeted and
governance selections); `ruff check` / `ruff format --preview --check`; `gh pr view --json
statusCheckRollup`; `gh api .../actions/jobs/<id>/logs`; the PyPI JSON API via `urllib`; `docker
build` + `docker run` of `scripts/dogfood/Dockerfile`; the installed `tg` binary for live contract
probes; `python ast` for symbol enumeration.

**Controls applied, because a number without a control is not evidence:**
- The file-size census reports per-category totals so an empty scan is distinguishable from a clean one.
- The PyPI measurement prints total file count and bytes, and states explicitly that the API call succeeded.
- Every schema-validation arm is paired with a mutation that must be rejected.
- Every CLI contract probe pairs the suspect command with a sibling known to behave correctly.
- Exit codes were read **unpiped**; a trailing `| head` reports the pipeline's status, not the tool's.
- The WSL probes were run with `MSYS_NO_PATHCONV=1`; without it Git Bash mangles `/mnt/c/...` into
  `C:/Program Files/Git/mnt/c/...` and manufactures a false `path_not_found`.

**Limitations, stated rather than papered over:**
- Findings marked "Unable to verify" in §1 were not executed and must not be read as passing.
- F-7 is a candidate defect identified by inspection, not reproduced.
- The GPU-lane test failure was localized to a whole-tree run but the polluting test was not
  identified; the root cause is stated as a hypothesis.
- Worktree-based test runs cannot exercise native/embedded paths (no compiled extension), so CI is
  the oracle for those; three tests failing in a worktree were confirmed to pass in CI.
- The census counts physical lines; it does not detect a file kept under a limit by compressed
  formatting. No such gaming was observed, but it was not systematically tested for.
