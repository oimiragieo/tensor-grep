# Code Audit Report — tensor-grep

**Date:** 2026-08-19 · **Base:** `origin/main` @ `9280992` · **Auditor:** orchestrated multi-agent
pass (4 parallel domain auditors + orchestrator verification) · **Standard:** CEO enterprise
criteria, 2026-08-19

---

## 1. Audit Scope

**Reviewed.** The whole git-tracked tree: 800 in-scope source files (Python `src/`, `scripts/`,
`benchmarks/`; Rust `rust_core/src/`; tests under `tests/` and `rust_core/tests/`), the data-contract
surface (`core/result.py`, `cli/rg_contract.py`, the MCP tool schemas, `.tg-registration.toml`,
`docs/CONTRACTS.md`), the documentation corpus (`README`, `AGENTS.md`, `docs/*`, 28 in-repo skill
folders), CI configuration, and repository/branch hygiene.

**Excluded from the size census**, and listed here per the standard's requirement to name exclusions:

| Excluded | Why |
|---|---|
| `.venv/`, `target/`, `__pycache__/`, `*.pyc` | build/runtime artifacts |
| `src/.tensor-grep/checkpoints/**` | point-in-time snapshots of the tool's own output, not authored source |
| `.orchestrator/`, `.claude/thinktank_*`, untracked scratch | untracked agent working files; CI never sees them |
| `docs/**`, `*.md` | prose, not code; assessed separately in §9 |
| Non-`.py`/`.rs`/`.schema.json` files | outside the standard's four categories |
| 54 sibling git worktrees | duplicate checkouts of the same tracked files |

Counting is over `git ls-files`, never a directory walk — with 54 worktrees plus untracked scratch
present, a walk would have inflated every number.

**Evidence unavailable / not verified.** Stated plainly rather than papered over:

- **No local Rust build or test.** This is a shared development box under an operator policy
  forbidding local `cargo build/test/clippy`. Every Rust claim here is static; Rust behaviour is
  CI-verified only.
- **The full test suite was not run.** 389 test files / ~182k LOC. Targeted regression surfaces were
  run (188 tests, all green); a whole-suite run is a CI responsibility.
- **~30 `subprocess.run` call sites in `main.py`** were spot-checked, not exhaustively classified.
- **117 `except Exception:` sites** were sampled, not individually adjudicated.
- **MCP path-confinement closure** across all ~50 tools was verified at 2 call sites, not all.
- **No product specification or upfront design document exists** to trace against. `docs/architecture.md`
  and `docs/CONTRACTS.md` are retrospective specs; `docs/design/` holds one planning doc. The
  traceability matrix in §6 is therefore built against *contracts and tests*, which do exist, rather
  than against a spec that does not. This is itself a finding (**DOC-003**).

---

## 2. Final Verdict

# ❌ FAIL

Triggered by exactly one mandatory rule: **a file-size limit is exceeded without a documented,
approved exception** — 35 files, up to 13.2× over.

The verdict is a governance failure, not a quality failure, and the distinction matters for the
release decision:

- **No unrefuted security finding at any severity above Low.** Eight classic vectors were tested and
  all eight came back already hardened.
- **One genuine HIGH contract defect (DC-001), fixed in this pass** and shipped in PR #1017.
- **Documentation is strong** — a junior analyst *can* rebuild the load-bearing features.

A single Fail bit cannot express that. The honest reading: **this codebase is safe to release and
not yet compliant with the size standard.** §10 separates the two so the release decision is not
held hostage to a multi-week refactor.

---

## 3. Executive Summary

**The dominant risk is not a bug — it is the absence of a mechanism.**

The size standard was enforced **nowhere**: no CI step, no test, no script (grep-verified, with a
positive control confirming the grep does find the repo's *other* governance gates, so the zero is
real). A standard with no mechanism is a wish, and 35 files drifted past it accordingly.

Worse, the drift is **active**. Across the 65 commits between the audit's start point and current
main:

| file | growth in 65 commits |
|---|---|
| `rust_core/src/index.rs` | **+1,069** |
| `tests/unit/test_cli_modes.py` | +362 |
| `src/tensor_grep/cli/main.py` | +342 |

Nothing objected to any of it. Absent a ratchet, any cleanup would be re-eroded.

**Every hand-scoped count of the violating population was wrong** — three times, in the same
direction. A first glob said 19; the gate's own census said 33; against real main it is **35**,
including two Rust files no earlier pass had seen. This is the repo's own documented
"population is the defect" failure mode, reproduced live during its own audit. The remediation
derives its census from `git ls-files` every run and trusts no written number, including its own.

**Strengths, stated because an audit that only finds fault is not calibrated.** The security posture
is genuinely good: flag injection (CWE-88), symlink-follow, atomic-write TOCTOU, zip-slip, pre-auth
DoS, unsafe deserialization, MCP bind/auth, and signing vacuousness were each probed and each
refuted, most with in-code comments citing the prior audit round that fixed them. The MCP contract
version is a literal with five pinning tests. `rg_contract.py` rows are executed against the real
`tg` binary. The evidence-signing path performs a real Ed25519 verify that can actually fail.
`docs/harness_api.md` documents ~35 JSON schemas including every MCP tool response.

