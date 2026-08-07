# Agentic CLI + Deep-Dive Audit Campaign

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development + superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. Every item is a PR-sized slice through `tensor-grep-change-control` (draft-PR-only, one-item-per-iteration, benchmark gates).

**Goal:** Land, in ranked order, (a) the verified deep-dive-audit fixes and (b) the decision-gate-respecting agentic-feature set, each completed 100% with TDD + real gates, zero backlog at the end. One ranked item per iteration (the orchestrator loop).

**Date / base:** 2026-08-06, HEAD `999dac8` (v1.109.0+1), working tree dirty only in `.claude/skills/*.md` + untracked `.claude/thinktank_f7task11.md` (do NOT stage these).

**Architecture:** Python CLI (`cli/`) + Rust native (`rust_core/`) + MCP + session daemon. Audit findings verified against this HEAD by an independent read-only census (every H/M verdict recorded below); research features come from a 2026 competitive/arXiv scan (Codebase-Memory 2603.27277, RANGER 2509.25257, ARISE 2605.03117, LARGER 2605.16352, SWE-Explore 2606.07297, Gortex, Serena, ABCoder, Ariadne, grepika, Probe).

**Tech Stack:** Python 3.11+, tree-sitter, ast-grep wrapper, ripgrep, Rust (pinned 1.96.0), uv, ruff (--preview), mypy, pytest.

---

## Gate policy (in-scope / blocked) — binds every item

