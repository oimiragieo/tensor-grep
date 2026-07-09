# Design — Dogfood #84: scoped-agent / edit-plan empty validation_plan + budget-only confidence drag

**Verdict: GO-WITH-CHANGES.** Fable design 2026-07-09 (agent a88b45b45), live-reproduced on the real gotcontext-saddle repo, every seam file:line-verified. Type: features/quality (NOT security). Load-bearing (repo_map + agent_capsule core); the confidence change is IDF/ranking-fragility-adjacent — gate carefully.

## Convergence (fix lands once in the shared seed builder)
All 3 surfaces (`tg agent <scope>`, `tg edit-plan`, session/daemon/MCP edit-plan) flow through `_build_edit_plan_seed` (repo_map.py:11143) via `_attach_edit_plan_metadata` (repo_map.py:12315 / 11457) / `build_context_edit_plan_from_map` (session_store.py:902). Capsule reads the plan at agent_capsule.py:1836, re-aligns via `_capsule_validation_alignment` (:248-279), emits at :2186-2188 (plan) / :2204 (confidence) / :2205-2208 (ask_user_before_editing).

## The two bugs (both live-reproduced)
**Bug A (root cause of the empty plan) — boundary-README trap in `_validation_repo_root` (repo_map.py:8257-8296).** The walk-up breaks at the first dir with a BOUNDARY marker (README.md/.gitignore/LICENSE/AGENTS.md, :8274-8279) BEFORE examining that dir's parent (:8293-8294). `gotcontext-saddle/core/hooks/README.md` traps the walk at `core/hooks` → never reaches root `pyproject.toml`. Cascade: discovery early-returns `[]` (validation_root==scoped_path, :3653-3656); python fallback dies (no markers/tests at the trapped root, :8952-8966); raw detection gets python_detection="heuristic" → no fallback.
Live receipt: `_validation_repo_root(saddle/core/hooks)` → `core\hooks` (trapped); from `core` → true root. `_validation_plan_and_alignment_for_tests([], repo_root=scope, primary=hook.py)` → `[] / "no-validation"`; same with `repo_root=root` → `['uv run pytest -q'] / "aligned"`.

**Bug B (blocks root-parity) — discovery is JS/TS-only.** `_discover_validation_tests_for_primary_file` skips non-JS/TS: `if current.suffix.lower() not in _JS_TS_SUFFIXES: continue` (repo_map.py:3689-3690). Python discovery never existed (git log: born JS-only, commit fa9cbb0). So even post-Fix-A a scoped python run gets only the repo-scope `uv run pytest -q`, never the per-file `uv run pytest tests/hooks/... -q` root emits.

NOT a --max-files cap, NOT a scope filter: discovery walks the real FS via `_iter_repo_files(validation_root, max_files=512)` (:3674-3678) and already finds out-of-scope tests for JS/TS.

## The fix (smallest change, shared builder)
**Fix A — strong-boundary qualification (repo_map.py:8288-8289):** a dir becomes `boundary_candidate` only if STRONG — has `.git` (dir/file) OR >=2 distinct boundary markers. `core/hooks` (README only) no longer arms the :8293 break → walk reaches root. Project markers (:8286-8287) still win first. Keep the tempdir guard (:8283-8285) + `boundary_candidate or root` return (:8296). This one function anchors discovery (:3653), python fallback (:8899), suggested-cmd probe (:9031), raw detection (:9174), seed (:10862) — heals all.

**Fix B — Python branch in `_discover_validation_tests_for_primary_file` (repo_map.py:3679-3701):** replace the blanket non-JS/TS `continue` (:3689-3690) — `.py` test files (already `_is_test_file`-gated at :3680) proceed to scoring (:3691-3701, language-neutral); JS/TS keep the node:test gate (:3685-3688). Downstream safe: `.py` tests flow to validation_tests (:10870-10880), `_raw_validation_plan_for_tests` emits pytest unconditionally for `.py` (:9261-9286), root-relative paths (root=`_validation_repo_root`, now correct). No dup fallback (`_ensure_primary_language_validation_fallback` returns once a python step exists, :9087-9090).

**Do NOT change:** the explicit_root detection branch (:9195-9199, protects python-subdir-in-JS-monorepo), the lightweight path (:11268-11283), the JS/TS node:test gate. Scope/ignore: validation walk honors gitignore only, not `--ignore` globs (validation evidence = repo-truth, intended). Session no-walk contract preserved (`_precomputed_validation_files_for_root` short-circuits every walk, :1071-1096; pin test_session_cli.py:493-538).