**Release implication.** Oversized-but-hardened is maintainability debt, not a security or
correctness barrier. The blocking question is honesty of claim, not safety of code: with the ratchet
landed, the project can say *"35 grandfathered, ceiling pinned, monotonic decrease enforced"* — it
cannot yet say *"enterprise size-compliant."*

---

## 4. Compliance Scorecard

| Control area | Status | Severity | Evidence | Required action |
|---|---|---|---|---|
| **File sizing** | ❌ FAIL | High | 35 files over budget; max 19,733 vs 1,500 (13.2×). `scripts/file_size_budget.py --report` | Ratchet landed (#1017); burn down in waves (§10) |
| **Size enforcement mechanism** | ✅ FIXED | — | Was absent (grep + positive control). Now `scripts/file_size_budget.py` + CI step + 28 mutation-controlled tests | Keep the allowlist monotonically shrinking |
| **Spec alignment** | ⚠️ UNABLE TO VERIFY | Medium | No product spec or upfront design doc exists (`docs/design/` = 1 planning doc) | DOC-003: adopt a lightweight design-doc convention |
| **Design alignment** | ✅ PASS | — | `docs/architecture.md` matches code; routing order cited to `routing.rs:222`, front door to `main.rs:1238`, both verified | — |
| **Data contracts** | ⚠️ PARTIAL | High→fixed | DC-001 fixed; DC-002 (producer/consumer parity untested) open | DC-002: add a live two-producer diff test |
| **Implementation quality** | ✅ PASS with debt | Medium | Fail-closed contract intact; no mutable-default args; timeouts present at sampled sites | Module decomposition (§10) |
| **Unit tests** | ✅ PASS with gaps | Medium | 389 files; duplicate-test-name class **absent** (verified). TEST-005: `tests/eval/` never invoked by CI | Wire the eval gate into CI |
| **Mocks & fixtures** | ✅ PASS | Low | `tests/fixtures/` < 3k LOC total; **no secrets** (scanned); no oversized shared fixture | — |
| **Security** | ✅ PASS | — | 8 vectors probed, 8 refuted, each with citation | — |
| **Operations / CI** | ✅ FIXED | Medium | TEST-005: the eval gate had no CI invocation path — now a gating step. TEST-004 **refuted**, the gpu hook is live | Keep the eval step gating |
| **Documentation** | ✅ PASS with drift | Medium | 16/20 sections present or partial; DOC-001/002 fixed this pass | §9 gaps |
| **Repo hygiene** | ❌ FAIL | Medium | 54 worktrees, ~140 branches, 1 open PR marked "do not merge" | §10 reconciliation |

---

## 5. File-Size Validation

**35 files over budget.** Full machine-generated census: `python scripts/file_size_budget.py --report`.
Pinned baselines: `scripts/file_size_allowlist.json`.

**No line-count manipulation.** Average line length is **37–43 characters** across every giant
against a `line-length = 100` ruff limit, and `ruff format --check` is clean. These files are
honestly formatted and genuinely enormous — there is no compression arbitrage to unwind.

### Worst offenders

| File | Category | Lines | Limit | × over | Recommended action |
|---|---|---|---|---|---|
| `src/tensor_grep/cli/repo_map.py` | core | 19,733 | 1,500 | 13.2 | Package-per-language; 482 top-level symbols |
| `src/tensor_grep/cli/main.py` | core | 17,948 | 1,500 | 12.0 | Package-per-command-group; ~50 Typer commands |
| `tests/unit/test_cli_modes.py` | test | 17,183 | 2,000 | 8.6 | Split by subject (9 files, §8) |
| `rust_core/src/main.rs` | core | 15,094 | 1,500 | 10.1 | **Defer — CI-only verification** |
| `tests/unit/test_benchmark_scripts.py` | test | 10,689 | 2,000 | 5.3 | Split by benchmark family |
| `tests/unit/test_mcp_server.py` | test | 9,710 | 2,000 | 4.9 | Split by tool domain (5 files, §8) |
| `src/tensor_grep/cli/mcp_server.py` | core | 7,963 | 1,500 | 5.3 | Extract `tg_ruleset_scan` (~1,800 lines) + envelope helpers |
| `tests/unit/test_release_assets_validation.py` | test | 5,258 | 2,000 | 2.6 | Split by governance domain |
| `rust_core/src/gpu_native.rs` | core | 4,952 | 1,500 | 3.3 | Defer (Rust) |
| `rust_core/tests/test_schema_compat.rs` | test | 4,412 | 2,000 | 2.2 | Defer (Rust) |
| `scripts/validate_release_assets.py` | core | 3,780 | 1,500 | 2.5 | Split by asset class |
| `src/tensor_grep/cli/agent_capsule.py` | core | 3,652 | 1,500 | 2.4 | Split |
| `rust_core/src/native_search.rs` | core | 3,563 | 1,500 | 2.4 | Defer (Rust) |
| `benchmarks/run_gpu_native_benchmarks.py` | core | 3,364 | 1,500 | 2.2 | Split |
| `tests/unit/test_session_cli.py` | test | 3,337 | 2,000 | 1.7 | Split (3 files, §8) |

Remaining 20 entries — `rust_core/tests/test_routing.rs` (2,995) through
`src/tensor_grep/cli/checkpoint_store.py` (1,566) — are in the allowlist.

### Within 10% of a limit (watch list)

| File | Category | Lines | Limit | Headroom |
|---|---|---|---|---|
| `src/tensor_grep/cli/lsp_external_provider.py` | core | 1,452 | 1,500 | 48 |
| `src/tensor_grep/cli/ledger_store.py` | core | 1,417 | 1,500 | 83 |
| `src/tensor_grep/cli/codemap.py` | core | 1,408 | 1,500 | 92 |
| `tests/unit/test_validation_commands.py` | test | 1,814 | 2,000 | 186 |

**Note on the limit.** The instruction specified core ≤1500; the embedded audit template specified
≤1000. Gated at **1500**. Under a 1000-line limit, **11 additional files** would violate
(`lsp_external_provider.py`, `ledger_store.py`, `codemap.py`, `lang_cpp.py`, `lang_php.py`,
`orient_capsule.py`, `audit_manifest.py`, `rule_packs.py`, `cpu_backend.py`, `apply_policy.py`,
`lsp_provider_setup.py`), taking the total to 46. Tightening is a one-line change to
`CORE_LIMIT` plus an allowlist regeneration.

---

## 6. Requirements Traceability Matrix

No product specification exists (see §1). The matrix therefore traces **published contracts** —
the things that actually constrain behaviour — rather than a spec that does not exist.

| Requirement / contract | Summary | Implementation | Test | Docs | Status |
|---|---|---|---|---|---|
| Completeness contract P1–P3 | Never silently return a partial result as complete | `core/result.py` `result_incomplete` / `incomplete_reason` | `test_silent_loss_census_ratchet.py`, `test_native_walk_error_ratchet.py` | `CONTRACTS.md` §0 | ✅ Compliant |
| Backend fail-closed | Backends raise `BackendExecutionError`; never empty, never a silent engine swap | `backends/*.py` | `test_cpu_backend.py`, `test_ast_backend.py`, `TestCuDFBackendFailClosed` | `architecture.md` | ✅ Compliant |
| JSON envelope schema version | Every `--json` payload carries `version` / `schema_version` | `core/result.py::JSON_OUTPUT_VERSION` | `test_json_output_version_pin.py` (new) | `harness_api.md` | ✅ **Fixed this pass** (was DC-001) |
| MCP contract version | Wire-shape change requires a version bump | `mcp_server.py::_TG_MCP_SERVER_CONTRACT_VERSION` | 5 pinning tests | `CONTRACTS.md` | ✅ Compliant |
| Two-front-door registration | A new command/flag registers at all 4 sites | `core/registration_check.py` | CI step `ci.yml:337-340` | `AGENTS.md` | ✅ Compliant |
| rg parity | Documented rg flags behave identically | `cli/rg_contract.py` | `test_rg_contract_row_matches_tg` — executes the **real binary** per row | `tool_comparison.md` | ✅ Compliant |
| Symbol-graph coverage | Tier split derived live, never hand-written | `repo_map._symbol_navigation_descriptor()` | `test_skill_library_drift.py` | README, `tool_comparison.md`, `ENGINEER_ONBOARDING.md` | ✅ **Fixed this pass** (was DOC-001/002) |
| Rust↔Python envelope parity | Both engines emit the same envelope | `rust_core/src/main.rs` + Python sidecar | ⚠️ **both validate against the same static fixtures** | `harness_api.md` | ⚠️ **Partial — DC-002** |
| Path confinement (MCP) | No tool escapes its root | `_confine_read_path` / `_confine_write_path` | `test_mcp_server.py` ratchet only | — | ⚠️ Single-witness |
| Agent-accuracy golden set | 15-case `tg prepare` regression gate | `tests/eval/` | ⚠️ **no CI invocation path found** | `ci.yml:435-440` comment | ❌ **Non-compliant — TEST-005** |
| Enterprise file-size standard | Four category limits | `scripts/file_size_budget.py` | `test_file_size_budget.py` (28 tests) | this report | ⚠️ **Enforced, not yet met** |

---

## 7. Detailed Findings

### DC-001 — Schema version silently stale in every wheel install · **HIGH** · ✅ FIXED

- **File:** `src/tensor_grep/cli/main.py:1938`, `src/tensor_grep/cli/audit_manifest.py:38`
- **Evidence:** `_json_output_version()` scraped `JSON_OUTPUT_VERSION` from
  `rust_core/src/main.rs` via `Path(__file__).resolve().parents[3]`. Reproduced both layouts:
  dev checkout → repo root (`rust_core/` present); wheel → the directory above `site-packages`
  (absent). `pyproject`'s `[tool.maturin]` include list does not ship `rust_core/`. The `OSError`
  handler returned a hardcoded `1`.
- **Risk:** Latent only because the Rust constant *is* `1`. On the first bump, every published
  install keeps stamping the old `version`/`schema_version` into every `--json` envelope, silently.
  Wrong, not broken — the class that costs the most to discover.
- **Remediation:** shipped literal `core.result.JSON_OUTPUT_VERSION`, cross-pinned by test.
- **Closure:** RED arm observed at both sites (`assert 1 == 4242`); GREEN after; 153 regression
  tests pass. *A naive "works in a wheel" test would have been vacuous — fallback and correct value
  are both `1` today — so the arm uses a sentinel.*

### SIZE-001 — 35 files exceed mandatory limits · **HIGH** · ⚠️ RATCHETED, NOT RESOLVED

- **Evidence:** `python scripts/file_size_budget.py --report`; §5.
- **Risk:** `repo_map.py` alone holds 482 top-level symbols and has 290 `monkeypatch.setattr`
  attribute paths pointed into it. Review is impractical, blast radius is unbounded, and onboarding
  cost is severe.
- **Remediation:** ratchet landed; staged waves in §10.

### HYG-001 — 54 worktrees / ~140 branches, mostly stale · **MEDIUM** · OPEN

- **Evidence:** `git worktree list`; per-branch `git diff origin/main <branch> -- <touched files>`.
- **Measured:** of 11 `audit/*` branches carrying apparently-unlanded fixes, **4 are already
  squash-merged** (M1, M3, M7, M14 — touched files byte-identical to main; positive control: H6,
  known-landed, reads the same). The remainder are **behind**, not ahead: merging them would apply
  **345 / 1,003 / 503 line deletions** to main.
- **Risk:** the instruction "merge all worktrees" executed literally would be a **large-scale
  regression**, not a consolidation.
- **Remediation:** §10 reconciliation. Blind merge is not defensible; this is measured, not asserted.

### TEST-005 — Agent-accuracy golden-set gate never runs in CI · **MEDIUM** · OPEN

- **Evidence:** `.github/workflows/ci.yml:435-440` documents `pytest tests/eval -m eval`; grepping
  every workflow for a `run:` line invoking `tests/eval` finds **none** — all three occurrences are
  inside that comment. `ci.yml:446` excludes it via `-m "not eval"` and nothing adds it back.
- **Risk:** a 15-case ranking/capsule regression gate that reads as covered and executes never. A
  ranking regression in `edit_plan`/`agent_capsule` would ship unnoticed.
- **Remediation:** add an explicit CI step, or delete the gate and the comment. A gate nobody runs
  is worse than no gate: it is believed.

### DC-002 — Producer/consumer parity is untested · **MEDIUM** · OPEN

- **Evidence:** `rust_core/tests/test_schema_compat.rs:7-45` deserializes committed
  `docs/examples/*.json`. `tests/unit/test_harness_api_docs.py` asserts the same static files.
  Neither runs a producer.
- **Risk:** Rust and Python are each checked against the same hand-maintained fixtures. If both
  drift the same way, or the fixtures go stale, nothing catches it — **two methods sharing an
  assumption are one method run twice.**
- **Remediation:** run the native binary and the Python sidecar on one fixture repo and diff the
  envelopes.

### TEST-004 — `pytest.mark.gpu` skip hook is dead code · ❌ **REFUTED 2026-08-19**

**The finding was wrong, and the way it was wrong is the lesson.**

- **Claimed:** `tests/conftest.py` implements a `pytest_collection_modifyitems` hook skipping
  `gpu`-keyword items without CUDA, but a repo-wide grep for `pytest.mark.gpu` returned **zero**
  usages — so the safety net was inert.
- **Reality:** the marker is applied at (at least) `tests/integration/test_cudf_read_text.py:3`,
  `test_gpu_memory.py:5`, and `test_pipeline_e2e.py:3` — all via
  `pytestmark = [pytest.mark.gpu, pytest.mark.integration]`. The originating grep matched only the
  **decorator** form `@pytest.mark.gpu` and could never have matched the module-level list form.
- **Mechanism of the error:** a zero from a grep is UNRESOLVED, not ABSENT — the guard was present
  in a shape the search did not cover. This repo has a named law for exactly this, and the audit
  reproduced it anyway.
- **Consequence had it been actioned:** "removing vestigial code" would have deleted a live skip
  gate, and every GPU test would then have run unconditionally on non-CUDA runners.
- **Disposition:** no change. Recorded rather than deleted, so the refutation survives.

*A refutation is a complete deliverable. This one is kept prominently because it was found by
re-verifying a sub-agent's finding against the tree before acting on it — which is the only step
that separates a wrong finding from a wrong fix.*

### DOC-001 / DOC-002 — Stale language-tier claims · **MEDIUM** · ✅ FIXED

- **Files:** `README.md:87`, `docs/ENGINEER_ONBOARDING.md:35-36`, `docs/tool_comparison.md:144-146`
- **Evidence:** docs claimed 6+4 / 9+1 / 8+2 splits; product returns **10 parser-backed / 0
  foundational**. One skill file carried **four mutually-contradicting values** (6+4, 5+5, 8+2, 9+1).
  `ENGINEER_ONBOARDING.md` printed the correct descriptor *directly beneath* its wrong hand-counted
  table, under a heading reading "NEVER HAND-COUNT THIS".
- **Notable:** `tool_comparison.md` was **understating** tg against a competitor (8 vs 10 deep-tier).
  A public number wrong in the direction that costs you. Corrected *with* the caveat that our ten are
  in-file only — correcting upward without stating the limit is the mirror error.
- **Remediation:** all three now carry the derivation one-liner instead of a hand count. Dated
  receipts under `docs/audits/` and `BACKLOG.md` deliberately untouched.

### TEST-002 — Tautological range assertions · **LOW** · OPEN (unconfirmed)

`assert 0.0 <= x <= 1.0` at 11 sites. Confirmed **present**; not confirmed **decorative** — that
requires checking whether each producing scorer clamps. Reported honestly as a lead, not a defect.
*The repo already self-corrected one instance of this class at
`test_context_tests_source_limit_and_deadline.py:770-778`.*

### TEST-006 — Absolute wall-clock assertions · **LOW** · ⚖️ **INSPECTED — no change warranted**

Absolute bounds at `test_cli_deadline_coverage_gaps.py:716` (`<0.6`),
`test_symbol_daemon_autostart.py:681` (`<2.5`), and ~8 others. The audit flagged them as
candidates for the same-run-ratio treatment the repo already applied to
`test_index_lock_concurrency.py` after two flakes.

**On inspection, the two tightest — the ones most likely to flake — should stay as they are.**
Both are documented, not naive:

| site | bound | against | margin |
|---|---|---|---|
| `test_cli_deadline_coverage_gaps.py:716` | `<0.6` | 0.15s budget; ~0.8s if the deadline is dropped | 4× the budget, 25% under the failure signal |
| `test_symbol_daemon_autostart.py:681` | `<2.5` | `_DAEMON_START_TIMEOUT_SECONDS` = 5.0s | exactly half the blocking threshold |

Applying this repo's own "classify each match, do not sweep" rule — *does some other assertion
already prove what this one claims?* — the answer is no in both cases. Nothing else shows the
deadline was threaded into the warm scan; and `len(spawn_calls) == 1` proves a spawn happened, not
that it was **non-blocking**. Each timing assert is the **sole proof of its property**, and each
bound was derived from the real failure signal rather than picked.

Converting them to same-run ratios would require manufacturing a baseline arm (an unbounded run)
that does not exist in either test. That is added machinery and added risk for no gain.

**Disposition: no change.** The remaining ~8 looser bounds (`<4.0`, `<10.0`, `<40.0`) are lower
flake risk still and were not individually adjudicated — stated rather than swept.

### REFUTED (reported per the standard — a refutation is a complete deliverable)

| Suspected | Verdict |
|---|---|
| CWE-88 flag injection in native argv | **Refuted** — `--` sentinel present and correctly ordered (`bootstrap.py:1170-1172`); list-argv throughout |
| MCP pre-auth network exposure | **Refuted** — stdio transport only; no socket bind (positive control: zero hits for any HTTP/SSE path) |
| Session-daemon pre-auth DoS | **Refuted** — `127.0.0.1` only, 1 MiB pre-auth cap, 30 s timeout, `hmac.compare_digest`, fails closed without a token |
| Evidence signing vacuous | **Refuted** — real Ed25519 `verify()`; can and does fail |
| Zip-slip in archive extraction | **Refuted** — member+symlink targets validated under root, `filter="data"`, checksum before extract |
| Symlink-follow disclosure | **Refuted** — only hit is explicitly `followlinks=False` |
| Atomic-write permission window | **Refuted** — `os.open(O_CREAT\|O_EXCL, mode)` at all sensitive sites |
| Unsafe deserialization | **Refuted** — all `pickle`/`yaml.load`/`eval` hits are *detection rules* in `rule_packs.py`, not executed code |
| Duplicate test names shadowing coverage | **Refuted** — zero hits repo-wide |
| Secrets in fixtures | **Refuted** — zero hits |
| Mutable default arguments | **Refuted** (weak negative — standard pattern, large file set) |
| `pytest.mark.gpu` hook is dead code | **Refuted on re-verification** — applied via `pytestmark = [...]` at 3+ integration files; the originating grep matched only `@pytest.mark.gpu`. This one was a *finding of this audit*, caught before it became a wrong fix |

---

## 8. Test and Fixture Assessment

**Completeness.** Strong where it matters. `BackendExecutionError` appears in 26 test files,
`resolve_native_tg_binary` in 14, `SearchConfig` in 39. Security-critical confinement primitives are
covered by a parametrized ratchet — though by a **single** file, so a regression in the shared helper
is caught only if that one ratchet stays comprehensive.

**Determinism risk.** Concentrated in the absolute wall-clock assertions (TEST-006). The repo has
already been bitten twice here and has a correct pattern to copy
(`elapsed < max(baseline * 6.0, 8.0)`).

**Fixtures.** Genuinely good. `tests/fixtures/` is under 3k LOC across 4 directories; no oversized
shared fixture; no secrets; `tests/conftest.py` is 131 lines with a documented rationale citing a
measured incident ("113 failures across 9 files, plus vacuous passers") behind its current design.

**Contract-test gap.** DC-002 above.

### Split plan for the five largest test files

| Current | Proposed | Shared helpers to extract |
|---|---|---|
| `test_cli_modes.py` (17,183) | 9 files: `_scan_guards`, `_doctor_lsp`, `_symbol_commands`, `_agent_capsule`, `_rg_passthrough`, `_ast_scan`, `_gpu_routing`, `_upgrade` | `_FakeBackend`/`_FakePipeline`/`_FakeGpuPipeline`/`_FakeRipgrepBackend` (:95-424), `_patch_cli_dependencies` (:380), `_make_stub_file_repo` (:1067) → a package `conftest.py` |
| `test_mcp_server.py` (9,710) | 5 files: `_rewrite_audit`, `_path_confinement`, `_context_session`, `_symbol_navigation`, `_meta_dispatch` | `_StubScanner` (:9622); **the confinement-ratchet case tables (:4140-4400, :8156-8345) must stay together or be re-exported** — splitting them silently shrinks the security case set |
| `test_benchmark_scripts.py` (10,689) | 5 files by benchmark family | ⚠️ top-of-file helper enumeration **not yet done** — disclosed gap; run the grep before splitting |
| `test_release_assets_validation.py` (5,258) | 4 files by governance domain | none — pure governance over static YAML/MD |
| `test_session_cli.py` (3,337) | 3 files: `_core`, `_cache`, `_daemon` | none found at module top; verify before splitting |

---

## 9. Documentation Rebuild Assessment

**Verdict: a junior analyst CAN rebuild the load-bearing features** — with source access alongside,
which is the repo's deliberate policy ("cite the symbol, name the file, tell you to grep" rather than
duplicate code into docs that drift).