| Decision | Status |
|---|---|
| GPU publish / promotion (CEO #169) | **BLOCKED** — no GPU build/fix ships public; H6 CuDF is a local correctness fix only, no promo claim. |
| Public benchmark publication (#72) | **CEO-GATED** — benchmark harnesses may be BUILT and run privately; publishing numbers needs #72. |
| Merge | **Draft-PR-only** autonomy; one-merge-per-tick; never auto-merge. |
| Rust changes | Author + Rust unit tests + CI as oracle (CPU-safe: no heavy local cargo unless targeted/cold-cheap); Python changes gated locally. |
| Env / config | New flags default-OFF / experimental unless proven (AGENTS.md). |
| Test surface | Any change to a pinned string/behavior updates the pin in the SAME PR (A6/A19/Form-10 family). |

---

## Part A — Verified audit ledger (read-only census verdicts @ HEAD 999dac8)

Verdicts: VERIFIED / PARTIAL (real, description imprecise) / WRONG / STALE. All 6 H's verified. No finding STALE.

| id | verdict | seam (file:line) | one-liner |
|---|---|---|---|
| H1 | VERIFIED | `backend_cpu.rs:339-353,371-385,793-807,829-837`; `rust_backend.py:314-329`; twin `native_search.rs:2596-2600` | backend_cpu swallows file/walk errors → clean 0-match (Fail-Closed violation); native twin tracks walk_errors. |
| H2 | PARTIAL | `main.rs:8285-8322` guard `!json&&!ndjson`; `native_search.rs:187-246`; `-o` IS mapped `main.rs:8445` | count-matches/files-with-matches/without-match dropped on native `--json` route (self-doc `OUT_OF_SCOPE_GAP`); **`-o` honored**, files-* not reachable via Python launcher. |
| H3 | VERIFIED | `python_sidecar.rs:757-767`; banned in `rg_passthrough.rs:729-736` | Cmd/BatBadBut re-fired: `cmd /d /c` wraps the RESOLVED python `.cmd/.bat` shim path, user args appended after it (injection surface = attacker-writable shim path) — twin of the rg ban (A27/A39). |
| H4 | VERIFIED | `repo_map.py:13773-13781`; `agent_capsule.py:560,2256-2267,3078-3109`; `prepare_service.py:432` | `edit_plan_seed.confidence.overall` never produced; both consumers `.get(...,0.9)`; cap ladder misses budget/empty-snippet. Maintainer-doc'd as "1A" future PR (agent_capsule.py:3080-3082). Pinned tests assume the bug (test_token_budget.py:726-727, test_validation_commands.py:1521). |
| H5 | VERIFIED | `cli/main.py:3970` (no timeout); caller `:7983-7991`; twin `subprocess_policy.py:63` `configured_ripgrep_timeout_seconds` | second native-delegation route has NO subprocess timeout → `tg search PAT --cpu` hang; bootstrap twin returns 124. |
| H6 | VERIFIED | `backends/cudf_backend.py` (0 `BackendExecutionError`); retry gate `main.py:8300-8308` | GPU faults escape raw; per-file CPU-fallback retry never fires. |
| M1 | VERIFIED (narrow) | `checkpoint_store.py:885-893` create vs `:1311-1317` undo | create-side ancestor-symlink containment missing. |
| M3 | VERIFIED | `lsp_server.py:116-130,760-765` | documentChanges CreateFile/Rename/Delete targets not URI-checked. |
| M8 | VERIFIED | `ast_backend.py`/`ast_wrapper_backend.py` no invert_match; cache key `(file,lang,pattern)` | `--ast -v` silently un-honored + cache collision. |
| M10 | VERIFIED | `ast_wrapper_backend.py:210-215,274-283`; twin `ripgrep_backend.py:141-150` | ast-grep partial scan unmarked `result_incomplete`. |
| M13 | VERIFIED | `repo_map.py:8100-8109` | query-language aliases cover 4/10 (py/ts/js/rust). |
| M14 | PARTIAL | `mcp_server.py:1125-1140`; ≥2 unstamped (`tg_navigate` :6524, `tg_classify_logs` :5467) | contract-version stamping per-function, not central; "15/58" approx. |
| M16 | VERIFIED | `backend_ast_workflow.rs:871-904,1074-1099` | Rust `tg scan` drops composite rules + severity/message. |
| M17 | VERIFIED | `rust_core/index.rs:782-856`; reuse `main.rs:9336` | index reuse never checks stored root == query path. |

## Part B — Research-feature ledger (2026 competitive/arXiv), ranked, gate-flagged

| id | feature | evidence | tg gap (verified) | gate |
|---|---|---|---|---|
| R1 | Typed graph edges INHERITS/IMPLEMENTS/INSTANTIATES/USES + provenance | Gortex resolved edges; ARISE typed edges +17 Rec@1; Atlas | only imports/references/callers; extends/implements → `ref_kind="type"` (`repo_map.py:5292-5293`, lang_java.py:64-65) | default-OFF surface, additive |
| R2 | `tg index`: wire persisted `semantic_index.py` (BM25+chunks) + daemon incremental sync | Codebase-Memory content-hash reindex; LARGER commit-aware alignment; SuperCoder $/solved | index code exists but NOT CLI-wired ("no `tg index` yet", semantic_index.py:12-16); find recomputes every run | default-OFF; differential byte-identical proof |
| R3 | Query-policy layer: content classifier (regex/structural/NSL) + escalate-on-low-recall + search-budget meter | ARCS budgeted loop; SWE-Explore Context Efficiency r=0.95; CoREB short-query collapse | only argv router + single-token gate (`main.py:4431-4440`); docs reject richer classifier (:4386) | default-OFF; no existing-semantics change |
| R4a | Node/symbol-addressed edit apply — `tg edit-ready` | CodeStruct/CodeCompass; Probe "LLM is semantic layer" | `EditReadyTicketV1` concept only, cmd missing (`prepare_service.py:459-472`) | experimental, **needs design** |
| R4b | ARISE data-flow slicing `tg slice <var>` | ARISE +17/+15 | unbuilt; research-frontier labels lower-priority | experimental default-OFF |
| R5 | Line-level localization + tokens-per-correct-answer private harness (SWE-Explore-style) | SWE-Explore line-level is the shared bottleneck; Sverklo t/correct | no public/private position; internal proof points exist (defs 7.5×, file-deps 2.24×) | private only; publish gated #72 |
| R6 | Git co-change evolutionary-coupling in blast-radius | Ripple ICSE'26; Codebase-Memory git co-change edges | no co-change signal | additive pin-first ranking gate (A16) |
| R7 | C/C++ cross-file caller + `#include` graph | Codebase-Memory/Atlas LSP-augmented C/C++ | hooks `None` on c/cpp LanguageSpec (Problem 6 open) | decision-gated backlog item, plan separately |

---

## Part C — Combined execution order (ONE item per iteration)

P1. **H5 — native-delegation subprocess timeout** (Python, self-contained) — THIS ITERATION.
P2. **H4 — emit real seed `confidence.overall` + unconditional primary_target cap** (Python; design locked in §P2; re-pin the 3 pinned tests).
P3. **H1 — backend_cpu fail-closed error propagation** (Rust; CI oracle + Rust unit tests).
P4. **H3 — python_sidecar Cmd/BatBadBut → plain Command::new** (Rust; class-twin of rg_passthrough; A27/A39).
P5. **H2 — count-matches/files-* on native `--json` route: refuse-to-delegate + coverage ratchet** (Rust+Python; PARTIAL—scope excludes `-o`). Files: `cli/main.py:_can_delegate_to_native_tg_search` (≈:3860-3879) + the delegation gate (≈:7970-7991), `rust_core/src/main.rs:8285-8322`, `rust_core/src/native_search.rs:187-246`.
P6. **H6 — CuDFBackend BackendExecutionError normalization** (Python; #169 disposition = merge-with-no-promotion, Phase-0-class local correctness fix that changes no public GPU claim; no public GPU path changes).
P7. **M-wave 1 (flag/result honesty): M8 (AST invert_match + cache key), M10 (ast-grep result_incomplete), M13 (query-language aliases to 10)** — one PR with shared fail-closed coverage ratchet (class theme 1).
P8. **M-wave 2 (security-adjacent): M1 (checkpoint create-side containment), M3 (LSP file-ops confinement)**.
P9. **M-wave 3: M16 (Rust scan composite rules), M17 (index root check), M14 (central contract stamping), M7 (verify_receipt never-raises)**.
P10. **R2 `tg index`** (wire persisted index, differential byte-identical + daemon incremental; highest-value research item, 80% built).
P11. **R1 typed graph edges** (additive provenance-labeled; across parser-backed langs, first slice = java/python/php).
P12. **R6 git co-change blast-radius** (pin-first ranking gate).
P13. **R3 query-policy layer** (default-OFF opt-in).
P14. **R4a `tg edit-ready`** (needs design council first).
P15. **R4b `tg slice`** (experimental).
P16. **R5 private localization+tokens harness** (built + run privately; publication stays #72-gated).
P17. **R7 C/C++ include graph** — decision-gated; plan + council before build.

Banked cosmetic (no behavior change; batch later): L1-L9, dead-code deletions — only in a **separate** hygiene PR, never mixed.

---

## P1 · H5 — native-delegation subprocess timeout (THIS ITERATION)

**Files**
- Modify: `src/tensor_grep/cli/main.py:3955-3971` (`_delegate_to_native_tg_search`)
- Create: `tests/unit/test_native_delegation_timeout.py`

**Why (verified):** `_delegate_to_native_tg_search` runs `subprocess.run(command, check=False)` with no `timeout=`. Reachable via `tg search PAT --cpu`/`--json` → `_can_delegate_to_native_tg_search` → `sys.exit(_delegate_to_native_tg_search(...))` (main.py:7983-7991) BEFORE the rg 60s timeout. A hung native search hangs forever (fail-open). Bootstrap twin `_streaming_passthrough_returncode` (bootstrap.py:1230-1269) already converts `TimeoutExpired` → 124 + stderr hint. Timeout source: `configured_ripgrep_timeout_seconds()` (subprocess_policy.py:63; default 60s; TG_RG_TIMEOUT_SECONDS / TG_SIDECAR_TIMEOUT_MS).

**[ ] Step 1 — RED test** (`tests/unit/test_native_delegation_timeout.py`), mirroring test_passthrough_timeout.py semantics + the repo's fake-module patch style (never mutate global `subprocess.run`):

```python
"""H5 audit: the second native-delegation route (`_delegate_to_native_tg_search`,
cli/main.py:3970) had NO subprocess timeout, so a hung native search on the
`--cpu`/`--json` route hung forever (fail-open). Mirror the bootstrap twin's
`TimeoutExpired` -> exit 124 contract (bootstrap.py:1263-1269)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tensor_grep.cli import main as tg_main
from tensor_grep.core.config import SearchConfig


class _TimeoutSubprocess:
    TimeoutExpired = subprocess.TimeoutExpired

    @staticmethod
    def run(*_args, **_kwargs) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd=["tg.exe"], timeout=60)


def test_native_delegation_timeout_returns_124(monkeypatch, capsys) -> None:
    # Replace main's `subprocess` binding (NOT the global module) so the except clause
    # resolves `subprocess.TimeoutExpired` through the fake; monkeypatch auto-restores.
    monkeypatch.setattr(tg_main, "subprocess", _TimeoutSubprocess)
    rc = tg_main._delegate_to_native_tg_search(
        Path("tg.exe"),
        pattern="foo",
        paths=["."],
        config=SearchConfig(),
        ndjson=False,
    )
    assert rc == 124
    assert "timeout" in capsys.readouterr().err.lower()
```

Run → expected FAIL: `TimeoutExpired` propagates uncaught (because `subprocess.run(..., check=False)` has no timeout and no try/except), i.e. the test errors with the requested behavior missing. (TDD-note: for a timeout-catching fix the pre-fix RED is the uncaught exception — that IS the proof the test exercises the fix.)

**[ ] Step 2 — GREEN** (minimal): in `_delegate_to_native_tg_search`, replace `subprocess.run(command, check=False)` with a bounded call + catch:

```python
    from tensor_grep.cli.subprocess_policy import configured_ripgrep_timeout_seconds

    try:
        completed = subprocess.run(
            command, check=False, timeout=configured_ripgrep_timeout_seconds()
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            "tensor-grep: native search exceeded the configured timeout and was stopped. "
            "For a large repo, scope the search to a path (e.g. `tg search PATTERN src/`), "
            "or raise TG_RG_TIMEOUT_SECONDS.\n"
        )
        return 124
    return int(completed.returncode)
```

**[ ] Step 3 — GREEN tests (2 more):**
- `test_native_delegation_relays_normal_return_code`: fake subprocess returns `subprocess.CompletedProcess(args=command, returncode=1)`; assert `rc == 1` (backward-compat pin, like test_passthrough_timeout.py:47-52). Must pass only after fix (pre-fix returns 1 too, so this is a pass-through guard, not the RED — keep it as regression pin).
- `test_native_delegation_run_is_bounded`: fake records kwargs; assert `"timeout" in recorded_kwargs` and `isinstance(recorded_kwargs["timeout"], float)` — proves the call stays bounded (this FAILS pre-fix: no `timeout` kwarg). This is the discriminating control.

**[ ] Step 4 — Gate**: `uv run pytest tests/unit/test_native_delegation_timeout.py -q` (all pass, pristine), then `uv run pytest tests/unit/test_cli_modes.py -q`, `uv run pytest tests/unit/test_native_delegation_field_coverage.py -q`, `uv run pytest tests/unit/test_search_command_tail_exit_policy_route_parity.py -q` (no regressions), then `uv run ruff check src/tensor_grep/cli/main.py tests/unit/test_native_delegation_timeout.py`, `uv run ruff format --check --preview src/tensor_grep/cli/main.py tests/unit/test_native_delegation_timeout.py`, `uv run mypy src/tensor_grep`.

**[ ] Step 5 — adversarial cold-read audit** (fresh subagent on the diff): try to break the timeout (timeout semantics under `TG_RG_TIMEOUT_SECONDS=0`, `TimeoutExpired` with `output` partial, `completed.returncode` on OSError). Fix findings. Re-gate.

**[ ] Step 6 — commit** (`git add` ONLY `src/tensor_grep/cli/main.py` + `tests/unit/test_native_delegation_timeout.py`) → `fix: bound native-delegation search subprocess timeout to exit 124 (H5 audit)`; then optional draft PR.

## P2 · H4 — real seed `confidence.overall` + unconditional primary_target cap

**Files:** `repo_map.py:13773-13781` (producer), `agent_capsule.py:560` (seed read), `agent_capsule.py:3078-3109` (cap), `prepare_service.py:432` (forward), re-pin `test_token_budget.py:726-727`, `test_validation_commands.py:1521/1469-1470`, `test_agent_capsule_tie_suggested_scope.py:153`.

**Design (locked, pending thinktank sign-off — approved 2026-08-06 with fixes):** (1) producer derives `overall = max(file, symbol or 0)` computed AFTER the 0.65 filtered-alignment cap block drops in, so `overall` inherits that cap; keeps `{file,symbol,test}` keys (additive). Rationale: `test` is a separate validation axis and is legitimately 0.0 when no test matched — using it would nuke overall on normal queries; `file` anchors the target when `symbol` is absent (primary_symbol can be None). (2) `agent_capsule.build_agent_capsule_from_map` adds an UNCONDITIONAL `_cap_primary_target_confidence(target, float(confidence["overall"]))` immediately after `confidence = _confidence(...)` (:3078), so primary_target.confidence can never exceed the ladder-capped overall (closes budget/empty-snippet ladders the current caps miss, subsuming the scan_truncated special case at :3084-3085 which stays as belt+braces). Order-safety (verified by plan audit): nothing reads `primary_target["confidence"]` between :3078 and the LSP block; the LSP block (3100-3106) CAPS both at `_CAPSULE_LSP_CONFIDENCE_CAP` (0.85) — it does NOT raise; the ONLY raise path is `_apply_capsule_token_budget_confidence_uplift` (:3336) which re-caps both at :2671 — so the unconditional cap at :3078 is superseded correctly and is order-safe. (3) `evidence_receipt.py:576` + `main.py:10112/10195/10888` consumers verbatim — only their input becomes honest. (4) **Tie-threshold side-effect (audit F4, must be pinned):** `_tied_alternative_targets` uses `primary_target["confidence"]` as the tie threshold (agent_capsule.py:205-209); a REAL lower overall can newlY form a tie that the 0.9 default never did. Add a test: weak real overall (0.7) + moderate alternative (0.8) → tie detected (assert intent: ties surface as `tie_requires_confirmation`, never a silent 0.9).
**Pins to change (assert SUBSTANCE, not the bug's 0.9):** `test_validation_commands.py:1521` `clean_payload["confidence"] == {"overall": 0.9, ...}` → assert `confidence["overall"] == agent["confidence"]["overall"]` parity + `isinstance(float)` (the harness_api shape contract) and that a weak seed yields `< 0.75`; ALSO the real-producer pins `:1524-1531` (truncated `== {"overall": 0.9, ...}`), `:1595` (`== 0.74` tie parity), `:1613-1616` (`== {"overall": 0.74, "downgrade_reasons": ["alternative target confidence tie"]}`) — all run through `build_context_edit_plan`/`build_agent_capsule` on real corpora, so their floor/tie outcomes must be re-derived, not the literal dict re-pinned. The `:726-727` token_budget fixture comment and `:153` tie fixture → update fixture comments to "allows 'overall' now; see H4", keep their asserted outcomes.
**Gate (full — audit F3):** targeted `tests/unit/test_h4_confidence_overall.py` (new); re-run `tests/unit/test_repo_map_*.py` (all), `tests/unit/test_validation_commands.py`, `tests/unit/test_agent_capsule_*.py` (best_effort / token_budget / lsp_confidence / tie_suggested_scope / hardcases / outbound_deps / inline_caller_annotation / gpu_probe), `tests/unit/test_cli_modes.py` (densest confidence/primary_target pin surface), `tests/unit/test_evidence_receipt.py`, `tests/unit/test_harness_api_docs.py`, `tests/unit/test_token_budget.py`, `tests/eval/test_agent_accuracy.py` (per-task accuracy gate — often first to catch a capsule rerank); then ruff/mypy/preview.

## P3-P17 — dispatched one-per-iteration with their own TDD specs (from Part A/B seam tables)

Each P-item follows the identical loop: verify seam → RED test → GREEN minimal → gate (ruff/mypy/preview + targeted suite) → adversarial cold-read audit on the diff → fix → re-gate → surgical commit (only the item's files) → draft PR. Rust items (H1/H3/H2/H5-native) rely on CI as oracle; author Rust unit tests locally but do not cold `cargo check` the whole crate on this shared box (A12). KEEP the PR title in the conventional schema and release-class-correct (`fix:` for H/M, `feat:` for R-features that publish).

---

## Loop state / resume (updated by orchestrator each iteration)

- **Completed:** none yet (P1 in flight).
- **In-flight:** P1 · H5.
- **Next:** P2 · H4 (design above, needs thinktank sign-off before build).
- **Blocked/gated:** GPU (#169: H6 fix is local-correctness only, no promo); public benchmark publication (#72); R7 C/C++ (decision-gated).
- **Audit truth:** Part A verdicts from independent census @ HEAD 999dac8; re-verify any seam before building (symbols drift).