## The confidence fix (finding 3) — capped corroboration channel
Mechanism: default max_tokens=1200 → primary snippet cut → `capsule_primary_file_omitted` → `_confidence` floors overall=0.55 (agent_capsule.py:1554-1557). The existing uplift (`_apply_capsule_token_budget_confidence_uplift`, :1669/:2092) requires call_site_evidence.status=="collected" (:1666), which a non-symbol query ("audit gotcontext read gate") can't earn (:475-479). So a populated plan buys nothing.
**Change:** in `_capsule_token_budget_uplift_eligible` (:1606-1666), accept an alternative: `targeted_validation_evidence` non-empty (`_targeted_validation_evidence` :351-364 — steps scope in {symbol,file} + non-empty target + confidence>=0.7) AND alignment "aligned" or ("mismatch-filtered" & kept>0). Thread `targeted_validation_evidence` (:1938) + `validation_alignment_status` (:1936) into the :2092 call.
**Threshold (ranking-fragility lens):** uplift to `_CAPSULE_TOKEN_BUDGET_CONFIDENCE_UPLIFT_CAP=0.75` (:32, below the graph-corroborated 0.8 at :38). 0.75 meets the no-ask threshold (:2055-2056). Append a channel-distinct reason for telemetry. NON-relaxations kept verbatim: scan-truncation (:1639), no-snippets (:1641), genuine-misroute (:1643-1650), tie (:1651), non-budget reasons (:1659-1663), query-overlap (:1664). A repo-scope `uv run pytest -q` @0.55 step is NOT targeted evidence → plans with only the fallback never uplift. Fix A+B alone also clears the scoped `ask_user_before_editing: required`-for-no-evidence (:2043-2052).

## Contract safety
Envelope additive-value-only (no schema change). Pins that stay green (verified): fail-closed no-evidence test_validation_commands.py:687-707 (still []); scoped fixtures :1066-1130 (no subdir README → unaffected; python one gets stronger); test_trust_planning.py:370-387; the 3 uplift/floor pins test_agent_capsule_token_budget_confidence.py:119-218 (test-2 fixture is repo-scope/0.55 → new channel doesn't flip it; test-3 misroute untouched); session no-walk :493-538. NO existing test pins the README-trap empty behavior → only NEW tests. Update any drifted pin in the same PR.

## TDD tasks (each red->green; gate uv run --no-sync)
1. Unit: `_validation_repo_root(core/hooks with README + root pyproject)` returns root (red today) + companions (git-top-stop, lone-subdir-README, tempdir guard).
2. Implement Fix A (:8288-8289) -> 1 green.
3. Unit: python discovery — `scoped/` (WITH README) + root pyproject + sibling `tests/test_<stem>.py`; discovery returns the test (red today); JS/TS gate unchanged.
4. Implement Fix B (:3685-3690) -> 3 green.
5. E2E headline: `build_agent_capsule("do thing", scoped_dir)` -> validation_plan has a pytest step scope=="file", root-relative target, detection=="detected"; validation_commands non-empty; ask_user_before_editing lacks "no validation command evidence". Mirror through build_context_edit_plan (finding-2 parity).
6. Confidence: (a) targeted step (scope:file, target:tests/test_handler.py, 0.82, aligned) + budget-cut + non-symbol query -> overall==0.75, required False, channel reason; (b) repo-scope step -> <=0.55, required True; (c) targeted + misroute -> floored; (d) existing 3 untouched-green.
7. Implement the confidence change (:1606/1666/1669/2092 threading) -> 6 green.
8. Regression sweep (test_validation_commands, test_agent_capsule_token_budget_confidence, test_trust_planning, test_session_cli, test_harness_api_docs) + full suite + REAL-BINARY dogfood `tg agent core/hooks "audit gotcontext read gate" --json` on gotcontext-saddle (CliRunner insufficient).

## Biggest risk + guard
The confidence change masking a genuine low-confidence case (a wrong-but-plausible primary whose stem matches a real test file gains "targeted evidence"). Guards: every genuine-ambiguity disqualifier retained verbatim (:1639-1663); channel only relieves the BUDGET-only clamp; 0.75 < graph-corroborated 0.8; repo-scope fallbacks never qualify; TDD 6b/6c pin the floor for the masking scenarios. Residual (accepted/documented): a vendored subtree with README+LICENSE (>=2 markers) still arms the old trap — but such subtrees usually carry their own project marker anyway.