16 of 20 required sections are present or partial. Genuinely strong: architecture and component
responsibilities, repo structure, data models and contracts, API/command surface
(`harness_api.md`, ~35 JSON schema sections including every MCP tool response), control/data flow,
error handling, local setup (`ENGINEER_ONBOARDING.md` §2 has exact copy-paste commands), and
build/test/deploy/rollback.

### Gaps requiring specific content

| # | Section | Status | Exactly what must be added |
|---|---|---|---|
| 19 | Step-by-step rebuild instructions | **ABSENT** | A "build tg from zero" tutorial. Docs teach *where* and *why*, never a from-scratch sequence. |
| 3 | Spec / design references | **PARTIAL** (**DOC-003**) | No upfront design-doc trail; `docs/design/` holds one planning doc. Material decisions live only in commit messages and code comments. |
| 12 | AuthN/AuthZ | **PARTIAL** | The session-daemon HMAC protocol — 0600 token file, `compare_digest`, auth-before-dispatch ordering, fail-closed-without-token — exists **only in code comments** in `session_daemon.py`. A security-conscious rebuild needs it in a doc. |
| 9 | Algorithms | **PARTIAL** | Exact RRF fusion weights, the `TG_FIND_DENSE_WEIGHT` default and gating rule, and the whitespace NL-vs-literal classifier's decision rule are code-only. |
| 20 | Rebuild verification checklist | **PARTIAL** | `RELEASE_CHECKLIST.md` verifies a *release*; nothing verifies that a *rebuild* is behaviourally equivalent. |
| 15 | Data migration | **PARTIAL** | No doc on `.tensor-grep/` cache/session schema compatibility across releases. |

### Per-feature verdicts

- **Bootstrap front door + native routing** — rebuildable. Exact functions and `file:line` given
  (`main_inner` `main.rs:1238`, `route_search` `routing.rs:222`, the 10-step guard chain). Missing:
  `SearchRoutingConfig`/`BackendSelection` field lists, sidecar subprocess protocol specifics.
- **`tg find` hybrid semantic search** — partial. The *what* and *why* are documented precisely
  (BM25 + CPU dense, RRF, checksum-pinned `potion-code-16M`, visible BM25-only fallback). The
  tunable constants are not.
- **MCP server** — rebuildable, the strongest of the three. Gap is the auth protocol above.

---

## 10. Recommended Refactoring Plan

Council-approved (7-seat thinktank, 7/7 verdicts: 6× mechanism-first-staged-waves, 1× the same plus
bounded exceptions for registry-shaped files).

**Why not a big bang.** A ~90-module decomposition immediately before a public release is the single
likeliest way to break the release the standard exists to protect. The measured reasons:
**658 `monkeypatch.setattr` attribute paths** into the three largest modules (290 / 268 / 100), **562
importer files** for `main.py`, and a Rust half that **cannot be verified locally** on a shared box —
every Rust wave is a CI round-trip with no local red arm.

### Immediate release blockers — ✅ DONE (PR #1017)

| Action | Files | Outcome | Validation |
|---|---|---|---|
| Fix DC-001 | `main.py`, `audit_manifest.py`, `core/result.py` | Wheels report the true schema version | RED arm seen at both sites; 153 regression tests |
| Land the size ratchet | `scripts/file_size_budget.py`, allowlist, CI | Cannot get worse; every wave provably shrinks it | 4 mutation controls + end-to-end perturbation |
| Fix doc drift | README, `tool_comparison.md`, `ENGINEER_ONBOARDING.md` | Public docs match the product | 71 docs-governance tests |
| Lint + format | 4 files | `ruff check` clean on tracked scope | CI-exact commands at the pinned version |

### Near-term corrections (pre- or post-release, independent of the split)

| Priority | Action | Files | Validation |
|---|---|---|---|
| ~~P1~~ | ~~Wire `tests/eval` into CI (TEST-005)~~ | `ci.yml` | **Done** — a separate gating step *after* the unit run (so it cannot mask them, which is what the original exclusion protected), single matrix leg, 3 tests / 138s verified passing first |
| ~~P1~~ | ~~Live two-producer envelope diff (DC-002)~~ | — | **Done** — `test_producer_envelope_parity.py`, gated in `native-build-smoke` with `TG_PARITY_REQUIRE=1` so a missing binary fails rather than skips. It found DC-003 by hand before it was even automated. |
| ~~P2~~ | ~~Resolve `pytest.mark.gpu` dead hook (TEST-004)~~ | — | **REFUTED** — the marker is applied via `pytestmark = [...]`; the originating grep matched only the decorator form. Actioning it would have deleted a live skip gate |
| P2 | Document the daemon HMAC protocol (§9 #12) | new doc | — |
| ~~P3~~ | ~~Convert absolute wall-clock asserts (TEST-006)~~ | — | **Inspected, no change warranted** — the two tightest are each the sole proof of their property, with bounds derived from the real failure signal. See TEST-006. |
| P3 | Classify the 11 tautological range asserts (TEST-002) | 11 sites | Check each scorer's clamp |

### Repository reconciliation (HYG-001) — replaces "merge all worktrees"

Blind merging is **measurably** wrong: the candidate branches are *behind* main, and merging them
would delete 345–1,003 lines each. The faithful reading of the instruction is *consolidate so the
release ships from one clean main*:

1. **Delete** branches whose touched files are byte-identical to main (M1, M3, M7, M14 confirmed;
   verify each with `gh pr view`, since squash-merge defeats `git branch --merged`).
2. **Rebase-and-cherry-pick**, never merge, anything genuinely unique from H1/H2/M8/M10/M13/M16/M17.
3. **Leave untouched** any worktree with a live agent — probe liveness first.
4. **Escalate one decision only:** PR #966, the draft explicitly titled *"not GREEN, do not merge"*.
   That is the single item requiring a human call; everything else is reversible reconciliation.

### Longer-term: the split waves (lowest coupling first)

Every wave: `sys.modules` alias shim + **a generated test asserting each monkeypatched attribute path
and its production call site resolve to the same binding**, seen RED against a deliberately broken
shim before it is trusted. One wave in flight at a time; full suite on the merged tree.

| Wave | Targets | Monkeypatch exposure | Notes |
|---|---|---|---|
| 1 | `checkpoint_store`, `session_store`, `session_daemon`, `ast_workflows`, `bootstrap` | low | Safest start; each is 1.0–1.4× over |
| 2 | `scripts/validate_release_assets.py`, the two `benchmarks/` files | none (not imported by product) | Zero product risk |
| 3 | `agent_capsule.py` | 113 importers | — |
| 4 | `mcp_server.py` | 100 | Extract `tg_ruleset_scan` (~1,800 lines) + envelope helpers first |
| 5 | Test files | n/a | Split plan in §8; independent of source waves |
| 6 | `main.py` | 268 | Command-shaped — one Typer group per module |
| 7 | `repo_map.py` | 290 | Package-per-language, mirroring the existing `lang_go.py` pattern |
| 8 | **Rust** (`main.rs`, `gpu_native.rs`, `index.rs`, …) | n/a | **Last.** CI-only verification; batch to respect the shared-box rule |

**What the alias shim does *not* preserve** — the trap that will break a wave if ignored: **early
binding.** If a new submodule does `from .helpers import X` and calls `X()`, patching
`repo_map.X` no longer affects it. Every intra-package call to a monkeypatched symbol must go through
late attribute lookup. Module-level caches and registries must live in exactly one module and be
*aliased*, never copied — `import *` re-export drops underscore names and copies bindings.

*(This fix demonstrates the technique: `_json_output_version()` binds the **module**, not the value,
precisely so the constant stays patchable — `from result import JSON_OUTPUT_VERSION` would have made
the red arm impossible to write.)*

---

## 11. Release Checklist

| # | Item | Owner | Status |
|---|---|---|---|
| 1 | DC-001 fixed and cross-pinned | — | ✅ Done (#1017) |
| 2 | File-size ratchet in CI, mutation-controlled | — | ✅ Done (#1017) |
| 3 | DOC-001/002 language-tier drift corrected | — | ✅ Done (#1017) |
| 4 | `ruff check` clean on tracked scope | — | ✅ Done |
| 5 | PR #1017 green on CI | _owner_ | ⏳ In flight |
| 6 | TEST-005: `tests/eval` wired into CI as a gating step | — | ✅ Done |
| 7 | DC-002: live two-producer envelope diff | — | ✅ Done |
| 8 | TEST-004 / TEST-006 | — | ❌ Refuted / inspected — no action |
| 9 | Daemon HMAC protocol documented | _owner_ | ☐ Open |
| 10 | Branch/worktree reconciliation per §10 | _owner_ | ☐ Open |
| 11 | **CEO decision on PR #966** ("not GREEN, do not merge") | **CEO** | ☐ Escalated |
| 12 | Size-compliance claim **withheld** until the allowlist reaches 0 | _owner_ | ☐ Standing |

**Item 12 is the honesty gate.** Installing a ratchet is not achieving compliance. Until the
allowlist is empty or a bounded exception is granted, the accurate public statement is *"35
grandfathered, ceiling pinned, monotonic decrease enforced"* — never *"enterprise size-compliant."*

---

## 12. Evidence and Limitations

**Commands and tools.**
`git ls-files` (census population), `wc -l` (physical lines), `python scripts/file_size_budget.py
--report`, `uv run ruff check .` and `uv run ruff format --check --preview .` at the **pinned
0.15.20** matching CI, `python -m pytest` (188 targeted tests), `gh run list` / `gh pr checks`,
`git diff origin/main <branch> -- <touched files>` (branch reconciliation),
`repo_map._symbol_navigation_descriptor()` (language tiers), a 7-seat thinktank council.

**Method.** Four parallel domain auditors (contracts, documentation, security+quality, tests+fixtures)
under an explicit refute-by-default instruction, each required to run a positive control before
reporting any zero. Findings were then re-verified by the orchestrator against the live tree rather
than accepted as reported.

**Instrument failures encountered during this audit** — recorded because they are the most
transferable output:

1. **The violation population was miscounted three times** (19 → 33 → 35), always low, always by
   hand-scoped globbing. Fixed structurally: the gate derives its own census every run.
2. **A substring grep falsely cleared two dead imports.** `grep -c 're\.'` returned 11 and read as
   "still used"; it was matching *"more."*, *"future."*. Ruff's AST analysis was right. **Count AST
   nodes, not substrings.**
3. **"137 files need reformatting" was pure instrument error** — local unpinned ruff 0.15.22 without
   `--preview`. CI's exact invocation at the pinned 0.15.20 reports a different set entirely.
4. **17 remaining format failures are a Windows CRLF artifact**, proven by a paired control on
   byte-identical content (LF → "already formatted", CRLF → "would be reformatted") and by CI being
   green on Linux. Deliberately **not** "fixed" — that would have broken CI.
5. **The allowlist was first baselined against a 65-commit-stale tree.** Running it against real main
   surfaced 2 unseen violations and 14 grown files. Caught only because the ratchet was re-run on the
   PR's actual base.
6. **A marker-file premise check returned `branch=0` for three audit branches** because the file path
   was wrong. Reported as UNRESOLVED, not as absent — a grep zero is not evidence.
7. **A finding of this audit was itself wrong** (TEST-004). A sub-agent grepped `@pytest.mark.gpu`,
   found zero, and concluded the skip hook was dead code. The marker is applied via
   `pytestmark = [pytest.mark.gpu, ...]` — the list form the decorator pattern cannot match.
   Acting on it would have deleted a live gate. Caught only by re-verifying the finding against
   the tree before touching anything, which is the single step separating a wrong finding from a
   wrong fix.

**The pattern across all seven: the instrument failed, not the subject.** Five were searches whose
zero meant "did not look there" rather than "not present"; two were toolchain or staleness
artifacts. None was caught by re-reading code — reading code confirms what the code says, and in
every one of these the code and the measurement disagreed. Each was caught by a control.

**Not completed.** Everything in §1's "evidence unavailable" list, plus: the `.claude/skills/`
corpus was sampled rather than audited (one skill file is known to carry four contradicting values of
one fact); `docs/CONTRACTS.md` §6-§11 were not line-by-line verified against implementation; and the
whole-suite pytest run is deferred to CI.

**Calibration note.** The security sweep returned 8 refutations and 0 confirmations. Published SAST
false-positive rates run 30–70%, so a sweep confirming everything would be the suspicious result —
but so is one confirming nothing. The refutations are individually cited and each names the specific
guard found; they are offered as evidence of prior hardening, not as proof of absence. The residual
risk is concentrated in the two populations explicitly **not** exhaustively classified (the 117
`except Exception:` sites and ~30 `subprocess.run` call sites), which warrant a dedicated pass.
